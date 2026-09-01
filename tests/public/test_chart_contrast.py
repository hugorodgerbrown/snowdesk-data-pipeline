"""
tests/public/test_chart_contrast.py — contrast guard for the meteogram palette.

The hourly chart's colours went into the design-token registry in SNOW-790,
which is what makes this guard possible: the registry is the CSS by the time
this runs, because ``check_design_tokens_match_css`` already fails
``manage.py check`` when the two disagree. Before that the chart's palette
lived in ``main.css`` alone, reachable by no test at all.

**These numbers were got wrong twice while the ticket was open**, which is
the whole reason the file exists. The axis bar's first dark pair held 2.0:1
between night and daylight and the two segments were hard to tell apart; the
now-pin was a flat ink scoring 1.2:1 where it crossed the lit segment in
dark. Both looked fine in a screenshot. Neither was.

Two thresholds, and they are not the same rule:

* **4.5:1** for the legend note, which is text on a fill (WCAG 1.4.3).
* **3:1** for the axis bar, whose fills are not text but *are* adjacent
  areas carrying different meanings — night versus day, and the pin against
  whichever of the two it lands on (WCAG 1.4.11 non-text contrast).

``--color-chart-snow`` is deliberately absent: it is ``rgba(…, 0.35)``, so
its effective colour is whatever sits behind it and a fixed ratio would be
a fiction.
"""

from __future__ import annotations

import pytest

from apps.public.design_tokens import FOUNDATION_CATEGORIES, Token
from tests.public._contrast import contrast_ratio

# WCAG 2.1 AA. Text on a fill; the legend note is 14px, so no large-text
# allowance applies.
AA_NORMAL_TEXT = 4.5

# WCAG 2.1 AA 1.4.11. Adjacent areas whose difference carries meaning.
AA_NON_TEXT = 3.0


def _token(name: str) -> Token:
    """
    Return one registered token by CSS custom-property name.

    Args:
        name: The property name, including the leading ``--``.

    Returns:
        The registered token.

    Raises:
        AssertionError: When the token is not in the registry — which is
            itself the finding, since an unregistered token is one no
            guard can see.

    """
    for category in FOUNDATION_CATEGORIES:
        for token in category.tokens:
            if isinstance(token, Token) and token.name == name:
                return token
    raise AssertionError(f"{name} is not in the design-token registry")


def _value(name: str, theme: str) -> str:
    """
    Return a token's value in one theme.

    Args:
        name: The property name, including the leading ``--``.
        theme: Either ``"light"`` or ``"dark"``.

    Returns:
        The literal colour. A token with no dark value is theme-invariant,
        so its light value is its value in both.

    """
    token = _token(name)
    return token.light if theme == "light" else (token.dark or token.light)


# The card each surface sits on, per theme — not a chart token, but half of
# every ratio below.
CARD = {"light": "#ffffff", "dark": "#2a2825"}

# The pin's core, which is --color-text-1.
PIN_CORE = {"light": "#1a1916", "dark": "#edece8"}


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_night_and_daylight_are_distinguishable(theme: str) -> None:
    """
    The two halves of the axis bar carry different meanings.

    Adjacent fills, so 1.4.11 applies. The first dark pair tried in
    SNOW-790 (#55606f / #d97706) held 2.0:1 and the segments read as one
    bar; toning the light night down a further step would have dropped
    that pair to 2.2:1.
    """
    ratio = contrast_ratio(
        _value("--color-chart-night", theme),
        _value("--color-chart-daylight", theme),
    )

    assert ratio >= AA_NON_TEXT, f"{theme}: night vs daylight is {ratio:.2f}:1"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_bar_separates_from_the_card_it_sits_on(theme: str) -> None:
    """
    A bar the width of the card must not sink into it.

    Checked on the night ground, which is the darker fill in light mode
    and therefore the one at risk on a white card — and the one at risk
    on the dark card for the opposite reason.
    """
    ratio = contrast_ratio(_value("--color-chart-night", theme), CARD[theme])

    assert ratio >= AA_NON_TEXT, f"{theme}: night vs card is {ratio:.2f}:1"


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("fill", ["--color-chart-night", "--color-chart-daylight"])
def test_the_now_pin_reads_on_whichever_fill_it_crosses(theme: str, fill: str) -> None:
    """
    The pin is two tones because no single flat colour can do this.

    In light mode, separating from slate-600 needs a relative luminance of
    at least 0.358 while separating from amber-500 needs either 1.54 —
    brighter than white — or at most 0.127. No number satisfies both, so
    the pin is a text-1 core inside a card-coloured outline and the
    assertion is that AT LEAST ONE of the two carries it.

    A flat pin passed this on one fill and scored 1.2:1 on the other.
    """
    background = _value(fill, theme)
    core = contrast_ratio(PIN_CORE[theme], background)
    halo = contrast_ratio(CARD[theme], background)

    assert max(core, halo) >= AA_NON_TEXT, (
        f"{theme}: on {fill} the pin's core is {core:.2f}:1 and its halo "
        f"is {halo:.2f}:1 — neither carries the mark"
    )


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_legend_note_ink_clears_aa_on_its_own_fill(theme: str) -> None:
    """
    Text on a fill, so the full 4.5:1 applies.

    The note was a bare ``text-accent`` on this fill until SNOW-790. That
    is 4.62:1 in light and would have been 3.84:1 once the fill gained a
    dark value — which is why the ink got a token of its own rather than
    the background being darkened alone.
    """
    ratio = contrast_ratio(
        _value("--color-chart-note-text", theme),
        _value("--color-chart-note-bg", theme),
    )

    assert ratio >= AA_NORMAL_TEXT, f"{theme}: note ink is {ratio:.2f}:1"


def test_the_translucent_snow_fill_is_not_guarded_here() -> None:
    """
    ``--color-chart-snow`` is rgba, so it has no fixed ratio.

    Pinned as a statement rather than left as a silent gap: its effective
    colour is whatever it is drawn over, and a number computed from the
    literal would be a fiction. If it ever becomes opaque, it belongs in
    the checks above.
    """
    assert _token("--color-chart-snow").light.startswith("rgba(")
