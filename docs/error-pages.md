---
name: error-pages
description: handler400/403/404/500, CSRF_FAILURE_VIEW, apps/public/errors.py ERROR_PAGES, JSON/HTMX/HTML negotiation, /_errors/ preview
status: current
last-reviewed: 2026-10-20
---

# Error pages

Every error Django raises or catches outside a view's own response goes
through `apps/public/errors.py` (SNOW-1151).

| Status | Raised by | Handler | HTML page |
|--------|-----------|---------|-----------|
| 400 | `SuspiciousOperation`, `DisallowedHost`, a malformed request | `bad_request` (`handler400`) | standalone |
| 403 | a raised `PermissionDenied` | `permission_denied` (`handler403`) | full site |
| 403 | a failed CSRF check | `csrf_failure` (`CSRF_FAILURE_VIEW`) | full site |
| 404 | no URL matched, or a raised `Http404` / `get_object_or_404` | `page_not_found` (`handler404`) | full site |
| 500 | any unhandled exception | `server_error` (`handler500`) | standalone |

A view that *returns* an error, such as `HttpResponse("Authentication required.", status=403)`
or `HttpResponseNotFound()`, does not reach a handler and keeps its own body.
Only raised errors and unmatched URLs reach these handlers.

## One handler, three shapes

`error_response(request, kind)` picks the shape:

1. **JSON** for `/api/`, `/oauth/` and `/.well-known/` paths, and for a
   request that accepts `application/json` but not `text/html`. The body is
   `{"error": "<slug>", "status": <code>}`, and the slugs are `bad_request`,
   `forbidden`, `csrf_failed`, `not_found` and `server_error`.
2. **Plain text** for an htmx request (`HX-Request: true`). The body is the page
   heading. htmx does not swap 4xx/5xx responses, so a full page would go
   unrendered. A page that wants to show the failure listens for
   `htmx:responseError` (see `accounts/partials/_htmx_error_banner.html`).
3. **HTML** for everything else.

## Two kinds of HTML page

- **Full site:** `templates/errors/page.html` extends `_status_page.html` and
  renders with the request, so the nav, account menu and footer are present.
  It is used for 403, CSRF failure and 404.
- **Standalone:** `templates/errors/_standalone.html` renders from a plain
  dict, so no context processors, session or database are involved. It is
  used for 400 and 500, and as the fallback when the full-site render fails.
  Why: [`decisions/the-500-page-renders-without-context.md`](decisions/the-500-page-renders-without-context.md).

If the standalone template also fails, for example because the static
manifest is missing, the handler returns the literal `_FALLBACK_HTML` string.

## Copy

Headings and bodies live in `ERROR_PAGES` in `apps/public/errors.py`, and
neither template contains copy of its own. Edit them there, so the
full-site and standalone renderings of a status stay in step. The strings are
`gettext_lazy`, so `makemessages` picks them up.

## Seeing the pages

With `DEBUG` on, Django shows its technical 404 and 500 pages instead of
these. Use the staff-only preview to see them: `/_errors/400/`, `/_errors/403/`,
`/_errors/csrf/`, `/_errors/404/` and `/_errors/500/`. Each returns the real
status and goes through the same negotiation, so sending
`Accept: application/json` or `HX-Request: true` previews those shapes too.

Tests: `tests/public/test_errors.py`. Their test URLconf,
`tests/public/_error_urls.py`, adds views that raise on purpose in front of
the real patterns.
