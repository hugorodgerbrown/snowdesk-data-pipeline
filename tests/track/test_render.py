"""
tests/track/test_render.py — the render's fingerprint (SNOW-1316).

``render_fingerprint`` names everything ``build_track_render`` reads, and
``same_render`` compares two renders by it, so a writer can skip an UPDATE
that would write what is already there. The builder's output itself is
``tests/routes/test_track_render.py``'s subject.
"""

from __future__ import annotations

from decimal import Decimal

from track.render import (
    TRACK_RENDER_VERSION,
    build_track_render,
    render_fingerprint,
    same_render,
)

POINTS: list[list[float | None]] = [[7.0, 46.0, 2000.0], [7.01, 46.01, 2100.0]]


def test_the_same_inputs_fingerprint_the_same() -> None:
    """Deterministic, and a Decimal reads as its float."""
    one = render_fingerprint(POINTS, None, ascent_m=100.0, descent_m=0.0)
    two = render_fingerprint(
        [list(p) for p in POINTS], None, ascent_m=Decimal(100), descent_m=Decimal(0)
    )
    assert one == two
    assert len(one) == 64


def test_any_input_moves_the_fingerprint() -> None:
    """Points, samples and either figure each count."""
    base = render_fingerprint(POINTS, None, ascent_m=100.0, descent_m=None)
    assert base != render_fingerprint(POINTS[:1], None, ascent_m=100.0, descent_m=None)
    assert base != render_fingerprint(POINTS, {}, ascent_m=100.0, descent_m=None)
    assert base != render_fingerprint(POINTS, None, ascent_m=101.0, descent_m=None)
    assert base != render_fingerprint(POINTS, None, ascent_m=100.0, descent_m=0.0)


def test_a_render_carries_its_fingerprint() -> None:
    """The builder stamps what it was built from, and its version."""
    render = build_track_render(POINTS, None, ascent_m=None, descent_m=None)
    assert render["v"] == TRACK_RENDER_VERSION
    assert render["fp"] == render_fingerprint(
        POINTS, None, ascent_m=None, descent_m=None
    )


def test_same_render_compares_fingerprints() -> None:
    """Equal fp is the same; none stored, or none on the stored, is not."""
    built = build_track_render(POINTS, None, ascent_m=None, descent_m=None)
    assert same_render(dict(built), built)
    assert not same_render(None, built)
    assert not same_render({k: v for k, v in built.items() if k != "fp"}, built)
    assert not same_render({**built, "fp": "0" * 64}, built)
