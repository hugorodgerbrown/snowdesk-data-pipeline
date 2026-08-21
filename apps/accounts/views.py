"""
apps/accounts/views.py — HTTP views for the accounts application.

Implements the subscription flow built around Django's TimestampSigner:

  sign_in_view        GET/POST — dedicated sign-in page (email entry / passkey).
                               POST: rate-limited (3/m per IP); sends magic link.
  register_view       GET/POST — standalone registration page (email required,
                               name optional). POST: rate-limited (3/m per IP);
                               sends an email-verification link (SNOW-430).
  verify_view         GET/POST — confirm an email-verification token. GET shows
                               a confirm button (no state change); POST marks the
                               Account verified, logs in, redirects to setup.
  setup_view          GET  — post-verification credential-setup landing page.
  set_password_view   POST — set a password for the logged-in user (SNOW-431).
  reset_password_request_view
                      GET/POST — request a password reset by email (SNOW-432).
  reset_password_confirm_view
                      GET/POST — set a new password from a reset link (SNOW-432).
  change_email_view   GET/POST — request an account email change (SNOW-433).
  change_email_confirm_view
                      GET/POST — confirm + apply an email change (SNOW-433).
  subscribe_partial   POST — inline HTMX subscribe CTA on bulletin pages.
                            Requires a region_id; uses a four-case matrix keyed
                            on (account_created, subscription_created) and
                            account.is_verified to decide which email to send
                            and which fragment to return.
  add_region          POST — HTMX: authenticated one-click add of a region from
                            the bulletin page. Idempotent; no email sent.
  remove_region_from_bulletin
                      POST — HTMX: authenticated one-click unsubscribe from the
                            bulletin page. Mirrors remove_region cascade logic.
  account_view        GET/POST — account-access ("magic link") token. GET shows
                            a confirm button (no state change, no login); POST
                            verifies the Account, logs in via Django auth, and
                            redirects to /account/manage/.
  manage_view         GET  — authenticated "your subscriptions" page.
                            Unauthenticated requests redirect to /sign-in/.
  remove_region       POST — HTMX: remove one subscribed region card.
  delete_account      POST — HTMX: hard-delete the account and redirect to done.
  unsubscribe_view    GET/POST — token-verified one-click unsubscribe.

Rate limiting via django-ratelimit (block=False pattern):
  subscribe_partial:  5 requests/min per IP.
  add_region:         5 requests/min per IP.
  sign_in_view POST:  3 requests/min per IP.
  remove_region POST: 10 requests/min per IP.
  remove_region_from_bulletin POST: 10 requests/min per IP.
  delete_account POST: 3 requests/min per IP.
  unsubscribe_view: 10 requests/min per IP.

Authentication uses Django's standard session auth (request.user).  After
a token is verified in account_view or passkey authentication completes in
views_passkey.py, django.contrib.auth.login() establishes the session.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import waffle
from django.conf import settings
from django.contrib.auth import (
    authenticate,
    get_user_model,
    login,
    logout,
    update_session_auth_hash,
)
from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django_ratelimit.core import get_usage
from django_ratelimit.decorators import ratelimit

from apps import analytics
from apps.core.decorators import require_htmx
from apps.core.services.request_log import capture as capture_request_log
from apps.public.decorators import lowercase_region_id
from apps.regions.models import MicroRegion
from apps.regions.services.point_match import IN_NEIGHBOUR, IN_REGION

from .forms import (
    ChangeEmailForm,
    EmailForm,
    PasswordSignInForm,
    RegisterForm,
    SnowdeskSetPasswordForm,
    SubscribeForm,
)
from .identity import user_identity
from .logging_utils import mask_email
from .models import Account, Subscription
from .services.email import (
    send_account_access_email,
    send_email_change_confirmation,
    send_email_change_notice,
    send_password_reset_email,
    send_subscription_confirmation_email,
    send_verification_email,
)
from .services.request_context import geo_match_snapshot
from .services.token import (
    SALT_ACCOUNT_ACCESS,
    SALT_EMAIL_VERIFICATION,
    generate_unsubscribe_token,
    verify_email_change_token,
    verify_password_reset_token,
    verify_token,
    verify_unsubscribe_token,
)
from .subnav import build_subnav

if TYPE_CHECKING:
    from django.contrib.auth.models import User as UserType

logger = logging.getLogger(__name__)

User = get_user_model()

# The backend used when calling login() after token/passkey verification.
_TOKEN_BACKEND = "apps.accounts.backends.TokenBackend"  # noqa: S105 — backend path, not a password

# Template for the generic link-expired / bad-token error page.
_LINK_EXPIRED_TEMPLATE = "accounts/link_expired.html"

# Referrer-Policy for token-bearing pages. Both values keep the secret token in
# the URL path from leaking to third parties; the difference is same-origin
# behaviour:
#   - ``no-referrer`` — strips Referer/Origin from *every* request, including
#     same-origin. Correct for terminal responses (error pages, redirects, done
#     pages) that carry no POST form.
#   - ``same-origin`` — sends a valid Origin/Referer for same-origin requests but
#     nothing cross-origin. **Required** on any GET page that renders a
#     same-origin POST form: under ``no-referrer`` the browser sends
#     ``Origin: null`` on the POST, which Django's CSRF middleware rejects with
#     403 on HTTPS (invisible on local HTTP, where the Origin check is skipped).
#     See SNOW-438.
_REFERRER_NO_REFERRER = "no-referrer"
_REFERRER_CONFIRM_PAGE = "same-origin"

# URL path for the account hub — used in redirects that append a query
# string. SNOW-667 moved this off /account/manage/, which is now a 301 to
# the hub; pointing at the redirect would cost a needless hop and drop
# nothing but time.
_HUB_URL = "/account/"

# URL for the unsubscribe-done page — used in HX-Redirect headers.
_UNSUBSCRIBE_DONE_URL = "/account/unsubscribe-done/"


def _get_account(request: HttpRequest) -> Account | None:
    """Return the authenticated Account profile from request.user, or None.

    Returns None for anonymous users and for authenticated staff users who have
    no Account profile (e.g. superusers created via createsuperuser).
    """
    if not request.user.is_authenticated:
        return None
    try:
        return request.user.account
    except Account.DoesNotExist:
        return None


# ---------------------------------------------------------------------------
# sign_in_view — dedicated sign-in page
# ---------------------------------------------------------------------------


def _password_sign_in(request: HttpRequest) -> HttpResponse | None:
    """Attempt password sign-in when a password is supplied (SNOW-431).

    A wrong password, an unknown email, and an account with no usable password
    all fail identically (``authenticate`` returns None), so the generic error
    leaks no account-existence signal.

    Args:
        request: The current POST request.

    Returns:
        A redirect to manage on success, the sign-in page with a generic error
        on failure, or ``None`` when no password was supplied (the caller then
        continues with the magic-link flow).

    """
    if not request.POST.get("password"):
        return None

    pw_form = PasswordSignInForm(request.POST)
    if pw_form.is_valid():
        user = authenticate(
            request,
            username=pw_form.cleaned_data["email"],
            password=pw_form.cleaned_data["password"],
        )
        if user is not None:
            login(request, user)
            return redirect("accounts:hub")

    return render(
        request,
        "accounts/sign_in.html",
        {"form": EmailForm(), "password_error": True},
        status=200,
    )


@require_http_methods(["GET", "POST"])
def sign_in_view(request: HttpRequest) -> HttpResponse:
    """
    Dedicated sign-in page for returning subscribers.

    GET: render the email entry form with passkey conditional UI.
    If the user is already authenticated, redirect to the manage page.

    POST (rate-limited 3/m per IP): with a password supplied, attempt password
    sign-in (SNOW-431) — success redirects to manage, failure re-renders with a
    generic error. Without a password, the magic-link flow runs and always
    returns the same "check your inbox" response regardless of whether the
    email is known.

    Args:
        request: Incoming HTTP request.

    Returns:
        Rendered sign-in page or redirect.

    """
    if request.user.is_authenticated:
        return redirect("accounts:hub")

    if request.method == "GET":
        return render(request, "accounts/sign_in.html", {"form": EmailForm()})

    # POST — rate-limit then send (or noop).
    usage = get_usage(
        request,
        group="accounts.sign_in.post",
        key="ip",
        rate="3/m",
        method=["POST"],
        increment=True,
    )
    if usage is not None and usage["should_limit"]:
        return HttpResponse(status=429)

    # Password sign-in branch (SNOW-431) — returns a response when a password
    # was supplied, else None to fall through to the magic-link flow.
    pw_response = _password_sign_in(request)
    if pw_response is not None:
        return pw_response

    form = EmailForm(request.POST)
    if not form.is_valid():
        return render(request, "accounts/sign_in.html", {"form": form})

    email: str = form.cleaned_data["email"]

    # Capture request context before creating / fetching the account so
    # first-observation wins on acquisition_request.
    req_log = capture_request_log(request)

    account, created = Account.objects.get_or_create_for_email(
        email,
        defaults={"acquisition_request": req_log},
    )
    send_account_access_email(email, request=request)
    logger.info(
        "Account-access email sent to account pk=%s via sign-in page", account.pk
    )

    sign_in_props: dict[str, object] = {}
    if req_log.country_code:
        sign_in_props["country_code"] = req_log.country_code
    analytics.track("sign_in_requested", str(account.uuid), sign_in_props)

    return render(request, "accounts/manage_sent.html", {})


# ---------------------------------------------------------------------------
# register_view — standalone registration page (SNOW-430)
# ---------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def register_view(request: HttpRequest) -> HttpResponse:
    """
    Standalone registration page: email required, name optional.

    Creates (or reuses) an ``auth.User`` and an ``Account`` profile — no
    ``Subscription`` rows.  Submitting sends an email-verification link
    asynchronously; the account only becomes verified when that link is
    confirmed (``verify_view``).

    Anti-enumeration: the response is identical whether the email is new,
    already registered-and-unverified, or already verified.  A verified
    account receives **no** second email (nothing to verify); an unverified
    one (including accounts first created via the subscribe flow) has its
    link re-sent.

    POST is rate-limited to 3/min per IP.

    Args:
        request: Incoming HTTP request.

    Returns:
        Rendered registration page, the "check your inbox" page, or a redirect
        to manage when already authenticated.

    """
    if request.user.is_authenticated:
        return redirect("accounts:hub")

    if request.method == "GET":
        return render(request, "accounts/register.html", {"form": RegisterForm()})

    # POST — rate-limit then create-or-reuse and (maybe) send.
    usage = get_usage(
        request,
        group="accounts.register.post",
        key="ip",
        rate="3/m",
        method=["POST"],
        increment=True,
    )
    if usage is not None and usage["should_limit"]:
        return HttpResponse(status=429)

    form = RegisterForm(request.POST)
    if not form.is_valid():
        return render(request, "accounts/register.html", {"form": form})

    email: str = form.cleaned_data["email"]
    name: str = form.cleaned_data.get("name", "")

    account, _created = Account.objects.get_or_create_for_email(
        email,
        defaults={"display_name": name} if name else None,
    )
    # Backfill a display name onto an existing account that had none.
    if name and not account.display_name:
        account.display_name = name
        account.save(update_fields=["display_name", "updated_at"])

    if account.is_verified:
        # Already verified — nothing to verify, and sending an email would
        # leak that the address exists.  Return the same response anyway.
        # (The response body is byte-identical; a residual timing signal from
        # skipping the enqueue is mitigated by the 3/min per-IP rate limit,
        # consistent with the sign-in/subscribe enumeration posture.)
        logger.info(
            "Registration for already-verified account pk=%s — no email sent",
            account.pk,
        )
    else:
        send_verification_email(email, request=request)
        logger.info("Verification email sent for account pk=%s", account.pk)

    return render(request, "accounts/register_sent.html", {})


# ---------------------------------------------------------------------------
# verify_view — confirm an email-verification token (SNOW-430)
# ---------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def verify_view(request: HttpRequest, token: str) -> HttpResponse:
    """
    Verify an email-verification token from the registration email.

    GET renders a confirm page with a single button — it performs **no**
    state change, so link-prefetch scanners cannot silently verify an
    account.  POST marks the ``Account`` verified, logs the user in, and
    redirects to the credential-setup page.

    On a bad, tampered, or expired token (or an unknown email) renders the
    generic ``link_expired`` page (400).

    Args:
        request: Incoming HTTP request.
        token: The signed verification token from the URL path.

    Returns:
        Confirm page (GET), redirect to setup (POST), or link-expired page.

    """
    max_age = getattr(settings, "ACCOUNT_TOKEN_MAX_AGE", 86400)
    email = verify_token(token, salt=SALT_EMAIL_VERIFICATION, max_age=max_age)

    if email is None:
        logger.debug("verify_view received an invalid/expired token")
        response = render(request, _LINK_EXPIRED_TEMPLATE, {}, status=400)
        response["Referrer-Policy"] = _REFERRER_NO_REFERRER
        return response

    try:
        user = User.objects.get(username=email.lower())
    except User.DoesNotExist:
        logger.warning(
            "verify_view: valid token for unknown email %s", mask_email(email)
        )
        response = render(request, _LINK_EXPIRED_TEMPLATE, {}, status=400)
        response["Referrer-Policy"] = _REFERRER_NO_REFERRER
        return response

    if request.method == "GET":
        response = render(request, "accounts/verify.html", {"token": token})
        response["Referrer-Policy"] = _REFERRER_CONFIRM_PAGE
        return response

    # POST — perform the verification. Create the Account already-verified so a
    # freshly-inserted row is never briefly visible as unverified; only an
    # existing row needs the follow-up UPDATE.
    now = timezone.now()
    account, created = Account.objects.get_or_create(
        user=user,
        defaults={"is_verified": True, "verified_at": now},
    )
    if not created:
        account.mark_verified(now)
        account.save(update_fields=["is_verified", "verified_at", "updated_at"])
    login(request, user, backend=_TOKEN_BACKEND)
    logger.info("Account pk=%s verified via registration link", account.pk)

    response = redirect("accounts:setup")
    response["Referrer-Policy"] = _REFERRER_NO_REFERRER
    return response


# ---------------------------------------------------------------------------
# setup_view — post-verification credential-setup landing (SNOW-430)
# ---------------------------------------------------------------------------


def _render_setup(
    request: HttpRequest,
    *,
    password_form: SnowdeskSetPasswordForm | None = None,
    status: int = 200,
) -> HttpResponse:
    """Render the credential-setup page with a (bound or fresh) password form.

    Shared by ``setup_view`` (fresh form) and ``set_password_view`` (bound
    form carrying validation errors).

    Args:
        request: The current request (its authenticated user seeds the form).
        password_form: A bound form to re-render with errors, or None to build
            a fresh unbound form.
        status: HTTP status code for the response.

    Returns:
        The rendered setup page.

    """
    form = password_form or SnowdeskSetPasswordForm(request.user)
    return render(
        request, "accounts/setup.html", {"password_form": form}, status=status
    )


@require_GET
def setup_view(request: HttpRequest) -> HttpResponse:
    """
    Post-verification "finish setup" landing page.

    Reached after ``verify_view`` logs the user in.  Offers an optional
    "set a password" card (SNOW-431); SNOW-434 adds a passkey card.  Neither
    is a gate — the user can continue to their account either way.
    Unauthenticated visitors are redirected to sign-in.

    Args:
        request: Incoming GET request.

    Returns:
        Rendered setup page, or a redirect to sign-in.

    """
    if not request.user.is_authenticated:
        return redirect("accounts:sign_in")
    return _render_setup(request)


@require_POST
def set_password_view(request: HttpRequest) -> HttpResponse:
    """
    Set a password for the authenticated user from the setup page (SNOW-431).

    Validates ``SnowdeskSetPasswordForm`` (confirmation match +
    ``AUTH_PASSWORD_VALIDATORS``).  On success the password is persisted and
    the session auth hash is refreshed so the user stays logged in, then
    redirects to manage.  On failure the setup page is re-rendered with field
    errors and nothing is stored.  Skipping the card is not a gate — the user
    can reach manage without ever calling this view.

    Args:
        request: Incoming POST request (authenticated session required).

    Returns:
        Redirect to manage on success, or the setup page with errors.

    """
    if not request.user.is_authenticated:
        return redirect("accounts:sign_in")

    form = SnowdeskSetPasswordForm(request.user, request.POST)
    if not form.is_valid():
        return _render_setup(request, password_form=form)

    form.save()
    # The password hash changed; refresh the session hash so the current
    # session is not invalidated by SessionAuthenticationMiddleware.
    update_session_auth_hash(request, request.user)
    logger.info("Password set for user pk=%s via setup page", request.user.pk)
    return redirect("accounts:hub")


# ---------------------------------------------------------------------------
# Password reset — request + confirm (SNOW-432)
# ---------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def reset_password_request_view(request: HttpRequest) -> HttpResponse:
    """
    Request a password reset by email (forgotten/change password, SNOW-432).

    GET renders the single-field request form.  POST (rate-limited 3/m per IP)
    enqueues a reset email and **always** renders the same "check your inbox"
    page — an unknown address, a known address with a password, and a
    passwordless account are byte-identical (the worker no-ops for the first
    and third), so nothing is leaked.

    Args:
        request: Incoming HTTP request.

    Returns:
        The request form, or the "check your inbox" page.

    """
    if request.method == "GET":
        return render(request, "accounts/reset_password.html", {"form": EmailForm()})

    usage = get_usage(
        request,
        group="accounts.reset_password.post",
        key="ip",
        rate="3/m",
        method=["POST"],
        increment=True,
    )
    if usage is not None and usage["should_limit"]:
        return HttpResponse(status=429)

    form = EmailForm(request.POST)
    if not form.is_valid():
        return render(request, "accounts/reset_password.html", {"form": form})

    send_password_reset_email(form.cleaned_data["email"], request=request)
    return render(request, "accounts/reset_password_sent.html", {})


@require_http_methods(["GET", "POST"])
@ratelimit(key="ip", rate="10/m", block=False)
def reset_password_confirm_view(request: HttpRequest, token: str) -> HttpResponse:
    """
    Confirm a password reset and set a new password (SNOW-432).

    Verifies the single-use token (fingerprint bound to the current password
    hash).  GET renders the set-password form; POST validates and saves the
    new password, logs the user in (session cycled by ``login``), and
    redirects to manage.  A bad/expired/already-used token renders the
    link-expired page (400).  Rate-limited 10/m per IP.

    Args:
        request: Incoming HTTP request.
        token: The signed reset token from the URL path.

    Returns:
        The set-password form (GET), a redirect to manage (POST success), or
        the link-expired page.

    """
    if getattr(request, "limited", False):
        return HttpResponse(status=429)

    max_age = getattr(settings, "ACCOUNT_TOKEN_MAX_AGE", 86400)
    user = verify_password_reset_token(token, max_age=max_age)

    if user is None:
        logger.debug("reset_password_confirm received an invalid/expired/used token")
        response = render(request, _LINK_EXPIRED_TEMPLATE, {}, status=400)
        response["Referrer-Policy"] = _REFERRER_NO_REFERRER
        return response

    if request.method == "GET":
        response = render(
            request,
            "accounts/reset_password_confirm.html",
            {"password_form": SnowdeskSetPasswordForm(user)},
        )
        response["Referrer-Policy"] = _REFERRER_CONFIRM_PAGE
        return response

    form = SnowdeskSetPasswordForm(user, request.POST)
    if not form.is_valid():
        response = render(
            request,
            "accounts/reset_password_confirm.html",
            {"password_form": form},
        )
        response["Referrer-Policy"] = _REFERRER_CONFIRM_PAGE
        return response

    form.save()
    # The password hash just changed, so the token's fingerprint no longer
    # matches — it is now single-use. login() cycles the session key.
    login(request, user, backend=_TOKEN_BACKEND)
    logger.info("Password reset completed for user pk=%s", user.pk)

    # Clicking a reset link proves the address is reachable — verify the
    # Account (addendum to the settled decisions).
    now = timezone.now()
    account, created = Account.objects.get_or_create(
        user=user,
        defaults={"is_verified": True, "verified_at": now},
    )
    if not created:
        account.mark_verified(now)
        account.save(update_fields=["is_verified", "verified_at", "updated_at"])

    response = redirect("accounts:hub")
    response["Referrer-Policy"] = _REFERRER_NO_REFERRER
    return response


# ---------------------------------------------------------------------------
# Change email — request + confirm (SNOW-433)
# ---------------------------------------------------------------------------


def _link_expired(request: HttpRequest) -> HttpResponse:
    """Render the generic link-expired page (400) with Referrer-Policy set."""
    response = render(request, _LINK_EXPIRED_TEMPLATE, {}, status=400)
    response["Referrer-Policy"] = _REFERRER_NO_REFERRER
    return response


def _email_available(new_email: str, *, exclude_pk: int) -> bool:
    """Return True when no *other* user already owns ``new_email``.

    The uniqueness check is never surfaced to the requester (anti-enumeration);
    it only gates whether the change proceeds.
    """
    return not User.objects.filter(username=new_email).exclude(pk=exclude_pk).exists()


def _process_email_change_request(
    user: UserType, new_email: str, request: HttpRequest
) -> None:
    """Record a pending change and dispatch the emails (SNOW-433).

    Always FYIs the old address; only sets the pending slot and sends the
    confirmation when the new address is free and differs from the current one
    — a taken or unchanged address is a silent no-op, so the response the
    caller renders is identical regardless.
    """
    now = timezone.now()
    # Submitting the current address is a no-op — do nothing (and don't send a
    # misleading "change requested" notice). The response is unchanged.
    if new_email == user.email:
        return

    # FYI to the old (current) address on every real request — it is the
    # owner's own inbox, so this leaks nothing about whether the new address is
    # taken.
    send_email_change_notice(user.email, stage="requested")

    if not _email_available(new_email, exclude_pk=user.pk):
        logger.info(
            "Email change to an already-registered address — silent no-op "
            "for user pk=%s",
            user.pk,
        )
        return

    account, _ = Account.objects.get_or_create(user=user)
    account.request_email_change(new_email, now)
    account.save(
        update_fields=["pending_email", "pending_email_requested_at", "updated_at"]
    )
    send_email_change_confirmation(user, new_email, request=request)


@never_cache
@require_http_methods(["GET", "POST"])
def change_email_view(request: HttpRequest) -> HttpResponse:
    """
    Request a change of account email address (signed-in only, SNOW-433).

    GET renders the form.  POST (rate-limited 3/m per IP) records the pending
    change, emails the new address a confirmation and the old address an FYI,
    and renders a "check your inbox at <new address>" page.  The displayed
    account email does **not** change until the new address's link is
    confirmed.  A new address that already belongs to another account is a
    silent no-op — the response is identical, so nothing is leaked.

    ``@never_cache`` for the same reason as ``manage_view``: the GET form
    renders the account's current email address, and the POST response
    renders the address the change was sent to.

    Args:
        request: Incoming HTTP request.

    Returns:
        The form, the "check your inbox" page, or a redirect to sign-in.

    """
    if not request.user.is_authenticated:
        return redirect("accounts:sign_in")

    if request.method == "GET":
        return render(
            request, "accounts/change_email.html", {"form": ChangeEmailForm()}
        )

    usage = get_usage(
        request,
        group="accounts.change_email.post",
        key="ip",
        rate="3/m",
        method=["POST"],
        increment=True,
    )
    if usage is not None and usage["should_limit"]:
        return HttpResponse(status=429)

    form = ChangeEmailForm(request.POST)
    if not form.is_valid():
        return render(request, "accounts/change_email.html", {"form": form})

    new_email = form.cleaned_data["email"]
    _process_email_change_request(request.user, new_email, request)
    return render(request, "accounts/change_email_sent.html", {"new_email": new_email})


@require_http_methods(["GET", "POST"])
@ratelimit(key="ip", rate="10/m", block=False)
def change_email_confirm_view(request: HttpRequest, token: str) -> HttpResponse:
    """
    Confirm and apply an email change (SNOW-433).

    Verifies the token (bound to the user + the specific new address), then
    checks the account's ``pending_email`` still matches (latest-request-wins,
    single-use) and the address is still free.  GET renders a confirm button;
    POST atomically swaps ``username``/``email`` to the new address, stamps the
    account verified, clears the pending slot, re-establishes the session under
    the new identity, and FYIs the old address.  Any failure renders the
    link-expired page (400).  Rate-limited 10/m per IP.

    Args:
        request: Incoming HTTP request.
        token: The signed email-change token from the URL path.

    Returns:
        The confirm page (GET), a redirect to manage (POST success), or the
        link-expired page.

    """
    if getattr(request, "limited", False):
        return HttpResponse(status=429)

    max_age = getattr(settings, "ACCOUNT_TOKEN_MAX_AGE", 86400)
    result = verify_email_change_token(token, max_age=max_age)
    if result is None:
        return _link_expired(request)

    user, new_email = result
    account = Account.objects.filter(user=user).first()
    # The pending slot must still name this address (a fresh request overwrites
    # it, and completion clears it — both invalidate an old link) and the
    # address must still be free.
    if (
        account is None
        or account.pending_email != new_email
        or not _email_available(new_email, exclude_pk=user.pk)
    ):
        return _link_expired(request)

    if request.method == "GET":
        response = render(
            request,
            "accounts/change_email_confirm.html",
            {"new_email": new_email},
        )
        response["Referrer-Policy"] = _REFERRER_CONFIRM_PAGE
        return response

    old_email = user.email
    try:
        _apply_email_change(user, account, new_email, timezone.now())
    except IntegrityError:
        # Lost the race — the address was taken between the check and the swap.
        return _link_expired(request)

    login(request, user, backend=_TOKEN_BACKEND)
    send_email_change_notice(old_email, stage="completed")
    logger.info("Email change completed for user pk=%s", user.pk)
    response = redirect("accounts:hub")
    response["Referrer-Policy"] = _REFERRER_NO_REFERRER
    return response


def _apply_email_change(
    user: UserType, account: Account, new_email: str, now: datetime
) -> None:
    """Atomically swap the user's email/username and clear the pending slot.

    Re-reads the account under ``select_for_update`` and re-checks the pending
    slot inside the transaction so a concurrent second request (that changed
    ``pending_email`` after the view's initial read) is not clobbered — a
    mismatch raises ``IntegrityError``, which the caller renders as an expired
    link.
    """
    with transaction.atomic():
        locked = Account.objects.select_for_update().get(pk=account.pk)
        if locked.pending_email != new_email:
            raise IntegrityError("pending_email changed concurrently")
        user.username = new_email
        user.email = new_email
        user.save(update_fields=["username", "email"])
        locked.is_verified = True
        locked.verified_at = now
        locked.clear_pending_email()
        locked.save(
            update_fields=[
                "is_verified",
                "verified_at",
                "pending_email",
                "pending_email_requested_at",
                "updated_at",
            ]
        )


# ---------------------------------------------------------------------------
# Analytics helpers
# ---------------------------------------------------------------------------


def _resolve_utm_source(request: HttpRequest, utm_source: str) -> str:
    """Return utm_source, falling back to the Referer host when empty.

    Args:
        request: The current HTTP request.
        utm_source: UTM source extracted from the session.  Returned as-is
            when non-empty.

    Returns:
        The utm_source string, or the Referer hostname, or an empty string.

    """
    if utm_source:
        return utm_source
    referer: str = request.META.get("HTTP_REFERER", "")
    if not referer:
        return ""
    try:
        from urllib.parse import urlparse

        return urlparse(referer).hostname or ""
    except Exception:  # noqa: BLE001
        return ""


def _track_subscription_started(
    request: HttpRequest,
    anon_id: str,
    country_code: str = "",
    geo_match_kind: str = "",
    region_match: bool = False,
    language_primary: str = "",
) -> None:
    """Emit the ``subscription_started`` analytics event.

    Derives the ``source`` from the UTM params stored in the session by the
    bulletin page (``request.session["analytics_utm"]``), falling back to the
    HTTP Referer host when no UTM data is present.

    Args:
        request: The current HTMX POST request.
        anon_id: The session-scoped anonymous UUID used as ``distinct_id``.
        country_code: ISO 3166-1 alpha-2 country code resolved from the
            client IP.  Included in props when non-empty.
        geo_match_kind: The region-relative classification string (one of
            ``IN_REGION``, ``IN_NEIGHBOUR``, ``ELSEWHERE``, ``UNKNOWN``).
            Included in props when non-empty.
        region_match: True when geo_match_kind is ``IN_REGION`` or
            ``IN_NEIGHBOUR``, signalling the subscriber was geographically
            close to the region they signed up for.
        language_primary: The primary language subtag from Accept-Language
            (e.g. ``'en'``, ``'de'``, ``'fr'``).  Included in props when
            non-empty.

    """
    utm: dict[str, str] = request.session.get("analytics_utm") or {}
    utm_source: str = _resolve_utm_source(request, utm.get("utm_source", ""))
    utm_medium: str = utm.get("utm_medium", "")
    utm_campaign: str = utm.get("utm_campaign", "")

    props: dict[str, object] = {}
    if utm_source:
        props["source"] = utm_source
    if utm_medium:
        props["utm_medium"] = utm_medium
    if utm_campaign:
        props["utm_campaign"] = utm_campaign
    if country_code:
        props["country_code"] = country_code
    if geo_match_kind:
        props["geo_match_kind"] = geo_match_kind
        props["region_match"] = region_match
    if language_primary:
        props["language_primary"] = language_primary

    analytics.track("subscription_started", anon_id, props)


# ---------------------------------------------------------------------------
# subscribe_partial — inline HTMX form on bulletin pages
# ---------------------------------------------------------------------------


@require_POST
@require_htmx
@ratelimit(key="ip", rate="5/m", block=False)
def subscribe_partial(request: HttpRequest) -> HttpResponse:
    """
    Accept a POST from the inline bulletin-page subscribe CTA.

    Requires ``region_id`` in the POST data.  Uses a four-case matrix keyed
    on ``(account_created, subscription_created)`` and ``account.is_verified``
    to decide which email to send and which success fragment to return:

    A. New account (account_created=True):
       Send account-access email → "Check your inbox" fragment.

    B. Existing unverified account (account_created=False, is_verified=False):
       Resend account-access email → "Check your inbox" fragment. **Byte-equal**
       to Case A's response — this is the documented anti-enumeration invariant
       (docs/accounts.md).

    C. Verified account + new region (is_verified=True, sub_created=True):
       Send subscription confirmation email → "Added {region} to your alerts" fragment.

    D. Verified account + already subscribed (is_verified=True,
       sub_created=False):
       No email → "You're already subscribed to {region}" fragment.

    If ``region_id`` does not resolve to a known MicroRegion, returns a 400 error
    fragment — this path should not occur in normal use.

    Rate limited to 5 POST requests per minute per IP.  Exceeding the
    limit returns HTTP 429.

    Args:
        request: HTMX POST request containing ``email`` and ``region_id``.

    Returns:
        HTML fragment representing the outcome of the subscribe attempt.

    """
    if getattr(request, "limited", False):
        return HttpResponse(status=429)

    form = SubscribeForm(request.POST)
    if not form.is_valid():
        # Re-render the form with validation errors instead of the success card.
        return render(
            request,
            "accounts/partials/subscribe_form.html",
            {"form": form, "region_id": request.POST.get("region_id", "")},
        )

    email: str = form.cleaned_data["email"]
    region_id: str = form.cleaned_data["region_id"]

    # Resolve the region — return a 400 error fragment if not found.
    try:
        region = MicroRegion.objects.get(region_id__iexact=region_id)
    except MicroRegion.DoesNotExist:
        logger.warning(
            "subscribe_partial: region_id %s not found in DB",
            region_id,
        )
        return render(
            request,
            "accounts/partials/subscribe_error.html",
            {},
            status=400,
        )

    # Capture request context once; first-observation wins on both FKs.
    req_log = capture_request_log(request)

    account, account_created = Account.objects.get_or_create_for_email(
        email,
        defaults={"acquisition_request": req_log},
    )

    # Compute geo match before get_or_create so the fields land in the
    # INSERT rather than a subsequent UPDATE.  Existing rows are never touched.
    geo_defaults = geo_match_snapshot(req_log, region)

    # Persist the region subscription idempotently; capture whether it's new.
    _subscription, subscription_created = Subscription.objects.get_or_create(
        account=account,
        region=region,
        defaults={"subscribed_via": req_log, **geo_defaults},
    )

    if account_created:
        # Case A — new account.
        logger.info("New account created pk=%s (unverified)", account.pk)
        send_account_access_email(email, request=request)

        # Emit subscription_started (Case A only — silent on Case B resend).
        anon_id: str = request.session.setdefault(
            "analytics_anon_id", str(uuid.uuid4())
        )
        _kind: str = str(geo_defaults.get("geo_match_kind", ""))
        _track_subscription_started(
            request,
            anon_id,
            country_code=req_log.country_code,
            geo_match_kind=_kind,
            region_match=_kind in {IN_REGION, IN_NEIGHBOUR},
            language_primary=req_log.language,
        )

        return render(
            request,
            "accounts/partials/subscribe_success_access.html",
            {},
        )

    if not account.is_verified:
        # Case B — existing unverified account; resend the access link.
        logger.info(
            "Resending account-access email to unverified account pk=%s", account.pk
        )
        send_account_access_email(email, request=request)
        return render(
            request,
            "accounts/partials/subscribe_success_access.html",
            {},
        )

    if subscription_created:
        # Case C — verified account, new region added.
        logger.info(
            "Verified account pk=%s added new region %s",
            account.pk,
            region.region_id,
        )
        send_subscription_confirmation_email(email, region=region, request=request)
        region_added_props: dict[str, object] = {
            "region_id": region.region_id,
            "source": "bulletin",
            "region_count_after": account.subscriptions.count(),
        }
        if req_log.country_code:
            region_added_props["country_code"] = req_log.country_code
        analytics.track(
            "region_added",
            str(account.uuid),
            region_added_props,
        )
        return render(
            request,
            "accounts/partials/subscribe_success_added.html",
            {"region_name": region.name},
        )

    # Case D — verified account, already subscribed to this region.
    logger.info(
        "Verified account pk=%s already subscribed to region %s — no-op",
        account.pk,
        region.region_id,
    )
    return render(
        request,
        "accounts/partials/subscribe_success_already.html",
        {"region_name": region.name},
    )


# ---------------------------------------------------------------------------
# _delete_subscription_with_cascade — shared cascade helper
# ---------------------------------------------------------------------------


def _delete_subscription_with_cascade(
    account: Account, region: MicroRegion, request: HttpRequest
) -> bool | None:
    """Delete an (account, region) Subscription row.

    Removing the last region deletes only the Subscription row(s) — the
    User and Account survive and the caller stays signed in on manage. No
    session change happens here.

    Short-circuits when no matching Subscription row exists (deleted_count == 0)
    so the caller can distinguish a stale/forged POST from a real removal.

    Args:
        account: The authenticated Account whose subscription is being removed.
        region: The MicroRegion to unsubscribe from.
        request: The current HTTP request (unused beyond signature parity with
            callers — retained for future request-scoped needs).

    Returns:
        True on success (a Subscription row was deleted); None when the
        (account, region) pair had no Subscription row to begin with.

    """
    deleted_count, _ = Subscription.objects.filter(
        account=account, region=region
    ).delete()
    if deleted_count == 0:
        logger.info(
            "Account pk=%s has no subscription for region %s — nothing removed",
            account.pk,
            region.region_id,
        )
        return None
    logger.info(
        "Account pk=%s removed region %s",
        account.pk,
        region.region_id,
    )
    region_count_after = account.subscriptions.count()
    analytics.track(
        "region_removed",
        str(account.uuid),
        {
            "region_id": region.region_id,
            "region_count_after": region_count_after,
        },
    )
    return True


# ---------------------------------------------------------------------------
# add_region — HTMX: authenticated one-click add from bulletin page
# ---------------------------------------------------------------------------


# @lowercase_region_id is outermost so it can redirect mixed-case URLs before
# @require_POST fires. preserve_method=True makes that a 308 rather than a 301,
# so the browser replays the POST at the canonical path; a 301 would downgrade it
# to a GET and @require_POST would answer 405 (SNOW-650). Every EAWS region id is
# uppercase and the templates post to it raw, so this fires on every click.
@lowercase_region_id(preserve_method=True)
@require_POST
@require_htmx
@ratelimit(key="ip", rate="5/m", block=False)
def add_region(request: HttpRequest, region_id: str) -> HttpResponse:
    """Add a Subscription for the authenticated user and the given region.

    Idempotent — uses ``get_or_create`` so POSTing twice returns the same
    success fragment without raising an IntegrityError.  No email is sent
    because the account is already verified.

    Guarded by authentication (no session → 403), ``@require_POST``,
    ``@require_htmx``, and rate-limited at 5 requests/min per IP.

    Args:
        request: HTMX POST request.
        region_id: The SLF region identifier to add.

    Returns:
        subscribe_success_added fragment (200), or 403 / 400 / 429 on error.

    """
    if getattr(request, "limited", False):
        return HttpResponse(status=429)

    account = _get_account(request)
    if account is None:
        return HttpResponse(status=403)

    try:
        region = MicroRegion.objects.get(region_id__iexact=region_id)
    except MicroRegion.DoesNotExist:
        logger.warning("add_region: region_id %s not found in DB", region_id)
        return render(
            request,
            "accounts/partials/subscribe_error.html",
            {},
            status=400,
        )

    req_log = capture_request_log(request)
    geo_defaults = geo_match_snapshot(req_log, region)
    _subscription, sub_created = Subscription.objects.get_or_create(
        account=account,
        region=region,
        defaults={"subscribed_via": req_log, **geo_defaults},
    )
    logger.info(
        "Account pk=%s added region %s via bulletin page (idempotent)",
        account.pk,
        region_id,
    )
    if sub_created:
        region_added_props: dict[str, object] = {
            "region_id": region.region_id,
            "source": "bulletin",
            "region_count_after": account.subscriptions.count(),
        }
        if req_log.country_code:
            region_added_props["country_code"] = req_log.country_code
        analytics.track(
            "region_added",
            str(account.uuid),
            region_added_props,
        )
    return render(
        request,
        "accounts/partials/subscribe_success_added.html",
        {"region_name": region.name},
    )


# ---------------------------------------------------------------------------
# remove_region_from_bulletin — HTMX: authenticated unsubscribe from bulletin page
# ---------------------------------------------------------------------------


# @lowercase_region_id is outermost so it can redirect mixed-case URLs before
# @require_POST fires. preserve_method=True makes that a 308 rather than a 301,
# so the browser replays the POST at the canonical path; a 301 would downgrade it
# to a GET and @require_POST would answer 405 (SNOW-650). Every EAWS region id is
# uppercase and the templates post to it raw, so this fires on every click.
@lowercase_region_id(preserve_method=True)
@require_POST
@require_htmx
@ratelimit(key="ip", rate="10/m", block=False)
def remove_region_from_bulletin(request: HttpRequest, region_id: str) -> HttpResponse:
    """Remove a Subscription for the authenticated user from the bulletin page.

    Deletes the ``(account, region)`` Subscription row and swaps the panel
    for a confirmation fragment. The account stays signed in, even when this
    was the last remaining region.

    Guarded by authentication (no session → 403), ``@require_POST``,
    ``@require_htmx``, and rate-limited at 10 requests/min per IP.

    Args:
        request: HTMX POST request.
        region_id: The SLF region identifier to unsubscribe from.

    Returns:
        subscribe_unsubscribed fragment (200), 403 when unauthenticated, 400
        when region unknown or the account never held the region (stale/forged
        POST), or 429 when rate-limited.

    """
    if getattr(request, "limited", False):
        return HttpResponse(status=429)

    account = _get_account(request)
    if account is None:
        return HttpResponse(status=403)

    try:
        region = MicroRegion.objects.get(region_id__iexact=region_id)
    except MicroRegion.DoesNotExist:
        logger.warning(
            "remove_region_from_bulletin: region_id %s not found in DB", region_id
        )
        return render(
            request,
            "accounts/partials/subscribe_error.html",
            {},
            status=400,
        )

    result = _delete_subscription_with_cascade(account, region, request)
    if result is None:
        # The account never held this region — stale or forged POST.
        logger.warning(
            "remove_region_from_bulletin: account pk=%s has no subscription for "
            "region %s — returning 400",
            account.pk,
            region.region_id,
        )
        return render(
            request,
            "accounts/partials/subscribe_error.html",
            {},
            status=400,
        )

    return render(
        request,
        "accounts/partials/subscribe_unsubscribed.html",
        {"region_name": region.name},
    )


# ---------------------------------------------------------------------------
# account_view — verify account-access token
# ---------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def account_view(request: HttpRequest, token: str) -> HttpResponse:
    """
    Verify an account-access ("magic link") token and sign the account in.

    GET renders a confirm page with a single button — it performs **no** state
    change and does **not** log anyone in, so following the emailed link (or a
    link-prefetch scanner hitting it) never grants account access on its own
    (SNOW-439).  Only the POST from that page acts: it marks the ``Account``
    verified (idempotent — re-submitting does not re-stamp ``verified_at``),
    then ``django.contrib.auth.login()`` establishes the session and redirects
    to ``/account/manage/?just_confirmed=1``.

    On a bad, tampered, or expired token — or a token for an unknown user —
    renders ``link_expired.html`` (400) for both verbs.

    Args:
        request: Incoming HTTP request.
        token: The signed token from the URL path.

    Returns:
        The confirm page (GET), a 302 redirect to manage (POST success), or the
        link-expired error page.

    """
    max_age = getattr(settings, "ACCOUNT_TOKEN_MAX_AGE", 86400)
    email = verify_token(token, salt=SALT_ACCOUNT_ACCESS, max_age=max_age)

    if email is None:
        logger.debug("account_view received an invalid/expired token")
        return _link_expired(request)

    try:
        user = User.objects.get(username=email.lower())
    except User.DoesNotExist:
        logger.warning(
            "account_view: valid token for unknown email %s", mask_email(email)
        )
        return _link_expired(request)

    if request.method == "GET":
        # No state change on GET — the token is carried in the form action (the
        # same URL) so the POST re-verifies it.
        response = render(request, "accounts/access.html", {"token": token})
        response["Referrer-Policy"] = _REFERRER_CONFIRM_PAGE
        return response

    # POST — verify (if not already) and sign in.
    now = timezone.now()
    account, created = Account.objects.get_or_create(
        user=user,
        defaults={"is_verified": True, "verified_at": now},
    )
    was_verified = account.is_verified
    if not created:
        account.mark_verified(now)
        account.save(update_fields=["is_verified", "verified_at", "updated_at"])
    logger.info("Account pk=%s verified via account-access link", account.pk)

    if created or not was_verified:
        # Emit subscription_confirmed and join the anonymous session identity
        # to the now-authenticated account identity — only on the transition
        # into verified, matching today's "only when transitioning out of
        # PENDING" idempotency. SNOW-549: the identity is Account.uuid, never
        # the sequential auth.User PK.
        hours_since: float = round(
            (timezone.now() - account.created_at).total_seconds() / 3600,
            2,
        )
        distinct_id = str(account.uuid)
        analytics.track(
            "subscription_confirmed",
            distinct_id,
            {"hours_since_started": hours_since},
        )
        anon_id: str | None = request.session.get("analytics_anon_id")
        if anon_id:
            analytics.alias(
                distinct_id=distinct_id,
                alias_id=anon_id,
            )
    login(request, user, backend=_TOKEN_BACKEND)
    response = redirect(f"{_HUB_URL}?just_confirmed=1")
    # Tokens appear in this view's URL path — suppress Referer leakage.
    response["Referrer-Policy"] = _REFERRER_NO_REFERRER
    return response


# ---------------------------------------------------------------------------
# hub_view / settings_view — the account area (SNOW-667)
# ---------------------------------------------------------------------------
#
# These two replace the former ``manage_view``, a single page that had
# accumulated nine unranked sections. The split is by what the user is doing:
# the hub holds their own data, settings holds everything they can change
# about the account itself. ``/account/manage/`` is now a permanent redirect
# to the hub (see urls.py).
#
# Neither view is ``@never_cache``, and that is deliberate — see
# ``_ACCOUNT_PAGE_CACHE_NOTE`` below.

_ACCOUNT_PAGE_CACHE_NOTE = """
Deliberately NOT ``@never_cache``, unlike ``change_email_view`` (C1,
``docs/code-reviews/2026-08-03-js-review.md``). These pages render the
signed-in user's own data, so they must never be served to anyone else — but
the offline favourites roster is built on the hub being in the PWA shell
cache, so ``no-store`` would break a shipped feature. The ``X-SW-Principal``
stamp is what makes that safe: ``_networkFirst`` in ``static/js/sw.js``
records the account this HTML was rendered for and the offline read refuses
an entry whose stamp is not the principal signed in now, so a sign-out or a
different user gets the offline fallback instead of the previous session's
page. Cache-partitioning, not cache-avoidance — the same trade
``map_overlay_offline_cache.js`` makes for the overlay cache under SNOW-493.
"""


@require_GET
def hub_view(request: HttpRequest) -> HttpResponse:
    """
    Show the account hub — the user's own saved data.

    Unauthenticated visitors are redirected to the sign-in page.  A registered
    user with no ``Subscription`` rows still sees the page (with no
    subscription cards) — this is the landing spot after registration.

    Carries the subscribed-region cards and the lazy-loaded favourites panel.
    SNOW-668 moves both to ``/account/places/`` once the unified place model
    exists; until then the hub is where "your data" lives, so no surface is
    unreachable mid-chain.

    Not ``@never_cache`` — the offline favourites roster depends on this page
    reaching the PWA shell cache. See ``_ACCOUNT_PAGE_CACHE_NOTE``.

    GET: render the subscriptions dashboard (one card per subscribed
    region, with resort list and per-region remove button).

    Context keys:
        account        — authenticated Account instance.
        subscriptions  — queryset of Subscription rows for the account.
        just_confirmed — True when arriving via the confirmation link.
        today          — today's date (datetime.date) for the bulletin link label.

    Args:
        request: Incoming HTTP request.

    Returns:
        Rendered page or redirect to sign-in.

    """
    if not request.user.is_authenticated:
        return redirect("accounts:sign_in")

    # A registered user (SNOW-430) has an Account but may have no Subscription
    # rows (no bulletin subscriptions). Render the dashboard for any
    # authenticated user — redirecting non-subscribers to sign-in would loop,
    # because sign_in_view sends authenticated users straight back here.
    account = _get_account(request)

    just_confirmed = request.GET.get("just_confirmed") == "1"

    subscriptions = (
        Subscription.objects.filter(account=account)
        .select_related("region", "region__subregion__major")
        .prefetch_related("region__resorts")
        .order_by("region__name")
        if account is not None
        else Subscription.objects.none()
    )

    return render(
        request,
        "accounts/hub.html",
        {
            "account": account,
            "subscriptions": subscriptions,
            "just_confirmed": just_confirmed,
            "today": timezone.now().date(),
            "subnav_groups": build_subnav("accounts:hub"),
        },
    )


@require_GET
def settings_view(request: HttpRequest) -> HttpResponse:
    """
    Show the account settings page for the authenticated user.

    Unauthenticated visitors are redirected to the sign-in page.

    Holds everything the user can change about the account itself: the
    verified email address, passkeys, the telemetry opt-in, the sync-log
    panel, the reset-local-data escape hatch, sign out, and the account
    deletion control.

    Not ``@never_cache`` — see ``_ACCOUNT_PAGE_CACHE_NOTE``. This page does
    not itself feed the offline roster, but it renders inside the same shell
    and is stamped by the same principal guard, so it follows the hub rather
    than inventing a second caching posture for the account area.

    Context keys:
        account          — authenticated Account instance.
        sync_log_visible — True when the ``sync_log`` waffle flag is active
                            for this request (SNOW-482). Gates the sync-log
                            panel, which reads ``window.pwaDb.getSyncLog()``
                            client-side — nothing server-side to query here.

    Args:
        request: Incoming HTTP request.

    Returns:
        Rendered page or redirect to sign-in.

    """
    if not request.user.is_authenticated:
        return redirect("accounts:sign_in")

    return render(
        request,
        "accounts/settings.html",
        {
            "account": _get_account(request),
            "sync_log_visible": waffle.flag_is_active(request, "sync_log"),
            "subnav_groups": build_subnav("accounts:settings"),
        },
    )


# ---------------------------------------------------------------------------
# remove_region — HTMX: remove one subscribed region
# ---------------------------------------------------------------------------


# @lowercase_region_id is outermost so it can redirect mixed-case URLs before
# @require_POST fires. preserve_method=True makes that a 308 rather than a 301,
# so the browser replays the POST at the canonical path; a 301 would downgrade it
# to a GET and @require_POST would answer 405 (SNOW-650). Every EAWS region id is
# uppercase and the templates post to it raw, so this fires on every click.
@lowercase_region_id(preserve_method=True)
@require_POST
@require_htmx
@ratelimit(key="ip", rate="10/m", block=False)
def remove_region(request: HttpRequest, region_id: str) -> HttpResponse:
    """
    Remove a single subscribed region for the authenticated account.

    Deletes the ``(account, region)`` Subscription row. The account stays
    signed in on manage, even when this was the last remaining region — no
    logout, no redirect away.

    Guarded by authentication (no session → 403), ``@require_POST``,
    ``@require_htmx``, and rate-limited at 10 requests/min per IP.

    Args:
        request: HTMX POST request.
        region_id: The SLF region identifier to remove.

    Returns:
        Empty 200 (card removed via outerHTML swap), 403 when unauthenticated,
        or 429 when rate-limited.

    """
    if getattr(request, "limited", False):
        return HttpResponse(status=429)

    account = _get_account(request)
    if account is None:
        return HttpResponse(status=403)

    region = get_object_or_404(MicroRegion, region_id__iexact=region_id)
    _delete_subscription_with_cascade(account, region, request)

    # Return empty content — hx-swap="outerHTML" on the card will remove it.
    # This covers both the "subscription removed" and the "no row existed" (None)
    # paths: either way the card should disappear from the manage UI.
    return HttpResponse(status=200)


# ---------------------------------------------------------------------------
# delete_account — HTMX: hard-delete account
# ---------------------------------------------------------------------------


@require_POST
@require_htmx
@ratelimit(key="ip", rate="3/m", block=False)
def delete_account(request: HttpRequest) -> HttpResponse:
    """
    Hard-delete the authenticated account (User) and all their subscriptions.

    The sole hard-delete path, available to any authenticated account —
    including a registered-only account with no subscriptions. Calls
    ``django.contrib.auth.logout()`` to clear the Django session and
    responds with an ``HX-Redirect`` header pointing to the unsubscribe-done
    page.

    Guarded by authentication (no session → 403), ``@require_POST``,
    ``@require_htmx``, and rate-limited at 3 requests/min per IP.

    Args:
        request: HTMX POST request.

    Returns:
        200 with HX-Redirect header, 403 when unauthenticated, or 429 when
        rate-limited.

    """
    if getattr(request, "limited", False):
        return HttpResponse(status=429)

    if not request.user.is_authenticated:
        return HttpResponse(status=403)

    account = _get_account(request)
    user = request.user
    email = user.email
    # Capture account_age_days and distinct_id BEFORE deleting the row —
    # SNOW-549's identity lives on the Account, which the CASCADE removes.
    account_age_days = (
        (timezone.now() - account.created_at).days if account is not None else 0
    )
    distinct_id = user_identity(user)
    user.delete()  # CASCADE deletes the Account and any Subscription rows.
    logout(request)
    logger.info("Account %s hard-deleted via delete_account", mask_email(email))
    analytics.track(
        "unsubscribed",
        distinct_id,
        {"reason": "account_deleted", "account_age_days": account_age_days},
    )

    response = HttpResponse(status=200)
    response["HX-Redirect"] = _UNSUBSCRIBE_DONE_URL
    return response


# ---------------------------------------------------------------------------
# sign_out — log out the account
# ---------------------------------------------------------------------------


@require_POST
def sign_out(request: HttpRequest) -> HttpResponse:
    """
    Log out the account and redirect to the sign-in page.

    Args:
        request: POST request (CSRF-protected via the standard Django form token).

    Returns:
        Redirect to the sign-in page.

    """
    logout(request)
    return redirect("accounts:sign_in")


# ---------------------------------------------------------------------------
# unsubscribe_done — standalone page for post-unsubscribe landing
# ---------------------------------------------------------------------------


@require_GET
def unsubscribe_done_view(request: HttpRequest) -> HttpResponse:
    """
    Render the "you've been unsubscribed" confirmation page.

    This view exists so that HTMX HX-Redirect from remove_region and
    delete_account can point to a stable GET URL rather than relying on
    the unsubscribe flow's POST-only done path.

    Args:
        request: Incoming GET request.

    Returns:
        Rendered unsubscribe-done page.

    """
    return render(request, "accounts/unsubscribe_done.html", {})


# ---------------------------------------------------------------------------
# unsubscribe_view — token-verified one-click unsubscribe
# ---------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
@ratelimit(key="ip", rate="10/m", block=False)
def unsubscribe_view(request: HttpRequest, token: str) -> HttpResponse:
    """
    Confirm and execute a single-region unsubscribe.

    Verifies the unsubscribe token (no expiry — tokens are permanent) to
    extract ``(email, region_id)``.

    GET: render a confirmation page showing which region will be removed.
    POST: delete that region's Subscription row. The User and Account are
          not touched — deletion here removes only the Subscription, even
          when it was the account's last one. Idempotent on re-submit
          (already deleted → renders done page anyway).

    Rate limited to 10 requests per minute per IP.

    Args:
        request: Incoming HTTP request.
        token: The signed unsubscribe token from the URL path.

    Returns:
        Rendered confirmation, done, or error page.

    """
    if getattr(request, "limited", False):
        return HttpResponse(status=429)

    result = verify_unsubscribe_token(token)
    if result is None:
        logger.debug("unsubscribe_view received an invalid token")
        response = render(request, _LINK_EXPIRED_TEMPLATE, {}, status=400)
        response["Referrer-Policy"] = _REFERRER_NO_REFERRER
        return response

    email, region_id = result

    # Look up the region — 404 if deleted from the pipeline side.
    region = get_object_or_404(MicroRegion, region_id=region_id)

    if request.method == "GET":
        response = render(
            request,
            "accounts/unsubscribe.html",
            {"email": email, "region": region, "token": token},
        )
        response["Referrer-Policy"] = _REFERRER_CONFIRM_PAGE
        return response

    # POST — execute unsubscribe.
    try:
        account = Account.objects.get(user__email=email.lower())
    except Account.DoesNotExist:
        # Already unsubscribed (perhaps from a different link) — idempotent.
        logger.info(
            "unsubscribe_view: account for %s not found — already deleted",
            mask_email(email),
        )
        response = render(request, "accounts/unsubscribe_done.html", {})
        response["Referrer-Policy"] = _REFERRER_NO_REFERRER
        return response

    # Capture distinct_id and account_age_days BEFORE deleting the row.
    distinct_id = str(account.uuid)
    account_age_days = (timezone.now() - account.created_at).days

    # Delete the specific subscription. The User and Account survive — this
    # unauthenticated token path makes no session change and removes only
    # the Subscription row, even when it was the account's last one.
    # Note: we intentionally do NOT fire ``region_removed`` here.  The
    # unsubscribe-link path fires only ``unsubscribed``; ``region_removed``
    # is reserved for the in-app "remove a region" flow.  Firing both would
    # double-count churn for subscribers who leave via the email link.
    Subscription.objects.filter(account=account, region=region).delete()
    logger.info("Account pk=%s unsubscribed from region %s", account.pk, region_id)

    analytics.track(
        "unsubscribed",
        distinct_id,
        {"reason": "unsubscribe_link", "account_age_days": account_age_days},
    )

    response = render(request, "accounts/unsubscribe_done.html", {})
    response["Referrer-Policy"] = _REFERRER_NO_REFERRER
    return response


# ---------------------------------------------------------------------------
# Unsubscribe token helper (used in bulletin email templates)
# ---------------------------------------------------------------------------


def build_unsubscribe_url(
    email: str, region_id: str, request: HttpRequest | None = None
) -> str:
    """
    Build an absolute unsubscribe URL for the given email and region.

    Convenience helper for use in bulletin email templates and management
    commands that need to embed per-region unsubscribe links.

    Args:
        email: The account's email address.
        region_id: The SLF region identifier.
        request: Optional request used to derive the base URL.

    Returns:
        Absolute URL string.

    """
    token = generate_unsubscribe_token(email, region_id)
    path = f"/account/unsubscribe/{token}/"
    if request is not None:
        return request.build_absolute_uri(path)
    base = getattr(settings, "SITE_BASE_URL", "http://localhost:8000").rstrip("/")
    return f"{base}{path}"
