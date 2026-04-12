# Aspect Rose — Technical Spec

## What this is

A Django template filter that renders an inline SVG compass rose showing active avalanche aspects. Generated server-side on the fly; no static assets.

## Location

Add to: `templatetags/card_tags.py`

## Data

Input is `p.aspects` — a Python list of strings from the CAAML bulletin data.

Valid values: `"N"`, `"NE"`, `"E"`, `"SE"`, `"S"`, `"SW"`, `"W"`, `"NW"`

Examples:
- `["NE", "E", "SE", "S"]` — typical wind slab
- `["N", "NE", "NW"]` — north-facing persistent weak layer
- `["N", "NE", "E", "SE", "S", "SW", "W", "NW"]` — all aspects

## Filter signature

```python
@register.filter
def aspect_rose(aspects, size=36):
    ...
```

Usage in template: `{{ p.aspects|aspect_rose|safe }}`

Custom size: `{{ p.aspects|aspect_rose:32|safe }}`

## Geometry

- 8 equal wedge segments, each spanning 45°
- Segment order clockwise from north: N, NE, E, SE, S, SW, W, NW
- Centre point: `cx = cy = size / 2`
- Outer radius: `r = size / 2 - 2` (2px inset for stroke clearance)
- Each wedge: `M cx,cy  L x1,y1  A r,r 0 0,1 x2,y2  Z`
  - `x1,y1` = start of arc (leading edge of segment)
  - `x2,y2` = end of arc (trailing edge of segment)
  - Half-angle per segment: `HALF = π / 8` (22.5°)
  - SVG angle mapping: north = −90° in standard Cartesian coords

Angle map (SVG degrees, clockwise from east = 0°):

| Aspect | SVG centre angle |
|--------|-----------------|
| N      | −90°            |
| NE     | −45°            |
| E      |   0°            |
| SE     |  45°            |
| S      |  90°            |
| SW     | 135°            |
| W      | 180°            |
| NW     | 225°            |

## Colours

**Active segment fill:** `#BA7517` (amber 600 — matches card's warm palette)

**Inactive segment fill:** `#E8E6E0` (hardcoded warm grey)

> **Critical:** Do NOT use CSS variables (`var(--...)`) for fill colours inside
> server-side generated SVG. CSS variables do not resolve in SVG `fill`
> attributes when the SVG is generated as a string in Python. Hardcode both
> values.

**Segment separator stroke:** `var(--color-background-primary)`, width `1.5`
(this IS safe as a stroke attribute — it separates segments cleanly against
the card background)

## Centre dot

- Radius: `r * 0.18` (proportional to overall size)
- Fill: `#FFFFFF` (hardcoded white — not a CSS variable)
- Sits on top of all wedges

## No N label

Do not render an "N" label. The rose is small enough that a label adds
clutter rather than clarity.

## Accessibility

```
aria-label="Aspects: N, NE, E"
role="img"
```

Populate `aria-label` from the active aspects list. If empty: `"Aspects: none"`.

## SVG output structure

```svg
<svg xmlns="http://www.w3.org/2000/svg"
     width="36" height="36" viewBox="0 0 36 36"
     aria-label="Aspects: NE, E, SE, S" role="img">
  <!-- 8 wedge paths, inactive first, active on top or interleaved -->
  <path d="M18,18 L..." fill="#E8E6E0" stroke="var(--color-background-primary)" stroke-width="1.5"/>
  <path d="M18,18 L..." fill="#BA7517" stroke="var(--color-background-primary)" stroke-width="1.5"/>
  <!-- ... -->
  <!-- Centre dot last (renders on top) -->
  <circle cx="18" cy="18" r="3.2" fill="#FFFFFF"/>
</svg>
```

## Template usage

The rose sits in `.problem-tag` between the problem icon and the problem label:

```django-html
{% load card_tags %}

<div class="problem-tag">
    {% with icon_file=p.problem_type|hazard_icon %}
        {% if icon_file %}
            <div class="problem-icon">
                <img src="{% static icon_file %}" alt="{{ p.label }}">
            </div>
        {% endif %}
    {% endwith %}

    {% if p.aspects %}
        <div class="problem-rose">
            {{ p.aspects|aspect_rose|safe }}
        </div>
    {% endif %}

    <span class="problem-label">{{ p.label }}</span>

    {% if p.time_period_label %}
        <span class="problem-period">{{ p.time_period_label }}</span>
    {% endif %}
</div>
```

## CSS

```css
.problem-rose {
    display: flex;
    align-items: center;
    flex-shrink: 0;
}

.problem-rose svg {
    display: block;
}
```

## Data wiring

`p.aspects` must be populated in `_panel_problems()`. Confirm the CAAML source
key and add to the problem dict if not already present:

```python
problems.append({
    ...
    "aspects": entry.get("aspects", []),   # confirm key name in raw CAAML
    ...
})
```

## What NOT to do

- Do not use `var(--...)` for `fill` attributes in Python-generated SVG strings
- Do not add an "N" label
- Do not use static image files — the rose must be generated inline
- Do not add hover states or interactivity — it is a display-only indicator
