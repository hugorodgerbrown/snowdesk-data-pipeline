"""
tests/public/test_smoke.py — Smoke test: loaddata test_data + canonical URL.

Verifies the contract-level guarantee that a single ``loaddata test_data``
invocation brings a freshly migrated DB to a fully navigable state.

The test loads the checked-in ``bulletins/fixtures/test_data.json`` fixture
(which bundles region, resort, bulletin, day-rating, and weather-snapshot data)
and asserts that the canonical preview URL for CH-4222/zermatt/2026-04-08/
returns HTTP 200.

This test is deliberately slow (it loads 1 155 records) and is the only test
in the suite that exercises ``loaddata test_data``.  All other tests use
factories so they remain fast.

The ``_disable_inline_weather_warmup`` autouse fixture in ``conftest.py``
patches out ``public.views.fetch_weather_async`` so the implicit warmup
triggered by a past-date render never reaches the network here.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.test import Client


# Use ``django_db_reset_sequences`` so the auto-increment counters start fresh
# and do not clash with any factory-created rows in the same test session.
@pytest.mark.django_db(transaction=True)
def test_loaddata_and_canonical_url_returns_200() -> None:
    """
    loaddata test_data + GET /ch-4222/zermatt/2026-04-08/ → HTTP 200.

    This is the acceptance-test for the SNOW-215 fixture: it proves
    that a single loaddata invocation is sufficient to serve a fully
    navigable bulletin page for the canonical preview date.
    """
    call_command("loaddata", "test_data", verbosity=0)

    client = Client()
    response = client.get("/ch-4222/zermatt/2026-04-08/")
    assert response.status_code == 200, (
        f"Expected 200 from /ch-4222/zermatt/2026-04-08/ but got {response.status_code}"
    )


@pytest.mark.django_db(transaction=True)
def test_loaddata_and_ch4115_april_url_returns_200() -> None:
    """
    loaddata test_data + GET /ch-4115/martigny-verbier/2026-04-02/ → HTTP 200.

    Verifies that a non-map-reference-date URL for the detail region
    also renders correctly after a single loaddata invocation.
    """
    call_command("loaddata", "test_data", verbosity=0)

    client = Client()
    response = client.get("/ch-4115/martigny-verbier/2026-04-02/")
    assert response.status_code == 200, (
        f"Expected 200 from /ch-4115/martigny-verbier/2026-04-02/ but got "
        f"{response.status_code}"
    )
