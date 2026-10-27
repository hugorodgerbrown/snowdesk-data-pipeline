"""
tests/config/test_mailers.py — Outbound email is configured through MAILERS.

Django 6.1 deprecated the flat ``EMAIL_HOST`` / ``EMAIL_PORT`` / … settings
(removed in Django 2028, and an error alongside ``MAILERS``). These tests
read ``config.settings.base`` as a module rather than through
``django.conf.settings``, because the test runner replaces ``MAILERS``
wholesale with a locmem backend.
"""

from __future__ import annotations

from config.settings import base

DEPRECATED_EMAIL_SETTINGS = (
    "EMAIL_BACKEND",
    "EMAIL_HOST",
    "EMAIL_PORT",
    "EMAIL_HOST_USER",
    "EMAIL_HOST_PASSWORD",
    "EMAIL_USE_TLS",
)


def test_default_mailer_is_smtp_built_from_the_smtp_settings() -> None:
    """The default mailer carries the values read from the EMAIL_* env vars."""
    mailer = base.MAILERS["default"]

    assert mailer["BACKEND"] == "django.core.mail.backends.smtp.EmailBackend"
    assert mailer["OPTIONS"] == {
        "host": base.SMTP_HOST,
        "port": base.SMTP_PORT,
        "username": base.SMTP_USERNAME,
        "password": base.SMTP_PASSWORD,
        "use_tls": base.SMTP_USE_TLS,
    }


def test_no_deprecated_email_setting_is_defined() -> None:
    """Defining one alongside MAILERS makes Django refuse to start."""
    defined = [name for name in DEPRECATED_EMAIL_SETTINGS if hasattr(base, name)]

    assert defined == []
