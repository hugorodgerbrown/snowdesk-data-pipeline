"""
tests/public/test_downloads_surface.py — the map's downloads surface (SNOW-749).

Covers ``apps.public.views._downloads_context`` and the one element its
output reaches the page through: ``#map-custom-download-control``, the
bottom-right roundel, which SNOW-749 makes the config carrier for BOTH
download start controls and for the account sync.

Three things are worth asserting here and nowhere else:

  1. **The gate and the pass are separate attributes.**
     ``data-downloads-sync`` is the ``download_sync`` rollout flag;
     ``data-downloads-eligible`` is authentication. Folding them into one
     boolean would leave the client unable to tell "the gate is off" from
     "this visitor passed the gate", and those paint differently — one is
     the pre-SNOW-749 behaviour, the other is a sign-in CTA.

  2. **Flag off is the old behaviour.** The whole rollout plan rests on
     it, and an anonymous visitor with the flag off is the exact request
     that shipped before this ticket.

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
from waffle.testutils import override_flag

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

    def test_the_flag_is_reported_separately_from_eligibility(
        self, rf: RequestFactory
    ) -> None:
        """The gate and the pass are two facts, so they are two keys."""
        request = rf.get("/")
        request.user = UserFactory.create()

        with override_flag("download_sync", active=True):
            assert _downloads_context(request)["downloads_sync_enabled"] is True
        with override_flag("download_sync", active=False):
            assert _downloads_context(request)["downloads_sync_enabled"] is False
        # Eligibility does not move with the flag — see the module docstring.
        assert _downloads_context(request)["downloads_eligible"] is True

    def test_it_costs_no_query_of_its_own(
        self, rf: RequestFactory, django_assert_num_queries: Any
    ) -> None:
        """Every URL is ``reverse()``; nothing here looks a row up.

        The homepage's query-count budget is monitored
        (docs/query-counts.md), and a context helper that quietly costs a
        query per request is how that budget moves without anyone deciding
        to move it. The flag is the one thing that can touch the DB, and
        waffle caches it — so the SECOND call is the honest measurement of
        what this helper itself costs, and it is zero.
        """
        request = rf.get("/")
        request.user = AnonymousUser()

        _downloads_context(request)
        with django_assert_num_queries(0):
            _downloads_context(request)


@pytest.mark.django_db
class TestDownloadsRoundelAttributes:
    """The roundel carries the wiring both start controls read off it."""

    def test_it_carries_every_attribute_the_js_reads(self, client: Client) -> None:
        """Seven attributes, one element.

        map_region_download.js, map_downloads_manager.js and
        downloads_sync.js all read their configuration from this element —
        the region roundel's own button deliberately carries none of it, so
        a missing attribute here disables the gate silently rather than
        loudly.
        """
        tag = _roundel(client)

        for attribute in (
            "data-downloads-sync",
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

    @override_flag("download_sync", active=False)
    def test_the_gate_is_off_by_default(self, client: Client) -> None:
        """Flag off — the client applies no gate, exactly as before SNOW-749."""
        assert 'data-downloads-sync="false"' in _roundel(client)

    @override_flag("download_sync", active=True)
    def test_the_gate_is_on_with_the_flag(self, client: Client) -> None:
        """Flag on — the client applies the sign-in gate and syncs."""
        assert 'data-downloads-sync="true"' in _roundel(client)
