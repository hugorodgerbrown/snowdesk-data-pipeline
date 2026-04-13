"""
subscriptions/urls.py — URL configuration for the subscriptions app.

Routes magic-link subscription and verification views. All URLs are mounted
under the ``/subscribe/`` prefix by the root URLconf. The ``partials/``
sub-prefix holds HTMX-only fragment endpoints.
"""

from django.urls import path

from . import views

app_name = "subscriptions"

urlpatterns = [
    path("", views.enter_email, name="enter_email"),
    path("sent/", views.email_sent, name="email_sent"),
    path("verify/", views.verify_token, name="verify_token"),
    path("regions/", views.pick_regions, name="pick_regions"),
    path("manage/", views.manage_regions, name="manage_regions"),
    path("confirmed/", views.confirmed, name="confirmed"),
    path(
        "partials/region-search/",
        views.region_search_partial,
        name="region_search_partial",
    ),
]
