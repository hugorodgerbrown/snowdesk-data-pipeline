"""
tests/bulletins/test_euregio_mirror.py — Tests for the EUREGIO dev mirror view.

The mirror replays ``bulletins/local_mirrors/euregio_archive.ndjson`` with the
same URL shape as the ALBINA CDN so that ``fetch_euregio_bulletins
--source local-mirror`` can run end-to-end without network access.

Tests use ``override_settings(EUREGIO_ARCHIVE_PATH=...)`` and a tmp_path
fixture to isolate archive contents.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from django.test import Client, override_settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bulletin(
    bulletin_id: str,
    main_date: str,
    region_ids: list[str],
) -> dict[str, Any]:
    """Build a minimal raw EUREGIO bulletin dict for archive use."""
    return {
        "bulletinID": bulletin_id,
        "publicationTime": f"{main_date}T18:00:00Z",
        "validTime": {
            "startTime": f"{main_date}T16:00:00Z",
            "endTime": f"{main_date}T16:00:00+01:00",
        },
        "lang": "en",
        "customData": {"ALBINA": {"mainDate": main_date}},
        "dangerRatings": [],
        "avalancheProblems": [],
        "regions": [{"regionID": rid, "name": rid} for rid in region_ids],
    }


def _write_archive(path: Path, bulletins: list[dict[str, Any]]) -> None:
    """Write bulletins as NDJSON to ``path``."""
    with path.open("w", encoding="utf-8") as fh:
        for b in bulletins:
            fh.write(json.dumps(b) + "\n")


def _url(date_str: str, region: str) -> str:
    """Build the EUREGIO mirror URL for a (date, region) pair."""
    return f"/dev/euregio-mirror/{date_str}/{date_str}_{region}_en_CAAMLv6.json"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEuregioMirror:
    """Tests for the euregio_mirror dev view."""

    def test_returns_matching_bulletins(self, tmp_path: Path) -> None:
        """Bulletins whose mainDate and region match the URL are returned."""
        archive_path = tmp_path / "euregio.ndjson"
        b1 = _make_bulletin("id-1", "2026-01-15", ["AT-07-01", "AT-07-02"])
        b2 = _make_bulletin("id-2", "2026-01-15", ["IT-32-BZ-01"])
        b3 = _make_bulletin("id-3", "2026-01-16", ["AT-07-01"])  # different date
        _write_archive(archive_path, [b1, b2, b3])

        with override_settings(EUREGIO_ARCHIVE_PATH=archive_path):
            response = Client().get(_url("2026-01-15", "AT-07"))

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["bulletinID"] == "id-1"

    def test_returns_empty_list_when_no_match(self, tmp_path: Path) -> None:
        """An empty JSON array is returned when no bulletins match."""
        archive_path = tmp_path / "euregio.ndjson"
        b1 = _make_bulletin("id-1", "2026-01-15", ["AT-07-01"])
        _write_archive(archive_path, [b1])

        with override_settings(EUREGIO_ARCHIVE_PATH=archive_path):
            response = Client().get(_url("2026-01-20", "AT-07"))

        assert response.status_code == 200
        assert response.json() == []

    def test_returns_400_on_invalid_date(self, tmp_path: Path) -> None:
        r"""A date that passes the regex but fails ISO parsing returns 400.

        The URL regex ``\d{4}-\d{2}-\d{2}`` accepts syntactically
        date-like strings (e.g. ``2026-13-01``) that are not valid
        calendar dates. The view validates the date and returns 400 for
        such inputs.
        """
        archive_path = tmp_path / "euregio.ndjson"
        _write_archive(archive_path, [])

        with override_settings(EUREGIO_ARCHIVE_PATH=archive_path):
            # Month 13 is syntactically a date-string but semantically invalid.
            response = Client().get(
                "/dev/euregio-mirror/2026-13-01/2026-13-01_AT-07_en_CAAMLv6.json"
            )

        assert response.status_code == 400

    def test_multiple_regions_matching_prefix(self, tmp_path: Path) -> None:
        """A bulletin covering multiple sub-regions of the requested prefix is returned."""
        archive_path = tmp_path / "euregio.ndjson"
        b1 = _make_bulletin(
            "id-1", "2026-01-15", ["IT-32-BZ-01", "IT-32-BZ-02", "IT-32-TN-01"]
        )
        _write_archive(archive_path, [b1])

        with override_settings(EUREGIO_ARCHIVE_PATH=archive_path):
            response = Client().get(_url("2026-01-15", "IT-32-BZ"))

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["bulletinID"] == "id-1"

    def test_region_prefix_does_not_match_different_region(
        self, tmp_path: Path
    ) -> None:
        """A bulletin for IT-32-TN is not returned when requesting AT-07."""
        archive_path = tmp_path / "euregio.ndjson"
        b1 = _make_bulletin("id-1", "2026-01-15", ["IT-32-TN-01"])
        _write_archive(archive_path, [b1])

        with override_settings(EUREGIO_ARCHIVE_PATH=archive_path):
            response = Client().get(_url("2026-01-15", "AT-07"))

        assert response.status_code == 200
        assert response.json() == []

    def test_empty_archive_returns_empty_list(self, tmp_path: Path) -> None:
        """An empty archive returns an empty JSON array."""
        archive_path = tmp_path / "euregio.ndjson"
        _write_archive(archive_path, [])

        with override_settings(EUREGIO_ARCHIVE_PATH=archive_path):
            response = Client().get(_url("2026-01-15", "AT-07"))

        assert response.status_code == 200
        assert response.json() == []
