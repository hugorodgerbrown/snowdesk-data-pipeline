# Authoring reference — help article pages

Everything mechanical about getting an article onto the site. Read this before
creating a file; the writing rules are in `SKILL.md`.

## The shape of the change

An article is one new page plus one link:

| File | Change |
|---|---|
| `apps/public/urls.py` | one `path()` for the article route |
| `apps/public/views.py` | one view (the first article creates it; later ones reuse it) |
| `apps/public/templates/public/help/articles/<slug>.html` | the article |
| `apps/public/templates/public/help/_topic_<slug>.html` | **one added link, nothing else** |
| `tests/public/test_help_articles.py` | coverage for the new page |

## Routing — the trap that will bite you

`/help/` is registered **before** the generic `<region_id>/` patterns, or the
string "help" resolves as a region id. Your article route has the same
problem one level down: `help/<slug>/` has two path segments, which is the
exact shape of `<region_id>/<slug>/`.

Register it immediately after the `help/` route, well above the generic
patterns:

```python
# Plain-language "how it works" help page (SNOW-456) — registered before
# the generic <region_id:region_id>/ patterns so "help" never resolves
# as a region id.
path("help/", views.help_page, name="help"),
# Long-form help articles. MUST stay above the generic
# <region_id>/<slug>/ pattern — "help/routes/" matches that shape too, and
# whichever is registered first wins.
path("help/<slug:slug>/", views.help_article, name="help_article"),
```

## The view

One view for every article, keyed by slug. Articles are static content with
no per-article logic, so a view each would be five copies of the same three
lines — and the project's rule is no abstraction until two callers need it,
which is satisfied the moment there is a second article.

```python
#: Slug → template for every published help article. A slug absent here is a
#: 404, so an article is reachable only once it is listed.
HELP_ARTICLES = {
    "routes": "public/help/articles/routes.html",
}


def help_article(request: HttpRequest, slug: str) -> HttpResponse:
    """
    Render one long-form help article.

    Args:
        request: The incoming HTTP request.
        slug: The article's URL slug, looked up in ``HELP_ARTICLES``.

    Returns:
        The rendered article page.

    Raises:
        Http404: If the slug names no published article.

    """
    template = HELP_ARTICLES.get(slug)
    if template is None:
        raise Http404(f"No help article for slug {slug!r}")
    return render(request, template)
```

Keep it query-free, like `help_page` — an article is static text and has no
business touching the ORM.

## The template

Model it on `public/help/articles/routes.html` — the first article, and the
template for the rest. The skeleton:

```django
{% extends "public/base.html" %}
{% comment %}
apps/public/templates/public/help/articles/<slug>.html — "<Title>" help article.

<Which feature, and which brief this answers. If you reordered the brief's
sections, say so and why here — that is the record of a deliberate choice.>

Companion to the FAQ panel at public/help/_topic_<slug>.html, which stays the
short answer; this page is the walkthrough. Anything asserted here must agree
with that panel.
{% endcomment %}
{% load i18n components %}

{% block page_meta %}
    {% trans "<Title>" as page_name %}
    {% trans "<One sentence, front-loading the feature noun.>" as page_description %}
    {% include "includes/_page_meta.html" with title=page_name|add:" · Snowdesk" description=page_description %}
{% endblock page_meta %}

{% block content %}
    <main class="{% page_shell_classes 'legal-prose max-w-narrow' %}">
        {% trans "<Title>" as page_heading %}
        {% include "includes/_page_title.html" with text=page_heading class_extra="mb-2" data_testid="help-article-heading" %}
        <p class="mb-8 text-base leading-prose text-text-2" data-testid="help-article-<slug>-lead">
            {% blocktrans trimmed %}<What it is, in one line.>{% endblocktrans %}
        </p>

        <section data-testid="help-article-<slug>-where">
            <h2>{% trans "Where to find it" %}</h2>
            {% include "includes/_help_illustration.html" with illustration="public/help/illustrations/<surface>.html" framed=True width="sheet" data_testid="help-article-<slug>-<surface>" %}
            <p class="mb-3 last:mb-0">
                {% blocktrans trimmed %}…{% endblocktrans %}
            </p>
        </section>

        <section data-testid="help-article-<slug>-how">
            <h2>{% trans "Doing it" %}</h2>
            {% include "includes/_help_steps.html" with body_template="public/help/articles/_<slug>_steps.html" %}
        </section>

        <section data-testid="help-article-<slug>-notes">
            <h2>{% trans "Good to know" %}</h2>
            {% include "includes/_help_steps.html" with body_template="public/help/articles/_<slug>_notes.html" unordered=True %}
        </section>
    </main>
{% endblock content %}
```

What the skeleton is doing:

- **`legal-prose`** is the site's long-form document style — the colophon
  and the terms use it. It sizes each `> section`'s prose and its `<h2>`,
  so a section carries no class of its own and a heading is a bare `<h2>`.
  Not `_eyebrow`: a mono uppercase label over a wall of text was the first
  draft's format, and it is what "not a great reading experience" meant.
- **`max-w-narrow`** keeps the measure readable. The page shell's own width
  is for pages with panels beside their text.
- **The lead replaces a "what it is" section.** One line under the title.
- **A definition list for a row of controls** — see the "On each row"
  section of the Routes article: `<dl>` of `<div class="flex gap-3">`, the
  control's own icon include beside a `<dt>` name and a one-line `<dd>`.
  The glyph is the same include the real row uses, so the reader matches a
  control by its mark rather than by a description of it.

Rules that bite if you skip them:

- **`page_meta` is mandatory.** `tests/public/test_page_meta.py` walks every
  public URL; a new page inherits `base.html`'s empty fallback and fails until
  you give it a title and description. The alternative state is an explicit
  `sharing=False` plus a comment saying why — an article wants a card, so use
  the block above.
- **Every string is inside `{% blocktrans trimmed %}` or `{% translate %}`.**
  A bare string ships as English to every locale.
- **Tokens only** — `text-text-2`, `leading-prose`, `mb-3`. Never
  `text-slate-500`, never `rounded-[12px]`. `tox -e ds-lint` blocks the PR.
- **Reuse the partials** — `_page_title`, `_help_illustration`,
  `_help_steps`, `_card`, `_button`. Rule one of the design system is reuse
  first, extract second, inline never.
- **`data-testid` on each section**, so tests can pin a claim without matching
  prose.
- `&mdash;` for em dashes, matching the rest of the templates.

## Numbered steps, and bullets

Use `templates/includes/_help_steps.html` for both. It is the only ordered
list in the codebase — `/help/`'s FAQ panels are prose and `slf-prose` styles
`ul`/`li` but no `ol` — so it was extracted with the first article rather
than inlined, and it is registered in the staff component library at
`/_components/` under **Help steps**. Pass `unordered=True` for a bulleted
list: steps are numbered because the reader does them in order, "good to
know" facts are not.

It takes its items by template path, the same slot-by-template shape
`_collapsible_panel.html` uses:

```django
{% include "includes/_help_steps.html" with body_template="public/help/articles/_<slug>_steps.html" %}
```

and that file is a flat list of `<li>` items, one whole `{% blocktrans %}`
each:

```django
{% load i18n %}
<li>
    {% blocktrans trimmed %}
        Sign in, then tap the routes button on the right of the map.
    {% endblocktrans %}
</li>
```

**Why items arrive by template rather than as a list in the context.** Each
step has to stay one msgid written at its call site. Passing translated
strings through a context list gives a translator fragments to reassemble and
puts the article's words in a Python file, away from the page they belong to.

Numbering is `list-decimal`, not a drawn badge, so the number a sighted reader
sees and the position a screen reader announces are the same thing with
nothing to keep in sync. Needs no custom CSS — don't add any.

## The one permitted edit to the FAQ panel

Add a link to the article. Change nothing else — the panel's other strings are
translated msgids, and rewording one throws its translations away.

```django
<p class="mb-3 last:mb-0" data-testid="help-routes-article-link">
    {% url 'public:help_article' slug='routes' as article_url %}
    {% blocktrans trimmed %}
        There is more in <a href="{{ article_url }}" class="text-status-info-text underline">the full guide to routes</a>.
    {% endblocktrans %}
</p>
```

The anchor sits **inside** the msgid so a translator can move it within the
sentence; the URL is a context variable and stays **outside**, so changing
where it points doesn't invalidate every translation. That is the shape
`_topic_routes.html` already uses for its gpx.studio link — follow it.

## Illustrations

If the article shows a component, render the **real partial** fed by a
synthetic in-memory context, never a screenshot — see
[`docs/decisions/help-illustrations-are-live-mocks.md`](../../../../docs/decisions/help-illustrations-are-live-mocks.md).
A PNG has no linter and no test, so it goes stale silently; four claims on
`/help/` had rotted exactly that way before SNOW-744.

The mechanics:

- **The view already provides the context.** `help_article` renders with
  `help_illustrations()`, the same mapping `/help/` gets — `panels`
  (favourites, observations, routes, downloads), `season_calendar`,
  `weather_panel` and the bulletin blocks. An article illustrating a surface
  the FAQ already does needs no Python at all.
- **The wrapper is `includes/_help_illustration.html`** — `inert` and
  `aria-hidden`, the same partial the FAQ panels use. Pass `framed=True` on
  an article (the FAQ's recessed well vanishes against the page) and
  `width="sheet"` or `width="popup"` for a map panel or a map popup, so it
  renders at the width the reader will meet rather than across the whole
  column.
- **A surface built in JavaScript has no partial to include.** The route
  popup is the standing example: `illustrations/_route_popup.html` mirrors
  map.js's markup and takes its chart geometry from a Python port in
  `component_previews.py`, with tests holding the port to the same
  properties the Vitest suite holds the original to. Do this only when the
  surface is the point of the article; say so in the template's header.
- **A panel's rows can carry the real controls.** `illustrations/_panel.html`
  takes an optional `rows_template`; the Routes article passes
  `_routes_rows.html`, whose rows render the real action cluster. Its
  `csrf_token="NOTPROVIDED"` is deliberate — the real token is a session
  read, which is a query, and a help page issues none.

Three constraints:

1. **No queries** — hand-built dataclasses and dicts, as in
   `apps/public/component_previews.py`. The article's no-queries test is what
   catches a partial that reaches for the session or the ORM.
2. **Only `output.css` is loaded.** A component styled from
   `static/css/map.css` — the season scrubber, and the map's round buttons —
   cannot be shown on a help page; it collapses to unstyled fragments. Say so
   in the page's header comment rather than shipping a broken mock.
3. **Decoration only** — the illustration wrapper is `aria-hidden`, so the
   prose must stand alone. Never write "as shown below".

## Tests

Add `tests/public/test_help_articles.py` covering, for each article:

- `GET` the URL returns 200 for an anonymous user and the heading testid is
  present;
- an unknown slug 404s (this is what makes `HELP_ARTICLES` the publication
  gate rather than a decoration);
- each section's `data-testid` renders;
- the FAQ panel links to the article — the pair is the point, and a link that
  silently disappears in a refactor is how the two surfaces drift apart;
- the page issues no queries, matching `/help/`.

A 404 and a rendered page both need only the Django test client. Neither
belongs in Playwright — see the test-layer rules in CLAUDE.md.
