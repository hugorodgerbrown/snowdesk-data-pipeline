"""
config/urls.py — Root URL configuration.

Mounts the Django admin, the subscriptions flow under /subscribe/, the JSON
API under /api/, the django-csp-plus report endpoint under /csp/, and the
public-facing bulletin site at the root.

The ``/sw.js``, ``/manifest.webmanifest``, ``/robots.txt``, ``/llms.txt``
and ``/favicon.ico`` routes are registered before ``public.urls`` so the
generic ``<str:region_id>/`` pattern in public.urls does not swallow them.

When ``settings.DEBUG`` is true, the development-only mirrors are mounted:

- ``/dev/slf-mirror/`` — SLF CAAML bulletin-list mirror (``bulletins.dev_urls``,
  namespace ``dev``), so ``fetch_bulletins --source local-mirror`` can replay
  the on-disk archive end-to-end.
- ``/dev/openmeteo-mirror/`` — Open-Meteo weather mirror
  (``bulletins.dev_urls_openmeteo``, namespace ``dev_om``), so ``fetch_weather
  --source local-mirror`` and ``backfill_weather --source local-mirror`` can
  replay ``bulletins/local_mirrors/openmeteo_archive.ndjson``.
- ``/dev/albina-mirror/`` — ALBINA bulletin mirror
  (``bulletins.dev_urls_albina``, namespace ``dev_albina``), so
  ``fetch_bulletins --source albina --local-mirror`` can replay
  ``bulletins/local_mirrors/albina_archive.ndjson``.

The three mirrors live in separate URL modules so Django's namespace-uniqueness
check (``urls.W005``) is satisfied. Production never imports any mirror module.
"""

from django.conf import settings
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path

from public.sitemaps import BulletinSitemap
from public.views import (
    serve_favicon,
    serve_llms_txt,
    serve_manifest,
    serve_robots,
    serve_sw,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("subscribe/", include("subscriptions.urls")),
    path("api/", include("public.api_urls")),
    path("csp/", include("csp.urls")),
    path("sw.js", serve_sw, name="service_worker"),
    path("manifest.webmanifest", serve_manifest, name="web_manifest"),
    path("robots.txt", serve_robots, name="robots"),
    path("llms.txt", serve_llms_txt, name="llms_txt"),
    path("favicon.ico", serve_favicon, name="favicon_ico"),
    path("favicon.ico/", serve_favicon, name="favicon_ico_slash"),
]

# Dev-only routes must register BEFORE ``public.urls`` because that
# include's generic ``<str:region_id>/`` pattern would otherwise swallow
# the prefix. Production never imports these modules.
if settings.DEBUG:
    urlpatterns.extend(
        [
            path("dev/slf-mirror/", include("bulletins.dev_urls")),
            path("dev/openmeteo-mirror/", include("bulletins.dev_urls_openmeteo")),
            path("dev/albina-mirror/", include("bulletins.dev_urls_albina")),
        ]
    )

urlpatterns.append(
    path(
        "sitemap.xml",
        sitemap,
        {"sitemaps": {"bulletins": BulletinSitemap}},
        name="sitemap",
    )
)
# observations/ partials must be registered BEFORE ``public.urls`` because
# the generic ``<str:region_id>/`` catch-all in public.urls would otherwise
# swallow ``partials/report/…`` and resolve "partials" as a region_id.
urlpatterns.append(path("", include("observations.urls")))
urlpatterns.append(path("", include("public.urls")))
