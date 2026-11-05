"""
tests/map/test_boundary.py — The import guard around apps/map.

Two rules, checked by parsing source with ``ast`` as
``tests/ingest/test_boundary.py`` does, so an import inside a function is
caught too (SNOW-1428, docs/decisions/public-splits-by-surface.md):

1. **apps/map reads apps/public through six modules only**:
   ``bulletin_panels``, ``weather_points``, ``decorators``, ``errors``,
   ``season_calendar`` and ``map_shared``. Anything else it needs from
   apps/public goes into one of them, or moves to the map.
2. **Only the URL modules import apps/map**: ``apps/public/urls.py``,
   ``apps/public/api_urls.py`` and ``config/urls.py``, which route its
   views under their existing ``public:`` and ``api:`` names.

Tests are exempt. Each rule is also fed one forbidden import, written to a
temporary tree, to prove it fails rather than passing vacuously.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MAP = "apps.map"
PUBLIC = "apps.public"
ALLOWED_PUBLIC = frozenset(
    f"{PUBLIC}.{name}"
    for name in (
        "bulletin_panels",
        "weather_points",
        "decorators",
        "errors",
        "season_calendar",
        "map_shared",
    )
)
URL_MODULES = frozenset(
    {
        Path("apps/public/urls.py"),
        Path("apps/public/api_urls.py"),
        Path("config/urls.py"),
    }
)


def _imported_modules(path: Path) -> Iterator[tuple[int, str]]:
    """Yield ``(line, module)`` for every absolute import in a source file.

    Args:
        path: A Python source file.

    Yields:
        The line number and dotted name of each import. ``from x import y``
        yields ``x.y``, so ``from apps.public import views`` reads as the
        ``views`` module it is.

    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                yield node.lineno, f"{node.module}.{alias.name}"


def _is_within(module: str, package: str) -> bool:
    """Return whether a dotted module is ``package`` or inside it."""
    return module == package or module.startswith(f"{package}.")


def outbound_violations(root: Path) -> list[str]:
    """Rule 1: imports of apps/public from apps/map outside the allowed six.

    Args:
        root: The repository root to check.

    Returns:
        One ``path:line imports module`` string per violation.

    """
    found = []
    for path in sorted((root / "apps" / "map").rglob("*.py")):
        for line, module in _imported_modules(path):
            if not _is_within(module, PUBLIC):
                continue
            if any(_is_within(module, allowed) for allowed in ALLOWED_PUBLIC):
                continue
            found.append(f"{path.relative_to(root)}:{line} imports {module}")
    return found


def inbound_violations(root: Path) -> list[str]:
    """Rule 2: imports of apps/map from anywhere but the URL modules.

    Args:
        root: The repository root to check.

    Returns:
        One ``path:line imports module`` string per violation.

    """
    own = root / "apps" / "map"
    found = []
    for base in ("apps", "config"):
        for path in sorted((root / base).rglob("*.py")):
            relative = path.relative_to(root)
            if path.is_relative_to(own) or relative in URL_MODULES:
                continue
            for line, module in _imported_modules(path):
                if _is_within(module, MAP):
                    found.append(f"{relative}:{line} imports {module}")
    return found


class TestTheRepository:
    """Both rules hold for the code as committed."""

    def test_map_reads_public_through_the_allowed_modules(self) -> None:
        """apps/map imports apps/public only via the six named modules."""
        assert outbound_violations(REPO_ROOT) == []

    def test_only_the_url_modules_import_the_map(self) -> None:
        """Nothing but the routing hub reaches into apps/map."""
        assert inbound_violations(REPO_ROOT) == []


def _write(root: Path, relative: str, source: str) -> None:
    """Write one source file into a temporary tree."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


class TestTheGuardFails:
    """Each rule catches the import it exists to catch."""

    def test_map_importing_public_views_fails(self, tmp_path: Path) -> None:
        """A map module reaching for a view module's helper is caught."""
        _write(
            tmp_path,
            "apps/map/views.py",
            "from apps.public.bulletin_panels import plain_text\n"
            "def f():\n    from apps.public.views import home\n",
        )
        assert outbound_violations(tmp_path) == [
            "apps/map/views.py:3 imports apps.public.views.home"
        ]

    def test_a_public_page_importing_the_map_fails(self, tmp_path: Path) -> None:
        """apps/public/views.py may not read the map; urls.py may."""
        _write(tmp_path, "apps/public/urls.py", "from apps.map import views\n")
        _write(tmp_path, "apps/public/views.py", "from apps.map.api import ratings\n")
        assert inbound_violations(tmp_path) == [
            "apps/public/views.py:1 imports apps.map.api.ratings"
        ]
