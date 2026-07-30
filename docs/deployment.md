---
name: deployment
description: Path-to-live: main→staging and release→production split, Render topology, cut a release by fast-forwarding release to main, CalVer tags
status: current
last-reviewed: 2026-06-22
---

# Deployment / path-to-live

Snowdesk runs on [Render](https://render.com). Deploys are split across two
long-lived branches so changes reach a staging environment automatically
and production only on an explicit release.

```
feature/SNOW-xxx ──PR──▶ main ──────────────▶ Staging   (auto, 1 web dyno)
                          │
                          └──fast-forward──▶ release ───▶ Production (3 services)
                                                          + GitHub Release (CalVer tag)
```

| Branch    | Deploys to | When                                   | Services |
|-----------|------------|----------------------------------------|----------|
| `main`    | Staging    | every merge (the default branch)       | 1 web    |
| `release` | Production | when `release` fast-forwards to `main` | web + scheduler + task worker |

`main` is the GitHub default branch; feature PRs target it. `release`
behaves like a tag that moves with `main`: it advances **only** by a
fast-forward to the current `main` tip (no merge commit, no divergence), so
the production commit is byte-identical to the `main` commit already
verified on staging. The "Release branch" GitHub ruleset enforces this —
the tip's required checks must already be green (reused from `main`), and
force-pushes and deletion are blocked, so `release` can only ever move
forward.

The ruleset requires **only checks that report on a `main`-tip commit via a
`push` trigger** (`Run tests`, `Static analysis (*)`, `Playwright smoke
tests`, `Security audit`, and the rest). A `pull_request`-only check —
notably `Audit public pages` (Lighthouse, see
[`lighthouse.yml`](../.github/workflows/lighthouse.yml)) — is deliberately
**excluded** from this ruleset: the fast-forward is a direct push with no PR,
so that check never reports on the `release` ref and would leave the gate
permanently at "Expected", forcing a bypass on every release. It stays
required on the "Main branch rules" ruleset, where PRs into `main` produce
it, so coverage is unchanged. Do not add PR-only checks to the Release-branch
ruleset.

## Why a branch split

Production is three services — the website
(`snowdesk-website`), the APScheduler worker (`snowdesk-scheduler`), and
the django-tasks-db worker (`snowdesk-background-tasks`). All three share one
Postgres database, so they must run the **same commit**. Pinning all three
to the `release` branch (`autoDeployTrigger: checksPass`) guarantees that:
one fast-forward redeploys all three from the same ref, once CI has gone
green. Because the advance is a fast-forward, that ref is a commit that was
already on `main` and green on staging. The topology is the source of truth in
[`render.yaml`](../render.yaml) — Blueprint auto-sync is enabled and reads
that file from `main`, so any change to services, plans, domains, or deploy
branches lands via a PR. Databases and env-group contents remain
dashboard-managed (no `databases:` block; env vars grouped via
`fromGroup`).

## Separate databases (important)

Staging and production use **separate Postgres databases**. This is not
optional: [`build.sh`](../bin/build.sh) runs `migrate` + `loaddata` on every
deploy, so a staging deploy applies migrations to whatever database the
staging service is wired to. If staging pointed at the production database,
every merge to `main` would mutate the production schema. Staging therefore
has its own `DATABASE_URL`, `SECRET_KEY`, `ALLOWED_HOSTS`, and email target
(its own env group in Render).

Staging has **no scheduler and no task worker**, so its database does not
ingest bulletins on its own — seed it with a manual `fetch_bulletins` run
when test data is needed. Because there is no `db_worker` to consume the
django-tasks-db queue, staging runs `config.settings.staging`
(`DJANGO_SETTINGS_MODULE` pinned in [`render.yaml`](../render.yaml)), which
inherits production's hardening but overrides the task backend to
`ImmediateBackend` so subscription email is sent **inline on the request**.
Under production's `DatabaseBackend`, staging would enqueue email that no
worker ever sends — persisted silently, with no error in the logs.

## `SITE_BASE_URL` is checked at deploy time

Every service must set `SITE_BASE_URL` to its own public origin. It has a
`http://localhost:8000` default in [`base.py`](../config/settings/base.py) so
local development needs no `.env` entry, and that default is silently wrong
everywhere else — absolute URLs still render, they just point at a machine the
visitor doesn't have: `og:image` / `twitter:image` on every page, the
`Sitemap:` line in `robots.txt`, the links in `llms.txt`, and the `id` /
`start_url` / `scope` fields in the PWA manifest. None of it shows up in logs
or in a browser.

`apps.core.checks.check_site_base_url` (SNOW-554) raises an `Error` when `DEBUG` is
off and the value still resolves to `localhost` / `127.0.0.1` / `::1`, or isn't
an absolute URL at all. `build.sh` runs `migrate`, which runs system checks
first, so a service missing the variable fails its deploy instead of shipping
the broken configuration.

The check is host-shape-only — it never asks whether the origin is the *right*
domain, because staging and production legitimately differ.
`config/settings/perf.py` is the one environment that runs `DEBUG=False`
against localhost on purpose (Lighthouse), and silences the check by id.

## Renaming or moving an authentication backend logs everyone out

Django stores the **dotted path** of the backend that authenticated a session
in the session itself, under `_auth_user_backend`. On every subsequent request
`django.contrib.auth.get_user()` checks that stored path against
`settings.AUTHENTICATION_BACKENDS` and returns `AnonymousUser` when it is not
found — so moving or renaming a backend module silently invalidates **every
live session** on deploy, even though nothing about the user rows changed.

This bit the `apps/` restructure (SNOW-557), which changed
`accounts.backends.TokenBackend` to `apps.accounts.backends.TokenBackend`.
Sessions are DB-backed (`SESSION_ENGINE = django.contrib.sessions.backends.db`),
so the stale rows sit in `django_session` until they expire.

There is nothing to fix — signed-in users simply request a new magic link, and
passkey holders re-authenticate in one tap. But treat any deploy that moves
`AUTHENTICATION_BACKENDS` entries as a **forced re-login event**: announce it if
the timing matters, and don't ship it alongside a change whose rollout you want
to measure by session continuity.

## Cutting a release

1. Confirm `main` is green and what is on `main` has been verified on
   staging.
2. Fast-forward `release` to `main`. Use the helper, which lists the
   `SNOW-xx` tickets that are on `main` but not yet on `release` and then
   advances the ref:

   ```bash
   bin/cut-release            # dry run — prints the target SHA and ticket list
   bin/cut-release --commit   # fast-forwards origin/release to origin/main
   ```

   The push carries the exact `main` commit, so no new CI run is needed: the
   ruleset reuses that commit's already-green checks, and the push is
   rejected if they are not green. There is no release PR.
3. Render redeploys the three production services from the new `release`
   tip.
4. The [`release.yml`](../.github/workflows/release.yml) workflow fires on
   the push to `release`, tags the commit, and creates a GitHub Release.

### One-time migration off the PR flow

The fast-forward only works when `release` is an ancestor of `main`. The
old PR-based flow left merge commits on `release` that are not on `main`, so
the first switch needs a one-time reset of `release` to the `main` tip. This
is a non-fast-forward update, which the ruleset blocks for everyone except a
bypass actor (a repo admin), so it must be done deliberately by an admin:

```bash
git fetch origin
git push origin origin/main:refs/heads/release --force-with-lease
```

This advances production to the current `main` tip (a normal production
deploy of whatever is on `main` but not yet released), and from then on
every `bin/cut-release --commit` is a clean fast-forward. Run it as, or in
place of, the next release.

## Versioning and Releases

Each production deploy is tagged **CalVer**: `YYYY.MM.DD`, with a `.N`
suffix when more than one release ships in a day (`2026.06.22`,
`2026.06.22.2`, …). The tag and the GitHub Release are created
automatically by `release.yml`; the release notes are auto-generated from
the merged PRs since the previous release. Because PR titles carry the
`SNOW-xx:` prefix, the GitHub Release is the record of which tickets
reached production. Note categorisation/exclusions live in
[`.github/release.yml`](../.github/release.yml).

This is distinct from Linear status: a ticket goes **Done** in Linear when
its PR merges to `main` (work complete, on staging). The GitHub Release is
the separate "shipped to production" record — see
[`linear-workflow.md`](linear-workflow.md).

## One-time setup (Render dashboard + GitHub)

Order matters — switch production onto `release` **before** the next merge
to `main`, or that merge deploys to production.

1. Create the `release` branch at the current `main` HEAD and push it, so
   `release` equals what production already runs (first prod deploy is a
   no-op):
   ```bash
   git branch release origin/main
   git push origin release
   ```
2. Render dashboard: change each production service's auto-deploy branch
   from `main` to `release` (`snowdesk-website`, `snowdesk-scheduler`,
   `snowdesk-background-tasks`).
3. Render dashboard: create the staging web service tracking `main`, wired
   to its own Postgres and env group.
4. GitHub: protect `release` with the "Release branch" ruleset — require
   status checks, block force-pushes (`non_fast_forward`) and deletion, and
   do **not** require a pull request (a fast-forward advance is a direct
   push, not a PR merge). Require only checks that run on a `push` to
   `main` (so they have a result on the fast-forwarded commit); leave
   `pull_request`-only checks such as `Audit public pages` off this ruleset
   — see "Why a branch split" above.
