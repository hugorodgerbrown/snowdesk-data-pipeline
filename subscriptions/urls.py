"""
subscriptions/urls.py — URL configuration for the subscriptions app.

All URLs are mounted under the ``/subscribe/`` prefix by the root URLconf.

URL map
-------
/subscribe/                           subscribe        POST-only HTMX inline form
/subscribe/account/<token>/           account          GET — verify token, activate
/subscribe/manage/                    manage           GET/POST — email entry or mgmt
/subscribe/manage/remove/<region_id>/ remove_region    POST HTMX — remove one region
/subscribe/manage/delete/             delete_account   POST HTMX — hard-delete account
/subscribe/unsubscribe/<token>/       unsubscribe      GET/POST — one-click unsubscribe
/subscribe/unsubscribe-done/          unsubscribe_done GET — confirmation page
"""

from django.urls import path

from . import views

app_name = "subscriptions"

urlpatterns = [
    path("", views.subscribe_partial, name="subscribe"),
    path("account/<str:token>/", views.account_view, name="account"),
    path("manage/", views.manage_view, name="manage"),
    path("manage/remove/<str:region_id>/", views.remove_region, name="remove_region"),
    path("manage/delete/", views.delete_account, name="delete_account"),
    path("unsubscribe/<str:token>/", views.unsubscribe_view, name="unsubscribe"),
    path("unsubscribe-done/", views.unsubscribe_done_view, name="unsubscribe_done"),
]
