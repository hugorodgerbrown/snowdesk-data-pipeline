---
name: deployment
description: Path-to-live — main→staging and release→production branch split, Render topology, cutting a release PR, GitHub Releases, CalVer tags
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
                          └──release PR──▶ release ─────▶ Production (3 services)
                                                          + GitHub Release (CalVer tag)
```

| Branch    | Deploys to | When                                   | Services |
|-----------|------------|----------------------------------------|----------|
| `main`    | Staging    | every merge (the default branch)       | 1 web    |
| `release` | Production | when a release PR merges onto it       | web + scheduler + task worker |

`main` is the GitHub default branch; feature PRs target it. The `release`
branch moves **only** via a release PR (`main` → `release`) and is
branch-protected against direct pushes.

## Why a branch split

Production is three services — the website
(`snowdesk-website`), the APScheduler worker (`snowdesk-scheduler`), and
the django-tasks-db worker (`snowdesk-background-tasks`). All three share one
Postgres database, so they must run the **same commit**. Pinning all three
to the `release` branch (`autoDeployTrigger: checksPass`) guarantees that:
one branch advance redeploys all three from the same ref, once CI has gone
green. The topology is the source of truth in
[`render.yaml`](../render.yaml) — Blueprint auto-sync is enabled and reads
that file from `main`, so any change to services, plans, domains, or deploy
branches lands via a PR. Databases and env-group contents remain
dashboard-managed (no `databases:` block; env vars grouped via
`fromGroup`).

## Separate databases (important)

Staging and production use **separate Postgres databases**. This is not
optional: [`build.sh`](../build.sh) runs `migrate` + `loaddata` on every
deploy, so a staging deploy applies migrations to whatever database the
staging service is wired to. If staging pointed at the production database,
every merge to `main` would mutate the production schema. Staging therefore
has its own `DATABASE_URL`, `SECRET_KEY`, `ALLOWED_HOSTS`, and email target
(its own env group in Render).

Staging has **no scheduler and no task worker**, so its database does not
ingest bulletins or send queued email on its own. Seed it with a manual
`fetch_bulletins` run when test data is needed.

## Cutting a release

1. Confirm `main` is green and what is on `main` has been verified on
   staging.
2. Open the release PR. Use the helper, which lists the `SNOW-xx` tickets
   that are on `main` but not yet on `release`:

   ```bash
   bin/cut-release            # dry run — prints the title and ticket list
   bin/cut-release --commit   # opens the PR (main → release) via gh
   ```

   The release PR gets full CI/e2e/Lighthouse/lint-guards/security checks
   like any other PR (those workflows run on every PR).
3. Merge the release PR. Render redeploys the three production services
   from the new `release` tip.
4. The [`release.yml`](../.github/workflows/release.yml) workflow fires on
   the push to `release`, tags the commit, and creates a GitHub Release.

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
4. GitHub: branch-protect `release` (require a PR; require status checks).
