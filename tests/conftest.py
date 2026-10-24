"""
tests/conftest.py — shared pytest fixtures.

The ``_pin_telemetry`` autouse fixture (SNOW-548) pins the telemetry
master switch to its shipped default for every test regardless of how the
process was launched: a developer with ``PWA_TELEMETRY_ENABLED=False`` in
``.env`` was previously watching 28 analytics tests fail locally that pass
in CI. (Until SNOW-1182 it also pinned PostHog's key empty; the
``_no_live_fivebar`` fixture and the Sentry re-init in ``pytest_configure``
now cover the hosts a test run could reach.)

The ``_no_live_terrain_origin`` autouse fixture (SNOW-910) is its sibling
for the other host a test run can reach on its own: since terrain sampling
is enqueued from ``create_route`` and the test task backend runs inline,
an ordinary upload test would otherwise walk the real tile origin.

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

import pytest
import requests
import sentry_sdk
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

    # SNOW-1181: settings initialise Sentry when SENTRY_DSN is set, and a
    # bare ``pytest`` run picks that up from a developer's ``.env``.
    # Re-initialising with an explicit empty DSN (``None`` would fall back
    # to the environment variable) leaves a client that sends nothing.
    # ``tox.ini`` also blanks SENTRY_DSN for the test env.
    sentry_sdk.init(dsn="")


@pytest.fixture(autouse=True)
def _pin_telemetry(settings: Settings) -> None:
    """Pin ``PWA_TELEMETRY_ENABLED`` to its shipped default (SNOW-548).

    It gates the telemetry pipeline's behaviour, not its network access, so
    tests must see the shipped default rather than whatever a developer
    happens to have in ``.env``.
    """
    settings.PWA_TELEMETRY_ENABLED = True


@pytest.fixture(autouse=True)
def _no_live_fivebar(settings: Settings) -> None:
    """Guarantee no test run sends to fivebar or renders its tag (SNOW-1180).

    The sibling of ``_pin_telemetry``: ``apps/analytics/fivebar.py`` sends
    whenever ``FIVEBAR_TOKEN`` is set, and a bare ``pytest`` run would
    otherwise pick it up from a developer's ``.env``. ``FIVEBAR_DOMAIN`` is
    pinned empty too, so pages render as a deploy without fivebar does.
    Tests of the enabled path re-override both and mock the send.
    """
    settings.FIVEBAR_TOKEN = ""
    settings.FIVEBAR_DOMAIN = ""


@pytest.fixture(autouse=True)
def _no_live_terrain_origin(settings: Settings) -> None:
    """Guarantee no test run reaches the terrain tile origin (SNOW-910).

    The sibling of ``_pin_telemetry``, and it exists for the same
    reason: an unguarded default sends real requests to a real host from
    a test run. Since SNOW-910 an ordinary ``create_route`` enqueues
    terrain sampling, and the task backend in tests is ``ImmediateBackend``
    — so the sampler runs INLINE inside a plain upload test and would walk
    ``https://tiles.snowdesk-data.info`` a tile at a time.

    Pointed at a closed local port rather than patched out, because the
    behaviour under test is then the real one:
    ``apps.locations.services.terrain`` promises that an unreachable
    origin is a ``TerrainUnknown.UNAVAILABLE`` result and never an
    exception, and a refused connection is exactly the path that promise
    covers. A test that wants real answers mocks the transport itself
    (``tests/locations/services/test_terrain.py``) or overrides this
    setting, and either wins over an autouse fixture.

    Port 9 is the discard port and is closed on every machine this runs
    on, so the refusal is immediate — no DNS, no timeout, no wait.
    """
    settings.TERRAIN_TILE_BASE_URL = "http://127.0.0.1:9/terrain/v1"


@pytest.fixture(autouse=True)
def _no_live_meeting_point_forecast(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guarantee saving a trip never reaches Open-Meteo (SNOW-1217).

    The terrain guard's twin. Since SNOW-1217 an ordinary ``create_trip``
    enqueues a forecast fetch for its meeting point, and under
    ``ImmediateBackend`` that fetch runs INLINE in a plain trip test. The
    fetch the worker calls is replaced with one that is refused, which the
    worker logs and drops — its real failure path. Patched at the worker's
    import rather than by pointing ``OPEN_METEO_API_BASE_URL`` at a closed
    port, because the Open-Meteo tests assert the shipped host is a free
    one. A test that wants a forecast patches the same name, and wins.
    """

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise requests.ConnectionError("no live Open-Meteo in tests")

    monkeypatch.setattr(
        "apps.trips.services.meeting_weather.fetch_weather_for_location", refuse
    )
