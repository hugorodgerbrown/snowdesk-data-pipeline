"""
tests/routes/test_my_routes.py — Tests for the account area's routes page
(SNOW-713).

``apps.routes.views.my_routes`` renders ``/account/routes/``: the signed-in
user's own saved GPX routes. It is a full page, not a fragment, so it shares
none of the rules the HTMX endpoints in ``tests/routes/test_views.py`` are
held to — and each difference is asserted here rather than assumed.

The routes twin of ``tests/observations/test_my_observations.py``. It carried
a second gate — the ``routes`` waffle flag — until SNOW-724 retired it, so
authentication is now the whole story.

Covers:
  routing       — ``accounts:routes`` resolves to ``/account/routes/``.
  gating        — anonymous → redirect to sign-in (never 403, never the
                  page); a plain non-HTMX GET is the normal case and must
                  NOT be rejected; any authenticated user gets the page.
  owner scope   — the user's own rows render; another user's never do.
  caching       — ``Cache-Control: private, no-store``.
  empty state   — a user with no uploads gets the list partial's own empty
                  clause, not a bare heading, and that clause names a source
                  for a .gpx (SNOW-721); a user WITH routes gets neither the
                  clause nor its outbound link.
  reuse         — the page renders the shared UGC row with BOTH its controls,
                  and supplies the rename endpoint account_routes.js needs.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from tests.factories import RouteFactory, UserFactory

PAGE_URL = "/account/routes/"

# The planner the empty clause links (SNOW-721). Asserted rather than
# spelled out per test, so a change of destination touches one line.
PLANNER_URL = "https://routeplanner.suunto.com/"


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMyRoutesRouting:
    """The page is mounted under /account/ and reversible by name."""

    def test_url_reverses_to_the_account_prefix(self) -> None:
        """``accounts:routes`` is the name, ``/account/`` the prefix.

        The route is declared in ``apps/accounts/urls.py`` (which owns the
        prefix) but points at a view in ``apps.routes``. This asserts the
        pairing, so moving either half without the other fails here.
        """
        assert reverse("accounts:routes") == PAGE_URL


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMyRoutesAuthGate:
    """Anonymous redirects; a plain GET is the normal case."""

    def test_anonymous_redirects_to_sign_in(self, client: Client) -> None:
        """An anonymous visitor is offered the way in, not a 403.

        The HTMX endpoints answer 403 because a fragment has nowhere to
        render a sign-in link. A page does, and the account pages beside
        this one (``accounts:hub``, ``accounts:settings``) redirect.
        """
        response = client.get(PAGE_URL)
        assert response.status_code == 302
        assert response.headers["Location"] == reverse("accounts:sign_in")

    def test_plain_non_htmx_get_renders_the_page(self, client: Client) -> None:
        """A normal browser navigation is the expected request, not a 400.

        The regression this guards: every other GET in ``apps.routes.views``
        is ``@require_htmx``, and applying that decorator here by habit would
        make the page unreachable by the only means anyone reaches it.
        """
        client.force_login(UserFactory.create())
        assert client.get(PAGE_URL).status_code == 200


@pytest.mark.django_db
class TestMyRoutesIsOpenToEveryAccount:
    """SNOW-724: authentication is the only gate the page carries.

    Replaces the ``routes``-flag suite that asserted a 404 for a signed-in
    user without the flag. The flag is gone; being signed in is enough, and
    an owner of routes gets them listed rather than hidden.
    """

    def test_any_authenticated_user_gets_the_page(self, client: Client) -> None:
        """No flag, no superuser bit — an ordinary account renders it."""
        client.force_login(UserFactory.create(is_staff=False))
        assert client.get(PAGE_URL).status_code == 200

    def test_an_owner_of_routes_sees_them(self, client: Client) -> None:
        """The case the old flag-off test hid: the rows are on the page."""
        user = UserFactory.create(is_staff=False)
        RouteFactory.create(user=user, name="Haute Route")
        client.force_login(user)
        response = client.get(PAGE_URL)
        assert response.status_code == 200
        assert b"Haute Route" in response.content


# ---------------------------------------------------------------------------
# Owner scoping
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMyRoutesOwnerScope:
    """Own rows render; another user's never do."""

    def test_own_routes_are_listed(self, client: Client) -> None:
        """The signed-in user sees their own route, with its figures."""
        user = UserFactory.create()
        RouteFactory.create(
            user=user, name="Haute Route", distance_m=2500.0, ascent_m=100.0
        )
        client.force_login(user)
        html = client.get(PAGE_URL).content.decode()
        assert "Haute Route" in html
        # The meta line the shared row renders: distance, ascent, descent.
        assert "2.5 km" in html

    def test_another_users_routes_are_never_listed(self, client: Client) -> None:
        """Ownership is enforced by the query, so a stranger's row is absent.

        Both users own exactly one route and the names differ — so a scoping
        failure shows up as the *other* name appearing, not merely as a count.
        """
        viewer = UserFactory.create()
        stranger = UserFactory.create()
        RouteFactory.create(user=viewer, name="Mine")
        RouteFactory.create(user=stranger, name="Theirs")
        client.force_login(viewer)
        html = client.get(PAGE_URL).content.decode()
        assert "Mine" in html
        assert "Theirs" not in html

    def test_a_users_own_page_is_not_a_stranger_oracle(self, client: Client) -> None:
        """A user with no routes sees the empty state, not someone else's."""
        RouteFactory.create(user=UserFactory.create(), name="Theirs")
        client.force_login(UserFactory.create())
        response = client.get(PAGE_URL)
        assert b'data-testid="route-list-empty"' in response.content
        assert b"Theirs" not in response.content


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMyRoutesCaching:
    """Per-user content must never land in a shared cache."""

    def test_response_is_private_no_store(self, client: Client) -> None:
        """``private, no-store``, mirroring ``routes_geojson``.

        This also keeps the page out of the PWA shell cache — caching routes
        for offline reads belongs with the map layer, not here.
        """
        client.force_login(UserFactory.create())
        response = client.get(PAGE_URL)
        assert response.headers["Cache-Control"] == "private, no-store"


# ---------------------------------------------------------------------------
# Empty state and shared-partial reuse
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMyRoutesEmptyState:
    """A user with no uploads gets a sentence, not a bare heading."""

    def test_empty_clause_renders(self, client: Client) -> None:
        """The list partial's own empty clause is present."""
        client.force_login(UserFactory.create())
        html = client.get(PAGE_URL).content.decode()
        assert 'data-testid="route-list-empty"' in html
        assert "no saved routes yet" in html

    def test_empty_clause_links_a_route_planner(self, client: Client) -> None:
        """The clause says where a .gpx comes from, and links one planner.

        SNOW-721. "You have no saved routes yet." states a fact a newcomer
        cannot act on: the CTA opens a file picker onto a folder with no
        .gpx in it. The link is the actionable half, so it is asserted
        rather than left to the copy.
        """
        client.force_login(UserFactory.create())
        html = client.get(PAGE_URL).content.decode()
        assert PLANNER_URL in html
        assert 'rel="noopener"' in html

    def test_empty_clause_is_absent_once_a_route_exists(self, client: Client) -> None:
        """A user WITH a route gets neither the clause nor the outbound link.

        The regression that matters if the include ever lands outside the
        ``{% if not routes %}`` guard: an owner of routes being told they
        have none, and shown a competitor's planner while looking at their
        own tracks.
        """
        user = UserFactory.create()
        RouteFactory.create(user=user, name="Mine")
        client.force_login(user)
        html = client.get(PAGE_URL).content.decode()
        assert 'data-testid="route-list-empty"' not in html
        assert PLANNER_URL not in html


@pytest.mark.django_db
class TestMyRoutesSharedPartials:
    """The page hosts the shared row rather than authoring a second one."""

    def test_row_carries_both_shared_controls(self, client: Client) -> None:
        """One anatomy for every UGC row (SNOW-711): pencil AND trash.

        Asserts the hooks belonging to ``includes/_ugc_panel_row.html`` and
        ``_route_row_actions.html`` — the row id the delete form targets, the
        shared label hook, the delete endpoint, and the pencil's uuid hook. A
        page that grew its own row treatment would stop emitting these.
        """
        user = UserFactory.create()
        route = RouteFactory.create(user=user)
        client.force_login(user)
        html = client.get(PAGE_URL).content.decode()

        assert f'id="route-{route.uuid}"' in html
        assert "data-row-label" in html
        assert f'data-route-rename="{route.uuid}"' in html
        assert f"/routes/partials/{route.uuid}/delete/" in html

    def test_rename_url_template_is_supplied_to_the_page(self, client: Client) -> None:
        """The page hands account_routes.js a ``__UUID__``-templated URL.

        Reversed server-side so the module needs no opinion about how this
        project spells its URLs. Without this attribute the pencil is a dead
        control — ``row_rename_commit.js`` returns early on an empty template.
        """
        user = UserFactory.create()
        RouteFactory.create(user=user)
        client.force_login(user)
        html = client.get(PAGE_URL).content.decode()

        assert "data-route-list" in html
        assert 'data-rename-url-template="/routes/partials/__UUID__/rename/"' in html

    def test_page_loads_the_four_row_modules(self, client: Client) -> None:
        """inline_rename, row_rename_commit, row_removed and this page's half.

        Document order is execution order for deferred scripts, and each
        module reads the previous one's ``window.pwa*`` global — so the order
        is load-bearing, not cosmetic.
        """
        client.force_login(UserFactory.create())
        html = client.get(PAGE_URL).content.decode()

        positions = [
            html.index("js/inline_rename.js"),
            html.index("js/row_rename_commit.js"),
            html.index("js/row_removed.js"),
            html.index("js/account_routes.js"),
        ]
        assert positions == sorted(positions)

    def test_delete_form_carries_the_shared_removal_hook(self, client: Client) -> None:
        """``data-row-remove`` is what tells this page a row has gone.

        On the FORM rather than the row, so a watcher can tell a removal
        apart from the other HTMX requests a row can make — see
        ``static/js/row_removed.js``.
        """
        user = UserFactory.create()
        RouteFactory.create(user=user)
        client.force_login(user)
        html = client.get(PAGE_URL).content.decode()

        assert "data-row-remove" in html

    def test_the_list_re_reads_when_a_row_is_removed(self, client: Client) -> None:
        """The wrapper re-reads routes:list on ``snowdesk:routes-changed``.

        A row's Remove empties one ``<li>`` and nothing else, so a page that
        has just lost its last route keeps rendering as a list of none — the
        empty state is a server-side clause and only a fresh response can
        carry it (SNOW-752).

        No ``load`` trigger: the view already rendered the list, and a
        page-load refetch would be a second round trip for markup already on
        screen. And no ``?variant=map``, which would answer with map-focus
        rows whose label is a control for a map this page has not got.
        """
        client.force_login(UserFactory.create())
        html = client.get(PAGE_URL).content.decode()

        assert f'hx-get="{reverse("routes:list")}"' in html
        assert 'hx-trigger="snowdesk:routes-changed from:document"' in html
        assert "variant=map" not in html
