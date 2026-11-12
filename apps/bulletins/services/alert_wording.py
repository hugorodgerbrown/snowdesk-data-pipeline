"""
apps/bulletins/services/alert_wording.py — The words of a bulletin alert.

Renders the places that fired for one account (``bulletin_change.py``)
into the title, body and link of one notification (SNOW-940). The same
message is sent as a push and in the alert email; the replay harness (A7)
renders a season of them so the copy can be read in sequence.

The rules:

- **Cold (Hugo, 2026-11-12).** Every alert reads "New avalanche bulletin",
  then who released it and when: "Bulletin released at 17:00 by SLF." The
  time is dropped when the bulletins behind it were released at different
  times. A reissue reads the same, with its own time. A single place, a
  bundle and a trip all read alike: the earlier detailed and bundled
  wordings (SNOW-1332, SNOW-1356) said too little to act on and too much
  to ignore.
- **One message per person per run.** :func:`alert_message` takes every
  place that fired for an account and makes one message of them.
- **Every alert opens the landing page.** ``/alerts/<day>/`` lists every
  place the person follows with its level, problems and provider, which is
  where the detail lives.
- **The full sentences live on the pages.** :func:`change_sentences` writes
  every move ("Wind slab added on N–E above 2,400 m.") for the since strip,
  and :func:`trend` the level's move for the landing page. Problem names and
  level words come from ``day_summary.py`` so they read as the bulletin page
  does.

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

# The least forecast new snow, in cm, the landing page names (SNOW-944):
# below it a dusting would read as news.
NEW_SNOW_MIN_CM: float = 5.0

AlertKind = Literal["new", "update", "season_start"]


@dataclass(frozen=True)
class Release:
    """One bulletin behind an alert: its provider and when it was issued."""

    source: str
    issued_at: datetime.datetime | None


@dataclass(frozen=True)
class PlaceAlert:
    """One place that fired in a run, with what moved there."""

    region_id: str
    label: str
    target_date: datetime.date
    kind: AlertKind
    change: BulletinChange
    # The bulletins behind it: one, or one per region for a trip crossing
    # several regions (SNOW-945).
    releases: tuple[Release, ...] = ()


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
# The message: cold (Hugo, 2026-11-12)
# ---------------------------------------------------------------------------

# How each provider is named in a message, in the order a message lists them.
PROVIDER_NAMES: dict[str, str] = {
    "SLF": "SLF",
    "ALBINA": "ALBINA",
    "METEOFRANCE": "Météo-France",
}


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


def hhmm(moment: datetime.datetime) -> str:
    """Return a moment as "17:00" in the Alps' time."""
    return moment.astimezone(ALERT_TZ).strftime("%H:%M")


def provider_name(source: str) -> str:
    """
    Return how a message names a provider.

    Args:
        source: A ``Bulletin.Source`` value.

    Returns:
        "SLF", "ALBINA", "Météo-France", or the value itself when unknown.

    """
    return PROVIDER_NAMES.get(source, source)


def trend(change: BulletinChange) -> str:
    """
    Write how the level moved, for the landing page beside the level.

    Args:
        change: The change between the day before and this day.

    Returns:
        "up from Moderate", "up from 2−" for a move within a level,
        "down from …", "no change", or "" with nothing to compare against.

    """
    current, previous = change.current, change.previous
    if previous is None:
        return ""
    if change.rating_direction:
        values = {"level": level_word(previous)}
    elif change.subdivision_moved:
        values = {"level": level_figure(previous) or level_word(previous)}
    else:
        return _("no change")
    if change.rating_direction > 0 or (
        not change.rating_direction and current.rank > previous.rank
    ):
        return _("up from %(level)s") % values
    return _("down from %(level)s") % values


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


def release_line(releases: list[Release]) -> str:
    """
    Write who released the bulletins an alert is for, and when.

    Args:
        releases: Every bulletin behind the alert.

    Returns:
        "Bulletin released at 17:00 by SLF." for one provider at one time;
        "Bulletins released by SLF and ALBINA." for several providers, and
        "Bulletins released by SLF." for one provider at several times: the
        time is named only when every bulletin shares it. "New bulletin."
        when no provider is known.

    """
    sources = {r.source for r in releases if r.source}
    names = [provider_name(s) for s in PROVIDER_NAMES if s in sources]
    names += sorted(provider_name(s) for s in sources if s not in PROVIDER_NAMES)
    times = {hhmm(r.issued_at) for r in releases if r.issued_at is not None}
    if not names:
        return _("New bulletin.")
    if len(names) == 1:
        joined = names[0]
    else:
        joined = _("%(head)s and %(tail)s") % {
            "head": ", ".join(names[:-1]),
            "tail": names[-1],
        }
    if len(times) == 1 and len(names) == 1:
        (time,) = times
        return _("Bulletin released at %(time)s by %(provider)s.") % {
            "time": time,
            "provider": joined,
        }
    if len(times) == 1:
        (time,) = times
        return _("Bulletins released at %(time)s by %(providers)s.") % {
            "time": time,
            "providers": joined,
        }
    if len(names) == 1:
        return _("Bulletins released by %(provider)s.") % {"provider": joined}
    return _("Bulletins released by %(providers)s.") % {"providers": joined}


def landing_url(places: list[PlaceAlert]) -> str:
    """
    Return the page every alert opens.

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

    Every alert reads the same, whatever fired: "New avalanche bulletin",
    then who released it and when. The detail lives on the page it opens
    (Hugo, 2026-11-12). A reissue says the same, with its own time.

    Args:
        places: The places, at least one.
        today: The local date the message is sent on (unused; kept so
            callers pass the same pair as before).

    Returns:
        The message.

    Raises:
        ValueError: When ``places`` is empty.

    """
    if not places:
        raise ValueError("alert_message needs at least one place")
    releases = [r for p in places for r in p.releases]
    return AlertMessage(
        title=_("New avalanche bulletin"),
        body=release_line(releases),
        url=landing_url(places),
        places=tuple(places),
    )
