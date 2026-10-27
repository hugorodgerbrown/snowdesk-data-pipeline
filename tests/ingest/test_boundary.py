"""
tests/ingest/test_boundary.py — The import guard that keeps apps/ingest rehostable.

Two rules, each checked by parsing source with ``ast`` rather than importing
it, so an import inside a function or behind ``TYPE_CHECKING`` is caught too
(SNOW-1248, docs/decisions/ingestion-is-a-rehostable-unit.md):

1. **Nothing under ``apps/ingest`` imports another ``apps.*`` package.**
   Copy the folder into an empty Django project and it must still import.
2. **Nothing else under ``apps/`` or ``config/`` reaches past
   ``apps.ingest.public``.** The facade is the contract; every other module
   in the package may move without a caller noticing.

Tests and ``scripts/`` are exempt from rule 2: a test may exercise a private
helper, and the scripts run without Django, so they import the pure module
they need directly. Each rule is also fed one forbidden import, written to a
temporary tree, to prove it fails rather than passing vacuously.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INGEST = "apps.ingest"
FACADE = "apps.ingest.public"


def _imported_modules(path: Path) -> Iterator[tuple[int, str]]:
    """Yield ``(line, module)`` for every absolute import in a source file.

    Args:
        path: A Python source file.

    Yields:
        The line number and dotted module name of each ``import x`` and
        ``from x import y``. ``from x import y`` also yields ``x.y``, so a
        submodule imported by name (``from apps.ingest import normalise``)
        is seen for what it is. Relative imports are skipped: they cannot
        leave the package they are written in.

    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.lineno, node.module
            for alias in node.names:
                yield node.lineno, f"{node.module}.{alias.name}"


def _is_within(module: str, package: str) -> bool:
    """Return whether a dotted module is ``package`` or inside it."""
    return module == package or module.startswith(f"{package}.")


def _first_per_line(found: list[str]) -> Iterator[str]:
    """Keep one violation per source line.

    ``from x import y`` is checked as both ``x`` and ``x.y``, so one bad
    line can match twice; it is reported once, as its first match.
    """
    seen: set[str] = set()
    for violation in found:
        where = violation.split(" ", 1)[0]
        if where not in seen:
            seen.add(where)
            yield violation


def ingest_violations(root: Path) -> list[str]:
    """Rule 1: imports of another ``apps.*`` package from inside ingest.

    Args:
        root: The repository root to check.

    Returns:
        One ``path:line imports module`` string per violation.

    """
    found = []
    for path in sorted((root / "apps" / "ingest").rglob("*.py")):
        for line, module in _imported_modules(path):
            if module == "apps" or not _is_within(module, "apps"):
                continue
            if not _is_within(module, INGEST):
                found.append(f"{path.relative_to(root)}:{line} imports {module}")
    return list(_first_per_line(found))


def facade_violations(root: Path) -> list[str]:
    """Rule 2: imports of ingest, from outside it, that bypass the facade.

    Args:
        root: The repository root to check.

    Returns:
        One ``path:line imports module`` string per violation.

    """
    ingest_dir = root / "apps" / "ingest"
    found = []
    for base in ("apps", "config"):
        for path in sorted((root / base).rglob("*.py")):
            if path.is_relative_to(ingest_dir):
                continue
            for line, module in _imported_modules(path):
                if not _is_within(module, INGEST) or _is_within(module, FACADE):
                    continue
                found.append(f"{path.relative_to(root)}:{line} imports {module}")
    return list(_first_per_line(found))


class TestTheRepository:
    """The two rules hold for the code as committed."""

    def test_ingest_imports_no_other_app(self) -> None:
        """apps/ingest is self-contained."""
        assert ingest_violations(REPO_ROOT) == []

    def test_the_rest_of_the_project_uses_the_facade(self) -> None:
        """apps/ and config/ import ingest only through apps.ingest.public."""
        assert facade_violations(REPO_ROOT) == []


def _write(root: Path, relative: str, source: str) -> None:
    """Write one source file into a temporary tree."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


class TestTheGuardFails:
    """Each rule catches the import it exists to catch."""

    @pytest.mark.parametrize(
        "source",
        [
            "from apps.bulletins.models import Bulletin\n",
            "import apps.weather.models\n",
            "def f() -> None:\n    from apps.core.models import BaseModel\n",
        ],
    )
    def test_an_ingest_module_importing_another_app(
        self, tmp_path: Path, source: str
    ) -> None:
        """A model, a module, or an import hidden in a function."""
        _write(tmp_path, "apps/ingest/normalise/x.py", source)
        _write(tmp_path, "apps/ingest/public.py", "from apps.ingest import x\n")
        (violation,) = ingest_violations(tmp_path)
        assert violation.startswith("apps/ingest/normalise/x.py:")

    @pytest.mark.parametrize(
        "source",
        [
            "from apps.ingest.normalise.slf import parse_dt\n",
            "import apps.ingest.normalise\n",
            "from apps.ingest import normalise\n",
        ],
    )
    def test_a_caller_reaching_past_the_facade(
        self, tmp_path: Path, source: str
    ) -> None:
        """Any path into ingest other than apps.ingest.public."""
        _write(tmp_path, "apps/bulletins/services/x.py", source)
        _write(tmp_path, "config/settings.py", "from apps.ingest.public import a\n")
        assert facade_violations(tmp_path)
        assert all(
            v.startswith("apps/bulletins/services/x.py:")
            for v in facade_violations(tmp_path)
        )

    def test_the_facade_and_ingest_itself_are_allowed(self, tmp_path: Path) -> None:
        """The two shapes the rules permit pass."""
        _write(tmp_path, "apps/bulletins/x.py", "from apps.ingest.public import a\n")
        _write(tmp_path, "config/x.py", "from apps.ingest.public import a\n")
        _write(
            tmp_path, "apps/ingest/public.py", "from apps.ingest.normalise import a\n"
        )
        assert ingest_violations(tmp_path) == []
        assert facade_violations(tmp_path) == []
