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
    response = client.get(reverse("public:map"))
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


@pytest.mark.django_db
class TestDownloadsSheetCopy:
    """SNOW-1137: the sheet's link, the roundel's name and the region toasts."""

    def test_the_header_no_longer_links_to_the_offline_page(
        self, client: Client
    ) -> None:
        """SNOW-1420: /offline/ is unlisted, so the header's pointer went."""
        html = client.get(reverse("public:map")).content.decode()

        assert "See what this device holds" not in html
        assert f'href="{reverse("public:offline_page")}"' not in html
        assert "See it all in Settings" not in html


@pytest.mark.django_db
class TestDownloadsSheetAnswersOffline:
    """SNOW-1420: the Downloads sheet is the one home for offline.

    Each row carries a verdict dot and one reason line, painted by
    ``map_downloads_manager.js`` from ``map_downloads_sync_all.js``; the
    foot carries Sync all. Every word is rendered here (docs/i18n.md).
    """

    def _template(self, html: str, template_id: str) -> str:
        """The inner markup of one ``<template id=…>``."""
        return html.split(f'<template id="{template_id}">', 1)[1].split(
            "</template>", 1
        )[0]

    def test_the_row_carries_a_verdict_dot_and_a_reason_line(
        self, client: Client
    ) -> None:
        """Both hooks, empty for the script: neutral dot, hidden notice."""
        html = client.get(reverse("public:map")).content.decode()
        row = self._template(html, "map-downloads-row-template")

        assert "data-row-status" in row
        assert "bg-sync-off" in row.split("data-row-status", 1)[1].split(">", 1)[0]
        notice = row.split("data-row-notice", 1)[1].split(">", 1)[0]
        assert "hidden" in notice

    def test_every_answer_is_in_the_strings_template(self, client: Client) -> None:
        """Ready, the two reasons, map missing, unchecked and syncing."""
        html = client.get(reverse("public:map")).content.decode()
        strings = self._template(html, "map-downloads-strings-template")

        for key, words in (
            ("area-status-ready", "Works offline"),
            ("area-reason-conditions", "Bulletins out of date"),
            ("area-reason-things", "Some trips or pins missing"),
            ("area-status-open-map", "Map data missing"),
            ("area-status-unknown", "Not checked yet"),
            ("area-status-running", "Syncing…"),
        ):
            assert f'<span data-string="{key}">{words}</span>' in strings
        assert "Download again" not in strings

    def test_the_row_carries_the_inventory_disclosure(self, client: Client) -> None:
        """SNOW-1418: the shared chevron, and the row's own empty panel."""
        html = client.get(reverse("public:map")).content.decode()
        row = self._template(html, "map-downloads-row-template")

        toggle = row.split("data-row-disclosure", 1)[1].split(">", 1)[0]
        assert 'aria-controls="downloads-row-inventory"' in toggle
        assert 'aria-expanded="false"' in toggle
        assert "hx-get" not in toggle
        assert 'id="downloads-row-inventory"' in row
        assert "data-row-panel" in row

    def test_the_inventory_template_and_its_words_render(self, client: Client) -> None:
        """SNOW-1418: one item with its three hooks, and every label."""
        html = client.get(reverse("public:map")).content.decode()
        inventory = self._template(html, "map-downloads-inventory-template")
        strings = self._template(html, "map-downloads-strings-template")

        for hook in (
            "data-inventory-list",
            "data-inventory-item",
            "data-inventory-dot",
            "data-inventory-label",
            "data-inventory-value",
            "data-inventory-status",
        ):
            assert hook in inventory
        assert "bg-tag" in inventory
        for key, words in (
            ("inventory-toggle-label", "What's in %(name)s"),
            ("inventory-map", "Map"),
            ("inventory-terrain", "Terrain"),
            ("inventory-bulletin", "Avalanche bulletin"),
            ("inventory-weather", "Weather"),
            ("inventory-routes", "Your routes"),
            ("inventory-trips", "Trips"),
            ("inventory-places", "Saved places"),
            ("inventory-tours", "SAC tours"),
            ("inventory-resorts", "Ski resorts"),
            ("inventory-reports", "Community reports"),
            ("inventory-today", "Today"),
            ("inventory-status-partial", "Needs a sync"),
        ):
            assert f'<span data-string="{key}">{words}</span>' in strings

    def test_the_foot_carries_sync_all_first(self, client: Client) -> None:
        """Hidden until the script finds an area on this device to sync."""
        html = client.get(reverse("public:map")).content.decode()
        body = self._template(html, "map-downloads-body-template")
        foot = body.split("data-panel-foot", 1)[1]

        tag = foot.split("data-downloads-sync-all", 1)[1].split(">", 1)[0]
        assert "hidden" in tag
        assert "Sync all" in foot
        assert foot.index("data-downloads-sync-all") < foot.index(
            "data-panel-add-region"
        )

    def test_the_map_loads_the_sync_all_module_before_the_manager(
        self, client: Client
    ) -> None:
        """The manager reads ``window.pwaDownloadsSyncAll`` at render time."""
        html = client.get(reverse("public:map")).content.decode()

        assert html.index("map_downloads_sync_all.js") < html.index(
            "map_downloads_manager.js"
        )

    def test_the_roundel_is_named_manage_downloads(self, client: Client) -> None:
        """The feature is "downloads"; the roundel says so without "offline"."""
        tag = _roundel(client)

        assert (
            'aria-label="Manage downloads"' in tag
            or "aria-label='Manage downloads'" in tag
        )
        assert "Manage offline downloads" not in tag

    def test_a_region_run_has_its_own_quota_and_budget_toasts(
        self, client: Client
    ) -> None:
        """A region cannot be framed smaller, so its toasts never say to."""
        html = " ".join(client.get(reverse("public:map")).content.decode().split())

        for toast_id in (
            "map-download-error-toast-quota-region",
            "map-download-error-toast-budget-region",
        ):
            assert f'id="{toast_id}"' in html
        assert "Not enough storage to save this region." in html
        assert "Raise the budget in Manage downloads" in html
