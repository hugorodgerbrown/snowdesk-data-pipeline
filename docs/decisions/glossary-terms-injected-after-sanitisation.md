---
name: glossary-terms-injected-after-sanitisation
description: EAWS glossary terms are marked up after bleach.clean, in a native popover with no JavaScript; the bleach allowlist stays narrow
status: current
last-reviewed: 2026-09-06
---

# Glossary terms are injected after sanitisation

**Decision.** `snowdesk_html` runs `bleach.clean` → inject glossary markup →
`mark_safe`, in that order. The injection wraps each EAWS term's first
occurrence per block in a `<button>` paired with a native `[popover]`
carrying the standard's definition. The bleach allowlist —
`h1 h2 p ul li strong em`, no attributes — is **unchanged**, and stays
that way.

## Why

**Why after, not before.** Injecting first would mean teaching bleach to
allow `button`, `span`, `a`, `popover`, `popovertarget`, `id`, `class` and
`href` so its own output survived the clean. Every one of those permissions
would then apply to *provider prose* as well, because bleach cannot tell
whose markup it is looking at. A provider could ship a `<button>`, or an
`id` that collides with one of ours. Injecting downstream buys the same
result with none of that: bleach never sees the injected markup, so the
allowlist never has to admit it. The narrow list is not an oversight to fix
later — it is narrow *because* the injection is downstream. This corrects
point 4 of the SNOW-853 ticket description, which asked for the allowlist to
be widened.

**Why a stdlib parser is enough.** At the point of injection the input is
known to be six structural tags with no attributes, because bleach just made
it so. `html.parser.HTMLParser` walks that safely with no new dependency;
there is no HTML tree parser in the runtime deps (bleach vendors its own
tokenizer and does not export one). Every hook other than `handle_data`
re-emits its source byte-identically, start tags via `get_starttag_text()`,
so the only thing that changes is text.

**Why native `popover` and no JavaScript.** The platform gives tap-to-open,
light-dismiss, Escape and top-layer placement for free. `title` was rejected
because it is inert on touch and a phone is where bulletins are read. A JS
module would have added a `static/js` file, an i18n-lint surface and a Vitest
suite to reimplement behaviour the browser already has. An older browser
degrades to the term rendering as marked text with the definition
unreachable — stated here rather than discovered later.

**Why committed data, not a fetch.** `apps/public/eaws_glossary.yaml` is a
curated subset of the ~165 EAWS entries: the vocabulary that actually turns
up in provider prose. It is a few dozen short paragraphs of safety text that
change about never, so a runtime fetch would add a failure mode and a cache
to a file a human should read before it ships. Same shape and same reasoning
as `field_guidance.yaml`.

**Why the text is verbatim.** The definitions are copied word-for-word from
<https://www.avalanches.org/glossary/> (© Avalanche Warning Service Tyrol),
including the source's own typos. Snowdesk paraphrases the SLF interpretation
guide in `field_guidance.yaml` and credits it as a paraphrase; it does not
paraphrase a *definition*. Rewriting what "weak layer" means, however
sensibly, would make Snowdesk the author of a safety definition it is only
relaying. Editing a `text:` value is therefore a defect, not a tidy-up; the
`synonyms` and `anchor` keys are ours and can be edited freely.

## Consequences

- **The allowlist must not be widened for this feature.** If a future change
  wants `<a>` in provider prose, it needs its own reason and its own ticket.
- **Definitions are not re-scanned.** The definition string goes straight to
  the output, so a definition that mentions another term does not itself gain
  a button. One level of explanation, deliberately.
- **Popover ids come from a process-global counter, not from the content.**
  `id` uniqueness is scoped to the *page*, and a page is many independent
  `snowdesk_html` calls that cannot see each other. A content digest was
  tried first and is wrong for exactly that reason: a hash is a pure function
  of its input, so two calls handed byte-identical prose emit identical ids —
  reachable in production, because `guidance.py` keys its note text on
  `problem_type` alone and two problem cards of one type render the same
  string twice. `popovertarget` resolves to the first match, so the reader
  taps one term and reads another's definition. The ids are therefore not
  stable between renders; they are wiring between a button and its own
  popover, never an addressable anchor, and nothing links to them.
- **Every surface that pipes prose through `snowdesk_html` gets this**, which
  includes Snowdesk's own field guidance. That is intended: a term is a term
  wherever the reader meets it.
- **`bin/ds-lint` cannot see these class names.** It scans templates and
  `static/js`; this markup is a Python format string. Token compliance in
  `.glossary-term` / `.glossary-def` / `.glossary-src` is a manual check at
  review time.
- **Tests that assert on a prose sentence containing a term** must read
  through `tests/glossary_markup.strip_glossary_markup`, which subtracts the
  wrappers. `tests/sentinels/fidelity.py` does this inside `visible_text`,
  for the same reason it strips `<script>`: a reader sees neither at rest.
