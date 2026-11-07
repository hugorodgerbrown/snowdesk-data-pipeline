---
name: a-fly-past-is-rendered-frame-by-frame
description: 3D fly-past video captured frame by frame via /map/ (flightFrame, pwaMap3dFlight, areTilesLoaded), not screen-recorded
status: current
last-reviewed: 2026-11-07
---

# A fly-past is rendered frame by frame

**Decision.** SNOW-1466 (7 Nov 2026). The 3D fly-past video is a sequence of
still frames. The camera path is a pure function of time
(`flightFrame(flight, t)` in `static/js/map_3d_flight_core.js`). For each
frame the recorder (`bin/record-3d-flyby`) asks the page to draw `t`
(`pwaMap3dFlight.frame(t)` in `static/js/map_3d_flight.js`), waits until the
map is idle with every tile loaded, screenshots it, and ffmpeg encodes the
frames. The frames are drawn by `/map/`'s own 3D view, not a copy of it.

**Why not a screen recording** of the map flying in real time. A real-time
flight shows whatever has loaded by each frame: tiles pop in, the relief
sharpens late, and a slow frame is a dropped frame. The result depends on the
machine and the network on the day. Stepped frames have no clock to keep up
with, so every frame is complete, a slow or GPU-less machine makes the same
video (only later), and a failed run resumes from the last frame on disk.

**Why not a separate renderer** (a Blender scene, Cesium, Google Earth Studio).
The video is meant to show the product working. Our own view gives our
basemap, hachures, relief shading, route colours and DEM, and it stays right
as they change, with nothing to keep in step. It also gives the later "Fly
this route" control (a reader's own route, played live) the same camera path:
`flightFrame` does not care whether `t` comes from a frame counter or
`requestAnimationFrame`.

**Consequences.**

- A frame that has not settled within 60 s rejects rather than resolving
  half-drawn, and the recorder stops with exit 1 (`--resume` picks up again).
  The error names the sources whose tiles were still loading.
- Settled means every tile loaded, not that MapLibre went idle. At some
  cameras over terrain it never goes idle: frame 349 of the Breithorn failed
  every time this way (7 Nov 2026), under SwiftShader with its label
  placement never resting and on a Mac GPU with every source loaded. A
  frame therefore also settles once every tile has stayed loaded for 1.5 s
  (`QUIET_MS`), polled on a timer so it does not depend on the map drawing.
  A first version counted renders with `map.loaded()` true, and still hung
  on the Mac (#1516). A label may still be fading in such a frame; a missing
  tile never is. The error on a timeout gives the map's state.
- The reveal's framing needs the map (`map.cameraForBounds`), so the core
  returns bounds and a weight, and the driver blends towards the fitted camera.
  The core stays free of the map and fully testable in Vitest.
- The swisstopo credit is laid over every frame at encode time, not drawn by
  the page, so a recording shows no page chrome (`body.map-recording`).
- MapLibre 4.7 lowers the pitch and zoom of a jump that would put the camera
  under the ground, judged by the centre's height at the jump. After a long
  jump that height is stale, and an ease not asked to freeze its elevation
  leaves `_elevationFreeze` set, so the height is never read again until a
  gesture. The driver clears that flag before each jump and, once the frame
  has settled with the heights in, jumps again if the camera did not land
  where it was sent. A reader's drag does the same for them.
- Software rendering is slow enough that a close-up frame can take over a
  minute, so `load` takes a `capMs` and the recorder raises the cap to ten
  minutes with `--software-gl`.

How to run it: [`../map-3d-flyby.md`](../map-3d-flyby.md).
