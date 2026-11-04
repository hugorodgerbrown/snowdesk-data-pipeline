"""
tests/templates/includes/test_icon_close.py — every dismiss draws the house ×.

``includes/_icon_close.html`` is the close glyph. Six dismiss buttons typed
a "×" character instead — a font's glyph, a different weight and size in
each, beside the stroked × the sheets and panels draw (Hugo, 4 Nov 2026:
tidy the one-off icon tap targets). This pins each to the partial: the
button holds the glyph's two strokes and no typed character.
"""

from __future__ import annotations

import re

import pytest
from django.template.loader import render_to_string

CLOSE_STROKES = ('d="M18 6 6 18"', 'd="m6 6 12 12"')

CASES: list[tuple[str, dict[str, object]]] = [
    (
        "includes/_overlay_banner.html",
        {"variant": "strip", "body": "Archive.", "dismissible": True},
    ),
    (
        "includes/_overlay_banner.html",
        {"variant": "floating", "body": "Off the map.", "dismissible": True},
    ),
    ("includes/_toast.html", {"kind": "info", "body": "Note."}),
    ("includes/_toast_banner.html", {}),
    ("includes/_pwa_install_prompt.html", {}),
    ("includes/_debug_log_panel.html", {}),
]


def _dismiss_button(html: str) -> str:
    """Return the dismiss/close button's markup, its opening tag to its end.

    Args:
        html: The rendered template.

    Returns:
        The first button labelled Dismiss or Close.

    """
    match = re.search(
        r'<button\b[^>]*aria-label="(?:Dismiss|Close)"[^>]*>.*?</button>', html, re.S
    )
    assert match is not None
    return match.group(0)


@pytest.mark.parametrize(("template", "context"), CASES)
def test_dismiss_draws_the_house_close_glyph(
    template: str, context: dict[str, object]
) -> None:
    """The button holds _icon_close.html's strokes, not a typed character."""
    button = _dismiss_button(render_to_string(template, context))
    for stroke in CLOSE_STROKES:
        assert stroke in button
    text = re.sub(r"<[^>]+>", "", button).strip()
    assert text == ""
    assert "&times;" not in button
