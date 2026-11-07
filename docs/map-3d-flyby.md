---
name: map-3d-flyby
description: Run bin/record-3d-flyby: ffmpeg, map_3d flag, --resume; flyby.mp4/webm/poster; R2 media/flyby/ upload; CSP media-src
status: current
last-reviewed: 2026-11-07
---

# Recording the 3D fly-past

`bin/record-3d-flyby` makes the marketing site's 20 s video of 3D at work: the
camera flies a ski tour on the swisstopo winter map while the line draws
itself, then fades into an end card with the logo and the site's address.
It drives the real `/map/` 3D view in Chromium, one frame at a time, and
encodes the frames with ffmpeg (SNOW-1466; why frame by frame:
[`decisions/a-fly-past-is-rendered-frame-by-frame.md`](decisions/a-fly-past-is-rendered-frame-by-frame.md)).

## How to run it

On your Mac, in the repo, with the dev server set up as in
[`AGENTS.md`](../AGENTS.md#running-locally):

1. **Install ffmpeg once.** `brew install ffmpeg`.
2. **Read the elevation from our copy.** In `.env`:

   ```
   MAP_3D_DEM_TILE_URL=https://tiles.snowdesk-data.info/dem/2026-11/{z}/{x}/{y}.webp
   ```

   The development default is Mapterhorn's server, and a 600-frame run
   should not hotlink it. Our tiles Worker already allows
   `http://127.0.0.1:8000`.
3. **Let the dev user see 3D.** The view is behind the `map_3d` flag:

   ```bash
   uv run python manage.py waffle_flag map_3d --everyone --create
   ```

   The recorder never sets flags itself. It signs in as the seeded dev user,
   the way `bin/capture-pwa-screenshots` does, so the dev database needs
   that user (`bin/init-worktree` seeds it; see [`worktrees.md`](worktrees.md)).
4. **Start the dev server** on its default address, and the Tailwind watcher
   so the CSS is built:

   ```bash
   uv run python manage.py runserver
   ```

5. **Record.** In a second terminal:

   ```bash
   uv run python bin/record-3d-flyby
   ```

   With no arguments it records the bundled Breithorn tour at 1920×1080,
   30 fps, 20 s, into `build/flyby/`. Each line is stamped with the time and
   says how long the run has taken and has left, counting only the frames
   drawn in this run:

   ```
   13:39:18 frame 212/600 follow · 23m 19s elapsed · ~42m 41s left
   ```

   Then come `encoding the MP4` and the other files, and the size of each
   file it wrote. Hugo's Mac took about 6.6 s a frame (7 Nov 2026), so the
   full clip takes a little over an hour, most of it waiting for tiles.
   The frames take about 2 GB at 1080p; delete `build/flyby/frames/` once
   the video is right, unless you want to re-encode without recording.

**Try a short, small cut first** to check the camera before the full run:

```bash
uv run python bin/record-3d-flyby --seconds 5 --size 960x540
```

**If a run stops part way** (a frame that has not settled after 60 s fails
the run, so a half-loaded frame never reaches the video), run the same
command again with `--resume`. It keeps the frames already on disk and
carries on from the first one missing.

### Flags

| Flag | Default | What it does |
|------|---------|--------------|
| `--route` | `bin/flyby/breithorn.geojson` | The route to fly: GeoJSON (the first LineString or MultiLineString) or a GPX, read through the app's own GPX parser |
| `--url` | `http://127.0.0.1:8000` | The dev server |
| `--size` | `1920x1080` | Width × height in pixels; both even, as H.264 needs |
| `--fps` | `30` | Frames per second |
| `--seconds` | `20` | Length, end card included; longer than the 2.5 s fade |
| `--out` | `build/flyby` | Where the frames and the files go |
| `--chromium` | Playwright's own | A Chromium binary to run instead |
| `--resume` | off | Keep the frames on disk and record only the missing ones |
| `--software-gl` | off | Draw with SwiftShader, for a machine with no GPU |

A route file can carry camera options for the fly-past in
`properties.flight`, such as `{"reveal": {"bearingDeg": 15}}` on the
Breithorn, which ends looking north-north-east over the tour. The options and
their defaults are `DEFAULTS` in `static/js/map_3d_flight_core.js`.

### Without a GPU

`--software-gl` renders with SwiftShader. It works, but slowly: a close-up
frame took over a minute in a cloud session, so a full 600-frame run is most
of a day. With it the recorder gives each frame ten minutes to settle rather
than the page's 60 s. A short, small cut (`--seconds 4 --fps 3 --size
960x540`) is a fair way to check a change to the camera path on such a
machine.

## What it writes

In `--out`:

| File | What it is |
|------|------------|
| `flyby.mp4` | H.264, yuv420p, CRF 20, `+faststart` so it plays before it has all downloaded; about 5 Mbps at 1080p |
| `flyby.webm` | VP9 at CRF 32, made from the MP4; smaller, for browsers that prefer it |
| `flyby-poster.jpg` | The frame just before the end card starts to fade in: the whole route in view |
| `frames/f00000.png` … | Every frame, kept for `--resume` |
| `credit.png`, `end-card.png` | The two stills the encode lays over the frames |

"Map © swisstopo" is burned into the bottom right of every frame, the end
card included, so a crop or a re-encode cannot lose it (Hugo, 7 Nov 2026).
The end card is `bin/flyby/end-card.html`, in the site's light tokens; edit
its words there.

## Putting it on the site

The video is hosted on our own R2 bucket, not YouTube (Hugo, 7 Nov 2026), and
served from `tiles.snowdesk-data.info`. The tiles Worker serves any object in
the bucket that is not a tile path, with Range requests, taking Content-Type
and Cache-Control from the object's metadata, so it needs no change.

Upload each file under `media/flyby/<version>/`, where the version is the
month it was cut, signed in to Cloudflare (`npx wrangler login`, or a
`CLOUDFLARE_API_TOKEN` with R2 write). The files are small, so wrangler does;
the tile archives need the multipart upload in the snowdesk-tiles README.

```bash
V=2026-11
for f in flyby.mp4:video/mp4 flyby.webm:video/webm flyby-poster.jpg:image/jpeg; do
  npx wrangler r2 object put "snowdesk-tiles/media/flyby/$V/${f%%:*}" \
    --file "build/flyby/${f%%:*}" \
    --content-type "${f#*:}" \
    --cache-control "public, max-age=31536000, immutable" \
    --remote
done
```

A new cut is a new version, never an overwrite: the files are cached for a
year as immutable, so a changed file under the same name would not reach
readers who already have it.

Check one before embedding it:

```bash
curl -sI https://tiles.snowdesk-data.info/media/flyby/2026-11/flyby.mp4
```

It should answer 200 with `content-type: video/mp4` and
`accept-ranges: bytes`.

**The page that embeds it** needs the tiles host in the Content Security
Policy: `media-src` for the video, which the policy in
`config/settings/base.py` does not have yet (`default-src` is `'none'`, so a
video is refused without it), and `img-src` for the poster. An embed plays
muted and inline, with the WebM listed before the MP4:

```html
<video autoplay muted loop playsinline preload="metadata"
       poster="https://tiles.snowdesk-data.info/media/flyby/2026-11/flyby-poster.jpg">
  <source src="https://tiles.snowdesk-data.info/media/flyby/2026-11/flyby.webm" type="video/webm">
  <source src="https://tiles.snowdesk-data.info/media/flyby/2026-11/flyby.mp4" type="video/mp4">
</video>
```

## How it works

- **The camera path** is `static/js/map_3d_flight_core.js`
  (`pwaMap3dFlightCore`): a pure function of `t` from 0 to 1, so the same `t`
  always gives the same frame. `buildFlight` smooths the route with a
  centripetal Catmull-Rom spline and precomputes a bearing table limited to a
  turn rate, so a hairpin does not whip the camera. `flightFrame` returns the
  camera, the line drawn so far and the head for one frame, through four
  phases: establish (high over the valley), descend (onto the start), follow
  (behind and above the head, looking along the line) and reveal (pulling back
  until the whole route is in view).
- **The page driver** is `static/js/map_3d_flight.js` (`pwaMap3dFlight`),
  loaded on `/map/` for readers in the `map_3d` flag. `load` enters 3D through
  `pwaMapView.beginTest`, the way the 3D test does, and draws the line in the
  route colours. `load(route, {chrome: false})` sets `body.map-recording`,
  which lays the map over the whole page with no controls. `frame(t)` jumps the
  camera and resolves only once the map is idle with every tile loaded. In the
  reveal it fits the route's bounds with `map.cameraForBounds` and blends
  towards them.
- **The recorder** steps `frame(i / (n − 1))` for every frame, screenshots the
  page, and hands the frames to ffmpeg. Nothing runs in real time, so a slow
  machine makes the same video, only later.

A later ticket turns the same camera path into a "Fly this route" control on
a reader's own route, played live.
