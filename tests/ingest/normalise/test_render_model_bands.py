"""
tests/ingest/normalise/test_render_model_bands.py — one problem type in two bands.

An ALBINA or Météo-France bulletin may describe the same problem type in
the same period twice, once per elevation band: considerable wind slab on
north-facing slopes above 2200 m and low wind slab on south-facing slopes
below it. The builder keyed raw problems by ``(problemType,
validTimePeriod)``, last one winning, so one band vanished from the render
model — and so from the page's problem cards and the MCP
``get_avalanche_problems`` tool. Found by Codex reviewing SNOW-1342; fixed
under SNOW-1341. Every raw problem must survive, each with its own fields.
"""

from __future__ import annotations

import datetime
from datetime import UTC
from typing import Any

import pytest

from apps.ingest.normalise.render_model import build_render_model
from apps.mcp_server import tools
from tests.factories import BulletinFactory, MicroRegionFactory, RegionBulletinFactory

HIGH_BAND = {
    "problemType": "wind_slab",
    "validTimePeriod": "all_day",
    "aspects": ["N", "NE", "NW"],
    "elevation": {"lowerBound": "2200"},
    "dangerRating": {"mainValue": "considerable"},
    "avalancheSize": 3,
}
LOW_BAND = {
    "problemType": "wind_slab",
    "validTimePeriod": "all_day",
    "aspects": ["S", "SE", "SW"],
    "elevation": {"upperBound": "2200"},
    "dangerRating": {"mainValue": "low"},
    "avalancheSize": 1,
}


def _properties(custom_data: dict[str, Any]) -> dict[str, Any]:
    """Return a two-band bulletin under the given provider's customData.

    Args:
        custom_data: ``{"ALBINA": {...}}`` or ``{"MF": {...}}``.

    Returns:
        The CAAML properties.

    """
    return {
        "bulletinID": "two-bands",
        "lang": "en",
        "customData": custom_data,
        "validTime": {
            "startTime": "2026-02-10T16:00:00+00:00",
            "endTime": "2026-02-11T16:00:00+00:00",
        },
        "dangerRatings": [
            {
                "mainValue": "considerable",
                "elevation": {"lowerBound": "2200"},
                "validTimePeriod": "all_day",
            },
            {
                "mainValue": "low",
                "elevation": {"upperBound": "2200"},
                "validTimePeriod": "all_day",
            },
        ],
        "avalancheProblems": [dict(HIGH_BAND), dict(LOW_BAND)],
        "regions": [{"regionID": "AT-07-01", "name": "Two Bands"}],
    }


PROVIDERS: dict[str, dict[str, Any]] = {
    "albina": {"ALBINA": {"mainDate": "2026-02-11"}},
    "meteofrance": {"MF": {}},
}


def _problems(render_model: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every problem across a render model's traits, in order."""
    return [p for t in render_model["traits"] for p in t["problems"]]


@pytest.mark.parametrize("provider", sorted(PROVIDERS))
def test_both_bands_survive_with_their_own_fields(provider: str) -> None:
    """Two same-type, same-period problems in two bands are two problems."""
    rm = build_render_model(_properties(PROVIDERS[provider]))
    problems = _problems(rm)
    assert [p["problem_type"] for p in problems] == ["wind_slab", "wind_slab"]
    by_rating = {p["danger_rating_value"]: p for p in problems}
    assert set(by_rating) == {"considerable", "low"}
    high, low = by_rating["considerable"], by_rating["low"]
    assert high["aspects"] == ["N", "NE", "NW"]
    assert low["aspects"] == ["S", "SE", "SW"]
    assert high["elevation"]["lower"] == 2200
    assert high["elevation"]["upper"] is None
    assert low["elevation"]["upper"] == 2200
    assert low["elevation"]["lower"] is None
    if provider == "albina":
        # ALBINA's per-problem EAWS fields (Météo-France carries none; neither
        # carries a per-problem comment — their prose is bulletin-level).
        assert high["avalanche_size"] == 3
        assert low["avalanche_size"] == 1


def test_albina_bands_are_one_trait_each() -> None:
    """ALBINA groups by band, so each band's trait holds only its problem."""
    rm = build_render_model(_properties(PROVIDERS["albina"]))
    by_band = {t["band_id"]: t["problems"] for t in rm["traits"]}
    assert set(by_band) == {"above-2200", "below-2200"}
    assert [p["danger_rating_value"] for p in by_band["above-2200"]] == ["considerable"]
    assert [p["danger_rating_value"] for p in by_band["below-2200"]] == ["low"]


@pytest.mark.django_db
@pytest.mark.parametrize("provider", sorted(PROVIDERS))
def test_get_avalanche_problems_returns_both_bands(provider: str) -> None:
    """The MCP tool reads the render model, so it answers both bands."""
    region = MicroRegionFactory.create(region_id="AT-07-01")
    properties = _properties(PROVIDERS[provider])
    valid_from = datetime.datetime(2026, 2, 10, 16, tzinfo=UTC)
    bulletin = BulletinFactory.create(
        raw_data={"type": "Feature", "geometry": None, "properties": properties},
        built=True,
        issued_at=valid_from,
        valid_from=valid_from,
        valid_to=valid_from + datetime.timedelta(days=1),
    )
    RegionBulletinFactory.create(bulletin=bulletin, region=region)
    assert bulletin.target_date is not None
    result = tools.get_avalanche_problems(region.region_id, bulletin.target_date)
    assert result["count"] == 2
    assert {p["danger_rating_value"] for p in result["problems"]} == {
        "considerable",
        "low",
    }
    assert {tuple(p["aspects"]) for p in result["problems"]} == {
        ("N", "NE", "NW"),
        ("S", "SE", "SW"),
    }
