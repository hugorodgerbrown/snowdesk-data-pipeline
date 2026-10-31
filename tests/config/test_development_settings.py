"""
tests/config/test_development_settings.py — development overlay defaults.

``bin/bootstrap-dev-db`` always runs ``fetch_bulletins --source slf albina
meteofrance --local-mirror``, so on a fresh checkout with no ``.env`` the
Météo-France step failed with "--local-mirror requires
settings.METEOFRANCE_API_LOCAL_MIRROR_URL to be configured" (SNOW-1241).
``development.py`` now defaults the setting to the sample committed at
``docs/research/meteofrance/bulletins-2026-05-18/``; these tests pin that
default, and that it stays out of the deployed overlays.

The overlays are reloaded with ``importlib`` against an empty decouple
repository, so a developer's own ``.env`` cannot mask the default under
test. Nothing here calls ``django.setup()``, so the test process's
configured settings are untouched.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType
from urllib.parse import urlsplit

import decouple
import pytest
from decouple import Config, RepositoryEmpty

from apps.core.settings_spec import local_mirror_url

# Shape-valid stubs for the settings the deployed overlays read with no
# default; development.py needs none of them but tolerates them.
STUB_ENV = {
    "SECRET_KEY": "test-only-not-a-real-key",
    "ALLOWED_HOSTS": "example.com",
    "DATABASE_URL": "postgresql://user:pw@db.example:5432/snowdesk",
    "CSRF_TRUSTED_ORIGINS": "https://example.com",
    "SITE_BASE_URL": "https://example.com",
}


def _load(module: str, monkeypatch: pytest.MonkeyPatch, **extra: str) -> ModuleType:
    """Import a settings overlay with only the stub environment visible.

    Args:
        module: Dotted path of the settings module to import.
        monkeypatch: Used to swap decouple's repository and set the env.
        **extra: Additional environment variables for this import.

    Returns:
        The freshly reloaded module.

    """
    monkeypatch.setattr(decouple.config, "config", Config(RepositoryEmpty()))
    monkeypatch.delenv("METEOFRANCE_API_LOCAL_MIRROR_URL", raising=False)
    for key, value in {**STUB_ENV, **extra}.items():
        monkeypatch.setenv(key, value)
    return importlib.reload(importlib.import_module(module))


def test_meteofrance_mirror_defaults_to_the_committed_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no env value the default is a file:/// URI of the sample dir."""
    module = _load("config.settings.development", monkeypatch)
    value = module.METEOFRANCE_API_LOCAL_MIRROR_URL

    assert value.startswith("file:///")
    assert local_mirror_url(value) is None
    directory = Path(urlsplit(value).path)
    assert directory.is_dir()
    assert (directory / "massif-001.xml").is_file()


def test_an_env_value_overrides_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit env value wins, including an explicit empty one."""
    module = _load(
        "config.settings.development",
        monkeypatch,
        METEOFRANCE_API_LOCAL_MIRROR_URL="file:///elsewhere",
    )
    assert module.METEOFRANCE_API_LOCAL_MIRROR_URL == "file:///elsewhere"

    module = _load(
        "config.settings.development",
        monkeypatch,
        METEOFRANCE_API_LOCAL_MIRROR_URL="",
    )
    assert module.METEOFRANCE_API_LOCAL_MIRROR_URL == ""


def test_production_does_not_inherit_the_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default lives in development.py only; base.py stays empty."""
    module = _load("config.settings.production", monkeypatch)

    assert module.METEOFRANCE_API_LOCAL_MIRROR_URL == ""
