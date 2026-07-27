"""
tests/bulletins/services/test_settled.py — Cover the settled-date boundary.

Exercises ``bulletins.services.settled.earliest_mutable_date`` /
``is_settled`` against a patched ``get_sources()`` registry (per the risk
note in the SNOW-526 plan: sparse fixture DBs make ``min()`` across real
sources drag the threshold to ``SEASON_START_DATE``, so these tests build a
fake registry rather than relying on seed data), plus an anti-drift test
that pins the function to the *real* registry against
``Command._default_start_date``.
"""

from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.utils import timezone

from bulletins.management.commands.fetch_bulletins import Command
from bulletins.services.settled import earliest_mutable_date, is_settled
from bulletins.services.slf_fetcher import BulletinSource, get_sources

_TODAY = date(2026, 4, 16)
_SEASON_START = date(2025, 11, 1)


def _stub_source(name: str, latest: date | None) -> BulletinSource:
    """Build a minimal ``BulletinSource`` whose ``latest_date_fn`` is fixed."""

    def _stash_writer(records: list[dict[str, Any]], path: Path) -> int:
        return 0

    return BulletinSource(
        name=name,
        pipeline_fn=lambda **kwargs: None,  # type: ignore[arg-type]
        latest_date_fn=lambda: latest,
        live_url_setting=f"{name}_API_BASE_URL",
        mirror_url_setting=f"{name}_API_LOCAL_MIRROR_URL",
        archive_path_setting=f"{name}_ARCHIVE_PATH",
        stash_writer=_stash_writer,
    )


def _patched_sources(
    **latest_by_name: date | None,
) -> Callable[[], dict[str, BulletinSource]]:
    """Return a ``get_sources``-shaped callable built from ``name: latest`` kwargs."""

    def _get_sources() -> dict[str, BulletinSource]:
        return {
            name: _stub_source(name, latest) for name, latest in latest_by_name.items()
        }

    return _get_sources


class TestEarliestMutableDate:
    """Cover ``earliest_mutable_date()`` across empty, populated, and mixed registries."""

    @override_settings(SEASON_START_DATE=_SEASON_START)
    @patch("bulletins.services.settled.timezone.localdate", return_value=_TODAY)
    def test_all_sources_empty_falls_back_to_season_start(
        self, mock_localdate: object
    ) -> None:
        """No source has a bulletin: the whole season is still mutable."""
        with patch(
            "bulletins.services.settled.get_sources",
            _patched_sources(SLF=None, ALBINA=None, METEOFRANCE=None),
        ):
            assert earliest_mutable_date() == _SEASON_START

    @patch("bulletins.services.settled.timezone.localdate", return_value=_TODAY)
    def test_takes_minimum_across_sources(self, mock_localdate: object) -> None:
        """The earliest of the per-source latest dates wins."""
        with patch(
            "bulletins.services.settled.get_sources",
            _patched_sources(
                SLF=date(2026, 4, 10),
                ALBINA=date(2026, 4, 5),
                METEOFRANCE=date(2026, 4, 12),
            ),
        ):
            assert earliest_mutable_date() == date(2026, 4, 5)

    @patch("bulletins.services.settled.timezone.localdate", return_value=_TODAY)
    def test_none_sources_contribute_no_constraint(
        self, mock_localdate: object
    ) -> None:
        """A source with no rows yet is ignored, not treated as the minimum."""
        with patch(
            "bulletins.services.settled.get_sources",
            _patched_sources(SLF=date(2026, 4, 10), ALBINA=None),
        ):
            assert earliest_mutable_date() == date(2026, 4, 10)

    @patch("bulletins.services.settled.timezone.localdate", return_value=_TODAY)
    def test_capped_at_today(self, mock_localdate: object) -> None:
        """A source's latest date in the future never pushes past today."""
        with patch(
            "bulletins.services.settled.get_sources",
            _patched_sources(SLF=_TODAY + timedelta(days=3)),
        ):
            assert earliest_mutable_date() == _TODAY


class TestIsSettled:
    """Cover ``is_settled()`` against a fixed threshold."""

    @patch("bulletins.services.settled.timezone.localdate", return_value=_TODAY)
    def test_date_before_threshold_is_settled(self, mock_localdate: object) -> None:
        """A day strictly before the threshold is settled."""
        with patch(
            "bulletins.services.settled.get_sources",
            _patched_sources(SLF=date(2026, 4, 10)),
        ):
            assert is_settled(date(2026, 4, 9)) is True

    @patch("bulletins.services.settled.timezone.localdate", return_value=_TODAY)
    def test_date_equal_to_threshold_is_not_settled(
        self, mock_localdate: object
    ) -> None:
        """The boundary date itself is still mutable (the regression that matters)."""
        with patch(
            "bulletins.services.settled.get_sources",
            _patched_sources(SLF=date(2026, 4, 10)),
        ):
            assert is_settled(date(2026, 4, 10)) is False

    @patch("bulletins.services.settled.timezone.localdate", return_value=_TODAY)
    def test_date_after_threshold_is_not_settled(self, mock_localdate: object) -> None:
        """A day after the threshold is not settled."""
        with patch(
            "bulletins.services.settled.get_sources",
            _patched_sources(SLF=date(2026, 4, 10)),
        ):
            assert is_settled(date(2026, 4, 11)) is False


@pytest.mark.django_db
def test_earliest_mutable_date_agrees_with_fetch_bulletins_default_start_date() -> None:
    """
    Anti-drift: the real registry must agree with the command's default start.

    ``earliest_mutable_date()`` is deliberately built on the same
    ``get_sources()`` registry and ``latest_date_fn()`` calls that
    ``Command._default_start_date`` uses to pick ``fetch_bulletins``'s
    default ``--start-date``. Compute the expected boundary independently
    (via the command's own helper) and assert the two agree — so a future
    change to the fetcher's default-start logic fails here rather than
    shipping a stale cache boundary.

    Runs against the real (unpatched) ``get_sources()`` — on an empty DB
    (a bare test run, no fixtures loaded) every source's ``latest_date_fn()``
    returns ``None``, so both sides independently fall back to
    ``min(settings.SEASON_START_DATE, today)`` and the assertion passes
    trivially, without the two code paths' ``get_sources()``/
    ``latest_date_fn()`` calls having actually been exercised against each
    other. The check only has real bite once at least one registered source
    has rows in the DB the test runs against (e.g. via seeded fixture data)
    — don't over-trust a green result here in isolation from that.
    """
    starts = [
        Command._default_start_date(source)[0] for source in get_sources().values()
    ]
    expected = min(min(starts), timezone.localdate())
    assert earliest_mutable_date() == expected
