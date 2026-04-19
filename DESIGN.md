# SnowDesk — Design Handover

This document captures the state of the SnowDesk bulletin page design as of April 2026, including the editorial principles, the current implementation, the prioritised task list, the field guidance content drafts, and the open design questions.

It exists so that a new design-focused conversation can pick up where the previous strategy conversation left off without losing the specific decisions and reasoning. For background on SnowDesk's mission, target audience, competitive positioning, and product shape, see `README.md`. This document is the design-specific complement to that.

**Major change as of April 2026:** the design approach has shifted from "iterate the existing card incrementally" to "replicate the WhiteRisk page structure exactly, then subtract." The reasoning is in the section "Why we're replicating WhiteRisk" below. The previous incremental task list has been retired in favour of a two-phase plan (replicate, then subtract).

---

## Design tokens

The canonical source of truth for all tokens is [`src/css/main.css`](src/css/main.css), which declares them in a Tailwind v4 `@theme` block. Tokens below are mirrored here for Claude Design import; when the CSS and this document disagree, the CSS wins.

### Typography

- **Sans (body, UI):** `DM Sans`, system-ui fallback. Weights 400 / 500 / 600.
- **Mono (metadata, codes):** `DM Mono`, ui-monospace fallback. Weights 400 / 500.
- **Serif (reserved):** not yet chosen. Phase 3 restyling may introduce a serif for page titles, section headers, and rating names. Until then, sans is the only family in use.
- **Scale:** matches Tailwind defaults; prefer utility classes (`text-sm`, `text-base`, `text-lg`, `text-xl`) in templates rather than hard-coded sizes.

### Colour — surfaces and text (light mode)

| Token | Value | Purpose |
|---|---|---|
| `--color-bg` | `#f2f0ec` | Warm off-white page background |
| `--color-card` | `#ffffff` | Card surface |
| `--color-card-subtle` | `#fafaf8` | Card footer, admin strip |
| `--color-tag` | `#f5f3ef` | Problem tag / inner chip background |
| `--color-text-1` | `#1a1916` | Headings, primary labels |
| `--color-text-2` | `#6b6860` | Body, descriptions |
| `--color-text-3` | `#6e6b65` | Tertiary: dates, eyebrows, meta (4.67:1 on `--color-bg`, WCAG AA boundary — do not dim further) |
| `--color-border` | `rgba(0,0,0,0.09)` | Default border |
| `--color-border-strong` | `rgba(0,0,0,0.16)` | Emphasised border |

### Colour — dark mode overrides

Applied when `.dark` is present on `<html>`. EAWS danger colours intentionally do **not** change — they are a regulated international standard.

| Token | Light | Dark |
|---|---|---|
| `--color-bg` | `#f2f0ec` | `#1c1b19` |
| `--color-card` | `#ffffff` | `#2a2825` |
| `--color-card-subtle` | `#fafaf8` | `#23211f` |
| `--color-tag` | `#f5f3ef` | `#302e2a` |
| `--color-text-1` | `#1a1916` | `#edece8` |
| `--color-text-2` | `#6b6860` | `#a8a49c` |
| `--color-text-3` | `#6e6b65` | `#8a867e` |
| `--color-border` | `rgba(0,0,0,0.09)` | `rgba(255,255,255,0.09)` |

### Colour — interactive accent

| Token | Light | Dark | Purpose |
|---|---|---|---|
| `--color-accent` | `#2563eb` | `#3b82f6` | Primary action / focus ring |
| `--color-accent-hover` | `#1d4ed8` | `#60a5fa` | Button hover |
| `--color-accent-text` | `#ffffff` | `#ffffff` | Text on accent backgrounds |
| `--color-ring-selected` | `#3b82f6` | `#60a5fa` | Calendar selected-date ring |

### Colour — EAWS danger scale (theme-invariant)

Exact EAWS specification colours. Do not adjust for brand reasons — these are a recognised international standard. Each level has three variants: the saturated indicator, a muted tint for band backgrounds, and readable text colours for each surface.

| Level | Indicator | Tint | Text on tint | FG on saturated |
|---|---|---|---|---|
| Low | `#ccff66` | `#e8ffb8` | `#3a5a00` | `#1a1916` |
| Moderate | `#ffff00` | `#fff7b8` | `#4d4500` | `#1a1916` |
| Considerable | `#ff9900` | `#ffe5c2` | `#5c3000` | `#1a1916` |
| High | `#ff0000` | `#ffd9d9` | `#6b0000` | `#ffffff` |
| Very high | `#ff0000` | `#1a0000` (inverted) | `#ffffff` | `#ffffff` |

### Colour — status feedback

| Token | Light BG / text | Dark BG / text | Purpose |
|---|---|---|---|
| `--color-status-error-*` | `#fee2e2` / `#991b1b` | `#451a1a` / `#fca5a5` | Error flash / badge |
| `--color-status-warning-*` | `#fef3c7` / `#92400e` | `#452a0a` / `#fcd34d` | Warning flash / badge |
| `--color-status-success-*` | `#d1fae5` / `#065f46` | `#14332a` / `#6ee7b7` | Success flash / badge |
| `--color-status-info-*` | `#dbeafe` / `#1e40af` | `#1e2a4a` / `#93c5fd` | Info flash / badge |

A dedicated warning-callout ramp (`--color-callout-warning-*`) exists for render-model error banners — see `src/css/main.css` for the full set.

### Radii

| Token | Value | Purpose |
|---|---|---|
| `--radius-card` | `16px` | Outer card shell |
| `--radius-tag` | `8px` | Problem tag, inner chips |
| `--radius-pill` | `4px` | Timing badge, period pill |

### Layout

| Token | Value | Purpose |
|---|---|---|
| `--breakpoint-tablet` | `600px` | Single-column → two-column |
| `--breakpoint-desktop` | `960px` | Two-column → grid |
| `--width-card-mobile` | `390px` | Single-column mobile max-width |
| `--width-grid-max` | `1200px` | Multi-column grid max-width |

Content column inside `<nav>` and primary body copy: **640px max-width**, centred (see `templates/includes/nav.html`).

### Token rules

- Never hard-code hex values in templates or new CSS. Reference the token.
- Use Tailwind utility classes (`text-text-1`, `bg-card`, `rounded-card`, etc.) in templates; custom CSS lives in `src/css/main.css` only when a utility can't express it (generated content, data-attribute selectors, HTML resets).
- When contrast matters, use `text-text-1`, `text-text-2`, or the `--color-eaws-*-text` tokens. `text-text-3` sits on the WCAG AA boundary — never dim it further with `opacity-*`.
- EAWS tokens are theme-invariant by design: their saturated backgrounds don't change in dark mode, so their foreground text mustn't either (`--color-eaws-*-fg`).

## Design direction (for Claude Design)

A condensed brief of the editorial character. Expanded reasoning is in "What SnowDesk's design has to do" and "Editorial principles for the page" below.

- **Character:** calm, confident, quietly expert. Closer to a well-edited publication than a software dashboard. Treat the reader as a thoughtful adult making real decisions.
- **Voice:** direct, unpatronising, no extreme-sports energy, no emojis, no cheerful disclaimers hiding uncertainty. Content should survive being pasted into a WhatsApp group without feeling out of place.
- **Mission lens:** SnowDesk is an **on-ramp** to the official SLF bulletin. Success = graduation. The visual language must respect the reader's time and expertise.
- **Hierarchy:** SLF source content is primary; SnowDesk-added content (day-character labels, field guidance) is visually distinguishable and deferential.
- **Restraint as a rule:** new features arrive in the same restrained idiom — small, quiet, deferential to the existing hierarchy. Do not add visual weight or accent colours to make additions feel "important".
- **Asymmetry is honest:** uneven CAAML data should produce visibly uneven blocks. Do not fabricate structure for symmetry.
- **Never recommend behaviour:** characterise, explain, do not tell users to go or stay.
- **Conservatism:** when in doubt on characterising a borderline day, be cautious. The cost of the opposite error is not comparable.

The editorial test for any addition: look at the page and ask whether it still feels like editorial content or whether it's drifted toward dashboard. If the latter, rework.

---

## What SnowDesk's design has to do

Before diving into specifics, the design exists to support a particular product framing that should shape every visual decision:

SnowDesk is an **on-ramp to the official SLF avalanche bulletin**. It exists to help recreational backcountry skiers begin reading bulletins with confidence. Its success is measured not by retention but by graduation — a user who progresses from SnowDesk to reading the full SLF bulletin directly is the goal, not a loss.

This framing has a load-bearing implication for design: the product treats users as thoughtful adults making real decisions, not as students of avalanche science (which is how WhiteRisk treats them) and not as casual consumers who need to be alarmed (which is how most safety apps treat them). The visual language needs to express respect for the reader's intelligence and time. Calm, confident, quietly expert. Closer to a well-edited publication than a software dashboard.

The cleanest test as features get added: look at the page and ask whether it still feels like editorial content or whether it's drifted toward dashboard. If the latter, the addition needs to be reworked, not because it's wrong as a feature but because the visual character of the page *is* part of the product, not separate from it.

## Why we're replicating WhiteRisk

The previous approach — iterate the bulletin card design by adding small refinements — was producing a page that was neither WhiteRisk's clarity nor SnowDesk's identity. Inch-toward-WR creates a permanent halfway state where every addition has to be argued for in isolation against an unfinished baseline.

Replicating WR's structure exactly, then subtracting, has three advantages:

1. **The canonical SLF/EAWS information architecture is already encoded in WR's layout.** WR didn't invent it — they're rendering the structure SLF authors the bulletin in. Replicating WR means replicating the structure a graduating user will eventually encounter on the SLF site. That serves the on-ramp mission directly.

2. **Subtraction is where SnowDesk's editorial character actually gets expressed.** What we choose to *remove* from the WR layout (the Explanation modal button, the close button, possibly the day-summary band on simple days) communicates SnowDesk's positioning more clearly than any incremental addition could.

3. **It collapses most of the previous task list automatically.** Tasks like "remove truncation," "promote elevation to body weight," "soften the timing badge" — these all fall out of replication, because WR already gets them right. The previous 17-task list is retired.

The replica pass uses **WR's typographic choices verbatim** (system sans throughout, no serif accents). SnowDesk's serif-headline styling is a restyling decision that comes after the structural replica is complete and we can see what it would replace.

## Canonical layout (replicated from SLF/WhiteRisk)

The single-day page is the canonical product surface. It renders one bulletin for one micro-region for one validity period. The structure, top to bottom:

**1. Page chrome.** Region name, date and validity window, prev/next day navigation. WhiteRisk has a "Close" button here because their bulletin opens as a modal over a map; SnowDesk's bulletin is the page itself, so Close is one of the first things to subtract.

**2. Bulletin headline band.** A coloured strip showing the bulletin's danger rating(s). On a simple day this shows one rating (e.g. `3- Considerable`). On a variable day this shows two ratings with a transition arrow (e.g. `2+ Moderate → 3 Considerable`) and a one-line note that hazard changes through the day. **Always present** — not a variable-day affordance.

**3. Rating block(s).** One block per `dangerRating` in the CAAML data. Each block is a self-contained container with:
   - A coloured header strip naming the rating and a one-line scope (e.g. `Considerable (3)  Wet snow, as the day progresses`).
   - An aspect/elevation row immediately below the header. The aspect rosette and elevation pictogram come from the structured CAAML fields. **Asymmetry is honest:** dry-side problems usually carry these structured fields; wet-side problems often don't, and the row is sparser or absent. Don't fabricate data to make blocks look uniform.
   - One row per avalanche problem in the block: the EAWS problem icon, the problem name, and the SLF prose comment in full. No truncation, no expand-to-read-more.

   On a simple day there is one block. On a variable day there are two stacked vertically. The block container is the unit of layout — not the page, not the problem.

**4. Bulletin metadata.** Issue time, valid-until, next-update. Three small fields in a thin strip.

**5. Snowpack and weather section.** A section heading followed by four sub-blocks rendered from SLF prose:
   - **Snowpack** (`snowpackStructure.comment`)
   - **Weather review** for yesterday (`weatherReview.comment`) — includes structured Fresh snow, Temperature, Wind subheadings
   - **Weather forecast** for tomorrow (`weatherForecast.comment`) — same structured subheadings
   - **Outlook** (`tendency[].comment`) — multi-day forward outlook, prose only

   These fields ship from SLF as **HTML strings** with `<h1>`, `<h2>`, `<p>`, `<ul>`, `<li>` tags. The structured Fresh snow / Temperature / Wind subheadings WhiteRisk shows are literal `<h2>` tags in the source HTML. No parsing required — sanitise (allow only the tags listed; bleach is the right tool) and render.

**6. Footer.** SLF attribution, region grouping context. The grouping context follows the rule from the previous task list: focal region first, grouped regions framed as additional context (e.g. `Val dal Spöl, with Münstertal · unteres Puschlav · Corvatsch`).

## What the replica is NOT

To keep the replica pass focused, these things are explicitly out of scope and will be decided in the subtraction pass:

- Day-character labels (the SnowDesk-derived interpretation layer)
- Field guidance (the SnowDesk-derived plain-language additions beneath SLF prose)
- The change strip / diff indicator
- The serif-headline restyling
- Any decision to remove or restructure WR sections
- The list view (see below)

## The list view

The previous testing surface at `/<region-id>/random/` displayed ten consecutive historical bulletins as a vertical scrolling list. This view is **deprecated as the canonical product surface** but **kept as a power-user history view** at a different route (suggested: `/<region-id>/history/`).

The list view's strengths (longitudinal pattern visible at a glance, useful once the diff strip exists, matches how experienced tourers think about conditions) genuinely serve a power-user use case, just not the primary one. Keeping it as a separate route means the canonical single-day page can be designed without compromise, while the longitudinal view continues to exist for users who want it. The list view does not need to be a design priority — it can continue to use the existing card design, or eventually inherit a compact variant of the rating-block layout, but neither is urgent.

## Editorial principles for the page

These are the principles that should govern any addition or change to the page design. They're derived from longer conversations about SnowDesk's positioning and should be treated as constraints, not suggestions.

**Never recommend behaviour.** SnowDesk characterises the day and explains the bulletin. It does not tell users to go or to stay. The line between "information" and "judgement" exists for good reasons in Swiss avalanche culture, and crossing it has real consequences. Even features that introduce SnowDesk's own interpretation (the day-character labels) are deliberately descriptive rather than prescriptive.

**SLF is the authoritative source, and the page should make this visible.** The bulletin prose comes from SLF; SnowDesk renders it faithfully. Anywhere SnowDesk adds its own content alongside the SLF source — plain-language sentences, day-character labels, field guidance — the visual treatment must distinguish the SnowDesk layer from the SLF layer so users can tell which content comes from where.

**Replicate first, subtract second, add third.** The replica is the baseline against which all additions and removals are measured. Don't add SnowDesk-derived content (day-character labels, field guidance) until the replica is complete and the subtraction pass has happened. The subtraction pass is where editorial judgement gets exercised — what stays, what goes, what gets quieter.

**Asymmetry is honest.** The CAAML data is uneven: dry-side problems carry structured aspect/elevation fields; wet-side problems often don't. Rating blocks for the two sides will visibly differ. Don't fabricate or NLP-extract structured data from prose to make blocks look uniform. The visible difference is a faithful reflection of how SLF authors the bulletin.

**The templating table is the highest-leverage editorial work in the project.** A bad sentence in a problem block fails the user directly. Hand-write every template. Review with an experienced tourer or guide. Test against real bulletins across a variety of conditions. Faithfulness beats cleverness; clarity beats polish.

**When in doubt, be conservative.** The cost of characterising a borderline day cautiously is a mild user complaint. The cost of the opposite error is not comparable.

**The voice is calm, confident, quietly expert.** Direct and unpatronising. No extreme-sports energy. No emojis. No cheerful disclaimers hiding uncertainty. The tone should survive being pasted verbatim into a WhatsApp group without feeling out of place.

**Restraint is part of the brand.** When adding new content (field guidance, day-character labels, change indicators), the temptation is to add visual weight or accent colours to make additions feel "important." Resist. New features should be added in the same restrained idiom — small, quiet, deferential to the existing hierarchy.

## Phase 1: Replication tasks

These tasks build the WR-canonical layout against the existing data pipeline. The goal is a single-day page that, screenshotted next to WR for the same bulletin, is structurally indistinguishable.

1. **Create the new route and view.** A Django view that takes a region ID and a date, fetches the bulletin for that region/validity period, and renders the new template. URL pattern something like `/<region-id>/<date>/`. Default landing route (`/<region-id>/`) redirects to today.

2. **Build the rating-block partial.** A template fragment that renders one `dangerRating` as a self-contained block: header strip (rating name, scope description), aspect/elevation row, problem rows. The same partial works for simple-day (one block) and variable-day (two stacked blocks). Pull aspect/elevation from the structured CAAML fields where present; render nothing where absent.

3. **Build the bulletin headline band.** Always present. Renders one rating on simple days, two ratings with a transition arrow on variable days. The transition logic comes from `customData.CH.aggregation` — that field already encodes SLF's editorial dry/wet clustering and should drive this rather than re-derivation from `dangerRatings` alone.

4. **Render snowpack and weather HTML safely.** Add a Django template filter (e.g. `snowdesk_html`) that runs the SLF HTML strings through bleach with a strict allowlist: `h1, h2, p, ul, li, strong, em`. Strip everything else. Apply CSS to style the resulting headings consistently with the rest of the page. Test against the four fields (`snowpackStructure.comment`, `weatherReview.comment`, `weatherForecast.comment`, `tendency[].comment`).

5. **Build the metadata strip and footer.** Three-field metadata (issue, valid, next update). Footer with SLF attribution and the focal-region-first grouping context.

6. **Replicate WR's typographic choices verbatim.** System sans throughout. No serif. Match WR's font sizes and weights as closely as possible. The point of the replica is structural fidelity; restyling comes later.

7. **Move the existing list view to `/<region-id>/history/`.** Keep the existing card design for now. Add a small navigation affordance from the single-day page to the history view ("View history" link in the footer or near the prev/next controls).

8. **Screenshot test.** Pick three bulletins with known character — a simple-day, a variable-day with dry/wet split, a Level 4+ day if one exists in the archive. Screenshot SnowDesk and WR side by side for each. The structural layout should match. Differences should be limited to colour, spacing, and the items deliberately dropped (Close button, Explanation button).

That's the entire replica pass. Eight tasks, mostly templating and HTML sanitisation. No new editorial content, no new visual identity decisions.

## Phase 2: Subtraction pass

Once the replica is in place, walk through it and make subtraction decisions explicitly. Each item below is a question, not a foregone conclusion. The answers come from looking at the live replica, not from deciding in the abstract.

The principle for subtraction: **remove anything that doesn't earn its place against the on-ramp mission.** WR is built for educating beginners about avalanche science (their core business is selling courses). SnowDesk is built for helping competent intermediates read the bulletin faster. Anything in WR that serves the former but not the latter is a candidate for removal.

Confirmed subtractions (decide first, no live-page review needed):

- **The Explanation button on each rating block.** It opens a modal explaining what "Considerable" means in general. Educational scaffolding — out of scope for SnowDesk's competent-intermediate audience. The SLF interpretation guide is the right home for this content; link it once from the page footer.
- **The Close button at the page top.** Artefact of WR's modal-over-map architecture. SnowDesk's bulletin is the page itself.

Subtraction questions to decide after looking at the replica:

- **Does the bulletin headline band still earn its place when there's only one rating?** The band exists to flag transitions. On a simple day it just repeats the rating-block header below. Possible answers: always show (consistency), hide on simple days (subtraction), keep but reduce visual weight on simple days (compromise).
- **Does the snowpack/weather section help the on-ramp goal, or does it pad the page?** This is a substantial chunk of content. Some of it (snowpack history, weather review) is genuinely useful context for an experienced tourer. Some of it (multi-day outlook) competes with dedicated weather services. Possible answers: render in full (replica fidelity), render only Snowpack and Weather review (drop forecast and outlook as duplicative of MeteoSwiss), collapse behind a "Show snowpack and weather" toggle.
- **Should the rating-block header be that loud?** WR's filled orange/yellow strips are the loudest visual element on the page. SnowDesk's editorial character would suggest a thinner left-border treatment in the same colour, with the rating name in body-weight type. Worth trying both side by side.
- **Is the aspect rosette readable at this size, or does it need a larger treatment?** Worth checking on a real phone.
- **What happens to the `comment` field at the bulletin level (the overall hazard description)?** WR doesn't seem to surface this prominently — the rating-block prose is the primary text. Worth checking whether this field exists in the CAAML data and where (if anywhere) it should appear.

## Phase 3: SnowDesk additions on top of the replica

Only after Phase 1 and Phase 2 are complete. These add SnowDesk's editorial layer on top of the cleaned-up replica.

- **Field guidance beneath each problem's SLF prose.** Test with the persistent weak layer problem first. Drafted content is in the "Field guidance drafts" section below. Visual treatment: separated from SLF prose by a horizontal rule, attributed to the SLF interpretation guide rather than to SnowDesk directly (we're paraphrasing SLF, not authoring novel guidance).
- **Day-character label.** Sits between the bulletin headline band and the first rating block. Visual treatment must distinguish SnowDesk interpretation from SLF source. The label needs a one-line explainer underneath that teaches the concept. See the day-character model section below.
- **Bulletin diffing logic and change strip.** Backend-first: produce a structured diff between consecutive bulletins for the same region using CAAML fields (not prose). Then add a quiet visual strip at the top of the page when material changes have occurred. *Absent* on cards where nothing material has changed — the absence is part of the design.
- **Serif-headline restyling pass.** Once additions are in place, decide whether to introduce the SnowDesk serif-headline treatment for `h1`/`h2` elements (page titles, section headers, rating names). Replicate-then-restyle is safer than restyling speculatively.

## When to ship to first users

After Phase 1 and Phase 2 are complete. The point of the replicate-then-subtract approach is that the result of those two phases is already a coherent, shippable product — it's WR's structure with WR's worst beginner-scaffolding removed and SnowDesk's micro-region focus baked in. That's enough to put in front of ten users and have real conversations.

Phase 3 is where you most need user feedback before committing. The day-character labels and field guidance are the most editorially load-bearing additions in the project, and they benefit from being shaped by what early users actually find missing in the replica + subtraction baseline.

The temptation will be to keep building before showing it to anyone. Resist that. The next ten users can give you feedback worth more than another month of solo iteration.

## Field guidance drafts

These are paraphrased from the SLF Avalanche Bulletin Interpretation Guide (November 2025 edition), specifically the "Identification of the problem in the field" and "Travel advice" sections for each avalanche problem type. They are written in the SnowDesk voice — calm, direct, faithful to the SLF source but rewritten for accessibility. **All drafts need review by an experienced tourer or guide before shipping**, and the SLF source should be checked against to ensure no nuance has been lost in compression.

Each one is one paragraph, designed to fit beneath the SLF prose in a problem block, separated by a horizontal rule and attributed to the SLF interpretation guide.

### new_snow

This kind of day is usually easy to spot. The snow is fresh, it's everywhere, and the problem isn't subtle. The harder question is how serious it is. Look at recent avalanche activity in similar terrain, and pay attention to how much new snow has fallen in the last three days. Critical loading depends on temperature, wind, and what was on the surface before the storm. SLF advises waiting until the snowpack has had time to bond before committing to steep terrain in fresh snow.

### wind_slab

With training and good visibility, wind slab can be read in the field. Look for fresh snow deposits on lee slopes, in gullies and bowls, and behind ridgelines and abrupt changes in terrain — these are where the wind has loaded the snow into slabs that are particularly easy to trigger. Recent avalanches, shooting cracks under your weight, and whumpfing sounds are clear confirmations of the problem. Without training, the safest move is to avoid wind-loaded terrain entirely. Note that wind signs alone don't always mean an avalanche problem exists, and the age of drifted snow is hard to judge.

### persistent_weak_layers

This is the problem field observation can't reliably solve. Persistent weak layers are very challenging to recognise. Whumpfing sounds and shooting cracks are typical when present, but they aren't always there — you can have a serious weak layer with no warning signs at the surface. Knowledge of how the snowpack has evolved over the season is essential, which is why reading the bulletin regularly through the winter matters more for this problem than any other. SLF's travel advice is unusually direct: travel conservatively, avoid terrain where the consequences of being caught are large (large steep slopes, terrain with overhead hazard, transitions from thin to deep snowpack), and treat the history of weather and snow as more important than what you can see today. The release of avalanches in persistent weak layers is a significant cause of recreational avalanche fatalities.

### wet_snow

Usually the easiest problem to read in the field. Rolling snowballs, deep penetration when you step or ski, and small natural slides are clear signals that the snowpack is losing strength. The key decision is timing. After a clear, cold night the surface usually freezes into a strong supporting crust, and conditions are favourable in the early morning. After a warm, overcast night, the problem is often present from the start of the day. Plan early returns and watch the runout zones below you.

### gliding_snow

Almost impossible to predict precisely, even when the warning signs are visible. Glide cracks — gaps that open in the snowpack down to the ground — are often precursors to release, but they don't tell you when. A glide avalanche can release minutes after the cracks appear, or weeks later, or not at all. Some release without any visible warning. The only useful response is to avoid lingering anywhere near glide cracks: above them, alongside them, or below in the runout.

### no_distinct_avalanche_problem

This isn't a specific avalanche problem. SLF uses this label when no single problem dominates the assessment, often on lower-danger days. It doesn't mean conditions are safe: any avalanche type is still possible, and normal caution applies. On stable days this is usually a signal to enjoy the mountains with general awareness; on less-stable days where the assessment is still inconclusive, it's a signal to be cautious specifically *because* there's no clear pattern to follow.

## The day-character model (Phase 3)

This is the bigger editorial commitment that comes after the replica + subtraction baseline is in place. It's documented in the README but worth restating here because the design implications are specific.

Every bulletin gets one of five labels, derived from a deterministic rule cascade over the structured CAAML fields:

- **Stable day.** Conditions are broadly favourable. Main hazards are user error and very steep terrain.
- **Manageable day.** The snowpack has identifiable issues, but they're readable in the field.
- **Hard-to-read day.** The snowpack contains a problem (typically a persistent weak layer) that doesn't announce itself in the field. Field skills don't save you; terrain choices do.
- **Widespread danger.** Dangerous conditions cover most of the typical touring envelope.
- **Dangerous conditions.** Level 4 or 5 territory.

Each label needs a one-paragraph plain-language explainer that teaches the concept. The labels become a shared vocabulary between SnowDesk and its readers over time, supporting the on-ramp goal.

The cascade rules (provisional, need calibration before the label is rendered):

| Order | Conditions | Label |
|---|---|---|
| 1 | Danger rating 4 or 5 | Dangerous conditions |
| 2 | Danger rating 2+ AND any problem is `persistent_weak_layers` or `gliding_snow` | Hard-to-read day |
| 3 | Danger rating 3 AND (6+ aspects OR lower bound ≤2000m OR 2+ problems) | Widespread danger |
| 3b | Danger rating 3+ subdivision | Widespread danger |
| 4 | Danger rating 2 or 3, no earlier match | Manageable day |
| 5 | Danger rating 1, OR rating 2 with no distinct problem | Stable day |

A spring-pattern rule (e.g. "Race the sun day" for the morning-low/afternoon-considerable wet hazard pattern) is proposed but not yet in the cascade. Worth adding once the variable-day rendering is working in the replica.

Visual treatment when added: distinct zone between the bulletin headline band and the first rating block. Must be clearly attributed to SnowDesk's interpretation rather than to SLF — perhaps a thin coloured left border, a different background tint, or a small distinctive icon. The label needs its own one-line explainer underneath that teaches the concept.

## Open design questions

A few things that haven't been resolved and are worth working through after Phase 1 is complete:

**The no-distinct-problem stable day case.** On days with `no_distinct_avalanche_problem` and Level 1 rating, the rating block has very little to render — just the danger headline and possibly a single brief note. What does this block look like? It needs to feel intentionally quiet rather than empty, and it needs to honour the SLF caution that "no distinct problem" doesn't mean "safe."

**The very-dangerous-day case (Level 4 or 5).** At the other end, the page needs to handle days where multiple problems are active, the rating is High, and the bulletin prose is longer and more urgent. Does the calm editorial treatment hold up when the content is genuinely alarming? The brand character requires that it does — SnowDesk shouldn't suddenly become a flashing-red dashboard on dangerous days — but it needs visual treatment that acknowledges the seriousness without abandoning the editorial tone.

**The SnowDesk visual identity beyond the page.** The bulletin page is the building block, but the surrounding website (homepage, about page, sample email page, account management) hasn't been designed yet. The page sets a visual direction; the rest of the site needs to extend it consistently. Worth thinking about as a system rather than letting each surface evolve in isolation.

**Email rendering vs web rendering.** The 5pm and 7am subscription emails need their own visual treatment that captures the same character within email's much tighter constraints (limited CSS support, no custom fonts, dark mode quirks, forwarding fragility). Email is the primary product surface — this is its own design conversation and hasn't been started yet.

## Archived: the side-by-side variable-day split

An earlier design iteration rendered variable days as two side-by-side period columns (dry on the left, wet on the right, with a `→` between them). This approach is **deprecated** in favour of the WR-canonical vertical-stacked rating blocks for two reasons:

1. It doesn't scale past two periods. Real bulletins occasionally have three (e.g. dry / wet morning / wet afternoon).
2. It doesn't match the SLF mental model the user is graduating toward. Stacked rating blocks do.

The pattern is preserved here in case a future surface (e.g. a comparison view between two adjacent regions) genuinely benefits from horizontal side-by-side layout. The CSS is in `bulletin_cards.html` under the `.split-wrapper` and `.period-col` classes. Don't reuse it for the daily bulletin page.

## Archived: the previous 17-task list

The previous task list (April 2026, pre-replication-decision) is retired. Most of its items either fall out of replication automatically (truncation removal, elevation promotion, problem icon improvements, timing badge softening) or move to Phase 3 (field guidance, day-character label, diff strip, SLF logo). A few small items survive into Phase 1 (alt text fix, region context line restructure, removing the dev-only "Open in admin" link). They're folded into the replication tasks above where relevant.

## How to use this document

This is intended as the input to a new design-focused chat. When starting that chat, reference this document directly: "I'm continuing the SnowDesk design work. See `DESIGN.md` for current state, the replicate-then-subtract plan, field guidance drafts, and open questions."

The strategy conversation that produced the original framing remains in its own chat and shouldn't need to be re-derived. If a strategic question comes up in the design work that's already been resolved in the strategy chat, refer back to it rather than re-litigating it here.
