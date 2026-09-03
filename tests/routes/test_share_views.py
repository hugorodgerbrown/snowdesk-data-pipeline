"""
tests/routes/test_share_views.py — Tests for the SNOW-764 share endpoints.

route_share_create:
  the owner gets an absolute URL naming routes:share_redirect;
  another user's uuid → 404, never 403 (no existence oracle);
  an unknown uuid → 404;
  anonymous → 403; GET → 405; the rate-limited branch → 429.

route_share_redirect:
  a live token → 302 (never 301) to /?route_share=<token>, no-store, and
    the token in the session;
  an expired share and one whose route was deleted → 410, no-store, and
    nothing written to the session;
  an unknown token → 404;
  a speculative request (HEAD, Sec-Purpose) redirects but writes nothing,
    and neither does a passive subresource load (Sec-Fetch-Dest: image /
    iframe / …) — only a top-level navigation, or a client sending no
    fetch metadata at all, plants the token;
  POST → 405.

route_share_claim:
  a signed-in claimer gets the new owned row and the token leaves the
    session;
  anonymous → 403; non-HTMX → 400; GET → 405;
  an unknown, expired or route-deleted token → 404;
  at ROUTES_MAX_PER_USER → 409 with _route_limit.html, and the token
    stays pending because the claim can still be retried after a delete.

TestAnonymousRecipientJourney walks the three endpoints end to end with
the sharer and the recipient as SEPARATE principals — the shape every
class above misses, each of which exercises one endpoint from one seat.

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


# ---------------------------------------------------------------------------
# route_share_redirect
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRouteShareRedirect:
    """Following a share link."""

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

    @pytest.mark.parametrize("destination", ["image", "iframe", "script", "empty"])
    def test_a_passive_subresource_load_writes_nothing(
        self, client: Client, destination: str
    ) -> None:
        """An <img> or <iframe> pointed here is not a person accepting a route.

        ``Sec-Purpose`` does not mark these — nothing does, except
        ``Sec-Fetch-Dest``. Without this check any page on the web could
        embed the share URL and plant a token in every visitor's session
        with no interaction at all.
        """
        share = RouteShareFactory.create()

        response = client.get(
            _redirect_url(share.token), HTTP_SEC_FETCH_DEST=destination
        )

        assert response.status_code == 302
        assert PENDING_SESSION_KEY not in client.session

    def test_a_document_navigation_still_writes_the_token(self, client: Client) -> None:
        """The header a real click carries — the case the feature is for."""
        share = RouteShareFactory.create()

        response = client.get(
            _redirect_url(share.token), HTTP_SEC_FETCH_DEST="document"
        )

        assert response.status_code == 302
        assert client.session[PENDING_SESSION_KEY] == [share.token]

    def test_a_request_with_no_fetch_metadata_still_writes(
        self, client: Client
    ) -> None:
        """An older browser sends no Sec-Fetch-Dest, and must still work.

        The permissive fallback is deliberate: fetch metadata is a
        hardening signal, not an authentication one, so its absence widens
        the endpoint rather than closing it (apps.core.http).
        """
        share = RouteShareFactory.create()

        response = client.get(_redirect_url(share.token))

        assert response.status_code == 302
        assert client.session[PENDING_SESSION_KEY] == [share.token]

    def test_post_is_not_allowed(self, client: Client) -> None:
        """A navigation, and only a navigation."""
        share = RouteShareFactory.create()
        assert client.post(_redirect_url(share.token)).status_code == 405


# ---------------------------------------------------------------------------
# route_share_claim
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRouteShareClaim:
    """Saving a shared route onto one's own account."""

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


# ---------------------------------------------------------------------------
# The Share control's own gate
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestShareControlRendering:
    """Which route rows draw a Share button."""

    def test_the_map_panel_row_has_share(self, client: Client) -> None:
        """The one surface with a wired handler draws it."""
        user = UserFactory.create()
        route = RouteFactory.create(user=user)
        client.force_login(user)

        response = client.get("/routes/partials/list/?variant=map", **HTMX_HEADERS)

        assert f'data-route-share="{route.uuid}"' in response.content.decode()

    def test_a_pending_row_never_draws_a_share_control(self, client: Client) -> None:
        """You cannot pass on a route you have not yet saved."""
        share = RouteShareFactory.create()

        client.get(_redirect_url(share.token))
        response = client.get("/routes/partials/list/?variant=map", **HTMX_HEADERS)

        assert "data-route-share" not in response.content.decode()


# ---------------------------------------------------------------------------
# The pending row's Save, and its authentication gate
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPendingRowClaimControl:
    """Which control a pending row draws, and for whom.

    The Save posts to ``route_share_claim``, which answers an anonymous
    request 403 — and the panel's Save is an HTMX form, so that 403 is
    swallowed and the press does nothing visible. An anonymous recipient
    therefore gets the way in instead of the control, which is the same
    pair the map popup already draws for the same visitor
    (static/js/map.js's appendRouteClaimCta).
    """

    def test_a_signed_in_recipient_gets_the_claim_form(self, client: Client) -> None:
        """They have an account to claim onto, so Save is real."""
        client.force_login(UserFactory.create())
        share = RouteShareFactory.create()

        client.get(_redirect_url(share.token))
        response = client.get("/routes/partials/list/?variant=map", **HTMX_HEADERS)

        content = response.content.decode()
        assert "data-row-claimed" in content
        assert f"/routes/partials/share/{share.token}/claim/" in content

    def test_an_anonymous_recipient_gets_a_sign_in_link(self, client: Client) -> None:
        """A control whose only outcome is a swallowed 403 is a broken page."""
        share = RouteShareFactory.create()

        client.get(_redirect_url(share.token))
        response = client.get("/routes/partials/list/?variant=map", **HTMX_HEADERS)

        content = response.content.decode()
        assert "Sign in to save this route" in content
        assert 'href="/account/sign-in/"' in content

    def test_an_anonymous_recipient_gets_no_claim_form(self, client: Client) -> None:
        """Never both: the row has one control, and it is the usable one."""
        share = RouteShareFactory.create()

        client.get(_redirect_url(share.token))
        response = client.get("/routes/partials/list/?variant=map", **HTMX_HEADERS)

        content = response.content.decode()
        assert "data-row-claimed" not in content
        assert f"/routes/partials/share/{share.token}/claim/" not in content

    def test_the_row_itself_still_renders_for_an_anonymous_recipient(
        self, client: Client
    ) -> None:
        """The route is the thing they were sent — never hide it."""
        route = RouteFactory.create(name="Vallée Blanche")
        share = RouteShareFactory.create(route=route)

        client.get(_redirect_url(share.token))
        response = client.get("/routes/partials/list/?variant=map", **HTMX_HEADERS)

        content = response.content.decode()
        assert "Vallée Blanche" in content
        assert f"route-share-{share.token}" in content


# ---------------------------------------------------------------------------
# The whole journey, with the sharer and the recipient as two principals
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAnonymousRecipientJourney:
    """User A shares; a stranger follows, sees, signs in as B and claims.

    THE END-TO-END PATH THE ROLLOUT FLAG USED TO BREAK. Route sharing
    shipped behind a ``route_sharing`` waffle flag targeted at
    ``superusers=True``, read on the recipient's endpoints as well as the
    sharer's. A recipient is by definition somebody else — and usually
    anonymous — so ``flag_is_active`` answered False for them and
    ``route_share_redirect`` raised ``Http404``: a link a superuser had
    just minted 404'd for everyone it was sent to. The feature could not
    work under its own targeting.

    Every test above this one uses ONE client and ONE seat, which is why
    none of them caught it, and the flag-scoped ones could not have: the
    ``override_flag`` context manager flips a flag globally for the whole
    test, seating the sharer and the recipient on the SAME side of a gate
    whose entire defect was that they sit on opposite sides of it. A
    same-flag-for-both test can express asymmetry between two principals
    only by never having any.

    So the fix is not the assertion here — the fix was deleting the flag —
    but this shape is: two ``Client`` objects, two sessions, and the
    recipient's one never told anything the sharer's knew.
    """

    def test_a_stranger_can_follow_see_and_claim_a_shared_route(self) -> None:
        """The four steps of the journey, each from the correct seat."""
        # 1. User A owns a route and mints a share through the endpoint,
        #    rather than through the factory, so the recipient is handed a
        #    token that the real minting path produced.
        sharer = UserFactory.create()
        route = RouteFactory.create(user=sharer, name="Vallée Blanche")
        sharer_client = Client()
        sharer_client.force_login(sharer)

        url = sharer_client.post(_share_url(route.uuid)).json()["url"]
        token = url.rstrip("/").rsplit("/", 1)[-1]

        # 2. A SEPARATE client — a different browser, nobody signed in,
        #    and no cookie in common with the sharer's — follows the link.
        recipient = Client()

        followed = recipient.get(_redirect_url(token))

        assert followed.status_code == 302
        assert followed["Location"] == f"/?route_share={token}"
        assert recipient.session[PENDING_SESSION_KEY] == [token]

        # 3. The map panel answers that anonymous session, and the row it
        #    draws carries the TOKEN and never the route's uuid — the
        #    rename and delete endpoints are addressed by uuid, and a
        #    non-owner must not be handed one.
        listing = recipient.get("/routes/partials/list/?variant=map", **HTMX_HEADERS)
        content = listing.content.decode()

        assert listing.status_code == 200
        assert "Vallée Blanche" in content
        assert f"route-share-{token}" in content
        assert str(route.uuid) not in content

        # 4. The same browser signs in — as user B, who is neither the
        #    sharer nor a superuser — and claims. The session carried the
        #    pending token across the sign-in, which is the whole reason
        #    it lives there (see the decision record).
        claimer = UserFactory.create()
        recipient.force_login(claimer)

        claimed = recipient.post(_claim_url(token), **HTMX_HEADERS)

        assert claimed.status_code == 200

        copy = Route.objects.for_user(claimer).get()
        assert copy.name == "Vallée Blanche"
        assert copy.uuid != route.uuid

        # A's original is untouched: a claim COPIES, it never transfers.
        route.refresh_from_db()
        assert route.user == sharer
        assert Route.objects.for_user(sharer).count() == 1
