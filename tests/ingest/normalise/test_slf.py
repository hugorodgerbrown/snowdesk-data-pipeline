"""
tests/ingest/normalise/test_slf.py — Tests for apps.ingest.normalise.slf.

Moved from tests/bulletins/services/test_slf_fetcher.py with the code
(SNOW-1248). Pure functions, so nothing here touches the database:

  - normalise_slf_response: all three API response shapes + empty cases
  - parse_dt: ISO-8601 parsing
  - resolve_issued_at: publicationTime, with the validTime fallback
  - slf_pdf_url: URL derivation for SLF archive PDFs
  - detect_caaml_shape: aggregated vs per-region (SNOW-900)
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from apps.ingest.normalise.slf import (
    CAAML_SHAPE_AGGREGATED,
    CAAML_SHAPE_PER_REGION,
    CAAML_SHAPE_UNKNOWN,
    detect_caaml_shape,
    normalise_slf_response,
    parse_dt,
    resolve_issued_at,
    slf_pdf_url,
)


def _make_raw_bulletin(
    bulletin_id: str = "test-001",
    publication_time: str = "2025-03-15T08:00:00Z",
    **overrides: Any,
) -> dict[str, Any]:
    """
    Build a raw bulletin dict matching the SLF CAAML API shape.

    Args:
        bulletin_id: The bulletin identifier.
        publication_time: ISO-8601 publication timestamp.
        **overrides: Additional keys to merge into the bulletin dict.

    Returns:
        A dict matching the shape returned by the SLF CAAML API.

    """
    base: dict[str, Any] = {
        "bulletinID": bulletin_id,
        "publicationTime": publication_time,
        "validTime": {
            "startTime": "2025-03-15T17:00:00Z",
            "endTime": "2025-03-16T17:00:00Z",
        },
        "nextUpdate": "2025-03-16T08:00:00Z",
        "lang": "en",
        "unscheduled": False,
        "regions": [
            {"regionID": "CH-4115", "name": "Piz Buin"},
            {"regionID": "CH-7111", "name": "Engadin"},
        ],
        "dangerRatings": [],
        "avalancheProblems": [],
        "customData": {"CH": {}},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# normalise_slf_response
# ---------------------------------------------------------------------------


class TestNormaliseResponse:
    """Tests for normalise_slf_response."""

    def test_flat_list(self) -> None:
        """A flat list of bulletin dicts is returned as-is."""
        bulletins = [{"bulletinID": "a"}, {"bulletinID": "b"}]
        assert normalise_slf_response(bulletins) == bulletins

    def test_single_collection_object(self) -> None:
        """A dict with a 'bulletins' key unwraps to the inner list."""
        data = {"bulletins": [{"bulletinID": "a"}]}
        assert normalise_slf_response(data) == [{"bulletinID": "a"}]

    def test_list_of_collection_objects(self) -> None:
        """A list of collection objects is flattened."""
        data = [
            {"bulletins": [{"bulletinID": "a"}]},
            {"bulletins": [{"bulletinID": "b"}, {"bulletinID": "c"}]},
        ]
        result = normalise_slf_response(data)
        assert len(result) == 3
        assert [b["bulletinID"] for b in result] == ["a", "b", "c"]

    def test_empty_list(self) -> None:
        """An empty list returns an empty list."""
        assert normalise_slf_response([]) == []

    def test_empty_dict(self) -> None:
        """A dict without 'bulletins' returns an empty list."""
        assert normalise_slf_response({}) == []

    def test_none_returns_empty(self) -> None:
        """None returns an empty list."""
        assert normalise_slf_response(None) == []

    def test_string_returns_empty(self) -> None:
        """An unexpected string returns an empty list."""
        assert normalise_slf_response("unexpected") == []


# ---------------------------------------------------------------------------
# parse_dt
# ---------------------------------------------------------------------------


class TestParseDt:
    """Tests for parse_dt."""

    def test_utc_timestamp(self) -> None:
        """Parses a Z-suffixed ISO-8601 string to a UTC-aware datetime."""
        result = parse_dt("2025-03-15T08:00:00Z")
        assert result == datetime(2025, 3, 15, 8, 0, 0, tzinfo=UTC)

    def test_offset_timestamp_is_converted_to_utc(self) -> None:
        """An ISO-8601 string with a +01:00 offset is converted to UTC."""
        result = parse_dt("2025-03-15T09:00:00+01:00")
        assert result == datetime(2025, 3, 15, 8, 0, 0, tzinfo=UTC)
        assert result.tzinfo is UTC

    def test_negative_offset_timestamp_is_converted_to_utc(self) -> None:
        """An ISO-8601 string with a -05:00 offset is converted to UTC."""
        result = parse_dt("2025-03-15T03:00:00-05:00")
        assert result == datetime(2025, 3, 15, 8, 0, 0, tzinfo=UTC)
        assert result.tzinfo is UTC

    def test_naive_timestamp_is_assumed_utc(self) -> None:
        """A naive ISO-8601 string is assumed to be UTC."""
        result = parse_dt("2025-03-15T08:00:00")
        assert result == datetime(2025, 3, 15, 8, 0, 0, tzinfo=UTC)
        assert result.tzinfo is UTC


# ---------------------------------------------------------------------------
# resolve_issued_at
# ---------------------------------------------------------------------------


class TestResolveIssuedAt:
    """Tests for resolve_issued_at."""

    def test_uses_publication_time_when_present(self) -> None:
        """Modern bulletins return ``publicationTime`` parsed to UTC."""
        raw = {
            "publicationTime": "2025-03-15T08:00:00Z",
            "validTime": {
                "startTime": "2025-03-15T17:00:00Z",
                "endTime": "2025-03-16T17:00:00Z",
            },
        }
        assert resolve_issued_at(raw) == datetime(2025, 3, 15, 8, 0, 0, tzinfo=UTC)

    def test_falls_back_to_valid_time_start_when_missing(self) -> None:
        """Pre-2024 bulletins lack publicationTime; fall back to validTime.startTime."""
        raw = {
            "validTime": {
                "startTime": "2023-12-13T16:00:00Z",
                "endTime": "2023-12-14T16:00:00Z",
            },
        }
        assert resolve_issued_at(raw) == datetime(2023, 12, 13, 16, 0, 0, tzinfo=UTC)

    def test_falls_back_when_publication_time_is_empty_string(self) -> None:
        """An empty publicationTime string is treated the same as missing."""
        raw = {
            "publicationTime": "",
            "validTime": {
                "startTime": "2023-12-13T16:00:00Z",
                "endTime": "2023-12-14T16:00:00Z",
            },
        }
        assert resolve_issued_at(raw) == datetime(2023, 12, 13, 16, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# slf_pdf_url
# ---------------------------------------------------------------------------


class TestSlfPdfUrl:
    """Tests for slf_pdf_url URL derivation (pure, no DB)."""

    def test_afternoon_issue_produces_1700_url(self) -> None:
        """A bulletin published at 16:00 UTC (17:00 CET) uses the 17-00 slot."""
        raw = _make_raw_bulletin(publication_time="2026-03-15T16:00:00Z", lang="en")
        url = slf_pdf_url(raw)
        assert url == (
            "https://www.slf.ch/fileadmin/avalanche_bulletin/pdf/"
            "2026/03/Bulletin_2026-03-15_17-00_en.pdf"
        )

    def test_morning_update_produces_0800_url(self) -> None:
        """A bulletin published at 07:00 UTC (08:00 CET) uses the 08-00 slot."""
        raw = _make_raw_bulletin(publication_time="2026-03-15T07:00:00Z", lang="en")
        url = slf_pdf_url(raw)
        assert url == (
            "https://www.slf.ch/fileadmin/avalanche_bulletin/pdf/"
            "2026/03/Bulletin_2026-03-15_08-00_en.pdf"
        )

    def test_hour_exactly_12_uses_1700(self) -> None:
        """UTC hour == 12 is >= 12, so it maps to the 17-00 slot."""
        raw = _make_raw_bulletin(publication_time="2026-03-15T12:00:00Z", lang="en")
        url = slf_pdf_url(raw)
        assert "_17-00_" in url

    def test_hour_11_uses_0800(self) -> None:
        """UTC hour == 11 is < 12, so it maps to the 08-00 slot."""
        raw = _make_raw_bulletin(publication_time="2026-03-15T11:59:59Z", lang="en")
        url = slf_pdf_url(raw)
        assert "_08-00_" in url

    def test_german_language_variant(self) -> None:
        """Language is taken from raw['lang'] and embedded in the filename."""
        raw = _make_raw_bulletin(publication_time="2026-03-15T16:00:00Z", lang="de")
        url = slf_pdf_url(raw)
        assert url.endswith("_17-00_de.pdf")

    def test_folder_uses_issue_date(self) -> None:
        """The year/month folder is derived from the issue date, not validity."""
        raw = _make_raw_bulletin(publication_time="2025-12-31T16:00:00Z", lang="en")
        url = slf_pdf_url(raw)
        # Folder must be 2025/12, not 2026/01
        assert "/2025/12/" in url


# ---------------------------------------------------------------------------
# detect_caaml_shape — SNOW-900
# ---------------------------------------------------------------------------
# Unit tests against fixture dicts, never the network. The one exception is
# ``test_the_committed_sentinels_are_aggregated``, which reads the three real
# SLF sentinels off disk: a synthetic "many regions" fixture proves only that
# the arithmetic works, whereas the sentinels prove the classifier answers
# correctly for the payloads we actually ingest today.


def _per_region_bulletin(
    index: int,
    **overrides: Any,
) -> dict[str, Any]:
    """
    Build a raw bulletin in SLF's 2026/27 one-region-per-bulletin shape.

    Args:
        index: Distinguishes bulletins within a page; used for both the
            bulletin ID and the region ID.
        **overrides: Additional keys merged into the bulletin dict.

    Returns:
        A raw bulletin dict carrying exactly one region.

    """
    return _make_raw_bulletin(
        bulletin_id=f"v5-{index:03d}",
        regions=[{"regionID": f"CH-{4000 + index}", "name": f"Region {index}"}],
        **overrides,
    )


class TestDetectCaamlShape:
    """Which CAAML export a page of bulletins actually is."""

    def test_an_empty_page_is_unknown(self) -> None:
        """No bulletins is no evidence, not a vote for today's shape."""
        detected = detect_caaml_shape([])

        assert detected.shape == CAAML_SHAPE_UNKNOWN
        assert detected.bulletin_count == 0
        assert detected.region_count == 0

    def test_a_multi_region_bulletin_is_aggregated(self) -> None:
        """One bulletin covering two regions is proof of aggregation."""
        detected = detect_caaml_shape([_make_raw_bulletin()])

        assert detected.shape == CAAML_SHAPE_AGGREGATED
        assert detected.max_regions_per_bulletin == 2

    def test_the_committed_sentinels_are_aggregated(self) -> None:
        """The real payloads we ingest today classify as aggregated.

        The three SLF sentinels carry 93, 65 and 13 regions, so each one
        on its own is enough — which is the margin the page-size
        threshold below is calibrated against.
        """
        sentinel_dir = Path(__file__).resolve().parents[2] / "sentinels" / "slf"
        sources = sorted(sentinel_dir.glob("*/source.json"))
        assert len(sources) == 3, "expected three committed SLF sentinels"

        for source in sources:
            bulletin = json.loads(source.read_text())
            detected = detect_caaml_shape([bulletin])

            assert detected.shape == CAAML_SHAPE_AGGREGATED, source.parent.name
            assert detected.max_regions_per_bulletin > 1

    def test_a_full_de_aggregated_page_is_per_region(self) -> None:
        """A page of single-region bulletins is the new export.

        Sized as SLF's sample is — 133 bulletins of one region each —
        rather than at the threshold, so the test says what the real
        payload does and not merely where the boundary sits.
        """
        page = [_per_region_bulletin(i) for i in range(133)]

        detected = detect_caaml_shape(page)

        assert detected.shape == CAAML_SHAPE_PER_REGION
        assert detected.bulletin_count == 133
        assert detected.region_count == 133
        assert detected.max_regions_per_bulletin == 1

    def test_a_short_page_without_a_marker_is_unknown(self) -> None:
        """Three single-region bulletins could be either shape.

        A legacy page CAN hold a narrow bulletin, so below the threshold
        and with no marker field there is genuinely nothing to go on.
        Reporting ``unknown`` says so; guessing ``aggregated`` would be
        the silent 25× this detection exists to prevent.
        """
        page = [_per_region_bulletin(i) for i in range(3)]

        detected = detect_caaml_shape(page)

        assert detected.shape == CAAML_SHAPE_UNKNOWN
        assert detected.markers == ()

    def test_the_weather_marker_settles_a_short_page(self) -> None:
        """``customData.CH.weather`` is new-export-only, at any page size."""
        page = [_per_region_bulletin(i) for i in range(3)]
        page[1]["customData"] = {"CH": {"weather": [{"validTimePeriod": "all_day"}]}}

        detected = detect_caaml_shape(page)

        assert detected.shape == CAAML_SHAPE_PER_REGION
        assert detected.markers == ("customData.CH.weather",)

    def test_the_evolution_marker_settles_a_short_page(self) -> None:
        """``dangerRatingEvolution`` likewise, for the day SLF ship it."""
        page = [_per_region_bulletin(i) for i in range(3)]
        page[0]["dangerRatingEvolution"] = [{"date": "2026-09-10T00:00:00Z"}]

        detected = detect_caaml_shape(page)

        assert detected.shape == CAAML_SHAPE_PER_REGION
        assert detected.markers == ("dangerRatingEvolution",)

    def test_aggregation_outranks_a_marker(self) -> None:
        """Region count wins, because region count is what multiplies.

        A payload that is both aggregated and carries a new field is a
        shape nobody has described. The markers are still reported, so
        the log line says both things happened.
        """
        page = [_make_raw_bulletin(dangerRatingEvolution=[{"date": "2026-09-10"}])]

        detected = detect_caaml_shape(page)

        assert detected.shape == CAAML_SHAPE_AGGREGATED
        assert detected.markers == ("dangerRatingEvolution",)

    def test_a_malformed_entry_does_not_raise(self) -> None:
        """Classification must never be what fails a fetch.

        It runs only to describe the page; a bad entry is the real
        ingest's to reject, with its own error naming the bulletin.
        """
        page: list[Any] = [_per_region_bulletin(0), "not a bulletin", {"regions": None}]

        detected = detect_caaml_shape(page)

        assert detected.bulletin_count == 3
        assert detected.region_count == 1
