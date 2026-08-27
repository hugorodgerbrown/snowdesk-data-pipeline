"""
tests/bin/test_migrations_lint.py — the migration-collision guard.

Covers the two checks `_check_app` composes, which SNOW-740 lifted out of it
into `_duplicate_numbers` and `_leaf_migrations`. Both were unreachable by
test before that split, and the guard has no `--root` flag, so this imports
the module rather than driving it by subprocess the way
tests/bin/test_i18n_lint.py and tests/bin/test_lint_guards_js.py do.

The guard's own end-to-end behaviour — walking `apps/` and returning a
non-zero exit code — is exercised by `tox -e migrations-lint` on every run.

The last test in the file is about bin/ generally rather than this guard:
it asserts every Python script there is listed in ruff's `extend-include`,
which is the omission that let this one drift.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MIGRATIONS_LINT = REPO_ROOT / "bin" / "migrations-lint"


def _load() -> ModuleType:
    """Import the extensionless guard as a module."""
    spec = importlib.util.spec_from_loader(
        "migrations_lint",
        importlib.machinery.SourceFileLoader("migrations_lint", str(MIGRATIONS_LINT)),
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["migrations_lint"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


guard = _load()


def _migration(directory: Path, name: str, dependencies: str = "[]") -> Path:
    """Write a minimal migration module and return its path."""
    path = directory / f"{name}.py"
    path.write_text(f"dependencies = {dependencies}\noperations = []\n")
    return path


class TestDuplicateNumbers:
    """Two migrations numbered off the same parent."""

    def test_no_duplicates_is_silent(self) -> None:
        """A clean sequence produces no violation."""
        files = [Path("0001_initial.py"), Path("0002_add_field.py")]
        assert guard._duplicate_numbers("bulletins", files) == []

    def test_duplicate_number_is_reported(self) -> None:
        """Two files sharing a number is the collision the guard exists for."""
        files = [Path("0002_add_field.py"), Path("0002_add_index.py")]
        violations = guard._duplicate_numbers("bulletins", files)
        assert len(violations) == 1
        assert violations[0].app == "bulletins"
        assert "duplicate migration number 0002" in violations[0].message

    def test_both_names_appear_in_the_message(self) -> None:
        """The message has to name what collided to be actionable."""
        files = [Path("0002_add_field.py"), Path("0002_add_index.py")]
        message = guard._duplicate_numbers("bulletins", files)[0].message
        assert "0002_add_field" in message
        assert "0002_add_index" in message

    def test_each_colliding_number_is_its_own_violation(self) -> None:
        """Two separate collisions are two separate reports."""
        files = [
            Path("0002_a.py"),
            Path("0002_b.py"),
            Path("0003_c.py"),
            Path("0003_d.py"),
        ]
        assert len(guard._duplicate_numbers("bulletins", files)) == 2

    def test_unnumbered_file_is_ignored(self) -> None:
        """A file outside the NNNN_ convention carries no number to collide."""
        files = [Path("helpers.py"), Path("0001_initial.py")]
        assert guard._duplicate_numbers("bulletins", files) == []


class TestLeafMigrations:
    """The leaves are the migrations nothing else depends on."""

    def test_linear_chain_has_one_leaf(self, tmp_path: Path) -> None:
        """A normal history ends in exactly one migration."""
        files = [
            _migration(tmp_path, "0001_initial"),
            _migration(tmp_path, "0002_second", '[("bulletins", "0001_initial")]'),
        ]
        assert guard._leaf_migrations("bulletins", files) == ["0002_second"]

    def test_branch_has_two_leaves(self, tmp_path: Path) -> None:
        """Two migrations off one parent leave a graph `migrate` refuses."""
        files = [
            _migration(tmp_path, "0001_initial"),
            _migration(tmp_path, "0002_a", '[("bulletins", "0001_initial")]'),
            _migration(tmp_path, "0003_b", '[("bulletins", "0001_initial")]'),
        ]
        assert guard._leaf_migrations("bulletins", files) == ["0002_a", "0003_b"]

    def test_cross_app_dependency_does_not_count(self, tmp_path: Path) -> None:
        """Only same-app dependencies retire a migration from the leaf set."""
        files = [
            _migration(tmp_path, "0001_initial"),
            _migration(tmp_path, "0002_second", '[("regions", "0001_initial")]'),
        ]
        assert guard._leaf_migrations("bulletins", files) == [
            "0001_initial",
            "0002_second",
        ]

    def test_single_migration_is_its_own_leaf(self, tmp_path: Path) -> None:
        """A one-migration app has one leaf, not zero."""
        files = [_migration(tmp_path, "0001_initial")]
        assert guard._leaf_migrations("bulletins", files) == ["0001_initial"]


class TestCheckApp:
    """The composed check still returns what `main` expects."""

    def _app(self, tmp_path: Path) -> Path:
        """Build an app directory with a migrations package."""
        migrations = tmp_path / "bulletins" / "migrations"
        migrations.mkdir(parents=True)
        (migrations / "__init__.py").write_text("")
        return tmp_path / "bulletins"

    def test_clean_app_returns_its_sole_leaf(self, tmp_path: Path) -> None:
        """No violations, and the leaf is reported for --show-leaves."""
        app_dir = self._app(tmp_path)
        _migration(app_dir / "migrations", "0001_initial")
        _migration(
            app_dir / "migrations", "0002_second", '[("bulletins", "0001_initial")]'
        )
        violations, leaf = guard._check_app(app_dir)
        assert violations == []
        assert leaf == "0002_second"

    def test_branched_app_reports_and_withholds_the_leaf(self, tmp_path: Path) -> None:
        """A multi-leaf graph is a violation, and there is no single leaf."""
        app_dir = self._app(tmp_path)
        _migration(app_dir / "migrations", "0001_initial")
        _migration(app_dir / "migrations", "0002_a", '[("bulletins", "0001_initial")]')
        _migration(app_dir / "migrations", "0003_b", '[("bulletins", "0001_initial")]')
        violations, leaf = guard._check_app(app_dir)
        assert leaf is None
        assert len(violations) == 1
        assert "2 leaf migrations" in violations[0].message

    def test_app_with_no_migrations_is_skipped(self, tmp_path: Path) -> None:
        """An empty migrations package is not a violation."""
        app_dir = self._app(tmp_path)
        assert guard._check_app(app_dir) == ([], None)

    def test_both_problems_are_reported_together(self, tmp_path: Path) -> None:
        """A duplicate number and a branch are independent findings."""
        app_dir = self._app(tmp_path)
        _migration(app_dir / "migrations", "0001_initial")
        _migration(app_dir / "migrations", "0002_a", '[("bulletins", "0001_initial")]')
        _migration(app_dir / "migrations", "0002_b", '[("bulletins", "0001_initial")]')
        violations, leaf = guard._check_app(app_dir)
        assert leaf is None
        assert len(violations) == 2


def _python_bin_scripts() -> list[str]:
    """Return the names of every Python script in bin/, found by shebang.

    Discovered rather than listed: a hardcoded list would pass for the
    scripts it already knows about, which is the failure mode this is here
    to catch.
    """
    names: list[str] = []
    for path in sorted((REPO_ROOT / "bin").iterdir()):
        if not path.is_file():
            continue
        try:
            first_line = path.open(encoding="utf-8").readline()
        except UnicodeDecodeError:
            continue  # a compiled artefact, not a script
        if first_line.startswith("#!") and "python" in first_line:
            names.append(path.name)
    return names


def test_every_python_bin_script_is_linted() -> None:
    """Every Python script in bin/ is listed in ruff's extend-include.

    ruff's directory scan finds Python by the `.py` extension, so an
    extensionless bin/ script is invisible to `ruff check .` — which is what
    tox and CI run — while the pre-commit hook still checks it by shebang.
    Pre-commit is bypassable and CI is the backstop, so the gap let
    bin/migrations-lint reach main carrying a C901 and an E501 (SNOW-740).

    Fails the moment a new script is added without the matching entry.
    """
    pyproject = (REPO_ROOT / "pyproject.toml").read_text()
    scripts = _python_bin_scripts()
    assert scripts, "no Python scripts found in bin/ — the shebang scan is broken"
    missing = [s for s in scripts if f'"bin/{s}"' not in pyproject]
    assert missing == [], (
        f"add to [tool.ruff] extend-include in pyproject.toml: {missing}"
    )
