---
name: oauth
description: OAuth 2.1 for /api/mcp/ — .well-known metadata, /oauth/authorize|token|register, OAuthGrant, CIMD guard, mint_mcp_token
status: current
last-reviewed: 2026-09-26
---

# OAuth for the MCP server

Since SNOW-1035 the MCP endpoint (`POST /api/mcp/`, [`docs/mcp-server.md`](mcp-server.md))
needs a Snowdesk account. Snowdesk is its own OAuth 2.1 authorization
server, written by hand on Django primitives in `apps/oauth/`, following
the MCP authorization spec (2025-11-25) and Claude's connector requirements
(claude.com/docs/connectors/building/authentication). Why hand-written, and
why no anonymous tier: [`docs/decisions/snowdesk-is-its-own-oauth-server.md`](decisions/snowdesk-is-its-own-oauth-server.md).

## The flow, as Claude runs it

1. Claude POSTs to the URL the user added (`/api/mcp/` or `/api/mcp`) with
   no token and gets `401` with
   `WWW-Authenticate: Bearer resource_metadata="<origin>/.well-known/oauth-protected-resource<path>", scope="mcp"`.
2. It fetches that protected-resource document. `resource` in it is the
   origin plus the path suffix exactly as requested, so it matches the URL
   the user typed — Claude requires that match byte for byte.
3. It fetches `/.well-known/oauth-authorization-server`. Because the
   document carries `client_id_metadata_document_supported: true` **and**
   `"none"` in `token_endpoint_auth_methods_supported`, Claude identifies
   itself by a Client ID Metadata Document URL. Otherwise it would fall back
   to Dynamic Client Registration at `/oauth/register/`; both work.
4. It opens `/oauth/authorize/?response_type=code&client_id=…&redirect_uri=…&code_challenge=…&code_challenge_method=S256&state=…&scope=mcp offline_access&resource=…`
   in the browser. A signed-out user goes to `/account/sign-in/?next=<that URL>`
   and comes back. An unverified account is asked to verify first. Otherwise
   the consent page names the app, the host the code will go to, and what
   access means (read-only avalanche data, as this user).
5. Allow issues a five-minute single-use code and sends the browser back to
   the redirect URI with `code` and `state`; Deny sends `error=access_denied`.
6. Claude exchanges the code at `/oauth/token/` (form-encoded, with the PKCE
   verifier) for a one-hour access token and — because it asked for
   `offline_access` — a thirty-day refresh token.
7. Every MCP call carries `Authorization: Bearer <access token>`. Near
   expiry Claude refreshes; the refresh token rotates each time.

## Endpoints

| URL | Name | Method | Purpose |
|-----|------|--------|---------|
| `/.well-known/oauth-protected-resource` | `oauth_protected_resource` | GET | RFC 9728 metadata for `<origin>/api/mcp/` |
| `/.well-known/oauth-protected-resource/<path>` | `oauth_protected_resource_suffix` | GET | Same, with `resource = <origin>/<path>` echoed exactly |
| `/.well-known/oauth-authorization-server` | `oauth_authorization_server` | GET | RFC 8414 metadata: endpoints, `S256`, `none`, `mcp offline_access`, CIMD supported |
| `/oauth/authorize/` | `oauth:authorize` | GET + POST | Consent page; the POST records Allow / Deny |
| `/oauth/token/` | `oauth:token` | POST | `authorization_code` and `refresh_token` grants (form-encoded); 60/min per IP |
| `/oauth/register/` | `oauth:register` | POST | Dynamic Client Registration (JSON), always a public client; 10/hour per IP |
| `/oauth/revoke/` | `oauth:revoke` | POST | RFC 7009; always 200 |
| `/oauth/grants/<uuid>/revoke/` | `oauth:grant_revoke` | POST | HTMX Disconnect from `/account/settings/`; owner-only |

The discovery documents are public and cacheable for an hour. The origin in
every URL comes from the request (`request.build_absolute_uri("/")`), not
`SITE_BASE_URL`, so a staging host or a tunnel serves its own consistent
set; `ALLOWED_HOSTS` bounds which hosts that can be, and
`SECURE_PROXY_SSL_HEADER` makes it https behind Render.

## Token model

`apps/oauth/models.py`:

- **`OAuthClient`** — `client_id` (random for DCR, the document URL for
  CIMD), `kind` (`DCR` / `CIMD` / `LOCAL`), `client_name`, `redirect_uris`,
  `metadata_fetched_at`. Public clients only; PKCE binds a code to the
  client that asked.
- **`OAuthGrant`** — one per (user, client): the "connected app" row
  `/account/settings/` lists. Approving again reactivates it. Revoking it
  revokes every token and unused code under it.
- **`AuthorizationCode`** — five minutes, single use, stores the redirect
  URI, PKCE challenge, resource and scope the user approved.
- **`OAuthToken`** — `ACCESS` (one hour) or `REFRESH` (thirty days), with
  `resource` as the audience and `replaced_by` on a rotated refresh token.

Codes and tokens are `sd_ac_` / `sd_at_` / `sd_rt_` + `secrets.token_urlsafe(32)`.
Only the SHA-256 hex digest is stored; the plaintext is returned once.

Rules in `apps/oauth/services/tokens.py`:

- **Exchange** checks the code exists, is unused, is unexpired, belongs to
  the presenting client, its grant is active, the `redirect_uri` equals the
  authorize request's, the PKCE verifier matches, and any `resource`
  re-sent matches. Failures are `invalid_grant` (`invalid_target` for the
  resource).
- **Refresh rotates.** Presenting a refresh token that was already rotated
  is reuse: the whole grant is revoked, and both the attacker and the
  legitimate client must reconnect. An unknown, expired or revoked refresh
  token is `invalid_grant`, which is what tells Claude to start over.
- **Bearer auth** (`authenticate_bearer`) rejects an unknown, expired or
  revoked token, a revoked grant, an inactive user, and a token whose
  audience is not this origin's MCP URL. It stamps `grant.last_used_at` at
  most once a minute.

`purge_expired_oauth_tokens --commit` runs daily at 03:45 UTC
(`schedule.py`) and deletes tokens and codes dead for more than a week.

## Redirect URIs

`apps/oauth/services/redirects.py`. A registered URI is `https` with a
host, or `http` on `localhost` / `127.0.0.1`; no fragment, no credentials.
Matching is exact, except that a loopback URI matches a registered loopback
URI on any port (RFC 8252 §7.3) — Claude Code binds a fresh port each run.
Claude's hosted apps use `https://claude.ai/api/mcp/auth_callback`.

An unknown client or an unregistered redirect URI renders an error page and
never redirects, so `/oauth/authorize/` cannot be used as an open
redirector. The consent page adds a warning when every redirect URI is
loopback: the code goes to a program on the user's machine, not a named
site.

**The consent POST does not answer with a 302.** The site CSP carries
`form-action 'self'`, and Chrome applies it to the redirects after a form
submission. django-csp-plus builds the header in middleware from settings
and database rules with no per-response hook, so the POST renders
`oauth/returning.html`, which moves on with `<meta http-equiv="refresh">`
and a link. A meta refresh is a navigation, not a form submission.

## CIMD and the SSRF guard

A CIMD `client_id` is an https URL Snowdesk fetches — a server-side request
to an address the requester chose. `apps/oauth/services/cimd.py`:

- only `https` URLs with a non-root path, no credentials, no fragment;
- the host is resolved with `socket.getaddrinfo` and refused if any address
  is private, loopback, link-local, reserved, multicast or unspecified
  (IPv4-mapped IPv6 is unwrapped first);
- `requests.get(timeout=3, allow_redirects=False, stream=True)`, body capped
  at 64 KB, non-200 refused (a redirect is not followed);
- the body must be a JSON object whose `client_id` equals the URL and whose
  `redirect_uris` is a non-empty list of registrable URIs.

The result is cached on an `OAuthClient` row and refetched after 24 hours.

**Residual risk: DNS rebinding.** `requests` resolves the host again when
it connects, so a name that answers publicly to the check and privately to
the connection passes. The exposure is one GET whose response must parse as
a matching metadata document before anything is kept, and is never shown to
the requester. Closing it needs the connection pinned to the checked
address.

## Testing locally

**curl.** Mint a token for a dev account (it prints once; `-v 0` prints only
the token):

```bash
TOKEN=$(uv run python manage.py mint_mcp_token --email you@example.com --commit -v 0)
curl -s -X POST http://localhost:8000/api/mcp/ \
  -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"ping"}'
```

Without the header the answer is a 401 with the `WWW-Authenticate`
challenge. The token's audience defaults to `SITE_BASE_URL` + `/api/mcp/`;
pass `--resource` when calling another host. The token appears on the
account's settings page as "Local token (mint_mcp_token)" and Disconnect
revokes it.

**Claude.** Claude's hosted apps cannot reach `localhost`. Run
`ngrok http 8000` and add `https://<tunnel>/api/mcp/` as a custom
connector. The development settings already allow ngrok hosts in
`ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` (`config/settings/development.py`),
so the consent POST works over the tunnel with no `.env` change; another
tunnel provider needs its host added to both. Claude should find the
metadata, register, send you to sign-in and consent, and return. Disconnect
on `/account/settings/` and the next tool call is a 401 that makes Claude
ask you to reconnect. The ngrok inspector shows the exact request sequence.

## Claude's requirements, and where each is met

| Requirement | Where |
|-------------|-------|
| 401 + `WWW-Authenticate: Bearer resource_metadata=…` (ignored on a 200) | `apps/mcp_server/views.py::_unauthorised` |
| PRM `resource` equals the URL typed, path included | `protected_resource_metadata` (path suffix echoed) |
| CIMD used only with `client_id_metadata_document_supported` + `none` | `authorization_server_metadata` |
| `offline_access` in AS `scopes_supported` to get a refresh token | `authorization_server_metadata`; `issue_token_pair` |
| PKCE S256 required and advertised | `pkce.py`; `code_challenge_methods_supported` |
| Loopback redirects on any port | `redirects.py::redirect_uri_allowed` |
| Token endpoint form-encoded; `/register` JSON | `token`, `register` views |
| Invalid refresh → `invalid_grant`; refresh tokens rotate | `tokens.py::refresh` |
| Consent shows the redirect host; warns on loopback-only | `oauth/consent.html` |

## Known risks

- **DCR auth methods.** A DCR request asking for `client_secret_*` is
  answered with `none` (RFC 7591 lets the server amend requested metadata).
  Untested against Claude's DCR path; CIMD is advertised and preferred.
- **The open endpoint closed at deploy.** Any anonymous MCP user lost access
  when this shipped; that was the chosen cutover.
- **IdempotencyMiddleware** logs `pwa.idempotency.missing` for every OAuth
  and MCP POST. The noise predates this change.
