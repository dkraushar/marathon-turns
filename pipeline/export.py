"""Export everything the web page needs as small WGS84 GeoJSON / JSON files."""

import json

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, Point, mapping
from shapely.ops import substring

from lion import ROOT
from turns import CURB_BUFFER_FT, friendly

PROC = ROOT / "data/processed"
OUT = ROOT / "web/public/data"
WINDOW_FT = 220  # how much course to draw on either side of a turn for the inside/outside paths


def to_wgs(geoms):
    return gpd.GeoSeries(geoms, crs=2263).to_crs(4326)


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


def write(name, obj):
    (OUT / name).write_text(json.dumps(obj, separators=(",", ":")))
    print(f"wrote {name} ({(OUT / name).stat().st_size / 1024:.0f} KB)")


def turn_paths(course: LineString, turn, w_half, before_ft=WINDOW_FT, after_ft=WINDOW_FT):
    """Inside line (1 ft off the inner curb) and outside line (1 ft off the outer curb) around a turn."""
    d = course.project(Point(turn.x, turn.y))
    piece = substring(course, max(d - before_ft, 0), min(d + after_ft, course.length))
    off = max(w_half - CURB_BUFFER_FT, 0)
    left = turn.direction == "left"
    inner = piece.offset_curve(off if left else -off, join_style="mitre", mitre_limit=10)
    outer_arc = piece.offset_curve(-off if left else off, join_style="round")
    outer_sharp = piece.offset_curve(-off if left else off, join_style="mitre", mitre_limit=10)
    return inner, outer_arc, outer_sharp


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    verts = pd.read_parquet(PROC / "course_vertices.parquet")
    turns = gpd.read_parquet(PROC / "turns.parquet")
    summary = json.loads((PROC / "summary.json").read_text())
    course = LineString(verts[["x", "y"]].to_numpy())

    # Course, split where the street changes, so the map can show street names + widths on hover.
    feats, start = [], 0
    for i in range(1, len(verts) + 1):
        if i == len(verts) or verts.street[i] != verts.street[start]:
            end = min(i, len(verts) - 1)
            seg = LineString(verts[["x", "y"]].to_numpy()[start:end + 1])
            feats.append(feature(to_wgs([seg]).iloc[0], street=friendly(verts.street[start]),
                                 width_ft=float(verts.width_ft[start]),
                                 mile_start=verts.dist_ft[start] / 5280, mile_end=verts.dist_ft[end] / 5280))
            start = end
    write("course.geojson", {"type": "FeatureCollection", "features": feats})

    # Mile markers.
    miles = [course.interpolate(m * 5280) for m in range(1, 27)]
    write("miles.geojson", {"type": "FeatureCollection", "features": [
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
    write("turns.geojson", {"type": "FeatureCollection", "features": turn_feats})
    write("turn_paths.geojson", {"type": "FeatureCollection", "features": path_feats})

    # Cumulative extra distance along the course, for the chart (every turn and bend counts).
    cum = turns[["dist_ft", "extra_arc_ft", "extra_sharp_ft", "kind", "id"]].copy()
    cum["cum_arc"] = cum.extra_arc_ft.cumsum()
    cum["cum_sharp"] = cum.extra_sharp_ft.cumsum()
    steps = [dict(mile=0, arc=0, sharp=0)] + [
        dict(mile=round(r.dist_ft / 5280, 3), arc=round(r.cum_arc, 1), sharp=round(r.cum_sharp, 1),
             id=int(r.id) if r.kind == "turn" else None)
        for r in cum.itertuples()] + [dict(mile=round(summary["course_mi"], 3),
                                           arc=round(cum.cum_arc.iloc[-1], 1), sharp=round(cum.cum_sharp.iloc[-1], 1))]
    write("cumulative.json", steps)

    # Course bounds + headline numbers.
    b = to_wgs([course]).total_bounds
    summary.update(bounds=[round(v, 5) for v in b],
                   biggest_turn=real.sort_values("rank").iloc[0][["streets", "extra_arc_ft"]].to_dict(),
                   marcus_garvey_ft=float(real[real.streets.str.contains("Mount Morris|124 Street|120 Street")].extra_arc_ft.sum()))
    write("summary.json", summary)


if __name__ == "__main__":
    build()
