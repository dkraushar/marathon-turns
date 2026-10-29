"""Load NYC LION street centerlines and turn them into a routable graph.

LION (NYC Planning's street centerline file) is in EPSG:2263 (NY State Plane, feet),
so every distance in this project is in feet.
"""

from pathlib import Path

import geopandas as gpd
import networkx as nx
from shapely.ops import linemerge

ROOT = Path(__file__).resolve().parents[1]
LION_GDB = ROOT / "data/raw/lion/lion/lion.gdb"
LION_CACHE = ROOT / "data/processed/lion_streets.parquet"

COLUMNS = [
    "Street", "FeatureTyp", "RB_Layer", "NonPed", "SegmentID", "LBoro",
    "NodeIDFrom", "NodeIDTo", "StreetWidth_Min", "StreetWidth_Max",
    "Number_Total_Lanes",
]


def load_streets() -> gpd.GeoDataFrame:
    """Street segments only, in the 'generic' representation.

    LION draws divided roads twice: once as a single generic centerline (RB_Layer G)
    and once per roadbed (R). Segments that are both are B. Routing on B+G gives one
    clean centerline per street, which is what a course map looks like.
    """
    if LION_CACHE.exists():
        return gpd.read_parquet(LION_CACHE)
    g = gpd.read_file(LION_GDB, layer="lion", columns=COLUMNS, engine="pyogrio")
    g = g[g.FeatureTyp.isin(["0", "6"]) & g.RB_Layer.isin(["B", "G"])].copy()
    g["geometry"] = g.geometry.apply(lambda geom: linemerge(geom) if geom.geom_type == "MultiLineString" else geom)
    g = g[g.geometry.geom_type == "LineString"]
    g["Street"] = g.Street.fillna("").str.strip()
    g.to_parquet(LION_CACHE)
    return g


def build_graph(streets: gpd.GeoDataFrame) -> nx.Graph:
    """Undirected graph: nodes are LION node IDs, edges are street segments.

    Undirected on purpose: the course is closed to traffic, so one-way rules don't apply.
    Where two segments join the same pair of nodes we keep the shorter one.
    """
    G = nx.Graph()
    for idx, row in zip(streets.index, streets.itertuples()):
        a, b = row.NodeIDFrom, row.NodeIDTo
        coords = row.geometry.coords
        G.add_node(a, xy=coords[0])
        G.add_node(b, xy=coords[-1])
        length = row.geometry.length
        if G.has_edge(a, b) and G[a][b]["length"] <= length:
            continue
        G.add_edge(a, b, length=length, street=row.Street, boro=row.LBoro, seg=idx)
    return G
