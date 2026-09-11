---
name: auth-testing-checklist
description: Manual auth test checklist — magic-link, password, passkey sign-in, logout, registration, password reset, change email, account deletion
status: current
last-reviewed: 2026-09-11
---

# Login / Logout Testing Checklist — Snowdesk

A tick-box script for manually exercising every authentication journey in the
`apps/accounts/` app (mounted at `/account/`). Complements the scenario tables in
[`testing-scenarios.md`](testing-scenarios.md); this doc is auth-focused and
covers the sign-in methods, registration, password reset, and email change that
the scenario doc does not.

> **Prerequisites**
>
> 1. Dev server running at http://localhost:8000 and Mailpit at http://localhost:8025 (catches all outbound email).
> 2. Database seeded: `uv run python manage.py loaddata eaws_CH && uv run python manage.py import_resorts --commit && uv run python manage.py seed_test_data --all --commit`.
> 3. Test accounts prepared:
>    - **A** — password set (via `/account/setup/password/` or reset).
>    - **B** — magic-link only (never set a password).
>    - **C** — has ≥1 registered passkey.
> 4. **Rate limits are disabled in development** (`RATELIMIT_ENABLE=False`). To
>    exercise the 429 cases, run the server with `RATELIMIT_ENABLE=True`.

---

## 1. Magic-link sign-in (passwordless baseline)

- [ ] `GET /account/sign-in/` renders the email form; password field is hidden behind a toggle.
- [ ] Submit a **known** email with no password → "Check your inbox" page (`manage_sent.html`).
- [ ] Mailpit receives an account-access email linking to `/account/access/<token>/`.
- [ ] `GET` the access link → confirm page with a POST button; **no login, no state change** (prefetch safety).
- [ ] Click the button (POST) → signed in, redirected to `/account/?just_confirmed=1`, which 301s on to `/?panel=favourites`; confirmation banner shown.
- [ ] Submit an **unknown** email → identical "check your inbox" page, no email sent (enumeration parity).
- [ ] Re-POST the same access link within the TTL → idempotent (lands in the same place, no error).

## 2. Password sign-in

- [ ] Correct email + correct password (account A) → signed in, redirected to the validated `?next=` if one was supplied, otherwise the map at `/`.
- [ ] Correct email + **wrong** password → generic error, stays on page (status 200).
- [ ] **Unknown** email + any password → same generic error (indistinguishable).
- [ ] Passwordless account (B) + any password → same generic error.
- [ ] Already signed in, visit `/account/sign-in/` → redirect to `?next=` or the map at `/`.

## 3. Passkey sign-in (WebAuthn)

- [ ] `GET /account/sign-in/` and wait without touching the page → no passkey provider UI appears (account C); the email field is not autofocused, so conditional UI must wait for real interaction.
- [ ] `/account/sign-in/`, focus the email field → browser offers the passkey via conditional-UI autofill (account C).
- [ ] Complete the OS prompt → `{"ok": true}`, signed in, redirected to `?next=` or the map.
- [ ] Sign in with a passkey deleted server-side but still cached in the browser → 404 `unknown_credential`; browser clears the stale credential.
- [ ] Unsupported browser → passkey affordance absent, email/password path still works.

## 4. Logout

- [ ] Nav avatar dropdown → "Sign out" → POST to `/account/sign-out/` → redirect to `/account/sign-in/`.
- [ ] `GET /account/sign-out/` directly → 405 (POST-only).
- [ ] After logout, visit `/account/settings/` → redirect to `/account/sign-in/` (session cleared).

## 5. Registration → email verification → login

- [ ] `GET /account/register/`, submit a **new** email (name optional) → "Check your inbox".
- [ ] Mailpit verification email → `GET /account/verify/<token>/` → confirm page, no state change.
- [ ] POST the confirm button → verified + signed in → redirected to `/account/setup/`.
- [ ] On setup, optionally set a password and/or save a passkey; neither is a gate — can proceed.
- [ ] Register an **already-verified** email → same "check your inbox", but **no** second email sent.
- [ ] As an authenticated-but-unverified user, attempt an observation submission → 403 (verification gate).

## 6. Passkey registration & deletion

- [ ] Sign in via magic link first (a passkey needs an authenticated session to bootstrap).
- [ ] On `/account/setup/` or `/account/settings/`, "Save a passkey for this device" → OS prompt → passkey card appears.
- [ ] Unsupported browser → passkey card hidden (`passkey:unsupported`).
- [ ] `/account/settings/` → delete a passkey → card removed (HTMX swap).
- [ ] Attempt to delete a passkey not owned by the current user → 404.

## 7. Password reset (ends in login)

- [ ] `/account/reset-password/`, submit account A's email → "check your inbox".
- [ ] Unknown email and passwordless account (B) → identical "check your inbox", no email sent.
- [ ] Reset email → `/account/reset-password/<token>/` → set-password form → submit → signed in.
- [ ] **Replay the used reset link** → `link_expired` (400) — token is single-use, invalidated by the password change.

## 8. Change email (auth-gated, re-verification)

- [ ] `/account/change-email/` while signed in, submit a **new** address → "check your inbox".
- [ ] The **new** address receives a confirmation link; the **old** address receives a link-free FYI.
- [ ] Confirm link → identity swapped (`username`/`email`), re-signed-in under the new address.
- [ ] Request a change to an address **already owned** by another account → silent no-op (same page, no email).
- [ ] Replay a stale change-email link after a newer request → `link_expired`.

## 9. Account deletion (auth-gated)

- [ ] `/account/settings/` → delete the account → HX-Redirect lands on `/account/deleted/`.
- [ ] That page says the account and its data were deleted; it says nothing about regions, pins or unsubscribing (SNOW-875).
- [ ] The session is gone: reloading `/account/settings/` redirects to sign-in.
- [ ] The `User`, its `Account` and everything it owned are gone — deletion is the only hard-delete path.

## 10. Nav visibility (check in each state)

- [ ] **Anonymous** → "Register" and "Sign in" links visible.
- [ ] **Authenticated account** → avatar dropdown: the offline-mode switch, "Settings", "Sign out" — no region links, no list entries (SNOW-802/803: those are map sheets).
- [ ] `/account/`, `/account/favourites/`, `/account/routes/`, `/account/observations/` → 301 to the map with the matching sheet open.
- [ ] **Staff** → extra cog dropdown (Component library, Django admin, …) rendered alongside the avatar.

## 11. Cross-cutting checks

- [ ] **GET never changes state** on any `/access/`, `/verify/`, or `/change-email/` confirm page — only the POST button acts.
- [ ] **Enumeration parity**: register, sign-in and password-reset give indistinguishable responses for known vs unknown emails. (The inline-subscribe CTA that used to carry the same invariant was removed in SNOW-875.)
- [ ] **Rate limits** (with `RATELIMIT_ENABLE=True`): exceed each and confirm a 429 —
      sign-in 3/min, register 3/min, reset-request 3/min, reset-confirm 10/min,
      change-email 3/min, change-email-confirm 10/min, passkey auth/register 10/min,
      passkey delete 5/min, delete-account 3/min.
- [ ] **Token TTLs**: account-access / verification / reset / change-email all expire after 24h → `link_expired`.
- [ ] **Cross-salt replay** is blocked — e.g. using an access token on the verify route → `link_expired`.
- [ ] **Post-login redirects**: verify → setup; logout → sign-in. Every other path honours a same-site `?next=` (SNOW-825/826), validated through `apps.accounts.redirects.safe_next`, and falls back to the map at `/` when it is absent, off-site, or names an auth-entry page.
- [ ] **Legacy redirects**: old `/subscribe/…` links 301 to `/account/…`; `/subscribe/account/<token>/` → `/account/access/<token>/`.
