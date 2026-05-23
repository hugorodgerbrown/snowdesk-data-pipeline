"""
tests/bulletins/management/commands/test_reformat_mf_comments.py — Tests for
the reformat_mf_comments management command.

Covers:
  - Plain-text → HTML upgrade for all three comment fields (snowpack,
    avalancheActivity, tendency[0]).
  - Idempotency: row with HTML already present is skipped (no double-escape).
  - Mixed state: snowpack is plain text but tendency already has HTML —
    snowpack is upgraded, tendency is left alone.
  - Default mode (no --commit) is read-only and does not write.
  - --commit writes changes.
  - --bulletin-id restricts to one row.
  - Non-FR rows are never touched.
  - --skip-day-ratings suppresses the day-rating refresh call.
  - Render-model side-effect: render_model_version bumped and
    render_model.prose reflects new HTML after --commit.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from bulletins.models import Bulletin
from bulletins.services.render_model import RENDER_MODEL_VERSION
from tests.factories import BulletinFactory, MicroRegionFactory, RegionBulletinFactory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PLAIN_SNOWPACK = "Enneigement :\nBon enneigement au-dessus de 2000 m."
_PLAIN_ACTIVITY = "Activité : Rares avalanches spontanées.\n* Plaques résiduelles."
_PLAIN_TENDENCY = "Tendance : Stable."

_HTML_COMMENT = "<h2>Enneigement</h2><p>Bon enneigement.</p>"


def _fr_raw_data(
    snowpack_comment: str = _PLAIN_SNOWPACK,
    activity_comment: str = _PLAIN_ACTIVITY,
    tendency_comment: str = _PLAIN_TENDENCY,
    bulletin_id: str = "FR-11-2026-05-18",
) -> dict:
    """
    Return a minimal FR GeoJSON Feature envelope suitable for use as raw_data.

    All three prose-comment fields default to plain-text so the command would
    normally upgrade them.

    Args:
        snowpack_comment: Value for snowpackStructure.comment.
        activity_comment: Value for avalancheActivity.comment.
        tendency_comment: Value for tendency[0].comment.
        bulletin_id: The bulletin identifier (must start with 'FR').

    Returns:
        A dict matching the GeoJSON Feature shape stored in Bulletin.raw_data.

    """
    return {
        "type": "Feature",
        "geometry": None,
        "properties": {
            "bulletinID": bulletin_id,
            "lang": "fr",
            "dangerRatings": [{"mainValue": "low", "elevation": {}}],
            "avalancheProblems": [],
            "snowpackStructure": {"comment": snowpack_comment},
            "avalancheActivity": {
                "highlights": "MANTEAU NEIGEUX STABLE.",
                "comment": activity_comment,
            },
            "tendency": [
                {
                    "comment": tendency_comment,
                    "tendencyType": "steady",
                    "dangerRating": "low",
                }
            ],
            "regions": [{"regionID": "FR-11"}],
            "customData": {
                "MF": {
                    "massif": "BELLEDONNE",
                    "date": "2026-05-18",
                }
            },
        },
    }


def _make_fr_bulletin(
    bulletin_id: str = "FR-11-2026-05-18",
    snowpack_comment: str = _PLAIN_SNOWPACK,
    activity_comment: str = _PLAIN_ACTIVITY,
    tendency_comment: str = _PLAIN_TENDENCY,
    render_model_version: int = 0,
) -> Bulletin:
    """
    Create an FR Bulletin with configurable comment fields.

    Args:
        bulletin_id: Bulletin identifier (must start with 'FR').
        snowpack_comment: Value for snowpackStructure.comment.
        activity_comment: Value for avalancheActivity.comment.
        tendency_comment: Value for tendency[0].comment.
        render_model_version: Initial render_model_version.

    Returns:
        A persisted Bulletin instance.

    """
    return BulletinFactory.create(
        bulletin_id=bulletin_id,
        raw_data=_fr_raw_data(
            snowpack_comment=snowpack_comment,
            activity_comment=activity_comment,
            tendency_comment=tendency_comment,
            bulletin_id=bulletin_id,
        ),
        render_model_version=render_model_version,
        issued_at=datetime(2026, 5, 18, 14, 0, tzinfo=UTC),
        valid_from=datetime(2026, 5, 18, 5, 0, tzinfo=UTC),
        valid_to=datetime(2026, 5, 19, 5, 0, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# Plain-text → HTML upgrade
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReformatUpgradesAllThreeFields:
    """All three comment fields are upgraded from plain-text to HTML."""

    def test_snowpack_comment_becomes_html(self) -> None:
        """After --commit, snowpackStructure.comment contains HTML tags."""
        b = _make_fr_bulletin()

        call_command("reformat_mf_comments", commit=True, verbosity=0)

        b.refresh_from_db()
        snowpack = b.raw_data["properties"]["snowpackStructure"]["comment"]
        assert "<" in snowpack
        assert "<h2>" in snowpack or "<p>" in snowpack

    def test_activity_comment_becomes_html(self) -> None:
        """After --commit, avalancheActivity.comment contains HTML tags."""
        b = _make_fr_bulletin()

        call_command("reformat_mf_comments", commit=True, verbosity=0)

        b.refresh_from_db()
        activity = b.raw_data["properties"]["avalancheActivity"]["comment"]
        assert "<" in activity

    def test_tendency_comment_becomes_html(self) -> None:
        """After --commit, tendency[0].comment contains HTML tags."""
        b = _make_fr_bulletin()

        call_command("reformat_mf_comments", commit=True, verbosity=0)

        b.refresh_from_db()
        tendency_comment = b.raw_data["properties"]["tendency"][0]["comment"]
        assert "<" in tendency_comment


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReformatIdempotency:
    """A row already containing HTML is skipped without double-escaping."""

    def test_html_row_is_skipped(self) -> None:
        """A bulletin with HTML comments is not modified by a second run."""
        b = _make_fr_bulletin(
            snowpack_comment=_HTML_COMMENT,
            activity_comment=_HTML_COMMENT,
            tendency_comment=_HTML_COMMENT,
        )
        original_raw_data = b.raw_data

        call_command("reformat_mf_comments", commit=True, verbosity=0)

        b.refresh_from_db()
        assert b.raw_data == original_raw_data

    def test_html_row_not_double_escaped(self) -> None:
        """Re-running on an already-formatted bulletin does not double-escape."""
        # Run once to produce HTML.
        b = _make_fr_bulletin()
        call_command("reformat_mf_comments", commit=True, verbosity=0)
        b.refresh_from_db()
        after_first_run = b.raw_data

        # Run again — should be a no-op.
        call_command("reformat_mf_comments", commit=True, verbosity=0)
        b.refresh_from_db()

        assert b.raw_data == after_first_run
        # Specifically, no &lt; or &gt; should appear in the comment.
        snowpack = b.raw_data["properties"]["snowpackStructure"]["comment"]
        assert "&lt;" not in snowpack
        assert "&gt;" not in snowpack


# ---------------------------------------------------------------------------
# Mixed state
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReformatMixedState:
    """Partial upgrade: plain-text fields are upgraded, HTML fields are left alone."""

    def test_plain_snowpack_upgraded_html_tendency_left_alone(self) -> None:
        """Plain-text snowpack is reformatted; HTML tendency stays unchanged."""
        b = _make_fr_bulletin(
            snowpack_comment=_PLAIN_SNOWPACK,
            tendency_comment=_HTML_COMMENT,
        )

        call_command("reformat_mf_comments", commit=True, verbosity=0)

        b.refresh_from_db()
        snowpack = b.raw_data["properties"]["snowpackStructure"]["comment"]
        tendency = b.raw_data["properties"]["tendency"][0]["comment"]

        # Snowpack should now be HTML.
        assert "<" in snowpack
        # Tendency should be exactly the original HTML (unchanged).
        assert tendency == _HTML_COMMENT


# ---------------------------------------------------------------------------
# Read-only mode (no --commit)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReformatReadOnlyMode:
    """Without --commit, no database writes occur."""

    def test_default_run_leaves_raw_data_untouched(self) -> None:
        """Default (no --commit) run does not modify raw_data."""
        b = _make_fr_bulletin()
        original_raw_data = b.raw_data

        call_command("reformat_mf_comments", verbosity=0)

        b.refresh_from_db()
        assert b.raw_data == original_raw_data

    def test_default_run_prints_read_only_banner(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """The default run displays a [READ-ONLY] banner in stdout."""
        _make_fr_bulletin()

        call_command("reformat_mf_comments", verbosity=1)

        out = capsys.readouterr().out
        assert "[READ-ONLY]" in out
        assert "--commit to persist" in out


# ---------------------------------------------------------------------------
# --bulletin-id scoping
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReformatBulletinIdScoping:
    """--bulletin-id restricts changes to exactly one row."""

    def test_only_named_bulletin_is_reformatted(self) -> None:
        """Only the targeted bulletin is modified when --bulletin-id is given."""
        target = _make_fr_bulletin(bulletin_id="FR-01-2026-05-18")
        other = _make_fr_bulletin(bulletin_id="FR-02-2026-05-18")

        call_command(
            "reformat_mf_comments",
            bulletin_id="FR-01-2026-05-18",
            commit=True,
            verbosity=0,
        )

        target.refresh_from_db()
        other.refresh_from_db()

        # Target's snowpack should now be HTML.
        assert "<" in target.raw_data["properties"]["snowpackStructure"]["comment"]
        # Other bulletin's raw_data must be unchanged.
        assert (
            other.raw_data["properties"]["snowpackStructure"]["comment"]
            == _PLAIN_SNOWPACK
        )

    def test_raises_command_error_on_unknown_bulletin_id(self) -> None:
        """A non-existent bulletin_id raises CommandError."""
        with pytest.raises(CommandError, match="No bulletin found"):
            call_command(
                "reformat_mf_comments",
                bulletin_id="FR-99-9999-99-99",
                verbosity=0,
            )


# ---------------------------------------------------------------------------
# Non-FR rows are skipped
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReformatNonFrSkipped:
    """Bulletins whose bulletin_id does not start with 'FR' are never touched."""

    def test_slf_bulletin_not_considered(self) -> None:
        """An SLF bulletin with plain-text comments is ignored."""
        slf_bulletin = BulletinFactory.create(
            bulletin_id="CH-4115-2026-05-18",
            raw_data={
                "type": "Feature",
                "geometry": None,
                "properties": {
                    "bulletinID": "CH-4115-2026-05-18",
                    "snowpackStructure": {"comment": "Plain text comment."},
                    "avalancheProblems": [],
                    "dangerRatings": [],
                    "customData": {"CH": {}},
                },
            },
            issued_at=datetime(2026, 5, 18, 14, 0, tzinfo=UTC),
            valid_from=datetime(2026, 5, 18, 5, 0, tzinfo=UTC),
            valid_to=datetime(2026, 5, 19, 5, 0, tzinfo=UTC),
        )
        original_raw_data = slf_bulletin.raw_data

        call_command("reformat_mf_comments", commit=True, verbosity=0)

        slf_bulletin.refresh_from_db()
        assert slf_bulletin.raw_data == original_raw_data


# ---------------------------------------------------------------------------
# --skip-day-ratings
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReformatSkipDayRatings:
    """--skip-day-ratings suppresses the day-rating refresh step."""

    def test_skip_day_ratings_suppresses_recompute_call(self) -> None:
        """recompute_region_day is not called when --skip-day-ratings is passed."""
        region = MicroRegionFactory.create(region_id="FR-BELLEDONNE")
        b = _make_fr_bulletin()
        RegionBulletinFactory.create(bulletin=b, region=region)

        with patch(
            "bulletins.management.commands.reformat_mf_comments.recompute_region_day"
        ) as mock_recompute:
            call_command(
                "reformat_mf_comments",
                commit=True,
                skip_day_ratings=True,
                verbosity=0,
            )

        mock_recompute.assert_not_called()

    def test_without_skip_day_ratings_recompute_is_called(self) -> None:
        """recompute_region_day is called when --skip-day-ratings is absent."""
        region = MicroRegionFactory.create(region_id="FR-BELLEDONNE-DR")
        b = _make_fr_bulletin(bulletin_id="FR-11-2026-05-19")
        RegionBulletinFactory.create(bulletin=b, region=region)

        with patch(
            "bulletins.management.commands.reformat_mf_comments.recompute_region_day"
        ) as mock_recompute:
            call_command(
                "reformat_mf_comments",
                commit=True,
                verbosity=0,
            )

        mock_recompute.assert_called()


# ---------------------------------------------------------------------------
# Render-model side-effect
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReformatRenderModelSideEffect:
    """After --commit, render_model_version is bumped and prose reflects HTML."""

    def test_render_model_version_is_bumped(self) -> None:
        """render_model_version equals RENDER_MODEL_VERSION after --commit."""
        b = _make_fr_bulletin(render_model_version=0)

        call_command("reformat_mf_comments", commit=True, verbosity=0)

        b.refresh_from_db()
        assert b.render_model_version == RENDER_MODEL_VERSION

    def test_render_model_prose_snowpack_contains_html(self) -> None:
        """render_model.prose.snowpack_structure contains HTML after --commit."""
        b = _make_fr_bulletin(render_model_version=0)

        call_command("reformat_mf_comments", commit=True, verbosity=0)

        b.refresh_from_db()
        snowpack_prose = b.render_model.get("prose", {}).get("snowpack_structure", "")
        assert snowpack_prose is not None
        assert "<" in str(snowpack_prose)

    def test_unchanged_bulletin_version_not_modified(self) -> None:
        """A bulletin that needs no reformatting does not get its version bumped."""
        b = _make_fr_bulletin(
            snowpack_comment=_HTML_COMMENT,
            activity_comment=_HTML_COMMENT,
            tendency_comment=_HTML_COMMENT,
            render_model_version=RENDER_MODEL_VERSION,
        )

        call_command("reformat_mf_comments", commit=True, verbosity=0)

        b.refresh_from_db()
        # Version must remain at the current level — no unnecessary write.
        assert b.render_model_version == RENDER_MODEL_VERSION
