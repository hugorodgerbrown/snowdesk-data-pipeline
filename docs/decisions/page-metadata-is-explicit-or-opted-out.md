---
name: page-metadata-is-explicit-or-opted-out
description: Every page emits page identity via includes/_page_meta.html, or opts out with sharing=False and a written reason; there is no third state
status: current
last-reviewed: 2026-07-29
---

# Page metadata is explicit, or opted out in writing

**Decision.** Every template extending `public/base.html` overrides
`{% block page_meta %}` with exactly one
`{% include "includes/_page_meta.html" %}`, supplying a `title` and a
`description`. A page that should not produce a link-unfurl card passes
`sharing=False` **and** carries a template comment saying why. There is no
third option: no page may simply not set its metadata. There are no separate
`title` / `meta_description` blocks — the partial is the only thing in the
codebase that emits `<title>`, the description meta, `og:url`, or the
`og:`/`twitter:` title and description pair.

**Why.** Before SNOW-553 only the home and bulletin pages set the three tags
that identify a page. Nine others — including the resort page, which SNOW-504
built specifically as an indexable, shareable URL — unfurled in Slack,
WhatsApp, X and LinkedIn as a generic, un-deduped card. None of that was a
decision. Silent omission is indistinguishable from a deliberate choice, so
nobody reviewing those templates had anything to notice.

Routing every page through one emitter turns the absent case into a visible
one. It also fixes two failures at their source rather than per page: the
`|squish` filter stops `djangofmt` reflowing a block body into literal
newlines inside a `content="…"` attribute (which reached the Google SERP
snippet), and building `og:url` from `settings.SITE_BASE_URL` stops the page
self-canonicalising to whatever `Host` header a crawler happened to arrive on.

**Consequences.** A new page that forgets `page_meta` inherits base.html's
empty fallback and fails `tests/public/test_page_meta.py`, which walks every
public URL and asserts each is in exactly one of the two states. Adding a page
therefore means deciding, in the template, whether it is shareable.

`og:url` is emitted even for opted-out pages: it is the page's own identity,
not a sharing affordance, and a page that declines a card still benefits from
crawlers agreeing on which URL it is.

Site-wide constants — `og:site_name`, `og:type`, `og:locale`, `og:image` and
its dimensions and alt text, `twitter:card` — stay in base.html's `og_tags`
block. They do not vary per page, so they do not belong in a per-page emitter.
Pages should not need to override `og_tags` at all; SNOW-555 parameterises
`og:type` through `page_meta` rather than reopening it.
