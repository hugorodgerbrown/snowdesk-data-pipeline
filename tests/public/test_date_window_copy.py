"""
tests/public/test_date_window_copy.py — no fixed-season copy is left on the
surfaces SNOW-1431 moved to data-driven date windows.

The map's timeline, the bulletin page's calendar, ``/resorts/`` and
``/help/`` used to name "the season" (1 November – 30 April or 31 May) in
their user-facing strings. Those windows now follow the data
(docs/decisions/date-windows-follow-the-data.md), so the copy says
"timeline" and "calendar" instead.

Two halves:
  - The touched templates' translatable strings (``trans`` / ``translate``
    literals and ``blocktrans`` bodies) carry no "season", apart from the
    kept resort and closure strings. Read from the template source, because
    a string behind a branch the test does not render would otherwise be
    missed.
  - The new strings render on the map, bulletin-calendar, ``/resorts/`` and
    ``/help/`` pages.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from freezegun import freeze_time

from tests.factories import (
    BulletinFactory,
    MicroRegionFactory,
    RegionDayRatingFactory,
)

REPO_ROOT = Path(settings.BASE_DIR)

# Every template whose copy SNOW-1431 rewrote.
TOUCHED_TEMPLATES: tuple[str, ...] = (
    "apps/map/templates/public/partials/_map_embed.html",
    "apps/map/templates/public/partials/_map_help_mode.html",
    "apps/map/templates/public/partials/_map_calendar.html",
    "apps/map/templates/public/partials/_season_scrubber_controls.html",
    "apps/map/templates/public/partials/_season_ribbon.html",
    "apps/design_system/templates/public/partials/_season_scrubber_demo.html",
    "apps/public/templates/public/partials/_season_sheet.html",
    "apps/public/templates/public/partials/_season_calendar.html",
    "templates/includes/nav.html",
    "apps/public/templates/public/resorts_index.html",
    "apps/public/templates/public/help.html",
    "apps/public/templates/public/help/_topic_calendar.html",
    "apps/public/templates/public/help/_topic_timeline.html",
    "apps/public/templates/public/help/_topic_overview.html",
)

# Strings that describe a resort's own season or a closure, not a
# bulletin window. They stay (SNOW-1431 scope, "Kept as they are").
KEPT: tuple[str, ...] = (
    "Closed by law, out of season",
    "Advised, out of season",
    "Season opens",
    "Season closes",
    "Typical season",
)

_TRANS_RE = re.compile(r"""{%\s*trans(?:late)?\s+(["'])(.*?)\1""", re.DOTALL)
_BLOCKTRANS_RE = re.compile(
    r"{%\s*blocktrans(?:late)?\b[^%]*%}(.*?){%\s*endblocktrans(?:late)?\s*%}",
    re.DOTALL,
)


def _strings(path: str) -> list[str]:
    """Return every translatable string literal in the template at ``path``."""
    source = (REPO_ROOT / path).read_text(encoding="utf-8")
    found = [m.group(2) for m in _TRANS_RE.finditer(source)]
    found += [" ".join(m.group(1).split()) for m in _BLOCKTRANS_RE.finditer(source)]
    return found


@pytest.mark.parametrize("path", TOUCHED_TEMPLATES)
def test_no_fixed_season_copy(path: str) -> None:
    """No user-facing string in a touched template names "the season"."""
    offending = [
        text
        for text in _strings(path)
        if "season" in text.lower() and not any(kept in text for kept in KEPT)
    ]
    assert offending == []


@pytest.mark.django_db
@freeze_time("2026-02-15")
def test_the_new_strings_render() -> None:
    """The map, bulletin, ``/resorts/`` and ``/help/`` pages carry the new copy."""
    region = MicroRegionFactory.create(region_id="CH-4115", name="Valais")
    RegionDayRatingFactory.create(
        region=region,
        date=datetime.date(2026, 2, 1),
        source_bulletin=BulletinFactory.create(),
    )
    client = Client()

    map_page = client.get(reverse("public:map")).content.decode()
    for text in (
        "aria-label='Timeline'",
        "Loading…",
        "Bulletins unavailable",
        "Play timelapse",
        "Stop timelapse",
        "Skip to start",
        "Skip to end",
        "Tap the date for a calendar, coloured by the selected region’s danger.",
        "Step through the days, or press play to watch the danger change.",
        'aria-label="Danger ribbon"',
    ):
        assert text in map_page, text

    bulletin_page = client.get(
        reverse(
            "public:bulletin_date",
            kwargs={"region_id": "ch-4115", "slug": "valais", "date_str": "2026-02-01"},
        )
    ).content.decode()
    assert 'aria-label="Show danger calendar"' in bulletin_page
    assert 'aria-label="Danger calendar"' in bulletin_page

    resorts = client.get(reverse("public:resorts")).content.decode()
    help_page = client.get(reverse("public:help")).content.decode()
    assert "The calendar" in help_page
    assert "The timeline" in help_page
    assert "The season calendar" not in help_page
    assert "The season timeline" not in help_page
    assert "The season usually opens in November" not in resorts
