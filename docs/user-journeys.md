---
name: user-journeys
description: Personas (anonymous visitor, signed-in user) and core journeys J1–J7 — two documents and a map — URL surfaces and invariants
status: current
last-reviewed: 2026-09-03
---

# User personas and core journeys

This document is the canonical reference for *who* uses Snowdesk and *what
they're trying to do*. Use it as a checklist before adding new pages,
endpoints, emails, or CTAs: any new functionality should map cleanly onto
an existing journey, or — if it doesn't — be a deliberate, named addition
to this list.

It deliberately does not describe implementation. For the URL map, see
the `urls.py` modules; for ingestion, see [`management-commands.md`](management-commands.md);
for the subscriber flow internals, see [`accounts.md`](accounts.md).

---

## Personas

There are two real personas. Staff is listed for completeness but isn't
a product journey.

### 1. Anonymous visitor

Default state for almost every entry to the site. No `Account` row,
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

### 2. Signed-in user

Has an `Account` row (created the moment they register or request an
account-access link; `is_verified` flips to `True` once they click it).
Owns saved things that live on the map — pins, saved resorts, region pins,
routes, field reports — every one of them a `Favourite`, `Route` or
`FieldObservation` row shown in a map sheet (SNOW-795,
[`two-documents-and-a-map`](decisions/two-documents-and-a-map.md)). Acts in
two modes, and a single feature often has to work in both:

- **Session-authenticated** — has clicked an account-access link or
  signed in with a password or passkey in this browser. The map's sheets
  are the management surface: pin and unpin, rename and remove, upload a
  route, file a report. `/account/settings/` — the one account page —
  holds email, passkeys, telemetry and account deletion.
- **Token-authenticated** — arrived via a signed token in an email, with
  no active session. The surface is intentionally narrow: confirm the
  account, or unpin one region from a historical unsubscribe link. Tokens
  encode the account identity, so we can act on their behalf without a
  login round-trip.

Defining traits:
- Identified by email. Email is the lookup key and the lowercased,
  normalised primary identifier.
- Cares about specific places, not the country as a whole. The map is the
  daily-driver surface; the two documents — the bulletin and the weather
  page — are what the map's pins open onto.

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
- Homepage `/` — the interactive map itself, for both first-time visitors
  (who meet the dismissable `#home-intro` overlay) and returning visitors
  (who land straight on the map).
- Deep link to a specific region (`/<region_id>/<slug>/`) from search
  engines, shared messages, or the PWA shortcut.

**URL surface:**
- `/` — interactive region-choropleth map, with a dismissable landing
  overlay on first visit (SNOW-314). There is no separate marketing page.
- `/map/` — permanent 301 redirect to `/`, kept for old bookmarks
  (SNOW-344).
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
- The bulletin renders in full: every field the provider published
  reaches a surface, and the source link is there to check it against.
  Adding to the page is fine; leaving provider content out is not
  ([`decisions/bulletin-fidelity-over-simplification.md`](decisions/bulletin-fidelity-over-simplification.md)).

**Adding functionality here:**
- Any new CTA on the bulletin page must not push the danger level,
  problems, or aspects below the fold on mobile.
- Anything new on the map must keep the page usable when JS is delayed
  or offline (PWA cached state).
- Don't introduce a route that would shadow the generic
  `<str:region_id>/` pattern without registering it before
  `apps.public.urls` is included.

### J2 — Sign up and pin a region (anonymous → signed-in)

> "Keep this region where I can find it."

The conversion journey. An anonymous visitor taps the pin control in the
map's region panel, is offered sign-in, registers or requests an
account-access link, and comes back to a map that remembers the region.

**Entry points:**
- The pin control in the region + date panel and the region popup
  (`favourites/partials/_region_pin_button.html`) — a sign-in path for a
  visitor, a toggle for a signed-in user.
- `/account/register/` and `/account/sign-in/`.

**URL surface:**
- `POST /account/register/` — creates the `Account`, sends a verification
  link.
- Email — account-access or verification link.
- `GET /account/access/<token>/` — renders a confirm page with a POST
  button; no state change and no sign-in on the GET (SNOW-439).
- `POST /account/access/<token>/` — verifies the `Account`
  (`is_verified → True`, stamps `verified_at`), signs the account in and
  lands on `/?panel=favourites` — the map with the pins sheet open.
- `POST /favourites/partials/region/<region_id>/toggle/` — pins or unpins
  the region (SNOW-802); answers `{"pinned": bool}` (SNOW-814 — the control
  is a roundel rendered once in the ribbon header, so what the caller needs
  back is the fact, not a rendered control).
- `GET /favourites/partials/regions/` — the user's pinned regions, listed
  in the region + date panel (SNOW-814).

**Key invariants:**
- Email normalisation happens at every entry point: `email.lower()`
  before storage and before lookup.
- Email sends are async (no blocking the request cycle from a web view).
- Token verification is salt-scoped: account-access tokens cannot be
  replayed as unsubscribe tokens, and vice versa.
- A region pin has no coordinate and never appears in
  `favourites.geojson`; it is a row in the region + date panel the map's
  readout chip opens (SNOW-814 — it was in the pins sheet until then, where
  none of that sheet's affordances applied to it), and nothing on the map.
- The favourites cap applies to region pins as to any other pin; the
  one-time backfill from `Subscription` rows bypassed it.

**Adding functionality here:**
- The pin control is one template rendered two ways (the panel's first
  render and the toggle's response). Keep it that way.
- Rate limiting on account endpoints is IP-keyed via `django-ratelimit`;
  new entry points should keep that pattern.

### J3 — Manage saved things (signed-in, session)

> "Rename that pin. Drop the region I don't ski any more. Register a
> passkey so I don't need email links."

**Entry points:**
- The map's roundels open its sheets: pins, routes, reports, downloads.
- `/account/sign-in/` — the dedicated sign-in page; a passkey present on
  the device is surfaced as a "Sign in with passkey" option.
- The nav avatar menu — the offline-mode switch, Settings, Sign out.

**URL surface:**
- `/?panel=favourites|routes|reports` — the map with a sheet open
  (SNOW-803); what every retired account list page redirects to.
- `/favourites/partials/…`, `/routes/partials/…`, `/partials/report/…`
  — the HTMX endpoints behind the sheets' rows and controls.
- `/account/settings/` — email, passkeys, telemetry, sync log, reset
  local data, sign out, delete account.
- `/account/manage/delete/` — hard-delete the account.
- `/account/manage/passkeys/<uuid>/delete/` — remove a passkey.
- `/account/webauthn/…` — WebAuthn challenge/response endpoints.
- `/account/sign-out/` — POST-only.

**Key invariants:**
- There is no account list page. `/account/`, `/account/favourites/`,
  `/account/observations/` and `/account/routes/` are permanent redirects
  into the map (SNOW-802/803); a sheet is the only rendering of a list.
- `delete_account` is the sole hard-delete path — it drops the `User`
  (cascading to `Account` and everything the user owns), available to any
  authenticated account. Unpinning the last region deletes only that
  `Favourite`; the `User`/`Account` survive and the session is untouched.
- Passkey registration and authentication require an active session;
  passkeys cannot bootstrap an account from scratch (account creation is
  always email-first).

**Adding functionality here:**
- New sheet controls are POST + HTMX (or a plain fetch that swaps the
  response in), matching the rest of the sheet; register any new overlay
  with `window.pwaMapOverlays`.
- Any new auth path must be guarded by `require_htmx` if it returns a
  fragment, and must respect the IP-keyed rate limit on email-based
  flows.

### J4 — Act on an email link (token)

> "I got an email. I'll click through, or I'll one-click unsubscribe."

This journey deliberately runs **without a session**. The signed token in
the email is the entire authentication mechanism. No scheduled job sends
a bulletin — every email is transactional — but the per-region
unsubscribe links in historical emails have no expiry, so the path stays.

**Entry points:**
- An email in the user's inbox.

**URL surface:**
- Click-through to a current bulletin — re-enters J1 from the URL on.
- `GET /account/unsubscribe/<token>/` — confirm page.
- `POST /account/unsubscribe/<token>/` — removes the region pin the
  subscription became (SNOW-802).
- `/account/unsubscribe-done/` — confirmation page.

**Key invariants:**
- Unsubscribe tokens have **no expiry**. A reader must be able to act on
  any historical email, no matter how old, and the action must still be
  the one the link promised — the region goes.
- The token encodes `{email}|{region_id}` so the action is unambiguous
  even when the recipient holds many pins.
- One-click unsubscribe never requires sign-in. Friction here is a
  legal compliance risk, not a UX choice.

**Adding functionality here:**
- Any new token surface goes through `apps/accounts/services/token.py`
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
  back-fill them with render-model changes. The
  same fidelity rule as J1 applies to them
  ([`decisions/bulletin-fidelity-over-simplification.md`](decisions/bulletin-fidelity-over-simplification.md)):
  a stored bulletin is shown whole, not summarised for age.
  (not the absolute instant) onto the page date — so a historical
  page rendered in the evening shows what the evening looked like
  back then.
- The guide and examples must work for anonymous visitors with no
  prior knowledge — no jargon without an inline definition or link.

**Adding functionality here:**
- Don't add features that require a fresh ingest to
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
