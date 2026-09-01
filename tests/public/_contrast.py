"""
tests/public/_contrast.py — WCAG contrast maths shared by the colour guards.

Extracted from ``test_eaws_contrast.py`` by SNOW-790, which needed the same
two functions for the meteogram's palette. Two copies of a luminance
formula is one copy too many: a guard that computes its own numbers
slightly differently from the guard next door is worse than no guard, and
the difference would not show up until a value sat right on a threshold.

Not a test module — the leading underscore keeps pytest from collecting it.
"""

from __future__ import annotations


def relative_luminance(hex_colour: str) -> float:
    """
    Return the WCAG relative luminance of a ``#rrggbb`` colour.

    Args:
        hex_colour: A six-digit hex colour, with or without the leading #.

    Returns:
        Relative luminance in the range 0.0–1.0.

    """
    raw = hex_colour.lstrip("#")
    channels = [int(raw[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [
        c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    ]
    red, green, blue = linear
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(foreground: str, background: str) -> float:
    """
    Return the WCAG contrast ratio between two ``#rrggbb`` colours.

    Args:
        foreground: Text or mark colour.
        background: Surface colour behind it.

    Returns:
        Contrast ratio from 1.0 (identical) to 21.0 (black on white).

    """
    lums = sorted((relative_luminance(foreground), relative_luminance(background)))
    return (lums[1] + 0.05) / (lums[0] + 0.05)
