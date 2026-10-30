"""OpenStreetMap streets along a course: download, estimate widths, build a routable graph.

Width estimate (the same everywhere, so cities are comparable):
- OSM `width` tag (carriageway width) when it's mapped;
- otherwise lanes x LANE_FT + EXTRA_FT, with a default lane count by road type.
LANE_FT and EXTRA_FT are calibrated against NYC's LION curb-to-curb widths (see calibrate.py).
"""

import json
import re
import time

import geopandas as gpd
import networkx as nx
import numpy as np
import pyproj
import requests
from shapely.geometry import LineString

from lion import ROOT

OSM_DIR = ROOT / "data/raw/osm"
MIRRORS = ["https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter",
           "https://overpass.kumi.systems/api/interpreter"]
CORRIDOR_M = 100
MAX_SEGMENT_FT = 100  # longer way segments get extra nodes

# Calibrated on NYC (calibrate.py). Width = lanes * LANE_FT + EXTRA_FT (parking, shoulders, gutters).
LANE_FT = 8.0
EXTRA_FT = 30.0

# Lanes per carriageway when OSM has no `lanes` tag: (two-way, one-way).
DEFAULT_LANES = {
    "motorway": (4, 3), "trunk": (4, 2), "primary": (4, 2), "secondary": (2, 2), "tertiary": (2, 2),
    "unclassified": (2, 1), "residential": (2, 1), "living_street": (2, 1), "service": (1, 1),
}
# Widths (ft) for paths without lanes, unless tagged.
PATH_WIDTH_FT = {"pedestrian": 30, "footway": 12, "path": 12, "cycleway": 10, "track": 12, "bridleway": 10}
SKIP_HIGHWAY = {"steps", "elevator", "platform", "corridor", "bus_stop", "construction", "proposed", "raceway",
                "escape", "busway", "via_ferrata", "services", "rest_area"}


def utm_ft(lon, lat):
    """A UTM projection in international feet for this spot, so all the turn math stays in feet."""
    zone = int((lon + 180) // 6) + 1
    south = " +south" if lat < 0 else ""
    return pyproj.CRS.from_proj4(f"+proj=utm +zone={zone}{south} +datum=WGS84 +units=ft +no_defs")


def _overpass(query: str, label: str) -> list:
    for attempt in range(8):
        url = MIRRORS[attempt % len(MIRRORS)]
        try:
            r = requests.post(url, data={"data": query}, timeout=360,
                              headers={"User-Agent": "marathon-turns (github.com/dkraushar/marathon-turns)"})
            r.raise_for_status()
            data = r.json()
            if not data.get("elements"):
                raise ValueError(f"empty result {data.get('remark', '')[:120]}")
            return data["elements"]
        except (requests.RequestException, ValueError) as e:
            print(f"  {label}: {url.split('/')[2]} failed ({str(e)[:100]}); retrying", flush=True)
            time.sleep(15 * (attempt + 1))
    raise RuntimeError(f"Overpass failed for {label}")


PBF_DIR = ROOT / "data/raw/pbf"
# OSM extracts (BBBike city extracts; Geofabrik's Massachusetts for Boston, which starts 26 miles out in Hopkinton).
PBF = {"berlin": "Berlin", "london": "London", "chicago": "Chicago", "tokyo": "Tokyo", "sydney": "Sydney",
       "capetown": "CapeTown", "boston": "Massachusetts"}


def read_pbf(slug: str, guide_lonlat: np.ndarray) -> dict:
    """Highway ways within CORRIDOR_M of the guide, read from a local .osm.pbf extract, in the same shape
    Overpass returns (so everything downstream is the same)."""
    import osmium
    from shapely import prepared
    lat0 = np.radians(guide_lonlat[:, 1].mean())
    k = np.cos(lat0)  # squash longitude so a degree buffer is round on the ground
    corridor = LineString(guide_lonlat * [k, 1]).buffer(CORRIDOR_M / 111_320)
    prepared.prep(corridor)
    minx, miny, maxx, maxy = corridor.bounds
    path = PBF_DIR / f"{PBF[slug]}.osm.pbf"
    elements = []
    fp = (osmium.FileProcessor(str(path), osmium.osm.NODE | osmium.osm.WAY).with_locations()
          .with_filter(osmium.filter.KeyFilter("highway").enable_for(osmium.osm.WAY)))
    for w in fp:
        if not w.is_way():
            continue
        try:
            ll = np.array([(n.lon, n.lat) for n in w.nodes])
        except osmium.InvalidLocationError:
            continue
        xy = ll * [k, 1]
        if xy[:, 0].max() < minx or xy[:, 0].min() > maxx or xy[:, 1].max() < miny or xy[:, 1].min() > maxy:
            continue
        geom = LineString(xy) if len(xy) > 1 else None
        if geom is None or not corridor.intersects(geom):
            continue
        elements.append(dict(type="way", id=w.id, tags=dict(w.tags), nodes=[n.ref for n in w.nodes],
                             geometry=[dict(lat=float(b), lon=float(a)) for a, b in ll]))
    return {"elements": elements}


def fetch(slug: str, guide_lonlat: np.ndarray) -> dict:
    """All highway ways within CORRIDOR_M of the guide line (cached). Queried in ~2.5 km chunks, each cached as
    it arrives, because the public Overpass servers are slow and a long corridor in one query tends to fail."""
    OSM_DIR.mkdir(parents=True, exist_ok=True)
    cache = OSM_DIR / f"{slug}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    if slug in PBF and (PBF_DIR / f"{PBF[slug]}.osm.pbf").exists():
        t0 = time.time()
        data = read_pbf(slug, guide_lonlat)
        cache.write_text(json.dumps(data))
        print(f"{slug}: {len(data['elements'])} ways from {PBF[slug]}.osm.pbf ({time.time() - t0:.0f} s)", flush=True)
        return data
    line = LineString(guide_lonlat).simplify(0.0002)  # ~20 m: plenty for a 100 m corridor
    coords = np.array(line.coords)
    cum = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(coords, axis=0).T))])
    n_chunks = max(1, int(np.ceil(cum[-1] / 0.025)))  # ~0.025° ≈ 2.5 km
    bounds = np.searchsorted(cum, np.linspace(0, cum[-1], n_chunks + 1))
    parts_dir = OSM_DIR / f"{slug}_parts"
    parts_dir.mkdir(exist_ok=True)
    ways = {}
    for k in range(n_chunks):
        part_file = parts_dir / f"{k:03d}.json"
        if not part_file.exists():
            part = coords[max(bounds[k] - 1, 0):bounds[k + 1] + 1]
            pts = ",".join(f"{lat:.5f},{lon:.5f}" for lon, lat in part)
            query = f'[out:json][timeout:300];way["highway"](around:{CORRIDOR_M},{pts});out body geom;'
            t0 = time.time()
            part_file.write_text(json.dumps(_overpass(query, f"{slug} {k + 1}/{n_chunks}")))
            print(f"  {slug} {k + 1}/{n_chunks} ({time.time() - t0:.0f} s)", flush=True)
        for el in json.loads(part_file.read_text()):
            ways[el["id"]] = el
    data = {"elements": list(ways.values())}
    cache.write_text(json.dumps(data))
    print(f"{slug}: {len(ways)} ways", flush=True)
    return data


def parse_width_ft(value):
    """OSM width tags: '12', '12 m', '12.5m', "40'", '40 ft'. Returns feet or None."""
    if not value:
        return None
    v = value.strip().lower().replace(",", ".")
    m = re.match(r"^([\d.]+)\s*(m|ft|')?$", v)
    if not m:
        return None
    n = float(m.group(1))
    return n if m.group(2) in ("ft", "'") else n * 3.28084


def width_inputs(tags: dict) -> dict:
    """What OSM tells us about one carriageway's width: a width tag, or a lane count (tagged or defaulted)."""
    hw = tags.get("highway", "")
    base = hw[:-5] if hw.endswith("_link") else hw
    w = parse_width_ft(tags.get("width") or tags.get("width:carriageway"))
    try:
        lanes, lanes_how = float(tags["lanes"].split(";")[0]), "lanes tag"
    except (KeyError, ValueError):
        oneway = tags.get("oneway") in ("yes", "1", "-1") or tags.get("junction") == "roundabout"
        lanes, lanes_how = DEFAULT_LANES.get(base, (2, 1))[1 if oneway else 0], "default lanes"
    if w and 6 <= w <= 150:
        how = "width tag"
    elif base in PATH_WIDTH_FT:
        how = "path default"
    else:
        how = lanes_how
    return dict(tag_width_ft=w if how == "width tag" else np.nan, lanes=lanes, width_how=how,
                path_width_ft=PATH_WIDTH_FT.get(base, np.nan))


def width_ft(edges, lane_ft=LANE_FT, extra_ft=EXTRA_FT) -> np.ndarray:
    """Curb-to-curb width estimate for each edge (a DataFrame with width_inputs() columns)."""
    from_lanes = edges.lanes * lane_ft + extra_ft
    return np.select([edges.width_how == "width tag", edges.width_how.isin(["path default", "gap (no mapped path)"])],
                     [edges.tag_width_ft, edges.path_width_ft], from_lanes)


def build_graph(data: dict, crs) -> nx.Graph:
    """Undirected graph of OSM way segments (runners use the whole road, whatever the one-way signs say)."""
    to_xy = pyproj.Transformer.from_crs(4326, crs, always_xy=True)
    G = nx.Graph()
    virtual = 0  # ids for nodes we add along long segments; negative, so they never clash with OSM's
    for el in data["elements"]:
        tags = el.get("tags", {})
        hw = tags.get("highway")
        if el["type"] != "way" or hw in SKIP_HIGHWAY or tags.get("area") == "yes":
            continue
        if tags.get("footway") in ("sidewalk", "crossing") or tags.get("path") == "sidewalk":
            continue
        name = tags.get("name:en") or tags.get("name") or tags.get("ref") or hw
        xs, ys = to_xy.transform([p["lon"] for p in el["geometry"]], [p["lat"] for p in el["geometry"]])
        attrs = dict(way=el["id"], name=name, highway=hw, layer=tags.get("layer", "0"), **width_inputs(tags))
        for i in range(len(el["nodes"]) - 1):
            a, b = el["nodes"][i], el["nodes"][i + 1]
            G.add_node(a, xy=(xs[i], ys[i]))
            G.add_node(b, xy=(xs[i + 1], ys[i + 1]))
            length = float(np.hypot(xs[i + 1] - xs[i], ys[i + 1] - ys[i]))
            if G.has_edge(a, b) and G[a][b]["length"] <= length:
                continue
            # Split long segments (bridges!) so the course can snap anywhere along them.
            k = max(1, int(np.ceil(length / MAX_SEGMENT_FT)))
            chain = [a]
            for j in range(1, k):
                virtual -= 1
                t = j / k
                G.add_node(virtual, xy=(xs[i] + t * (xs[i + 1] - xs[i]), ys[i] + t * (ys[i + 1] - ys[i])))
                chain.append(virtual)
            chain.append(b)
            for u, v in zip(chain[:-1], chain[1:]):
                G.add_edge(u, v, length=length / k, **attrs)
    return G


def edges_frame(G: nx.Graph, crs) -> gpd.GeoDataFrame:
    rows = [dict(u=u, v=v, **d, geometry=LineString([G.nodes[u]["xy"], G.nodes[v]["xy"]]))
            for u, v, d in G.edges(data=True)]
    return gpd.GeoDataFrame(rows, crs=crs)
