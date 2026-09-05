# Competitor store listings — screenshots and review scan

**Captured 2026-09-05.** Research reference for SNOW-836 (the public
`/compare/` page) and SNOW-837 (the unestablished matrix cells).

**The images are not committed.** They are the competitors' own
copyrighted store assets, and there is no reason to carry ~35 MB of them
in this repository's history to make a research point. What is committed
is the reproducible half:

```
./fetch.sh            # 1200px wide
./fetch.sh 600x1300   # smaller, to skim
```

`sources.txt` holds every asset URL and `.gitignore` covers the downloads,
so a fetched image cannot reach a commit by accident.

**Not for publication either way.** Nothing here is referenced from a
template, and the site's CSP `img-src` is `'self' data:` (plus the
slope-tile origin), so these cannot be hotlinked onto a page. Putting
competitor imagery on `/compare/` would be a separate decision about
republishing third-party material — not something to slide in from this
directory.

## The listings

| App | Store | Rating | Reviews | Downloads |
|---|---|---|---|---|
| WhiteRisk | Google Play | 5.0 | 684 | 100K+ |
| WhiteRisk | App Store (CH) | 4.3 | 3 | — |
| Whympr | Google Play | 3.9 | 1,380 | 100K+ |
| SnowSafe | Google Play | none shown | — | 100K+ |
| SnowSafe | App Store (AT) | 4.1 | 8 | — |

**Use Google Play, not the App Store, for this category.** Apple's web
listing showed WhiteRisk with 3 ratings against Play's 684 — it appears to
report a per-storefront, possibly per-version count. A conclusion drawn
from the Apple number alone ("even the official SLF app has no reviews")
would have been wrong.

SnowSafe has no aggregate rating on either store despite 100K+ downloads,
which corroborates what `docs/competitors.md` already suspected.

## What reviewers complain about

Consistent across both apps with real review volume, and the ranking is
the interesting part:

1. **Offline does not work.** The single most damaging complaint, and it
   lands on both. WhiteRisk: downloaded maps do not render when offline.
   Whympr: routes saved for offline use are unavailable on the mountain,
   the app reporting no cached data — from a reviewer who had already
   given it a second chance after a previous two-star review. This is the
   core promise of a backcountry app failing at the one moment it matters.
2. **Paywall and account friction.** WhiteRisk's useful features sit
   behind roughly $32. Whympr users report paying for premium and then
   meeting further paid content, and that an account is required before
   you can see anything at all.
3. **Crashes** (Whympr).
4. **No location search** (WhiteRisk). SLF replied that it is planned.

Credit where due: WhiteRisk holds 5.0 across 684 reviews, so the critical
reviews Play surfaces are the "most helpful" rather than the average. Both
developers answer reviews substantively and within a day.

## In-app purchases across the field

Checked on the store listings, 2026-09-05. Every app is free to install;
the question is what happens next.

| App | IAP | Ads | Notes |
|---|---|---|---|
| WhiteRisk | Yes | No | Paid tiers are topo maps and e-learning; the bulletin is in the free app |
| SnowSafe | Yes | **Yes** | The only one carrying advertising |
| Whympr | Yes | No | Premium, plus à-la-carte content on top |
| OpenSnow | Yes | No | Base and Premium tiers |
| AvalancheClarity | **No** | No | No in-app purchases at all, on either store |
| Skitourenguru / Yéti | — | — | No mobile app found on either store; these appear to be web products |

**AvalancheClarity is the only profiled competitor with no in-app
purchases**, which makes it the only one whose free-ness resembles ours.
Everyone else monetises inside the app. That matters for how we describe
"free" on `/compare/`: our claim is common in the category at install
time and uncommon after it.

Skitourenguru and Yéti returning no app is itself worth recording — the
profile in `docs/competitors.md` does not say whether they are apps or web
products, and the answer changes how a reader would use them.

## AvalancheClarity: the two platforms are at different stages

The profile in `docs/competitors.md` records pan-European coverage — 14
countries, all 134 SLF micro-regions, eight languages — from secondary
reporting, flagged across three passes as never primary-verified. The
store listings now give a primary source, and they do not agree with each
other:

* **iOS** (`id6759971547`, subtitle "Alps Avalanche Reports English"):
  "translates and explains official avalanche bulletins from across
  Europe... in 8 languages". Mentions Switzerland. Free, no IAP, 9 ratings
  at 5.0. This supports the doc's picture.
* **Android** (`com.avalancheclarity.avalanche_clarity`, updated 17 Jul
  2026): "AI-translated avalanche bulletins for **French Alpine
  massifs**", available in English and French only. No mention of
  Switzerland, of eight languages, or of any country but France. **100+
  downloads.**

So the pan-European product exists, on iOS. The Android app is a
France-only subset. The doc's coverage claim should be qualified by
platform rather than restated flat, and the 100+ Android download count is
worth holding next to the doc's read that this is "a real competitor, not
a watch item" — that judgement rested on press coverage, not on usage.

Its listing also confirms, from the developer's own words, two things the
profile inferred: push notifications on new bulletins for chosen massifs,
and offline caching for use without signal.

## Two further findings for docs/competitors.md

* **SnowSafe carries advertising.** Its Play listing says "Contains ads".
  Our profile records the business model as a free app with a paid
  weather tier and says nothing about advertising.
* **SnowSafe's push covers daily updates, not only danger levels.** The
  v6.3.0810 release notes read "Push Notifications for danger levels &
  daily updates". The matrix cell says "Danger levels", which undersells
  it — theirs is closer to AvalancheClarity's re-issue alert than the
  profile implies. Relevant to SNOW-838's trigger design.

## The listings are reachable, contrary to three scans

`docs/competitors.md` records apps.apple.com and the Play listings as
blocked to direct fetch on the 2026-08-19, 08-23 and 08-30 passes. Both
stores loaded fine in the browser pane on 2026-09-05, and `mzstatic.com`
serves image assets to plain `curl` as well.

The scans were using a fetch path that those hosts refuse; a real browser
is not refused. Worth fixing in the scan process before the next pass,
because it is the reason five products carry search-corroborated profiles
instead of primary-verified ones — and the reason the matrix has 27
unestablished cells.

Two lookup notes for whoever automates this: `itunes.apple.com` is denied
where `apps.apple.com` is allowed, and the App Store's own search route
404s, so resolve a numeric app id or Play package name via a search engine
first. Guessing either wastes a round trip.

## Files

* `sources.txt` — the asset base URL behind each screenshot, plus the
  store listing each came from. Seventeen images: WhiteRisk 6, SnowSafe 6,
  Whympr 5, all from the GB App Store galleries.
* `fetch.sh` — downloads them from those URLs at a size you choose.
* `.gitignore` — keeps the downloads out of the repository.
