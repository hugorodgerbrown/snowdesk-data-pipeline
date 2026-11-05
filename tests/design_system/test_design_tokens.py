"""
tests/design_system/test_design_tokens.py — Every icon partial is in the library.

Hugo's rule of 5 Nov 2026: all icons are in the component library. The UI
icons are the ``_icon_*.html`` house stroke partials
(docs/decisions/ui-icons-are-house-stroke-partials.md), and the registry's
``ui-icons`` entry (``ICON_PARTIALS`` in apps/design_system/design_tokens.py)
lists them. These tests keep the two in step:

  - every ``_icon_*.html`` in any project template directory is registered,
    so a new glyph fails CI until it is browsable at ``/_components/``;
  - every registered partial exists, and none is listed twice;
  - each entry's ``default_size`` and parameter names match the partial's
    own header, so the library's captions cannot drift from the source.

The glob is also fed a temporary tree holding one unregistered glyph, to
prove the guard fails rather than passing vacuously.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from django.template.utils import get_app_template_dirs

from apps.design_system.design_tokens import ICON_PARTIALS, IconPartial

ICON_GLOB = "_icon_*.html"

_COMMENT_RE = re.compile(r"{%\s*comment\s*%}(.*?){%\s*endcomment\s*%}", re.DOTALL)
_SECTION_RE = re.compile(r"^Parameters\b")
_PARAM_RE = re.compile(r"^\s{2,}(\w+)\s*\(")
_DEFAULT_SIZE_RE = re.compile(r"size\|default:(\d+)")


def _project_template_dirs() -> list[Path]:
    """Return every template directory inside the repository.

    The ``DIRS`` of each engine plus each installed app's ``templates/``,
    filtered to the repository so a third-party app's templates are not
    the project's to register.
    """
    base = Path(settings.BASE_DIR).resolve()
    engines: list[dict[str, Any]] = settings.TEMPLATES
    dirs = [Path(d) for engine in engines for d in engine.get("DIRS", [])]
    dirs.extend(Path(d) for d in get_app_template_dirs("templates"))
    return [d for d in dirs if d.resolve().is_relative_to(base)]


def _icon_partials_in(template_dirs: Iterable[Path]) -> dict[str, Path]:
    """Map each ``_icon_*.html`` found to its file, keyed by its template path.

    Args:
        template_dirs: Template roots to search, recursively.

    Returns:
        ``{"includes/_icon_close.html": Path(...)}`` — the key is the path a
        caller passes to ``{% include %}``, relative to its template root.

    """
    return {
        path.relative_to(root).as_posix(): path
        for root in template_dirs
        for path in sorted(root.rglob(ICON_GLOB))
    }


def _unregistered(found: Iterable[str]) -> list[str]:
    """Return the found template paths no ``ICON_PARTIALS`` entry names.

    Args:
        found: Template paths, as ``_icon_partials_in`` keys them.

    """
    registered = {icon.partial for icon in ICON_PARTIALS}
    return sorted(set(found) - registered)


def _header(path: Path) -> str:
    """Return the text of a partial's leading ``{% comment %}`` block.

    Args:
        path: The partial's file.

    """
    match = _COMMENT_RE.search(path.read_text(encoding="utf-8"))
    assert match, f"{path}: no {{% comment %}} header block"
    return match.group(1)


def _documented_parameters(header: str) -> list[str]:
    """Return the parameter names a header's Parameters section lists.

    The section runs from its heading line to the next blank line. A name
    is a line indented at least two spaces whose first word is followed by
    ``(`` — ``size (int, optional) — …`` — so wrapped continuation lines
    are skipped.

    Args:
        header: The partial's header comment text.

    """
    names: list[str] = []
    in_section = False
    for line in header.splitlines():
        if _SECTION_RE.match(line):
            in_section = True
            continue
        if in_section:
            if not line.strip():
                break
            if match := _PARAM_RE.match(line):
                names.append(match.group(1))
    return names


_FOUND = _icon_partials_in(_project_template_dirs())


def test_every_icon_partial_is_registered() -> None:
    """A ``_icon_*.html`` anywhere in the project has an ``ICON_PARTIALS`` entry."""
    missing = _unregistered(_FOUND)
    assert not missing, (
        "Icon partials missing from the component library — add each to "
        "ICON_PARTIALS in apps/design_system/design_tokens.py: " + ", ".join(missing)
    )


def test_every_registered_icon_partial_exists() -> None:
    """No entry names a partial that is not on disk (a rename or a deletion)."""
    stale = sorted({icon.partial for icon in ICON_PARTIALS} - set(_FOUND))
    assert not stale, f"ICON_PARTIALS names partials with no file: {stale}"


def test_no_icon_partial_is_registered_twice() -> None:
    """One entry per glyph, so the panel never draws a card twice."""
    partials = [icon.partial for icon in ICON_PARTIALS]
    assert len(partials) == len(set(partials))


def test_the_registry_is_alphabetical() -> None:
    """Entries sort by file name, so a new glyph has one obvious place."""
    partials = [icon.partial for icon in ICON_PARTIALS]
    assert partials == sorted(partials)


def test_the_guard_catches_an_unregistered_glyph(tmp_path: Path) -> None:
    """A glyph dropped into any template root is reported, not skipped."""
    (tmp_path / "includes").mkdir()
    (tmp_path / "includes" / "_icon_close.html").write_text("<svg></svg>")
    (tmp_path / "deep" / "partials").mkdir(parents=True)
    (tmp_path / "deep" / "partials" / "_icon_new.html").write_text("<svg></svg>")
    found = _icon_partials_in([tmp_path])
    assert _unregistered(found) == ["deep/partials/_icon_new.html"]


@pytest.mark.parametrize("icon", ICON_PARTIALS, ids=lambda i: i.name)
def test_default_size_matches_the_partial(icon: IconPartial) -> None:
    """The card's "default" caption is the size the partial really falls back to."""
    source = _FOUND[icon.partial].read_text(encoding="utf-8")
    match = _DEFAULT_SIZE_RE.search(source)
    assert match, f"{icon.partial}: no size default found"
    assert int(match.group(1)) == icon.default_size


@pytest.mark.parametrize("icon", ICON_PARTIALS, ids=lambda i: i.name)
def test_parameters_match_the_partial_header(icon: IconPartial) -> None:
    """The parameters listed on the card are the ones the header documents."""
    documented = _documented_parameters(_header(_FOUND[icon.partial]))
    assert [p.name for p in icon.parameters] == documented


@pytest.mark.parametrize("icon", ICON_PARTIALS, ids=lambda i: i.name)
def test_every_icon_takes_svg_class(icon: IconPartial) -> None:
    """The decision record's optional ``svg_class`` reaches the ``<svg>``.

    docs/decisions/ui-icons-are-house-stroke-partials.md gives every icon
    partial the parameter, so a caller never hard-codes a wrapper to style
    one glyph (SNOW-1433).
    """
    assert "svg_class" in [p.name for p in icon.parameters]
    source = _FOUND[icon.partial].read_text(encoding="utf-8")
    assert '{% if svg_class %}class="{{ svg_class }}"{% endif %}' in source


def test_documented_parameters_skips_wrapped_lines() -> None:
    """A continuation line under a parameter is not read as another one."""
    header = (
        "Intro.\n\n"
        "Parameters (passed via include):\n"
        "  size      (int, optional) — rendered px. Default 16, the\n"
        "            banner strip's glyph size.\n"
        "  svg_class (string, optional) — classes on the <svg>.\n"
    )
    assert _documented_parameters(header) == ["size", "svg_class"]


def test_renders_lead_with_the_default_then_the_grid_size() -> None:
    """Every card shows the default, then 24px, then its documented variants."""
    favourite = next(i for i in ICON_PARTIALS if i.name == "favourite")
    captions = [r["caption"] for r in favourite.renders]
    assert captions == ["18 · default", "24 · grid", "22 · panel header", "18 · filled"]
    assert favourite.renders[0]["context"] == {}
    assert favourite.renders[-1]["context"] == {"filled": True}
    assert favourite.include_snippet == '{% include "includes/_icon_favourite.html" %}'


def test_base_context_reaches_every_render() -> None:
    """A partial with a required key gets it in every render, overridable."""
    family = next(i for i in ICON_PARTIALS if i.name == "observation_family")
    contexts = [r["context"] for r in family.renders]
    assert contexts == [
        {"family": "WARNING"},
        {"family": "WARNING", "size": 24},
        {"family": "SURFACE"},
        {"family": "TRAIL"},
    ]
