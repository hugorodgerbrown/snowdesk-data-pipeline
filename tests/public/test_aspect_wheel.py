"""
tests/public/test_aspect_wheel.py — the aspect wheel's host partial and its
registry entry (SNOW-1063).

The partial renders an empty host carrying the wheel's state as JSON, the
line slot under it and the strings template the renderer reads; the
registry at /_components/ shows the eight states at 48 px and one at
120 px, and loads the scripts that draw them. What the wheel draws is
tests/js/test_aspect_wheel_core.js's.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse

from apps.public._component_fixtures import ASPECT_WHEEL_VARIANTS
from tests.factories import UserFactory

_HOST_RE = re.compile(
    r'<span\s+data-aspect-wheel\s+data-size="(\d+)"\s+data-state="([^"]*)"'
)


@pytest.fixture()
def staff_client(db: Any) -> Client:
    """Return a logged-in staff client carrying the HX-Request header."""
    user: User = UserFactory.create()
    client = Client()
    client.force_login(user)
    client.defaults["HTTP_HX_REQUEST"] = "true"
    return client


def test_the_partial_renders_a_host_with_its_state() -> None:
    """The host carries the state as JSON and the size, with a line slot."""
    state = {
        "track": [7],
        "gradeDeg": -25,
        "prev": None,
        "next": None,
        "terrain": {"kind": "flat"},
    }
    body = render_to_string(
        "includes/_aspect_wheel.html", {"state_json": json.dumps(state), "size": 64}
    )
    match = _HOST_RE.search(body)
    assert match is not None
    assert match.group(1) == "64"
    assert json.loads(html.unescape(match.group(2))) == state
    assert "data-aspect-wheel-line" in body


def test_the_partial_carries_every_word_in_its_strings_template() -> None:
    """The compass points and the patterns render under the template id."""
    body = render_to_string("includes/_aspect_wheel.html", {"state_json": "{}"})
    assert '<template id="aspect-wheel-strings-template">' in body
    for sector in range(8):
        assert f'data-string="compass-{sector}"' in body
    for key in (
        "label",
        "label-faces",
        "label-flat",
        "label-unknown",
        "line",
        "line-heading",
    ):
        assert f'data-string="{key}"' in body


def test_the_size_defaults_to_48() -> None:
    """A host with no size draws at 48 px."""
    body = render_to_string("includes/_aspect_wheel.html", {"state_json": "{}"})
    assert 'data-size="48"' in body


def test_the_fixtures_are_eight_states_at_48_and_one_at_120() -> None:
    """Eight states at 48 px, then one at 120 px, each a valid state."""
    sizes = [v["context"]["size"] for v in ASPECT_WHEEL_VARIANTS]
    assert sizes == [48] * 8 + [120]
    kinds = [
        json.loads(v["context"]["state_json"])["terrain"]["kind"]
        for v in ASPECT_WHEEL_VARIANTS
    ]
    assert {"faces", "flat", "unknown"} <= set(kinds)


def test_the_registry_panel_renders_every_host(staff_client: Client) -> None:
    """The panel renders the nine hosts in each theme."""
    response = staff_client.get(
        reverse("public:components_panel", kwargs={"slug": "aspect-wheel"})
    )
    assert response.status_code == 200
    sizes = [m.group(1) for m in _HOST_RE.finditer(response.content.decode())]
    assert sizes.count("48") == 16
    assert sizes.count("120") == 2


def test_the_library_page_loads_the_wheel_scripts_in_order(
    staff_client: Client,
) -> None:
    """The class table, then the core, then the renderer."""
    client = Client()
    client.cookies = staff_client.cookies
    body = client.get(reverse("public:components_index")).content.decode()
    positions = [
        body.index("js/route_slope_core"),
        body.index("js/aspect_wheel_core"),
        body.index("js/aspect_wheel."),
    ]
    assert positions == sorted(positions)
