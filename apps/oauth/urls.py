"""
apps/oauth/urls.py — URL routing for the oauth application.

Mounted at ``/oauth/`` from ``config/urls.py`` (namespace ``oauth``), beside
mcp-auth's own ``/oauth/`` routes (namespace ``oauth2_provider``). The
consent gate is registered in ``config/urls.py`` directly, ahead of
mcp-auth's route for the same path.
"""

from django.urls import path

from . import views

app_name = "oauth"

urlpatterns = [
    path(
        "apps/<int:application_id>/disconnect/",
        views.disconnect,
        name="disconnect",
    ),
]
