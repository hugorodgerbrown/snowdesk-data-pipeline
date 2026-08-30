"""
tests/conftest.py — shared pytest fixtures.

The ``_disable_posthog`` autouse fixture (SNOW-548) pins the analytics
kill switches off for every test regardless of how the process was
launched. ``tox.ini`` already sets ``POSTHOG_API_KEY=`` for
``[testenv:test]``, but that guard lives entirely in the tox environment:
a bare ``pytest`` invocation — against convention, but routine during
iterative debugging — picks up whatever is in the developer's ``.env``
and ``config.settings.development`` forwards it straight through to
``AnalyticsConfig.ready()``, producing live uploads to PostHog from a
test run. The fixture closes that hole from the other direction, and
along the way makes the suite deterministic: a developer with
``PWA_TELEMETRY_ENABLED=False`` in ``.env`` was previously watching 28
analytics tests fail locally that pass in CI.

Email tests no longer need a sync-force fixture — the test settings module
inherits ``ImmediateBackend`` from ``development.py``, which runs tasks inline
and populates ``mail.outbox`` synchronously.

``pytest_configure`` swaps the production password hasher for MD5 for the
duration of the run. ``PBKDF2PasswordHasher`` runs 1,500,000 iterations by
design — 0.11s per hash on CI hardware — and every factory that builds a
``User`` pays it (``UserFactory`` sets ``factory.django.Password``), as does
every ``check_password`` assertion. Across the suite that was the single
largest cost in test *setup*. The swap is confined to the test process; it
changes no stored hash and no application code, and the hashing *contract*
stays covered because ``check_password`` still round-trips against whatever
hasher is configured. Tests asserting on a specific algorithm must set
``PASSWORD_HASHERS`` themselves via the ``settings`` fixture.
"""

from __future__ import annotations

import posthog
import pytest
from pytest_django.fixtures import Settings

# Test-only password hashing. MD5 is unusable for storing a real credential;
# it is used here because the suite never stores one — every password in the
# test process is a literal written a few lines above the assertion that
# reads it back. See the module docstring for why this is worth doing.
_FAST_PASSWORD_HASHER = "django.contrib.auth.hashers.MD5PasswordHasher"


def pytest_configure() -> None:
    """Install the cheap password hasher before any fixture builds a user.

    Set here rather than in an autouse fixture so that it also covers
    session- and class-scoped fixtures, which build their users outside
    any individual test's ``settings`` override.
    """
    from django.conf import settings as django_settings

    django_settings.PASSWORD_HASHERS = [_FAST_PASSWORD_HASHER]


@pytest.fixture(autouse=True)
def _disable_posthog(settings: Settings) -> None:
    """Guarantee no test run reaches PostHog over the network (SNOW-548).

    Pins both halves of the analytics configuration rather than trusting
    the launcher:

    * ``POSTHOG_API_KEY = ""`` — the condition ``analytics.track()`` and
      ``apps.analytics.signals.emit_server_signal()`` short-circuit on. Also
      set on the ``posthog`` module globals, because
      ``AnalyticsConfig.ready()`` copies the setting onto them once at
      startup and a settings override alone would leave a live client
      behind.
    * ``PWA_TELEMETRY_ENABLED = True`` — the operator master switch. It
      is forced *on* deliberately: it gates the telemetry pipeline's
      behaviour, not its network access, so tests must see the shipped
      default rather than whatever a developer happens to have in
      ``.env``. With the API key empty, nothing leaves the process
      either way.

    Tests that exercise the enabled path re-override these via
    ``@override_settings`` and patch ``posthog.capture`` explicitly, so
    they assert against a mock rather than ambient global state.
    """
    settings.POSTHOG_API_KEY = ""
    settings.PWA_TELEMETRY_ENABLED = True
    posthog.api_key = ""
    posthog.disabled = True
