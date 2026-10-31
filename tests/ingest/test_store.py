"""
tests/ingest/test_store.py — Tests for store_bulletin (SNOW-1250).

A new id is created, a changed hash updates, the same hash writes nothing —
not even ``updated_at`` — and the PDF link is resolved only when a row is
written, or when ``force`` asks for it. Every write builds the render model
(SNOW-1270), a stale one is rebuilt on an unchanged fetch, and
``rebuild_render_model`` rebuilds one row on demand.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from apps.ingest.models import SourceBulletin, content_hash
from apps.ingest.normalise.render_model import RENDER_MODEL_VERSION
from apps.ingest.runs import NormalisedBulletin
from apps.ingest.store import (
    OUTCOME_CREATED,
    OUTCOME_UNCHANGED,
    OUTCOME_UPDATED,
    REBUILD_FAILED,
    REBUILT,
    rebuild_render_model,
    store_bulletin,
)
from tests.factories import SourceBulletinFactory

pytestmark = pytest.mark.django_db


def _record(
    caaml: dict, pdf: str = "https://x/a.pdf"
) -> tuple[NormalisedBulletin, MagicMock]:
    """A walk's record, and its PDF lookup as a mock so calls can be counted."""
    lookup = MagicMock(return_value=pdf)
    return NormalisedBulletin("a", "SLF", caaml, lookup), lookup


def _rec(caaml: dict, pdf: str = "https://x/a.pdf") -> NormalisedBulletin:
    """Just the record, for a call whose lookup count does not matter."""
    return _record(caaml, pdf)[0]


def test_a_new_bulletin_is_created() -> None:
    """Every column is written, the hash from the CAAML."""
    row, outcome = store_bulletin(_rec({"bulletinID": "a"}))
    assert outcome == OUTCOME_CREATED
    stored = SourceBulletin.objects.get()
    assert stored == row
    assert (stored.provider, stored.caaml, stored.pdf_url) == (
        "SLF",
        {"bulletinID": "a"},
        "https://x/a.pdf",
    )
    assert stored.content_hash == content_hash({"bulletinID": "a"})


def test_a_failed_rating_refresh_rolls_the_row_back() -> None:
    """Codex on #1267: the retry with the same hash refreshes the ratings."""
    with (
        patch(
            "apps.ingest.store.refresh_source_day_ratings",
            side_effect=RuntimeError("boom"),
        ),
        pytest.raises(RuntimeError),
    ):
        store_bulletin(_rec({"bulletinID": "a"}))
    assert not SourceBulletin.objects.exists()
    with patch("apps.ingest.store.refresh_source_day_ratings") as refresh:
        _, outcome = store_bulletin(_rec({"bulletinID": "a"}))
    assert outcome == OUTCOME_CREATED
    refresh.assert_called_once()


def test_the_same_content_writes_nothing() -> None:
    """Unchanged: no write, no PDF lookup, updated_at unmoved."""
    first, _ = store_bulletin(_rec({"bulletinID": "a"}))
    again, lookup = _record({"bulletinID": "a"})
    row, outcome = store_bulletin(again)
    assert outcome == OUTCOME_UNCHANGED
    lookup.assert_not_called()
    assert SourceBulletin.objects.get().updated_at == first.updated_at
    assert row.pk == first.pk


def test_changed_content_updates_the_row() -> None:
    """A new hash rewrites the CAAML, the link and fetched_at."""
    first, _ = store_bulletin(_rec({"bulletinID": "a"}))
    row, outcome = store_bulletin(_rec({"bulletinID": "a", "v": 2}, "https://x/b"))
    assert outcome == OUTCOME_UPDATED
    stored = SourceBulletin.objects.get()
    assert stored.caaml == {"bulletinID": "a", "v": 2}
    assert stored.pdf_url == "https://x/b"
    assert stored.fetched_at > first.fetched_at
    assert stored.updated_at > first.updated_at


def test_force_rewrites_a_moved_pdf_link_only() -> None:
    """force re-resolves the link; a moved one is written, a same one is not."""
    store_bulletin(_rec({"bulletinID": "a"}))
    same, lookup = _record({"bulletinID": "a"})
    assert store_bulletin(same, force=True)[1] == OUTCOME_UNCHANGED
    lookup.assert_called_once()
    _, outcome = store_bulletin(_rec({"bulletinID": "a"}, "https://x/new"), force=True)
    assert outcome == OUTCOME_UPDATED
    assert SourceBulletin.objects.get().pdf_url == "https://x/new"


# ---------------------------------------------------------------------------
# The render model (SNOW-1270)
# ---------------------------------------------------------------------------

# A stand-in builder output: the store keeps whatever the builder returns.
_MODEL = {"version": RENDER_MODEL_VERSION, "marker": "built"}


class TestRenderModel:
    """store_bulletin builds the model on every write; stale ones are rebuilt."""

    def test_a_new_bulletin_stores_its_render_model(self) -> None:
        """The created row carries the builder's output at the current version."""
        with patch("apps.ingest.store.build_render_model", return_value=_MODEL):
            row, _ = store_bulletin(_rec({"bulletinID": "a"}))
        row.refresh_from_db()
        assert row.render_model == _MODEL
        assert row.render_model_version == RENDER_MODEL_VERSION

    def test_a_failed_build_stores_the_sentinel(self) -> None:
        """No source marker: version 0 and the error, but the row is kept."""
        row, outcome = store_bulletin(_rec({"bulletinID": "a"}))
        assert outcome == OUTCOME_CREATED
        assert row.render_model_version == 0
        assert row.render_model["version"] == 0
        assert row.render_model["error_type"] == "RenderModelBuildError"

    def test_a_changed_bulletin_rebuilds_its_model(self) -> None:
        """An update writes the new CAAML's model with it."""
        store_bulletin(_rec({"bulletinID": "a"}))
        with patch("apps.ingest.store.build_render_model", return_value=_MODEL):
            row, _ = store_bulletin(_rec({"bulletinID": "a", "v": 2}))
        assert row.render_model == _MODEL

    def test_a_stale_model_is_rebuilt_on_an_unchanged_fetch(self) -> None:
        """Same CAAML, older builder: the model is rebuilt and the feed resends it."""
        with patch("apps.ingest.store.build_render_model", return_value=_MODEL):
            first, _ = store_bulletin(_rec({"bulletinID": "a"}))
        SourceBulletin.objects.filter(pk=first.pk).update(
            render_model={"version": 1}, render_model_version=1
        )
        with patch("apps.ingest.store.build_render_model", return_value=_MODEL):
            row, outcome = store_bulletin(_rec({"bulletinID": "a"}))
        assert outcome == OUTCOME_UPDATED
        assert row.render_model == _MODEL
        assert row.updated_at > first.updated_at

    def test_a_version_bump_adds_the_derived_block_to_an_unchanged_row(
        self,
    ) -> None:
        """SNOW-1341: a v9 row with the same CAAML gains ``derived`` on refetch."""
        source = Path(__file__).resolve().parents[1] / "sentinels"
        caaml = json.loads(
            (source / "slf" / "A-single-level" / "source.json").read_text()
        )
        first, _ = store_bulletin(_rec(caaml))
        assert "derived" in first.render_model
        old = {k: v for k, v in first.render_model.items() if k != "derived"}
        SourceBulletin.objects.filter(pk=first.pk).update(
            render_model={**old, "version": 9}, render_model_version=9
        )
        row, outcome = store_bulletin(_rec(caaml))
        assert outcome == OUTCOME_UPDATED
        row.refresh_from_db()
        assert row.render_model_version == RENDER_MODEL_VERSION
        assert row.render_model == first.render_model
        assert row.render_model["derived"]["danger_levels"]["levels"]

    def test_a_current_model_is_not_rebuilt(self) -> None:
        """Same CAAML, current builder: nothing is built or written."""
        caaml = {"bulletinID": "a", "validTime": {"startTime": "2026-01-02T07:00Z"}}
        with patch("apps.ingest.store.build_render_model", return_value=_MODEL):
            store_bulletin(_rec(caaml))
        with patch("apps.ingest.store.build_render_model") as build:
            _, outcome = store_bulletin(_rec(caaml))
        build.assert_not_called()
        assert outcome == OUTCOME_UNCHANGED

    def test_a_failure_that_still_fails_writes_nothing(self) -> None:
        """A build that keeps failing does not resend the row on every fetch."""
        first, _ = store_bulletin(_rec({"bulletinID": "a"}))
        row, outcome = store_bulletin(_rec({"bulletinID": "a"}))
        assert outcome == OUTCOME_UNCHANGED
        assert row.updated_at == first.updated_at


class TestRebuildRenderModel:
    """rebuild_render_model, one row on demand."""

    def test_a_stale_row_is_rebuilt(self) -> None:
        """A changed model is saved and reported."""
        row = SourceBulletinFactory.create()
        with patch("apps.ingest.store.build_render_model", return_value=_MODEL):
            assert rebuild_render_model(row) == REBUILT
        row.refresh_from_db()
        assert row.render_model == _MODEL

    def test_an_identical_rebuild_is_unchanged(self) -> None:
        """The same output writes nothing."""
        row = SourceBulletinFactory.create(
            render_model=_MODEL, render_model_version=RENDER_MODEL_VERSION
        )
        before = row.updated_at
        with patch("apps.ingest.store.build_render_model", return_value=_MODEL):
            assert rebuild_render_model(row) == OUTCOME_UNCHANGED
        row.refresh_from_db()
        assert row.updated_at == before

    def test_without_commit_nothing_is_saved(self) -> None:
        """The preview decides and leaves the row alone."""
        row = SourceBulletinFactory.create()
        with patch("apps.ingest.store.build_render_model", return_value=_MODEL):
            assert rebuild_render_model(row, commit=False) == REBUILT
        row.refresh_from_db()
        assert row.render_model == {}

    def test_a_failed_build_is_reported(self) -> None:
        """The sentinel is saved and the outcome says it failed."""
        row = SourceBulletinFactory.create()
        assert rebuild_render_model(row) == REBUILD_FAILED
        row.refresh_from_db()
        assert row.render_model_version == 0
        assert "error" in row.render_model

    def test_stale_render_model_selects_old_and_failed_rows(self) -> None:
        """The command's walk: old versions, failures and unindexed rows."""
        current = SourceBulletinFactory.create(
            render_model_version=RENDER_MODEL_VERSION, target_date=date(2026, 1, 2)
        )
        unindexed = SourceBulletinFactory.create(
            render_model_version=RENDER_MODEL_VERSION
        )
        old = SourceBulletinFactory.create(render_model_version=1)
        failed = SourceBulletinFactory.create(render_model_version=0)
        stale = set(SourceBulletin.objects.stale_render_model(RENDER_MODEL_VERSION))
        assert stale == {old, failed, unindexed}
        assert current not in stale
