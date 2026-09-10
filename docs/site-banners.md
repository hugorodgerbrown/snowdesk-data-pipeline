---
name: site-banners
description: Admin-managed site banners — django-persistent-messages, PersistentMessage, apps/public/banners.py, _persistent_banners.html, dismissal
status: current
last-reviewed: 2026-09-10
---

# Site banners

A banner is a row in `/admin/persistent_messages/persistentmessage/`. It
renders as a full-width strip under the nav on every page extending
`public/base.html`, in flow so it pushes the page down rather than
covering it. Nothing needs deploying to put one up, change its wording,
or take it down.

This replaced a hard-coded off-season archive bar whose copy was baked
into the map template. It stated a resume month the data no longer
supported and could only be corrected by shipping code, which is the
whole argument for a row in a table.

## Putting one up

| Field | What it does |
|-------|--------------|
| `content` | The copy. Escaped unless `mark_content_safe` is set. |
| `mark_content_safe` | Allows HTML — a link, usually. See [Markup](#markup). |
| `level` | Django message level. Picks the palette: DEBUG/INFO → info, SUCCESS → success, WARNING → warning, ERROR → error. |
| `target` | Who sees it: everyone (logged out included), all signed-in readers, anonymous only, or named users/groups. |
| `display_from` | When it starts. Defaults to now, so leave it alone to publish immediately; set it ahead to schedule. |
| `display_until` | When it stops. **Set this.** A banner with no end date runs until somebody remembers it. |
| `is_dismissable` | Whether the reader gets a "×". |
| `custom_tags` | Extra `extra_tags` values. Snowdesk reads none of them. |

Two banners at once is legal; they stack most-severe-first, then newest.
`display_until` in the past is how a banner retires — there is no
"disabled" checkbox, and deleting the row also discards its dismissal
records.

## Dismissal

The "×" has two halves, and they are not the same guarantee.

- **Everyone** — `static/js/overlays.js`'s shared handler adds the
  `hidden` class. That lasts the page view.
- **A signed-in reader** — `static/js/persistent_messages.js` also
  DELETEs the row's dismiss URL, which records the dismissal against
  their account. The banner stops being sent to them.

An anonymous reader's dismissal is not recorded anywhere: the package's
endpoint is `login_required`. So a logged-out visitor who closes a banner
sees it again on their next page load. `apps.public.banners.dismiss_url_for`
is what keeps the page honest about this — it renders the
`data-dismiss-url` attribute only when the dismissal can actually be
stored, so nothing fires a request that could only be redirected.

If a notice genuinely must stay closed for a logged-out visitor, it needs
a different mechanism (a `localStorage` key via
`data-overlay-persist`, which the banner primitive supports) and a
deliberate decision that a reader may permanently hide it. Nothing needs
that today.

## Markup

`mark_content_safe` is the one switch that lets HTML into a banner, and
it applies `mark_safe` to the row's content. That does not contravene the
`mark_safe` invariant in [`CLAUDE.md`](../CLAUDE.md): the invariant is
about content originating outside the codebase, and a banner is authored
by a staff user with change permission on the model. Nothing a visitor
submits reaches it.

Leave it off unless the banner needs a link. With it off, angle brackets
are shown rather than executed —
`tests/public/test_banners.py` pins both directions.

## How it reaches the page

- `apps.public.banners` — the read side. Which rows apply
  (`banners_for_request`), which status token a level paints in
  (`kind_for`), whether a dismissal can be recorded (`dismiss_url_for`).
  It runs the package's `filter_user` query directly rather than calling
  `persistent_messages.shortcuts.get_persistent_messages`, which is
  decorated `functools.cache` **keyed on the request** and so retains
  every request object for the life of the worker.
- `apps.public.context_processors.persistent_banners` — puts the result
  in every template context, lazily.
- `templates/includes/_persistent_banners.html` — one
  `includes/_overlay_banner.html` "strip" per row. The strip is the
  shared banner primitive, not a bespoke shape; the design-system rules
  in [`CLAUDE.md`](../CLAUDE.md) apply to it as to anything else.

## The cost

Two queries on every page that extends `public/base.html`, whether or not
a banner exists — reflected in `perf/query_counts.txt` (`home` 5 → 7,
`bulletin_historic` 7 → 9) and in
[`docs/query-counts.md`](query-counts.md).

One of the two is avoidable and lives upstream.
`PersistentMessageQuerySet.filter_user` calls `custom_group_query`
unconditionally, and that method evaluates a queryset eagerly to build a
`Q`. With `settings.MESSAGE_CUSTOM_GROUPS` empty — which it is, and which
the model's own `clean()` enforces — the query it runs can never match a
row. A short-circuit on an empty setting in
[django-persistent-messages](https://github.com/yunojuno/django-persistent-messages)
would halve the per-page cost here.
