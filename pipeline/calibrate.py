"""Calibrate the OSM width estimate against NYC's LION curb-to-curb widths.

Both NYC courses (LION-routed and OSM-snapped) cover the same streets. For each OSM course segment we look up
the LION width at the same spot, then fit  width = lanes x LANE_FT + EXTRA_FT  on the segments whose width
comes from a lane count. Prints the fit and how well each width source agrees with LION.
"""

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely import STRtree
from shapely.geometry import LineString, Point

from osm import EXTRA_FT, LANE_FT, width_ft
from turns import PROC

MATCH_FT = 60  # the two courses must be this close for a segment to count


def paired() -> pd.DataFrame:
    osm_line = gpd.read_parquet(PROC / "nyc/course_osm.parquet")
    edges = pd.read_parquet(PROC / "nyc/edges_osm.parquet")
    lion = pd.read_parquet(PROC / "nyc/lion_vertices.parquet")

    coords = np.array(osm_line.geometry.iloc[0].coords)
    mids = gpd.GeoSeries([Point(p) for p in (coords[:-1] + coords[1:]) / 2], crs=osm_line.crs).to_crs(2263)
    lion_segs = [LineString(lion[["x", "y"]].to_numpy()[i:i + 2]) for i in range(len(lion) - 1)]
    tree = STRtree(lion_segs)
    idx, dist = tree.query_nearest(mids.values, return_distance=True, all_matches=False)
    edges = edges.assign(lion_width_ft=lion.width_ft.to_numpy()[idx[1]], gap_ft=dist)
    return edges[edges.gap_ft < MATCH_FT]


def main():
    e = paired()
    print(f"{e.length.sum() / 5280:.1f} mi of the OSM course matched to LION\n")

    lanes = e[e.width_how.isin(["lanes tag", "default lanes"])]
    # Length-weighted least squares: lion_width ~ lanes * a + b
    w = np.sqrt(lanes.length.to_numpy())
    A = np.column_stack([lanes.lanes, np.ones(len(lanes))]) * w[:, None]
    (a, b), *_ = np.linalg.lstsq(A, lanes.lion_width_ft.to_numpy() * w, rcond=None)
    print(f"fit: width = lanes x {a:.1f} ft + {b:.1f} ft   (in use: lanes x {LANE_FT} + {EXTRA_FT})\n")

    e = e.assign(osm_width_ft=width_ft(e))
    e["err"] = e.osm_width_ft - e.lion_width_ft
    rows = []
    for how, g in e.groupby("width_how"):
        wt = g.length
        rows.append(dict(source=how, miles=wt.sum() / 5280,
                         bias_ft=np.average(g.err, weights=wt), mean_abs_err_ft=np.average(g.err.abs(), weights=wt),
                         lion_mean_ft=np.average(g.lion_width_ft, weights=wt)))
    wt = e.length
    rows.append(dict(source="ALL", miles=wt.sum() / 5280, bias_ft=np.average(e.err, weights=wt),
                     mean_abs_err_ft=np.average(e.err.abs(), weights=wt),
                     lion_mean_ft=np.average(e.lion_width_ft, weights=wt)))
    print(pd.DataFrame(rows).round(1).to_string(index=False))

    print("\nBiggest disagreements (by street):")
    by = e.groupby("name").apply(lambda g: pd.Series(dict(
        miles=g.length.sum() / 5280, osm=np.average(g.osm_width_ft, weights=g.length),
        lion=np.average(g.lion_width_ft, weights=g.length), lanes=g.lanes.median(), how=g.width_how.mode()[0])),
        include_groups=False)
    by["err"] = by.osm - by.lion
    print(by[by.miles > 0.1].sort_values("err", key=abs, ascending=False).head(15).round(1).to_string())


if __name__ == "__main__":
    main()
