"""
subscriptions/aaguids.py — AAGUID to authenticator provider name mapping.

AAGUID (Authenticator Attestation GUID) is a UUID embedded in every
WebAuthn registration response that identifies the authenticator make and
model.  This module maps well-known AAGUIDs to human-readable provider
names.

Apple platform authenticators (iCloud Keychain, Touch ID, Face ID) report
the all-zeros AAGUID by design for privacy.  These are already normalised to
``None`` by ``_parse_aaguid`` in the passkey service, so no entry is needed
here; call-sites handle ``None`` as "platform passkey (provider unknown)".

To extend this list, cross-reference:
    https://github.com/passkeydeveloper/passkey-authenticator-aaguids
"""

from __future__ import annotations

import uuid

# Map from lower-case UUID string to the provider's display name.
_KNOWN: dict[str, str] = {
    "bada5566-a7aa-401f-bd96-45619a55120d": "1Password",
    "ea9b8d66-4d01-1d21-3ce4-b6b48cb575d4": "Google Password Manager",
    "d548826e-79b4-db40-a3d8-11116f7e8349": "Bitwarden",
    "d6d0bdce-698b-a9ab-4b11-a3231d159d0d": "Dashlane",
}


def lookup(aaguid: uuid.UUID | None) -> str | None:
    """Return a provider display name for the given AAGUID, or None if unknown.

    Args:
        aaguid: A UUID parsed from the authenticator's AAGUID field, or None
            when the authenticator reported the all-zeros placeholder.

    Returns:
        Provider name string, or None when the AAGUID is not recognised.

    """
    if aaguid is None:
        return None
    return _KNOWN.get(str(aaguid).lower())
