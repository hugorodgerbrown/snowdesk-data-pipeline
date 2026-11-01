"""
apps/public/daily_read.py — the homepage's daily read (SNOW-1357).

"Across the Alps today": what moved since yesterday, per country, assembled
each morning from the day ratings and the stored bulletin states. Templated,
never generated, and never advice — every sentence is a count or a name the
providers' own bulletins already carry (Hugo, 2026-11-01).

Two layers, so the cached part never holds a translated string:

* ``build_daily_read(day)`` reads the database and returns a frozen
  :class:`DailyRead` of keys and counts (level keys, problem types, region
  names and urls). Three queries, however many regions: today's
  ``RegionDayRating`` rows, yesterday's, and the distinct source bulletins'
  render models. ``cached_daily_read(day)`` wraps it in the default cache
  for ``CACHE_SECONDS``.
* ``present_daily_read(read)`` turns it into what the template renders, in
  the active language, with ``gettext`` / ``ngettext``.

The rules:

* A row with ``max_rating = NO_RATING`` takes part in nothing: it is not a
  rated region today, and a region unrated on either day has no movement.
* A country with no rated region today is left out; with none at all (off
  season) there is no read and the homepage renders as it always has.
* Movement compares ``max_rating`` — the compressed views' peak
  (docs/compressed-views-rating-rule.md) — for regions rated on both days.
* The problem named most counts REGIONS, not bulletins: one grouped
  bulletin over three regions names its problems three times. Ties go to
  the problem type's code; the stored state orders problems by type, so
  the order a bulletin listed them in is not available to break one.
* The biggest swing is the region whose peak moved furthest; ties go to the
  higher level today, then the region code.
"""

from __future__ import annotations

import datetime
import logging
from collections import Counter
from dataclasses import dataclass
from typing import Any

from django.core.cache import cache
from django.utils.translation import gettext as _, ngettext

from apps.bulletins.models import Bulletin, RegionDayRating
from apps.bulletins.services.alert_wording import problem_name
from apps.bulletins.services.day_summary import LEVEL_WORDS
from apps.ingest.public import DANGER_NUMBER, state_of

logger = logging.getLogger(__name__)

# How long one built read is served before the next request rebuilds it. A
# morning issue reaches the homepage within this, which is soon enough for
# a daily read; ingest does not invalidate it.
CACHE_SECONDS: int = 15 * 60

# The countries the read lists first, in this order; any other follows,
# ordered by its code.
COUNTRY_ORDER: tuple[str, ...] = ("CH", "FR", "AT", "IT")

NO_RATING = RegionDayRating.Rating.NO_RATING


@dataclass(frozen=True)
class Swing:
    """The region whose peak moved furthest since yesterday."""

    region_code: str
    region_name: str
    url: str
    before: str
    after: str


@dataclass(frozen=True)
class CountryRead:
    """One country's day: counts and keys, no translated text."""

    country: str
    rated_count: int
    rose: int
    fell: int
    held: int
    peak_level: str
    peak_count: int
    top_problem: str | None
    top_problem_count: int
    swing: Swing | None

    @property
    def compared(self) -> int:
        """Return how many regions were rated on both days."""
        return self.rose + self.fell + self.held


@dataclass(frozen=True)
class DailyRead:
    """The whole read for one day."""

    day: datetime.date
    countries: tuple[CountryRead, ...]


def _level(key: str) -> int:
    """
    Return a danger key as its level, 1–5, or 0 for anything else.

    Args:
        key: A ``RegionDayRating.Rating`` value.

    Returns:
        The level.

    """
    return DANGER_NUMBER.get(key, 0)


def _country_sort_key(country: str) -> tuple[int, str]:
    """
    Order countries CH, FR, AT, IT first, then the rest by code.

    Args:
        country: An ISO-3166-1 alpha-2 code.

    Returns:
        A sort key.

    """
    if country in COUNTRY_ORDER:
        return (COUNTRY_ORDER.index(country), country)
    return (len(COUNTRY_ORDER), country)


def _problems_by_bulletin(bulletin_ids: set[int]) -> dict[int, tuple[str, ...]]:
    """
    Return each bulletin's problem types, read from its stored state.

    Args:
        bulletin_ids: The source bulletins of today's rated rows.

    Returns:
        ``{bulletin pk: problem types}``; one query.

    """
    if not bulletin_ids:
        return {}
    rows = Bulletin.objects.filter(pk__in=bulletin_ids).only("id", "render_model")
    return {
        b.pk: tuple(p.problem_type for p in state_of(b.render_model)[0].problems)
        for b in rows
    }


def _swing(
    today: list[RegionDayRating], yesterday: dict[int, str], day: datetime.date
) -> Swing | None:
    """
    Return the region whose peak moved furthest, or None when none moved.

    Args:
        today: One country's rated rows for the day.
        yesterday: ``{region pk: max_rating}`` for the day before, rated
            rows only.
        day: The day, for the region's dated bulletin url.

    Returns:
        The swing, or None.

    """
    moved = [
        row
        for row in today
        if row.region_id in yesterday
        and _level(row.max_rating) != _level(yesterday[row.region_id])
    ]
    if not moved:
        return None
    # Furthest move, then the higher level today, then the region code.
    best_row = min(
        moved,
        key=lambda row: (
            -abs(_level(row.max_rating) - _level(yesterday[row.region_id])),
            -_level(row.max_rating),
            row.region.region_id,
        ),
    )
    region = best_row.region
    return Swing(
        region_code=region.region_id,
        region_name=region.name,
        url=region.get_absolute_url(day),
        before=yesterday[best_row.region_id],
        after=best_row.max_rating,
    )


def _country_read(
    country: str,
    rows: list[RegionDayRating],
    yesterday: dict[int, str],
    problems: dict[int, tuple[str, ...]],
    day: datetime.date,
) -> CountryRead:
    """
    Build one country's read from its rated rows.

    Args:
        country: The country code.
        rows: The country's rated rows for the day (at least one).
        yesterday: ``{region pk: max_rating}`` for the day before.
        problems: ``{bulletin pk: problem types}``.
        day: The day.

    Returns:
        The country's read.

    """
    rose = fell = held = 0
    for row in rows:
        before = yesterday.get(row.region_id)
        if before is None:
            continue
        delta = _level(row.max_rating) - _level(before)
        if delta > 0:
            rose += 1
        elif delta < 0:
            fell += 1
        else:
            held += 1

    peak = max(rows, key=lambda r: _level(r.max_rating)).max_rating
    peak_count = sum(1 for r in rows if r.max_rating == peak)

    named: Counter[str] = Counter()
    for row in rows:
        named.update(set(problems.get(row.source_bulletin_id or 0, ())))
    top_problem: str | None = None
    top_count = 0
    if named:
        top_problem, top_count = min(named.items(), key=lambda kv: (-kv[1], kv[0]))

    return CountryRead(
        country=country,
        rated_count=len(rows),
        rose=rose,
        fell=fell,
        held=held,
        peak_level=peak,
        peak_count=peak_count,
        top_problem=top_problem,
        top_problem_count=top_count,
        swing=_swing(rows, yesterday, day),
    )


def build_daily_read(day: datetime.date) -> DailyRead | None:
    """
    Build the day's read from the database.

    Args:
        day: The day the read describes (today, on the homepage).

    Returns:
        The read, or None when no region has a provider rating that day.

    """
    today = list(
        RegionDayRating.objects.filter(date=day)
        .exclude(max_rating=NO_RATING)
        .select_related("region__subregion__major")
        .only(
            "region",
            "max_rating",
            "source_bulletin",
            "region__region_id",
            "region__name",
            "region__slug",
            "region__subregion",
            "region__subregion__major",
            "region__subregion__major__country",
        )
    )
    if not today:
        return None
    yesterday: dict[int, str] = dict(
        RegionDayRating.objects.filter(date=day - datetime.timedelta(days=1))
        .exclude(max_rating=NO_RATING)
        .values_list("region_id", "max_rating")
    )
    problems = _problems_by_bulletin(
        {r.source_bulletin_id for r in today if r.source_bulletin_id}
    )

    by_country: dict[str, list[RegionDayRating]] = {}
    for row in today:
        by_country.setdefault(row.region.subregion.major.country, []).append(row)

    countries = tuple(
        _country_read(code, by_country[code], yesterday, problems, day)
        for code in sorted(by_country, key=_country_sort_key)
    )
    return DailyRead(day=day, countries=countries)


def cached_daily_read(day: datetime.date) -> DailyRead | None:
    """
    Return the day's read, built at most once per ``CACHE_SECONDS``.

    An off-season day (no read) is cached too, so a quiet homepage costs
    no query either.

    Args:
        day: The day.

    Returns:
        The read, or None.

    """
    key = f"daily-read:{day.isoformat()}"
    hit = cache.get(key)
    if hit is not None:
        return hit or None
    read = build_daily_read(day)
    # False marks "built, nothing to say", so a miss and an empty day differ.
    cache.set(key, read or False, CACHE_SECONDS)
    return read


# The countries' names, as the read heads each group.
def _country_label(country: str) -> str:
    """
    Return a country's name in the active language.

    Args:
        country: An ISO-3166-1 alpha-2 code.

    Returns:
        The name, or the code for a country the read does not name.

    """
    labels = {
        "CH": _("Switzerland"),
        "FR": _("France"),
        "AT": _("Austria"),
        "IT": _("Italy"),
        "DE": _("Germany"),
        "SI": _("Slovenia"),
    }
    return labels.get(country, country)


def level_word(key: str) -> str:
    """
    Return a danger key as a capitalised word ("Considerable").

    Args:
        key: A ``RegionDayRating.Rating`` value.

    Returns:
        The word, or "No rating".

    """
    word = LEVEL_WORDS.get(_level(key))
    if word is None:
        return _("No rating")
    text = str(word)
    return text[:1].upper() + text[1:]


def _movement_sentence(read: CountryRead) -> str | None:
    """
    Return the movement sentence, or None when nothing could be compared.

    A claim about "every region" or "all N" is made only of the regions it
    counts: when some regions rated today were unrated yesterday, the
    sentence says "rated both days", and a mixed day names how many held
    rather than saying "elsewhere".

    Args:
        read: One country's read.

    Returns:
        The sentence, in the active language, or None.

    """
    n = read.compared
    if not n:
        return None
    whole = n == read.rated_count
    if read.rose == 0 and read.fell == 0:
        if whole:
            return _("Danger held in every region since yesterday.")
        return ngettext(
            "Danger held in the one region rated both days.",
            "Danger held in all %(n)d regions rated both days.",
            n,
        ) % {"n": n}
    if read.rose == n or read.fell == n:
        if whole:
            rose = ngettext(
                "Danger rose in the one region since yesterday.",
                "Danger rose in all %(n)d regions since yesterday.",
                n,
            )
            fell = ngettext(
                "Danger fell in the one region since yesterday.",
                "Danger fell in all %(n)d regions since yesterday.",
                n,
            )
        else:
            rose = ngettext(
                "Danger rose in the one region rated both days.",
                "Danger rose in all %(n)d regions rated both days.",
                n,
            )
            fell = ngettext(
                "Danger fell in the one region rated both days.",
                "Danger fell in all %(n)d regions rated both days.",
                n,
            )
        return (rose if read.rose == n else fell) % {"n": n}
    if read.rose and read.fell:
        rose_part = ngettext(
            "Danger rose in %(n)d region", "Danger rose in %(n)d regions", read.rose
        ) % {"n": read.rose}
        return _("%(rose)s and fell in %(fell)d since yesterday.") % {
            "rose": rose_part,
            "fell": read.fell,
        }
    if read.rose:
        moved = ngettext(
            "Danger rose in %(n)d region since yesterday",
            "Danger rose in %(n)d regions since yesterday",
            read.rose,
        ) % {"n": read.rose}
    else:
        moved = ngettext(
            "Danger fell in %(n)d region since yesterday",
            "Danger fell in %(n)d regions since yesterday",
            read.fell,
        ) % {"n": read.fell}
    return _("%(moved)s and held in %(held)d.") % {"moved": moved, "held": read.held}


def daily_read_sentences(read: CountryRead) -> list[str]:
    """
    Return a country's movement, peak and problem sentences.

    The swing is not among them: it carries a link, so the template writes
    it from :func:`present_daily_read`'s ``swing``.

    Args:
        read: One country's read.

    Returns:
        Up to three sentences, in the active language.

    """
    sentences: list[str] = []
    movement = _movement_sentence(read)
    if movement:
        sentences.append(movement)
    sentences.append(
        ngettext(
            "Highest today: %(level)s, in %(count)d of %(total)d region.",
            "Highest today: %(level)s, in %(count)d of %(total)d regions.",
            read.rated_count,
        )
        % {
            "level": level_word(read.peak_level),
            "count": read.peak_count,
            "total": read.rated_count,
        }
    )
    if read.top_problem:
        sentences.append(
            ngettext(
                "Named most: %(problem)s, in %(count)d of %(total)d region.",
                "Named most: %(problem)s, in %(count)d of %(total)d regions.",
                read.rated_count,
            )
            % {
                "problem": problem_name(read.top_problem),
                "count": read.top_problem_count,
                "total": read.rated_count,
            }
        )
    return sentences


def present_daily_read(read: DailyRead | None) -> dict[str, Any] | None:
    """
    Return the read as the homepage partial renders it.

    Args:
        read: The day's read, or None.

    Returns:
        ``{"day", "countries": [{country, label, peak_level, sentences,
        swing}]}``, or None when there is no read. ``swing`` holds the
        region's name and url and both levels as words.

    """
    if read is None:
        return None
    countries = []
    for c in read.countries:
        swing = None
        if c.swing is not None:
            swing = {
                "region_code": c.swing.region_code,
                "region_name": c.swing.region_name,
                "url": c.swing.url,
                "before": level_word(c.swing.before),
                "after": level_word(c.swing.after),
            }
        countries.append(
            {
                "country": c.country,
                "label": _country_label(c.country),
                "peak_level": c.peak_level,
                "sentences": daily_read_sentences(c),
                "swing": swing,
            }
        )
    return {"day": read.day, "countries": countries}
