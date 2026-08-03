# tests/bulletins/management/commands/test_rekey_meteofrance_bulletins.py —
# Tests for the rekey_meteofrance_bulletins management command.
#
# Covers the management-command contract (read-only by default, --commit to
# write, non-zero exit on failure) plus the properties that make the one-time
# migration safe to run against live data: idempotency, deriving the new id from
# each row's own raw_data, and refusing to invent an id for a row with no
# publication timestamp (SNOW-559).
"""Tests for the rekey_meteofrance_bulletins management command."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from django.core.management import CommandError, call_command

from apps.bulletins.models import Bulletin
from apps.regions.models import MicroRegion
from tests.factories import BulletinFactory, MicroRegionFactory, RegionBulletinFactory

# 2026-02-12T16:00 Paris (CET, UTC+1) → 15:00Z.
_EVENING = "2026-02-12T15:00:00Z"
_EVENING_STAMP = "20260212150000"


def _raw_data(
    *,
    covered: str = "2026-02-13",
    publication_time: str | None = _EVENING,
    raw_local_published: str | None = None,
    massif: str = "ARAVIS",
) -> dict[str, Any]:
    """Build a stored ``raw_data`` payload for a Météo-France bulletin.

    Args:
        covered: The covered date recorded in ``customData.MF.date``.
        publication_time: Top-level ``publicationTime``, or ``None`` to omit it.
        raw_local_published: The live path's
            ``customData.MF.rawLocalTimes.publishedAt`` fallback, or ``None``.
        massif: The massif slug.

    Returns:
        A GeoJSON Feature envelope as stored on ``Bulletin.raw_data``.

    """
    mf: dict[str, Any] = {"massif": massif, "date": covered}
    if raw_local_published is not None:
        mf["rawLocalTimes"] = {"publishedAt": raw_local_published}
    properties: dict[str, Any] = {
        "lang": "fr",
        "customData": {"MF": mf},
    }
    if publication_time is not None:
        properties["publicationTime"] = publication_time
    return {"type": "Feature", "geometry": None, "properties": properties}


def _region(region_id: str = "FR-02") -> MicroRegion:
    """Return the MicroRegion for ``region_id``, creating it once per test.

    Several tests build more than one bulletin, and ``MicroRegion.slug`` is
    unique, so the region has to be reused rather than re-created.

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


def _make_bulletin(bulletin_id: str, **raw_kwargs: Any) -> Bulletin:
    """Create an FR bulletin with a stored payload and a linked region.

    Args:
        bulletin_id: The bulletin's current identifier.
        **raw_kwargs: Passed through to :func:`_raw_data`.

    Returns:
        The created Bulletin.

    """
    bulletin = BulletinFactory.create(
        bulletin_id=bulletin_id,
        raw_data=_raw_data(**raw_kwargs),
        issued_at=datetime(2026, 2, 12, 15, 0, tzinfo=UTC),
        valid_from=datetime(2026, 2, 12, 15, 0, tzinfo=UTC),
        valid_to=datetime(2026, 2, 13, 23, 59, 59, tzinfo=UTC),
    )
    # The region component of the id decides which massif the row belongs to.
    region_id = "-".join(bulletin_id.split("-")[:2])
    RegionBulletinFactory.create(bulletin=bulletin, region=_region(region_id))
    return bulletin


@pytest.mark.django_db
class TestReadOnlyByDefault:
    """The command must not write without --commit."""

    def test_no_writes_without_commit(self) -> None:
        """A bare invocation reports but does not change any id."""
        _make_bulletin("FR-02-2026-02-13")

        call_command("rekey_meteofrance_bulletins", verbosity=0)

        assert Bulletin.objects.filter(bulletin_id="FR-02-2026-02-13").exists()

    def test_reports_what_would_change(self, capsys: pytest.CaptureFixture) -> None:
        """The read-only run names the count it would re-key.

        Args:
            capsys: pytest stdout capture fixture.

        """
        _make_bulletin("FR-02-2026-02-13")

        call_command("rekey_meteofrance_bulletins")

        assert "Would re-key 1" in capsys.readouterr().out


@pytest.mark.django_db
class TestCommitMode:
    """--commit persists the publication-stamped id."""

    def test_rekeys_to_the_new_grammar(self) -> None:
        """The id gains the publication stamp taken from raw_data."""
        _make_bulletin("FR-02-2026-02-13")

        call_command("rekey_meteofrance_bulletins", "--commit", verbosity=0)

        assert Bulletin.objects.filter(
            bulletin_id=f"FR-02-2026-02-13-{_EVENING_STAMP}"
        ).exists()
        assert not Bulletin.objects.filter(bulletin_id="FR-02-2026-02-13").exists()

    def test_falls_back_to_raw_local_times(self) -> None:
        """A live-ingested row without publicationTime uses rawLocalTimes.

        ``publishedAt`` is naive Europe/Paris; 16:00 in February (CET) is 15:00Z.
        """
        _make_bulletin(
            "FR-02-2026-02-13",
            publication_time=None,
            raw_local_published="2026-02-12T15:00:00",
        )

        call_command("rekey_meteofrance_bulletins", "--commit", verbosity=0)

        assert Bulletin.objects.filter(
            bulletin_id=f"FR-02-2026-02-13-{_EVENING_STAMP}"
        ).exists()

    def test_leaves_non_fr_bulletins_alone(self) -> None:
        """Only FR ids are in scope."""
        BulletinFactory.create(bulletin_id="CH-2133-2026-02-13", raw_data={})

        call_command("rekey_meteofrance_bulletins", "--commit", verbosity=0)

        assert Bulletin.objects.filter(bulletin_id="CH-2133-2026-02-13").exists()


@pytest.mark.django_db
class TestIdempotency:
    """Re-running the command must be a no-op."""

    def test_second_run_changes_nothing(self, capsys: pytest.CaptureFixture) -> None:
        """A row already on the new grammar is skipped, not re-stamped.

        Args:
            capsys: pytest stdout capture fixture.

        """
        _make_bulletin("FR-02-2026-02-13")
        call_command("rekey_meteofrance_bulletins", "--commit", verbosity=0)
        capsys.readouterr()

        call_command("rekey_meteofrance_bulletins", "--commit")

        out = capsys.readouterr().out
        assert "Re-keyed 0" in out
        assert "already current 1" in out
        assert Bulletin.objects.filter(
            bulletin_id=f"FR-02-2026-02-13-{_EVENING_STAMP}"
        ).exists()

    def test_already_current_row_is_untouched(self) -> None:
        """A row created on the new grammar is left exactly as it is."""
        new_id = f"FR-02-2026-02-13-{_EVENING_STAMP}"
        _make_bulletin(new_id)

        call_command("rekey_meteofrance_bulletins", "--commit", verbosity=0)

        assert Bulletin.objects.filter(bulletin_id=new_id).count() == 1


@pytest.mark.django_db
class TestUnkeyableRows:
    """A row with no usable timestamp must fail loudly, not get a guessed id."""

    def test_missing_timestamp_exits_non_zero(self) -> None:
        """The command raises CommandError so cron and CI notice."""
        _make_bulletin("FR-02-2026-02-13", publication_time=None)

        with pytest.raises(CommandError, match="could not be re-keyed"):
            call_command("rekey_meteofrance_bulletins", "--commit", verbosity=0)

    def test_missing_timestamp_leaves_the_row_alone(self) -> None:
        """The id is not changed when it cannot be derived correctly."""
        _make_bulletin("FR-02-2026-02-13", publication_time=None)

        with pytest.raises(CommandError):
            call_command("rekey_meteofrance_bulletins", "--commit", verbosity=0)

        assert Bulletin.objects.filter(bulletin_id="FR-02-2026-02-13").exists()

    def test_existing_target_id_is_reported_not_clobbered(self) -> None:
        """A row whose new id is taken is left for inspection.

        This happens when the correctly-keyed issue has already been loaded from
        the rebuilt archive; the stale row must not overwrite it.
        """
        new_id = f"FR-02-2026-02-13-{_EVENING_STAMP}"
        _make_bulletin(new_id)
        _make_bulletin("FR-02-2026-02-13")

        with pytest.raises(CommandError):
            call_command("rekey_meteofrance_bulletins", "--commit", verbosity=0)

        assert Bulletin.objects.filter(bulletin_id="FR-02-2026-02-13").exists()
        assert Bulletin.objects.filter(bulletin_id=new_id).count() == 1


@pytest.mark.django_db
class TestArguments:
    """Argument handling."""

    def test_bulletin_id_restricts_scope(self) -> None:
        """--bulletin-id re-keys only the named row."""
        _make_bulletin("FR-02-2026-02-13")
        _make_bulletin("FR-03-2026-02-13")

        call_command(
            "rekey_meteofrance_bulletins",
            "--commit",
            "--bulletin-id",
            "FR-02-2026-02-13",
            verbosity=0,
        )

        assert Bulletin.objects.filter(
            bulletin_id=f"FR-02-2026-02-13-{_EVENING_STAMP}"
        ).exists()
        assert Bulletin.objects.filter(bulletin_id="FR-03-2026-02-13").exists()

    def test_rejects_a_non_fr_bulletin_id(self) -> None:
        """--bulletin-id must name a French bulletin."""
        with pytest.raises(CommandError, match="must start with"):
            call_command(
                "rekey_meteofrance_bulletins",
                "--bulletin-id",
                "CH-2133-2026-02-13",
                verbosity=0,
            )

    def test_rejects_an_unknown_bulletin_id(self) -> None:
        """--bulletin-id must name an existing row."""
        with pytest.raises(CommandError, match="No bulletin found"):
            call_command(
                "rekey_meteofrance_bulletins",
                "--bulletin-id",
                "FR-99-2026-02-13",
                verbosity=0,
            )

    def test_verbosity_zero_is_silent(self, capsys: pytest.CaptureFixture) -> None:
        """--verbosity 0 suppresses the progress output.

        Args:
            capsys: pytest stdout capture fixture.

        """
        _make_bulletin("FR-02-2026-02-13")

        call_command("rekey_meteofrance_bulletins", "--commit", verbosity=0)

        assert capsys.readouterr().out == ""


@pytest.mark.django_db
class TestStreaming:
    """SNOW-602: every row is visited exactly once, even across chunk boundaries.

    The queryset filters on ``bulletin_id__startswith='FR-'`` and the loop body
    rewrites that very column — the shape that broke the old OFFSET/LIMIT
    batching in the other three commands. The new id still starts with
    ``FR-``, so this doesn't hit the row-skipping failure mode directly, but
    it is exactly the pattern the plan calls out, so it is covered here too.
    """

    def test_every_row_rekeyed_exactly_once_across_chunk_boundaries(self) -> None:
        """A row count exceeding --batch-size is still re-keyed exactly once each."""
        bulletin_ids = [f"FR-{i:02d}-2026-02-13" for i in range(2, 7)]
        for bulletin_id in bulletin_ids:
            _make_bulletin(bulletin_id)

        call_command(
            "rekey_meteofrance_bulletins", "--commit", "--batch-size", "2", verbosity=0
        )

        assert Bulletin.objects.filter(bulletin_id__startswith="FR-").count() == 5
        for bulletin_id in bulletin_ids:
            assert Bulletin.objects.filter(
                bulletin_id=f"{bulletin_id}-{_EVENING_STAMP}"
            ).exists()

    def test_stdout_carries_processed_ids_in_descending_pk_order(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """Each inspected bulletin's original bulletin_id is printed, newest pk first."""
        first = _make_bulletin("FR-02-2026-02-13")
        second = _make_bulletin("FR-03-2026-02-13")
        expected = [
            b.bulletin_id
            for b in Bulletin.objects.filter(pk__in=[first.pk, second.pk]).order_by(
                "-id"
            )
        ]

        call_command("rekey_meteofrance_bulletins", "--commit", verbosity=1)

        out_lines = capsys.readouterr().out.splitlines()
        printed = [line for line in out_lines if line in set(expected)]
        assert printed == expected
