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
                            redirects to /account/.
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
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

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
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django_ratelimit.core import get_usage
from django_ratelimit.decorators import ratelimit

from apps import analytics
from apps.core.decorators import require_htmx
from apps.core.services.request_log import capture as capture_request_log
from apps.favourites.services import delete_region_favourite
from apps.regions.models import MicroRegion

from .forms import (
    ChangeEmailForm,
    EmailForm,
    PasswordSignInForm,
    RegisterForm,
    SnowdeskSetPasswordForm,
)
from .identity import user_identity
from .logging_utils import mask_email
from .models import Account
from .redirects import safe_next
from .services.deletion import erase_account
from .services.email import (
    send_account_access_email,
    send_email_change_confirmation,
    send_email_change_notice,
    send_password_reset_email,
    send_verification_email,
)
from .services.token import (
    SALT_ACCOUNT_ACCESS,
    SALT_EMAIL_VERIFICATION,
    generate_unsubscribe_token,
    verify_email_change_token,
    verify_password_reset_token,
    verify_token,
    verify_unsubscribe_token,
)

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
# SNOW-802: where a just-verified account lands — the map, pins sheet open.
_VERIFIED_LANDING_URL = "/?panel=favourites"

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


def _password_sign_in(
    request: HttpRequest, next_url: str | None
) -> HttpResponse | None:
    """Attempt password sign-in when a password is supplied (SNOW-431).

    A wrong password, an unknown email, and an account with no usable password
    all fail identically (``authenticate`` returns None), so the generic error
    leaks no account-existence signal.

    Args:
        request: The current POST request.
        next_url: The already-validated ``next`` destination (SNOW-825), or
            ``None`` to land on the map. Carried back into the error
            re-render so a mistyped password does not lose the destination.

    Returns:
        A redirect to ``next_url`` (or the map) on success, the sign-in page
        with a generic error on failure, or ``None`` when no password was
        supplied (the caller then continues with the magic-link flow).

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
            return redirect(next_url or reverse("public:home"))

    return render(
        request,
        "accounts/sign_in.html",
        {"form": EmailForm(), "password_error": True, "next_url": next_url},
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

    ``?next=`` (SNOW-825): a same-site destination to return to after signing
    in, so a recipient who opened a trip share link and signed in lands back
    on the trip rather than on the map. All three paths honour it — the
    password redirect, the magic-link email's URL, and the passkey response's
    ``next`` — and every one of them validates it through
    ``apps.accounts.redirects.safe_next`` first.

    Args:
        request: Incoming HTTP request.

    Returns:
        Rendered sign-in page or redirect.

    """
    # SNOW-825: where to land afterwards. Read from the query string on GET
    # and from the form's hidden field on POST, and validated by ``safe_next``
    # on every read — the raw value is attacker-supplied, and an unchecked one
    # would make this page an open redirector.
    next_url = safe_next(
        request,
        request.GET.get("next")
        if request.method == "GET"
        else request.POST.get("next"),
    )

    # A ``next`` pointing back at THIS page is a bounce rather than a
    # destination: the already-authenticated branch below would redirect
    # here, and this page would then redirect onward carrying no ``next``
    # at all. It terminates — each hop consumes one level — but it spends
    # two redirects to land exactly where a missing ``next`` lands in none.
    # Dropped here rather than in ``safe_next``, which answers "is this
    # destination safe" and not "is it worth going to".
    if next_url and urlsplit(next_url).path == reverse("accounts:sign_in"):
        next_url = None

    if request.user.is_authenticated:
        return redirect(next_url or reverse("public:home"))

    if request.method == "GET":
        return render(
            request,
            "accounts/sign_in.html",
            {"form": EmailForm(), "next_url": next_url},
        )

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
    pw_response = _password_sign_in(request, next_url)
    if pw_response is not None:
        return pw_response

    form = EmailForm(request.POST)
    if not form.is_valid():
        return render(
            request, "accounts/sign_in.html", {"form": form, "next_url": next_url}
        )

    email: str = form.cleaned_data["email"]

    # Capture request context before creating / fetching the account so
    # first-observation wins on acquisition_request.
    req_log = capture_request_log(request)

    account, created = Account.objects.get_or_create_for_email(
        email,
        defaults={"acquisition_request": req_log},
    )
    send_account_access_email(email, request=request, next_url=next_url)
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
        return redirect("public:home")

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
    return redirect("public:home")


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

    response = redirect("public:home")
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
    response = redirect("accounts:settings")
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
    to ``/account/?just_confirmed=1``.

    On a bad, tampered, or expired token — or a token for an unknown user —
    renders ``link_expired.html`` (400) for both verbs.

    **The ``?next=`` query parameter (SNOW-825).** The emailed link carries
    the destination the person was heading for — a trip share page, say — as
    an ordinary query parameter rather than as a claim baked into the signed
    token, and that is deliberate. The TOKEN is what authenticates; ``next``
    is only a destination, so signing it would protect nothing that matters
    and would fix the destination at send time for a link that lives for a
    day. Tampering with the query is a person redirecting THEMSELVES: it
    grants no access the token did not already grant, and it cannot reach a
    foreign host, because the value is re-validated with ``safe_next`` here,
    on arrival, at the moment of the redirect.

    Args:
        request: Incoming HTTP request.
        token: The signed token from the URL path.

    Returns:
        The confirm page (GET), a 302 redirect to the ``next`` destination or
        the map (POST success), or the link-expired error page.

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

    # The destination the emailed link was minted for, re-validated on arrival
    # (SNOW-825). GET carries it in the query string and hands it to the
    # confirm form as a hidden field; POST reads it back from there.
    next_url = safe_next(
        request,
        request.GET.get("next")
        if request.method == "GET"
        else request.POST.get("next"),
    )

    if request.method == "GET":
        # No state change on GET — the token is carried in the form action (the
        # same URL) so the POST re-verifies it.
        response = render(
            request, "accounts/access.html", {"token": token, "next_url": next_url}
        )
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
    response = redirect(next_url or _VERIFIED_LANDING_URL)
    # Tokens appear in this view's URL path — suppress Referer leakage.
    response["Referrer-Policy"] = _REFERRER_NO_REFERRER
    return response


# ---------------------------------------------------------------------------
# settings_view — the account area (SNOW-667, cut to one page by SNOW-802/803)
# ---------------------------------------------------------------------------
#
# SNOW-667 split the former ``manage_view`` — a single page that had
# accumulated nine unranked sections — into a hub (subscriptions), a
# favourites page and settings. SNOW-803 sent the favourites page to the
# map's pins sheet; SNOW-802 sent the hub there too, because a subscription
# was only ever a bookmark on a region. Settings — everything the user can
# change about the account itself — is the one page left, and ``/account/``
# and ``/account/manage/`` both 301 to the map (see urls.py).
#
# Not ``@never_cache``, and that is deliberate — see
# ``_ACCOUNT_PAGE_CACHE_NOTE`` below.

_ACCOUNT_PAGE_CACHE_NOTE = """
Deliberately NOT ``@never_cache``, unlike ``change_email_view`` (C1,
``docs/code-reviews/2026-08-03-js-review.md``). These pages render the
signed-in user's own data, so they must never be served to anyone else — but
the account area is cache-PARTITIONED rather than cache-avoided, and these
pages keep that posture because they render in the same PWA shell under
the same principal guard as the map. The ``X-SW-Principal`` stamp is what
makes that safe: ``_networkFirst`` in ``static/js/sw.js`` records the
account this HTML was rendered for and the offline read refuses an entry
whose stamp is not the principal signed in now, so a sign-out or a
different user gets the offline fallback instead of the previous session's
page. The same trade ``map_overlay_offline_cache.js`` makes for the overlay
cache under SNOW-493.

History: the posture was first load-bearing on ``/account/favourites/``,
which the offline favourites roster read out of the shell cache
(SNOW-418/668). SNOW-803 removed that page; the roster's write-through keys
on the ``/favourites/partials/list/`` request path, which the map sheet
fetches, and the map at ``/`` is the navigation the shell caches for it.
"""


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
        },
    )


# ---------------------------------------------------------------------------
# delete_account — HTMX: hard-delete account
# ---------------------------------------------------------------------------


@require_POST
@require_htmx
@ratelimit(key="ip", rate="3/m", block=False)
def delete_account(request: HttpRequest) -> HttpResponse:
    """
    Hard-delete the authenticated account (User) and all their subscriptions.

    The sole hard-delete path for a signed-in person, available to any
    authenticated account — including a registered-only account with no
    subscriptions. The erasure itself lives in
    ``apps.accounts.services.deletion.erase_account``, which the admin also
    calls; this view adds only the session and analytics work around it,
    calling ``django.contrib.auth.logout()`` to clear the Django session and
    responding with an ``HX-Redirect`` header pointing to the
    unsubscribe-done page.

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

    # One transaction, and the parts a CASCADE cannot reach — the favourites'
    # minted Locations and the request rows written before the account
    # existed — go with it. The admin deletes through the same service, so
    # there is one erasure path rather than two that drift.
    erase_account(user, account)

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
    POST: remove that region's **region pin** (SNOW-802 — the row a
          ``Subscription`` became; see ``apps.favourites.services
          .delete_region_favourite``). The User and Account are not
          touched. Idempotent on re-submit (already removed → renders the
          done page anyway). The tokens have no expiry and are live in
          historical emails, so this path keeps resolving and keeps doing
          what the person clicking intends.

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

    # Remove the region pin. The User and Account survive — this
    # unauthenticated token path makes no session change and removes only
    # the pin, even when it was the account's last one.
    # Note: we intentionally do NOT fire ``region_removed`` here.  The
    # unsubscribe-link path fires only ``unsubscribed``; ``region_removed``
    # is reserved for the in-app toggle.  Firing both would double-count
    # churn for people who leave via the email link.
    delete_region_favourite(account.user, region)
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
