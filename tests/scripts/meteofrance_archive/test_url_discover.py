# test_url_discover.py — Tests for Stage 2: mf_bra_url_discover.py
"""Tests for the BRA URL discovery stage (Stage 2)."""

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3] / "scripts" / "meteofrance-archive"))

from mf_bra_url_discover import (  # noqa: E402
    CSV_COLUMNS,
    _parse_args,
    build_pdf_url,
    expand_index_record,
    heures_to_date,
    run,
    write_csv,
)


class TestHeurestoDate:
    """Tests for heures_to_date()."""

    def test_extracts_date_from_heures(self) -> None:
        """Should parse YYYYMMDDHHMMSS into YYYY-MM-DD."""
        assert heures_to_date("20260521140706") == "2026-05-21"

    def test_december_date(self) -> None:
        """Should handle December (month 12) correctly."""
        assert heures_to_date("20251201120000") == "2025-12-01"


class TestBuildPdfUrl:
    """Tests for build_pdf_url()."""

    def test_builds_correct_url(self) -> None:
        """URL should follow BRA.{MASSIF}.{HEURES}.pdf pattern."""
        url = build_pdf_url("CHABLAIS", "20260521140706")
        assert url == (
            "https://donneespubliques.meteofrance.fr/donnees_libres/Pdf/BRA"
            "/BRA.CHABLAIS.20260521140706.pdf"
        )

    def test_hyphenated_massif(self) -> None:
        """Hyphenated massif names should be preserved in the URL."""
        url = build_pdf_url("MONT-BLANC", "20260521140702")
        assert "MONT-BLANC" in url


class TestExpandIndexRecord:
    """Tests for expand_index_record()."""

    def _ok_record(
        self, massif: str = "CHABLAIS", heures: list[str] | None = None
    ) -> dict[str, object]:
        """Return a minimal ok index record."""
        if heures is None:
            heures = ["20260521140706"]
        return {
            "date": "20260521",
            "status": "ok",
            "massifs": [{"massif": massif, "heures": heures}],
        }

    def test_ok_record_produces_rows(self) -> None:
        """An ok record should produce one row per (massif, heures) pair."""
        rows = expand_index_record(self._ok_record())
        assert len(rows) == 1
        assert rows[0]["massif"] == "CHABLAIS"

    def test_row_contains_all_columns(self) -> None:
        """Each row should have all CSV columns."""
        rows = expand_index_record(self._ok_record())
        for col in CSV_COLUMNS:
            assert col in rows[0]

    def test_multi_heures_produces_multiple_rows(self) -> None:
        """Multiple heures values should produce one row each."""
        record = self._ok_record(heures=["20260521120000", "20260521160000"])
        rows = expand_index_record(record)
        assert len(rows) == 2
        heures_vals = {r["heures"] for r in rows}
        assert heures_vals == {"20260521120000", "20260521160000"}

    def test_non_alpine_massif_filtered_out_by_default(self) -> None:
        """A Pyrenean massif should be excluded when using the default filter."""
        record = self._ok_record(massif="ASPE-OSSAU")
        rows = expand_index_record(record)
        assert rows == []

    def test_custom_filter_allows_non_alpine(self) -> None:
        """A custom massif_filter can include any massif."""
        record = self._ok_record(massif="ASPE-OSSAU")
        rows = expand_index_record(record, massif_filter={"ASPE-OSSAU"})
        assert len(rows) == 1

    def test_not_found_record_produces_no_rows(self) -> None:
        """A not_found record should produce no rows."""
        record = {"date": "20260521", "status": "not_found", "massifs": []}
        rows = expand_index_record(record)
        assert rows == []

    def test_date_in_row_uses_iso_format(self) -> None:
        """The date column in the row should be in YYYY-MM-DD format."""
        rows = expand_index_record(self._ok_record())
        assert rows[0]["date"] == "2026-05-21"

    def test_url_in_row_contains_massif_and_heures(self) -> None:
        """The url column should contain both the massif name and heures stamp."""
        rows = expand_index_record(self._ok_record())
        assert "CHABLAIS" in rows[0]["url"]
        assert "20260521140706" in rows[0]["url"]


class TestWriteCsv:
    """Tests for write_csv()."""

    def test_writes_header_and_rows(self, tmp_path: Path) -> None:
        """CSV output should have a header row followed by data rows."""
        rows = [
            {
                "massif": "CHABLAIS",
                "date": "2026-05-21",
                "heures": "20260521140706",
                "url": "https://example.com/BRA.CHABLAIS.20260521140706.pdf",
                "status": "pending",
            }
        ]
        out = tmp_path / "urls.csv"
        write_csv(rows, out)
        with out.open() as fh:
            reader = csv.DictReader(fh)
            data = list(reader)
        assert len(data) == 1
        assert data[0]["massif"] == "CHABLAIS"


class TestRun:
    """Integration tests for the run() function."""

    def _make_index(self, tmp_path: Path, records: list[dict[str, object]]) -> Path:
        """Write records to a temp NDJSON file and return the path."""
        p = tmp_path / "bra_indexes.ndjson"
        p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        return p

    def test_run_produces_correct_row_count(self, tmp_path: Path) -> None:
        """run() should produce one row per (alpine massif, heures) pair."""
        index = self._make_index(
            tmp_path,
            [
                {
                    "date": "20260521",
                    "status": "ok",
                    "massifs": [
                        {"massif": "CHABLAIS", "heures": ["20260521140706"]},
                        {"massif": "MONT-BLANC", "heures": ["20260521140702"]},
                        {"massif": "ASPE-OSSAU", "heures": ["20260521120000"]},
                    ],
                }
            ],
        )
        output = tmp_path / "urls.csv"
        counts = run(
            index_path=index,
            output_path=output,
            commit=False,
            verbosity=0,
        )
        # ASPE-OSSAU is Pyrenean and should be excluded
        assert counts["rows_written"] == 2
        assert counts["massifs_skipped"] == 1

    def test_commit_writes_csv_file(self, tmp_path: Path) -> None:
        """With commit=True, the CSV file should be written."""
        index = self._make_index(
            tmp_path,
            [
                {
                    "date": "20260521",
                    "status": "ok",
                    "massifs": [{"massif": "CHABLAIS", "heures": ["20260521140706"]}],
                }
            ],
        )
        output = tmp_path / "urls.csv"
        run(
            index_path=index,
            output_path=output,
            commit=True,
            verbosity=0,
        )
        assert output.exists()
        with output.open() as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["massif"] == "CHABLAIS"

    def test_dry_run_does_not_write(self, tmp_path: Path) -> None:
        """Without commit=True, no CSV file should be written."""
        index = self._make_index(
            tmp_path,
            [
                {
                    "date": "20260521",
                    "status": "ok",
                    "massifs": [{"massif": "CHABLAIS", "heures": ["20260521140706"]}],
                }
            ],
        )
        output = tmp_path / "urls.csv"
        run(
            index_path=index,
            output_path=output,
            commit=False,
            verbosity=0,
        )
        assert not output.exists()

    def test_multiple_heures_per_massif(self, tmp_path: Path) -> None:
        """Re-published bulletins (multiple heures) each become a separate row."""
        index = self._make_index(
            tmp_path,
            [
                {
                    "date": "20260521",
                    "status": "ok",
                    "massifs": [
                        {
                            "massif": "CHABLAIS",
                            "heures": ["20260521120000", "20260521160000"],
                        }
                    ],
                }
            ],
        )
        output = tmp_path / "urls.csv"
        counts = run(
            index_path=index,
            output_path=output,
            commit=False,
            verbosity=0,
        )
        assert counts["rows_written"] == 2

    def test_sample_index_fixture(
        self, sample_index_json: list[dict[str, object]]
    ) -> None:
        """The live index fixture should expand correctly (>= 23 Alpine rows)."""
        # Write the fixture to a temp NDJSON-style file.
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".ndjson", delete=False, encoding="utf-8"
        ) as fh:
            # The fixture is a JSON array — wrap it in an ok record.
            fh.write(
                json.dumps(
                    {"date": "20260521", "status": "ok", "massifs": sample_index_json}
                )
                + "\n"
            )
            index_path = Path(fh.name)

        try:
            import tempfile as _tf

            with _tf.TemporaryDirectory() as tmp_dir:
                out = Path(tmp_dir) / "urls.csv"
                counts = run(
                    index_path=index_path,
                    output_path=out,
                    commit=False,
                    verbosity=0,
                )
            # The fixture has 35 massifs; 23 are Alpine
            assert counts["rows_written"] == 23
        finally:
            index_path.unlink(missing_ok=True)


class TestMassifCLIFlag:
    """Tests for the --massif command-line flag."""

    def test_parse_args_accepts_massif_flag(self) -> None:
        """--massif should parse a single massif name."""
        args = _parse_args(["--massif", "CHABLAIS"])
        assert args.massif == ["CHABLAIS"]

    def test_parse_args_accepts_multiple_massifs(self) -> None:
        """--massif should accept multiple space-separated massif names."""
        args = _parse_args(["--massif", "CHABLAIS", "MONT-BLANC"])
        assert args.massif == ["CHABLAIS", "MONT-BLANC"]

    def test_parse_args_massif_defaults_to_none(self) -> None:
        """Without --massif, args.massif should be None (use all Alpine massifs)."""
        args = _parse_args([])
        assert args.massif is None

    def test_run_with_massif_filter_limits_output(self, tmp_path: Path) -> None:
        """run() with a massif_filter of {CHABLAIS} should only produce CHABLAIS rows."""
        index = tmp_path / "bra_indexes.ndjson"
        index.write_text(
            json.dumps(
                {
                    "date": "20260521",
                    "status": "ok",
                    "massifs": [
                        {"massif": "CHABLAIS", "heures": ["20260521140706"]},
                        {"massif": "MONT-BLANC", "heures": ["20260521140702"]},
                        {"massif": "BELLEDONNE", "heures": ["20260521140700"]},
                    ],
                }
            )
            + "\n"
        )
        output = tmp_path / "urls.csv"
        counts = run(
            index_path=index,
            output_path=output,
            massif_filter={"CHABLAIS"},
            commit=True,
            verbosity=0,
        )
        assert counts["rows_written"] == 1
        rows = list(csv.DictReader(output.open()))
        assert len(rows) == 1
        assert rows[0]["massif"] == "CHABLAIS"

    def test_run_with_two_massif_filter(self, tmp_path: Path) -> None:
        """run() with massif_filter={CHABLAIS, MONT-BLANC} should emit two rows."""
        index = tmp_path / "bra_indexes.ndjson"
        index.write_text(
            json.dumps(
                {
                    "date": "20260521",
                    "status": "ok",
                    "massifs": [
                        {"massif": "CHABLAIS", "heures": ["20260521140706"]},
                        {"massif": "MONT-BLANC", "heures": ["20260521140702"]},
                        {"massif": "BELLEDONNE", "heures": ["20260521140700"]},
                    ],
                }
            )
            + "\n"
        )
        output = tmp_path / "urls.csv"
        counts = run(
            index_path=index,
            output_path=output,
            massif_filter={"CHABLAIS", "MONT-BLANC"},
            commit=False,
            verbosity=0,
        )
        assert counts["rows_written"] == 2
