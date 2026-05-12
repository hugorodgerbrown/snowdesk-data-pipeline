# User personas and core journeys

This document is the canonical reference for *who* uses Snowdesk and *what
they're trying to do*. Use it as a checklist before adding new pages,
endpoints, emails, or CTAs: any new functionality should map cleanly onto
an existing journey, or — if it doesn't — be a deliberate, named addition
to this list.

It deliberately does not describe implementation. For the URL map, see
the `urls.py` modules; for ingestion, see [`management-commands.md`](management-commands.md);
for the subscriber flow internals, see [`subscriptions.md`](subscriptions.md).

---

## Personas

There are two real personas. Staff is listed for completeness but isn't
a product journey.

### 1. Anonymous visitor

Default state for almost every entry to the site. No `Subscriber` row,
no session. Arrives from a search engine, a shared link, a bookmark, or
the PWA shell. The product has to be useful to this persona without any
account — they may never subscribe.

Defining traits:
- May not know the SLF region system. The map and resort lookup must
  work without prior knowledge.
- Cannot be addressed by email. Any "you should know about X" message
  has to land on a page they're already looking at.
- Reads as much as they read. Treat the bulletin and the explainer
  content (`/how-to-read-a-bulletin/`, `/examples/…`) as equally
  important entry points.

### 2. Subscriber

Has a `Subscriber` row (created the moment they submit the inline form;
flipped to `active` once they click the account-access link). Owns a
set of `Subscription` rows pinning specific regions. Acts in two modes,
and a single feature often has to work in both:

- **Session-authenticated** — has clicked an account-access link or
  signed in with a passkey in this browser. The full management surface
  under `/subscribe/manage/` is available: add regions, remove regions,
  register passkeys, delete the account.
- **Token-authenticated** — arrived via a signed token in a bulletin
  email, with no active session. The surface is intentionally narrow:
  confirm account, unsubscribe one region. Tokens encode the subscriber
  identity, so we can act on their behalf without a login round-trip.

Defining traits:
- Identified by email. Email is the lookup key and the lowercased,
  normalised primary identifier.
- Reachable asynchronously. Most of their relationship with the product
  happens outside a session.
- Cares about specific regions, not the country as a whole. The map and
  region pages are the daily-driver surfaces; the homepage is for first
  contact.

### 3. Staff (stub)

Hugo + future collaborators. Admin pages, the `/_components/` library,
debug views, dev-only mirrors. Out of scope for product journeys but
called out so a "core journey" check doesn't accidentally drag staff
tooling into the same bucket.

---

## Core journeys

Each journey lists:
- **Entry points** — how the user arrives.
- **URL surface** — the routes that participate.
- **Key invariants** — properties that must hold for the journey to
  remain trustworthy. Break one and you've damaged the journey, not
  just a page.
- **Adding functionality here** — what to check when extending or
  changing this journey.

### J1 — Check today's bulletin (anonymous)

> "What's the danger near where I'm going today?"

The single most important journey. Everything else is secondary to a
visitor being able to look up today's bulletin for a region in under
ten seconds.

**Entry points:**
- Homepage `/` for first-time visitors.
- Map page `/map/` for returning visitors who land directly.
- Deep link to a specific region (`/<region_id>/<slug>/`) from search
  engines, shared messages, or the PWA shortcut.

**URL surface:**
- `/` — marketing landing.
- `/map/` — interactive region-choropleth map.
- `/<region_id>/` and `/<region_id>/<slug>/` — today's bulletin in
  place, never redirecting away.
- `/api/…` — JSON endpoints consumed by the map. Not a user-visible
  URL but part of this journey's reliability surface.

**Key invariants:**
- All three bulletin URL forms always render today's bulletin without a
  redirect; only the explicit-date form 302s to canonical when needed.
- Every bulletin page emits a `<link rel="canonical">` so SEO collapses
  the three forms onto one indexed URL.
- The page is usable on mobile, on a cold cache, and without any
  account.

**Adding functionality here:**
- Any new CTA on the bulletin page must not push the danger level,
  problems, or aspects below the fold on mobile.
- Anything new on the map must keep the page usable when JS is delayed
  or offline (PWA cached state).
- Don't introduce a route that would shadow the generic
  `<str:region_id>/` pattern without registering it before
  `public.urls` is included.

### J2 — Subscribe (anonymous → subscriber)

> "Tell me when this region's bulletin changes."

The conversion journey. An anonymous visitor on a bulletin page (or the
homepage) drops an email into the inline form and becomes a subscriber.

**Entry points:**
- Inline subscribe form on any bulletin page, region pre-seeded.
- Inline subscribe form on the homepage, no region context.

**URL surface:**
- `POST /subscribe/` — HTMX form submit, returns one of four success
  cards (A new, B existing-pending, C active-new-region, D
  active-already).
- Email — account-access link.
- `GET /subscribe/account/<token>/` — verifies the token, flips
  `pending → active`, stamps `confirmed_at`, drops `subscriber_uuid`
  into the session.
- `/subscribe/manage/` — the post-confirm destination.

**Key invariants:**
- Cases A and B return **byte-equal** response fragments. This is the
  security-relevant invariant: an unauthenticated submitter must not
  be able to tell whether an address is already on the system.
- Email normalisation happens at every entry point: `email.lower()`
  before storage and before lookup.
- The Resend email send is async (no blocking the request cycle from a
  web view).
- Token verification is salt-scoped: account-access tokens cannot be
  replayed as unsubscribe tokens, and vice versa.

**Adding functionality here:**
- If you touch `subscribe_partial`, A=B byte-equality is mandatory.
  Add coverage that asserts it. C and D may diverge further.
- New email templates inherit from the shared base; never inline
  unsubscribe markup, use the shared partial so the signed token is
  generated in one place.
- Rate limiting on subscribe-side endpoints is IP-keyed via
  `django-ratelimit`; new entry points should keep that pattern.

### J3 — Sign in to manage (subscriber, session)

> "Add another region. Remove one I don't care about any more. Register
> a passkey so I don't need email links."

**Entry points:**
- `/subscribe/sign-in/` — the dedicated sign-in page.
- Account-access email link (covered in J2) — implicitly signs the
  user in on click.
- A passkey present on the device, surfaced as a "Sign in with
  passkey" option on the sign-in page.

**URL surface:**
- `/subscribe/sign-in/` — email entry or passkey challenge.
- `/subscribe/account/<token>/` — token consumed during the email
  branch of sign-in.
- `/subscribe/manage/` — landing page once authenticated.
- `/subscribe/manage/remove/<region_id>/` — HTMX region removal.
- `/subscribe/manage/delete/` — hard-delete the account.
- `/subscribe/manage/passkeys/<uuid>/delete/` — remove a passkey.
- `/subscribe/webauthn/…` — WebAuthn challenge/response endpoints.
- `/subscribe/sign-out/` — POST-only.

**Key invariants:**
- Adding a new region happens on a bulletin page through the inline
  subscribe form — the manage page itself does not have a region
  picker. There is one entry point for "I want this region", not two.
- Hard-delete cascades: removing the last region or deleting the
  account drops the `Subscriber` row entirely.
- Passkey registration and authentication require an active session;
  passkeys cannot bootstrap an account from scratch (account creation
  is always email-first).

**Adding functionality here:**
- New manage-page surfaces should be POST + HTMX with `outerHTML`
  swap, matching the rest of the page.
- Any new auth path must be guarded by `require_htmx` if it returns a
  fragment, and must respect the IP-keyed rate limit on email-based
  flows.

### J4 — Act on a bulletin email (subscriber, token)

> "I got the morning email. I'll either click through to read today's
> bulletin, or I'll one-click unsubscribe."

This journey deliberately runs **without a session**. The signed token
in the email is the entire authentication mechanism.

**Entry points:**
- A bulletin email in the subscriber's inbox.

**URL surface:**
- Click-through to a current bulletin — re-enters J1 from the URL on.
- `GET /subscribe/unsubscribe/<token>/` — confirm page.
- `POST /subscribe/unsubscribe/<token>/` — perform the unsubscribe.
- `/subscribe/unsubscribe-done/` — confirmation page.

**Key invariants:**
- Unsubscribe tokens have **no expiry**. A subscriber must be able to
  opt out from any historical email, no matter how old.
- The token encodes `{email}|{region_id}` so the action is unambiguous
  even when the recipient holds many subscriptions.
- One-click unsubscribe never requires sign-in. Friction here is a
  legal compliance risk, not a UX choice.

**Adding functionality here:**
- New email types must include the per-region unsubscribe partial.
  Skipping it is a CAN-SPAM-class regression even if technically the
  user can manage subscriptions elsewhere.
- Any new token surface goes through `subscriptions/services/token.py`
  and uses a fresh salt — never overload `SALT_UNSUBSCRIBE`.

### J5 — Read historically or learn (either persona)

> "What was the danger on this date?" / "What does a level-3 wind-slab
> problem actually mean?"

Educational and longitudinal reading. Important for trust: visitors
who understand how the bulletin works are more likely to subscribe;
subscribers reviewing past conditions are doing the thing the product
is for.

**Entry points:**
- Calendar tile on a bulletin page, linking to a historical date.
- Footer or in-page link to the bulletin reading guide.
- Examples links, often from the homepage or guide.

**URL surface:**
- `/<region_id>/<slug>/<YYYY-MM-DD>/` — historical bulletin. 302s to
  canonical form on URL mismatches.
- `/how-to-read-a-bulletin/` — static reference guide.
- `/examples/random/` — a random recent bulletin, rendered inline.
- `/examples/category/<danger_level>/` — a random bulletin matching
  a danger level.

**Key invariants:**
- Historical bulletins render exactly as they were stored — never
  back-fill them with present-day weather or render-model changes.
- The weather header on historical pages projects the time-of-day
  (not the absolute instant) onto the page date — so a historical
  page rendered in the evening shows what the evening looked like
  back then.
- The guide and examples must work for anonymous visitors with no
  prior knowledge — no jargon without an inline definition or link.

**Adding functionality here:**
- Don't add features that require live weather or a fresh ingest to
  render — historical bulletins must remain self-contained.
- New educational content goes in `/how-to-read-a-bulletin/`, not
  scattered across bulletin pages. One canonical place to learn the
  thing.

### J6 — Compliance / about (either persona)

> "Who is behind this?" / "What's the data licence?" / "How do I see
> the privacy policy?"

Low-traffic but load-bearing. Every footer points here, every legal
question routes here.

**Entry points:**
- Footer links on every page.
- Direct links from emails (privacy, terms of service).

**URL surface:**
- `/terms/` — SLF data-licence acknowledgement + Snowdesk liability
  disclaimer.
- `/privacy/` — privacy policy.
- `/terms-of-service/` — terms of service.
- `/colophon/` — technology credits and attribution.

**Key invariants:**
- Every page reachable from the public site links to terms and
  privacy in its footer. No exceptions; this is non-negotiable for
  the data licence and for GDPR-class compliance.
- These pages render without any external dependency — they must work
  even when ingestion is broken.

**Adding functionality here:**
- Any change that touches data collection, retention, or sharing
  needs a corresponding edit to `/privacy/` and possibly `/terms/`.
- Don't add tracking or analytics to a page without first updating
  `/privacy/`.

### J7 — PWA / offline (cross-cutting)

> "I'm on a chairlift with no signal and I want to re-check the
> bulletin I read this morning."

Cuts across J1 and J4 rather than standing alone, but worth naming so
that "does this work offline?" is a question someone always asks when
adding a new public page.

**Entry points:**
- Install prompt on the homepage / bulletin page.
- Launch from home screen on iOS/Android.

**URL surface:**
- `/manifest.webmanifest`
- `/sw.js`
- The cached shell plus the last-fetched bulletins served from cache.

**Key invariants:**
- The service worker caches the shell + the most recently viewed
  bulletins. A user who loaded a bulletin online must be able to
  reopen the PWA offline and see it.
- The service worker is registered before the generic
  `<str:region_id>/` route so it's never shadowed by a region lookup.
- Cache strategy is documented in [`offline-map.md`](offline-map.md);
  don't deviate without updating it.

**Adding functionality here:**
- New pages on the offline shell must work without network. If a page
  cannot, don't add it to the shell.
- Any change to `/sw.js` invalidates existing installs — bump the
  cache version and test the upgrade path.

---

## Using this document

When adding new functionality, work through:

1. **Which persona is this for?** If you can't pick one cleanly,
   you're probably building two features.
2. **Which journey does it extend?** If none, name it. Add it here as
   J8 before writing code, not after.
3. **Which invariants does it touch?** Specifically list them in the
   PR description so the reviewer can check them.
4. **Will it work offline / in the PWA shell?** If not, justify it.
5. **Does it affect compliance pages?** Update `/privacy/` and
   `/terms/` in the same PR, not a follow-up.

This document is the contract. The QA and security-audit agents read
it before reviewing PRs that touch user-facing surfaces.
