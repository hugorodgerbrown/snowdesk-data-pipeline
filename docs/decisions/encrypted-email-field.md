---
name: encrypted-email-field
description: Subscriber.email encrypted at rest with deterministic AES-SIV via a custom Django field — rationale, key posture, and deploy ordering
status: historical
last-reviewed: 2026-06-12
---

# Encrypted email field (SNOW-285) — reversed by SNOW-312

**This decision was reversed.** The AES-SIV encryption introduced in SNOW-285
was rolled back in SNOW-312 (migration `0006_rollback_email_encryption`). The
backfill command (`encrypt_subscriber_email`) was never run in production, so
no subscriber rows held real ciphertext. The added complexity — a custom field,
a required environment variable, an admin search workaround, and a key-rotation
burden — was not justified given that encryption was never active in production.

---

**Original decision.** `Subscriber.email` is stored encrypted at rest using a
small custom Django field (`EncryptedEmailField` in `subscriptions/fields.py`),
built on AES-SIV (RFC 5297) via the `cryptography` library already in the
project.

## Why AES-SIV (not the scoped library)

The original scope recommended `django-encrypted-model-fields`. That library
uses **Fernet with a random IV** — identical plaintext produces different
ciphertext on every write. This breaks `unique=True` (each new insert of the
same email looks different to the DB) and every equality lookup (`filter(email=…)`
always returns nothing for subsequent queries).

No maintained Django field-encryption library offers deterministic encryption on
Django 6 / Python 3.14.

AES-SIV with **no associated data** is deterministic by design (RFC 5297
§2.6): same plaintext → same ciphertext, unconditionally. This preserves:

- `unique=True` constraint (the index compares identical ciphertext values).
- `db_index` equality lookups (`by_email`, `get`, `get_or_create`).
- Admin exact-match search via the `email:<addr>` token.

The tradeoff is that an attacker who knows two users share an email can confirm
it by comparing ciphertext. Our subscriber model has `unique=True` on email, so
no two rows ever share an address — this leakage path does not exist.

AES-256-SIV uses a 64-byte (512-bit) key. `cryptography 48` is already a
dependency. The field is ~40 lines, one caller — no premature abstraction.

## Key posture

`FIELD_ENCRYPTION_KEY` is set in `config/settings/base.py` with
`config("FIELD_ENCRYPTION_KEY")` and **no default**. Django fails to start
without it. This mirrors `SECRET_KEY` — fail-closed is the right posture for a
secret that protects subscriber identities.

The key is a base64-encoded 64-byte value. Generate one with:

```python
from subscriptions.fields import generate_encryption_key
print(generate_encryption_key())
```

## Legacy-plaintext fallback

`from_db_value` attempts decryption. If it fails (`InvalidTag`, `binascii.Error`,
`ValueError`) it returns the raw value unchanged. This keeps any plaintext rows
that pre-date the backfill readable during the migration window.

## Deploy ordering

1. **Run `migrate`** — a metadata-only `AlterField` (varchar → text). No data
   writes. Existing plaintext rows are readable via the fallback.
2. **Run `encrypt_subscriber_email --commit`** — re-saves every `Subscriber`
   row so the ORM writes ciphertext. Idempotent.
3. From this point, all new writes are encrypted automatically via `get_prep_value`.

## Consequences

- A database dump no longer exposes plaintext email addresses.
- Admin partial-match email search is replaced by the `email:<addr>` exact-match
  token (encrypted column, so icontains is meaningless).
- Key rotation requires a new `FIELD_ENCRYPTION_KEY` plus a re-run of
  `encrypt_subscriber_email --commit`. This is a planned future operation, not
  in scope for SNOW-285. Rotating `FIELD_ENCRYPTION_KEY` also requires a
  process restart — the AES-SIV cipher is cached at module level
  (`_cipher_instance` in `subscriptions/fields.py`) for the life of the process
  and is not re-read on a settings change.
- The column type changes from `varchar(254)` to `text` — metadata-only on
  Postgres; SQLite rebuilds the table (fine on a tiny subscriber table).
