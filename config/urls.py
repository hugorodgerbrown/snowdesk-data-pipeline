"""
config/urls.py — Root URL configuration.

Mounts the Django admin, the accounts flow under /account/ (with a permanent
redirect from the legacy /subscribe/ prefix), the JSON API under /api/, the
django-csp-plus report endpoint under /csp/, and the public-facing bulletin
site at the root.

``/livez`` and ``/healthz`` (SNOW-565) are the infrastructure health checks,
registered ahead of everything else so ``APPEND_SLASH`` cannot hand a probe
to the ``<region_id:region_id>/`` catch-all. ``/livez`` is the path wired to
Render's ``healthCheckPath``; see ``apps/core/views.py`` for why the two are
separate.

The ``/sw.js``, ``/manifest.webmanifest``, ``/robots.txt``, ``/llms.txt``
and ``/favicon.ico`` routes are registered before ``apps.public.urls`` so the
generic ``<str:region_id>/`` pattern in apps.public.urls does not swallow them.

When ``settings.DEBUG`` is true, the development-only mirrors are mounted:

- ``/dev/slf-mirror/`` — SLF CAAML bulletin-list mirror (``apps.bulletins.dev_urls``,
  namespace ``dev``), so ``fetch_bulletins --source local-mirror`` can replay
  the on-disk archive end-to-end.
- ``/dev/openmeteo-mirror/`` — Open-Meteo weather mirror
  (``apps.weather.dev_urls``, namespace ``dev_om``), so
  ``fetch_weather --local-mirror`` can replay
  ``apps/weather/local_mirrors/openmeteo_archive.ndjson``.
- ``/dev/albina-mirror/`` — ALBINA bulletin mirror
  (``apps.bulletins.dev_urls_albina``, namespace ``dev_albina``), so
  ``fetch_bulletins --source albina --local-mirror`` can replay
  ``apps/bulletins/local_mirrors/albina_archive.ndjson``.

The three mirrors live in separate URL modules so Django's namespace-uniqueness
check (``urls.W005``) is satisfied. Production never imports any mirror module.
"""

from django.conf import settings
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path, re_path
from django.views.generic import RedirectView

from apps.core.views import healthz, livez
from apps.public.sitemaps import BulletinSitemap
from apps.public.views import (
    serve_favicon,
    serve_llms_full_txt,
    serve_llms_txt,
    serve_manifest,
    serve_robots,
    serve_sw,
    serve_sw_kill,
)

urlpatterns = [
    # Health checks (SNOW-565). Registered first, ahead of every other
    # pattern: ``APPEND_SLASH`` would otherwise redirect a probe of
    # ``/healthz`` to ``/healthz/`` and hand it to the generic
    # ``<region_id:region_id>/`` catch-all in apps.public.urls.
    path("livez", livez, name="livez"),
    path("healthz", healthz, name="healthz"),
    path("admin/", admin.site.urls),
    path("account/", include("apps.accounts.urls")),
    # SNOW-430: the accounts app moved from /subscribe/ to /account/. Redirect
    # the legacy prefix permanently so in-flight account-access and unsubscribe
    # email links keep working. The account-access token route was also renamed
    # (account/<token>/ -> access/<token>/), so it needs its own mapping ahead
    # of the generic catch-all.
    re_path(
        r"^subscribe/account/(?P<token>[^/]+)/?$",
        RedirectView.as_view(url="/account/access/%(token)s/", permanent=True),
    ),
    re_path(
        r"^subscribe/(?P<rest>.*)$",
        RedirectView.as_view(
            url="/account/%(rest)s", permanent=True, query_string=True
        ),
    ),
    # /api/telemetry must be listed BEFORE the public api_urls include so
    # Django resolves it against the analytics namespace rather than
    # falling through to apps.public.api_urls (which does not define it).
    # SNOW-381: first-party telemetry receiver (spec §16).
    path("api/telemetry", include("apps.analytics.urls")),
    path("api/", include("apps.public.api_urls")),
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

# Dev-only routes must register BEFORE ``apps.public.urls`` because that
# include's generic ``<str:region_id>/`` pattern would otherwise swallow
# the prefix. Production never imports these modules.
if settings.DEBUG:
    urlpatterns.extend(
        [
            path("dev/slf-mirror/", include("apps.bulletins.dev_urls")),
            path("dev/openmeteo-mirror/", include("apps.weather.dev_urls")),
            path("dev/albina-mirror/", include("apps.bulletins.dev_urls_albina")),
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
# observations/ partials must be registered BEFORE ``apps.public.urls`` because
# the generic ``<str:region_id>/`` catch-all in apps.public.urls would otherwise
# swallow ``partials/report/…`` and resolve "partials" as a region_id.
urlpatterns.append(path("", include("apps.observations.urls")))
# favourites/ must likewise be registered BEFORE ``apps.public.urls`` — the
# generic ``<str:region_id>/`` catch-all would otherwise swallow the
# "favourites" prefix and resolve it as a region_id.
urlpatterns.append(path("favourites/", include("apps.favourites.urls")))
urlpatterns.append(path("", include("apps.public.urls")))
