"""
subscriptions/push_config.py — Spike-only VAPID keypair for Web Push.

Reads the dev VAPID keypair from .env via python-decouple:

  - ``VAPID_PUBLIC_KEY``       URL-safe-base64 uncompressed P-256 point (87 chars)
  - ``VAPID_PRIVATE_KEY_PATH`` path to a PEM file (gitignored)
  - ``VAPID_CLAIM_EMAIL``      mailto: URI used as the JWT ``sub`` claim

The PEM lives in a gitignored file rather than as a string env var so
multi-line content stays readable and gitleaks never sees it.
"""

from __future__ import annotations

from pathlib import Path

from decouple import config
from django.conf import settings

VAPID_PUBLIC_KEY: str = config("VAPID_PUBLIC_KEY", default="")
VAPID_CLAIM_EMAIL: str = config("VAPID_CLAIM_EMAIL", default="mailto:noreply@localhost")

_pem_path = Path(settings.BASE_DIR) / config(
    "VAPID_PRIVATE_KEY_PATH", default=".vapid-private.pem"
)

VAPID_PRIVATE_KEY: str = _pem_path.read_text() if _pem_path.exists() else ""
