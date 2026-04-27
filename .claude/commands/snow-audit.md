# /snow-audit — Run a security audit scoped to Snowdesk

Invokes the `security-auditor` agent with Snowdesk's specific threat surface
pre-loaded, so you don't have to describe the stack each time.

## Steps

1. Invoke the `security-auditor` subagent with the following context injected:

   **Snowdesk threat surface** (share with the auditor):
   - **SLF CAAML ingest** — `pipeline/services/` fetches from `aws.slf.ch`; check
     for SSRF, unvalidated redirects, and injection via bulletin content.
   - **Resend email** — `subscriptions/` sends magic-link and notification emails
     via the Resend HTTP API; check for header injection, open redirect in magic
     links, and rate-limit bypass.
   - **Anthropic API** — if any view proxies model calls, check for prompt
     injection via user-controlled input.
   - **HTMX partials** — all fragment endpoints must be guarded by `require_htmx`;
     check for missing guards and CSRF exposure.
   - **Subscription tokens** — signed tokens for magic links and unsubscribe flows;
     check for timing attacks, token reuse, and missing expiry enforcement.
   - **Django settings** — check `DEBUG`, `ALLOWED_HOSTS`, `SECRET_KEY` source,
     `SECURE_*` headers, and `SESSION_COOKIE_SECURE`.

2. The auditor writes its report to `.claude/security-audits/YYYY-MM-DD-HHMM.md`.

3. After the auditor completes, summarise:
   - Count of Critical / High / Medium / Low findings
   - Top 3 issues with one-line descriptions
   - Whether any of the [`## Invariants`](../CLAUDE.md) in CLAUDE.md are violated

4. Ask the user if they want to create Linear tickets for any Critical or High
   findings.

## Scope options

- `/snow-audit` — full audit of the current working tree
- `/snow-audit deps` — dependency CVE scan only (`pip-audit` + `npm audit`)
- `/snow-audit <path>` — limit the audit to a specific module or directory
