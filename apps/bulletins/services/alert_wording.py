"""
apps/bulletins/services/alert_wording.py — The words of a bulletin alert.

Renders one or more A1 changes (``bulletin_change.py``) into the title,
body and link of one notification (SNOW-940). The same message is sent as a
push and, as the fallback, in the alert email; the replay harness (A7)
renders a season of them so the copy can be read in sequence.

The rules:

- **The day, the level and what moved (SNOW-1356).** The title names the
  place and the day the bulletin is for: "Verbier – Thursday bulletin",
  and a reissue "Verbier – Thursday bulletin update" (so Wednesday's
  17:00 issue says Thursday). The body is the level and whether it moved,
  then the forecast new snow when there is some: "Considerable, up from
  Moderate. 20 cm new snow forecast." / "Moderate, no change." The
  open-door wording of SNOW-1332 made a quiet week seven identical
  messages (Hugo, 2026-11-01); the problems, aspects and heights still
  live only on the page the tap opens.
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
from zoneinfo import ZoneInfo

from django.urls import reverse
from django.utils import formats, translation
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

# The clock every alert is written in: the Alps' own.
ALERT_TZ = ZoneInfo("Europe/Zurich")

# Places listed by name in a bundled body before "and N more".
MAX_LISTED_PLACES: int = 4

# The least forecast new snow, in cm, a one-place alert names (SNOW-944):
# below it a dusting would read as news.
NEW_SNOW_MIN_CM: float = 5.0

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
    # When the bulletin holds from and until (SNOW-1332): the reader's way
    # to place "this alert" among the day's issues.
    valid_from: datetime.datetime | None = None
    valid_until: datetime.datetime | None = None
    # The new snow forecast for ``target_date`` at the region's weather
    # point, in cm (SNOW-944), or None when nothing is known.
    new_snow_cm: float | None = None
    # How many regions' bulletins the place covers: more than one for a
    # trip crossing several (SNOW-945), whose body then names the count
    # rather than one region's level.
    bulletin_count: int = 1


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


def clock(moment: datetime.datetime) -> str:
    """
    Write a moment as "Saturday at 5 pm" in the Alps' time.

    Args:
        moment: An aware datetime.

    Returns:
        The weekday and time: "5 pm", "5:30 pm" in English, the locale's
        24-hour "17:00" otherwise.

    """
    local = moment.astimezone(ALERT_TZ)
    if translation.get_language().startswith("en"):
        hour = local.hour % 12 or 12
        minutes = f":{local.minute:02d}" if local.minute else ""
        time = f"{hour}{minutes} {'am' if local.hour < 12 else 'pm'}"
    else:
        time = formats.time_format(local, "H:i")
    return _("%(day)s at %(time)s") % {"day": weekday(local.date()), "time": time}


def validity(
    valid_from: datetime.datetime | None, valid_until: datetime.datetime | None
) -> str:
    """
    Write when a bulletin holds.

    Args:
        valid_from: The start of its validity.
        valid_until: The end of its validity.

    Returns:
        "Valid from Friday at 5 pm to Saturday at 5 pm.", or an empty
        string when either time is unknown.

    """
    if valid_from is None or valid_until is None:
        return ""
    return _("Valid from %(start)s to %(end)s.") % {
        "start": clock(valid_from),
        "end": clock(valid_until),
    }


def place_title(place: PlaceAlert) -> str:
    """
    Write the title for a single place: the place and the day it is for.

    Args:
        place: The place that fired.

    Returns:
        "Verbier – Thursday bulletin", or "Verbier – Thursday bulletin
        update" for a reissue. The day is the one the bulletin forecasts,
        so Wednesday's 17:00 issue names Thursday.

    """
    values = {"place": place.label, "day": weekday(place.target_date)}
    if place.bulletin_count > 1:
        if place.kind == "update":
            return _("%(place)s – %(day)s bulletins update") % values
        return _("%(place)s – %(day)s bulletins") % values
    if place.kind == "update":
        return _("%(place)s – %(day)s bulletin update") % values
    return _("%(place)s – %(day)s bulletin") % values


def movement(change: BulletinChange) -> str:
    """
    Write the level and whether it moved.

    Args:
        change: The change that fired.

    Returns:
        "Considerable, up from Moderate.", "Considerable (3+), up from 3−."
        for a sub-level move, "Moderate, avalanche problems changed." when
        a problem was added or removed, "Moderate, no change." when
        nothing did or a problem only shifted its aspects or band,
        and the bare "Moderate." with nothing to compare against.

    """
    current, previous = change.current, change.previous
    word = level_word(current)
    if previous is None:
        return _("%(level)s.") % {"level": word}
    if change.rating_direction > 0:
        return _("%(level)s, up from %(previous)s.") % {
            "level": word,
            "previous": level_word(previous),
        }
    if change.rating_direction < 0:
        return _("%(level)s, down from %(previous)s.") % {
            "level": word,
            "previous": level_word(previous),
        }
    if change.subdivision_moved:
        values = {
            "level": headline(current),
            "previous": level_figure(previous) or level_word(previous),
        }
        if current.rank > previous.rank:
            return _("%(level)s, up from %(previous)s.") % values
        return _("%(level)s, down from %(previous)s.") % values
    if change.added or change.removed:
        # A problem shifting its aspects or band is not news (SNOW-1356).
        return _("%(level)s, avalanche problems changed.") % {"level": word}
    return _("%(level)s, no change.") % {"level": word}


def new_snow(cm: float | None) -> str:
    """
    Write the forecast new snow ("20 cm new snow forecast.").

    Args:
        cm: The day's forecast snowfall in cm, or None.

    Returns:
        The sentence, or an empty string below ``NEW_SNOW_MIN_CM``.

    """
    if cm is None or cm < NEW_SNOW_MIN_CM:
        return ""
    return _("%(cm)d cm new snow forecast.") % {"cm": round(cm)}


def place_body(place: PlaceAlert, today: datetime.date) -> str:
    """
    Write the body for a single place: the level, what moved, new snow.

    Args:
        place: The place that fired.
        today: The local date the message is sent on (unused since the
            title names the day; kept so every body takes the same pair).

    Returns:
        "Considerable, up from Moderate. 20 cm new snow forecast.", led by
        "For your trip." for a trip, "First of the season." at season
        start and "Bulletins are back." after a gap inside it. A trip
        crossing several regions reads "For your trip. 2 bulletins.": no
        level and no problems, since one region's would misstate the
        others, and the trip page lists each (SNOW-945).

    """
    parts: list[str] = []
    if place.url:
        parts.append(_("For your trip."))
    if place.bulletin_count > 1:
        parts.append(_("%(count)d bulletins.") % {"count": place.bulletin_count})
        return " ".join(parts)
    if place.kind == "season_start":
        parts.append(_("First of the season."))
    elif place.kind == "new" and place.change.previous is None:
        # Bulletins resumed after a gap inside the season (SNOW-1356).
        parts.append(_("Bulletins are back."))
    parts.append(movement(place.change))
    snow = new_snow(place.new_snow_cm)
    if snow:
        parts.append(snow)
    return " ".join(parts)


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
        "Bulletins for your 3 places", or "Bulletins updated for your 2
        places" when every one is an update.

    """
    count = len(places)
    if all(p.kind == "update" for p in places):
        return _("Bulletins updated for your %(count)d places") % {"count": count}
    return _("Bulletins for your %(count)d places") % {"count": count}


def _bundle_body(places: list[PlaceAlert], today: datetime.date) -> str:
    """
    Write the body for several places: their names, then when.

    Args:
        places: Every place that fired.
        today: The local send date.

    Returns:
        "Chamonix, Davos and Verbier. Valid from Friday at 5 pm to Saturday
        at 5 pm.", with "and 2 more" past four places. Bulletins that hold
        for different times fall back to their shared day ("…, for
        tomorrow."), or the names alone.

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
    times = {(p.valid_from, p.valid_until) for p in places}
    if len(times) == 1:
        when = validity(*next(iter(times)))
        if when:
            return f"{joined}. {when}"
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
