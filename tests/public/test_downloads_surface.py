"""
tests/public/test_downloads_surface.py — the map's downloads surface (SNOW-749).

Covers ``apps.public.views._downloads_context`` and the one element its
output reaches the page through: ``#map-custom-download-control``, the
bottom-right roundel, which SNOW-749 makes the config carrier for BOTH
download start controls and for the account sync.

Three things are worth asserting here and nowhere else:

  1. **Eligibility is the whole gate, and it is one attribute.**
     ``data-downloads-eligible`` is authentication, and the client paints
     a sign-in state from it. SNOW-749 briefly carried a second attribute
     beside it for a ``download_sync`` rollout flag; the flag was dropped
     before merge on query cost, so there is one fact here now and one
     attribute for it.

  2. **The helper costs no query.** That is why the flag went, so it is
     the property most worth pinning against a future edit that quietly
     reintroduces a lookup — the homepage is the site's most-requested
     page and its count is monitored (docs/query-counts.md).

  3. **The URL templates survive ``<str:area_id>``.** ``_downloads_context``
     reverses with a placeholder and string-replaces it, the same trick
     ``_favourites_context`` uses for ``__UUID__`` — but a bracketed token
     would not match the converter, so the placeholder is a bare
     alphanumeric run and the round trip has to be pinned.

The endpoints behind those URLs are covered in ``tests/downloads/``; the
client behaviour they drive is covered in ``tests/js/``. This module is
only about what the page ships.

Scoped to the Django test client — rendering a template needs no browser.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.public.views import _downloads_context
from tests.factories import UserFactory

# The roundel's opening tag, up to the first ``>``. Every attribute this
# module asserts lives on that one element, so the assertions are scoped to
# it rather than to the whole 200 KB page — an attribute that moved onto
# some other control would otherwise still pass.
_ROUNDEL_RE = re.compile(r'<button[^>]*id="map-custom-download-control"[^>]*>')


def _roundel(client: Client) -> str:
    """Render the homepage and return the downloads roundel's opening tag."""
    response = client.get(reverse("public:home"))
    assert response.status_code == 200
    match = _ROUNDEL_RE.search(response.content.decode())
    assert match is not None, "#map-custom-download-control is not on the page"
    return match.group(0)


@pytest.mark.django_db
class TestDownloadsContext:
    """``_downloads_context`` for both visitor states."""

    def test_an_anonymous_visitor_is_not_eligible(self, rf: RequestFactory) -> None:
        """Anonymous means no download may be STARTED once the flag is on."""
        request = rf.get("/")
        request.user = AnonymousUser()

        assert _downloads_context(request)["downloads_eligible"] is False

    def test_an_authenticated_visitor_is_eligible(self, rf: RequestFactory) -> None:
        """A signed-in user passes the gate."""
        request = rf.get("/")
        request.user = UserFactory.create()

        assert _downloads_context(request)["downloads_eligible"] is True

    def test_every_url_is_present(self, rf: RequestFactory) -> None:
        """All five URLs are emitted for every visitor.

        They are ``reverse()`` output rather than data, and each endpoint
        403s an anonymous request on its own — so there is nothing to
        withhold, and withholding the sign-in URL would leave the
        signed-out path unable to say where it leads.
        """
        request = rf.get("/")
        request.user = UserFactory.create()
        ctx = _downloads_context(request)

        assert ctx["download_sync_url"] == reverse("downloads:sync")
        assert ctx["download_areas_url"] == reverse("downloads:areas")
        assert ctx["downloads_signin_url"] == reverse("accounts:sign_in")

    def test_the_rename_and_forget_urls_are_area_id_templated(
        self, rf: RequestFactory
    ) -> None:
        """``__AREA_ID__`` round-trips through the ``<str:area_id>`` converter.

        The placeholder reversed with has to be something the converter
        accepts, so it is ``AREAID`` rather than a bracketed token — and
        the replacement has to leave a marker the client can substitute
        into. Both halves are asserted, because getting either wrong
        yields a URL that looks plausible and 404s.
        """
        request = rf.get("/")
        request.user = UserFactory.create()
        ctx = _downloads_context(request)

        assert ctx["download_rename_url_template"].endswith("/__AREA_ID__/rename/")
        assert ctx["download_forget_url_template"].endswith("/__AREA_ID__/forget/")
        # The substituted URL is the one the endpoint actually serves.
        assert ctx["download_forget_url_template"].replace(
            "__AREA_ID__", "region-ch-4115"
        ) == reverse("downloads:forget", args=["region-ch-4115"])

    def test_it_costs_no_query_at_all(
        self, rf: RequestFactory, django_assert_num_queries: Any
    ) -> None:
        """Every value is ``reverse()``; nothing here looks anything up.

        The reason this is a test and not a comment: SNOW-749 shipped a
        ``download_sync`` waffle flag read from this helper, which took the
        homepage from 5 queries to 8 and had to be rebased into
        ``perf/query_counts.txt``. The flag was removed for exactly that,
        so the zero is the decision — asserted on the FIRST call, with no
        cache to warm, because a cached lookup is still a lookup (waffle
        reads through Django's ``default`` cache, which in production is
        ``DatabaseCache``).
        """
        request = rf.get("/")
        request.user = AnonymousUser()

        with django_assert_num_queries(0):
            _downloads_context(request)


@pytest.mark.django_db
class TestDownloadsRoundelAttributes:
    """The roundel carries the wiring both start controls read off it."""

    def test_it_carries_every_attribute_the_js_reads(self, client: Client) -> None:
        """Six attributes, one element.

        map_region_download.js, map_downloads_manager.js and
        downloads_sync.js all read their configuration from this element —
        the region roundel's own button deliberately carries none of it, so
        a missing attribute here disables the gate silently rather than
        loudly.
        """
        tag = _roundel(client)

        for attribute in (
            "data-downloads-eligible",
            "data-signin-url",
            "data-download-sync-url",
            "data-download-areas-url",
            "data-download-rename-url-template",
            "data-download-forget-url-template",
        ):
            assert attribute in tag, f"{attribute} missing from the roundel"

    def test_an_anonymous_visitor_reads_as_ineligible(self, client: Client) -> None:
        """The signed-out state the sign-in CTA branches on."""
        assert 'data-downloads-eligible="false"' in _roundel(client)

    def test_a_signed_in_visitor_reads_as_eligible(self, client: Client) -> None:
        """And the signed-in state, which starts a real run."""
        user = UserFactory.create()
        client.force_login(user)

        assert 'data-downloads-eligible="true"' in _roundel(client)

    def test_it_carries_no_rollout_flag_attribute(self, client: Client) -> None:
        """``data-downloads-sync`` is gone, not rendered always-true.

        The flag it carried was dropped on query cost. An attribute left
        behind reading ``"true"`` on every request would be a gate with no
        off state that three JS modules still branch on — dead weight that
        reads as a live control.
        """
        assert "data-downloads-sync" not in _roundel(client)
