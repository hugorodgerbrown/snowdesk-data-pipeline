"""Tests for bin/render-churn — the weekly churn page renderer.

Two layers, and neither of them reads the repository this file lives in.

The parsing rules are unit-tested against synthetic `git log --numstat`
output, because that is where the decisions live: which paths count as
churn, which count as data, and what happens to a week that merged nothing.

Everything that needs a real `git` is pointed at a throwaway repository
built by the `fixture_repo` fixture, with a known shape — two commits in one
week, a silent week, then a week carrying two release tags and one named tag
that must not count as a release.

The first version of this file asserted against the real history instead,
and passed locally and failed in CI: `actions/checkout` produces a shallow
clone with no tags, so there was no gap week and no release to find. A test
that depends on how the checkout was made is testing the checkout.
"""

from __future__ import annotations

import datetime as dt
import importlib.machinery
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

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


# The fixture repository: (date, [(added, removed, path), ...]) per commit.
# Week of 6 Apr carries two commits touching the same file plus a fixture
# import; the week of 13 Apr is deliberately empty; the week of 20 Apr
# carries the tags.
FIXTURE_COMMITS = [
    ("2026-04-06T09:00:00", {"a.py": 10, "apps/x/fixtures/regions.json": 100}),
    ("2026-04-08T09:00:00", {"a.py": 15}),
    ("2026-04-20T09:00:00", {"b.py": 7}),
]
FIXTURE_TAGS = ["2026.04.20", "2026.04.20.2", "spike-something"]


def _run(repo: Path, *args: str, when: str | None = None) -> None:
    """Run one git command in the fixture repo, optionally at a fixed date."""
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(repo),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
    }
    if when is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when
    # S603 — a test helper driving git with a fixed argument list, no shell,
    # against a temporary directory pytest created.
    subprocess.run(  # noqa: S603
        [
            churn.GIT,
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.com",
            "-c",
            "commit.gpgsign=false",
            "-C",
            str(repo),
            *args,
        ],
        check=True,
        capture_output=True,
        env=env,
    )


@pytest.fixture(scope="module")
def fixture_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build a throwaway git repository with a known weekly shape."""
    repo = tmp_path_factory.mktemp("churn-repo")
    _run(repo, "init", "-b", "main")

    for when, files in FIXTURE_COMMITS:
        for name, lines in files.items():
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(f"line {n}" for n in range(lines)) + "\n")
        # A binary file: git reports "-" for its line counts, which the
        # renderer has to skip rather than parse as an integer.
        (repo / "logo.png").write_bytes(b"\x89PNG\x00\x00binary\x00")
        _run(repo, "add", "-A")
        _run(repo, "commit", "-m", f"commit at {when}", when=when)

    for tag in FIXTURE_TAGS:
        _run(repo, "tag", tag)
    return repo


@pytest.fixture
def churn_at(fixture_repo: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Point the renderer at the fixture repository instead of this one."""
    monkeypatch.setattr(churn, "REPO_ROOT", fixture_repo)
    return churn


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
    """The calendar walk, against a repository of known shape."""

    def test_one_record_per_week_including_the_silent_one(
        self, churn_at: ModuleType
    ) -> None:
        weeks = churn_at.weekly_history()
        assert [w["label"] for w in weeks] == ["6 Apr", "13 Apr", "20 Apr"]

    def test_weeks_are_contiguous(self, churn_at: ModuleType) -> None:
        """Consecutive records are exactly seven days apart."""
        starts = [
            dt.date.fromisoformat(str(w["start"])) for w in churn_at.weekly_history()
        ]
        assert {(b - a).days for a, b in zip(starts, starts[1:])} == {7}

    def test_a_silent_week_is_a_zero_row_not_a_missing_one(
        self, churn_at: ModuleType
    ) -> None:
        silent = churn_at.weekly_history()[1]
        assert (silent["c"], silent["a"], silent["d"], silent["f"]) == (0, 0, 0, 0)
        assert silent["tags"] == []

    def test_commits_and_churn_are_summed_across_the_week(
        self, churn_at: ModuleType
    ) -> None:
        """Two commits, one file rewritten, and a fixture import beside it."""
        first = churn_at.weekly_history()[0]
        assert first["c"] == 2
        assert first["f"] == 1  # a.py, touched twice
        assert first["g"] == 100  # the fixture import, kept out of churn
        assert first["a"] > 0

    def test_release_tags_are_counted_and_named_tags_are_not(
        self, churn_at: ModuleType
    ) -> None:
        last = churn_at.weekly_history()[-1]
        assert last["rel"] == 2
        assert last["tags"] == ["2026.04.20", "2026.04.20.2"]

    def test_release_counts_match_their_tag_lists(self, churn_at: ModuleType) -> None:
        assert all(w["rel"] == len(w["tags"]) for w in churn_at.weekly_history())


class TestRender:
    """The rendered page."""

    def test_page_is_a_fragment_not_a_document(self, churn_at: ModuleType) -> None:
        """Artifacts supply the skeleton; the renderer must not."""
        page = churn_at.render(churn_at.weekly_history(), dt.date(2026, 4, 27))
        lowered = page.lower()
        for tag in ("<!doctype", "<html", "<head>", "<body>"):
            assert tag not in lowered

    def test_every_placeholder_is_substituted(self, churn_at: ModuleType) -> None:
        page = churn_at.render(churn_at.weekly_history(), dt.date(2026, 4, 27))
        assert re.search(r"__[A-Z_]+__", page) is None

    def test_embedded_data_matches_the_history(self, churn_at: ModuleType) -> None:
        """The JSON the chart reads is the same data the table totals."""
        weeks = churn_at.weekly_history()
        page = churn_at.render(weeks, dt.date(2026, 4, 27))
        match = re.search(r"const DATA = (\[.*?\]);\n", page)
        assert match is not None, "the page carries an embedded DATA array"
        assert json.loads(match.group(1)) == json.loads(json.dumps(weeks))

    def test_the_render_date_is_stamped(self, churn_at: ModuleType) -> None:
        page = churn_at.render(churn_at.weekly_history(), dt.date(2026, 4, 27))
        assert "27 April 2026" in page

    def test_the_silent_week_reaches_the_prose(self, churn_at: ModuleType) -> None:
        """The notes are computed, so the gap must be named there too."""
        page = churn_at.render(churn_at.weekly_history(), dt.date(2026, 4, 27))
        assert "13 Apr" in page
        assert "no commits at all" in page
