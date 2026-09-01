# Weather icon licences

Two sets live in this directory. The fourteen **condition** icons are Yr /
MET Norway's; the two **sun-event** icons (`sunrise.svg`, `sunset.svg`) are
Meteocons', which Yr has no equivalent for.

---

## Condition icons — Yr / MET Norway (MIT)

The condition SVGs are sourced from the
[metno/weathericons](https://github.com/metno/weathericons) project by the
Norwegian Meteorological Institute and NRK.

**Provenance:** `weather/svg/` in that repository. Files have been renamed on
copy to match the internal icon-bucket naming scheme:

| shipped file | Yr source |
|---|---|
| `clear-day.svg` | `clearsky_day.svg` |
| `clear-night.svg` | `clearsky_night.svg` |
| `partly_cloudy-day.svg` | `partlycloudy_day.svg` |
| `partly_cloudy-night.svg` | `partlycloudy_night.svg` |
| `cloudy.svg` | `cloudy.svg` |
| `fog.svg` | `fog.svg` (**modified**, see below) |
| `drizzle.svg` | `lightrain.svg` |
| `light_rain.svg` | `lightrain.svg` |
| `moderate_rain.svg` | `rain.svg` |
| `heavy_rain.svg` | `heavyrain.svg` |
| `light_snow.svg` | `lightsnow.svg` |
| `moderate_snow.svg` | `snow.svg` |
| `heavy_snow.svg` | `heavysnow.svg` |
| `thunder.svg` | `rainandthunder.svg` |

`drizzle.svg` and `light_rain.svg` are byte-identical copies of the same
source drawing. Yr publishes no drizzle icon, and `lightrainshowers_*` is
wrong for WMO 51–57 — continuous drizzle is not a shower. The two buckets
stay distinct in text (`_ICON_BUCKET_LABEL`) and share a drawing.

**Modification.** `fog.svg` carries two edits against upstream, both made so
fog separates from overcast at map-symbol size. Upstream's bars are already
the right size — 124 pixels differ from `cloudy.svg` at 27 px — but too light,
at mean ΔE 34. The edits leave the size alone (127 px) and double the contrast
(ΔE 68):

- the fog bars' fill `#999999` → `#666666`;
- the fog `<use>` transform `translate(0,76) scale(1,1)` →
  `translate(0,72) scale(1,1.35)`.

MIT License

Copyright (c) 2015-2017 Yr

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

## Sun-event icons — Meteocons (MIT)

`sunrise.svg` and `sunset.svg` are sourced from the
[Meteocons](https://github.com/basmilius/meteocons) project by Bas Milius.

**Provenance:** `packages/svg-static` npm package (`@meteocons/svg-static`
v0.1.0), `package/fill/` variant (static fill-style icons, no SMIL
animations). Unmodified apart from the copy.

MIT License

Copyright (c) Bas Milius

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
