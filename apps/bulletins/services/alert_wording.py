"""
apps/bulletins/services/alert_wording.py — The words of a bulletin alert.

Renders one or more A1 changes (``bulletin_change.py``) into the title,
body and link of one notification (SNOW-940). The same message is sent as a
push and, as the fallback, in the alert email; the replay harness (A7)
renders a season of them so the copy can be read in sequence.

The rules:

- **An open door, not the bulletin (SNOW-1332).** An alert says only
  that there is news, for which place and which day: "Verbier: new
  avalanche bulletin" / "For tomorrow." No level, no problems: a message
  can never give the whole picture, so it sends the reader to the page
  that does (Hugo, 2026-10-30).
- **One message per person per run.** :func:`alert_message` takes every
  place that fired for an account and bundles them; a single place reads as
  that place, several as their names ("Chamonix, Davos and Verbier, for
  tomorrow.").
- **The same words in the email.** The email is the fallback for the same
  alert, so it carries the same title and body.
- **The full sentences live on the page.** :func:`change_sentences` writes
  every move ("Wind slab added on N–E above 2,400 m.") for the since strip
  and ``/alerts/<day>/``, which have the room. Problem names and level
  words come from ``day_summary.py`` so they read as the bulletin page does.

Server-side strings, so ``makemessages`` covers them.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from typing import Literal

from django.urls import reverse
from django.utils import formats
from django.utils.translation import gettext as _

from apps.bulletins.services.bulletin_change import (
    CLOCKWISE_ASPECTS,
    DANGER_NUMBER,
    BulletinChange,
    BulletinState,
    ProblemMove,
    ProblemState,
)
from apps.bulletins.services.day_summary import LEVEL_WORDS, PROBLEM_PHRASES

logger = logging.getLogger(__name__)

# Places listed by name in a bundled body before "and N more".
MAX_LISTED_PLACES: int = 4

AlertKind = Literal["new", "update", "season_start"]


@dataclass(frozen=True)
class PlaceAlert:
    """One place that fired in a run, with what moved there."""

    region_id: str
    label: str
    target_date: datetime.date
    kind: AlertKind
    change: BulletinChange
    # The day the comparison was made against (yesterday for a new day,
    # the same day for an update), for "Same as Friday".
    compared_to: datetime.date | None = None
    # Where a one-place alert opens, when not the map: a saved trip's own
    # page (SNOW-947).
    url: str = ""


@dataclass(frozen=True)
class AlertMessage:
    """The title, body and link of one notification."""

    title: str
    body: str
    url: str
    places: tuple[PlaceAlert, ...] = field(default=())

    def as_push(self) -> dict[str, str]:
        """Return the ``{title, body, url}`` payload ``enqueue_push`` takes."""
        return {"title": self.title, "body": self.body, "url": self.url}


# ---------------------------------------------------------------------------
# Small phrases
# ---------------------------------------------------------------------------


def level_word(state: BulletinState) -> str:
    """
    Return the headline level as a capitalised word ("Considerable").

    Args:
        state: A bulletin state.

    Returns:
        The word, or "No rating" for an unrated state.

    """
    word = LEVEL_WORDS.get(state.level)
    if word is None:
        return _("No rating")
    text = str(word)
    return text[:1].upper() + text[1:]


def level_figure(state: BulletinState) -> str:
    """
    Return the level as a figure with its sub-rating ("3+", "3−", "3").

    Args:
        state: A bulletin state.

    Returns:
        The figure; the neutral sub-rating and no sub-rating both read as
        the bare digit. Empty for an unrated state.

    """
    if not state.level:
        return ""
    suffix = {"+": "+", "-": "−"}.get(state.subdivision or "", "")
    return f"{state.level}{suffix}"


def headline(state: BulletinState) -> str:
    """
    Return "Considerable (3+)".

    Args:
        state: A bulletin state.

    Returns:
        The level word and figure.

    """
    figure = level_figure(state)
    word = level_word(state)
    return f"{word} ({figure})" if figure else word


def problem_name(problem_type: str, capital: bool = False) -> str:
    """
    Return a problem's mid-sentence name, optionally capitalised.

    Args:
        problem_type: An EAWS problem type.
        capital: True to capitalise for the start of a sentence.

    Returns:
        "wind slab" / "Wind slab"; unknown types fall back to the token
        with underscores as spaces.

    """
    phrase = PROBLEM_PHRASES.get(problem_type)
    text = str(phrase) if phrase is not None else problem_type.replace("_", " ")
    return text[:1].upper() + text[1:] if capital else text


def aspect_ranges(aspects: tuple[str, ...]) -> str:
    """
    Write aspects as contiguous clockwise ranges ("NW–NE, S").

    Args:
        aspects: Aspect tokens, any order.

    Returns:
        "all aspects" for the full rose, ranges joined by commas otherwise,
        and an empty string for none.

    """
    known = [a for a in CLOCKWISE_ASPECTS if a in aspects]
    if not known:
        return ""
    if len(known) == len(CLOCKWISE_ASPECTS):
        return _("all aspects")
    present = [a in aspects for a in CLOCKWISE_ASPECTS]
    # Start the walk just after a gap so a run across north (NW, N, NE)
    # reads as one range.
    start = next(i for i in range(8) if present[i] and not present[i - 1])
    runs: list[list[str]] = []
    for step in range(8):
        i = (start + step) % 8
        if not present[i]:
            continue
        if runs and present[i - 1] and step:
            runs[-1].append(CLOCKWISE_ASPECTS[i])
        else:
            runs.append([CLOCKWISE_ASPECTS[i]])
    return ", ".join(_run_text(r) for r in runs)


def _run_text(run: list[str]) -> str:
    """
    Write one contiguous run of aspects.

    A run of five or more names its middle too ("W–N–E"), because "W–E"
    alone reads equally as the southern half.

    Args:
        run: Adjacent aspects, clockwise.

    Returns:
        "N", "N–E" or "W–N–E".

    """
    if len(run) == 1:
        return run[0]
    if len(run) >= 5:
        return f"{run[0]}–{run[len(run) // 2]}–{run[-1]}"
    return f"{run[0]}–{run[-1]}"


def _names(problem_types: list[str]) -> str:
    """
    Join problem names for the start of a sentence ("Gliding snow and wet snow").

    Args:
        problem_types: EAWS problem types, at least one.

    Returns:
        The names, the first capitalised, joined with commas and "and".

    """
    names = [problem_name(t) for t in problem_types]
    if len(names) == 1:
        text = names[0]
    else:
        text = _("%(head)s and %(tail)s") % {
            "head": ", ".join(names[:-1]),
            "tail": names[-1],
        }
    return text[:1].upper() + text[1:]


def _metres(value: int) -> str:
    """Return "2,400 m" in the active locale's grouping."""
    return _("%(height)s m") % {
        "height": formats.number_format(value, force_grouping=True)
    }


def elevation_phrase(problem: ProblemState) -> str:
    """
    Write a problem's band ("above 2,400 m", "between 1,800 and 2,400 m").

    Args:
        problem: A problem state.

    Returns:
        The phrase, or an empty string when the problem has no band.

    """
    lower, upper = problem.lower, problem.upper
    if lower is not None and upper is not None:
        return _("between %(lower)s and %(upper)s") % {
            "lower": formats.number_format(lower, force_grouping=True),
            "upper": _metres(upper),
        }
    if lower is not None:
        return _("above %(height)s") % {"height": _metres(lower)}
    if upper is not None:
        return _("below %(height)s") % {"height": _metres(upper)}
    if problem.treeline == "upper":
        return _("below the treeline")
    if problem.treeline:
        return _("above the treeline")
    return ""


def where(problem: ProblemState) -> str:
    """
    Write where a problem is ("on N–E above 2,400 m").

    Args:
        problem: A problem state.

    Returns:
        The phrase with a leading space, or an empty string when the
        bulletin gave neither aspects nor a band.

    """
    parts: list[str] = []
    aspects = aspect_ranges(problem.aspects)
    if aspects:
        parts.append(_("on %(aspects)s") % {"aspects": aspects})
    band = elevation_phrase(problem)
    if band:
        parts.append(band)
    return (" " + " ".join(parts)) if parts else ""


def weekday(day: datetime.date) -> str:
    """Return the localised weekday name ("Friday")."""
    return formats.date_format(day, "l")


# ---------------------------------------------------------------------------
# Sentences
# ---------------------------------------------------------------------------


def _grouped(pairs: list[tuple[str, str]]) -> list[tuple[list[str], str]]:
    """
    Group problems that share a phrase, keeping first-seen order.

    Args:
        pairs: ``(problem_type, phrase)`` pairs.

    Returns:
        ``(problem_types, phrase)`` per distinct phrase.

    """
    groups: dict[str, list[str]] = {}
    for problem_type, phrase in pairs:
        groups.setdefault(phrase, []).append(problem_type)
    return [(types, phrase) for phrase, types in groups.items()]


def _moved_phrases(moves: tuple[ProblemMove, ...]) -> list[str]:
    """
    Write the moved problems, those that moved to the same place together.

    Args:
        moves: The problems that moved.

    Returns:
        "Gliding snow and wet snow now on E–S–W below 2,600 m.", then
        "Wind slab now rated considerable." for rating-only moves.

    """
    placed = [
        (m.after.problem_type, where(m.after) or (" " + _("without a stated area")))
        for m in moves
        if m.band_moved or m.aspects_moved
    ]
    rated = []
    for m in moves:
        if m.band_moved or m.aspects_moved:
            continue
        level = LEVEL_WORDS.get(DANGER_NUMBER.get(m.after.rating or "", 0))
        rated.append(
            (
                m.after.problem_type,
                str(level) if level is not None else _("without a level"),
            )
        )
    out = [
        _("%(problems)s now%(where)s.") % {"problems": _names(types), "where": phrase}
        for types, phrase in _grouped(placed)
    ]
    out.extend(
        _("%(problems)s now rated %(level)s.")
        % {"problems": _names(types), "level": phrase}
        for types, phrase in _grouped(rated)
    )
    return out


def change_sentences(change: BulletinChange) -> list[str]:
    """
    Write every movement in a change, most important first.

    Problems that moved the same way share a sentence, so two problems the
    bulletin bands together ("gliding snow and wet snow below 2,600 m") are
    not read out twice.

    Args:
        change: An A1 change with a previous state.

    Returns:
        The sentences: the rating move, then problems added, moved and
        removed. Empty when nothing moved.

    """
    previous = change.previous
    if previous is None:
        return []
    out: list[str] = []
    if change.rating_direction > 0 or (
        change.subdivision_moved and change.current.rank > previous.rank
    ):
        out.append(_("Up from %(level)s.") % {"level": headline(previous)})
    elif change.rating_direction < 0 or change.subdivision_moved:
        out.append(_("Down from %(level)s.") % {"level": headline(previous)})
    out.extend(
        _("%(problems)s added%(where)s.") % {"problems": _names(types), "where": phrase}
        for types, phrase in _grouped(
            [(p.problem_type, where(p)) for p in change.added]
        )
    )
    out.extend(_moved_phrases(change.moved))
    if change.removed:
        out.append(
            _("%(problems)s no longer named.")
            % {"problems": _names([p.problem_type for p in change.removed])}
        )
    return out


# ---------------------------------------------------------------------------
# The message: an open door (SNOW-1332)
# ---------------------------------------------------------------------------


def day_phrase(day: datetime.date, today: datetime.date) -> str:
    """
    Name the day a bulletin is for, as it reads after "for".

    Args:
        day: The bulletin's day.
        today: The local date the message is sent on.

    Returns:
        "today", "tomorrow", or the weekday ("Sunday").

    """
    if day == today:
        return _("today")
    if day == today + datetime.timedelta(days=1):
        return _("tomorrow")
    return weekday(day)


def place_title(place: PlaceAlert) -> str:
    """
    Write the title for a single place: the place, and that there is news.

    Args:
        place: The place that fired.

    Returns:
        "Verbier: new avalanche bulletin", "Verbier: avalanche bulletin
        updated" or "Verbier: avalanche bulletins are back".

    """
    if place.kind == "season_start":
        return _("%(place)s: avalanche bulletins are back") % {"place": place.label}
    if place.kind == "update":
        return _("%(place)s: avalanche bulletin updated") % {"place": place.label}
    return _("%(place)s: new avalanche bulletin") % {"place": place.label}


def place_body(place: PlaceAlert, today: datetime.date) -> str:
    """
    Write the body for a single place: which day the bulletin is for.

    Args:
        place: The place that fired.
        today: The local date the message is sent on.

    Returns:
        "For tomorrow.", "For your trip on Sunday." or "The first of the
        season, for tomorrow."

    """
    day = day_phrase(place.target_date, today)
    if place.url:
        if place.target_date - today <= datetime.timedelta(days=1):
            text = _("For your trip %(day)s.") % {"day": day}
        else:
            text = _("For your trip on %(day)s.") % {"day": day}
    else:
        text = _("For %(day)s.") % {"day": day}
    if place.kind == "season_start":
        return _("The first of the season, %(rest)s") % {
            "rest": text[:1].lower() + text[1:]
        }
    return text


def place_url(place: PlaceAlert) -> str:
    """
    Return the link a one-place alert opens.

    Args:
        place: The place that fired.

    Returns:
        The place's own ``url`` (a trip page) when it has one, otherwise
        ``/map/?d=YYYY-MM-DD#<region_id>``, its region panel on its day.

    """
    if place.url:
        return place.url
    return (
        f"{reverse('public:map')}?d={place.target_date.isoformat()}#{place.region_id}"
    )


def _bundle_title(places: list[PlaceAlert]) -> str:
    """
    Write the title for several places.

    Args:
        places: Every place that fired.

    Returns:
        "New avalanche bulletins for your 3 places", or "Avalanche
        bulletins updated for your 2 places" when every one is an update.

    """
    count = len(places)
    if all(p.kind == "update" for p in places):
        return _("Avalanche bulletins updated for your %(count)d places") % {
            "count": count
        }
    return _("New avalanche bulletins for your %(count)d places") % {"count": count}


def _bundle_body(places: list[PlaceAlert], today: datetime.date) -> str:
    """
    Write the body for several places: their names, and the day.

    Args:
        places: Every place that fired.
        today: The local send date.

    Returns:
        "Chamonix, Davos and Verbier, for tomorrow.", with "and 2 more" past
        four places and no day when the days differ.

    """
    names = sorted(p.label for p in places)
    listed = names[:MAX_LISTED_PLACES]
    extra = len(names) - len(listed)
    if extra:
        joined = _("%(head)s and %(count)d more") % {
            "head": ", ".join(listed),
            "count": extra,
        }
    else:
        joined = _("%(head)s and %(tail)s") % {
            "head": ", ".join(listed[:-1]),
            "tail": listed[-1],
        }
    days = {p.target_date for p in places}
    if len(days) > 1:
        return joined + "."
    (day,) = days
    return _("%(places)s, for %(day)s.") % {
        "places": joined,
        "day": day_phrase(day, today),
    }


def bundle_url(places: list[PlaceAlert]) -> str:
    """
    Return the landing page for several places.

    Args:
        places: Every place that fired.

    Returns:
        ``/alerts/<date>/`` for the latest day named.

    """
    day = max(p.target_date for p in places)
    return reverse("public:alerts_day", kwargs={"day": day.isoformat()})


def alert_message(places: list[PlaceAlert], today: datetime.date) -> AlertMessage:
    """
    Render every place that fired for one account into one message.

    Args:
        places: The places, at least one.
        today: The local date the message is sent on.

    Returns:
        The message.

    Raises:
        ValueError: When ``places`` is empty.

    """
    if not places:
        raise ValueError("alert_message needs at least one place")
    if len(places) == 1:
        (place,) = places
        return AlertMessage(
            title=place_title(place),
            body=place_body(place, today),
            url=place_url(place),
            places=(place,),
        )
    return AlertMessage(
        title=_bundle_title(places),
        body=_bundle_body(places, today),
        url=bundle_url(places),
        places=tuple(places),
    )
