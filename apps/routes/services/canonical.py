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

Recordings — a second corpus, with the opposite contract
--------------------------------------------------------

``apps/routes/fixtures/recordings/`` holds raw device exports: the bytes a
watch actually produces, before anything of ours has touched them. One
today, ``verbier-lift-served-day.gpx`` — a Suunto app export of a full
lift-served day, 7,217 points.

It is kept apart from the canonical four rather than added to them because
it breaks both of their pinned properties, and breaks them deliberately:

* **It is timed.** Every point carries a ``<time>``. That is the whole
  reason it is here — a recording gap is only visible in the interval
  between two points, and no canonical track has one to look at. In this
  file the device loses its fix for 403 seconds on a lift and the next two
  consecutive points sit 2,815 m apart and 643.6 m higher, with nothing in
  the GPX marking it (SNOW-991).
* **It is over ``MAX_POINTS``.** 7,217 points thin to 1,724, so its stored
  spacing is OURS. That is exactly the contamination
  ``test_is_stored_whole`` exists to keep out of the canonical corpus,
  whose spacing spread is an argument about recording devices. Putting
  this file in that set would quietly undermine the argument the set was
  committed to make, which is why ``canonical_paths`` does not return it.

Two removals from the file as exported, and no others: the per-point
``<extensions>`` subtree (heart rate) and the ``<author>`` block. Neither
is read by any parser or test here, and this repository is public. The
file says so in its own ``<desc>``. Coordinates, elevations and per-point
timestamps are untouched, so what it demonstrates about a recording gap is
what the watch recorded.

Not seeded. These are ingest fixtures, and ``seed_test_data`` puts every
canonical track into a trip and a leg baseline the suite pins — a timed,
simplified route in that set would move figures for reasons that have
nothing to do with what it is here to show.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ``apps/routes/fixtures/canonical/``. Resolved from this file rather than
# from settings so it is correct in a worktree, a container and a test run
# alike — the same way seed_test_data locates the eaws_CH fixture.
CANONICAL_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "canonical"

# ``apps/routes/fixtures/recordings/`` — raw device exports, resolved the
# same way and for the same reasons. Deliberately a separate directory: a
# caller asking for canonical tracks must not get a timed, simplified one
# by accident, and a glob over one directory is the cheapest guarantee of
# that there is.
RECORDING_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "recordings"


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


def recording_paths() -> list[Path]:
    """Return every committed raw device recording, in a stable order.

    Sorted by filename for the same reason ``canonical_paths`` is: a test
    that walks them should get the same order on every platform.

    Deliberately a separate function over a separate directory rather than
    a flag on ``canonical_paths`` — a recording is timed and simplified,
    and a caller that wanted the canonical corpus and silently got one of
    these would lose the very property it was reaching for.

    Returns:
        The paths, sorted. Empty only if the directory is missing, which
        means the fixtures were not checked out.

    """
    if not RECORDING_DIR.is_dir():
        logger.warning("Raw recording fixtures missing at %s", RECORDING_DIR)
        return []
    return sorted(RECORDING_DIR.glob("*.gpx"))


def recording_documents() -> list[tuple[str, bytes]]:
    """Return each raw recording as ``(filename, raw bytes)``.

    Bytes rather than a parsed result, on the same reasoning as
    ``canonical_documents``: the point of these files is what the parser
    makes of them, so handing over something already parsed would defeat
    the only thing they are for.

    Returns:
        One pair per file, in ``recording_paths`` order.

    """
    return [(path.name, path.read_bytes()) for path in recording_paths()]
