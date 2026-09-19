"""
apps/routes/services/canonical.py — the committed corpus of real GPX tracks.

SNOW-989. Until this landed the project held exactly one route in any
environment a developer could reach: an eight-point synthetic track built
from a constant in ``seed_test_data``. That is enough to prove a row can be
written and nothing else. Every question worth asking about a route —
where its legs fall, how its parameters behave, whether a threshold tuned
on one track survives another — needs real tracks, and needs more than one.

``apps/routes/fixtures/canonical/`` holds them as ``.gpx`` files, read
through ``parse_gpx`` like any upload. Four today:

===========================  ======  ========  ===========  =============
File                         Points  Spacing   Legs         Shape
===========================  ======  ========  ===========  =============
chamonix-col-de-balme          1134     3.8 m  2            one up, one down
hidden-valley                   524    13.2 m  4            short final climb
mont-fort-col-de-la-chaux       894    17.9 m  7            eastward ratchet
mont-fort-backside              694    18.6 m  4            two big descents
===========================  ======  ========  ===========  =============

**THE SPACING SPREAD IS THE POINT, not the leg counts.** Those four tracks
put a point every 3.8 m to every 18.6 m — a factor of 4.7 — and the leg
detector's smoothing window is specified in POINTS. Ten points is 76 m of
ground on the Chamonix track and 372 m on the Backside one, so the same
setting smooths away a roll on one track that it preserves on another. A
corpus that was all one recording device would never show that, and the
fix it argues for (a window in metres) cannot be justified without it.

**These are reconstitutions, not the uploads.** Each was exported by
``apps.routes.services.gpx_export`` and carries that module's ``<desc>``
saying so: no per-point timestamps, and a ``source_point_count`` that reads
"not recorded" because all four rows predate SNOW-988. A canonical route is
therefore a track and its elevations, and nothing about when it was walked.

**They are real places and real days**, committed deliberately with the
owner's agreement. Coordinates are as exported — not trimmed at the ends,
not coarsened. Anything added here later is the same decision made again,
so it is made by a person rather than by this module.

Nothing here writes to the database. ``seed_test_data`` reads the bytes and
puts them through ``create_route``, which is the same path an upload takes
— so a seeded canonical route has derived fields the real parser produced,
and the seed exercises that parser against a live database.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ``apps/routes/fixtures/canonical/``. Resolved from this file rather than
# from settings so it is correct in a worktree, a container and a test run
# alike — the same way seed_test_data locates the eaws_CH fixture.
CANONICAL_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "canonical"


def canonical_paths() -> list[Path]:
    """Return every committed canonical ``.gpx``, in a stable order.

    Sorted by filename so a caller that seeds them, or a test that walks
    them, gets the same order on every platform — ``Path.glob`` does not
    promise one.

    Returns:
        The paths, sorted. Empty only if the directory is missing, which
        means the fixtures were not checked out.

    """
    if not CANONICAL_DIR.is_dir():
        logger.warning("Canonical route fixtures missing at %s", CANONICAL_DIR)
        return []
    return sorted(CANONICAL_DIR.glob("*.gpx"))


def canonical_documents() -> list[tuple[str, bytes]]:
    """Return each canonical track as ``(filename, raw bytes)``.

    Bytes rather than a parsed result: every caller so far wants to put
    them through ``parse_gpx`` or ``create_route`` itself, and handing over
    a parsed object would put a second ingest path in the codebase for the
    one corpus that exists to test the first.

    Returns:
        One pair per file, in ``canonical_paths`` order.

    """
    return [(path.name, path.read_bytes()) for path in canonical_paths()]
