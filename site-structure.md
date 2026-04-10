# Snowdesk Site Structure

## Site Architecture

Two routes, server-rendered, reading from a database of avalanche bulletins.

### Route: `/` (Home)

- Queries the DB for the most recent bulletin issue date
- Fetches all regions from that issue
- Redirects to a random region's bulletin page — acts purely as an entry point

### Route: `/{zone}` (Bulletin Viewer)

- Dynamic route accepting a URL slug (e.g. `/valais`, `/haut-val-de-bagnes`)
- Optional query param `?id=<bulletinId>` to view a specific historical bulletin
- Fetches up to 60 recent bulletins (≈30 days) for the zone, newest first
- Renders the selected (or latest) bulletin, with prev/next navigation links

## Page Layout (Bulletin Viewer)

Top-to-bottom, a single bulletin page renders:

1. **Masthead** — wordmark ("Snowdesk"), issued date/time, region name as large headline, tagline, prev/next navigation with timestamps
2. **Verdict Banner** — colour-coded card (green/amber/red) showing danger level, overall verdict (GO / CAUTION / AVOID), and summary paragraph
3. **Outlook** — left-bordered card with a 24h+ forecast paragraph
4. **Activity Ratings** — 3-column grid (collapses to 1 on mobile): On Piste, Off Piste, Ski Touring — each with a rating label + notes
5. **Key Hazards** — bulleted list of hazard descriptions
6. **Weather** — two grids:
   - **Temps**: 4-column (Summit, Mid, Resort, Freezing Level)
   - **Details**: 2-column (Wind, Visibility, New Snow 24h, Base Depth)
7. **Related Regions** — inline list of other regions covered by the same bulletin
8. **Footer** — copyright + data source attribution

## Data Shape (per bulletin)

Each bulletin view expects this structure from the AI-generated summary:

| Field                                | Type             | Notes                                                                                       |
| ------------------------------------ | ---------------- | ------------------------------------------------------------------------------------------- |
| `date`, `validFrom`, `validTo`, `nextUpdate` | string   | Temporal metadata                                                                           |
| `overallVerdict`                     | string           | e.g. "GO", "CAUTION", "AVOID"                                                              |
| `verdictColour`                      | enum             | `green` / `amber` / `red`                                                                   |
| `dangerLevel`                        | string           | e.g. "Level 3"                                                                              |
| `summary`                            | string           | Paragraph overview                                                                          |
| `outlook`                            | string           | 24h+ forecast                                                                               |
| `onPiste`, `offPiste`, `skiTouring`  | `{ rating, notes }` | Activity ratings                                                                         |
| `keyHazards`                         | string[]         | Bulleted list                                                                               |
| `bestBets`                           | string[]         | Present in data, not currently rendered                                                     |
| `weather`                            | object           | `summitTemp`, `midTemp`, `resortTemp`, `freezingLevel`, `wind`, `visibility`, `newSnow24h`, `baseDepth` |

## Navigation Model

```
/ (home) → redirect → /{randomZone}
/{zone} ← prev/next → /{zone}?id={bulletinId}
```

No cross-zone navigation in the UI — users change zone by editing the URL.

## Design System

### Colour Palette (warm earth tones)

- Background: `#f5f0e8` (cream)
- Text: `#1a1612` (ink), `#4a4035` (mid), `#8a7d6e` (light), `#c5b9a8` (faint/borders)
- Accent: `#c4722a` (burnt orange)
- Risk colours: `#2d4a3e` (green/safe), `#7a5c1e` (gold/warn), `#8b2e2e` (red/danger)

### Typography (3 fonts)

- Display/headings: Playfair Display (serif)
- Body: DM Sans (sans-serif)
- Labels/timestamps: DM Mono (monospace, small-caps, uppercase)

### Layout

- Max content width: 780px, centred
- Responsive padding via `clamp()`
- Grids collapse from multi-column to single-column at 540px breakpoint
- Full-viewport-height flex wrapper to pin footer to bottom

### Background Decorations (fixed, low-opacity, non-interactive)

- Topographic grid: repeating 40px lines in alpine green
- Mountain silhouette: SVG path pinned to bottom of viewport

### Styling Notes

- No external images or icon fonts — all visuals are CSS + inline SVG
- Cards: 16px padding, 1px border, 2px border-radius, semi-transparent cream background
- Verdict banner backgrounds use the risk colour at ~6% opacity with a left border
- Labels use monospace font in small-caps / uppercase at 9–11px
- Region headline uses responsive sizing: `clamp(26px, 5vw, 40px)`
