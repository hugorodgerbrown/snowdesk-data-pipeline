"""
tests/pipeline/test_dev_mirror.py — Tests for the dev-only SLF mirror view.

The mirror replays ``sample_data/slf_archive.ndjson`` with the same
limit/offset paging contract as the upstream SLF API. These tests
exercise it via the Django test client (DEBUG is True under
config.settings.development, so the URL is mounted).
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from django.test import Client, override_settings

from pipeline.services.slf_archive import write_archive


def _record(bulletin_id: str, publication_time: str) -> dict[str, Any]:
    """Build a minimal CAAML record for mirror tests."""
    return {
        "bulletinID": bulletin_id,
        "publicationTime": publication_time,
        "validTime": {
            "startTime": publication_time,
            "endTime": publication_time,
        },
        "lang": "en",
    }


def _make_archive(path: Path, count: int) -> list[dict[str, Any]]:
    """
    Populate the archive with ``count`` records.

    Records have monotonically increasing publicationTime so the mirror's
    descending sort is exercised. Returns the records in the order the
    mirror should serve them (descending publicationTime).

    """
    base = datetime(2025, 11, 1, 8, 0, 0)
    records = [
        _record(f"b{i:03d}", (base + timedelta(days=i)).isoformat() + "Z")
        for i in range(count)
    ]
    write_archive(path, records)
    return list(reversed(records))


@pytest.mark.django_db
class TestSlfMirror:
    """Tests for the slf_mirror view."""

    def test_returns_first_page_descending_by_publication_time(
        self, tmp_path: Path
    ) -> None:
        """Default limit serves the newest records first."""
        archive_path = tmp_path / "archive.ndjson"
        descending = _make_archive(archive_path, count=75)

        with override_settings(SLF_ARCHIVE_PATH=archive_path):
            response = Client().get(
                "/dev/slf-mirror/api/bulletin-list/caaml/en/json",
                {"limit": 50, "offset": 0},
            )

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 50
        assert [r["bulletinID"] for r in body] == [
            r["bulletinID"] for r in descending[:50]
        ]

    def test_offset_serves_subsequent_page(self, tmp_path: Path) -> None:
        """offset=50 returns the remaining 25 records of a 75-record archive."""
        archive_path = tmp_path / "archive.ndjson"
        descending = _make_archive(archive_path, count=75)

        with override_settings(SLF_ARCHIVE_PATH=archive_path):
            response = Client().get(
                "/dev/slf-mirror/api/bulletin-list/caaml/en/json",
                {"limit": 50, "offset": 50},
            )

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 25
        assert [r["bulletinID"] for r in body] == [
            r["bulletinID"] for r in descending[50:]
        ]

    def test_fewer_than_limit_signals_last_page(self, tmp_path: Path) -> None:
        """Mirror returns fewer than ``limit`` records on the last page.

        ``run_pipeline`` relies on this signal to terminate its fetch
        loop (see data_fetcher.py: 'fewer results than requested means
        last page').
        """
        archive_path = tmp_path / "archive.ndjson"
        _make_archive(archive_path, count=10)

        with override_settings(SLF_ARCHIVE_PATH=archive_path):
            response = Client().get(
                "/dev/slf-mirror/api/bulletin-list/caaml/en/json",
                {"limit": 50, "offset": 0},
            )

        assert response.status_code == 200
        assert len(response.json()) == 10  # < 50 → fetch loop terminates.

    def test_empty_archive_returns_empty_list(self, tmp_path: Path) -> None:
        """A missing or empty archive returns ``[]``, not a 500."""
        archive_path = tmp_path / "missing.ndjson"

        with override_settings(SLF_ARCHIVE_PATH=archive_path):
            response = Client().get(
                "/dev/slf-mirror/api/bulletin-list/caaml/en/json",
            )

        assert response.status_code == 200
        assert response.json() == []

    def test_lang_is_accepted_but_ignored(self, tmp_path: Path) -> None:
        """The lang URL segment is parity-only; any value is accepted."""
        archive_path = tmp_path / "archive.ndjson"
        _make_archive(archive_path, count=3)

        with override_settings(SLF_ARCHIVE_PATH=archive_path):
            for lang in ("en", "de", "fr", "it"):
                response = Client().get(
                    f"/dev/slf-mirror/api/bulletin-list/caaml/{lang}/json",
                )
                assert response.status_code == 200
                assert len(response.json()) == 3

    def test_invalid_limit_returns_400(self, tmp_path: Path) -> None:
        """Non-integer ?limit / ?offset surface as 400 rather than 500."""
        archive_path = tmp_path / "archive.ndjson"
        _make_archive(archive_path, count=3)

        with override_settings(SLF_ARCHIVE_PATH=archive_path):
            response = Client().get(
                "/dev/slf-mirror/api/bulletin-list/caaml/en/json",
                {"limit": "not-a-number"},
            )

        assert response.status_code == 400
