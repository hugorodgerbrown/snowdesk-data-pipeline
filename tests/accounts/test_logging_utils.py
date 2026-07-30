"""
tests/accounts/test_logging_utils.py — Tests for apps.accounts.logging_utils.

Covers mask_email: normal address, single-char local part, empty local part,
no ``@`` character, empty string, a check that the full original address never
appears in the output for the normal case, and a unicode local-part case.
"""

from __future__ import annotations

from apps.accounts.logging_utils import mask_email


class TestMaskEmail:
    """Tests for mask_email."""

    def test_normal_address_returns_masked_form(self) -> None:
        """A typical address is masked to single initial + *** @ domain."""
        assert mask_email("alice@example.com") == "a***@example.com"

    def test_single_char_local_part(self) -> None:
        """A single-character local part still yields the initial."""
        assert mask_email("a@example.com") == "a***@example.com"

    def test_empty_local_part(self) -> None:
        """An empty local part (address starts with @) yields ***@domain."""
        assert mask_email("@example.com") == "***@example.com"

    def test_no_at_symbol_returns_stars(self) -> None:
        """An input without @ returns '***' — nothing recognisable to preserve."""
        assert mask_email("noop") == "***"

    def test_empty_string_returns_stars(self) -> None:
        """An empty string returns '***'."""
        assert mask_email("") == "***"

    def test_full_address_not_in_output_normal_case(self) -> None:
        """The full original address must never appear verbatim in the masked output."""
        original = "alice@example.com"
        result = mask_email(original)
        assert original not in result

    def test_subdomain_address(self) -> None:
        """Subdomain addresses are handled correctly."""
        assert mask_email("bob@mail.example.co.uk") == "b***@mail.example.co.uk"

    def test_long_local_part_still_single_char_prefix(self) -> None:
        """Long local parts are masked to one char prefix."""
        assert mask_email("verylongname@example.com") == "v***@example.com"

    def test_unicode_local_part_returns_first_char_prefix(self) -> None:
        """A unicode local part is masked to its first character + ***@domain."""
        assert mask_email("héllo@example.com") == "h***@example.com"
