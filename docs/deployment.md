---
name: deployment
description: Path-to-live: main→staging, release→production, release PR via bin/cut-release, release-sync fast-forward, Render topology, CalVer tags
status: current
last-reviewed: 2026-09-02
---

# Deployment / path-to-live

Snowdesk runs on [Render](https://render.com). Deploys are split across two
long-lived branches so changes reach a staging environment automatically
and production only on an explicit release.

```
feature/SNOW-xxx ──PR──▶ main ──────────────▶ Staging   (auto, 1 web dyno)
                          │
 release PR (VERSION) ────┤
                          └──fast-forward──▶ release ───▶ Production (3 services)
                            (release-sync)                + GitHub Release (CalVer tag)
```

| Branch    | Deploys to | When                                   | Services |
|-----------|------------|----------------------------------------|----------|
| `main`    | Staging    | every merge (the default branch)       | 1 web    |
| `release` | Production | when the release PR merges to `main`   | web + scheduler + task worker |

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
ingest bulletins on its own. The `snowdesk-staging-data-sync` cron job
(SNOW-729) copies the provider-derived tables out of production nightly at
07:20 UTC instead — bulletins, region ratings and the curated resort
estate, and no user data whatsoever. Setup, the first full load, and skipped-row triage:
[`runbooks/refresh-staging-from-production.md`](runbooks/refresh-staging-from-production.md).
A manual `fetch_bulletins` run against staging still works, but is no longer
the way staging gets its data. Because there is no `db_worker` to consume the
django-tasks-db queue, staging runs `config.settings.staging`
(`DJANGO_SETTINGS_MODULE` pinned in [`render.yaml`](../render.yaml)), which
inherits production's hardening but overrides the task backend to
`ImmediateBackend` so subscription email is sent **inline on the request**.
Under production's `DatabaseBackend`, staging would enqueue email that no
worker ever sends — persisted silently, with no error in the logs.

## Open-Meteo: free tier by default, paid tier by env var

`OPEN_METEO_API_BASE_URL` and `OPEN_METEO_API_KEY` (SNOW-577) default to the
free public host, which needs no key. The free per-IP quota is 600/minute,
5,000/hour, 10,000/day.

Two callers, with very different profiles:

- **Elevation** (`apps.locations.services.elevation.fetch_elevation`)
  resolves a `Location`'s height once and stores it — a handful of calls
  when a favourite is created or a backfill runs, not a scheduled load.
- **Forecast** (`apps.weather.services.fetch`, SNOW-759) runs on a schedule:
  one call per active location, four times a day. **That is the number to
  size the plan against.** Today it is the resort estate; giving all 461
  micro-regions a centroid `Location` adds roughly 1,800 calls a day on top
  — see
  [`runbooks/region-centroid-backfill.md`](runbooks/region-centroid-backfill.md)
  and confirm headroom before running that backfill in production.

`OPEN_METEO_ARCHIVE_BASE_URL` is still not read: the archive endpoint went
with the old weather app and a historical backfill (SNOW-731) has not
landed.

Cutting over to a paid subscription is an env-group edit, not a deploy: set
the variables on the `Production` group (web, scheduler, and worker all read
it via `fromGroup`) and on `Staging`. The documented customer host is
`https://customer-api.open-meteo.com/v1`; confirm it against the subscription
confirmation rather than assuming the prefix. Set the key at the same time —
a customer host without a key fails auth on every call, and
`apps.core.checks.check_open_meteo_key_host_pairing` raises an `Error` at
deploy time for either half of that mistake.

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

## Health checks: `/livez` and `/healthz`

Two endpoints (SNOW-565, [`apps/core/views.py`](../apps/core/views.py)), because
the two questions they answer have different consequences when the answer is
"no". Both return a one-word `text/plain` body and are `no-store`.

| Path | Asserts | Consumer |
|------|---------|----------|
| `/livez` | The WSGI process is alive. No database, no session, no cache. | Render `healthCheckPath` |
| `/healthz` | `/livez` plus one read of `auth_user`. 503 when the database is unreachable. | External monitoring |

Render's `healthCheckPath` points at **`/livez`, not `/healthz`**. Render uses
that path to gate deploy promotion and instance health, so it has to answer
"should this instance be replaced?" — a database-coupled probe there turns a
transient Postgres blip into a blocked deploy or a restart of an instance that
was still serving. `/healthz` is the right check for a monitor, which alerts a
human instead of taking action.

`/healthz` reads a real table rather than issuing `SELECT 1`: a bare ping only
proves the socket answers, while reading `auth_user` also proves migrations have
run and the connected role can read the application schema. An empty table is
still healthy — the assertion is that the query completes.

Neither worker declares a health check. Render background workers cannot take an
HTTP probe; scheduler and `db_worker` liveness needs a heartbeat mechanism and is
not built.

Two settings exist only to keep the probes answerable, both derived from
`settings.HEALTH_CHECK_PATHS` so they cannot drift from the URLconf:

- `SECURE_REDIRECT_EXEMPT` ([`production.py`](../config/settings/production.py))
  — the prober reaches the instance behind the TLS-terminating proxy, so it
  sends no `X-Forwarded-Proto` and `SECURE_SSL_REDIRECT` would answer it with a
  301 that Render scores as a failure.
- `_POSTHOG_EXEMPT_PATHS` ([`base.py`](../config/settings/base.py)) — unlike
  every other entry in that set, this one is not about `Vary: Cookie`. It stops
  `PosthogContextMiddleware` reading `request.user`, whose session lookup would
  put a database query on every probe of the endpoint designed not to need one.

**Verify on staging before the first release.** `ALLOWED_HOSTS` is read from an
env var, so if Render's prober sends a `Host` that isn't listed, Django answers
400 and every later deploy fails its health check. Staging auto-deploys from
`main`, which gives a free gate: confirm the staging deploy goes healthy and
both paths answer 200, then merge the release PR.

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

Releasing is two commands and one click.

```bash
bin/cut-release            # dry run — prints the version, tickets and PR body
bin/cut-release --commit   # pushes release-vNN and opens the release PR
```

The script opens a PR against `main` carrying a single commit — the `VERSION`
bump — with the release note in the description: every `SNOW-xx` ticket on
`main` that production has not yet seen, each under its own commit subject.
Nothing has shipped at that point; read the list, confirm staging is healthy,
and merge when you want to ship.

**Merging the PR is the release.**
[`release-sync.yml`](../.github/workflows/release-sync.yml) sees `VERSION`
change on `main` and then:

1. waits for that commit's required checks — by retrying the ref update,
   since the "Release branch" ruleset rejects it until they are green;
2. fast-forwards `release` to the commit, which redeploys all three
   production services on Render;
3. dispatches [`release.yml`](../.github/workflows/release.yml), which tags
   the commit CalVer and creates the GitHub Release.

Staging gets only the `VERSION` diff (it already has everything else);
production gets the bump and every commit since the last release; and the two
refs end up identical.

**Squash or merge-commit both work.** `release` is always an ancestor of
`main`, so advancing it to `main`'s tip is a fast-forward whichever way the PR
lands. (Rebase merging is disabled on `main` by the ruleset.)

The script derives the next ordinal itself, so `VERSION` can no longer be
forgotten or mistyped — the bump and the release are now the same act. It
refuses to open a second release while `VERSION` differs between `main` and
`release`, which is the signature of a release PR that merged while the sync
had not finished.

**The bump commit names only one release number.** Render's deploy list
renders subject and body flattened onto a single line, so a message that also
described what the *previous* release shipped produced "Bump VERSION to 27 …
Release 26 shipped …" against the live deploy, and a reader could not tell
which was running. That happened on 2026-08-30. What the last release
contained belongs in the PR description and the GitHub Release, both of which
are read on their own.

### Why the sync dispatches rather than relying on a push

A push made with `GITHUB_TOKEN` does not trigger other workflows. If
`release-sync.yml` left `release.yml` to fire on its `push:` trigger,
production would deploy with no CalVer tag and no GitHub Release, and nothing
would report the omission. So it dispatches `release.yml` explicitly.
`release.yml` keeps its `push:` trigger for a human pushing `release` by hand;
only one of the two paths runs for any given release.

### Why the sync waits by retrying the ref update

The "Release branch" ruleset already names the contexts that gate a release.
Listing them again in the workflow would duplicate that list and rot the day
someone edits the ruleset, so the workflow retries the update and lets the
ruleset decide. Waiting on *all* check runs instead would deadlock on the sync
job's own check run, and would block on non-gating ones such as `Dependency
audit (dev + npm)`, which is detection-only.

The advance is a GitHub ref update rather than a `git push`: `actions/checkout`
leaves a shallow clone that git can refuse to push from, the remote already
holds every object (the commit is on `main`), and the API rejects a
non-fast-forward unless `force` is passed, which it is not.

### One-time reset after the old merge-into-release flow (historical)

This was done once, before `release` began fast-forwarding; it is recorded
here for the case where `release` ever diverges again.

The fast-forward only works when `release` is an ancestor of `main`. The
old flow merged into `release` directly, leaving merge commits on it that are
not on `main`, so
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

## Two version numbers, and why

The app carries two, for two different readers. Conflating them is the
mistake this section exists to prevent.

| | `APP_VERSION` | `APP_RELEASE` |
|---|---|---|
| Value | git SHA (`RENDER_GIT_COMMIT`) | release ordinal (`24`) |
| Source | Render's build environment | the tracked `VERSION` file |
| Changes | every deploy | every production release |
| Read by | the PWA update check, `APP_BLOCKED_VERSIONS`, ETags | people |
| Surfaced as | `X-App-Version`, `<meta name="pwa-app-version">` | "v24" in the account menu |

`APP_VERSION` has to change on every deploy and name one build exactly, or
the update check cannot tell a stale shell from a current one and
`APP_BLOCKED_VERSIONS` cannot name a build to retire. A SHA is right for
that and unreadable for anything else.

`APP_RELEASE` answers "which version are you on?". It is rendered by
[`apps/public/release.py`](../apps/public/release.py): `v24` on production,
`v24 · fce4f14` on every other tier, since staging deploys on every merge
to `main` and therefore sits between releases — there the build id is the
part that identifies what is actually running.

**Why a tracked file and not the CalVer tag.** Three things rule out
deriving it at build time:

* Render's build gets `RENDER_GIT_COMMIT` and no tag context.
* `release.yml` creates the CalVer tag *after* the push to `release`, which
  is the same event that starts the deploy — so at build time the tag for
  the release being deployed does not exist yet.
* A commit redeployed later (a Render rollback or a manual redeploy) would
  see a different set of tags than its first deploy did, so any count would
  disagree with itself.

A file in the tree has none of those problems: present, identical and
unambiguous at build time on every tier, and reviewable in the PR that
changes it. The cost is that it is a hand-maintained number, which is why
`bin/cut-release` derives the next ordinal and writes it into the release PR
rather than leaving it to be remembered — a forgotten bump has no other
symptom, since the deploy is green either way and only the menu is wrong.

## Versioning and Releases

Each production deploy is tagged **CalVer**: `YYYY.MM.DD`, with a `.N`
suffix when more than one release ships in a day (`2026.06.22`,
`2026.06.22.2`, …). The tag and the GitHub Release are created
automatically by `release.yml`; the release notes are auto-generated from
the merged PRs since the previous release. Because PR titles carry the
`SNOW-xx:` prefix, the GitHub Release is the record of which tickets
reached production. Note categorisation/exclusions live in
[`.github/release.yml`](../.github/release.yml).

The CalVer tag and `APP_RELEASE` are separate identities on purpose: the
tag is how a release is looked up in GitHub, the ordinal is how a user
names it. Nothing links them automatically today — if that matters, the
place to reconcile it is `release.yml`, which computes the tag and could
equally read the number the deploy shipped with.

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
