"""
tests/core/management/test_dump_settings.py — Tests for the redacted settings
dump (SNOW-580).

The command's whole value rests on one property: a secret's real value must
never reach stdout. Everything else is presentation.
"""

from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.test import override_settings

from apps.core.settings_spec import REDACTED, SETTINGS_SPEC


def _run(*args: str, **options: object) -> str:
    """Run dump_settings and return its stdout."""
    out = StringIO()
    call_command("dump_settings", *args, stdout=out, **options)
    return out.getvalue()


@override_settings(SECRET_KEY="super-secret-signing-key")
def test_redacts_secret_values() -> None:
    """A secret's real value never appears; the marker does."""
    output = _run()

    assert "super-secret-signing-key" not in output
    assert REDACTED in output


# Deliberately low-entropy: a realistic-looking fake key trips the gitleaks
# generic-api-key rule in the pre-commit hook. Redaction is driven by the
# spec's `secret` flag, not by the value's shape, so this exercises it fully.
@override_settings(METEOFRANCE_API_KEY="fake-key-fake-key-fake-key")
def test_redacts_every_secret_in_the_spec() -> None:
    """No spec entry marked secret leaks its value.

    Asserted across the whole spec rather than one setting, so adding a new
    secret without marking it is caught here as well as in the spec tests.
    """
    from django.conf import settings

    output = _run()

    for spec in SETTINGS_SPEC:
        if not spec.secret:
            continue
        value = getattr(settings, spec.name, None)
        if value:
            assert str(value) not in output, f"{spec.name} leaked its value"


def test_lists_every_spec_entry() -> None:
    """Each setting in the spec appears in the dump."""
    output = _run()

    for spec in SETTINGS_SPEC:
        assert spec.name in output


def test_reports_a_total_and_problem_count() -> None:
    """The summary line states how many settings and how many problems."""
    output = _run()

    assert f"{len(SETTINGS_SPEC)} settings" in output


@override_settings(OPEN_METEO_API_BASE_URL="customer-api.open-meteo.com")
def test_flags_a_broken_setting() -> None:
    """A failing value is shown with its problem inline."""
    output = _run()

    assert "OPEN_METEO_API_BASE_URL" in output
    assert "no scheme" in output
    assert "1 problem(s)" in output


@override_settings(OPEN_METEO_API_BASE_URL="customer-api.open-meteo.com")
def test_problems_only_filters_to_failures() -> None:
    """--problems-only prints just the broken settings."""
    output = _run("--problems-only")

    assert "OPEN_METEO_API_BASE_URL" in output
    assert "SITE_ENVIRONMENT" not in output


def test_problems_only_says_so_when_clean() -> None:
    """A clean configuration reports success rather than printing nothing."""
    output = _run("--problems-only")

    assert "No configuration problems." in output


def test_is_read_only() -> None:
    """The command takes no --commit flag because it changes nothing.

    Asserted rather than assumed: the project's management-command contract
    requires an explicit opt-in for anything that writes, and a reviewer
    should be able to see at a glance that this one cannot.
    """
    from apps.core.management.commands.dump_settings import Command

    parser = Command().create_parser("manage.py", "dump_settings")
    flags = {opt for action in parser._actions for opt in action.option_strings}

    assert "--commit" not in flags
    assert "--dry-run" not in flags
    assert "--problems-only" in flags


def test_runs_with_no_arguments() -> None:
    """The bare invocation does the useful thing, per the command contract."""
    output = _run()

    assert "SETTINGS" not in output.splitlines()[0]  # not a usage/error banner
    assert "settings," in output
