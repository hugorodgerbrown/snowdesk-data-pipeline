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
  - The anonymous "Sign in" button, which since SNOW-826 carries the page
    the visitor is on as ``?next=`` so signing in from the nav returns them
    to it.
  - The SNOW-748 offline surfaces, split the way a phone splits aeroplane
    mode: a header SYMBOL every viewer gets, and an "Offline mode" switch
    that sets what it reports. SNOW-921 moved that switch out of the
    account menu and into the network menu the symbol opens, so BOTH are
    now rendered for every viewer — the asymmetry the earlier tests pinned
    is the thing this file now pins the removal of.
  - The SNOW-921 additions to that menu: the traffic arrows beside the
    symbol, and the flag-gated "Debug log" row.

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
included. (SNOW-802 removed ``nav_subscriptions``; the menu no longer
lists regions.)
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import reverse
from waffle.testutils import override_flag

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
        """Staff users see the admin menu and all five destination links.

        SNOW-755 made it five: the single "Edit map" item pointed only at
        ``?edit=resorts``, so the location editor had no entry point at
        all and could only be reached by typing the querystring.
        """
        request = rf.get("/")
        request.user = staff_user
        html = render_to_string("includes/nav.html", {}, request=request)
        assert 'id="admin-menu"' in html
        assert reverse("public:components_index") in html
        assert reverse("public:push_demo") in html
        assert reverse("public:home") + "?edit=resorts" in html
        assert reverse("public:home") + "?edit=locations" in html
        assert reverse("admin:index") in html

    def test_both_editors_are_named_in_the_menu(
        self, rf: RequestFactory, staff_user: User
    ) -> None:
        """The label says which estate, because two items now share a shape.

        "Edit map" was unambiguous while there was one editor. With two it
        would say nothing about which one the item opens.
        """
        request = rf.get("/")
        request.user = staff_user
        html = render_to_string("includes/nav.html", {}, request=request)
        assert "Edit resorts" in html
        assert "Edit locations" in html

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

    def test_the_sign_in_button_carries_the_current_page_as_next(
        self, rf: RequestFactory
    ) -> None:
        """SNOW-826: signing in from the nav returns the visitor here.

        The sibling above asserts only that the sign-in URL appears, which
        a bare link satisfies just as well as one carrying a destination —
        so it cannot catch this regressing. The path is asserted encoded
        and in full, query string included: an unencoded ``?`` would end
        the ``next`` value at the destination's own query string and land
        the visitor on the map for that day rather than this one.
        """
        request = rf.get("/", {"d": "2026-04-08"})
        request.user = AnonymousUser()

        html = render_to_string("includes/nav.html", {}, request=request)

        expected = f'href="{reverse("accounts:sign_in")}?next=/%3Fd%3D2026-04-08"'
        assert expected in html

    def test_the_sign_in_button_carries_a_page_with_no_query_string(
        self, rf: RequestFactory
    ) -> None:
        """The common case, where the path is its own whole destination."""
        request = rf.get("/trips/")
        request.user = AnonymousUser()

        html = render_to_string("includes/nav.html", {}, request=request)

        assert f'href="{reverse("accounts:sign_in")}?next=/trips/"' in html

    def test_anonymous_sees_no_register_link(self, rf: RequestFactory) -> None:
        """...and no standalone Register link (registration lives on sign-in)."""
        request = rf.get("/")
        request.user = AnonymousUser()
        html = render_to_string("includes/nav.html", {}, request=request)
        assert reverse("accounts:register") not in html

    def test_authenticated_sees_the_settings_link(
        self, rf: RequestFactory, regular_user: User
    ) -> None:
        """Authenticated users see the "Settings" menu item.

        "Subscriptions" was the entry until SNOW-802 folded the hub into the
        map's pins sheet; "My account" the one before that (SNOW-668). Inside
        /account/ the possessive carries no information.
        """
        html = _render_nav_for(rf, regular_user)
        assert reverse("accounts:settings") in html
        assert "Settings" in html
        assert "Subscriptions" not in html
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

    def test_every_entry_is_present(
        self, rf: RequestFactory, regular_user: User
    ) -> None:
        """Trips, Settings and Sign out — each asserted by name.

        SNOW-803 removed Favourites, Routes and Observations and SNOW-802
        Subscriptions: those are map sheets now, reached from the map's
        roundels. SNOW-823 added Trips, which is not a fourth of those —
        a trip is indexed by WHEN and the map indexes by where
        (docs/decisions/two-documents-and-a-map.md). Every entry keeps a
        positive assertion, so one cannot vanish the way the two orphaned
        pages once shipped.
        """
        html = _render_nav_for(rf, regular_user)

        for url_name, label in (
            ("trips:list", "Trips"),
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
        """Trips, then Settings, then Sign out (SNOW-705's ranking).

        Order is the ranking, so a reshuffle is a real regression. Trips is
        a destination a signed-in user goes to; Settings is machinery they
        visit rarely; Sign out is last on the rule that was already here.
        """
        html = _render_nav_for(rf, regular_user)

        assert html.index(f'href="{reverse("trips:list")}"') < html.index(
            f'href="{reverse("accounts:settings")}"'
        )
        assert html.index(f'href="{reverse("accounts:settings")}"') < html.index(
            f'action="{reverse("accounts:sign_out")}"'
        )

    def test_the_three_list_entries_are_gone(
        self, rf: RequestFactory, regular_user: User
    ) -> None:
        """Favourites, Routes and Observations no longer appear (SNOW-803).

        Their URLs still resolve — as permanent redirects to the map with the
        matching sheet open — but the menu must not offer a link to a
        redirect, and the map's own roundels are where those lists live.
        """
        html = _render_nav_for(rf, regular_user)
        for url_name, label in (
            ("accounts:favourites", ">Favourites<"),
            ("accounts:routes", ">Routes<"),
            ("accounts:observations", ">Observations<"),
            ("accounts:hub", ">Subscriptions<"),
        ):
            assert f'href="{reverse(url_name)}"' not in html, url_name
            assert label not in html, label

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


def _class_tokens(opening_tag: str) -> set[str]:
    """Return the class utilities on an opening tag, as whole tokens.

    ``"hidden" in tag`` is not the same question as "is this element
    hidden": a Tailwind variant such as
    ``[&::-webkit-details-marker]:hidden`` contains the substring while
    hiding only the disclosure triangle. Every ``<summary>`` in this nav
    carries exactly that, so the distinction is load-bearing here.
    """
    _, _, after = opening_tag.partition('class="')
    return set(after.partition('"')[0].split())


def _opening_tag_around(html: str, marker: str) -> str:
    """Return the whole opening tag containing ``marker``.

    The attribute a test locates an element by is rarely the first one on
    the tag, so splitting forward from it drops everything written before —
    which is where ``role`` and ``aria-checked`` live on the offline-mode
    row. This takes the tag from its own ``<`` to its own ``>``, so an
    assertion about the element's attributes cannot pass or fail on the
    order they happen to be written in.
    """
    before, _, after = html.partition(marker)
    return f"<{before.rsplit('<', 1)[1]}{marker}{after.split('>', 1)[0]}>"


@pytest.mark.django_db
class TestNavConnectivitySymbol:
    """The SNOW-748 connectivity symbol, beside the sync badge.

    The status-bar half of the aeroplane-mode model this feature follows,
    and PERMANENT: it renders on every page for every viewer, online or
    off, signed in or not, and is never ``hidden``. An earlier pass hid it
    in ``'auto'`` on the phone's-aeroplane-glyph model, which meant the one
    element telling a user whether their avalanche data was live existed
    only once it was not. Its permanence is what allowed
    ``includes/_offline_banner.html`` to be deleted.

    It is a control, but a DISCLOSURE: it is the ``<summary>`` of a native
    ``<details>``, and pressing it opens the connection-status panel
    (``includes/_connection_panel.html``) anchored beneath it — the third
    disclosure in this header, built like the two dropdowns beside it. It
    never changes the network mode — that is the switch below, and a control
    in the status area that changes the thing it reports invites exactly the
    misread that the symbol IS the switch.

    It is shown to anonymous viewers as well as signed-in ones, unlike the
    switch. The worker latches itself for anybody, so the state the symbol
    reports is one an anonymous user can be in, and the panel's CTA is
    their only way out of it; only CHOOSING the mode needs an account.
    """

    def test_anonymous_sees_the_symbol(self, rf: RequestFactory) -> None:
        """Anonymous users get it.

        Deliberate, not an oversight: an anonymous user can be latched
        offline by the worker exactly as a signed-in one can, and a state
        nothing announces is a state the user has to guess at.
        """
        html = _render_nav_for(rf, AnonymousUser())
        assert "data-network-indicator" in html

    def test_authenticated_sees_the_symbol(
        self, rf: RequestFactory, regular_user: User
    ) -> None:
        """...and so do signed-in users, in the same place."""
        html = _render_nav_for(rf, regular_user)
        assert "data-network-indicator" in html

    def test_symbol_is_never_hidden(self, rf: RequestFactory) -> None:
        """It ships visible, in the "using the network" state.

        The assertion that carries the whole rework: the banner this
        replaced only appeared once something was wrong, so a user learned
        where to look by losing their connection. A permanent mark that
        changes appearance is the stronger guarantee, and it is only
        stronger while it is actually permanent.
        """
        opening_tag = _opening_tag_around(
            _render_nav_for(rf, AnonymousUser()), "data-network-indicator"
        )
        # The class TOKEN, not the substring: the summary carries
        # ``[&::-webkit-details-marker]:hidden`` to suppress the disclosure
        # triangle, exactly as its two sibling menus do, and a substring
        # check reads that as the element hiding itself.
        assert "hidden" not in _class_tokens(opening_tag)
        assert 'data-network-state="online"' in opening_tag

    def test_symbol_is_a_disclosure_not_a_toggle(self, rf: RequestFactory) -> None:
        """``aria-expanded`` + ``aria-controls``, never ``aria-pressed``.

        SNOW-748 shipped this as a ``<button aria-pressed>`` that switched
        the network mode. It is a control again, but for a different job: it
        discloses the panel and nothing else. These assertions are what
        stops it drifting back into a mode control.
        """
        opening_tag = _opening_tag_around(
            _render_nav_for(rf, AnonymousUser()), "data-network-indicator"
        )
        assert 'aria-expanded="false"' in opening_tag
        assert 'aria-controls="pwa-connection-panel"' in opening_tag
        assert "aria-pressed" not in opening_tag

    def test_symbol_is_the_summary_of_a_native_disclosure(
        self, rf: RequestFactory
    ) -> None:
        """A ``<details data-network-panel>`` wrapping a ``<summary>``.

        The same primitive as the account and admin menus, and the reason
        this surface needs no dismissal mechanism of its own: opening,
        closing, Enter/Space and focus are the browser's, and nav.html's
        shared ``enhanceDisclosure`` script adds outside-click and Escape to
        all three alike. The first pass anchored nothing and fixed the panel
        to the bottom of the viewport instead.
        """
        html = _render_nav_for(rf, AnonymousUser())
        assert '<details class="relative" data-network-panel>' in html
        opening_tag = _opening_tag_around(html, "data-network-indicator")
        assert opening_tag.startswith("<summary")
        assert 'id="network-indicator-toggle"' in opening_tag

    def test_symbol_carries_both_glyphs(self, rf: RequestFactory) -> None:
        """Both marks are server-rendered; the script only toggles ``hidden``.

        Building either glyph in JavaScript would put a mark on the page
        that no template ever declared, and the struck-through one would
        arrive a frame after the state it reports.
        """
        html = _render_nav_for(rf, AnonymousUser())
        element = html.split("data-network-indicator", 1)[1].split("</summary>", 1)[0]
        assert 'data-role="network-online-icon"' in element
        assert 'data-role="network-offline-icon"' in element

    def test_symbol_paints_available_online_and_muted_offline(
        self, rf: RequestFactory
    ) -> None:
        """Coloured means reaching the network; grey means not.

        The sync-status pair, whose whole purpose is "available / not
        available" — the question this symbol answers. It shipped inverted
        for one pass: the plain arcs took the muted ``text-text-3`` and the
        struck-through ones ``text-status-warning-text``, which both read
        the wrong way round (the working state was the greyed one) and
        borrowed a flash-message severity for a state that is not an error.
        A user who has chosen offline mode is not being warned.
        """
        html = _render_nav_for(rf, AnonymousUser())
        online = _class_tokens(
            _opening_tag_around(html, 'data-role="network-online-icon"')
        )
        offline = _class_tokens(
            _opening_tag_around(html, 'data-role="network-offline-icon"')
        )
        assert "text-sync-ok" in online
        assert "text-sync-off" in offline
        assert not [cls for cls in online | offline if cls.startswith("text-status-")]

    def test_symbol_has_an_accessible_name_for_each_state(
        self, rf: RequestFactory
    ) -> None:
        """The glyphs are aria-hidden, so sr-only text carries the name.

        Both ``includes/_icon_wifi.html`` and ``_icon_wifi_off.html`` set
        ``aria-hidden="true"`` on their own <svg>, which is right for a
        decorative mark and would leave this button nameless. Two names,
        one per state, rendered here and toggled by ``hidden`` — a name
        assigned from a JS literal ships English to every locale, because
        ``makemessages`` never scans ``static/js``.
        """
        html = _render_nav_for(rf, AnonymousUser())
        element = html.split("data-network-indicator", 1)[1].split("</summary>", 1)[0]
        assert "Network menu: using the network" in element
        assert "Network menu: offline" in element


@pytest.mark.django_db
class TestNavTrafficArrows:
    """The SNOW-921 traffic pair, beside the connectivity glyph.

    The header could say whether the app COULD reach the server and when it
    last DID, and nothing at all about whether anything was moving right
    now. A pan over a downloaded region and a pan spending a roaming
    connection looked identical from the top bar.

    Two arrows, one mark rotated, lit for a beat by
    ``static/js/pwa_offline.js``: up when a request goes out, down when a
    response comes back. Deliberately approximate — they answer "is
    anything moving", not "how much" — so nothing here asserts a count.

    What the assertions below pin is the part a refactor would quietly get
    wrong: that the pair is decoration in the accessibility sense and says
    so, that it is one partial rather than two drawings of the same idea,
    and that it is painted by a data attribute rather than a class the
    script builds.
    """

    def test_both_directions_render_for_every_viewer(self, rf: RequestFactory) -> None:
        """Anonymous readers get them too, like the glyph they sit beside.

        The pair belongs to the symbol, and the symbol is permanent for
        every viewer (see ``TestNavConnectivitySymbol``). Nothing about
        watching traffic needs an account.
        """
        html = _render_nav_for(rf, AnonymousUser())
        assert 'data-traffic-arrow="up"' in html
        assert 'data-traffic-arrow="down"' in html

    def test_the_pair_is_hidden_from_assistive_technology(
        self, rf: RequestFactory
    ) -> None:
        """``aria-hidden`` on the wrapper, and no string of their own.

        Not an omission. Announcing "sent, received, sent, received" over a
        tile burst would be noise, and everything the arrows hint at is
        already said in words by the symbol's own accessible name and the
        menu's summary line. A future pass that gives them a label should
        have to delete this test and argue with it.
        """
        html = _render_nav_for(rf, AnonymousUser())
        wrapper = _opening_tag_around(html, "data-traffic-arrows")
        assert 'aria-hidden="true"' in wrapper

    def test_the_down_arrow_is_the_up_arrow_rotated(self, rf: RequestFactory) -> None:
        """One partial, two orientations — never two drawings.

        The same contract ``includes/_icon_chevron.html`` has with the row
        disclosure, and for the same reason the wifi pair has its own: a
        reader takes the difference between the two marks as meaning
        something, so the only difference allowed is the rotation.
        """
        html = _render_nav_for(rf, AnonymousUser())
        up = html.split('data-traffic-arrow="up"', 1)[1].split("</span>", 1)[0]
        down = html.split('data-traffic-arrow="down"', 1)[1].split("</span>", 1)[0]
        # The path data is identical; only the down one carries the rotation.
        assert 'd="M12 20V5"' in up
        assert 'd="M12 20V5"' in down
        assert "rotate-180" not in up
        assert "rotate-180" in down

    def test_the_pair_is_staggered_and_overlapped(self, rf: RequestFactory) -> None:
        """Shoulder to shoulder, not stacked in a column.

        The wrapper is a ``flex`` ROW: two arrows in a vertical column read
        as two separate lamps that happen to be adjacent, and the point of
        the pair is that they are not independent — a user is meant to read
        "traffic, in this direction". The stagger and the overlap that turn
        them into one transfer mark are in ``src/css/main.css`` with the
        paint, so what is pinned here is the axis, which is the half a
        stylesheet cannot put back if a later pass restacks them.
        """
        html = _render_nav_for(rf, AnonymousUser())
        classes = _class_tokens(_opening_tag_around(html, "data-traffic-arrows"))
        assert "inline-flex" in classes
        assert "flex-col" not in classes

    def test_arrows_carry_no_class_of_their_own(self, rf: RequestFactory) -> None:
        """Colour, opacity AND geometry live in ``src/css/main.css``.

        The rest state is 22% opacity with a transition either side of it,
        which Tailwind cannot express on a JS-driven state — so
        ``[data-traffic-arrow]`` owns the whole appearance and
        ``pwa_offline.js`` only sets and clears ``data-active``. That is
        what keeps a colour out of a JS class string, where ``bin/ds-lint``
        would have to catch it and a typo would not show up at all.

        The offsets went the same way rather than into Tailwind utilities
        here: they are chosen against the colour they sit next to, and a
        mark whose geometry is in one file and whose paint is in another is
        a mark nobody adjusts correctly.
        """
        html = _render_nav_for(rf, AnonymousUser())
        for direction in ("up", "down"):
            assert "class" not in _opening_tag_around(
                html, f'data-traffic-arrow="{direction}"'
            )


@pytest.mark.django_db
class TestNavConnectionPanel:
    """The SNOW-748 connection-status panel, anchored to the symbol above.

    Built first on ``includes/_toast.html`` and rebuilt here, because a toast
    is transient, system-initiated, bottom-centred and status-coloured, and
    this surface is none of those: it is user-invoked, persistent, read at
    the point that summoned it, and it describes a healthy connection as
    often as a broken one. Every complaint the toast version drew — a new
    blue, the wrong end of the screen, a "×" that was neither top-right nor
    a tap target — followed from that one wrong primitive.

    What replaces it is the shape this header already uses twice: a popover
    anchored under its disclosure, on the card tokens, closed by the shared
    ``enhanceDisclosure`` script. The tests below pin the three properties a
    later refactor would silently drop — where it is anchored, that it is
    neutral rather than status-coloured, and that its close control is a real
    44×44 target — plus the CTA, which is the only exit an anonymous reader
    has from an auto-latched service worker.
    """

    def test_panel_is_anchored_under_the_symbol(self, rf: RequestFactory) -> None:
        """Positioned like the account and admin dropdowns, not fixed.

        ``absolute right-0 top-full`` inside the ``relative`` <details> puts
        the detail under the finger that asked for it. The toast version was
        ``fixed bottom-4 left-1/2``, i.e. as far from the control as the
        screen allows, on a page whose control is in the top-right corner.
        """
        html = _render_nav_for(rf, AnonymousUser())
        classes = _class_tokens(_opening_tag_around(html, 'id="pwa-connection-panel"'))
        assert {"absolute", "right-0", "top-full"} <= classes
        assert "fixed" not in classes

    def test_panel_is_a_card_not_a_status_colour(self, rf: RequestFactory) -> None:
        """Neutral surface tokens, and no ``status-*`` paint anywhere on it.

        The toast version passed ``kind="info"``, so the panel painted itself
        ``status-info`` blue while the symbol beside it painted the same state
        ``status-warning`` amber. Neither carries a ``status-*`` colour now:
        the symbol paints the sync-status pair (see
        ``test_symbol_paints_available_online_and_muted_offline``), and the
        panel is the card the detail is read on.
        """
        html = _render_nav_for(rf, AnonymousUser())
        classes = _class_tokens(_opening_tag_around(html, 'id="pwa-connection-panel"'))
        assert {"bg-card", "border-border", "text-text-1"} <= classes
        assert not [cls for cls in classes if cls.startswith("bg-status-")]

    def test_panel_is_height_bounded_and_scrolls(self, rf: RequestFactory) -> None:
        """It floats over the map, and that rule has no exceptions here.

        ``dvh`` rather than ``vh``: ``vh`` ignores mobile browser chrome, so
        a ``vh``-bounded panel resolves taller than the visible viewport
        exactly where the bound is needed.
        """
        html = _render_nav_for(rf, AnonymousUser())
        classes = _class_tokens(_opening_tag_around(html, 'id="pwa-connection-panel"'))
        assert "max-h-[60dvh]" in classes
        assert "overflow-y-auto" in classes

    def test_close_control_is_top_right_and_a_real_tap_target(
        self, rf: RequestFactory
    ) -> None:
        """44×44 minimum, named "Dismiss", first control in the panel.

        The toast's "×" was ``px-1 leading-none`` with no minimum size, and
        sat after the CTA so it wrapped to the bottom of the panel. This one
        is drawn with ``icon_button_classes`` — the ``h-11 w-11`` idiom every
        panel header's close already uses — and is emitted before the
        explanation and the CTA, so it renders in the top-right corner.
        """
        html = _render_nav_for(rf, AnonymousUser())
        panel = html.split('id="pwa-connection-panel"', 1)[1]
        closer = _opening_tag_around(panel, "data-disclosure-close")
        classes = _class_tokens(closer)
        assert {"h-11", "w-11"} <= classes
        assert "Dismiss" in closer
        assert panel.index("data-disclosure-close") < panel.index(
            "data-network-reconnect"
        )

    def test_close_control_uses_the_shared_disclosure_mechanism(
        self, rf: RequestFactory
    ) -> None:
        """``[data-disclosure-close]``, handled by nav.html's own script.

        Not a third dismissal mechanism and not ``overlays.js``: the same
        script that gives all three of this header's disclosures
        outside-click and Escape closes this control too, so there is one
        answer to "how does a nav disclosure close".
        """
        html = _render_nav_for(rf, AnonymousUser())
        assert "enhanceDisclosure('data-network-panel'" in html
        assert "[data-disclosure-close]" in html

    def test_panel_carries_every_state_and_both_ways_back(
        self, rf: RequestFactory
    ) -> None:
        """All four explanations and both CTA labels are server-rendered.

        ``makemessages`` never scans ``static/js``, so a string set from a JS
        literal ships as English to every locale — ``bin/i18n-lint`` fails on
        exactly that. Every variant is rendered here and toggled by
        ``hidden`` in ``static/js/pwa_offline.js``.
        """
        html = _render_nav_for(rf, AnonymousUser())
        panel = html.split('id="pwa-connection-panel"', 1)[1]
        for role in (
            "online-message",
            "offline-message",
            "latched-message",
            "synced-at",
            "online-explainer",
            "offline-explainer",
            "latched-explainer",
            "forced-explainer",
            "reconnect-label",
            "resume-label",
        ):
            assert f'data-role="{role}"' in panel

    def test_anonymous_gets_the_panel_and_its_way_back(
        self, rf: RequestFactory
    ) -> None:
        """The whole menu renders for a signed-out reader.

        The service worker latches offline for anybody, so the state this
        menu describes is one an anonymous reader can be in, and a refactor
        that folded it into the account dropdown would take it away from
        exactly the people who cannot do without it.

        SNOW-748 made the CTA below their ONLY exit from that state,
        because the "Offline mode" switch was in the account menu. SNOW-921
        ended that asymmetry by moving the switch in here — see
        ``TestNavOfflineModeSwitch`` — so an anonymous reader now has both.
        The CTA is still asserted here because it is what the panel
        promises in its own right.
        """
        html = _render_nav_for(rf, AnonymousUser())
        assert 'id="pwa-connection-panel"' in html
        assert "data-network-reconnect" in html
        assert "Try reconnecting" in html
        assert "Use the network again" in html

    def test_panel_ships_closed(self, rf: RequestFactory) -> None:
        """Closed at rest: the <details> has no ``open`` attribute.

        Visibility belongs to the disclosure, not to a class the script
        toggles — which is why nothing in ``pwa_offline.js`` opens or closes
        this panel any more.
        """
        html = _render_nav_for(rf, AnonymousUser())
        assert '<details class="relative" data-network-panel>' in html
        assert "data-network-panel open" not in html


@pytest.mark.django_db
class TestNavOfflineModeSwitch:
    """The "Offline mode" switch — in the NETWORK MENU since SNOW-921.

    The settings half of the aeroplane-mode model. SNOW-742 built it inside
    the offline banner, which ``static/js/pwa_offline.js`` revealed only
    once the connection had already failed — so the user it was built for
    ("I have signal now and am about to lose it") could never reach it.
    SNOW-748 moved it to the account dropdown, on the reasoning that the
    mode is a device preference and belongs with the other device
    preferences.

    SNOW-921 moved it again, into the menu the connectivity symbol opens,
    and that move changes WHO HAS IT. The account dropdown was the only
    menu available to put it in, so "device preference" quietly became
    "account feature": an anonymous reader who got latched on a lift could
    only escape a mode chosen for them, never choose one. Nothing about the
    mode ever needed an account — it is a row in this device's IndexedDB
    and a flag in this device's service worker.

    The assertions below pin that reversal explicitly, because it is the
    kind of thing a later refactor "tidies" back: the switch renders for an
    anonymous viewer, it is inside the panel rather than the account menu,
    and the account menu no longer carries it.
    """

    def test_anonymous_sees_the_switch(self, rf: RequestFactory) -> None:
        """The reversal, asserted head-on.

        This assertion was ``not in`` until SNOW-921. It is the whole point
        of the move: the reader most likely to want offline mode — on a
        lift, one bar of signal, no account — is the one who could not
        reach it.
        """
        html = _render_nav_for(rf, AnonymousUser())
        assert "data-network-toggle" in html

    def test_authenticated_sees_the_switch(
        self, rf: RequestFactory, regular_user: User
    ) -> None:
        """And a signed-in user still does, in the same place."""
        html = _render_nav_for(rf, regular_user)
        assert "data-network-toggle" in html

    def test_switch_lives_in_the_network_menu(self, rf: RequestFactory) -> None:
        """Inside ``#pwa-connection-panel``, not merely somewhere in the nav.

        Asserted by containment rather than by eye: the row would still be
        "present" if a refactor left it floating in the header, and the
        whole argument for the move is that it sits with the surface that
        reports the state it sets.
        """
        html = _render_nav_for(rf, AnonymousUser())
        panel = html.split('id="pwa-connection-panel"', 1)[1].split("</details>", 1)[0]
        assert "data-network-toggle" in panel

    def test_account_menu_no_longer_carries_it(
        self, rf: RequestFactory, regular_user: User
    ) -> None:
        """One switch, in one place.

        The failure this guards against is the obvious one for a move: the
        new copy lands and the old one is never deleted, leaving two
        controls driving the same mode, both painted by the same
        ``pwa_offline.js`` selector — which selects the first only, so the
        second would be a dead switch that never repaints.
        """
        html = _render_nav_for(rf, regular_user)
        menu = html.split('id="subscriber-menu"', 1)[1]
        assert "data-network-toggle" not in menu
        assert "nav-offline-mode" not in menu

    def test_the_account_menu_keeps_no_orphaned_divider(
        self, rf: RequestFactory, regular_user: User
    ) -> None:
        """The rule the switch sat above went with it.

        The specific failure this caught: the switch was lifted out of the
        account dropdown and the ``<div class="border-t">`` that had
        separated its section from the destinations below was left behind,
        so the open menu drew a line across its own top with nothing on one
        side of it. A divider separates two groups; one group is not two.

        Asserted as "the first thing in the menu is a link", which is the
        claim that actually holds — a later section added back above Trips
        would bring its own rule and should update this, not delete it.
        """
        html = _render_nav_for(rf, regular_user)
        menu = html.split('id="subscriber-menu"', 1)[1]
        first_element = re.search(r"<(?!/)([a-z]+)", menu.split(">", 1)[1])
        assert first_element is not None
        assert first_element.group(1) == "a"

    def test_switch_sits_first_among_the_menu_controls(
        self, rf: RequestFactory
    ) -> None:
        """Before the debug-log row and before the reconnect CTA.

        The menu's order is meaning, not decoration — the design system's
        consistency rule asks a new menu to adopt the order an existing one
        established, and the account menu's is: the row you open the menu
        to OPERATE first, then the destination, then the terminal action
        last. Asserted by position, because an entry inserted in the wrong
        group reads fine in a diff.
        """
        html = _render_nav_for(rf, AnonymousUser())
        panel = html.split('id="pwa-connection-panel"', 1)[1].split("</details>", 1)[0]
        assert panel.index("data-network-toggle") < panel.index(
            "data-network-reconnect"
        )

    def test_switch_sits_below_the_explanations(self, rf: RequestFactory) -> None:
        """What is true, a rule, then what you can do.

        The panel is two halves and the rule between them is load-bearing:
        a control mixed in among four mutually-exclusive explanatory
        paragraphs reads as belonging to whichever one happens to be
        visible.
        """
        html = _render_nav_for(rf, AnonymousUser())
        panel = html.split('id="pwa-connection-panel"', 1)[1].split("</details>", 1)[0]
        assert panel.index('data-role="forced-explainer"') < panel.index(
            "data-network-toggle"
        )

    def test_switch_row_renders_hidden(self, rf: RequestFactory) -> None:
        """It ships hidden, and the script reveals it.

        Hidden because ``pwa_offline.js`` reveals it: it drives a service
        worker, so a row that appeared without the script would be a dead
        control. Unlike the symbol above, which is hidden from nobody —
        the two have opposite contracts and this is where that is pinned.
        """
        html = _render_nav_for(rf, AnonymousUser())
        opening_tag = _opening_tag_around(html, "data-network-toggle")
        assert "hidden" in opening_tag

    def test_switch_is_a_real_checkbox_starting_unchecked(
        self, rf: RequestFactory
    ) -> None:
        """``includes/_switch.html``, not a ``role="menuitemcheckbox"`` button.

        A real ``<input type="checkbox" role="switch">`` gives keyboard
        activation, focus and checked-state bookkeeping for free, which the
        button shape had to reimplement. Unchecked because the mode a page
        boots in is ``'auto'`` — the script repaints it after reading the
        persisted mode back.
        """
        html = _render_nav_for(rf, AnonymousUser())
        input_tag = _opening_tag_around(html, 'id="nav-offline-mode"')
        assert 'type="checkbox"' in input_tag
        assert 'role="switch"' in input_tag
        # The bare HTML attribute, not the `peer-checked:` utilities the
        # track and thumb carry — hence the opening tag rather than the row.
        assert "checked" not in input_tag.replace('id="nav-offline-mode"', "")

    def test_switch_row_needs_no_menu_role_juggling(self, rf: RequestFactory) -> None:
        """No ``role="none"``, and no ``menuitemcheckbox`` either.

        The row carried ``role="none"`` for its whole life in the account
        dropdown, and for exactly one reason: a ``role="switch"`` checkbox
        is not a valid child of ``role="menu"``, which admits only
        menuitem / menuitemcheckbox / menuitemradio (plus group and none),
        so the wrapper had to be taken out of the accessibility tree to
        stop a bare <div> being announced as an unexpected menu child.

        The network menu claims no ARIA role at all — it is a disclosure
        holding a small form, which is what it has always actually been —
        so there is no invalid parent/child relationship to neutralise and
        the attribute would now be noise asserting a fix for a problem that
        is not here. SNOW-921 dropped it, and this is where that is
        recorded rather than looking like an omission.
        """
        opening_tag = _opening_tag_around(
            _render_nav_for(rf, AnonymousUser()), "data-network-toggle"
        )
        assert 'role="none"' not in opening_tag
        assert "menuitemcheckbox" not in opening_tag

    def test_switch_label_is_a_sibling_pointing_at_the_input(
        self, rf: RequestFactory
    ) -> None:
        """A ``<label for>`` beside the include, never wrapping it.

        ``_switch.html``'s own outer element is itself a ``<label>``, and
        labels must not nest; its track and thumb are both
        ``pointer-events-none``, so a wrapper that is not a label leaves
        only the text clickable — silently, with every server-side test
        still green. The label is server-rendered so it is translated:
        ``makemessages`` never scans ``static/js``.
        """
        html = _render_nav_for(rf, AnonymousUser())
        row = html.split("data-network-toggle", 1)[1].split("</div>", 1)[0]
        assert 'for="nav-offline-mode"' in row
        assert "Offline mode" in row

    def test_switch_row_is_words_and_switch_with_no_glyph(
        self, rf: RequestFactory
    ) -> None:
        """The row carries a label and a switch, and nothing else.

        It shipped with a struck-through wifi mark beside the label for one
        pass, which restated in a glyph what the words already say and put
        a second copy of the header symbol's offline mark two inches below
        it — in a menu whose every other row is text.
        """
        html = _render_nav_for(rf, AnonymousUser())
        row = html.split("data-network-toggle", 1)[1].split("</div>", 1)[0]
        assert "<svg" not in row


@pytest.mark.django_db
class TestNavDebugLogEntry:
    """The SNOW-921 "Debug log" row in the network menu.

    The on-device trace (SNOW-812) has been reachable only from a
    low-contrast pill in the bottom-left corner of every page — a mark you
    find by already knowing it is there. It answers "the app is not getting
    what I expect off the network", which is the question this menu exists
    to answer at every other level of detail, so the menu now carries the
    way in.

    Gated on the same ``debug_log`` waffle flag that decides whether the
    panel and the recorder reach the page at all, so the row can never
    point at a panel that is not there. Both halves of that gate are
    asserted below, because a row rendered unconditionally would be a
    control that silently does nothing for everyone outside GRP_DEBUG.
    """

    def test_absent_without_the_flag(
        self, rf: RequestFactory, regular_user: User
    ) -> None:
        """No flag, no row — not even for a signed-in user."""
        html = _render_nav_for(rf, regular_user)
        assert "data-network-debug-log" not in html

    def test_absent_for_anonymous_viewers(self, rf: RequestFactory) -> None:
        """The gate short-circuits on authentication before it reads waffle.

        ``apps.public.context_processors.debug_log_visible`` evaluates
        ``request.user.is_authenticated`` first, which is what keeps the
        homepage's anonymous path at its query baseline
        (``tests/public/test_debug_log_panel.py``). The row inherits that,
        and this pins it.
        """
        html = _render_nav_for(rf, AnonymousUser())
        assert "data-network-debug-log" not in html

    @override_flag("debug_log", active=True)
    def test_present_with_the_flag(
        self, rf: RequestFactory, regular_user: User
    ) -> None:
        """A GRP_DEBUG member gets the row, inside the network menu."""
        html = _render_nav_for(rf, regular_user)
        panel = html.split('id="pwa-connection-panel"', 1)[1].split("</details>", 1)[0]
        assert "data-network-debug-log" in panel
        assert "Debug log" in panel

    @override_flag("debug_log", active=True)
    def test_row_is_a_button_that_closes_the_menu(
        self, rf: RequestFactory, regular_user: User
    ) -> None:
        """A ``<button>`` carrying ``data-disclosure-close``.

        A button and not an ``<a>`` because the trace has no URL — it is a
        panel already on the page, opened by
        ``static/js/debug_log_panel.js``, which binds this control
        alongside its own handle.

        ``data-disclosure-close`` because the trace opens over the page and
        a menu left hanging above it would cover the first lines of the
        thing the user just asked to read. That is nav.html's shared
        mechanism (see
        ``TestNavConnectionPanel.test_close_control_uses_the_shared_disclosure_mechanism``),
        not a fourth dismissal of its own — and it is the reason that
        script's selector had to become ``querySelectorAll``: the "×" is
        first in document order, so the singular form bound it and nothing
        else.
        """
        html = _render_nav_for(rf, regular_user)
        tag = _opening_tag_around(html, "data-network-debug-log")
        assert tag.startswith("<button")
        assert 'type="button"' in tag
        assert "data-disclosure-close" in tag

    @override_flag("debug_log", active=True)
    def test_row_sits_between_the_switch_and_the_cta(
        self, rf: RequestFactory, regular_user: User
    ) -> None:
        """Operate, then go, then the terminal action — the account menu's order.

        The design system's consistency rule asks a new menu to adopt the
        order an existing one established and drop what does not apply.
        "Offline mode" is the row you open the menu to operate, the debug
        log is the destination, and the way back to the network is last.
        """
        html = _render_nav_for(rf, regular_user)
        panel = html.split('id="pwa-connection-panel"', 1)[1].split("</details>", 1)[0]
        assert (
            panel.index("data-network-toggle")
            < panel.index("data-network-debug-log")
            < panel.index("data-network-reconnect")
        )
