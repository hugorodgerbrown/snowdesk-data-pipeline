---
name: every-command-run-is-registered
description: Why every command inherits RegisteredCommand and writes a CommandRun row (dry runs too), not django-command-log
status: current
last-reviewed: 2026-10-22
---

# Every command run is registered

**Decision.** Every project management command inherits
`apps.core.command_register.RegisteredCommand` (SNOW-1189). Each run, dry or
`--commit`, writes one `CommandRun` row, opened before `handle()` and closed
after it, stamped with `settings.RELEASE_VERSION` (the git SHA) and
`settings.APP_RELEASE` (the `VERSION` ordinal). Commands declare what they
changed with `self.tally(name, n)`. A test fails any command under `apps/`
that does not inherit the class.

**Why.** Backfills, relinks and reloads are run by hand after a deploy, and
nothing recorded that they had been: whether a step ran on production, on
which build, and what it changed was memory. `Deploy-Step:` trailers say
what should run; the register says what did.

django-command-log (YunoJuno, MIT) was the obvious base and was read before
deciding against it:

- its transactional variant writes the log row INSIDE the atomic block, so a
  run without `--commit` rolls back its own row — dry runs vanish, and
  comparing a dry run's counts with the commit run's is half the point;
- it catches every exception and returns, so a failed command exits 0,
  breaking management-command rule 4 that cron and CI rely on;
- it records no arguments, operator, host or build, and its "output" is
  `str()` of the return value rather than what was printed;
- it owns the transaction and the `--commit` flag, which our commands
  already own, one bulletin or batch at a time.

A global hook on `BaseCommand.execute` was rejected too: it is a
monkeypatch, it records Django's `shell` and `runserver` as noise, and it
still needs per-command code for counts. A base class is explicit, and the
guard makes it as complete as a hook.

**Consequences.**

- A dry run now writes one row. That is metadata about the run, not the
  data the command reads, so rule 2 ("never alters data by default") holds.
- The row is written on the default connection outside the command's own
  transactions. A command run inside an OUTER transaction (a test, an admin
  action) shares that transaction's fate.
- Writing the row is best-effort: a failure is logged and the command runs
  unrecorded, so the register can never stop or change a command.
- The CalVer tag is not stored — `release.yml` creates it after the deploy
  starts — and Render's deploy id is not in the environment. SHA plus
  release number identifies the build; GitHub maps the SHA to a tag.
- The register is readable in the admin and over MCP, so it must never
  hold a credential: arguments named like a secret are redacted, and a
  command whose OUTPUT is a credential (`mint_vapid_keypair`,
  `dev_magic_link`) sets `capture_output = False`.
  The admin allows no add, edit or delete.
- Who ran it relies on `SNOWDESK_OPERATOR`, because a Render shell runs
  every operator as one OS user.
- Scheduled runs (48 a day of `fetch_bulletins`) are deleted after 90 days by
  `prune_command_runs`; everything else is kept for good.
