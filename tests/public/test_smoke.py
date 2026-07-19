"""
tests/public/test_smoke.py — Smoke test: seeded dataset + canonical URLs.

Verifies the contract-level guarantee that seeding a freshly migrated DB brings
it to a fully navigable state. The dataset is built by ``seed_test_dataset``
(``loaddata eaws_CH resorts`` + ``seed_test_data --all --commit``) — the
factory-based path that replaced the old ``loaddata test_data`` fixture.

It asserts that the canonical preview URL ``/ch-4115/martigny-verbier/2026-04-08/``
returns HTTP 200, that the Lighthouse URL ``/ch-4115/martigny-verbier/2026-04-02/``
also returns HTTP 200, and that ``/examples/random/`` also returns HTTP 200
(picking a random bulletin from the seeded data).

This test is deliberately slow (it seeds the full dataset and builds every render
model) and is the only test in the suite that exercises the seed path. All other
tests use factories directly so they remain fast.

The ``_disable_inline_weather_warmup`` autouse fixture in ``conftest.py``
patches out ``public.views.fetch_weather_async`` so the implicit warmup
triggered by a past-date render never reaches the network here.
"""

from __future__ import annotations

import pytest
from django.test import Client

from tests.seeding import seed_test_dataset


@pytest.mark.django_db(transaction=True)
def test_seed_and_canonical_url_returns_200() -> None:
    """
    seed_test_dataset + GET /ch-4115/martigny-verbier/2026-04-08/ → HTTP 200.

    This is the acceptance-test for the seed path: it proves that seeding is
    sufficient to serve a fully navigable bulletin page for the canonical
    preview date.
    """
    seed_test_dataset()

    client = Client()
    response = client.get("/ch-4115/martigny-verbier/2026-04-08/")
    assert response.status_code == 200, (
        f"Expected 200 from /ch-4115/martigny-verbier/2026-04-08/ but got "
        f"{response.status_code}"
    )


@pytest.mark.django_db(transaction=True)
def test_seed_and_ch4115_april_url_returns_200() -> None:
    """
    seed_test_dataset + GET /ch-4115/martigny-verbier/2026-04-02/ → HTTP 200.

    Verifies that a non-map-reference-date URL for the detail region also
    renders correctly after seeding.
    """
    seed_test_dataset()

    client = Client()
    response = client.get("/ch-4115/martigny-verbier/2026-04-02/")
    assert response.status_code == 200, (
        f"Expected 200 from /ch-4115/martigny-verbier/2026-04-02/ but got "
        f"{response.status_code}"
    )


@pytest.mark.django_db(transaction=True)
def test_seed_and_examples_random_returns_200() -> None:
    """
    seed_test_dataset + GET /examples/random/ → HTTP 200.

    Verifies that the random-example view picks a bulletin from the seeded data
    and renders it inline, confirming the seed provides enough data for this
    evergreen URL to work without additional setup.
    """
    seed_test_dataset()

    client = Client()
    response = client.get("/examples/random/")
    assert response.status_code == 200, (
        f"Expected 200 from /examples/random/ but got {response.status_code}"
    )
