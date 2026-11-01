"""Tests for bin/render-plan — the weekly 30-day plan renderer.

The renderer owns three decisions worth pinning: which plans it refuses
(schema validation), how it carries a plan forward (the embedded JSON and
the week-to-week diff), and that nothing a ticket title contains can break
out of the page (escaping). Layout is not asserted beyond the presence of
each panel; a shifted margin is not a defect a test should catch.
"""

from __future__ import annotations

import copy
import datetime as dt
import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RENDER_PLAN = REPO_ROOT / "bin" / "render-plan"


def _load() -> ModuleType:
    """Import the extensionless renderer as a module."""
    spec = importlib.util.spec_from_loader(
        "render_plan",
        importlib.machinery.SourceFileLoader("render_plan", str(RENDER_PLAN)),
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["render_plan"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


render_plan = _load()


def _item(ident: str, kind: str = "feature", **extra: Any) -> dict[str, Any]:
    """Build one planned ticket."""
    item = {
        "id": ident,
        "title": f"Title of {ident}",
        "kind": kind,
        "area": "Map",
        "status": "Ready for dev",
        "estimate": 2,
        "score": 10,
        "why": "Because.",
        "drivers": ["season"],
    }
    item.update(extra)
    return item


def _plan(
    weeks: list[list[dict[str, Any]]] | None = None, start: str = "2026-11-02"
) -> dict[str, Any]:
    """Build a minimal valid plan whose four weeks start a week apart."""
    weeks = weeks or [[_item("SNOW-1")], [_item("SNOW-2", "bug")], [], []]
    return {
        "generated_on": start,
        "window": {"start": start, "end": start},
        "summary": "A month for the season.",
        "capacity": {
            "done_42d": 250,
            "unplanned_share": 0.6,
            "planned_per_30d": 70,
            "reserve_pct": 20,
            "note": "Measured.",
        },
        "weeks": [
            {
                "label": f"Week {i + 1}",
                "start": (
                    dt.date.fromisoformat(start) + dt.timedelta(weeks=i)
                ).isoformat(),
                "theme": "T",
                "items": w,
            }
            for i, w in enumerate(weeks)
        ],
        "decisions": [],
        "new_tickets": [],
        "linear_changes": [],
        "signals": [],
        "not_planned": [],
    }


class TestValidate:
    """Schema checks: a malformed plan is refused, not half-rendered."""

    def test_valid_plan_has_no_problems(self) -> None:
        """The minimal plan passes."""
        assert render_plan.validate(_plan()) == []

    def test_missing_key_is_reported(self) -> None:
        """A missing top-level key is named."""
        plan = _plan()
        del plan["signals"]
        assert render_plan.validate(plan) == ["missing key: signals"]

    def test_wrong_week_count(self) -> None:
        """A plan must have exactly four weeks."""
        plan = _plan()
        plan["weeks"].pop()
        assert "expected 4 weeks, got 3" in render_plan.validate(plan)

    def test_ticket_in_two_weeks(self) -> None:
        """One ticket cannot be planned twice."""
        plan = _plan([[_item("SNOW-1")], [_item("SNOW-1")], [], []])
        assert "SNOW-1 is planned in two weeks" in render_plan.validate(plan)

    def test_unknown_kind_and_driver(self) -> None:
        """Kinds and drivers come from closed sets."""
        plan = _plan([[_item("SNOW-1", "epic", drivers=["vibes"])], [], [], []])
        problems = render_plan.validate(plan)
        assert any("kind 'epic'" in p for p in problems)
        assert any("driver 'vibes'" in p for p in problems)


class TestDiff:
    """The week-to-week comparison."""

    def test_first_plan(self) -> None:
        """With no previous plan the diff says so."""
        assert render_plan.diff(_plan(), None)["first"] is True

    def test_added_moved_shipped_dropped(self) -> None:
        """Every kind of change is classified."""
        previous = _plan(
            [[_item("SNOW-1"), _item("SNOW-3")], [_item("SNOW-4")], [], []]
        )
        plan = _plan([[_item("SNOW-5")], [], [_item("SNOW-1")], []])
        plan["completed_since_last"] = ["SNOW-3"]
        delta = render_plan.diff(plan, previous)
        assert delta["added"] == [("SNOW-5", "Title of SNOW-5", 0)]
        assert delta["moved"] == [
            ("SNOW-1", "Title of SNOW-1", "2026-11-02", "2026-11-16")
        ]
        assert delta["shipped"] == [("SNOW-3", "Title of SNOW-3")]
        assert delta["dropped"] == [("SNOW-4", "Title of SNOW-4")]

    def test_rollover_is_not_a_move(self) -> None:
        """A ticket still on the same date a week later has not moved."""
        previous = _plan([[], [_item("SNOW-1")], [], []])
        plan = _plan([[_item("SNOW-1")], [], [], []], start="2026-11-09")
        assert render_plan.diff(plan, previous)["moved"] == []

    def test_same_index_a_week_later_is_a_slip(self) -> None:
        """A ticket left in week 2 across a rollover has slipped a week."""
        previous = _plan([[], [_item("SNOW-1")], [], []])
        plan = _plan([[], [_item("SNOW-1")], [], []], start="2026-11-09")
        assert render_plan.diff(plan, previous)["moved"] == [
            ("SNOW-1", "Title of SNOW-1", "2026-11-09", "2026-11-16")
        ]


class TestRender:
    """The page itself."""

    def test_round_trip_through_previous(self, tmp_path: Path) -> None:
        """The embedded JSON is read back exactly by next week's run."""
        plan = _plan()
        plan["summary"] = "Contains </script> on purpose."
        page = tmp_path / "prev.html"
        page.write_text(render_plan.render(plan, None), encoding="utf-8")
        assert render_plan.read_previous(page) == plan

    def test_missing_previous_is_none(self, tmp_path: Path) -> None:
        """A path that does not exist means a first plan."""
        assert render_plan.read_previous(tmp_path / "absent.html") is None
        assert render_plan.read_previous(None) is None

    def test_titles_are_escaped(self) -> None:
        """A ticket title cannot inject markup."""
        plan = _plan(
            [[_item("SNOW-1", title="<img src=x onerror=alert(1)>")], [], [], []]
        )
        page = render_plan.render(plan, None)
        assert "<img src=x" not in page
        assert "&lt;img src=x" in page

    def test_panels_and_links_present(self) -> None:
        """Every panel renders and tickets link to Linear."""
        previous = _plan()
        plan = copy.deepcopy(previous)
        plan["weeks"][3]["items"].append(_item("SNOW-9", "chore"))
        plan["linear_changes"] = [
            {"id": "SNOW-9", "field": "priority", "from": "Low", "to": "Medium"}
        ]
        page = render_plan.render(plan, previous)
        for heading in (
            "Since last week",
            "Waiting on Hugo",
            "Competitor signals",
            "Raised this run",
            "Written to Linear this run",
            "Not planned this month",
        ):
            assert heading in page
        assert "https://linear.app/hugorodgerbrown/issue/SNOW-9" in page
        assert "+1 new" in page
        assert "<td>Medium</td>" in page


class TestLive:
    """The live refresh: status markers, closed styling and the script."""

    def test_every_ticket_carries_a_status_marker(self) -> None:
        """Week items and list rows can be repainted by ticket id."""
        plan = _plan()
        plan["decisions"] = [
            {"id": "SNOW-7", "title": "T", "question": "Q?", "status": "Requires input"}
        ]
        page = render_plan.render(plan, None)
        assert 'data-ticket="SNOW-1"' in page
        assert "data-ticket='SNOW-7'" in page
        assert page.count("data-status") >= 3

    def test_closed_ticket_is_marked(self) -> None:
        """A Done or Canceled ticket renders with the closed class."""
        plan = _plan([[_item("SNOW-1", status="Done")], [_item("SNOW-2")], [], []])
        page = render_plan.render(plan, None)
        assert 'class="item k-feature closed" data-ticket="SNOW-1"' in page
        assert 'class="item k-feature" data-ticket="SNOW-2"' in page

    def test_refresh_reads_linear_read_only(self) -> None:
        """The script calls list_issues on Linear and nothing else."""
        page = render_plan.render(_plan(), None)
        assert 'id="plan-refresh"' in page
        assert "callTool('Linear', 'list_issues'" in page
        assert "save_issue" not in page
        # Moves are counted against the published statuses, so a second
        # refresh reports the same tickets as moved, not "nothing moved".
        assert "s !== original[id]" in page

    def test_statuses_at_is_shown(self) -> None:
        """The note names when the embedded statuses were read."""
        plan = _plan()
        plan["statuses_at"] = "2026-11-01T16:40:00Z"
        assert "Statuses as of 1 Nov, 16:40 UTC." in render_plan.render(plan, None)
        assert "Statuses as of 2 Nov." in render_plan.render(_plan(), None)


class TestMain:
    """The command line."""

    def test_invalid_plan_exits_non_zero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A schema failure exits 1 and names the problem."""
        source = tmp_path / "plan.json"
        source.write_text(json.dumps({"weeks": []}), encoding="utf-8")
        assert render_plan.main([str(source)]) == 1
        assert "missing key" in capsys.readouterr().err

    def test_writes_output(self, tmp_path: Path) -> None:
        """A valid plan is written to --output."""
        source = tmp_path / "plan.json"
        source.write_text(json.dumps(_plan()), encoding="utf-8")
        out = tmp_path / "plan.html"
        assert render_plan.main([str(source), "--output", str(out)]) == 0
        assert "<title>Snowdesk 30-Day Plan</title>" in out.read_text(encoding="utf-8")

    def test_prints_to_stdout(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Without --output the page goes to stdout."""
        source = tmp_path / "plan.json"
        source.write_text(json.dumps(_plan()), encoding="utf-8")
        assert render_plan.main([str(source)]) == 0
        assert 'id="plan-data"' in capsys.readouterr().out
