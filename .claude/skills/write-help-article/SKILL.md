---
name: write-help-article
description: |
  Write one end-user help article for a Snowdesk feature you name, as a new
  page under /help/ — plain language, step-by-step, what to click and what to
  expect. Invoke it with the topic and, optionally, the angle:
  "/write-help-article on Routes - what they are, how they work, where to find
  them". Use whenever the user asks for a help article, user-facing
  documentation, a how-to or a user guide for a Snowdesk feature; names a
  feature and asks for it "explained for users"; or says the help doesn't
  cover something. Do NOT use for internal or developer documentation
  (CLAUDE.md, docstrings, anything under docs/ — that is the documenter
  agent), for the avalanche domain primer at /how-to-read-a-bulletin/, or for
  Linear ticket scoping.
allowed-tools: Bash, Read, Write, Edit, Grep, Glob
---

# Write a help article — $ARGUMENTS

## Read the brief

`$ARGUMENTS` names the feature and, usually, the angle:

> on Routes — what they are, how they work, where to find them

- **The feature** is the subject. One article, one feature.
- **The angle**, when given, is the outline. Those three clauses are the
  article's sections, in the order asked. Someone who says "what they are,
  how they work, where to find them" has told you what they think a reader
  is missing — answer that, in that order, before anything you think should
  also be in there.
- **No angle given?** Use the default shape in Step 3.

If the named feature doesn't exist in the product, say so and stop rather
than writing an article about something you inferred.

## What you are adding — and what you must not touch

`/help/` today is an **FAQ accordion**: eighteen collapsible panels, one per
feature, each a few tight paragraphs. It stays exactly as it is. It is
valuable and it is finished work — accurate, reviewed, already translated
into every locale the site ships.

You are adding something alongside it: **one article, at its own URL under
`/help/`**, that walks a reader through the feature properly.

| | FAQ panel | Article |
|---|---|---|
| Job | The quick answer | The walkthrough |
| Length | A few paragraphs | A page |
| Read when | Checking one fact | Learning the feature |

**The only change you may make to an existing panel is adding one link to
your article.** Do not reword its copy, even where you would have written it
differently. Every string in it is a `blocktrans` msgid: change the English
and you orphan its translation in every locale, so a rewrite you think is a
small improvement silently reverts that paragraph to English for everyone
else. If you believe a panel is actually *wrong*, that is a separate fix with
its own ticket — say so, and don't quietly correct it inside your article.

## The reader

Someone sitting down with the app and time to spare — exploring after signing
up, or planning ahead of a trip. Curious, not stuck. English may be their
second language; the site is translated, so your copy will be too.

**Help is read at leisure, not in the field.** If someone is looking this up
mid-decision, something has already gone wrong: a control that can only be
understood by reading about it is a design problem, not a documentation gap.
When you hit one while writing, raise it as a UI finding rather than writing
a paragraph that compensates for it.

That settles most questions of tone:

- **Simple and informative beats terse.** There is no stopwatch. Explain the
  feature properly — what it is, how to use it, what it won't do — in plain
  words, and don't clip a useful sentence for brevity's sake.
- **Be exact about what the product does and does not do.** "Routes work
  offline" is a promise; if what actually happens is that the lines stay
  drawn but the list needs a connection, say that. An unhurried reader
  notices the gap later, and the whole page loses credit when they do.

## The line you must not cross

`/help/` documents **the product**. `/how-to-read-a-bulletin/` teaches **the
domain**. The split is deliberate and `apps/public/views.py::help_page` says
so in its docstring.

"Tap a region to open its bulletin" is help. "A considerable rating means
human-triggered avalanches are likely" is the bulletin guide. If your article
starts explaining what a wind slab is, link to the guide and carry on.

## Step 1 — Read what already exists

Before writing a word, read the feature's existing panel under
`apps/public/templates/public/help/` — **including its `{% comment %}`
block**, which is where the reasoning lives. `_topic_routes.html` records
that its two caps are described without their numbers because both are
env-overridable, so a figure written there would be a claim the page can't
keep true. That is exactly the kind of thing you would otherwise get wrong.

Your article must:

- **agree with the panel** — two help surfaces contradicting each other is
  worse than one thin one;
- **go beyond it** — if your article is the panel with more words around it,
  it has no reason to exist. The panel already says what the feature is; your
  job is to show someone using it.

Then read the feature's own templates and view for the states the panel
doesn't mention: signed out, empty, offline, at a limit.

## Step 2 — Use the thing before you describe it

Do not write help from reading the code. The code tells you what is possible;
it does not tell you what the screen says or what happens when you tap. Four
claims on `/help/` had quietly stopped being true by the time SNOW-744
audited them, and every one would have survived another code-reading.

```bash
uv run python manage.py runserver          # + the Tailwind watcher, see CLAUDE.md
```

Dev credentials and the seeded database are in `docs/worktrees.md`. Walk the
flow signed out and signed in, and write down:

- the **exact visible label** of every control you touch — copy it from the
  rendered page, not from memory of the template;
- what happens **after** each tap: what appears, what moves, what closes;
- where it **fails**: the signed-out state, the empty state, no connection.

## Step 3 — Shape the article around the brief

**`/help/routes/` is the template.** Copy its skeleton
(`public/help/articles/routes.html`), not its words. Design, readability and
simplicity are the brief for every article, and that page is what they look
like:

| Part | What it is | Form |
|---|---|---|
| Lead | What the feature is, in one line under the title. | One sentence. That is the whole "what it is" section. |
| Where to find it | The control, located, and what it opens. | A live illustration of the surface, then one short paragraph per place. |
| Doing it | The steps. | Numbered steps, four or five, then one paragraph on what to expect. |
| The controls | Each control on the surface, by its glyph. | A definition list: icon, name, one or two lines. |
| Good to know | Privacy, offline, caps, anything it deliberately won't do. | Bullets, one fact each. |
| Back link | "More about the rest of Snowdesk is on the help page." | One line. |

Three rules the first draft of that page broke, which is why it was rewritten:

- **Around three hundred words.** The first draft was twice that and read as
  a wall. What it lost was repetition of the FAQ panel, not facts — the
  panel is the short answer and the article does not restate it, it
  structures it. If a paragraph says what the panel says, cut it.
- **Every section is a real heading over a short form.** Prose only for the
  shape of a thing; steps for anything done in order; bullets for facts with
  no order; a definition list for a row of controls. Two paragraphs in a row
  is the limit. Six was the problem.
- **One illustration per surface the reader has to recognise** — the real
  component, live, at the width they will meet it (see
  [`references/authoring.md`](references/authoring.md) → Illustrations). A
  surface whose styles are not in `output.css` is described, not drawn.

When the brief gives an angle, map its clauses onto those parts. "What they
are, how they work, where to find them" became lead → where → doing → the
controls → good to know. Answering "where to find them" **before** "how they
work" serves the reader better than the order it was asked in — someone who
can't find the button can't follow the steps. Reordering for that reason is
fine; dropping a clause is not. Say what you did and why in the page's header
comment.

"Good to know" is the part people skip and the one that prevents support
questions. "Routes are private" answers a question nobody asked out loud.

## Step 4 — Writing the steps

One action per step, verb first, control named by its visible label, and the
consequence attached to the action that causes it.

> 1. Sign in, then tap the routes button on the right of the map.
> 2. Choose **Add a route** and pick a `.gpx` file — the kind a GPS watch, a
>    planning tool or a touring app exports.
> 3. The route appears under **Tracks** with its distance, and its ascent and
>    descent where the file recorded heights.
> 4. Turn on **Display on the map** at the foot of the panel to draw it.

- **Verb first** — "tap", "choose", "turn on" — not "you should now be able
  to".
- **Exact labels, in bold.** If the reader can't find your words on the
  screen, the step has failed. The example above said "Upload a route" until
  the first article was written against the running app — the button is
  labelled **Add a route**, and the list heading is **Tracks**. A plausible
  label is not a label.
- **Icon-only controls get shape and position** — "the routes button on the
  right of the map". If you can't do it in six words, that is a UI finding
  worth raising, not something to write around.
- **Sequential only.** If the order doesn't matter it isn't steps — it is a
  list of things you can do, and it should read as prose.

Four or five steps. Longer usually means two features, or a UI problem you
are papering over.

## Step 5 — The words themselves

British English, per CLAUDE.md — colour, favourite, organise.

Short words: *turn on* not *enable*, *open* not *navigate to*, *set up* not
*configure*. Second person, present tense, active voice.

**Never reaches user copy:** partial, waffle flag, endpoint, queryset, CAAML,
region_id, service worker, IndexedDB, HTMX, PWA. Describe the behaviour
instead — "saved on this device" beats "cached in IndexedDB".

**Fine to use**, because users meet them on the product already: bulletin,
danger rating, avalanche problem, region, aspect, elevation, resort, route,
favourite.

**Every factual claim is traceable** to a flow you walked or a line you read.
A sentence you assume is true is a sentence to check or cut.

## Step 6 — Build the page

Routing, the template skeleton, the page-metadata rule, the numbered-steps
problem, and the one permitted edit to the FAQ panel are in
[`references/authoring.md`](references/authoring.md). Read it before creating
any file.

## Step 7 — Check it

```bash
pre-commit run djangofmt --files <the templates you touched>
uv run tox -e ds-lint
uv run tox -e test -- tests/public/
uv run tox                                   # before opening the PR
```

`tests/public/test_page_meta.py` walks every public URL and fails a page that
sets neither sharing metadata nor an explicit opt-out — a new page inherits
the empty fallback and fails until you give it a title and description.

Then read the article back on a narrow viewport as someone who has never seen
the app. If you stumble, so will they.

## How this lands

A help article is a product change and ships like one: a `SNOW-xxx` branch, a
commit prefixed with the ticket, a PR. Use the `implement` skill for the
lifecycle and this one for the writing.

## Things that look like a help article and aren't

- **The panel, expanded.** Same facts, more words, no instructions. If a
  reader learns nothing they couldn't get from the accordion, don't ship it.
- **A rewrite of the panel.** Not yours to do, and it costs every translation
  of it. One link is the whole permitted edit.
- **A tour of the UI.** Controls in the order they appear on screen, with no
  account of what any of them is for.
- **Release notes.** "New in this version" is dated the moment it ships.
- **Domain teaching.** Snowpack, danger scales and problem types belong in
  the bulletin guide; link to it.
- **Documentation for something the reader cannot use.** A flag-gated feature
  gets flag-gated documentation, or none.
