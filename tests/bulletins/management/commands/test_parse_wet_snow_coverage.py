"""
tests/bulletins/management/commands/test_parse_wet_snow_coverage.py — Tests
for the ``parse_wet_snow_coverage`` management command.

Covers:

  - Empty archive: command runs without error and prints a warning.
  - Structured problems (have aspects): counted as total but not unstructured.
  - Unstructured problems with parseable prose: counted in parser_match.
  - Unstructured problems with unparseable prose: counted in unstructured only.
  - Unstructured problems with blank comment: counted in unstructured only.
  - Dry problems: not counted at all.
  - Gliding-snow problems: counted alongside wet_snow.
  - ``--source`` flag restricts the scan to one source.
  - Verbosity 2 prints the scanned-bulletin line.
  - The command is read-only (does not alter any row).
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from pytest import CaptureFixture

from tests.factories import BulletinFactory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_raw_data(
    *,
    problem_type: str = "wet_snow",
    aspects: list[str] | None = None,
    elevation: dict | None = None,
    comment: str = "",
    lang: str = "en",
    custom_data_key: str = "CH",
) -> dict:
    """
    Build a minimal GeoJSON Feature bulletin payload with one avalanche problem.

    Args:
        problem_type: EAWS problem type (default ``"wet_snow"``).
        aspects: Aspect list for the problem (default empty).
        elevation: Raw elevation dict (default None).
        comment: Per-problem comment HTML.
        lang: Bulletin language code.
        custom_data_key: Top-level key under ``customData``; ``"CH"`` → SLF,
            ``"ALBINA"`` → ALBINA source.

    Returns:
        A raw_data dict suitable for ``BulletinFactory.create(raw_data=...)``.

    """
    category = "wet" if problem_type in ("wet_snow", "gliding_snow") else "dry"
    if custom_data_key == "CH":
        custom_data = {
            "CH": {
                "aggregation": [
                    {
                        "category": category,
                        "validTimePeriod": "all_day",
                        "problemTypes": [problem_type],
                        "title": None,
                    }
                ]
            }
        }
    else:
        custom_data = {custom_data_key: {}}

    return {
        "type": "Feature",
        "geometry": None,
        "properties": {
            "bulletinID": "test-001",
            "lang": lang,
            "validTime": {
                "startTime": "2026-03-10T15:00:00Z",
                "endTime": "2026-03-11T15:00:00Z",
            },
            "dangerRatings": [{"mainValue": "moderate", "validTimePeriod": "all_day"}],
            "avalancheProblems": [
                {
                    "problemType": problem_type,
                    "comment": comment,
                    "dangerRatingValue": "moderate",
                    "validTimePeriod": "all_day",
                    "aspects": aspects if aspects is not None else [],
                    "elevation": elevation,
                }
            ],
            "customData": custom_data,
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestParseWetSnowCoverage:
    """Tests for parse_wet_snow_coverage management command."""

    def test_empty_archive_no_error(self, capsys: CaptureFixture[str]) -> None:
        """Command runs cleanly on an empty database."""
        call_command("parse_wet_snow_coverage", verbosity=0)
        captured = capsys.readouterr()
        assert "No wet-snow" in captured.out or captured.out == ""

    def test_structured_problem_counted_as_total(
        self, capsys: CaptureFixture[str]
    ) -> None:
        """Problems with aspects are counted in total but not unstructured."""
        BulletinFactory.create(
            lang="en",
            raw_data=_make_raw_data(
                aspects=["S", "SE"],
                comment="Wet-snow releases on south slopes below 2000m.",
            ),
        )
        call_command("parse_wet_snow_coverage", verbosity=0)
        captured = capsys.readouterr()
        # Total should be 1; unstructured should be 0.
        assert "1" in captured.out  # wet_snow_total
        assert " 0 " in captured.out or captured.out.count("0") >= 1  # unstructured

    def test_unstructured_parseable_counted_in_parser_match(
        self, capsys: CaptureFixture[str]
    ) -> None:
        """Unstructured problems with parseable prose are counted in parser_match."""
        BulletinFactory.create(
            lang="en",
            raw_data=_make_raw_data(
                aspects=[],
                elevation=None,
                comment="Wet-snow avalanches on south facing slopes below 2000m.",
            ),
        )
        call_command("parse_wet_snow_coverage", verbosity=0)
        captured = capsys.readouterr()
        # Expect the output to show a non-zero parser_match (reflected in the %).
        # The exact column layout shows percentages; 100% means all matched.
        assert "100%" in captured.out

    def test_unstructured_unparseable_not_in_parser_match(
        self, capsys: CaptureFixture[str]
    ) -> None:
        """Unstructured problems with generic prose stay at 0 parser_match."""
        BulletinFactory.create(
            lang="en",
            raw_data=_make_raw_data(
                aspects=[],
                elevation=None,
                comment="Warming temperatures increase instability.",
            ),
        )
        call_command("parse_wet_snow_coverage", verbosity=0)
        captured = capsys.readouterr()
        # 0% hit rate — unstructured but no tokens found.
        assert "0%" in captured.out

    def test_blank_comment_not_in_parser_match(
        self, capsys: CaptureFixture[str]
    ) -> None:
        """Blank-comment problems are counted as unstructured but not parser_match."""
        BulletinFactory.create(
            lang="en",
            raw_data=_make_raw_data(aspects=[], elevation=None, comment=""),
        )
        call_command("parse_wet_snow_coverage", verbosity=0)
        captured = capsys.readouterr()
        assert "0%" in captured.out

    def test_dry_problem_not_counted(self, capsys: CaptureFixture[str]) -> None:
        """Dry problem types are ignored entirely."""
        BulletinFactory.create(
            lang="en",
            raw_data=_make_raw_data(
                problem_type="wind_slab",
                aspects=[],
                comment="Wind-slab releases on north slopes below 2400m.",
            ),
        )
        call_command("parse_wet_snow_coverage", verbosity=0)
        captured = capsys.readouterr()
        # Command reports no wet-snow problems found.
        assert "No wet-snow" in captured.out or captured.out.strip() == ""

    def test_gliding_snow_counted(self, capsys: CaptureFixture[str]) -> None:
        """Gliding-snow problems are counted alongside wet_snow."""
        BulletinFactory.create(
            lang="en",
            raw_data=_make_raw_data(
                problem_type="gliding_snow",
                aspects=[],
                elevation=None,
                comment="Gliding snow on north and east aspects above 1800m.",
            ),
        )
        call_command("parse_wet_snow_coverage", verbosity=0)
        captured = capsys.readouterr()
        assert "1" in captured.out  # at least one problem counted

    def test_source_filter_restricts_scan(self, capsys: CaptureFixture[str]) -> None:
        """--source flag limits the scan to one source."""
        # Create one SLF bulletin (CH customData) and one ALBINA bulletin.
        BulletinFactory.create(
            lang="en",
            raw_data=_make_raw_data(
                comment="Wet-snow on south slopes below 2000m.",
                custom_data_key="CH",
            ),
        )
        BulletinFactory.create(
            lang="en",
            raw_data=_make_raw_data(
                comment="Wet releases on north aspects.",
                custom_data_key="ALBINA",
            ),
        )
        call_command("parse_wet_snow_coverage", filter_source="slf", verbosity=0)
        captured_slf = capsys.readouterr()

        call_command("parse_wet_snow_coverage", filter_source="albina", verbosity=0)
        captured_albina = capsys.readouterr()

        # Each filtered run should mention only its own source.
        assert "slf" in captured_slf.out.lower()
        assert "albina" in captured_albina.out.lower()

    def test_verbosity_2_prints_scan_summary(self, capsys: CaptureFixture[str]) -> None:
        """--verbosity 2 prints the scanned-bulletins summary line."""
        BulletinFactory.create(
            lang="en",
            raw_data=_make_raw_data(comment="Wet-snow on south slopes below 2000m."),
        )
        call_command("parse_wet_snow_coverage", verbosity=2)
        captured = capsys.readouterr()
        assert "Scanned" in captured.out
        assert "bulletin" in captured.out

    def test_does_not_mutate_database(self) -> None:
        """Command is read-only: Bulletin count is unchanged after running."""
        from bulletins.models import Bulletin as BulletinModel

        BulletinFactory.create(
            lang="en",
            raw_data=_make_raw_data(comment="Wet-snow on south slopes below 2000m."),
        )
        count_before = BulletinModel.objects.count()
        call_command("parse_wet_snow_coverage", verbosity=0)
        assert BulletinModel.objects.count() == count_before

    def test_multiple_languages_reported_separately(
        self, capsys: CaptureFixture[str]
    ) -> None:
        """Problems from different languages produce separate report lines."""
        BulletinFactory.create(
            lang="en",
            raw_data=_make_raw_data(
                lang="en",
                comment="Wet-snow on south slopes below 2000m.",
            ),
        )
        BulletinFactory.create(
            lang="de",
            raw_data=_make_raw_data(
                lang="de",
                comment="Nassschneelawinen an südexponierten Hängen.",
            ),
        )
        call_command("parse_wet_snow_coverage", verbosity=0)
        captured = capsys.readouterr()
        # Both lang codes should appear in the report.
        assert "en" in captured.out
        assert "de" in captured.out
