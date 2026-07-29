# _massifs.py — Shim that re-exports the canonical massifs module.
#
# The canonical implementation has moved to
# ``apps/bulletins/services/meteofrance_massifs.py`` so it can be imported by the
# Django admin archive-upload view without duplicating the lookup tables.
#
# This shim exists so the offline scripts in ``scripts/meteofrance-archive/``
# continue to work unchanged — they import ``from _massifs import …`` and do
# not need to know about Django's package layout.
#
# How it works:
#   1. Inserts the repository root into ``sys.path`` at position 0.
#   2. Re-exports every public name from the canonical module via star-import.
#
# The canonical module is pure-Python (no Django imports at module load) so
# this shim works in non-Django contexts (e.g. the offline backfill scripts
# and the test suite for those scripts).
"""Re-export the canonical massifs module so offline scripts keep working."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from apps.bulletins.services.meteofrance_massifs import *  # noqa: E402, F401, F403
from apps.bulletins.services.meteofrance_massifs import (
    _normalise_slug,  # noqa: E402, F401
)
