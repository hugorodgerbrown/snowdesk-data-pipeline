# test_build_aria2c_input.py — Tests for Stage 3: build_aria2c_input.py
"""Tests for the aria2c input file builder (Stage 3)."""

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3] / "scripts" / "meteofrance-archive"))

from build_aria2c_input import (  # noqa: E402
    EXIT_SKIPPED,
    build_aria2c_input,
    main,
    normalise_filename,
    row_to_aria2c_entry,
    run,
)


class TestNormaliseFilename:
    """Tests for normalise_filename()."""

    def test_basic_format(self) -> None:
        """Should produce BRA.{MASSIF}.{YYYY-MM-DD}.pdf."""
        assert (
            normalise_filename("CHABLAIS", "20260521140706")
            == "BRA.CHABLAIS.20260521140706.pdf"
        )

    def test_hyphenated_massif(self) -> None:
        """Hyphenated massif names should be preserved."""
        assert (
            normalise_filename("MONT-BLANC", "20260521140706")
            == "BRA.MONT-BLANC.20260521140706.pdf"
        )


class TestRowToAria2cEntry:
    """Tests for row_to_aria2c_entry()."""

    def _row(
        self,
        massif: str = "CHABLAIS",
        heures: str = "20260521140706",
        url: str = "https://example.com/BRA.CHABLAIS.20260521140706.pdf",
    ) -> dict[str, str]:
        """Return a minimal CSV row dict."""
        return {"massif": massif, "heures": heures, "url": url}

    def test_valid_row_returns_tuple(self) -> None:
        """A complete row should return a (url, out_line) tuple."""
        result = row_to_aria2c_entry(self._row())
        assert result is not None
        url_line, out_line = result
        assert url_line == "https://example.com/BRA.CHABLAIS.20260521140706.pdf"
        assert out_line == "  out=BRA.CHABLAIS.20260521140706.pdf"

    def test_missing_url_returns_none(self) -> None:
        """A row missing url should return None."""
        row = {"massif": "CHABLAIS", "heures": "20260521140706", "url": ""}
        assert row_to_aria2c_entry(row) is None

    def test_missing_massif_returns_none(self) -> None:
        """A row missing massif should return None."""
        row = {
            "massif": "",
            "heures": "20260521140706",
            "url": "https://example.com/bra.pdf",
        }
        assert row_to_aria2c_entry(row) is None

    def test_out_line_keeps_the_publication_timestamp(self) -> None:
        """The out= directive must retain ``heures`` so each issue gets its own file.

        This previously asserted the opposite — that the timestamp was stripped.
        Collapsing it meant two issues of one massif-day contended for one
        filename, aria2c renamed the loser ``.1``, and the record of which issue
        was which was lost (SNOW-559).
        """
        row = self._row(massif="MONT-BLANC")
        result = row_to_aria2c_entry(row)
        assert result is not None
        _, out_line = result
        assert out_line == "  out=BRA.MONT-BLANC.20260521140706.pdf"

    def test_two_issues_of_one_day_get_different_filenames(self) -> None:
        """Two issues published on the same date land on separate paths."""
        evening = row_to_aria2c_entry(self._row(heures="20260521140706"))
        morning = row_to_aria2c_entry(self._row(heures="20260522073000"))
        assert evening is not None
        assert morning is not None

        assert evening[1] != morning[1]


class TestBuildAria2cInput:
    """Tests for build_aria2c_input()."""

    def _row(
        self, massif: str = "CHABLAIS", heures: str = "20260521140706"
    ) -> dict[str, str]:
        """Return a minimal valid CSV row."""
        return {
            "massif": massif,
            "heures": heures,
            "url": f"https://example.com/BRA.{massif}.20260521140706.pdf",
        }

    def test_single_row_produces_three_lines(self) -> None:
        """Each entry should produce url line, out= line, and a blank separator."""
        lines, skipped = build_aria2c_input([self._row()])
        assert skipped == 0
        assert len(lines) == 3  # url, out=, blank
        assert lines[2] == ""

    def test_two_rows_produce_six_lines(self) -> None:
        """Two entries should produce 6 lines (3 per entry)."""
        rows = [self._row("CHABLAIS"), self._row("MONT-BLANC")]
        lines, skipped = build_aria2c_input(rows)
        assert len(lines) == 6

    def test_invalid_row_increments_skipped(self) -> None:
        """A row with missing fields should be counted as skipped."""
        rows = [{"massif": "", "heures": "", "url": ""}]
        lines, skipped = build_aria2c_input(rows)
        assert skipped == 1
        assert lines == []

    def test_url_appears_in_output(self) -> None:
        """The download URL should appear in the output lines."""
        url = "https://example.com/BRA.CHABLAIS.20260521140706.pdf"
        rows = [{"massif": "CHABLAIS", "heures": "20260521140706", "url": url}]
        lines, _ = build_aria2c_input(rows)
        assert url in lines


class TestRun:
    """Integration tests for run()."""

    def _write_csv(self, tmp_path: Path, rows: list[dict[str, str]]) -> Path:
        """Write a CSV file with the given rows and return the path."""
        p = tmp_path / "bra_urls.csv"
        with p.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=["massif", "date", "heures", "url", "status"]
            )
            writer.writeheader()
            writer.writerows(rows)
        return p

    def test_commit_writes_output_file(self, tmp_path: Path) -> None:
        """With commit=True, the aria2c input file should be written."""
        csv_path = self._write_csv(
            tmp_path,
            [
                {
                    "massif": "CHABLAIS",
                    "date": "2026-05-21",
                    "heures": "20260521140706",
                    "url": "https://example.com/bra.pdf",
                    "status": "pending",
                }
            ],
        )
        output = tmp_path / "downloads.txt"
        run(csv_path=csv_path, output_path=output, commit=True, verbosity=0)
        assert output.exists()
        content = output.read_text()
        assert "https://example.com/bra.pdf" in content
        assert "BRA.CHABLAIS.20260521140706.pdf" in content

    def test_dry_run_does_not_write(self, tmp_path: Path) -> None:
        """Without commit=True, no output file should be written."""
        csv_path = self._write_csv(
            tmp_path,
            [
                {
                    "massif": "CHABLAIS",
                    "date": "2026-05-21",
                    "heures": "20260521140706",
                    "url": "https://example.com/bra.pdf",
                    "status": "pending",
                }
            ],
        )
        output = tmp_path / "downloads.txt"
        run(csv_path=csv_path, output_path=output, commit=False, verbosity=0)
        assert not output.exists()

    def test_skipped_count_returned(self, tmp_path: Path) -> None:
        """Rows with missing fields should be counted as skipped."""
        csv_path = self._write_csv(
            tmp_path,
            [{"massif": "", "date": "", "heures": "", "url": "", "status": ""}],
        )
        output = tmp_path / "downloads.txt"
        counts = run(csv_path=csv_path, output_path=output, commit=False, verbosity=0)
        assert counts["skipped"] == 1


class TestMain:
    """Tests for the main() entry point."""

    def _write_csv(self, tmp_path: Path) -> Path:
        """Write a minimal valid CSV file."""
        p = tmp_path / "bra_urls.csv"
        with p.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=["massif", "date", "heures", "url", "status"]
            )
            writer.writeheader()
            writer.writerow(
                {
                    "massif": "CHABLAIS",
                    "date": "2026-05-21",
                    "heures": "20260521140706",
                    "url": "https://example.com/bra.pdf",
                    "status": "pending",
                }
            )
        return p

    def test_missing_csv_returns_exit_code_1(self, tmp_path: Path) -> None:
        """A missing CSV file should produce exit code 1."""
        result = main(["--input", str(tmp_path / "nonexistent.csv")])
        assert result == 1

    def test_no_skips_returns_exit_code_0(self, tmp_path: Path) -> None:
        """All rows valid → exit code 0."""
        csv_path = self._write_csv(tmp_path)
        output = tmp_path / "downloads.txt"
        result = main(
            ["--input", str(csv_path), "--output", str(output), "--verbosity", "0"]
        )
        assert result == 0

    def test_skipped_rows_return_exit_code_2(self, tmp_path: Path) -> None:
        """Any skipped rows → exit code EXIT_SKIPPED (2)."""
        p = tmp_path / "bra_urls.csv"
        with p.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=["massif", "date", "heures", "url", "status"]
            )
            writer.writeheader()
            writer.writerow(
                {"massif": "", "date": "", "heures": "", "url": "", "status": ""}
            )
        output = tmp_path / "downloads.txt"
        result = main(["--input", str(p), "--output", str(output), "--verbosity", "0"])
        assert result == EXIT_SKIPPED
