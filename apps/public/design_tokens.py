"""
apps/public/design_tokens.py — Component-library registry.

Hand-curated catalogue of everything the design-system page at
``/_components/`` renders. Two top-level groups, both built from the same
``FoundationCategory`` dataclass:

1. **Foundations** — design tokens, mirroring src/css/main.css's
   ``@theme {}`` block. ``kind`` is one of ``"swatches"``, ``"typography"``,
   ``"radius"``, ``"layout"``, ``"icons"``. Each entry's ``tokens`` field
   carries ``Token`` and/or ``IconToken`` instances. The
   registry-vs-CSS sync check in ``apps/public/checks.py`` walks this group.

2. **Components** — rendered HTML components. ``kind`` is ``"components"``.
   Each entry sources its render content from its ``partial`` template
   path and a ``variants`` tuple of context dicts; the renderer iterates
   the variants and ``{% include partial with **variant.context %}`` for
   each. Variant fixtures live in ``apps/public/_component_fixtures.py`` so
   this file stays free of synthetic-data construction.

The shape is deliberately Python-side (not parsed from CSS) so the renderer
stays simple and the registry can carry presentation hints (panel kind,
description, ordering) the CSS doesn't know about. Theme-invariant tokens
(EAWS, weather) declare ``dark=None``; the swatch templates use
``style="background: var(<name>)"`` so a single inlined declaration picks
up the right value automatically depending on whether the token sits inside
a ``.dark`` ancestor.
"""

from dataclasses import dataclass
from typing import Any

from django.conf import settings

from apps.public._component_fixtures import (
    BULLETIN_HEADLINE_VARIANTS,
    BUTTON_VARIANTS,
    CALLOUT_VARIANTS,
    CARD_VARIANTS,
    CHIP_VARIANTS,
    COLLAPSIBLE_PANEL_VARIANTS,
    DAY_CHARACTER_VARIANTS,
    DAY_WINDOWS_VARIANTS,
    EYEBROW_VARIANTS,
    FAVOURITE_PROBLEM_VARIANTS,
    FORM_FIELD_VARIANTS,
    HOURLY_CHART_VARIANTS,
    MAP_OVERLAY_TOGGLE_VARIANTS,
    META_CELL_VARIANTS,
    NAV_VARIANTS,
    NO_DATA_SUPPLIED_VARIANTS,
    OVERFLOW_MENU_VARIANTS,
    OVERLAY_BANNER_VARIANTS,
    OVERLAY_MODAL_VARIANTS,
    OVERLAY_SHEET_VARIANTS,
    PAGE_TITLE_VARIANTS,
    PERIOD_TRANSITION_VARIANTS,
    RATING_BLOCK_ALBINA_BAND_VARIANTS,
    RATING_BLOCK_VARIANTS,
    REGION_TOOLTIP_VARIANTS,
    RESORT_FACTS_VARIANTS,
    RESORT_META_ROW_VARIANTS,
    RESORT_WHY_IT_MATTERS_VARIANTS,
    ROW_DISCLOSURE_VARIANTS,
    SEASON_CALENDAR_VARIANTS,
    SEASON_SCRUBBER_VARIANTS,
    SHEET_HEADER_VARIANTS,
    SITE_FOOTER_VARIANTS,
    STATUS_PAGE_VARIANTS,
    SUBSCRIBE_FORM_VARIANTS,
    SUBSCRIBE_OUTCOMES_VARIANTS,
    SWITCH_VARIANTS,
    TENDENCY_OUTLOOK_VARIANTS,
    THEME_PREFERENCE_VARIANTS,
    TOAST_BANNER_VARIANTS,
    TOAST_VARIANTS,
    UGC_PANEL_ROW_VARIANTS,
    UGC_PANEL_VARIANTS,
    WEATHER_DAY_LINE_VARIANTS,
    WEATHER_DAY_PICKER_VARIANTS,
    WEATHER_MASTHEAD_VARIANTS,
    WEATHER_PANEL_VARIANTS,
    WEATHER_PROVENANCE_VARIANTS,
)
from apps.weather.icon_sets import icon_set_dir

# SNOW-791: condition icons live under the configured set's directory while
# four candidates are compared (apps/weather/icon_sets.py). Resolved once at
# import — the component library documents the configured set, not whatever
# a ``?icons=`` session has pinned.
_ICON_DIR = icon_set_dir(settings.WEATHER_ICON_SET)


@dataclass(frozen=True)
class Token:
    """One CSS custom property surfaced in the component library.

    Attributes:
        name: CSS custom-property name including the leading ``--``.
        label: Human-readable caption shown alongside the swatch.
        light: Literal CSS value in the light theme — shown as caption text.
        dark: Literal CSS value in the dark theme, or ``None`` if the token
            is theme-invariant (EAWS scale, weather backdrops).

    """

    name: str
    label: str
    light: str
    dark: str | None


@dataclass(frozen=True)
class IconToken:
    """One static-asset SVG icon surfaced in the component library.

    Distinct from ``Token`` because icons don't carry a CSS value — they
    have a static-relative path, an optional ``alt`` for accessibility,
    and a ``group`` so the panel template can sub-section the rendered
    grid (favicons / danger-level pictograms / avalanche problem icons).

    Attributes:
        name: File-stem identifier shown as the caption monospace label.
        label: Human-readable caption.
        path: Path relative to ``STATIC_URL``; passed straight to ``{% static %}``.
        group: Sub-group label used by the icons panel to sub-section the grid.

    """

    name: str
    label: str
    path: str
    group: str


@dataclass(frozen=True)
class FoundationCategory:
    """One sidebar entry / one panel-worth of content.

    Used for both Foundations entries (``kind`` in ``swatches``,
    ``typography``, ``radius``, ``layout``, ``icons``; populated via
    ``tokens``) and Components entries (``kind="components"``; populated
    via ``partial`` + ``variants``). The class name is preserved from
    SNOW-103 for backwards compatibility with the existing sync check;
    the ``Foundation`` prefix is now mildly inaccurate but renaming would
    churn imports across the whole library.

    Attributes:
        slug: URL slug used in ``/partials/_components/<slug>/``.
        label: Sidebar label.
        description: One-line panel intro shown above the content.
        kind: Panel template hint; the panel wrapper dispatches to
            ``_<kind>.html`` based on this value.
        tokens: Tuple of ``Token`` and/or ``IconToken`` entries rendered
            inside the panel. Required when ``kind`` is a foundations
            kind; ignored (defaults to empty) when ``kind="components"``.
            The sync check in ``apps/public/checks.py`` filters to ``Token``
            instances since only those map to CSS custom properties.
        partial: Django template path included once per variant when
            ``kind="components"``. Required for components entries;
            ignored for foundations.
        variants: Tuple of context dicts. Each variant carries a
            ``caption`` plus the keys the ``partial`` reads. Variants may
            optionally carry ``solo=True`` — that variant spans both
            columns on a two-column layout. Required for components
            entries; ignored for foundations.
        panel_layout: Layout shape for the panel content. ``"stack"`` is
            a vertical stack (default, used by foundations and most
            components). ``"two-col"`` is single-column on mobile and
            two-column on desktop (≥ md breakpoint) — useful for
            paired variant matrices.
        swatch_columns: Optional fixed column count for the swatch grid
            (``kind="swatches"`` only). Defaults to ``None``, which
            renders the responsive grid (2 → 3 → 4 columns at sm/lg).
            Set to a positive int when the token list has a meaningful
            row/column structure that lines up — e.g. EAWS uses ``5`` so
            each column corresponds to one danger level (low → very_high).

    """

    slug: str
    label: str
    description: str
    kind: str
    tokens: tuple[Token | IconToken, ...] = ()
    partial: str | None = None
    variants: tuple[dict[str, Any], ...] = ()
    panel_layout: str = "stack"
    swatch_columns: int | None = None


@dataclass(frozen=True)
class LibraryGroup:
    """One top-level grouping in the component-library sidebar.

    Today there are two: Foundations (design tokens) and Components
    (rendered HTML partials). Each renders as a sidebar heading followed
    by its category entries, in the order declared.

    Attributes:
        slug: Stable identifier used by tests; not surfaced in the URL.
        label: Sidebar heading text.
        categories: Tuple of category entries shown under this heading.

    """

    slug: str
    label: str
    categories: tuple[FoundationCategory, ...]


FOUNDATION_CATEGORIES: tuple[FoundationCategory, ...] = (
    FoundationCategory(
        slug="typography",
        label="Typography",
        description="Type families and the heading / body / mono ramp.",
        kind="typography",
        tokens=(
            Token("--font-sans", "Sans", "'DM Sans', system-ui, sans-serif", None),
            Token("--font-mono", "Mono", "'DM Mono', ui-monospace, monospace", None),
        ),
    ),
    FoundationCategory(
        slug="type-scale",
        label="Type scale",
        description=(
            "Labels, chip captions and the prose body line-height. Plugs the "
            "gaps in the Tailwind default ramp that previously appeared as "
            "``text-[9px]`` / ``text-[9.5px]`` / ``leading-[1.6]`` arbitraries."
        ),
        kind="typescale",
        tokens=(
            Token("--text-meta", "Meta label", "11px", None),
            Token("--text-pill", "Pill-chip caption", "9.5px", None),
            Token("--text-caption", "Caption", "11px", None),
            Token("--text-summary", "Summary title", "13px", None),
            Token("--text-label", "Problem / wordmark label", "15px", None),
            Token("--leading-prose", "Prose body leading", "1.6", None),
        ),
    ),
    FoundationCategory(
        slug="surfaces",
        label="Surfaces",
        description="Page background and card surface fills.",
        kind="swatches",
        tokens=(
            Token("--color-bg", "Page background", "#f2f0ec", "#1c1b19"),
            Token("--color-card", "Card", "#ffffff", "#2a2825"),
            Token("--color-card-subtle", "Card (subtle)", "#fafaf8", "#23211f"),
            Token("--color-tag", "Tag", "#f5f3ef", "#302e2a"),
        ),
    ),
    FoundationCategory(
        slug="text-ramp",
        label="Text ramp",
        description="Three-step neutral ramp for headings, body and meta.",
        kind="swatches",
        tokens=(
            Token("--color-text-1", "Primary", "#1a1916", "#edece8"),
            Token("--color-text-2", "Secondary", "#6b6860", "#a8a49c"),
            Token("--color-text-3", "Tertiary", "#6e6b65", "#9a968e"),
        ),
    ),
    FoundationCategory(
        slug="borders",
        label="Borders",
        description="Hairlines and stronger separators.",
        kind="swatches",
        tokens=(
            Token(
                "--color-border",
                "Border",
                "rgba(0, 0, 0, 0.09)",
                "rgba(255, 255, 255, 0.09)",
            ),
            Token(
                "--color-border-strong",
                "Border strong",
                "rgba(0, 0, 0, 0.16)",
                "rgba(255, 255, 255, 0.16)",
            ),
        ),
    ),
    FoundationCategory(
        slug="status",
        label="Status",
        description="Flash messages and status badges.",
        kind="swatches",
        tokens=(
            Token("--color-status-error-bg", "Error bg", "#fee2e2", "#451a1a"),
            Token("--color-status-error-text", "Error text", "#991b1b", "#fca5a5"),
            Token("--color-status-warning-bg", "Warning bg", "#fef3c7", "#452a0a"),
            Token("--color-status-warning-text", "Warning text", "#92400e", "#fcd34d"),
            Token("--color-status-success-bg", "Success bg", "#d1fae5", "#14332a"),
            Token("--color-status-success-text", "Success text", "#065f46", "#6ee7b7"),
            Token("--color-status-info-bg", "Info bg", "#dbeafe", "#1e2a4a"),
            Token("--color-status-info-text", "Info text", "#1e40af", "#93c5fd"),
        ),
    ),
    FoundationCategory(
        slug="eaws",
        label="EAWS scale",
        description="Five-level danger scale (theme-invariant by EAWS spec).",
        kind="swatches",
        swatch_columns=5,
        tokens=(
            Token("--color-eaws-low", "Low", "#ccff66", None),
            Token("--color-eaws-moderate", "Moderate", "#ffff00", None),
            Token("--color-eaws-considerable", "Considerable", "#ff9900", None),
            Token("--color-eaws-high", "High", "#ff0000", None),
            Token("--color-eaws-very-high", "Very high", "#820100", None),
            Token("--color-eaws-low-tint", "Low tint", "#e8ffb8", None),
            Token("--color-eaws-moderate-tint", "Moderate tint", "#fff7b8", None),
            Token(
                "--color-eaws-considerable-tint", "Considerable tint", "#ffe5c2", None
            ),
            Token("--color-eaws-high-tint", "High tint", "#ffd9d9", None),
            Token("--color-eaws-very-high-tint", "Very-high tint", "#1a0000", None),
            Token("--color-eaws-low-text", "Low text", "#3a5a00", None),
            Token("--color-eaws-moderate-text", "Moderate text", "#4d4500", None),
            Token(
                "--color-eaws-considerable-text", "Considerable text", "#5c3000", None
            ),
            Token("--color-eaws-high-text", "High text", "#6b0000", None),
            Token("--color-eaws-very-high-text", "Very-high text", "#ffffff", None),
            Token("--color-eaws-low-fg", "Low fg", "#1a1916", None),
            Token("--color-eaws-moderate-fg", "Moderate fg", "#1a1916", None),
            Token("--color-eaws-considerable-fg", "Considerable fg", "#1a1916", None),
            Token("--color-eaws-high-fg", "High fg", "#ffffff", None),
            Token("--color-eaws-very-high-fg", "Very-high fg", "#ffffff", None),
            Token("--color-eaws-mixed-fg", "Mixed-level fg", "#1a0000", None),
        ),
    ),
    FoundationCategory(
        slug="slope",
        label="Slope angle",
        description=(
            "swisstopo slope-angle classes, SLF classification "
            "(theme-invariant; below 30° is unpainted)."
        ),
        kind="swatches",
        swatch_columns=5,
        tokens=(
            Token("--color-slope-30", "30–35°", "#f2e50a", None),
            Token("--color-slope-35", "35–40°", "#f46f24", None),
            Token("--color-slope-40", "40–45°", "#de055b", None),
            Token("--color-slope-45", "45–50°", "#c889bb", None),
            Token("--color-slope-50", "Over 50°", "#4b4b4b", None),
        ),
    ),
    FoundationCategory(
        slug="weather",
        label="Weather header",
        description=(
            "Bulletin-header backdrops, 7 buckets × day/night (theme-invariant)."
        ),
        kind="swatches",
        tokens=(
            Token("--color-weather-clear-day", "Clear · day", "#5fa1d3", None),
            Token("--color-weather-clear-night", "Clear · night", "#1a2a4a", None),
            Token(
                "--color-weather-partly-cloudy-day",
                "Partly cloudy · day",
                "#8aabc8",
                None,
            ),
            Token(
                "--color-weather-partly-cloudy-night",
                "Partly cloudy · night",
                "#22324a",
                None,
            ),
            Token("--color-weather-cloudy-day", "Cloudy · day", "#a3aab3", None),
            Token("--color-weather-cloudy-night", "Cloudy · night", "#2a2e34", None),
            Token("--color-weather-fog-day", "Fog · day", "#b6b3aa", None),
            Token("--color-weather-fog-night", "Fog · night", "#2c2e35", None),
            Token("--color-weather-rain-day", "Rain · day", "#7a92aa", None),
            Token("--color-weather-rain-night", "Rain · night", "#1d2932", None),
            Token("--color-weather-snow-day", "Snow · day", "#b3c2ce", None),
            Token("--color-weather-snow-night", "Snow · night", "#1f2a3a", None),
            Token("--color-weather-thunder-day", "Thunder · day", "#5e6470", None),
            Token("--color-weather-thunder-night", "Thunder · night", "#101220", None),
            Token("--color-weather-fallback", "Fallback", "#3a3733", None),
        ),
    ),
    FoundationCategory(
        slug="chart",
        label="Meteogram",
        description=(
            "The hourly chart's own marks — two series colours, the "
            "accumulation fill, the day/night axis bar and the legend "
            "note. The other four series reuse foundations above: "
            "temperature is text-1, freezing level and the metre scale are "
            "accent, gusts are text-2, axis furniture is border."
        ),
        kind="swatches",
        tokens=(
            Token("--color-chart-precip", "Precipitation · mm", "#0f766e", None),
            Token("--color-chart-wind", "Sustained wind", "#4a4740", None),
            Token(
                "--color-chart-snow",
                "New snow · cm",
                "rgba(37, 99, 235, 0.35)",
                None,
            ),
            Token("--color-chart-night", "Axis bar · night", "#475569", "#64748b"),
            Token(
                "--color-chart-daylight", "Axis bar · daylight", "#f59e0b", "#fcd34d"
            ),
            Token("--color-chart-note-bg", "Legend note · fill", "#eef2ff", "#252a44"),
            Token("--color-chart-note-text", "Legend note · ink", "#2563eb", "#93c5fd"),
        ),
    ),
    FoundationCategory(
        slug="radius",
        label="Radius",
        description="Corner-radius scale for cards, tags and pills.",
        kind="radius",
        tokens=(
            Token("--radius-card", "Card", "12px", None),
            Token("--radius-tag", "Tag", "8px", None),
            Token("--radius-sm", "Small", "6px", None),
            Token("--radius-pill", "Pill", "4px", None),
        ),
    ),
    FoundationCategory(
        slug="layout",
        label="Layout",
        description="Breakpoints and content widths.",
        kind="layout",
        tokens=(
            Token("--breakpoint-tablet", "Tablet ≥", "600px", None),
            Token("--breakpoint-desktop", "Desktop ≥", "960px", None),
            Token("--container-card-mobile", "Card (mobile)", "390px", None),
            Token("--container-narrow", "Content (narrow)", "640px", None),
            Token("--container-wide", "Content (wide)", "720px", None),
            Token("--container-grid-max", "Grid (max)", "1200px", None),
        ),
    ),
    FoundationCategory(
        slug="icons",
        label="Icons",
        description=(
            "Static SVG assets shipped with the site: danger-tinted favicons, "
            "EAWS danger-level pictograms and EAWS avalanche-problem icons."
        ),
        kind="icons",
        tokens=(
            # ---- Favicons (browser tab + bookmark; tinted by danger level) ----
            IconToken("favicon", "Default", "favicon.svg", "Favicon"),
            IconToken("favicon-low", "Low", "favicon-low.svg", "Favicon"),
            IconToken(
                "favicon-moderate", "Moderate", "favicon-moderate.svg", "Favicon"
            ),
            IconToken(
                "favicon-considerable",
                "Considerable",
                "favicon-considerable.svg",
                "Favicon",
            ),
            IconToken("favicon-high", "High", "favicon-high.svg", "Favicon"),
            IconToken(
                "favicon-very_high", "Very high", "favicon-very_high.svg", "Favicon"
            ),
            # ---- EAWS danger-level pictograms (per-category, 1–5 + no rating) ----
            IconToken(
                "Dry-Snow-1",
                "Dry · 1 (Low)",
                "icons/eaws/danger_levels/Dry-Snow-1.svg",
                "Danger level",
            ),
            IconToken(
                "Dry-Snow-2",
                "Dry · 2 (Moderate)",
                "icons/eaws/danger_levels/Dry-Snow-2.svg",
                "Danger level",
            ),
            IconToken(
                "Dry-Snow-3",
                "Dry · 3 (Considerable)",
                "icons/eaws/danger_levels/Dry-Snow-3.svg",
                "Danger level",
            ),
            IconToken(
                "Dry-Snow-4-5",
                "Dry · 4–5 (High / Very High)",
                "icons/eaws/danger_levels/Dry-Snow-4-5.svg",
                "Danger level",
            ),
            IconToken(
                "Wet-Snow-1",
                "Wet · 1 (Low)",
                "icons/eaws/danger_levels/Wet-Snow-1.svg",
                "Danger level",
            ),
            IconToken(
                "Wet-Snow-2",
                "Wet · 2 (Moderate)",
                "icons/eaws/danger_levels/Wet-Snow-2.svg",
                "Danger level",
            ),
            IconToken(
                "Wet-Snow-3",
                "Wet · 3 (Considerable)",
                "icons/eaws/danger_levels/Wet-Snow-3.svg",
                "Danger level",
            ),
            IconToken(
                "Wet-Snow-4",
                "Wet · 4 (High)",
                "icons/eaws/danger_levels/Wet-Snow-4.svg",
                "Danger level",
            ),
            IconToken(
                "Wet-Snow-5",
                "Wet · 5 (Very High)",
                "icons/eaws/danger_levels/Wet-Snow-5.svg",
                "Danger level",
            ),
            IconToken(
                "No-Rating",
                "No rating",
                "icons/eaws/danger_levels/No-Rating.svg",
                "Danger level",
            ),
            # ---- EAWS avalanche-problem icons (canonical via hazard_icon filter) ----
            IconToken(
                "New-Snow",
                "New snow",
                "icons/eaws/avalanche_problems/New-Snow.svg",
                "Avalanche problem",
            ),
            IconToken(
                "Wind-Slab",
                "Wind slab",
                "icons/eaws/avalanche_problems/Wind-Slab.svg",
                "Avalanche problem",
            ),
            IconToken(
                "Persistent-Weak-Layer",
                "Persistent weak layer",
                "icons/eaws/avalanche_problems/Persistent-Weak-Layer.svg",
                "Avalanche problem",
            ),
            IconToken(
                "Wet-Snow",
                "Wet snow",
                "icons/eaws/avalanche_problems/Wet-Snow.svg",
                "Avalanche problem",
            ),
            IconToken(
                "Gliding-Snow",
                "Gliding snow",
                "icons/eaws/avalanche_problems/Gliding-Snow.svg",
                "Avalanche problem",
            ),
            IconToken(
                "Cornices",
                "Cornices",
                "icons/eaws/avalanche_problems/Cornices.svg",
                "Avalanche problem",
            ),
            IconToken(
                "No-Distinct-Avalanche-Problem",
                "No distinct problem",
                "icons/eaws/avalanche_problems/No-Distinct-Avalanche-Problem.svg",
                "Avalanche problem",
            ),
            # ---- Yr / MET Norway weather icons (MIT, see weather/LICENSE.md) ----
            # 14 entries: two day/night pairs (the buckets drawn with a sun or a
            # moon) plus ten single files. Granularity is higher than
            # WEATHER_BUCKETS — drizzle and light/moderate/heavy rain all map to
            # the "rain" bucket — so a surface can choose its asset by WMO code
            # rather than by bucket alone. `drizzle` and `light_rain` are the same
            # drawing under two names; see the licence file for why.
            IconToken(
                "clear-day", "Clear · day", _ICON_DIR + "clear-day.svg", "Weather"
            ),
            IconToken(
                "clear-night",
                "Clear · night",
                _ICON_DIR + "clear-night.svg",
                "Weather",
            ),
            IconToken(
                "partly_cloudy-day",
                "Partly cloudy · day",
                _ICON_DIR + "partly_cloudy-day.svg",
                "Weather",
            ),
            IconToken(
                "partly_cloudy-night",
                "Partly cloudy · night",
                _ICON_DIR + "partly_cloudy-night.svg",
                "Weather",
            ),
            IconToken("cloudy", "Cloudy", _ICON_DIR + "cloudy.svg", "Weather"),
            IconToken("fog", "Fog", _ICON_DIR + "fog.svg", "Weather"),
            IconToken("drizzle", "Drizzle", _ICON_DIR + "drizzle.svg", "Weather"),
            IconToken(
                "light_rain",
                "Light rain",
                _ICON_DIR + "light_rain.svg",
                "Weather",
            ),
            IconToken(
                "moderate_rain",
                "Moderate rain",
                _ICON_DIR + "moderate_rain.svg",
                "Weather",
            ),
            IconToken(
                "heavy_rain",
                "Heavy rain",
                _ICON_DIR + "heavy_rain.svg",
                "Weather",
            ),
            IconToken(
                "light_snow",
                "Light snow",
                _ICON_DIR + "light_snow.svg",
                "Weather",
            ),
            IconToken(
                "moderate_snow",
                "Moderate snow",
                _ICON_DIR + "moderate_snow.svg",
                "Weather",
            ),
            IconToken(
                "heavy_snow",
                "Heavy snow",
                _ICON_DIR + "heavy_snow.svg",
                "Weather",
            ),
            IconToken("thunder", "Thunder", _ICON_DIR + "thunder.svg", "Weather"),
            IconToken(
                "sunrise",
                "Sunrise",
                "icons/weather/sunrise.svg",
                "Weather",
            ),
            IconToken(
                "sunset",
                "Sunset",
                "icons/weather/sunset.svg",
                "Weather",
            ),
        ),
    ),
)


COMPONENT_CATEGORIES: tuple[FoundationCategory, ...] = (
    FoundationCategory(
        slug="day-windows",
        label="Day windows",
        description=(
            "Per-window EAWS rating panel — one row per validTimePeriod with "
            "a coloured danger-level tile, label and time-window pill. "
            "Banded periods (ALBINA / Météo-France with two elevation-split "
            "danger levels in one period) emit a row per band, each carrying "
            "the mountain elevation glyph beside the tile to mark the "
            "above/below band (SNOW-298); single-band periods (SLF; "
            "constant-danger ALBINA) omit the glyph. Variants cover the "
            "all-day case (≈95% of bulletins) across every danger level, a "
            "realistic split-day layout, and numeric- and treeline-pivot "
            "banded examples."
        ),
        kind="components",
        partial="includes/day_windows.html",
        variants=DAY_WINDOWS_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="period-transition",
        label="Period transition",
        description=(
            "Two-period day handling — hero badge + rise/fall chip in the "
            "bulletin header, and the transition row between period rows in "
            "the Day Risk Profile panel (SNOW-248). Four patterns: "
            "SLF escalating, SLF de-escalating, SLF flat-but-split, and "
            "EUREGIO elevation-banded."
        ),
        kind="components",
        partial="includes/bulletin_header.html",
        variants=PERIOD_TRANSITION_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="callout",
        label="Callout",
        description=(
            "Inline status callout — four semantic kinds (warning, error, success, "
            "info) backed by the --color-status-* palette, with optional body, "
            "monospace diagnostic block, and call-to-action link."
        ),
        kind="components",
        partial="includes/_callout.html",
        variants=CALLOUT_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="toast",
        label="Toast",
        description=(
            "Fixed-position notification toast — bottom-centred pill backed by "
            "the --color-status-* palette. Used for the service-worker update "
            "banner (info kind with Reload CTA) and HTMX error banners (error "
            "kind, no CTA). Single-slot; ARIA live region keyed off kind."
        ),
        kind="components",
        partial="includes/_toast.html",
        variants=TOAST_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="toast-banner",
        label="Toast banner",
        description=(
            "Full-width top-of-page toast (SNOW-376) — distinct from the "
            "bottom-centred Toast above. Purpose-built for the mutation-"
            "queue permanent-failure UX: a queued client mutation that a "
            "server response has permanently rejected. Single status-error "
            "kind; ARIA alert/assertive."
        ),
        kind="components",
        partial="includes/_toast_banner.html",
        variants=TOAST_BANNER_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="overlay-banner",
        label="Overlay banner",
        description=(
            "Persistent, condition-driven banner primitive (SNOW-486) — the "
            "'strip' variant is a full-width in-flow edge bar (off-season "
            "notice); the 'floating' variant is a fixed bottom-centre card "
            "with an optional icon, CTA and dismiss. Migrated the sw-update "
            "banner and the install prompts; the mutation-queue toast keeps "
            "its own template (a documented exception — see "
            "docs/decisions/overlay-primitives.md)."
        ),
        kind="components",
        partial="includes/_overlay_banner.html",
        variants=OVERLAY_BANNER_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="overlay-modal",
        label="Overlay modal",
        description=(
            "Full-screen backdrop + centred card primitive (SNOW-486), "
            "CTA-only by design — no dismiss control. Shared shell for the "
            "two PWA modals (forced update, reset required)."
        ),
        kind="components",
        partial="includes/_overlay_modal.html",
        variants=OVERLAY_MODAL_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="overlay-sheet",
        label="Overlay sheet",
        description=(
            "Fly-out sheet primitive (SNOW-486) shared by the favourite and "
            "report map surfaces — fixed to the viewport edge on mobile, a "
            "floating card on larger screens. Content is injected by the "
            "owning JS module (favourites.js / report.js) at open time; the "
            "shell itself carries the data-overlay dismiss contract."
        ),
        kind="components",
        partial="includes/_overlay_sheet.html",
        variants=OVERLAY_SHEET_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="day-character-callout",
        label="Day character callout",
        description=(
            "Inline bordered banner surfacing the day-character assessment "
            "above the bulletin body. Combines the Snowdesk favicon, a bold "
            "label, and a one-line explainer in a quiet warm-grey tint that "
            "holds in both light and dark modes."
        ),
        kind="components",
        partial="includes/day_character_callout.html",
        variants=DAY_CHARACTER_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="tendency-outlook",
        label="Tendency outlook",
        description=(
            "Directional outlook card for ALBINA bulletins. Shows a Unicode "
            "arrow (→ / ↗ / ↘), a bold danger-direction label, the target "
            "date, and optional forecaster highlights prose. Suppressed for "
            "SLF bulletins and any bulletin without a tendency_type."
        ),
        kind="components",
        partial="includes/_tendency_outlook.html",
        variants=TENDENCY_OUTLOOK_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="bulletin-headline",
        label="Bulletin headline",
        description=(
            "Data-driven one-liner below the day-character callout. "
            "Keyed on (source, partition_type, peak_rating, direction, "
            "family); top-12 SLF cells get hand-authored copy, everything "
            "else falls back to a generic danger-level phrase. "
            "Carries data-testid='bulletin-headline'."
        ),
        kind="components",
        partial="public/_bulletin_headline.html",
        variants=BULLETIN_HEADLINE_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="season-calendar",
        label="Season calendar",
        description=(
            "Full-season heatmap grid — one circle per day from SEASON_START_DATE "
            "through today+1, weeks-as-columns. Synthetic fixture covers all cell "
            "states: no-rating, every EAWS level solid, split pairs for "
            "afternoon-elevated days, today ring, and selected ring."
        ),
        kind="components",
        partial="public/partials/_season_calendar_demo.html",
        variants=SEASON_CALENDAR_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="season-scrubber",
        label="Season scrubber",
        description=(
            "Floating media-player transport at the bottom of /map/. Four circular "
            "buttons (skip-to-start, play-reverse, play-forward, skip-to-end) "
            "bracketing a draggable track; the two play buttons flank the track as a "
            "symmetric pair. A fifth button closes the row — the calendar toggle "
            "(SNOW-792), which is not a transport step: it opens a month grid above "
            "the pill for jumping to a named day. That popup is built in JavaScript "
            "and so renders empty here. The library variants pin the thumb at season "
            "start, mid-season, and season end so the thumb positioning is visible at "
            "the extremes; interaction is non-functional on this page (JS lives in "
            "map.js and map_scrubber_calendar.js)."
        ),
        kind="components",
        partial="public/partials/_season_scrubber_demo.html",
        variants=SEASON_SCRUBBER_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="button",
        label="Button",
        description=(
            "Primary, secondary and ghost button variants across standard and "
            "compact sizes. Renders either an ``<a>`` or a ``<button>`` depending "
            "on whether ``href`` is supplied."
        ),
        kind="components",
        partial="includes/_button.html",
        variants=BUTTON_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="card",
        label="Card",
        description=(
            "Surface card chrome — ``bg-card rounded-card border shadow-sm`` — "
            "across every padding variant used in the codebase."
        ),
        kind="components",
        partial="includes/_card.html",
        variants=CARD_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="status-page",
        label="Status page",
        description=(
            "Centred status-page shell — flex full-viewport wrapper → max-w-md "
            "column → p-8 centred card — used by all five confirmation / error "
            "pages in the subscriptions flow."
        ),
        kind="components",
        partial="public/partials/_status_page_demo.html",
        variants=STATUS_PAGE_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="nav",
        label="Nav",
        description=(
            "Persistent top navigation bar — wordmark, optional back-chevron link, "
            "optional season-trigger button, and a right-side auth/admin area. "
            "Variants cover the bare logo-only state, back link, season trigger, "
            "and an authenticated subscriber with a subscribed region."
        ),
        kind="components",
        partial="includes/nav.html",
        variants=NAV_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="site-footer",
        label="Site footer",
        description=(
            "Global site footer — one line of legal links (Help, Terms of Use, "
            "Privacy Policy, Colophon) followed by the running release. "
            "Reverses URLs internally; the release comes from the "
            "``pwa_version`` context processor, so no context variables are "
            "passed in."
        ),
        kind="components",
        partial="public/_site_footer.html",
        variants=SITE_FOOTER_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="rating-block",
        label="Rating block",
        description=(
            "Problem card for the bulletin page — danger-band header, aspect/elevation "
            "row, and prose comment. Eight core variants cover every EAWS problem "
            "type at a representative danger level: new snow, wind slab, persistent "
            "weak layers, "
            "cornices (dry), wet snow, gliding snow (wet), plus two prose-only "
            "empty-state variants — scope mentioned ('See description below') vs "
            "no scope ('All aspects · all elevations')."
        ),
        kind="components",
        partial="public/_rating_block.html",
        variants=RATING_BLOCK_VARIANTS,
        panel_layout="two-col",
    ),
    FoundationCategory(
        slug="rating-block-albina-bands",
        label="Rating block — ALBINA band headings",
        description=(
            "ALBINA elevation-band section headings injected above the first card of "
            "each band group (SNOW-292). The band_label field renders as "
            "data-testid='band-heading'; the optional time_subheader (pivot migration) "
            "renders as data-testid='band-time-subheader'. SLF and MF cards never "
            "carry these fields and are unaffected."
        ),
        kind="components",
        partial="public/_rating_block.html",
        variants=RATING_BLOCK_ALBINA_BAND_VARIANTS,
        panel_layout="two-col",
    ),
    FoundationCategory(
        slug="region-tooltip",
        label="Region tooltip",
        description=(
            "MapLibre popup body for a micro-region — danger-tile chip, region name, "
            "geographic breadcrumb, and bulletin CTA. Three variants: considerable "
            "rating, high with upper-band subdivision, and no-bulletin fallback."
        ),
        kind="components",
        partial="public/_region_tooltip.html",
        variants=REGION_TOOLTIP_VARIANTS,
        panel_layout="two-col",
    ),
    FoundationCategory(
        slug="subscribe-form",
        label="Subscribe form",
        description=(
            "Inline subscription CTA embedded on bulletin pages. Branches into four "
            "variants based on authentication state and subscription status: "
            "(1) anonymous — email-input form; "
            "(2) anonymous with validation error — form re-displayed with an error; "
            "(3) authenticated, not yet subscribed — one-click 'Add region' CTA; "
            "(4) authenticated, already subscribed — one-click 'Unsubscribe' CTA."
        ),
        kind="components",
        partial="accounts/partials/subscribe_form.html",
        variants=SUBSCRIBE_FORM_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="subscribe-outcomes",
        label="Subscribe outcomes",
        description=(
            "HTMX response fragments returned after a subscribe POST. Five outcome "
            "templates: generic check-inbox, account-access link sent, region added, "
            "already subscribed, and error. Each variant overrides the template path "
            "via the ``include_variant`` partial-key mechanism."
        ),
        kind="components",
        partial="accounts/partials/subscribe_success.html",
        variants=SUBSCRIBE_OUTCOMES_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="no-data-supplied",
        label="No data supplied",
        description=(
            "Empty-state placeholder used inside collapsible bulletin panels "
            "(Outlook, etc.) when the upstream feed supplied the field structurally "
            "but with no usable content. Single variant; no context variables required."
        ),
        kind="components",
        partial="includes/_no_data_supplied.html",
        variants=NO_DATA_SUPPLIED_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="collapsible-panel",
        label="Collapsible panel",
        description=(
            "Expandable ``<details>`` panel used for the four Snowpack & Weather "
            "sections on the bulletin page. Three variants: closed simple body, "
            "open simple body, and open multi-entry tendency body."
        ),
        kind="components",
        partial="includes/_collapsible_panel.html",
        variants=COLLAPSIBLE_PANEL_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="form-field",
        label="Form field",
        description=(
            "One labelled control with its error list — the shape every "
            "account form repeats. Ten call sites across six templates had "
            "written it by hand before SNOW-672. The widget comes from the "
            "form; this owns the chrome around it."
        ),
        kind="components",
        partial="includes/_form_field.html",
        variants=FORM_FIELD_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="page-title",
        label="Page title",
        description=(
            "The <h1> at the top of an ordinary content page. Thirteen call "
            "sites had written it by hand in three variants (SNOW-672); this "
            "is the one that wins. Bottom spacing stays a caller decision — "
            "mb-2 when a subtitle follows, mb-6 when the body starts after."
        ),
        kind="components",
        partial="includes/_page_title.html",
        variants=PAGE_TITLE_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="eyebrow",
        label="Eyebrow",
        description=(
            "Small uppercase eyebrow heading used above bulletin sections, "
            "guide sections, and component-library panels. Accepts an "
            "element tag override so the partial fits both h2/h3 headings "
            "and standalone <p> labels."
        ),
        kind="components",
        partial="includes/_eyebrow.html",
        variants=EYEBROW_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="meta-cell",
        label="Meta cell label",
        description=(
            "Tiny uppercase label paragraph used as the top line of each "
            "metadata-strip cell on the bulletin page. Label-only emit — "
            "the caller owns the wrapping <div> and the value paragraph."
        ),
        kind="components",
        partial="includes/_meta_cell.html",
        variants=META_CELL_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="resort-meta-row",
        label="Resort meta row",
        description=(
            "Label + value/placeholder row used for the single-value resort "
            "metadata fields (operator, lifts, runs) in the resort-pin popup "
            "(SNOW-501). A blank value never omits the row — it renders a "
            "dashed-box placeholder: a public em-dash, or an explicit "
            '"Add <field>" hint when ``is_staff`` is set, cueing curation.'
        ),
        kind="components",
        partial="public/partials/_resort_meta_row.html",
        variants=RESORT_META_ROW_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="resort-facts",
        label="Resort facts block",
        description=(
            "The curated Resort columns the detail page stores but never "
            "rendered before SNOW-695 — operator, website, lifts, runs, "
            "piste, elevation range, typical season and notes. Unlike the "
            "resort-popup meta row, which keeps a dashed placeholder so "
            "missing curation stays visible to staff, this block omits an "
            "unset cell entirely and omits the whole container when every "
            "field is unset. Elevation and season each compose two nullable "
            "columns and degrade to a one-sided from/to reading."
        ),
        kind="components",
        partial="public/partials/_resort_facts.html",
        variants=RESORT_FACTS_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="resort-why-it-matters",
        label="Resort why-it-matters line",
        description=(
            'The curated one-sentence "why it matters" line for a resort '
            "(SNOW-542), shared by the resort page and the resort-pin popup. "
            "Blank is a first-class state with three branches: a dashed "
            "curation hint for staff, a register prompt for anonymous "
            "visitors, and nothing at all for a signed-in reader (who has no "
            "way to contribute copy yet)."
        ),
        kind="components",
        partial="public/partials/_resort_why_it_matters.html",
        variants=RESORT_WHY_IT_MATTERS_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="chip",
        label="Chip",
        description=(
            "Mono uppercase pill chip. Two variants: the default filled "
            "pill, which carries an opaque bg-tag and is the one to use "
            "inside a hazard bar (the EAWS tint takes no dark-mode "
            "override, so a transparent chip there drops to ~2:1); and "
            "the 'time' ghost pill, for the Day Risk Profile rows, which "
            "sit on bg-card. The dry/wet type variants were removed in "
            "SNOW-727 — the problem card's title bar already names the "
            "category."
        ),
        kind="components",
        partial="includes/_chip.html",
        variants=CHIP_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="sheet-header",
        label="Sheet header",
        description=(
            "Shared title + persistent × close control for the favourites, "
            "report, and downloads map fly-out sheets (SNOW-474; the × grew "
            "a 44×44 tap target and the title an optional title_class "
            "override in SNOW-645 review). The × carries the close_action "
            "value as its data-action attribute, so it triggers the owning "
            "sheet's existing delegated close listener. SNOW-658 adds an "
            "optional icon_template: the three UGC panels put the glyph of "
            "the roundel that opens them before the title, as the panel's "
            "one identity mark."
        ),
        kind="components",
        partial="includes/_sheet_header.html",
        variants=SHEET_HEADER_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="switch",
        label="Switch",
        description=(
            "iOS-style ON/OFF switch (SNOW-645) — a real checkbox input "
            "with role=switch, styled with Tailwind's peer variant, no JS. "
            "First use: the Manage downloads sheet's map-overlay control. "
            "Renders the control only — a caller supplies its own label "
            "alongside it."
        ),
        kind="components",
        partial="includes/_switch.html",
        variants=SWITCH_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="theme-preference",
        label="Theme preference",
        description=(
            "The settings page's light/dark/system radio group — the write "
            "side of the `theme` localStorage key that "
            "includes/theme_head.html reads. Three options rather than a "
            "switch because that key has three states, and writing only two "
            "would make 'follow the OS' unreachable once touched. 'System' "
            "is spelled by removing the key. Renders here in its "
            "no-JavaScript state: the server always sends 'System' checked "
            "because it cannot see localStorage."
        ),
        kind="components",
        partial="includes/_theme_preference.html",
        variants=THEME_PREFERENCE_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="map-overlay-toggle",
        label="Map overlay toggle",
        description=(
            "The 'Show X on the map' footer panel shared by the three map "
            "sheets (SNOW-658) — a label plus includes/_switch.html in a "
            "bg-tag box. A view control for the map BEHIND the sheet, not a "
            "row in the list the sheet is about; the owning JS module binds "
            "the switch by id and drives the matching window.pwa*Overlay "
            "bridge in static/js/map.js."
        ),
        kind="components",
        partial="includes/_map_overlay_toggle.html",
        variants=MAP_OVERLAY_TOGGLE_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="ugc-panel",
        label="UGC panel",
        description=(
            "The skeleton shared by the three map panels that manage a "
            "user's own data (SNOW-658) — downloads, favourites, field "
            "observations. Five parts, always in this order: header (the "
            "opening roundel's icon plus the title, at one size for all "
            "three), context strip (one line saying where the data lives), "
            "list (mono uppercase section label, then hairline-separated "
            "rows — never cards), one add-CTA, and the 'Display on the "
            "map' switch at the foot. The icon, rows and header-extra slots "
            "take template paths, since Django has no slot mechanism."
        ),
        kind="components",
        partial="includes/_ugc_panel.html",
        variants=UGC_PANEL_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="ugc-panel-row",
        label="UGC panel row",
        description=(
            "One row inside a UGC panel's list (SNOW-658). Five slots in a "
            "fixed order: an optional semantic rule, the label, a muted "
            "meta line, an optional mono value (measured quantities only), "
            "and right-aligned icon actions with the trash always last. "
            "Rows are not cards — a hairline separates them and nothing "
            "boxes them. A renameable row edits its label in place. Serves "
            "both a server-side loop and a JS-cloned <template>. SNOW-711 "
            "adds an optional trailing disclosure (see Row disclosure "
            "below), outside the action cluster because it is not an "
            "action, and puts the account page's favourite rows on this "
            "same row — the last surface still managing user data with an "
            "always-visible text field and an underlined 'Remove'. The "
            "name is inert text except on the three map panels whose rows "
            "are places, where it is a button that frames that place; "
            "rename stays the pencil's, so the two are still different "
            "controls."
        ),
        kind="components",
        partial="includes/_ugc_panel_row.html",
        variants=UGC_PANEL_ROW_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="row-disclosure",
        label="Row disclosure",
        description=(
            "The chevron at a UGC row's trailing edge (SNOW-711), filling "
            "includes/_ugc_panel_row.html's disclosure_template slot: it "
            "expands that row's own detail underneath it. A chevron rather "
            "than an arrow because it expands in place — the meaning "
            "_collapsible_panel.html already gives the mark — and a real "
            "<a href> rather than a button, so it navigates to the same "
            "detail with no JavaScript. Its one caller is the account "
            "page's favourite row, where it replaced a 'Details →' text "
            "link sitting beside two icon controls."
        ),
        kind="components",
        partial="includes/_row_disclosure.html",
        variants=ROW_DISCLOSURE_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="overflow-menu",
        label="Overflow menu",
        description=(
            "Reusable ellipsis kebab overflow menu (SNOW-645) — a trigger "
            "button opening a role=menu popover, dismissed on outside "
            "click or Escape by the delegated, instance-agnostic "
            "static/js/overflow_menu.js. The Open variants below are "
            "rendered pre-expanded (this page loads no interaction JS) so "
            "the menu contents are visible without a click. NO CURRENT "
            "CALLERS: its one use was the UGC panel rows' Rename/Remove, "
            "and SNOW-658 replaced that with visible icon controls on "
            'Hugo\'s design ("no ellipsis menu"). Kept as a primitive '
            "rather than deleted — the decision to retire it is not this "
            "ticket's to take."
        ),
        kind="components",
        partial="includes/_overflow_menu.html",
        variants=OVERFLOW_MENU_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="favourite-problem",
        label="Favourite problem",
        description=(
            "Altitude-annotated avalanche-problem card on the favourite detail "
            "card (SNOW-422): the shared _rating_block.html card plus an "
            "altitude-relevance chip (applies/above/below this location)."
        ),
        kind="components",
        partial="favourites/partials/_favourite_problem.html",
        variants=FAVOURITE_PROBLEM_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="weather-panel",
        label="Weather panel",
        description=(
            "One day's weather at one location (SNOW-761) — condition icon, "
            "En-GB label, hi/lo, snowfall, freezing level and the sunrise–"
            "sunset pair. Shared by the bulletin masthead, the resort page "
            "and the favourite card, which is why it carries no colour of "
            "its own: it drops into whichever card includes it. Every "
            "measurement group renders independently, so a partially-"
            "populated row shows what it has — Open-Meteo drops variables "
            "depending on which model backs the coordinates. The whole "
            "panel is absent when there is no row, which is the ordinary "
            "state for a historical date."
        ),
        kind="components",
        partial="includes/_weather_panel.html",
        variants=WEATHER_PANEL_VARIANTS,
        panel_layout="two-col",
    ),
    FoundationCategory(
        slug="weather-masthead",
        label="Weather masthead",
        description=(
            "Region 1 of the location forecast page (SNOW-789) — what the "
            "place is called, how high it is, and where a reader goes "
            "next. No weather at all: the day picker under it opens on "
            "today, and every number belongs to whichever day is "
            "selected. Both onward links live here rather than in a "
            "bottom nav, because 461 of the estate's 540 public locations "
            "are region centroids whose ONLY way on is the bulletin — a "
            "masthead naming just the resort would strand the majority of "
            "this page's visitors. They are accent text links, not "
            "buttons: a button pair under the heading competes with the "
            "picker for the reader's first action, and the first action "
            "here is choosing a day."
        ),
        kind="components",
        partial="includes/_weather_masthead.html",
        variants=WEATHER_MASTHEAD_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="weather-day-picker",
        label="Weather day picker",
        description=(
            "The week as NAVIGATION (SNOW-789) — seven cells carrying a "
            "weekday, a condition icon and a high/low, and nothing else. "
            "Freezing level, wind and the snowfall chip were in the strip "
            "this replaces and are gone: they are per-day facts, so they "
            "belong to the selected day's own line rather than to seven "
            "columns at once. EVERY cell is a radio, which is the change "
            "at the heart of the ticket — the old strip gave one only to "
            "the two days carrying an hourly series, so five columns were "
            "inert. Selection changes the border colour and nothing else, "
            "so stepping along the week cannot shift a cell's contents. "
            "The reveal beneath it is CSS-only and hand-written in "
            "``src/css/main.css``; each input/label pair keeps its own "
            "wrapper because ``peer-checked:`` is the general sibling "
            "combinator."
        ),
        kind="components",
        partial="includes/_weather_day_picker.html",
        variants=WEATHER_DAY_PICKER_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="weather-day-line",
        label="Weather day line",
        description=(
            "The selected day, stated once (SNOW-789) — date and "
            "condition, then the two figures that decide whether a plan "
            "holds: where it is freezing, and how long there is light. "
            "High and low are deliberately ABSENT; they are in the cell "
            "the reader just pressed and in the meteogram below, and a "
            "third statement is the duplication this ticket removed. "
            "Freezing level is tested ``is not None`` rather than for "
            "truthiness, because 0 m means freezing at the valley floor. "
            "Visibility lives on this row's own root element rather than "
            "on a wrapper: the reveal resolves to ``display: flex`` and "
            "this row IS the flex container, so a wrapper would leave it "
            "shrunk to its content with the hairline rule pulled in with "
            "it."
        ),
        kind="components",
        partial="includes/_weather_day_line.html",
        variants=WEATHER_DAY_LINE_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="weather-provenance",
        label="Weather provenance",
        description=(
            "One line closing the location forecast page (SNOW-789): when "
            "the row was last fetched, and who it came from. A TIME, not "
            "a date — today's row is rewritten in place across four "
            "fetches a day, so how recent it is is the whole point, and "
            "the day it belongs to is already on the day line above. It "
            "credits Open-Meteo rather than naming a forecast model: "
            "Open-Meteo picks the backing model per coordinate and does "
            "not report which, so a page naming one would be inventing "
            "it."
        ),
        kind="components",
        partial="includes/_weather_provenance.html",
        variants=WEATHER_PROVENANCE_VARIANTS,
        panel_layout="stack",
    ),
    FoundationCategory(
        slug="hourly-chart",
        label="Hourly chart",
        description=(
            "One day's weather in detail (SNOW-723) — three charts sharing "
            "one time axis, each with its own header, summary, vertical "
            "scale and resolution. Temperature (hourly) carries the air "
            "temperature and the freezing level on two scales; "
            "precipitation (hourly) carries new snow and rainfall on their "
            "own baselines; wind (three-hourly) carries speed, gusts and "
            "the direction the wind comes from. Both vertical scales are "
            "derived per day rather than fixed — the three variants are "
            "real observed days chosen to disagree with each other, and "
            "two of them fall entirely outside the domains the design was "
            "first drawn against. Distinct from the forecast chart above, "
            "which carries the shape of a WEEK; this one is a single day."
        ),
        kind="components",
        partial="includes/_hourly_chart.html",
        variants=HOURLY_CHART_VARIANTS,
        panel_layout="stack",
    ),
)


LIBRARY_GROUPS: tuple[LibraryGroup, ...] = (
    LibraryGroup("foundations", "Foundations", FOUNDATION_CATEGORIES),
    LibraryGroup("components", "Components", COMPONENT_CATEGORIES),
)


_BY_SLUG: dict[str, FoundationCategory] = {
    category.slug: category for group in LIBRARY_GROUPS for category in group.categories
}


def get_category(slug: str) -> FoundationCategory | None:
    """Return the library category matching ``slug``, or None if unknown.

    Walks every category across both groups (Foundations + Components),
    so callers don't need to know which group a slug belongs to.
    """
    return _BY_SLUG.get(slug)
