# SnowDesk — Design Handover

This document captures the state of the SnowDesk bulletin card design as of April 2026, including the editorial principles, the current implementation, the prioritised task list for refining it, the field guidance content drafts, and the open design questions worth working through next.

It exists so that a new design-focused conversation can pick up where the previous strategy conversation left off without losing the specific decisions and reasoning. For background on SnowDesk's mission, target audience, competitive positioning, and product shape, see `README.md`. This document is the design-specific complement to that.

---

## What SnowDesk's design has to do

Before diving into specifics, the design exists to support a particular product framing that should shape every visual decision:

SnowDesk is an **on-ramp to the official SLF avalanche bulletin**. It exists to help recreational backcountry skiers begin reading bulletins with confidence. Its success is measured not by retention but by graduation — a user who progresses from SnowDesk to reading the full SLF bulletin directly is the goal, not a loss.

This framing has a load-bearing implication for design: the product treats users as thoughtful adults making real decisions, not as students of avalanche science (which is how WhiteRisk treats them) and not as casual consumers who need to be alarmed (which is how most safety apps treat them). The visual language needs to express respect for the reader's intelligence and time. Calm, confident, quietly expert. Closer to a well-edited publication than a software dashboard.

The cleanest test as features get added: look at the card and ask whether it still feels like editorial content or whether it's drifted toward dashboard. If the latter, the addition needs to be reworked, not because it's wrong as a feature but because the visual character of the card *is* part of the product, not separate from it.

## Current state of the card design

The bulletin card is the building block of the website. The current implementation (as of mid-April 2026) is rendered on a testing surface at `/<region-id>/random/` that displays ten consecutive historical bulletins for a single micro-region as a vertical scrolling list. This is not the final page layout — it's a development tool for getting lots of card variants on screen simultaneously. The card design itself is what's being iterated on.

What the card currently contains, top to bottom:

A **warm cream/orange header strip** containing the EAWS danger pictogram (top-left), the danger level name in large serif type (e.g. "Considerable"), the numeric level beside it ("3"), and a one-line tagline derived from the danger level meaning ("Dangerous off-piste conditions" for Considerable, "Cautious route selection needed" for Moderate).

A **white body section** labelled "AVALANCHE PROBLEMS" containing one block per active avalanche problem from the bulletin. Each problem block has the EAWS problem icon, the problem name in clear sans-serif (e.g. "Persistent weak layers"), an outlined timing pill ("ALL DAY", "LATER (AFTERNOON)"), the elevation qualifier in muted grey ("above 2400m"), and the SLF prose comment for that problem rendered with character-count truncation ending in an ellipsis.

A **footer strip** with the date in monospace ("Sat 11 Apr · 06:00–15:00") and the list of micro-regions grouped together in that bulletin edition ("Münstertal · unteres Puschlav · Corvatsch").

**What's working well:**
- Typography hierarchy with serif headlines and the calm editorial feel
- Muted warm palette that reads as paper rather than as alarm
- EAWS pictogram placement and integration with the danger headline
- Per-problem structure with icon, name, timing, elevation, prose
- Monospace date format with interpunct separators (small detail, real character)
- Card-as-self-contained-unit feel — boundaries between cards are clear without heavy separators

**What isn't yet quite right** (the items the task list addresses):
- SLF prose is truncated mid-sentence at character count, losing the most actionable parts
- Region context line doesn't make clear which micro-region the page is *about* — only lists the grouped regions
- Page header showing the focal micro-region scrolls off and isn't sticky
- Elevation qualifier is too quiet visually given how decision-relevant it is
- Avalanche problem icons are slightly small and similar problem types are hard to differentiate at a glance
- Inter-card spacing is tight enough that consecutive cards run together
- Timing badges are slightly louder than they need to be (less urgent than initially thought)
- Developer-internal alt text on the danger icon needs replacement
- "Open in admin" link is currently visible on the testing surface

## Editorial principles for the card

These are the principles that should govern any addition or change to the card design. They're derived from longer conversations about SnowDesk's positioning and should be treated as constraints, not suggestions.

**Never recommend behaviour.** SnowDesk characterises the day and explains the bulletin. It does not tell users to go or to stay. The line between "information" and "judgement" exists for good reasons in Swiss avalanche culture, and crossing it has real consequences. Even features that introduce SnowDesk's own interpretation (the day-character labels) are deliberately descriptive rather than prescriptive.

**SLF is the authoritative source, and the card should make this visible.** The bulletin prose comes from SLF; SnowDesk renders it faithfully. Anywhere SnowDesk adds its own content alongside the SLF source — plain-language sentences, day-character labels, field guidance — the visual treatment must distinguish the SnowDesk layer from the SLF layer so users can tell which content comes from where.

**The templating table is the highest-leverage editorial work in the project.** A bad sentence in a problem card fails the user directly. Hand-write every template. Review with an experienced tourer or guide. Test against real bulletins across a variety of conditions. Faithfulness beats cleverness; clarity beats polish.

**When in doubt, be conservative.** The cost of characterising a borderline day cautiously is a mild user complaint. The cost of the opposite error is not comparable.

**The voice is calm, confident, quietly expert.** Direct and unpatronising. No extreme-sports energy. No emojis. No cheerful disclaimers hiding uncertainty. The tone should survive being pasted verbatim into a WhatsApp group without feeling out of place.

**Restraint is part of the brand.** When adding new content (field guidance, day-character labels, change indicators), the temptation is to add visual weight or accent colours to make additions feel "important." Resist. The information density of the current card is high enough; what makes it work is the restraint in how the information is presented. New features should be added in the same restrained idiom — small, quiet, deferential to the existing hierarchy — not as visual additions that compete for attention.

## Task list — in priority order

This list is the result of looking at the card on a real phone and thinking about what would most improve it without changing its fundamental character. Tasks are ordered so that earlier tasks set up the conditions for later ones (e.g. removing truncation before adding field guidance beneath the prose, because the field guidance layout depends on the prose being its full length).

The list has seventeen items. You don't need to do all of them before showing the card to real users — see the "when to ship" note after the list.

1. **Replace the developer alt text** on the EAWS danger icon. Currently reads `dangerRatings[*].mainValue (highest)`; should read something like "Danger level 3, Considerable". Five-minute task. Accessibility win.

2. **Restructure the region context line in the footer.** Change from "Münstertal · unteres Puschlav · Corvatsch" to "Val dal Spöl, with Münstertal · unteres Puschlav · Corvatsch" (or similar). The user's micro-region needs to appear first, with the grouped regions explicitly framed as additional context. A user landing on the page from a shared link, or scrolling past the header, needs to see at a glance that yes, this bulletin is about their region.

3. **Make the page header sticky on scroll.** The "VAL DAL SPÖL · LAST 10 DAYS" line at the top of the page should remain visible as the user reads down the list, anchoring the whole page. Small frontend change, real clarity improvement.

4. **Fix the truncation by removing it.** Show the SLF prose in full for both avalanche problems. No expand affordance, no "read more" link — just the full text. The card becomes taller; this is fine because it's a bulletin page, not a feed. Important to do before subsequent additions because the field guidance sits beneath the prose and the layout decisions for that depend on having the full prose in place.

5. **Promote the elevation qualifier to body weight.** "Above 2400m" currently sits in muted grey under the problem name; move it to the same visual weight as the body text so it reads as substantive information rather than as metadata. The elevation band is the second-most decision-relevant piece of information on the card and shouldn't be hidden.

6. **Add SLF attribution to the prose blocks.** Each avalanche problem currently shows the SLF prose without explicit attribution. Add a small "— SLF assessment" or similar marker at the end of each prose block, in muted text. This is the foundation for the next task (adding SnowDesk-derived field guidance that needs to be visually distinguished from the SLF source).

7. **Increase inter-card vertical spacing.** When card two starts immediately after card one ends, there isn't quite enough vertical space for the eye to register the boundary. Add 16-24 extra pixels between cards.

8. **Soften the avalanche problem icons** by giving them slightly larger sizing (perhaps 32px instead of 24px) and small background tints to differentiate the problem types visually. Persistent weak layer and wet snow icons are currently too similar in colour and texture for at-a-glance differentiation.

9. **Add the SLF interpretation guide field guidance for the persistent weak layer problem only.** As a test of the layered approach. The field guidance text (drafted below) slots in beneath the SLF prose, separated by a horizontal rule, with its own attribution. Look at the card afterwards. Does the additional content feel like it belongs, or does it tip the card toward dashboard? If it belongs, proceed. If it tips, rework the visual treatment before extending to other problems. Persistent weak layer is the right test case because it's the most consequential problem and the place where the on-ramp framing pays off most.

10. **Soften the timing badge.** The "ALL DAY" / "LATER (AFTERNOON)" pills currently compete with the problem name. Either reduce the visual weight (lighter outline, smaller font, sentence case instead of all caps) or move the timing inline with the problem name as italic text.

11. **Extend field guidance to the other four problem types.** Once the persistent weak layer version has been validated, write the rendering for new snow, wind slab, wet snow, gliding snow, and the no-distinct-problem case.

12. **Add SLF logo or wordmark to the footer.** Something like "Bulletin data from SLF" with the SLF mark, in the same visual zone as the date and region context. Visiting tourers and beginners may not know who SLF is, and the credibility of the card depends on them understanding the source.

13. **Day-character calibration** (logic only, no UI yet). Run the day-character rules cascade against your stored bulletins and produce the label for each historical bulletin in the database. Don't render the labels yet — just compute them and check that the distribution looks right (most days Stable or Manageable, Hard-to-read days clustered around persistent weak layer events, Widespread danger and Dangerous conditions rare). If the distribution is off, the rules need calibration before they're worth surfacing.

14. **Add the day-character label to the card** in its own visual zone, distinct from the SLF danger headline above. The label sits between the danger headline and the avalanche problems list. Visual treatment needs to make clear it's SnowDesk's interpretation, not SLF's — perhaps a thin coloured left border, or a different background tint. The one-line explainer underneath teaches the concept ("The snowpack contains a problem you can't reliably read in the field..."). Highest-risk addition for the card's character because it's where SnowDesk steps furthest from being a faithful renderer toward being an interpreter.

15. **Implement bulletin diffing logic.** Backend work. The diff produces a structured list of change events between two consecutive bulletins for the same micro-region, using the structured CAAML fields (not the prose). Output is a list of change events with type, severity, and a renderable description. No UI yet.

16. **Add the change strip to the top of the card** when material changes have occurred since the previous bulletin for the same micro-region. Quiet visual treatment — a thin coloured strip with one or two lines of text — and crucially, *absent* on cards where nothing material has changed. The absence is part of the design.

17. **Remove the "Open in admin" link** from the public-facing card template before any external user sees it.

## When to ship to first users

Looking at the current state, the card is closer to "ready to show real users" than the desktop screenshots first suggested. My recommendation is **after task 9** in this list — once you have:

- The un-truncated prose
- The fixed region context
- The sticky header
- The better inter-card spacing
- The improved icons
- The elevation promoted
- The SLF attribution
- The field guidance for the persistent weak layer problem as a test case

That's nine small-to-medium tasks, all focused on refining and extending what's already working rather than adding new architectural pieces. None of them require the day-character labels or the diff strip, both of which are bigger commitments that benefit from user feedback before being built.

Tasks 10-17 are the more ambitious additions and they're worth doing, but they're also where you most need user feedback before committing. Shipping the card after task 9 to ten users, having conversations about what they actually wanted, and *then* deciding whether tasks 10-17 are still the right priorities is better than building all seventeen tasks in isolation and hoping the result resonates.

You have an unusually good visual foundation already, and the temptation will be to keep refining it before showing it to anyone. Resist that. The next ten users can give you feedback worth more than another month of solo iteration.

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

## The day-character model (for tasks 13-14)

This is included for context because it's the bigger editorial commitment that comes after the field guidance is in place. It's documented in the README but worth restating here because the design implications are specific.

Every bulletin gets one of five labels, derived from a deterministic rule cascade over the structured CAAML fields:

- **Stable day.** Conditions are broadly favourable. Main hazards are user error and very steep terrain.
- **Manageable day.** The snowpack has identifiable issues, but they're readable in the field.
- **Hard-to-read day.** The snowpack contains a problem (typically a persistent weak layer) that doesn't announce itself in the field. Field skills don't save you; terrain choices do.
- **Widespread danger.** Dangerous conditions cover most of the typical touring envelope.
- **Dangerous conditions.** Level 4 or 5 territory.

Each label needs a one-paragraph plain-language explainer that teaches the concept. The labels become a shared vocabulary between SnowDesk and its readers over time, supporting the on-ramp goal.

The cascade rules (provisional, need calibration in task 13):

| Order | Conditions | Label |
|---|---|---|
| 1 | Danger rating 4 or 5 | Dangerous conditions |
| 2 | Danger rating 2+ AND any problem is `persistent_weak_layers` or `gliding_snow` | Hard-to-read day |
| 3 | Danger rating 3 AND (6+ aspects OR lower bound ≤2000m OR 2+ problems) | Widespread danger |
| 3b | Danger rating 3+ subdivision | Widespread danger |
| 4 | Danger rating 2 or 3, no earlier match | Manageable day |
| 5 | Danger rating 1, OR rating 2 with no distinct problem | Stable day |

Visual treatment when added: distinct zone between the SLF danger headline and the avalanche problems list. Must be clearly attributed to SnowDesk's interpretation rather than to SLF — perhaps a thin coloured left border, a different background tint, or a small distinctive icon. The label needs its own one-line explainer underneath that teaches the concept.

## Open design questions

A few things that haven't been resolved and are worth thinking about in the new design conversation:

**The no-distinct-problem stable day case.** The current card design assumes there's at least one avalanche problem to render. On days with `no_distinct_avalanche_problem` and Level 1 rating, the card has very little to render — just the danger headline and possibly a single brief note. What does this card look like? It needs to feel intentionally quiet rather than empty, and it needs to honour the SLF caution that "no distinct problem" doesn't mean "safe." This case should be designed deliberately before committing too hard to the current layout assumptions.

**The very-dangerous-day case (Level 4 or 5).** At the other end, the card needs to handle days where multiple problems are active, the rating is High, and the bulletin prose is longer and more urgent. Does the calm editorial treatment hold up when the content is genuinely alarming? The brand character requires that it does — SnowDesk shouldn't suddenly become a flashing-red dashboard on dangerous days — but it needs visual treatment that acknowledges the seriousness without abandoning the editorial tone.

**The longitudinal list view.** The current testing surface stacks ten cards vertically. Is this the right format for the actual bulletin page, or is the canonical bulletin page a single-day view with day-navigation controls? The list view has real strengths (longitudinal pattern visible at a glance, the diff strip becomes more useful, matches how experienced tourers think about conditions) and real weaknesses (longer pages, harder to share a specific day, potentially overwhelming for casual users). A hybrid is possible. Worth deciding deliberately rather than letting the testing surface become the production design by default.

**The SnowDesk visual identity beyond the card.** The card design is strong but the surrounding website (homepage, about page, sample email page, account management) hasn't been designed yet. The card sets a visual direction — paper-like, restrained, editorial — and the rest of the site needs to extend it consistently. Worth thinking about as a system rather than letting each page evolve in isolation.

**Email rendering vs web rendering.** The card design uses typography and spacing decisions that work in a browser but don't necessarily translate to email clients. The 5pm and 7am subscription emails need their own visual treatment that captures the same character within email's much tighter constraints (limited CSS support, no custom fonts, dark mode quirks, forwarding fragility). This is its own design conversation and hasn't been started yet.

## How to use this document

This is intended as the input to a new design-focused chat. When starting that chat, reference this document directly: "I'm continuing the SnowDesk design work. See `DESIGN.md` for current state, task list, field guidance drafts, and open questions." The new conversation can then dig into any of the open questions, refine specific tasks, or work through the cards for the cases that haven't been designed yet (no-distinct-problem, very-dangerous-day, the email format).

The strategy conversation that produced this document remains in its own chat and shouldn't need to be re-derived. If a strategic question comes up in the design work that's already been resolved in the strategy chat, refer back to it rather than re-litigating it here.
