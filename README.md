# SnowDesk

**Plain-language avalanche bulletins for the Swiss Alps, delivered to your inbox.**

SnowDesk is an on-ramp to the official Swiss avalanche bulletin. It exists to help recreational backcountry skiers and ski tourers begin reading SLF bulletins with confidence, by rendering each day's bulletin in plain language and characterising the day in terms that map onto how people actually plan tours.

SnowDesk is not a forecasting service, not a recommendation engine, and not a substitute for avalanche education or judgement. Its success is measured not by retention but by graduation: a user who progresses from SnowDesk to reading the full SLF bulletin directly is the goal, not a loss.

Commercial success is a secondary concern. SnowDesk is built to find an audience and serve it well.

---

## Mission

The Swiss avalanche bulletin is authoritative, free, and dense. It is written for people who already know how to read it. Most recreational tourers either don't read it, read it superficially, or rely on a friend or guide to interpret it.

SnowDesk closes that gap by delivering a daily plain-language summary of the bulletin to subscribers' inboxes, and by hosting a website where any micro-region's bulletin — current or historical — can be browsed in the same accessible format. Over time, users learn the vocabulary and visual conventions of the official bulletin by using SnowDesk, and graduate to reading SLF directly.

## Target audience

The **primary user** is the competent intermediate Swiss-based ski tourer — someone who has done an avalanche course, owns the gear, tours fifteen to forty days a season, and is capable of reading a bulletin but finds the official format dense or intimidating. They are the largest underserved segment in the Swiss market.

The **secondary audience** is visiting tourers from Germany, Austria, the UK, and the Nordics who ski Switzerland one to three weeks a year and are linguistically and culturally adrift in the SLF ecosystem. This group has higher willingness to engage with multilingual plain-language rendering and represents the most natural growth segment.

Explicitly outside the target: professional guides and forecasters (already served by WhiteRisk), absolute beginners (should be hiring a guide, not subscribing to a bulletin service), and resort-only off-piste skiers.

## Competitive positioning

The Swiss market is dominated by **WhiteRisk**, built by the SLF/WSL Institute itself. It is free, exceptionally well-respected, and treated by guides and patrol teams as the de facto reference. WhiteRisk is strong on data authority and tour planning depth but built like a reference work, not a decision tool — it treats users as students of avalanche science, which is appropriate for a research product and wrong for a tired person at 6am trying to figure out whether today's plan still makes sense.

SnowDesk does not attempt to out-data SLF (impossible and unnecessary — SLF is the data source). It does not attempt to compete with the social and trip-planning layer of the mountain sports stack (Oak increasingly owns this, and it is a better partner than a competitor). SnowDesk's wedge is design and tone: the same official data, rendered for decision-making rather than study.

**Oak**, the fast-growing mountain-sports community app, is a natural strategic complement. Oak owns "who am I going with and what are we doing"; SnowDesk owns "is it safe to go and where exactly should we go." The two products have overlapping audiences and non-overlapping jobs-to-be-done, and a future integration or partnership is a deliberate design goal.

## Value proposition

SnowDesk delivers four things that no other product in the Swiss market currently combines:

**Plain-language rendering** of the structured fields in the SLF bulletin. Avalanche problems, aspects, elevations, and danger levels are translated into sentences a friend would say to you, not the clinical prose of a forecaster document. Multi-language from launch (English, German, French, Italian), with sentences hand-templated in each language rather than machine-translated.

**Day-character labels** that characterise the day in terms of how the snowpack is likely to behave, not what the user should do. Five labels — Stable day, Manageable day, Hard-to-read day, Widespread danger, Dangerous conditions — derived from a deterministic rule cascade over the structured bulletin fields. The rules privilege the *type* of avalanche problem alongside the danger rating, so that a Moderate day with a persistent weak layer is correctly characterised as Hard-to-read rather than as routinely manageable. Labels describe the world; they never prescribe behaviour.

**Change detection between consecutive bulletins.** Each rendered bulletin is diffed against the previous one using structured fields (not prose). Material changes — danger rating shifts, new or resolved problems, expanded aspect or elevation footprint — surface as a short change strip on the bulletin page and as a one-line delta in the planning email. Prose changes without structural changes are deliberately suppressed.

**A daily email that meets users where they plan.** The 5pm email arrives just after the evening SLF update, designed to be read at the dinner table while friends discuss tomorrow's plan. The 7am email confirms or updates the picture before users leave the car park. Neither email recommends behaviour; both characterise the day and link through to the full SLF bulletin as the authoritative source.

## Product shape

SnowDesk consists of two delivery surfaces sharing a single content engine: **a daily email subscription** and **a public website**. The email is the primary product; the website is its companion.

There is no native mobile app. The cost of asking users to install and trust another app is disproportionate to the value exchange, and a well-built mobile website delivers the same experience without the friction.

### The emails

Subscribers choose a micro-region and a send-time preference (5pm, 7am, or both).

The **5pm email** is the planning email. It contains a one-line day-character headline, a one-sentence delta from today's conditions, a short plain-language explanation of the active avalanche problem, a one-line weather summary, and prominent links to the full SLF bulletin and to the SnowDesk website. Readable in under thirty seconds, designed to survive being read aloud at a dinner table or forwarded into a group chat.

The **7am email** is the confirmation email — short by default, leading with what changed overnight if anything material has, restating the current character and problem briefly, and noting current weather. On stable mornings the 7am email is two sentences. This deliberate brevity trains a reflex: when SnowDesk is short in the morning, the plan is still the plan.

Both emails are prose-first with minimal imagery (at most a danger-level pictogram inline), because more complex graphics don't survive email reliably across clients and forwarding.

### The website

A small public site that renders bulletins in the same plain-language style as the emails, with navigable history for each micro-region. The site is read-only from the user's perspective, with one exception: a subscription form on the homepage. No user accounts in the conventional sense — subscription management is handled via magic links from email footers.

The website uses the **EAWS icon set** — danger level pictograms, aspect roses, elevation bars — alongside plain-language sentences. The icons build credibility with experienced users and teach the visual language to new ones, reinforcing the on-ramp framing.

A key feature is **day navigation**: from any bulletin page, users can step backward or forward day by day through the season's archive for that micro-region, or jump directly to a specific date. This lets users develop intuition for how conditions evolve — a kind of learning no other product in the Swiss market supports well. The change strip is rendered on historical pages as well as current ones, so scrolling through the archive reveals the narrative of the season: "the weak layer appeared on the 8th, danger climbed to Considerable on the 10th, dropped back on the 14th, but the weak layer persisted."

Historical bulletin pages carry consistent visual and editorial markers — a banner at the top, dated section headings throughout, and a subtle archive treatment — to prevent any possibility of confusing a historical bulletin for current conditions.

### The day-character model

Every bulletin is rendered with one of five labels, derived from a deterministic rule cascade:

- **Stable day.** Conditions are broadly favourable. Main hazards are user error and very steep terrain.
- **Manageable day.** The snowpack has identifiable issues, but they're readable in the field. The day rewards attention and punishes carelessness.
- **Hard-to-read day.** The snowpack contains a problem (typically a persistent weak layer) that doesn't announce itself in the field. Field skills don't save you; terrain choices do.
- **Widespread danger.** Dangerous conditions cover most of the typical touring envelope.
- **Dangerous conditions.** Level 4 or 5 territory. The bulletin is clearly saying "not today."

Each label is accompanied by a one-paragraph plain-language explainer. Over weeks of use, the labels become a shared vocabulary between SnowDesk and its readers — and the explainers teach the underlying concepts in the same vocabulary SLF uses in the full bulletin, supporting the on-ramp goal.

## Sitemap

```
/                                   Homepage
                                    Hero (two-line headline) →
                                    Featured bulletin (full render of one micro-region) →
                                    Subscription form →
                                    Three short paragraphs (what's in the email, who it's for, data source) →
                                    Footer

/sample                             Sample email page
                                    Renders a recent 5pm and 7am email pair as HTML

/bulletin/{region-slug}             Current bulletin for a micro-region
                                    Redirects to /bulletin/{region-slug}/{today's-date}

/bulletin/{region-slug}/{date}      Bulletin page for a specific date
                                    Same template for current and historical days
                                    Historical days carry visual and editorial indicators
                                    Day navigation: previous, next, jump-to-date, jump-to-today

/account                            Subscription management
                                    Accessed via magic link from any email footer
                                    Edit region, send-time, pause-until, unsubscribe

/about                              About / colophon
                                    Mission, attribution, credits, contact, disclaimer
```

The homepage shows a real bulletin — not a marketing representation of one — because SnowDesk's value is hard to describe but immediate to demonstrate. A visitor lands on the site and sees exactly what a subscriber receives on any given day, alongside the form to subscribe themselves. The featured region refreshes once per day using a "most interesting region" algorithm, with a subtle affordance to cycle to other regions.

## Roadmap (first 30 days)

The 30-day plan is optimised for finding an audience and earning their trust, not for feature volume or monetisation. The centre of gravity is the email, not the website, and the goal by the end of the month is to have ten real Swiss tourers receiving daily emails and providing feedback.

**Week 1 — Data backbone.** Finish the SLF bulletin pipeline: pagination, upsert, handling multi-edition days and gaps. Decide on micro-region as the unit of "today." By end of week, the database reliably reflects current SLF bulletins every day.

**Week 2 — The email.** Parse CAAML into the structured-claims model. Implement the day-character rules conservatively. Hand-write the templating table for the most common problem patterns, with an experienced tourer reviewing the output. Compose the 5pm email template, render it for a week of recent bulletins, send to self, read at dinner, iterate. The email is the product this week.

**Week 3 — The website and the morning email.** Build the website (homepage with featured bulletin, subscription form, bulletin pages with day navigation, account management via magic links, sample and about pages). Implement the diffing logic. Compose the 7am email template. Connect the subscription form end-to-end so real subscribers can receive real emails.

**Week 4 — Real users.** Recruit ten Swiss-based tourers (personal network is fine), get them subscribed, send them a week of emails. After their weekend, have individual conversations — not surveys — about whether they read the email, whether they forwarded it, whether they talked about it with touring partners, whether they clicked through to SLF. The output is a written learning document that becomes the input to month two.

Explicitly **not in the first 30 days**: no marketing site beyond the homepage, no monetisation, no second sport, no second country, no social features, no Oak integration, no native app, no offline-first engineering, no map layer, no tour route library, no GPX import, no notifications beyond the scheduled emails, no user accounts with passwords.

Each of these is defensible eventually. None of them belongs in the first 30 days, because none of them helps answer the only question that matters: *do real Swiss tourers find this useful enough to open on planning nights and tour mornings?*

## Editorial principles

The stack doesn't determine whether SnowDesk succeeds — the editorial work does. A few principles worth writing down:

**Never recommend behaviour.** SnowDesk characterises the day and explains the bulletin. It does not tell users to go or to stay. The line between "information" and "judgement" exists for good reasons in Swiss avalanche culture, and crossing it has both ethical and practical consequences. Even the five day-character labels are deliberately descriptive rather than prescriptive.

**The templating table is the highest-leverage work in the project.** A bad sentence in a problem card fails the user directly. Hand-write every template, review with an experienced tourer, test against real bulletins across a variety of conditions. Faithfulness beats cleverness; clarity beats polish.

**When in doubt, be conservative.** The cost of characterising a borderline day as hard-to-read when it's really borderline-manageable is a mild user complaint. The cost of the opposite error is not comparable.

**The voice is calm, confident, quietly expert.** Closer to a trusted mountain partner than a software product. Direct and unpatronising. No extreme-sports energy. No emojis. No cheerful disclaimers hiding uncertainty. The tone should survive being pasted verbatim into a WhatsApp group without feeling out of place.

**SLF is the destination, not the competitor.** Every email and every bulletin page links prominently to the full SLF bulletin. The on-ramp metaphor is taken seriously: a user who graduates to reading SLF directly is a success, not a loss. Credit SLF/WSL explicitly wherever their data is used, including the EAWS attribution for the icon set.

**Privacy as part of the brand.** SnowDesk collects only the email address and subscription preferences. No tracking, no third-party analytics, no behavioural targeting. The privacy posture is visible in the about page.

## Attribution and credits

Avalanche bulletin data is provided by **SLF / WSL Institute for Snow and Avalanche Research**, Davos, Switzerland, via their public API. SnowDesk renders this data in a derived format; it does not author or alter the underlying assessments.

Danger level pictograms, aspect roses, and elevation diagrams are from the **European Avalanche Warning Services (EAWS)** and used under their terms with attribution.

SnowDesk is an independent project and is not affiliated with SLF, WSL, EAWS, or any official forecasting service.

## What SnowDesk is not

Worth saying explicitly, because the temptation to expand scope is real:

- Not a forecasting service. SnowDesk does not produce avalanche forecasts; it renders the ones SLF already produces.
- Not a recommendation engine. No "go or no-go" indicator, no suggested tours, no terrain routing.
- Not a substitute for avalanche education, a qualified guide, or the user's own judgement.
- Not a social network or trip-planning platform. Partner-finding, trip organising, and community features belong to other products (notably Oak), and SnowDesk deliberately does not compete on that axis.
- Not a commercial product in its current form. There is no paid tier, no premium features, no monetisation plan.
- Not a replacement for WhiteRisk. Power users who want the full depth of SLF's tour-planning tools should continue to use WhiteRisk. SnowDesk is an on-ramp, not a competitor.

---

*SnowDesk is a personal project built to find its audience and serve it well. Feedback, corrections, and conversations are welcome.*
