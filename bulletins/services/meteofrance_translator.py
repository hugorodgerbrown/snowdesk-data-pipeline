"""
bulletins/services/meteofrance_translator.py — DPBRA XML → CAAML JSON translator.

Converts a single Météo-France DPBRA XML document into the CAAML v6 JSON dict
shape that ``upsert_bulletin()`` in ``bulletins/services/data_fetcher.py``
consumes — identical in structure to the payloads produced by the SLF and
EUREGIO adapters.

The translator is **pure**: it performs no I/O, makes no database calls, and
has no side effects. Calling code (the fetcher) supplies XML bytes and
receives a dict back, or catches one of the two domain exceptions:

- ``MeteoFranceDelegatedRegionError`` — the massif delegates to another
  authority (e.g. Andorre / massif 71). The caller should skip silently
  without counting the massif as a failure.
- ``MeteoFranceTranslationError`` — a required field is missing, a danger
  level is out of range, or an unknown SAT code was encountered. The caller
  should log an error and increment ``PipelineRun.records_failed``.

Timezone handling: all ``DATE*`` attributes in DPBRA are naive local time in
Europe/Paris. They are localised on ingress and emitted as UTC ISO-8601 strings
ending in ``Z`` — the format ``_parse_dt()`` in ``data_fetcher.py`` expects.

Provider-specific DPBRA content that has no CAAML equivalent is stored under
``customData.MF``, mirroring ``customData.CH`` (SLF) and
``customData.ALBINA`` (EUREGIO).
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET  # noqa: S405 — see _safe_parse comment
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Timezone
# ---------------------------------------------------------------------------

_PARIS = ZoneInfo("Europe/Paris")

# ---------------------------------------------------------------------------
# Danger level lookup (EAWS tokens 1..5)
# ---------------------------------------------------------------------------

_DANGER_LEVEL: dict[int, str] = {
    1: "low",
    2: "moderate",
    3: "considerable",
    4: "high",
    5: "very_high",
}

# ---------------------------------------------------------------------------
# SAT → EAWS problem-type lookup (verified against MF avalanche guide 2025)
# ---------------------------------------------------------------------------

SAT_TO_EAWS: dict[int, str] = {
    1: "new_snow",
    2: "wind_slab",
    3: "persistent_weak_layers",
    4: "wet_snow",
    5: "gliding_snow",
    6: "no_distinct_avalanche_problem",
}

# ---------------------------------------------------------------------------
# Aspect mapping (DPBRA 8-point rose, CAAML compass tokens)
# ---------------------------------------------------------------------------

_ASPECTS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")

# ---------------------------------------------------------------------------
# Per-problem elevation prose patterns
# ---------------------------------------------------------------------------

_ABOVE = re.compile(r"[Aa]u-dessus de (\d{3,4})\s?m")
_BELOW = re.compile(r"[Ee]n dessous de (\d{3,4})\s?m")
_BETWEEN = re.compile(r"[Ee]ntre (\d{3,4})\s?(?:et|à)\s?(\d{3,4})\s?m")


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class MeteoFranceTranslationError(ValueError):
    """Raised when a DPBRA XML document cannot be translated.

    Covers missing required attributes, out-of-range danger levels, and
    unknown SAT codes. The orchestrator catches this per-bulletin, logs it
    as an error, and increments ``PipelineRun.records_failed``.
    """


class MeteoFranceDelegatedRegionError(Exception):
    """Raised when the DPBRA response is a delegation redirect, not a bulletin.

    Detected by root-element tag check: anything other than
    ``BULLETINS_NEIGE_AVALANCHE`` is treated as a delegation. The orchestrator
    catches this, logs it at INFO level, and does **not** increment
    ``records_failed`` — the delegation is the expected MF API behaviour for
    certain massif IDs (e.g. massif 71 → Andorre).
    """


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_parse(xml_bytes: bytes) -> ET.Element:
    """
    Parse DPBRA XML bytes and return the root element.

    Note on S314 (xml.etree.ElementTree known vulnerable): DPBRA XML is
    fetched from Météo-France's own authenticated API and from a local mirror
    directory we control. It is not user-supplied input, so the xml.etree
    attack surface (quadratic blowup, XXE) does not apply here. defusedxml is
    not listed in ``pyproject.toml`` and adding it for first-party trusted
    content would be premature. The ``S314`` rule is suppressed at the import
    via ``# noqa: S405``; individual calls are clean.

    Args:
        xml_bytes: Raw XML bytes from the DPBRA API or local mirror.

    Returns:
        The root ``ET.Element``.

    Raises:
        MeteoFranceTranslationError: The bytes cannot be parsed as XML.

    """
    try:
        return ET.fromstring(xml_bytes)  # noqa: S314
    except ET.ParseError as exc:
        raise MeteoFranceTranslationError(f"XML parse error: {exc}") from exc


def _parse_local_to_utc(value: str) -> str:
    """
    Convert a naive Europe/Paris datetime string to a UTC ISO-8601 string.

    The DPBRA API returns all ``DATE*`` attributes as naive local-time strings
    (e.g. ``"2026-05-17T16:00:00"``). This helper localises them to
    Europe/Paris and converts to UTC, emitting a ``Z``-suffixed string that
    matches the format produced by the SLF and EUREGIO adapters.

    Args:
        value: A naive ISO-8601 datetime string in Europe/Paris local time.

    Returns:
        A UTC ISO-8601 string ending in ``Z``.

    Raises:
        MeteoFranceTranslationError: The value cannot be parsed as an
            ISO-8601 datetime.

    """
    try:
        dt = datetime.fromisoformat(value).replace(tzinfo=_PARIS).astimezone(UTC)
    except (ValueError, TypeError) as exc:
        raise MeteoFranceTranslationError(
            f"Cannot parse datetime string {value!r}: {exc}"
        ) from exc
    return dt.isoformat().replace("+00:00", "Z")


def _parse_local_date(value: str) -> date:
    """
    Parse a naive Europe/Paris datetime string and return the local date part.

    Used for the ``bulletinID`` validity-date component, which should reflect
    the Paris-local calendar day, not the UTC calendar day.

    Args:
        value: A naive ISO-8601 datetime string in Europe/Paris local time.

    Returns:
        The Europe/Paris local date.

    Raises:
        MeteoFranceTranslationError: The value cannot be parsed.

    """
    try:
        return datetime.fromisoformat(value).replace(tzinfo=_PARIS).date()
    except (ValueError, TypeError) as exc:
        raise MeteoFranceTranslationError(
            f"Cannot parse date string {value!r}: {exc}"
        ) from exc


def _parse_plus_one_day(value: str) -> str:
    """
    Parse a naive Europe/Paris datetime string and add one day.

    Used when constructing the tendency ``validTime.endTime``: DPBRA provides
    a start date for the J+2 outlook but no explicit end; we project forward
    by one calendar day (matching CAAML convention for 24-hour windows).

    Args:
        value: A naive ISO-8601 datetime string in Europe/Paris local time.

    Returns:
        A UTC ISO-8601 string ending in ``Z``, one day after the input.

    Raises:
        MeteoFranceTranslationError: The value cannot be parsed.

    """
    try:
        dt = (
            datetime.fromisoformat(value).replace(tzinfo=_PARIS) + timedelta(days=1)
        ).astimezone(UTC)
    except (ValueError, TypeError) as exc:
        raise MeteoFranceTranslationError(
            f"Cannot parse datetime string {value!r}: {exc}"
        ) from exc
    return dt.isoformat().replace("+00:00", "Z")


def _aspects_from_pente(pente: ET.Element) -> list[str]:
    """
    Extract the avalanche problem aspects from a ``<PENTE>`` element.

    DPBRA encodes the 8-point compass rose as boolean attributes on the
    ``<PENTE>`` element (``NE="true"``, ``E="false"``, etc.). The same
    rose applies to all avalanche problems in a bulletin — DPBRA does not
    distinguish per-problem aspects.

    Args:
        pente: The ``<PENTE>`` XML element.

    Returns:
        A list of CAAML aspect tokens (e.g. ``["N", "NE", "W"]``).

    """
    return [a for a in _ASPECTS if pente.attrib.get(a) == "true"]


def _elevation_from_prose(
    text: str | None, bulletin_split: int | None
) -> dict[str, str] | None:
    """
    Derive per-problem elevation bounds from prose text or bulletin-wide split.

    Attempts to match common French altitude-range patterns in the problem
    text. Falls back to the bulletin-wide ``ALTITUDE`` split when no pattern
    matches. Returns ``None`` only when both the prose match and the
    bulletin-wide split are unavailable.

    Pattern priority: ``_BETWEEN`` → ``_ABOVE`` → ``_BELOW`` →
    bulletin-wide split (lowerBound).

    Args:
        text: The problem's ``<TEXTE>`` body (may be ``None``).
        bulletin_split: Altitude in metres from ``RISQUE/@ALTITUDE``
            (``None`` for single-band bulletins).

    Returns:
        A CAAML elevation dict such as ``{"upperBound": "2400"}``,
        ``{"lowerBound": "2000"}``, or ``{"lowerBound": "1800",
        "upperBound": "2400"}``. Returns ``None`` when no elevation data
        is available.

    """
    if text:
        m = _BETWEEN.search(text)
        if m:
            return {"lowerBound": m.group(1), "upperBound": m.group(2)}
        m = _ABOVE.search(text)
        if m:
            return {"lowerBound": m.group(1)}
        m = _BELOW.search(text)
        if m:
            return {"upperBound": m.group(1)}

    if bulletin_split is not None:
        # Use the bulletin-wide elevation split as the fallback.
        return {"lowerBound": str(bulletin_split)}

    return None


def _evolution_from_levels(today: int, tomorrow: int) -> str:
    """
    Derive a CAAML tendency type by comparing today's and tomorrow's danger.

    Args:
        today: Today's maximum danger level (integer 1..5).
        tomorrow: Tomorrow's maximum danger level (integer 1..5).

    Returns:
        ``"increasing"``, ``"steady"``, or ``"decreasing"``.

    """
    if tomorrow > today:
        return "increasing"
    if tomorrow < today:
        return "decreasing"
    return "steady"


def _require_attrib(element: ET.Element, name: str) -> str:
    """
    Return a required attribute value or raise ``MeteoFranceTranslationError``.

    Args:
        element: The XML element to inspect.
        name: The attribute name.

    Returns:
        The attribute value (a non-empty string).

    Raises:
        MeteoFranceTranslationError: The attribute is absent or empty.

    """
    value = element.attrib.get(name, "").strip()
    if not value:
        raise MeteoFranceTranslationError(
            f"Required attribute {name!r} is missing or empty on "
            f"<{element.tag}> element."
        )
    return value


def _elem_text(element: ET.Element | None) -> str:
    """
    Return the stripped text content of an element, or empty string.

    A convenience wrapper that eliminates repetitive ``(el.text or
    "").strip() if el is not None else ""`` patterns in the main body.

    Args:
        element: An XML element, or ``None``.

    Returns:
        The stripped text, or ``""`` when the element is absent or has no
        text content.

    """
    if element is None:
        return ""
    return (element.text or "").strip()


# ---------------------------------------------------------------------------
# Sub-translators (called from parse_dpbra_xml to reduce complexity)
# ---------------------------------------------------------------------------


def _parse_danger_ratings(
    risque: ET.Element,
    massif_id: int,
) -> tuple[list[dict[str, Any]], int, int | None]:
    """
    Parse the ``<RISQUE>`` element into CAAML dangerRatings entries.

    Returns the CAAML list, today's RISQUE1 value (used for tendency
    direction), and the optional bulletin-wide altitude split (used as
    per-problem elevation fallback).

    Args:
        risque: The ``<RISQUE>`` XML element from ``<CARTOUCHERISQUE>``.
        massif_id: The massif integer ID (for error messages).

    Returns:
        ``(danger_ratings, risque1, bulletin_split)`` where
        ``bulletin_split`` is ``None`` for single-band bulletins.

    Raises:
        MeteoFranceTranslationError: Any required attribute is absent,
            non-numeric, or out of range.

    """
    risque1_raw = _require_attrib(risque, "RISQUE1")
    try:
        risque1 = int(risque1_raw)
    except ValueError as exc:
        raise MeteoFranceTranslationError(
            f"Non-integer RISQUE1={risque1_raw!r} for massif {massif_id}."
        ) from exc
    if risque1 not in _DANGER_LEVEL:
        raise MeteoFranceTranslationError(
            f"RISQUE1={risque1} out of range 1..5 for massif {massif_id}."
        )

    altitude_raw = risque.attrib.get("ALTITUDE", "").strip()
    risque2_raw = risque.attrib.get("RISQUE2", "").strip()
    bulletin_split: int | None = None

    if altitude_raw and risque2_raw:
        try:
            bulletin_split = int(altitude_raw)
        except ValueError as exc:
            raise MeteoFranceTranslationError(
                f"Non-integer ALTITUDE={altitude_raw!r} for massif {massif_id}."
            ) from exc
        try:
            risque2 = int(risque2_raw)
        except ValueError as exc:
            raise MeteoFranceTranslationError(
                f"Non-integer RISQUE2={risque2_raw!r} for massif {massif_id}."
            ) from exc
        if risque2 not in _DANGER_LEVEL:
            raise MeteoFranceTranslationError(
                f"RISQUE2={risque2} out of range 1..5 for massif {massif_id}."
            )
        danger_ratings: list[dict[str, Any]] = [
            {
                "mainValue": _DANGER_LEVEL[risque1],
                "elevation": {"upperBound": str(bulletin_split)},
                "validTimePeriod": "all_day",
            },
            {
                "mainValue": _DANGER_LEVEL[risque2],
                "elevation": {"lowerBound": str(bulletin_split)},
                "validTimePeriod": "all_day",
            },
        ]
    else:
        danger_ratings = [
            {
                "mainValue": _DANGER_LEVEL[risque1],
                "validTimePeriod": "all_day",
            }
        ]

    return danger_ratings, risque1, bulletin_split


def _stabilite_texte(stabilite: ET.Element) -> str | None:
    """
    Extract the problem prose text from a ``<STABILITE>`` element.

    Prefers ``<TEXTESANSTITRE>`` (without title) over ``<TEXTE>``.

    Args:
        stabilite: The ``<STABILITE>`` XML element.

    Returns:
        The stripped text content, or ``None`` when both elements are absent
        or have no text.

    """
    texte_el = stabilite.find("TEXTESANSTITRE")
    if texte_el is None:
        texte_el = stabilite.find("TEXTE")
    if texte_el is None or not texte_el.text:
        return None
    return texte_el.text.strip() or None


def _parse_avalanche_problems(
    stabilite: ET.Element | None,
    aspects: list[str],
    bulletin_split: int | None,
    massif_id: int,
) -> list[dict[str, Any]]:
    """
    Parse the ``<STABILITE>`` element into CAAML avalancheProblems entries.

    Iterates SAT1/SAT2, looks up each code in ``SAT_TO_EAWS``, and attempts
    to derive per-problem elevation from the problem prose text, falling back
    to the bulletin-wide altitude split.

    Args:
        stabilite: The ``<STABILITE>`` XML element (may be ``None``).
        aspects: The bulletin-wide aspect list (from ``<PENTE>``).
        bulletin_split: Bulletin-wide altitude split in metres (or ``None``).
        massif_id: The massif integer ID (for error messages).

    Returns:
        A (possibly empty) list of CAAML avalanche-problem dicts.

    Raises:
        MeteoFranceTranslationError: An SAT code is non-numeric or outside
            the known ``{1..6}`` vocabulary.

    """
    problems: list[dict[str, Any]] = []
    if stabilite is None:
        return problems

    sit_aval = stabilite.find("SitAvalTyp")
    if sit_aval is None:
        return problems

    texte_text = _stabilite_texte(stabilite)

    for slot in ("SAT1", "SAT2"):
        code_raw = sit_aval.attrib.get(slot, "").strip()
        if not code_raw:
            continue
        try:
            code = int(code_raw)
        except ValueError as exc:
            raise MeteoFranceTranslationError(
                f"Non-integer {slot}={code_raw!r} for massif {massif_id}."
            ) from exc
        if code not in SAT_TO_EAWS:
            raise MeteoFranceTranslationError(
                f"{slot}={code} is outside the known SAT vocabulary "
                f"{{1..6}} for massif {massif_id}. "
                "MF may have extended their problem-type vocabulary."
            )
        problem: dict[str, Any] = {
            "problemType": SAT_TO_EAWS[code],
            "aspects": aspects,
            "validTimePeriod": "all_day",
        }
        elev = _elevation_from_prose(texte_text, bulletin_split)
        if elev:
            problem["elevation"] = elev
        problems.append(problem)

    return problems


def _parse_tendency(
    risque: ET.Element,
    cartouche: ET.Element,
    risque1: int,
    massif_id: int,
) -> list[dict[str, Any]]:
    """
    Parse the J+2 outlook fields into a CAAML tendency entry list.

    Returns an empty list when the required J+2 fields are absent or
    unparseable (rather than raising, to keep tendency non-blocking).

    Args:
        risque: The ``<RISQUE>`` element (carries ``RISQUEMAXIJ2`` and
            ``DATE_RISQUE_J2`` attributes).
        cartouche: The ``<CARTOUCHERISQUE>`` element (carries the J+2
            prose nodes).
        risque1: Today's maximum danger level (for direction comparison).
        massif_id: The massif integer ID (for log messages).

    Returns:
        A list of zero or one CAAML tendency dicts.

    """
    risque_maxi_j2_raw = risque.attrib.get("RISQUEMAXIJ2", "").strip()
    date_risque_j2_raw = risque.attrib.get("DATE_RISQUE_J2", "").strip()
    if not risque_maxi_j2_raw or not date_risque_j2_raw:
        return []

    try:
        risque_maxi_j2 = int(risque_maxi_j2_raw)
    except ValueError:
        return []

    risque_j2_el = cartouche.find("RisqueJ2")
    commentaire_j2_el = cartouche.find("CommentaireRisqueJ2")

    try:
        j2_valid_from = _parse_local_to_utc(date_risque_j2_raw)
        j2_valid_to = _parse_plus_one_day(date_risque_j2_raw)
    except MeteoFranceTranslationError:
        logger.warning(
            "Could not parse DATE_RISQUE_J2=%r for massif %d — omitting tendency.",
            date_risque_j2_raw,
            massif_id,
        )
        return []

    return [
        {
            "tendencyType": _evolution_from_levels(risque1, risque_maxi_j2),
            "highlights": _elem_text(risque_j2_el),
            "comment": _elem_text(commentaire_j2_el),
            "validTime": {
                "startTime": j2_valid_from,
                "endTime": j2_valid_to,
            },
        }
    ]


def _parse_snowpack_structure(root: ET.Element) -> dict[str, str] | None:
    """
    Extract the snowpack-structure comment from ``<QUALITE>/<TEXTE>``.

    Args:
        root: The ``<BULLETINS_NEIGE_AVALANCHE>`` root element.

    Returns:
        A ``{"comment": str}`` dict, or ``None`` when the element is absent.

    """
    qualite = root.find("QUALITE")
    if qualite is None:
        return None
    qualite_texte = qualite.find("TEXTE")
    if qualite_texte is None or not qualite_texte.text:
        return None
    return {"comment": qualite_texte.text.strip()}


def _parse_avalanche_activity(
    cartouche: ET.Element,
    stabilite: ET.Element | None,
) -> dict[str, str]:
    """Build the ``avalancheActivity`` dict from CARTOUCHERISQUE and STABILITE.

    Highlights: first line of ``<RESUME>``. Comment: ``<TEXTESANSTITRE>``
    when present; otherwise ``<TEXTE>`` with the title prefix stripped.

    Args:
        cartouche: The ``<CARTOUCHERISQUE>`` element.
        stabilite: The ``<STABILITE>`` element (may be ``None``).

    Returns:
        A dict with ``"highlights"`` and ``"comment"`` string keys.

    """
    resume_el = cartouche.find("RESUME")
    resume_text = _elem_text(resume_el)
    highlights = resume_text.split("\n")[0].strip() if resume_text else ""

    activity_comment = ""
    if stabilite is not None:
        texte_sst = stabilite.find("TEXTESANSTITRE")
        if texte_sst is not None and texte_sst.text:
            activity_comment = texte_sst.text.strip()
        else:
            activity_comment = _strip_titre_from_texte(stabilite)

    return {"highlights": highlights, "comment": activity_comment}


def _strip_titre_from_texte(stabilite: ET.Element) -> str:
    """
    Extract the body of ``<TEXTE>`` with the ``<TITRE>`` prefix removed.

    When ``<TEXTESANSTITRE>`` is absent (e.g. for older bulletins), the
    structured body is in ``<TEXTE>`` with the title on the first line.
    This helper strips the title prefix for a cleaner activity comment.

    Args:
        stabilite: The ``<STABILITE>`` element.

    Returns:
        The stripped body text, or ``""`` when ``<TEXTE>`` is absent.

    """
    texte_el = stabilite.find("TEXTE")
    titre_el = stabilite.find("TITRE")
    if texte_el is None or not texte_el.text:
        return ""
    raw_text = texte_el.text.strip()
    if titre_el is not None and titre_el.text:
        titre = titre_el.text.strip()
        if raw_text.startswith(titre):
            raw_text = raw_text[len(titre) :].strip()
    return raw_text


def _parse_header(root: ET.Element) -> tuple[int, str, str, str, str, bool, str]:
    """
    Parse and validate the root-element scalar attributes of a DPBRA document.

    Returns a tuple of all values needed by the main function body to avoid
    repeated attribute access and keep ``parse_dpbra_xml`` within the
    cyclomatic-complexity limit.

    Args:
        root: The ``<BULLETINS_NEIGE_AVALANCHE>`` root element.

    Returns:
        ``(massif_id, massif_name, date_bulletin, date_validite,
        date_diffusion, is_amendment, bulletin_id)``

    Raises:
        MeteoFranceTranslationError: Any required attribute is missing or
            non-integer.

    """
    massif_id_str = _require_attrib(root, "ID")
    try:
        massif_id = int(massif_id_str)
    except ValueError as exc:
        raise MeteoFranceTranslationError(
            f"Non-integer @ID value {massif_id_str!r}."
        ) from exc

    massif_name = _require_attrib(root, "MASSIF")
    date_bulletin = _require_attrib(root, "DATEBULLETIN")
    date_validite = _require_attrib(root, "DATEVALIDITE")
    date_diffusion = _require_attrib(root, "DATEDIFFUSION")
    amendment_str = root.attrib.get("AMENDEMENT", "false").strip().lower()
    is_amendment = amendment_str == "true"

    if is_amendment:
        logger.info(
            "MeteoFrance bulletin @ID=%d @MASSIF=%s has AMENDEMENT=true — "
            "logging for later amendment-suffix implementation.",
            massif_id,
            massif_name,
        )

    validity_date = _parse_local_date(date_validite)
    bulletin_id = f"FR-{massif_id:02d}-{validity_date.isoformat()}"
    return (
        massif_id,
        massif_name,
        date_bulletin,
        date_validite,
        date_diffusion,
        is_amendment,
        bulletin_id,
    )


def _require_sub_elements(
    root: ET.Element,
    cartouche: ET.Element | None,
    massif_id: int,
) -> tuple[ET.Element, ET.Element, ET.Element, ET.Element | None]:
    """
    Validate and return the required sub-elements of a DPBRA document.

    Args:
        root: The ``<BULLETINS_NEIGE_AVALANCHE>`` root element.
        cartouche: The ``<CARTOUCHERISQUE>`` element (may be ``None``).
        massif_id: The massif integer ID (for error messages).

    Returns:
        ``(cartouche, risque, pente, stabilite)`` — all non-``None``
        except ``stabilite`` which is optional.

    Raises:
        MeteoFranceTranslationError: ``<CARTOUCHERISQUE>``, ``<RISQUE>``,
            or ``<PENTE>`` is absent.

    """
    if cartouche is None:
        raise MeteoFranceTranslationError(
            f"Missing <CARTOUCHERISQUE> in bulletin for massif {massif_id}."
        )

    risque = cartouche.find("RISQUE")
    if risque is None:
        raise MeteoFranceTranslationError(
            f"Missing <RISQUE> in bulletin for massif {massif_id}."
        )

    pente = cartouche.find("PENTE")
    if pente is None:
        raise MeteoFranceTranslationError(
            f"Missing <PENTE> in bulletin for massif {massif_id}."
        )

    stabilite = root.find("STABILITE")
    return cartouche, risque, pente, stabilite


def _parse_custom_data_mf(
    root: ET.Element,
    cartouche: ET.Element,
    risque: ET.Element,
    massif_id: int,
    is_amendment: bool,
    date_bulletin: str,
    date_validite: str,
    date_diffusion: str,
) -> dict[str, Any]:
    """
    Build the ``customData.MF`` dict from provider-specific DPBRA fields.

    Collects everything DPBRA carries that CAAML cannot represent: amendment
    flag, MF internal ID, raw local timestamps (for UTC-conversion debugging),
    images, snow-cover data, and the J+2 outlook object.

    Args:
        root: The ``<BULLETINS_NEIGE_AVALANCHE>`` root element.
        cartouche: The ``<CARTOUCHERISQUE>`` element.
        risque: The ``<RISQUE>`` element inside ``<CARTOUCHERISQUE>``.
        massif_id: The massif integer ID.
        is_amendment: Whether the bulletin has ``@AMENDEMENT="true"``.
        date_bulletin: Raw ``@DATEBULLETIN`` string (naive Paris time).
        date_validite: Raw ``@DATEVALIDITE`` string (naive Paris time).
        date_diffusion: Raw ``@DATEDIFFUSION`` string (naive Paris time).

    Returns:
        The ``customData.MF`` dict ready for inclusion in the CAAML output.

    """
    # Images
    image_risque_el = cartouche.find("ImageRisque")
    image_pente_el = cartouche.find("ImagePente")
    accidentel_el = cartouche.find("ACCIDENTEL")
    naturel_el = cartouche.find("NATUREL")
    risque_j2_el = cartouche.find("RisqueJ2")
    commentaire_j2_el = cartouche.find("CommentaireRisqueJ2")

    images: dict[str, str | None] = {
        "danger": _elem_text(image_risque_el) or None,
        "aspectRose": _elem_text(image_pente_el) or None,
    }

    # Snow cover data
    enneigement_el = root.find("ENNEIGEMENT")
    snow_cover: dict[str, Any] | None = None
    if enneigement_el is not None:
        limite_nord = enneigement_el.attrib.get("LimiteNord", "")
        limite_sud = enneigement_el.attrib.get("LimiteSud", "")
        enneigement_date = enneigement_el.attrib.get("DATE", "")
        depths: list[dict[str, Any]] = []
        for niveau_el in enneigement_el.findall("NIVEAU"):
            try:
                depths.append(
                    {
                        "altitudeM": int(niveau_el.attrib.get("ALTI", 0)),
                        "north": int(niveau_el.attrib.get("N", 0)),
                        "south": int(niveau_el.attrib.get("S", 0)),
                    }
                )
            except (ValueError, KeyError):
                pass
        snow_cover = {
            "date": enneigement_date,
            "snowLineNorthM": int(limite_nord) if limite_nord.isdigit() else None,
            "snowLineSouthM": int(limite_sud) if limite_sud.isdigit() else None,
            "depthsCm": depths,
        }

    # J+2 outlook
    risque_maxi_j2_raw = risque.attrib.get("RISQUEMAXIJ2", "").strip()
    date_risque_j2_raw = risque.attrib.get("DATE_RISQUE_J2", "").strip()
    j2_outlook: dict[str, Any] | None = None
    if risque_maxi_j2_raw and date_risque_j2_raw:
        try:
            risque_maxi_j2_int = int(risque_maxi_j2_raw)
            j2_outlook = {
                "maxDanger": risque_maxi_j2_int,
                "date": date_risque_j2_raw,
                "label": _elem_text(risque_j2_el),
                "comment": _elem_text(commentaire_j2_el),
            }
        except ValueError:
            pass

    return {
        "mfInternalId": massif_id,
        "amendment": is_amendment,
        "rawLocalTimes": {
            "issuedAt": date_bulletin,
            "validTo": date_validite,
            "publishedAt": date_diffusion,
        },
        "images": images,
        "snowCover": snow_cover,
        "j2Outlook": j2_outlook,
        "redundantProse": {
            "accidentel": _elem_text(accidentel_el),
            "naturel": _elem_text(naturel_el),
        },
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_dpbra_xml(xml_bytes: bytes) -> dict[str, Any]:
    """
    Parse a single DPBRA XML document and return a CAAML v6 JSON dict.

    The returned dict has the same top-level keys as the SLF and EUREGIO
    payloads stored in ``Bulletin.raw_data.properties`` so it can be fed
    directly into ``upsert_bulletin(raw, run)``.

    Args:
        xml_bytes: Raw XML bytes of a Météo-France DPBRA bulletin.

    Returns:
        A CAAML v6 JSON dict ready for ``upsert_bulletin()``.

    Raises:
        MeteoFranceDelegatedRegionError: The root element is not
            ``BULLETINS_NEIGE_AVALANCHE`` — the massif delegates to another
            forecast authority (e.g. Andorre). The caller should skip the
            massif without counting it as a failure.
        MeteoFranceTranslationError: A required attribute is absent, a danger
            level is out of range, or an SAT code is outside the known
            ``{1..6}`` vocabulary. The caller should log and count as
            ``records_failed``.

    """
    root = _safe_parse(xml_bytes)

    # Delegated-region check — must come before any attribute access.
    if root.tag != "BULLETINS_NEIGE_AVALANCHE":
        raise MeteoFranceDelegatedRegionError(
            f"Unexpected root element <{root.tag}> — "
            "massif delegates to another forecast authority."
        )

    (
        massif_id,
        massif_name,
        date_bulletin,
        date_validite,
        date_diffusion,
        is_amendment,
        bulletin_id,
    ) = _parse_header(root)

    cartouche, risque, pente, stabilite = _require_sub_elements(
        root, root.find("CARTOUCHERISQUE"), massif_id
    )

    danger_ratings, risque1, bulletin_split = _parse_danger_ratings(risque, massif_id)
    aspects = _aspects_from_pente(pente)
    problems = _parse_avalanche_problems(stabilite, aspects, bulletin_split, massif_id)
    snowpack_structure = _parse_snowpack_structure(root)
    activity = _parse_avalanche_activity(cartouche, stabilite)
    tendency = _parse_tendency(risque, cartouche, risque1, massif_id)
    custom_data_mf = _parse_custom_data_mf(
        root=root,
        cartouche=cartouche,
        risque=risque,
        massif_id=massif_id,
        is_amendment=is_amendment,
        date_bulletin=date_bulletin,
        date_validite=date_validite,
        date_diffusion=date_diffusion,
    )

    caaml: dict[str, Any] = {
        "bulletinID": bulletin_id,
        "lang": "fr",
        "validTime": {
            "startTime": _parse_local_to_utc(date_bulletin),
            "endTime": _parse_local_to_utc(date_validite),
        },
        "publicationTime": _parse_local_to_utc(date_diffusion),
        "unscheduled": is_amendment,
        "regions": [{"regionID": f"FR-{massif_id:02d}", "name": massif_name}],
        "dangerRatings": danger_ratings,
        "avalancheProblems": problems,
        "avalancheActivity": activity,
        "tendency": tendency,
        "customData": {"MF": custom_data_mf},
    }

    if snowpack_structure is not None:
        caaml["snowpackStructure"] = snowpack_structure

    return caaml
