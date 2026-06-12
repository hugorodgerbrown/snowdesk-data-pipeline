"""
Rollback AES-256-SIV at-rest email encryption (SNOW-312).

Reverts the encryption introduced by SNOW-285 (migration 0005).

This migration has two operations:

1. RunPython — opportunistic decrypt.  Reads each subscriber row's raw email
   column.  Any value that cannot contain '@' is assumed to be base64 AES-SIV
   ciphertext and is decrypted inline.  Plaintext rows (containing '@') are
   left unchanged.  If ciphertext rows are present but FIELD_ENCRYPTION_KEY
   is absent from the environment, a RuntimeError is raised with a clear
   message.

2. AlterField — restores the email column to a standard EmailField (varchar
   254), undoing the TextField widening from migration 0005.

Deploy ordering
---------------
Production deployments must keep FIELD_ENCRYPTION_KEY set in the Render
environment until this migration has run successfully.  Once the migration
completes the variable can be removed from the environment and from
config/settings/base.py (both are done in the same SNOW-312 deploy).

The backfill command (encrypt_subscriber_email) was never run in production,
so the decrypt step is a precaution for any rows saved between the SNOW-285
deploy and this rollback.
"""

import base64
import logging

from django.db import migrations, models

logger = logging.getLogger(__name__)


def _decrypt_emails(apps: object, schema_editor: object) -> None:
    """Decrypt any AES-SIV-encrypted email rows, writing back plaintext.

    Uses the historical ``Subscriber`` model (plain TextField, no transparent
    decrypt) so ORM reads return the raw column value.

    Detection heuristic: base64 tokens never contain '@'; every valid email
    address does.  Any value without '@' is treated as ciphertext.

    The FIELD_ENCRYPTION_KEY is read from the environment via python-decouple
    inside this function — NOT from Django settings, because the settings key
    is being deleted in the same deploy.

    Args:
        apps: Django Apps registry (historical models).
        schema_editor: Database schema editor (unused; only ORM writes used).

    Raises:
        RuntimeError: If ciphertext rows exist but FIELD_ENCRYPTION_KEY is
            absent from the environment.

    """
    # Import inside the function — decouple reads from .env / environment;
    # cryptography is available because it is still listed in pyproject.toml.
    from decouple import config  # type: ignore[import-untyped]

    Subscriber = apps.get_model("subscriptions", "Subscriber")  # noqa: N806
    all_rows = list(Subscriber.objects.all().only("id", "email"))

    ciphertext_rows = [row for row in all_rows if "@" not in row.email]

    if not ciphertext_rows:
        logger.info(
            "0006_rollback_email_encryption: no encrypted rows found; nothing to do"
        )
        return

    raw_key_b64: str | None = config("FIELD_ENCRYPTION_KEY", default=None)
    if not raw_key_b64:
        raise RuntimeError(
            "SNOW-312 rollback migration: found %d subscriber row(s) with "
            "AES-SIV ciphertext but FIELD_ENCRYPTION_KEY is not set in the "
            "environment.  Set FIELD_ENCRYPTION_KEY to the production key and "
            "re-run migrate, then remove the variable." % len(ciphertext_rows)
        )

    from cryptography.hazmat.primitives.ciphers.aead import AESSIV

    raw_key = base64.b64decode(raw_key_b64)
    cipher = AESSIV(raw_key)

    decrypted_count = 0
    for row in ciphertext_rows:
        ciphertext = base64.b64decode(row.email)
        plaintext = cipher.decrypt(ciphertext, None).decode()
        row.email = plaintext.lower()
        row.save(update_fields=["email"])
        decrypted_count += 1

    logger.info(
        "0006_rollback_email_encryption: decrypted %d subscriber row(s)",
        decrypted_count,
    )


def _noop(apps: object, schema_editor: object) -> None:
    """No-op reverse operation — cannot re-encrypt without knowing the original key.

    Args:
        apps: Django Apps registry (unused).
        schema_editor: Database schema editor (unused).

    """


class Migration(migrations.Migration):
    """Roll back AES-SIV email encryption; restore email to a standard EmailField."""

    dependencies = [
        ("subscriptions", "0005_encrypt_subscriber_email"),
    ]

    operations = [
        migrations.RunPython(_decrypt_emails, _noop),
        migrations.AlterField(
            model_name="subscriber",
            name="email",
            field=models.EmailField(db_index=True, unique=True),
        ),
    ]
