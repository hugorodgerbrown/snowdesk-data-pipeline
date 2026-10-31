"""
tests/ingest/test_runs.py — Tests for apps.ingest.runs, the provider walks.

The walks never touch a database, so these run without one: each provider's
HTTP source is patched and the yielded items are read directly. The app-side
pipelines that consume them are tested in tests/bulletins/services/
(SNOW-1249).
"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from apps.ingest.normalise.fetcher_common import OUTCOME_FAILED, OUTCOME_SKIPPED
from apps.ingest.normalise.meteofrance.translator import (
    MeteoFranceDelegatedRegionError,
    MeteoFranceTranslationError,
)
from apps.ingest.runs import (
    OUTCOME_DELEGATED,
    OUTCOME_DUPLICATE,
    OUTCOME_SKIPPED_NEWER,
    PROVIDER_ALBINA,
    PROVIDER_SLF,
    FetchOutcome,
    FetchStats,
    NormalisedBulletin,
    SlfConflictingBulletinIdError,
    fetch_bulletins,
)

DAY = date(2026, 1, 15)


def _raw(bulletin_id: str, published: str) -> dict[str, Any]:
    """A minimal raw CAAML bulletin."""
    return {
        "bulletinID": bulletin_id,
        "publicationTime": published,
        "validTime": {"startTime": published, "endTime": published},
        "lang": "en",
    }


class TestNormalisedBulletin:
    """The record a walk yields."""

    def test_the_pdf_url_is_resolved_only_when_asked(self) -> None:
        """A bulletin never written never pays for its PDF lookup."""
        resolve = MagicMock(return_value="https://example/x.pdf")
        record = NormalisedBulletin("b1", PROVIDER_SLF, {}, resolve)
        resolve.assert_not_called()
        assert record.pdf_url() == "https://example/x.pdf"
        assert str(record) == "SLF b1"


class TestDispatch:
    """fetch_bulletins picks the provider's walk."""

    def test_an_unknown_provider_is_refused(self) -> None:
        """Only the three providers have a walk."""
        with pytest.raises(ValueError, match="Unknown provider"):
            fetch_bulletins("NOPE", DAY, DAY)


class TestSlfWalk:
    """iter_slf, through fetch_bulletins."""

    @patch("apps.ingest.sources.slf.fetch_bulletin_page")
    def test_yields_the_window_and_counts_pages(self, page: MagicMock) -> None:
        """Newer is an outcome, in-window a record, older stops the walk."""
        page.return_value = [
            _raw("new", "2026-01-16T08:00:00Z"),
            _raw("in", "2026-01-15T08:00:00Z"),
            _raw("old", "2026-01-14T08:00:00Z"),
        ]
        seen: list[str] = []
        stats = FetchStats()
        items = list(
            fetch_bulletins(
                PROVIDER_SLF,
                DAY,
                DAY,
                on_item=lambda raw: seen.append(raw["bulletinID"]),
                stats=stats,
            )
        )
        assert items[0] == FetchOutcome(OUTCOME_SKIPPED_NEWER)
        assert isinstance(items[1], NormalisedBulletin)
        assert items[1].bulletin_id == "in"
        assert len(items) == 2
        assert seen == ["new", "in", "old"]
        assert stats.requests == 1

    @patch("apps.ingest.sources.slf.PAGE_SIZE", 2)
    @patch("apps.ingest.sources.slf.fetch_bulletin_page")
    def test_an_identical_repeat_across_pages_is_a_duplicate(
        self, page: MagicMock
    ) -> None:
        """Paging overlap repeats a bulletin; the walk yields it once (SNOW-998).

        SLF publishing mid-walk shifts every offset by the new issue's
        size, so the next page starts with what the last one ended on.
        """
        first = _raw("a", "2026-01-15T09:00:00Z")
        second = _raw("b", "2026-01-15T08:00:00Z")
        page.side_effect = [[first, second], [dict(second)], []]

        items = list(fetch_bulletins(PROVIDER_SLF, DAY, DAY))

        assert [type(item).__name__ for item in items] == [
            "NormalisedBulletin",
            "NormalisedBulletin",
            "FetchOutcome",
        ]
        assert items[2] == FetchOutcome(OUTCOME_DUPLICATE)

    @patch("apps.ingest.sources.slf.fetch_bulletin_page")
    def test_a_conflicting_repeat_raises(self, page: MagicMock) -> None:
        """One id with two region sets means the id names nothing; stop."""
        zermatt = _raw("shared", "2026-01-15T08:00:00Z")
        zermatt["regions"] = [{"regionID": "CH-4222", "name": "Zermatt"}]
        saas = dict(zermatt, regions=[{"regionID": "CH-4223", "name": "Saas Fee"}])
        page.return_value = [zermatt, saas]

        walk = fetch_bulletins(PROVIDER_SLF, DAY, DAY)
        assert isinstance(next(walk), NormalisedBulletin)
        with pytest.raises(SlfConflictingBulletinIdError, match="shared"):
            next(walk)

    @patch("apps.ingest.sources.slf.fetch_bulletin_page")
    def test_key_order_is_not_a_conflict(self, page: MagicMock) -> None:
        """The fingerprint is of canonical JSON, so reordered keys still match."""
        original = _raw("a", "2026-01-15T08:00:00Z")
        reordered = dict(reversed(list(original.items())))
        page.return_value = [original, reordered]

        items = list(fetch_bulletins(PROVIDER_SLF, DAY, DAY))

        assert items[1] == FetchOutcome(OUTCOME_DUPLICATE)


class TestAlbinaWalk:
    """iter_albina, through fetch_bulletins."""

    @patch("apps.ingest.sources.albina.fetch_albina_for_date")
    def test_a_failed_slot_and_a_repeat_are_outcomes(self, fetch: MagicMock) -> None:
        """A slot's HTTP error fails the slot; a repeat is a duplicate."""
        bulletin = _raw("a1", "2026-01-15T16:00:00Z")
        fetch.side_effect = [
            requests.HTTPError("500"),
            [bulletin],
            [bulletin, {"bulletinID": ""}],
        ]
        stats = FetchStats()
        items = list(
            fetch_bulletins(
                PROVIDER_ALBINA, DAY, DAY, regions=("A", "B", "C"), stats=stats
            )
        )
        assert items[0] == FetchOutcome(OUTCOME_FAILED)
        assert isinstance(items[1], NormalisedBulletin)
        assert "region=B" in items[1].pdf_url()
        assert items[2:] == [
            FetchOutcome(OUTCOME_DUPLICATE),
            FetchOutcome(OUTCOME_SKIPPED),
        ]
        assert stats.requests == 3


class TestMeteofranceWalk:
    """iter_meteofrance, through fetch_bulletins."""

    @patch("apps.ingest.runs.parse_dpbra_xml")
    @patch("apps.ingest.sources.meteofrance.fetch_meteofrance_bulletin")
    def test_every_way_a_massif_can_end(
        self, fetch: MagicMock, parse: MagicMock
    ) -> None:
        """Failed fetch, 404, delegated, untranslatable, and a bulletin."""
        fetch.side_effect = [requests.HTTPError("500"), None, b"x", b"x", b"x"]
        parse.side_effect = [
            MeteoFranceDelegatedRegionError("Andorre"),
            MeteoFranceTranslationError("bad"),
            _raw("FR-01", "2026-01-15T16:00:00Z"),
        ]
        items = list(
            fetch_bulletins(
                "METEOFRANCE",
                DAY,
                DAY,
                massif_ids=(1, 2, 3, 4, 5),
                base_url="https://mf.example",
            )
        )
        assert items[:4] == [
            FetchOutcome(OUTCOME_FAILED),
            FetchOutcome(OUTCOME_SKIPPED),
            FetchOutcome(OUTCOME_DELEGATED),
            FetchOutcome(OUTCOME_FAILED),
        ]
        record = items[4]
        assert isinstance(record, NormalisedBulletin)
        assert record.bulletin_id == "FR-01"
        # No customData.MF.massif, so no archive lookup and no URL.
        assert record.pdf_url() == ""
