"""
analytics/apps.py — AppConfig for the analytics application.

Registers the analytics app with Django so it is discovered by the
framework. The app has no models, migrations, or URLs — it exists only
to provide the ``track()`` and ``alias()`` wrappers that fire server-side
events to PostHog.

The global ``posthog`` module-level client is configured here in
``ready()`` so that the middleware (``PosthogContextMiddleware``) and the
``posthog.capture`` / ``posthog.alias`` module-level helpers share the
same initialised client without any import-time side effects.
"""

import logging

import posthog
from django.apps import AppConfig

logger = logging.getLogger(__name__)


class AnalyticsConfig(AppConfig):
    """Django application configuration for the analytics app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "analytics"

    def ready(self) -> None:
        """Initialise the global PostHog module-level client.

        Called once by Django after all apps are loaded. Reads
        ``POSTHOG_API_KEY`` and ``POSTHOG_HOST`` from settings and
        configures the ``posthog`` module globals so that both
        ``posthog.capture()`` / ``posthog.alias()`` module-level calls
        and the ``PosthogContextMiddleware`` share the same client.

        When the key is empty, ``posthog.disabled`` is set to ``True``
        so that all module-level calls become guaranteed no-ops — belt-
        and-braces in addition to ``posthog``'s own auto-disable logic.
        """
        from django.conf import settings

        api_key: str = (getattr(settings, "POSTHOG_API_KEY", "") or "").strip()
        host: str = getattr(settings, "POSTHOG_HOST", "https://eu.i.posthog.com")

        posthog.api_key = api_key
        posthog.host = host
        # Retain PostHog's server-side GeoIP enrichment (country/city from
        # the inbound IP). The raw ``ip`` property is never included in
        # event payloads — enforced by _assert_no_pii in analytics/__init__.py.
        posthog.disable_geoip = False
        # Frame-locals on captured exception tracebacks. PostHog's built-in
        # mask/ignore patterns redact common credential names, but anything
        # in scope at the crash frame is still shipped — gated on the
        # POSTHOG_CAPTURE_EXCEPTION_CODE_VARIABLES env var so it can be
        # turned off per-environment if needed.
        posthog.capture_exception_code_variables = getattr(
            settings, "POSTHOG_CAPTURE_EXCEPTION_CODE_VARIABLES", True
        )

        if not api_key:
            posthog.disabled = True
            logger.debug("PostHog disabled (POSTHOG_API_KEY not set)")
        else:
            posthog.disabled = False
            logger.debug("PostHog initialised (host=%s)", host)
