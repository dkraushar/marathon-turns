"""Build the 2026 TCS NYC Marathon course as a line on the LION street network.

There's no official GeoJSON of the course, so we rebuild it: the course is a list of
legs ("follow STREET from cross street A to cross street B"), taken from the NYPD
road-closure list for race day. Each leg is routed on LION, strongly preferring the
named street(s). That avoids Manhattan grid ties, where a staircase of side streets
is exactly as short as staying on the avenue.

Known simplifications (worth flagging in the write-up):
- Bay Ridge has three corral variants for the first ~8 miles; we use the
  Dahlgren Pl -> 92nd St -> 4th Ave variant.
- The start point is placed on the Verrazzano approach so the total is 26.22 mi.
- Routing is on street centerlines, not the measured shortest possible route.
"""

import json
import re
from dataclasses import dataclass, field

import geopandas as gpd
import networkx as nx
import numpy as np
from pyproj import Transformer
from shapely.geometry import LineString, Point

from lion import ROOT, build_graph, load_streets

MANHATTAN, BRONX, BROOKLYN, QUEENS, STATEN_ISLAND = 1.0, 2.0, 3.0, 4.0, 5.0  # LION LBoro codes
OFF_ROUTE_PENALTY = 6.0  # cost multiplier for streets a leg doesn't name
MARATHON_FT = 42_195 / 0.3048

to_ft = Transformer.from_crs(4326, 2263, always_xy=True)


@dataclass
class At:
    """Intersection of two named LION streets (regexes), or a lat/lon point snapped to a street."""
    a: str
    b: str | None = None
    boro: str | None = None
    latlon: tuple[float, float] | None = None


@dataclass
class Leg:
    name: str
    to: At
    via: list[str] = field(default_factory=list)  # street-name regexes to prefer


START = At("VERRAZZANO", latlon=(40.6035, -74.0605))

LEGS = [
    Leg("Verrazzano Bridge & 92nd St", At("^92 STREET$", "^4 AVENUE$", BROOKLYN), ["VERRAZZANO", "DAHLGREN", "^92 STREET$"]),
    Leg("4th Ave", At("^4 AVENUE$", "^FLATBUSH AVENUE$", BROOKLYN), ["^4 AVENUE$"]),
    Leg("Flatbush Ave", At("^FLATBUSH AVENUE$", "^LAFAYETTE AVENUE$", BROOKLYN), ["^FLATBUSH AVENUE"]),
    Leg("Lafayette Ave", At("^LAFAYETTE AVENUE$", "^BEDFORD AVENUE$", BROOKLYN), ["^LAFAYETTE AVENUE$"]),
    Leg("Bedford Ave", At("^BEDFORD AVENUE$", "^NASSAU AVENUE$", BROOKLYN), ["^BEDFORD AVENUE$"]),
    Leg("Nassau Ave", At("^NASSAU AVENUE$", "^MANHATTAN AVENUE$", BROOKLYN), ["^NASSAU AVENUE$"]),
    Leg("Manhattan Ave", At("^MANHATTAN AVENUE$", "^GREENPOINT AVENUE$", BROOKLYN), ["^MANHATTAN AVENUE$"]),
    Leg("Greenpoint Ave", At("^GREENPOINT AVENUE$", "^MCGUINNESS BOULEVARD$", BROOKLYN), ["^GREENPOINT AVENUE$"]),
    Leg("McGuinness Blvd", At("PULASKI BRIDGE$", "MCGUINNESS", BROOKLYN), ["MCGUINNESS"]),
    Leg("Pulaski Bridge", At("^11 STREET$", "^48 AVENUE$", QUEENS), ["PULASKI BRIDGE$", "^11 STREET$", "^JACKSON AVENUE$"]),
    Leg("48th Ave", At("^48 AVENUE$", "^VERNON BOULEVARD$", QUEENS), ["^48 AVENUE$"]),
    Leg("Vernon Blvd", At("^VERNON BOULEVARD$", "^10 STREET$", QUEENS), ["^VERNON BOULEVARD$"]),
    Leg("10th St", At("^10 STREET$", "^44 DRIVE$", QUEENS), ["^10 STREET$"]),
    Leg("44th Dr", At("^44 DRIVE$", "^HUNTER STREET$", QUEENS), ["^44 DRIVE$"]),
    Leg("Hunter St", At("^HUNTER STREET$", "^CRESCENT STREET$", QUEENS), ["^HUNTER STREET$"]),
    Leg("Crescent St", At("^CRESCENT STREET$", "^QUEENS PLAZA SOUTH$", QUEENS), ["^CRESCENT STREET$"]),
    Leg("Queensboro Bridge", At("^1 AVENUE$", "^EAST 60 STREET$", MANHATTAN),
        ["QUEENSBORO|KOCH|QBORO", "^QUEENS PLAZA", "^EAST 59 STREET$", "^EAST 60 STREET$", "^1 AVENUE"]),
    Leg("1st Ave", At("^1 AVENUE$", "WILLIS AVENUE BRIDGE$", MANHATTAN), ["^1 AVENUE$"]),
    Leg("Willis Ave Bridge", At("^EAST 135 STREET$", "^WILLIS AVENUE$", BRONX), ["WILLIS AVENUE"]),
    Leg("E 135th St", At("^EAST 135 STREET$", "^ALEXANDER AVENUE$", BRONX), ["^EAST 135 STREET$"]),
    Leg("Alexander Ave", At("^ALEXANDER AVENUE$", "^EAST 141 STREET$", BRONX), ["^ALEXANDER AVENUE$"]),
    Leg("E 141st St", At("^EAST 141 STREET$", "^3 AVENUE$", BRONX), ["^EAST 141 STREET$"]),
    Leg("3rd Ave", At("^3 AVENUE$", "^EAST 138 STREET$", BRONX), ["^3 AVENUE$"]),
    Leg("E 138th St", At("^EAST 138 STREET$", "MADISON AVENUE BRIDGE", BRONX), ["^EAST 138 STREET$"]),
    Leg("Madison Ave Bridge", At("^EAST 138 STREET$", "^5 AVENUE$", MANHATTAN),
        ["MADISON AVENUE BRIDGE", "^MADISON AVENUE$", "^EAST 138 STREET$"]),
    Leg("5th Ave (Harlem)", At("^5 AVENUE$", "^WEST 124 STREET$", MANHATTAN), ["^5 AVENUE$"]),
    Leg("Marcus Garvey Park", At("^MOUNT MORRIS PARK WEST$", "^WEST 120 STREET$", MANHATTAN),
        ["^WEST 124 STREET$", "^MOUNT MORRIS PARK WEST$"]),
    Leg("5th Ave", At("^5 AVENUE$", "^EAST 90 STREET$", MANHATTAN), ["^WEST 120 STREET$", "^5 AVENUE$"]),
    Leg("Central Park East Drive", At("^EAST DRIVE$", "GRAND ARMY|CENTRAL PARK ENTRANCE E 60", MANHATTAN),
        ["^EAST 90 STREET$", "CENTRAL PARK", "^EAST DRIVE$"]),
    Leg("Central Park South", At("^CENTRAL PARK SOUTH$|^WEST 59 STREET$", "^COLUMBUS CIRCLE$", MANHATTAN),
        ["GRAND ARMY", "CENTRAL PARK SOUTH", "59 STREET", "CENTRAL PARK ENTRANCE E 60"]),
    Leg("Finish (West Drive)", At("^WEST DRIVE$", latlon=(40.77215, -73.97705)),
        ["^COLUMBUS CIRCLE$", "CENTRAL PARK", "^WEST DRIVE$"]),
]


def node_streets(G):
    out = {}
    for u, v, d in G.edges(data=True):
        for n in (u, v):
            out.setdefault(n, []).append((d["street"], d["boro"]))
    return out


def resolve(at: At, G, streets_at, near_xy=None):
    """Return the LION node for a waypoint."""
    if at.latlon:
        x, y = to_ft.transform(at.latlon[1], at.latlon[0])
        cands = [n for n, s in streets_at.items() if any(re.search(at.a, name) for name, _ in s)]
        return min(cands, key=lambda n: np.hypot(G.nodes[n]["xy"][0] - x, G.nodes[n]["xy"][1] - y))
    cands = []
    for n, s in streets_at.items():
        in_boro = at.boro is None or any(boro == at.boro for _, boro in s)
        if in_boro and any(re.search(at.a, name) for name, _ in s) and any(re.search(at.b, name) for name, _ in s):
            cands.append(n)
    if not cands:
        raise ValueError(f"No intersection for {at}")
    if near_xy is None or len(cands) == 1:
        return cands[0]
    # Several nodes (divided roads, multi-node intersections): take the nearest one.
    return min(cands, key=lambda n: np.hypot(G.nodes[n]["xy"][0] - near_xy[0], G.nodes[n]["xy"][1] - near_xy[1]))


def route_leg(G, src, dst, via):
    pats = [re.compile(p) for p in via]

    def cost(u, v, d):
        on_route = any(p.search(d["street"]) for p in pats)
        return d["length"] * (1.0 if on_route else OFF_ROUTE_PENALTY)

    return nx.shortest_path(G, src, dst, weight=cost)


def path_coords(G, streets, nodes):
    """Stitch segment geometries along a node path, flipping each to run in travel order."""
    coords = [G.nodes[nodes[0]]["xy"]]
    seg_ids = []
    for u, v in zip(nodes[:-1], nodes[1:]):
        idx = G[u][v]["seg"]
        line = list(streets.geometry.loc[idx].coords)
        if np.hypot(line[0][0] - coords[-1][0], line[0][1] - coords[-1][1]) > \
           np.hypot(line[-1][0] - coords[-1][0], line[-1][1] - coords[-1][1]):
            line = line[::-1]
        coords.extend(line[1:])
        seg_ids.extend([idx] * (len(line) - 1))
    return coords, seg_ids


def trim_start(coords, segs, excess):
    """Drop `excess` feet from the start of the line so the course measures a marathon."""
    if excess <= 0:
        return coords, segs
    walked = 0.0
    for i in range(1, len(coords)):
        step = np.hypot(coords[i][0] - coords[i - 1][0], coords[i][1] - coords[i - 1][1])
        if walked + step >= excess:
            t = (excess - walked) / step
            start = (coords[i - 1][0] + t * (coords[i][0] - coords[i - 1][0]),
                     coords[i - 1][1] + t * (coords[i][1] - coords[i - 1][1]))
            return [start] + coords[i:], segs[i - 1:]
        walked += step
    raise ValueError("excess longer than course")


def build():
    streets = load_streets()
    G = build_graph(streets)
    streets_at = node_streets(G)

    node = resolve(START, G, streets_at)
    coords, segs, legs_out = [G.nodes[node]["xy"]], [], []
    for leg in LEGS:
        nxt = resolve(leg.to, G, streets_at, near_xy=G.nodes[node]["xy"])
        path = route_leg(G, node, nxt, leg.via)
        c, s = path_coords(G, streets, path)
        legs_out.append({"name": leg.name, "start_idx": len(coords) - 1,
                         "streets": sorted({G[u][v]["street"] for u, v in zip(path[:-1], path[1:])})})
        coords.extend(c[1:])
        segs.extend(s)
        node = nxt

    total = LineString(coords).length
    excess = total - MARATHON_FT
    print(f"Routed length {total / 5280:.2f} mi; trimming {excess:.0f} ft off the start")
    coords, segs = trim_start(coords, segs, excess)
    for leg in legs_out:
        print(f"  {leg['name']:<26} via {', '.join(leg['streets'])[:110]}")

    course = gpd.GeoDataFrame({"name": ["course"]}, geometry=[LineString(coords)], crs=2263)
    out = ROOT / "data/processed"
    course.to_parquet(out / "course.parquet")
    np.save(out / "course_segments.npy", np.array(segs))
    (out / "course_legs.json").write_text(json.dumps(legs_out, indent=1))
    print(f"Course: {course.length.iloc[0] / 5280:.3f} mi, {len(coords)} vertices")
    return course


if __name__ == "__main__":
    build()
