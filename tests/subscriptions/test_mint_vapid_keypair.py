"""
tests/subscriptions/test_mint_vapid_keypair.py — Tests for the
mint_vapid_keypair management command.

Covers:
  - No args → no disk write, output contains a "would" marker.
  - --commit with no existing PEM → writes a valid PEM, prints public key.
  - --commit with an existing PEM → exits non-zero with a clear message.

Uses tmp_path + monkeypatching of the VAPID_PRIVATE_KEY_PATH env var to keep
generated PEMs out of the repository and the test environment clean.
"""

from __future__ import annotations

import os
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management import call_command


def _call(tmp_pem: Path, *args: str, **options: object) -> tuple[str, str]:
    """Run mint_vapid_keypair with VAPID_PRIVATE_KEY_PATH pointing to tmp_pem.

    Returns (stdout, stderr) as strings.
    """
    stdout, stderr = StringIO(), StringIO()
    with patch.dict(os.environ, {"VAPID_PRIVATE_KEY_PATH": str(tmp_pem.name)}):
        # Patch _resolve_pem_path so we use the exact tmp_path, not BASE_DIR / name.
        with patch(
            "subscriptions.management.commands.mint_vapid_keypair._resolve_pem_path",
            return_value=tmp_pem,
        ):
            call_command(
                "mint_vapid_keypair",
                *args,
                stdout=stdout,
                stderr=stderr,
                **options,
            )
    return stdout.getvalue(), stderr.getvalue()


class TestMintVapidKeypairDryRun:
    """Tests for the default (no --commit) dry-run mode."""

    def test_no_args_prints_would_generate(self, tmp_path: Path) -> None:
        """Without --commit, output contains a 'would' marker (dry-run)."""
        pem_path = tmp_path / ".vapid-test.pem"
        stdout, _stderr = _call(pem_path)
        assert "would" in stdout.lower() or "dry run" in stdout.lower()

    def test_no_args_does_not_write_pem(self, tmp_path: Path) -> None:
        """Without --commit, no PEM file is written."""
        pem_path = tmp_path / ".vapid-test.pem"
        _call(pem_path)
        assert not pem_path.exists()


@pytest.mark.django_db
class TestMintVapidKeypairCommit:
    """Tests for --commit mode."""

    def test_commit_writes_valid_pem(self, tmp_path: Path) -> None:
        """--commit writes a PEM file that starts with '-----BEGIN'."""
        pem_path = tmp_path / ".vapid-test.pem"
        _call(pem_path, commit=True)
        assert pem_path.exists()
        content = pem_path.read_text()
        assert "-----BEGIN" in content

    def test_commit_prints_public_key(self, tmp_path: Path) -> None:
        """--commit output includes a VAPID_PUBLIC_KEY line with an 87-char key."""
        pem_path = tmp_path / ".vapid-test.pem"
        stdout, _stderr = _call(pem_path, commit=True)
        # The public key is 87 URL-safe-base64 characters (uncompressed P-256).
        import re

        match = re.search(r"VAPID_PUBLIC_KEY=([A-Za-z0-9_\-]{80,})", stdout)
        assert match is not None, f"No VAPID_PUBLIC_KEY found in output:\n{stdout}"
        pub_key = match.group(1)
        assert len(pub_key) == 87  # noqa: PLR2004 — 65 bytes base64url without padding

    def test_commit_existing_pem_exits_nonzero(self, tmp_path: Path) -> None:
        """--commit fails with SystemExit when the PEM already exists."""
        pem_path = tmp_path / ".vapid-test.pem"
        pem_path.write_text("existing content")
        with pytest.raises(SystemExit) as exc_info:
            _call(pem_path, commit=True)
        assert exc_info.value.code != 0

    def test_commit_existing_pem_prints_clear_message(self, tmp_path: Path) -> None:
        """--commit with existing PEM prints a message mentioning the file path."""
        pem_path = tmp_path / ".vapid-test.pem"
        pem_path.write_text("existing content")
        stdout, stderr = StringIO(), StringIO()
        with patch(
            "subscriptions.management.commands.mint_vapid_keypair._resolve_pem_path",
            return_value=pem_path,
        ):
            try:
                call_command(
                    "mint_vapid_keypair",
                    commit=True,
                    stdout=stdout,
                    stderr=stderr,
                )
            except SystemExit:
                pass
        combined = stdout.getvalue() + stderr.getvalue()
        # The error message must mention the file path (or at least "already exists").
        assert "already exists" in combined or str(pem_path) in combined

    def test_commit_does_not_overwrite_existing_pem(self, tmp_path: Path) -> None:
        """--commit with existing PEM leaves the original file untouched."""
        pem_path = tmp_path / ".vapid-test.pem"
        original = "do not overwrite me"
        pem_path.write_text(original)
        with pytest.raises(SystemExit):
            _call(pem_path, commit=True)
        assert pem_path.read_text() == original
