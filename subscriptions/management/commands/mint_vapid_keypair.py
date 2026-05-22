"""
subscriptions/management/commands/mint_vapid_keypair.py — mint_vapid_keypair command.

Generates a VAPID P-256 keypair for Web Push and writes the private key to a
PEM file. Read-only by default; pass --commit to write to disk.

Usage:
    # Dry-run — shows what would be generated without touching disk.
    python manage.py mint_vapid_keypair

    # Generate a real keypair and write the PEM.
    python manage.py mint_vapid_keypair --commit

The output includes:
  - The VAPID_PUBLIC_KEY value (URL-safe-base64 uncompressed P-256 point).
  - The VAPID_CLAIM_EMAIL template for the Render environment tab.
  - The path where the PEM was (or would be) written.

Safety: the command refuses to overwrite an existing PEM file. If you need
to rotate the keypair, delete the PEM manually first (be aware that rotating
invalidates every live PushSubscription row — all devices must re-subscribe).
"""

import base64
import logging
import sys
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

import py_vapid
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)
from decouple import config
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)


def _resolve_pem_path() -> Path:
    """Return the absolute path where the VAPID PEM should be written.

    Reads VAPID_PRIVATE_KEY_PATH from the environment (default:
    ``.vapid-private.pem``), resolved relative to settings.BASE_DIR.
    """
    relative: str = config("VAPID_PRIVATE_KEY_PATH", default=".vapid-private.pem")
    return Path(settings.BASE_DIR) / relative


def _public_key_b64(vapid: py_vapid.Vapid) -> str:
    """Return the URL-safe-base64-encoded uncompressed P-256 public key.

    This is the value that goes into ``VAPID_PUBLIC_KEY`` on Render and into
    the ``<meta name="vapid-public-key">`` tag consumed by the browser's
    ``PushManager.subscribe()`` call.

    The key is the raw 65-byte uncompressed point (0x04 prefix + X + Y),
    encoded without padding per RFC 7515.
    """
    pub_key = vapid.public_key
    pub_bytes = pub_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode("ascii")


class Command(BaseCommand):
    """Generate a VAPID keypair for Web Push.

    Dry-run by default; pass --commit to write the PEM to disk.
    """

    help = (
        "Generate a VAPID P-256 keypair for Web Push. "
        "Dry-run by default; pass --commit to write the private-key PEM to disk. "
        "Refuses to overwrite an existing PEM — delete it manually to rotate."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Write the private-key PEM to disk. "
                "Without this flag the command is read-only and nothing is written."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the command."""
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]
        pem_path = _resolve_pem_path()

        if commit:
            self._run_commit(pem_path, verbosity)
        else:
            self._run_dry(pem_path, verbosity)

    def _run_dry(self, pem_path: Path, verbosity: int) -> None:
        """Print what would be done without touching disk."""
        self.stdout.write(
            self.style.WARNING(
                "Dry run — would generate a new VAPID keypair.\n"
                f"PEM would be written to: {pem_path}\n"
                "Pass --commit to generate and write the keypair."
            )
        )
        if verbosity >= 2:  # noqa: PLR2004
            self.stdout.write("(No files read or written in dry-run mode.)")

    def _run_commit(self, pem_path: Path, verbosity: int) -> None:
        """Generate the keypair and write the PEM, refusing to overwrite."""
        if pem_path.exists():
            self.stderr.write(
                self.style.ERROR(
                    f"PEM file already exists: {pem_path}\n"
                    "Delete it manually to rotate the keypair.\n"
                    "WARNING: rotating invalidates every live PushSubscription — "
                    "all subscribed devices must re-register."
                )
            )
            sys.exit(1)

        # Generate a fresh P-256 keypair via py_vapid.
        vapid = py_vapid.Vapid()
        vapid.generate_keys()

        # Write the private key PEM to disk.
        try:
            vapid.save_key(str(pem_path))
        except OSError as exc:
            raise CommandError(f"Failed to write PEM to {pem_path}: {exc}") from exc

        pub_b64 = _public_key_b64(vapid)

        self.stdout.write(self.style.SUCCESS(f"VAPID keypair written to: {pem_path}"))
        self.stdout.write("")
        self.stdout.write(
            "Add these to your Render environment (Settings → Environment):"
        )
        self.stdout.write("")
        self.stdout.write(f"  VAPID_PUBLIC_KEY={pub_b64}")
        self.stdout.write("  VAPID_CLAIM_EMAIL=mailto:ops@yourdomain.com")
        self.stdout.write(f"  VAPID_PRIVATE_KEY_PATH={pem_path.name}")
        self.stdout.write("")
        self.stdout.write(
            "Upload the PEM as a Render secret file at the path above "
            "(Dashboard → Secret Files → Add Secret File)."
        )
        if verbosity >= 2:  # noqa: PLR2004
            self.stdout.write(f"\nPublic key (87 chars): {pub_b64}")
