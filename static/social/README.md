# Social share assets

## `og-default.png`

The default OpenGraph / Twitter share card (1200×630, the
`og:image` / `summary_large_image` ratio). It is referenced site-wide by the
`og:image` and `twitter:image` tags in
[`public/templates/public/base.html`](../../public/templates/public/base.html)
and used on every page that does not override the `og_tags` block.

**What it shows:** a render of the Snowdesk map page — the Swiss avalanche
danger choropleth with the bulletin footer strip. It is a static card; it does
not reflect the danger level, region, or date of any specific bulletin
(dynamic per-page OG generation is a separate, future ticket).

**Provenance (SNOW-221):** produced from a 2× (2400×1260) screenshot of the
live map page, downscaled and compressed to land under the 300 KB unfurl
budget. There is no committed source artefact — to regenerate, recapture the
screenshot and re-run the transform below (requires the dev `sharp` package):

```js
const sharp = require('sharp');
await sharp('<source-screenshot>.png')
  .resize(1200, 630, { fit: 'cover' })
  .flatten({ background: '#2d4a3e' })  // brand green; drops any alpha at edges
  .removeAlpha()                        // opaque, no alpha channel
  .png({ compressionLevel: 9, palette: true, colours: 128, quality: 80 })
  .toFile('static/social/og-default.png');
```

`tests/public/test_og_image.py` locks the binding spec (1200×630, opaque,
< 300 KB) against the committed file.
