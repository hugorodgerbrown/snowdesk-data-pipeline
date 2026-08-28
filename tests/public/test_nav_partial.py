"""
tests/public/test_nav_partial.py — Tests for the nav partial's two dropdowns.

Covers:
  - Staff users (is_staff=True) see the admin menu and all four links
    (component library, push demo, edit map, Django admin).
  - Non-staff users (is_staff=False) do not see the admin menu element.
  - Anonymous users (AnonymousUser) do not see the admin menu element.
  - **Every entry in the account menu**, one assertion each. Routes used
    to appear and disappear with the ``routes`` waffle flag; SNOW-724
    retired it, so the entry is now asserted unconditionally like its
    siblings.
  - The SNOW-748 network toggle: present for every viewer including
    anonymous ones, shipped hidden and unpressed, with both glyphs and its
    strings template.

That last group is not decoration. ``docs/decisions/
account-area-navigation-lives-in-the-nav-menu.md`` makes this menu the
account area's ONLY navigation, and two pages have already shipped without
an entry in it: ``/account/observations/`` (SNOW-677) and
``/account/routes/`` (SNOW-713) were both mounted, tested and unreachable —
their tests reverse the URL directly, so a green suite said nothing about
whether a user could get there. SNOW-668 added the entries and this file is
what stops a sixth page shipping the same way.

The template is rendered in isolation via render_to_string + RequestFactory
so no database views or URL routing are needed. ``request=`` is load-bearing
rather than incidental: it builds a RequestContext, which is what runs the
context processors nav.html depends on (``nav_subscriptions``) — this
partial renders on pages that pass no context of their own, this one
included.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import reverse

from tests.factories import UserFactory


@pytest.fixture()
def rf() -> RequestFactory:
    """Return a Django RequestFactory."""
    return RequestFactory()


@pytest.fixture()
def staff_user(db: Any) -> User:
    """Return a staff Django user."""
    return UserFactory.create(is_staff=True)


@pytest.fixture()
def regular_user(db: Any) -> User:
    """Return a non-staff Django user."""
    return UserFactory.create(is_staff=False)


def _render_nav_for(rf: RequestFactory, user: User | AnonymousUser) -> str:
    """Render nav.html for one viewer and return the HTML.

    Attaches a session because a bare ``RequestFactory`` request has none
    and a real one always does. It was load-bearing while the nav's Routes
    entry was flag-gated — waffle reads a session when evaluating a
    percentage rollout — and SNOW-724 removed that gate; the attach stays
    because rendering a nav against a request no browser could send is a
    worse test, not because a specific line needs it today.

    Args:
        rf: The RequestFactory building the request.
        user: The viewer to render for.

    Returns:
        The rendered nav HTML.

    """
    request = rf.get("/")
    request.user = user
    SessionMiddleware(lambda _req: HttpResponse()).process_request(request)
    return render_to_string("includes/nav.html", {}, request=request)


@pytest.mark.django_db
class TestNavAdminMenu:
    """Tests for the staff-only Admin dropdown rendered inside nav.html."""

    def test_staff_sees_admin_links(self, rf: RequestFactory, staff_user: User) -> None:
        """Staff users see the admin menu and all four destination links."""
        request = rf.get("/")
        request.user = staff_user
        html = render_to_string("includes/nav.html", {}, request=request)
        assert 'id="admin-menu"' in html
        assert reverse("public:components_index") in html
        assert reverse("public:push_demo") in html
        assert reverse("public:home") + "?edit=resorts" in html
        assert reverse("admin:index") in html

    def test_non_staff_sees_no_admin_menu(
        self, rf: RequestFactory, regular_user: User
    ) -> None:
        """Non-staff authenticated users do not see the admin menu."""
        request = rf.get("/")
        request.user = regular_user
        html = render_to_string("includes/nav.html", {}, request=request)
        assert 'id="admin-menu"' not in html

    def test_anonymous_sees_no_admin_menu(self, rf: RequestFactory) -> None:
        """Anonymous users do not see the admin menu."""
        request = rf.get("/")
        request.user = AnonymousUser()
        html = render_to_string("includes/nav.html", {}, request=request)
        assert 'id="admin-menu"' not in html


@pytest.mark.django_db
class TestNavObservationsLink:
    """The Observations link was removed from the nav (menu cleanup, #497)."""

    def test_link_absent(self, rf: RequestFactory) -> None:
        """The link is absent for every viewer."""
        request = rf.get("/")
        request.user = AnonymousUser()
        html = render_to_string("includes/nav.html", {}, request=request)
        assert reverse("public:observations") not in html


@pytest.mark.django_db
class TestNavAuthArea:
    """The unauthenticated auth area shows a single "Sign in" button."""

    def test_anonymous_sees_sign_in_button(self, rf: RequestFactory) -> None:
        """Anonymous users see a "Sign in" link to the sign-in page..."""
        request = rf.get("/")
        request.user = AnonymousUser()
        html = render_to_string("includes/nav.html", {}, request=request)
        assert reverse("accounts:sign_in") in html
        assert "Sign in" in html

    def test_anonymous_sees_no_register_link(self, rf: RequestFactory) -> None:
        """...and no standalone Register link (registration lives on sign-in)."""
        request = rf.get("/")
        request.user = AnonymousUser()
        html = render_to_string("includes/nav.html", {}, request=request)
        assert reverse("accounts:register") not in html

    def test_authenticated_sees_the_subscriptions_link(
        self, rf: RequestFactory, regular_user: User
    ) -> None:
        """Authenticated users see the "Subscriptions" menu item.

        It was "My account" until SNOW-668. The hub stopped being a hub when
        favourites moved off it, and inside /account/ the possessive carries
        no information — every page in the area is the viewer's by
        definition.
        """
        html = _render_nav_for(rf, regular_user)
        assert reverse("accounts:hub") in html
        assert "Subscriptions" in html
        assert "My account" not in html


@pytest.mark.django_db
class TestNavAccountMenuEntries:
    """Every account page is reachable from this menu, asserted one by one.

    The regression guard for the defect SNOW-668 fixed: an account page can
    be mounted, covered by its own passing tests, and reachable only by
    typing its URL, because those tests reverse the URL rather than
    following a link. Deleting an assertion here is deleting the only thing
    that would notice.
    """

    def test_every_unflagged_entry_is_present(
        self, rf: RequestFactory, regular_user: User
    ) -> None:
        """Subscriptions, Favourites, Observations, Settings and Sign out.

        Routes is asserted separately below because it is the one entry
        behind a flag.
        """
        html = _render_nav_for(rf, regular_user)

        for url_name, label in (
            ("accounts:hub", "Subscriptions"),
            ("accounts:favourites", "Favourites"),
            ("accounts:observations", "Observations"),
            ("accounts:settings", "Settings"),
            ("accounts:sign_out", "Sign out"),
        ):
            assert f'href="{reverse(url_name)}"' in html or (
                # Sign out is a POST form, not an anchor.
                f'action="{reverse(url_name)}"' in html
            ), url_name
            assert label in html, label

    def test_entries_render_in_the_ranked_order(
        self, rf: RequestFactory, regular_user: User
    ) -> None:
        """Content first, account machinery last (SNOW-705's ranking).

        Subscriptions is what an account is for; Favourites and Observations
        are the lists of saved things; Settings and Sign out are machinery
        and sit below a rule. Order is the ranking, so a reshuffle that puts
        Sign out mid-menu is a real regression.
        """
        html = _render_nav_for(rf, regular_user)

        positions = [
            html.index(f'href="{reverse(name)}"')
            for name in (
                "accounts:hub",
                "accounts:favourites",
                "accounts:observations",
                "accounts:settings",
            )
        ]
        assert positions == sorted(positions)
        assert positions[-1] < html.index(f'action="{reverse("accounts:sign_out")}"')

    def test_routes_entry_is_offered_to_every_signed_in_user(
        self, rf: RequestFactory, regular_user: User
    ) -> None:
        """The signed-in menu offers /account/routes/.

        This is the entry SNOW-713 could not add, which is why that page
        shipped orphaned. It was flag-gated when SNOW-668 added it, because
        ``my_routes`` answered 404 behind an inactive ``routes`` flag;
        SNOW-724 removed both the flag and that 404, so an ordinary account
        gets the entry and the destination it points at.
        """
        html = _render_nav_for(rf, regular_user)
        assert f'href="{reverse("accounts:routes")}"' in html
        assert "Routes" in html

    def test_anonymous_sees_no_account_entries(self, rf: RequestFactory) -> None:
        """The whole menu is behind the authenticated branch.

        Routes is named in the loop below alongside its siblings: with no
        flag left to hide it, an entry that escaped the
        ``request.user.is_authenticated`` guard shows up here.
        """
        html = _render_nav_for(rf, AnonymousUser())

        for url_name in (
            "accounts:hub",
            "accounts:favourites",
            "accounts:routes",
            "accounts:observations",
            "accounts:settings",
        ):
            assert f'href="{reverse(url_name)}"' not in html, url_name


@pytest.mark.django_db
class TestNavNetworkToggle:
    """The SNOW-748 network toggle, beside the sync badge.

    SNOW-742 built this control and put it inside the offline banner, which
    ``static/js/pwa_offline.js`` reveals only when the connection has already
    failed — so the user it was built for ("I have signal now and am about
    to lose it") could never reach it. Here it renders on every page for
    every viewer, and these assertions are what stops it drifting back
    behind a condition.
    """

    def test_anonymous_sees_the_toggle(self, rf: RequestFactory) -> None:
        """Anonymous users get it too.

        Deliberate, not an oversight: basemap downloads are not auth-gated,
        so neither is the switch deciding whether the app calls the server.
        """
        html = _render_nav_for(rf, AnonymousUser())
        assert "data-network-toggle" in html

    def test_authenticated_sees_the_toggle(
        self, rf: RequestFactory, regular_user: User
    ) -> None:
        """...and so do signed-in users, in the same place."""
        html = _render_nav_for(rf, regular_user)
        assert "data-network-toggle" in html

    def test_toggle_renders_hidden_and_unpressed(self, rf: RequestFactory) -> None:
        """It ships hidden and in the "using the network" state.

        Hidden because ``pwa_offline.js`` reveals it, exactly as
        ``mutation_queue.js`` reveals the sync badge beside it: a control
        that only works while a script is running must not be on screen when
        that script is absent (an old cached shell, a JS error). Unpressed
        because the mode a page boots in is ``'auto'`` — the script repaints
        it after reading the persisted mode back.
        """
        html = _render_nav_for(rf, AnonymousUser())
        # The opening tag only — the button's own class list. Searching the
        # whole element would match the ``hidden`` on the off-state glyph
        # inside it and pass whatever the button itself carried.
        opening_tag = html.split("data-network-toggle", 1)[1].split(">", 1)[0]
        assert 'aria-pressed="false"' in opening_tag
        assert "hidden" in opening_tag

    def test_toggle_renders_both_glyphs(self, rf: RequestFactory) -> None:
        """Both marks are server-rendered; the script swaps ``hidden``.

        The glyph paths stay in ``includes/_icon_wifi.html`` and
        ``includes/_icon_wifi_off.html`` — a JS-built SVG would put a second
        copy of the mark somewhere ``bin/ds-lint`` and a reader both have to
        find.
        """
        html = _render_nav_for(rf, AnonymousUser())
        button = html.split("data-network-toggle", 1)[1].split("</button>", 1)[0]
        assert 'data-role="network-on"' in button
        assert 'data-role="network-off"' in button

    def test_toggle_labels_come_from_a_strings_template(
        self, rf: RequestFactory
    ) -> None:
        """Both labels are rendered server-side for ``pwaStrings.read()``.

        The label names the action, so it changes with the state — and a
        label assigned from a JavaScript literal ships English to every
        locale, because ``makemessages`` never scans ``static/js``. This is
        the template ``bin/i18n-lint`` exists to keep populated.
        """
        html = _render_nav_for(rf, AnonymousUser())
        assert 'id="network-toggle-strings-template"' in html
        assert 'data-string="go-offline"' in html
        assert 'data-string="go-online"' in html
