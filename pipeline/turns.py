"""Find the course's turns, attach street widths, and compute the wide-turn penalty.

The geometry in one line: if you run parallel to the inside line, offset by w feet,
a turn through angle theta (radians) costs you w * theta extra feet, no matter how
sharp the corner is. So the ceiling is roughly sum(w_i * |theta_i|) over every turn.

Two flavors are reported:
- arc:   swing around the outside in a smooth arc         -> w * theta
- sharp: run all the way into the far corner, then turn    -> 2 * w * tan(theta / 2)
"""

import json
import re

import geopandas as gpd
import numpy as np
import pandas as pd
import pyproj
from shapely.geometry import LineString, Point

from lion import LION_GDB, ROOT, load_streets

PROC = ROOT / "data/processed"
SIMPLIFY_FT = 10        # Douglas-Peucker tolerance: removes digitizing wiggles
MIN_VERTEX_DEG = 2      # ignore deflections smaller than this (straight-ish street)
MERGE_GAP_FT = 250      # same-direction bends closer than this are one turn
MIN_TURN_DEG = 15       # turns below this go in the "gentle bends" bucket
CURB_BUFFER_FT = 1      # USATF measures 1 ft off the curb; assume the same on the outside
DEFAULT_WIDTH_FT = 34   # last-resort fallback (typical NYC two-way street)


def segment_widths(streets: gpd.GeoDataFrame, seg_ids: np.ndarray) -> pd.Series:
    """Curb-to-curb width (ft) for each course segment, filling divided roads from roadbeds."""
    used = streets.loc[np.unique(seg_ids)].copy()
    width = used.StreetWidth_Max.copy()

    missing = used[width.isna()]
    if len(missing):
        roadbeds = gpd.read_file(LION_GDB, layer="lion", engine="pyogrio",
                                 columns=["Street", "RB_Layer", "StreetWidth_Max"],
                                 where="RB_Layer = 'R' AND StreetWidth_Max > 0",
                                 bbox=tuple(missing.total_bounds + [-500, -500, 500, 500]))
        for idx, row in missing.iterrows():
            same = roadbeds[roadbeds.Street == row.Street]
            near = same[same.distance(row.geometry) < 200]
            if len(near):
                # One roadbed, not both: a runner can't cut across the median.
                width[idx] = near.StreetWidth_Max.median()

    # Anything still missing: borrow the median width of the same street on the course.
    by_street = width.groupby(used.Street).transform("median")
    width = width.fillna(by_street).fillna(DEFAULT_WIDTH_FT)
    return width


FRIENDLY = [
    ("VERRAZZANO", "Verrazzano Bridge"), ("ED KOCH|QUEENSBORO|QBORO", "Queensboro Bridge"),
    ("PULASKI", "Pulaski Bridge"), ("WILLIS AVENUE BRIDGE", "Willis Ave Bridge"),
    ("MADISON AVENUE BRIDGE", "Madison Ave Bridge"), ("CENTRAL PARK ENTRANCE", "Central Park entrance"),
]
ABBREV = {"AVENUE": "Ave", "STREET": "St", "BOULEVARD": "Blvd", "DRIVE": "Dr", "PLACE": "Pl"}


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def friendly(name: str) -> str:
    """Readable street names: LION ramp names like 'VERRAZZANO BR UPPER EN 92 ST' become 'Verrazzano Bridge'."""
    for pattern, label in FRIENDLY:
        if re.search(pattern, name):
            return label
    words = name.split()
    if words and words[0] in ("EAST", "WEST") and len(words) > 1 and words[1].isdigit():
        words[0] = words[0][0]  # EAST 135 STREET -> E 135th St
    out = []
    for i, w in enumerate(words):
        if w.isdigit() and i + 1 < len(words) and words[i + 1] in ABBREV:
            out.append(ordinal(int(w)))
        else:
            out.append(ABBREV.get(w, w if len(w) == 1 else w.title()))
    return " ".join(out).replace("Mcguinness", "McGuinness")


def vertex_angles(coords: np.ndarray) -> np.ndarray:
    """Signed deflection (radians) at each interior vertex; + is a left turn."""
    v = np.diff(coords, axis=0)
    heading = np.arctan2(v[:, 1], v[:, 0])
    turn = np.diff(heading)
    return (turn + np.pi) % (2 * np.pi) - np.pi


def analyze(raw: np.ndarray, edge_width: np.ndarray, edge_name: np.ndarray, name_fn=friendly):
    """Find turns on any course.

    raw:        (n, 2) course vertices in feet
    edge_width: (n-1,) curb-to-curb width (ft) of each course segment
    edge_name:  (n-1,) street name of each course segment
    Returns (turns, summary, vertices).
    """
    edge_width, edge_name = np.asarray(edge_width, float), np.asarray(edge_name, object)
    line = LineString(raw).simplify(SIMPLIFY_FT, preserve_topology=False)
    coords = np.array(line.coords)
    # Simplified vertices are a subset of the raw ones, in order. Match them walking forward, so a course that
    # passes the same spot twice (out-and-backs) picks up the right pass.
    raw_idx, j = [], 0
    for c in coords:
        while j < len(raw) and not np.allclose(raw[j], c):
            j += 1
        raw_idx.append(min(j, len(raw) - 1))
    raw_idx = np.array(raw_idx)
    e_out = np.minimum(raw_idx, len(raw) - 2)   # segment leaving each simplified vertex
    e_in = np.maximum(raw_idx - 1, 0)           # segment arriving

    dist = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(coords, axis=0).T))])
    angles = vertex_angles(coords)  # angles[k] is at vertex k+1

    # Width at a vertex: average of the streets coming in and going out.
    rows = []
    for k, theta in enumerate(angles, start=1):
        if abs(np.degrees(theta)) < MIN_VERTEX_DEG:
            continue
        rows.append(dict(vertex=k, x=coords[k, 0], y=coords[k, 1], dist_ft=dist[k], theta=theta,
                         w_in=edge_width[e_in[k]], w_out=edge_width[e_out[k]],
                         street_in=edge_name[e_in[k]], street_out=edge_name[e_out[k]]))
    v = pd.DataFrame(rows)
    v["w_eff"] = ((v.w_in + v.w_out) / 2 - 2 * CURB_BUFFER_FT).clip(lower=0)
    v["extra_arc"] = v.w_eff * v.theta.abs()
    v["extra_sharp"] = 2 * v.w_eff * np.tan(np.minimum(v.theta.abs(), np.radians(120)) / 2)

    # Group consecutive same-direction vertices into turns.
    turn_id, last = [], None
    for r in v.itertuples():
        new = (last is None or np.sign(r.theta) != np.sign(last.theta)
               or r.dist_ft - last.dist_ft > MERGE_GAP_FT)
        turn_id.append((turn_id[-1] + 1 if turn_id else 0) if new else turn_id[-1])
        last = r
    v["turn"] = turn_id

    def summarize(g):
        streets_here = list(dict.fromkeys(name_fn(n) for n in [g.street_in.iloc[0], *g.street_out]))
        return pd.Series(dict(
            x=np.average(g.x, weights=g.theta.abs()), y=np.average(g.y, weights=g.theta.abs()),
            dist_ft=np.average(g.dist_ft, weights=g.theta.abs()),
            angle_deg=np.degrees(g.theta.sum()), width_ft=np.average(g.w_eff, weights=g.theta.abs()),
            extra_arc_ft=g.extra_arc.sum(), extra_sharp_ft=g.extra_sharp.sum(),
            direction="left" if g.theta.sum() > 0 else "right",
            streets=" → ".join(streets_here[:3]),
            n_vertices=len(g)))

    turns = v.groupby("turn").apply(summarize, include_groups=False).reset_index(drop=True)
    turns["kind"] = np.where(turns.angle_deg.abs() >= MIN_TURN_DEG, "turn", "bend")
    turns["mile"] = turns.dist_ft / 5280
    turns = turns.sort_values("dist_ft").reset_index(drop=True)

    t = turns[turns.kind == "turn"]
    summary = dict(
        course_mi=dist[-1] / 5280,
        n_turns=int(len(t)),
        total_turning_deg=float(turns.angle_deg.abs().sum()),
        extra_arc_ft=float(turns.extra_arc_ft.sum()),
        extra_sharp_ft=float(turns.extra_sharp_ft.sum()),
        extra_arc_turns_only_ft=float(t.extra_arc_ft.sum()),
        extra_arc_bends_ft=float(turns[turns.kind == "bend"].extra_arc_ft.sum()),
    )
    vertices = pd.DataFrame(dict(x=coords[:, 0], y=coords[:, 1], dist_ft=dist, width_ft=edge_width[e_out],
                                 street=edge_name[e_out]))
    return turns, summary, vertices


def save(slug: str, source: str, turns, summary, vertices, crs):
    """Write data/processed/<slug>/<source>_{turns,summary,vertices}."""
    out = PROC / slug
    out.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame(turns, geometry=gpd.points_from_xy(turns.x, turns.y), crs=crs).to_parquet(out / f"{source}_turns.parquet")
    (out / f"{source}_summary.json").write_text(json.dumps(dict(summary, crs=pyproj.CRS(crs).to_wkt()), indent=1))
    vertices.to_parquet(out / f"{source}_vertices.parquet")


def report(turns, summary):
    print(json.dumps(summary, indent=1))
    t = turns[turns.kind == "turn"]
    print(t.sort_values("extra_arc_ft", ascending=False)
          [["mile", "streets", "angle_deg", "width_ft", "extra_arc_ft", "extra_sharp_ft"]].head(15).round(1).to_string())


def build():
    """NYC, measured on LION (the reference everything else is calibrated against)."""
    streets = load_streets()
    course = gpd.read_parquet(PROC / "course.parquet")
    seg_ids = np.load(PROC / "course_segments.npy")
    raw = np.array(course.geometry.iloc[0].coords)
    widths = segment_widths(streets, seg_ids)
    turns, summary, vertices = analyze(raw, widths.reindex(seg_ids).to_numpy(), streets.Street.reindex(seg_ids).to_numpy())
    report(turns, summary)
    save("nyc", "lion", turns, summary, vertices, 2263)
    return turns, summary


OSM_ABBREV = {"Avenue": "Ave", "Street": "St", "Boulevard": "Blvd", "Drive": "Dr", "Place": "Pl", "Road": "Rd",
              "Parkway": "Pkwy", "Terrace": "Ter", "Square": "Sq"}
OSM_GENERIC = {"motorway_link": "ramp", "trunk_link": "ramp", "primary_link": "ramp", "secondary_link": "ramp",
               "tertiary_link": "ramp", "service": "service road", "footway": "path", "path": "path",
               "pedestrian": "plaza", "cycleway": "path", "track": "path", "gap": "path"}


OSM_UNNAMED_ROADS = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
                     "living_street", "road"}


def osm_friendly(name: str) -> str:
    """OSM names are already readable; shorten the common suffixes and give unnamed ramps/paths a word."""
    if name in OSM_GENERIC:
        return OSM_GENERIC[name]
    if name in OSM_UNNAMED_ROADS:  # no name in OSM: we fell back to the road class
        return "street"
    name = str(name)
    if name.startswith("(") and name.endswith(")"):  # e.g. Tokyo's "(田町駅西口交差点)" intersection names
        name = name[1:-1]
    return " ".join(OSM_ABBREV.get(w, w) for w in name.split())


HAIRPIN_DEG = 120      # turns sharper than this must also show up in the guide
GUIDE_WINDOW_FT = 800  # how far along the guide to look for that turn (turnarounds can snap a block early)


def drop_artifacts(turns, summary, guide: LineString):
    """Hairpins that the GPX guide doesn't make are snapping artifacts (the street graph forced a loop or a
    reversal between parallel carriageways). Real turnarounds are in the guide, so they survive."""
    g = np.array(guide.simplify(30).coords)
    g_dist = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(g, axis=0).T))])[1:-1]
    g_ang = np.degrees(vertex_angles(g))
    keep = []
    for t in turns.itertuples():
        if abs(t.angle_deg) < HAIRPIN_DEG:
            keep.append(True)
            continue
        at = guide.project(Point(t.x, t.y))
        near = np.abs(g_dist - at) <= GUIDE_WINDOW_FT
        keep.append(abs(g_ang[near].sum()) >= 0.6 * abs(t.angle_deg))
    keep = np.array(keep)
    dropped = turns[~keep]
    if len(dropped):
        print(f"dropped {len(dropped)} hairpin artifacts ({dropped.extra_arc_ft.sum():.0f} ft): "
              + "; ".join(f"mi {r.mile:.1f} {r.streets} {r.angle_deg:+.0f}°" for r in dropped.itertuples()))
    turns = turns[keep].reset_index(drop=True)
    t = turns[turns.kind == "turn"]
    summary = dict(summary, n_turns=int(len(t)), total_turning_deg=float(turns.angle_deg.abs().sum()),
                   extra_arc_ft=float(turns.extra_arc_ft.sum()), extra_sharp_ft=float(turns.extra_sharp_ft.sum()),
                   extra_arc_turns_only_ft=float(t.extra_arc_ft.sum()),
                   artifacts_dropped=int(len(dropped)), artifacts_ft=float(dropped.extra_arc_ft.sum()))
    return turns, summary


def build_osm(slug: str):
    """Any city, measured on OSM streets snapped from its GPX guide (match.py)."""
    from osm import width_ft
    course = gpd.read_parquet(PROC / slug / "course_osm.parquet")
    edges = pd.read_parquet(PROC / slug / "edges_osm.parquet")
    raw = np.array(course.geometry.iloc[0].coords)
    turns, summary, vertices = analyze(raw, width_ft(edges), edges.name.to_numpy(), name_fn=osm_friendly)
    turns, summary = drop_artifacts(turns, summary, gpd.read_parquet(PROC / slug / "guide.parquet").geometry.iloc[0])
    summary["width_sources"] = (edges.groupby("width_how").length.sum() / edges.length.sum()).round(3).to_dict()
    save(slug, "osm", turns, summary, vertices, course.crs)
    return turns, summary


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        for slug in sys.argv[1:]:
            report(*build_osm(slug))
    else:
        build()
