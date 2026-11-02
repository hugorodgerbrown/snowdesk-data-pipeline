"""
apps/accounts/alert_views.py — The bulletin alert settings (SNOW-939, 1325, 1327).

Five endpoints behind the ``bulletin_alerts`` flag:

- ``alert_settings``     POST HTMX — set ``notify_when`` and ``alert_email``.
                         Turning alerts on from OFF stamps
                         ``alerts_enabled_at``. Returns the Alerts group.
- ``alert_place``        POST HTMX — switch one place's alerts on or off
                         (``Favourite.alerts``). Returns the Alerts group.
- ``alert_test``         POST JSON — push one test alert to every active
                         subscription on the account, so the person sees one
                         land on the device they just turned on. Sent while
                         they wait, so the answer says whether it went.
- ``alert_recent``       GET JSON — the messages sent in the past week, for
                         the Today sun's dot (SNOW-1333, SNOW-1366).
                         Private, never cached.
- ``alert_unsubscribe``  GET confirms, POST stops alert EMAIL (sets
                         ``alert_email`` to NEVER) — the signed link in every
                         alert email, and the RFC 8058 one-click target, so
                         the POST is CSRF-exempt and needs no session.

The Alerts group itself is ``accounts/partials/_alert_settings.html``,
rendered into the settings page and returned by the two HTMX posts.
"""

from __future__ import annotations

import logging
from typing import Any

import waffle
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.models import Account
from apps.accounts.push_service import dispatch_push
from apps.accounts.services.alert_email import account_for_unsubscribe
from apps.bulletins.services.alert_wording import clock
from apps.bulletins.services.alerts import recent_alerts
from apps.core.decorators import require_htmx
from apps.favourites.models import Favourite

logger = logging.getLogger(__name__)

ALERTS_FLAG = "bulletin_alerts"


def alerts_enabled(request: HttpRequest) -> bool:
    """Return True when the bulletin_alerts flag is on for this request."""
    return bool(waffle.flag_is_active(request, ALERTS_FLAG))


def _account_or_404(request: HttpRequest) -> Account:
    """Return the signed-in user's Account, or raise 404 (flag off, no profile)."""
    if not alerts_enabled(request):
        raise Http404
    try:
        return request.user.account  # type: ignore[union-attr]  # login_required guarantees authentication
    except Account.DoesNotExist as exc:
        raise Http404 from exc


def alert_list_url(request: HttpRequest) -> str:
    """
    Return where the Today sun reads alerts for its dot, or "" for no dot.

    No dot when signed out, with the flag off, without an Account, or with
    alerts off (Hugo, 2026-10-30). The bell this fed was retired by SNOW-1366.

    Args:
        request: The map page's request.

    Returns:
        ``/account/alerts/recent/`` or an empty string.

    """
    if not request.user.is_authenticated or not alerts_enabled(request):
        return ""
    account = Account.objects.filter(user=request.user).only("notify_when").first()
    if account is None or account.notify_when == Account.NotifyWhen.OFF:
        return ""
    return reverse("accounts:alert_recent")


def alert_settings_context(request: HttpRequest, account: Account) -> dict[str, Any]:
    """
    Return the context ``_alert_settings.html`` renders.

    Args:
        request: The request (its user owns the places).
        account: The user's account.

    Returns:
        ``account``, ``alert_places`` (one row per favourite that can alert,
        with its name and whether it is on), ``notify_choices`` and
        ``email_choices``.

    """
    favourites = (
        Favourite.objects.for_user(account.user)
        .alertable()
        .select_related("region", "resort")
        .order_by("created_at")
    )
    places = [
        {
            "favourite": fav,
            "name": (
                fav.resort.name
                if fav.resort is not None
                else fav.name or (fav.region.name if fav.region else "")
            ),
            "kind": _("Ski resort")
            if fav.resort is not None
            else _("Avalanche region"),
            "switch_id": f"alert-place-{fav.uuid}",
        }
        for fav in favourites
    ]
    return {
        "account": account,
        "alert_places": places,
        "notify_choices": [
            ("NEW_BULLETIN", _("New bulletin")),
            ("CHANGES", _("Changes")),
            ("OFF", _("Off")),
        ],
        "email_choices": [
            ("FALLBACK", _("If no push")),
            ("ALWAYS", _("Always")),
            ("NEVER", _("Never")),
        ],
    }


def _render_group(request: HttpRequest, account: Account) -> HttpResponse:
    """Render the Alerts group partial."""
    return render(
        request,
        "accounts/partials/_alert_settings.html",
        alert_settings_context(request, account),
    )


@login_required
@require_htmx
@require_POST
def alert_settings(request: HttpRequest) -> HttpResponse:
    """Save the alert mode and email preference, and return the group."""
    account = _account_or_404(request)
    notify_when = request.POST.get("notify_when", account.notify_when)
    alert_email = request.POST.get("alert_email", account.alert_email)
    if notify_when not in Account.NotifyWhen.values:
        notify_when = account.notify_when
    if alert_email not in Account.AlertEmail.values:
        alert_email = account.alert_email
    fields = ["notify_when", "alert_email", "updated_at"]
    if (
        account.notify_when == Account.NotifyWhen.OFF
        and notify_when != Account.NotifyWhen.OFF
    ):
        account.alerts_enabled_at = timezone.now()
        fields.append("alerts_enabled_at")
    account.notify_when = notify_when
    account.alert_email = alert_email
    account.save(update_fields=fields)
    return _render_group(request, account)


@login_required
@require_htmx
@require_POST
def alert_place(request: HttpRequest, favourite_uuid: str) -> HttpResponse:
    """Switch one place's alerts on or off, and return the group."""
    account = _account_or_404(request)
    favourite = get_object_or_404(
        Favourite.objects.for_user(account.user), uuid=favourite_uuid
    )
    favourite.alerts = request.POST.get("alerts") == "on"
    favourite.save(update_fields=["alerts", "updated_at"])
    return _render_group(request, account)


@login_required
@require_POST
def alert_test(request: HttpRequest) -> HttpResponse:
    """Push one test alert to every active subscription on the account.

    Sent inline rather than enqueued: the button exists to tell the person
    whether alerts reach this device, so the answer has to say what the push
    service did. ``devices`` is how many subscriptions were tried and
    ``delivered`` how many the push service accepted; ``ok`` is true only
    when at least one was.
    """
    account = _account_or_404(request)
    payload = {
        "title": _("Snowdesk alerts are on"),
        "body": _("This is how a bulletin alert will look on this device."),
        "url": "/account/settings/",
    }
    client_version = request.headers.get("X-Client-Version", "")
    devices = delivered = 0
    for sub in account.push_subscriptions.active():
        devices += 1
        if dispatch_push(sub, payload, client_version).get("ok"):
            delivered += 1
    return JsonResponse(
        {"ok": delivered > 0, "devices": devices, "delivered": delivered}
    )


@login_required
@require_GET
@never_cache
def alert_recent(request: HttpRequest) -> HttpResponse:
    """Return the alerts sent to the account in the past week, newest first.

    The Today sun's dot reads them (SNOW-1333, SNOW-1366). Each entry carries
    the message's own title, body and link, and ``sent``, when it went
    ("Friday at 5 pm").
    With alerts off the list is empty, so the dot stays hidden.
    """
    account = _account_or_404(request)
    if account.notify_when == Account.NotifyWhen.OFF:
        return JsonResponse({"alerts": []})
    alerts = [
        {
            "title": a.title,
            "body": a.body,
            "url": a.url,
            "sent_at": a.sent_at.isoformat(),
            "sent": clock(a.sent_at),
        }
        for a in recent_alerts(account, timezone.now())
    ]
    return JsonResponse({"alerts": alerts})


# RFC 8058 one-click unsubscribe: the mail provider POSTs with no session
# and no CSRF token, so the signed token in the path is the authority, and
# the only effect is turning alert email off.
# nosemgrep: python.django.security.audit.csrf-exempt.no-csrf-exempt
@csrf_exempt
def alert_unsubscribe(request: HttpRequest, token: str) -> HttpResponse:
    """Confirm (GET) or stop (POST) alert emails for the account a link names.

    CSRF-exempt on purpose: RFC 8058's one-click unsubscribe is a POST from
    the mail provider with no session and no token, and the signed path
    segment is the authority. The only effect is turning email OFF.
    """
    account = account_for_unsubscribe(token)
    if account is None:
        raise Http404
    done = False
    if request.method == "POST":
        account.alert_email = Account.AlertEmail.NEVER
        account.save(update_fields=["alert_email", "updated_at"])
        logger.info("Alert email unsubscribed for account %s", account.pk)
        done = True
    return render(
        request,
        "accounts/alert_unsubscribe.html",
        {"token": token, "done": done},
    )
