---
name: accounts
description: accounts app — Account/Subscriber models, registration + email verification, is_verified gate, signed-token salts, /account/ mount
status: current
last-reviewed: 2026-07-18
---

# Accounts

Users subscribe to bulletin alerts via a signed-token flow — no passwords, no third-party auth library. An inline HTMX form on bulletin pages (or the landing page) captures an email address; an account-access link is sent by email. Clicking the link activates the subscriber and opens the account page where they manage their regions. Every outbound bulletin email carries a per-region unsubscribe token so subscribers can opt out without logging in.

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
| `/account/` | `subscribe` | POST | HTMX inline subscribe form |
| `/account/register/` | `register` | GET + POST | Standalone registration (email required, name optional); sends a verification link |
| `/account/verify/<token>/` | `verify` | GET + POST | GET shows a confirm button (no state change); POST marks the `Account` verified, logs in, redirects to setup |
| `/account/setup/` | `setup` | GET | Post-verification credential-setup landing (extended by SNOW-431/434) |
| `/account/access/<token>/` | `account` | GET | Verify account-access token; activate subscriber |
| `/account/manage/` | `manage` | GET + POST | Email entry (unauth) or region management (auth) |
| `/account/manage/remove/<region_id>/` | `remove_region` | POST | HTMX — remove one region from the authenticated subscriber |
| `/account/manage/delete/` | `delete_account` | POST | HTMX — hard-delete the authenticated subscriber and cascade subscriptions |
| `/account/unsubscribe/<token>/` | `unsubscribe` | GET + POST | One-click region unsubscribe |
| `/account/unsubscribe-done/` | `unsubscribe_done` | GET | Post-unsubscribe confirmation page |

**Models**:
- `Account(user, is_verified, verified_at, display_name)` — the identity profile (SNOW-430), OneToOne to `auth.User` (`related_name="account"`). Created by the registration flow independent of any bulletin subscription — an `Account` can exist with no `Subscriber` row. `is_verified` records that the *current* email address has been proven reachable; it is deliberately distinct from `User.is_active` (the kill switch). `Account.mark_verified(now)` mutates in memory and returns `self` (idempotent on `verified_at`); the caller owns the `save()` + `login()`. `Account.objects.get_or_create_for_email(email)` mirrors the Subscriber helper (username == email == lowercased).
- `Subscriber(email, status, confirmed_at)` — `status` is a `TextChoices` with `pending` (address captured, not yet confirmed) and `active` (confirmed; receives emails). `confirmed_at` is stamped on first account-link click.
- `Subscription(subscriber, region)` — links a `Subscriber` to a `regions.MicroRegion`. `unique_together` on `(subscriber, region)`.
- Hard-delete semantics: removing all regions via the manage page or unsubscribe token cascades via `on_delete=CASCADE` to drop the `Subscriber` row.

**Registration & verification (SNOW-430)** — `register_view` (`/account/register/`, 3/min) creates or reuses a `User` + unverified `Account` (no `Subscriber`) and enqueues a verification email (`send_verification_email`, `SALT_EMAIL_VERIFICATION`). The response is indistinguishable across new / unverified / already-verified emails: an already-verified address gets **no** second email (nothing to verify), an unverified one has its link re-sent. `verify_view` (`/account/verify/<token>/`) renders a confirm button on GET (no state change — safe against link-prefetch scanners) and only marks the account verified + logs in on POST. **`is_verified` gate**: `observations` views (`report_form`, `report_submit`) reject an authenticated-but-unverified user with 403 via `_auth_gate`. Existing confirmed subscribers are migrated by the `backfill_verified_accounts` management command (dry-run by default; `--commit` to write — creates or flips a verified `Account`, stamping `verified_at` from the subscriber's `confirmed_at`).

**Tokens** — `accounts/services/token.py` uses Django's built-in `TimestampSigner` (no extra secret needed; derived from `settings.SECRET_KEY` + salt):

- `SALT_ACCOUNT_ACCESS` — 24h TTL (configurable via `ACCOUNT_TOKEN_MAX_AGE`). Used for account-access email links.
- `SALT_EMAIL_VERIFICATION` — 24h TTL (`ACCOUNT_TOKEN_MAX_AGE`). Used for registration verification links (SNOW-430).
- `SALT_UNSUBSCRIBE` — no expiry (`max_age=None`). Embedded in bulletin emails; permanent so a subscriber can always opt out from a historical email.
- Cross-salt replay is blocked at the signing layer — a token generated with one salt cannot be verified with another.
- `generate_unsubscribe_token(email, region_id)` / `verify_unsubscribe_token(token)` — convenience wrappers that encode `{email}|{region_id}` and use `SALT_UNSUBSCRIBE`.

**Indistinguishable responses** — `subscribe_partial` returns a byte-equal `subscribe_success_access.html` fragment for the two branches where the submitter cannot already know whether the email is registered: case A (new subscriber) and case B (existing-pending). This A=B equality is the security-relevant invariant — it stops an unauthenticated submitter from probing whether an address is on the system. The active-subscriber branches are intentionally distinct: case C (active, new region) returns `subscribe_success_added.html` and case D (active, existing region) returns `subscribe_success_already.html`. Distinguishing C from D leaks no new information, because reaching either branch already requires the submitter to know the address is registered as active; the two responses just give a more useful confirmation message. The active branches still call `send_noop_email` (generates a token + renders templates but does not call `send_mail`) to equalise CPU timing against A/B. `POST /manage/` (unauthenticated) returns the same "check your inbox" page regardless of whether the email is known. **If you change `subscribe_partial`, preserve A=B byte-equality** — C and D are free to diverge further, but A and B must continue to return the identical fragment.

**Rate limiting** — `django-ratelimit`, IP-keyed, `block=False` (views check `request.limited` and return 429 manually):

| View | Rate |
|------|------|
| `subscribe_partial` | 5/min |
| `register_view` POST | 3/min |
| `manage_view` POST (unauthenticated) | 3/min |
| `unsubscribe_view` | 10/min |

Production uses `DatabaseCache` (`LOCATION = "django_cache"`) so rate-limit counters are shared across workers. The cache table is created by `accounts/migrations/0002_create_cache_table.py`. Development sets `RATELIMIT_ENABLE = False` so tests are not throttled.

**Account page** — `account_view` is dual-purpose: the first click on a pending subscriber's link flips `status → active` and stamps `confirmed_at`; re-clicks within the 24h window are idempotent (no double-stamp). Stores `subscriber_uuid` in the session so the manage page skips the email-entry step for the same browser session.

**Passkeys (WebAuthn)** — users can sign in without an email round-trip:

- `PasskeyCredential` (`accounts/models.py`) — one row per registered passkey per device; stores the base64url `credential_id`, raw COSE `public_key` bytes, `sign_count` (clone detection), `aaguid`, and `device_type` (`platform` / `cross-platform`). FK to `auth.User` (`on_delete=CASCADE`). Any authenticated `auth.User` — including staff/superusers without a `Subscriber` profile — can register and use passkeys. The WebAuthn user handle is an unsalted SHA-256 digest of the user's primary key (stable, non-PII, within the 64-byte limit; see the SNOW-334 plan for the full rationale).
- `/account/sign-in/` (`sign_in`) — GET renders the email form with passkey conditional UI; POST (rate-limited 3/min) always returns the same "check your inbox" response whether or not the email is known.
- JSON API endpoints (`accounts/views_passkey.py`): `/account/webauthn/auth-request/` (GET, challenge), `/account/webauthn/auth-response/` (POST, verifies `navigator.credentials.get()` and logs the `auth.User` in via Django auth; 10/min), `/account/webauthn/register-request/` (GET, requires `request.user.is_authenticated`), `/account/webauthn/register-response/` (POST, persists the new credential; 10/min).
- `/account/manage/passkeys/<uuid>/delete/` (`passkey_delete`) — HTMX POST, hard-deletes one passkey for the authenticated user (scoped to `user=request.user`; 5/min).
- Signed account-access tokens remain the fallback (and bootstrap) path: a subscriber registers a passkey only after first authenticating via an emailed account link, and unsubscribe tokens are unaffected.
- Server config: `WEBAUTHN_RP_ID` / `WEBAUTHN_RP_NAME` / `WEBAUTHN_ORIGIN` env vars (see `render.yaml`).

**Email** — Django's standard SMTP backend. No custom backend.

- **Development**: Mailhog on `localhost:1025` (no auth, no TLS). Web inbox at `http://localhost:8025`.
- **Production**: Resend SMTP relay — `EMAIL_HOST=smtp.resend.com`, `EMAIL_PORT=587`, `EMAIL_HOST_USER=resend`, `EMAIL_HOST_PASSWORD=<Resend API key>`, `EMAIL_USE_TLS=True`.

**Settings** (all in `.env`):
- `ACCOUNT_TOKEN_MAX_AGE` — account-access token TTL in seconds; defaults to `86400` (24h).
- `SITE_BASE_URL` — base URL for absolute links when no request is available (e.g. management commands).
- `EMAIL_HOST` / `EMAIL_PORT` / `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` / `EMAIL_USE_TLS` — standard Django SMTP settings.
- `DEFAULT_FROM_EMAIL` — sender address for outbound mail.

**Dropped settings** — `MAGIC_LINK_SECRET_KEY`, `MAGIC_LINK_EXPIRY_SECONDS`, `MAGIC_LINK_BASE_URL`, and `RESEND_API_KEY` are no longer referenced anywhere in the codebase. Do not look for them.
