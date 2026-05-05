"""
public/design_tokens.py — Component-library registry.

Hand-curated catalogue of everything the design-system page at
``/_components/`` renders. Two top-level groups, both built from the same
``FoundationCategory`` dataclass:

1. **Foundations** — design tokens, mirroring src/css/main.css's
   ``@theme {}`` block. ``kind`` is one of ``"swatches"``, ``"typography"``,
   ``"radius"``, ``"layout"``, ``"icons"``. Each entry's ``tokens`` field
   carries ``Token`` and/or ``IconToken`` instances. The
   registry-vs-CSS sync check in ``public/checks.py`` walks this group.

2. **Components** — rendered HTML components. ``kind`` is ``"components"``.
   Each entry sources its render content from its ``partial`` template
   path and a ``variants`` tuple of context dicts; the renderer iterates
   the variants and ``{% include partial with **variant.context %}`` for
   each. Variant fixtures live in ``public/_component_fixtures.py`` so
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

from public._component_fixtures import DAY_WINDOWS_VARIANTS, WEATHER_HEADER_VARIANTS


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
            The sync check in ``public/checks.py`` filters to ``Token``
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
            paired variants like the weather-header day/night matrix.

    """

    slug: str
    label: str
    description: str
    kind: str
    tokens: tuple[Token | IconToken, ...] = ()
    partial: str | None = None
    variants: tuple[dict[str, Any], ...] = ()
    panel_layout: str = "stack"


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
            Token("--color-text-3", "Tertiary", "#6e6b65", "#8a867e"),
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
        slug="accent",
        label="Accent",
        description="Primary action colour and focus ring.",
        kind="swatches",
        tokens=(
            Token("--color-accent", "Accent", "#2563eb", "#3b82f6"),
            Token("--color-accent-hover", "Accent hover", "#1d4ed8", "#60a5fa"),
            Token("--color-accent-text", "Accent text", "#ffffff", "#ffffff"),
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
        tokens=(
            Token("--color-eaws-low", "Low", "#ccff66", None),
            Token("--color-eaws-moderate", "Moderate", "#ffff00", None),
            Token("--color-eaws-considerable", "Considerable", "#ff9900", None),
            Token("--color-eaws-high", "High", "#ff0000", None),
            Token("--color-eaws-very-high", "Very high", "#ff0000", None),
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
        slug="radius",
        label="Radius",
        description="Corner-radius scale for cards, tags and pills.",
        kind="radius",
        tokens=(
            Token("--radius-card", "Card", "16px", None),
            Token("--radius-tag", "Tag", "8px", None),
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
            Token("--width-card-mobile", "Card (mobile)", "390px", None),
            Token("--width-grid-max", "Grid (max)", "1200px", None),
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
            # ---- Meteocons weather icons (MIT, see weather/LICENSE.md) ----
            # 23 entries: 11 day/night pairs + cloudy (no diurnal variant). Granularity
            # is higher than WEATHER_BUCKETS — drizzle and light/moderate/heavy rain
            # all map to the "rain" bucket — so future SNOW-98 wiring can choose the
            # right asset by WMO code rather than bucket alone.
            IconToken(
                "clear-day", "Clear · day", "icons/weather/clear-day.svg", "Weather"
            ),
            IconToken(
                "clear-night",
                "Clear · night",
                "icons/weather/clear-night.svg",
                "Weather",
            ),
            IconToken(
                "partly_cloudy-day",
                "Partly cloudy · day",
                "icons/weather/partly_cloudy-day.svg",
                "Weather",
            ),
            IconToken(
                "partly_cloudy-night",
                "Partly cloudy · night",
                "icons/weather/partly_cloudy-night.svg",
                "Weather",
            ),
            IconToken("cloudy", "Cloudy", "icons/weather/cloudy.svg", "Weather"),
            IconToken("fog-day", "Fog · day", "icons/weather/fog-day.svg", "Weather"),
            IconToken(
                "fog-night", "Fog · night", "icons/weather/fog-night.svg", "Weather"
            ),
            IconToken(
                "drizzle-day",
                "Drizzle · day",
                "icons/weather/drizzle-day.svg",
                "Weather",
            ),
            IconToken(
                "drizzle-night",
                "Drizzle · night",
                "icons/weather/drizzle-night.svg",
                "Weather",
            ),
            IconToken(
                "light_rain-day",
                "Light rain · day",
                "icons/weather/light_rain-day.svg",
                "Weather",
            ),
            IconToken(
                "light_rain-night",
                "Light rain · night",
                "icons/weather/light_rain-night.svg",
                "Weather",
            ),
            IconToken(
                "moderate_rain-day",
                "Moderate rain · day",
                "icons/weather/moderate_rain-day.svg",
                "Weather",
            ),
            IconToken(
                "moderate_rain-night",
                "Moderate rain · night",
                "icons/weather/moderate_rain-night.svg",
                "Weather",
            ),
            IconToken(
                "heavy_rain-day",
                "Heavy rain · day",
                "icons/weather/heavy_rain-day.svg",
                "Weather",
            ),
            IconToken(
                "heavy_rain-night",
                "Heavy rain · night",
                "icons/weather/heavy_rain-night.svg",
                "Weather",
            ),
            IconToken(
                "light_snow-day",
                "Light snow · day",
                "icons/weather/light_snow-day.svg",
                "Weather",
            ),
            IconToken(
                "light_snow-night",
                "Light snow · night",
                "icons/weather/light_snow-night.svg",
                "Weather",
            ),
            IconToken(
                "moderate_snow-day",
                "Moderate snow · day",
                "icons/weather/moderate_snow-day.svg",
                "Weather",
            ),
            IconToken(
                "moderate_snow-night",
                "Moderate snow · night",
                "icons/weather/moderate_snow-night.svg",
                "Weather",
            ),
            IconToken(
                "heavy_snow-day",
                "Heavy snow · day",
                "icons/weather/heavy_snow-day.svg",
                "Weather",
            ),
            IconToken(
                "heavy_snow-night",
                "Heavy snow · night",
                "icons/weather/heavy_snow-night.svg",
                "Weather",
            ),
            IconToken(
                "thunder-day",
                "Thunder · day",
                "icons/weather/thunder-day.svg",
                "Weather",
            ),
            IconToken(
                "thunder-night",
                "Thunder · night",
                "icons/weather/thunder-night.svg",
                "Weather",
            ),
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
        slug="weather-header",
        label="Weather header",
        description=(
            "Bulletin-page weather header — region + sub-region + date + "
            "weather hero, rendered against every icon-bucket × time-of-day "
            "combination plus the no-snapshot fallback."
        ),
        kind="components",
        partial="includes/bulletin_header.html",
        variants=WEATHER_HEADER_VARIANTS,
        panel_layout="two-col",
    ),
    FoundationCategory(
        slug="day-windows",
        label="Day windows",
        description=(
            "Per-window EAWS rating panel — one row per validTimePeriod with "
            "a coloured danger-level tile, label and time-window pill. "
            "Variants cover the all-day case (≈95% of bulletins) across "
            "every danger level plus a realistic split-day layout."
        ),
        kind="components",
        partial="includes/day_windows.html",
        variants=DAY_WINDOWS_VARIANTS,
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
