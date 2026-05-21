# test_coverage_report.py — Tests for the field-coverage report (Step 7).
"""Tests for generate_coverage_report and the --dry-run flow in mf_bra_to_caaml."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3] / "scripts" / "meteofrance-archive"))

from mf_bra_to_caaml import (  # noqa: E402
    COVERAGE_CHECKS,
    _check_field,
    generate_coverage_report,
    main,
    parse_pdf,
    run,
)


class TestCheckField:
    """Unit tests for _check_field path resolution."""

    def _env(self, props: dict) -> dict:
        """Return a minimal Feature envelope with given properties."""
        return {"type": "Feature", "geometry": None, "properties": props}

    def test_simple_key_found(self) -> None:
        """A top-level properties key returns True when non-empty."""
        assert _check_field(self._env({"highlights": "hello"}), "highlights") is True

    def test_simple_key_empty_string(self) -> None:
        """An empty string should return False."""
        assert _check_field(self._env({"highlights": ""}), "highlights") is False

    def test_simple_key_empty_list(self) -> None:
        """An empty list should return False."""
        assert _check_field(self._env({"dangerRatings": []}), "dangerRatings") is False

    def test_nested_dot_path(self) -> None:
        """Dot-notation path should resolve nested dict."""
        env = self._env({"weather": {"comment": "sunny"}})
        assert _check_field(env, "weather.comment") is True

    def test_index_path(self) -> None:
        """[0] indexing should resolve into lists."""
        env = self._env({"dangerRatings": [{"mainValue": "low"}]})
        assert _check_field(env, "dangerRatings[0].mainValue") is True

    def test_index_out_of_range(self) -> None:
        """Accessing index beyond list length should return False."""
        env = self._env({"dangerRatings": []})
        assert _check_field(env, "dangerRatings[0].mainValue") is False

    def test_missing_key_returns_false(self) -> None:
        """A completely missing key should return False."""
        assert _check_field(self._env({}), "highlights") is False


class TestGenerateCoverageReport:
    """Unit tests for generate_coverage_report."""

    def test_empty_list_returns_no_bulletins_message(self) -> None:
        """An empty envelope list should produce a 'no bulletins' message."""
        report = generate_coverage_report([])
        assert "No bulletins" in report

    def test_report_contains_all_check_labels(self, chablais_pdf_path: Path) -> None:
        """Every coverage check label must appear in the report."""
        import pdfplumber

        with pdfplumber.open(chablais_pdf_path) as pdf:
            _ = pdf.pages[0]  # ensure the PDF opens correctly

        result = parse_pdf(chablais_pdf_path)
        assert result is not None
        report = generate_coverage_report([result])
        for _, label in COVERAGE_CHECKS:
            assert label in report, f"Label missing from report: {label}"

    def test_report_header_shows_bulletin_count(self, chablais_pdf_path: Path) -> None:
        """The report header should state the number of bulletins."""
        result = parse_pdf(chablais_pdf_path)
        assert result is not None
        report = generate_coverage_report([result])
        assert "1 bulletin" in report

    def test_report_shows_100_percent_for_present_field(
        self, chablais_pdf_path: Path
    ) -> None:
        """A field present in all envelopes should show 100%."""
        result = parse_pdf(chablais_pdf_path)
        assert result is not None
        report = generate_coverage_report([result])
        # highlights is always present for CHABLAIS
        assert "100%" in report

    def test_bar_chart_format(self, chablais_pdf_path: Path) -> None:
        """Each line should contain a progress bar with '[' and ']'."""
        result = parse_pdf(chablais_pdf_path)
        assert result is not None
        report = generate_coverage_report([result])
        lines = [ln for ln in report.splitlines() if "[" in ln and "]" in ln]
        assert len(lines) == len(COVERAGE_CHECKS)


class TestDryRunFlow:
    """Integration tests for run() --dry-run mode."""

    def test_dry_run_does_not_write_output(
        self, tmp_path: Path, chablais_pdf_path: Path
    ) -> None:
        """dry_run=True must not write an output file."""
        import shutil

        pdf_dir = tmp_path / "pdfs"
        pdf_dir.mkdir()
        shutil.copy(chablais_pdf_path, pdf_dir / chablais_pdf_path.name)
        output = tmp_path / "bulletins.ndjson"
        run(input_dir=pdf_dir, output_path=output, dry_run=True, verbosity=0)
        assert not output.exists()

    def test_dry_run_returns_counts(
        self, tmp_path: Path, chablais_pdf_path: Path
    ) -> None:
        """dry_run=True should still return the parsed/failed counts."""
        import shutil

        pdf_dir = tmp_path / "pdfs"
        pdf_dir.mkdir()
        shutil.copy(chablais_pdf_path, pdf_dir / chablais_pdf_path.name)
        output = tmp_path / "bulletins.ndjson"
        counts = run(input_dir=pdf_dir, output_path=output, dry_run=True, verbosity=0)
        assert counts["parsed"] == 1
        assert counts["failed"] == 0

    def test_commit_mode_writes_ndjson(
        self, tmp_path: Path, chablais_pdf_path: Path
    ) -> None:
        """dry_run=False should write NDJSON to output_path."""
        import json
        import shutil

        pdf_dir = tmp_path / "pdfs"
        pdf_dir.mkdir()
        shutil.copy(chablais_pdf_path, pdf_dir / chablais_pdf_path.name)
        output = tmp_path / "bulletins.ndjson"
        run(input_dir=pdf_dir, output_path=output, dry_run=False, verbosity=0)
        assert output.exists()
        lines = output.read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["type"] == "Feature"

    def test_empty_directory_returns_zero_counts(self, tmp_path: Path) -> None:
        """An input directory with no PDFs should return 0/0 counts."""
        output = tmp_path / "bulletins.ndjson"
        counts = run(input_dir=tmp_path, output_path=output, dry_run=True, verbosity=0)
        assert counts == {"parsed": 0, "failed": 0}


class TestMainEntryPoint:
    """Integration tests for main() CLI entry point."""

    def test_missing_input_dir_returns_1(self, tmp_path: Path) -> None:
        """A non-existent input directory should produce exit code 1."""
        result = main(["--input", str(tmp_path / "nonexistent")])
        assert result == 1

    def test_dry_run_succeeds(self, tmp_path: Path, chablais_pdf_path: Path) -> None:
        """--dry-run with a valid PDF should return exit code 0."""
        import shutil

        pdf_dir = tmp_path / "pdfs"
        pdf_dir.mkdir()
        shutil.copy(chablais_pdf_path, pdf_dir / chablais_pdf_path.name)
        result = main(
            [
                "--input",
                str(pdf_dir),
                "--output",
                str(tmp_path / "out.ndjson"),
                "--dry-run",
            ]
        )
        assert result == 0
