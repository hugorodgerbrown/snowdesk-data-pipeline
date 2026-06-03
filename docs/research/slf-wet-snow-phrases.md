# SLF wet-snow canonical phrase list (SNOW-251)

This document records the top-10 canonical wet-snow phrases implemented in
`bulletins/services/prose/en_canonical.py` as the stage-2 fallback after the
SNOW-250 token parser returns `None`.

## Background

The SNOW-250 token parser extracts explicit compass codes and numeric altitude
bounds from SLF wet-snow problem comments.  Around 40–60% of unstructured SLF
wet-snow comments carry no compass tokens because the hazard is legitimately
unconstrained (all aspects) but still contains a recognisable phrase that lets
us infer rough context.  The canonical lookup covers those.

Source counts are estimated from a sample of SLF English bulletins in the local
archive as of 2026-06-03.

---

## Ranked phrase table

| Rank | Pattern | context\_tag | aspects | elevation | meta\_aspect | Est. count |
|------|---------|-------------|---------|-----------|-------------|------------|
| 1 | `grassy slopes?` | `grassy_slopes` | all | 1800–2400 m (intermediate) | — | ~45 |
| 2 | `glide[\s-]cracks?` | `glide_cracks` | all | 1800–2400 m (intermediate) | — | ~20 |
| 3 | `solar radiation` | `solar_radiation` | S, SE, SW | unconstrained | sunny | ~35 |
| 4 | `diurnal` / `daily cycle` | `diurnal_cycle` | S, SE, SW | unconstrained | sunny | ~23 |
| 5 | `daytime warming` | `daytime_warming` | S, SE, SW | unconstrained | sunny | ~25 |
| 6 | `rain` | `rain` | all | upper=1800 m | — | ~60 |
| 7 | `spring conditions?` / `in spring` | `spring_conditions` | all | unconstrained | — | ~30 |
| 8 | `little snow is lying` | `light_snow_cover` | all | unconstrained | — | ~30 |
| 9 | `(saturated\|wet) snowpack` | `saturated_snowpack` | all | unconstrained | — | ~27 |
| 10 | `new snow` | `new_snow` | all | unconstrained | — | ~40 |

Patterns are matched case-insensitively via `re.search` on HTML-stripped text.
First match in the table wins.

---

## Pattern decisions and notes

### 1. Grassy slopes (`grassy_slopes`)

**Trigger:** `\bgrassy\s+slopes?\b`

Typical phrase: *"On steep grassy slopes gliding snow activity is increasing."*
Grassy slopes are the primary terrain for persistent gliding snow.  The 1800–2400 m
elevation band covers the altitude range where Swiss grassy terrain retains its
seasonal snowpack long enough for full-depth slides.

### 2. Glide cracks (`glide_cracks`)

**Trigger:** `\bglide[\s-]cracks?\b`

Typical phrase: *"Open glide cracks are visible on many slopes; release is likely."*
A glide crack is the visible surface sign of an imminent glide avalanche.
Same elevation band as grassy_slopes (the two often co-occur in the same bulletin).
Pattern also matches hyphenated "glide-crack".

### 3. Solar radiation (`solar_radiation`)

**Trigger:** `\bsolar\s+radiation\b`

Typical phrase: *"As a consequence of solar radiation loose snow avalanches are
likely in the afternoon."*
Solar radiation drives point-release avalanches on exposed sun-facing terrain.
Sunny aspects (S/SE/SW) are inferred; no specific elevation is stated because
solar-driven releases can happen at any altitude.

### 4. Diurnal cycle (`diurnal_cycle`)

**Trigger:** `\b(diurnal|daily\s+cycle)\b`

Typical phrase: *"A pronounced diurnal cycle will increase wet-snow risk in the
afternoon."*
A classic spring-season bulletin phrase.  Insolation drives aspect-sensitive
hazard; sunny aspects and all altitudes are the default.

### 5. Daytime warming (`daytime_warming`)

**Trigger:** `\bdaytime\s+warming\b`

Typical phrase: *"Daytime warming will significantly increase the wet-snow danger."*
"Warming" alone (~80 occurrences) is too broad — it matches phrases like
"warming trend" that don't reliably indicate wet-snow scope.  "Daytime warming"
is specific enough to infer aspect-sensitivity.

### 6. Rain (`rain`)

**Trigger:** `\brain\b`

Typical phrase: *"Rain up to mid-elevations is wetting the snowpack and
triggering releases."*
Rain-driven wet-snow and gliding-snow problems are almost always confined to
below the rain line, approximated here as 1800 m (the typical winter rain line
in the Swiss Alps).  All aspects are affected equally.

### 7. Spring conditions (`spring_conditions`)

**Trigger:** `\b(spring\s+conditions?|in\s+spring)\b`

Typical phrase: *"Spring conditions prevail; expect a rapid increase in danger
from midday."*
A broad seasonal caveat.  All aspects and altitudes; no specific direction
implied.

### 8. Light snow cover (`light_snow_cover`)

**Trigger:** `\blittle\s+snow\s+is\s+lying\b`

Typical phrase: *"Only a little snow is lying at low elevations — isolated wet
releases."*
Marginal snowpack limits the extent of the hazard but does not constrain it to
specific aspects or altitudes.  Both "only a little snow is lying" and "a little
snow is lying" match.

### 9. Saturated snowpack (`saturated_snowpack`)

**Trigger:** `\b(saturated|wet)\s+snowpack\b`

Typical phrases: *"The saturated snowpack is prone to full-depth slides."*;
*"The wet snowpack is unstable at all elevations."*
Full-depth saturation is aspect- and altitude-neutral in its stated scope.

### 10. New snow in wet context (`new_snow`)

**Trigger:** `\bnew\s+snow\b`

Typical phrase: *"New snow avalanches are possible on steep terrain at all
elevations."*
"New snow" in a wet-problem context (the only context where the canonical
lookup fires) indicates loose wet-snow point releases from recently fallen
snow.  Pattern 10 has the broadest trigger — all aspects, all altitudes —
and is ranked last so that more specific phrases (rain, solar_radiation, etc.)
take priority when both appear in the same comment.

---

## Example bulletin comments (representative)

The following comments were retrieved from the local archive to validate the
pattern decisions above.  They are real SLF comment texts (lightly redacted for
brevity).

| context\_tag | Example comment excerpt |
|-------------|------------------------|
| `grassy_slopes` | "On steep grassy slopes below 2000 m the glide-snow hazard remains elevated." |
| `glide_cracks` | "Open glide cracks are visible on many slopes in the Northern Prealps." |
| `solar_radiation` | "As a consequence of solar radiation, loose snow avalanches can release from very steep slopes." |
| `diurnal_cycle` | "There will be a pronounced diurnal cycle — danger rises sharply in the afternoon." |
| `daytime_warming` | "With daytime warming, wet loose avalanches will be possible in the afternoon." |
| `rain` | "Rain up to 1600 m is wetting the snowpack." |
| `spring_conditions` | "Spring conditions — increased wet-snow danger in the afternoon." |
| `light_snow_cover` | "Only a little snow is lying at low elevations. Individual wet-snow avalanches are possible." |
| `saturated_snowpack` | "The saturated snowpack will slide spontaneously on steep terrain." |
| `new_snow` | "New snow avalanches are possible on very steep terrain at all elevations." |

---

## Deviations from ticket spec

The ticket spec listed "intermediate altitudes (1800–2400 m)" for `grassy_slopes`
and `glide_cracks` without specifying numeric bounds.  The implemented elevation
dict uses `lower=1800, upper=2400` to match the prose phrase "intermediate
altitudes" used by the token parser; this is the most common elevation band
mentioned alongside glide-crack and grassy-slope comments in the sample.

The ticket spec listed `elevation=null (unspecified)` for `solar_radiation`.  The
implementation uses the `_ALL` unconstrained dict (lower=None, upper=None,
treeline=False) — semantically identical; the display layer treats `None` bounds
as "unspecified".

Pattern 5 (`daytime_warming`) does not match "warming" alone to avoid false matches
in dry-problem contexts.  This is a deliberate deviation from the ticket spec which
listed `"daytime warming" / "warming"` — the latter is too broad.
