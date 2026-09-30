---
name: cloud-sessions-hold-no-provider-credentials
description: Cloud sessions never hold a provider API key (METEOFRANCE_API_KEY, WHAT3WORDS_API_KEY, MAXMIND_LICENSE_KEY); tests and seeds need none
status: current
last-reviewed: 2026-09-30
---

# Cloud sessions hold no provider credentials

**Decision.** A Claude Code on the web session is never given a credential
for an authenticated external API. The throwaway `.env` that
`bin/setup-remote-env` seeds carries a random `SECRET_KEY` and nothing
else, and no key is added to the environment's settings to make up for
it. The corollary is a constraint on the codebase: **running the test
suite and developing on the code must never require such a key.** A test
or a seed that needs one is a defect in the test, not a missing secret.

The keys this covers, all read through python-decouple with an empty
default: `METEOFRANCE_API_KEY` (the DPBRA bulletin API),
`WHAT3WORDS_API_KEY`, `MAXMIND_LICENSE_KEY` (the GeoLite2 download in
`bin/fetch-geoip-data`), and `OPEN_METEO_API_KEY` (the paid tier; the free
tier the code uses by default is unauthenticated and stays available).
`POSTHOG_API_KEY` is a client-side project key rather than a secret, and
every test env blanks it anyway.

**Why.** Decided 2026-09-30, when a stress test of a cloud session found
that a dry-run `fetch_bulletins --source meteofrance` fails for every
massif without the key, and the obvious fix was to add the key to the
environment. It was declined on two grounds. An agent session is not a
place a production credential should live: the container is ephemeral,
shared with whatever the session fetches, and holds a key only so that
something no one asked for can call a paid, rate-limited API on the
account's behalf. And nothing a cloud session is for needs it — the suite
already passes on a keyless `.env`, the seeds build from the committed
`local_mirrors/` archives and the golden week, `seed_test_data` invents
its what3words addresses offline, and a live ingest is what the
production scheduler on Render does with the key it holds in the
`Production` env-var group.

**Consequences.** A cloud session can run every tox env, the dev server,
both seed commands and an SLF or ALBINA dry run (those providers are
unauthenticated). It cannot run a live Météo-France, what3words or
MaxMind call, and that is the intended shape rather than a gap to close:
the Météo-France path in a session is `--local-mirror`
([meteofrance-live-ingest.md](../meteofrance-live-ingest.md)). Anyone
adding a dependency on a new keyed service owes the same offline path —
a fixture, a mirror, or a fake — before a test may touch it. The network
allowlist ([environment-network-allowlist.md](../environment-network-allowlist.md))
can still open the provider's host for a probe; the policy opens the
door, this decision keeps the key out of the room.
