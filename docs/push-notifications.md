---
name: push-notifications
description: Web Push — mint_vapid_keypair, VAPID raw-scalar secret on Render, /_push-demo/ smoke test, rotation, troubleshooting
status: current
last-reviewed: 2026-06-14
---

# Web Push notifications

## Status

**Spike / staff-only.** Web Push delivery is wired and working end-to-end
but is not yet exposed to subscribers. The code lives behind
`@staff_member_required` on `/_push-demo/`. The subscriber-facing CTA,
fan-out, and ingestion trigger are tracked in SNOW-226.

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

The repo-root `render.yaml` is a documentation-only record of the Render
topology (Blueprint auto-sync is not enabled); live deploy changes are still
made via the dashboard. It does not include the VAPID env vars or the secret
file — those live only in the dashboard (the env-var group marked
`fromGroup: Django App Settings`, plus Secret Files).

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
from subscriptions import push_config
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
   Django admin (`/admin/subscriptions/pushsubscription/`) cleans up the dead
   rows before the next push attempt does it one-by-one.
