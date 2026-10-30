"""Snap a GPX course guide onto OpenStreetMap streets.

GPS traces wobble and cut corners, so we don't measure them directly. Instead we cut the guide into ~400 m
pieces and, for each, find the cheapest path on the street graph where an edge costs
    length x road-type penalty x (1 + (distance from this piece of the guide / SIGMA_FT)^2).
Streets that follow the guide are nearly free; streets 50 ft off it are expensive. The result is a clean
centerline made of real streets, with names and widths attached.
"""

import re
import sys

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
from shapely import STRtree
from shapely.geometry import LineString, Point
from shapely.ops import substring

from cities import CITIES
from lion import ROOT
from osm import PATH_WIDTH_FT, build_graph, edges_frame, fetch, utm_ft

PIECE_FT = 1300      # longest guide piece (~400 m); pieces also end at every sharp turn in the guide
MISFIT_FT = 100      # a course point this far from the guide (or vice versa) counts as a misfit
CUT_TURN_DEG = 45    # guide turns sharper than this get a piece boundary (pins loops and turnarounds)
OFF_GUIDE_WEIGHT = 1300  # cost per (distance / SIGMA_FT)^2 of a snap point off the guide
SIGMA_FT = 40        # how far off the guide a street can be before it gets expensive
SEARCH_FT = 330      # only consider streets within ~100 m of the piece
CAND_FT = 160        # candidate snap points within ~50 m of the guide
CANDIDATES = 20
GAP_FT = 330         # longest straight gap link (see corridor_graph)
GAP_MIN_FT = 150     # shortest
GAP_MAX_DEG = 25     # gap links must run within this angle of the guide's direction
GAP_NODE_FT = 40     # gap links only join nodes this close to the guide
GAP_PENALTY = 3
SPUR_FT = 300        # out-and-back spurs shorter than this are snapping noise
EXCESS_FT = 40       # a piece's path may be this much longer than its stretch of guide (GPS noise makes
                     # guides longer than the real line, so anything more is a detour or a zigzag)…
SLACK = 1.05         # …or this much shorter (noise again), before the difference starts costing
DETOUR_COST = 20     # …(or this much shorter) after which every foot of difference costs this much
# Prefer streets: an expressway overhead or a park path next to the road shouldn't win a tie.
TYPE_PENALTY = {"motorway": 1.5, "motorway_link": 1.5, "trunk_link": 1.2, "footway": 1.3, "path": 1.3,
                "cycleway": 1.5, "service": 1.1, "track": 1.5, "bridleway": 1.5}


def read_gpx(path) -> np.ndarray:
    t = path.read_text()
    pts = [(float(lo), float(la)) for la, lo in re.findall(r'<trkpt lat="([-\d.]+)" lon="([-\d.]+)"', t)]
    out = [pts[0]]
    for p in pts[1:]:
        if p != out[-1]:
            out.append(p)
    return np.array(out)


def remove_spurs(path: list, G, max_ft=SPUR_FT) -> list:
    """Drop short out-and-back spurs (…A, B, C, B, A…) left where a piece ended on a side street.

    Longer out-and-backs are kept: some courses really do run out and back along the same street.
    """
    path = [n for i, n in enumerate(path) if i == 0 or n != path[i - 1]]
    changed = True
    while changed:
        changed = False
        for i in range(1, len(path) - 1):
            if path[i - 1] != path[i + 1]:
                continue
            # Grow the spur outward while the path retraces itself.
            k = 1
            while i - k - 1 >= 0 and i + k + 1 < len(path) and path[i - k - 1] == path[i + k + 1]:
                k += 1
            spur = path[i - k:i + 1]
            length = sum(G[a][b]["length"] for a, b in zip(spur[:-1], spur[1:]))
            if length <= max_ft:
                path = path[:i - k + 1] + path[i + k + 1:]
                changed = True
                break
    return path


def even_cuts(guide: LineString) -> np.ndarray:
    """Evenly spaced cuts, no piece longer than PIECE_FT."""
    return np.linspace(0, guide.length, max(1, int(np.ceil(guide.length / PIECE_FT))) + 1)


def piece_cuts(guide: LineString) -> np.ndarray:
    """Where to cut the guide into pieces: at every sharp guide turn, plus enough evenly spaced cuts that no
    piece is longer than PIECE_FT. Cutting at turns makes the path visit each corner of a loop and run all the
    way out to each turnaround; long pieces on the straights leave no room to zigzag."""
    g = np.array(guide.simplify(30).coords)
    dist = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(g, axis=0).T))])
    v = np.diff(g, axis=0)
    turn = np.degrees(np.abs((np.diff(np.arctan2(v[:, 1], v[:, 0])) + np.pi) % (2 * np.pi) - np.pi))
    turn_cuts = [d for d, a in zip(dist[1:-1], turn) if a >= CUT_TURN_DEG]
    cuts = [0.0]
    for target in sorted(turn_cuts) + [guide.length]:
        if target - cuts[-1] < 100:
            continue
        n = int(np.ceil((target - cuts[-1]) / PIECE_FT))
        cuts += list(np.linspace(cuts[-1], target, n + 1)[1:])
    if guide.length - cuts[-1] > 1:
        cuts.append(guide.length)
    return np.array(cuts)


def snap(slug: str):
    city = CITIES[slug]
    lonlat = read_gpx(city.gpx)
    crs = utm_ft(*lonlat.mean(axis=0))
    guide = gpd.GeoSeries([LineString(lonlat)], crs=4326).to_crs(crs).iloc[0]

    G = build_graph(fetch(slug, lonlat), crs)
    G = G.subgraph(max(nx.connected_components(G), key=len)).copy()
    edges = edges_frame(G, crs)
    tree = STRtree(edges.geometry.values)

    def candidates(pt):
        """Up to CANDIDATES street nodes near a guide point, with how far off the guide each is."""
        idx = tree.query(pt.buffer(CAND_FT))
        if not len(idx):
            idx = tree.query_nearest(pt)
        cand = np.unique(np.concatenate([edges.u.values[idx], edges.v.values[idx]]))
        xy = np.array([G.nodes[n]["xy"] for n in cand])
        d = np.hypot(*(xy - [pt.x, pt.y]).T)
        keep = np.argsort(d)[:CANDIDATES]
        return dict(zip(cand[keep], d[keep]))

    def corridor_graph(piece, search):
        idx = tree.query(piece.buffer(search))
        sub = edges.iloc[idx]
        d = sub.geometry.centroid.distance(piece).to_numpy()
        pen = sub.highway.map(TYPE_PENALTY).fillna(1.0).to_numpy()
        H = nx.Graph()
        for u, v, l, p, dd in zip(sub.u, sub.v, sub.length, pen, d):
            H.add_edge(u, v, cost=l * p * (1 + (dd / SIGMA_FT) ** 2))
        # Gap links: where the guide crosses a park or plaza with no mapped path, let the course run straight
        # between street nodes that sit on the guide. They cost GAP_PENALTY x their length, so they never beat
        # going around an ordinary street corner, only a long detour around a whole block or park.
        on_guide = [n for n in H if piece.distance(Point(G.nodes[n]["xy"])) <= GAP_NODE_FT]
        xy = np.array([G.nodes[n]["xy"] for n in on_guide]).reshape(-1, 2)
        for a in range(len(on_guide)):
            for b in range(a + 1, len(on_guide)):
                u, v = on_guide[a], on_guide[b]
                length = float(np.hypot(*(xy[a] - xy[b])))
                if not GAP_MIN_FT <= length <= GAP_FT or H.has_edge(u, v):
                    continue
                line = LineString([xy[a], xy[b]])
                dd = line.centroid.distance(piece)
                if dd > GAP_NODE_FT:
                    continue
                # Must run along the guide, not across it (a hop between parallel carriageways is a zigzag).
                at = piece.project(line.centroid)
                p0, p1 = piece.interpolate(max(at - 60, 0)), piece.interpolate(min(at + 60, piece.length))
                along = np.arctan2(p1.y - p0.y, p1.x - p0.x)
                link = np.arctan2(*(xy[b] - xy[a])[::-1])
                off = np.degrees(abs((link - along + np.pi / 2) % np.pi - np.pi / 2))
                if off > GAP_MAX_DEG:
                    continue
                H.add_edge(u, v, cost=length * GAP_PENALTY * (1 + (dd / SIGMA_FT) ** 2))
                if not G.has_edge(u, v):
                    G.add_edge(u, v, length=length, way=0, name="gap", highway="gap", layer="0",
                               tag_width_ft=np.nan, lanes=np.nan, width_how="gap (no mapped path)",
                               path_width_ft=PATH_WIDTH_FT["path"])
        return H

    def route(cuts):
        # Dynamic program over piece boundaries: best[c] = cheapest way to reach candidate node c.
        n_pieces = len(cuts) - 1
        layers = [candidates(guide.interpolate(d)) for d in cuts]
        best = {c: (d / SIGMA_FT) ** 2 * OFF_GUIDE_WEIGHT for c, d in layers[0].items()}
        back = []  # back[i][c2] = (c, path from c to c2)
        for i in range(n_pieces):
            piece = substring(guide, max(cuts[i] - 150, 0), min(cuts[i + 1] + 150, guide.length))
            for search in (SEARCH_FT, SEARCH_FT * 3, SEARCH_FT * 10):
                H = corridor_graph(piece, search)
                nxt, ptr = {}, {}
                for c, base in best.items():
                    if c not in H:
                        continue
                    dist, paths = nx.single_source_dijkstra(H, c, weight="cost")
                    for c2, d2 in layers[i + 1].items():
                        if c2 not in dist:
                            continue
                        p = paths[c2]
                        length = sum(G[a][b]["length"] for a, b in zip(p[:-1], p[1:]))
                        piece_len = cuts[i + 1] - cuts[i]
                        # Too long = a detour or loop. Too short = a shortcut, e.g. U-turning early on an
                        # out-and-back, where the guide runs out to the turnaround and back over itself.
                        detour = max(0.0, length - piece_len * 1.03 - EXCESS_FT) + max(0.0, piece_len / SLACK - 40 - length)
                        total = base + dist[c2] + (d2 / SIGMA_FT) ** 2 * OFF_GUIDE_WEIGHT + detour * DETOUR_COST
                        if total < nxt.get(c2, np.inf):
                            nxt[c2], ptr[c2] = total, (c, paths[c2])
                if nxt:
                    break
            if not nxt:
                raise RuntimeError(f"{slug}: no path for piece {i}")
            if search > SEARCH_FT:
                print(f"  piece {i}: widened corridor to {search} ft")
            best = nxt
            back.append(ptr)
        # Walk back from the cheapest finish.
        c = min(best, key=best.get)
        chunks = []
        for ptr in reversed(back):
            prev, seg = ptr[c]
            chunks.append(seg)
            c = prev
        path = [chunks[-1][0]]
        for seg in reversed(chunks):
            path += seg[1:]
        path = remove_spurs(path, G)

        return path

    def misfit(path):
        """Feet of guide the course misses plus feet of course off the guide (both: more than MISFIT_FT away)."""
        course = LineString([G.nodes[n]["xy"] for n in path])
        g_pts = [guide.interpolate(d) for d in np.arange(0, guide.length, 50)]
        c_pts = [course.interpolate(d) for d in np.arange(0, course.length, 50)]
        return 50 * (sum(course.distance(p) > MISFIT_FT for p in g_pts) + sum(guide.distance(p) > MISFIT_FT for p in c_pts))

    # Two ways to cut the guide into pieces; keep whichever course fits the guide better (see piece_cuts).
    options = {}
    for name, cuts in [("even", even_cuts(guide)), ("at turns", piece_cuts(guide))]:
        path = route(cuts)
        options[name] = (misfit(path), path)
        print(f"  {name:8}: misfit {options[name][0] / 5280:.2f} mi, length "
              f"{sum(G[a][b]['length'] for a, b in zip(path[:-1], path[1:])) / 5280:.2f} mi")
    choice = min(options, key=lambda k: options[k][0])
    path = options[choice][1]

    coords = np.array([G.nodes[n]["xy"] for n in path])
    attrs = pd.DataFrame([G[u][v] for u, v in zip(path[:-1], path[1:])])
    course = LineString(coords)
    print(f"{slug} ({choice} cuts): guide {guide.length / 5280:.2f} mi -> snapped {course.length / 5280:.2f} mi, "
          f"{len(path)} nodes, widths: {attrs.width_how.value_counts(normalize=True).round(2).to_dict()}")

    out = ROOT / "data/processed" / slug
    out.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame(dict(name=[slug]), geometry=[course], crs=crs).to_parquet(out / "course_osm.parquet")
    attrs[["way", "name", "highway", "length", "tag_width_ft", "lanes", "width_how", "path_width_ft"]].to_parquet(
        out / "edges_osm.parquet")
    gpd.GeoDataFrame(dict(name=["guide"]), geometry=[guide], crs=crs).to_parquet(out / "guide.parquet")
    return course, attrs, crs


if __name__ == "__main__":
    for slug in sys.argv[1:] or CITIES:
        snap(slug)
