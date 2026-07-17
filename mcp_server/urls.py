"""
mcp_server/urls.py — URL routing for the mcp_server application.

Included from ``public/api_urls.py`` under ``mcp/``, giving the single
route a final URL of ``POST /api/mcp/`` and reversing as
``api:mcp:endpoint``.
"""

from django.urls import path

from . import views

app_name = "mcp"

urlpatterns = [
    path("", views.mcp_endpoint, name="endpoint"),
]
