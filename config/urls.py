"""
config/urls.py — Root URL configuration.

Mounts the Django admin, the accounts flow under /subscribe/, the JSON
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
  (``bulletins.dev_urls_openmeteo``, namespace ``dev_om``), so
  ``fetch_weather --local-mirror`` can replay
  ``bulletins/local_mirrors/openmeteo_archive.ndjson``.
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
    serve_llms_full_txt,
    serve_llms_txt,
    serve_manifest,
    serve_robots,
    serve_sw,
    serve_sw_kill,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("subscribe/", include("accounts.urls")),
    # /api/telemetry must be listed BEFORE the public api_urls include so
    # Django resolves it against the analytics namespace rather than
    # falling through to public.api_urls (which does not define it).
    # SNOW-381: first-party telemetry receiver (spec §16).
    path("api/telemetry", include("analytics.urls")),
    path("api/", include("public.api_urls")),
    path("csp/", include("csp.urls")),
    path("sw.js", serve_sw, name="service_worker"),
    # Kill-switch SW (SNOW-373, spec §6.3 Mechanism B). Always present so
    # ops can point ``SW_URL=/sw-kill.js`` in Render env when the real SW
    # needs evicting cohort-wide without a code deploy.
    path("sw-kill.js", serve_sw_kill, name="service_worker_kill"),
    path("manifest.webmanifest", serve_manifest, name="web_manifest"),
    path("robots.txt", serve_robots, name="robots"),
    path("llms.txt", serve_llms_txt, name="llms_txt"),
    path("llms-full.txt", serve_llms_full_txt, name="llms_full_txt"),
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
# favourites/ must likewise be registered BEFORE ``public.urls`` — the
# generic ``<str:region_id>/`` catch-all would otherwise swallow the
# "favourites" prefix and resolve it as a region_id.
urlpatterns.append(path("favourites/", include("favourites.urls")))
urlpatterns.append(path("", include("public.urls")))
