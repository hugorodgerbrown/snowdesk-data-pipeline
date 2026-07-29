"""
tests/public/test_page_meta.py — The sharing-metadata standard (SNOW-553).

Nine pages shipped with generic, un-deduped link-unfurl cards because
omitting og:title / og:description / og:url looked exactly like deciding
not to set them. These tests remove that ambiguity: every page rendered
from ``public/base.html`` must be in one of two states, and a page in
neither fails.

  1. **Sharing.** The full page-identity set is present: og:title,
     og:description, og:url, twitter:title, twitter:description.
  2. **Opted out.** None of the og:/twitter: identity tags is present.
     og:url still is — that is the page's identity, not a card.

A newly added page inherits base.html's fallback page_meta block, which
emits an empty description and no title of its own, so it fails
``test_every_page_sets_a_title_and_description`` rather than shipping a
generic card.

The whitespace sweep is the regression test for the second defect in the
ticket: block bodies reflowed by ``djangofmt`` leaked literal newlines and
indentation into ``content="…"`` attributes, which reached the Google SERP
snippet.
"""

from __future__ import annotations

import datetime
import re

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse

from tests.factories import MicroRegionFactory, ResortFactory, SubRegionFactory

# Every meta/title tag this module reasons about.
IDENTITY_PROPERTIES = ("og:title", "og:description")
IDENTITY_NAMES = ("twitter:title", "twitter:description")

_META_CONTENT_RE = re.compile(
    r"<meta[^>]*\scontent=\"(?P<content>[^\"]*)\"[^>]*>", re.IGNORECASE
)
_TITLE_RE = re.compile(r"<title>(?P<title>.*?)</title>", re.IGNORECASE | re.DOTALL)


def _has_property(html: str, prop: str) -> bool:
    """Return True when ``html`` carries a ``<meta property="prop">`` tag."""
    return f'property="{prop}"' in html


def _has_name(html: str, name: str) -> bool:
    """Return True when ``html`` carries a ``<meta name="name">`` tag."""
    return f'name="{name}"' in html


def _meta_content(html: str, prop: str) -> str:
    """Return the ``content`` of the ``<meta property="prop">`` tag."""
    match = re.search(
        rf'<meta\s+property="{re.escape(prop)}"\s+content="([^"]*)"', html
    )
    assert match is not None, f"no {prop} tag in page"
    return match.group(1)


@pytest.fixture
def sharing_pages(db: None) -> dict[str, str]:
    """URLs of every page expected to emit a full sharing set."""
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
    }


@pytest.fixture
def opted_out_pages(db: None) -> dict[str, str]:
    """URLs of every page expected to opt out of the sharing set."""
    return {
        "sign_in": reverse("accounts:sign_in"),
        "register": reverse("accounts:register"),
        "reset_password": reverse("accounts:reset_password"),
        # A _status_page.html child, so this also covers the eleven
        # confirmation pages that share that parent.
        "unsubscribe_done": reverse("accounts:unsubscribe_done"),
    }


class TestSharingPages:
    """Pages that must carry a complete, self-consistent sharing set."""

    def test_every_page_sets_a_title_and_description(
        self, client: Client, sharing_pages: dict[str, str]
    ) -> None:
        """No page renders base.html's empty fallback metadata."""
        for label, url in sharing_pages.items():
            html = client.get(url).content.decode()
            title = _TITLE_RE.search(html)
            assert title is not None, f"{label}: no <title> tag"
            assert title.group("title").strip(), f"{label}: empty <title>"
            description = re.search(r'<meta name="description" content="([^"]*)"', html)
            assert description is not None, f"{label}: no description meta"
            assert description.group(1).strip(), f"{label}: empty description"

    def test_every_page_emits_the_full_identity_set(
        self, client: Client, sharing_pages: dict[str, str]
    ) -> None:
        """og:title/description and twitter:title/description are all present.

        This is the assertion that would have caught the original bug —
        nine of these ten pages emitted none of these four tags.
        """
        for label, url in sharing_pages.items():
            html = client.get(url).content.decode()
            for prop in IDENTITY_PROPERTIES:
                assert _has_property(html, prop), f"{label}: missing {prop}"
            for name in IDENTITY_NAMES:
                assert _has_name(html, name), f"{label}: missing {name}"

    def test_og_title_matches_twitter_title(
        self, client: Client, sharing_pages: dict[str, str]
    ) -> None:
        """The two card titles cannot drift — one emitter fills both."""
        for label, url in sharing_pages.items():
            html = client.get(url).content.decode()
            og = _meta_content(html, "og:title")
            twitter = re.search(r'<meta name="twitter:title" content="([^"]*)"', html)
            assert twitter is not None, f"{label}: no twitter:title"
            assert og == twitter.group(1), f"{label}: card titles disagree"

    def test_og_url_is_absolute_and_origin_keyed(
        self, client: Client, sharing_pages: dict[str, str]
    ) -> None:
        """og:url is built from SITE_BASE_URL, not the request's Host header."""
        base = settings.SITE_BASE_URL.rstrip("/")
        for label, url in sharing_pages.items():
            html = client.get(url).content.decode()
            og_url = _meta_content(html, "og:url")
            assert og_url.startswith(base), (
                f"{label}: og:url {og_url!r} not under {base}"
            )


class TestOptedOutPages:
    """Pages that deliberately emit no share card."""

    def test_no_card_identity_tags(
        self, client: Client, opted_out_pages: dict[str, str]
    ) -> None:
        """None of the four card-identity tags is present."""
        for label, url in opted_out_pages.items():
            html = client.get(url).content.decode()
            for prop in IDENTITY_PROPERTIES:
                assert not _has_property(html, prop), f"{label}: leaked {prop}"
            for name in IDENTITY_NAMES:
                assert not _has_name(html, name), f"{label}: leaked {name}"

    def test_title_and_description_still_render(
        self, client: Client, opted_out_pages: dict[str, str]
    ) -> None:
        """Opting out withholds the card, not the tab title or the SERP snippet."""
        for label, url in opted_out_pages.items():
            html = client.get(url).content.decode()
            title = _TITLE_RE.search(html)
            assert title is not None and title.group("title").strip(), (
                f"{label}: opting out should not drop the <title>"
            )

    def test_og_url_is_still_emitted(
        self, client: Client, opted_out_pages: dict[str, str]
    ) -> None:
        """og:url is page identity, not a sharing affordance — it stays."""
        for label, url in opted_out_pages.items():
            html = client.get(url).content.decode()
            assert _has_property(html, "og:url"), f"{label}: lost og:url"


class TestNoWhitespaceLeaks:
    """Every rendered meta content attribute is a clean single line.

    The bulletin and resort descriptions used to render with literal
    newlines and template indentation inside content="…", because
    djangofmt reflowed the block bodies that filled them. The |squish
    filter in the shared emitter is what prevents it; this is the test
    that keeps it prevented.
    """

    def test_no_newlines_or_double_spaces_in_meta_content(
        self,
        client: Client,
        sharing_pages: dict[str, str],
        opted_out_pages: dict[str, str],
    ) -> None:
        """No meta content attribute contains a newline or a run of two spaces."""
        for label, url in {**sharing_pages, **opted_out_pages}.items():
            html = client.get(url).content.decode()
            for match in _META_CONTENT_RE.finditer(html):
                content = match.group("content")
                assert "\n" not in content, f"{label}: newline in {content!r}"
                assert "  " not in content, f"{label}: double space in {content!r}"

    def test_title_tag_is_a_clean_single_line(
        self, client: Client, sharing_pages: dict[str, str]
    ) -> None:
        """The <title> is squished too — it shares the emitter."""
        for label, url in sharing_pages.items():
            html = client.get(url).content.decode()
            title = _TITLE_RE.search(html)
            assert title is not None
            assert "\n" not in title.group("title"), f"{label}: newline in <title>"
            assert "  " not in title.group("title"), f"{label}: double space in <title>"


class TestBulletinEmptyState:
    """A bulletin page for a date with no data keeps its identity."""

    def test_empty_state_still_emits_og_url(self, client: Client, db: None) -> None:
        """The danger_key guard used to strip og:url from empty-state pages.

        That was the worst case of the bug: the page most in need of
        being deduped against its populated twin was the one that lost
        the tag platforms dedupe on.
        """
        subregion = SubRegionFactory.create()
        region = MicroRegionFactory.create(
            region_id="CH-4116", name="Berner Oberland", subregion=subregion
        )
        url = region.get_absolute_url(datetime.date(2020, 1, 1))
        html = client.get(url).content.decode()
        assert _has_property(html, "og:url")
        assert _has_property(html, "og:title")
        assert _has_property(html, "og:description")

    def test_empty_state_og_url_is_the_canonical_url(
        self, client: Client, db: None
    ) -> None:
        """og:url and <link rel=canonical> agree, so neither can drift."""
        subregion = SubRegionFactory.create()
        region = MicroRegionFactory.create(
            region_id="CH-4117", name="Graubünden", subregion=subregion
        )
        url = region.get_absolute_url(datetime.date(2020, 1, 1))
        html = client.get(url).content.decode()
        og_url = _meta_content(html, "og:url")
        canonical = re.search(r'<link rel="canonical" href="([^"]*)"', html)
        assert canonical is not None, "no canonical link"
        assert og_url == canonical.group(1)


class TestOgType:
    """og:type is a page-level choice, not a site-wide constant (SNOW-555)."""

    def test_non_bulletin_pages_are_website(
        self, client: Client, sharing_pages: dict[str, str]
    ) -> None:
        """Every page except the bulletin keeps og:type=website."""
        for label, url in sharing_pages.items():
            if label == "bulletin":
                continue
            html = client.get(url).content.decode()
            assert _meta_content(html, "og:type") == "website", (
                f"{label}: wrong og:type"
            )

    def test_non_bulletin_pages_carry_no_article_properties(
        self, client: Client, sharing_pages: dict[str, str]
    ) -> None:
        """No page picks up orphaned article:* properties."""
        for label, url in sharing_pages.items():
            if label == "bulletin":
                continue
            html = client.get(url).content.decode()
            assert "article:published_time" not in html, (
                f"{label}: leaked published_time"
            )
            assert "article:modified_time" not in html, f"{label}: leaked modified_time"

    def test_og_type_is_emitted_exactly_once(
        self, client: Client, sharing_pages: dict[str, str]
    ) -> None:
        """Moving og:type out of base.html must not leave a duplicate behind."""
        for label, url in sharing_pages.items():
            html = client.get(url).content.decode()
            head = html.split("</head>")[0]
            assert head.count('property="og:type"') == 1, f"{label}: duplicate og:type"
