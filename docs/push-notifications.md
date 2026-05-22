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

- The **private key** lives on the server (in a secret file on Render).
- The **public key** is sent to the browser at subscribe time so the push
  service can verify the server's JWT.

Generate the keypair with the management command:

```bash
# Dry-run first — shows what would be written, no disk changes:
python manage.py mint_vapid_keypair

# Generate and write the PEM:
python manage.py mint_vapid_keypair --commit
```

The `--commit` run writes `<BASE_DIR>/.vapid-private.pem` (or the path set by
`VAPID_PRIVATE_KEY_PATH`) and prints the `VAPID_PUBLIC_KEY` value and wiring
instructions.

**Rotation warning.** The command refuses to overwrite an existing PEM. To
rotate, delete the PEM manually first — but be aware that rotating the keypair
invalidates every live `PushSubscription` row. All subscribed devices must
re-register (re-click "Enable push" on `/_push-demo/`). There is no automated
re-subscription path in the current spike.

---

## Wiring on Render

After running `mint_vapid_keypair --commit` locally (or in a one-off shell),
do the following in the Render dashboard:

1. **Add environment variables** (Settings → Environment):

   | Key | Value |
   |-----|-------|
   | `VAPID_PUBLIC_KEY` | The 87-character URL-safe-base64 string printed by the command. |
   | `VAPID_CLAIM_EMAIL` | `mailto:ops@yourdomain.com` (your contact address for the push service). |
   | `VAPID_PRIVATE_KEY_PATH` | `.vapid-private.pem` (relative to the app root; must match where the secret file is mounted). |

2. **Upload the PEM as a secret file** (Settings → Secret Files → Add Secret File):

   - Filename: `.vapid-private.pem` (must match `VAPID_PRIVATE_KEY_PATH`).
   - Content: paste the full PEM contents (including `-----BEGIN PRIVATE KEY-----` header/footer).

3. Trigger a new deploy so the environment variables and secret file take effect.

There is no `render.yaml` in this repository — all deploy configuration is
dashboard-only.

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

## Rotation

Avoid rotating the keypair unless strictly necessary — rotation invalidates
every live `PushSubscription` row and requires all users to re-subscribe.

If you must rotate:

1. Run `python manage.py mint_vapid_keypair` (dry-run) to confirm the target
   path.
2. On Render, remove the existing secret file.
3. Delete the old PEM locally: `rm .vapid-private.pem`.
4. Run `python manage.py mint_vapid_keypair --commit` to generate a new keypair.
5. Update `VAPID_PUBLIC_KEY` in Render's environment variables with the new key.
6. Upload the new PEM as a secret file on Render.
7. Deploy.
8. Every existing `PushSubscription` row is now invalid. A bulk-delete via the
   Django admin (`/admin/subscriptions/pushsubscription/`) cleans up the dead
   rows before the next push attempt does it one-by-one.
