"""Tests for apps.core.middleware — Query, Security, and AppVersion header middleware."""

from __future__ import annotations

import pytest
from django.test import Client, override_settings

from apps.accounts.services.token import (
    SALT_ACCOUNT_ACCESS,
    generate_token,
    generate_unsubscribe_token,
)
from tests.factories import AccountFactory, MicroRegionFactory, SubscriptionFactory


@pytest.mark.django_db
@override_settings(QUERY_COUNT_HEADER_ENABLED=True)
def test_header_present_when_enabled() -> None:
    """When enabled, every response carries X-DB-Query-Count with an int."""
    response = Client().get("/")
    assert response.status_code == 200
    assert "X-DB-Query-Count" in response
    assert int(response["X-DB-Query-Count"]) >= 0


@pytest.mark.django_db
@override_settings(QUERY_COUNT_HEADER_ENABLED=False)
def test_header_absent_when_disabled() -> None:
    """Disabled flag makes the middleware a no-op — no header on responses."""
    response = Client().get("/")
    assert "X-DB-Query-Count" not in response


@pytest.mark.django_db
@override_settings(QUERY_COUNT_HEADER_ENABLED=True)
def test_header_counts_queries_on_db_view() -> None:
    """A view that touches the DB reports a count >= 1."""
    # SNOW-344: /map/ is now a 301 redirect; use an API endpoint that is
    # known to query the database.
    response = Client().get("/api/regions.geojson?country=ch")
    assert response.status_code == 200
    assert int(response["X-DB-Query-Count"]) >= 1


# ---------------------------------------------------------------------------
# SecurityHeadersMiddleware
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_referrer_policy_present_on_normal_response() -> None:
    """Every response carries Referrer-Policy with the strict-origin default."""
    response = Client().get("/")
    assert response.status_code == 200
    assert response["Referrer-Policy"] == "strict-origin-when-cross-origin"


@pytest.mark.django_db
def test_permissions_policy_present_on_normal_response() -> None:
    """Every response carries Permissions-Policy disabling sensitive APIs."""
    response = Client().get("/")
    assert response.status_code == 200
    policy = response["Permissions-Policy"]
    assert "camera=()" in policy
    assert "microphone=()" in policy
    assert "geolocation=(self)" in policy
    assert "geolocation=()" not in policy


@pytest.mark.django_db
def test_account_view_get_sets_same_origin() -> None:
    """account_view GET confirm page uses same-origin so its POST passes CSRF.

    A ``no-referrer`` policy makes the browser send ``Origin: null`` on the
    subsequent same-origin POST, which Django's CSRF middleware rejects on
    HTTPS (SNOW-438). ``same-origin`` still strips the token-bearing URL from
    any cross-origin request.
    """
    account = AccountFactory.create()
    token = generate_token(account.user.email, salt=SALT_ACCOUNT_ACCESS)
    response = Client().get(f"/account/access/{token}/")
    assert response.status_code == 200
    assert response["Referrer-Policy"] == "same-origin"


@pytest.mark.django_db
def test_account_view_error_keeps_no_referrer() -> None:
    """The link-expired error page (no POST form) still uses no-referrer."""
    response = Client().get("/account/access/garbage-token/")
    assert response.status_code == 400
    assert response["Referrer-Policy"] == "no-referrer"


@pytest.mark.django_db
def test_unsubscribe_view_get_sets_same_origin() -> None:
    """unsubscribe_view GET renders a POST form → same-origin (SNOW-438)."""
    region = MicroRegionFactory.create()
    account = AccountFactory.create()
    SubscriptionFactory.create(account=account, region=region)
    token = generate_unsubscribe_token(account.user.email, region.region_id)
    response = Client().get(f"/account/unsubscribe/{token}/")
    assert response.status_code == 200
    assert response["Referrer-Policy"] == "same-origin"


@pytest.mark.django_db
def test_view_override_takes_precedence_over_middleware_default() -> None:
    """A view-set Referrer-Policy survives the middleware (not overwritten)."""
    region = MicroRegionFactory.create()
    account = AccountFactory.create()
    SubscriptionFactory.create(account=account, region=region)
    token = generate_unsubscribe_token(account.user.email, region.region_id)
    response = Client().get(f"/account/unsubscribe/{token}/")
    # Must be the view-set value, not the middleware default.
    assert response["Referrer-Policy"] == "same-origin"
    assert response["Referrer-Policy"] != "strict-origin-when-cross-origin"


# ---------------------------------------------------------------------------
# AppVersionHeaderMiddleware (SNOW-369, spec §5.3 / §12.2)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(APP_VERSION="2026.07.15.testabc")
def test_app_version_header_present_on_page_response() -> None:
    """Every response carries X-App-Version.

    The PWA client parses it on every response, not just on
    ``/api/version`` polls — see spec §5.3.
    """
    response = Client().get("/")
    assert response["X-App-Version"] == "2026.07.15.testabc"


@pytest.mark.django_db
@override_settings(APP_VERSION="2026.07.15.testabc")
def test_app_version_header_present_on_api_response() -> None:
    """Non-page (JSON API) responses also carry the version header."""
    response = Client().get("/api/regions.geojson?country=ch")
    assert response.status_code == 200
    assert response["X-App-Version"] == "2026.07.15.testabc"


@pytest.mark.django_db
def test_min_version_header_is_never_stamped() -> None:
    """``X-App-Min-Version`` is gone — SNOW-609.

    The header carried a floor the client compared by string inequality,
    which is meaningless against a git-SHA ``APP_VERSION``: every client
    read as "below the floor". Its absence is load-bearing, not incidental
    — shells deployed before SNOW-609 treat a missing floor as "no floor
    enforced", so removing the header disarms the buggy comparison in
    clients we cannot update.
    """
    response = Client().get("/")
    assert "X-App-Min-Version" not in response
