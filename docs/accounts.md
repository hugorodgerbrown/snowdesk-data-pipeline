---
name: accounts
description: accounts app — Account model, registration, email verification, is_verified gate, signed-token salts, the five /account/ pages
status: current
last-reviewed: 2026-08-25
---

# Accounts

Users subscribe to bulletin alerts via a signed-token flow — no passwords, no third-party auth library. An inline HTMX form on bulletin pages (or the landing page) captures an email address; an account-access link is sent by email. Clicking the link verifies the `Account` and opens the account page where they manage their regions. Every outbound bulletin email carries a per-region unsubscribe token so subscribers can opt out without logging in.

**Entry points** — the subscribe form is a single partial included wherever a CTA is needed:

```django
{# bulletin page — region pre-seeded #}
{% include "accounts/partials/subscribe_form.html" with region_id=region.region_id %}

{# landing page — no region context #}
{% include "accounts/partials/subscribe_form.html" %}
```

The outer wrapper is `<div id="subscribe-cta-{{ region_id|default:'global' }}">`. The form posts to `accounts:subscribe` with `hx-target="this"` and `hx-swap="outerHTML"` so the success card replaces the form in-place.

**URL surfaces** — all mounted under `/account/` (`app_name = "accounts"`). The app was remounted from `/subscribe/` to `/account/` in SNOW-430; `config/urls.py` keeps a permanent (301) redirect from `/subscribe/*` so in-flight account-access and unsubscribe email links still resolve. The account-access token route was renamed `account/<token>/` → `access/<token>/` at the same time (a dedicated legacy redirect maps `/subscribe/account/<token>/` → `/account/access/<token>/`).

| URL | Name | Method | Purpose |
|-----|------|--------|---------|
| `/account/` | `hub` | GET | Subscriptions — one card per subscribed region (SNOW-667; favourites moved off it by SNOW-668) |
| `/account/favourites/` | `favourites` | GET | Saved pins — hosts `favourites:list` by `hx-get` (SNOW-668) |
| `/account/observations/` | `observations` | GET | The user's own field reports; view lives in `apps.observations` (SNOW-677) |
| `/account/routes/` | `routes` | GET | The user's own GPX routes; view lives in `apps.routes`, 404 behind the `routes` flag (SNOW-713) |
| `/account/settings/` | `settings` | GET | Email, passkeys, telemetry, sync log, reset local data, sign out, delete account (SNOW-667) |
| `/account/subscribe/` | `subscribe` | POST | HTMX inline subscribe form (moved off `/account/` by SNOW-667) |
| `/account/register/` | `register` | GET + POST | Standalone registration (email required, name optional); sends a verification link |
| `/account/verify/<token>/` | `verify` | GET + POST | GET shows a confirm button (no state change); POST marks the `Account` verified, logs in, redirects to setup |
| `/account/setup/` | `setup` | GET | Post-verification credential-setup landing (extended by SNOW-431/434) |
| `/account/setup/password/` | `set_password` | POST | Set a password for the logged-in user (SNOW-431) |
| `/account/reset-password/` | `reset_password` | GET + POST | Request a password reset by email (SNOW-432) |
| `/account/reset-password/<token>/` | `reset_password_confirm` | GET + POST | Choose a new password from a reset link (SNOW-432) |
| `/account/change-email/` | `change_email` | GET + POST | Request a change of account email (SNOW-433) |
| `/account/change-email/<token>/` | `change_email_confirm` | GET + POST | Confirm + apply an email change (SNOW-433) |
| `/account/access/<token>/` | `account` | GET + POST | GET shows a confirm button (no state change, no login); POST verifies the `Account` and signs in (SNOW-439) |
| `/account/manage/` | `manage` | GET | Permanent redirect to `/account/` (SNOW-667) |
| `/account/manage/remove/<region_id>/` | `remove_region` | POST | HTMX — remove one region from the authenticated account |
| `/account/manage/delete/` | `delete_account` | POST | HTMX — hard-delete the authenticated account (User) and cascade subscriptions |
| `/account/unsubscribe/<token>/` | `unsubscribe` | GET + POST | One-click region unsubscribe |
| `/account/unsubscribe-done/` | `unsubscribe_done` | GET | Post-unsubscribe confirmation page |

**Account area layout (SNOW-667 → SNOW-668)** — `/account/manage/` was a single 529-line template stacking nine unranked sections. It is now five pages, one per list: `/account/` (subscriptions), `/account/favourites/`, `/account/observations/`, `/account/routes/` and `/account/settings/`, with `/account/manage/` kept as a permanent redirect so old bookmarks and in-flight email links still resolve.

**The nav avatar menu is the area's only navigation**, by decision — [`docs/decisions/account-area-navigation-lives-in-the-nav-menu.md`](decisions/account-area-navigation-lives-in-the-nav-menu.md). Two sub-navs were built and removed (SNOW-667's grouped strip, rendered from a since-deleted `apps/accounts/subnav.py`; SNOW-705's flat tab strip), so adding a page means adding one `<a>` to `templates/includes/nav.html` — **and an assertion to `tests/public/test_nav_partial.py`**. That is not a formality: `/account/observations/` and `/account/routes/` both shipped mounted, tested and reachable only by typing the URL, because their tests reverse the URL directly. SNOW-668 linked them and added the per-entry assertions. A gated page needs its entry gated to match, or the menu offers a broken destination — Routes was gated that way until SNOW-724 removed the `routes` flag and the 404 behind it, and every entry is now inside the signed-in branch and nothing more.

Two things to know before touching the routing. `subscribe_partial` used to own the `""` route, which made a GET of `/account/` answer 405; it moved to `/account/subscribe/` keeping its URL name, so every `{% url 'accounts:subscribe' %}` is unaffected. And none of `hub_view`, `settings_view` or `favourites_view` is `@never_cache`, nor may any of them set `Cache-Control: private, no-store`: the offline favourites roster reads `/account/favourites/` out of the PWA shell cache, and `static/js/sw.js` refuses to cache a `no-store` response, so safety comes from the `X-SW-Principal` stamp instead. Their two neighbours in the area — `my_routes` and `my_observations`, whose views live in other apps — DO send `no-store`, deliberately, so copying a header between account pages is the mistake to watch for.

**Models** (SNOW-514 collapsed `Subscriber` into `Account` — there is now a single public-user identity):
- `Account(user, is_verified, verified_at, display_name, acquisition_request, pending_email, pending_email_requested_at)` — the single public-user identity, OneToOne to `auth.User` (`related_name="account"`). Auto-created at every public entry point (subscribe, sign-in, register) via `Account.objects.get_or_create_for_email(email)` (username == email == lowercased). `is_verified` is the sole "email proven reachable" gate, set by **every** email-proving link (`verify_view`, `account_view`, `change_email_confirm_view`, and `reset_password_confirm_view`); it is deliberately distinct from `User.is_active` (the kill switch). `Account.mark_verified(now)` mutates in memory and returns `self` (idempotent on `verified_at`); the caller owns the `save()` + `login()`. `acquisition_request` (FK to `core.RequestLog`, `SET_NULL`) records the request that first created the account — first-observation wins, never overwritten. `pending_email` / `pending_email_requested_at` (SNOW-433) hold a new address awaiting verification without touching the live `User.email`.
- `Subscription(account, region)` — links an `Account` to a `regions.MicroRegion`. `unique_together` on `(account, region)`.
- Delete semantics: removing a region (via the manage page, bulletin page, or unsubscribe token) deletes only the `Subscription` row — the `User` and `Account` survive, even when it was the last region, and the caller stays signed in. `delete_account` is the **sole** hard-delete path, available to any authenticated account (including a registered-only account with zero subscriptions) — it deletes the `User`, which cascades to `Account` and any `Subscription` rows.

**Change email (SNOW-433)** — a signed-in user changes their address via the same re-verify pattern; `username == email` is never touched until the new address is proven. `change_email_view` (`/account/change-email/`, 3/m) records the new address on `Account.pending_email` (a nullable slot; the account keeps its current verified address), emails the **new** address a confirmation (`SALT_EMAIL_CHANGE`, bound to user + address) and the **old** address a link-free FYI. A new address already owned by another account is a **silent no-op** — same "check your inbox" response, no confirmation sent, `pending_email` left unset. `change_email_confirm_view` (`/account/change-email/<token>/`, 10/m) requires `pending_email` to still match (latest-request-wins + single-use) and the address to still be free, then on POST atomically swaps `username`/`email`, stamps verified, clears the pending slot, re-`login()`s under the new identity, and FYIs the old address again. `mask_email` on both addresses in logs.

**Password reset (SNOW-432)** — "forgot" and "change" password are the same email-re-verification flow; there is no "enter your old password" path. `reset_password_request_view` (`/account/reset-password/`, 3/m) always returns the same "check your inbox" page — `send_password_reset_email` no-ops silently for unknown and passwordless (magic-link/passkey-only) addresses, so the response is byte-identical across all three cases. `reset_password_confirm_view` (`/account/reset-password/<token>/`, 10/m) verifies the single-use token, renders the SNOW-431 set-password form on GET, and on POST saves the new password, `login()`s (session cycled), and redirects to manage. Because the token fingerprint is bound to the old password hash, replaying a used link renders `link_expired` (400).

**Password credentials (SNOW-431)** — the setup page (and, later, SNOW-432's reset-confirm page) offer an optional "set a password" card backed by `SnowdeskSetPasswordForm` (Django's `SetPasswordForm` restyled). `set_password_view` (`/account/setup/password/`, auth-gated) validates against `AUTH_PASSWORD_VALIDATORS`, saves, and calls `update_session_auth_hash` so the session survives the hash change. `sign_in_view` gains a password branch (toggle-revealed field on `sign_in.html`): `authenticate(username=email, password=…)` via the default `ModelBackend` — wrong password, unknown email, and passwordless accounts all fail identically ("Invalid email or password"), so no enumeration. Submitting email with no password keeps the magic-link flow; password sign-in is a progressive enhancement over the no-JS baseline.

**Registration & verification (SNOW-430)** — `register_view` (`/account/register/`, 3/min) creates or reuses a `User` + unverified `Account` (no `Subscription` rows) and enqueues a verification email (`send_verification_email`, `SALT_EMAIL_VERIFICATION`). The response is indistinguishable across new / unverified / already-verified emails: an already-verified address gets **no** second email (nothing to verify), an unverified one has its link re-sent. `verify_view` (`/account/verify/<token>/`) renders a confirm button on GET (no state change — safe against link-prefetch scanners) and only marks the account verified + logs in on POST. **`is_verified` gate**: `observations` views (`report_form`, `report_submit`) reject an authenticated-but-unverified user with 403 via `_auth_gate`.

**Tokens** — `apps/accounts/services/token.py` uses Django's built-in `TimestampSigner` (no extra secret needed; derived from `settings.SECRET_KEY` + salt):

- `SALT_ACCOUNT_ACCESS` — 24h TTL (configurable via `ACCOUNT_TOKEN_MAX_AGE`). Used for account-access email links.
- `SALT_EMAIL_VERIFICATION` — 24h TTL (`ACCOUNT_TOKEN_MAX_AGE`). Used for registration verification links (SNOW-430).
- `SALT_PASSWORD_RESET` — 24h TTL, **single-use**: the signed value embeds a fingerprint (`salted_hmac`) of the user's current password hash + `last_login`, so the token stops verifying the moment the password changes — no persisted used-token record needed (SNOW-432, mirrors Django's `PasswordResetTokenGenerator`). `generate_password_reset_token(user)` / `verify_password_reset_token(token, max_age)`.
- `SALT_EMAIL_CHANGE` — 24h TTL. The signed value is `{user_pk}|{new_email}`, binding the token to both the requesting user and the specific pending address so it can't be replayed against a different user or address (SNOW-433). `generate_email_change_token(user, new_email)` / `verify_email_change_token(token, max_age)`.
- `SALT_UNSUBSCRIBE` — no expiry (`max_age=None`). Embedded in bulletin emails; permanent so an account can always opt out from a historical email.
- Cross-salt replay is blocked at the signing layer — a token generated with one salt cannot be verified with another.
- `generate_unsubscribe_token(email, region_id)` / `verify_unsubscribe_token(token)` — convenience wrappers that encode `{email}|{region_id}` and use `SALT_UNSUBSCRIBE`.

**Indistinguishable responses** — `subscribe_partial` returns a byte-equal `subscribe_success_access.html` fragment for the two branches where the submitter cannot already know whether the email is registered: case A (new account) and case B (existing-unverified). This A=B equality is the security-relevant invariant — it stops an unauthenticated submitter from probing whether an address is on the system. The verified-account branches are intentionally distinct: case C (verified, new region) returns `subscribe_success_added.html` and case D (verified, existing region) returns `subscribe_success_already.html`. Distinguishing C from D leaks no new information, because reaching either branch already requires the submitter to know the address is a verified account; the two responses just give a more useful confirmation message. `POST /manage/` (unauthenticated) returns the same "check your inbox" page regardless of whether the email is known. **If you change `subscribe_partial`, preserve A=B byte-equality** — C and D are free to diverge further, but A and B must continue to return the identical fragment.

**Rate limiting** — `django-ratelimit`, IP-keyed, `block=False` (views check `request.limited` and return 429 manually):

| View | Rate |
|------|------|
| `subscribe_partial` | 5/min |
| `register_view` POST | 3/min |
| `manage_view` POST (unauthenticated) | 3/min |
| `unsubscribe_view` | 10/min |

Production uses `DatabaseCache` (`LOCATION = "django_cache"`) so rate-limit counters are shared across workers. The cache table is created by `apps/accounts/migrations/0002_create_cache_table.py`. Development sets `RATELIMIT_ENABLE = False` so tests are not throttled.

**Account page** — `account_view` mirrors the verify-email flow (SNOW-439): a GET on the account-access ("magic link") URL renders a confirm page (`accounts/access.html`) with a POST button and performs **no** state change and **no** login — so following the emailed link, or a link-prefetch scanner hitting it, never grants account access on a GET verb. The POST from that page verifies the `Account` (`is_verified → True`, stamps `verified_at`) and signs in; re-POSTing within the 24h window is idempotent (no double-stamp). The confirm page carries `Referrer-Policy: same-origin` (not `no-referrer`) so its POST passes Django's CSRF check on HTTPS — see [`docs/decisions/referrer-policy-same-origin-for-csrf.md`](decisions/referrer-policy-same-origin-for-csrf.md) (SNOW-438).

**Passkeys (WebAuthn)** — users can sign in without an email round-trip:

- `PasskeyCredential` (`apps/accounts/models.py`) — one row per registered passkey per device; stores the base64url `credential_id`, raw COSE `public_key` bytes, `sign_count` (clone detection), `aaguid`, and `device_type` (`platform` / `cross-platform`). FK to `auth.User` (`on_delete=CASCADE`). Any authenticated `auth.User` — including staff/superusers without an `Account` profile — can register and use passkeys. The WebAuthn user handle is an unsalted SHA-256 digest of the user's primary key (stable, non-PII, within the 64-byte limit; see the SNOW-334 plan for the full rationale).
- `/account/sign-in/` (`sign_in`) — GET renders the email form with passkey conditional UI; POST (rate-limited 3/min) always returns the same "check your inbox" response whether or not the email is known.
- JSON API endpoints (`apps/accounts/views_passkey.py`): `/account/webauthn/auth-request/` (GET, challenge), `/account/webauthn/auth-response/` (POST, verifies `navigator.credentials.get()` and logs the `auth.User` in via Django auth; 10/min), `/account/webauthn/register-request/` (GET, requires `request.user.is_authenticated`), `/account/webauthn/register-response/` (POST, persists the new credential; 10/min).
- `/account/manage/passkeys/<uuid>/delete/` (`passkey_delete`) — HTMX POST, hard-deletes one passkey for the authenticated user (scoped to `user=request.user`; 5/min).
- **Setup-page CTA (SNOW-434)** — the credential-setup page (`setup.html`) offers a "Save a passkey for this device" card that wires the existing `passkey_register_request`/`passkey_register_response` endpoints via `window.Passkey.registerPasskey`; on `passkey:registered` it auto-advances to manage, on `passkey:unsupported` it hides the card. No new backend. `manage_view` renders for any authenticated user (a registered-only user has an `Account` but no `Subscription` rows); it no longer redirects non-subscribers to sign-in, which used to loop against `sign_in_view`'s authenticated-user redirect.
- Signed account-access tokens remain the fallback (and bootstrap) path: an account registers a passkey only after first authenticating via an emailed account link, and unsubscribe tokens are unaffected.
- Server config: `WEBAUTHN_RP_ID` / `WEBAUTHN_RP_NAME` / `WEBAUTHN_ORIGIN` env vars (see `render.yaml`).

**Email** — Django's standard SMTP backend. No custom backend.

- **Development**: Mailpit on `localhost:1025` (no auth, no TLS). Web inbox at `http://localhost:8025`.
- **Production**: Resend SMTP relay — `EMAIL_HOST=smtp.resend.com`, `EMAIL_PORT=587`, `EMAIL_HOST_USER=resend`, `EMAIL_HOST_PASSWORD=<Resend API key>`, `EMAIL_USE_TLS=True`.

**Settings** (all in `.env`):
- `ACCOUNT_TOKEN_MAX_AGE` — account-access token TTL in seconds; defaults to `86400` (24h).
- `SITE_BASE_URL` — base URL for absolute links when no request is available (e.g. management commands).
- `EMAIL_HOST` / `EMAIL_PORT` / `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` / `EMAIL_USE_TLS` — standard Django SMTP settings.
- `DEFAULT_FROM_EMAIL` — sender address for outbound mail.

**Dropped settings** — `MAGIC_LINK_SECRET_KEY`, `MAGIC_LINK_EXPIRY_SECONDS`, `MAGIC_LINK_BASE_URL`, and `RESEND_API_KEY` are no longer referenced anywhere in the codebase. Do not look for them.
