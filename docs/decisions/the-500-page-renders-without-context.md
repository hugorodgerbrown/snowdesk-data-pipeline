---
name: the-500-page-renders-without-context
description: Why 500/400 pages (errors/_standalone.html, server_error) render with no context processors, request.user or DB
status: current
last-reviewed: 2026-10-20
---

# The 500 page renders without context

## Decision

The 500 and 400 pages render `templates/errors/_standalone.html` through
`render_to_string` without the request, so no context processor runs. The
template does not extend `public/base.html`. Its only asset is
`css/output.css`. The request is passed as a plain variable, and the
template reads only `request.csp_nonce` from it, for the dark-mode script.
If that render fails, the handler returns a literal HTML string.

## Why

A 500 page that needs the thing that just failed shows nothing.
`public/base.html` runs about a dozen context processors. One reads
`request.user` (session, then database). Several build absolute URLs from
the host, and the nav's account menu queries the account. A database outage,
a broken migration or a bad host header would make each of these raise
inside the error handler, and Django would then return its bare
"Server Error (500)".

The 400 page shares the shell because `DisallowedHost` is a 400. In that
case every call to `request.get_host()`, and so every absolute-URL context
processor, raises again.

403 and 404 are different. The site is working, the reader has only
followed a bad link, and they should keep their nav and account. Those
pages render with the full context. If that render fails, they drop to the
standalone shell.

## Consequences

- The 500 page has no account menu, no footer and no feature flags. Anything
  added to it must be computable from a plain dict.
- Do not add an inline `<style>` to the standalone shell. Production's CSP
  blocks it, so styling comes from `output.css` classes only.
- The shell copies the nav wordmark and `_status_page.html`'s heading class
  string by hand. If one changes, change the other.
