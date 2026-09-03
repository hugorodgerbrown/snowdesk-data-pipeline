# Authoring reference — help panels

Everything mechanical about getting a help article onto `/help/`. Read this
before editing a template; the writing rules are in `SKILL.md`.

## How the page is built

`/help/` is one Django template — `apps/public/templates/public/help.html` —
holding ~18 collapsible panels grouped under four eyebrow headings:

| Group | What belongs there |
|---|---|
| Getting started | What Snowdesk is |
| Bulletins | Anything read on a bulletin page — rating, problems, calendar, weather |
| The map | Map controls, **in the order the control column runs** |
| Your account and this device | Accounts, the Observations page, install & offline |

Each panel is `templates/includes/_collapsible_panel.html`, and each panel's
body copy is its own partial under
`apps/public/templates/public/help/_topic_<slug>.html`.

The view is `apps/public/views.py::help_page`. It issues **no database
queries** and a test pins that — so nothing you add may touch the ORM.

## The panel include

```django
{% translate "The day's weather" as t_weather %}
{% include "includes/_collapsible_panel.html" with title=t_weather data_testid="help-topic-weather" illustration="public/help/illustrations/_weather_panel.html" body_template="public/help/_topic_weather.html" %}
```

| Parameter | Purpose |
|---|---|
| `title` | Panel heading. Always a `{% translate %}`d variable. |
| `data_testid` | `help-topic-<slug>`. Tests pin this; pick it deliberately. |
| `body_template` | Your content partial. |
| `illustration` | Optional, rendered above the copy, `aria-hidden`. |

Full parameter list is in the partial's own `{% comment %}` block.

## The content partial

Copy this shape:

```django
{% comment %}
apps/public/templates/public/help/_topic_<slug>.html — "<Panel title>" panel body.

Content partial for the help page; included via the body_template parameter
of includes/_collapsible_panel.html.

<Why this topic exists, and any detail a future editor would otherwise have
to rediscover — a control that moved, a claim that was wrong before, a
behaviour that surprises people.>
{% endcomment %}
{% load i18n %}
<div class="text-sm leading-prose text-text-2">
    <p class="mb-3 last:mb-0">
        {% blocktrans trimmed %}
            One sentence saying what this does and why you would use it.
        {% endblocktrans %}
    </p>
</div>
```

Rules that bite if you skip them:

- **The header comment is not optional** — CLAUDE.md requires one on every
  module, and these partials are where the "why" of a wording choice lives.
- **Every string is inside `{% blocktrans trimmed %}` or `{% translate %}`.**
  A bare string ships as English to every locale.
- **Tokens only** — `text-text-2`, `mb-3`, `leading-prose`. Never
  `text-slate-500`, never `rounded-[12px]`. `tox -e ds-lint` blocks the PR.
- **`mb-3 last:mb-0` on every paragraph**, so the final one doesn't add a gap.
- **`data-testid` on any paragraph a test needs to find**, named
  `help-<topic>-<claim>` (e.g. `help-favourites-private`).
- Use `&mdash;` for em dashes, matching the existing partials.

## Numbered steps: there is no `<ol>` yet

There is not a single `<ol>` anywhere in the template tree, and `slf-prose`
styles `ul`/`li` but no ordered list. So the first article that needs steps
has to create the markup — and the design system's rule is
**reuse first, extract second, inline never**. Inlining a step list into one
topic partial means the second one copies it and the third diverges.

Extract `templates/includes/_help_steps.html` instead:

- takes a list of step strings (already translated by the caller) and renders
  an `<ol>` with the number as a visible marker;
- uses tokens for the marker and the text — no hex, no raw palette utilities;
- earns a registry entry in the staff component library at `/_components/`
  (`apps/public/design_tokens.py` + `apps/public/_component_fixtures.py`), like
  every other shared partial, so the next person finds it instead of
  reinventing it;
- gets the styling that doesn't exist yet added to `src/css/main.css`
  **only** if Tailwind utilities genuinely can't express it. Marker
  positioning usually can.

That is a small piece of implementation work. If the ticket in hand is
copy-only, raise it as its own ticket rather than smuggling a new shared
component into a content PR — and say so, don't quietly inline a class string.

## Illustrations are live mocks

Decision doc:
[`docs/decisions/help-illustrations-are-live-mocks.md`](../../../../docs/decisions/help-illustrations-are-live-mocks.md).

Illustrate a topic by rendering the **real partial** for the surface it
describes, fed by a synthetic in-memory context from
`apps/public/component_previews.py`. Never a screenshot: a PNG has no linter
and no test, so it goes stale silently — four claims on this page had rotted
exactly that way before SNOW-744.

Three constraints that catch people out:

1. **No queries.** Contexts are hand-built dataclasses and dicts. The
   no-queries test on `/help/` is not negotiable.
2. **`/help/` loads `output.css` only.** A component whose rules live in
   `static/css/map.css` — the season scrubber is the standing example —
   cannot be illustrated here; it collapses to unstyled fragments. Leave a
   comment saying why the topic is unillustrated rather than shipping a
   broken mock.
3. **The illustration is decoration** — its wrapper is `aria-hidden`, so a
   screen-reader user gets the prose alone. The copy has to make sense with
   the picture removed. Never write "as shown below".

## Registering a new panel

1. **Create the content partial** at
   `apps/public/templates/public/help/_topic_<slug>.html`.
2. **Include it in `help.html`** under the right eyebrow group. For a map
   topic, insert it in the position matching the control column's order — a
   reader working down the group is working down the right-hand side of the
   map, and that's the whole reason the grouping exists.
3. **Add the testid to `ALWAYS_ON_TESTIDS`** in `tests/public/test_help.py`.
   A panel only visible to some users is gated instead, like the Sync-log
   panel's `sync_log` waffle flag, and stays out of that list.

   While you are in there, check the list still covers every ungated panel.
   It is a hand-maintained mirror of the template with nothing enforcing the
   correspondence, so it drifts quietly — a panel added without a matching
   entry is silently unguarded, and the missing entry looks exactly like a
   deliberately gated one. If you find a gap, close it in the same PR.

**If the topic documents a map control, that is not enough.** The map's
coachmark tour is the other half of the same job:

4. Add a step to `#map-help-steps` in
   `apps/public/templates/public/partials/_map_embed.html`:
   ```django
   <li data-help-target="#<control-id>" data-help-title='{% trans "Short title" %}'>
       {% trans "One sentence on what this control does." %}
   </li>
   ```
   Keep the list in DOM order so the tour walks the screen top to bottom
   instead of zig-zagging.
5. Add the pair to `CONTROL_TO_TOPIC` in
   `tests/public/test_help.py::TestHelpCoversTheMapControls`.

Routes shipped with neither a panel nor a coachmark step and stayed
undocumented through two more tickets that touched the stack. That test pair
exists to make it impossible to repeat.

## Worked rewrite

The current Favourites copy, and what steps do to it.

**Before** — accurate, complete, and unusable in a hurry. The instruction is
three clauses deep in a paragraph, and the reader has to hold the whole
sentence to extract two taps:

> Favourites let you pin specific spots — a resort, a trailhead, a favourite
> line — directly on the map. Sign in, open the star button on the right of
> the map, and use "Add a favourite": the map moves under a fixed pin, so you
> place it with one hand. You can also save a resort straight from its pin's
> popup.

**After** — same facts, same length, scannable:

> Pin a spot you ski often so you can jump straight to its bulletin.
>
> You need to be signed in.
>
> 1. Tap the star button on the right of the map.
> 2. Choose **Add a favourite**. The map moves under a fixed pin, so you can
>    place it one-handed.
> 3. Drag the map until the pin sits where you want it, then tap **Save**.
> 4. Give it a name. It appears in the list below the button.
>
> Quicker for a resort: open its pin on the map and save it from the popup.
>
> Favourites are private — never shown to other users. Each one gets its own
> page you can bookmark or share. To draw them all on the map, turn on
> **Display on the map** at the foot of the same panel; it lives there rather
> than in the layers menu.

What changed, and why each move is worth copying:

- **The task moved to line one.** "Pin a spot you ski often" is what the
  reader searched for; "Favourites let you pin specific spots" is a
  definition of a noun they may not have thought of.
- **The precondition came out of the step list.** Signing in is not step one
  of placing a pin; it is a gate, and a signed-out reader needs to hit it
  before they start counting taps.
- **Each tap got its own number and its consequence.** "The map moves under a
  fixed pin" now sits with the action that causes it.
- **The alternative path stopped competing with the main one.** "You can also
  save a resort straight from its pin's popup" was a third clause in the
  primary flow; as a one-line aside after the steps it is findable without
  interrupting.
- **The limits got their own closing block** — private, own page, and the
  switch that isn't where you'd look for it. Nothing was cut.
