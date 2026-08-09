"""
tests/public/test_htmx_pages.py — Every page that emits hx-* must load htmx (SNOW-650).

``public/base.html`` does not load htmx globally; each page that needs it
self-loads the script in ``{% block extra_head %}``.  That is a per-page
decision nothing checked, and the resort page proved the failure mode: it
shipped with SNOW-509's weather retry and SNOW-504's favourite toggle both
carrying ``hx-post``, while ``window.htmx`` stayed ``undefined`` for the
life of the page.  Both controls were inert from the day they landed, past
a green pytest suite, because no Python test observed the *absence* of a
script tag.

This module is the structural guard for that whole class.  It renders every
public page, greps the response for htmx attributes, and fails any page that
carries one without also loading ``js/htmx.min.js``.  Unlike a hand-written
click test it needs no maintenance to cover a page added next year — the
same walk-every-URL shape as ``tests/public/test_page_meta.py``.
"""

from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Account
from tests.factories import (
    AccountFactory,
    MicroRegionFactory,
    ResortFactory,
    SubRegionFactory,
    SubscriptionFactory,
)

_TOKEN_BACKEND = "apps.accounts.backends.TokenBackend"

# The htmx bundle every page self-loads. Matching on the filename rather than
# the hashed static URL keeps this insensitive to ManifestStaticFilesStorage.
_HTMX_SCRIPT = "js/htmx.min.js"

# Any htmx-driven attribute. hx-headers alone is not enough to make a request,
# but a page carrying it and nothing else is still relying on htmx being there.
_HX_ATTRIBUTE_RE = re.compile(r"\shx-[a-z-]+\s*=")


def _hx_attributes(html: str) -> set[str]:
    """Return the distinct ``hx-*`` attribute names present in ``html``."""
    return {
        match.strip().rstrip("=").strip() for match in _HX_ATTRIBUTE_RE.findall(html)
    }


@pytest.fixture
def anonymous_pages(db: None) -> dict[str, str]:
    """URL of every public page reachable without a session."""
    subregion = SubRegionFactory.create()
    region = MicroRegionFactory.create(
        region_id="CH-4115", name="Valais", subregion=subregion
    )
    resort = ResortFactory.create(name="Verbier", region=region)
    return {
        "home": reverse("public:home"),
        "help": reverse("public:help"),
        "observations": reverse("public:observations"),
        "colophon": reverse("public:colophon"),
        "privacy": reverse("public:privacy"),
        "terms": reverse("public:terms"),
        "terms_of_service": reverse("public:terms_of_service"),
        "how_to_read_bulletin": reverse("public:how_to_read_bulletin"),
        "resort": resort.get_absolute_url(),
        "bulletin": region.get_absolute_url(),
        "sign_in": reverse("accounts:sign_in"),
        "register": reverse("accounts:register"),
        "reset_password": reverse("accounts:reset_password"),
    }


@pytest.fixture
def subscribed_account(db: None) -> Account:
    """An account with one subscribed region, for the manage page."""
    account = AccountFactory.create()
    region = MicroRegionFactory.create(region_id="CH-4115", name="Valais")
    SubscriptionFactory.create(account=account, region=region)
    return account


class TestHtmxIsLoadedWhereItIsUsed:
    """A page emitting hx-* attributes must also load the htmx script."""

    def test_every_anonymous_page(
        self, client: Client, anonymous_pages: dict[str, str]
    ) -> None:
        """No anonymous page ships hx-* attributes with no htmx to run them."""
        for label, url in anonymous_pages.items():
            html = client.get(url).content.decode()
            attributes = _hx_attributes(html)
            if not attributes:
                continue
            assert _HTMX_SCRIPT in html, (
                f"{label}: emits {sorted(attributes)} but never loads htmx — "
                f"the controls are inert. Add the script in "
                f"{{% block extra_head %}}, or drop the attributes."
            )

    def test_manage_page(self, subscribed_account: Account) -> None:
        """The signed-in manage page is htmx-driven end to end."""
        client = Client()
        client.force_login(subscribed_account.user, backend=_TOKEN_BACKEND)
        html = client.get(reverse("accounts:manage")).content.decode()
        assert _hx_attributes(html), "manage page no longer emits hx-* attributes"
        assert _HTMX_SCRIPT in html, "manage page emits hx-* but never loads htmx"


class TestResortPage:
    """The page the guard was written for (SNOW-509 → SNOW-650)."""

    def test_resort_page_emits_hx_attributes_and_loads_htmx(
        self, client: Client, anonymous_pages: dict[str, str]
    ) -> None:
        """Both the favourite toggle and the weather retry can actually fire.

        Asserting the attributes are present as well as the script stops the
        guard being silently satisfied by a future change that removes the
        htmx wiring instead of fixing it.
        """
        html = client.get(anonymous_pages["resort"]).content.decode()
        assert _hx_attributes(html), "resort page lost its hx-* attributes"
        assert _HTMX_SCRIPT in html, "resort page emits hx-* but never loads htmx"


class TestGuardItself:
    """The detector has to be able to fail, or it guards nothing."""

    def test_detects_hx_attributes(self) -> None:
        """A page carrying hx-post is recognised."""
        assert _hx_attributes('<button hx-post="/x/" hx-swap="outerHTML">') == {
            "hx-post",
            "hx-swap",
        }

    def test_ignores_unrelated_markup(self) -> None:
        """Plain markup — including lookalike words — yields nothing."""
        assert _hx_attributes('<a href="/hx-post/" class="hx-thing">go</a>') == set()
