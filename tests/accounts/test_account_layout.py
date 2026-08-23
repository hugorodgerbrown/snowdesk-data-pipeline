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

What survives from the ticket is the layout work, which is what these tests
pin:

  * **The nav dropdown reaches both account pages.** It is now the only
    route to /account/settings/, so losing the entry would strand the page
    behind a URL nobody links to.
  * **Neither page carries a sub-nav.** A third attempt at one is the thing
    this ticket concluded against, and it would arrive looking like an
    improvement.
  * **The account pages carry an sr-only <h1> and no visible heading naming
    the page.** Nothing on the hub summarises anything, so there is no
    honest name for it; the menu named the destination on the way in.
  * **The hub's two sections are peers.** "Your subscriptions" was the <h1>
    inherited from the combined manage.html and "My favourites" an <h2>
    appended under it, so the markup claimed one contained the other and
    rendered it smaller to match.
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

    def test_dropdown_links_to_both_account_pages(self) -> None:
        """Hub and settings are both reachable from the nav menu.

        SNOW-667 added the settings entry as a placeholder "until SNOW-705
        designs the account area's own navigation". SNOW-705 decided the
        area gets none: this menu is the navigation, so the entry is the
        design and not a stopgap. It is also now the ONLY route to
        /account/settings/ — remove it and the page is stranded behind a URL
        nothing links to.
        """
        client = _client_for(AccountFactory.create())
        html = client.get(reverse("public:home")).content.decode()

        assert f'href="{reverse("accounts:hub")}"' in html
        assert f'href="{reverse("accounts:settings")}"' in html


@pytest.mark.django_db
class TestNoSubNav:
    """Neither account page grows a sub-nav of its own."""

    @pytest.mark.parametrize("url_name", ["accounts:hub", "accounts:settings"])
    def test_account_pages_carry_no_subnav(self, url_name: str) -> None:
        """Two attempts at a sub-nav were built and removed.

        This is a guard against a third, which would arrive looking like an
        improvement — the pages do have two destinations between them, and a
        strip tying them together is the obvious thing to reach for. The
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

    def test_hub_has_no_visible_page_heading(self) -> None:
        """The hub's ``<h1>`` is sr-only, because nothing names the page.

        It holds two lists of saved things and summarises neither, so there
        is no honest heading for it as a whole — and the nav dropdown named
        the destination on the way in. The outline still needs an ``<h1>``,
        so it stays and is hidden.
        """
        client = _client_for(AccountFactory.create())
        html = client.get(reverse("accounts:hub")).content.decode()

        assert '<h1 class="sr-only">Account</h1>' in html
        assert html.count("<h1") == 1

    def test_settings_keeps_its_visible_heading(self) -> None:
        """Settings names itself; it is one page about one thing."""
        client = _client_for(AccountFactory.create())
        html = client.get(reverse("accounts:settings")).content.decode()

        assert "Settings" in html
        assert '<h1 class="sr-only"' not in html.split("</h1>")[0]
        assert html.count("<h1") == 1

    def test_hub_sections_are_peers(self) -> None:
        """Subscriptions and Favourites render as sibling section labels.

        Before SNOW-705, "Your subscriptions" was the page ``<h1>`` at
        text-2xl and "My favourites" an ``<h2>`` at text-lg beneath it: the
        markup said one contained the other. They are two lists of saved
        things at the same rank.
        """
        client = _client_for(AccountFactory.create())
        html = client.get(reverse("accounts:hub")).content.decode()

        assert 'data-testid="hub-section-subscriptions"' in html
        assert 'data-testid="hub-section-favourites"' in html

    def test_hub_headings_carry_no_possessive(self) -> None:
        """Inside /account/ the possessive carries no information.

        Every page in the area is the signed-in user's by definition. Mixing
        "My account", "Your subscriptions" and "My favourites" across four
        labels was where the inconsistency showed.
        """
        client = _client_for(AccountFactory.create())
        html = client.get(reverse("accounts:hub")).content.decode()

        assert "My favourites" not in html
        assert "Your subscription" not in html

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
