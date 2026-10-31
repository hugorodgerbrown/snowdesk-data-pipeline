"""
apps/bulletins/services/render_model.py — The instance's side of the render model.

The render model is built by the weather service (SNOW-1270): the builder,
its version, the period transition and the day-character cascade live in
``apps/ingest/normalise/render_model.py`` and are imported here from
``apps.ingest.public``, so every existing caller keeps its import. What
stays here is presentation: ``compute_day_character`` pairs the service's
cascade key with the translated label and the hand-authored explainer
(``day_summary``), which follow the locale and the copy, not the provider.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING, Any

from django.utils.translation import gettext_lazy as _

from apps.bulletins.models import Bulletin
from apps.bulletins.services import day_summary
from apps.ingest.public import (
    DRY_PROBLEM_TYPES,
    KNOWN_PROBLEM_TYPES,
    PROBLEM_TYPE_TO_CATEGORY,
    RENDER_MODEL_VERSION,
    WET_PROBLEM_TYPES,
    PeriodTransition,
    RenderModelBuildError,
    band_id_for_problem,
    band_label_for_elevation,
    build_render_model,
    compute_period_transition,
    day_character_key,
    derive_problem_family,
    derived_of,
    detect_prose_spatial,
    detect_source as detect_provider,
    period_transition_of,
    problem_types_for,
)

if TYPE_CHECKING:
    # ``django_stubs_ext`` ships only with the typing toolchain; importing
    # it at runtime would force every test/CI env to install a typing-only
    # dependency. ``from __future__ import annotations`` (above) means the
    # ``StrOrPromise`` reference in DayCharacter resolves as a forward
    # string at runtime, so the import is genuinely free.
    from django_stubs_ext import StrOrPromise

logger = logging.getLogger(__name__)

__all__ = [
    "DRY_PROBLEM_TYPES",
    "KNOWN_PROBLEM_TYPES",
    "PROBLEM_TYPE_TO_CATEGORY",
    "RENDER_MODEL_VERSION",
    "WET_PROBLEM_TYPES",
    "DayCharacter",
    "PeriodTransition",
    "RenderModelBuildError",
    "band_id_for_problem",
    "band_label_for_elevation",
    "build_render_model",
    "compute_day_character",
    "compute_period_transition",
    "danger_levels_column",
    "derive_problem_family",
    "detect_prose_spatial",
    "detect_source",
    "problem_types_for",
]


def detect_source(properties: dict[str, Any]) -> Bulletin.Source:
    """Return the provider of a bulletin's CAAML as a ``Bulletin.Source``.

    The service answers with a plain provider code; the app's callers set it
    on a ``Bulletin`` and read ``.value`` / ``.label`` off it, so it is
    wrapped in the model's choice here.

    Args:
        properties: The bulletin's CAAML properties.

    Returns:
        The ``Bulletin.Source`` member.

    Raises:
        RenderModelBuildError: The ``customData`` names no known provider.

    """
    return Bulletin.Source(detect_provider(properties))


# ---------------------------------------------------------------------------
# Day character
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class DayCharacter:
    """
    Key, label and one-line explainer for the day-character callout.

    The label is one of the five canonical strings from the day-character
    cascade and answers "what kind of day is this". The explainer answers
    "why, and what does it mean" — it comes from the hand-authored matrix
    in :mod:`apps.bulletins.services.day_summary`, so it names the
    bulletin's own problems and says whether the day's danger moves. Both
    text fields may hold ``gettext_lazy`` proxies so the active locale
    resolves them at render time.

    ``key`` is the stable, locale-independent cascade key
    (``"stable"``, ``"manageable"``, ``"hard_to_read"``, ``"widespread"``,
    ``"dangerous"``) — the field consumers should branch and export on,
    never the localised ``label``.
    """

    key: str
    label: StrOrPromise
    explainer: StrOrPromise


# Canonical label per cascade key. The explainer is no longer stored here:
# it varies per bulletin and is built by ``day_summary.summary_for``.
_DAY_CHARACTER_LABELS: dict[str, StrOrPromise] = {
    "stable": _("Stable day"),
    "manageable": _("Manageable day"),
    "hard_to_read": _("Hard-to-read day"),
    "widespread": _("Widespread danger"),
    "dangerous": _("Dangerous conditions"),
}


def compute_day_character(render_model: dict[str, Any]) -> DayCharacter:
    """
    Classify a render model into a day character with a bulletin-specific summary.

    The **label** comes from the service's five-rule cascade, ``day_character_key``
    (see docs/day_character_rules_spec.md). The **explainer** comes from
    the hand-authored matrix in
    :mod:`apps.bulletins.services.day_summary`, keyed on how the day's
    danger moves and whether its named problems can be read in the field.

    Deriving the explainer needs three things off the render model:

    - the **movement** — whether the danger level holds all day, rises,
      falls, or holds while the problem underneath it changes. Taken from
      :func:`compute_period_transition` plus a comparison of the two
      windows' problem types, so a split that changes nothing for the
      reader is reported as a static day.
    - the **level** — the destination level on a changing day (what the
      day ends at), the peak level on a static one.
    - the **problems** — every problem type the day names, in editorial
      order. Both windows count on a changing day: the movement says what
      the afternoon does, the problem list says what is in play while it
      happens.

    When ``traits`` is empty (no avalanche problems reported), returns the
    ``"stable"`` entry with the quiet-day copy for its danger level.

    This function is pure — no side effects, no database access.

    Args:
        render_model: A render model dict as produced by
            :func:`build_render_model`.

    Returns:
        A :class:`DayCharacter` carrying the cascade key, the canonical
        label, and the one-line explainer for the callout on the bulletin
        page.

    """
    danger_info = render_model.get("danger") or {}
    danger = int(danger_info.get("number") or 1)
    traits: list[dict[str, Any]] = render_model.get("traits") or []

    # The service's cascade; empty traits are a quiet day.
    key = day_character_key(render_model)

    # Movement and the window the explainer describes. The transition is
    # the one the service stored with the model (SNOW-1341).
    transition = period_transition_of(derived_of(render_model))
    earlier_types = problem_types_for(traits, {"all_day", "earlier"})
    later_types = problem_types_for(traits, {"later"})
    movement = day_summary.classify_movement(
        transition.direction if transition is not None and transition.has_split else "",
        set(earlier_types),
        set(later_types),
    )

    if movement == "static" or transition is None:
        level = danger
        from_level = danger
        window_types = problem_types_for(traits, {"all_day", "earlier", "later"})
    else:
        # A changing day is described by where it ends up, from where it
        # started — both read off the transition rather than the peak, which
        # is only the higher of the two windows.
        level = int(transition.destination_number)
        from_level = int(transition.source_number or transition.destination_number)
        # Both windows, not just the later one: a problem that runs all
        # morning still sets the day's character, and dropping it here
        # would let the explainer contradict the label the cascade built
        # from every problem on the page.
        window_types = problem_types_for(traits, {"all_day", "earlier", "later"})

    return DayCharacter(
        key=key,
        label=_DAY_CHARACTER_LABELS[key],
        explainer=day_summary.summary_for(movement, level, window_types, from_level),
    )


# ---------------------------------------------------------------------------
# The danger-levels column (SNOW-1341)
# ---------------------------------------------------------------------------


def danger_levels_column(render_model: dict[str, Any] | None) -> str:
    """Return ``Bulletin.danger_levels`` for a render model.

    The levels come from the model's ``derived.danger_levels`` (computed
    when a model carries no ``derived``), ``|``-delimited with a leading and
    trailing ``|`` so a filter can match one whole key —
    ``danger_levels__contains="|high|"`` never matches ``very_high``.

    Args:
        render_model: The model being stored, or None.

    Returns:
        E.g. ``"|moderate|considerable|"``, or ``""`` when it names no level
        (a failed build's sentinel names none).

    """
    levels: list[str] = derived_of(render_model)["danger_levels"]["levels"]
    return f"|{'|'.join(levels)}|" if levels else ""
