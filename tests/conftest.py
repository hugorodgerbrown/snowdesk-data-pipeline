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

``pytest_configure`` also prepares a ``--testmon`` run (SNOW-1221): it
refuses to start unless ``COVERAGE_CORE=ctrace`` (see
``_require_ctrace_under_testmon``), and it lets testmon read the
extensionless ``bin/`` scripts that ``tests/bin/`` imports (see
``_let_testmon_read_extensionless_scripts``).
"""

from __future__ import annotations

import os
from pathlib import PurePosixPath
from typing import Any, SupportsIndex

import pytest
import requests
import sentry_sdk
from pytest_django.fixtures import Settings

# Test-only password hashing. MD5 is unusable for storing a real credential;
# it is used here because the suite never stores one — every password in the
# test process is a literal written a few lines above the assertion that
# reads it back. See the module docstring for why this is worth doing.
_FAST_PASSWORD_HASHER = "django.contrib.auth.hashers.MD5PasswordHasher"


class _PythonFilename(str):
    """A path whose extension reads as ``py`` — see the function below."""

    __slots__ = ()

    def rsplit(self, sep: str | None = None, maxsplit: SupportsIndex = -1) -> list[str]:
        """Answer testmon's ``rsplit(".", 1)`` as if the file ended ``.py``."""
        if sep == "." and int(maxsplit) == 1:
            return [str(self), "py"]
        return str(self).rsplit(sep, maxsplit)


def _let_testmon_read_extensionless_scripts() -> None:
    """
    Stop pytest-testmon 2.2 crashing on an extensionless Python file.

    ``tests/bin/`` imports scripts such as ``bin/migrations-lint`` and
    ``bin/select-tests`` by path, so coverage records lines in files with no
    extension. testmon's ``SourceTree.get_file`` derives the language from
    ``filename.rsplit(".", 1)[1]`` and raises ``IndexError`` on such a file,
    which ends the whole ``--testmon`` run with INTERNALERROR (exit 3). The
    files are Python, so the shim hands testmon a filename that answers
    that one call with ``py``; every other use of the name is unchanged.

    A no-op when testmon is not installed. Remove it once testmon handles
    extensionless files itself.
    """
    try:
        from testmon.testmon_core import SourceTree
    except ImportError:
        return
    original = SourceTree.get_file
    if getattr(original, "snowdesk_shim", False):
        return

    def get_file(self: Any, filename: str) -> Any:
        """Read an extensionless file as Python; defer to testmon otherwise."""
        if "." not in PurePosixPath(filename).name:
            filename = _PythonFilename(filename)
        return original(self, filename)

    get_file.snowdesk_shim = True  # type: ignore[attr-defined]
    SourceTree.get_file = get_file


TESTMON_OPTIONS = ("testmon", "testmon_noselect", "testmon_forceselect")


def _require_ctrace_under_testmon(config: pytest.Config) -> None:
    """
    Refuse a ``--testmon`` run on coverage's default core.

    On Python 3.12+ coverage measures with ``sys.monitoring``, which stops
    reporting a line once it has fired. testmon switches coverage context
    per test, so every test after the first to run a line is recorded as
    not depending on it. Measured here: a change to ``upsert_weather``
    selected 3 of the tests that call it on the default core and all 39 on
    ``ctrace``. A map built that way silently skips tests, so it is an
    error rather than a warning. CI and tox set ``COVERAGE_CORE=ctrace``.
    """
    if not any(config.getoption(name, default=False) for name in TESTMON_OPTIONS):
        return
    if os.environ.get("COVERAGE_CORE") != "ctrace":
        raise pytest.UsageError(
            "pytest-testmon needs COVERAGE_CORE=ctrace on this Python: the "
            "default sys.monitoring core records each line for one test only, "
            "so the selection would miss tests (SNOW-1221)."
        )


def pytest_configure(config: pytest.Config) -> None:
    """Install the cheap password hasher before any fixture builds a user.

    Set here rather than in an autouse fixture so that it also covers
    session- and class-scoped fixtures, which build their users outside
    any individual test's ``settings`` override.
    """
    from django.conf import settings as django_settings

    django_settings.PASSWORD_HASHERS = [_FAST_PASSWORD_HASHER]
    _require_ctrace_under_testmon(config)
    _let_testmon_read_extensionless_scripts()

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
