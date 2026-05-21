# test_streaming_resume.py — Tests for incremental NDJSON write and
# resume-on-restart behaviour in mf_bra_to_caaml.run().
#
# The parser must write each bulletin to disk as soon as it is produced
# (so an interrupted run leaves a valid partial NDJSON file behind) and
# must skip PDFs whose envelopes are already present in the output file
# on the next invocation, making the pipeline idempotent.
"""Tests for the streaming write + resume contract of mf_bra_to_caaml.run."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[3] / "scripts" / "meteofrance-archive"))

from mf_bra_to_caaml import (  # noqa: E402
    _envelope_source_file,
    load_completed_sources,
    run,
)


def _read_ndjson(path: Path) -> list[dict]:
    """Return the list of envelopes stored in an NDJSON file.

    Args:
        path: Path to an NDJSON file.

    Returns:
        List of parsed JSON envelopes, one per non-empty line.

    """
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _seed_two_pdfs(
    tmp_path: Path, chablais_pdf_path: Path, mont_blanc_pdf_path: Path
) -> Path:
    """Copy both sample PDFs into a fresh directory and return it.

    Args:
        tmp_path: pytest-provided per-test temp directory.
        chablais_pdf_path: Fixture path to the CHABLAIS PDF.
        mont_blanc_pdf_path: Fixture path to the MONT-BLANC PDF.

    Returns:
        Path to the directory containing both sample PDFs.

    """
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    shutil.copy(chablais_pdf_path, pdf_dir / chablais_pdf_path.name)
    shutil.copy(mont_blanc_pdf_path, pdf_dir / mont_blanc_pdf_path.name)
    return pdf_dir


class TestEnvelopeSourceFile:
    """Unit tests for the _envelope_source_file dedup-key helper."""

    def test_returns_source_file_when_present(self) -> None:
        """Pulls the source_file string out of the customData.MF block."""
        envelope = {"properties": {"customData": {"MF": {"source_file": "BRA.X.pdf"}}}}
        assert _envelope_source_file(envelope) == "BRA.X.pdf"

    def test_returns_none_when_missing(self) -> None:
        """Returns None if customData.MF.source_file is absent."""
        assert _envelope_source_file({"properties": {}}) is None

    def test_returns_none_when_properties_missing(self) -> None:
        """Returns None for a malformed envelope with no properties key."""
        assert _envelope_source_file({}) is None

    def test_returns_none_when_value_not_string(self) -> None:
        """Returns None if the source_file value isn't a string."""
        envelope = {"properties": {"customData": {"MF": {"source_file": 42}}}}
        assert _envelope_source_file(envelope) is None


class TestLoadCompletedSources:
    """Unit tests for the load_completed_sources resume primitive."""

    def test_missing_file_returns_empty_set(self, tmp_path: Path) -> None:
        """A non-existent output file yields no completed sources."""
        assert load_completed_sources(tmp_path / "absent.ndjson") == set()

    def test_reads_all_source_filenames(self, tmp_path: Path) -> None:
        """Each newline-terminated valid record contributes its source_file."""
        path = tmp_path / "bulletins.ndjson"
        env_a = {"properties": {"customData": {"MF": {"source_file": "A.pdf"}}}}
        env_b = {"properties": {"customData": {"MF": {"source_file": "B.pdf"}}}}
        path.write_text(json.dumps(env_a) + "\n" + json.dumps(env_b) + "\n")

        assert load_completed_sources(path) == {"A.pdf", "B.pdf"}

    def test_truncates_partial_trailing_line(self, tmp_path: Path) -> None:
        """A killed mid-write tail (no terminating newline) is truncated."""
        path = tmp_path / "bulletins.ndjson"
        env_a = {"properties": {"customData": {"MF": {"source_file": "A.pdf"}}}}
        good_line = json.dumps(env_a) + "\n"
        # Append a partial line — half-written JSON with no trailing newline.
        path.write_bytes(good_line.encode("utf-8") + b'{"partial')

        completed = load_completed_sources(path)

        assert completed == {"A.pdf"}
        # File on disk should be truncated back to the last clean line.
        assert path.read_bytes() == good_line.encode("utf-8")

    def test_truncates_malformed_full_line(self, tmp_path: Path) -> None:
        """A complete-but-malformed JSON line is also dropped."""
        path = tmp_path / "bulletins.ndjson"
        env_a = {"properties": {"customData": {"MF": {"source_file": "A.pdf"}}}}
        good_line = json.dumps(env_a) + "\n"
        bad_line = "{not valid json}\n"
        path.write_bytes(good_line.encode("utf-8") + bad_line.encode("utf-8"))

        completed = load_completed_sources(path)

        assert completed == {"A.pdf"}
        assert path.read_bytes() == good_line.encode("utf-8")

    def test_skips_envelopes_without_source_file(self, tmp_path: Path) -> None:
        """Records that don't carry a source_file are not counted."""
        path = tmp_path / "bulletins.ndjson"
        path.write_text(json.dumps({"properties": {}}) + "\n")

        assert load_completed_sources(path) == set()


class TestStreamingWrite:
    """Integration tests for incremental NDJSON streaming in run()."""

    def test_writes_each_envelope_immediately(
        self,
        tmp_path: Path,
        chablais_pdf_path: Path,
        mont_blanc_pdf_path: Path,
    ) -> None:
        """Output file is non-empty after every parsed PDF.

        We can't directly observe the per-PDF flush from a normal test, but
        we can confirm the post-run file contains both envelopes and that
        each line independently parses as valid JSON (which would not hold
        if writes were buffered into a single un-terminated blob).
        """
        pdf_dir = _seed_two_pdfs(tmp_path, chablais_pdf_path, mont_blanc_pdf_path)
        output = tmp_path / "bulletins.ndjson"

        counts = run(input_dir=pdf_dir, output_path=output, dry_run=False)

        assert counts == {"parsed": 2, "failed": 0, "skipped": 0}
        envelopes = _read_ndjson(output)
        assert len(envelopes) == 2
        sources = {_envelope_source_file(e) for e in envelopes}
        assert sources == {
            chablais_pdf_path.name,
            mont_blanc_pdf_path.name,
        }


class TestResume:
    """Integration tests for the resume contract."""

    def test_skips_already_parsed_pdfs(
        self,
        tmp_path: Path,
        chablais_pdf_path: Path,
        mont_blanc_pdf_path: Path,
    ) -> None:
        """Second run with both PDFs already in output parses nothing new."""
        pdf_dir = _seed_two_pdfs(tmp_path, chablais_pdf_path, mont_blanc_pdf_path)
        output = tmp_path / "bulletins.ndjson"

        first = run(input_dir=pdf_dir, output_path=output, dry_run=False)
        before = output.read_bytes()
        second = run(input_dir=pdf_dir, output_path=output, dry_run=False)
        after = output.read_bytes()

        assert first == {"parsed": 2, "failed": 0, "skipped": 0}
        assert second == {"parsed": 0, "failed": 0, "skipped": 2}
        # File contents are byte-identical — true idempotency, not just
        # logically equivalent.
        assert before == after

    def test_resumes_from_partial_output(
        self,
        tmp_path: Path,
        chablais_pdf_path: Path,
        mont_blanc_pdf_path: Path,
    ) -> None:
        """One PDF already in output; the other still needs parsing.

        Mirrors the real interrupted-run case: stage 5 wrote the first
        envelope and was killed before writing the second.
        """
        pdf_dir = _seed_two_pdfs(tmp_path, chablais_pdf_path, mont_blanc_pdf_path)
        output = tmp_path / "bulletins.ndjson"

        # Pre-seed the output with a CHABLAIS envelope as if a previous
        # run had completed only that file.
        first_only = tmp_path / "first_only"
        first_only.mkdir()
        shutil.copy(chablais_pdf_path, first_only / chablais_pdf_path.name)
        run(input_dir=first_only, output_path=output, dry_run=False)
        assert len(_read_ndjson(output)) == 1

        counts = run(input_dir=pdf_dir, output_path=output, dry_run=False)

        assert counts == {"parsed": 1, "failed": 0, "skipped": 1}
        envelopes = _read_ndjson(output)
        assert len(envelopes) == 2
        sources = {_envelope_source_file(e) for e in envelopes}
        assert sources == {chablais_pdf_path.name, mont_blanc_pdf_path.name}

    def test_recovers_from_partial_trailing_line(
        self,
        tmp_path: Path,
        chablais_pdf_path: Path,
        mont_blanc_pdf_path: Path,
    ) -> None:
        """A torn last line is truncated; missing PDFs are then parsed."""
        pdf_dir = _seed_two_pdfs(tmp_path, chablais_pdf_path, mont_blanc_pdf_path)
        output = tmp_path / "bulletins.ndjson"

        first_only = tmp_path / "first_only"
        first_only.mkdir()
        shutil.copy(chablais_pdf_path, first_only / chablais_pdf_path.name)
        run(input_dir=first_only, output_path=output, dry_run=False)

        # Append a partial / torn line, as if the writer was killed mid-flush.
        with output.open("ab") as fh:
            fh.write(b'{"properties": {"customData":')

        counts = run(input_dir=pdf_dir, output_path=output, dry_run=False)

        # CHABLAIS is already complete; MONT-BLANC gets parsed; the torn
        # tail is dropped and not double-counted.
        assert counts == {"parsed": 1, "failed": 0, "skipped": 1}
        envelopes = _read_ndjson(output)
        assert len(envelopes) == 2

    def test_no_resume_overwrites_existing_file(
        self,
        tmp_path: Path,
        chablais_pdf_path: Path,
        mont_blanc_pdf_path: Path,
    ) -> None:
        """resume=False truncates the output file before writing."""
        pdf_dir = _seed_two_pdfs(tmp_path, chablais_pdf_path, mont_blanc_pdf_path)
        output = tmp_path / "bulletins.ndjson"
        output.write_text("stale garbage from a previous run\n")

        counts = run(input_dir=pdf_dir, output_path=output, dry_run=False, resume=False)

        assert counts == {"parsed": 2, "failed": 0, "skipped": 0}
        envelopes = _read_ndjson(output)
        assert len(envelopes) == 2

    def test_dry_run_ignores_resume(
        self,
        tmp_path: Path,
        chablais_pdf_path: Path,
        mont_blanc_pdf_path: Path,
    ) -> None:
        """dry_run=True re-parses every PDF and never reads the output file."""
        pdf_dir = _seed_two_pdfs(tmp_path, chablais_pdf_path, mont_blanc_pdf_path)
        output = tmp_path / "bulletins.ndjson"
        output.write_text("this file should be untouched\n")

        counts = run(input_dir=pdf_dir, output_path=output, dry_run=True)

        assert counts == {"parsed": 2, "failed": 0, "skipped": 0}
        # Output file is untouched by dry-run.
        assert output.read_text() == "this file should be untouched\n"


class TestNoResumeCLI:
    """Integration test for the --no-resume CLI flag."""

    def test_no_resume_flag_wires_to_run(
        self,
        tmp_path: Path,
        chablais_pdf_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The --no-resume CLI flag flips resume=False on the run() call."""
        from mf_bra_to_caaml import main

        pdf_dir = tmp_path / "pdfs"
        pdf_dir.mkdir()
        shutil.copy(chablais_pdf_path, pdf_dir / chablais_pdf_path.name)
        output = tmp_path / "bulletins.ndjson"
        output.write_text("stale\n")

        exit_code = main(
            [
                "--input",
                str(pdf_dir),
                "--output",
                str(output),
                "--no-resume",
                "--verbosity",
                "0",
            ]
        )

        assert exit_code == 0
        assert "stale" not in output.read_text()
        assert len(_read_ndjson(output)) == 1
