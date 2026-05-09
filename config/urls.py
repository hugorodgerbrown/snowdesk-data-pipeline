"""
config/urls.py — Root URL configuration.

Mounts the Django admin, the subscriptions flow under /subscribe/, the JSON
API under /api/, the django-csp-plus report endpoint under /csp/, and the
public-facing bulletin site at the root.

The ``/sw.js`` and ``/manifest.webmanifest`` routes are registered before
``public.urls`` so the generic ``<str:region_id>/`` pattern in public.urls
does not swallow them.

When ``settings.DEBUG`` is true, the development-only mirrors are mounted:

- ``/dev/slf-mirror/`` — SLF CAAML bulletin-list mirror, so
  ``fetch_bulletins --source local-mirror`` can replay the on-disk archive
  end-to-end. Routes have the ``dev`` namespace.
- ``/dev/openmeteo-mirror/`` — Open-Meteo weather mirror, so
  ``fetch_weather --source local-mirror`` and ``backfill_weather --source
  local-mirror`` can replay ``sample_data/openmeteo_archive.ndjson``. Routes
  have the ``dev_om`` namespace.

Both mirrors are in ``bulletins.dev_urls``; each mount gets its own
namespace so ``reverse()`` calls are unambiguous. Production never imports
``bulletins.dev_urls``.
"""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from public.views import serve_manifest, serve_sw

urlpatterns = [
    path("admin/", admin.site.urls),
    path("subscribe/", include("subscriptions.urls")),
    path("api/", include("public.api_urls")),
    path("csp/", include("csp.urls")),
    path("sw.js", serve_sw, name="service_worker"),
    path("manifest.webmanifest", serve_manifest, name="web_manifest"),
]

# Dev-only routes must register BEFORE ``public.urls`` because that
# include's generic ``<str:region_id>/`` pattern would otherwise swallow
# the prefix. Production never imports ``bulletins.dev_urls``.
if settings.DEBUG:
    urlpatterns.extend(
        [
            path("dev/slf-mirror/", include("bulletins.dev_urls")),
            path(
                "dev/openmeteo-mirror/",
                include(("bulletins.dev_urls", "dev_om")),
            ),
        ]
    )

urlpatterns.append(path("", include("public.urls")))
