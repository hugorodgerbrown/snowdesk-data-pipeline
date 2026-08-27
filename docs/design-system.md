---
name: design-system
description: Bulletin page design handover — fidelity-first editorial principles, design tokens, the component layer, field guidance drafts
status: current
last-reviewed: 2026-08-26
---


# Snowdesk — Design Handover

This document is the design-specific reference for the bulletin page: the
editorial principles that govern it, the design tokens and component layer it
is built from, the field-guidance content drafts, and the open design
questions. For background on Snowdesk's mission, audience, competitive
positioning, and product shape, see `README.md`.

**The governing principle is fidelity.** Snowdesk renders the provider's
bulletin *whole* — every field the provider published, verifiable against the
source link — and adds what the provider cannot: weather at a finer grain than
the warning region, and field observations from the people who were there. We
add to the bulletin. We do not edit it, summarise it, or leave parts of it out.

This replaces the earlier replicate-then-subtract method, under which the
design's next step was a pass to remove "anything that doesn't earn its place".
That premise inverted: completeness *is* the product, and the subtraction pass
was retired rather than completed —
[`decisions/bulletin-fidelity-over-simplification.md`](decisions/bulletin-fidelity-over-simplification.md)
records why. The principle is enforced mechanically, not by convention:
`tests/sentinels/fidelity.py` holds one row per CAAML path across the nine
sentinels, each either rendered or excluded with a written reason, and
`bin/fidelity-lint` fails the build when a field reaches no rendered surface
([`tests/sentinels/README.md`](../tests/sentinels/README.md)).

---

## Design tokens


The canonical source of truth for all tokens is [`src/css/main.css`](../src/css/main.css), which declares them in a Tailwind v4 `@theme` block. Tokens below are mirrored here for Claude Design import; when the CSS and this document disagree, the CSS wins.

### Typography

- **Sans (body, UI):** `DM Sans`, system-ui fallback. Weights 400 / 500 / 600.
- **Mono (metadata, codes):** `DM Mono`, ui-monospace fallback. Weights 400 / 500.
- **Serif (reserved):** not yet chosen. A future restyling pass may introduce a serif for page titles, section headers, and rating names. Until then, sans is the only family in use.
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

A condensed brief of the editorial character. Expanded reasoning is in "What Snowdesk's design has to do" and "Editorial principles for the page" below.

- **Character:** calm, confident, quietly expert. Closer to a well-edited publication than a software dashboard. Treat the reader as a thoughtful adult making real decisions.
- **Voice:** direct, unpatronising, no extreme-sports energy, no emojis, no cheerful disclaimers hiding uncertainty. Content should survive being pasted into a WhatsApp group without feeling out of place.
- **Mission lens:** Snowdesk shows the provider's bulletin **in full**, and adds the context the provider cannot. Completeness is the product; the visual language must carry a long, uneven document without making it feel heavy.
- **Hierarchy:** provider content is primary; Snowdesk-added content (day-character labels, weather, field observations, field guidance) is visually distinguishable and deferential.
- **Restraint as a rule:** new features arrive in the same restrained idiom — small, quiet, deferential to the existing hierarchy. Do not add visual weight or accent colours to make additions feel "important".
- **Asymmetry is honest:** uneven CAAML data should produce visibly uneven blocks. Do not fabricate structure for symmetry.
- **Never recommend behaviour:** characterise, explain, do not tell users to go or stay.
- **Conservatism:** when in doubt on characterising a borderline day, be cautious. The cost of the opposite error is not comparable.

The editorial test for any addition: look at the page and ask whether it still feels like editorial content or whether it's drifted toward dashboard. If the latter, rework.

---

## What Snowdesk's design has to do

Before diving into specifics, the design exists to support a particular product framing that should shape every visual decision:

Snowdesk shows the **real bulletin**, not a simplified reading of one. A user can follow the source link and check: nothing the provider published is missing, softened, or paraphrased away. That claim is the product's foundation — it is what makes Snowdesk safe to rely on for a decision, and it is what a competitor shipping a prettier summary cannot copy without doing the same work. On top of it, Snowdesk adds what the provider does not offer: weather at a finer grain than the warning region, community field observations, and cross-provider coverage in one place.

This framing has a load-bearing implication for design: the page has to carry a long, structurally uneven document without becoming exhausting. The work is hierarchy, pacing and restraint — deciding what is loud, what is quiet, and what is behind a disclosure — never deciding what to drop. Where a section is genuinely secondary, the answer is a collapsible panel, not a deletion.

It also means the product treats users as thoughtful adults making real decisions, not as students of avalanche science and not as casual consumers who need to be alarmed. The visual language needs to express respect for the reader's intelligence and time. Calm, confident, quietly expert. Closer to a well-edited publication than a software dashboard.

The cleanest test as features get added: look at the page and ask whether it still feels like editorial content or whether it's drifted toward dashboard. If the latter, the addition needs to be reworked, not because it's wrong as a feature but because the visual character of the page *is* part of the product, not separate from it.

## Why the page renders the bulletin whole

The page follows the structure the provider authors the bulletin in — the same information architecture WhiteRisk renders, because neither of us invented it. That is the canonical SLF/EAWS layout, and following it is fidelity rather than imitation: a reader who goes on to open the source bulletin directly meets a document they already know how to read.

Two consequences follow, and they are the ones that matter in review:

1. **A field the provider published gets a surface.** If a CAAML path has no home on the page, that is a bug, not an editorial choice — and `bin/fidelity-lint` will say so. The legitimate way to decide a field is not worth showing is to write the exclusion reason into `tests/sentinels/fidelity.py`, where a reviewer can audit it with `bin/fidelity-lint --show-exclusions`.

2. **Length is managed, not avoided.** A bulletin on a complex day is long because the day is complex. Collapsible panels, quiet metadata strips and clear section boundaries are how the page absorbs that. `templates/includes/_collapsible_panel.html` is the workhorse.

What Snowdesk does *not* replicate is the parts of WhiteRisk that belong to WhiteRisk's own app, rather than to the bulletin: the Close button (an artefact of their modal-over-map architecture) and the Explanation modal (educational scaffolding for their course business). Dropping those was never in tension with fidelity — they are not the provider's content.

## Canonical layout


The single-day page is the canonical product surface. It renders one bulletin for one micro-region for one validity period. The structure, top to bottom:

**1. Page chrome.** Region name, date and validity window, prev/next day navigation. WhiteRisk has a "Close" button here because their bulletin opens as a modal over a map; Snowdesk's bulletin is the page itself, so there is no Close button.

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

**6. Footer.** Provider attribution. The page-specific footer was removed in SNOW-80: the global `_site_footer.html` carries the attribution requirement, and adjacent regions are reachable from the masthead deep-link (SNOW-81).

## Editorial principles for the page

These are the principles that should govern any addition or change to the page design. They're derived from longer conversations about Snowdesk's positioning and should be treated as constraints, not suggestions.

**Never recommend behaviour.** Snowdesk characterises the day and explains the bulletin. It does not tell users to go or to stay. The line between "information" and "judgement" exists for good reasons in Swiss avalanche culture, and crossing it has real consequences. Even features that introduce Snowdesk's own interpretation (the day-character labels) are deliberately descriptive rather than prescriptive.

**The provider is the authoritative source, and the page should make this visible.** The bulletin prose comes from SLF, ALBINA or Météo-France; Snowdesk renders it faithfully. Anywhere Snowdesk adds its own content alongside the source — plain-language sentences, day-character labels, weather, field observations, field guidance — the visual treatment must distinguish the Snowdesk layer from the provider layer so users can tell which content comes from where.

**Render everything; add alongside, never instead.** Snowdesk-derived content sits next to the provider's, and never displaces it. When a surface feels crowded, the move is to change the hierarchy — quieten, group, collapse — not to remove what the provider published. "This section is long" is not a reason to drop it; it is a reason to give it a disclosure.

**Asymmetry is honest.** The CAAML data is uneven: dry-side problems carry structured aspect/elevation fields; wet-side problems often don't. Rating blocks for the two sides will visibly differ. Don't fabricate or NLP-extract structured data from prose to make blocks look uniform. The visible difference is a faithful reflection of how the provider authors the bulletin.

**The templating table is the highest-leverage editorial work in the project.** A bad sentence in a problem block fails the user directly. Hand-write every template. Review with an experienced tourer or guide. Test against real bulletins across a variety of conditions. Faithfulness beats cleverness; clarity beats polish.

**When in doubt, be conservative.** The cost of characterising a borderline day cautiously is a mild user complaint. The cost of the opposite error is not comparable.

**The voice is calm, confident, quietly expert.** Direct and unpatronising. No extreme-sports energy. No emojis. No cheerful disclaimers hiding uncertainty. The tone should survive being pasted verbatim into a WhatsApp group without feeling out of place.

**Restraint is part of the brand.** When adding new content (field guidance, day-character labels, change indicators), the temptation is to add visual weight or accent colours to make additions feel "important." Resist. New features should be added in the same restrained idiom — small, quiet, deferential to the existing hierarchy.

## The component layer

Tokens are only half the system. The other half is the set of partials and
template tags that own a *shape*, so the same card, button or page title is not
re-typed as a utility string in the next template. The rules and the guards
that enforce them live in `CLAUDE.md` ("Design system"); this section records
what the layer is for and where its edges are.

**Reuse first, extract second, inline never.** The registry in
[`apps/public/design_tokens.py`](../apps/public/design_tokens.py) is the
canonical inventory, rendered for browsing at the staff-only `/_components/`.
A new partial is not finished until it has a registry entry and a variant
fixture in [`apps/public/_component_fixtures.py`](../apps/public/_component_fixtures.py).

**Ordinary pages are in the system too.** This was the layer's blind spot for
most of the project's life: the bulletin and map surfaces were thoroughly
componentised while the legal pages, the help and observation pages and the
accounts forms stayed as inline utility strings, and drifted into near-miss
variants of each other — nine page shells differing by one class, three page
titles differing by two. SNOW-672 brought them in. A page is a page; if it
renders a title, a shell or a form field, it uses the component.

**Three kinds of component, and when to reach for each:**

| Kind | Use when | Example |
|---|---|---|
| Include partial | The shape has content slots and variants | `_button.html`, `_page_title.html`, `_form_field.html` |
| Block tag | The shape *wraps* arbitrary page content and owns the wrapper element | `{% card %}` in [`apps/public/templatetags/components.py`](../apps/public/templatetags/components.py) |
| Class-string tag | The element stays in the template — it carries its own attributes — and only the classes are shared | `{% page_shell_classes %}`, `{% input_classes %}`, `button_classes` |
| CSS component class | The shape styles *descendants* it does not author | `.slf-prose`, `.legal-prose`, `.text-link` in [`src/css/main.css`](../src/css/main.css) |

Two of these are worth stating plainly.

A **class-string tag** rather than a block tag whenever call sites need their
own attributes on the element. The page shell is the case that settled it: two
of its nine pages carry `data-testid` and `data-resort-id`, so a
`{% page_shell %}` block tag would have needed an attribute escape hatch on
day one. Sharing the class string alone shares exactly the part that drifted.

A **CSS component class** is the exception to "no custom CSS", and it is
narrow: when the markup inside a block is not written in the template —
provider HTML, a long legal document — a wrapper class with descendant rules
is the only treatment that reaches it.

**Where the line is.** Not every repeated string is a component. Twenty-two
places draw a `border-t border-border` rule with some top spacing, in twelve
distinct class strings — a menu separator, a card footer, a settings group, a
panel foot. That is not drift, it is four different spacings doing four
different jobs, and the shared part is already two token-based utilities that
no abstraction would shorten. The test is whether the sites are trying to be
the same thing. Nine page shells were; twenty-two rules are not.

**Class strings built in JavaScript are template class strings.** A string
assembled in `static/js/` reaches the page exactly as one written in a
template does, which is why `bin/ds-lint` scans both. When two modules need the
same string, it belongs in one shared helper, not copied into each.

## What the replica delivered

The replication pass is complete and shipped. It built the canonical layout above against the existing data pipeline: the single-day route and view, the rating-block partial, the always-present headline band driven by `customData.CH.aggregation`, safe rendering of provider prose HTML through the `snowdesk_html` filter, and the metadata strip.

Two of its eight original tasks did not survive contact:

- **The list view move was dropped.** It proposed relocating a ten-bulletin scrolling list to `/<region-id>/history/`. That route was never built and the list view no longer exists in that form — `/random/` is now a permanent redirect to `/examples/random/`, which renders a single bulletin inline. There is no phantom work here to pick up.
- **The side-by-side screenshot test against WhiteRisk was superseded** by `tests/sentinels/test_fidelity.py`, which asks a stricter question: not "does this look like WhiteRisk" but "did every field the provider published arrive on the page".

## Additions on top of the provider's bulletin

These add Snowdesk's own layer alongside the provider content. They are no longer gated behind a phase plan — each is independently shippable, and the fidelity principle already settles the question the old plan was waiting to answer.

- **Day-character label and day summary.** *Shipped.* Sits between the bulletin headline band and the first rating block. The label comes from the cascade below; the one-line summary beside it is selected per bulletin from an 80-cell matrix keyed on how the day moves, what level it reaches, and whether its problems leave surface evidence — [`docs/day-summary.md`](day-summary.md). Derived by `compute_day_character` in [`apps/bulletins/services/render_model.py`](../apps/bulletins/services/render_model.py) and rendered via `templates/includes/day_character_callout.html`.
- **Field guidance beneath each problem's provider prose.** *Shipped — SNOW-673.* Rendered at the foot of every problem card in `public/_rating_block.html`, below a rule, in a collapsible "Field notes" panel that is **closed by default**, and credited to the SLF interpretation guide rather than to Snowdesk — we're paraphrasing SLF, not authoring novel guidance. The credit reads "Source: SLF Avalanche Bulletin Interpretation Guide" and links the guide's [landing page](https://www.slf.ch/en/avalanche-bulletin-and-snow-situation/about-the-avalanche-bulletin/interpretation-guide/), not the PDF — SLF treats only the current online version as binding, so a PDF URL would pin an edition and go stale. The rule sits outside the panel so the provenance boundary is drawn whether the panel is open or shut. The panel is `includes/_collapsible_panel.html` in its `bare` form — no card chrome, since it is nested inside a card that already has some. Collapsing is a deliberate trade: a busy level-3 day grew ~31% longer with every note expanded, and the notes are reference material a reader consults rather than reads through. Absent entirely when the problem type has no entry. See "Field guidance" below.
- **Bulletin diffing logic and change strip.** *Open.* Backend-first: produce a structured diff between consecutive bulletins for the same region using CAAML fields (not prose). Then add a quiet visual strip at the top of the page when material changes have occurred. *Absent* when nothing material has changed — the absence is part of the design.
- **Serif-headline restyling pass.** *Open.* Whether to introduce a serif treatment for `h1`/`h2` elements (page titles, section headers, rating names). Restyling after the structure settled is safer than restyling speculatively.

## Field guidance

The shipped text lives in [`apps/public/field_guidance.yaml`](../apps/public/field_guidance.yaml) — one entry per EAWS problem type, loaded by `apps/public/guidance.py` and attached to every problem card by `_build_problem_card`. It is **not** restated here: this document used to carry its own drafts alongside the YAML, and two copies of a safety text with no owner is precisely the drift the component work of SNOW-672 spent a PR removing. Read the YAML.

What the entries are, and the constraints on editing them:

Each is one paragraph, paraphrased from the SLF Avalanche Bulletin Interpretation Guide (November 2025 edition) — specifically its "Identification of the problem in the field" and "Travel advice" sections — and written in the Snowdesk voice: calm, direct, faithful to the source but rewritten for accessibility. They are sized to sit beneath the provider's prose in a problem card.

- **They paraphrase; they do not author.** The credit line on the page says so. A note that adds advice SLF does not give is a defect, however sensible the advice.
- **They never recommend behaviour.** Same rule as the rest of the page: characterise and explain, don't tell the reader to go or stay.
- **Markup is limited to what `snowdesk_html` allows** (`strong`, `em`, and the structural tags) — everything else is escaped, so a `<b>` reaches the reader as literal characters. That was a real bug in `wet_snow` until SNOW-673.
- **A type with no entry renders nothing.** Adding a problem type does not oblige you to write a note for it; leaving it out is a supported state, not a gap.
- **All entries want review by an experienced tourer or guide**, and checking against the SLF source to be sure no nuance was lost in compression.

## The day-character model


The interpretation layer Snowdesk adds on top of the provider's bulletin. It's documented in the README but worth restating here because the design implications are specific.

Every bulletin gets one of five labels, derived from a deterministic rule cascade over the structured CAAML fields:

- **Stable day.** Conditions are broadly favourable. Main hazards are user error and very steep terrain.
- **Manageable day.** The snowpack has identifiable issues, but they're readable in the field.
- **Hard-to-read day.** The snowpack contains a problem (typically a persistent weak layer) that doesn't announce itself in the field. Field skills don't save you; terrain choices do.
- **Widespread danger.** Dangerous conditions cover most of the typical touring envelope.
- **Dangerous conditions.** Level 4 or 5 territory.

Each label carries a one-line summary beside it. That summary is **not** a fixed gloss on the label — a fixed gloss was what shipped first, and it put "Persistent or gliding-snow problems can mask the real risk" on every hard-to-read page whatever the bulletin actually said. It is now selected per bulletin from the matrix in [`docs/day-summary.md`](day-summary.md), so it names the day's own problems and says whether the danger moves. The labels become a shared vocabulary between Snowdesk and its readers over time; the summary is what makes each one specific.

The cascade rules (provisional, need calibration before the label is rendered):

| Order | Conditions | Label |
|---|---|---|
| 1 | Danger rating 4 or 5 | Dangerous conditions |
| 2 | Danger rating 2+ AND any problem is `persistent_weak_layers` or `gliding_snow` | Hard-to-read day |
| 3 | Danger rating 3 AND (6+ aspects OR lower bound ≤2000m OR 2+ problems) | Widespread danger |
| 3b | Danger rating 3+ subdivision | Widespread danger |
| 4 | Danger rating 2 or 3, no earlier match | Manageable day |
| 5 | Danger rating 1, OR rating 2 with no distinct problem | Stable day |

A spring-pattern rule (e.g. "Race the sun day" for the morning-low/afternoon-considerable wet hazard pattern) was proposed as a sixth **label**. The day-summary matrix now covers the pattern in copy without adding a label: a rising day is stated as deterioration with both levels named, and wet snow is the arriving problem on 254 of the archive's 312 split days. Whether the pattern also deserves its own label is open, but it is no longer unsaid.

Visual treatment when added: distinct zone between the bulletin headline band and the first rating block. Must be clearly attributed to Snowdesk's interpretation rather than to SLF — perhaps a thin coloured left border, a different background tint, or a small distinctive icon. The label needs its own one-line explainer underneath that teaches the concept.

## Open design questions

Two of these arrived from the retired subtraction pass. They survive because they were always *styling* questions wearing a subtraction question's clothes — how loud a thing should be, not whether it should exist.

**Should the rating-block header be that loud?** The filled orange/yellow strips are the loudest visual element on the page. Snowdesk's editorial character would suggest a thinner left-border treatment in the same colour, with the rating name in body-weight type. Worth trying both side by side.

**Is the aspect rosette readable at this size, or does it need a larger treatment?** Worth checking on a real phone.

The other three subtraction questions are **closed** by the fidelity principle, and are recorded here so they are not reopened as if undecided:

- *Does the headline band earn its place on a simple day?* It stays. Consistency of structure across days is worth more than the line it saves, and a reader comparing two days should not meet two different page shapes.
- *Does the snowpack and weather section pad the page?* It renders in full. All four prose fields are `Rendered` in the fidelity ledger, inside collapsible panels — the length is managed by disclosure, not by omission.
- *What happens to the bulletin-level comment?* It is rendered. `avalancheActivity.comment` has a surface today; the question was only ever whether to find it one.

## Archived: the side-by-side variable-day split


An earlier design iteration rendered variable days as two side-by-side period columns (dry on the left, wet on the right, with a `→` between them). This approach is **deprecated** in favour of the WR-canonical vertical-stacked rating blocks for two reasons:

1. It doesn't scale past two periods. Real bulletins occasionally have three (e.g. dry / wet morning / wet afternoon).
2. It doesn't match the mental model of the source bulletin. Stacked rating blocks do.

Only the *rationale* is preserved here, so the approach isn't re-proposed without the two objections above being answered. The implementation is gone: the `bulletin_cards.html` template and its `.split-wrapper` / `.period-col` classes no longer exist anywhere in the tree. A future surface that genuinely wants a horizontal side-by-side layout (e.g. a comparison view between two adjacent regions) starts from the design-system partials, not from a revival of that markup.

## Archived: the replicate-then-subtract phase plan

From April 2026 until SNOW-672, this document described a three-phase method: replicate WhiteRisk's page structure exactly, subtract what didn't earn its place, then add Snowdesk's own layer. Phase 1 shipped. Phase 2 was retired rather than completed, and Phase 3's gate went with it.

Only the *rationale* is preserved, in [`decisions/bulletin-fidelity-over-simplification.md`](decisions/bulletin-fidelity-over-simplification.md), so the method isn't re-proposed without the objection being answered: a pass whose stated principle is "remove anything that doesn't earn its place" cannot coexist with a product claim of completeness, or with a lint guard that fails the build when a provider field reaches no surface.

## Archived: the previous 17-task list


The previous task list (April 2026, pre-replication-decision) is retired. Most of its items either fall out of replication automatically (truncation removal, elevation promotion, problem icon improvements, timing badge softening) or move to Phase 3 (field guidance, day-character label, diff strip, SLF logo). A few small items survive into Phase 1 (alt text fix, region context line restructure, removing the dev-only "Open in admin" link). They're folded into the replication tasks above where relevant.

## How to use this document

This is the input to a design-focused conversation about the bulletin page. Reference it directly: "See `docs/design-system.md` for the fidelity-first principles, the tokens and component layer, the field guidance drafts, and the open questions."

Two companions carry the parts that aren't here. The staff-only component library at `/_components/` is the canonical rendered inventory — read it before adding any visual surface. [`decisions/`](decisions/) holds the *why* behind choices this document treats as settled; if a strategic question surfaces that a decision doc already answers, refer to it rather than re-litigating it here.
