"""
tests/accounts/test_favourites_page.py — the account area's favourites page
(SNOW-668).

``apps.accounts.views.favourites_view`` renders ``/account/favourites/``: the
signed-in user's saved pins, lifted out of the section they occupied at the
foot of ``/account/`` from SNOW-415 until this ticket.

The routes/observations twin (``tests/routes/test_my_routes.py``,
``tests/observations/test_my_observations.py``) with two differences that are
the whole point of asserting it separately:

  * **The caching posture is the opposite of theirs.** Both of those pages
    set ``Cache-Control: private, no-store`` to stay out of the PWA shell
    cache. This page must NOT, because
    ``static/js/favourites_offline.js`` reads the offline roster from it —
    copying the header across would break a shipped feature while every
    other test still passed. ``TestFavouritesPageCaching`` is that guard.
  * **No rows are rendered server-side.** The list arrives over HTMX from
    ``favourites:list``, so the page's job is to host that endpoint, and the
    rows are asserted one hop away.

Covers:
  routing    — ``accounts:favourites`` resolves to ``/account/favourites/``.
  gating     — anonymous → redirect to sign-in (never 403, never the page);
               a plain non-HTMX GET is the normal case and must not be
               rejected.
  caching    — no ``no-store``, no ``Cache-Control`` avoidance of any kind.
  hosting    — the page carries the ``hx-get`` for ``favourites:list`` and
               the four modules the rows are driven by, in load order.
  rows       — the hosted endpoint renders the owner's favourites, and never
               a stranger's.
  headings   — one visible ``<h1>`` naming the page (the hub's was sr-only,
               because it named nothing).
"""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from tests.factories import FavouriteFactory, UserFactory

PAGE_URL = "/account/favourites/"

# The list endpoint the page hosts. ``@require_htmx``, so a direct fetch in
# these tests has to declare itself as HTMX the way the page's hx-get does.
HTMX_HEADERS: dict[str, Any] = {"HTTP_HX_REQUEST": "true"}


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFavouritesPageRouting:
    """The page is mounted under /account/ and reversible by name."""

    def test_url_reverses_to_the_account_prefix(self) -> None:
        """``accounts:favourites`` is the name, ``/account/`` the prefix.

        Asserted rather than assumed because the nav menu, the hub's former
        section and the offline module all reach this page by name.
        """
        assert reverse("accounts:favourites") == PAGE_URL


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFavouritesPageAuthGate:
    """Anonymous redirects; a plain GET is the normal case."""

    def test_anonymous_redirects_to_sign_in(self, client: Client) -> None:
        """An anonymous visitor is offered the way in, not a 403.

        ``favourites:list`` answers 403 because a fragment has nowhere to
        render a sign-in link. A page does, and the account pages beside
        this one (``accounts:hub``, ``accounts:settings``) redirect.
        """
        response = client.get(PAGE_URL)
        assert response.status_code == 302
        assert response.headers["Location"] == reverse("accounts:sign_in")

    def test_plain_non_htmx_get_renders_the_page(self, client: Client) -> None:
        """A normal browser navigation is the expected request, not a 400.

        The regression this guards: the endpoint this page hosts IS
        ``@require_htmx``, and applying that decorator to the host by habit
        would make the page unreachable by the only means anyone reaches it.
        """
        client.force_login(UserFactory.create())
        assert client.get(PAGE_URL).status_code == 200


# ---------------------------------------------------------------------------
# Caching — the assertion this file exists for
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFavouritesPageCaching:
    """This page must stay cacheable, unlike its two sibling pages."""

    def test_response_does_not_forbid_storage(self, client: Client) -> None:
        """No ``no-store`` on the response, in any form.

        ``my_routes`` and ``my_observations`` both send
        ``Cache-Control: private, no-store`` and both are right to: caching
        those for offline reads belongs elsewhere. Copying the line here is
        the easy mistake, and it is silent — the page would still render,
        every other test would still pass, and the offline favourites roster
        would simply stop resolving, because
        ``static/js/favourites_offline.js`` needs this HTML in the PWA shell
        cache. Safety comes from partitioning, not avoidance: ``_networkFirst``
        in ``static/js/sw.js`` stamps the entry with ``X-SW-Principal`` and
        refuses it to any other account.
        """
        client.force_login(UserFactory.create())
        response = client.get(PAGE_URL)
        assert "no-store" not in response.headers.get("Cache-Control", "")

    def test_response_is_not_never_cache(self, client: Client) -> None:
        """``@never_cache`` would show up as the headers it sets.

        Asserted separately from ``no-store`` because the decorator is the
        other way the same regression arrives, and it also emits ``Expires``
        and a ``max-age=0``.
        """
        client.force_login(UserFactory.create())
        response = client.get(PAGE_URL)
        assert "max-age=0" not in response.headers.get("Cache-Control", "")
        assert "Expires" not in response.headers


# ---------------------------------------------------------------------------
# What the page hosts
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFavouritesPageHosting:
    """The page hosts apps.favourites' own list; it renders no rows itself."""

    def test_page_lazy_loads_the_list_endpoint(self, client: Client) -> None:
        """The ``hx-get`` moved from the hub unchanged.

        Reversed by URL name — the cross-app boundary is that this template
        imports nothing from ``apps.favourites``.
        """
        client.force_login(UserFactory.create())
        html = client.get(PAGE_URL).content.decode()

        assert f'hx-get="{reverse("favourites:list")}"' in html
        assert 'hx-trigger="load"' in html

    def test_page_loads_the_four_row_modules(self, client: Client) -> None:
        """htmx, then inline_rename, row_rename_commit and this page's half.

        Document order is execution order for deferred scripts, and each
        module reads the previous one's ``window.pwa*`` global — so the order
        is load-bearing, not cosmetic. htmx is first and parser-blocking
        because ``account_favourites.js`` calls ``htmx.process`` on a row it
        swapped in itself.
        """
        client.force_login(UserFactory.create())
        html = client.get(PAGE_URL).content.decode()

        positions = [
            html.index("js/htmx.min.js"),
            html.index("js/inline_rename.js"),
            html.index("js/row_rename_commit.js"),
            html.index("js/account_favourites.js"),
        ]
        assert positions == sorted(positions)

    def test_page_carries_the_shared_failure_banner(self, client: Client) -> None:
        """Both write controls reveal one banner, so neither builds copy in JS.

        ``accounts/partials/_htmx_error_banner.html`` — reused, not
        re-authored: a rename failure is a plain fetch and reveals it by
        hand, a Remove failure is HTMX's own listener.
        """
        client.force_login(UserFactory.create())
        html = client.get(PAGE_URL).content.decode()

        assert 'id="htmx-error-banner"' in html


@pytest.mark.django_db
class TestFavouritesPageRows:
    """The rows the hosted endpoint answers with, one HTMX hop away."""

    def test_own_favourites_are_listed(self, client: Client) -> None:
        """The signed-in user's saved pin renders in the hosted list."""
        user = UserFactory.create()
        FavouriteFactory.create(user=user, name="Mont Fort")
        client.force_login(user)

        html = client.get(reverse("favourites:list"), **HTMX_HEADERS).content.decode()

        assert "Mont Fort" in html
        assert 'data-testid="favourite-list"' in html

    def test_another_users_favourites_are_never_listed(self, client: Client) -> None:
        """Ownership is enforced by the query, so a stranger's pin is absent.

        Both users own exactly one favourite and the names differ — so a
        scoping failure shows up as the *other* name appearing, not merely
        as a count.
        """
        viewer = UserFactory.create()
        FavouriteFactory.create(user=viewer, name="Mine")
        FavouriteFactory.create(user=UserFactory.create(), name="Theirs")
        client.force_login(viewer)

        html = client.get(reverse("favourites:list"), **HTMX_HEADERS).content.decode()

        assert "Mine" in html
        assert "Theirs" not in html

    def test_a_user_with_no_favourites_gets_the_empty_clause(
        self, client: Client
    ) -> None:
        """A user with nothing saved sees the list's own empty state."""
        FavouriteFactory.create(user=UserFactory.create(), name="Theirs")
        client.force_login(UserFactory.create())

        html = client.get(reverse("favourites:list"), **HTMX_HEADERS).content.decode()

        assert 'data-testid="favourite-list-empty"' in html
        assert "Theirs" not in html


# ---------------------------------------------------------------------------
# Heading structure
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFavouritesPageHeading:
    """One visible <h1>, naming the page — the thing the hub could not do."""

    def test_page_names_itself_in_a_visible_h1(self, client: Client) -> None:
        """The hub's ``<h1>`` was sr-only because nothing named the page.

        A page holding one list of saved things has an honest name, so the
        heading is visible and the eyebrow that used to label the section is
        gone with it — under a named page it would only repeat the ``<h1>``.
        """
        client.force_login(UserFactory.create())
        html = client.get(PAGE_URL).content.decode()

        assert "Favourites" in html
        assert '<h1 class="sr-only"' not in html
        assert html.count("<h1") == 1
