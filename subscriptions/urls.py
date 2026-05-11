"""
subscriptions/urls.py — URL configuration for the subscriptions app.

All URLs are mounted under the ``/subscribe/`` prefix by the root URLconf.

URL map
-------
/subscribe/                              subscribe              POST-only HTMX form
/subscribe/account/<token>/             account                GET — verify token
/subscribe/manage/                      manage                 GET/POST — mgmt page
/subscribe/manage/remove/<region_id>/   remove_region          POST HTMX
/subscribe/manage/delete/               delete_account         POST HTMX
/subscribe/manage/passkeys/<uuid>/delete/ passkey_delete       POST HTMX
/subscribe/unsubscribe/<token>/         unsubscribe            GET/POST
/subscribe/unsubscribe-done/            unsubscribe_done       GET
/subscribe/webauthn/auth-request/       passkey_auth_request   GET
/subscribe/webauthn/auth-response/      passkey_auth_response  POST
/subscribe/webauthn/register-request/   passkey_reg_request    GET
/subscribe/webauthn/register-response/  passkey_reg_response   POST
"""

from django.urls import path

from . import views, views_passkey

app_name = "subscriptions"

urlpatterns = [
    path("", views.subscribe_partial, name="subscribe"),
    path("account/<str:token>/", views.account_view, name="account"),
    path("manage/", views.manage_view, name="manage"),
    path("manage/remove/<str:region_id>/", views.remove_region, name="remove_region"),
    path("manage/delete/", views.delete_account, name="delete_account"),
    path(
        "manage/passkeys/<str:passkey_uuid>/delete/",
        views_passkey.passkey_delete,
        name="passkey_delete",
    ),
    path("unsubscribe/<str:token>/", views.unsubscribe_view, name="unsubscribe"),
    path("unsubscribe-done/", views.unsubscribe_done_view, name="unsubscribe_done"),
    # WebAuthn / passkey API endpoints
    path(
        "webauthn/auth-request/",
        views_passkey.passkey_auth_request,
        name="passkey_auth_request",
    ),
    path(
        "webauthn/auth-response/",
        views_passkey.passkey_auth_response,
        name="passkey_auth_response",
    ),
    path(
        "webauthn/register-request/",
        views_passkey.passkey_register_request,
        name="passkey_register_request",
    ),
    path(
        "webauthn/register-response/",
        views_passkey.passkey_register_response,
        name="passkey_register_response",
    ),
]
