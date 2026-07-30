"""
apps/accounts/push_config.py — VAPID configuration for Web Push.

Reads the VAPID keypair from the environment:

  - ``VAPID_PUBLIC_KEY``       URL-safe-base64 uncompressed P-256 point
                               (87 chars). Sent to the browser at subscribe
                               time so the push service can verify the JWT.
  - ``VAPID_PRIVATE_KEY_PATH`` Path (relative to ``settings.BASE_DIR``) to
                               the secret file containing the raw 32-byte
                               private scalar as a single-line URL-safe
                               base64 string (default ``.vapid-private.key``).
                               This is the format ``py_vapid.Vapid.from_string()``
                               accepts — see ``mint_vapid_keypair`` for why
                               we use the raw scalar rather than a PEM.
  - ``VAPID_CLAIM_EMAIL``      ``mailto:`` URI used as the JWT ``sub`` claim,
                               telling push services who to contact about
                               delivery issues.

The secret file lives outside the working tree (gitignored) so it never
ends up in source control.
"""

from __future__ import annotations

from pathlib import Path

from decouple import config
from django.conf import settings

VAPID_PUBLIC_KEY: str = config("VAPID_PUBLIC_KEY", default="")
VAPID_CLAIM_EMAIL: str = config("VAPID_CLAIM_EMAIL", default="mailto:noreply@localhost")

_secret_path = Path(settings.BASE_DIR) / config(
    "VAPID_PRIVATE_KEY_PATH", default=".vapid-private.key"
)

# Strip surrounding whitespace — Render's secret-file editor and many text
# editors append a trailing newline, which py_vapid.Vapid.from_string() does
# not tolerate.
VAPID_PRIVATE_KEY: str = (
    _secret_path.read_text().strip() if _secret_path.exists() else ""
)
