"""
apps/public/templatetags/pwa_splash.py — iOS startup-image link tag.

SNOW-878. Bridges ``apps.public.pwa_splash.splash_links()`` — which reads
the manifest ``bin/build-pwa-splash`` writes — to the ``<head>`` of every
public page.

Usage:

    {% load pwa_splash %}
    {% pwa_splash_links %}

The tag renders ``templates/includes/_pwa_splash.html``, one
``<link rel="apple-touch-startup-image">`` per entry.
"""

from __future__ import annotations

from django import template

from apps.public.pwa_splash import SplashLink, splash_links

register = template.Library()


@register.inclusion_tag("includes/_pwa_splash.html")
def pwa_splash_links() -> dict[str, tuple[SplashLink, ...]]:
    """
    Render the iOS ``apple-touch-startup-image`` links.

    Returns:
        The inclusion-tag context: ``splash_links``, the tuple of
        ``SplashLink`` rows to emit (empty when the manifest is missing,
        in which case the partial renders nothing).

    """
    return {"splash_links": splash_links()}
