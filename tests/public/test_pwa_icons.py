"""tests/public/test_pwa_icons.py — assertions on the checked-in PWA icon PNGs.

The four PNGs in each of ``static/icons/pwa/`` and
``static/icons/pwa-staging/`` are build artefacts of ``bin/build-pwa-icons``
(``npm run build:icons``), committed rather than generated at deploy time.
Nothing in the request path reads their pixels, so a regression in the build
script ships silently and only surfaces as a wrong-looking tile on somebody's
home screen — days later, on a device nobody is testing on. These tests read
the bytes off disk and assert the two properties that have actually broken:

1. **No border under the OS mask.** Android crops a maskable icon to a
   circle that reaches the midpoint of each edge; iOS crops the
   apple-touch icon to a squircle that clips its corners. Both flattened
   PNGs must therefore be backed by the tile's own colour. SNOW-151 fixed
   this for the production maskable (cream padding showed a border); the
   staging maskable, whose canvas was a shade darker than its rect fill,
   and the production apple-touch icon, flattened onto the cream manifest
   ``background_color``, were later fixed the same way.
2. **The season label is current.** Every icon carries the two-year
   season identifier ("25/26"). Because the PNGs are committed, they go
   stale the moment ``settings.SEASON_START_DATE`` rolls over to a new
   season. ``static/icons/pwa-build.json`` records what the committed
   PNGs were built with, so the roll-over fails a test instead of
   shipping last season's tile.

Pixel colours are read with Pillow rather than decoded by hand; the alpha
assertion reads the PNG IHDR byte directly, which needs no decoder at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.conf import settings
from PIL import Image

from apps.public.season_calendar import _season_label

# Tile colours, one per icon set — the fill of the rounded rect in the
# artwork itself. Mirrors TILE_COLOUR_PRODUCTION / TILE_COLOUR_STAGING in
# bin/build-pwa-icons; the production value is additionally asserted
# against static/favicon.svg below, so the three cannot drift apart
# silently.
TILE_COLOURS = {
    "pwa": (0x2D, 0x4A, 0x3E),
    "pwa-staging": (0xB4, 0x53, 0x09),
}

ICON_SETS = sorted(TILE_COLOURS)

# The two variants that are flattened onto an opaque backdrop. The
# purpose:any icons (192/512) keep their alpha deliberately — the browser
# composites those itself.
OPAQUE_ICONS = ["icon-maskable-512.png", "apple-touch-icon-180.png"]


def _icons_dir() -> Path:
    """Return the directory holding both committed PWA icon sets."""
    return Path(settings.BASE_DIR) / "static" / "icons"


def _icon_path(icon_set: str, name: str) -> Path:
    """Return the on-disk path of one committed icon PNG."""
    return _icons_dir() / icon_set / name


def _rgb(path: Path, x: int, y: int) -> tuple[int, int, int]:
    """Return the RGB triple at ``(x, y)`` of the PNG at ``path``."""
    with Image.open(path) as image:
        pixel = image.convert("RGB").getpixel((x, y))
    assert isinstance(pixel, tuple)
    return (pixel[0], pixel[1], pixel[2])


def _edge_samples(size: int) -> dict[str, tuple[int, int]]:
    """Return the four cardinal edge midpoints of a ``size``-square image.

    These are the outermost points an Android circular mask keeps — the
    circle inscribed in the square touches the square exactly here, so
    any padding colour that differs from the tile shows as a border ring
    precisely at these four points and nowhere else.
    """
    mid = size // 2
    return {
        "top": (mid, 0),
        "bottom": (mid, size - 1),
        "left": (0, mid),
        "right": (size - 1, mid),
    }


@pytest.mark.parametrize("icon_set", ICON_SETS)
def test_maskable_icon_has_no_border_at_cardinal_edges(icon_set: str) -> None:
    """The maskable icon's edge midpoints are the tile colour (SNOW-151).

    Android's adaptive-icon mask crops to a circle that touches each edge
    at its midpoint. The maskable variant pads the artwork into an 80%
    safe zone, so those four points fall on the canvas, not the artwork —
    and any canvas colour other than the tile's own reads as a border
    ring against the launcher background.
    """
    path = _icon_path(icon_set, "icon-maskable-512.png")
    assert path.exists(), f"maskable icon not found at {path}"
    expected = TILE_COLOURS[icon_set]
    for edge, (x, y) in _edge_samples(512).items():
        assert _rgb(path, x, y) == expected, (
            f"{icon_set}/icon-maskable-512.png has {_rgb(path, x, y)} at the "
            f"{edge} edge midpoint, expected the tile colour {expected}. "
            "Android's circular mask reaches this pixel, so a mismatch shows "
            "as a border ring. Rebuild with `npm run build:icons`."
        )


@pytest.mark.parametrize("icon_set", ICON_SETS)
def test_apple_touch_icon_has_no_fringe_at_corners(icon_set: str) -> None:
    """The apple-touch icon's corners are the tile colour.

    iOS rounds home-screen icons with a squircle whose curvature differs
    from the artwork's own ``rx``, so slivers of whatever the PNG was
    flattened onto survive at the corners. Flattening onto the tile
    colour makes those slivers invisible.
    """
    path = _icon_path(icon_set, "apple-touch-icon-180.png")
    assert path.exists(), f"apple-touch icon not found at {path}"
    expected = TILE_COLOURS[icon_set]
    corners = {
        "top-left": (0, 0),
        "top-right": (179, 0),
        "bottom-left": (0, 179),
        "bottom-right": (179, 179),
    }
    for corner, (x, y) in corners.items():
        assert _rgb(path, x, y) == expected, (
            f"{icon_set}/apple-touch-icon-180.png has {_rgb(path, x, y)} at the "
            f"{corner} corner, expected the tile colour {expected}. "
            "Rebuild with `npm run build:icons`."
        )


@pytest.mark.parametrize("icon_set", ICON_SETS)
@pytest.mark.parametrize("name", OPAQUE_ICONS)
def test_flattened_icons_have_no_alpha_channel(icon_set: str, name: str) -> None:
    """The flattened PWA icon PNGs are opaque RGB (SNOW-150).

    Android's adaptive-icon system applies a mask (circle, squircle, etc.)
    to the icon. If the PNG retains an alpha channel (PNG color_type 6,
    RGBA), the transparent pixels outside the rounded SVG corners leak
    through the mask as white or black depending on the device launcher,
    making the logo appear shrunken on a coloured background. iOS refuses
    to render transparency in home-screen icons cleanly for the same
    reason.

    This reads the IHDR color_type byte directly from the on-disk PNG
    (offset 25: 8-byte signature + 4-byte length + 4-byte type + 4-byte
    width + 4-byte height + 1-byte bit-depth = offset 25) and asserts it
    equals 2 (RGB). Any other value — including 0 (greyscale) or 6 (RGBA)
    — would indicate the build script is not producing a plain-RGB PNG.
    """
    path = _icon_path(icon_set, name)
    assert path.exists(), f"icon not found at {path}"
    colour_type = path.read_bytes()[25]
    assert colour_type == 2, (
        f"{icon_set}/{name} has color_type={colour_type}, expected 2 (RGB). "
        "It must be opaque RGB so the OS icon mask doesn't leak transparency "
        "at the masked corners. "
        "Regenerate via `npm run build:icons` after fixing bin/build-pwa-icons."
    )


def test_production_tile_colour_matches_the_favicon_rect_fill() -> None:
    """The production tile colour is the brand green in ``static/favicon.svg``.

    Both icon sets are rendered from that one SVG, so the colour the
    flattened PNGs are backed with has to be the colour the artwork's own
    rounded rect is filled with. Asserting it here means a brand-colour
    change that misses ``bin/build-pwa-icons`` fails a test rather than
    reintroducing the border this module exists to prevent.
    """
    svg = (Path(settings.BASE_DIR) / "static" / "favicon.svg").read_text()
    red, green, blue = TILE_COLOURS["pwa"]
    assert f'fill="#{red:02x}{green:02x}{blue:02x}"' in svg


def test_committed_icons_carry_the_configured_season_label() -> None:
    """The committed icons were built for ``settings.SEASON_START_DATE``.

    The season label ("25/26") is baked into the PNGs at build time and
    cannot be read back out of them, so ``bin/build-pwa-icons`` records
    it in ``static/icons/pwa-build.json`` instead. When the season rolls
    over, bumping ``SEASON_START_DATE`` without re-running
    ``npm run build:icons`` fails here — otherwise every installed home
    screen would keep advertising last season indefinitely.
    """
    build = json.loads((_icons_dir() / "pwa-build.json").read_text())
    expected = _season_label(settings.SEASON_START_DATE)
    assert build["season_label"] == expected, (
        f"committed PWA icons carry the season label {build['season_label']!r} "
        f"but SEASON_START_DATE ({settings.SEASON_START_DATE.isoformat()}) is "
        f"season {expected!r}. Run `npm run build:icons` and commit the "
        "regenerated PNGs and static/icons/pwa-build.json."
    )
