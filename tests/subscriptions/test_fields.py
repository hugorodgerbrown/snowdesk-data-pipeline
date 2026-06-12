"""
tests/subscriptions/test_fields.py — Tests for subscriptions/fields.py.

Covers:
  - Round-trip encrypt/decrypt for typical email addresses.
  - Determinism: encrypting the same plaintext twice yields identical ciphertext.
  - Ciphertext differs from plaintext.
  - from_db_value legacy-plaintext fallback: returns raw input when decryption
    fails (non-base64 or non-ciphertext input).
  - generate_encryption_key produces a valid base64 string.
"""

import base64
import binascii

import pytest

from subscriptions.fields import (
    EncryptedEmailField,
    decrypt_value,
    encrypt_value,
    generate_encryption_key,
)


class TestEncryptValue:
    """Tests for encrypt_value helper."""

    def test_round_trip_typical_email(self) -> None:
        """Encrypting then decrypting recovers the original email."""
        plaintext = "alice@example.com"
        token = encrypt_value(plaintext)
        assert decrypt_value(token) == plaintext

    def test_round_trip_long_email(self) -> None:
        """Round-trip works for a long email address."""
        plaintext = "very.long.email.address+tag@sub.domain.example.com"
        token = encrypt_value(plaintext)
        assert decrypt_value(token) == plaintext

    def test_determinism_same_plaintext_produces_identical_ciphertext(self) -> None:
        """AES-SIV with no associated data is deterministic."""
        plaintext = "deterministic@example.com"
        token1 = encrypt_value(plaintext)
        token2 = encrypt_value(plaintext)
        assert token1 == token2

    def test_different_plaintexts_produce_different_ciphertext(self) -> None:
        """Different inputs produce different outputs."""
        token_a = encrypt_value("alice@example.com")
        token_b = encrypt_value("bob@example.com")
        assert token_a != token_b

    def test_ciphertext_is_not_plaintext(self) -> None:
        """The encrypted token does not equal or contain the original email."""
        plaintext = "alice@example.com"
        token = encrypt_value(plaintext)
        assert token != plaintext
        assert plaintext not in token

    def test_output_is_valid_base64(self) -> None:
        """The ciphertext is a valid base64 string."""
        token = encrypt_value("test@example.com")
        # Should not raise.
        base64.b64decode(token)

    def test_output_is_longer_than_input(self) -> None:
        """AES-SIV ciphertext (base64-encoded) is longer than the plaintext.

        AES-SIV adds a 16-byte authentication tag; after base64 encoding the
        token is roughly (len(plaintext) + 16) * 4/3 characters.
        """
        plaintext = "test@example.com"
        token = encrypt_value(plaintext)
        assert len(token) > len(plaintext)


class TestDecryptValue:
    """Tests for decrypt_value helper."""

    def test_decrypt_restores_plaintext(self) -> None:
        """decrypt_value inverts encrypt_value."""
        plaintext = "bob@example.com"
        assert decrypt_value(encrypt_value(plaintext)) == plaintext

    def test_tampered_token_raises(self) -> None:
        """A tampered ciphertext raises InvalidTag (integrity check)."""
        from cryptography.exceptions import InvalidTag

        token = encrypt_value("alice@example.com")
        # Flip a byte in the ciphertext.
        raw = bytearray(base64.b64decode(token))
        raw[0] ^= 0xFF
        bad_token = base64.b64encode(bytes(raw)).decode()
        with pytest.raises(InvalidTag):
            decrypt_value(bad_token)

    def test_non_base64_raises_binascii_error(self) -> None:
        """Non-base64 input raises binascii.Error."""
        with pytest.raises(binascii.Error):
            decrypt_value("not valid base64!!!")


class TestGenerateEncryptionKey:
    """Tests for generate_encryption_key helper."""

    def test_returns_string(self) -> None:
        """generate_encryption_key returns a string."""
        key = generate_encryption_key()
        assert isinstance(key, str)

    def test_decodes_to_64_bytes(self) -> None:
        """The key decodes to exactly 64 bytes (512 bits for AES-256-SIV)."""
        key = generate_encryption_key()
        assert len(base64.b64decode(key)) == 64

    def test_each_call_returns_unique_key(self) -> None:
        """Two generated keys are different (randomness check)."""
        key1 = generate_encryption_key()
        key2 = generate_encryption_key()
        assert key1 != key2


class TestEncryptedEmailFieldFromDbValue:
    """Tests for EncryptedEmailField.from_db_value legacy-plaintext fallback."""

    def _field(self) -> EncryptedEmailField:
        """Return a bare EncryptedEmailField instance for testing."""
        return EncryptedEmailField()

    def test_none_returns_none(self) -> None:
        """from_db_value(None) returns None."""
        field = self._field()
        assert field.from_db_value(None, None, None) is None

    def test_valid_ciphertext_decrypts(self) -> None:
        """A token produced by encrypt_value is successfully decrypted."""
        field = self._field()
        plaintext = "alice@example.com"
        token = encrypt_value(plaintext)
        assert field.from_db_value(token, None, None) == plaintext

    def test_legacy_plaintext_fallback_returns_raw(self) -> None:
        """A plaintext row (not valid ciphertext) is returned as-is."""
        field = self._field()
        raw = "legacy@example.com"
        result = field.from_db_value(raw, None, None)
        assert result == raw

    def test_non_base64_returns_raw(self) -> None:
        """A non-base64 value is returned as-is (not raised)."""
        field = self._field()
        raw = "not-even-base64!!!"
        result = field.from_db_value(raw, None, None)
        assert result == raw

    def test_get_internal_type_is_textfield(self) -> None:
        """get_internal_type returns 'TextField' so the column is text."""
        field = self._field()
        assert field.get_internal_type() == "TextField"

    def test_get_prep_value_none_returns_none(self) -> None:
        """get_prep_value(None) returns None."""
        field = self._field()
        assert field.get_prep_value(None) is None

    def test_get_prep_value_encrypts(self) -> None:
        """get_prep_value encrypts a string value."""
        field = self._field()
        plaintext = "alice@example.com"
        result = field.get_prep_value(plaintext)
        assert result is not None
        assert result != plaintext
        assert decrypt_value(result) == plaintext

    def test_get_prep_value_deterministic(self) -> None:
        """get_prep_value produces the same ciphertext on repeated calls."""
        field = self._field()
        result1 = field.get_prep_value("alice@example.com")
        result2 = field.get_prep_value("alice@example.com")
        assert result1 == result2
