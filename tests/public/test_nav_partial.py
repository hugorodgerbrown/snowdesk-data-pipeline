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
  - The SNOW-748 offline surfaces, which are split the way a phone splits
    aeroplane mode: a header SYMBOL every viewer gets, and an "Offline
    mode" switch in the account menu that only a signed-in user gets.

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

    def test_every_entry_is_present(
        self, rf: RequestFactory, regular_user: User
    ) -> None:
        """Subscriptions, Settings and Sign out — each asserted by name.

        SNOW-803 removed Favourites, Routes and Observations: those are map
        sheets now, reached from the map's roundels. The survivors keep a
        positive assertion each, so one cannot vanish the way the two
        orphaned pages once shipped.
        """
        html = _render_nav_for(rf, regular_user)

        for url_name, label in (
            ("accounts:hub", "Subscriptions"),
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

        Subscriptions is what an account is for; Settings and Sign out are
        machinery and sit below a rule. Order is the ranking, so a reshuffle
        that puts Sign out mid-menu is a real regression.
        """
        html = _render_nav_for(rf, regular_user)

        positions = [
            html.index(f'href="{reverse(name)}"')
            for name in ("accounts:hub", "accounts:settings")
        ]
        assert positions == sorted(positions)
        assert positions[-1] < html.index(f'action="{reverse("accounts:sign_out")}"')

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
        assert "Connection status: using the network" in element
        assert "Connection status: offline" in element


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
        """The whole panel renders for a signed-out reader, switch or no switch.

        This is the asymmetry the feature turns on: the service worker
        latches offline for anybody, and an anonymous user has no account
        menu and therefore no "Offline mode" switch, so the panel's CTA is
        their ONLY exit from that state. A refactor that folded the panel in
        with the menu would take it away from exactly the people who cannot
        do without it.
        """
        html = _render_nav_for(rf, AnonymousUser())
        assert 'id="pwa-connection-panel"' in html
        assert "data-network-reconnect" in html
        assert "Try reconnecting" in html
        assert "Use the network again" in html
        assert "data-network-toggle" not in html

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
    """The SNOW-748 "Offline mode" switch, in the account menu.

    The settings half of the aeroplane-mode model: turning the mode ON is a
    device preference, so it sits in the menu rather than in the header.
    SNOW-742 built this control inside the offline banner, which
    ``static/js/pwa_offline.js`` revealed only when the connection had
    already failed — so the user it was built for ("I have signal now and am
    about to lose it") could never reach it.

    It sits FIRST in the menu, in its own section between the subscribed
    regions and "Subscriptions": everything below it is a destination you
    browse to, and this is the one row you open the menu to operate.

    Signed-in only, and these assertions pin both halves of that: the row is
    present for a signed-in user and absent for an anonymous one, who still
    gets the symbol above.
    """

    def test_anonymous_does_not_see_the_switch(self, rf: RequestFactory) -> None:
        """No switch for anonymous viewers — the menu it lives in is theirs."""
        html = _render_nav_for(rf, AnonymousUser())
        assert "data-network-toggle" not in html

    def test_authenticated_sees_the_switch(
        self, rf: RequestFactory, regular_user: User
    ) -> None:
        """Signed-in users get it, at the top of their menu."""
        html = _render_nav_for(rf, regular_user)
        assert "data-network-toggle" in html

    def test_switch_sits_above_every_destination(
        self, rf: RequestFactory, regular_user: User
    ) -> None:
        """It comes before Subscriptions, Settings and Sign out.

        The menu's order is meaning, not decoration (SNOW-705). Asserted by
        position rather than by eye, because a later entry inserted in the
        wrong group reads fine in a diff.
        """
        html = _render_nav_for(rf, regular_user)
        assert html.index("data-network-toggle") < html.index(reverse("accounts:hub"))
        assert html.index("data-network-toggle") < html.index(
            reverse("accounts:settings")
        )
        assert html.index("data-network-toggle") < html.index(
            reverse("accounts:sign_out")
        )

    def test_switch_row_renders_hidden(
        self, rf: RequestFactory, regular_user: User
    ) -> None:
        """It ships hidden, and the script reveals it.

        Hidden because ``pwa_offline.js`` reveals it: it drives a service
        worker, so a row that appeared without the script would be a dead
        control. Unlike the symbol above, which is hidden from nobody —
        the two have opposite contracts and this is where that is pinned.
        """
        html = _render_nav_for(rf, regular_user)
        opening_tag = _opening_tag_around(html, "data-network-toggle")
        assert "hidden" in opening_tag

    def test_switch_is_a_real_checkbox_starting_unchecked(
        self, rf: RequestFactory, regular_user: User
    ) -> None:
        """``includes/_switch.html``, not a ``role="menuitemcheckbox"`` button.

        A real ``<input type="checkbox" role="switch">`` gives keyboard
        activation, focus and checked-state bookkeeping for free, which the
        button shape had to reimplement. Unchecked because the mode a page
        boots in is ``'auto'`` — the script repaints it after reading the
        persisted mode back.
        """
        html = _render_nav_for(rf, regular_user)
        input_tag = _opening_tag_around(html, 'id="nav-offline-mode"')
        assert 'type="checkbox"' in input_tag
        assert 'role="switch"' in input_tag
        # The bare HTML attribute, not the `peer-checked:` utilities the
        # track and thumb carry — hence the opening tag rather than the row.
        assert "checked" not in input_tag.replace('id="nav-offline-mode"', "")

    def test_switch_row_is_removed_from_the_menu_role_model(
        self, rf: RequestFactory, regular_user: User
    ) -> None:
        """``role="none"`` on the wrapper.

        A ``role="switch"`` checkbox is not a valid child of ``role="menu"``,
        which admits only menuitem / menuitemcheckbox / menuitemradio (plus
        group and none). ``role="none"`` takes the wrapper out of the
        accessibility tree so a bare <div> is not announced as an unexpected
        menu child, and leaves the switch to announce itself as what it is.
        Keeping ``menuitemcheckbox`` would have meant re-implementing
        Space/Enter activation and ``aria-checked`` by hand — the trap
        ``includes/_switch.html``'s docstring documents.
        """
        opening_tag = _opening_tag_around(
            _render_nav_for(rf, regular_user), "data-network-toggle"
        )
        assert 'role="none"' in opening_tag
        assert "menuitemcheckbox" not in opening_tag

    def test_switch_label_is_a_sibling_pointing_at_the_input(
        self, rf: RequestFactory, regular_user: User
    ) -> None:
        """A ``<label for>`` beside the include, never wrapping it.

        ``_switch.html``'s own outer element is itself a ``<label>``, and
        labels must not nest; its track and thumb are both
        ``pointer-events-none``, so a wrapper that is not a label leaves
        only the text clickable — silently, with every server-side test
        still green. The label is server-rendered so it is translated:
        ``makemessages`` never scans ``static/js``.
        """
        html = _render_nav_for(rf, regular_user)
        row = html.split("data-network-toggle", 1)[1].split("</div>", 1)[0]
        assert 'for="nav-offline-mode"' in row
        assert "Offline mode" in row

    def test_switch_row_is_words_and_switch_with_no_glyph(
        self, rf: RequestFactory, regular_user: User
    ) -> None:
        """The row carries a label and a switch, and nothing else.

        It shipped with a struck-through wifi mark beside the label for one
        pass, which restated in a glyph what the words already say and put a
        second copy of the header symbol's offline mark two inches below it
        — in a menu whose every other row is text.
        """
        html = _render_nav_for(rf, regular_user)
        row = html.split("data-network-toggle", 1)[1].split("</div>", 1)[0]
        assert "<svg" not in row
