"""
tests/ingest/normalise/test_rest_zones.py — Rest-zone features as documents (SNOW-1263).

The mapping (binding vs recommended, the period, a missing rules page), the
identity rule (geo.admin's feature id, unchanged by order or a renamed
zone) and the de-duplication of a zone two cantons both answer.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.ingest.normalise.rest_zones import (
    RestZoneError,
    collect_documents,
    rest_zone_document,
    rest_zone_uuid,
)


def feature(
    feature_id: int = 30989, *, name: str = "Plan Mezdi (Nr. 169.0)", **props: Any
) -> dict[str, Any]:
    """One feature in the shape geo.admin's ``find`` answers."""
    return {
        "type": "Feature",
        "featureId": feature_id,
        "id": feature_id,
        "properties": {
            "wrz_name": name,
            "schutzs_de": "rechtsverbindlich",
            "schutzs_fr": "contraignant",
            "schutzs_it": "vincolante",
            "bestimmung": "R20",
            "best_de": "Zutrittsverbot (zu Fuss und Wintersportarten)",
            "best_fr": "Interdiction d’accès",
            "best_it": "Divieto di accesso",
            "schutzzeit": "21.12. - 30.04.",
            "grundlage": "Nutzungsplanung",
            "beschlussjahr": "2001",
            "zusatzinformation": " Zutrittsverbot ",
            "kanton": "GR",
            "url_kanton": None,
            **props,
        },
        "geometry": {
            "type": "MultiPolygon",
            "coordinates": [
                [[[10.12, 46.75], [10.13, 46.75], [10.13, 46.76], [10.12, 46.75]]]
            ],
        },
    }


def test_a_binding_zone_reads_as_binding() -> None:
    """The German status decides; the three languages are kept."""
    document = rest_zone_document(feature())
    assert document["binding"] is True
    assert document["status"]["fr"] == "contraignant"
    assert document["feature_id"] == 30989
    assert document["geometry"]["type"] == "MultiPolygon"


def test_a_recommended_zone_is_not_binding() -> None:
    """Anything but "rechtsverbindlich" is a recommendation."""
    document = rest_zone_document(feature(schutzs_de="empfohlen"))
    assert document["binding"] is False


def test_the_period_and_restriction_are_kept_as_published() -> None:
    """FOEN's own text, stripped."""
    document = rest_zone_document(feature())
    assert document["period"] == "21.12. - 30.04."
    assert document["restriction"]["de"].startswith("Zutrittsverbot")
    assert document["note"] == "Zutrittsverbot"


def test_a_missing_rules_page_is_none() -> None:
    """A null url_kanton stays null rather than becoming ""."""
    assert rest_zone_document(feature())["url"] is None
    page = "https://www.gr.ch/wildruhe"
    assert rest_zone_document(feature(url_kanton=page))["url"] == page


def test_identity_is_the_feature_id_alone() -> None:
    """Renaming a zone keeps its uuid; another id gets another."""
    assert rest_zone_uuid(30989) == rest_zone_uuid(30989)
    assert rest_zone_uuid(30989) != rest_zone_uuid(30990)
    renamed = rest_zone_document(feature(name="Plan Mezdi (neu)"))
    assert rest_zone_uuid(renamed["feature_id"]) == rest_zone_uuid(30989)


def test_order_does_not_change_what_is_collected() -> None:
    """The same zones in another order are the same documents."""
    a, b = feature(1), feature(2)
    forward = {d["feature_id"]: d for d in collect_documents([a, b])}
    backward = {d["feature_id"]: d for d in collect_documents([b, a])}
    assert forward == backward


def test_a_zone_two_cantons_answer_is_collected_once() -> None:
    """The cross-canton duplicate collapses to one document."""
    assert len(collect_documents([feature(7), feature(7)])) == 1


def test_a_repeat_that_differs_fails_loudly() -> None:
    """Two versions of one zone: no telling which is FOEN's."""
    with pytest.raises(RestZoneError, match="answered twice"):
        collect_documents([feature(7), feature(7, name="Other")])


def test_a_feature_without_a_polygon_is_refused() -> None:
    """No geometry, no zone."""
    broken = feature()
    broken["geometry"] = {"type": "Point", "coordinates": [10.1, 46.7]}
    with pytest.raises(RestZoneError, match="no polygon"):
        rest_zone_document(broken)


def test_a_feature_without_an_id_is_refused() -> None:
    """Identity is the id, so a feature without one cannot be stored."""
    broken = feature()
    del broken["featureId"]
    del broken["id"]
    with pytest.raises(RestZoneError, match="no feature id"):
        rest_zone_document(broken)
