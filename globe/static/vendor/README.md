# Vendored files

Everything the page loads is served from this folder or the page's own origin, apart from the OpenFreeMap
basemap. Nothing here is fetched at runtime from a CDN.

## MapLibre GL JS 5.24.1

- Source: the npm registry, `npm pack maplibre-gl@5.24.1 --ignore-scripts`
  (https://registry.npmjs.org/maplibre-gl/-/maplibre-gl-5.24.1.tgz), fetched 2026-11-11.
- Tarball integrity (npm `dist.integrity`, checked against a local `openssl dgst -sha512` of the tarball):
  `sha512-HyiYBwtsbk3L1n1EPLJHWqdoyMiF80n7tEYXFZyu2m1TjvaNx6KMLiFeDdmszt1/mq2zvsR1TJuX/NMu5BonXQ==`
- Tarball sha1 (npm `dist.shasum`): `e2cd747b3703d84e2221b8dac979f20cec0d0f49`
- npm provenance: the SLSA attestation's subject digest matches the tarball, built by
  `.github/workflows/release.yml` in github.com/maplibre/maplibre-gl-js on `refs/heads/5.x`.
- Why this version: it is the newest 5.x (the `latest-5` dist-tag). It was published on 2026-11-11, so
  before using it the minified `dist/maplibre-gl.js` was compared with 5.24.0 (April 2026). With minified
  names masked, the only differences are the version string and the stricter allow-list HTML sanitizer for
  map attributions (`DOM.sanitize`, used by the attribution control). The CSS and license are byte-identical
  to 5.24.0.
- Files copied unchanged from the package (sha256):

| file | sha256 |
|---|---|
| `maplibre-gl/maplibre-gl.js` (from `dist/`) | `a9c9e2aa36d4d5b63b131f27c1272f1a28883eeecc0eddd1e0dbfae4033bc7c9` |
| `maplibre-gl/maplibre-gl.css` (from `dist/`) | `ab1e70d59ec40465bae7e7030da2f3ccf28133fd502e62bd598eefbadfd7a732` |
| `maplibre-gl/LICENSE.txt` | `ee5fc05a0677eaf69601d2c7db0d9ecd6cc27c3abc1d0733bc9ed34707cf8ef2` |

- License: 3-Clause BSD, see `maplibre-gl/LICENSE.txt`.
- CSP: the standard build creates its web workers from a `blob:` URL (allowed by `worker-src 'self' blob:`)
  and uses no `eval` or `new Function`. The bundle ends with a `sourceMappingURL` comment; the map file is
  not vendored, so browser developer tools may log a missing source map, which does not affect the page.

To upgrade: `npm pack maplibre-gl@<version> --ignore-scripts` into a new empty directory, compare the sha512
with `npm view maplibre-gl@<version> dist.integrity`, extract, copy the three files above, and update this
file.

## Natural Earth 110m land

- File: `natural-earth/ne_110m_land.json`, byte-identical to `geojson/ne_110m_land.geojson` (renamed to
  `.json` so any static server sends a JSON content type).
- Source: https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_110m_land.geojson
  (tag v5.1.2, commit f1890d9f152c896d250a77557a5751a93d494776), fetched 2026-11-11.
- sha256: `9e0729ee253ca7d7a5c4ae9395fb1902264c5377c52e224d13dd85010e2835d9`
- 127 land polygons, about 138 KB.
- License: public domain (Natural Earth, naturalearthdata.com).
- Used only by the offline basemap the page switches to when the OpenFreeMap style cannot load.
