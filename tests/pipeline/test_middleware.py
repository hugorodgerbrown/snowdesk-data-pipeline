"""Tests for pipeline.middleware — QueryCountMiddleware and SecurityHeadersMiddleware."""

from __future__ import annotations

import pytest
from django.test import Client, override_settings

from subscriptions.services.token import (
    SALT_ACCOUNT_ACCESS,
    generate_token,
    generate_unsubscribe_token,
)
from tests.factories import RegionFactory, SubscriberFactory, SubscriptionFactory


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
    # /map/ is a template-only view but the session + auth middleware
    # may not issue queries either. Use an API endpoint that is known to
    # query the database.
    response = Client().get("/api/regions.geojson")
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
    assert "geolocation=()" in policy


@pytest.mark.django_db
def test_account_view_sets_no_referrer() -> None:
    """account_view overrides Referrer-Policy to no-referrer (token in URL)."""
    subscriber = SubscriberFactory.create()
    token = generate_token(subscriber.email, salt=SALT_ACCOUNT_ACCESS)
    response = Client().get(f"/subscribe/account/{token}/")
    # Redirects or error page — both should carry no-referrer.
    assert response["Referrer-Policy"] == "no-referrer"


@pytest.mark.django_db
def test_unsubscribe_view_get_sets_no_referrer() -> None:
    """unsubscribe_view GET overrides Referrer-Policy to no-referrer."""
    region = RegionFactory.create()
    subscriber = SubscriberFactory.create()
    SubscriptionFactory.create(subscriber=subscriber, region=region)
    token = generate_unsubscribe_token(subscriber.email, region.region_id)
    response = Client().get(f"/subscribe/unsubscribe/{token}/")
    assert response.status_code == 200
    assert response["Referrer-Policy"] == "no-referrer"


@pytest.mark.django_db
def test_view_override_takes_precedence_over_middleware_default() -> None:
    """A view-set no-referrer survives the middleware (not overwritten)."""
    region = RegionFactory.create()
    subscriber = SubscriberFactory.create()
    SubscriptionFactory.create(subscriber=subscriber, region=region)
    token = generate_unsubscribe_token(subscriber.email, region.region_id)
    response = Client().get(f"/subscribe/unsubscribe/{token}/")
    # Must be no-referrer, not the middleware default.
    assert response["Referrer-Policy"] == "no-referrer"
    assert response["Referrer-Policy"] != "strict-origin-when-cross-origin"
