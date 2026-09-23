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
the closest templates. Take structure and visual style from them, not
their density: several run long on validation detail (step 2). This skill is
the list of things that go wrong.

## 1. Settle the argument before the page

Write, in chat or a scratch file, the one sentence the page exists to make a
reader believe, and the challenges that stand between the reader and that
sentence. Each challenge becomes a numbered `stage`. Use as many as the
subject has — a simple rule may need two, a pipeline six. Do not merge two
challenges to hit a count, or pad one out to reach it. If you cannot state
the sentence, the page is not ready — ask the user.

Then write the question the reader arrived with — "what is a leg, and why
does the app count distance in metres?" — and check each stage answers part
of it. A stage that answers a question only the developer had is not a stage.

## 2. Each stage: the challenge, a real example, the solution

Every stage has the same three parts, in this order.

1. **The challenge.** What makes this part hard, in plain words — the thing
   a reasonable person would get wrong. "A recorded height wobbles by a few
   metres from one point to the next, so read raw, every wobble is a turn."
2. **A real example.** One place on one real tour where the challenge
   happens, with its numbers and a chart. Numbers and charts are both
   encouraged: they are what makes the challenge believable. Pick the
   example that shows it most plainly, and label the chart so the reader
   can find the moment the text describes.
3. **The solution.** What the app does about it, and what the same example
   looks like afterwards. State it succinctly — one or two sentences a
   reader could repeat.

Close the page with a short summary of the answer: the solution from each
stage in a line, so the page can be read backwards from its end.

Where this went wrong in the skill's own test rounds: the challenge and
solution were stated well, but the example grew into the engineer's
validation — every tour thinned four ways, "16 of 16 against 14 of 16",
parameter sweeps from 75 m to 400 m, a boundary drift measured two ways —
and the solution was lost under it. So:

- **The example illustrates; it does not prove.** One tour, one place,
  drawn, with the numbers that describe that place. Not a table across every
  tour and every setting. A reader believes a claim because they can see it
  happen once, not because it happened sixteen times.
- **Numbers describe the example.** "At 2.45 km the recording drops 360 m in
  5 m" earns its place. "Between 38 and 117 runs, reducing to 18 to 48"
  does not: it is a range across tours, not a thing the reader can see. No
  counts of comparisons, and no second measure of the same thing.
- **Validation stays off the page.** Sweeps, tolerance tests and "we checked
  it against X" are how the design was chosen, and belong in the docstring
  or decision record. The page may say in one sentence that a setting was
  tested rather than guessed. When the challenge is itself robustness ("the
  same route recorded differently gives the same legs"), one such test, run
  on one tour and drawn, is the example.
- **One caveat, if it changes what the reader believes.** Not a closing
  catalogue of limits.
- **Say what to look at, not what the chart already says.** The prose around
  a figure points at the moment that matters; it does not read the figure's
  numbers back.

Before building, read each stage as challenge, example, solution. If a
paragraph is none of the three, cut it.

## 3. Write for someone without the repo

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

## 4. Get the numbers from the running code

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
  tracks plus the synthetic Verbier track, all with slope records). `uv run`
  commands need the Bash sandbox off (`dangerouslyDisableSandbox: true`):
  uv's cache lives outside it.
- Export exactly the arrays the chosen example needs with
  `uv run python manage.py shell -c "…"`, calling the service function
  itself, and write compact JSON (`separators=(',', ':')`). One tour's rows,
  not all of them. Measuring more than the page shows is fine — it is how
  you find the right example — but it stays in your notes.
- When the prose states a figure ("360 m in 5 m"), check it against that
  export before writing it, and again after the page is built (step 7).
  Better still, have the script write the sentence's numbers from the
  embedded data and bake them (step 6), so prose and
  figures cannot disagree.
- **Say what the app does with it, and no more.** A service can exist before
  anything in the app calls it (the leg-cutting code did, for a while).
  Check with `grep -rn <function> apps/ templates/ static/` before writing
  "the map shows" or "a route displays".
- **When one quantity can be measured two ways**, put one on the page — the
  one nearest to what a reader sees, a distance on the route as recorded
  rather than along a derived copy of it — and keep the other in your notes.
- **A comparison against something the app no longer does** (a dropped
  design, an old constant) has no service to call. Reconstruct it by
  replacing only the part that differs — monkeypatch one function, pass
  one keyword argument — and run the rest of the real code. Say on the
  page that it is a reconstruction, and in what way.

## 5. Build the page

**Structure.** Start from the existing page closest in shape. The series
pieces are: `.hero` (eyebrow, `h1` with one `<em>` word, `.lede`, `.facts`,
and a small figure), numbered `article.stage` sections (`.num`, `h2`, an
italic `.q` that states the stage's challenge in one line, then `.body`,
often `.cols` with a `.side` of `.rules`), `.chartbox` for full-width
figures with a `.legend`, `.tbl-wrap` tables, `.caveat` / `.next` callouts,
the closing summary (a `.later` block, as in "Pitch and Roll"), and a
`footer`.

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
element the script fills gets an `id` and `data-baked`. Baking writes the
element's own start tag as well as its contents, so an `aria-label` or
`viewBox` the script sets from the data survives too.

- **Colours are `var(--token)` references**, never values read with
  `getComputedStyle`. Put them in a `style` attribute or as presentation
  attributes; both follow the theme. A resolved literal freezes the figure
  in one theme, and baking (step 6) would bake that theme in.
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

## 6. Bake the figures

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

## 7. Check it in a browser

Serve the directory (`python3 -m http.server <port> --bind 127.0.0.1` from
`docs/explainers/`, on a free port) and open it in the Browser pane. Binding
a port needs the Bash sandbox off (`dangerouslyDisableSandbox: true`); run
it in the background and stop it when done. A `file://` URL opens as a
static snapshot with scripts disabled — useful for seeing the baked copy,
but no page tools work on it.

The pane has a tab limit. Open your own tab with `tabs_create`, and close it
at the end. Writing an `.html` file can open a preview tab of its own, which
counts against the limit — close it if `tabs_create` refuses.

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

## 8. Register it

Add the page to the "Public explainers" row of the feature table in
`CLAUDE.md`: one sentence on what it explains. `tox -e docs-lint` checks the
routing table. Commit with the ticket's `SNOW-xxx:` prefix if there is one.

## Revising an existing explainer

Each page's footer dates the decision or measurement it describes. When a
revision changes what the page claims — a corrected figure, a decision that
replaced the one it recorded — say so in the footer rather than rewriting
the record silently. After any edit to a figure's script or data, re-run
step 6 and step 7.
