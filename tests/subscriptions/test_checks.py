"""
tests/subscriptions/test_checks.py — Tests for the VAPID subject system check.

Exercises ``subscriptions.checks.check_vapid_claim_email`` directly against
monkeypatched ``push_config.VAPID_CLAIM_EMAIL`` values — no full app startup
needed. Covers a valid ``mailto:``, a valid ``https:``, and two invalid
cases (a bare address with no scheme, and an empty string).
"""

from __future__ import annotations

import pytest

from subscriptions import checks, push_config


def test_valid_mailto_subject_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A well-formed 'mailto:' subject produces no errors."""
    monkeypatch.setattr(push_config, "VAPID_CLAIM_EMAIL", "mailto:ops@example.com")
    errors = checks.check_vapid_claim_email(app_configs=None)
    assert errors == []


def test_valid_https_subject_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A well-formed 'https:' subject produces no errors."""
    monkeypatch.setattr(push_config, "VAPID_CLAIM_EMAIL", "https://example.com/contact")
    errors = checks.check_vapid_claim_email(app_configs=None)
    assert errors == []


def test_bare_address_without_scheme_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """An address with no 'mailto:' or 'https:' scheme fails with E001."""
    monkeypatch.setattr(push_config, "VAPID_CLAIM_EMAIL", "noreply@localhost")
    errors = checks.check_vapid_claim_email(app_configs=None)
    assert len(errors) == 1
    assert errors[0].id == "subscriptions.push_config.E001"
    assert "noreply@localhost" in errors[0].msg


def test_empty_string_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty VAPID_CLAIM_EMAIL fails with E001."""
    monkeypatch.setattr(push_config, "VAPID_CLAIM_EMAIL", "")
    errors = checks.check_vapid_claim_email(app_configs=None)
    assert len(errors) == 1
    assert errors[0].id == "subscriptions.push_config.E001"
