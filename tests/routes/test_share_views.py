"""
tests/routes/test_share_views.py — Tests for the SNOW-764 share endpoints.

route_share_create:
  the owner gets an absolute URL naming routes:share_redirect;
  another user's uuid → 404, never 403 (no existence oracle);
  an unknown uuid → 404;
  anonymous → 403; GET → 405; the flag off → 404;
  the rate-limited branch → 429.

route_share_redirect:
  a live token → 302 (never 301) to /?route_share=<token>, no-store, and
    the token in the session;
  an expired share and one whose route was deleted → 410, no-store, and
    nothing written to the session;
  an unknown token → 404;
  a speculative request (HEAD, Sec-Purpose) redirects but writes nothing;
  the flag off → 404;
  POST → 405.

route_share_claim:
  a signed-in claimer gets the new owned row and the token leaves the
    session;
  anonymous → 403; non-HTMX → 400; GET → 405; the flag off → 404;
  an unknown, expired or route-deleted token → 404;
  at ROUTES_MAX_PER_USER → 409 with _route_limit.html, and the token
    stays pending because the claim can still be retried after a delete.

The widened endpoints (route_list, routes_geojson) live in
tests/routes/test_views.py beside their own unwidened cases.

Everything here is Django-test-client work per CLAUDE.md's layer rules:
statuses, headers, session contents and rendered HTML. Nothing needs a
browser.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.test import Client
from django.utils import timezone
from waffle.testutils import override_flag

from apps.routes.models import Route
from apps.routes.services.shares import PENDING_SESSION_KEY
from tests.factories import RouteFactory, RouteShareFactory, UserFactory

HTMX_HEADERS: dict[str, Any] = {"HTTP_HX_REQUEST": "true"}


def _share_url(uuid: object) -> str:
    """Build the mint-a-share URL for a route's uuid."""
    return f"/routes/{uuid}/share/"


def _redirect_url(token: str) -> str:
    """Build the follow-a-share URL for a token."""
    return f"/routes/s/{token}/"


def _claim_url(token: str) -> str:
    """Build the claim URL for a token."""
    return f"/routes/partials/share/{token}/claim/"


# ---------------------------------------------------------------------------
# route_share_create
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRouteShareCreate:
    """Minting a share link from the owner's row."""

    @pytest.fixture(autouse=True)
    def _sharing_on(self) -> Any:
        """Turn the rollout flag on for every test in this class.

        ``override_flag`` is a ``TestCase``-shaped decorator and refuses a
        plain pytest class, so it is entered as a context manager by an
        autouse fixture rather than repeated on twenty methods.
        """
        with override_flag("route_sharing", active=True):
            yield

    def test_the_owner_gets_a_share_url(self, client: Client) -> None:
        """The response carries an absolute URL naming the redirect route."""
        user = UserFactory.create()
        route = RouteFactory.create(user=user)
        client.force_login(user)

        response = client.post(_share_url(route.uuid))

        assert response.status_code == 200
        url = response.json()["url"]
        assert url.startswith("http")
        assert "/routes/s/" in url

    def test_the_url_carries_the_new_shares_token(self, client: Client) -> None:
        """The link that comes back is the row that was written."""
        user = UserFactory.create()
        route = RouteFactory.create(user=user)
        client.force_login(user)

        url = client.post(_share_url(route.uuid)).json()["url"]

        share = route.shares.get()
        assert url.endswith(f"/routes/s/{share.token}/")

    def test_another_users_route_returns_404_not_403(self, client: Client) -> None:
        """No existence oracle — "not yours" reads as "doesn't exist"."""
        owner = UserFactory.create()
        stranger = UserFactory.create()
        route = RouteFactory.create(user=owner)
        client.force_login(stranger)

        response = client.post(_share_url(route.uuid))

        assert response.status_code == 404
        assert not route.shares.exists()

    def test_an_unknown_uuid_returns_404(self, client: Client) -> None:
        """The same answer a stranger's route gets."""
        client.force_login(UserFactory.create())
        response = client.post(_share_url("00000000-0000-0000-0000-000000000000"))
        assert response.status_code == 404

    def test_anonymous_gets_403(self, client: Client) -> None:
        """A share names a route the caller owns; anonymous owns none."""
        route = RouteFactory.create()
        response = client.post(_share_url(route.uuid))
        assert response.status_code == 403

    def test_get_is_not_allowed(self, client: Client) -> None:
        """Minting a row is a POST."""
        route = RouteFactory.create()
        client.force_login(route.user)
        assert client.get(_share_url(route.uuid)).status_code == 405

    def test_the_rate_limited_branch_returns_429(self, client: Client) -> None:
        """django-ratelimit sets request.limited; the view answers 429."""
        route = RouteFactory.create()
        client.force_login(route.user)

        with patch("apps.routes.views.create_route_share") as create:
            with patch("django_ratelimit.decorators.is_ratelimited", return_value=True):
                response = client.post(_share_url(route.uuid))

        assert response.status_code == 429
        create.assert_not_called()


@pytest.mark.django_db
class TestRouteShareCreateFlag:
    """The flag closes the endpoint entirely."""

    def test_the_flag_off_returns_404(self, client: Client) -> None:
        """404 and not 403 — off means the endpoint does not exist yet."""
        route = RouteFactory.create()
        client.force_login(route.user)

        response = client.post(_share_url(route.uuid))

        assert response.status_code == 404
        assert not route.shares.exists()


# ---------------------------------------------------------------------------
# route_share_redirect
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRouteShareRedirect:
    """Following a share link."""

    @pytest.fixture(autouse=True)
    def _sharing_on(self) -> Any:
        """Turn the rollout flag on for every test in this class.

        ``override_flag`` is a ``TestCase``-shaped decorator and refuses a
        plain pytest class, so it is entered as a context manager by an
        autouse fixture rather than repeated on twenty methods.
        """
        with override_flag("route_sharing", active=True):
            yield

    def test_a_live_token_redirects_to_the_map(self, client: Client) -> None:
        """302 to the homepage carrying the token as a deep-link parameter."""
        share = RouteShareFactory.create()

        response = client.get(_redirect_url(share.token))

        assert response.status_code == 302
        assert response["Location"] == f"/?route_share={share.token}"

    def test_the_redirect_is_never_a_301(self, client: Client) -> None:
        """A 301 is cached by the browser and never re-seats the session."""
        share = RouteShareFactory.create()
        response = client.get(_redirect_url(share.token))
        assert response.status_code != 301

    def test_the_redirect_is_not_stored(self, client: Client) -> None:
        """Its effect is per-session; no cache may hold it."""
        share = RouteShareFactory.create()
        response = client.get(_redirect_url(share.token))
        assert response["Cache-Control"] == "no-store"

    def test_the_token_lands_in_the_session(self, client: Client) -> None:
        """This is what survives the sign-in round trip."""
        share = RouteShareFactory.create()

        client.get(_redirect_url(share.token))

        assert client.session[PENDING_SESSION_KEY] == [share.token]

    def test_following_two_links_keeps_both(self, client: Client) -> None:
        """The list is a list, in the order the links were followed."""
        first = RouteShareFactory.create()
        second = RouteShareFactory.create()

        client.get(_redirect_url(first.token))
        client.get(_redirect_url(second.token))

        assert client.session[PENDING_SESSION_KEY] == [first.token, second.token]

    def test_an_unknown_token_returns_404(self, client: Client) -> None:
        """A guesser learns only that a random string is not a token."""
        assert client.get(_redirect_url("nope")).status_code == 404

    def test_an_expired_share_returns_410(self, client: Client) -> None:
        """The link existed and has stopped working — that is what 410 says."""
        share = RouteShareFactory.create(expires_at=timezone.now() - timedelta(days=1))

        response = client.get(_redirect_url(share.token))

        assert response.status_code == 410
        assert response["Cache-Control"] == "no-store"

    def test_a_deleted_routes_share_returns_410(self, client: Client) -> None:
        """The other way a link dies, answered the same."""
        route = RouteFactory.create()
        share = RouteShareFactory.create(route=route)
        route.delete()

        response = client.get(_redirect_url(share.token))

        assert response.status_code == 410

    def test_a_dead_share_writes_nothing_to_the_session(self, client: Client) -> None:
        """Nothing pending, so nothing to offer and nothing to prune later."""
        share = RouteShareFactory.create(expires_at=timezone.now() - timedelta(days=1))

        client.get(_redirect_url(share.token))

        assert PENDING_SESSION_KEY not in client.session

    def test_a_head_request_redirects_without_writing(self, client: Client) -> None:
        """A prefetch is not a person choosing to accept a route."""
        share = RouteShareFactory.create()

        response = client.head(_redirect_url(share.token))

        assert response.status_code == 302
        assert PENDING_SESSION_KEY not in client.session

    def test_a_prefetch_redirects_without_writing(self, client: Client) -> None:
        """Sec-Purpose says the browser did this, not the reader."""
        share = RouteShareFactory.create()

        response = client.get(
            _redirect_url(share.token), HTTP_SEC_PURPOSE="prefetch;anonymous-client-ip"
        )

        assert response.status_code == 302
        assert PENDING_SESSION_KEY not in client.session

    def test_post_is_not_allowed(self, client: Client) -> None:
        """A navigation, and only a navigation."""
        share = RouteShareFactory.create()
        assert client.post(_redirect_url(share.token)).status_code == 405


@pytest.mark.django_db
class TestRouteShareRedirectFlag:
    """The flag closes the link."""

    def test_the_flag_off_returns_404(self, client: Client) -> None:
        """No token reaches a session while the rollout is closed."""
        share = RouteShareFactory.create()

        response = client.get(_redirect_url(share.token))

        assert response.status_code == 404
        assert PENDING_SESSION_KEY not in client.session


# ---------------------------------------------------------------------------
# route_share_claim
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRouteShareClaim:
    """Saving a shared route onto one's own account."""

    @pytest.fixture(autouse=True)
    def _sharing_on(self) -> Any:
        """Turn the rollout flag on for every test in this class.

        ``override_flag`` is a ``TestCase``-shaped decorator and refuses a
        plain pytest class, so it is entered as a context manager by an
        autouse fixture rather than repeated on twenty methods.
        """
        with override_flag("route_sharing", active=True):
            yield

    def test_a_claim_creates_the_copy(self, client: Client) -> None:
        """The claimer ends up owning a route they did not upload."""
        claimer = UserFactory.create()
        share = RouteShareFactory.create()
        client.force_login(claimer)
        client.get(_redirect_url(share.token))

        response = client.post(_claim_url(share.token), **HTMX_HEADERS)

        assert response.status_code == 200
        assert Route.objects.for_user(claimer).count() == 1

    def test_the_response_is_the_owned_row(self, client: Client) -> None:
        """The claimed row carries a uuid and the owner's own controls."""
        claimer = UserFactory.create()
        share = RouteShareFactory.create()
        client.force_login(claimer)
        client.get(_redirect_url(share.token))

        response = client.post(_claim_url(share.token), **HTMX_HEADERS)

        copy = Route.objects.for_user(claimer).get()
        content = response.content.decode()
        assert f'id="route-{copy.uuid}"' in content
        assert "Shared with you" not in content

    def test_the_token_leaves_the_session(self, client: Client) -> None:
        """The intention has been acted on; re-offering Save would be wrong."""
        claimer = UserFactory.create()
        share = RouteShareFactory.create()
        client.force_login(claimer)
        client.get(_redirect_url(share.token))

        client.post(_claim_url(share.token), **HTMX_HEADERS)

        assert client.session[PENDING_SESSION_KEY] == []

    def test_a_claim_works_without_having_followed_the_link(
        self, client: Client
    ) -> None:
        """The session is where a PENDING claim lives, not where authority does.

        Authority is the token itself, so a claim posted by a signed-in
        user whose session was cycled between the follow and the save
        still lands — which is the whole reason the token is in the URL.
        """
        claimer = UserFactory.create()
        share = RouteShareFactory.create()
        client.force_login(claimer)

        response = client.post(_claim_url(share.token), **HTMX_HEADERS)

        assert response.status_code == 200

    def test_anonymous_gets_403(self, client: Client) -> None:
        """A claim needs an account to claim onto."""
        share = RouteShareFactory.create()
        client.get(_redirect_url(share.token))

        response = client.post(_claim_url(share.token), **HTMX_HEADERS)

        assert response.status_code == 403

    def test_non_htmx_returns_400(self, client: Client) -> None:
        """A fragment endpoint, guarded like every other one here."""
        claimer = UserFactory.create()
        share = RouteShareFactory.create()
        client.force_login(claimer)

        assert client.post(_claim_url(share.token)).status_code == 400

    def test_get_is_not_allowed(self, client: Client) -> None:
        """Claiming writes a row."""
        claimer = UserFactory.create()
        share = RouteShareFactory.create()
        client.force_login(claimer)

        assert client.get(_claim_url(share.token), **HTMX_HEADERS).status_code == 405

    def test_an_unknown_token_returns_404(self, client: Client) -> None:
        """Nothing to claim."""
        client.force_login(UserFactory.create())
        response = client.post(_claim_url("nope"), **HTMX_HEADERS)
        assert response.status_code == 404

    def test_an_expired_share_returns_404(self, client: Client) -> None:
        """The window closed between the follow and the save."""
        claimer = UserFactory.create()
        share = RouteShareFactory.create(expires_at=timezone.now() - timedelta(days=1))
        client.force_login(claimer)

        response = client.post(_claim_url(share.token), **HTMX_HEADERS)

        assert response.status_code == 404
        assert not Route.objects.for_user(claimer).exists()

    def test_a_deleted_routes_share_returns_404(self, client: Client) -> None:
        """The owner removed the route before the recipient saved it."""
        claimer = UserFactory.create()
        route = RouteFactory.create()
        share = RouteShareFactory.create(route=route)
        route.delete()
        client.force_login(claimer)

        response = client.post(_claim_url(share.token), **HTMX_HEADERS)

        assert response.status_code == 404

    def test_at_the_cap_returns_409_with_the_limit_partial(
        self, client: Client, settings: Any
    ) -> None:
        """The same treatment an at-cap upload gets, from the same partial."""
        settings.ROUTES_MAX_PER_USER = 1
        claimer = UserFactory.create()
        RouteFactory.create(user=claimer)
        share = RouteShareFactory.create()
        client.force_login(claimer)
        client.get(_redirect_url(share.token))

        response = client.post(_claim_url(share.token), **HTMX_HEADERS)

        assert response.status_code == 409
        assert "saved-route limit" in response.content.decode()

    def test_an_at_cap_claim_keeps_the_token_pending(
        self, client: Client, settings: Any
    ) -> None:
        """Deleting a route and saving again has to work without the link."""
        settings.ROUTES_MAX_PER_USER = 1
        claimer = UserFactory.create()
        RouteFactory.create(user=claimer)
        share = RouteShareFactory.create()
        client.force_login(claimer)
        client.get(_redirect_url(share.token))

        client.post(_claim_url(share.token), **HTMX_HEADERS)

        assert client.session[PENDING_SESSION_KEY] == [share.token]

    def test_the_rate_limited_branch_returns_429(self, client: Client) -> None:
        """429, and nothing is claimed.

        The transient status, unlike the cap's 409: a limited claimer gets
        their route by waiting, where an at-cap one has to delete something
        first. The token therefore stays pending — the same reason
        ``test_an_at_cap_claim_keeps_the_token_pending`` above checks it.
        """
        claimer = UserFactory.create()
        share = RouteShareFactory.create()
        client.force_login(claimer)
        client.get(_redirect_url(share.token))

        with patch("django_ratelimit.decorators.is_ratelimited", return_value=True):
            response = client.post(_claim_url(share.token), **HTMX_HEADERS)

        assert response.status_code == 429
        assert not Route.objects.filter(user=claimer).exists()
        assert client.session[PENDING_SESSION_KEY] == [share.token]

    def test_an_anonymous_claim_is_403_not_429(self, client: Client) -> None:
        """The auth check runs FIRST, and the ordering is the point.

        The limiter keys on ``user``, so an anonymous request has no bucket
        to charge. Answering 429 there would be both wrong and a way to
        spend somebody else's budget.
        """
        share = RouteShareFactory.create()
        client.get(_redirect_url(share.token))

        with patch("django_ratelimit.decorators.is_ratelimited", return_value=True):
            response = client.post(_claim_url(share.token), **HTMX_HEADERS)

        assert response.status_code == 403


@pytest.mark.django_db
class TestRouteShareClaimFlag:
    """The flag closes the claim."""

    def test_the_flag_off_returns_404(self, client: Client) -> None:
        """Nothing is copied while the rollout is closed."""
        claimer = UserFactory.create()
        share = RouteShareFactory.create()
        client.force_login(claimer)

        response = client.post(_claim_url(share.token), **HTMX_HEADERS)

        assert response.status_code == 404
        assert not Route.objects.for_user(claimer).exists()


# ---------------------------------------------------------------------------
# The Share control's own gate
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestShareControlRendering:
    """Which route rows draw a Share button."""

    def test_the_map_panel_row_has_share_when_the_flag_is_on(
        self, client: Client
    ) -> None:
        """The surface with a wired handler, and the rollout open."""
        user = UserFactory.create()
        route = RouteFactory.create(user=user)
        client.force_login(user)

        with override_flag("route_sharing", active=True):
            response = client.get("/routes/partials/list/?variant=map", **HTMX_HEADERS)

        assert f'data-route-share="{route.uuid}"' in response.content.decode()

    def test_the_flag_off_draws_no_share_control(self, client: Client) -> None:
        """A control whose endpoint 404s is a dead control."""
        user = UserFactory.create()
        RouteFactory.create(user=user)
        client.force_login(user)

        response = client.get("/routes/partials/list/?variant=map", **HTMX_HEADERS)

        assert "data-route-share" not in response.content.decode()

    def test_the_account_variant_draws_no_share_control(self, client: Client) -> None:
        """/account/routes/ has no handler for it yet — see route_list."""
        user = UserFactory.create()
        RouteFactory.create(user=user)
        client.force_login(user)

        with override_flag("route_sharing", active=True):
            response = client.get("/routes/partials/list/", **HTMX_HEADERS)

        assert "data-route-share" not in response.content.decode()

    def test_a_pending_row_never_draws_a_share_control(self, client: Client) -> None:
        """You cannot pass on a route you have not yet saved."""
        share = RouteShareFactory.create()

        with override_flag("route_sharing", active=True):
            client.get(_redirect_url(share.token))
            response = client.get("/routes/partials/list/?variant=map", **HTMX_HEADERS)

        assert "data-route-share" not in response.content.decode()
