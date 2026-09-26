"""
apps/oauth/urls.py — URL routing for the oauth application.

Mounted at ``/oauth/`` from ``config/urls.py`` (namespace ``oauth``). The two
``/.well-known/`` discovery documents live at the root and are registered in
``config/urls.py`` directly, ahead of the ``apps.public.urls`` catch-all.
"""

from django.urls import path

from . import views

app_name = "oauth"

urlpatterns = [
    path("authorize/", views.authorize, name="authorize"),
    path("token/", views.token, name="token"),
    path("register/", views.register, name="register"),
    path("revoke/", views.revoke, name="revoke"),
    path(
        "grants/<uuid:grant_uuid>/revoke/",
        views.grant_revoke,
        name="grant_revoke",
    ),
]
