# How to Read the Avalanche Bulletin

The Swiss avalanche bulletin is published daily by the WSL Institute for Snow and Avalanche Research (SLF) and covers the Swiss Alps and Jura. It is an informed forecast — not a guarantee — and cannot replace an on-site assessment. The information here explains how the bulletin is structured, what each field means, and what the data reveals about how the bulletin behaves in practice.

---

## The danger level

The bulletin uses the five-point European avalanche danger scale.

| Level | Name | Characteristics |
|---|---|---|
| 1 | Low | Generally favourable. Avalanches only in isolated cases on extremely steep slopes. |
| 2 | Moderate | Mostly favourable. Avalanches possible on steep slopes with the indicated aspects and elevation. |
| 3 | Considerable | Critical. Avalanches can be easily triggered on steep slopes; natural avalanches and remote triggering possible. |
| 4 | High | Very critical. Natural avalanches likely; easily triggered on many steep slopes. |
| 5 | Very high | Extraordinary. Numerous very large natural avalanches expected. |

Danger increases disproportionately — the jump from level 2 to 3 is far more significant than the numbers suggest. Around 50% of avalanche fatalities occur at level 3, 30% at level 2.

In the data covering October 2025 – May 2026, the distribution across 2,159 bulletins was: low 20%, moderate 54%, considerable 35%, high 5%, very high <1%. (Some bulletins carry two danger levels for different times of day; these percentages reflect the all-day rating.)

---

## Subdivisions (–, =, +)

When there is a **moderate or higher danger of dry-snow avalanches** (level 2 and above), the SLF subdivides each level into three positions:

- **–** (minus) — towards the bottom end of the level
- **=** (neutral) — more or less in the middle
- **+** (plus) — towards the top end of the level

This reflects the fact that avalanche danger is a continuous spectrum, not a step function. A rating of `3–` sits just above the level 2/3 boundary; `3+` is just below level 4.

**Subdivision only applies to dry-snow danger.** It is never assigned to:
- Level 1 (low) — confirmed across all 2,159 bulletins in the dataset; not a single low rating carries a subdivision
- Wet-snow hazards — wet snow and gliding snow are not assessed with subdivisions
- The `later` danger rating — the afternoon period is always wet, so subdivision never appears there

A missing subdivision means "not applicable", not "neutral".

---

## Hazard categories: dry and wet

The bulletin assesses dry and wet avalanche hazards separately. These are fundamentally different in cause, location, and behaviour.

### Dry avalanches

Caused by structural weaknesses in the snowpack — new snow loading, wind-deposited slabs, or persistent buried weak layers. Dry hazard is always present **throughout the day** (it does not emerge or disappear with temperature). In the dataset, dry hazards account for 76% of all aggregation entries and appear exclusively as `all_day`.

Dry problems always carry a specific **lower elevation bound** (the altitude above which the hazard applies) and one or more **aspects** (compass directions of affected slopes). In 100% of cases in the dataset, a dry problem has both an aspect list and a lower elevation bound, and never an upper bound. Dry hazard lives above a threshold, not below one.

The dry problem types are:

| Problem type | Description |
|---|---|
| **New snow** | Recent snowfall loading the existing snowpack. Typically widespread across all aspects. |
| **Wind slab** | Wind-deposited snow on leeward slopes, gullies and bowls. Localised but often hard to spot when buried. |
| **Persistent weak layers** (old snow) | Buried weak layers — faceted crystals, depth hoar, surface hoar — that can remain reactive for weeks. Dangerous because avalanche prone locations are barely recognisable even to trained eyes. Accounts for 64% of dry aggregations in the dataset — the dominant winter hazard. |
| **No distinct avalanche problem** | No clear dry hazard pattern. Not equivalent to no danger — any avalanche type remains possible. Typically used at level 1. |

### Wet avalanches

Caused by liquid water weakening the snowpack, whether from melt (solar radiation, warm air) or rain. Wet hazard can be present all day or emerge progressively **as the day progresses** — the only hazard type that is time-dependent.

Wet problems are spatially more diffuse than dry ones. In the dataset:
- At moderate wet danger: 70% of cases have specified aspects, typically with an **upper** elevation bound (the hazard exists *below* the freeze/thaw or rain/snow line)
- At considerable wet danger: 68% have aspects, always upper bound
- At `later` timing: only 42% have aspects — the afternoon hazard is increasingly treated as region-wide

The wet problem types are:

| Problem type | Description |
|---|---|
| **Wet snow** | Snowpack weakened by melt or rain. Mainly natural avalanches. Solar radiation and aspect determine which slopes are affected first. Timing is critical — east-facing slopes warm earlier than west-facing ones. |
| **Gliding snow** | The entire snowpack slides on a smooth substrate (grass, rock). Almost exclusively natural; human triggering is virtually impossible. Glide cracks are a warning sign but release timing is unpredictable. Can occur any time of day, including overnight in mid-winter. |

---

## Elevation and aspect

For each hazard block, the bulletin specifies *where* the danger applies.

**Elevation** is given as a lower bound for dry hazards ("above 2400 m") or an upper bound for wet hazards ("below 2400 m"). These are approximate — conditions change gradually across the boundary, not sharply. A ±200 m margin is reasonable.

**Aspects** are compass directions of affected slopes. The convention is: a north-facing slope falls to the north (you stand at the top, facing north, and the slope drops away below you). North-facing slopes receive no direct solar radiation in mid-winter; south-facing slopes receive regular sunshine year-round.

When the bulletin cites specific aspects and an elevation, the stated danger level applies to slopes satisfying *both* criteria. A common rule of thumb for slopes outside the stated zone: assume one danger level lower. This is a guideline, not a guarantee.

When no aspects or elevation are given for a wet hazard, the danger is region-wide and not spatially constrained.

---

## How the day evolves

Most bulletins describe a single, stable hazard picture for the whole day. In 8% of bulletins (176 of 2,159 in the dataset), a second danger level applies *later in the day* — exclusively a wet-snow hazard emerging as temperatures rise.

**All bulletins where the afternoon differs from the morning involve wet snow or gliding snow.** Dry hazards never appear as a `later` entry. This reflects the physics: dry snowpack problems are weather-independent within a single day, while wet hazards are driven by daytime heating.

The SLF bulletin reflects this with two danger ratings when the day evolves. The morning rating shows the all-day dry baseline; the afternoon rating shows the wet hazard that develops. The stated danger level is normally based on the morning situation.

### Typical patterns

| Pattern | Frequency | Description |
|---|---|---|
| Dry only, all day | 70% | A single dry hazard with no wet component |
| Dry and wet simultaneously | 16% | Both dry (above an elevation) and wet (below it) are active all day — often after rain at lower elevations with new snow higher up |
| Dry all day, wet emerging later | 8% | Morning is dominated by dry hazard; afternoon brings wet snow as temperatures rise |
| Wet only, all day | 5% | No dry hazard — typically low-elevation regions (Jura, Prealps) or late spring |

When the afternoon brings a *higher* danger level than the morning, timing is critical: aim to complete exposed terrain before the snow softens. On east-facing slopes this may mean before midday; on west-facing slopes it may be mid-afternoon. The bulletin cannot pin down the exact transition time — it depends on aspect, elevation, and conditions on the day.

---

## When multiple problem types appear together

A single hazard block may cite two problem types — for example, `wind slab + persistent weak layers`. This means both contribute to the danger in the same area, at the same elevation, on the same aspects. In the dataset, when two problem types are grouped together, they always share identical spatial constraints and description text. They are two labels for one danger zone, not two independent hazards with different locations.

Across 2,159 bulletins, multiple problem types within one hazard block occur in 1,019 cases (47%). The most common pairing is wind slab + persistent weak layers.

---

## The narrative sections

Each bulletin includes longer text in four sections, updated once daily in the evening:

**Snowpack** — describes the layering and stability of the snowpack across the region. Explains why the danger is as it is. Mentions observed avalanche activity. Most directly useful for planning.

**Weather forecast** — covers the next 24 hours: fresh snow amounts, temperature, wind. The danger assessment is built on this forecast; if actual weather deviates, the danger may differ.

**Weather review** — summarises what actually happened in the preceding period. Provides context for why the snowpack is in its current state.

**Outlook (tendency)** — indicative assessment for the two days after the bulletin's validity period. Less detailed but useful for multi-day planning.

---

## Limitations

The bulletin is a regional forecast. It cannot assess individual slopes — it can only indicate which types of terrain (aspect, elevation, steepness) carry the indicated danger. Local conditions always require on-site assessment.

Boundaries between regions, elevation bands, and aspects are gradual, not sharp. A slope near the boundary of the stated zone may sit in a transition area where either the more or less favourable situation applies.

The bulletin applies exclusively to unsecured terrain away from prepared pistes and marked ski runs.

---

*Data findings in this document are derived from analysis of 2,159 Swiss avalanche bulletins (October 2025 – May 2026) from the SLF CAAML API. Definitions and qualitative descriptions are drawn from the SLF Avalanche Bulletin Interpretation Guide, Edition November 2025.*
