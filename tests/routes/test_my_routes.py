"""
tests/routes/test_my_routes.py — Tests for the account area's routes page
(SNOW-713).

``apps.routes.views.my_routes`` renders ``/account/routes/``: the signed-in
user's own saved GPX routes. It is a full page, not a fragment, so it shares
none of the rules the HTMX endpoints in ``tests/routes/test_views.py`` are
held to — and each difference is asserted here rather than assumed.

The routes twin of ``tests/observations/test_my_observations.py``, with one
gate that page does not carry: the ``routes`` waffle flag.

Covers:
  routing       — ``accounts:routes`` resolves to ``/account/routes/``.
  gating        — anonymous → redirect to sign-in (never 403, never the
                  page); a plain non-HTMX GET is the normal case and must
                  NOT be rejected; the ``routes`` flag inactive → 404, even
                  for a user who owns routes.
  owner scope   — the user's own rows render; another user's never do.
  caching       — ``Cache-Control: private, no-store``.
  empty state   — a user with no uploads gets the list partial's own empty
                  clause, not a bare heading.
  reuse         — the page renders the shared UGC row with BOTH its controls,
                  and supplies the rename endpoint account_routes.js needs.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from django.test import Client
from django.urls import reverse
from waffle.testutils import override_flag

from tests.factories import RouteFactory, UserFactory

PAGE_URL = "/account/routes/"


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

    @pytest.fixture(autouse=True)
    def _routes_flag_on(self) -> Generator[None, None, None]:
        """Force ``routes=on`` for every test in this class.

        Per-class ``@override_flag`` decoration would need a
        ``unittest.TestCase`` subclass; with plain pytest classes the project's
        idiom is an autouse fixture wrapping each test in the context manager
        (see ``tests/public/test_edit_resorts_api.py``). Forcing it on rather
        than relying on the seeding migration's ``superusers=True`` row, since
        these tests sign in as ordinary users.
        """
        with override_flag("routes", active=True):
            yield

    def test_anonymous_redirects_to_sign_in(self, client: Client) -> None:
        """An anonymous visitor is offered the way in, not a 403.

        The HTMX endpoints answer 403 because a fragment has nowhere to
        render a sign-in link. A page does, and the account pages beside
        this one (``accounts:hub``, ``accounts:settings``) redirect.
        """
        response = client.get(PAGE_URL)
        assert response.status_code == 302
        assert response.headers["Location"] == reverse("accounts:sign_in")

    def test_anonymous_redirect_precedes_the_flag_check(self, client: Client) -> None:
        """Order matters: an anonymous visitor is redirected, flag or not.

        Checking the flag first would answer 404 to a signed-out user whose
        account, once they signed in, would be allowed the page.
        """
        with override_flag("routes", active=False):
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
class TestMyRoutesFlagGate:
    """The ``routes`` waffle flag gates the page, 404 when inactive."""

    @override_flag("routes", active=False)
    def test_flag_off_is_404(self, client: Client) -> None:
        """404, not 403 — the flag hides the feature's existence.

        A 403 would admit there is something behind the URL. 404 is what a
        user without the feature should see, and matches the map, where the
        roundel is simply absent.
        """
        client.force_login(UserFactory.create())
        assert client.get(PAGE_URL).status_code == 404

    @override_flag("routes", active=False)
    def test_flag_off_is_404_even_for_an_owner_of_routes(self, client: Client) -> None:
        """Owning routes does not exempt a user from the gate.

        The interesting case: a user who uploaded routes while the flag was
        on, then had it turned off. The gate is about the feature, not about
        whether the table has rows for them.
        """
        user = UserFactory.create()
        RouteFactory.create(user=user, name="Haute Route")
        client.force_login(user)
        response = client.get(PAGE_URL)
        assert response.status_code == 404
        assert b"Haute Route" not in response.content

    @override_flag("routes", active=True)
    def test_flag_on_renders(self, client: Client) -> None:
        """With the flag active the page renders normally."""
        client.force_login(UserFactory.create())
        assert client.get(PAGE_URL).status_code == 200


# ---------------------------------------------------------------------------
# Owner scoping
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMyRoutesOwnerScope:
    """Own rows render; another user's never do."""

    @pytest.fixture(autouse=True)
    def _routes_flag_on(self) -> Generator[None, None, None]:
        """Force ``routes=on`` for every test in this class.

        Per-class ``@override_flag`` decoration would need a
        ``unittest.TestCase`` subclass; with plain pytest classes the project's
        idiom is an autouse fixture wrapping each test in the context manager
        (see ``tests/public/test_edit_resorts_api.py``). Forcing it on rather
        than relying on the seeding migration's ``superusers=True`` row, since
        these tests sign in as ordinary users.
        """
        with override_flag("routes", active=True):
            yield

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

    @pytest.fixture(autouse=True)
    def _routes_flag_on(self) -> Generator[None, None, None]:
        """Force ``routes=on`` for every test in this class.

        Per-class ``@override_flag`` decoration would need a
        ``unittest.TestCase`` subclass; with plain pytest classes the project's
        idiom is an autouse fixture wrapping each test in the context manager
        (see ``tests/public/test_edit_resorts_api.py``). Forcing it on rather
        than relying on the seeding migration's ``superusers=True`` row, since
        these tests sign in as ordinary users.
        """
        with override_flag("routes", active=True):
            yield

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

    @pytest.fixture(autouse=True)
    def _routes_flag_on(self) -> Generator[None, None, None]:
        """Force ``routes=on`` for every test in this class.

        Per-class ``@override_flag`` decoration would need a
        ``unittest.TestCase`` subclass; with plain pytest classes the project's
        idiom is an autouse fixture wrapping each test in the context manager
        (see ``tests/public/test_edit_resorts_api.py``). Forcing it on rather
        than relying on the seeding migration's ``superusers=True`` row, since
        these tests sign in as ordinary users.
        """
        with override_flag("routes", active=True):
            yield

    def test_empty_clause_renders(self, client: Client) -> None:
        """The list partial's own empty clause is present."""
        client.force_login(UserFactory.create())
        html = client.get(PAGE_URL).content.decode()
        assert 'data-testid="route-list-empty"' in html
        assert "no saved routes yet" in html


@pytest.mark.django_db
class TestMyRoutesSharedPartials:
    """The page hosts the shared row rather than authoring a second one."""

    @pytest.fixture(autouse=True)
    def _routes_flag_on(self) -> Generator[None, None, None]:
        """Force ``routes=on`` for every test in this class.

        Per-class ``@override_flag`` decoration would need a
        ``unittest.TestCase`` subclass; with plain pytest classes the project's
        idiom is an autouse fixture wrapping each test in the context manager
        (see ``tests/public/test_edit_resorts_api.py``). Forcing it on rather
        than relying on the seeding migration's ``superusers=True`` row, since
        these tests sign in as ordinary users.
        """
        with override_flag("routes", active=True):
            yield

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

    def test_page_loads_the_three_rename_modules(self, client: Client) -> None:
        """inline_rename, row_rename_commit and this page's own half.

        Document order is execution order for deferred scripts, and each
        module reads the previous one's ``window.pwa*`` global — so the order
        is load-bearing, not cosmetic.
        """
        client.force_login(UserFactory.create())
        html = client.get(PAGE_URL).content.decode()

        positions = [
            html.index("js/inline_rename.js"),
            html.index("js/row_rename_commit.js"),
            html.index("js/account_routes.js"),
        ]
        assert positions == sorted(positions)
