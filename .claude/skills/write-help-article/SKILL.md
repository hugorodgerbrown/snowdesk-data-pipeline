---
name: write-help-article
description: |
  Write or rewrite end-user help content for Snowdesk — plain-language,
  step-by-step "what to click and what to expect" instructions that land as a
  topic panel on /help/. Use this whenever the user asks for help articles,
  user documentation, a how-to, a user guide, onboarding copy, or says things
  like "explain X to users", "document the new Y for users", "our help page
  doesn't cover Z", "write the help for this feature", or "rewrite that panel
  as steps" — and also when a feature has just shipped and nobody has written
  the user-facing explanation yet, even if they don't use the word "help".
  Do NOT use for internal or developer documentation (CLAUDE.md, docstrings,
  anything under docs/ — that is the documenter agent), for the avalanche
  domain primer at /how-to-read-a-bulletin/, or for Linear ticket scoping.
allowed-tools: Bash, Read, Write, Edit, Grep, Glob
---

# Write a Snowdesk help article

You are writing for someone who is trying to *do something* in Snowdesk and
cannot work out how. Not for a developer, not for a reviewer, and not for
someone who wants to understand avalanches. Everything below follows from
that one fact.

## The reader

A skier or ski tourer, on a phone more often than a laptop, reading either
the night before a day out or standing at a trailhead with one glove off.
Signal may be poor. English may be their second language — the site is
translated, so your copy will be too. They are looking up one specific
thing and they will stop reading the moment they think they have found it.

Two consequences worth holding on to:

- **They scan, they don't read.** The first line of a panel has to tell them
  whether they are in the right place. Bury the answer in paragraph three
  and it does not exist.
- **They are making a safety decision somewhere downstream.** That is not a
  licence to hedge every sentence — it is a reason to be exact about what
  the product does and does not do. "Downloads work offline" is a promise;
  if what actually happens is that map tiles are cached but bulletins are
  not, say that.

## The line you must not cross

`/help/` documents **the product**. `/how-to-read-a-bulletin/` teaches **the
domain**. The split is deliberate and `apps/public/views.py::help_page` says
so in its docstring.

So: "Tap a region to open its bulletin" is help. "A considerable rating means
human-triggered avalanches are likely" is the bulletin guide. If your article
starts explaining what a wind slab is, you are writing on the wrong page —
link to the guide and carry on.

## Step 1 — Use the thing before you describe it

Do not write help from reading the code. The code tells you what is possible;
it does not tell you what the screen actually says or what happens when you
tap. Four claims on `/help/` had quietly stopped being true by the time
SNOW-744 audited them, and every one of them would have survived another
code-reading.

```bash
uv run python manage.py runserver          # + the Tailwind watcher, see CLAUDE.md
```

Dev credentials and the seeded database are in `docs/worktrees.md`. Walk the
flow end to end, signed out and signed in, and write down:

- the **exact visible label** of every control you touch (copy it from the
  rendered page, not from your memory of the template)
- what happens **after** each tap — what appears, what moves, what closes
- where it **fails** — the signed-out state, the empty state, no connection

Read the template afterwards to confirm the edge cases you did not hit. The
existing panels also carry hard-won detail in their `{% comment %}` blocks —
`_topic_favourites.html` records that the "Display on the map" switch used to
live in the layers menu, which is exactly the kind of thing that sends a
reader hunting.

## Step 2 — Name the task, not the feature

Panel titles today name surfaces: "Favourites", "Layers", "Routes". That is a
reference index and it works for someone who already knows the vocabulary.
Steps are for someone who doesn't, and they are looking up a *task*.

You do not have to rename the panel to fix this — put the task in the first
line:

> **Favourites**
> Pin a spot you ski often so you can jump straight to its bulletin.

If the task genuinely spans two panels — "get set up for a day with no
signal" touches Downloads, Favourites and Install — do not smear it across
three panels or invent a fourth. Write it in the panel where the work starts,
and cross-link. Whether Snowdesk grows standalone `/help/<slug>/` article
pages is an open product decision; write so that lifting an article out later
is a copy-paste, not a rewrite.

## Step 3 — The shape of an article

Four parts, in this order. It maps onto how someone in a hurry reads.

1. **Orientation — one sentence.** What this does and why you'd want it.
   No preamble, no "Snowdesk allows you to".
2. **Preconditions, if any — one short sentence.** "You need to be signed in."
   Before the steps, never as step zero.
3. **The steps.** Numbered, one action each, in the order you do them.
4. **What to expect and what it won't do.** The result, then the limits:
   what needs an account, what happens with no connection, what is private,
   what this is *not*.

Part 4 is the one people skip and the one that prevents support questions.
"Favourites are private: they are yours alone and are never shown to other
users" answers a question nobody asked out loud.

## Step 4 — Writing the steps

Each step is one action, starting with the verb, naming the control by its
visible label, and saying what happens.

> 1. Sign in, then tap the star button on the right of the map.
> 2. Choose **Add a favourite**. The map moves under a fixed pin, so you can
>    place it one-handed.
> 3. Drag the map until the pin sits where you want it, then tap **Save**.
> 4. Give it a name and tap **Done**. It appears in the list below, and on the
>    map once **Display on the map** is on.

What that example is doing, and why:

- **Verb first.** "Tap", "choose", "drag" — not "you should now be able to".
- **Exact labels, in bold.** If the reader can't find the words you used on
  the screen, the step has failed. Copy them character for character.
- **Icon-only controls get shape and position.** "The star button on the right
  of the map." If you can't describe it in six words, that is a UI finding
  worth raising — say so rather than writing around it.
- **The consequence is part of the step.** "The map moves under a fixed pin"
  is what stops someone thinking the app has glitched.
- **One action per number.** Two verbs in one step means the reader loses
  their place when one of them fails.
- **Sequential only.** If the order doesn't matter it isn't steps — it's a
  list of things you can do, and it should read as prose.

Keep it to four or five steps. Longer than that usually means two tasks, or a
UI problem you are papering over.

## Step 5 — The words themselves

British English, per CLAUDE.md — colour, favourite, organise.

Short words, always: *turn on* not *enable*, *open* not *navigate to*, *set up*
not *configure*, *use* not *utilise*. Second person, present tense, active
voice.

**Never leaks into user copy:** partial, waffle flag, endpoint, queryset,
CAAML, region_id, service worker, IndexedDB, HTMX, PWA. Some of these are
unavoidable concepts — describe the behaviour instead. "Install Snowdesk to
your home screen" beats "install the PWA"; "saved on this device" beats
"cached in IndexedDB".

**Fine to use, because they are on the product already:** bulletin, danger
rating, avalanche problem, region, aspect, elevation, resort. Users meet
these words on the bulletins themselves.

**Every factual claim is traceable** to a flow you clicked or a line you read.
If you find yourself writing a sentence you assume is true, go and check it or
cut it.

## Step 6 — Getting it into the page

Mechanics, file shape, the steps-markup question, and the illustration
contract are in
[`references/authoring.md`](references/authoring.md) — read it before you
touch a template. In short:

- Body copy is one partial per topic at
  `apps/public/templates/public/help/_topic_<slug>.html`.
- Every user-visible string is wrapped for translation. Copy that ships
  untranslated ships as English to every locale.
- **No `<ol>` exists anywhere in the template tree yet** — the
  first article that needs numbered steps extracts a shared partial rather
  than inlining a class string, which is the design system's own
  reuse → extract → never-inline rule.
- Illustrations are **live mocks, never screenshots**
  ([ADR](../../../docs/decisions/help-illustrations-are-live-mocks.md)). A PNG
  rots silently; a rendered partial breaks loudly.
- A new panel is registered in three places, and a map control needs a
  coachmark step too or it ships undocumented in both halves of the product.

## Step 7 — Check it

```bash
pre-commit run djangofmt --files <the templates you touched>
uv run tox -e ds-lint                        # tokens, no inline class strings
uv run tox -e test -- tests/public/test_help.py
uv run tox                                   # before opening the PR
```

Then read it back on a narrow viewport, out loud, as someone who has never
seen the app. If you stumble, the reader will stop.

## How this lands

Help copy is a product change and ships like one: a `SNOW-xxx` branch, a
commit prefixed with the ticket, a PR. If there is a scoped ticket, use the
`implement` skill for the lifecycle and this skill for the writing.

## Things that look like help and aren't

- **A description of a feature.** "The layers menu lets you control which
  data appears on the map" tells someone who already found the menu what they
  already know.
- **A tour of the UI.** Screens in layout order rather than tasks in the order
  someone does them.
- **Release notes.** "New in this version" is not help; it is dated the moment
  it ships.
- **Reassurance instead of information.** "Don't worry, it's easy!" costs a
  line and answers nothing.
- **Documentation for something a reader cannot use.** The Sync-log panel is
  flag-gated for exactly this reason — a reader should not find instructions
  for a control that isn't on their screen.
