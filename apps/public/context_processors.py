"""
apps/public/context_processors.py — Template context processors for the public site.

Exposes site-level settings as template variables so templates can build
absolute URLs without view-layer boilerplate, and injects PWA version
metadata so ``static/js/pwa_version_check.js`` can compare the served
version against the server's declared min-version verdict.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import waffle
from django.conf import settings
from django.utils.functional import SimpleLazyObject

from apps.public.release import release_label
from apps.public.site_environment import PWAEnvironmentIdentity

if TYPE_CHECKING:
    from django.http import HttpRequest


def site_base_url(request: HttpRequest) -> dict[str, Any]:
    """
    Inject ``SITE_BASE_URL`` into every template context.

    Reads ``settings.SITE_BASE_URL`` (configured via ``.env`` / python-decouple,
    defaulting to ``"http://localhost:8000"`` in development) and exposes it as
    ``{{ SITE_BASE_URL }}`` so templates can build absolute URLs for OG/Twitter
    meta tags and other contexts that require a fully-qualified base URL.

    The trailing slash is stripped so templates can concatenate directly:
    ``{{ SITE_BASE_URL }}{% static 'social/og-default.png' %}`` produces a
    well-formed absolute URL without a double slash.

    Args:
        request: The incoming HTTP request (unused — value comes from settings).

    Returns:
        ``{"SITE_BASE_URL": str}`` with the base URL, trailing-slash stripped.

    """
    return {"SITE_BASE_URL": settings.SITE_BASE_URL.rstrip("/")}


def pwa_version(request: HttpRequest) -> dict[str, Any]:
    """
    Inject the PWA build identifier into every template context (SNOW-374).

    Exposes ``APP_VERSION`` (the build the server is serving) so
    ``base.html`` can bake it into a ``<meta>`` tag. The client-side
    version check (``static/js/pwa_version_check.js``) reads that tag at
    page load to know the version the current shell was delivered on,
    then compares it against ``X-App-Version`` on every response.

    ``APP_RELEASE_LABEL`` is the other half of the same subject and is
    deliberately not the same string: the SHA above identifies a build to
    a machine, this names the release to a person ("v24"), and the site
    footer shows it. See ``apps.public.release``.

    SNOW-609 removed the companion ``APP_MIN_VERSION`` value and its
    ``<meta name="pwa-app-min-version">`` tag: the forced-update verdict is
    a server decision (``update_required`` in the ``/api/version`` body),
    not a comparison the client can perform.

    Args:
        request: The incoming HTTP request (unused — value comes from settings).

    Returns:
        ``{"APP_VERSION": str, "APP_RELEASE_LABEL": str}``. An empty
        ``APP_VERSION`` is passed through unchanged — the client treats it
        as "no build declared" and makes the whole version check a no-op.
        An empty label means no release number is configured, and the
        footer omits it rather than rendering a blank.

    """
    return {
        "APP_VERSION": str(getattr(settings, "APP_VERSION", "")),
        "APP_RELEASE_LABEL": release_label(),
    }


def pwa_telemetry(request: HttpRequest) -> dict[str, Any]:
    """
    Inject the PWA telemetry master switch into every template context.

    Exposes ``PWA_TELEMETRY_ENABLED`` (from ``settings``, default True) so
    ``base.html`` can bake it into a ``<meta name="pwa-telemetry-enabled">``
    tag. ``static/js/telemetry.js`` reads that tag at load time and turns
    the whole client pipeline into a no-op when the value is ``"0"`` — no
    event buffering, no ``navigator.sendBeacon``, no ``/api/telemetry``
    POSTs. This is the client half of the operator-level off switch
    documented in ``docs/telemetry-pipeline.md``; the server receiver and
    the §16.2 server-emitted signals gate on the same setting.

    Args:
        request: The incoming HTTP request (unused — value comes from settings).

    Returns:
        ``{"PWA_TELEMETRY_ENABLED": bool}``. The template renders it as
        ``"1"`` / ``"0"`` via the ``yesno`` filter.

    """
    return {
        "PWA_TELEMETRY_ENABLED": bool(getattr(settings, "PWA_TELEMETRY_ENABLED", True)),
    }


def pwa_dev_shell_bypass(request: HttpRequest) -> dict[str, Any]:
    """
    Inject the dev-only shell-cache bypass flag into every template context (SNOW-585).

    Exposes ``SW_DEV_SHELL_BYPASS`` (from ``settings``, default False) so
    ``base.html`` can bake it into a ``<meta name="pwa-dev-shell-bypass">``
    tag, read synchronously at startup by ``static/js/sw_register.js`` and
    ``static/js/pwa_version_check.js`` to suppress the update banner (and
    its ``pwa.sw.update_available`` telemetry) whenever the bypass is
    active — a stale-shell reload is fixed on the first try, so a banner
    telling the developer to reload would be actively misleading. See
    ``docs/decisions/dev-bypasses-the-shell-cache.md``.

    Args:
        request: The incoming HTTP request (unused — value comes from settings).

    Returns:
        ``{"SW_DEV_SHELL_BYPASS": bool}``.

    """
    return {
        "SW_DEV_SHELL_BYPASS": bool(getattr(settings, "SW_DEV_SHELL_BYPASS", False)),
    }


def debug_log_visible(request: HttpRequest) -> dict[str, Any]:
    """
    Inject the debug-trace gate into every template context (SNOW-812).

    Exposes ``debug_log_visible`` so ``base.html`` can decide whether to
    render the debug-log panel and load ``static/js/debug_log.js`` at all.
    A context processor rather than a per-view context key — the panel is
    a universal surface (the map is only its richest producer), and a
    per-view key would have meant editing every public view to add one,
    and forgetting the next one.

    The flag is scoped to the ``GRP_DEBUG`` group (see
    ``apps/core/fixtures/waffle_flags.json``): everyone in it gets the
    toggle, and recording stays a per-device choice made in the panel. For
    everyone else neither the markup nor the recorder reaches the page,
    so every instrumented call site stays an optional-chain no-op.

    **Two guards, and both are load-bearing.** ``docs/feature-flags.md``
    records SNOW-749 deleting a ``download_sync`` flag before merge because
    its gate cost the homepage three queries. This flag gates a surface on
    ``base.html``, so it is read on that same page and has to answer that
    objection rather than repeat it. The first draft did repeat it —
    ``monitor_query_counts`` caught ``home`` at 5 -> 8.

    1. **Lazy.** A context processor runs for every ``render()``, so an
       eager read would bill responses that never render the panel at all:
       the JSON API endpoints returning a partial as ``{"html": ...}``
       (``apps.public.api.region_summary`` and friends) and every HTMX
       fragment. ``SimpleLazyObject`` defers it until a template actually
       reads the variable.
    2. **Anonymous short-circuit.** ``request.user.is_authenticated`` is
       evaluated first and, being ``and``, stops there for a visitor with
       no session — no waffle read, no ``waffle_flag`` row fetch, no
       group join. That is what keeps the homepage at its baseline, since
       waffle's own cache is cold on a first request and, in production,
       is ``DatabaseCache`` anyway: a warm cache trades three model
       queries for a cache-table one, not for none.

    **The trade the short-circuit makes.** It reads the flag as "never
    active for an anonymous request", which is true of the group scoping
    this flag ships with (``GRP_DEBUG``) but would NOT be true if an
    operator set ``everyone = Yes`` on it in the admin. That is deliberate:
    turning this particular flag on for everyone would ship a debug panel
    to every visitor, which is a mistake in its own right rather than a
    configuration worth supporting. Pinned by
    ``tests/public/test_debug_log_panel.py``, so it stays a stated contract
    rather than a silent optimisation.

    Args:
        request: The incoming HTTP request — the flag is per-user.

    Returns:
        ``{"debug_log_visible": SimpleLazyObject}``, truthy exactly when
        the request is authenticated AND the flag is active. Templates read
        it as a plain boolean.

    """
    return {
        "debug_log_visible": SimpleLazyObject(
            lambda: (
                request.user.is_authenticated
                and waffle.flag_is_active(request, "debug_log")
            )
        )
    }


def site_environment(request: HttpRequest) -> dict[str, Any]:
    """
    Inject PWA environment identity into every template context (SNOW-399).

    Reads ``settings.SITE_ENVIRONMENT`` and resolves it to a
    ``PWAEnvironmentIdentity`` whose fields are used by ``base.html`` and
    ``apps.public.views.serve_manifest`` to render a visibly distinct PWA
    install per environment — the manifest ``name``, the ``<title>``
    default, the ``apple-mobile-web-app-title``, the ``apple-touch-icon``
    href, and the ``theme-color`` meta tag all key off it.

    Exposed template variables:

    - ``SITE_ENVIRONMENT`` — the raw setting (e.g. ``"production"``,
      ``"staging"``).
    - ``SITE_NAME_DISPLAY`` — the human-facing brand string (e.g.
      ``"Snowdesk"`` on production, ``"Snowdesk (Staging)"`` on staging).
    - ``PWA_ICON_DIR`` — the ``/static/icons/…/`` prefix under which
      ``base.html`` looks up ``apple-touch-icon-180.png``. Matches the
      icon prefix used by ``serve_manifest`` so the two agree per
      environment.
    - ``PWA_THEME_COLOR`` — hex string used in the ``<meta name="theme-color">``
      tag. Matches ``theme_color`` in the manifest so the OS chrome tint
      and the browser chrome tint match.
    - ``PWA_TITLE_SUFFIX`` — string appended after every ``<title>``
      block (empty on production, ``" — Staging"`` otherwise). Child
      templates already override ``{% block title %}`` with page-specific
      copy; the suffix rides outside the block so every page tab reads as
      staging without requiring each child template to opt in.

    Args:
        request: The incoming HTTP request (unused — identity is derived
            from ``settings.SITE_ENVIRONMENT``, not per-request).

    Returns:
        The five template variables described above.

    """
    identity = PWAEnvironmentIdentity.from_settings()
    return {
        "SITE_ENVIRONMENT": identity.environment,
        "SITE_NAME_DISPLAY": identity.name_display,
        "PWA_ICON_DIR": identity.icon_dir,
        "PWA_THEME_COLOR": identity.theme_color,
        "PWA_TITLE_SUFFIX": identity.title_suffix,
    }
