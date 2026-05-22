# test_load_archive.py — Tests for the Météo-France archive loader.
#
# Exercises load_archive.py against a minimal 3-line fixture NDJSON:
#   - Line 1: ARAVIS (FR-02) — valid Alpine massif.
#   - Line 2: MONT-BLANC (FR-03) — valid Alpine massif.
#   - Line 3: UNKNOWNMASSIF — slug not in SLUG_TO_CODE (failure path).
#
# Integration tests require FR-02 and FR-03 MicroRegion rows to exist so
# that ``upsert_bulletin`` can link them.  These are loaded via the
# ``eaws_FR`` fixture using ``call_command("loaddata")``.
"""Tests for the Météo-France NDJSON archive loader (load_archive.py)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Add the script directory to sys.path so both load_archive and _massifs are
# importable, following the same pattern used by the other tests in this suite.
_SCRIPT_DIR = Path(__file__).parents[3] / "scripts" / "meteofrance-archive"
sys.path.insert(0, str(_SCRIPT_DIR))

import load_archive  # noqa: E402
import pytest  # noqa: E402
from django.core.management import call_command  # noqa: E402
from load_archive import _fixup_envelope, load_archive as run_loader  # noqa: E402

from bulletins.models import Bulletin, PipelineRun  # noqa: E402

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_NDJSON = FIXTURES_DIR / "sample_archive.ndjson"
REAL_SAMPLES_NDJSON = FIXTURES_DIR / "real_samples.ndjson"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def _load_fr_regions(db: object) -> None:
    """Load the eaws_FR.json fixture so FR-02 and FR-03 MicroRegion rows exist."""
    call_command("loaddata", "eaws_FR", verbosity=0)


# ---------------------------------------------------------------------------
# _fixup_envelope unit tests (no DB required)
# ---------------------------------------------------------------------------


class TestFixupEnvelope:
    """Unit tests for _fixup_envelope — no database access needed."""

    def test_translates_region_id(self) -> None:
        """regionID is translated from FR-{SLUG} to FR-{NN}."""
        from _massifs import SLUG_TO_CODE

        properties: dict[str, Any] = {
            "regions": [{"regionID": "FR-ARAVIS", "name": "Aravis"}],
            "customData": {"MF": {"massif": "ARAVIS", "date": "2026-01-15"}},
        }
        _fixup_envelope(properties, SLUG_TO_CODE)
        regions = properties["regions"]
        assert isinstance(regions, list)
        assert regions[0]["regionID"] == "FR-02"

    def test_synthesises_bulletin_id(self) -> None:
        """bulletinID is synthesised as FR-{NN}-{date}."""
        from _massifs import SLUG_TO_CODE

        properties: dict[str, Any] = {
            "regions": [{"regionID": "FR-CHABLAIS", "name": "Chablais"}],
            "customData": {"MF": {"massif": "CHABLAIS", "date": "2026-01-15"}},
        }
        bulletin_id = _fixup_envelope(properties, SLUG_TO_CODE)
        assert bulletin_id == "FR-01-2026-01-15"
        assert properties["bulletinID"] == "FR-01-2026-01-15"

    def test_unknown_slug_returns_none(self) -> None:
        """Unknown massif slug returns None without mutating properties."""
        from _massifs import SLUG_TO_CODE

        properties: dict[str, Any] = {
            "regions": [{"regionID": "FR-UNKNOWNMASSIF", "name": "Unknown"}],
            "customData": {"MF": {"massif": "UNKNOWNMASSIF", "date": "2026-01-15"}},
        }
        result = _fixup_envelope(properties, SLUG_TO_CODE)
        assert result is None
        assert "bulletinID" not in properties

    def test_missing_custom_data_returns_none(self) -> None:
        """Missing customData block returns None."""
        from _massifs import SLUG_TO_CODE

        properties: dict[str, Any] = {
            "regions": [{"regionID": "FR-ARAVIS", "name": "Aravis"}],
        }
        result = _fixup_envelope(properties, SLUG_TO_CODE)
        assert result is None

    def test_missing_regions_returns_none(self) -> None:
        """Missing regions list returns None."""
        from _massifs import SLUG_TO_CODE

        properties: dict[str, Any] = {
            "customData": {"MF": {"massif": "ARAVIS", "date": "2026-01-15"}},
        }
        result = _fixup_envelope(properties, SLUG_TO_CODE)
        assert result is None

    def test_mont_blanc_region_id(self) -> None:
        """MONT-BLANC slug translates to FR-03."""
        from _massifs import SLUG_TO_CODE

        properties: dict[str, Any] = {
            "regions": [{"regionID": "FR-MONT-BLANC", "name": "Mont Blanc"}],
            "customData": {"MF": {"massif": "MONT-BLANC", "date": "2026-01-20"}},
        }
        bulletin_id = _fixup_envelope(properties, SLUG_TO_CODE)
        assert bulletin_id == "FR-03-2026-01-20"


# ---------------------------------------------------------------------------
# Dry-run integration tests (no DB writes)
# ---------------------------------------------------------------------------


class TestDryRun:
    """Tests for dry-run mode (commit=False)."""

    def test_dry_run_writes_no_bulletins(self) -> None:
        """Dry-run must not create any Bulletin rows."""
        assert Bulletin.objects.count() == 0
        run_loader(SAMPLE_NDJSON, commit=False, verbose=False)
        assert Bulletin.objects.count() == 0

    def test_dry_run_writes_no_pipeline_run(self) -> None:
        """Dry-run must not create any PipelineRun rows."""
        assert PipelineRun.objects.count() == 0
        run_loader(SAMPLE_NDJSON, commit=False, verbose=False)
        assert PipelineRun.objects.count() == 0

    def test_dry_run_exits_nonzero_on_unknown_slug(self) -> None:
        """Exit code is 1 when the fixture contains an unknown slug."""
        # sample_archive.ndjson line 3 has UNKNOWNMASSIF.
        exit_code = run_loader(SAMPLE_NDJSON, commit=False, verbose=False)
        assert exit_code == 1

    def test_dry_run_exits_zero_on_clean_file(self, tmp_path: Path) -> None:
        """Exit code is 0 when all rows are valid (no unknown slugs)."""
        # Write a 2-line NDJSON with no unknown slugs.
        import json

        clean_ndjson = tmp_path / "clean.ndjson"
        lines = []
        for slug, code, name, date_str in [
            ("ARAVIS", "02", "Aravis", "2026-01-15"),
            ("CHABLAIS", "01", "Chablais", "2026-01-15"),
        ]:
            envelope = {
                "type": "Feature",
                "geometry": None,
                "properties": {
                    "lang": "fr",
                    "regions": [{"regionID": f"FR-{slug}", "name": name}],
                    "validTime": {
                        "startTime": f"{date_str}T00:00:00+00:00",
                        "endTime": f"{date_str}T23:59:59+00:00",
                    },
                    "customData": {"MF": {"massif": slug, "date": date_str}},
                    "dangerRatings": [],
                    "avalancheProblems": [],
                },
            }
            lines.append(json.dumps(envelope))
        clean_ndjson.write_text("\n".join(lines) + "\n")
        exit_code = run_loader(clean_ndjson, commit=False, verbose=False)
        assert exit_code == 0

    def test_dry_run_missing_file_exits_one(self, tmp_path: Path) -> None:
        """Missing input file exits with code 1."""
        missing = tmp_path / "does_not_exist.ndjson"
        exit_code = run_loader(missing, commit=False, verbose=False)
        assert exit_code == 1

    pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Commit integration tests (writes to DB)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCommitMode:
    """Integration tests for --commit mode.

    These tests require the eaws_FR fixture so that FR-02 and FR-03
    MicroRegion rows exist for upsert_bulletin to link.
    """

    pytestmark = pytest.mark.usefixtures("_load_fr_regions")

    def test_commit_creates_pipeline_run(self) -> None:
        """--commit mode opens exactly one PipelineRun."""
        assert PipelineRun.objects.count() == 0
        run_loader(SAMPLE_NDJSON, commit=True, verbose=False)
        assert PipelineRun.objects.count() == 1

    def test_pipeline_run_is_success(self) -> None:
        """The PipelineRun is marked SUCCESS after a clean load."""
        run_loader(SAMPLE_NDJSON, commit=True, verbose=False)
        run = PipelineRun.objects.get()
        assert run.status == PipelineRun.Status.SUCCESS

    def test_pipeline_run_triggered_by(self) -> None:
        """triggered_by is set to the canonical backfill label."""
        run_loader(SAMPLE_NDJSON, commit=True, verbose=False)
        run = PipelineRun.objects.get()
        assert run.triggered_by == load_archive.TRIGGERED_BY

    def test_commit_creates_two_bulletins(self) -> None:
        """Two valid rows (ARAVIS + MONT-BLANC) produce two Bulletin rows."""
        # Line 3 (UNKNOWNMASSIF) is skipped.
        run_loader(SAMPLE_NDJSON, commit=True, verbose=False)
        assert Bulletin.objects.count() == 2

    def test_bulletin_ids_are_synthesised(self) -> None:
        """Synthesised bulletinIDs follow the FR-{NN}-{date} format."""
        run_loader(SAMPLE_NDJSON, commit=True, verbose=False)
        ids = set(Bulletin.objects.values_list("bulletin_id", flat=True))
        assert "FR-02-2026-01-15" in ids
        assert "FR-03-2026-01-15" in ids

    def test_region_id_translated(self) -> None:
        """The stored raw_data uses FR-{NN} region IDs, not FR-{SLUG}."""
        run_loader(SAMPLE_NDJSON, commit=True, verbose=False)
        aravis = Bulletin.objects.get(bulletin_id="FR-02-2026-01-15")
        regions = aravis.raw_data["properties"]["regions"]
        assert regions[0]["regionID"] == "FR-02"

    def test_unknown_slug_is_skipped_not_stored(self) -> None:
        """The UNKNOWNMASSIF row is not persisted."""
        run_loader(SAMPLE_NDJSON, commit=True, verbose=False)
        # Only FR-02 and FR-03; nothing else.
        assert Bulletin.objects.count() == 2

    def test_exit_code_nonzero_on_unknown_slug(self) -> None:
        """Exit code is 1 when any row failed (UNKNOWNMASSIF line)."""
        exit_code = run_loader(SAMPLE_NDJSON, commit=True, verbose=False)
        assert exit_code == 1

    def test_pipeline_run_records_failed(self) -> None:
        """run.records_failed is incremented for the UNKNOWNMASSIF row."""
        run_loader(SAMPLE_NDJSON, commit=True, verbose=False)
        run = PipelineRun.objects.get()
        assert run.records_failed >= 1

    def test_upsert_second_run_updates_not_creates(self) -> None:
        """A second load with --commit updates existing rows (upsert semantics)."""
        run_loader(SAMPLE_NDJSON, commit=True, verbose=False)
        assert Bulletin.objects.count() == 2
        run_loader(SAMPLE_NDJSON, commit=True, verbose=False)
        assert Bulletin.objects.count() == 2  # No duplicates.
        assert PipelineRun.objects.count() == 2  # Two separate runs.

    def test_lang_is_fr(self) -> None:
        """Stored bulletins have lang='fr'."""
        run_loader(SAMPLE_NDJSON, commit=True, verbose=False)
        aravis = Bulletin.objects.get(bulletin_id="FR-02-2026-01-15")
        assert aravis.lang == "fr"


# ---------------------------------------------------------------------------
# Real-archive sample tests — verifies the loader handles the actual
# upstream data shape, including the space-form massif names and the
# EMBRUNNAIS typo aliased in ``_massifs._MASSIF_ALIASES``.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRealSamples:
    """Integration tests against verbatim lines from the real BRA archive.

    The fixture ``real_samples.ndjson`` contains five lines lifted from the
    2025-2026 ``bulletins.ndjson`` artefact, chosen to exercise every shape
    of ``customData.MF.massif`` that appears upstream: canonical single-word,
    canonical hyphenated, and the space-form variants used in some PDFs.
    """

    pytestmark = pytest.mark.usefixtures("_load_fr_regions")

    def test_all_five_rows_land(self) -> None:
        """Every real-archive sample lands as a Bulletin row."""
        exit_code = run_loader(REAL_SAMPLES_NDJSON, commit=True, verbose=False)
        assert exit_code == 0
        assert Bulletin.objects.count() == 5

    def test_canonical_hyphen_slugs_resolve(self) -> None:
        """ARAVIS, MONT-BLANC, CHARTREUSE land under their canonical region IDs."""
        run_loader(REAL_SAMPLES_NDJSON, commit=True, verbose=False)
        ids = set(Bulletin.objects.values_list("bulletin_id", flat=True))
        assert "FR-02-2026-11-04" in ids  # ARAVIS
        assert "FR-03-2026-11-04" in ids  # MONT-BLANC
        assert "FR-07-2025-11-20" in ids  # CHARTREUSE

    def test_space_form_slug_resolves(self) -> None:
        """EMBRUNAIS PARPAILLON (space form) lands under FR-20."""
        run_loader(REAL_SAMPLES_NDJSON, commit=True, verbose=False)
        assert Bulletin.objects.filter(bulletin_id="FR-20-2026-01-08").exists()

    def test_mixed_hyphen_space_slug_resolves(self) -> None:
        """HAUT-VAR HAUT-VERDON (internal hyphens + middle space) lands under FR-22."""
        run_loader(REAL_SAMPLES_NDJSON, commit=True, verbose=False)
        assert Bulletin.objects.filter(bulletin_id="FR-22-2026-11-05").exists()
