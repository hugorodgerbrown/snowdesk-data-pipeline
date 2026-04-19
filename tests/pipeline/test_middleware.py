"""Tests for pipeline.middleware.QueryCountMiddleware."""

from __future__ import annotations

import pytest
from django.test import Client, override_settings


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
