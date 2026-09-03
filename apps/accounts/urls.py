"""
apps/accounts/urls.py — URL configuration for the accounts app.

All URLs are mounted under the ``/account/`` prefix by the root URLconf
(SNOW-430 remounted the app from ``/subscribe/``; a permanent redirect from
``/subscribe/*`` preserves in-flight links and emails).

URL map
-------
/account/                             hub                    301 → /?panel=favourites
/account/settings/                    settings               GET  — settings (authed)
/account/favourites/                  favourites             301 → /?panel=favourites
/account/observations/                observations           301 → /?panel=reports
/account/routes/                      routes                 301 → /?panel=routes
/account/register/                    register               GET/POST — registration
/account/verify/<token>/              verify                 GET/POST — verify email
/account/setup/                       setup                  GET — credential setup
/account/sign-in/                     sign_in                GET/POST — sign-in page
/account/access/<token>/              account                GET/POST — access token
/account/manage/                      manage                 301 → /?panel=favourites
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
from django.views.generic import RedirectView

from . import push_views, views, views_passkey

app_name = "accounts"

urlpatterns = [
    # SNOW-802: the hub — the Subscriptions page — is gone. A subscription
    # was a bookmark on a region, and bookmarks are pins on the map, so the
    # account root 301s to the map with the pins sheet open (``?panel=`` is
    # consumed by static/js/map.js). The name is kept: older emails and
    # bookmarks reverse it, and a redirect is the sensible place to land.
    path(
        "",
        RedirectView.as_view(url="/?panel=favourites", permanent=True),
        name="hub",
    ),
    path("settings/", views.settings_view, name="settings"),
    # SNOW-803: the three account list pages were second renderings of
    # sheets the map already has (docs/decisions/two-documents-and-a-map.md).
    # Each URL stays as a permanent redirect to the map with the matching
    # sheet open — ``?panel=`` is consumed by static/js/map.js — so a
    # bookmark still lands somewhere. Same shape as the ``manage/``
    # redirect below; the names are kept so an old reverse still resolves.
    path(
        "favourites/",
        RedirectView.as_view(url="/?panel=favourites", permanent=True),
        name="favourites",
    ),
    path(
        "observations/",
        RedirectView.as_view(url="/?panel=reports", permanent=True),
        name="observations",
    ),
    path(
        "routes/",
        RedirectView.as_view(url="/?panel=routes", permanent=True),
        name="routes",
    ),
    path("register/", views.register_view, name="register"),
    path("verify/<str:token>/", views.verify_view, name="verify"),
    path("setup/", views.setup_view, name="setup"),
    path("setup/password/", views.set_password_view, name="set_password"),
    path(
        "reset-password/",
        views.reset_password_request_view,
        name="reset_password",
    ),
    path(
        "reset-password/<str:token>/",
        views.reset_password_confirm_view,
        name="reset_password_confirm",
    ),
    path("change-email/", views.change_email_view, name="change_email"),
    path(
        "change-email/<str:token>/",
        views.change_email_confirm_view,
        name="change_email_confirm",
    ),
    path("sign-in/", views.sign_in_view, name="sign_in"),
    path("access/<str:token>/", views.account_view, name="account"),
    # SNOW-667: /account/manage/ was the single stacked page this ticket
    # split up. Kept as a permanent redirect so bookmarks, and the reverses
    # in older emails, still land somewhere sensible. Mirrors the
    # /subscribe/ -> /account/ precedent in config/urls.py (SNOW-430).
    path(
        "manage/",
        RedirectView.as_view(url="/?panel=favourites", permanent=True),
        name="manage",
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
