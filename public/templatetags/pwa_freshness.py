"""
public/templatetags/pwa_freshness.py — Template tag for the freshness state.

Bridges ``core.freshness.freshness_state()`` to templates so a
server-rendered page can bake the correct ``fresh`` / ``stale`` /
``unsafe`` state into its initial HTML — the first paint carries the
same verdict a client-side check would compute.

Usage:

    {% load pwa_freshness %}
    {% freshness_state bulletin.updated_at as state %}
    {% include "includes/_freshness_indicator.html"
       with state=state generated_at=bulletin.updated_at %}
"""

from __future__ import annotations

from datetime import datetime

from django import template

from core.freshness import (
    DEFAULT_MAX_AGE_SECONDS,
    DEFAULT_UNSAFE_AFTER_SECONDS,
    FreshnessState,
    freshness_state as _compute,
)

register = template.Library()


@register.simple_tag
def freshness_state(
    generated_at: datetime | None,
    max_age: int = DEFAULT_MAX_AGE_SECONDS,
    unsafe_after: int | None = DEFAULT_UNSAFE_AFTER_SECONDS,
) -> FreshnessState:
    """
    Return the freshness state for ``generated_at`` as a string.

    A ``None`` or naive datetime falls back to ``"unsafe"`` — refusing to
    show a bogus green tick is safer than misclassifying data we can't
    time.

    Args:
        generated_at: When the source data was produced.
        max_age: Seconds after which the state flips from ``"fresh"``
            to ``"stale"``.
        unsafe_after: Seconds after which the state flips from
            ``"stale"`` to ``"unsafe"``. Pass ``None`` for non-safety
            data.

    Returns:
        One of ``"fresh"``, ``"stale"``, ``"unsafe"``.

    """
    if generated_at is None or generated_at.tzinfo is None:
        return "unsafe"
    return _compute(generated_at, max_age=max_age, unsafe_after=unsafe_after)
