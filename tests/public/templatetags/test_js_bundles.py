"""
tests/public/templatetags/test_js_bundles.py — the map page's script bundles
(SNOW-1412).

Three things are pinned here:

  * The tag. With ``JS_BUNDLES`` off it renders one ``<script defer>`` per
    list member, in list order; with it on, one tag for the built bundle.
  * The lists. Every member exists, none is listed twice.
  * The page. ``/map/`` under every condition that changes what it renders
    tags each list member exactly where the list says when bundles are off,
    and none of them individually when they are on — with the bundles sitting
    either side of ``base.html``'s end-of-body scripts, as the members did
    before they were bundled.

What ``bin/bundle-js`` writes is tested in tests/js/test_bundle_js.js.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.template import Context, Template, TemplateSyntaxError
from django.test import Client, override_settings
from django.urls import reverse
from pytest_django.fixtures import Settings
from waffle.testutils import override_flag

from apps.public.templatetags.js_bundles import bundle_members, parse_bundle_list
from tests.factories import UserFactory

LISTS_DIR = Path(settings.BASE_DIR) / "static" / "js" / "bundles"
BUNDLES = ("map_surfaces", "map")
TERRAIN_CLASS_URL = "https://tiles.example.test/terrain-class/v1/{z}/{x}/{y}.png"

# `<script defer src="/static/js/map.js">` — the path under /static/.
_SCRIPT_SRC_RE = re.compile(r'<script[^>]+src="/static/([^"]+)"')


@pytest.fixture(autouse=True)
def _fresh_lists() -> Iterator[None]:
    """Drop the per-process list cache around each test."""
    bundle_members.cache_clear()
    yield
    bundle_members.cache_clear()


def _render(name: str) -> str:
    """Render ``{% js_bundle name %}`` on its own."""
    return Template('{% load js_bundles %}{% js_bundle "' + name + '" %}').render(
        Context()
    )


class TestParseBundleList:
    """The list format: one filename per line, ``#`` notes, blanks ignored."""

    def test_reads_filenames_in_order_and_skips_notes(self) -> None:
        """Notes and blank lines are not members."""
        text = "# header\n\na.js\n  # indented note\nb.js  \n\n"
        assert parse_bundle_list(text) == ("a.js", "b.js")


class TestTag:
    """``{% js_bundle %}`` in each of its two modes."""

    @pytest.fixture
    def lists(self, tmp_path: Path) -> Iterator[Path]:
        """A BASE_DIR holding one list, ``demo``, of two members."""
        bundles = tmp_path / "static" / "js" / "bundles"
        bundles.mkdir(parents=True)
        (bundles / "demo.txt").write_text("# note\nfirst.js\nsecond.js\n")
        (bundles / "empty.txt").write_text("# nothing here\n")
        with override_settings(BASE_DIR=tmp_path):
            yield tmp_path

    def test_off_renders_each_member_in_order(
        self, lists: Path, settings: Settings
    ) -> None:
        """Development and tests load every source file on its own."""
        settings.JS_BUNDLES = False
        assert _SCRIPT_SRC_RE.findall(_render("demo")) == [
            "js/first.js",
            "js/second.js",
        ]

    def test_on_renders_the_built_bundle_alone(
        self, lists: Path, settings: Settings
    ) -> None:
        """Staging and production load the one file bin/bundle-js wrote."""
        settings.JS_BUNDLES = True
        html = _render("demo")
        assert _SCRIPT_SRC_RE.findall(html) == ["bundles/demo.bundle.js"]
        assert "defer" in html

    def test_an_unknown_bundle_fails_loudly(self, lists: Path) -> None:
        """A typo must not ship a page without its scripts."""
        with pytest.raises(TemplateSyntaxError, match="No JS bundle named"):
            _render("missing")

    def test_an_empty_list_fails_loudly(self, lists: Path) -> None:
        """A list of notes only is a mistake, not a bundle."""
        with pytest.raises(TemplateSyntaxError, match="names no scripts"):
            _render("empty")


class TestLists:
    """The two lists the map page ships."""

    @pytest.mark.parametrize("name", BUNDLES)
    def test_every_member_exists(self, name: str) -> None:
        """``bin/bundle-js`` and the dev tags both need the file."""
        for member in bundle_members(name):
            assert (Path(settings.BASE_DIR) / "static" / "js" / member).is_file(), (
                member
            )

    def test_no_member_is_listed_twice(self) -> None:
        """Browsers do not deduplicate scripts: a repeat runs the module twice."""
        members = [m for name in BUNDLES for m in bundle_members(name)]
        assert len(members) == len(set(members))

    def test_only_lists_the_page_uses_exist(self) -> None:
        """A stray list would be built and shipped for nothing."""
        assert sorted(p.stem for p in LISTS_DIR.glob("*.txt")) == sorted(BUNDLES)


def _map_page(user: User | None, query: str = "") -> list[str]:
    """Return the script paths ``/map/`` renders, every condition on."""
    client = Client()
    if user is not None:
        client.force_login(user)
    with (
        override_flag("map_modes", active=True),
        override_flag("route_drawing", active=True),
        override_settings(TERRAIN_CLASS_TILE_URL=TERRAIN_CLASS_URL),
    ):
        content = client.get(reverse("public:map") + query).content.decode()
    return _SCRIPT_SRC_RE.findall(content)


@pytest.fixture
def superuser() -> User:
    """The one reader every map condition renders for."""
    return UserFactory.create(is_superuser=True, is_staff=True)


@pytest.mark.django_db
class TestMapPageBundlesOff:
    """What development and tests render: the members, where the lists say."""

    @pytest.mark.parametrize("signed_in", [False, True])
    def test_members_render_in_list_order(
        self, signed_in: bool, superuser: User
    ) -> None:
        """The lists ARE the page's order; nothing else tags a member."""
        rendered = _map_page(superuser if signed_in else None)
        members = [f"js/{m}" for name in BUNDLES for m in bundle_members(name)]
        assert [path for path in rendered if path in set(members)] == members

    def test_each_list_is_one_contiguous_run(self, superuser: User) -> None:
        """So the bundle can replace it without moving anything across it."""
        rendered = _map_page(superuser)
        for name in BUNDLES:
            wanted = [f"js/{m}" for m in bundle_members(name)]
            start = rendered.index(wanted[0])
            assert rendered[start : start + len(wanted)] == wanted, name


@pytest.mark.django_db
class TestMapPageBundlesOn:
    """What staging and production render."""

    @pytest.fixture(autouse=True)
    def _bundles_on(self, settings: Settings) -> None:
        """Render as staging and production do."""
        settings.JS_BUNDLES = True

    @pytest.mark.parametrize("signed_in", [False, True])
    def test_no_member_is_tagged_on_its_own(
        self, signed_in: bool, superuser: User
    ) -> None:
        """Each member arrives inside its bundle, once."""
        rendered = _map_page(superuser if signed_in else None, "?layers=exploded")
        members = {f"js/{m}" for name in BUNDLES for m in bundle_members(name)}
        assert not members & set(rendered)
        for name in BUNDLES:
            assert rendered.count(f"bundles/{name}.bundle.js") == 1

    def test_the_bundles_keep_their_place_around_the_shell(
        self, superuser: User
    ) -> None:
        """The surfaces' scripts ran before base.html's end-of-body ones, the
        map's after. ``db.js`` and ``pwa_install.js`` stand for that run.
        """
        rendered = _map_page(superuser)
        surfaces = rendered.index("bundles/map_surfaces.bundle.js")
        main = rendered.index("bundles/map.bundle.js")
        assert rendered.index("js/error_reporting.js") < surfaces
        assert surfaces < rendered.index("js/db.js")
        assert rendered.index("js/pwa_install.js") < main

    @pytest.mark.parametrize(
        ("query", "conditional"),
        [
            ("?layers=exploded", "js/map_exploded.js"),
            ("?edit=resorts", "js/map_edit_resorts.js"),
            ("?edit=locations", "js/map_edit_locations.js"),
        ],
    )
    def test_conditional_scripts_follow_the_map_bundle(
        self, query: str, conditional: str, superuser: User
    ) -> None:
        """The scripts left behind a condition still run after map.js."""
        rendered = _map_page(superuser, query)
        assert rendered.index("bundles/map.bundle.js") < rendered.index(conditional)
