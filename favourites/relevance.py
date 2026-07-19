"""
favourites/relevance.py — Elevation-aware avalanche-problem highlighting.

Annotates a bulletin's already-resolved problem cards with an altitude
verdict relative to a favourite's pin elevation (SNOW-422). This is a
highlight, never a filter: every problem the bulletin publishes is always
shown; the verdict only adds an "applies at this altitude" / "above this
location" / "below this location" annotation.

Copy surfaced from these verdicts must stay altitude-relative — never
safety-relative ("safe", "not at risk", "you're fine" are forbidden
anywhere downstream of this module). Problems with no elevation band data,
or whose bound pivots on "treeline" rather than a numeric metre value, are
left unannotated — there is no honest altitude verdict to give.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from public.views import ElevationBounds

RELEVANCE_APPLIES = "APPLIES"
RELEVANCE_ABOVE = "ABOVE"
RELEVANCE_BELOW = "BELOW"


def band_relevance(
    pin_elevation: float, lower: int | None, upper: int | None
) -> str | None:
    """
    Return the altitude verdict for a pin against a numeric elevation band.

    Args:
        pin_elevation: The favourite's elevation in metres.
        lower: The band's lower bound in metres, or ``None`` when absent.
        upper: The band's upper bound in metres, or ``None`` when absent.

    Returns:
        ``None`` when neither bound is present (no band data to compare
        against); ``RELEVANCE_ABOVE`` when the band sits above the pin;
        ``RELEVANCE_BELOW`` when the band sits below the pin;
        ``RELEVANCE_APPLIES`` otherwise (including when the pin sits exactly
        on a bound — the boundary is inclusive).

    """
    if lower is None and upper is None:
        return None
    if lower is not None and pin_elevation < lower:
        return RELEVANCE_ABOVE
    if upper is not None and pin_elevation > upper:
        return RELEVANCE_BELOW
    return RELEVANCE_APPLIES


def _relevance_from_bounds(
    pin_elevation: float, elevation: "ElevationBounds | None"
) -> str | None:
    """
    Adapt a problem card's ``ElevationBounds``-like value into a verdict.

    Duck-typed on ``.bound_type`` / ``.lower`` / ``.upper`` so callers don't
    need to import ``public.views.ElevationBounds`` — any object with that
    shape works. Non-numeric bound strings (e.g. ``"treeline"``) leave the
    problem unannotated rather than fabricate an altitude verdict, per the
    highlight-never-suppress, never-fabricate ticket constraint.

    Args:
        pin_elevation: The favourite's elevation in metres.
        elevation: The card's ``elevation`` value, or ``None``.

    Returns:
        A verdict string, or ``None`` when the card carries no usable band.

    """
    if not elevation or elevation.bound_type == "":
        return None

    lower_raw = elevation.lower
    upper_raw = elevation.upper
    if (lower_raw and not str(lower_raw).isdigit()) or (
        upper_raw and not str(upper_raw).isdigit()
    ):
        return None

    lower = int(lower_raw) if lower_raw else None
    upper = int(upper_raw) if upper_raw else None
    return band_relevance(pin_elevation, lower, upper)


def annotate_problem_relevance(
    cards: list[dict[str, Any]], pin_elevation: float
) -> list[dict[str, Any]]:
    """
    Annotate each problem card with an ``altitude_relevance`` verdict.

    Never filters, reorders, or drops a card — the returned list always has
    the same length as ``cards`` (highlight-never-suppress).

    Args:
        cards: Problem cards as produced by
            ``public.views.problem_cards_for_bulletin`` (each carries an
            ``elevation`` key holding an ``ElevationBounds``-like value).
        pin_elevation: The favourite's elevation in metres.

    Returns:
        A new list of card dicts, each extended with an
        ``altitude_relevance`` key (one of ``RELEVANCE_APPLIES``,
        ``RELEVANCE_ABOVE``, ``RELEVANCE_BELOW``, or ``None``).

    """
    return [
        {
            **card,
            "altitude_relevance": _relevance_from_bounds(
                pin_elevation, card.get("elevation")
            ),
        }
        for card in cards
    ]
