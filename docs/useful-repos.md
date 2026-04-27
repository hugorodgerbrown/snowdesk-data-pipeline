# Useful repos

Public Python/Django libraries under [github.com/yunojuno](https://github.com/yunojuno) authored or maintained by Hugo / the YunoJuno team. Before implementing new functionality in Snowdesk, check if one of these already solves the problem — most are installable from PyPI under the same name.

> **Scope:** non-fork source repos only. Excludes third-party forks (`pytest-split`, `django-linear-migrations`, `django-vite`, etc.), Heroku buildpacks, archived repos (`django-amiqus`, `django-onfido` on some accounts), and non-Python repos (`docker-heroku`, `figma-tokens`).

---

## Auth, access & identity

### [django-request-token](https://github.com/yunojuno/django-request-token)
JWT-backed tokens that grant one-time or time-limited access to specific Django views. Decorator-based, tracks token use, and is unrelated to DRF auth. **Use for:** password reset links, email action confirmations, any "click this link to do X once" flow.

### [django-magic-link](https://github.com/yunojuno/django-magic-link)
Opinionated magic-link login flow, extracted from `django-request-token` specifically to handle the real-world problem of URL pre-fetchers (email scanners, browsers) burning single-use tokens. **Use for:** passwordless sign-in where you don't want Outlook to invalidate the link before the user clicks it.

### [django-visitor-pass](https://github.com/yunojuno/django-visitor-pass)
Temporary "visitor" users who can access a scoped set of views for a limited time without being real accounts. **Use for:** sharing a draft with an external reviewer, guest access to a dashboard, or any time a full signup is too much friction.

### [django-perimeter](https://github.com/yunojuno/django-perimeter)
Site-wide access control — a middleware that blocks all requests unless a shared password has been entered. **Use for:** gating a staging environment or a pre-launch site from the public without touching individual views.

### [django-account-locker](https://github.com/yunojuno/django-account-locker)
Tracks failed login attempts and locks accounts after a threshold is hit. **Use for:** basic brute-force protection on login endpoints without pulling in a heavier auth package.

### [django-simple-saml](https://github.com/yunojuno/django-simple-saml)
A small demo-style app showing how to wire SAML SSO into a Django project. **Use for:** reference when integrating with an enterprise IdP — it's more starting point than drop-in library.

---

## Email & messaging

### [django-appmail](https://github.com/yunojuno/django-appmail)
Stores transactional email templates in the database (name / language / version) with admin editing and optional logging of sent messages. Does not send mail itself — just renders. **Use for:** any time non-developers (support, marketing, ops) need to edit email copy without a deploy.

### [django-persistent-messages](https://github.com/yunojuno/django-persistent-messages)
Like `django.contrib.messages`, but messages persist across sessions, can be targeted at specific users, and stay until explicitly dismissed. **Use for:** onboarding nudges, "please verify your email" banners, or any in-app notification that shouldn't vanish on the next page load.

### [django-nps](https://github.com/yunojuno/django-nps)
Records NPS scores (0-10) per user, groups them into detractors / neutrals / promoters, and exposes a queryset method to compute the score. Ships a middleware to signal when a user should be shown the survey. **Use for:** measuring product satisfaction on a recurring cadence.

---

## Analytics, tracking & logging

### [django-user-visit](https://github.com/yunojuno/django-user-visit)
Middleware + `UserVisit` model that records one row per user / session / device / IP per day. Denormalises the user-agent into `device`, `os`, `browser`. **Use for:** lightweight DAU/MAU analytics directly in the database — far cheaper than logging every request.

### [django-utm-tracker](https://github.com/yunojuno/django-utm-tracker)
Pair of middlewares that extract `utm_source` / `utm_medium` / etc. from inbound querystrings and store them against the authenticated user or the session. **Use for:** marketing attribution — tying sign-ups back to the campaign that drove them.

### [django-request-log](https://github.com/yunojuno/django-request-log)
Persists `HttpRequest` metadata to the database. Intended as a building block used by other apps (e.g. `django-request-token` uses it for audit trails). **Use for:** when another YJ package asks for it, or when you need an append-only record of specific requests.

### [django-request-profiler](https://github.com/yunojuno/django-request-profiler)
Shallow request timing middleware — records total response time per URL, not a full stack profiler. Designed to help you *decide* where to optimise, not to do the optimisation. **Use for:** spotting which endpoints are slowing down in production without the overhead of `django-debug-toolbar` / `silk`.

### [django-management-command-log](https://github.com/yunojuno/django-management-command-log)
Wraps `BaseCommand` to log each run (start time, end time, exit status, captured stdout/stderr) to the database. **Use for:** cron-driven jobs where you want an audit trail of "did this actually run and did it succeed" accessible from the admin.

### [django-csv-downloads](https://github.com/yunojuno/django-csv-downloads)
Generates CSV downloads from Django querysets and records which user downloaded which CSV when. **Use for:** admin / back-office data exports where you need an audit log for compliance.

### [django-geoip2-extras](https://github.com/yunojuno/django-geoip2-extras)
Middleware that uses Django's built-in GeoIP2 integration (MaxMind Lite) to annotate every request with country / city data, cached for performance. **Use for:** geo-fencing, language defaults, or showing region-specific content — note you supply the `.mmdb` database yourself.

---

## Data, models & infrastructure

### [django-charid-field](https://github.com/yunojuno/django-charid-field)
Drop-in replacement for `AutoField` that generates string primary keys — CUID, KSUID, ULID, with optional per-model prefixes (`usr_...`, `ord_...`). **Use for:** user-facing IDs that don't leak row counts and sort/debug nicely (Stripe-style).

### [django-anonymise](https://github.com/yunojuno/django-anonymise)
Model mixin and utilities for anonymising PII in place — field-by-field redaction strategies on specific records or querysets. **Use for:** GDPR deletion requests, data retention cleanup, or producing anonymised dumps for staging.

### [elasticsearch-django](https://github.com/yunojuno/elasticsearch-django)
Declaratively maps Django models onto Elasticsearch indexes, syncs on save/delete, and ships management commands to bulk-build indexes. Based on `elasticsearch-py` 8. **Use for:** Django projects that need real search rather than `icontains` — configuration lives in settings, not spread across signals.

### [django-side-effects](https://github.com/yunojuno/django-side-effects)
Decorator-based registry of external side-effects (emails, Slack posts, analytics events) that fire *after* a function completes successfully, with documentation generated from docstrings. **Use for:** keeping `atomic()` blocks pure — don't send the welcome email until the transaction actually commits.

### [django-s3-upload](https://github.com/yunojuno/django-s3-upload)
Renders a widget that uploads files directly from browser to S3 with a progress bar, skipping the Django server entirely. **Use for:** any file upload bigger than a few MB, especially on Heroku where request timeouts kill large uploads.

### [django-stripe-lite](https://github.com/yunojuno/django-stripe-lite)
Thin Stripe wrapper that validates webhook signatures, persists every event to the database, and dispatches to handler functions you register via decorator. Leaves all business logic to your app. **Use for:** when `dj-stripe` is too much — you want Stripe plumbing without an opinionated billing model.

### [django-zapier-trigger](https://github.com/yunojuno/django-zapier-trigger)
DRF-based scaffolding for exposing your Django app as a Zapier trigger (polling or instant), including a reference Zapier CLI app. **Use for:** letting users pipe Snowdesk events (e.g. "new bulletin published") into their own Zaps.

### [django-onfido](https://github.com/yunojuno/django-onfido)
Django integration with the Onfido identity-verification API (v3), including webhook callback handling for async status updates. **Use for:** KYC / right-to-work checks if you're using Onfido specifically — not a general identity-verification abstraction.

---

## Ops, security & developer experience

### [django-csp-plus](https://github.com/yunojuno/django-csp-plus)
Endpoint and models for collecting CSP violation reports from browsers, with admin views to inspect them. **Use for:** rolling out Content-Security-Policy headers without being blind to what breaks — log first, enforce later.

### [django-project-checks](https://github.com/yunojuno/django-project-checks)
Management commands that output project metadata: git SHA, migration state, installed Django version, relevant env vars. **Use for:** health-check endpoints, deploy verification, or just `manage.py status` during incident triage.

### [django-heroku-auto-scale-rq](https://github.com/yunojuno/django-heroku-auto-scale-rq)
Autoscales RQ worker dynos on Heroku based on queue depth, via the Heroku platform API. **Use for:** Heroku + RQ setups where queue spikes justify paying for more dynos only when needed. Note: Snowdesk currently uses this stack context.

### [django-juno-testrunner](https://github.com/yunojuno/django-juno-testrunner)
Legacy custom test runner that prints failures inline (without stopping the run), writes a `test_failures.txt` file, shows ETA, and outputs a failed-tests list for easy re-runs. **Use for:** historical reference only — modern pytest-django with `--tb=short` and `--lf` covers most of this.

---

## Utilities & scaffolding

### [python-env-utils](https://github.com/yunojuno/python-env-utils)
Small pure-Python package wrapping `os.environ` with coercion helpers: `get_bool`, `get_int`, `get_list`, `get_dict`, `get_date`, and a generic `get_env(..., coerce=...)`. **Use for:** reading `.env` values with types, without pulling in all of `django-environ` or `pydantic-settings`.

### [poetry-template](https://github.com/yunojuno/poetry-template)
Cookiecutter-style template for a new redistributable Django app with Poetry, pytest, ruff/flake8, mypy, and YJ's conventions pre-wired. **Use for:** spinning up the *next* YJ-style open-source package — not for Snowdesk itself.
