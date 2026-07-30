"""
tests/bulletins/services/test_golden_week.py — Tests for the golden-week corpus.

The golden week (SNOW-528) exists to make questions about behaviour *across*
days answerable against real data. These tests assert the properties that make
it worth having, so a CAAML shape change or an archive refresh that quietly
destroys one of them fails loudly here:

  - Every selected payload round-trips through the production render-model
    builder at the current ``RENDER_MODEL_VERSION`` — the same guarantee the
    sentinels carry.
  - The week's bulletins **partition** their regions: no region is claimed by
    two bulletins of the same provider and issue. This is the invariant whose
    absence produced the false conclusion behind the cancelled SNOW-527, and
    asserting it on real data is the main reason the corpus is worth keeping.
  - At least one date carries both a previous-evening and a morning issue, and
    the day-rating service selects the morning one for every shared region.
  - Loading produces RegionDayRating rows across all seven dates with more than
    one distinct rating value — i.e. the corpus actually shows danger spread
    and change, which the factory dataset cannot.
  - Météo-France's colliding synthesised bulletinIDs are collapsed
    deterministically rather than by archive line order.

Selection tests read the committed archives directly and need no database.
"""

from __future__ import annotations

import collections
import datetime

import pytest
from django.core.management import call_command

from apps.bulletins.models import Bulletin, RegionBulletin, RegionDayRating
from apps.bulletins.services.day_rating import target_day_for_valid_from
from apps.bulletins.services.golden_week import (
    GOLDEN_WEEK_LENGTH,
    SOURCE_ALBINA,
    SOURCE_METEOFRANCE,
    SOURCE_SLF,
    SOURCES,
    _dedupe_meteofrance,
    _iter_source_records,
    _meteofrance_publication_date,
    golden_week_dates,
    select_records,
    target_day,
)
from apps.bulletins.services.render_model import (
    RENDER_MODEL_VERSION,
    build_render_model,
)

# Region fixtures the week spans — CH (SLF), AT + IT (ALBINA), FR (Météo-France).
_REGION_FIXTURES = ("eaws_CH", "eaws_AT", "eaws_IT", "eaws_FR")


@pytest.fixture(scope="module")
def selected() -> dict[str, list[dict]]:
    """Return the week's selected payloads per provider, read once per module.

    Reading the three archives is the slow part of these tests (~30 MB of
    NDJSON), and selection is a pure function of committed files, so it is
    safe and worthwhile to do it once.
    """
    return {source: select_records(source) for source in SOURCES}


class TestWeekShape:
    """The week is seven consecutive days covered by all three providers."""

    def test_week_is_seven_consecutive_days(self) -> None:
        """The date list is contiguous, ascending, and starts on a Monday."""
        dates = golden_week_dates()
        assert len(dates) == GOLDEN_WEEK_LENGTH
        assert dates == sorted(dates)
        assert dates[0].weekday() == 0, "the week should start on a Monday"
        for earlier, later in zip(dates, dates[1:], strict=False):
            assert later - earlier == datetime.timedelta(days=1)

    def test_every_provider_covers_every_day(
        self, selected: dict[str, list[dict]]
    ) -> None:
        """Each provider contributes at least one bulletin on all seven dates.

        A provider missing a day would make that day's map silently partial,
        which is the failure mode the corpus exists to rule out.
        """
        for source in SOURCES:
            covered = {target_day(source, raw) for raw in selected[source]}
            assert covered == set(golden_week_dates()), (
                f"{source} does not cover every day of the golden week"
            )

    def test_selection_falls_entirely_inside_the_week(
        self, selected: dict[str, list[dict]]
    ) -> None:
        """No selected record targets a day outside the week."""
        week = set(golden_week_dates())
        for source in SOURCES:
            for raw in selected[source]:
                assert target_day(source, raw) in week


class TestTargetDayResolution:
    """Each provider's target day is read from that provider's own day key."""

    def test_slf_evening_issue_targets_the_following_day(
        self, selected: dict[str, list[dict]]
    ) -> None:
        """An SLF issue published after noon forecasts the next calendar day.

        This is the rule that makes selection agree with the day a persisted
        bulletin is later rated against; getting it wrong shifts SLF by a day.
        """
        evening = [
            raw
            for raw in selected[SOURCE_SLF]
            if raw["validTime"]["startTime"][11:13] >= "12"
        ]
        assert evening, "expected the week to contain SLF evening issues"
        for raw in evening:
            start = datetime.datetime.fromisoformat(
                raw["validTime"]["startTime"].replace("Z", "+00:00")
            )
            assert target_day(SOURCE_SLF, raw) == start.date() + datetime.timedelta(
                days=1
            )

    def test_albina_uses_main_date_not_valid_from(
        self, selected: dict[str, list[dict]]
    ) -> None:
        """ALBINA's target day comes from customData.ALBINA.mainDate.

        Its ``validTime.startTime`` is 16:00 on the *previous* day, so reading
        the day from that field would shift the whole provider back by one.
        """
        for raw in selected[SOURCE_ALBINA]:
            main_date = raw["customData"]["ALBINA"]["mainDate"]
            assert target_day(SOURCE_ALBINA, raw) == datetime.date.fromisoformat(
                main_date
            )

    def test_meteofrance_uses_its_own_date_key(
        self, selected: dict[str, list[dict]]
    ) -> None:
        """Météo-France's target day comes from customData.MF.date."""
        for raw in selected[SOURCE_METEOFRANCE]:
            mf_date = raw["customData"]["MF"]["date"]
            assert target_day(SOURCE_METEOFRANCE, raw) == datetime.date.fromisoformat(
                mf_date
            )

    def test_unknown_source_is_rejected(self) -> None:
        """A source outside the known providers raises rather than returning None."""
        with pytest.raises(ValueError, match="Unknown bulletin source"):
            target_day("NOT_A_PROVIDER", {})


class TestMeteoFranceDeduplication:
    """Colliding synthesised bulletinIDs are collapsed deterministically."""

    def test_selected_meteofrance_ids_are_unique(
        self, selected: dict[str, list[dict]]
    ) -> None:
        """No two selected MF records share a bulletinID.

        Without the dedupe, ``upsert_bulletin`` would write one record then
        overwrite it with the other, and the survivor would depend on the order
        of lines in the archive file.
        """
        ids = [raw["bulletinID"] for raw in selected[SOURCE_METEOFRANCE]]
        assert len(ids) == len(set(ids))

    def test_dedupe_keeps_the_latest_published_issue(self) -> None:
        """Where MF published twice for one day, the morning issue survives.

        The archive holds a previous-evening BRA and a morning BRA for the same
        massif and covered day. Keeping the later-published one matches the
        morning-wins rule applied to every other provider.
        """
        week = set(golden_week_dates())
        raw_records = [
            raw
            for raw in _iter_source_records(SOURCE_METEOFRANCE)
            if target_day(SOURCE_METEOFRANCE, raw) in week
        ]
        by_id: dict[str, list[dict]] = collections.defaultdict(list)
        for raw in raw_records:
            by_id[raw["bulletinID"]].append(raw)
        collided = {k: v for k, v in by_id.items() if len(v) > 1}
        assert collided, "expected the week to exercise the MF ID collision"

        kept = {raw["bulletinID"]: raw for raw in _dedupe_meteofrance(raw_records)}
        for bulletin_id, group in collided.items():
            latest = max(group, key=_meteofrance_publication_date)
            assert _meteofrance_publication_date(
                kept[bulletin_id]
            ) == _meteofrance_publication_date(latest)

    def test_publication_date_of_unparseable_source_file_is_empty(self) -> None:
        """A missing or dateless source_file sorts first rather than raising."""
        assert _meteofrance_publication_date({}) == ""
        assert (
            _meteofrance_publication_date(
                {"customData": {"MF": {"source_file": "BRA.ARAVIS.pdf"}}}
            )
            == ""
        )


class TestRenderModelRoundTrip:
    """Every selected payload builds a render model at the current version."""

    def test_every_payload_builds_a_render_model(
        self, selected: dict[str, list[dict]]
    ) -> None:
        """No selected record fails ``build_render_model``.

        Mirrors the sentinel round-trip guarantee: a CAAML shape change that
        the builder cannot handle fails here rather than surfacing as an
        error-sentinel render model in a seeded database.
        """
        for source in SOURCES:
            for raw in selected[source]:
                render_model = build_render_model(raw)
                assert render_model, f"{source} {raw.get('bulletinID')} built nothing"


class TestRegionPartition:
    """A single issue never claims the same region twice (the SNOW-527 trap)."""

    def test_regions_partition_within_each_issue(
        self, selected: dict[str, list[dict]]
    ) -> None:
        """For any provider, day and issue time, regions appear at most once.

        Two bulletins of the same issue claiming one region cannot happen
        upstream; a fixture that manufactures it invites exactly the false
        conclusion that killed SNOW-527. Grouping by issue time — not just by
        day — is what makes this meaningful, since a day legitimately carries
        both a previous-evening and a morning issue covering the same regions.
        """
        for source in SOURCES:
            claims: dict[tuple, collections.Counter] = collections.defaultdict(
                collections.Counter
            )
            for raw in selected[source]:
                issue = raw.get("publicationTime") or raw.get("customData", {}).get(
                    "MF", {}
                ).get("source_file", "")
                key = (target_day(source, raw), issue)
                for region in raw.get("regions", []):
                    claims[key][region["regionID"]] += 1

            for key, counter in claims.items():
                overlaps = {r: n for r, n in counter.items() if n > 1}
                assert not overlaps, (
                    f"{source} issue {key} claims region(s) more than once: {overlaps}"
                )


@pytest.mark.django_db
class TestGoldenWeekLoad:
    """Loading the week through the production ingest path."""

    @pytest.fixture(autouse=True)
    def _regions(self) -> None:
        """Load the four region fixtures the week's bulletins reference."""
        call_command("loaddata", *_REGION_FIXTURES, verbosity=0)

    def test_dry_run_writes_nothing(self) -> None:
        """Without --commit the command creates no rows at all."""
        call_command("seed_test_week", verbosity=0)
        assert Bulletin.objects.count() == 0
        assert RegionDayRating.objects.count() == 0

    def test_commit_loads_every_selected_record(
        self, selected: dict[str, list[dict]]
    ) -> None:
        """--commit persists one Bulletin per selected record, none failed."""
        call_command("seed_test_week", commit=True, verbosity=0)
        expected = sum(len(records) for records in selected.values())
        assert Bulletin.objects.count() == expected

    def test_every_loaded_bulletin_has_a_current_render_model(self) -> None:
        """No bulletin lands with the version-0 error sentinel."""
        call_command("seed_test_week", commit=True, verbosity=0)
        stale = Bulletin.objects.exclude(render_model_version=RENDER_MODEL_VERSION)
        assert not stale.exists(), (
            f"{stale.count()} bulletin(s) did not build a current render model"
        )

    def test_ratings_span_the_week_with_real_spread(self) -> None:
        """Every date gets ratings, and the week shows more than one level.

        This is what the factory dataset cannot do: ``seed_test_data`` rates
        every map-date region ``moderate``, so nothing that depends on danger
        spread or change over time can be tested against it.
        """
        call_command("seed_test_week", commit=True, verbosity=0)

        rated_days = set(
            RegionDayRating.objects.values_list("date", flat=True).distinct()
        )
        assert set(golden_week_dates()) <= rated_days

        levels = set(
            RegionDayRating.objects.filter(date__in=golden_week_dates()).values_list(
                "max_rating", flat=True
            )
        )
        meaningful = levels - {None, RegionDayRating.Rating.NO_RATING}
        assert len(meaningful) > 1, f"expected a danger spread, got {levels}"

    def test_morning_issue_supersedes_the_previous_evening(self) -> None:
        """Where both issues cover a day, the rating comes from the morning one.

        The day-rating service picks the candidate with the latest
        ``valid_from``, so a morning issue must win over the previous evening's
        for every region the two share. This is the cross-day behaviour the
        corpus exists to make testable.
        """
        call_command("seed_test_week", commit=True, verbosity=0)

        contested = 0
        for day in golden_week_dates():
            previous_evening = day - datetime.timedelta(days=1)

            # MicroRegion PKs covered by an issue published the previous
            # evening (which forecasts `day`) and by a morning issue on `day`.
            evening_regions = set(
                RegionBulletin.objects.filter(
                    bulletin__valid_from__date=previous_evening,
                    bulletin__valid_from__hour__gte=12,
                ).values_list("region_id", flat=True)
            )
            morning_regions = set(
                RegionBulletin.objects.filter(
                    bulletin__valid_from__date=day,
                    bulletin__valid_from__hour__lt=12,
                ).values_list("region_id", flat=True)
            )
            both = evening_regions & morning_regions
            if not both:
                continue

            ratings = RegionDayRating.objects.filter(
                date=day, region_id__in=both
            ).select_related("source_bulletin")
            for rating in ratings:
                source = rating.source_bulletin
                if source is None:
                    continue
                assert target_day_for_valid_from(source.valid_from) == day
                assert source.target_date == day
                assert (
                    source.valid_from.date() == day and source.valid_from.hour < 12
                ), (
                    f"{rating.region} on {day} was rated from the "
                    f"{source.valid_from:%Y-%m-%d %H:%M} issue, but a morning "
                    "issue for that day exists and should have won"
                )
                contested += 1

        assert contested, (
            "expected at least one region covered by both a previous-evening "
            "and a morning issue"
        )
