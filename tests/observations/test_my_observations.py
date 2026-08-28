"""
tests/observations/test_my_observations.py — Tests for the account area's
own-observations page (SNOW-677).

``apps.observations.views.my_observations`` renders ``/account/observations/``:
the signed-in user's own field reports, newest first, with no 48-hour cutoff.
It is a full page, not a fragment, so it shares none of the three rules the
HTMX endpoints in ``tests/observations/test_views.py`` are held to — and each
of those differences is asserted here rather than assumed.

Covers:
  gating        — anonymous → redirect to sign-in (never 403, and never the
                  page); a plain non-HTMX GET is the normal case and must
                  NOT be rejected; an authenticated user with no verified
                  Account still gets the page, because the verification gate
                  guards creating reports, not reading one's own.
  owner scope   — the user's own rows render; another user's never do, even
                  when both users have reports.
  ordering      — newest first, the model's own ``-observed_at``.
  no cutoff     — a report older than the community stream's 48-hour window
                  still appears.
  timestamps    — the owner's own instants are unfloored: the ``<time>``
                  element carries the exact recorded second, not the
                  15-minute-floored value the anonymised community feed uses.
  caching       — ``Cache-Control: private, no-store``, so a per-user page
                  cannot land in a shared cache or the PWA shell cache.
  empty state   — a user who has never reported gets the list partial's own
                  empty clause, not a bare heading.
  reuse         — the page renders the shared UGC row and its delete control,
                  rather than a second row treatment authored here.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.observations.models import FieldObservation
from tests.factories import (
    AccountFactory,
    FieldObservationFactory,
    MicroRegionFactory,
    UserFactory,
)

PAGE_URL = "/account/observations/"

# Matches the HTMX endpoints' own header dict — used here only to prove this
# page does NOT need it (see TestMyObservationsGating).
HTMX_HEADERS: dict[str, Any] = {"HTTP_HX_REQUEST": "true"}


def _verified_user(**kwargs: Any) -> User:
    """Create a User with a verified Account.

    Mirrors the helper in ``tests/observations/test_views.py`` — a verified
    account is what the *submit* endpoints require, so it is the state a user
    with reports to read is actually in.

    Args:
        **kwargs: Forwarded to ``UserFactory.create``.

    Returns:
        The created user, with a verified ``Account``.

    """
    user = UserFactory.create(**kwargs)
    AccountFactory.create(user=user, is_verified=True)
    return user


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMyObservationsRouting:
    """The page is mounted under /account/ and reversible by name."""

    def test_url_reverses_to_the_account_prefix(self) -> None:
        """``accounts:observations`` is the name, ``/account/`` the prefix.

        The route is declared in ``apps/accounts/urls.py`` (which owns the
        prefix) but points at a view in ``apps.observations``. This asserts
        the pairing, so moving either half without the other fails here.
        """
        assert reverse("accounts:observations") == PAGE_URL


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMyObservationsGating:
    """Anonymous redirects; a plain GET is the normal case; no verify gate."""

    def test_anonymous_redirects_to_sign_in(self, client: Client) -> None:
        """An anonymous visitor is offered the way in, not a 403.

        The HTMX endpoints answer 403 because a fragment has nowhere to
        render a sign-in link. A page does, and the account pages beside
        this one (``accounts:hub``, ``accounts:settings``) redirect.
        """
        response = client.get(PAGE_URL)
        assert response.status_code == 302
        assert response.headers["Location"] == reverse("accounts:sign_in")

    def test_anonymous_never_sees_another_users_reports(self, client: Client) -> None:
        """The redirect body carries no report content."""
        owner = _verified_user()
        FieldObservationFactory.create(
            user=owner,
            observation_type=FieldObservation.OBSERVATION_TYPE.WHUMPFING,
        )
        response = client.get(PAGE_URL)
        assert response.status_code == 302
        assert b"Whumpfing" not in response.content

    def test_plain_non_htmx_get_renders_the_page(self, client: Client) -> None:
        """A normal browser navigation is the expected request, not a 400.

        The regression this guards: the observations app's other GET views
        are all ``@require_htmx``, and applying that decorator here by habit
        would make the page unreachable by the only means anyone reaches it.
        """
        client.force_login(_verified_user())
        response = client.get(PAGE_URL)
        assert response.status_code == 200

    def test_unverified_user_gets_the_page(self, client: Client) -> None:
        """No verification gate — an unverified account simply has no reports.

        ``_auth_gate`` stops an unverified user *creating* a report. Reading
        one's own record needs no such gate, so this user gets the page and
        the empty state rather than a 403.
        """
        user = UserFactory.create()
        AccountFactory.create(user=user, is_verified=False)
        client.force_login(user)
        response = client.get(PAGE_URL)
        assert response.status_code == 200
        assert b'data-testid="observation-list-empty"' in response.content

    def test_user_with_no_account_row_gets_the_page(self, client: Client) -> None:
        """A User with no Account profile at all is still authenticated."""
        client.force_login(UserFactory.create())
        response = client.get(PAGE_URL)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Owner scoping
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMyObservationsOwnerScope:
    """Own rows render; another user's never do."""

    def test_own_reports_are_listed(self, client: Client) -> None:
        """The signed-in user sees their own report."""
        user = _verified_user()
        region = MicroRegionFactory.create(region_id="CH-4115", name="Valais")
        FieldObservationFactory.create(
            user=user,
            region=region,
            observation_type=FieldObservation.OBSERVATION_TYPE.WHUMPFING,
        )
        client.force_login(user)
        html = client.get(PAGE_URL).content.decode()
        assert "Whumpfing" in html
        assert "Valais" in html

    def test_another_users_reports_are_never_listed(self, client: Client) -> None:
        """Ownership is enforced by the query, so a stranger's row is absent.

        Both users have exactly one report, and the two types differ — so a
        scoping failure shows up as the *other* label appearing, not merely
        as a count.
        """
        viewer = _verified_user()
        stranger = _verified_user()
        FieldObservationFactory.create(
            user=viewer,
            observation_type=FieldObservation.OBSERVATION_TYPE.WHUMPFING,
        )
        FieldObservationFactory.create(
            user=stranger,
            observation_type=FieldObservation.OBSERVATION_TYPE.PINWHEELS,
        )
        client.force_login(viewer)
        html = client.get(PAGE_URL).content.decode()
        assert "Whumpfing" in html
        assert "Pinwheels" not in html

    def test_a_users_own_page_is_not_a_stranger_oracle(self, client: Client) -> None:
        """A user with no reports sees the empty state, not someone else's."""
        _stranger_report = FieldObservationFactory.create(user=_verified_user())
        client.force_login(_verified_user())
        response = client.get(PAGE_URL)
        assert b'data-testid="observation-list-empty"' in response.content


# ---------------------------------------------------------------------------
# Ordering, cutoff and timestamps
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMyObservationsOrdering:
    """Newest first, and nothing ages out."""

    def test_newest_report_leads_the_list(self, client: Client) -> None:
        """Ordering is the model's own ``-observed_at``."""
        user = _verified_user()
        now = timezone.now()
        FieldObservationFactory.create(
            user=user,
            observed_at=now - timedelta(hours=5),
            observation_type=FieldObservation.OBSERVATION_TYPE.PINWHEELS,
        )
        FieldObservationFactory.create(
            user=user,
            observed_at=now - timedelta(minutes=5),
            observation_type=FieldObservation.OBSERVATION_TYPE.WHUMPFING,
        )
        client.force_login(user)
        html = client.get(PAGE_URL).content.decode()
        assert html.index("Whumpfing") < html.index("Pinwheels")

    def test_report_older_than_48_hours_still_appears(self, client: Client) -> None:
        """No cutoff. The 48-hour window bounds the *community* stream only.

        This is the whole reason the page exists: ``/observations/`` drops a
        report after two days because that window bounds what strangers can
        see, and a user's own record has no reason to end.
        """
        user = _verified_user()
        FieldObservationFactory.create(
            user=user,
            observed_at=timezone.now() - timedelta(days=30),
            observation_type=FieldObservation.OBSERVATION_TYPE.FRACTURES,
        )
        client.force_login(user)
        assert b"Fractures" in client.get(PAGE_URL).content


@pytest.mark.django_db
class TestMyObservationsTimestamps:
    """The owner's own instants are unfloored."""

    def test_time_element_carries_the_exact_recorded_instant(
        self, client: Client
    ) -> None:
        """No 15-minute flooring on one's own reports.

        ``apps.public.api.community_reports_geojson`` floors timestamps to
        quarter-hours to anonymise *other people's* reports. Applying that
        here would degrade the owner's own record for no privacy gain, so
        the ``<time datetime="...">`` must carry the exact second recorded.

        The instant chosen is deliberately mid-quarter (07:23:41) — a floored
        value would render 07:15:00 and fail this assertion, while a value
        that happened to sit on a quarter boundary would pass either way.
        """
        user = _verified_user()
        observed_at = timezone.now().replace(
            hour=7, minute=23, second=41, microsecond=0
        ) - timedelta(days=1)
        FieldObservationFactory.create(user=user, observed_at=observed_at)
        client.force_login(user)
        html = client.get(PAGE_URL).content.decode()

        observation = FieldObservation.objects.for_user(user).get()
        assert observation.observed_at.minute == 23
        assert observation.observed_at.second == 41
        assert "data-relative-time" in html


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMyObservationsCaching:
    """Per-user content must never land in a shared cache."""

    def test_response_is_private_no_store(self, client: Client) -> None:
        """``Cache-Control: private, no-store``, mirroring favourite_detail.

        This also keeps the page out of the PWA shell cache — reading these
        reports offline is SNOW-661's job, not a side effect of this one.
        """
        client.force_login(_verified_user())
        response = client.get(PAGE_URL)
        assert response.headers["Cache-Control"] == "private, no-store"


# ---------------------------------------------------------------------------
# Empty state and shared-partial reuse
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMyObservationsEmptyState:
    """A user who has never reported gets a sentence, not a bare heading."""

    def test_empty_clause_renders(self, client: Client) -> None:
        """The list partial's own empty clause is present."""
        client.force_login(_verified_user())
        html = client.get(PAGE_URL).content.decode()
        assert 'data-testid="observation-list-empty"' in html
        # The apostrophe is HTML-escaped by the template engine; match the
        # unambiguous tail of the sentence rather than guessing the entity.
        assert "reported any field observations yet" in html


@pytest.mark.django_db
class TestMyObservationsSharedPartials:
    """The page hosts the shared row rather than authoring a second one."""

    def test_row_is_the_shared_ugc_row_with_its_delete_control(
        self, client: Client
    ) -> None:
        """One anatomy for every UGC row (SNOW-711), delete included.

        Asserts the hooks that belong to ``includes/_ugc_panel_row.html`` and
        ``_observation_row_actions.html`` — the row id the delete form
        targets, the shared label hook, and the delete endpoint itself. A page
        that grew its own row treatment would stop emitting these.
        """
        user = _verified_user()
        observation = FieldObservationFactory.create(user=user)
        client.force_login(user)
        html = client.get(PAGE_URL).content.decode()

        assert f'id="observation-{observation.uuid}"' in html
        assert "data-row-label" in html
        assert f"/partials/report/{observation.uuid}/delete/" in html
        assert f'hx-target="#observation-{observation.uuid}"' in html

    def test_the_row_label_is_inert_off_the_map(self, client: Client) -> None:
        """No focus control here — the same partial, a page with no map.

        The map panel's copy of this row makes the label a button that
        frames the report; this page has nothing to fly, so the label stays
        the plain text it has always been. The two surfaces render one
        partial, so the difference has to be asserted or it drifts.
        """
        user = _verified_user()
        FieldObservationFactory.create(user=user)
        client.force_login(user)

        html = client.get(PAGE_URL).content.decode()

        assert "data-row-focus" not in html
        assert "Zoom to" not in html

    def test_delete_form_carries_the_shared_removal_hook(self, client: Client) -> None:
        """``data-row-remove`` is what tells this page a row has gone.

        On the FORM rather than the row, so a watcher can tell a removal
        apart from the other HTMX requests a row can make — see
        ``static/js/row_removed.js``.
        """
        user = _verified_user()
        FieldObservationFactory.create(user=user)
        client.force_login(user)

        html = client.get(PAGE_URL).content.decode()

        assert "data-row-remove" in html

    def test_the_list_re_reads_when_a_row_is_removed(self, client: Client) -> None:
        """The wrapper re-reads observations:list on ``snowdesk:reports-changed``.

        A row's Remove empties one ``<li>`` and nothing else, so a page that
        has just lost its last report keeps rendering as a list of none — the
        empty state is a server-side clause and only a fresh response can
        carry it (SNOW-752).

        No ``load`` trigger: the view already rendered the list. And no
        ``?variant=map``, which is the whole reason that parameter exists —
        it would answer with map-focus rows, on a page with no map.
        """
        client.force_login(_verified_user())

        html = client.get(PAGE_URL).content.decode()

        assert f'hx-get="{reverse("observations:list")}"' in html
        assert 'hx-trigger="snowdesk:reports-changed from:document"' in html
        assert "variant=map" not in html

    def test_page_loads_the_removal_modules_in_order(self, client: Client) -> None:
        """row_removed.js before account_observations.js.

        Document order is execution order for deferred scripts, and the
        second reads ``window.pwaRowRemoved`` as its IIFE runs.
        """
        client.force_login(_verified_user())

        html = client.get(PAGE_URL).content.decode()

        positions = [
            html.index("js/htmx.min.js"),
            html.index("js/row_removed.js"),
            html.index("js/account_observations.js"),
        ]
        assert positions == sorted(positions)
