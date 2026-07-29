"""
tests/core/test_checks.py — Tests for the SITE_BASE_URL system check.

Exercises ``core.checks.check_site_base_url`` directly against
``override_settings`` combinations — no full app startup needed. The
check's whole job is to turn a silent misconfiguration into a failed
deploy (SNOW-554), so the cases that matter are the two it must stay
quiet for (``DEBUG`` on, real origin) as much as the ones it must catch.
"""

from __future__ import annotations

from django.test import override_settings

from core import checks


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

    from core.apps import CoreConfig

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
