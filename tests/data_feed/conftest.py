"""
tests/data_feed/conftest.py — Shared fixtures for the data feed tests (SNOW-1197).

``feed_token`` is a live service-client token bound to the feed, the
credential every server-side test presents.
"""

from __future__ import annotations

import pytest
from django.core.cache import cache
from oauth2_provider.models import Application

from tests.factories import OAuthAccessTokenFactory, OAuthApplicationFactory

FEED = "http://testserver/api/data/v1/"


@pytest.fixture(autouse=True)
def clear_caches() -> None:
    """Clear the cache, where the rate-limit counters live."""
    cache.clear()


@pytest.fixture
def service_client(db: None) -> Application:
    """Return a service client: a hand-made, confidential client-credentials app."""
    return OAuthApplicationFactory.create(
        name="Staging",
        client_type=Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=Application.GRANT_CLIENT_CREDENTIALS,
        redirect_uris="",
    )


@pytest.fixture
def feed_token(service_client: Application) -> str:
    """Return a live service-client token with the data scope, bound to the feed."""
    raw: str = OAuthAccessTokenFactory.create(
        user=None, application=service_client, scope="data", resource=[FEED]
    ).raw_token
    return raw
