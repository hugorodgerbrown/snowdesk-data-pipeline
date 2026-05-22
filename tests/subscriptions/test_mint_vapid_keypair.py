"""
tests/subscriptions/test_mint_vapid_keypair.py — Tests for the
mint_vapid_keypair management command.

Covers:
  - No args → no disk write, output contains a "would" marker.
  - --commit with no existing file → writes a single-line raw private
    scalar (NOT a PEM), prints both the 87-char public key and the
    43-char scalar.
  - --commit with an existing file → fails with CommandError.
  - Round-trip validation works for 100 consecutive generations — the
    earlier shape of this command randomly broke ~25% of the time because
    py_vapid 1.9's from_pem() decoder fails on certain DER bodies. The
    100-iteration assertion catches any future regression that re-introduces
    a probabilistic failure path.

Uses tmp_path + monkeypatching of the VAPID_PRIVATE_KEY_PATH env var to keep
generated secrets out of the repository and the test environment clean.
"""

from __future__ import annotations

import base64
import os
import re
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import py_vapid
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


def _call(tmp_secret: Path, *args: str, **options: object) -> tuple[str, str]:
    """Run mint_vapid_keypair with VAPID_PRIVATE_KEY_PATH pointing to tmp_secret.

    Returns (stdout, stderr) as strings.
    """
    stdout, stderr = StringIO(), StringIO()
    with patch.dict(os.environ, {"VAPID_PRIVATE_KEY_PATH": str(tmp_secret.name)}):
        # Patch _resolve_secret_path so we use the exact tmp_path, not
        # BASE_DIR / name (BASE_DIR points at the repo root in tests).
        with patch(
            "subscriptions.management.commands.mint_vapid_keypair._resolve_secret_path",
            return_value=tmp_secret,
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
        secret = tmp_path / ".vapid-test.key"
        stdout, _stderr = _call(secret)
        assert "would" in stdout.lower() or "dry run" in stdout.lower()

    def test_no_args_does_not_write_secret(self, tmp_path: Path) -> None:
        """Without --commit, no secret file is written."""
        secret = tmp_path / ".vapid-test.key"
        _call(secret)
        assert not secret.exists()


@pytest.mark.django_db
class TestMintVapidKeypairCommit:
    """Tests for --commit mode."""

    def test_commit_writes_raw_scalar_not_pem(self, tmp_path: Path) -> None:
        """--commit writes a single-line raw scalar, NOT a PEM block.

        The secret-file content is what dispatch_push will hand to pywebpush,
        and that string must take the bug-free Vapid.from_string() path.
        A PEM here would be a regression.
        """
        secret = tmp_path / ".vapid-test.key"
        _call(secret, commit=True)
        assert secret.exists()
        content = secret.read_text()
        assert "-----BEGIN" not in content, (
            "Secret file must be a raw scalar, not a PEM — see the command "
            "docstring for why."
        )
        # 32-byte scalar in URL-safe-base64 without padding = 43 chars.
        # The file may carry a trailing newline depending on text-editor
        # conventions; strip before length-checking.
        stripped = content.strip()
        assert len(stripped) == 43, f"expected 43 chars, got {len(stripped)}"  # noqa: PLR2004
        # And it must round-trip through standard URL-safe base64.
        padded = stripped + "=" * ((4 - len(stripped) % 4) % 4)
        raw = base64.urlsafe_b64decode(padded)
        assert len(raw) == 32  # noqa: PLR2004

    def test_commit_prints_public_key_and_scalar(self, tmp_path: Path) -> None:
        """--commit output includes both a 87-char public and a 43-char scalar."""
        secret = tmp_path / ".vapid-test.key"
        stdout, _stderr = _call(secret, commit=True)

        pub_match = re.search(r"VAPID_PUBLIC_KEY=([A-Za-z0-9_\-]+)", stdout)
        assert pub_match is not None, f"No VAPID_PUBLIC_KEY in output:\n{stdout}"
        assert len(pub_match.group(1)) == 87  # noqa: PLR2004

        # The scalar appears in the "Secret-file contents" block. Its value
        # must equal what was written to disk.
        scalar_on_disk = secret.read_text().strip()
        assert scalar_on_disk in stdout, (
            f"Scalar from disk ({scalar_on_disk!r}) not echoed in output."
        )

    def test_commit_secret_loads_via_vapid_from_string(self, tmp_path: Path) -> None:
        """The secret we write must load via py_vapid.Vapid.from_string().

        This is the same code path push_service.py runs at dispatch time.
        If this assertion ever fires it means we've broken production push.
        """
        secret = tmp_path / ".vapid-test.key"
        _call(secret, commit=True)
        scalar = secret.read_text().strip()
        # Must not raise.
        py_vapid.Vapid.from_string(scalar)

    def test_commit_public_key_matches_secret(self, tmp_path: Path) -> None:
        """Public key in output must match the public derived from the secret.

        Catches the SNOW-228 production bug where a mismatched (public, private)
        pair could be deployed without anyone noticing until push services
        rejected the JWT.
        """
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            PublicFormat,
        )

        secret = tmp_path / ".vapid-test.key"
        stdout, _stderr = _call(secret, commit=True)

        pub_match = re.search(r"VAPID_PUBLIC_KEY=([A-Za-z0-9_\-]+)", stdout)
        assert pub_match is not None
        printed_pub = pub_match.group(1)

        vapid = py_vapid.Vapid.from_string(secret.read_text().strip())
        derived_pub_bytes = vapid.public_key.public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint
        )
        derived_pub = base64.urlsafe_b64encode(derived_pub_bytes).rstrip(b"=").decode()
        assert printed_pub == derived_pub

    def test_commit_round_trip_works_100_iterations(self, tmp_path: Path) -> None:
        """100 consecutive generations must all produce a usable secret.

        The earlier PEM-based shape of this command had a ~25% failure rate
        because py_vapid 1.9's from_pem() mishandles DER bodies containing
        '+' or '/'. The raw-scalar shape uses from_string() and is bug-free.
        Running 100 iterations gives a near-zero false-pass probability if a
        regression ever re-introduces the random-failure path.
        """
        for i in range(100):
            secret = tmp_path / f".vapid-iter-{i}.key"
            _call(secret, commit=True)
            scalar = secret.read_text().strip()
            # Will raise on bad input; the command's own validate_secret
            # already runs this check, so reaching this line is the proof.
            py_vapid.Vapid.from_string(scalar)

    def test_commit_existing_file_raises_command_error(self, tmp_path: Path) -> None:
        """--commit fails with CommandError when the secret already exists."""
        secret = tmp_path / ".vapid-test.key"
        secret.write_text("existing content")
        with pytest.raises(CommandError) as exc_info:
            _call(secret, commit=True)
        assert "already exists" in str(exc_info.value).lower()

    def test_commit_does_not_overwrite_existing_file(self, tmp_path: Path) -> None:
        """--commit with existing secret leaves the original file untouched."""
        secret = tmp_path / ".vapid-test.key"
        original = "do not overwrite me"
        secret.write_text(original)
        with pytest.raises(CommandError):
            _call(secret, commit=True)
        assert secret.read_text() == original
