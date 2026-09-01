"""
tests/public/test_eaws_contrast.py — contrast guard for the EAWS colour pairs.

The EAWS danger scale is a fixed standard: five backgrounds this project
cannot adjust, three of which are bright and two of which are the same red.
The copy that sits on them is ours to choose, and choosing it wrongly is
invisible in review — a tile reads as "obviously fine" long after it has
stopped clearing 4.5:1.

Level 4 carries white, which is 4.00:1 on #ff0000 and so does not clear
AA. That is deliberate: it is SLF's own pairing on its danger-level
legend, and this app renders SLF's bulletins (Hugo, SNOW-739). #ff0000
admits no compliant light ink at all — white is the lightest colour there
is and it reaches 4.00 — so the real choice was white against near-black,
and near-black on EAWS red reads worse than the number it would satisfy.
``KNOWN_EXCEPTIONS`` pins the pair, so the exception stays deliberate,
stays one, and cannot quietly grow a second.

Level 5 needs no exception: SLF darkens it to #820100 where EAWS gives it
no colour of its own, and white on that is 10.75:1.

Everything else must pass, including the case that has no SLF precedent: a
split calendar cell paints ONE ink across a two-colour gradient, so its ink
has to clear both fills, not just one. Before SNOW-739 those cells took a
level's own -fg and put white over a yellow half at 1.07:1.

Read against FOUNDATION_CATEGORIES rather than main.css: the existing
``check_design_tokens_match_css`` system check already fails when the two
disagree, so the registry is the CSS by the time this runs.
"""

from __future__ import annotations

import pytest

from apps.public.design_tokens import FOUNDATION_CATEGORIES, Token
from tests.public._contrast import (
    contrast_ratio as _contrast_ratio,
    relative_luminance as _relative_luminance,
)

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
# judged to matter more than the ratio on exactly these.
KNOWN_EXCEPTIONS: dict[tuple[str, str], str] = {
    ("--color-eaws-high-fg", "--color-eaws-high"): (
        "SLF renders level 4 as white on EAWS red; #ff0000 admits no "
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


def test_the_slf_parity_exceptions_are_still_exactly_the_recorded_set() -> None:
    """Every shortfall on a fill is one of the recorded SLF-parity pairs.

    The skip above would hide a NEW failure introduced on a different level,
    so the set of shortfalls is asserted whole. One more means either fixing
    it or recording it in KNOWN_EXCEPTIONS with its reason.
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


# The four fills a split cell can pair without involving level 5. Its ink is
# painted across both halves, so it has to clear every one of them.
LIGHT_FILLS: tuple[str, ...] = tuple(
    fill for fill in EAWS_FILLS if fill != "--color-eaws-very-high"
)


@pytest.mark.parametrize("bg_token", LIGHT_FILLS)
def test_mixed_level_ink_clears_aa_on_every_light_fill(bg_token: str) -> None:
    """The split-cell ink clears every fill it can be asked to sit on.

    A split calendar cell has no dominant half to tune for and no SLF
    equivalent to match, so among levels 1–4 it gets no exception.
    """
    values = _token_values()
    ratio = _contrast_ratio(values["--color-eaws-mixed-fg"], values[bg_token])
    assert ratio >= AA_NORMAL_TEXT, (
        f"--color-eaws-mixed-fg ({values['--color-eaws-mixed-fg']}) on "
        f"{bg_token} ({values[bg_token]}) is {ratio:.2f}:1, below WCAG AA "
        f"{AA_NORMAL_TEXT}:1 — a split cell can pair these"
    )


def test_a_level_5_split_cell_takes_white_because_no_ink_serves_both() -> None:
    """Pairing 5 with a light fill is unservable, and unreachable in practice.

    #820100 is dark and #ccff66 is bright: an ink light enough for one is
    too light for the other, so no flat colour clears 4.5:1 on both. The
    CSS gives those cells white — right for the only realistic pairing
    (4 with 5, 4.00:1, the same value as the High exception) and wrong for
    the rest, which would need a region to be at level 1 and level 5 on the
    same day. Across a full season of all three providers — the 8,080
    bulletins in apps/bulletins/local_mirrors/ — exactly one carries a
    level-5 rating, and it is level 5 throughout: a solid cell.

    This test records the arithmetic rather than asserting a pass, so the
    gap is visible to whoever next reads the palette.
    """
    values = _token_values()
    mixed = _contrast_ratio(
        values["--color-eaws-mixed-fg"], values["--color-eaws-very-high"]
    )
    white_on_5 = _contrast_ratio(
        values["--color-eaws-very-high-fg"], values["--color-eaws-very-high"]
    )
    white_on_4 = _contrast_ratio(
        values["--color-eaws-very-high-fg"], values["--color-eaws-high"]
    )
    white_on_1 = _contrast_ratio(
        values["--color-eaws-very-high-fg"], values["--color-eaws-low"]
    )
    # Near-black is not an option on level 5 — this is why the CSS flips.
    assert mixed < AA_NORMAL_TEXT
    # White serves the 4+5 cell at the same ratio the High exception accepts.
    assert white_on_5 >= AA_NORMAL_TEXT
    assert white_on_4 == pytest.approx(4.0, abs=0.05)
    # And cannot serve a 1+5 cell, which no bulletin has ever produced.
    assert white_on_1 < AA_NORMAL_TEXT
