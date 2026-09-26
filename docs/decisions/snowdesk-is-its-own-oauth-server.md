---
name: snowdesk-is-its-own-oauth-server
description: Why the MCP endpoint is gated by a hand-written OAuth 2.1 server in apps/oauth — no library, no anonymous tier, PKCE
status: current
last-reviewed: 2026-09-26
---

# Snowdesk is its own OAuth server, written by hand

**Decision.** `POST /api/mcp/` requires an OAuth access token issued by
Snowdesk itself (SNOW-1035). The authorization server is hand-written on
Django primitives in `apps/oauth/` — no `django-oauth-toolkit`, no
`authlib`. There is no anonymous access. Clients are public only (PKCE
S256, `token_endpoint_auth_method: none`), identified by a Client ID
Metadata Document or registered dynamically. Access tokens live an hour;
refresh tokens thirty days and rotate on use. A user disconnects a client
from `/account/settings/`.

**Why.** The account is the thing being delegated, and it already lives
here: sign-in, passkeys, verification and the settings page all exist, so
an external identity provider would add a second account system to link
rather than remove work. The MCP spec and Claude's connector requirements
need a narrow slice of OAuth — authorization code + PKCE, refresh, RFC 8414
and 9728 metadata, CIMD, DCR, RFC 8707 audience — and several of the
requirements are specific (the `resource` must echo the URL the user typed,
path included; CIMD only when two metadata fields agree; loopback redirects
on any port). The libraries cover far more than that slice, cover CIMD not
at all, and would need configuring around exactly the details Claude checks.
Four models and a few hundred lines of services keep every rule visible and
tested. Anonymous access was dropped because a per-IP limit is the only
control it allows, and it put no account behind any call.

**Consequences.** Security fixes to the flow are ours to make; the rules
live in `apps/oauth/services/tokens.py` and `redirects.py`, and
`docs/oauth.md` lists what each Claude requirement maps to. Only hashes of
codes and tokens are stored. Adding a confidential client, a new grant type
or a new scope is a code change, not configuration. The CIMD fetch is a
server-side request to a requester-chosen URL and carries its own SSRF
guard (`apps/oauth/services/cimd.py`), with DNS rebinding a documented
residual risk. Any MCP client that relied on anonymous access stopped
working at deploy.
