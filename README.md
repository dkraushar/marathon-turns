# Running Wide

**How much farther would you run in the NYC Marathon if you took every turn on the outside?**

The marathon is certified along the *shortest possible route*: tangents, hugging every corner 1 ft off the curb.
Almost nobody runs that line, which is one reason most GPS watches read more than 26.2. This project measures
the ceiling: the extra distance if you swung around the outside of every turn.

**Answer (2026 course): about +2,990 ft (0.57 mi) across 66 turns, or +3,500 ft (0.66 mi) if you run all the way
into the far corner every time.** That's a 26.78 to 26.88 mile marathon.

## The idea in one line

Run parallel to the inside line, *w* feet farther out, and a turn through angle *θ* (radians) costs *w × θ* extra
feet, however tight or gentle the corner. So the ceiling is Σ *wᵢ·|θᵢ|* over every turn.

- **Smooth arc:** *w·θ*. A 90° turn on a 60 ft street is about +94 ft.
- **Corner to corner:** *2w·tan(θ/2)*. You run into the far corner of the intersection, then turn.

## How it's built

```
pipeline/lion.py    load NYC LION street centerlines and build a routable graph
pipeline/course.py  rebuild the course street by street, from the race-day road-closure list
pipeline/turns.py   detect turns, attach street widths, compute the extra distance
pipeline/export.py  write small GeoJSON/JSON files for the web page
web/                Vite + MapLibre + D3 interactive page
```

- **Course:** there's no official GeoJSON, so the course is a list of legs ("4th Ave from 92nd St to Flatbush
  Ave") routed on LION. It includes 2026's new Bronx section (E 135th → Alexander → E 141st → 3rd Ave →
  E 138th). As a check, the routed line came out at 26.23 mi before any adjustment; the start is trimmed 75 ft
  to make it exactly 42.195 km.
- **Widths:** LION `StreetWidth_Max` (curb to curb, ft), minus 1 ft off each curb. Divided roads (4th Ave,
  bridges) use one roadbed's width, since you can't cut across the median.
- **Turns:** simplify the line (10 ft Douglas-Peucker), measure the heading change at each vertex, and merge
  same-direction bends within 250 ft into one turn. Turns of at least 15° count as turns; smaller ones are "bends".

## Known limitations

- A ceiling, not a prediction: barricades, water stations and crowds shrink the usable width.
- Bay Ridge has three corral routes for the first ~8 miles; this uses Dahlgren Pl → 92nd St → 4th Ave.
- The Queensboro Bridge exit onto 1st Ave is probably a bigger loop in real life than LION's ramp geometry shows.
- The Madison Ave Bridge shows a small zigzag (about 60 ft of "turns") that may be a LION drawing artifact.

## Run it

```bash
# Python pipeline (Python 3.12, uv). LION 26C from NYC Planning goes in data/raw/lion/.
curl -L -o data/raw/lion/nyclion_26c.zip https://s-media.nyc.gov/agencies/dcp/assets/files/zip/data-tools/bytes/lion/nyclion_26c.zip
unzip data/raw/lion/nyclion_26c.zip -d data/raw/lion
uv run python pipeline/course.py
uv run python pipeline/turns.py
uv run python pipeline/export.py

# Web
cd web && npm install && npm run dev
```

Data: [NYC LION](https://www.nyc.gov/content/planning/pages/resources/datasets/lion) (NYC Department of City
Planning). Basemap © CARTO, © OpenStreetMap contributors.
