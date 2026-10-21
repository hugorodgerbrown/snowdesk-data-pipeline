"""
apps/public/resort_facts.py — the one formatter for a resort's facts (SNOW-1161).

A resort's facts reached readers through two components that formatted the
same columns differently: the resort page's facts card
(``_resort_facts.html``: "Piste", "Typical season", "1,500–3,330 m", blanks
omitted) and the map's meta rows (``_resort_meta_rows.html``: "Piste
length", "Season", "1500–3330 m", a dashed "—" box per blank). SNOW-1161
deleted the meta rows; both surfaces now read this module, so a resort
cannot be described two ways.

``resort_facts`` returns the facts in display order, set fields only — an
unset field has no fact, and a resort with none returns an empty list.
``resort_fact_summary`` is the place sheet's one line: elevation, lifts and
piste, joined with " · ".

Numeric fields test ``is not None`` rather than truthiness, so a genuine
zero (no lifts, no piste) is a fact rather than a blank. Elevation and
season each compose two nullable columns and degrade to a one-sided "from" /
"to" reading when only one end is curated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.contrib.humanize.templatetags.humanize import intcomma
from django.template.defaultfilters import date as date_filter, floatformat
from django.utils.translation import gettext as _, ngettext

from apps.public.templatetags.snowdesk_time import month_day

if TYPE_CHECKING:
    from apps.regions.models import Resort

# The facts the place sheet's one line carries, in its order.
SUMMARY_KEYS: tuple[str, ...] = ("elevation", "lifts", "piste")


@dataclass(frozen=True)
class ResortFact:
    """One curated fact about a resort, formatted for reading.

    Attributes:
        key: Stable identifier ("elevation", "lifts", …); the card's
            ``data-testid`` suffix.
        label: The row label on the facts card.
        value: The formatted value ("1,500–3,330 m", "34", "Téléverbier").
            For the website it is the link text; for notes, the prose.
        summary: The value as the sheet's one line says it ("34 lifts").
            Equal to ``value`` where the value already reads on its own.
        href: The link target, for the website only.

    """

    key: str
    label: str
    value: str
    summary: str
    href: str = ""


def _elevation(resort: Resort) -> str:
    base, top = resort.base_elevation_m, resort.top_elevation_m
    if base is not None and top is not None:
        return f"{intcomma(base)}–{intcomma(top)} m"
    if base is not None:
        return _("from %(m)s m") % {"m": intcomma(base)}
    return _("to %(m)s m") % {"m": intcomma(top)}


def _season(resort: Resort) -> str:
    s_open = month_day(resort.typical_season_open)
    s_close = month_day(resort.typical_season_close)
    if s_open and s_close:
        return f"{date_filter(s_open, 'j M')} – {date_filter(s_close, 'j M')}"
    if s_open:
        return _("from %(d)s") % {"d": date_filter(s_open, "j M")}
    return _("to %(d)s") % {"d": date_filter(s_close, "j M")}


def _measures(resort: Resort) -> list[ResortFact]:
    """Return the numeric facts: elevation, lifts, runs, piste."""
    facts: list[ResortFact] = []
    if resort.base_elevation_m is not None or resort.top_elevation_m is not None:
        value = _elevation(resort)
        facts.append(ResortFact("elevation", _("Elevation"), value, value))
    if resort.num_lifts is not None:
        n = resort.num_lifts
        lifts = ngettext("%(n)s lift", "%(n)s lifts", n) % {"n": n}
        facts.append(ResortFact("lifts", _("Lifts"), str(n), lifts))
    if resort.num_runs is not None:
        n = resort.num_runs
        runs = ngettext("%(n)s run", "%(n)s runs", n) % {"n": n}
        facts.append(ResortFact("runs", _("Runs"), str(n), runs))
    if resort.total_piste_km is not None:
        km = floatformat(resort.total_piste_km, "-1")
        piste = _("%(km)s km piste") % {"km": km}
        facts.append(ResortFact("piste", _("Piste"), f"{km} km", piste))
    return facts


def _particulars(resort: Resort) -> list[ResortFact]:
    """Return the other facts: typical season, operator, website, notes."""
    facts: list[ResortFact] = []
    if resort.typical_season_open or resort.typical_season_close:
        value = _season(resort)
        facts.append(ResortFact("season", _("Typical season"), value, value))
    if resort.operator_name:
        name = resort.operator_name
        facts.append(ResortFact("operator", _("Operator"), name, name))
    if resort.website:
        text = _("Official website")
        facts.append(ResortFact("website", _("Website"), text, text, resort.website))
    if resort.notes:
        facts.append(ResortFact("notes", _("Notes"), resort.notes, resort.notes))
    return facts


def resort_facts(resort: Resort) -> list[ResortFact]:
    """Return a resort's set facts, in display order.

    Args:
        resort: The resort to describe.

    Returns:
        One ``ResortFact`` per curated field that is set: elevation, lifts,
        runs, piste, typical season, operator, website, notes. Empty when
        none is.

    """
    return _measures(resort) + _particulars(resort)


def resort_fact_summary(resort: Resort) -> str:
    """Return the place sheet's one-line summary of a resort.

    Args:
        resort: The resort to summarise.

    Returns:
        Its elevation, lifts and piste, the ones that are set, joined with
        " · " ("1,500–3,330 m · 34 lifts · 210 km piste"); "" when none is.

    """
    by_key = {fact.key: fact for fact in resort_facts(resort)}
    return " · ".join(by_key[key].summary for key in SUMMARY_KEYS if key in by_key)
