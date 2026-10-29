---
name: a-problem-card-states-its-own-level
description: panel-title-level "Considerable (3-)"; _day_risk_profile_adds_to_cards; region sheet peek holds the breadcrumb
status: current
last-reviewed: 2026-10-29
---

# A problem card states its own level

SNOW-1234, Hugo, 25 Oct 2026.

## Decision

Every problem card's title bar leads with its level in words and number —
"**Considerable (3-)** Dry avalanches · all day" — with the subdivision's
words after it for a screen reader. The Day Risk Profile renders only where
it says what no card can: an elevation split (ALBINA, Météo-France), or a
day with no problem cards (`views._day_risk_profile_adds_to_cards`). On the
map's region sheet the peek holds the tile, the name and the breadcrumb; the
bulletin's first heading names the day (a-sheet-names-its-day-in-its-section-headers.md).

## Why

The peek's tile and a one-row profile said the same level one under the
other. Replaying the 8,080 archived bulletins through `_build_day_windows`:
48% have a one-row profile (92% of SLF days), so the profile was a pure
repeat about half the time. The other half it carried a split the peek tile,
which shows the day's highest level, cannot.

SLF itself gives no day rating. Its bulletin and WhiteRisk put the level on
each problem's header with its window: "Low (1) Dry avalanches, whole day",
"Considerable (3) Wet-snow avalanches, as the day progresses". Saying the
level there tells a time split exactly as SLF does, so a profile row for a
time window repeats a card. ALBINA and Météo-France rate the danger by
height instead (63% and 56% of their days split), which no single card's
title can say, so for those the profile stays.

The peek swap is Hugo's ("I want the peek to be meaningful"): at its lowest
the sheet says what was tapped and where it is. The day still reads at half.

Options weighed, with mock-ups: keep as is; drop the profile on one-row
days; the level word in the peek; one plain line on a one-row day.

## Consequences

- The profile's flat-split caption ("Problem type changes as the day
  progresses") now shows only alongside an elevation split; on an SLF split
  day the cards' titles carry the change.
- The fidelity probes for a time window and a subdivision read the card's
  title bar (`tests/sentinels/fidelity.py`).
- The region sheet's blank peek copy renders a blank breadcrumb line of the
  same height: the page holds no sub-region names to fill it from, and the
  peek must not grow when the body lands.
