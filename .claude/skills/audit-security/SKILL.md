---
name: audit-security
description: |
  Run a security audit scoped to Snowdesk by invoking the security-auditor
  agent with the project's specific threat surface pre-loaded (SLF CAAML
  ingest, Resend email, subscription tokens, HTMX partials, Django
  settings) — no need to describe the stack each time. Use whenever the
  user asks for a security audit, vulnerability scan, CVE or dependency
  check, secrets scan, pentest, or pre-deploy security review of this
  project — "/audit-security", "audit the security of this project", "check
  for vulnerabilities", "run a security scan". A bare "audit the project" is
  ambiguous — ask which audit they mean rather than assuming this one.
  Accepts a scope argument:
  "deps" for a dependency CVE scan only, or a path to limit the audit to
  one module. Do NOT use for reviewing the pending changes on a single
  branch or diff — that is the `security-review` skill.
allowed-tools: Agent, Read, Bash
---

# Snowdesk security audit

Invokes the `security-auditor` agent with Snowdesk's specific threat
surface pre-loaded, so you don't have to describe the stack each time.

## Scope argument ($ARGUMENTS)

- *(empty)* — full audit of the current working tree.
- `deps` — dependency CVE scan only (`pip-audit` via `tox -e audit` +
  `npm audit`).
- `<path>` — limit the audit to a specific module or directory.

## Steps

1. Invoke the `security-auditor` subagent via the Agent tool, passing the
   scope from $ARGUMENTS and the following context injected:

   **Snowdesk threat surface** (share with the auditor):
   - **SLF CAAML ingest** — `bulletins/services/` fetches from
     `aws.slf.ch` (plus Météo-France and ALBINA sources); check for SSRF,
     unvalidated redirects, and injection via bulletin content.
   - **Resend email** — `accounts/` sends magic-link and notification
     emails via the Resend HTTP API; check for header injection, open
     redirect in magic links, and rate-limit bypass.
   - **Anthropic API** — if any view proxies model calls, check for prompt
     injection via user-controlled input.
   - **HTMX partials** — all fragment endpoints must be guarded by
     `require_htmx`; check for missing guards and CSRF exposure.
   - **Subscription tokens** — signed tokens for magic links and
     unsubscribe flows; check for timing attacks, token reuse, and missing
     expiry enforcement.
   - **Django settings** — check `DEBUG`, `ALLOWED_HOSTS`, `SECRET_KEY`
     source, `SECURE_*` headers, and `SESSION_COOKIE_SECURE` across the
     split settings layout (`config/settings/`).

2. The auditor writes its report to
   `.claude/security-audits/YYYY-MM-DD-HHMM.md`.

3. After the auditor completes, summarise:
   - Count of Critical / High / Medium / Low findings
   - Top 3 issues with one-line descriptions
   - Whether any of the `## Invariants` in [CLAUDE.md](../../CLAUDE.md)
     are violated

4. Ask the user if they want to create Linear tickets for any Critical or
   High findings. If yes, create them via the `ticket-authoring-guide`
   skill so the tickets follow the standard contract.
