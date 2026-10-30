"""Export everything the web page needs as small WGS84 GeoJSON / JSON files."""

import json

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, Point, mapping
from shapely.ops import substring

from cities import CITIES
from lion import ROOT
from turns import CURB_BUFFER_FT, friendly, osm_friendly

PROC = ROOT / "data/processed"
OUT_ROOT = ROOT / "web/public/data"
# NYC's page uses LION (measured widths); every other city uses OpenStreetMap.
SOURCE = {"nyc": "lion"}
WINDOW_FT = 220  # how much course to draw on either side of a turn for the inside/outside paths


CRS = None  # set per city in build()


def to_wgs(geoms):
    return gpd.GeoSeries(geoms, crs=CRS).to_crs(4326)


def rounded(geom, nd=6):
    """GeoJSON with coordinates rounded to ~10 cm, to keep files small."""
    g = mapping(geom)

    def r(c):
        return [round(v, nd) for v in c] if isinstance(c[0], float) else [r(x) for x in c]

    g["coordinates"] = r(g["coordinates"])
    return g


def feature(geom, **props):
    clean = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in props.items()}
    return {"type": "Feature", "geometry": rounded(geom), "properties": clean}


def write(path, obj):
    path.write_text(json.dumps(obj, separators=(",", ":")))
    print(f"wrote {path.relative_to(OUT_ROOT)} ({path.stat().st_size / 1024:.0f} KB)")


def turn_paths(course: LineString, turn, w_half, before_ft=WINDOW_FT, after_ft=WINDOW_FT):
    """Inside line (1 ft off the inner curb) and outside line (1 ft off the outer curb) around a turn."""
    d = course.project(Point(turn.x, turn.y))
    piece = substring(course, max(d - before_ft, 0), min(d + after_ft, course.length))
    off = max(w_half - CURB_BUFFER_FT, 0)
    side = 1 if turn.direction == "left" else -1

    def offset(dist, **kw):
        # On a hairpin the inside offset can be wider than the gap between the two legs, which folds the line
        # into a MultiLineString; step the offset down until it's a clean line (the centerline at worst).
        for f in (1, 0.6, 0.35, 0.15, 0):
            g = piece.offset_curve(dist * f, **kw) if f else piece
            if g.geom_type == "LineString" and not g.is_empty:
                return g
        return piece

    inner = offset(side * off, join_style="mitre", mitre_limit=10)
    outer_arc = offset(-side * off, join_style="round")
    # Mitre limit 2 = a 120° corner, the same cap the corner-to-corner formula uses (a U-turn would spike out).
    outer_sharp = offset(-side * off, join_style="mitre", mitre_limit=2)
    return inner, outer_arc, outer_sharp


def build(slug: str):
    global CRS
    source = SOURCE.get(slug, "osm")
    name_fn = friendly if source == "lion" else osm_friendly
    out = OUT_ROOT / slug
    out.mkdir(parents=True, exist_ok=True)
    verts = pd.read_parquet(PROC / slug / f"{source}_vertices.parquet")
    turns = gpd.read_parquet(PROC / slug / f"{source}_turns.parquet")
    summary = json.loads((PROC / slug / f"{source}_summary.json").read_text())
    CRS = summary.pop("crs")
    course = LineString(verts[["x", "y"]].to_numpy())

    # Course, split where the street changes, so the map can show street names + widths on hover.
    feats, start = [], 0
    for i in range(1, len(verts) + 1):
        if i == len(verts) or verts.street[i] != verts.street[start]:
            end = min(i, len(verts) - 1)
            if end == start:  # a new street name on the very last vertex: nothing left to draw
                break
            seg = LineString(verts[["x", "y"]].to_numpy()[start:end + 1])
            feats.append(feature(to_wgs([seg]).iloc[0], street=name_fn(verts.street[start]),
                                 width_ft=float(verts.width_ft[start]),
                                 mile_start=verts.dist_ft[start] / 5280, mile_end=verts.dist_ft[end] / 5280))
            start = end
    write(out / "course.geojson", {"type": "FeatureCollection", "features": feats})

    # Mile markers.
    miles = [course.interpolate(m * 5280) for m in range(1, 27)]
    write(out / "miles.geojson", {"type": "FeatureCollection", "features": [
        feature(p, mile=m) for m, p in zip(range(1, 27), to_wgs(miles))]})

    # Turns (points) + their inside/outside paths.
    turns = turns.sort_values("dist_ft").reset_index(drop=True)
    turns["id"] = turns.index
    real = turns[turns.kind == "turn"].copy()
    real["rank"] = real.extra_arc_ft.rank(ascending=False, method="first").astype(int)
    pts = to_wgs(gpd.points_from_xy(real.x, real.y))
    turn_feats, path_feats = [], []
    # Stop each turn's window halfway to its neighbors, so a race shows one corner at a time.
    gaps = np.diff(turns.dist_ft.to_numpy())
    turns["room_before"] = np.clip(np.concatenate([[WINDOW_FT * 2], gaps]) / 2, 90, WINDOW_FT)
    turns["room_after"] = np.clip(np.concatenate([gaps, [WINDOW_FT * 2]]) / 2, 90, WINDOW_FT)
    real = real.join(turns[["room_before", "room_after"]])
    for (_, t), p in zip(real.iterrows(), pts):
        inner, outer_arc, outer_sharp = turn_paths(course, t, (t.width_ft + 2 * CURB_BUFFER_FT) / 2,
                                                   t.room_before, t.room_after)
        props = dict(id=int(t.id), rank=int(t["rank"]), mile=t.mile, streets=t.streets,
                     angle_deg=t.angle_deg, direction=t.direction, width_ft=t.width_ft,
                     extra_arc_ft=t.extra_arc_ft, extra_sharp_ft=t.extra_sharp_ft,
                     inner_len_ft=inner.length, outer_len_ft=outer_arc.length)
        turn_feats.append(feature(p, **props))
        for kind, geom in [("inner", inner), ("outer", outer_arc), ("outer_sharp", outer_sharp)]:
            if geom.geom_type == "LineString" and not geom.is_empty:
                path_feats.append(feature(to_wgs([geom]).iloc[0], id=int(t.id), kind=kind, length_ft=geom.length))
    write(out / "turns.geojson", {"type": "FeatureCollection", "features": turn_feats})
    write(out / "turn_paths.geojson", {"type": "FeatureCollection", "features": path_feats})

    # Cumulative extra distance along the course, for the chart (every turn and bend counts).
    cum = turns[["dist_ft", "extra_arc_ft", "extra_sharp_ft", "kind", "id"]].copy()
    cum["cum_arc"] = cum.extra_arc_ft.cumsum()
    cum["cum_sharp"] = cum.extra_sharp_ft.cumsum()
    steps = [dict(mile=0, arc=0, sharp=0)] + [
        dict(mile=round(r.dist_ft / 5280, 3), arc=round(r.cum_arc, 1), sharp=round(r.cum_sharp, 1),
             id=int(r.id) if r.kind == "turn" else None)
        for r in cum.itertuples()] + [dict(mile=round(summary["course_mi"], 3),
                                           arc=round(cum.cum_arc.iloc[-1], 1), sharp=round(cum.cum_sharp.iloc[-1], 1))]
    write(out / "cumulative.json", steps)

    # Course bounds + headline numbers.
    b = to_wgs([course]).total_bounds
    c = CITIES[slug]
    summary.update(slug=slug, name=c.name, city=c.city, edition=c.edition, source=source, gpx_source=c.gpx_source,
                   sections=c.sections, bounds=[round(v, 5) for v in b],
                   biggest_turn=real.sort_values("rank").iloc[0][["streets", "extra_arc_ft"]].to_dict())
    write(out / "summary.json", summary)
    return summary


def comparison(summaries: dict):
    """cities.json: one row per city for the comparison chart. Every city's ranking number comes from OSM, so
    the method is identical everywhere; NYC also carries its LION number."""
    rows = []
    for slug, page in summaries.items():
        osm = json.loads((PROC / slug / "osm_summary.json").read_text())
        rows.append(dict(slug=slug, name=page["name"], city=page["city"], edition=page["edition"],
                         n_turns=osm["n_turns"], total_turning_deg=round(osm["total_turning_deg"]),
                         extra_arc_ft=round(osm["extra_arc_ft"]), extra_sharp_ft=round(osm["extra_sharp_ft"]),
                         page_extra_arc_ft=round(page["extra_arc_ft"]), page_source=page["source"]))
    rows.sort(key=lambda r: -r["extra_arc_ft"])
    write(OUT_ROOT / "cities.json", rows)


if __name__ == "__main__":
    import sys
    slugs = sys.argv[1:] or [s for s in CITIES if (PROC / s / f"{SOURCE.get(s, 'osm')}_summary.json").exists()]
    comparison({slug: build(slug) for slug in slugs})
