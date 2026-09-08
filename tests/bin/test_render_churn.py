"""Tests for bin/render-churn — the weekly churn page renderer.

The parsing half is unit-tested against synthetic `git log --numstat` output,
because that is where the decisions live: which paths count as churn, which
count as data, and what happens to a week that merged nothing. The rendering
half is covered by one end-to-end pass over the real repository, which is
also what proves the embedded JSON and the computed prose agree with each
other.
"""

from __future__ import annotations

import datetime as dt
import importlib.machinery
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RENDER_CHURN = REPO_ROOT / "bin" / "render-churn"


def _load() -> ModuleType:
    """Import the extensionless renderer as a module."""
    spec = importlib.util.spec_from_loader(
        "render_churn",
        importlib.machinery.SourceFileLoader("render_churn", str(RENDER_CHURN)),
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["render_churn"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


churn = _load()


def _log(*commits: tuple[str, list[tuple[int, int, str]]]) -> str:
    """Build synthetic `git log --numstat` output.

    Args:
        *commits: (date, [(added, removed, path), ...]) pairs.

    Returns:
        The log text the renderer parses.

    """
    lines: list[str] = []
    for date, rows in commits:
        lines.append(f"C {date}")
        lines.extend(f"{a}\t{d}\t{p}" for a, d, p in rows)
        lines.append("")
    return "\n".join(lines)


class TestAccumulate:
    """Folding numstat rows into weekly accumulators."""

    def test_authored_churn_is_counted(self) -> None:
        raw = _log(("2026-04-08", [(10, 3, "apps/public/views.py")]))
        week = churn._accumulate(raw)["2026-W15"]
        assert week["commits"] == 1
        assert (week["add"], week["dele"]) == (10, 3)
        assert week["gen"] == 0

    def test_generated_paths_are_diverted_not_dropped(self) -> None:
        """Data movement is counted, but never as churn."""
        raw = _log(
            (
                "2026-04-08",
                [(9000, 1, "apps/regions/fixtures/eaws_AT.json"), (5, 0, "a.py")],
            )
        )
        week = churn._accumulate(raw)["2026-W15"]
        assert (week["add"], week["dele"]) == (5, 0)
        assert week["gen"] == 9001

    def test_every_excluded_path_is_matched(self) -> None:
        """Each documented exclusion actually matches the pattern."""
        excluded = [
            "apps/regions/fixtures/eaws_CH.json",
            "uv.lock",
            "package-lock.json",
            "locale/de/LC_MESSAGES/django.po",
            "locale/de/LC_MESSAGES/django.mo",
            "sample_data/openapi.json",
            "static/css/output.css",
            "scripts/archive/bulletins.ndjson",
            "docs/archive_pdfs/2026-01-01.pdf",
        ]
        assert [p for p in excluded if not churn.GENERATED.search(p)] == []

    def test_source_paths_are_not_excluded(self) -> None:
        """The exclusion pattern does not reach into ordinary source."""
        kept = [
            "apps/public/views.py",
            "static/js/map.js",
            "static/css/map.css",
            "tests/bin/test_render_churn.py",
            "docs/glossary.md",
        ]
        assert [p for p in kept if churn.GENERATED.search(p)] == []

    def test_binary_rows_are_skipped(self) -> None:
        """git writes `-` for a binary file; there are no lines to attribute."""
        raw = "C 2026-04-08\n-\t-\tstatic/img/logo.png\n4\t0\ta.py\n"
        week = churn._accumulate(raw)["2026-W15"]
        assert (week["add"], week["dele"]) == (4, 0)

    def test_a_file_touched_twice_in_a_week_counts_once(self) -> None:
        raw = _log(
            ("2026-04-08", [(5, 0, "a.py")]),
            ("2026-04-09", [(7, 2, "a.py")]),
        )
        week = churn._accumulate(raw)["2026-W15"]
        assert week["commits"] == 2
        assert len(week["files"]) == 1

    def test_rows_before_any_commit_line_are_ignored(self) -> None:
        assert churn._accumulate("4\t0\ta.py\n") == {}


class TestReleaseTagPattern:
    """Which tags count as a production release."""

    def test_date_stamped_tags_are_releases(self) -> None:
        for tag in ("2026.06.22", "2026.08.30.2", "2026.08.30.12"):
            assert churn.RELEASE_TAG.match(tag), tag

    def test_named_tags_are_not_releases(self) -> None:
        """Spike and recovery tags share the repo but are not releases."""
        for tag in (
            "map-performance-spike-45",
            "exportable-design-tokens",
            "recovery/snow-330-pre-untangle",
            "2026.08.30-rc1",
        ):
            assert not churn.RELEASE_TAG.match(tag), tag


class TestWeekMonday:
    """ISO week keys resolve to the right Monday."""

    def test_key_resolves_to_its_monday(self) -> None:
        assert churn._week_monday("2026-W15") == dt.date(2026, 4, 6)

    def test_round_trips_through_iso_week(self) -> None:
        day = dt.date(2026, 8, 27)
        assert churn._week_monday(churn.iso_week(day)) == dt.date(2026, 8, 24)


class TestWeeklyHistory:
    """The calendar walk over the real repository."""

    def test_weeks_are_contiguous_and_ordered(self) -> None:
        """Every week between the first and last commit is present exactly once."""
        weeks = churn.weekly_history()
        assert weeks, "the repository has commits"
        starts = [dt.date.fromisoformat(str(w["start"])) for w in weeks]
        assert starts == sorted(starts)
        gaps = {(b - a).days for a, b in zip(starts, starts[1:])}
        assert gaps == {7}

    def test_silent_weeks_are_kept_as_zeroes(self) -> None:
        """A week that merged nothing is a zero row, not a missing one."""
        weeks = churn.weekly_history()
        silent = [w for w in weeks if w["c"] == 0]
        assert silent, "the history contains at least one week with no commits"
        for week in silent:
            assert (week["a"], week["d"], week["f"]) == (0, 0, 0)

    def test_release_counts_match_their_tag_lists(self) -> None:
        weeks = churn.weekly_history()
        assert all(w["rel"] == len(w["tags"]) for w in weeks)
        assert sum(int(w["rel"]) for w in weeks) > 0


class TestRender:
    """The rendered page."""

    def test_page_is_a_fragment_not_a_document(self) -> None:
        """Artifacts supply the skeleton; the renderer must not."""
        page = churn.render(churn.weekly_history(), dt.date(2026, 9, 8))
        lowered = page.lower()
        for tag in ("<!doctype", "<html", "<head>", "<body>"):
            assert tag not in lowered

    def test_every_placeholder_is_substituted(self) -> None:
        page = churn.render(churn.weekly_history(), dt.date(2026, 9, 8))
        assert re.search(r"__[A-Z_]+__", page) is None

    def test_embedded_data_matches_the_history(self) -> None:
        """The JSON the chart reads is the same data the table totals."""
        weeks = churn.weekly_history()
        page = churn.render(weeks, dt.date(2026, 9, 8))
        match = re.search(r"const DATA = (\[.*?\]);\n", page)
        assert match is not None, "the page carries an embedded DATA array"
        embedded = json.loads(match.group(1))
        assert embedded == json.loads(json.dumps(weeks))

    def test_the_render_date_is_stamped(self) -> None:
        page = churn.render(churn.weekly_history(), dt.date(2026, 9, 8))
        assert "8 September 2026" in page
