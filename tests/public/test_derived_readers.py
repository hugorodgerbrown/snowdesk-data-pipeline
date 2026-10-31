"""
tests/public/test_derived_readers.py — the region body reads the derived block.

SNOW-1341 stored what the region body computed from the render model on
every request — the period transition, the problem family and the headline
inputs — in the model's ``derived`` block. A current model must be read,
never recomputed: the headline follows the stored inputs, and the page
renders without calling ``build_derived`` at all.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from django.test import Client

from apps.bulletins.models import Bulletin
from apps.ingest.public import build_render_model, parse_dt
from apps.public.headlines import headline_for
from tests.factories import BulletinFactory, MicroRegionFactory, RegionBulletinFactory

SENTINEL = (
    Path(__file__).resolve().parents[1]
    / "sentinels"
    / "slf"
    / "C-split-day-multi-problem"
    / "source.json"
)


def _publish(model: dict[str, Any]) -> tuple[Any, Bulletin]:
    """Store the split-day sentinel with ``model`` as its render model."""
    properties = json.loads(SENTINEL.read_text())
    region_id = properties["regions"][0]["regionID"]
    region = MicroRegionFactory.create(region_id=region_id, slug=region_id.lower())
    start = parse_dt(properties["validTime"]["startTime"])
    bulletin = BulletinFactory.create(
        raw_data={"type": "Feature", "geometry": None, "properties": properties},
        render_model=model,
        render_model_version=model["version"],
        issued_at=start,
        valid_from=start,
        valid_to=start + timedelta(days=1),
    )
    RegionBulletinFactory.create(bulletin=bulletin, region=region)
    return region, bulletin


@pytest.mark.django_db
def test_the_headline_follows_the_stored_inputs(client: Client) -> None:
    """A different stored family is a different headline: it was read."""
    model = build_render_model(json.loads(SENTINEL.read_text()))
    inputs = model["derived"]["headline_inputs"]
    stored = {**inputs, "family": "wet" if inputs["family"] != "wet" else "slab"}
    model["derived"]["headline_inputs"] = stored
    region, bulletin = _publish(model)
    response = client.get(region.get_absolute_url(bulletin.target_date))
    assert response.status_code == 200
    assert response.context["headline"] == headline_for(
        stored["source"],
        stored["partition_type"],
        stored["peak_rating"],
        stored["direction"],
        stored["family"],
    )


@pytest.mark.django_db
def test_a_current_model_is_never_recomputed(client: Client) -> None:
    """No call to build_derived while the page renders a v10 model."""
    model = build_render_model(json.loads(SENTINEL.read_text()))
    region, bulletin = _publish(model)
    with patch(
        "apps.ingest.normalise.render_model.build_derived",
        side_effect=AssertionError("recomputed"),
    ):
        response = client.get(region.get_absolute_url(bulletin.target_date))
    assert response.status_code == 200
