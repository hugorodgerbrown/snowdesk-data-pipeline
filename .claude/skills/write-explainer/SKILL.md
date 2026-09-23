---
name: write-explainer
description: |
  Write or revise a public explainer under docs/explainers/ — a self-contained
  HTML page, in the series style, that explains one piece of how Snowdesk
  works to a reader with no repo access, with figures drawn from real tracks.
  Use when the user asks for "an explainer", "an explainer doc", "write this
  up for people outside the repo", "explain how X works" as a page, or wants
  an existing explainer changed, extended or fixed (including figures that
  do not render). Do NOT use for: docs/decisions/ records, docs/*.md
  reference docs, a Linear comment, a PR description, the build blog (a
  separate repo), or a quick answer in chat.
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, mcp__Claude_Browser__navigate, mcp__Claude_Browser__javascript_tool, mcp__Claude_Browser__computer, mcp__Claude_Browser__read_console_messages, mcp__Claude_Browser__resize_window, mcp__Claude_Browser__tabs_create, mcp__Claude_Browser__tabs_close, mcp__Claude_Browser__tabs_context, mcp__Claude_Browser__browser_batch, mcp__Claude_Browser__find, mcp__Claude_Browser__get_page_text
---

# Write an explainer

An explainer is one HTML file in `docs/explainers/` that a person outside the
project can open and understand: what a thing is, why it is built the way it
is, and what it cannot do. Read two of the existing pages before writing —
`the-track-cannot-be-steeper-than-the-ground.html` (a measurement and a rule)
and `pitch-and-roll-on-a-ski-track.html` (a drawing, with interaction) are
the closest templates. The pages are the source of truth for the style; this
skill is the list of things that go wrong.

## 1. Settle the argument before the page

Write, in chat or a scratch file, the one sentence the page exists to make a
reader believe, and the three to five steps that get them there. Each step
becomes a numbered `stage`. If you cannot state the sentence, the page is
not ready — ask the user.

Then list every number the page will state. Each one needs a source you will
query, not remember (step 3).

## 2. Write for someone without the repo

The reader cannot follow a link into the codebase, so the page contains:

- **no ticket ids** (`SNOW-…`), **no file paths**, **no function or setting
  names**, and **no references to `docs/decisions/`**. Say what the thing
  does in words: "the stored route", "the terrain model on a 5 m grid", not
  `Route.points` or `sample_slope`.
- **British English**, and the project's plain register: short sentences,
  few adjectives, no adverbs, no selling. A caveat that changes what the
  reader should believe goes in the body, not a footnote.
- **real places, stated as real.** The four canonical tracks are real
  tours, committed with the owner's agreement. The seed data names them
  with a hyphen (`Mont Fort - Backside`); pages display an en dash
  ("Mont Fort – Backside"). Leave the eight-point synthetic "Verbier skin
  track" out of figures and totals — it is a seed fixture, not a tour —
  and say so where a count would otherwise differ from a command's output.

The footer says when the design or measurement was made and what the
figures are drawn from.

## 3. Get the numbers from the running code

Every figure and every number in the prose comes from the services run
against the canonical tracks, never from arithmetic done by hand. Ticket
comments, docstrings and commit messages are claims to measure, not
sources: this skill exists partly because a ticket comment described a
recording break as one gap when it was two.

When your measurement disagrees with one of those, find out whether you
measured the same thing before calling it wrong. The legs docstring's
"~600 m" boundary movement is 74 m on the full recording and ~600 m along
the thinned track; both are right, and the docstring now says which it
used. Fix a source only once the two measures are reconciled.

- Seed a worktree DB if it has none: `bin/init-worktree` (the four canonical
  tracks plus the synthetic Verbier track, all with slope records).
- Export exactly the arrays the figures need with
  `uv run python manage.py shell -c "…"`, calling the same service the app
  calls, and write compact JSON (`separators=(',', ':')`). Keep it small:
  one tour's per-segment rows, not all of them.
- When the prose states a figure ("360 m in 5 m", "4 to 29 segments"), check
  it against that export before writing it, and again after the page is
  built (step 6). Better still, have the script write the sentence's
  numbers from the embedded data and bake them (step 5), so prose and
  figures cannot disagree.
- **A comparison against something the app no longer does** (a dropped
  design, an old constant) has no service to call. Reconstruct it by
  replacing only the part that differs — monkeypatch one function, pass
  one keyword argument — and run the rest of the real code. Say on the
  page that it is a reconstruction, and in what way.

## 4. Build the page

**Structure.** Start from the existing page closest in shape. The series
pieces are: `.hero` (eyebrow, `h1` with one `<em>` word, `.lede`, `.facts`,
and a small figure), numbered `article.stage` sections (`.num`, `h2`, an
italic `.q` that states the section's point in one line, then `.body`,
often `.cols` with a `.side` of `.rules`), `.chartbox` for full-width
figures with a `.legend`, `.tbl-wrap` tables, `.caveat` / `.next` callouts,
and a `footer`.

**Styles.** Copy the whole first `<style>` block (the series tokens and
components, light and dark) verbatim from an existing explainer, then add a
second `<style>` block for this page's own tokens. Do not restyle the series
block; the pages are meant to read as one set.

The series block does not hold everything a page with figures needs. The
rest lives in each page's second block — copy what you use from there:

- `.chartbox`, `.legend`, `td.num` / `th.num`, and the stage `min-width`
  rules below: `the-track-cannot-be-steeper-than-the-ground.html`.
- `.legbar` / `.legbtn` (the picker buttons) and `.cases`:
  `pitch-and-roll-on-a-ski-track.html`.

**Figures.** Draw them with an inline script from the embedded JSON, into
`<svg id="…" data-baked viewBox="…" role="img" aria-label="…">` slots. Every
element the script fills gets an `id` and `data-baked`.

- **Colours are `var(--token)` references**, never values read with
  `getComputedStyle`. Put them in a `style` attribute or as presentation
  attributes; both follow the theme. A resolved literal freezes the figure
  in one theme, and baking (step 5) would bake that theme in.
- **Every draw function clears its target first** (`svg.innerHTML = ''`, or
  `host.textContent = ''` for an HTML container). Baked content is replaced
  by the live drawing, and a function that appends without clearing draws
  everything twice.
- **Controls are script-only.** Buttons (leg pickers and the like) are
  created by the script and are not baked, so a reader without JavaScript
  sees the default state and no dead buttons.
- `aria-label` states what the figure shows in words, with the numbers.

**Mobile.** Grid items default to `min-width: auto`, so a table with a
`min-width` widens its column and the page scrolls sideways at phone width.
Add this whenever a page has a table or a scrolling chart:

```css
.stage > div, .cols > *, .hero > * { min-width: 0; }
```

Each covers one grid the series uses. The same rule is what lets a
scrolling chart box (below) scroll in place instead of widening the page.

**Chart text.** A figure scales to the screen, so its text shrinks with it.
On a 375 px phone a full-width chart is about 280 px wide, so a 1000-unit
`viewBox` with 12-unit labels renders them at 3.5 px — this happened on two
pages in the series. A small figure beside the text (a 330-unit `viewBox`)
is fine as it is. For a wide chart, give the SVG a minimum width and let
its box scroll sideways rather than shrink:

```css
.chartbox { overflow-x: auto; }
.chartbox svg { min-width: 760px; }  /* 9 px × viewBox width ÷ smallest label size */
```

Labels a reader must be able to read also belong in the HTML legend or
caption, which never shrink.

## 5. Bake the figures

```bash
bin/bake-explainer docs/explainers/<page>.html
```

This runs the page's script under jsdom and writes each `[data-baked]`
element's content into the HTML, so the page reads with JavaScript off —
which includes the Claude app's file preview, a sanitising viewer, and any
reader who blocks scripts. It is idempotent: re-run it after every edit to
the script or data, and a second run leaves the file unchanged. It fails
loudly if the script throws under jsdom or a baked element draws nothing.
It prints `failed to copy trust settings of system certificate` lines on
every run; they are Node's, and harmless.

## 6. Check it in a browser

Serve the directory (`python3 -m http.server <port> --bind 127.0.0.1` from
`docs/explainers/`, on a free port) and open it in the Browser pane. Binding
a port needs the Bash sandbox off (`dangerouslyDisableSandbox: true`); run
it in the background and stop it when done. A `file://` URL opens as a
static snapshot with scripts disabled — useful for seeing the baked copy,
but no page tools work on it.

The pane has a tab limit. Open your own tab with `tabs_create`, and close it
at the end.

Check, with `javascript_tool` rather than screenshots where you can:

- no console errors (`read_console_messages`);
- each figure has content, and any control redraws without duplicating;
- every number in the prose matches the embedded data;
- no horizontal scroll at the mobile preset
  (`document.documentElement.scrollWidth === clientWidth`, measured after
  a reload at that size);
- chart labels at least about 8 px at the mobile preset
  (`Math.min(...[...document.querySelectorAll('svg text')].map(t => t.getBoundingClientRect().height))`);
- both themes, set explicitly with `resize_window` and `colorScheme` — the
  pane follows the app's theme, which is often dark, so light is the one
  most likely to go unchecked.

Screenshots below the fold often come back blank. That is the pane, not the
page: shift `.wrap` with a CSS `transform` to bring the figure to the top,
screenshot, then reload.

## 7. Register it

Add the page to the "Public explainers" row of the feature table in
`CLAUDE.md`: one sentence on what it explains. `tox -e docs-lint` checks the
routing table. Commit with the ticket's `SNOW-xxx:` prefix if there is one.

## Revising an existing explainer

Each page's footer dates the decision or measurement it describes. When a
revision changes what the page claims — a corrected figure, a decision that
replaced the one it recorded — say so in the footer rather than rewriting
the record silently. After any edit to a figure's script or data, re-run
step 5 and step 6.
