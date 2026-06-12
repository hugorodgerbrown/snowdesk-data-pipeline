"""
tests/subscriptions/test_migration_0006.py — Unit tests for the decrypt step
in migration 0006_rollback_email_encryption.

Tests the ``_decrypt_emails`` data-migration function in isolation — no
database migrations are run; the function is exercised by injecting a
lightweight fake Apps registry and a fake Subscriber queryset.

Covers:
  - All-plaintext rows: no key required; rows are left unchanged.
  - Mixed rows: ciphertext rows are decrypted; plaintext rows are untouched.
  - Ciphertext rows with no key: RuntimeError with a clear message.
  - Lowercase invariant: decrypted email is stored lowercased.
"""

import base64
import importlib
import types
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESSIV

# ---------------------------------------------------------------------------
# Module loading (module name starts with a digit)
# ---------------------------------------------------------------------------

_MIGRATION_MODULE = importlib.import_module(
    "subscriptions.migrations.0006_rollback_email_encryption"
)
_decrypt_emails = _MIGRATION_MODULE._decrypt_emails


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_key() -> bytes:
    """Generate a throwaway 512-bit AES-SIV key for test use only."""
    return AESSIV.generate_key(512)


def _encrypt(key: bytes, plaintext: str) -> str:
    """Encrypt plaintext with AES-SIV, return base64 ciphertext string."""
    cipher = AESSIV(key)
    ciphertext = cipher.encrypt(plaintext.encode(), None)
    return base64.b64encode(ciphertext).decode()


class _FakeSubscriber:
    """Minimal Subscriber stand-in with id and email attributes."""

    def __init__(self, pk: int, email: str) -> None:
        """Initialise with pk and email."""
        self.pk = pk
        self.email = email
        self._saved_fields: list[str] | None = None

    def save(self, update_fields: list[str] | None = None) -> None:
        """Record the last save call for assertion in tests."""
        self._saved_fields = update_fields


class _FakeQuerySet:
    """Minimal queryset stand-in backed by a list of _FakeSubscriber instances."""

    def __init__(self, rows: list[_FakeSubscriber]) -> None:
        """Initialise with a list of fake subscriber rows."""
        self._rows = rows

    def all(self) -> "_FakeQuerySet":
        """Return self (mimics QuerySet.all())."""
        return self

    def only(self, *_fields: str) -> "_FakeQuerySet":
        """Return self (field selection is irrelevant for the fake)."""
        return self

    def __iter__(self) -> object:
        """Iterate over the backing rows."""
        return iter(self._rows)


def _make_apps(rows: list[_FakeSubscriber]) -> types.SimpleNamespace:
    """Return a minimal fake Apps registry serving the given subscriber rows.

    Args:
        rows: The fake subscriber rows to serve.

    Returns:
        A SimpleNamespace with a ``get_model`` method.

    """

    class _FakeModel:
        objects = _FakeQuerySet(rows)

    def get_model(app_label: str, model_name: str) -> type[_FakeModel]:
        """Return the fake Subscriber model regardless of arguments."""
        return _FakeModel

    return types.SimpleNamespace(get_model=get_model)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDecryptEmails:
    """Tests for the _decrypt_emails migration function."""

    def test_no_rows_no_key_required(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With no ciphertext rows the function succeeds even without a key."""
        # Plaintext rows all contain '@' — no decryption path triggered.
        rows = [
            _FakeSubscriber(1, "alice@example.com"),
            _FakeSubscriber(2, "bob@example.com"),
        ]
        apps = _make_apps(rows)
        # No key in environment.
        monkeypatch.delenv("FIELD_ENCRYPTION_KEY", raising=False)

        # Should not raise.
        _decrypt_emails(apps, None)

        # Rows are untouched.
        assert rows[0].email == "alice@example.com"
        assert rows[1].email == "bob@example.com"

    def test_ciphertext_rows_are_decrypted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ciphertext rows (no '@') are decrypted and written back."""
        key = _make_key()
        ciphertext = _encrypt(key, "alice@example.com")
        # Verify the ciphertext has no '@' (the detection heuristic).
        assert "@" not in ciphertext

        rows = [_FakeSubscriber(1, ciphertext)]
        apps = _make_apps(rows)

        monkeypatch.setenv("FIELD_ENCRYPTION_KEY", base64.b64encode(key).decode())

        _decrypt_emails(apps, None)

        assert rows[0].email == "alice@example.com"
        assert rows[0]._saved_fields == ["email"]

    def test_mixed_rows_only_ciphertext_rows_updated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Plaintext rows are left unchanged; only ciphertext rows are decrypted."""
        key = _make_key()
        ciphertext = _encrypt(key, "charlie@example.com")

        plaintext_row = _FakeSubscriber(1, "alice@example.com")
        cipher_row = _FakeSubscriber(2, ciphertext)
        rows = [plaintext_row, cipher_row]
        apps = _make_apps(rows)

        monkeypatch.setenv("FIELD_ENCRYPTION_KEY", base64.b64encode(key).decode())

        _decrypt_emails(apps, None)

        assert plaintext_row.email == "alice@example.com"
        assert plaintext_row._saved_fields is None  # not saved
        assert cipher_row.email == "charlie@example.com"
        assert cipher_row._saved_fields == ["email"]

    def test_ciphertext_without_key_raises_runtime_error(self) -> None:
        """RuntimeError raised when ciphertext rows exist but key is absent.

        ``decouple.config`` is patched at the module level to return None,
        simulating a missing FIELD_ENCRYPTION_KEY regardless of the local
        ``.env`` file (decouple reads from the file as well as the
        environment, so patching the env var alone is insufficient).
        """
        key = _make_key()
        ciphertext = _encrypt(key, "alice@example.com")

        rows = [_FakeSubscriber(1, ciphertext)]
        apps = _make_apps(rows)

        # Patch decouple.config to return None for any variable.
        def _null_config(var: str, default: object = None) -> None:  # noqa: ARG001
            return None

        with patch("decouple.config", side_effect=_null_config):
            with pytest.raises(RuntimeError, match="FIELD_ENCRYPTION_KEY"):
                _decrypt_emails(apps, None)

    def test_decrypted_email_is_lowercased(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Decrypted email is stored in lowercase (preserves lowercase invariant)."""
        key = _make_key()
        # Encrypt an uppercase variant to simulate a row that was saved before
        # case-normalisation was fully enforced.
        ciphertext = _encrypt(key, "ALICE@EXAMPLE.COM")

        rows = [_FakeSubscriber(1, ciphertext)]
        apps = _make_apps(rows)

        monkeypatch.setenv("FIELD_ENCRYPTION_KEY", base64.b64encode(key).decode())

        _decrypt_emails(apps, None)

        assert rows[0].email == "alice@example.com"
