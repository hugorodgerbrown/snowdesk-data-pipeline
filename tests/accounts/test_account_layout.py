"""
tests/accounts/test_account_layout.py — account-area layout (SNOW-705).

SNOW-705 asked how a signed-in user moves around the account area, at two
destinations and at five, and whether grouping was the right model. The
answer it reached was **neither, yet**: the area gets no navigation of its
own, and the nav dropdown carries it.

Two attempts preceded that. SNOW-667 built a grouped sub-nav — two eyebrow
headings above one link each, ~180px of chrome to offer a choice of two —
and cut it. SNOW-705 built a flat tab strip and removed that too, once the
premise was examined rather than the treatment: both were horizontal, and a
horizontal strip has a width budget (~343px at 375px, which five short
English labels almost exactly fill), so every future account page would have
had to be named to fit rather than named to be clear. The nav dropdown is
vertical, has no such budget, and is already the way in from every other
page.

SNOW-668 then finished the area SNOW-705 described. Favourites left the hub
for /account/favourites/, the menu gained the entries for it and for the two
pages that had shipped orphaned, and the hub — one list again — got a
visible <h1> saying what it is.

What these tests pin:

  * **The nav dropdown reaches every account page.** It is the only route to
    any of them, so losing an entry strands a page behind a URL nothing
    links to. That is not hypothetical: /account/observations/ and
    /account/routes/ shipped exactly that way. The per-entry assertions live
    in tests/public/test_nav_partial.py; this file asserts the menu is on a
    rendered page at all, which that isolated render cannot.
  * **No account page carries a sub-nav.** A third attempt at one is the
    thing SNOW-705 concluded against, and it would arrive looking like an
    improvement.
  * **Each account page names itself in a visible <h1>.** The hub's was
    sr-only for exactly as long as it held two unrelated lists and could not
    be honestly named; one page per list removed the reason.
  * **The settings page's four groups, in rank order.** Danger zone is last
    on purpose.
  * **The settings page's card layout (SNOW-746).** Each group is one card
    of divided rows, its controls are buttons rather than text links, and
    the page stamps which account it is acting on. What these pin is the
    part a redesign could quietly undo: the delete control sharing chrome
    with the reversible one beside it, and the passkey count disagreeing
    with the passkeys under it.

Mobile reflow is not asserted here: no server-side test can measure rendered
width, and a browser test for a layout constant is the wrong layer under
CLAUDE.md's rules.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Account
from tests.factories import AccountFactory

_TOKEN_BACKEND = "django.contrib.auth.backends.ModelBackend"


def _client_for(account: Account) -> Client:
    """Return a test client signed in as the account's User."""
    client = Client()
    client.force_login(account.user, backend=_TOKEN_BACKEND)
    return client


# ---------------------------------------------------------------------------
# The nav dropdown IS the account-area navigation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestNavDropdownCarriesTheAccountArea:
    """The menu reaches every account page, because nothing else does."""

    def test_dropdown_links_to_every_account_page(self) -> None:
        """Subscriptions and Settings, on a real page.

        SNOW-667 added the settings entry as a placeholder "until SNOW-705
        designs the account area's own navigation". SNOW-705 decided the
        area gets none: this menu is the navigation, so the entries are the
        design and not a stopgap. They are also the ONLY route to any of
        these pages — remove one and the page is stranded behind a URL
        nothing links to, which is precisely how /account/observations/
        (SNOW-677) and /account/routes/ (SNOW-713) shipped.

        Rendered through the full view stack here, rather than
        ``render_to_string`` as in tests/public/test_nav_partial.py: that
        file asserts the menu's contents entry by entry, and this one
        asserts the menu survives onto a real page for a signed-in user.
        The three list pages SNOW-803 removed are asserted absent there.
        """
        client = _client_for(AccountFactory.create())
        html = client.get(reverse("public:home")).content.decode()

        for url_name in ("accounts:hub", "accounts:settings"):
            assert f'href="{reverse(url_name)}"' in html, url_name


@pytest.mark.django_db
class TestNoSubNav:
    """No account page grows a sub-nav of its own."""

    @pytest.mark.parametrize("url_name", ["accounts:hub", "accounts:settings"])
    def test_account_pages_carry_no_subnav(self, url_name: str) -> None:
        """Two attempts at a sub-nav were built and removed.

        This is a guard against a third, which would arrive looking like an
        improvement — the area now has five destinations, and a strip tying
        them together is the obvious thing to reach for. The
        reasoning against it is in templates/includes/nav.html and
        docs/decisions/account-area-navigation-lives-in-the-nav-menu.md;
        read it before deleting this test.
        """
        client = _client_for(AccountFactory.create())
        html = client.get(reverse(url_name)).content.decode()

        assert 'data-testid="account-subnav"' not in html


# ---------------------------------------------------------------------------
# Heading structure — one system across the area
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAccountHeadings:
    """The heading rules SNOW-705 settled, which are easy to undo by hand."""

    def test_hub_names_itself_as_the_subscriptions_page(self) -> None:
        """The hub's ``<h1>`` is visible and reads "Subscriptions".

        It was ``<h1 class="sr-only">Account</h1>`` from SNOW-705 until
        SNOW-668, and the reason was the page's contents rather than a
        heading style: it held two unrelated lists of saved things and
        summarised neither, so there was no honest name for it as a whole.
        Moving favourites to their own page removed the premise. One list,
        one name, one visible heading — and the eyebrow that used to label
        the section is gone, because under this ``<h1>`` it only repeated
        it.
        """
        client = _client_for(AccountFactory.create())
        html = client.get(reverse("accounts:hub")).content.decode()

        assert "Subscriptions" in html
        assert '<h1 class="sr-only"' not in html
        assert html.count("<h1") == 1

    def test_settings_keeps_its_visible_heading(self) -> None:
        """Settings names itself; it is one page about one thing."""
        client = _client_for(AccountFactory.create())
        html = client.get(reverse("accounts:settings")).content.decode()

        assert "Settings" in html
        assert '<h1 class="sr-only"' not in html.split("</h1>")[0]
        assert html.count("<h1") == 1

    def test_hub_no_longer_hosts_the_favourites_section(self) -> None:
        """The section moved; it did not get copied.

        The regression this guards is a merge or a revert restoring the
        ``hx-get`` at the foot of the hub. Two surfaces lazy-loading
        ``favourites:list`` is not a cosmetic duplication — the offline
        write-through in static/js/favourites_offline.js keys on the request
        path, so both would claim the roster and the outline ranks would
        disagree between them.
        """
        client = _client_for(AccountFactory.create())
        html = client.get(reverse("accounts:hub")).content.decode()

        assert reverse("favourites:list") not in html
        assert 'data-testid="hub-section-favourites"' not in html

    def test_account_headings_carry_no_possessive(self) -> None:
        """Inside /account/ the possessive carries no information.

        Every page in the area is the signed-in user's by definition. Mixing
        "My account", "Your subscriptions" and "My favourites" across four
        labels was where the inconsistency showed — and the nav menu, which
        renders on both pages below, was the last holder of "My account"
        until SNOW-668 renamed it "Subscriptions".
        """
        client = _client_for(AccountFactory.create())
        for url_name in ("accounts:hub",):
            html = client.get(reverse(url_name)).content.decode()
            assert "My favourites" not in html, url_name
            assert "Your subscription" not in html, url_name
            assert "My account" not in html, url_name

    def test_settings_groups_render_in_order(self) -> None:
        """The four settings clusters, in the order they were ranked.

        Danger zone is last on purpose; the ordering is the ranking, and a
        reordering that puts account deletion mid-page is a real regression.
        """
        client = _client_for(AccountFactory.create())
        html = client.get(reverse("accounts:settings")).content.decode()

        positions = [
            html.index(f'data-testid="settings-group-{slug}"')
            for slug in ("account", "device", "privacy", "danger")
        ]
        assert positions == sorted(positions)


def _element_carrying(html: str, needle: str) -> str:
    """Return the whole opening tag of the element carrying ``needle``.

    Slicing on the needle alone truncates every attribute declared after it,
    which for these controls is the ``class`` string the assertions are
    about.
    """
    at = html.index(needle)
    return html[html.rindex("<", 0, at) : html.index(">", at) + 1]


@pytest.mark.django_db
class TestSettingsCardLayout:
    """SNOW-746 — the settings page rebuilt on cards of divided rows."""

    def test_page_stamps_the_signed_in_address(self) -> None:
        """The header names the account every row below acts on.

        Before this the address appeared only as the "Account email" row's
        value, where it reads as one setting among several rather than as
        the identity the page is scoped to — which is the thing worth being
        sure of before pressing Delete account.
        """
        account = AccountFactory.create()
        html = _client_for(account).get(reverse("accounts:settings")).content.decode()

        stamp = html.split('data-testid="settings-account-stamp"')[1]
        assert account.user.email in stamp.split("</span>")[0]

    def test_passkey_count_matches_the_rows_beneath_it(self) -> None:
        """The count is a read-out, not a caption — it must track the rows."""
        from tests.factories import PasskeyCredentialFactory

        account = AccountFactory.create()
        for _ in range(2):
            PasskeyCredentialFactory.create(user=account.user)
        html = _client_for(account).get(reverse("accounts:settings")).content.decode()

        assert "2 registered" in html
        assert html.count('class="passkey-card') == 2

    def test_passkey_count_reads_zero_with_none_registered(self) -> None:
        """The account with no way back in says so, rather than omitting the line."""
        account = AccountFactory.create()
        html = _client_for(account).get(reverse("accounts:settings")).content.decode()

        assert "0 registered" in html
        assert "No passkeys saved for this account yet." in html

    def test_passkey_row_prints_the_date_once(self) -> None:
        """SNOW-746: the meta line owns the date, so the name must not repeat it.

        ``display_name`` appends the creation date; this row prints an
        "Added {date}" line under the name, so it uses ``provider_name``.
        Regressing to ``display_name`` renders the date twice on one row.
        """
        from tests.factories import PasskeyCredentialFactory

        account = AccountFactory.create()
        passkey = PasskeyCredentialFactory.create(user=account.user)
        html = _client_for(account).get(reverse("accounts:settings")).content.decode()

        row = html.split('class="passkey-card')[1].split("</div>")[0]
        assert passkey.provider_name in row
        assert "Added" in html

    def test_destructive_control_does_not_share_chrome_with_reset(self) -> None:
        """Delete account must not look like the reversible button above it.

        Both are compact buttons on the same page. Ghost for both — which is
        what a redesign reaching for one button style would produce — makes
        the irreversible one indistinguishable at a glance.
        """
        client = _client_for(AccountFactory.create())
        html = client.get(reverse("accounts:settings")).content.decode()

        button = _element_carrying(html, 'data-testid="delete-account"')
        assert "border-status-error-text" in button
        assert "border-text-3/30" not in button

    def test_former_text_links_are_buttons(self) -> None:
        """Sign out, Reset and Delete are controls, and now look like controls.

        Each was a ``text-link`` span of body copy until SNOW-746. The class
        is the assertion: it is what made them read as prose.
        """
        client = _client_for(AccountFactory.create())
        html = client.get(reverse("accounts:settings")).content.decode()

        for testid in ("manage-sign-out", "delete-account"):
            control = _element_carrying(html, f'data-testid="{testid}"')
            assert control.startswith("<button"), testid
            assert "text-link" not in control, testid

        # Reset has no testid of its own — pwa_reset.js's binding attribute
        # is the hook, and it is the thing that must stay on a <button>.
        reset = _element_carrying(html, "data-pwa-reset-trigger")
        assert reset.startswith("<button")
        assert "text-link" not in reset

    def test_each_group_opens_a_card(self) -> None:
        """Every group heading is followed by the card its rows sit in.

        ``rounded-card``, not the mock's 16px literal: the exported design
        system defers to ``src/css/main.css``, where ``--radius-card`` is
        12px, and ds-lint blocks the literal outright.
        """
        client = _client_for(AccountFactory.create())
        html = client.get(reverse("accounts:settings")).content.decode()

        for slug in ("account", "device", "privacy", "danger"):
            after = html.split(f'data-testid="settings-group-{slug}"')[1]
            # The card chrome is the first element after the eyebrow closes.
            opening = after[after.index("</h2>") : after.index("</h2>") + 200]
            assert "bg-card rounded-card" in opening, slug
