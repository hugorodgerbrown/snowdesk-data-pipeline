"""
tests/ingest/normalise/test_albina.py — Tests for apps.ingest.normalise.albina.

albina_pdf_url: URL derivation for ALBINA archive PDFs (region codes,
language, grayscale=false). Moved from
tests/bulletins/services/test_albina_fetcher.py with the code (SNOW-1248).
"""

from __future__ import annotations

from typing import Any

from apps.ingest.normalise.albina import albina_pdf_url


def _make_raw_bulletin(
    bulletin_id: str = "albina-001",
    publication_time: str = "2026-01-15T18:00:00Z",
    **overrides: Any,
) -> dict[str, Any]:
    """Build a minimal raw ALBINA bulletin dict."""
    base: dict[str, Any] = {
        "bulletinID": bulletin_id,
        "publicationTime": publication_time,
        "validTime": {
            "startTime": "2026-01-15T16:00:00Z",
            "endTime": "2026-01-16T16:00:00Z",
        },
        "lang": "en",
        "unscheduled": False,
        "regions": [{"regionID": "AT-07-01", "name": "Allgäu Alps East"}],
        "dangerRatings": [],
        "avalancheProblems": [],
        "customData": {"ALBINA": {"mainDate": "2026-01-15"}},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# albina_pdf_url
# ---------------------------------------------------------------------------


class TestAlbinaPdfUrl:
    """Tests for albina_pdf_url URL derivation (pure, no DB)."""

    def test_euregio_region_produces_correct_url(self) -> None:
        """EUREGIO region and English language produce the expected URL."""
        raw = _make_raw_bulletin(
            validTime={
                "startTime": "2025-11-30T16:00:00Z",
                "endTime": "2025-12-01T16:00:00Z",
            },
            lang="en",
        )
        url = albina_pdf_url(raw, "EUREGIO")
        assert "date=2025-11-30T16%3A00%3A00Z" in url
        assert "region=EUREGIO" in url
        assert "lang=en" in url
        assert "grayscale=false" in url
        assert url.startswith("https://api.avalanche.report/albina/api/bulletins/pdf?")

    def test_province_region_code(self) -> None:
        """Province codes (AT-07, IT-32-BZ, IT-32-TN) are passed through unchanged."""
        raw = _make_raw_bulletin(
            validTime={
                "startTime": "2025-11-30T16:00:00Z",
                "endTime": "2025-12-01T16:00:00Z",
            },
            lang="de",
        )
        url = albina_pdf_url(raw, "AT-07")
        assert "region=AT-07" in url
        assert "lang=de" in url

    def test_it_32_bz_region(self) -> None:
        """IT-32-BZ region code is URL-encoded correctly."""
        raw = _make_raw_bulletin(
            validTime={
                "startTime": "2026-01-10T16:00:00Z",
                "endTime": "2026-01-11T16:00:00Z",
            },
            lang="it",
        )
        url = albina_pdf_url(raw, "IT-32-BZ")
        assert "region=IT-32-BZ" in url
        assert "lang=it" in url

    def test_grayscale_always_false(self) -> None:
        """The grayscale parameter is always set to false."""
        raw = _make_raw_bulletin(
            validTime={
                "startTime": "2026-01-10T16:00:00Z",
                "endTime": "2026-01-11T16:00:00Z",
            },
        )
        url = albina_pdf_url(raw, "AT-07")
        assert "grayscale=false" in url
