"""
config/urls.py — Root URL configuration.

Mounts the Django admin, the subscriptions flow under /subscribe/, the JSON
API under /api/, the django-csp-plus report endpoint under /csp/, and the
public-facing bulletin site at the root.

The ``/sw.js`` route is registered before ``public.urls`` so the generic
``<str:region_id>/`` pattern in public.urls does not swallow it.

When ``settings.DEBUG`` is true, the development-only SLF mirror is
mounted under ``/dev/slf-mirror/`` so ``fetch_bulletins --source
local-mirror`` can replay the on-disk archive end-to-end. The mirror
module is never imported in production.
"""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from public.views import serve_sw

urlpatterns = [
    path("admin/", admin.site.urls),
    path("subscribe/", include("subscriptions.urls")),
    path("api/", include("public.api_urls")),
    path("csp/", include("csp.urls")),
    path("sw.js", serve_sw, name="service_worker"),
]

# Dev-only routes must register BEFORE ``public.urls`` because that
# include's generic ``<str:region_id>/`` pattern would otherwise swallow
# the prefix. Production never imports ``pipeline.dev_urls``.
if settings.DEBUG:
    urlpatterns.append(
        path("dev/slf-mirror/", include("pipeline.dev_urls")),
    )

urlpatterns.append(path("", include("public.urls")))
