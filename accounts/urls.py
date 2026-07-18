"""
accounts/urls.py — URL configuration for the accounts app.

All URLs are mounted under the ``/account/`` prefix by the root URLconf
(SNOW-430 remounted the app from ``/subscribe/``; a permanent redirect from
``/subscribe/*`` preserves in-flight links and emails).

URL map
-------
/account/                              subscribe              POST-only HTMX form
/account/add/<region_id>/             add_region             POST HTMX (authed)
/account/remove-region/<region_id>/   remove_region_from_bulletin  POST HTMX (authed)
/account/register/                    register               GET/POST — registration
/account/verify/<token>/              verify                 GET/POST — verify email
/account/setup/                       setup                  GET — credential setup
/account/sign-in/                     sign_in                GET/POST — sign-in page
/account/access/<token>/              account                GET — account-access token
/account/manage/                      manage                 GET — authenticated
/account/manage/remove/<region_id>/   remove_region          POST HTMX
/account/manage/delete/               delete_account         POST HTMX
/account/manage/passkeys/<uuid>/delete/ passkey_delete       POST HTMX
/account/sign-out/                    sign_out               POST
/account/unsubscribe/<token>/         unsubscribe            GET/POST
/account/unsubscribe-done/            unsubscribe_done       GET
/account/webauthn/auth-request/       passkey_auth_request   GET
/account/webauthn/auth-response/      passkey_auth_response  POST
/account/webauthn/register-request/   passkey_reg_request    GET
/account/webauthn/register-response/  passkey_reg_response   POST
"""

from django.urls import path

from . import push_views, views, views_passkey

app_name = "accounts"

urlpatterns = [
    path("", views.subscribe_partial, name="subscribe"),
    path("add/<region_id:region_id>/", views.add_region, name="add_region"),
    path(
        "remove-region/<region_id:region_id>/",
        views.remove_region_from_bulletin,
        name="remove_region_from_bulletin",
    ),
    path("register/", views.register_view, name="register"),
    path("verify/<str:token>/", views.verify_view, name="verify"),
    path("setup/", views.setup_view, name="setup"),
    path("sign-in/", views.sign_in_view, name="sign_in"),
    path("access/<str:token>/", views.account_view, name="account"),
    path("manage/", views.manage_view, name="manage"),
    path(
        "manage/remove/<region_id:region_id>/",
        views.remove_region,
        name="remove_region",
    ),
    path("manage/delete/", views.delete_account, name="delete_account"),
    path("sign-out/", views.sign_out, name="sign_out"),
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
    # Web Push (spike)
    path("push/register/", push_views.push_register, name="push_register"),
    path("push/unregister/", push_views.push_unregister, name="push_unregister"),
    path("push/test/", push_views.push_test, name="push_test"),
]
