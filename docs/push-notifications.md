---
name: push-notifications
description: Web Push — mint_vapid_keypair, VAPID secret on Render, /_push-demo/ smoke test, Declarative Web Push, mechanism/inactive_at lifecycle
status: current
last-reviewed: 2026-08-07
---

# Web Push notifications

## Status

**Spike / staff-only.** Web Push delivery is wired and working end-to-end
but is not yet exposed to subscribers. The code lives behind
`@staff_member_required` on `/_push-demo/`. The subscriber-facing CTA,
fan-out, and ingestion trigger are tracked in SNOW-226.

---

## Declarative Web Push

Apple's Safari 18.4+ (iOS/iPadOS/macOS) supports **Declarative Web Push**: a
fixed JSON payload shape the OS renders directly into a notification,
without running the service worker's `push` event handler at all. This
matters because SW-based push on iOS is fragile — a background SW eviction
silently drops the notification — whereas a declarative payload is rendered
by the OS even if the SW is gone.

`apps/accounts/models.py::PushSubscription.mechanism` records which path a
given subscription uses:

- `SW` — the service-worker-parsed path (`{title, body, url}`), used by
  every browser today except Safari 18.4+.
- `DECLARATIVE` — Apple's fixed shape, used when the browser exposes
  `'declarativePush' in Notification` at subscribe time.

`static/js/push_demo.js::_supportsDeclarativePush()` does the feature
detection and sends the result as a `"mechanism"` field on the
`/account/push/register/` POST body.

`apps/accounts/push_service.py::dispatch_push` branches the *outgoing wire
payload* on `sub.mechanism` (see `_build_wire_payload`). For a `DECLARATIVE`
subscription, whatever `{title, body, url}`-shaped payload the caller
passes in is translated to:

```json
{
  "web_push": 8030,
  "notification": {
    "title": "New bulletin available",
    "body": "A fresh avalanche bulletin was published for your region.",
    "navigate": "/bulletins/CH-6-11/"
  }
}
```

`web_push: 8030` is Apple's declarative-push version tag (see
[the WebKit blog post](https://webkit.org/blog/16535/meet-declarative-web-push/)).
`notification.navigate` carries what the `SW` shape calls `url` — the page
opened when the notification is tapped. This is the documented **minimum**
subset of `notification` keys; Apple's accepted key set has drifted between
releases, so treat any future field addition (`app_badge`, `silent`,
`mutable`, …) as a schema change requiring a matching update to
`_build_wire_payload` and this section.

`SW` subscriptions are unaffected — they still get the plain
`{title, body, url}` shape the existing service worker `push` handler
parses.

### Browser support for Declarative Web Push

| Browser / Platform | Declarative Web Push |
|---------------------|----------------------|
| Safari 18.4+ (macOS, iOS, iPadOS) | Yes |
| Chrome, Firefox, Edge, Samsung Internet | No — always `SW` |
| Safari < 18.4 | No — always `SW` |

---

## `mechanism` field lifecycle

- Defaults to `"SW"` — both on the model (`PushSubscription.Mechanism.SW`)
  and when `push_register`'s POST body omits the field entirely, so old
  clients that predate SNOW-380 keep working unchanged.
- Set from the `"mechanism"` key in the `push_register` POST body, which
  `static/js/push_demo.js::enablePush` and `::reverifyPushSubscription` both
  populate via `_supportsDeclarativePush()`. The server upper-cases the
  incoming value before validating (SNOW-582) — a stale cached page or
  service worker still sending the pre-SNOW-582 lower-case wire value
  (`"sw"`, `"declarative"`) keeps working — then rejects anything outside
  `PushSubscription.Mechanism.values` with `400`.
- Flips only on a fresh `update_or_create` call against the same endpoint —
  it is not reconciled retroactively; a device only reports `DECLARATIVE`
  once it resubscribes after upgrading to a Declarative-Web-Push-capable
  browser.

---

## `inactive_at` lifecycle

- `null` on every freshly created or freshly resubscribed row.
- Set to `timezone.now()` by `dispatch_push` when the push service returns
  **410 Gone** — the confirmed-dead signal (permission revoked, PWA
  uninstalled, site data cleared). The row is **not** deleted; it stays
  around as the record `reverifyPushSubscription` reconciles against on the
  device's next launch (via the `meta:app` / `push.subscribed_before` flag
  — see [`indexeddb-scaffolding.md`](indexeddb-scaffolding.md)).
- A **404** (rare transport-layer error, not a confirmed-gone signal) still
  hard-deletes the row — there's nothing useful to reconcile against a
  wrong URL.
- Cleared back to `null` automatically the next time the same endpoint
  registers — `push_register`'s `update_or_create` always writes
  `inactive_at=None` into `defaults`. In practice a resubscribe after 410
  usually produces a **new** endpoint (the push service issues a fresh
  token), so this typically manifests as a brand-new row rather than the
  old one flipping back to active — but the reset guards the same-endpoint
  edge case too.
- `PushSubscription.objects.active()` filters to `inactive_at__isnull=True`
  — use this instead of `.all()` wherever a caller only cares about
  deliverable subscriptions (e.g. a future bulk-dispatch command).

---

## VAPID subject requirement

`push_config.VAPID_CLAIM_EMAIL` is the JWT `sub` claim sent with every
Web Push dispatch (RFC 8292 §2). It **must** start with `mailto:` or
`https:` — push services (notably Apple's APNs, which backs Safari/iOS Web
Push) reject the JWT with a 403 for anything else, and the failure only
surfaces at dispatch time in production.

`apps/accounts/checks.py::check_vapid_claim_email` — registered via
`apps/accounts/apps.py::AccountsConfig.ready()` — fails
`manage.py check` (error code `apps.accounts.push_config.E001`) if
`VAPID_CLAIM_EMAIL` doesn't start with one of those two prefixes, catching
a misconfigured environment before it reaches production silently.

---

## Never `unsubscribe()` on logout

Per spec §8.2.5: `PushSubscription.unsubscribe()` (the browser API call,
distinct from the Django model) must only ever run from an explicit,
user-initiated "Disable push" action. Signing out of an account session
is **not** the same thing as opting out of push, and calling
`unsubscribe()` from a logout hook would silently break notifications for
a user who just wanted a fresh session on the same device.

`static/js/push_demo.js::disablePush` is the only call site of
`sub.unsubscribe()` in the codebase, and carries a `WHY` comment
documenting this rule directly above the call — enforcement here is
code-level (the comment plus code review), not a runtime guard, so keep it
in mind if you ever add a logout flow that touches push state.

---

## Minting the VAPID keypair

VAPID (Voluntary Application Server Identification) is the authentication
mechanism that proves to the browser's push service (Apple, Google, Mozilla)
that a push originates from your server. It requires a P-256 keypair:

- The **public key** is sent to the browser at subscribe time so the push
  service can verify the server's JWT.
- The **private key** lives on the server in a secret file. We store it as
  the raw 32-byte private scalar encoded as URL-safe-base64 (a single 43-char
  line) — **not** as a PEM. See [Why raw scalar and not PEM?](#why-raw-scalar-and-not-pem)
  below for the reason.

Generate the keypair with the management command:

```bash
# Dry-run first — shows what would be written, no disk changes:
python manage.py mint_vapid_keypair

# Generate and write the secret file:
python manage.py mint_vapid_keypair --commit
```

The `--commit` run:

1. Generates a fresh P-256 keypair.
2. Writes the raw private scalar to `<BASE_DIR>/.vapid-private.key`
   (or the path set by `VAPID_PRIVATE_KEY_PATH`).
3. Runs a self-test — loads the file back via `py_vapid.Vapid.from_string()`
   and confirms the derived public key matches the printed one. If the
   self-test fails, the command exits non-zero so you can't deploy a broken
   keypair.
4. Prints both the `VAPID_PUBLIC_KEY` env-var value and the raw-scalar
   secret-file contents, plus a Render wiring template.

**Rotation warning.** The command refuses to overwrite an existing secret
file. To rotate, delete the file manually first — but be aware that rotating
the keypair invalidates every live `PushSubscription` row. All subscribed
devices must re-register (re-click "Enable push" on `/_push-demo/`). There
is no automated re-subscription path in the current spike.

---

## Wiring on Render

After running `mint_vapid_keypair --commit` (locally or in a one-off
Render shell), do the following in the Render dashboard:

1. **Add environment variables** (Settings → Environment):

   | Key | Value |
   |-----|-------|
   | `VAPID_PUBLIC_KEY` | The 87-character URL-safe-base64 string printed by the command. |
   | `VAPID_CLAIM_EMAIL` | `mailto:ops@yourdomain.com` (your contact address for the push service). |
   | `VAPID_PRIVATE_KEY_PATH` | `.vapid-private.key` (relative to the app root; must match the secret-file name). |

2. **Upload the secret file** (Settings → Secret Files → Add Secret File):

   - **Filename:** `.vapid-private.key` (must match `VAPID_PRIVATE_KEY_PATH`).
   - **Contents:** the single-line raw scalar printed by the command. Do
     **not** paste the PEM — see [Why raw scalar and not PEM?](#why-raw-scalar-and-not-pem).

3. Trigger a new deploy so the environment variables and secret file take
   effect.

> **Important:** The public key in `VAPID_PUBLIC_KEY` and the private key in
> the secret file **must come from the same `mint_vapid_keypair` run.** If
> they don't match, browsers will subscribe successfully against the public
> key but every push dispatch will fail because the JWT signed by the private
> key won't verify against it. The command's self-test ensures the two halves
> match at generation time; the only way to ship a mismatch is to copy them
> from different runs by hand. Don't.

The repo-root `render.yaml` is the source of truth for the Render service
topology (Blueprint auto-sync is enabled). It does not include the VAPID env
vars or the secret file — those live only in the dashboard (in the
`Production` env group referenced via `fromGroup`, plus Secret Files —
Blueprint doesn't touch env-group contents or Secret Files).

---

## Why raw scalar and not PEM?

py_vapid 1.9's `Vapid.from_pem()` decodes the PEM body via a URL-safe-base64
decoder (`b64urldecode`) instead of the standard base64 decoder that PEM bodies
actually use. Whenever the DER body happens to contain a `+` or `/` character
— roughly 25% of generated keys, depending on the bit alignment of the
underlying numbers — parsing fails with:

```
ValueError: Could not deserialize key data. The data may be in an incorrect
format ... ASN.1 parsing error: unexpected tag
```

The same bug fires in pywebpush's dispatch path, so an unlucky keypair would
make every push delivery fail silently in production, even though the secret
"looks" loadable in local testing.

The raw private scalar form (the 32-byte EC scalar as URL-safe-base64) routes
through py_vapid's `Vapid.from_string()` instead — a completely separate code
path that's bug-free. By writing the secret in that form we sidestep the issue
entirely.

This means:

- The secret file extension is `.key`, not `.pem`.
- Its contents are a single ~43-char line, not a multi-line block with
  `-----BEGIN PRIVATE KEY-----` headers.
- The PEM is still printed (with `--verbosity 2`) for human inspection, but
  you should never upload it as the secret.

---

## Manual smoke test

### Desktop Chrome

1. Log in to `https://snowdesk.info/admin/` as a Django superuser.
2. Navigate to `/_push-demo/`.
3. Click **Enable push** → browser requests Notification permission → grant it.
4. The **Subscription** row should flip to `subscribed` and show an endpoint URL.
5. Fill in a title/body and click **Send test push to this device**.
6. A notification should appear on the desktop within a few seconds.

### iOS PWA (standalone mode)

Safari on iOS only delivers Web Push in standalone (installed PWA) mode.

1. On an iPhone, visit `https://snowdesk.info` in Safari.
2. Tap the Share button → **Add to Home Screen** → Add.
3. Launch Snowdesk from the home screen icon (must open in standalone mode,
   not in Safari).
4. Navigate to `/_push-demo/` within the PWA and follow steps 3–6 above.
5. Lock the screen — the notification should appear on the lock screen.

### Declarative Web Push (Safari 18.4+)

1. Follow the iOS PWA steps above on a device running Safari 18.4+ (or
   macOS Safari 18.4+, non-standalone is fine there).
2. Before clicking **Enable push**, open the browser console and confirm
   `'declarativePush' in Notification` returns `true` — this is the same
   check `_supportsDeclarativePush()` runs client-side.
3. Click **Enable push**, grant permission, and confirm the register POST
   in the network tab carries `"mechanism":"DECLARATIVE"`.
4. Check `/admin/accounts/pushsubscription/` — the new row's
   **Mechanism** column should read `DECLARATIVE`.
5. Send a test push. The OS renders the notification directly from the
   Declarative Web Push JSON shape — no service worker `push` handler
   runs for this delivery, so a broken/evicted SW does not prevent the
   notification appearing.

---

## Browser support matrix

| Browser / Platform | Support |
|--------------------|---------|
| Chrome (desktop, Android) | Full |
| Firefox (desktop) | Full |
| Edge (desktop) | Full |
| Safari 16.4+ (macOS) | Full |
| Safari on iOS 16.4+ (standalone PWA only) | Full (in standalone mode; blocked in Safari in-app) |
| Samsung Internet | Full |
| Safari < 16.4 | No |

---

## Troubleshooting

### `InvalidAccessError: applicationServerKey is not valid`

The browser couldn't decode the public key into a valid 65-byte P-256
point. Almost always means `VAPID_PUBLIC_KEY` isn't set (or is set to an
empty string) in the environment, so the page rendered an empty
`<meta name="vapid-public-key" content="">` tag. Check in the browser console:

```js
document.querySelector('meta[name="vapid-public-key"]').content.length
// should be 87
```

If it's 0, set the env var on Render and redeploy. If it's 87 but you still
get the error, the page may be cached — DevTools → Application → Service
Workers → Unregister, then Storage → Clear site data, reload.

### `Could not deserialize key data ... unexpected tag` from py_vapid

You uploaded a PEM as the secret file. See
[Why raw scalar and not PEM?](#why-raw-scalar-and-not-pem). Re-run
`mint_vapid_keypair --commit` (after deleting the old secret), upload the
**raw scalar** output instead, and update `VAPID_PUBLIC_KEY` to match.

### Push notification doesn't arrive even though subscribe succeeded

Most common cause: the public key in `VAPID_PUBLIC_KEY` and the private key in
the secret file don't come from the same `mint_vapid_keypair` run. The browser
registered with one public key; the server is signing with a different private
key; the push service rejects the JWT.

Diagnose on Render with:

```python
from py_vapid import Vapid
from accounts import push_config
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import base64

vapid = Vapid.from_string(push_config.VAPID_PRIVATE_KEY)
pub = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
print("from secret:", base64.urlsafe_b64encode(pub).rstrip(b"=").decode())
print("from env:   ", push_config.VAPID_PUBLIC_KEY)
```

If those two lines differ, regenerate the keypair end-to-end and update both
halves together.

---

## Rotation

Avoid rotating the keypair unless strictly necessary — rotation invalidates
every live `PushSubscription` row and requires all users to re-subscribe.

If you must rotate:

1. Run `python manage.py mint_vapid_keypair` (dry-run) to confirm the target
   path.
2. On Render, remove the existing secret file.
3. Delete the old secret locally: `rm .vapid-private.key`.
4. Run `python manage.py mint_vapid_keypair --commit` to generate a new keypair.
5. Update `VAPID_PUBLIC_KEY` in Render's environment variables with the new
   key the command printed.
6. Upload the new secret file on Render (raw scalar; same filename).
7. Deploy.
8. Every existing `PushSubscription` row is now invalid. A bulk-delete via the
   Django admin (`/admin/accounts/pushsubscription/`) cleans up the dead
   rows before the next push attempt does it one-by-one.
