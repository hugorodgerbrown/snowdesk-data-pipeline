"""
tests/conftest.py — shared pytest fixtures.

The ``_force_sync_weather_fetch`` autouse fixture forces
``settings.WEATHER_FETCH_ASYNC = False`` so any direct call to
``fetch_weather_async`` in tests runs synchronously on the main thread
(see tests/weather/services/test_weather_fetcher.py::TestFetchWeatherAsync).
The ``finally`` clause inside the helper skips ``connections.close_all()``
on the main thread, so the test's transaction connection is preserved.

The ``_disable_inline_weather_warmup`` autouse fixture patches
``apps.public.views.fetch_weather_async`` to a no-op so the implicit warmup
fired from ``bulletin_detail`` on past-date renders never touches the
network in the test environment. Without this, every test that renders a
past-date bulletin page (e.g. ``_freeze("2026-03-20")`` + ``date(2026, 3, 15)``)
hangs CI on a real ``requests.get`` to the Open-Meteo archive endpoint.
Tests that need to assert the warmup IS scheduled re-patch the same
attribute with a spy; ``monkeypatch.setattr`` is LIFO so the spy wins
over this autouse no-op. Tests that exercise the helper itself import it
from ``apps.weather.services.weather_fetcher`` and are unaffected by the
``apps.public.views`` patch.

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
"""

from __future__ import annotations

import posthog
import pytest
from pytest_django.fixtures import SettingsWrapper


@pytest.fixture(autouse=True)
def _force_sync_weather_fetch(settings: SettingsWrapper) -> None:
    """Force synchronous weather fetch in tests by default (SNOW-164)."""
    settings.WEATHER_FETCH_ASYNC = False


@pytest.fixture(autouse=True)
def _disable_posthog(settings: SettingsWrapper) -> None:
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


@pytest.fixture(autouse=True)
def _disable_inline_weather_warmup(monkeypatch: pytest.MonkeyPatch) -> None:
    """No-op ``apps.public.views.fetch_weather_async`` for every test by default.

    Stops the implicit past-date warmup scheduled by ``bulletin_detail``
    from hitting Open-Meteo during the test run — without this, CI hangs
    on real network calls. Tests that need to verify the warmup is
    scheduled override this with their own ``monkeypatch.setattr`` (last
    setattr wins). Tests that drive ``fetch_weather_async`` directly
    import it from ``apps.weather.services.weather_fetcher`` and bypass
    this patch entirely.
    """
    monkeypatch.setattr(
        "apps.public.views.fetch_weather_async",
        lambda *args, **kwargs: None,
    )
