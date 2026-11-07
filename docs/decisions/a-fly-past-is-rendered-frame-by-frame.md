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
- The reveal's framing needs the map (`map.cameraForBounds`), so the core
  returns bounds and a weight, and the driver blends towards the fitted camera.
  The core stays free of the map and fully testable in Vitest.
- The swisstopo credit is laid over every frame at encode time, not drawn by
  the page, so a recording shows no page chrome (`body.map-recording`).
- Recording found that entering 3D before the style had loaded left the
  map blank in MapLibre 4.7: terrain put on under an ease begun flat throws on
  that ease's next frame and wedges the render loop. `installTerrain` stops a
  running ease first, and `map_view_3d.js` runs the tilt again. Readers hit
  the same race on a slow `?view=3d` boot, so the fix is in the view, not the
  recorder.

How to run it: [`../map-3d-flyby.md`](../map-3d-flyby.md).
