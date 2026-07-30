"""
tests/public/test_llms_full_txt.py — Tests for the /llms-full.txt endpoint.

``/llms-full.txt`` is the full-URL counterpart to ``/llms.txt`` under the
llmstxt.org convention: one Markdown link per micro-region, ordered by
``region_id``, so an LLM crawler that reads ``/llms.txt`` has a low-token
path to enumerate every canonical bulletin URL without walking the
sitemap or the map page (SNOW-393).

Tests fetch the file over HTTP via the test ``Client`` so the assertions
cover the live response shape, headers, and templated URL substitution.
"""

from __future__ import annotations

import re

import pytest
from django.test import Client, override_settings

from tests.factories import MajorRegionFactory, MicroRegionFactory, SubRegionFactory


def _body() -> str:
    """Fetch ``/llms-full.txt`` via the test client and return the decoded body."""
    response = Client().get("/llms-full.txt")
    assert response.status_code == 200
    return response.content.decode("utf-8")


@pytest.mark.django_db
def test_llms_full_served_as_markdown() -> None:
    """``/llms-full.txt`` returns 200 with a ``text/markdown`` content type."""
    response = Client().get("/llms-full.txt")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/markdown")


@pytest.mark.django_db
def test_llms_full_header_matches_shape_from_llms_txt() -> None:
    """The header mirrors the /llms.txt shape (H1 + blockquote) so the file stands alone."""
    body = _body()
    assert body.lstrip().startswith("# Snowdesk — full URL index")
    assert "\n> " in body
    assert "## Regions" in body


@pytest.mark.django_db
def test_llms_full_lists_every_microregion_ordered_by_region_id() -> None:
    """Every MicroRegion appears once, in region_id order (SNOW-393)."""
    # Deliberately unsorted creation order so the sort assertion is meaningful.
    major = MajorRegionFactory.create(
        prefix="CH-4",
        country="CH",
        name_native="Valais",
        name_en="Valais",
    )
    subregion = SubRegionFactory.create(major=major)
    MicroRegionFactory.create(
        region_id="CH-4200",
        name="Aletsch",
        subregion=subregion,
    )
    MicroRegionFactory.create(
        region_id="CH-4115",
        name="Martigny-Verbier",
        subregion=subregion,
    )
    MicroRegionFactory.create(
        region_id="CH-4300",
        name="Zermatt",
        subregion=subregion,
    )

    body = _body()

    # Region_ids appear in ascending order.
    positions = [
        body.index("/ch-4115/"),
        body.index("/ch-4200/"),
        body.index("/ch-4300/"),
    ]
    assert positions == sorted(positions), (
        f"region URLs must appear in region_id order; got positions {positions}"
    )
    # Every region name is present.
    assert "Martigny-Verbier" in body
    assert "Aletsch" in body
    assert "Zermatt" in body


@pytest.mark.django_db
def test_llms_full_urls_are_evergreen_not_dated() -> None:
    """Every region URL is form-2 evergreen (no ``YYYY-MM-DD`` segment)."""
    major = MajorRegionFactory.create(country="CH")
    subregion = SubRegionFactory.create(major=major)
    MicroRegionFactory.create(
        region_id="CH-4115",
        name="Martigny-Verbier",
        subregion=subregion,
    )

    body = _body()
    region_urls = re.findall(r"/(?:ch|at|it|fr)-[a-z0-9-]+/[a-z0-9-]+/", body)
    assert region_urls, "expected at least one region URL"
    date_segment = re.compile(r"/\d{4}-\d{2}-\d{2}/?$")
    for url in region_urls:
        assert not date_segment.search(url), (
            f"llms-full URL still carries a date segment: {url!r}"
        )


@pytest.mark.django_db
def test_llms_full_absolute_urls_pin_to_site_base_url() -> None:
    """Links are built from ``SITE_BASE_URL`` so they resolve per environment."""
    major = MajorRegionFactory.create(country="CH")
    subregion = SubRegionFactory.create(major=major)
    MicroRegionFactory.create(
        region_id="CH-4115",
        name="Martigny-Verbier",
        subregion=subregion,
    )

    with override_settings(SITE_BASE_URL="https://snowdesk.info"):
        body = _body()
    assert "https://snowdesk.info/ch-4115/" in body
    # No environment-relative paths leaked in as bare links.
    assert "](/ch-4115/" not in body


@pytest.mark.django_db
def test_llms_full_line_carries_country_and_major_region() -> None:
    """Each row surfaces country + major-region name so the index reads as a directory."""
    major = MajorRegionFactory.create(
        prefix="CH-4",
        country="CH",
        name_native="Wallis",
        name_en="Valais",
    )
    subregion = SubRegionFactory.create(major=major)
    MicroRegionFactory.create(
        region_id="CH-4115",
        name="Martigny-Verbier",
        subregion=subregion,
    )

    body = _body()
    # The country name (from apps.public.api.COUNTRY_NAMES) and the English
    # major-region name both appear on the same line as the region link.
    line = next(line for line in body.splitlines() if "/ch-4115/" in line)
    assert "Switzerland" in line
    assert "Valais" in line


@pytest.mark.django_db
def test_llms_full_is_cacheable() -> None:
    """A public Cache-Control header is set so the index caches for one hour."""
    response = Client().get("/llms-full.txt")
    cache_control = response["Cache-Control"]
    assert "public" in cache_control
    assert "max-age=3600" in cache_control


@override_settings(POSTHOG_API_KEY="phc_test")
@pytest.mark.django_db
def test_llms_full_no_cookie_vary_with_analytics_enabled() -> None:
    """SNOW-338 regression pattern: Vary: Cookie must be absent even with PostHog active.

    ``/llms-full.txt`` is a public-good cacheable document. Without the
    ``_POSTHOG_EXEMPT_PATHS`` exemption, ``PosthogContextMiddleware``
    would touch ``request.user`` and cause ``SessionMiddleware`` to
    append ``Vary: Cookie``, defeating shared caches.
    """
    response = Client().get("/llms-full.txt")
    assert "public" in response.get("Cache-Control", "")
    vary = response.get("Vary", "")
    assert "Cookie" not in vary, (
        f"Vary: Cookie must not be set even with analytics enabled; got: {vary!r}"
    )


@pytest.mark.django_db
def test_llms_txt_references_llms_full_txt() -> None:
    """SNOW-393: /llms.txt lists /llms-full.txt under ## Data."""
    response = Client().get("/llms.txt")
    body = response.content.decode("utf-8")
    assert "/llms-full.txt" in body
    assert "Full URL index" in body
