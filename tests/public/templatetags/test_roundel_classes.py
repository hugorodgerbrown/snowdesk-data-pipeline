"""
tests/public/templatetags/test_roundel_classes.py — the glass roundel's one string.

``roundel_classes`` (apps/public/templatetags/components.py) is the shape of
every map control drawn as a frosted circle and of every action in a map
sheet's header. Hugo, 4 Nov 2026: the resort sheet's save star drew bare
while the region sheet's pin beside the same kind of title was a roundel —
two shapes for one act. The tag is the one source both now read.
"""

from __future__ import annotations

from apps.public.templatetags.components import roundel_classes


class TestRoundelClasses:
    """The string carries the shape and the shared hover, and appends extras."""

    def test_carries_the_glass_shape_and_the_hover(self) -> None:
        """The two classes every roundel needs, and nothing else."""
        assert roundel_classes().split() == ["map-glass-roundel", "hover-affordance"]

    def test_appends_a_controls_own_hook(self) -> None:
        """A control's state hook rides after the shared pair."""
        classes = roundel_classes(extra="map-region-pin-control").split()
        assert classes == [
            "map-glass-roundel",
            "hover-affordance",
            "map-region-pin-control",
        ]
