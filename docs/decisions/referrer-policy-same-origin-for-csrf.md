---
name: referrer-policy-same-origin-for-csrf
description: Token-confirm GET pages use Referrer-Policy same-origin, not no-referrer, so their same-origin POST passes Django CSRF on HTTPS (SNOW-438)
status: current
last-reviewed: 2026-07-19
---

# Referrer-Policy on token-bearing confirm pages

## Decision

Token-bearing views in `accounts/views.py` set their own `Referrer-Policy`
header (overriding the global `strict-origin-when-cross-origin` default from
`SecurityHeadersMiddleware`). The value depends on whether the response
renders a same-origin POST form:

- **`same-origin`** on any GET page that renders a POST form back to the same
  view: `verify_view`, `reset_password_confirm_view`, `change_email_confirm_view`,
  `unsubscribe_view`, and `account_view` (all the "click the button to confirm"
  interstitials).
- **`no-referrer`** on terminal responses that carry no POST form: the
  `link_expired` error page, and the post-confirmation redirects.

The two module constants `_REFERRER_CONFIRM_PAGE` and `_REFERRER_NO_REFERRER`
carry this distinction.

## Why

These pages carry a secret token in the URL path, so they must not leak the
URL to third parties via the `Referer`/`Origin` header. `no-referrer` does
that — but it strips the header from **every** request, including the page's
own same-origin POST. Per the Fetch spec, a non-GET request from a page with
`no-referrer` is sent with `Origin: null`. On HTTPS, Django's
`CsrfViewMiddleware` checks the `Origin` header; `null` matches no trusted
origin, so the POST is rejected with **403** ("CSRF verification failed").

This was invisible locally: on plain HTTP (dev) Django skips the Origin /
strict-Referer check, so the confirm button worked in dev and only failed on
staging/production (SNOW-438: the registration verify button).

`same-origin` is the fix: it sends a valid `Origin`/`Referer` for same-origin
requests (so CSRF passes) but strips them entirely for cross-origin requests
(so the token URL still never reaches a third party). The anti-leak intent is
fully preserved.

## Consequences

- New token-confirm interstitials must use `_REFERRER_CONFIRM_PAGE`
  (`same-origin`), **not** `no-referrer`, or their POST button breaks on HTTPS.
- The Django test client cannot reproduce the failure (it is HTTP and applies
  no referrer policy), so tests assert the **header value** rather than a live
  403→200. `same-origin` on the confirm pages is the fix's contract.
- Terminal responses (error pages, redirects) keep `no-referrer` — stricter,
  and they carry no form to break.
