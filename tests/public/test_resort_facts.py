"""
tests/public/test_resort_facts.py — the one resort-facts formatter
(apps.public.resort_facts, SNOW-1161).

Both the resort page's facts card and the place sheet's summary line read
it, so these tests pin the formatting in one place:
  - display order, and an unset field having no fact at all;
  - a genuine zero is a fact, not a blank;
  - elevation and season degrade to one-sided from/to readings;
  - the summary line joins elevation, lifts and piste, pluralised, and is
    "" when none is set.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from apps.public.resort_facts import resort_fact_summary, resort_facts


def _resort(**overrides: Any) -> Any:
    """A stand-in resort carrying every field the formatter reads."""
    fields: dict[str, Any] = {
        "operator_name": "Téléverbier",
        "website": "https://www.verbier.ch/",
        "notes": "Marked itineraries, not pistes.",
        "num_lifts": 34,
        "num_runs": 92,
        "total_piste_km": 210,
        "base_elevation_m": 1500,
        "top_elevation_m": 3330,
        "typical_season_open": "12-01",
        "typical_season_close": "04-30",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_facts_come_in_display_order() -> None:
    keys = [fact.key for fact in resort_facts(_resort())]
    assert keys == [
        "elevation",
        "lifts",
        "runs",
        "piste",
        "season",
        "operator",
        "website",
        "notes",
    ]


def test_values_are_formatted_as_the_page_reads_them() -> None:
    by_key = {fact.key: fact for fact in resort_facts(_resort())}
    assert by_key["elevation"].value == "1,500–3,330 m"
    assert by_key["lifts"].value == "34"
    assert by_key["piste"].value == "210 km"
    assert by_key["season"].value == "1 Dec – 30 Apr"
    assert by_key["website"].value == "Official website"
    assert by_key["website"].href == "https://www.verbier.ch/"


def test_unset_fields_have_no_fact() -> None:
    resort = _resort(
        operator_name="",
        website="",
        notes="",
        num_runs=None,
        typical_season_open="",
        typical_season_close="",
    )
    keys = [fact.key for fact in resort_facts(resort)]
    assert keys == ["elevation", "lifts", "piste"]


def test_nothing_set_is_an_empty_list() -> None:
    resort = _resort(
        operator_name="",
        website="",
        notes="",
        num_lifts=None,
        num_runs=None,
        total_piste_km=None,
        base_elevation_m=None,
        top_elevation_m=None,
        typical_season_open="",
        typical_season_close="",
    )
    assert resort_facts(resort) == []
    assert resort_fact_summary(resort) == ""


def test_a_zero_is_a_fact() -> None:
    by_key = {fact.key: fact for fact in resort_facts(_resort(num_lifts=0))}
    assert by_key["lifts"].value == "0"


def test_one_sided_elevation_and_season() -> None:
    resort = _resort(
        top_elevation_m=None, typical_season_open="12-14", typical_season_close=""
    )
    by_key = {fact.key: fact for fact in resort_facts(resort)}
    assert by_key["elevation"].value == "from 1,500 m"
    assert by_key["season"].value == "from 14 Dec"
    resort = _resort(base_elevation_m=None, typical_season_open="")
    by_key = {fact.key: fact for fact in resort_facts(resort)}
    assert by_key["elevation"].value == "to 3,330 m"
    assert by_key["season"].value == "to 30 Apr"


def test_summary_joins_elevation_lifts_and_piste() -> None:
    assert resort_fact_summary(_resort()) == "1,500–3,330 m · 34 lifts · 210 km piste"


def test_summary_pluralises_and_skips_unset() -> None:
    resort = _resort(base_elevation_m=None, top_elevation_m=None, num_lifts=1)
    assert resort_fact_summary(resort) == "1 lift · 210 km piste"
