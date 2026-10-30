# Running Wide — notes for Claude

## Goals
- Quick personal project for LinkedIn, timed to the NYC Marathon on Sun Nov 1, 2026. Target: live by Fri Oct 30.
- Dana is learning AI-assisted development: explain the reasoning behind changes, offer options with tradeoffs,
  let Dana make the calls. Small reviewed commits.
- Budget: $0/month. Static site on GitHub Pages.

## Decisions so far
- Ceiling = Σ w·|θ| over every turn (smooth arc), with 2w·tan(θ/2) (corner to corner) as the alternative.
- Course rebuilt from the race-day road-closure list, routed on LION (EPSG:2263, feet). 2026 Bronx change included.
- Widths: LION StreetWidth_Max minus 1 ft per curb; divided roads use one roadbed.
- Multi-city (branch `multi-city`): all eight World Marathon Majors. Other cities use OSM streets + a GPX guide
  (goandrace.com) snapped by `match.py`; widths = OSM width tag or lanes × 8 + 30 ft (fitted to LION in
  `calibrate.py`). The comparison uses the OSM method for every city, NYC included; NYC's own page uses LION.
- Front end: Vite + MapLibre 6 + D3 in `web/`, dark CARTO basemap. City picked by URL hash (`#/berlin`). Python does all the math; the page only draws
  the files in `web/public/data/`.

## Conventions
- Python via uv, 3.12. Run pipeline steps from the repo root: `uv run python pipeline/<step>.py`.
- `data/` is gitignored (LION is 46 MB). Small derived files in `web/public/data/` are committed so Pages can
  deploy them. Per city: `web/public/data/<slug>/`, plus `cities.json`.
- MapLibre 6 worker is loaded via `?url` import + `setWorkerUrl` (see `web/src/main.js`); don't remove it, or the
  production build breaks.
