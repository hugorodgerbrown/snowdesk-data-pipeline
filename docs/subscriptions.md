# Subscriptions

Users subscribe to bulletin alerts via a signed-token flow — no passwords, no third-party auth library. An inline HTMX form on bulletin pages (or the landing page) captures an email address; an account-access link is sent by email. Clicking the link activates the subscriber and opens the account page where they manage their regions. Every outbound bulletin email carries a per-region unsubscribe token so subscribers can opt out without logging in.

**Entry points** — the subscribe form is a single partial included wherever a CTA is needed:

```django
{# bulletin page — region pre-seeded #}
{% include "subscriptions/partials/subscribe_form.html" with region_id=region.region_id %}

{# landing page — no region context #}
{% include "subscriptions/partials/subscribe_form.html" %}
```

The outer wrapper is `<div id="subscribe-cta-{{ region_id|default:'global' }}">`. The form posts to `subscriptions:subscribe` with `hx-target="this"` and `hx-swap="outerHTML"` so the success card replaces the form in-place.

**URL surfaces** — all mounted under `/subscribe/` (`app_name = "subscriptions"`):

| URL | Name | Method | Purpose |
|-----|------|--------|---------|
| `/subscribe/` | `subscribe` | POST | HTMX inline subscribe form |
| `/subscribe/account/<token>/` | `account` | GET | Verify token; activate subscriber |
| `/subscribe/manage/` | `manage` | GET + POST | Email entry (unauth) or region management (auth) |
| `/subscribe/manage/remove/<region_id>/` | `remove_region` | POST | HTMX — remove one region from the authenticated subscriber |
| `/subscribe/manage/delete/` | `delete_account` | POST | HTMX — hard-delete the authenticated subscriber and cascade subscriptions |
| `/subscribe/unsubscribe/<token>/` | `unsubscribe` | GET + POST | One-click region unsubscribe |
| `/subscribe/unsubscribe-done/` | `unsubscribe_done` | GET | Post-unsubscribe confirmation page |

**Models**:
- `Subscriber(email, status, confirmed_at)` — `status` is a `TextChoices` with `pending` (address captured, not yet confirmed) and `active` (confirmed; receives emails). `confirmed_at` is stamped on first account-link click.
- `Subscription(subscriber, region)` — links a `Subscriber` to a `pipeline.Region`. `unique_together` on `(subscriber, region)`.
- Hard-delete semantics: removing all regions via the manage page or unsubscribe token cascades via `on_delete=CASCADE` to drop the `Subscriber` row.

**Tokens** — `subscriptions/services/token.py` uses Django's built-in `TimestampSigner` (no extra secret needed; derived from `settings.SECRET_KEY` + salt):

- `SALT_ACCOUNT_ACCESS` — 24h TTL (configurable via `ACCOUNT_TOKEN_MAX_AGE`). Used for account-access email links.
- `SALT_UNSUBSCRIBE` — no expiry (`max_age=None`). Embedded in bulletin emails; permanent so a subscriber can always opt out from a historical email.
- Cross-salt replay is blocked at the signing layer — a token generated with one salt cannot be verified with another.
- `generate_unsubscribe_token(email, region_id)` / `verify_unsubscribe_token(token)` — convenience wrappers that encode `{email}|{region_id}` and use `SALT_UNSUBSCRIBE`.

**Indistinguishable responses** — `subscribe_partial` returns a byte-equal `subscribe_success_access.html` fragment for the two branches where the submitter cannot already know whether the email is registered: case A (new subscriber) and case B (existing-pending). This A=B equality is the security-relevant invariant — it stops an unauthenticated submitter from probing whether an address is on the system. The active-subscriber branches are intentionally distinct: case C (active, new region) returns `subscribe_success_added.html` and case D (active, existing region) returns `subscribe_success_already.html`. Distinguishing C from D leaks no new information, because reaching either branch already requires the submitter to know the address is registered as active; the two responses just give a more useful confirmation message. The active branches still call `send_noop_email` (generates a token + renders templates but does not call `send_mail`) to equalise CPU timing against A/B. `POST /manage/` (unauthenticated) returns the same "check your inbox" page regardless of whether the email is known. **If you change `subscribe_partial`, preserve A=B byte-equality** — C and D are free to diverge further, but A and B must continue to return the identical fragment.

**Rate limiting** — `django-ratelimit`, IP-keyed, `block=False` (views check `request.limited` and return 429 manually):

| View | Rate |
|------|------|
| `subscribe_partial` | 5/min |
| `manage_view` POST (unauthenticated) | 3/min |
| `unsubscribe_view` | 10/min |

Production uses `DatabaseCache` (`LOCATION = "django_cache"`) so rate-limit counters are shared across workers. The cache table is created by `subscriptions/migrations/0003_create_cache_table.py`. Development sets `RATELIMIT_ENABLE = False` so tests are not throttled.

**Account page** — `account_view` is dual-purpose: the first click on a pending subscriber's link flips `status → active` and stamps `confirmed_at`; re-clicks within the 24h window are idempotent (no double-stamp). Stores `subscriber_uuid` in the session so the manage page skips the email-entry step for the same browser session.

**Email** — Django's standard SMTP backend. No custom backend.

- **Development**: Mailhog on `localhost:1025` (no auth, no TLS). Web inbox at `http://localhost:8025`.
- **Production**: Resend SMTP relay — `EMAIL_HOST=smtp.resend.com`, `EMAIL_PORT=587`, `EMAIL_HOST_USER=resend`, `EMAIL_HOST_PASSWORD=<Resend API key>`, `EMAIL_USE_TLS=True`.

**Settings** (all in `.env`):
- `ACCOUNT_TOKEN_MAX_AGE` — account-access token TTL in seconds; defaults to `86400` (24h).
- `SITE_BASE_URL` — base URL for absolute links when no request is available (e.g. management commands).
- `EMAIL_HOST` / `EMAIL_PORT` / `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` / `EMAIL_USE_TLS` — standard Django SMTP settings.
- `DEFAULT_FROM_EMAIL` — sender address for outbound mail.

**Dropped settings** — `MAGIC_LINK_SECRET_KEY`, `MAGIC_LINK_EXPIRY_SECONDS`, `MAGIC_LINK_BASE_URL`, and `RESEND_API_KEY` are no longer referenced anywhere in the codebase. Do not look for them.
