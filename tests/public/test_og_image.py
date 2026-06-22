"""
tests/public/test_og_image.py — Spec assertions for the OG / social share card.

SNOW-221 replaces the placeholder ``static/social/og-default.png`` with the
production card referenced by the ``og:image`` / ``twitter:image`` tags in
``public/templates/public/base.html``. These tests lock the binding acceptance
criteria against the committed binary so a future re-export (e.g. an
oversized or wrong-ratio PNG) fails CI rather than shipping silently.

The PNG header is parsed with the standard library (``struct``) — no image
dependency is needed for the four facts under test.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest
from django.conf import settings

# Standard 8-byte PNG signature (PNG spec §5.2).
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Colour-type codes that carry an alpha channel (PNG spec §11.2.2):
# 4 = greyscale+alpha, 6 = truecolour+alpha. An OG card must be opaque, so the
# committed file's colour type must not be one of these.
ALPHA_COLOUR_TYPES = {4, 6}

# og:image / summary_large_image spec.
EXPECTED_WIDTH = 1200
EXPECTED_HEIGHT = 630
MAX_BYTES = 300 * 1024


@pytest.fixture
def og_image_path() -> Path:
    """Absolute path to the committed OG card under the static source dir."""
    return Path(settings.STATICFILES_DIRS[0]) / "social" / "og-default.png"


def _read_ihdr(path: Path) -> tuple[bytes, int, int, int]:
    """Return (signature, width, height, colour_type) parsed from the PNG IHDR.

    The IHDR chunk is the first chunk after the 8-byte signature: a 4-byte
    length, the ``IHDR`` type, then width (4 bytes), height (4 bytes) and the
    bit-depth / colour-type bytes. We read the leading 26 bytes and unpack the
    fields the acceptance criteria depend on.
    """
    header = path.read_bytes()[:26]
    signature = header[:8]
    width, height = struct.unpack(">II", header[16:24])
    colour_type = header[25]
    return signature, width, height, colour_type


def _has_trns_chunk(path: Path) -> bool:
    """Return True if the PNG carries a ``tRNS`` (palette transparency) chunk.

    An indexed-colour (type 3) PNG has no alpha channel but can still be
    transparent by assigning transparency to palette entries via a ``tRNS``
    chunk — the colour-type byte alone does not detect this. We walk the chunk
    stream (4-byte big-endian length, 4-byte type, data, 4-byte CRC) from the
    end of the signature until ``IEND`` and report whether ``tRNS`` appears.
    """
    data = path.read_bytes()
    offset = len(PNG_SIGNATURE)
    while offset + 8 <= len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        chunk_type = data[offset + 4 : offset + 8]
        if chunk_type == b"tRNS":
            return True
        if chunk_type == b"IEND":
            break
        offset += 12 + length  # length + type + data + CRC
    return False


def test_og_image_exists_and_is_png(og_image_path: Path) -> None:
    """The card exists and begins with the canonical PNG signature."""
    assert og_image_path.is_file(), f"missing OG card at {og_image_path}"
    assert og_image_path.read_bytes()[:8] == PNG_SIGNATURE


def test_og_image_dimensions(og_image_path: Path) -> None:
    """The card is exactly 1200×630 (og:image / summary_large_image ratio)."""
    _, width, height, _ = _read_ihdr(og_image_path)
    assert (width, height) == (EXPECTED_WIDTH, EXPECTED_HEIGHT)


def test_og_image_is_opaque(og_image_path: Path) -> None:
    """The card is fully opaque — no alpha channel and no palette transparency.

    Rejects the alpha-bearing colour types (4/6) and, for the indexed-colour
    type 3 the card actually uses, the ``tRNS`` palette-transparency mechanism.
    """
    _, _, _, colour_type = _read_ihdr(og_image_path)
    assert colour_type not in ALPHA_COLOUR_TYPES
    assert not _has_trns_chunk(og_image_path)


def test_og_image_under_size_budget(og_image_path: Path) -> None:
    """The card stays under the 300 KB unfurl budget."""
    assert og_image_path.stat().st_size < MAX_BYTES
