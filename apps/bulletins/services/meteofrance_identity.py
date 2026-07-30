# apps/bulletins/services/meteofrance_identity.py — The Météo-France bulletin
# identity scheme, shared by every path that creates or repairs an MF row.
#
# Météo-France issues more than one BRA per massif per covered day — typically a
# previous-evening issue and a morning refresh, occasionally an amendment. The
# original id, ``FR-{NN}-{covered date}``, could not tell them apart, so:
#
#   - the archive loader silently overwrote one issue with the other, the winner
#     decided by line order in the NDJSON file;
#   - the live fetcher silently skipped the second issue, never updating a
#     massif after the first fetch of the day.
#
# Neither counted a failure. Across the committed archive that affected 105
# massif-days, 56 of which differ in structured forecast data (SNOW-559).
#
# The fix is to put the publication instant in the id. Both ingest paths have a
# real one — the archive from each PDF's ``CreationDate`` metadata, the live
# path from the DPBRA ``@DATEDIFFUSION`` attribute — so neither has to fabricate
# a value or derive a sequence number from a filename.
#
# The two paths use the same grammar but are **separate identity spaces**: their
# timestamps are recorded at different moments (PDF render vs internal
# diffusion) and differ by up to a few minutes for the same bulletin, so an id
# from one path will not match the other. See
# docs/decisions/meteofrance-bulletin-identity.md.
"""The Météo-France ``bulletin_id`` grammar, shared across ingest paths."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# The compact UTC form used as the id's trailing component: YYYYMMDDHHMMSS.
_STAMP_FORMAT = "%Y%m%d%H%M%S"

# Matches an id already on the new grammar, so re-keying is idempotent:
# FR-02-2026-02-13-20260213074407
BULLETIN_ID_RE = re.compile(r"^FR-\d{2}-\d{4}-\d{2}-\d{2}-\d{14}$")


def compact_publication_stamp(value: object) -> str | None:
    """Return an ISO-8601 timestamp as the compact UTC form used in ids.

    Accepts either a string (any ISO-8601 form, ``Z``-suffixed or offset-aware)
    or a ``datetime``. Naive values are treated as UTC — every producer in this
    codebase writes UTC — and offset-aware values are converted.

    Args:
        value: An ISO-8601 string, a ``datetime``, or ``None``.

    Returns:
        A 14-character ``YYYYMMDDHHMMSS`` string in UTC, or ``None`` when the
        value is absent or unparseable.

    """
    if value is None or value == "":
        return None

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("Unparseable publication timestamp %r", value)
            return None
    else:
        logger.warning("Unexpected publication timestamp type %r", type(value))
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime(_STAMP_FORMAT)


def build_bulletin_id(
    region_id: str,
    covered_date: str,
    publication_time: object,
) -> str | None:
    """Return the canonical Météo-France ``bulletin_id``, or ``None``.

    Args:
        region_id: The canonical region identifier, e.g. ``"FR-02"``.
        covered_date: The ISO date the bulletin forecasts, e.g.
            ``"2026-02-13"``.
        publication_time: The publication instant, as accepted by
            :func:`compact_publication_stamp`.

    Returns:
        An id of the form ``FR-02-2026-02-13-20260213074407``, or ``None`` when
        the publication timestamp is missing or unparseable — in which case the
        caller must treat the record as a failure rather than emit an id that
        cannot distinguish the day's issues.

    """
    stamp = compact_publication_stamp(publication_time)
    if stamp is None:
        return None
    return f"{region_id}-{covered_date}-{stamp}"
