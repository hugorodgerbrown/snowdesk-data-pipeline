"""
apps/bulletins/services/day_summary.py — Day-summary explainer matrix.

Provides the one-line explainer that sits beside the day-character label in
the callout at the top of a bulletin page (``templates/includes/
day_character_callout.html``). Where the label answers "what kind of day is
this", the explainer answers "why, and what does it mean for me" — so it
names the bulletin's actual problems and says whether they can be read in
the field.

Matrix key: ``(movement, level, readability)``

- movement:   ``"static"`` (the level holds all day, 96% of the archive),
              ``"rising"``, ``"easing"``, or ``"shifting"`` (the level
              holds across a split but the problem underneath it changes).
- level:      ``1``–``5``. The destination level on a changing day — the
              level the day ends at — and the peak level on a static one.
- readability: ``"readable"`` (every named problem leaves surface evidence),
              ``"hidden"`` (none of them do), ``"mixed"`` (both kinds), or
              ``"quiet"`` (no distinct problem named). Classified over
              every problem the day names, both windows included, so the
              explainer can never omit the hidden problem that the
              day-character label was built from.

Every one of the 80 cells is hand-authored, so no page falls back to
generic copy. Sentences take up to three interpolations — ``%(problems)s``
(the named problems, joined for mid-sentence use), ``%(from_word)s`` and
``%(to_word)s`` (level words for a changing day).

The readability split is the load-bearing distinction and comes straight
from the EAWS problem types: wind slab, new snow, wet snow and cornices all
announce themselves at the surface; persistent weak layers and gliding snow
do not. Field craft works on the first group; only terrain choice works on
the second.

Grounding — a census of the 8,080 committed season mirrors (27 Aug 2026,
scripts in the SNOW day-summary work, method as in
``docs/research/problem-card-redundancy/``):

- 7,768 bulletins (96.1%) never split: ``static`` carries the page.
- Of the 312 that split, 189 rise, 95 hold the level while the problem
  underneath changes, 22 fall, and 6 split with nothing changing at all
  (those are classified ``static`` — see :func:`classify_movement`).
- On 254 of the 312, the arriving problem is wet snow. A split day is, in
  practice, the sun getting to work on the snowpack.
- All 22 falling days replace a dry problem (persistent weak layers or new
  snow) with wet snow. A falling number never means the hazard cleared, so
  the ``easing`` copy says the problem changed, not that the day improved.

This module is pure copy plus classification over primitives: it imports
nothing from ``render_model`` and holds no I/O, so
:func:`~apps.bulletins.services.render_model.compute_day_character` can
call it without a cycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.utils.translation import gettext_lazy as _

if TYPE_CHECKING:
    # ``django_stubs_ext`` is a typing-only dependency; ``from __future__
    # import annotations`` keeps every reference below a forward string at
    # runtime, so the import costs nothing outside the mypy env.
    from django_stubs_ext import StrOrPromise

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

# Incremented whenever a cell's copy or the classification changes, so
# callers can detect stale cached output. Mirrors HEADLINE_MATRIX_VERSION.
DAY_SUMMARY_VERSION: int = 1

# ---------------------------------------------------------------------------
# Problem classification
# ---------------------------------------------------------------------------

# Problems that leave evidence a traveller can read on the day: fresh
# drifting, new snow depth, a wet surface, a corniced ridge.
READABLE_PROBLEMS: frozenset[str] = frozenset(
    {"new_snow", "wind_slab", "wet_snow", "cornices"}
)

# Problems buried in the snowpack. Glide cracks show where, never when, so
# gliding snow sits here with persistent weak layers — matching the
# hard-to-read rule in ``compute_day_character``.
HIDDEN_PROBLEMS: frozenset[str] = frozenset({"persistent_weak_layers", "gliding_snow"})

# Placeholder entries that name no hazard; they never reach %(problems)s.
NEUTRAL_PROBLEMS: frozenset[str] = frozenset(
    {"no_distinct_avalanche_problem", "favourable_situation"}
)

# Lower-case mid-sentence phrasing for the EAWS problem types. Distinct
# from ``_PROBLEM_LABELS`` in apps/public/views.py, which are Title Case
# tag labels — these run inside a sentence.
PROBLEM_PHRASES: dict[str, StrOrPromise] = {
    "new_snow": _("new snow"),
    "wind_slab": _("wind slab"),
    "persistent_weak_layers": _("persistent weak layers"),
    "wet_snow": _("wet snow"),
    "gliding_snow": _("gliding snow"),
    "cornices": _("cornices"),
}

LEVEL_WORDS: dict[int, StrOrPromise] = {
    1: _("low"),
    2: _("moderate"),
    3: _("considerable"),
    4: _("high"),
    5: _("very high"),
}


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify_readability(problem_types: set[str]) -> str:
    """
    Classify a set of problem types by whether the field shows them.

    Args:
        problem_types: CAAML ``problemType`` values for the window being
            described. Neutral placeholders are ignored.

    Returns:
        ``"quiet"``, ``"readable"``, ``"hidden"``, or ``"mixed"``.

    """
    named = {t for t in problem_types if t} - NEUTRAL_PROBLEMS
    if not named:
        return "quiet"
    readable = bool(named & READABLE_PROBLEMS)
    hidden = bool(named & HIDDEN_PROBLEMS)
    if readable and hidden:
        return "mixed"
    return "readable" if readable else "hidden"


def classify_movement(
    direction: str,
    earlier_types: set[str],
    later_types: set[str],
) -> str:
    """
    Classify how the day moves, from its direction and its two problem windows.

    ``direction`` comes from
    :func:`~apps.bulletins.services.render_model.compute_period_transition`
    and is ``""`` when the day carries no split at all.

    A flat-but-split day is ``"shifting"`` only when something under the
    unchanged number actually moves — a new problem type, or the same types
    on new ground. Six bulletins in the archive split with two identical
    windows; those are ``"static"``, because nothing changes for the reader.

    Args:
        direction: ``"rise"``, ``"fall"``, ``"none"``, or ``""``/``None``
            for an unsplit day.
        earlier_types: Problem types named for the earlier window.
        later_types: Problem types named for the later window.

    Returns:
        ``"static"``, ``"rising"``, ``"easing"``, or ``"shifting"``.

    """
    if direction == "rise":
        return "rising"
    if direction == "fall":
        return "easing"
    if direction == "none" and later_types and later_types != earlier_types:
        return "shifting"
    return "static"


def join_problems(problem_types: list[str]) -> str:
    """
    Join named problem types into a mid-sentence phrase.

    Preserves caller order (editorial aggregation order), drops neutral
    placeholders and unknown types, and de-duplicates. One name is returned
    bare; two are joined with "and"; three or more use commas and a final
    "and".

    Args:
        problem_types: CAAML ``problemType`` values in editorial order.

    Returns:
        A phrase such as ``"wind slab and persistent weak layers"``, or
        ``""`` when nothing nameable is present.

    """
    names: list[str] = []
    for problem_type in problem_types:
        phrase = PROBLEM_PHRASES.get(problem_type)
        if phrase is not None and str(phrase) not in names:
            names.append(str(phrase))
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    # Translators: joins the last two items of a problem list.
    return str(_("%(head)s and %(tail)s")) % {
        "head": ", ".join(names[:-1]),
        "tail": names[-1],
    }


# ---------------------------------------------------------------------------
# Transition clauses
# ---------------------------------------------------------------------------

# The opening clause of every ``rising`` and ``easing`` sentence, keyed on
# ``(movement, same_band)``. Direction is ranked on (level, subdivision), so
# an SLF day can move without its digit changing — 45 of the archive's 211
# changing days do exactly that, a fifth of them. Naming both ends there
# would render "moderate this morning, moderate by afternoon", which reads
# as a bug rather than a subdivision. The same-band clause says the move
# happened inside the level instead.
_TRANSITIONS: dict[tuple[str, bool], StrOrPromise] = {
    ("rising", False): _(
        "Deteriorating: %(from_word)s this morning, %(to_word)s by afternoon"
    ),
    ("rising", True): _("Deteriorating within %(to_word)s"),
    ("easing", False): _(
        "Easing: %(from_word)s this morning, %(to_word)s by afternoon"
    ),
    ("easing", True): _("Easing within %(to_word)s"),
}


# ---------------------------------------------------------------------------
# Matrix — 4 movements × 5 levels × 4 readability classes
# ---------------------------------------------------------------------------

_MATRIX: dict[tuple[str, int, str], StrOrPromise] = {
    # ══ STATIC — the level holds all day (7,768 bulletins, 96.1%) ═══════════
    ("static", 1, "quiet"): _(
        "Low, and the bulletin names no distinct problem"
        " — the ordinary care that steep terrain always needs."
    ),
    ("static", 1, "readable"): _(
        "Low, with only %(problems)s named"
        " — visible where it sits, and easy to go round."
    ),
    ("static", 1, "hidden"): _(
        "Low, with %(problems)s buried out of sight"
        " — few places, and nothing at the surface to mark them."
    ),
    ("static", 1, "mixed"): _(
        "Low, with %(problems)s named together"
        " — one of them visible at the surface, the other buried."
    ),
    ("static", 2, "quiet"): _(
        "Moderate, with no distinct problem named"
        " — the level itself is the whole of the message."
    ),
    ("static", 2, "readable"): _(
        "Moderate, with %(problems)s at the surface"
        " — the evidence is there to read before you commit to a slope."
    ),
    ("static", 2, "hidden"): _(
        "Moderate, with %(problems)s buried"
        " — the slope that fails will look like the one that held."
    ),
    ("static", 2, "mixed"): _(
        "Moderate, with %(problems)s in play"
        " — part of it readable at the surface, part of it buried and silent."
    ),
    ("static", 3, "quiet"): _(
        "Considerable, with no distinct problem named"
        " — rare enough that the level alone should set your limits."
    ),
    ("static", 3, "readable"): _(
        "Considerable, with %(problems)s at the surface"
        " — readable, widespread, and unforgiving of a casual line."
    ),
    ("static", 3, "hidden"): _(
        "Considerable, with %(problems)s buried"
        " — no warning underfoot, so terrain choice is the only control left."
    ),
    ("static", 3, "mixed"): _(
        "Considerable, with %(problems)s in play"
        " — you can read the surface problem; the buried one sets the consequence."
    ),
    ("static", 4, "quiet"): _(
        "High, with no distinct problem named"
        " — at this level the number alone rules out avalanche terrain."
    ),
    ("static", 4, "readable"): _(
        "High, with %(problems)s at the surface"
        " — visible everywhere, and past what route choice can offset."
    ),
    ("static", 4, "hidden"): _(
        "High, with %(problems)s buried"
        " — remote triggering is expected, and nothing at the surface will warn you."
    ),
    ("static", 4, "mixed"): _(
        "High, with %(problems)s in play"
        " — the readable half is the smaller half; stay out of avalanche terrain."
    ),
    ("static", 5, "quiet"): _(
        "Very high, with no distinct problem named"
        " — an exceptional day, and avalanche terrain is closed."
    ),
    ("static", 5, "readable"): _(
        "Very high, with %(problems)s at the surface"
        " — large natural releases, running beyond the usual paths."
    ),
    ("static", 5, "hidden"): _(
        "Very high, with %(problems)s buried"
        " — the snowpack is failing at depth, and runout zones are exposed."
    ),
    ("static", 5, "mixed"): _(
        "Very high, with %(problems)s in play"
        " — natural releases at every scale; stay out of avalanche terrain."
    ),
    # ══ RISING — the level climbs through the day (189 bulletins) ═════════
    #    The opening clause is %(transition)s so a subdivision-only move
    #    does not render "moderate this morning, moderate by afternoon".
    ("rising", 1, "quiet"): _(
        "%(transition)s, no distinct problem named at either end of the day."
    ),
    ("rising", 1, "readable"): _(
        "%(transition)s,"
        " with %(problems)s at the surface"
        " — visible, and still confined to isolated places."
    ),
    ("rising", 1, "hidden"): _(
        "%(transition)s,"
        " with %(problems)s buried — nothing at the surface gives it away."
    ),
    ("rising", 1, "mixed"): _(
        "%(transition)s,"
        " with %(problems)s in play — one part of it visible, one part buried."
    ),
    ("rising", 2, "quiet"): _(
        "%(transition)s, with no distinct problem named for either half."
    ),
    ("rising", 2, "readable"): _(
        "%(transition)s, with %(problems)s at the surface — you can watch it build."
    ),
    ("rising", 2, "hidden"): _(
        "%(transition)s,"
        " with %(problems)s buried — nothing at the surface will show the change."
    ),
    ("rising", 2, "mixed"): _(
        "%(transition)s, with %(problems)s in play — you will see part of it, not all."
    ),
    ("rising", 3, "quiet"): _(
        "%(transition)s, with no distinct problem named either side."
    ),
    ("rising", 3, "readable"): _(
        "%(transition)s,"
        " with %(problems)s at the surface — turn round before it gets there."
    ),
    ("rising", 3, "hidden"): _(
        "%(transition)s,"
        " with %(problems)s buried — time the exit rather than feel for it."
    ),
    ("rising", 3, "mixed"): _(
        "%(transition)s, with %(problems)s in play — only half of it will be visible."
    ),
    ("rising", 4, "quiet"): _(
        "%(transition)s, no distinct problem named, and no margin left by then."
    ),
    ("rising", 4, "readable"): _(
        "%(transition)s,"
        " with %(problems)s at the surface"
        " — be out of avalanche terrain before it lands."
    ),
    ("rising", 4, "hidden"): _(
        "%(transition)s, with %(problems)s buried — past what timing can manage."
    ),
    ("rising", 4, "mixed"): _(
        "%(transition)s,"
        " with %(problems)s in play — the afternoon is an exposure, not a plan."
    ),
    ("rising", 5, "quiet"): _(
        "%(transition)s, no distinct problem named, and no version of the day"
        " that stays out."
    ),
    ("rising", 5, "readable"): _(
        "%(transition)s, with %(problems)s at the surface — do not be out in it."
    ),
    ("rising", 5, "hidden"): _(
        "%(transition)s, with %(problems)s buried — failures at depth, on every scale."
    ),
    ("rising", 5, "mixed"): _(
        "%(transition)s, with %(problems)s in play — stay out of avalanche terrain."
    ),
    # ══ EASING — the level falls (22 bulletins; every one swaps a dry ══════
    #    problem for wet snow, so the copy never claims the day improved)
    ("easing", 1, "quiet"): _(
        "%(transition)s, with no distinct problem named by then."
    ),
    ("easing", 1, "readable"): _(
        "%(transition)s,"
        " with %(problems)s at the surface"
        " — visible, and worth respecting all the same."
    ),
    ("easing", 1, "hidden"): _(
        "%(transition)s,"
        " with %(problems)s buried — a lower number, not a clean snowpack."
    ),
    ("easing", 1, "mixed"): _(
        "%(transition)s,"
        " with %(problems)s in play — the number falls further than the hazard does."
    ),
    ("easing", 2, "quiet"): _(
        "%(transition)s, with no distinct problem named for the later half."
    ),
    ("easing", 2, "readable"): _(
        "%(transition)s,"
        " with %(problems)s at the surface"
        " — an easing rating, a different problem behind it."
    ),
    ("easing", 2, "hidden"): _(
        "%(transition)s,"
        " with %(problems)s buried — the number drops, the weak layer does not."
    ),
    ("easing", 2, "mixed"): _(
        "%(transition)s,"
        " with %(problems)s in play"
        " — the number eases, the problem swaps rather than clears."
    ),
    ("easing", 3, "quiet"): _(
        "%(transition)s, with no distinct problem named at the lower level."
    ),
    ("easing", 3, "readable"): _(
        "%(transition)s,"
        " with %(problems)s at the surface"
        " — still considerable, still no place to be casual."
    ),
    ("easing", 3, "hidden"): _(
        "%(transition)s,"
        " with %(problems)s buried"
        " — less danger on paper, the same weakness underneath."
    ),
    ("easing", 3, "mixed"): _(
        "%(transition)s,"
        " with %(problems)s in play — an easing rating, not a safer snowpack."
    ),
    ("easing", 4, "quiet"): _(
        "%(transition)s, still high, and still no distinct problem named."
    ),
    ("easing", 4, "readable"): _(
        "%(transition)s,"
        " with %(problems)s at the surface — a falling number is not a green light."
    ),
    ("easing", 4, "hidden"): _(
        "%(transition)s,"
        " with %(problems)s buried — high is high, whichever way it arrived."
    ),
    ("easing", 4, "mixed"): _(
        "%(transition)s,"
        " with %(problems)s in play"
        " — a level that still closes the terrain, whichever way it moved."
    ),
    ("easing", 5, "quiet"): _(
        "%(transition)s, the number moves, the situation does not, and the"
        " terrain is closed."
    ),
    ("easing", 5, "readable"): _(
        "%(transition)s,"
        " with %(problems)s at the surface"
        " — the number moves, the snow on the surface does not."
    ),
    ("easing", 5, "hidden"): _(
        "%(transition)s,"
        " with %(problems)s buried — the number moves, the snowpack does not."
    ),
    ("easing", 5, "mixed"): _(
        "%(transition)s,"
        " with %(problems)s in play — the number moves, nothing under it does."
    ),
    # ══ SHIFTING — the level holds, the problem under it changes (95) ══════
    ("shifting", 1, "quiet"): _(
        "Low through both halves of the day, with no distinct problem named in either."
    ),
    ("shifting", 1, "readable"): _(
        "Low all day, with %(problems)s across the two halves"
        " — the number holds; which problem you face does not."
    ),
    ("shifting", 1, "hidden"): _(
        "Low all day, with %(problems)s underneath"
        " — the number holds, and nothing at the surface marks the change."
    ),
    ("shifting", 1, "mixed"): _(
        "Low all day, with %(problems)s across the two halves"
        " — the number holds, the mix underneath it changes."
    ),
    ("shifting", 2, "quiet"): _(
        "Moderate all day, with no distinct problem named"
        " — the two halves differ in timing, not in hazard."
    ),
    ("shifting", 2, "readable"): _(
        "Moderate all day, with %(problems)s across the two halves"
        " — same number morning and afternoon, different problem behind it."
    ),
    ("shifting", 2, "hidden"): _(
        "Moderate all day, with %(problems)s buried"
        " — the number holds while the problem moves out of sight."
    ),
    ("shifting", 2, "mixed"): _(
        "Moderate all day, with %(problems)s across the two halves"
        " — the level holds; what changes is what sits under it."
    ),
    ("shifting", 3, "quiet"): _(
        "Considerable all day, with no distinct problem named"
        " — the split marks timing, not a change in level."
    ),
    ("shifting", 3, "readable"): _(
        "Considerable all day, with %(problems)s across the two halves"
        " — the number holds; the reason for it changes."
    ),
    ("shifting", 3, "hidden"): _(
        "Considerable all day, with %(problems)s buried"
        " — an unchanged number over a problem you cannot inspect."
    ),
    ("shifting", 3, "mixed"): _(
        "Considerable all day, with %(problems)s across the two halves"
        " — same level, different problem, different right answer."
    ),
    ("shifting", 4, "quiet"): _(
        "High all day, with no distinct problem named"
        " — the split changes nothing you would act on."
    ),
    ("shifting", 4, "readable"): _(
        "High all day, with %(problems)s across the two halves"
        " — the number holds above travel limits either way."
    ),
    ("shifting", 4, "hidden"): _(
        "High all day, with %(problems)s buried"
        " — the number holds, and so does the reason to stay out."
    ),
    ("shifting", 4, "mixed"): _(
        "High all day, with %(problems)s across the two halves"
        " — the level never drops far enough to matter."
    ),
    ("shifting", 5, "quiet"): _(
        "Very high all day, with no distinct problem named"
        " — nothing in the split changes the answer."
    ),
    ("shifting", 5, "readable"): _(
        "Very high all day, with %(problems)s through both halves"
        " — the terrain is closed either way."
    ),
    ("shifting", 5, "hidden"): _(
        "Very high all day, with %(problems)s buried"
        " — the level holds at the top of the scale."
    ),
    ("shifting", 5, "mixed"): _(
        "Very high all day, with %(problems)s across the two halves"
        " — no part of the day is travellable."
    ),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def summary_for(
    movement: str,
    level: int,
    problem_types: list[str],
    from_level: int | None = None,
) -> str:
    """
    Return the day-summary explainer for one bulletin's profile.

    Looks up ``(movement, level, readability)`` in the hand-authored
    :data:`_MATRIX` and interpolates the named problems and level words.
    Every cell of the grid is populated, so a valid key always resolves;
    an out-of-range level is clamped rather than falling back to generic
    copy.

    Args:
        movement: ``"static"``, ``"rising"``, ``"easing"``, or
            ``"shifting"``, as returned by :func:`classify_movement`.
        level: Destination danger level (1–5) on a changing day, peak
            level on a static one.
        problem_types: The window's CAAML ``problemType`` values in
            editorial order. Drives both the readability class and the
            ``%(problems)s`` interpolation.
        from_level: Source danger level — the level the day starts at.
            Selects the ``rising``/``easing`` opening clause from
            :data:`_TRANSITIONS`: naming both ends when the digit moves,
            and saying the move happened inside the level when it does
            not. Defaults to *level* when absent.

    Returns:
        A translated one-line explainer.

    """
    level = min(5, max(1, level))
    source = min(5, max(1, from_level or level))
    readability = classify_readability(set(problem_types))
    template = _MATRIX[(movement, level, readability)]

    words = {
        "problems": join_problems(problem_types),
        "from_word": str(LEVEL_WORDS[source]),
        "to_word": str(LEVEL_WORDS[level]),
    }
    transition = _TRANSITIONS.get((movement, source == level))
    if transition is not None:
        words["transition"] = str(transition) % words
    return str(template) % words
