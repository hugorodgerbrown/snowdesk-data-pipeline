"""
tests/public/test_eaws_contrast.py — contrast guard for the EAWS colour pairs.

The EAWS danger scale is a fixed standard: five backgrounds this project
cannot adjust, three of which are bright and two of which are the same red.
The copy that sits on them is ours to choose, and choosing it wrongly is
invisible in review — a tile reads as "obviously fine" long after it has
stopped clearing 4.5:1.

Levels 4 and 5 carry white, which is 4.00:1 on #ff0000 and so does not
clear AA. That is deliberate: it is SLF's own pairing on its danger-level
legend, and this app renders SLF's bulletins (Hugo, SNOW-739). #ff0000
admits no compliant light ink at all — white is the lightest colour there
is and it reaches 4.00 — so the real choice was white against near-black,
and near-black on EAWS red reads worse than the number it would satisfy.
``KNOWN_EXCEPTIONS`` pins both pairs, so the exception stays deliberate,
stays two, and cannot quietly grow a third.

Everything else must pass, including the case that has no SLF precedent: a
split calendar cell paints ONE ink across a two-colour gradient, so its ink
has to clear every fill, not just its own. Before SNOW-739 those cells took
a level's own -fg and put white over a yellow half at 1.07:1.

Read against FOUNDATION_CATEGORIES rather than main.css: the existing
``check_design_tokens_match_css`` system check already fails when the two
disagree, so the registry is the CSS by the time this runs.
"""

from __future__ import annotations

import pytest

from apps.public.design_tokens import FOUNDATION_CATEGORIES, Token

# WCAG 2.1 AA for normal-size text. Every surface consuming these tokens is
# normal-size (tiles are 14–15px at 600–700, calendar cells smaller), so the
# large-text 3:1 allowance is deliberately not offered here.
AA_NORMAL_TEXT = 4.5

EAWS_FILLS: tuple[str, ...] = (
    "--color-eaws-low",
    "--color-eaws-moderate",
    "--color-eaws-considerable",
    "--color-eaws-high",
    "--color-eaws-very-high",
)

# The ink each level puts on its own saturated fill — .danger-tile, .dw-tile,
# .panel-title and the solid calendar cells.
EAWS_FG_ON_OWN_FILL: tuple[tuple[str, str], ...] = tuple(
    (f"{fill}-fg", fill) for fill in EAWS_FILLS
)

# Pairs that knowingly fall short, and why. Matching the issuing service was
# judged to matter more than the ratio on exactly these two.
KNOWN_EXCEPTIONS: dict[tuple[str, str], str] = {
    ("--color-eaws-high-fg", "--color-eaws-high"): (
        "SLF renders level 4 as white on EAWS red; #ff0000 admits no "
        "compliant light ink"
    ),
    ("--color-eaws-very-high-fg", "--color-eaws-very-high"): (
        "SLF renders level 5 as white on EAWS red; #ff0000 admits no "
        "compliant light ink"
    ),
}

# The ink each level puts on its own pale tint — .danger-band and
# .period-transition-chip. No exceptions here: the tints are ours to pick.
EAWS_TEXT_ON_TINT: tuple[tuple[str, str], ...] = tuple(
    (f"{fill}-text", f"{fill}-tint") for fill in EAWS_FILLS
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
    """Black and white anchor the scale; the extremes land where WCAG says."""
    assert _relative_luminance("#000000") == pytest.approx(0.0)
    assert _relative_luminance("#ffffff") == pytest.approx(1.0)
    assert _contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)


@pytest.mark.parametrize(("fg_token", "bg_token"), EAWS_FG_ON_OWN_FILL)
def test_eaws_foreground_clears_aa_on_its_own_fill(
    fg_token: str, bg_token: str
) -> None:
    """Each level's ink clears 4.5:1 on its own fill, bar the SLF exceptions."""
    values = _token_values()
    ratio = _contrast_ratio(values[fg_token], values[bg_token])
    if (fg_token, bg_token) in KNOWN_EXCEPTIONS:
        pytest.skip(f"deliberate: {KNOWN_EXCEPTIONS[fg_token, bg_token]}")
    assert ratio >= AA_NORMAL_TEXT, (
        f"{fg_token} ({values[fg_token]}) on {bg_token} ({values[bg_token]}) "
        f"is {ratio:.2f}:1, below WCAG AA {AA_NORMAL_TEXT}:1"
    )


def test_the_slf_parity_exceptions_are_still_exactly_the_known_two() -> None:
    """Every shortfall on a fill is one of the two SLF-parity pairs.

    The skip above would hide a NEW failure introduced on a different level,
    so the set of shortfalls is asserted whole. Adding a third means either
    fixing it or recording it in KNOWN_EXCEPTIONS with its reason.
    """
    values = _token_values()
    failing = {
        (fg, bg)
        for fg, bg in EAWS_FG_ON_OWN_FILL
        if _contrast_ratio(values[fg], values[bg]) < AA_NORMAL_TEXT
    }
    assert failing == set(KNOWN_EXCEPTIONS), (
        f"AA shortfalls on EAWS fills are {sorted(failing)}, expected exactly "
        f"the recorded exceptions {sorted(KNOWN_EXCEPTIONS)}"
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


@pytest.mark.parametrize("bg_token", EAWS_FILLS)
def test_mixed_level_ink_clears_aa_on_every_fill(bg_token: str) -> None:
    """The split-cell ink clears every fill — it is painted across two.

    A split calendar cell has no dominant half to tune for, and no SLF
    equivalent to match, so this one gets no exception.
    """
    values = _token_values()
    ratio = _contrast_ratio(values["--color-eaws-mixed-fg"], values[bg_token])
    assert ratio >= AA_NORMAL_TEXT, (
        f"--color-eaws-mixed-fg ({values['--color-eaws-mixed-fg']}) on "
        f"{bg_token} ({values[bg_token]}) is {ratio:.2f}:1, below WCAG AA "
        f"{AA_NORMAL_TEXT}:1 — a split cell can pair these"
    )
