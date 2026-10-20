---
name: a-download-carries-its-principal
description: Why basemap.regions / customAreas records carry `principal`, and an unowned one is claimed once (pwaDownloadsSync.ownership, SNOW-1142)
status: current
last-reviewed: 2026-10-20
---

# A download carries its principal; an unowned one is claimed once

**Decision.** Every local area record in `meta:app` (`basemap.regions`,
`basemap.customAreas`) carries `principal` — the `<meta name="pwa-user-id">`
account id it was downloaded under, or `''` for an anonymous download. A
record is recorded to the signed-in account only when that id is its
principal (`static/js/downloads_sync.js`'s `ownership()`). A LEGACY record
(no field) or an ANONYMOUS one is claimed ONCE: the first signed-in account
to see it stamps the local record with its own id, and only then pushes it.
A refresh, repair or content stamp preserves whatever stamp the record had,
including none. `basemap.syncedAreaIds` is keyed by principal.

**Why.** The bytes are device-wide by design — a user who signs out keeps
their maps for the trip, and the tiles cannot be partitioned per account
without breaking that. But `adopt()` and the Manage downloads sheet's
re-assert pushed every area on the device to whoever was signed in, and
the synced marker named no account. On a shared browser a second person
opening the sheet had the first person's areas, names included, copied into
their account and onto their other devices. The device has to know whose a
download is, and the only moment it reliably knows is when the record is
written.

Legacy and anonymous records have no owner to read, so something must
decide. Refusing them for ever would strand every area downloaded before
this change, which in practice belongs to the one person who uses the
device. Letting every account push them is the bug. Claiming once is the
narrowest rule that keeps the common case working: the stamp is written
before the push, conditionally inside one `readModifyWrite`, and a claim
whose stamp does not land does not push, so no area can reach two
accounts through it. A refresh does not claim, because a refresh is not a
statement about who owns an area.

**Consequences.**

- Another account's area is still listed under "On this device", usable,
  syncable and removable here; it is never pushed, never renamed on the
  account and has no Forget (that verb exists only on account-only rows).
- A device used by two people before this change hands each unstamped area
  to whichever of them signs in and opens the map first. That one-time
  misattribution is accepted; it is no worse than the behaviour it replaces,
  and it cannot repeat.
- The old flat synced marker is read as nobody's, so the first load after
  the deploy re-pushes each area the signed-in account owns or claims — one
  redundant `update_or_create` per area, once.
- A new writer of either record must stamp through `stampPrincipal`, or its
  records read as legacy and are claimable.
