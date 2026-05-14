# CAAML v6.0 Bulletin EAWS — Schema Reference

**Source:** <http://caaml.org/Schemas/BulletinEAWS/v6.0/json/CAAMLv6_BulletinEAWS.json>
**Specification:** JSON Schema (draft-07)

This document describes the structure of SLF avalanche bulletins as defined
by the CAAMLv6 BulletinEAWS schema, annotated with how fields appear in
this project's stored data.

---

## Storage note

The SLF API returns bare bulletin objects. This pipeline wraps each one in a
GeoJSON Feature envelope before storage:

```json
{
  "type": "Feature",
  "geometry": null,
  "properties": { "<bulletin fields>" }
}
```

Everything below describes the bulletin fields found inside `properties`.

---

## Top-level bulletin fields

| Field | Type | Description |
|---|---|---|
| `bulletinID` | `string`, required | Unique identifier for the bulletin (UUID assigned by SLF). |
| `lang` | `string` (2 chars) | ISO 639-1 language code. SLF publishes in `"de"`, `"fr"`, `"it"`, and `"en"`. |
| `publicationTime` | `datetime` (ISO 8601, UTC) | When the bulletin was published to the public by SLF. |
| `validTime` | `object` | The time window during which this bulletin is valid. See [validTime](#validtime). |
| `nextUpdate` | `datetime` (ISO 8601, UTC) | When the next bulletin will be published. Usually matches `validTime.endTime`. |
| `unscheduled` | `boolean` | `true` if this is an unscheduled (out-of-cycle) bulletin, e.g. issued in response to a sudden change in conditions. |
| `highlights` | `string` (optional) | Short text highlighting an exceptionally dangerous situation. Only present in extreme scenarios. |

---

## `regions` — array of Region objects

Each region identifies a micro-region for which the bulletin is valid.
Region IDs follow the EAWS region scheme (e.g. `"CH-1213"` = Hohgant).

| Field | Type | Description |
|---|---|---|
| `regionID` | `string`, required | EAWS region identifier. |
| `name` | `string` | Human-readable region name. |

---

## `dangerRatings` — array of DangerRating objects

Defines the avalanche danger level(s) for the bulletin's regions.
When the danger changes during the day (e.g. low in the morning,
considerable in the afternoon), multiple ratings are given with
different `validTimePeriod` values.

| Field | Type | Description |
|---|---|---|
| `mainValue` | `string`, required | The danger level. Maps to the EAWS 5-level danger scale. |
| `validTimePeriod` | `string` (optional) | When this rating applies. See [validTimePeriod](#validtimeperiod). |
| `elevation` | `object` (optional) | Elevation constraint. See [Elevation](#elevation). |
| `aspects` | `string[]` (optional) | Compass aspects. See [Aspects](#aspects). |

### `mainValue` enum (Danger Rating)

| Value | EAWS Level |
|---|---|
| `"low"` | 1 |
| `"moderate"` | 2 |
| `"considerable"` | 3 |
| `"high"` | 4 |
| `"very_high"` | 5 |
| `"no_snow"` | — |
| `"no_rating"` | — |

---

## `avalancheProblems` — array of AvalancheProblem objects

Describes the specific avalanche problems present. Each problem can be
constrained by time period, elevation, and aspect.

| Field | Type | Description |
|---|---|---|
| `problemType` | `string`, required | The type of avalanche problem. See enum below. |
| `comment` | `string` | Free-text description of the problem, its characteristics and recommended behaviour. May contain HTML tags (see [Texts](#texts)). |
| `dangerRatingValue` | `string` (optional) | Implied danger rating for this specific problem. Same enum as `dangerRatings.mainValue`. |
| `validTimePeriod` | `string` (optional) | See [validTimePeriod](#validtimeperiod). |
| `elevation` | `object` (optional) | Elevation constraint. See [Elevation](#elevation). |
| `aspects` | `string[]` (optional) | Compass aspects. See [Aspects](#aspects). |
| `avalancheSize` | `number` (optional, 1–5) | Expected avalanche size on the EAWS scale: 1=small, 2=medium, 3=large, 4=very large, 5=extremely large. |
| `snowpackStability` | `string` (optional) | Stability of the snowpack. Enum: `"good"`, `"fair"`, `"poor"`, `"very_poor"`. |
| `frequency` | `string` (optional) | Expected frequency of triggering. Enum: `"none"`, `"few"`, `"some"`, `"many"`. |

### `problemType` enum

| Value | Description |
|---|---|
| `"new_snow"` | Fresh snow problem |
| `"wind_slab"` | Wind slab problem |
| `"persistent_weak_layers"` | Persistent weak layers |
| `"wet_snow"` | Wet snow problem |
| `"gliding_snow"` | Gliding snow problem |
| `"cornices"` | Cornice problem |
| `"no_distinct_avalanche_problem"` | No distinct problem identified |
| `"favourable_situation"` | Generally favourable conditions |

---

## Shared types

### `validTime`

| Field | Type | Description |
|---|---|---|
| `startTime` | `datetime` (ISO 8601) | Start of validity period. |
| `endTime` | `datetime` (ISO 8601) | End of validity period. SLF bulletins typically run 15:00 UTC to 15:00 UTC the next day (17:00 CET to 17:00 CET). |

### `validTimePeriod`

| Value | Meaning |
|---|---|
| `"all_day"` | Applies to the entire validity period |
| `"earlier"` | Applies to the first half of the day (morning) |
| `"later"` | Applies to the second half of the day (afternoon) |

### Elevation

Defines an elevation range. At least one bound is set. Values are strings
representing metres (100m resolution) or `"treeline"`.

| Field | Type | Description |
|---|---|---|
| `lowerBound` | `string` | Lower elevation limit (e.g. `"1800"`, `"treeline"`). If only `lowerBound` is set: "above this elevation". |
| `upperBound` | `string` | Upper elevation limit. If only `upperBound` is set: "below this elevation". If both are set: defines an elevation band. |

### Aspects

Array of compass direction strings (8-point, 45-degree segments):

`"N"`, `"NE"`, `"E"`, `"SE"`, `"S"`, `"SW"`, `"W"`, `"NW"`, `"n/a"`

### Texts

Used for `weatherForecast`, `weatherReview`, `snowpackStructure`,
`avalancheActivity`, `travelAdvisory`, and `tendency` comment fields.

| Field | Type | Description |
|---|---|---|
| `highlights` | `string` (optional) | Short headline/summary. |
| `comment` | `string` | Longer description. May contain limited HTML: `<br/>`, `<ul>`/`<li>`, `<h1>`–`<h6>`, `<b>`, `<p>`. |

---

## Narrative / text sections

These are all [Texts](#texts) objects (`highlights` + `comment`). In practice,
SLF bulletins use the `comment` field with embedded HTML headings.

| Field | Description |
|---|---|
| `weatherForecast` | Weather forecast synopsis for the bulletin period. |
| `weatherReview` | Weather review for the preceding period. |
| `snowpackStructure` | Description of current snowpack conditions, weak layers, and structural observations. |
| `avalancheActivity` | (optional) Description of observed or expected avalanche activity. |
| `travelAdvisory` | (optional) Travel advice and recommended behaviour. |

### `tendency` — array

Outlook describing the expected avalanche situation after the bulletin's
validity period. Each element is either:

- A **Texts** object (comment only, as used by SLF), or
- A structured **Tendency** object:

| Field | Type | Description |
|---|---|---|
| `tendencyType` | `string` | Enum: `"decreasing"`, `"steady"`, `"increasing"` |
| `validTime` | `object` | `startTime` / `endTime` |

---

## `customData`

Arbitrary AWS-specific data. SLF uses a `"CH"` key containing:

### `CH.aggregation` — array

Groups avalanche problems into display categories used by the SLF website/app.

| Field | Type | Description |
|---|---|---|
| `category` | `string` | `"dry"` or `"wet"` |
| `validTimePeriod` | `string` | `"all_day"`, `"earlier"`, or `"later"` |
| `problemTypes` | `string[]` | Array of problem type strings |
| `title` | `string` | Display title for the aggregation group |

### `CH.coreZoneText`

Found inside individual avalanche problem `customData`. A pre-formatted
summary sentence describing the core danger zone (aspect + elevation).

---

## `source` (optional, not present in SLF feed)

Information about the bulletin issuer.

| Field | Type | Description |
|---|---|---|
| `provider` | `object` | AWS organisation details. |
| `provider.name` | `string` | Organisation name. |
| `provider.website` | `string` (URI) | Organisation website. |
| `provider.contactPerson` | `object` | Person object (name, website). |
| `person` | `object` | Individual author. |
| `person.name` | `string` | Author name. |
| `person.website` | `string` (URI) | Author website. |

---

## `metaData` (optional, not present in SLF feed)

Metadata for linking external resources (maps, thumbnails, etc.).

| Field | Type | Description |
|---|---|---|
| `extFiles` | `array` | Array of external file references. |
| `extFiles[].fileType` | `string` | File type identifier. |
| `extFiles[].description` | `string` | File description. |
| `extFiles[].fileReferenceURI` | `string` (URI) | Link to the external file. |
| `comment` | `string` | Free-text metadata comment. |
