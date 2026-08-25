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

    def test_dropdown_links_to_every_unflagged_account_page(self) -> None:
        """Subscriptions, Favourites, Observations and Settings, on a real page.

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
        Routes is omitted because it is flag-gated and off for this account;
        the flag's two branches are asserted in that file.
        """
        client = _client_for(AccountFactory.create())
        html = client.get(reverse("public:home")).content.decode()

        for url_name in (
            "accounts:hub",
            "accounts:favourites",
            "accounts:observations",
            "accounts:settings",
        ):
            assert f'href="{reverse(url_name)}"' in html, url_name


@pytest.mark.django_db
class TestNoSubNav:
    """No account page grows a sub-nav of its own."""

    @pytest.mark.parametrize(
        "url_name", ["accounts:hub", "accounts:favourites", "accounts:settings"]
    )
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

    def test_favourites_page_names_itself(self) -> None:
        """/account/favourites/ carries the same one-visible-``<h1>`` rule.

        The pair to the hub assertion above, and the reason it can be made:
        two lists that shared a page are two pages, each named for the one
        list it holds. The rest of that page's contract is pinned in
        tests/accounts/test_favourites_page.py.
        """
        client = _client_for(AccountFactory.create())
        html = client.get(reverse("accounts:favourites")).content.decode()

        assert "Favourites" in html
        assert '<h1 class="sr-only"' not in html
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
        for url_name in ("accounts:hub", "accounts:favourites"):
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
