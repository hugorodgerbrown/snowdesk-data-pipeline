"""
tests/sentinels/test_fidelity.py — the rendering half of the CAAML fidelity guard.

``bin/fidelity-lint`` asks whether every path in every sentinel is
classified. This asks the other question: for each path the table calls
``RENDERED``, does its value actually reach the page?

The two halves are split because they need different things. The
structural check is a JSON walk and belongs in the dependency-free lint
matrix, where it runs in a second on every PR. This one renders nine
bulletin pages through the full Django stack — view, render model,
template tags, partials — because a field can be present in a template
and still never reach a reader, and only rendering proves otherwise.

Why the page rather than the render model: a template refactor is the
change most likely to break fidelity silently, and it leaves the render
model untouched. Asserting on the render model would have passed
throughout the entire life of the ``unscheduled`` bug (SNOW-670).

See ``tests/sentinels/fidelity.py`` for the table and the probes, and
``tests/sentinels/README.md`` for the contract.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from django.test import Client

from apps.bulletins.models import Bulletin
from apps.bulletins.services.render_model import build_render_model
from tests.factories import BulletinFactory, MicroRegionFactory, RegionBulletinFactory
from tests.sentinels.fidelity import (
    Excluded,
    Rendered,
    RenderedPage,
    flatten,
    provider_of,
    resolve,
    visible_text,
)

SENTINELS_DIR = Path(__file__).resolve().parent

#: Sentinel directory name → the ``Bulletin.Source`` it was fetched from.
_SOURCES: dict[str, Bulletin.Source] = {
    "slf": Bulletin.Source.SLF,
    "albina": Bulletin.Source.ALBINA,
    "meteofrance": Bulletin.Source.METEOFRANCE,
}


def _all_source_json_paths() -> list[Path]:
    """Return all ``source.json`` files under the sentinels directory."""
    return sorted(SENTINELS_DIR.glob("*/*/source.json"))


def _sentinel_id(path: Path) -> str:
    """Return a pytest id like ``"slf/A-single-level"`` from a sentinel path."""
    return str(path.relative_to(SENTINELS_DIR).parent)


def _parse(timestamp: str) -> datetime:
    """Parse a CAAML ISO-8601 timestamp, tolerating a trailing ``Z``."""
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))


def render_sentinel(source_path: Path, client: Client) -> RenderedPage:
    """Render a sentinel's bulletin page and return it in probe-ready shapes.

    The bulletin is created with the factory's default
    ``render_model_version`` of 0, so the view rebuilds the render model
    from ``raw_data`` on the fly. That is deliberate: it means the page
    under test is built from the committed payload rather than from a
    render model snapshotted at some earlier version, which is the whole
    point of pointing the guard at the sentinels.

    The request is anonymous. The bulletin page carries superuser-only
    debug affordances (a raw-CAAML viewer among them), and rendering as
    staff would let a probe find its value in a surface no reader sees.

    Args:
        source_path: Path to a committed sentinel ``source.json``.
        client: The Django test client.

    Returns:
        The rendered page, its visible text, and the render model.

    """
    properties: dict[str, Any] = json.loads(source_path.read_text(encoding="utf-8"))
    sentinel_id = _sentinel_id(source_path)
    provider = sentinel_id.split("/")[0]

    # The region's *name* is deliberately not taken from the payload. The
    # page reads it from the regions table, so seeding the DB with
    # ``regions[].name`` would make any probe for that path circular —
    # it would match the value this harness had just injected. Only
    # ``regionID`` comes from the payload, because that is the join key
    # deciding which page the bulletin is served on.
    region_id = properties["regions"][0]["regionID"]
    region = MicroRegionFactory.create(
        region_id=region_id,
        name="Sentinel Region",
        slug=region_id.lower(),
    )
    valid_from = _parse(properties["validTime"]["startTime"])
    valid_to = _parse(properties["validTime"]["endTime"])
    bulletin = BulletinFactory.create(
        source=_SOURCES[provider],
        bulletin_id=properties["bulletinID"],
        lang=properties.get("lang") or "en",
        raw_data={"type": "Feature", "geometry": None, "properties": properties},
        issued_at=valid_from,
        valid_from=valid_from,
        valid_to=valid_to,
    )
    RegionBulletinFactory.create(
        bulletin=bulletin,
        region=region,
        region_name_at_time=region.name,
    )

    response = client.get(region.get_absolute_url(bulletin.target_date))
    assert response.status_code == 200, (
        f"{sentinel_id}: bulletin page returned {response.status_code}"
    )
    page_html = response.content.decode()
    # Built here rather than read back from ``bulletin.render_model``: the
    # view rebuilds a stale render model on the fly and does not write it
    # back, so the stored column is still the factory's stub. Calling the
    # same builder the view called is what makes the render model a probe
    # sees the one the page was actually built from.
    return RenderedPage(
        sentinel_id=sentinel_id,
        text=visible_text(page_html),
        html=page_html,
        render_model=build_render_model(properties),
    )


@pytest.mark.django_db
@pytest.mark.parametrize("source_path", _all_source_json_paths(), ids=_sentinel_id)
def test_every_rendered_path_reaches_the_page(
    source_path: Path, client: Client
) -> None:
    """Every path the table calls rendered is findable on the rendered page.

    This is the check that fails when a template refactor drops a field.
    A path this sentinel does not carry is skipped rather than failed —
    ``weatherReview`` is SLF-only, ``avalancheActivity`` is not — so each
    sentinel is judged on what it actually contains.

    Args:
        source_path: Path to a committed sentinel ``source.json``.
        client: The Django test client.

    """
    properties: dict[str, Any] = json.loads(source_path.read_text(encoding="utf-8"))
    page = render_sentinel(source_path, client)
    provider = provider_of(page.sentinel_id)

    failures: list[str] = []
    for path, values in flatten(properties).items():
        entry = resolve(path, provider)
        if not isinstance(entry, Rendered):
            continue
        if not entry.probe(values, page):
            failures.append(
                f"  {path}\n"
                f"    declared rendered on: {entry.surface}\n"
                f"    sentinel values:      {values[:3]!r}"
            )

    assert not failures, (
        f"{page.sentinel_id}: {len(failures)} CAAML path(s) are declared rendered "
        "but no representation was found on the page.\n\n"
        + "\n".join(failures)
        + "\n\nEither the surface stopped rendering the field — which is the "
        "regression this guard exists to catch — or the field moved and its "
        "probe in tests/sentinels/fidelity.py needs updating to match. If the "
        "field was removed on purpose, move its row to EXCLUDED with a reason."
    )


@pytest.mark.django_db
@pytest.mark.parametrize("source_path", _all_source_json_paths(), ids=_sentinel_id)
def test_no_excluded_path_is_silently_rendered(
    source_path: Path, client: Client
) -> None:
    """An excluded path's value does not turn up in the page's visible text.

    The weaker, cheaper direction of the same claim: an exclusion says a
    reader never sees this, and this catches the case where somebody
    starts rendering it without moving the row. Only long, distinctive
    string values are checked — a short one ("en", "2400", "DP1") would
    match somewhere by accident and the failure would be noise. Rows
    carrying ``duplicate_of`` are skipped: their value legitimately does
    appear, under the name of the path that renders it.

    Args:
        source_path: Path to a committed sentinel ``source.json``.
        client: The Django test client.

    """
    properties: dict[str, Any] = json.loads(source_path.read_text(encoding="utf-8"))
    page = render_sentinel(source_path, client)
    provider = provider_of(page.sentinel_id)

    leaked: list[str] = []
    for path, values in flatten(properties).items():
        entry = resolve(path, provider)
        if not isinstance(entry, Excluded) or entry.duplicate_of:
            continue
        for value in values:
            if not isinstance(value, str) or len(value) < 20:
                continue
            if value.casefold() in page.text:
                leaked.append(f"  {path} — {value[:60]!r}")

    assert not leaked, (
        f"{page.sentinel_id}: {len(leaked)} excluded CAAML path(s) now appear in "
        "the page's visible text.\n\n"
        + "\n".join(leaked)
        + "\n\nIf this is deliberate, move the row from EXCLUDED to RENDERED in "
        "tests/sentinels/fidelity.py and give it a probe."
    )


@pytest.mark.django_db
@pytest.mark.parametrize("source_path", _all_source_json_paths()[:1], ids=_sentinel_id)
def test_probes_cannot_see_the_embedded_raw_payload(
    source_path: Path, client: Client
) -> None:
    """The raw-CAAML debug block is not part of the text probes search.

    Under DEBUG the bulletin template embeds the whole raw payload in a
    ``<script type="application/json">`` block. If ``visible_text`` did
    not strip script bodies, every probe would find its value there and
    the guard would report perfect fidelity for a blank page. This is the
    load-bearing assumption of the whole check, so it is asserted rather
    than assumed.

    Args:
        source_path: Path to a committed sentinel ``source.json``.
        client: The Django test client.

    """
    properties: dict[str, Any] = json.loads(source_path.read_text(encoding="utf-8"))
    page = render_sentinel(source_path, client)

    # bulletinID is on the exclusion list precisely because no surface
    # shows it, so it is the cleanest canary: present in the payload,
    # absent from anything a reader sees.
    bulletin_id = properties["bulletinID"]
    assert bulletin_id.casefold() not in page.text, (
        "visible_text() is leaking script content — every probe would pass "
        "against the embedded raw payload rather than the rendered page."
    )
