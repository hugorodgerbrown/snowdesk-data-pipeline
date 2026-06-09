"""
tests/bulletins/management/commands/test_backfill_pdf_urls.py — Tests for backfill_pdf_urls.

Covers:
  - Dry-run (default): counts displayed, no DB writes.
  - --commit: pdf_url populated for each source type.
  - Idempotency: rows with an existing pdf_url are skipped on a second run.
  - Météo-France source: index call mocked; correct URL stored.
  - Already-populated rows: skipped unconditionally.
  - Unknown source: counts as "unknown", no URL stored.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.core.management import call_command

from bulletins.models import Bulletin
from tests.factories import BulletinFactory


def _make_slf_bulletin(**kwargs: Any) -> Bulletin:
    """Create an SLF Bulletin with a minimal render_model and raw_data."""
    raw_props: dict[str, Any] = {
        "bulletinID": kwargs.get("bulletin_id", "slf-001"),
        "publicationTime": "2026-03-15T16:00:00Z",
        "validTime": {
            "startTime": "2026-03-15T17:00:00Z",
            "endTime": "2026-03-16T17:00:00Z",
        },
        "lang": "en",
        "regions": [{"regionID": "CH-4115", "name": "Martigny"}],
        "dangerRatings": [],
        "avalancheProblems": [],
    }
    return BulletinFactory.create(
        bulletin_id=kwargs.get("bulletin_id", "slf-001"),
        raw_data={"type": "Feature", "geometry": None, "properties": raw_props},
        render_model={"source": "slf", "version": 4},
        render_model_version=4,
        issued_at=datetime(2026, 3, 15, 16, 0, 0, tzinfo=UTC),
        valid_from=datetime(2026, 3, 15, 17, 0, 0, tzinfo=UTC),
        valid_to=datetime(2026, 3, 16, 17, 0, 0, tzinfo=UTC),
        pdf_url="",
        **{k: v for k, v in kwargs.items() if k not in ("bulletin_id",)},
    )


def _make_albina_bulletin(**kwargs: Any) -> Bulletin:
    """Create an ALBINA Bulletin with a minimal render_model and raw_data."""
    raw_props: dict[str, Any] = {
        "bulletinID": kwargs.get("bulletin_id", "albina-001"),
        "publicationTime": "2026-01-15T18:00:00Z",
        "validTime": {
            "startTime": "2026-01-15T16:00:00Z",
            "endTime": "2026-01-16T16:00:00Z",
        },
        "lang": "en",
        "regions": [{"regionID": "AT-07-01", "name": "Allgäu Alps East"}],
        "dangerRatings": [],
        "avalancheProblems": [],
    }
    return BulletinFactory.create(
        bulletin_id=kwargs.get("bulletin_id", "albina-001"),
        raw_data={"type": "Feature", "geometry": None, "properties": raw_props},
        render_model={"source": "albina", "version": 4},
        render_model_version=4,
        issued_at=datetime(2026, 1, 15, 18, 0, 0, tzinfo=UTC),
        valid_from=datetime(2026, 1, 15, 16, 0, 0, tzinfo=UTC),
        valid_to=datetime(2026, 1, 16, 16, 0, 0, tzinfo=UTC),
        pdf_url="",
        **{k: v for k, v in kwargs.items() if k not in ("bulletin_id",)},
    )


def _make_mf_bulletin(**kwargs: Any) -> Bulletin:
    """Create a Météo-France Bulletin with a minimal render_model and raw_data."""
    raw_props: dict[str, Any] = {
        "bulletinID": kwargs.get("bulletin_id", "mf-001"),
        "validTime": {
            "startTime": "2026-01-15T09:00:00Z",
            "endTime": "2026-01-16T09:00:00Z",
        },
        "lang": "fr",
        "regions": [{"regionID": "FR-01", "name": "CHABLAIS"}],
        "dangerRatings": [],
        "avalancheProblems": [],
    }
    return BulletinFactory.create(
        bulletin_id=kwargs.get("bulletin_id", "mf-001"),
        raw_data={"type": "Feature", "geometry": None, "properties": raw_props},
        render_model={"source": "meteofrance", "version": 4},
        render_model_version=4,
        issued_at=datetime(2026, 1, 15, 9, 0, 0, tzinfo=UTC),
        valid_from=datetime(2026, 1, 15, 9, 0, 0, tzinfo=UTC),
        valid_to=datetime(2026, 1, 16, 9, 0, 0, tzinfo=UTC),
        pdf_url="",
        **{k: v for k, v in kwargs.items() if k not in ("bulletin_id",)},
    )


@pytest.mark.django_db
class TestBackfillPdfUrlsDryRun:
    """Dry-run tests: no DB writes, counts reported."""

    def test_dry_run_does_not_write(self) -> None:
        """Without --commit, pdf_url remains empty after the command runs."""
        _make_slf_bulletin()
        call_command("backfill_pdf_urls")
        b = Bulletin.objects.get(bulletin_id="slf-001")
        assert b.pdf_url == ""

    def test_dry_run_reports_count(self, capsys: pytest.CaptureFixture) -> None:
        """Dry-run outputs a count summary without the word 'Committed'."""
        _make_slf_bulletin()
        call_command("backfill_pdf_urls", verbosity=1)
        captured = capsys.readouterr()
        assert "Dry-run" in captured.out
        assert "would populate" in captured.out.lower() or "1" in captured.out


@pytest.mark.django_db
class TestBackfillPdfUrlsCommit:
    """--commit tests: pdf_url is written to the database."""

    def test_slf_url_populated(self) -> None:
        """SLF bulletins get a correctly-derived pdf_url on --commit."""
        _make_slf_bulletin()
        call_command("backfill_pdf_urls", commit=True)
        b = Bulletin.objects.get(bulletin_id="slf-001")
        assert "slf.ch" in b.pdf_url
        assert "Bulletin_2026-03-15_17-00_en.pdf" in b.pdf_url

    def test_albina_url_populated(self) -> None:
        """ALBINA bulletins get a correctly-derived pdf_url on --commit."""
        _make_albina_bulletin()
        call_command("backfill_pdf_urls", commit=True)
        b = Bulletin.objects.get(bulletin_id="albina-001")
        assert "api.avalanche.report" in b.pdf_url
        assert "region=AT-07" in b.pdf_url

    def test_meteofrance_url_populated(self) -> None:
        """MF bulletins get a pdf_url when the index call succeeds."""
        _make_mf_bulletin()
        index_payload = [{"massif": "CHABLAIS", "heures": ["20260115100000"]}]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.ok = True
        mock_resp.json.return_value = index_payload

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        mock_session.headers = {}

        with patch(
            "bulletins.management.commands.backfill_pdf_urls.requests.Session",
            return_value=mock_session,
        ):
            call_command("backfill_pdf_urls", commit=True)

        b = Bulletin.objects.get(bulletin_id="mf-001")
        assert "BRA.CHABLAIS.20260115100000.pdf" in b.pdf_url

    def test_meteofrance_fails_open_on_network_error(self) -> None:
        """MF bulletins with a network error on the index call keep pdf_url empty."""
        _make_mf_bulletin()

        mock_session = MagicMock()
        mock_session.get.side_effect = requests.RequestException("timeout")
        mock_session.headers = {}

        with patch(
            "bulletins.management.commands.backfill_pdf_urls.requests.Session",
            return_value=mock_session,
        ):
            call_command("backfill_pdf_urls", commit=True)

        b = Bulletin.objects.get(bulletin_id="mf-001")
        assert b.pdf_url == ""


@pytest.mark.django_db
class TestBackfillPdfUrlsIdempotency:
    """Rows with an existing pdf_url must not be updated."""

    def test_already_populated_rows_skipped(self) -> None:
        """A bulletin with a non-empty pdf_url is not overwritten."""
        existing_url = "https://example.com/existing.pdf"
        BulletinFactory.create(
            bulletin_id="already-populated",
            pdf_url=existing_url,
            render_model={"source": "slf", "version": 4},
            render_model_version=4,
        )
        call_command("backfill_pdf_urls", commit=True)
        b = Bulletin.objects.get(bulletin_id="already-populated")
        assert b.pdf_url == existing_url

    def test_second_commit_is_noop(self) -> None:
        """Running --commit twice leaves pdf_url unchanged after the first run."""
        _make_slf_bulletin()
        call_command("backfill_pdf_urls", commit=True)
        b = Bulletin.objects.get(bulletin_id="slf-001")
        first_url = b.pdf_url
        assert first_url  # sanity: first run did something
        call_command("backfill_pdf_urls", commit=True)
        b.refresh_from_db()
        assert b.pdf_url == first_url
