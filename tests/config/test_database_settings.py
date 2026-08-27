"""
tests/config/test_database_settings.py — connection-persistence settings.

The deployed settings overlays had no test coverage at all, so the pairing
that matters here was never guarded: ``CONN_MAX_AGE`` without
``CONN_HEALTH_CHECKS`` reuses a pooled Postgres connection for up to ten
minutes without checking it is still open. When the server closes one out of
band, the next query fails *mid-flight* rather than at connect time —
``OperationalError: consuming input failed: SSL error: unexpected eof`` —
and Django then discards the connection, so the request after it succeeds
and the incident reads as an unexplained blip. Production ``/healthz``
returned 503 that way on 2026-08-27 (SNOW-733).

The two settings are only meaningful together, which is exactly what makes
the omission easy to reintroduce while tuning one of them. These tests
assert the pairing per environment rather than the individual values:
production keeps connections and therefore must health-check them; staging
keeps none and therefore need not.

The overlays are imported directly with ``importlib`` under stub
environment variables. Nothing here calls ``django.setup()`` — the modules
only build dictionaries at import time — so this does not disturb the test
process's own configured settings.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

import decouple
import pytest
from decouple import Config, RepositoryEmpty, UndefinedValueError

# Every setting the deployed overlays read with **no default** — importing
# them fails outright without these. The values are shape-valid but point
# nowhere; nothing here opens a connection.
#
# The list is short because almost everything else carries a default. Keep it
# complete: a `config("X")` with no default added to base.py or production.py
# has to appear here too, or these tests fail on a CI runner while passing on
# any machine with a .env — which is exactly how CSRF_TRUSTED_ORIGINS was
# missed the first time.
STUB_ENV = {
    "SECRET_KEY": "test-only-not-a-real-key",
    "ALLOWED_HOSTS": "example.com",
    "DATABASE_URL": "postgresql://user:pw@db.example:5432/snowdesk",
    "CSRF_TRUSTED_ORIGINS": "https://example.com",
    # Has a default, but a localhost one that check_site_base_url rejects
    # under DEBUG=False. Pinned so the overlay is exercised as deployed.
    "SITE_BASE_URL": "https://example.com",
}


def _load(module: str, monkeypatch: pytest.MonkeyPatch, **extra: str) -> ModuleType:
    """Import a settings overlay in isolation.

    Args:
        module: Dotted path of the settings module to import.
        monkeypatch: Used to set the stub environment for the import.
        **extra: Additional environment variables for this overlay.

    Returns:
        The freshly imported module.

    """
    # Read settings the way a deploy does: environment only. python-decouple
    # otherwise falls back to the repo's .env, so a value missing from
    # STUB_ENV resolves on a developer machine and raises only on a bare CI
    # runner — which is precisely how CSRF_TRUSTED_ORIGINS slipped through a
    # green local `tox`. Swapping in an empty repository makes local and CI
    # the same test. monkeypatch restores the real one afterwards.
    monkeypatch.setattr(decouple.config, "config", Config(RepositoryEmpty()))

    for key, value in {**STUB_ENV, **extra}.items():
        monkeypatch.setenv(key, value)
    try:
        return importlib.reload(importlib.import_module(module))
    except UndefinedValueError as exc:  # pragma: no cover - guard rail
        # With the .env fallback gone this now fires identically everywhere,
        # so name the culprit rather than leaving a raw decouple traceback.
        raise AssertionError(
            f"{module} needs an environment variable STUB_ENV does not set "
            f"({exc}). Add it to STUB_ENV in this module."
        ) from exc


def _default_db(module: ModuleType) -> dict[str, Any]:
    """Return the ``default`` database config from a settings module."""
    config: dict[str, Any] = module.DATABASES["default"]
    return config


def test_production_health_checks_its_persistent_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production keeps connections, so it must verify them before reuse.

    Without this, one server-side disconnect costs a request — and on
    ``/healthz`` it reports the whole service unavailable to whatever is
    monitoring it.
    """
    config = _default_db(_load("config.settings.production", monkeypatch))

    assert config["CONN_MAX_AGE"] == 600
    assert config["CONN_HEALTH_CHECKS"] is True


def test_staging_inherits_the_same_checked_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Staging is exposed to this too, despite appearances.

    ``staging.py`` does ``from .production import *`` and never overrides
    ``DATABASES["default"]``, so it inherits production's persistent
    connection wholesale. The ``conn_max_age=0`` visible in that module
    belongs to the separate read-only ``production`` alias it registers for
    ``sync_from_production`` — a different connection entirely.

    That misreading is the whole reason this test exists: staging looked
    immune while sharing the defect.
    """
    module = _load("config.settings.staging", monkeypatch, SITE_ENVIRONMENT="staging")

    assert _default_db(module)["CONN_MAX_AGE"] == 600
    assert _default_db(module)["CONN_HEALTH_CHECKS"] is True


def test_a_persistent_connection_is_never_left_unchecked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Across both deployed overlays: keep connections ⇒ health-check them.

    The pairing is the actual invariant. Asserting it directly means a
    future overlay, or a change to an existing one, cannot reintroduce the
    combination that caused SNOW-733 while still passing the per-module
    tests above.
    """
    overlays = {
        "config.settings.production": {},
        "config.settings.staging": {"SITE_ENVIRONMENT": "staging"},
    }

    for module, extra in overlays.items():
        config = _default_db(_load(module, monkeypatch, **extra))
        if config["CONN_MAX_AGE"]:
            assert config["CONN_HEALTH_CHECKS"] is True, (
                f"{module} reuses connections for {config['CONN_MAX_AGE']}s "
                "but does not health-check them"
            )
