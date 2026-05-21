# test_caaml_envelope.py — Tests for the _caaml.py GeoJSON Feature envelope builder.
"""Tests for the CAAML 6.0 GeoJSON Feature envelope builder."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3] / "scripts" / "meteofrance-archive"))

from _caaml import build_envelope  # noqa: E402


class TestBuildEnvelope:
    """Tests for build_envelope()."""

    def test_geojson_feature_type(self) -> None:
        """The top-level type must be 'Feature'."""
        env = build_envelope(massif="CHABLAIS", bulletin_date=date(2026, 5, 22))
        assert env["type"] == "Feature"

    def test_geometry_is_none(self) -> None:
        """geometry must be None (BRA has no spatial footprint at this stage)."""
        env = build_envelope(massif="CHABLAIS", bulletin_date=date(2026, 5, 22))
        assert env["geometry"] is None

    def test_properties_present(self) -> None:
        """Properties dict must be present."""
        env = build_envelope(massif="CHABLAIS", bulletin_date=date(2026, 5, 22))
        assert isinstance(env["properties"], dict)

    def test_region_id_format(self) -> None:
        """regionID should use the FR-{MASSIF} format."""
        env = build_envelope(massif="CHABLAIS", bulletin_date=date(2026, 5, 22))
        props = env["properties"]
        assert isinstance(props, dict)
        regions = props["regions"]
        assert isinstance(regions, list)
        assert regions[0]["regionID"] == "FR-CHABLAIS"

    def test_valid_time_contains_date(self) -> None:
        """validTime.startTime must contain the bulletin date string."""
        env = build_envelope(massif="CHABLAIS", bulletin_date=date(2026, 5, 22))
        props = env["properties"]
        assert isinstance(props, dict)
        valid_time = props["validTime"]
        assert isinstance(valid_time, dict)
        assert "2026-05-22" in str(valid_time["startTime"])

    def test_custom_data_mf_massif(self) -> None:
        """customData.MF.massif must match the input massif name."""
        env = build_envelope(massif="MONT-BLANC", bulletin_date=date(2026, 5, 22))
        props = env["properties"]
        assert isinstance(props, dict)
        mf = props["customData"]
        assert isinstance(mf, dict)
        mf_inner = mf["MF"]
        assert isinstance(mf_inner, dict)
        assert mf_inner["massif"] == "MONT-BLANC"

    def test_custom_data_mf_date(self) -> None:
        """customData.MF.date must be the ISO date string."""
        env = build_envelope(massif="CHABLAIS", bulletin_date=date(2026, 5, 22))
        props = env["properties"]
        assert isinstance(props, dict)
        mf_data = props["customData"]
        assert isinstance(mf_data, dict)
        mf_inner = mf_data["MF"]
        assert isinstance(mf_inner, dict)
        assert mf_inner["date"] == "2026-05-22"

    def test_extra_properties_merged(self) -> None:
        """Extra properties passed via 'properties' kwarg are merged in."""
        env = build_envelope(
            massif="CHABLAIS",
            bulletin_date=date(2026, 5, 22),
            properties={"dangerRatings": [{"mainValue": "low"}]},
        )
        props = env["properties"]
        assert isinstance(props, dict)
        assert "dangerRatings" in props

    def test_lang_default_french(self) -> None:
        """Default language should be 'fr'."""
        env = build_envelope(massif="CHABLAIS", bulletin_date=date(2026, 5, 22))
        props = env["properties"]
        assert isinstance(props, dict)
        assert props["lang"] == "fr"
