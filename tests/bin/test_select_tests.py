"""
tests/bin/test_select_tests.py — the gate that picks testmon or the full suite.

bin/select-tests decides, for a pull request, whether pytest-testmon can be
trusted to choose the tests (SNOW-1221). These tests pin the rule its header
comment states: a change is safe only inside a function body, or in a
docstring, an import, a comment or a blank line; a test module is safe
anywhere; everything else — non-Python files, settings, conftest,
migrations, new or deleted modules, class and module attributes,
signatures, decorators — means the full suite.

The end-to-end cases build a throwaway git repository under ``tmp_path``,
because the script's input is two commits and the diff between them.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SELECT_TESTS = REPO_ROOT / "bin" / "select-tests"


def _load() -> ModuleType:
    """Import the extensionless script as a module."""
    spec = importlib.util.spec_from_loader(
        "select_tests",
        importlib.machinery.SourceFileLoader("select_tests", str(SELECT_TESTS)),
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["select_tests"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


select = _load()

SERVICE = '''"""A service module."""

import datetime

LIMIT = 5


class Thing:
    """A thing."""

    size = 1

    def grow(self, by: int = 1) -> int:
        """Grow the thing."""
        return self.size + by


@decorated
def helper(value: int) -> int:
    """Help."""
    # a comment
    return value * LIMIT
'''


def _git(repo: Path, *args: str) -> str:
    """Run git inside ``repo`` and return stdout."""
    # S603/S607 — a fixed git argv against a throwaway repo; no shell, no
    # external input. git from PATH, as bin/select-tests itself runs it.
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Return a git repository holding one commit of ``SERVICE``."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "apps").mkdir()
    (tmp_path / "apps" / "service.py").write_text(SERVICE)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_service.py").write_text("X = 1\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _commit(repo: Path, path: str, text: str | None) -> str:
    """Write (or, for ``None``, delete) ``path``, commit, return the base sha."""
    base = _git(repo, "rev-parse", "HEAD")
    target = repo / path
    if text is None:
        target.unlink()
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change")
    return base


def _line(source: str, needle: str) -> int:
    """Return the 1-based number of the first line containing ``needle``."""
    for number, text in enumerate(source.splitlines(), start=1):
        if needle in text:
            return number
    raise AssertionError(needle)


class TestSafeLines:
    """Which lines of a module testmon can attribute to a test."""

    def test_function_and_method_bodies_are_safe(self) -> None:
        """Statements inside a def, nested in a class or not, are safe."""
        safe = select.safe_lines(SERVICE)
        assert _line(SERVICE, "return self.size + by") in safe
        assert _line(SERVICE, "return value * LIMIT") in safe

    def test_docstrings_imports_comments_and_blanks_are_safe(self) -> None:
        """Lines that change nothing at import time are safe."""
        safe = select.safe_lines(SERVICE)
        assert _line(SERVICE, "A service module.") in safe
        assert _line(SERVICE, '"""A thing."""') in safe
        assert _line(SERVICE, "import datetime") in safe
        assert _line(SERVICE, "# a comment") in safe
        assert 2 in safe

    def test_import_time_statements_are_not_safe(self) -> None:
        """Constants, class attributes, defs, signatures and decorators run at import."""
        safe = select.safe_lines(SERVICE)
        for needle in (
            "LIMIT = 5",
            "size = 1",
            "class Thing",
            "def grow",
            "@decorated",
            "def helper",
        ):
            assert _line(SERVICE, needle) not in safe, needle

    def test_a_one_line_body_shares_the_signature_line(self) -> None:
        """``def f(): ...`` cannot change its body without its signature line."""
        assert 1 not in select.safe_lines("def f(x=1): return x\n")

    def test_a_hash_inside_a_module_string_is_code(self) -> None:
        """A string line that starts with # is part of a statement, not a comment."""
        source = 'QUERY = """\n# not a comment\n"""\n'
        assert 2 not in select.safe_lines(source)


class TestChangedLines:
    """Parsing ``git diff -U0`` hunk headers."""

    def test_counts_default_to_one_and_zero_means_none(self) -> None:
        """``@@ -3 +3,0 @@`` is one old line and no new line."""
        old, new = select.changed_lines("@@ -3 +3,0 @@\n@@ -9,0 +8,2 @@\n")
        assert old == {3}
        assert new == {8, 9}


class TestPathRules:
    """Paths decided without reading their contents."""

    @pytest.mark.parametrize(
        ("path", "forced"),
        [
            ("docs/notes.md", False),
            ("README.md", False),
            (".claude/settings.json", False),
            ("tests/weather/test_models.py", False),
            ("templates/includes/_card.html", True),
            ("uv.lock", True),
            ("static/js/map.js", True),
            ("config/settings/base.py", True),
            ("apps/weather/migrations/0008_x.py", True),
            ("tests/conftest.py", True),
            ("tests/weather/conftest.py", True),
        ],
    )
    def test_path(self, path: str, forced: bool) -> None:
        """Each path either forces the full suite or does not."""
        reason = select.path_reason("M", path, "unused", "unused")
        assert bool(reason) is forced, reason

    def test_an_added_or_deleted_module_forces_the_full_suite(self) -> None:
        """Nothing in the map refers to a module that did not exist."""
        assert select.path_reason("A", "apps/weather/new.py", "x", "y")
        assert select.path_reason("D", "apps/weather/old.py", "x", "y")


class TestDecide:
    """End to end, between two real commits."""

    def test_a_function_body_change_is_affected(self, repo: Path) -> None:
        """Editing a return statement leaves the choice to testmon."""
        base = _commit(
            repo, "apps/service.py", SERVICE.replace("value * LIMIT", "value + 1")
        )
        assert select.decide(base).mode == "affected"

    def test_a_docstring_change_is_affected(self, repo: Path) -> None:
        """The case that started SNOW-1221."""
        base = _commit(repo, "apps/service.py", SERVICE.replace("Help.", "Assist."))
        assert select.decide(base).mode == "affected"

    def test_a_class_attribute_change_is_full(self, repo: Path) -> None:
        """A class attribute runs once, at import, outside any test."""
        base = _commit(repo, "apps/service.py", SERVICE.replace("size = 1", "size = 2"))
        decision = select.decide(base)
        assert decision.mode == "full"
        assert "change outside a function body" in decision.reasons[0]

    def test_a_deleted_module_level_line_is_full(self, repo: Path) -> None:
        """A removal is judged against the old file's structure."""
        base = _commit(repo, "apps/service.py", SERVICE.replace("LIMIT = 5\n", ""))
        assert select.decide(base).mode == "full"

    def test_a_test_module_change_anywhere_is_affected(self, repo: Path) -> None:
        """testmon fingerprints a test's own module whole."""
        base = _commit(repo, "tests/test_service.py", "X = 2\n")
        assert select.decide(base).mode == "affected"

    def test_a_template_change_is_full(self, repo: Path) -> None:
        """testmon never sees a template."""
        base = _commit(repo, "templates/page.html", "<p>hi</p>\n")
        assert select.decide(base).reasons == ["templates/page.html: not Python"]

    def test_an_unresolvable_map_commit_is_full(self, repo: Path) -> None:
        """A map whose commit is not in this clone cannot be trusted."""
        decision = select.decide("0" * 40)
        assert decision.mode == "full"
        assert decision.reasons[0].startswith("cannot resolve")

    def test_an_unparseable_module_is_full(self, repo: Path) -> None:
        """A syntax error is a reason to run everything, not to crash."""
        base = _commit(
            repo, "apps/service.py", SERVICE.replace("return value", "return (value")
        )
        assert select.decide(base).mode == "full"


class TestMain:
    """The command line: stdout and $GITHUB_OUTPUT."""

    def test_writes_the_mode_to_github_output(
        self,
        repo: Path,
        tmp_path_factory: pytest.TempPathFactory,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``--github-output`` appends the same ``mode=`` line CI reads."""
        base = _commit(repo, "templates/page.html", "<p>hi</p>\n")
        output = tmp_path_factory.mktemp("gh") / "output"
        monkeypatch.setenv("GITHUB_OUTPUT", str(output))
        assert select.main(["--since", base, "--github-output"]) == 0
        assert output.read_text() == "mode=full\n"
        assert "full suite: templates/page.html: not Python" in capsys.readouterr().out

    def test_never_fails_the_build(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unexpected error still exits 0, with the full suite chosen."""

        def explode(since: str, head: str) -> None:
            """Stand in for a decide() that raises."""
            raise RuntimeError("boom")

        monkeypatch.setattr(select, "decide", explode)
        monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
        assert select.main(["--github-output"]) == 0
