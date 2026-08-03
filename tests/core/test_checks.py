"""
tests/core/test_checks.py — Tests for the project-wide system checks.

Exercises ``core.checks`` directly against ``override_settings``
combinations — no full app startup needed. Each check's whole job is to
turn a silent misconfiguration into a failed deploy (SNOW-554 for
``SITE_BASE_URL``, SNOW-585 for ``SW_DEV_SHELL_BYPASS``, SNOW-590 for the
service worker's substitutable ``CACHE_VERSION`` line), so the cases they
must stay quiet for matter as much as the ones they must catch.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.test import override_settings

from apps.core import checks, sw_shell


@override_settings(DEBUG=False, SITE_BASE_URL="http://localhost:8000")
def test_localhost_default_with_debug_off_fails() -> None:
    """The base.py default on a non-debug deploy fails with E001."""
    errors = checks.check_site_base_url(app_configs=None)
    assert len(errors) == 1
    assert errors[0].id == "core.site_base_url.E001"
    assert "http://localhost:8000" in errors[0].msg


@override_settings(DEBUG=False, SITE_BASE_URL="http://127.0.0.1:10000")
def test_loopback_ip_with_debug_off_fails() -> None:
    """A loopback IP on a non-default port fails too — the port isn't the tell."""
    errors = checks.check_site_base_url(app_configs=None)
    assert len(errors) == 1
    assert errors[0].id == "core.site_base_url.E001"


@override_settings(DEBUG=False, SITE_BASE_URL="http://[::1]:8000")
def test_ipv6_loopback_with_debug_off_fails() -> None:
    """The IPv6 loopback literal is recognised as local."""
    errors = checks.check_site_base_url(app_configs=None)
    assert len(errors) == 1
    assert errors[0].id == "core.site_base_url.E001"


@override_settings(DEBUG=False, SITE_BASE_URL="https://snowdesk.info")
def test_real_origin_with_debug_off_passes() -> None:
    """A real https origin produces no errors."""
    assert checks.check_site_base_url(app_configs=None) == []


@override_settings(DEBUG=False, SITE_BASE_URL="https://snowdesk-staging.onrender.com")
def test_staging_origin_passes() -> None:
    """Staging is a legitimately different origin — the check must not police domains."""
    assert checks.check_site_base_url(app_configs=None) == []


@override_settings(DEBUG=True, SITE_BASE_URL="http://localhost:8000")
def test_localhost_default_under_debug_passes() -> None:
    """Local development is exactly what the localhost default is for."""
    assert checks.check_site_base_url(app_configs=None) == []


@override_settings(DEBUG=False, SITE_BASE_URL="snowdesk.info")
def test_missing_scheme_fails() -> None:
    """A bare host with no scheme can't build absolute links — E002."""
    errors = checks.check_site_base_url(app_configs=None)
    assert len(errors) == 1
    assert errors[0].id == "core.site_base_url.E002"
    assert "snowdesk.info" in errors[0].msg


@override_settings(DEBUG=False, SITE_BASE_URL="")
def test_empty_value_fails() -> None:
    """An empty SITE_BASE_URL fails with E002 rather than passing silently."""
    errors = checks.check_site_base_url(app_configs=None)
    assert len(errors) == 1
    assert errors[0].id == "core.site_base_url.E002"


def test_core_app_config_is_coreconfig() -> None:
    """``core`` resolves to ``CoreConfig``, whose ``ready()`` registers the check.

    ``core/apps.py`` also defines ``BootstrapTolerantCSPTrackerConfig``.
    While both classes were candidates for the bare ``"core"`` entry in
    ``INSTALLED_APPS``, Django could not choose and fell back to a plain
    ``AppConfig`` — so ``ready()`` never ran and the check below was
    registered only as a side effect of importing it. Asserting the
    config class is what makes that regression visible; asserting the
    registry alone is not, because this module's own import registers
    the check either way.
    """
    from django.apps import apps

    from apps.core.apps import CoreConfig

    assert isinstance(apps.get_app_config("core"), CoreConfig)


def test_check_is_registered() -> None:
    """The check is wired into Django's registry, not just importable.

    ``CoreConfig.ready()`` is what makes the check run during
    ``manage.py migrate`` on deploy; without registration the module
    above is dead code that every other test in this file would still
    pass against.
    """
    from django.core.checks import registry

    assert checks.check_site_base_url in registry.registry.get_checks()


# ---------------------------------------------------------------------------
# SW_DEV_SHELL_BYPASS release gate (SNOW-585)
# ---------------------------------------------------------------------------


@override_settings(SW_DEV_SHELL_BYPASS=True, DEBUG=False)
def test_dev_shell_bypass_on_with_debug_off_fails() -> None:
    """The dev-only bypass reaching a non-debug deploy is an Error."""
    errors = checks.check_sw_dev_shell_bypass(app_configs=None)
    assert len(errors) == 1
    assert errors[0].id == "core.sw_dev_shell_bypass.E001"


@override_settings(SW_DEV_SHELL_BYPASS=True, DEBUG=True)
def test_dev_shell_bypass_on_with_debug_on_passes() -> None:
    """Local development is exactly what the bypass is for."""
    assert checks.check_sw_dev_shell_bypass(app_configs=None) == []


@override_settings(SW_DEV_SHELL_BYPASS=False, DEBUG=False)
def test_dev_shell_bypass_off_with_debug_off_passes() -> None:
    """The production default (off) never trips the check."""
    assert checks.check_sw_dev_shell_bypass(app_configs=None) == []


@override_settings(SW_DEV_SHELL_BYPASS=False, DEBUG=True)
def test_dev_shell_bypass_off_with_debug_on_passes() -> None:
    """A dev checkout that has explicitly opted back out is also fine."""
    assert checks.check_sw_dev_shell_bypass(app_configs=None) == []


def test_dev_shell_bypass_check_is_registered() -> None:
    """The check is wired into Django's registry, not just importable."""
    from django.core.checks import registry

    assert checks.check_sw_dev_shell_bypass in registry.registry.get_checks()


# ---------------------------------------------------------------------------
# SW CACHE_VERSION substitutability (SNOW-590)
# ---------------------------------------------------------------------------


def test_sw_cache_version_check_passes_on_the_real_tree() -> None:
    """The committed static/js/sw.js is substitutable as shipped."""
    assert checks.check_sw_cache_version_substitutable(app_configs=None) == []


def test_sw_cache_version_check_fails_on_an_unsubstitutable_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sw.js whose assignment was reshaped is an Error, not a silent pass.

    Without this the substitution would be skipped and every client would
    keep one frozen cache name — the SNOW-457 stale-shell regression.
    """
    broken = tmp_path / "sw.js"
    broken.write_text('const CACHE_VERSION = "double-quoted";\n', encoding="utf-8")
    monkeypatch.setattr(sw_shell, "SW_JS_PATH", broken)

    errors = checks.check_sw_cache_version_substitutable(app_configs=None)

    assert len(errors) == 1
    assert errors[0].id == "core.sw_cache_version.E001"


def test_sw_cache_version_check_fails_when_the_file_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing sw.js is reported rather than raising out of the check."""
    monkeypatch.setattr(sw_shell, "SW_JS_PATH", tmp_path / "absent.js")

    errors = checks.check_sw_cache_version_substitutable(app_configs=None)

    assert len(errors) == 1
    assert errors[0].id == "core.sw_cache_version.E002"


def test_sw_cache_version_check_is_registered() -> None:
    """The check is wired into Django's registry, not just importable."""
    from django.core.checks import registry

    assert checks.check_sw_cache_version_substitutable in registry.registry.get_checks()


# ---------------------------------------------------------------------------
# Environment settings spec (SNOW-580)
# ---------------------------------------------------------------------------


def test_settings_spec_check_passes_on_the_real_config() -> None:
    """The committed settings pass their own spec."""
    assert checks.check_settings_spec(app_configs=None) == []


@override_settings(OPEN_METEO_API_BASE_URL="customer-api.open-meteo.com")
def test_settings_spec_check_catches_a_bare_host() -> None:
    """The SNOW-580 incident value now fails at startup, not mid-batch."""
    errors = checks.check_settings_spec(app_configs=None)

    assert [e.id for e in errors] == ["core.settings.E001"]
    assert "no scheme" in errors[0].msg


@override_settings(DEBUG=True, SITE_BASE_URL="")
def test_settings_spec_check_allows_empty_required_settings_under_debug() -> None:
    """A fresh local checkout runs with no .env entries."""
    assert checks.check_settings_spec(app_configs=None) == []


@override_settings(DEBUG=False, WEBAUTHN_ORIGIN="")
def test_settings_spec_check_requires_production_settings_off_debug() -> None:
    """A required setting left empty on a real deploy is an Error."""
    errors = checks.check_settings_spec(app_configs=None)

    assert "core.settings.E002" in {e.id for e in errors}


@override_settings(DEBUG=False, METEOFRANCE_API_LOCAL_MIRROR_URL="file://docs/x")
def test_settings_spec_check_catches_a_relative_file_mirror() -> None:
    """A relative file:// mirror silently reads the wrong directory."""
    errors = checks.check_settings_spec(app_configs=None)

    assert "core.settings.E001" in {e.id for e in errors}


def test_settings_spec_check_is_registered() -> None:
    """The check is wired into Django's registry, not just importable."""
    from django.core.checks import registry

    assert checks.check_settings_spec in registry.registry.get_checks()


# ---------------------------------------------------------------------------
# Open-Meteo key/host pairing (SNOW-580)
# ---------------------------------------------------------------------------


@override_settings(
    DEBUG=False,
    OPEN_METEO_API_BASE_URL="https://customer-api.open-meteo.com/v1",
    OPEN_METEO_ARCHIVE_BASE_URL="https://customer-archive-api.open-meteo.com/v1",
    OPEN_METEO_API_KEY="",
)
def test_open_meteo_customer_host_without_key_fails() -> None:
    """The live incident: customer hosts with no key 401 on every request."""
    errors = checks.check_open_meteo_key_host_pairing(app_configs=None)

    assert [e.id for e in errors] == ["core.open_meteo.E002"]


@override_settings(
    DEBUG=False,
    OPEN_METEO_API_BASE_URL="https://api.open-meteo.com/v1",
    OPEN_METEO_ARCHIVE_BASE_URL="https://archive-api.open-meteo.com/v1",
    OPEN_METEO_API_KEY="sk-live-something",
)
def test_open_meteo_key_on_free_hosts_fails() -> None:
    """A key that is never sent reads as "paid tier" while still on the free quota."""
    errors = checks.check_open_meteo_key_host_pairing(app_configs=None)

    assert [e.id for e in errors] == ["core.open_meteo.E001"]


@override_settings(
    DEBUG=False,
    OPEN_METEO_API_BASE_URL="https://api.open-meteo.com/v1",
    OPEN_METEO_ARCHIVE_BASE_URL="https://archive-api.open-meteo.com/v1",
    OPEN_METEO_API_KEY="",
)
def test_open_meteo_free_hosts_without_key_passes() -> None:
    """The free tier, correctly configured, is silent."""
    assert checks.check_open_meteo_key_host_pairing(app_configs=None) == []


@override_settings(
    DEBUG=False,
    OPEN_METEO_API_BASE_URL="https://customer-api.open-meteo.com/v1",
    OPEN_METEO_ARCHIVE_BASE_URL="https://archive-api.open-meteo.com/v1",
    OPEN_METEO_API_KEY="sk-live-something",
)
def test_open_meteo_split_tier_with_key_passes() -> None:
    """One host moved to the paid tier with a key set is a supported shape.

    SNOW-579 sends the key only to a host that has been moved off its free
    default, so a split configuration is legitimate rather than a mistake.
    """
    assert checks.check_open_meteo_key_host_pairing(app_configs=None) == []


@override_settings(
    DEBUG=True,
    OPEN_METEO_API_BASE_URL="https://customer-api.open-meteo.com/v1",
    OPEN_METEO_API_KEY="",
)
def test_open_meteo_pairing_is_skipped_under_debug() -> None:
    """Local development runs whatever combination it likes."""
    assert checks.check_open_meteo_key_host_pairing(app_configs=None) == []


def test_open_meteo_pairing_check_is_registered() -> None:
    """The check is wired into Django's registry, not just importable."""
    from django.core.checks import registry

    assert checks.check_open_meteo_key_host_pairing in registry.registry.get_checks()
