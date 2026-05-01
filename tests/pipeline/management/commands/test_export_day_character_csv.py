"""
tests/pipeline/management/commands/test_export_day_character_csv.py.

Covers ``export_day_character_csv``: read-only behaviour, header layout,
row content for each rule branch (stable / manageable / hard_to_read /
widespread / dangerous), and the ``--start-date`` / ``--end-date`` /
``--lang`` / ``--output`` flags.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

from tests.factories import BulletinFactory, RegionBulletinFactory, RegionFactory


def _problem(
    problem_type: str = "new_snow",
    aspects: list[str] | None = None,
    lower: int | None = None,
    upper: int | None = None,
) -> dict:
    """Build a minimal render_model problem dict (mirrors test_day_character)."""
    elevation: dict | None = None
    if lower is not None or upper is not None:
        elevation = {"lower": lower, "upper": upper, "treeline": False}
    return {
        "problem_type": problem_type,
        "aspects": aspects or [],
        "elevation": elevation,
        "time_period": "all_day",
        "comment_html": "",
        "core_zone_text": None,
        "danger_rating_value": None,
    }


def _render_model(
    danger_number: str = "1",
    danger_subdivision: str | None = None,
    problems: list | None = None,
) -> dict:
    """Build a minimal render_model dict with one ``dry / all_day`` trait."""
    return {
        "version": 3,
        "danger": {
            "key": "low",
            "number": danger_number,
            "subdivision": danger_subdivision,
        },
        "traits": [
            {
                "category": "dry",
                "time_period": "all_day",
                "problems": problems or [],
            }
        ],
    }


def _read_rows(output: str) -> list[dict[str, str]]:
    """Parse a captured stdout block as CSV ``DictReader`` rows."""
    return list(csv.DictReader(StringIO(output)))


@pytest.mark.django_db
class TestExportDayCharacterCsv:
    """Tests for the ``export_day_character_csv`` management command."""

    def test_empty_database_writes_header_only(self) -> None:
        """No bulletins → exactly the header row, no data rows."""
        out = StringIO()
        call_command("export_day_character_csv", stdout=out)

        rows = _read_rows(out.getvalue())
        assert rows == []
        # First line must be the header — sanity check on column order.
        first_line = out.getvalue().splitlines()[0]
        assert first_line.startswith("bulletin_id,valid_from,valid_until,lang,")
        assert "day_character" in first_line
        assert "min_lower_elevation" in first_line

    def test_stable_day_row_has_blank_elevation_and_zero_aspects(self) -> None:
        """An empty-traits bulletin is classified as ``stable``."""
        BulletinFactory.create(
            bulletin_id="b-stable",
            lang="de",
            issued_at=datetime(2026, 1, 10, 8, 0, tzinfo=UTC),
            valid_from=datetime(2026, 1, 10, 7, 0, tzinfo=UTC),
            valid_to=datetime(2026, 1, 10, 18, 0, tzinfo=UTC),
            render_model={"version": 3, "danger": {"number": "1"}, "traits": []},
        )

        out = StringIO()
        call_command("export_day_character_csv", stdout=out)
        (row,) = _read_rows(out.getvalue())

        assert row["bulletin_id"] == "b-stable"
        assert row["day_character"] == "stable"
        assert row["danger_number"] == "1"
        assert row["danger_subdivision"] == ""
        assert row["trait_count"] == "0"
        assert row["problem_count"] == "0"
        assert row["problem_types"] == ""
        assert row["has_persistent_weak_layers"] == "false"
        assert row["has_gliding_snow"] == "false"
        assert row["unique_aspects_count"] == "0"
        assert row["min_lower_elevation"] == ""
        assert row["day_character_explainer"]  # non-empty

    def test_each_rule_branch_emits_expected_key(self) -> None:
        """One bulletin per cascade branch — labels match compute_day_character."""
        # Rule 1: danger 4 → dangerous.
        BulletinFactory.create(
            bulletin_id="b-dangerous",
            issued_at=datetime(2026, 1, 11, 8, 0, tzinfo=UTC),
            valid_from=datetime(2026, 1, 11, 7, 0, tzinfo=UTC),
            valid_to=datetime(2026, 1, 11, 18, 0, tzinfo=UTC),
            render_model=_render_model("4", problems=[_problem("new_snow")]),
        )
        # Rule 2: danger 2 + persistent_weak_layers → hard_to_read.
        BulletinFactory.create(
            bulletin_id="b-hard",
            issued_at=datetime(2026, 1, 12, 8, 0, tzinfo=UTC),
            valid_from=datetime(2026, 1, 12, 7, 0, tzinfo=UTC),
            valid_to=datetime(2026, 1, 12, 18, 0, tzinfo=UTC),
            render_model=_render_model(
                "2",
                problems=[_problem("persistent_weak_layers", lower=2400)],
            ),
        )
        # Rule 3: danger 3 + low elevation → widespread.
        BulletinFactory.create(
            bulletin_id="b-widespread",
            issued_at=datetime(2026, 1, 13, 8, 0, tzinfo=UTC),
            valid_from=datetime(2026, 1, 13, 7, 0, tzinfo=UTC),
            valid_to=datetime(2026, 1, 13, 18, 0, tzinfo=UTC),
            render_model=_render_model(
                "3",
                problems=[_problem("new_snow", lower=1800, aspects=["N", "NE"])],
            ),
        )
        # Rule 4: danger 2 + new_snow → manageable.
        BulletinFactory.create(
            bulletin_id="b-manageable",
            issued_at=datetime(2026, 1, 14, 8, 0, tzinfo=UTC),
            valid_from=datetime(2026, 1, 14, 7, 0, tzinfo=UTC),
            valid_to=datetime(2026, 1, 14, 18, 0, tzinfo=UTC),
            render_model=_render_model(
                "2", problems=[_problem("new_snow", lower=2600, aspects=["N"])]
            ),
        )
        # Rule 5: danger 1 → stable.
        BulletinFactory.create(
            bulletin_id="b-stable-1",
            issued_at=datetime(2026, 1, 15, 8, 0, tzinfo=UTC),
            valid_from=datetime(2026, 1, 15, 7, 0, tzinfo=UTC),
            valid_to=datetime(2026, 1, 15, 18, 0, tzinfo=UTC),
            render_model=_render_model("1"),
        )

        out = StringIO()
        call_command("export_day_character_csv", stdout=out)
        rows = {r["bulletin_id"]: r for r in _read_rows(out.getvalue())}

        assert rows["b-dangerous"]["day_character"] == "dangerous"
        assert rows["b-hard"]["day_character"] == "hard_to_read"
        assert rows["b-hard"]["has_persistent_weak_layers"] == "true"
        assert rows["b-widespread"]["day_character"] == "widespread"
        assert rows["b-widespread"]["min_lower_elevation"] == "1800"
        assert rows["b-widespread"]["unique_aspects_count"] == "2"
        assert rows["b-manageable"]["day_character"] == "manageable"
        assert rows["b-stable-1"]["day_character"] == "stable"

    def test_widespread_subdivision_3_plus(self) -> None:
        """Danger ``3+`` triggers rule 3b even with narrow exposure."""
        BulletinFactory.create(
            bulletin_id="b-3plus",
            issued_at=datetime(2026, 2, 1, 8, 0, tzinfo=UTC),
            valid_from=datetime(2026, 2, 1, 7, 0, tzinfo=UTC),
            valid_to=datetime(2026, 2, 1, 18, 0, tzinfo=UTC),
            render_model=_render_model(
                "3",
                danger_subdivision="+",
                problems=[_problem("new_snow", lower=2600, aspects=["N"])],
            ),
        )

        out = StringIO()
        call_command("export_day_character_csv", stdout=out)
        (row,) = _read_rows(out.getvalue())

        assert row["day_character"] == "widespread"
        assert row["danger_subdivision"] == "+"

    def test_problem_types_column_is_sorted_and_deduped(self) -> None:
        """Multi-problem bulletin lists problem types sorted, deduped, ``;``-joined."""
        BulletinFactory.create(
            bulletin_id="b-multi",
            issued_at=datetime(2026, 2, 2, 8, 0, tzinfo=UTC),
            valid_from=datetime(2026, 2, 2, 7, 0, tzinfo=UTC),
            valid_to=datetime(2026, 2, 2, 18, 0, tzinfo=UTC),
            render_model=_render_model(
                "3",
                problems=[
                    _problem("wind_slab", lower=2400),
                    _problem("new_snow", lower=2200),
                    _problem("new_snow", lower=2100),
                ],
            ),
        )

        out = StringIO()
        call_command("export_day_character_csv", stdout=out)
        (row,) = _read_rows(out.getvalue())

        assert row["problem_types"] == "new_snow;wind_slab"
        assert row["problem_count"] == "3"
        assert row["min_lower_elevation"] == "2100"

    def test_region_ids_column_is_sorted_and_count_matches(self) -> None:
        """Linked regions render as a sorted ``;``-joined list with matching count."""
        bulletin = BulletinFactory.create(
            bulletin_id="b-regions",
            issued_at=datetime(2026, 3, 1, 8, 0, tzinfo=UTC),
            valid_from=datetime(2026, 3, 1, 7, 0, tzinfo=UTC),
            valid_to=datetime(2026, 3, 1, 18, 0, tzinfo=UTC),
            render_model=_render_model("1"),
        )
        region_b = RegionFactory.create(region_id="CH-2222")
        region_a = RegionFactory.create(region_id="CH-1111")
        RegionBulletinFactory.create(bulletin=bulletin, region=region_b)
        RegionBulletinFactory.create(bulletin=bulletin, region=region_a)

        out = StringIO()
        call_command("export_day_character_csv", stdout=out)
        (row,) = _read_rows(out.getvalue())

        assert row["region_ids"] == "CH-1111;CH-2222"
        assert row["regions_count"] == "2"

    def test_date_filters_restrict_rows(self) -> None:
        """``--start-date`` / ``--end-date`` filter on ``valid_from`` (date)."""
        for ix, day in enumerate((10, 15, 20)):
            BulletinFactory.create(
                bulletin_id=f"b-{ix}",
                issued_at=datetime(2026, 4, day, 8, 0, tzinfo=UTC),
                valid_from=datetime(2026, 4, day, 7, 0, tzinfo=UTC),
                valid_to=datetime(2026, 4, day, 18, 0, tzinfo=UTC),
                render_model=_render_model("1"),
            )

        out = StringIO()
        call_command(
            "export_day_character_csv",
            "--start-date",
            "2026-04-12",
            "--end-date",
            "2026-04-18",
            stdout=out,
        )
        rows = _read_rows(out.getvalue())

        assert {r["bulletin_id"] for r in rows} == {"b-1"}

    def test_lang_filter_restricts_rows(self) -> None:
        """``--lang`` matches ``Bulletin.lang`` exactly."""
        BulletinFactory.create(
            bulletin_id="b-de",
            lang="de",
            issued_at=datetime(2026, 4, 10, 8, 0, tzinfo=UTC),
            valid_from=datetime(2026, 4, 10, 7, 0, tzinfo=UTC),
            valid_to=datetime(2026, 4, 10, 18, 0, tzinfo=UTC),
            render_model=_render_model("1"),
        )
        BulletinFactory.create(
            bulletin_id="b-fr",
            lang="fr",
            issued_at=datetime(2026, 4, 10, 8, 0, tzinfo=UTC),
            valid_from=datetime(2026, 4, 10, 7, 0, tzinfo=UTC),
            valid_to=datetime(2026, 4, 10, 18, 0, tzinfo=UTC),
            render_model=_render_model("1"),
        )

        out = StringIO()
        call_command("export_day_character_csv", "--lang", "de", stdout=out)
        rows = _read_rows(out.getvalue())

        assert [r["bulletin_id"] for r in rows] == ["b-de"]

    def test_output_flag_writes_file_with_same_content(self, tmp_path: Path) -> None:
        """``--output PATH`` writes the same CSV that stdout emits."""
        BulletinFactory.create(
            bulletin_id="b-file",
            issued_at=datetime(2026, 4, 1, 8, 0, tzinfo=UTC),
            valid_from=datetime(2026, 4, 1, 7, 0, tzinfo=UTC),
            valid_to=datetime(2026, 4, 1, 18, 0, tzinfo=UTC),
            render_model=_render_model("2", problems=[_problem("new_snow")]),
        )

        stdout_buf = StringIO()
        call_command("export_day_character_csv", stdout=stdout_buf)

        out_path = tmp_path / "dc.csv"
        call_command("export_day_character_csv", "--output", str(out_path))

        assert out_path.read_text(encoding="utf-8") == stdout_buf.getvalue()

    def test_invalid_date_raises_command_error(self) -> None:
        """A non-ISO ``--start-date`` value raises ``CommandError``."""
        from django.core.management.base import CommandError

        with pytest.raises(CommandError, match="Invalid --start-date"):
            call_command(
                "export_day_character_csv",
                "--start-date",
                "not-a-date",
                stdout=StringIO(),
            )
