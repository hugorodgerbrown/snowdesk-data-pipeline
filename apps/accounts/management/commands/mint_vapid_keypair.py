"""
apps/accounts/management/commands/mint_vapid_keypair.py — mint_vapid_keypair command.

Generates a VAPID P-256 keypair for Web Push and writes the **raw private
scalar** (single-line URL-safe-base64) to a secret file. Read-only by default;
pass --commit to write to disk.

Why the raw scalar and not a PEM
--------------------------------
py_vapid 1.9's ``Vapid.from_pem()`` decodes the PEM body via a URL-safe-base64
decoder (``b64urldecode``) instead of the standard base64 decoder PEMs actually
use. Whenever the DER body happens to contain a ``+`` or ``/`` character
(roughly 25% of generated keys, due to bit alignment), parsing fails with an
ASN.1 error.

py_vapid's ``Vapid.from_string()`` path is bug-free: it expects the raw 32-byte
private scalar encoded as URL-safe-base64 (no headers, no newlines). pywebpush
delegates to that same path when handed a single-line string. We canonicalise
on that format throughout: the management command writes it, push_config.py
reads it, dispatch_push passes it straight through.

Usage:
    # Dry-run — shows what would be generated without touching disk.
    python manage.py mint_vapid_keypair

    # Generate a real keypair and write the secret file.
    python manage.py mint_vapid_keypair --commit

The output includes:
  - The VAPID_PUBLIC_KEY value (URL-safe-base64 uncompressed P-256 point).
  - The raw private scalar (URL-safe-base64), to be written to the secret file.
  - The PEM rendering for human inspection only (NOT for upload).
  - The VAPID_CLAIM_EMAIL template for the Render environment tab.

Safety: the command refuses to overwrite an existing secret file. If you
need to rotate the keypair, delete the file manually first (rotating
invalidates every live PushSubscription row — all devices must re-subscribe).
"""

import base64
import logging
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

import py_vapid
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from decouple import config
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)


def _resolve_secret_path() -> Path:
    """Return the absolute path where the VAPID secret should be written.

    Reads ``VAPID_PRIVATE_KEY_PATH`` from the environment (default:
    ``.vapid-private.key``), resolved relative to ``settings.BASE_DIR``.

    The file extension is ``.key`` rather than ``.pem`` because the file
    content is the raw private scalar (single-line URL-safe-base64), not a
    PEM-framed key. The earlier spike used ``.vapid-private.pem`` — operators
    migrating from that should rename the secret file *and* update
    ``VAPID_PRIVATE_KEY_PATH`` on Render to match.
    """
    relative: str = config("VAPID_PRIVATE_KEY_PATH", default=".vapid-private.key")
    return Path(settings.BASE_DIR) / relative


def _encode_b64url(raw: bytes) -> str:
    """URL-safe-base64 encode ``raw`` and strip ``=`` padding (RFC 7515)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _public_key_b64(private_key: ec.EllipticCurvePrivateKey) -> str:
    """Return the URL-safe-base64-encoded uncompressed P-256 public key.

    The 65-byte uncompressed point (``0x04`` prefix + X + Y) is what goes
    into ``VAPID_PUBLIC_KEY`` on Render and into the browser-side meta tag.
    """
    pub_bytes = private_key.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint
    )
    return _encode_b64url(pub_bytes)


def _private_scalar_b64(private_key: ec.EllipticCurvePrivateKey) -> str:
    """Return the URL-safe-base64-encoded 32-byte private scalar.

    This is the format py_vapid's ``Vapid.from_string()`` accepts and the
    format we write to the secret file.
    """
    scalar = private_key.private_numbers().private_value.to_bytes(32, "big")
    return _encode_b64url(scalar)


def _private_key_pem(private_key: ec.EllipticCurvePrivateKey) -> str:
    """Return the PKCS8 PEM for human inspection only.

    DO NOT upload this to the secret file — py_vapid 1.9's ``from_pem()``
    parser is broken for roughly 25% of generated keys (see module docstring).
    """
    pem_bytes = private_key.private_bytes(
        Encoding.PEM,
        PrivateFormat.PKCS8,
        NoEncryption(),
    )
    return pem_bytes.decode("ascii")


class Command(BaseCommand):
    """Generate a VAPID keypair for Web Push.

    Dry-run by default; pass --commit to write the raw private scalar to
    the secret file at ``VAPID_PRIVATE_KEY_PATH``.

    SNOW-602 exempt: no queryset loop — a one-shot keypair generation.
    """

    help = (
        "Generate a VAPID P-256 keypair for Web Push. "
        "Dry-run by default; pass --commit to write the secret file. "
        "Refuses to overwrite an existing file — delete it manually to rotate."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Write the raw private scalar to the secret file. "
                "Without this flag the command is read-only and nothing is written."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the command."""
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]
        secret_path = _resolve_secret_path()

        if commit:
            self._run_commit(secret_path, verbosity)
        else:
            self._run_dry(secret_path, verbosity)

    def _run_dry(self, secret_path: Path, verbosity: int) -> None:
        """Print what would be done without touching disk."""
        self.stdout.write(
            self.style.WARNING(
                "Dry run — would generate a new VAPID keypair.\n"
                f"Secret would be written to: {secret_path}\n"
                "Pass --commit to generate and write the keypair."
            )
        )
        if verbosity >= 2:  # noqa: PLR2004
            self.stdout.write("(No files read or written in dry-run mode.)")

    def _run_commit(self, secret_path: Path, verbosity: int) -> None:
        """Generate the keypair and write the secret file, refusing to overwrite."""
        if secret_path.exists():
            raise CommandError(
                f"Secret file already exists: {secret_path}\n"
                "Delete it manually to rotate the keypair.\n"
                "WARNING: rotating invalidates every live PushSubscription — "
                "all subscribed devices must re-register."
            )

        # Generate a fresh P-256 keypair via cryptography directly. We avoid
        # py_vapid.Vapid().generate_keys() so the keypair is decoupled from
        # py_vapid's representation — it's just an EC private key.
        private_key = ec.generate_private_key(ec.SECP256R1())

        pub_b64 = _public_key_b64(private_key)
        scalar_b64 = _private_scalar_b64(private_key)
        pem = _private_key_pem(private_key)

        # Write the raw scalar (single line, no trailing newline) to the
        # secret file. This is the format py_vapid.Vapid.from_string()
        # accepts — the bug-free path. See module docstring.
        try:
            secret_path.write_text(scalar_b64)
        except OSError as exc:
            raise CommandError(
                f"Failed to write secret to {secret_path}: {exc}"
            ) from exc

        # Validate the round-trip end-to-end before declaring success: load
        # the file we just wrote via the exact code path push_service.py
        # will use, derive the public key from it, and confirm it matches
        # the public key we just printed. If this assertion ever fires it
        # means we've shipped a regression that would silently break push
        # delivery in production.
        self._validate_secret(secret_path, pub_b64)

        self.stdout.write(self.style.SUCCESS(f"VAPID secret written to: {secret_path}"))
        self.stdout.write("")
        self.stdout.write(
            "Add these to your Render environment (Settings → Environment):"
        )
        self.stdout.write("")
        self.stdout.write(f"  VAPID_PUBLIC_KEY={pub_b64}")
        self.stdout.write("  VAPID_CLAIM_EMAIL=mailto:ops@yourdomain.com")
        self.stdout.write(f"  VAPID_PRIVATE_KEY_PATH={secret_path.name}")
        self.stdout.write("")
        self.stdout.write(
            "Upload the secret file on Render (Dashboard → Secret Files → "
            "Add Secret File). Filename must match VAPID_PRIVATE_KEY_PATH; "
            "contents are the single-line raw scalar shown below."
        )
        self.stdout.write("")
        self.stdout.write(self.style.WARNING("Secret-file contents (raw scalar):"))
        self.stdout.write(f"  {scalar_b64}")
        if verbosity >= 2:  # noqa: PLR2004
            self.stdout.write("")
            self.stdout.write(
                "PEM (for human inspection only — DO NOT upload as the secret):"
            )
            self.stdout.write(pem)

    def _validate_secret(self, secret_path: Path, expected_pub_b64: str) -> None:
        """Load the secret via py_vapid and confirm the derived public matches.

        This is the same code path push_service.py runs at dispatch time, so a
        success here proves the file we just wrote will work in production.
        Raises CommandError on any mismatch so a regression cannot be shipped
        silently.
        """
        try:
            scalar = secret_path.read_text().strip()
            vapid = py_vapid.Vapid.from_string(scalar)
            derived_pub_b64 = _public_key_b64(vapid.private_key)
        except Exception as exc:  # noqa: BLE001 — re-raised as CommandError
            raise CommandError(
                f"Round-trip validation failed: could not load the secret "
                f"file via py_vapid.Vapid.from_string(): {exc}\n"
                "This is a bug in mint_vapid_keypair; please report it."
            ) from exc

        if derived_pub_b64 != expected_pub_b64:
            raise CommandError(
                "Round-trip validation failed: the public key derived from "
                "the secret file does not match the public key just printed.\n"
                f"  printed:    {expected_pub_b64}\n"
                f"  re-derived: {derived_pub_b64}\n"
                "This is a bug in mint_vapid_keypair; please report it."
            )
