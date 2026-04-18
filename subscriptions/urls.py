"""
subscriptions/urls.py — URL configuration for the subscriptions app.

All URLs are mounted under the ``/subscribe/`` prefix by the root URLconf.

URL map
-------
/subscribe/                       subscribe        POST-only HTMX inline form
/subscribe/account/<token>/       account          GET — verify token, activate
/subscribe/manage/                manage           GET/POST — email entry or region mgmt
/subscribe/unsubscribe/<token>/   unsubscribe      GET/POST — one-click unsubscribe
"""

from django.urls import path

from . import views

app_name = "subscriptions"

urlpatterns = [
    path("", views.subscribe_partial, name="subscribe"),
    path("account/<str:token>/", views.account_view, name="account"),
    path("manage/", views.manage_view, name="manage"),
    path("unsubscribe/<str:token>/", views.unsubscribe_view, name="unsubscribe"),
]
