# tests/bulletins/management/commands/test_purge_legacy_meteofrance_bulletins.py —
# Tests for the purge_legacy_meteofrance_bulletins management command.
#
# Covers the management-command contract (read-only by default, --commit to
# write, non-zero exit on failure) plus the properties that make the delete
# safe against production data: the replacement guard (only delete a legacy
# row once a new-grammar sibling already exists), the BulletinShare gate, the
# RegionDayRating recompute after deletion, and idempotency (SNOW-562).
"""Tests for the purge_legacy_meteofrance_bulletins management command."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.core.management import CommandError, call_command

from apps.bulletins.models import (
    Bulletin,
    BulletinGrouping,
    RegionBulletin,
    RegionDayRating,
)
from apps.bulletins.services.meteofrance_archive_loader import load_meteofrance_archive
from apps.bulletins.services.meteofrance_identity import (
    BULLETIN_ID_RE,
    compact_publication_stamp,
)
from apps.bulletins.services.render_model import RENDER_MODEL_VERSION
from apps.regions.models import MicroRegion
from tests.factories import (
    BulletinFactory,
    BulletinGroupingFactory,
    BulletinShareFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
    RegionDayRatingFactory,
)

# 2026-02-13, morning issue — target_day_for_valid_from puts this on 2026-02-13.
_MORNING = datetime(2026, 2, 13, 6, 0, tzinfo=UTC)
_STAMP = compact_publication_stamp(_MORNING.isoformat())


def _region(region_id: str = "FR-02") -> MicroRegion:
    """Return the MicroRegion for ``region_id``, creating it once per test.

    Args:
        region_id: The EAWS region identifier.

    Returns:
        The existing or newly-created MicroRegion.

    """
    existing = MicroRegion.objects.filter(region_id=region_id).first()
    if existing is not None:
        return existing
    return MicroRegionFactory.create(
        region_id=region_id, name=f"Massif {region_id[-2:]}"
    )


def _fr_bulletin(
    bulletin_id: str,
    *,
    region: MicroRegion | None = None,
    valid_from: datetime = _MORNING,
    headline_key: str = "low",
) -> Bulletin:
    """Create an FR bulletin with a qualifying render model and a linked region.

    Args:
        bulletin_id: The bulletin's identifier (old or new grammar).
        region: The region to link. Defaults to the FR-02 region resolved
            from ``bulletin_id``.
        valid_from: The bulletin's validity start (also used as issued_at).
        headline_key: The render model's headline danger key.

    Returns:
        The created Bulletin, linked to ``region`` via RegionBulletin.

    """
    if region is None:
        region_id = "-".join(bulletin_id.split("-")[:2])
        region = _region(region_id)
    render_model: dict[str, Any] = {
        "version": RENDER_MODEL_VERSION,
        "source": "meteofrance",
        "danger": {
            "key": headline_key,
            "subdivision": None,
            "number": 1,
            "ratings": [],
        },
        "traits": [],
    }
    bulletin = BulletinFactory.create(
        bulletin_id=bulletin_id,
        raw_data={},
        issued_at=valid_from,
        valid_from=valid_from,
        valid_to=valid_from + timedelta(hours=23),
        render_model=render_model,
        render_model_version=RENDER_MODEL_VERSION,
    )
    RegionBulletinFactory.create(
        bulletin=bulletin, region=region, region_name_at_time=region.name
    )
    return bulletin


def _old_and_new(
    *, old_headline: str = "low", new_headline: str = "considerable"
) -> tuple[Bulletin, Bulletin]:
    """Create a replaceable pair: an old-grammar row and its new-grammar sibling.

    Args:
        old_headline: The headline danger key on the legacy row.
        new_headline: The headline danger key on the replacement row.

    Returns:
        A ``(old_bulletin, new_bulletin)`` tuple, both linked to FR-02.

    """
    region = _region("FR-02")
    old = _fr_bulletin("FR-02-2026-02-13", region=region, headline_key=old_headline)
    new = _fr_bulletin(
        f"FR-02-2026-02-13-{_STAMP}", region=region, headline_key=new_headline
    )
    return old, new


@pytest.mark.django_db
class TestReadOnlyByDefault:
    """The command must not write without --commit."""

    def test_no_writes_without_commit(self) -> None:
        """A bare invocation reports but deletes nothing."""
        _old_and_new()

        call_command("purge_legacy_meteofrance_bulletins", verbosity=0)

        assert Bulletin.objects.filter(bulletin_id="FR-02-2026-02-13").exists()

    def test_reports_per_massif_counts(self, capsys: pytest.CaptureFixture) -> None:
        """The read-only run names the per-massif candidate/replaceable counts.

        Args:
            capsys: pytest stdout capture fixture.

        """
        _old_and_new()

        call_command("purge_legacy_meteofrance_bulletins")

        out = capsys.readouterr().out
        assert "FR-02: candidates=1 replaceable=1 unreplaced=0" in out
        assert "Would delete 1 bulletin(s)" in out


@pytest.mark.django_db
class TestCommitMode:
    """--commit deletes only replaceable old-grammar rows."""

    def test_deletes_the_replaceable_old_grammar_row(self) -> None:
        """The legacy row is removed once its replacement exists."""
        old, new = _old_and_new()

        call_command("purge_legacy_meteofrance_bulletins", "--commit", verbosity=0)

        assert not Bulletin.objects.filter(pk=old.pk).exists()
        assert Bulletin.objects.filter(pk=new.pk).exists()

    def test_leaves_non_fr_bulletins_alone(self) -> None:
        """Only FR ids are ever candidates."""
        BulletinFactory.create(bulletin_id="CH-2133-2026-02-13", raw_data={})
        _old_and_new()

        call_command("purge_legacy_meteofrance_bulletins", "--commit", verbosity=0)

        assert Bulletin.objects.filter(bulletin_id="CH-2133-2026-02-13").exists()

    def test_every_surviving_fr_bulletin_matches_new_grammar(self) -> None:
        """After purging, no FR bulletin is left on the old grammar."""
        _old_and_new()

        call_command("purge_legacy_meteofrance_bulletins", "--commit", verbosity=0)

        remaining = Bulletin.objects.filter(bulletin_id__startswith="FR-").values_list(
            "bulletin_id", flat=True
        )
        assert remaining
        assert all(BULLETIN_ID_RE.match(bid) for bid in remaining)

    def test_region_bulletin_and_grouping_cascade_away(self) -> None:
        """RegionBulletin and BulletinGrouping rows for the deleted bulletin vanish."""
        old, _new = _old_and_new()
        grouping = BulletinGroupingFactory.create(
            bulletin=old, target_date=old.target_date
        )
        grouping_pk = grouping.pk
        region_bulletin_pk = RegionBulletin.objects.get(bulletin=old).pk

        call_command("purge_legacy_meteofrance_bulletins", "--commit", verbosity=0)

        assert not BulletinGrouping.objects.filter(pk=grouping_pk).exists()
        assert not RegionBulletin.objects.filter(pk=region_bulletin_pk).exists()


@pytest.mark.django_db
class TestDayRatingRecompute:
    """RegionDayRating is nulled and recomputed, not left stale."""

    def test_source_bulletin_nulled_and_rating_recomputed(self) -> None:
        """The stale min/max is replaced by the surviving bulletin's headline."""
        old, new = _old_and_new(old_headline="low", new_headline="considerable")
        region = _region("FR-02")
        rating = RegionDayRatingFactory.create(
            region=region,
            date=old.target_date,
            min_rating="low",
            max_rating="low",
            source_bulletin=old,
        )

        call_command("purge_legacy_meteofrance_bulletins", "--commit", verbosity=0)

        rating.refresh_from_db()
        assert rating.source_bulletin_id == new.pk
        assert rating.min_rating == "considerable"
        assert rating.max_rating == "considerable"

    def test_skip_day_ratings_leaves_the_stale_rating(self) -> None:
        """--skip-day-ratings nulls the FK but does not recompute the row."""
        old, _new = _old_and_new(old_headline="low", new_headline="considerable")
        region = _region("FR-02")
        rating = RegionDayRatingFactory.create(
            region=region,
            date=old.target_date,
            min_rating="low",
            max_rating="low",
            source_bulletin=old,
        )

        call_command(
            "purge_legacy_meteofrance_bulletins",
            "--commit",
            "--skip-day-ratings",
            verbosity=0,
        )

        rating.refresh_from_db()
        assert rating.source_bulletin_id is None
        assert rating.max_rating == "low"


@pytest.mark.django_db
class TestBulletinShareGate:
    """A live BulletinShare on a candidate blocks --commit unless overridden."""

    def test_share_does_not_block_dry_run(self, capsys: pytest.CaptureFixture) -> None:
        """A dry-run reports the share count but does not raise.

        Nothing is being deleted in a dry-run, so nothing can be orphaned —
        the share gate must only fire under --commit.

        Args:
            capsys: pytest stdout capture fixture.

        """
        old, _new = _old_and_new()
        region = _region("FR-02")
        BulletinShareFactory.create(
            bulletin=old, region=region, target_date=old.target_date
        )

        call_command("purge_legacy_meteofrance_bulletins")

        out = capsys.readouterr().out
        assert "BulletinShare(bulletin to null)=1" in out
        assert Bulletin.objects.filter(pk=old.pk).exists()

    def test_share_blocks_commit(self) -> None:
        """The command refuses to delete a bulletin with a live share."""
        old, _new = _old_and_new()
        region = _region("FR-02")
        BulletinShareFactory.create(
            bulletin=old, region=region, target_date=old.target_date
        )

        with pytest.raises(CommandError, match="BulletinShare"):
            call_command("purge_legacy_meteofrance_bulletins", "--commit", verbosity=0)

        assert Bulletin.objects.filter(pk=old.pk).exists()

    def test_allow_orphaned_shares_proceeds_and_nulls_the_link(self) -> None:
        """--allow-orphaned-shares lets the delete through and nulls the FK."""
        old, _new = _old_and_new()
        region = _region("FR-02")
        share = BulletinShareFactory.create(
            bulletin=old, region=region, target_date=old.target_date
        )

        call_command(
            "purge_legacy_meteofrance_bulletins",
            "--commit",
            "--allow-orphaned-shares",
            verbosity=0,
        )

        assert not Bulletin.objects.filter(pk=old.pk).exists()
        share.refresh_from_db()
        assert share.bulletin_id is None


@pytest.mark.django_db
class TestUnreplacedCandidates:
    """A legacy row with no new-grammar sibling must never be deleted."""

    def test_unreplaced_candidate_exits_non_zero(self) -> None:
        """The command raises CommandError so cron and CI notice."""
        _fr_bulletin("FR-02-2026-02-13")

        with pytest.raises(CommandError, match="no new-grammar"):
            call_command("purge_legacy_meteofrance_bulletins", "--commit", verbosity=0)

    def test_unreplaced_candidate_is_left_alone(self) -> None:
        """The row survives the failed run."""
        _fr_bulletin("FR-02-2026-02-13")

        with pytest.raises(CommandError):
            call_command("purge_legacy_meteofrance_bulletins", "--commit", verbosity=0)

        assert Bulletin.objects.filter(bulletin_id="FR-02-2026-02-13").exists()

    def test_a_replaceable_candidate_alongside_an_unreplaced_one_is_untouched(
        self,
    ) -> None:
        """One unreplaced candidate blocks the whole run — nothing is deleted."""
        old, _new = _old_and_new()
        _fr_bulletin("FR-03-2026-02-13")

        with pytest.raises(CommandError):
            call_command("purge_legacy_meteofrance_bulletins", "--commit", verbosity=0)

        assert Bulletin.objects.filter(pk=old.pk).exists()


@pytest.mark.django_db
class TestIdempotency:
    """Re-running the command after a successful purge must be a no-op."""

    def test_second_commit_finds_nothing(self, capsys: pytest.CaptureFixture) -> None:
        """A second --commit run reports nothing to purge and exits 0.

        Args:
            capsys: pytest stdout capture fixture.

        """
        _old_and_new()
        call_command("purge_legacy_meteofrance_bulletins", "--commit", verbosity=0)
        capsys.readouterr()

        call_command("purge_legacy_meteofrance_bulletins", "--commit")

        assert "Nothing to purge" in capsys.readouterr().out


@pytest.mark.django_db
class TestArguments:
    """Argument handling."""

    def test_verbosity_zero_is_silent(self, capsys: pytest.CaptureFixture) -> None:
        """--verbosity 0 suppresses the progress output.

        Args:
            capsys: pytest stdout capture fixture.

        """
        _old_and_new()

        call_command("purge_legacy_meteofrance_bulletins", "--commit", verbosity=0)

        assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# End-to-end: a synthetic archive load feeding the purge.
# ---------------------------------------------------------------------------


def _archive_line(slug: str, date_str: str, *, publication_time: str) -> str:
    """Return a minimal valid Météo-France NDJSON line for one massif-day.

    Mirrors the shape built by the sibling
    ``tests/bulletins/services/test_meteofrance_archive_loader.py``'s
    ``_make_line`` helper — kept local rather than imported so this test's
    fixture stays self-contained.

    Args:
        slug: Massif slug used in ``customData.MF.massif`` and the raw
            ``regionID`` (e.g. ``"ARAVIS"``); translated to ``FR-{NN}`` by
            the loader via ``SLUG_TO_CODE``.
        date_str: The covered date, ``YYYY-MM-DD``.
        publication_time: ISO-8601 publication instant.

    Returns:
        JSON-serialised NDJSON line.

    """
    envelope: dict[str, Any] = {
        "type": "Feature",
        "geometry": None,
        "properties": {
            "lang": "fr",
            "regions": [{"regionID": f"FR-{slug}", "name": slug.capitalize()}],
            "publicationTime": publication_time,
            "validTime": {
                "startTime": publication_time,
                "endTime": f"{date_str}T23:59:59+00:00",
            },
            "customData": {"MF": {"massif": slug, "date": date_str}},
            "dangerRatings": [
                {
                    "mainValue": "considerable",
                    "elevation": {"lowerBound": None, "upperBound": None},
                    "valid": True,
                }
            ],
            "avalancheProblems": [],
        },
    }
    return json.dumps(envelope)


@pytest.fixture()
def _load_fr_regions(db: object) -> None:
    """Load the eaws_FR.json fixture so FR-01/FR-02 MicroRegion rows exist."""
    call_command("loaddata", "eaws_FR", verbosity=0)


@pytest.mark.django_db
@pytest.mark.usefixtures("_load_fr_regions")
class TestEndToEnd:
    """A legacy row is replaced by a load of the rebuilt archive, then purged."""

    def test_purge_after_archive_load(self) -> None:
        """The pre-existing legacy row is deleted once the archive supplies it."""
        publication_time = "2026-01-14T16:00:00Z"
        legacy = _fr_bulletin(
            "FR-02-2026-01-15",
            valid_from=datetime.fromisoformat(publication_time.replace("Z", "+00:00")),
        )
        expected_new_id = (
            f"FR-02-2026-01-15-{compact_publication_stamp(publication_time)}"
        )
        line = _archive_line("ARAVIS", "2026-01-15", publication_time=publication_time)
        load_result = load_meteofrance_archive([line], commit=True)
        assert load_result.failed == 0
        assert Bulletin.objects.filter(bulletin_id=expected_new_id).exists()

        call_command("purge_legacy_meteofrance_bulletins", "--commit", verbosity=0)

        assert not Bulletin.objects.filter(pk=legacy.pk).exists()
        assert Bulletin.objects.filter(bulletin_id=expected_new_id).exists()
        remaining = Bulletin.objects.filter(bulletin_id__startswith="FR-").values_list(
            "bulletin_id", flat=True
        )
        assert all(BULLETIN_ID_RE.match(bid) for bid in remaining)
        # No row is left holding a real rating alongside a null source_bulletin
        # — either it was recomputed against the surviving bulletin, or it has
        # no qualifying bulletin at all (no_rating).
        assert (
            not RegionDayRating.objects.filter(source_bulletin__isnull=True)
            .exclude(max_rating=RegionDayRating.Rating.NO_RATING)
            .exists()
        )
