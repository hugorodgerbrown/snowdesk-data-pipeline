"""
subscriptions/fields.py — Custom field for deterministic at-rest encryption.

Provides AES-SIV-based deterministic encryption for email addresses stored in the
database.  With no associated data, AES-SIV (RFC 5297) produces identical ciphertext
for identical plaintext, so ``unique=True`` constraints, equality lookups, and
``db_index`` all work directly on the encrypted column with no second blind-index.

Public API
----------
``encrypt_value(plaintext)``   — encrypt a string, return a base64-encoded token.
``decrypt_value(token)``       — decrypt a base64-encoded token back to plaintext.
``generate_encryption_key()``  — generate a new 64-byte AES-256-SIV key as a
                                  base64-encoded string, for use in ``.env`` / ops.
``EncryptedEmailField``        — a ``models.EmailField`` subclass that transparently
                                  encrypts on write and decrypts on read.

Settings
--------
``FIELD_ENCRYPTION_KEY`` — base64url-encoded 64-byte key (512-bit AES-SIV).
No default; fail-closed (Django will not start without it).

Deploy ordering
---------------
Run ``makemigrations`` → ``migrate`` first (the migration is a metadata-only
``AlterField``; existing plaintext rows are left in place and decoded by the
``from_db_value`` fallback).  Then run ``encrypt_subscriber_email --commit``
to backfill ciphertext for all existing rows.
"""

import base64
import binascii
import logging

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESSIV
from django.conf import settings
from django.db import models

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level cipher cache
# ---------------------------------------------------------------------------

_cipher_instance: AESSIV | None = None


def _cipher() -> AESSIV:
    """Return a cached AESSIV instance, building it on first call.

    Reads ``settings.FIELD_ENCRYPTION_KEY`` (a base64-encoded 64-byte key)
    at call time — not at module import — so Django's settings machinery is
    fully initialised before the key is accessed.

    Returns:
        A cached ``AESSIV`` cipher instance.

    Raises:
        ImproperlyConfigured: if the key is missing or not valid base64.

    """
    global _cipher_instance
    if _cipher_instance is None:
        raw_key = base64.b64decode(settings.FIELD_ENCRYPTION_KEY)
        _cipher_instance = AESSIV(raw_key)
    return _cipher_instance


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def encrypt_value(plaintext: str) -> str:
    """Encrypt a plaintext string using AES-256-SIV and return a base64 token.

    Encryption is deterministic (same plaintext → same ciphertext) because no
    associated data is passed.  The ciphertext is base64-encoded so it can be
    stored in a text column.

    Args:
        plaintext: The string value to encrypt.

    Returns:
        A base64-encoded ciphertext string (~360 characters for a typical email).

    """
    ciphertext = _cipher().encrypt(plaintext.encode(), None)
    return base64.b64encode(ciphertext).decode()


def decrypt_value(token: str) -> str:
    """Decrypt a base64-encoded AES-SIV token back to plaintext.

    Args:
        token: A base64-encoded ciphertext string as produced by ``encrypt_value``.

    Returns:
        The original plaintext string.

    Raises:
        cryptography.exceptions.InvalidTag: if the ciphertext has been tampered
            with or was encrypted under a different key.
        binascii.Error: if ``token`` is not valid base64.

    """
    ciphertext = base64.b64decode(token)
    return _cipher().decrypt(ciphertext, None).decode()


def generate_encryption_key() -> str:
    """Generate a new AES-256-SIV key suitable for use as ``FIELD_ENCRYPTION_KEY``.

    Returns:
        A base64-encoded 64-byte (512-bit) key string.

    Example::

        from subscriptions.fields import generate_encryption_key
        print(generate_encryption_key())  # copy into .env

    """
    return base64.b64encode(AESSIV.generate_key(512)).decode()


# ---------------------------------------------------------------------------
# Custom Django field
# ---------------------------------------------------------------------------


class EncryptedEmailField(models.EmailField):
    """An ``EmailField`` that transparently encrypts its value at rest.

    The database column is a ``text`` type (not ``varchar(254)``) because
    AES-SIV ciphertext is substantially longer than the plaintext email.
    EmailField's validators (``EmailValidator``, ``MaxLengthValidator``)
    still run against the *plaintext* value during ``full_clean()``, so
    invalid email formats are still rejected at the model layer.

    Encryption is deterministic (AES-SIV with no associated data), so
    ``unique=True`` constraints and equality lookups work without a
    second blind-index column.

    Legacy-plaintext fallback
    -------------------------
    ``from_db_value`` attempts decryption; if the value cannot be decoded
    (``InvalidTag``, ``binascii.Error``, ``ValueError``) it returns the raw
    value.  This keeps rows that were written before the migration window
    readable, and makes the ``encrypt_subscriber_email --commit`` backfill
    idempotent.
    """

    def get_internal_type(self) -> str:
        """Return 'TextField' so the DB column is an unbounded text type.

        AES-SIV ciphertext for a ~80-char email is ~360 chars — far too long
        for varchar(254).  Using TextField avoids any max-length truncation at
        the database layer.
        """
        return "TextField"

    def get_prep_value(self, value: object) -> str | None:
        """Encrypt the value before writing it to the database.

        Called by the ORM on save and on the right-hand side of filter
        expressions, so ``filter(email="alice@example.com")`` automatically
        compares ciphertext against ciphertext in the column.

        Args:
            value: The field value to prepare (plaintext string or None).

        Returns:
            The base64-encoded ciphertext, or None if value is None.

        """
        if value is None:
            return None
        prepped = super().get_prep_value(value)
        if prepped is None:
            return None
        return encrypt_value(str(prepped))

    def from_db_value(
        self,
        value: str | None,
        expression: object,
        connection: object,
    ) -> str | None:
        """Decrypt a value read from the database.

        Falls back to returning the raw value if decryption fails — this
        handles plaintext rows that pre-date the encryption backfill.

        Args:
            value: The raw value read from the DB column (ciphertext or
                legacy plaintext).
            expression: The ORM expression (unused).
            connection: The database connection (unused).

        Returns:
            The decrypted plaintext, or the raw value if decryption fails.

        """
        if value is None:
            return None
        try:
            return decrypt_value(value)
        except (InvalidTag, binascii.Error, ValueError):
            # Legacy plaintext row — return as-is until backfilled.
            logger.debug(
                "from_db_value: could not decrypt value; "
                "returning raw (likely legacy plaintext)"
            )
            return value
