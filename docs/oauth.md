---
name: oauth
description: mcp-auth OAuth — /oauth/authorize|token|register, verified_account, disconnect, mint_mcp_token, service-client credentials
status: current
last-reviewed: 2026-10-23
---

# OAuth for the MCP server

Both MCP endpoints — the public catalogue at `POST /api/mcp/` and the staff
ops catalogue at `POST /api/ops/mcp/` ([`docs/mcp-server.md`](mcp-server.md))
— need a Snowdesk account. Since SNOW-1193 the authorization server is
[mcp-auth](https://github.com/hugorodgerbrown/mcp-auth), the hardened
django-oauth-toolkit preset shared by every Titan project, which follows the
MCP authorization spec and Claude's connector requirements and runs them as
a contract test in our suite. Why mcp-auth replaced the hand-written server
of SNOW-1035: [`docs/decisions/mcp-auth-replaces-the-hand-written-oauth-server.md`](decisions/mcp-auth-replaces-the-hand-written-oauth-server.md).

## What is mcp-auth's and what is Snowdesk's

| Piece | Owner | Where |
|-------|-------|-------|
| Discovery documents, consent view, token / register / revoke, switch-account | mcp-auth | `path("", include("mcp_auth.urls"))` in `config/urls.py` |
| Token, refresh, code and application tables | django-oauth-toolkit | `oauth2_provider` migrations |
| `@mcp_endpoint` — bearer → `request.user`, 401 challenge, per-user limit, `no-store` | mcp-auth | `apps/mcp_server/views.py` |
| Settings preset (`OAUTH2_PROVIDER = oauth2_settings(...)`) and `MCP_AUTH` | Snowdesk config | `config/settings/base.py` |
| Who may connect: `verified_account` | Snowdesk | `apps/oauth/policy.py` |
| The verify-first gate in front of the consent page | Snowdesk | `apps/oauth/views.py::authorize` |
| Consent and returning pages in the site's look | Snowdesk | `apps/oauth/templates/mcp_auth/` |
| Connected apps on `/account/settings/` and their Disconnect | Snowdesk | `apps/accounts/views.py::settings_view`, `apps/oauth/views.py::disconnect` |
| Daily purge, under the command contract | Snowdesk | `purge_expired_oauth_tokens` |

`apps.oauth` sits before `mcp_auth` in `INSTALLED_APPS` so its templates
override the package's. It has no models of its own any more; migration
0003 dropped the four tables of the hand-written server.

## The flow, as Claude runs it

1. Claude POSTs to the URL the user added with no token and gets `401` with
   `WWW-Authenticate: Bearer … resource_metadata="<origin>/.well-known/oauth-protected-resource<path>"`.
2. It fetches that document. `resource` is the origin plus the path called,
   trailing slash kept — Claude requires it to equal the URL the user typed.
   django-oauth-toolkit's view strips the slash; mcp-auth's puts it back
   (since v0.1.7).
3. It fetches `/.well-known/oauth-authorization-server`, identifies itself
   by its Client ID Metadata Document (or registers at `/oauth/register/`).
4. It opens `/oauth/authorize/?…&resource=<the URL>` in the browser. A
   signed-out user goes to `accounts:sign_in` (`LOGIN_URL`) with `?next=`
   and comes back. A signed-in account that has not verified its address
   gets `oauth/verify_first.html` (403). Otherwise mcp-auth's consent view
   renders Snowdesk's `mcp_auth/authorize.html`: the app, the host the code
   goes to (with a warning for a loopback callback), the account it will act
   as, and "Use another account". A request whose `resource` is the ops
   server says it opens operational data, not avalanche data
   (`ops_access`).
5. Allow or Cancel posts back. The answer is `mcp_auth/returning.html`, a
   page that moves on with a meta refresh, not a 302 — see below.
6. Claude exchanges the code at `/oauth/token/` with its PKCE verifier for a
   one-hour access token and a thirty-day rotating refresh token, both bound
   to the `resource` it asked for (RFC 8707).
7. Every MCP call carries `Authorization: Bearer <access token>`.
   `@mcp_endpoint` checks scope, expiry, audience and the connect rule on
   every call, so deactivating an account cuts its apps off at once.

**Audience is a URL prefix.** django-oauth-toolkit accepts a token at any
URL beneath its `resource`. That is why the ops server is the sibling
`/api/ops/mcp/` and not `/api/mcp/ops/`: a public-catalogue token must not
open it. A user who connects both gets two connections, one per URL.

**The consent POST does not answer with a 302.** The site CSP carries
`form-action 'self'`, and browsers apply it to the redirect that follows a
form post. django-csp-plus builds the header in middleware with no
per-response hook, so `MCP_AUTH["CONSENT_RETURN"] = "page"` makes mcp-auth
render the returning page instead: a navigation, which `form-action` does
not govern, plus a link for a browser that ignores the refresh.

## Service clients and the client-credentials grant

The data feed at `/api/data/v1/` (SNOW-1197, [`data-feed.md`](data-feed.md))
is read by services, not people. It uses the client-credentials grant on
this same server. A **service client** is a confidential
client-credentials application created by hand in the admin
(`registration_source = manual`), as defined by
`apps.oauth.validators.is_service_client`. Its tokens carry the `data`
scope and no user.

`OAUTH2_PROVIDER["OAUTH2_VALIDATOR_CLASS"]` is
`apps.oauth.validators.Validator`: mcp-auth's validator with one rule
added. The client-credentials grant is refused to anything but a service
client. Without that rule, open Dynamic Client Registration
turns `grant_types: ["client_credentials"]` into a confidential
client-credentials app with a secret, and `/oauth/token/` issues it a
userless token. Before SNOW-1197 it did. Such a token opened nothing,
because mcp-auth's `can_connect` refuses a token with no user before
`verified_account` is asked, but it would have opened any endpoint that
accepts userless tokens, which the feed is.

## Endpoints

| URL | Name | Purpose |
|-----|------|---------|
| `/.well-known/oauth-protected-resource` | `oauth2_provider:oauth-resource-metadata` | RFC 9728, `resource` = the origin |
| `/.well-known/oauth-protected-resource/<path>` | `oauth2_provider:oauth-resource-metadata-path` | Same, `resource` = origin + path verbatim |
| `/.well-known/oauth-authorization-server` | `oauth2_provider:oauth-server-metadata` | RFC 8414 |
| `/oauth/authorize/` | `oauth_authorize` (Snowdesk's gate) → mcp-auth's consent view | Consent page and its POST; 30/min per IP |
| `/oauth/switch-account/` | `oauth2_provider:switch-account` | Signs out and returns through sign-in |
| `/oauth/token/` | `oauth2_provider:token` | Code and refresh grants; 60/min per IP |
| `/oauth/register/` | `oauth2_provider:dcr-register` | Dynamic Client Registration; 10/min per IP |
| `/oauth/revoke/` | `oauth2_provider:revoke-token` | RFC 7009 |
| `/oauth/connected/` | `oauth2_provider:connected-apps` | mcp-auth's own list; nothing links to it |
| `/oauth/apps/<id>/disconnect/` | `oauth:disconnect` | HTMX Disconnect from `/account/settings/`; empty 200 removes the row |

`IdempotencyMiddleware` skips both MCP endpoints and the token, register
and revoke URLs (`IDEMPOTENCY_EXEMPT_PATHS`, SNOW-1169): each checks its
own credential inside the view, after the middleware has run.

## Commands

- **`mint_mcp_token`** (mcp-auth's) — a token for curl. Read-only without
  `--commit`. Snowdesk usernames are email addresses:

  ```bash
  TOKEN=$(uv run python manage.py mint_mcp_token --username you@example.com \
      --resource http://localhost:8000/api/mcp/ --commit -v 0)
  curl -s -X POST http://localhost:8000/api/mcp/ \
    -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" \
    -d '{"jsonrpc":"2.0","id":1,"method":"ping"}'
  ```

  Pass `--resource http://localhost:8000/api/ops/mcp/` for the ops server.
  The account must pass the connect rule (verified). The token's app shows
  on the settings page as "Local token (mint_mcp_token)" and Disconnect
  revokes it. It is not a `RegisteredCommand` — it is the package's — and
  it prints a credential, so it would keep its output out of the register
  anyway.
- **`purge_expired_oauth_tokens`** — django-oauth-toolkit's
  `clear_expired()` under Snowdesk's command contract: read-only without
  `--commit`, tallies `access_tokens_deleted`, `refresh_tokens_deleted` and
  `codes_deleted` (a dry run tallies what it would delete, by running the
  purge in a transaction it rolls back). Scheduled daily at 03:45 UTC (`schedule.py`).

## Testing

`tests/mcp_server/test_views.py` subclasses mcp-auth's `MCPAuthContract`
once per endpoint, so Claude's connector requirements — discovery,
registration, consent, the code and refresh exchange, audience binding, the
connect rule, disconnect — run against Snowdesk's own URLs and settings on
every build. Snowdesk's additions are in `tests/oauth/`.

**Against Claude.** Claude's hosted apps cannot reach `localhost`. Run
`ngrok http 8000` and add `https://<tunnel>/api/mcp/` as a custom connector;
the development settings already allow ngrok hosts. Disconnect on
`/account/settings/` and the next tool call is a 401 that makes Claude ask
you to reconnect.

## Known risks

- **Every connection made before SNOW-1193 reconnects once.** The tokens
  lived in the dropped tables.
