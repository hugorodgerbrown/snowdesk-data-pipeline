"""
tests/map/test_map_help_mode.py — every map control has a help-mode entry (SNOW-1471).

Help mode (apps/map/templates/public/partials/_map_help_mode.html, SNOW-1119)
names each control on the map from a hand-written list, so a control added
later was silently left out: the compass, the Terrain switch and the 3D
switch all were. These tests render /map/ the two ways that between them
draw every control — signed out with every flag off, and as a superuser
with every flag in the manifest on — and fail when a control on the map
surface is named by no entry's ``data-control`` or ``data-also`` (its own id
or a wrapper's: the scrubber's buttons are named by ``#season-scrubber``), or
when an entry names an element that neither page draws.

The surface is the map's three control clusters and the header's own
controls. A menu or panel a control opens (the layers menu, the map key,
the calendar, the zoom flyout, the header menus) is the control's content,
not another control, and is skipped. Controls drawn by JavaScript after load
are out of reach of this test.
"""

from __future__ import annotations

import json
from contextlib import ExitStack
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from waffle.testutils import override_flag

from tests.factories import MicroRegionFactory, UserFactory

pytestmark = pytest.mark.django_db

# Where the map's controls live: the search pill, the right-hand roundels,
# the left-hand roundels with the date row and scrubber, and the header.
SURFACE_ROOTS = frozenset(
    {"map-utility-cluster", "map-controls-br", "map-legend", "nav-controls"}
)

# Panels a control opens. What is inside one is that control's content.
PANELS = frozenset(
    {
        "basemap-menu",
        "map-legend-card",
        "map-calendar",
        "map-zoom-flyout",
        "stale-day-notice",
        "search-results",
        "subscriber-menu",
        "admin-menu",
        "pwa-connection-panel",
    }
)

# Controls on the surface that are deliberately not named, and why.
EXEMPT = {
    "map-help-toggle": "the '?' itself: in help mode a tap on it leaves help mode",
}

VOID = frozenset(
    {
        "area",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }
)

FLAGS_MANIFEST = (
    Path(settings.BASE_DIR) / "apps" / "core" / "fixtures" / "waffle_flags.json"
)


class _Controls(HTMLParser):
    """Collect the map surface's controls and the help-mode entries."""

    def __init__(self) -> None:
        """Start with an empty stack and nothing found."""
        super().__init__()
        self.stack: list[tuple[str, str]] = []
        self.controls: list[tuple[str, frozenset[str]]] = []
        self.drawn: set[str] = set()
        self.unnamed: list[str] = []
        self.entries: list[str] = []
        self.also: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record a control on the surface, or a help-mode entry."""
        attr = {k: v or "" for k, v in attrs}
        ids = {i for _, i in self.stack}
        in_template = "template" in {t for t, _ in self.stack}
        if attr.get("id") and not in_template:
            self.drawn.add(attr["id"])
        if tag == "li" and "data-control" in attr:
            self.entries.append(attr["data-control"])
            self.also.extend(
                s.strip() for s in attr.get("data-also", "").split(",") if s.strip()
            )
        is_control = (
            tag in ("button", "summary")
            or (tag == "a" and "href" in attr)
            or attr.get("role") == "button"
        )
        if is_control and ids & SURFACE_ROOTS and not ids & PANELS and not in_template:
            if attr.get("id"):
                self.controls.append((attr["id"], frozenset(ids - {""})))
            else:
                self.unnamed.append(
                    f"<{tag} {attr.get('aria-label') or attr.get('class', '')}>"
                )
        if tag not in VOID:
            self.stack.append((tag, attr.get("id", "")))

    def handle_endtag(self, tag: str) -> None:
        """Pop back to the tag being closed."""
        if tag in VOID:
            return
        while self.stack:
            if self.stack.pop()[0] == tag:
                break


def _all_flags() -> list[str]:
    """Return every flag name in the manifest.

    Returns:
        The names.

    """
    return [f["name"] for f in json.loads(FLAGS_MANIFEST.read_text())["flags"]]


def _parse(client: Client, active: bool) -> _Controls:
    """Render /map/ with every flag on or off and parse it.

    Args:
        client: The client to render with.
        active: Whether every flag is on.

    Returns:
        The parsed page.

    """
    with ExitStack() as stack:
        for name in _all_flags():
            stack.enter_context(override_flag(name, active=active))
        response = client.get(reverse("public:map"))
    assert response.status_code == 200
    parsed = _Controls()
    parsed.feed(response.content.decode())
    return parsed


@pytest.fixture
def pages() -> list[_Controls]:
    """Render the two pages that between them draw every control.

    Returns:
        Signed out with every flag off, then a superuser with every flag on.

    """
    # The region chip and its pin and save roundels need CH-4115 to render.
    MicroRegionFactory.create(region_id="CH-4115")
    staff = Client()
    staff.force_login(UserFactory.create(is_staff=True, is_superuser=True))
    return [_parse(Client(), active=False), _parse(staff, active=True)]


def _named(page: _Controls) -> set[str]:
    """Return the ids the page's help-mode entries name.

    Args:
        page: A parsed page.

    Returns:
        Every id in a ``data-control`` or ``data-also``, without its ``#``.

    """
    selectors = page.entries + page.also
    assert all(s.startswith("#") for s in selectors), "help-mode selectors are ids"
    return {s[1:] for s in selectors}


def test_every_map_control_has_a_help_entry(pages: list[_Controls]) -> None:
    """Each control on the map surface is named in help mode, or exempt with a reason."""
    for page in pages:
        assert page.controls, "the surface roots were found"
        named = _named(page) | EXEMPT.keys()
        missing = sorted(
            i for i, wrappers in page.controls if not ({i} | wrappers) & named
        )
        assert missing == [], f"no help-mode entry for: {missing}"


def test_every_map_control_has_an_id(pages: list[_Controls]) -> None:
    """A control help mode cannot name by id would be missed."""
    for page in pages:
        assert page.unnamed == []


def test_every_help_entry_names_a_control_that_renders(pages: list[_Controls]) -> None:
    """No entry is left behind by a control that was removed or renamed."""
    drawn = {i for page in pages for i in page.drawn}
    stale = sorted(_named(pages[0]) - drawn)
    assert stale == [], f"help-mode entries for controls neither page draws: {stale}"
