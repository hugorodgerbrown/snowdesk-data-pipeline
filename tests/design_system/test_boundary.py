"""
tests/design_system/test_boundary.py — Nothing reads the component library.

``apps/design_system`` reads the domain apps, and nothing reads it
(SNOW-1427, docs/decisions/public-splits-by-surface.md). The one exception
is the URL hub: ``apps/public/urls.py`` routes the library's pages under
their existing ``public:`` names, so it may import ``apps.design_system.views``
and nothing else from the app.

Checked by parsing source with ``ast``, as ``tests/ingest/test_boundary.py``
does, so an import inside a function is caught too. Tests are exempt. The
rule is also fed one forbidden import, written to a temporary tree, to
prove it fails rather than passing vacuously.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "apps.design_system"
URL_HUB = Path("apps/public/urls.py")
URL_HUB_MAY_IMPORT = f"{PACKAGE}.views"


def _imported_modules(path: Path) -> Iterator[tuple[int, str]]:
    """Yield ``(line, module)`` for every absolute import in a source file.

    Args:
        path: A Python source file.

    Yields:
        The line number and dotted module of each import. ``from x import y``
        yields ``x.y`` rather than ``x``, so ``from apps.design_system import
        views`` reads as the ``views`` module it is.

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


def inbound_violations(root: Path) -> list[str]:
    """Return every import of the library from outside it, bar the URL hub's.

    Args:
        root: The repository root to check.

    Returns:
        One ``path:line imports module`` string per violation.

    """
    own = root / "apps" / "design_system"
    found = []
    for base in ("apps", "config"):
        for path in sorted((root / base).rglob("*.py")):
            if path.is_relative_to(own):
                continue
            relative = path.relative_to(root)
            for line, module in _imported_modules(path):
                if not _is_within(module, PACKAGE):
                    continue
                if relative == URL_HUB and _is_within(module, URL_HUB_MAY_IMPORT):
                    continue
                found.append(f"{relative}:{line} imports {module}")
    return found


def test_only_the_url_hub_imports_the_library() -> None:
    """In apps/ and config/, only apps/public/urls.py imports it, via views."""
    assert inbound_violations(REPO_ROOT) == []


def _write(root: Path, relative: str, source: str) -> None:
    """Write one source file into a temporary tree."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_another_app_importing_the_library_fails(tmp_path: Path) -> None:
    """A domain module reading the tokens is caught."""
    _write(
        tmp_path,
        "apps/public/views.py",
        "def f():\n    from apps.design_system.design_tokens import X\n",
    )
    assert inbound_violations(tmp_path) == [
        "apps/public/views.py:2 imports apps.design_system.design_tokens.X"
    ]


def test_the_url_hub_reaching_past_views_fails(tmp_path: Path) -> None:
    """The hub may route the views, not read the fixtures."""
    _write(
        tmp_path,
        "apps/public/urls.py",
        "from apps.design_system import views\n"
        "from apps.design_system import _component_fixtures\n",
    )
    assert inbound_violations(tmp_path) == [
        "apps/public/urls.py:2 imports apps.design_system._component_fixtures"
    ]
