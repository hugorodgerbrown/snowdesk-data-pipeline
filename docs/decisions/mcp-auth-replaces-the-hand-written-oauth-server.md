---
name: mcp-auth-replaces-the-hand-written-oauth-server
description: Why MCP OAuth is mcp-auth on django-oauth-toolkit, not hand-written — MCPAuthContract, verified_account, /api/ops/mcp/
status: current
last-reviewed: 2026-10-22
---

# mcp-auth replaces the hand-written OAuth server

**Decision.** The authorization server for `/api/mcp/` and `/api/ops/mcp/`
is [mcp-auth](https://github.com/hugorodgerbrown/mcp-auth), the
django-oauth-toolkit preset every Titan project shares (SNOW-1193). The
hand-written server of SNOW-1035 — four models, the token, redirect, PKCE
and CIMD services, the views and `mint_mcp_token` — was deleted, and its
tables dropped. Snowdesk keeps what is genuinely its own: the connect rule
(`apps.oauth.policy.verified_account`), the verify-first page, the consent
and returning templates in its look, the connected-apps list on the settings
page, and the purge
command under the command contract. Unlike other Titan projects, which hash
and discard an address once it is verified, Snowdesk keeps the account's
email — it is the username and the address bulletins go to.

**Why.** When SNOW-1035 was written no library covered Claude's slice of
OAuth (CIMD, the `resource` echo, loopback ports), so writing it by hand was
the smaller job. mcp-auth now covers exactly that slice on a maintained
toolkit, hardened once for every project, with Claude's connector
requirements written as a contract test (`MCPAuthContract`) that each
project runs against its own URLs. A fix to the flow is made once and
reaches Snowdesk with a version bump instead of being re-derived here, and
a settings or URL change that would break a connector fails Snowdesk's own
build. The ops server (SNOW-1191) needed a second protected resource, which
is configuration under mcp-auth and was new code under the hand-written one.

**Consequences.**

- Every connection made before the change reconnects once.
- Audience is checked by URL prefix, so a second endpoint must be a sibling
  path, never nested beneath `/api/mcp/`.
- The site CSP's `form-action 'self'` would block the consent page's 302;
  mcp-auth gained `CONSENT_RETURN = "page"` (a meta-refresh page) for that,
  rather than a per-response hook in django-csp-plus.
- A change to the flow is an mcp-auth change and a pin bump, not an edit in
  `apps/oauth/`.
