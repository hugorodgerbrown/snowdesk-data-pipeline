"""
tests/track/test_boundary.py — ``track`` stays pure (SNOW-1316).

The package is the maths an instance draws an upload with and the
reference-data service draws a SAC tour with, so it may import neither
Django nor any ``apps.*`` package: either would tie the service to
Snowdesk, or the maths to a settings module. Checked by parsing source with
``ast``, so an import inside a function is caught too, and fed one
forbidden import to prove the check bites.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = ("django", "apps", "config")


def _forbidden_imports(root: Path) -> list[str]:
    """Return ``path:line module`` for every forbidden import under ``root``."""
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = [node.module or ""]
            else:
                continue
            found.extend(
                f"{path.relative_to(root)}:{node.lineno} {module}"
                for module in modules
                if module.split(".")[0] in FORBIDDEN
            )
    return found


def test_track_imports_no_django_and_no_app() -> None:
    """Every module under track/ is stdlib, requests and track itself."""
    assert _forbidden_imports(REPO_ROOT / "track") == []


def test_the_check_catches_a_forbidden_import(tmp_path: Path) -> None:
    """A deferred Django import is still found."""
    (tmp_path / "bad.py").write_text(
        "def f() -> None:\n    from django.conf import settings\n"
    )
    assert _forbidden_imports(tmp_path) == ["bad.py:2 django.conf"]
