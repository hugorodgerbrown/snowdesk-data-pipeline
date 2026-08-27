"""
tests/public/test_eaws_contrast.py — WCAG AA guard for the EAWS colour pairs.

The EAWS danger scale is a fixed standard: five backgrounds this project
cannot adjust, three of which are bright and two of which are the same red.
The copy that sits on them is ours to choose, and choosing it wrongly is
invisible in review — a tile reads as "obviously fine" long after it has
stopped clearing 4.5:1.

That is how it happened. ``--color-eaws-high-fg`` and its very-high twin
were #ffffff from the palette's first commit: 4.00:1 on the #ff0000 both
levels share, and 1.07:1 where a split calendar cell painted the same white
across a yellow half. Neither is large text under WCAG (the tiles are
14–15px at 600–700, the calendar cells smaller), so the large-text
allowance never applied. Nothing caught it because the ratios lived only in
a hand-maintained comment beside the tokens; SNOW-739 corrected the values
and added this, so the comment and the palette cannot drift apart again.

Read against FOUNDATION_CATEGORIES rather than main.css: the existing
``check_design_tokens_match_css`` system check already fails when the two
disagree, so the registry is the CSS by the time this runs.
"""

from __future__ import annotations

import pytest

from apps.public.design_tokens import FOUNDATION_CATEGORIES, Token

# WCAG 2.1 AA for normal-size text. Every surface consuming these tokens is
# normal-size — see the module docstring — so the large-text 3:1 allowance
# is deliberately not offered here.
AA_NORMAL_TEXT = 4.5

# (foreground token, background token) — the copy each EAWS level puts on
# its own saturated fill. The five pairs consumed by .danger-tile, .dw-tile
# and .calendar-cell.
EAWS_FG_ON_SOLID: tuple[tuple[str, str], ...] = (
    ("--color-eaws-low-fg", "--color-eaws-low"),
    ("--color-eaws-moderate-fg", "--color-eaws-moderate"),
    ("--color-eaws-considerable-fg", "--color-eaws-considerable"),
    ("--color-eaws-high-fg", "--color-eaws-high"),
    ("--color-eaws-very-high-fg", "--color-eaws-very-high"),
)

# (foreground token, background token) — the copy each level puts on its own
# pale tint, used by .danger-band and .period-transition-chip.
EAWS_TEXT_ON_TINT: tuple[tuple[str, str], ...] = (
    ("--color-eaws-low-text", "--color-eaws-low-tint"),
    ("--color-eaws-moderate-text", "--color-eaws-moderate-tint"),
    ("--color-eaws-considerable-text", "--color-eaws-considerable-tint"),
    ("--color-eaws-high-text", "--color-eaws-high-tint"),
    ("--color-eaws-very-high-text", "--color-eaws-very-high-tint"),
)


def _token_values() -> dict[str, str]:
    """Return every registered token's light-theme value, keyed by name.

    The EAWS tokens are theme-invariant (``dark`` is None on all of them),
    so the light value is the only value.

    Returns:
        Mapping of CSS custom-property name to its literal value.

    """
    values: dict[str, str] = {}
    for category in FOUNDATION_CATEGORIES:
        for token in category.tokens:
            if isinstance(token, Token):
                values[token.name] = token.light
    return values


def _relative_luminance(hex_colour: str) -> float:
    """Return the WCAG relative luminance of a ``#rrggbb`` colour.

    Args:
        hex_colour: A six-digit hex colour, with or without the leading #.

    Returns:
        Relative luminance in the range 0.0–1.0.

    """
    raw = hex_colour.lstrip("#")
    channels = [int(raw[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [
        c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    ]
    red, green, blue = linear
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast_ratio(foreground: str, background: str) -> float:
    """Return the WCAG contrast ratio between two ``#rrggbb`` colours.

    Args:
        foreground: Text colour.
        background: Surface colour behind the text.

    Returns:
        Contrast ratio from 1.0 (identical) to 21.0 (black on white).

    """
    lums = sorted((_relative_luminance(foreground), _relative_luminance(background)))
    return (lums[1] + 0.05) / (lums[0] + 0.05)


def test_relative_luminance_matches_known_values() -> None:
    """Black and white anchor the scale; mid grey lands where WCAG says."""
    assert _relative_luminance("#000000") == pytest.approx(0.0)
    assert _relative_luminance("#ffffff") == pytest.approx(1.0)
    assert _contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)


@pytest.mark.parametrize(("fg_token", "bg_token"), EAWS_FG_ON_SOLID)
def test_eaws_foreground_clears_aa_on_its_solid(fg_token: str, bg_token: str) -> None:
    """Each level's -fg copy clears 4.5:1 on that level's saturated fill."""
    values = _token_values()
    ratio = _contrast_ratio(values[fg_token], values[bg_token])
    assert ratio >= AA_NORMAL_TEXT, (
        f"{fg_token} ({values[fg_token]}) on {bg_token} ({values[bg_token]}) "
        f"is {ratio:.2f}:1, below WCAG AA {AA_NORMAL_TEXT}:1"
    )


@pytest.mark.parametrize(("fg_token", "bg_token"), EAWS_TEXT_ON_TINT)
def test_eaws_text_clears_aa_on_its_tint(fg_token: str, bg_token: str) -> None:
    """Each level's -text copy clears 4.5:1 on that level's pale tint."""
    values = _token_values()
    ratio = _contrast_ratio(values[fg_token], values[bg_token])
    assert ratio >= AA_NORMAL_TEXT, (
        f"{fg_token} ({values[fg_token]}) on {bg_token} ({values[bg_token]}) "
        f"is {ratio:.2f}:1, below WCAG AA {AA_NORMAL_TEXT}:1"
    )


@pytest.mark.parametrize(
    ("fg_token", "bg_token"),
    [
        # A split calendar cell paints ONE level's -fg across both halves of
        # a two-colour gradient, so every -fg has to clear every other
        # level's fill as well as its own. This is the pairing that was
        # worst before SNOW-739: white on the pale-green low half, 1.16:1.
        (fg, bg)
        for fg, _ in EAWS_FG_ON_SOLID
        for _, bg in EAWS_FG_ON_SOLID
    ],
)
def test_every_eaws_foreground_clears_aa_on_every_fill(
    fg_token: str, bg_token: str
) -> None:
    """Any -fg must survive any fill — split cells mix two levels under one copy."""
    values = _token_values()
    ratio = _contrast_ratio(values[fg_token], values[bg_token])
    assert ratio >= AA_NORMAL_TEXT, (
        f"{fg_token} ({values[fg_token]}) on {bg_token} ({values[bg_token]}) "
        f"is {ratio:.2f}:1, below WCAG AA {AA_NORMAL_TEXT}:1 — a split "
        f"calendar cell can pair these"
    )
