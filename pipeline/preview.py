"""Draw the link-preview image (1200x627 layout, saved at 2x): the NYC course and its headline number (Open Graph) from the site's own data: web/public/preview.png."""

import json

import geopandas as gpd
import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from lion import ROOT  # noqa: E402

DATA = ROOT / "web/public/data"
# NYC Open Data "Borough Boundaries" (gthc-hcne): shoreline-clipped, so water stays water.
BOROUGHS = ROOT / "data/raw/boroughs/boroughs.geojson"
# US Census cartographic county boundaries (1:500k, shoreline-clipped): New Jersey, Long Island and Westchester
# around the city, so the map has no artificial edges. NYC's own five counties come from BOROUGHS instead.
COUNTIES = ROOT / "data/raw/counties/cb_2023_us_county_500k.zip"
NYC_COUNTIES = {"36005", "36047", "36061", "36081", "36085"}
OUT = ROOT / "web/public/preview.png"
W, H, DPI = 1200, 627, 100
SCALE = 2  # save at 2400x1254 so it stays sharp on high-resolution screens (same layout)
BG, TEXT, MUTED = "#0d0f14", "#e9ecf2", "#b3bacb"
LAND, COAST = "#222834", "#2e3542"  # land just lighter than the water
OUTER, COURSE = "#ff6a3d", "#f4f1ea"
SANS = "Avenir Next"


def land_shape():
    """NYC's detailed shoreline merged with the neighboring counties into one shape, so only real coastline
    gets an outline (no county or borough lines across the land)."""
    from shapely import union_all
    nyc = gpd.read_file(BOROUGHS).geometry
    counties = gpd.read_file(f"zip://{COUNTIES}").to_crs(4326).cx[-74.5:-73.2, 40.3:41.2]
    # A hair of overlap closes slivers where the two sources' borders don't quite meet.
    neighbors = counties[~counties.GEOID.isin(NYC_COUNTIES)].geometry.buffer(0.0006)
    return union_all(list(nyc) + list(neighbors)).simplify(0.0001)


def text_width(fig, t):
    fig.canvas.draw()
    return t.get_window_extent().width / W


def main():
    course = json.loads((DATA / "nyc/course.geojson").read_text())
    turns = json.loads((DATA / "nyc/turns.geojson").read_text())
    # NYC's own page numbers (LION widths), so the image matches the page and the post: +0.57 mi.
    nyc = json.loads((DATA / "nyc/summary.json").read_text())
    extra_mi = nyc["extra_arc_ft"] / 5280
    certified_mi = 42.195 / 1.609344

    fig = plt.figure(figsize=(W / DPI, H / DPI), dpi=DPI, facecolor=BG)

    # LinkedIn shows this card ~520 px wide (less than half size) and re-compresses it, so everything is
    # sized to survive that: no text under ~19 pt here (≈11 px on the card), bold weights, no fine print.

    # The NYC course on a quiet map (land a shade lighter than the water) behind the whole card, every turn
    # sized by its cost. The course sits on the left; the text sits on the map to its right.
    ax = fig.add_axes([0, 0, 1, 1], facecolor=BG)
    lat0 = np.radians(40.72)
    k = np.cos(lat0)
    land = land_shape()
    for poly in getattr(land, "geoms", [land]):
        x, y = poly.exterior.xy
        ax.fill(np.array(x) * k, y, color=LAND, ec=COAST, lw=1.0, zorder=1)
        for hole in poly.interiors:  # lakes and reservoirs
            hx, hy = hole.xy
            ax.fill(np.array(hx) * k, hy, color=BG, ec=COAST, lw=1.0, zorder=1)
    for f in course["features"]:
        xy = np.array(f["geometry"]["coordinates"])
        x, y = xy[:, 0] * k, xy[:, 1]
        ax.plot(x, y, color=OUTER, lw=16, alpha=0.2, solid_capstyle="round", zorder=2)
        ax.plot(x, y, color=COURSE, lw=3.5, solid_capstyle="round", zorder=2)
    pts = np.array([f["geometry"]["coordinates"] for f in turns["features"]])
    extra = np.array([f["properties"]["extra_arc_ft"] for f in turns["features"]])
    ax.scatter(pts[:, 0] * k, pts[:, 1], s=extra * 2.4, color=OUTER, alpha=0.9,
               edgecolors=BG, linewidths=1.2, zorder=3)
    # Frame the course: a little padding top and bottom, centered a bit left of the fade.
    cx = np.concatenate([np.array(f["geometry"]["coordinates"])[:, 0] for f in course["features"]]) * k
    cy = np.concatenate([np.array(f["geometry"]["coordinates"])[:, 1] for f in course["features"]])
    y_lo, y_hi = cy.min(), cy.max()
    pad = (y_hi - y_lo) * 0.06
    y_lo, y_hi = y_lo - pad, y_hi + pad
    x_span = (y_hi - y_lo) * W / H
    x_mid = (cx.min() + cx.max()) / 2
    ax.set_xlim(x_mid - x_span * 0.21, x_mid + x_span * 0.79)
    ax.set_ylim(y_lo, y_hi)
    ax.axis("off")
    # Dim the map behind the text (soft, left-to-right, no edge) so the busy coastline doesn't fight the
    # smaller lines; the course side stays untouched.
    scrim = fig.add_axes([0, 0, 1, 1])
    ramp = np.zeros((1, 512, 4))
    ramp[..., :3] = matplotlib.colors.to_rgb(BG)
    t = np.clip((np.linspace(0, 1, 512) - 0.30) / 0.22, 0, 1)
    ramp[..., 3] = 0.55 * t * t * (3 - 2 * t)  # smoothstep from x=30% to 52%, then a steady 55%
    scrim.imshow(ramp, aspect="auto", extent=(0, 1, 0, 1))
    scrim.axis("off")

    # Right: the question, then the answer.
    x0 = 0.38
    fig.text(x0, 0.86, f"NYC MARATHON · {nyc['n_turns']} TURNS", family=SANS, weight="bold", size=20, color=OUTER)
    fig.text(x0, 0.725, "What if you took", family=SANS, weight="bold", size=48, color=TEXT)
    every = fig.text(x0, 0.6, "every", family=SANS, weight="bold", size=48, color=OUTER)
    fig.text(x0 + text_width(fig, every) + 0.014, 0.6, "turn wide?", family=SANS, weight="bold", size=48,
             color=TEXT)
    fig.text(x0, 0.30, f"+{extra_mi:.2f} mi", family=SANS, weight="bold", size=96, color=OUTER)
    fig.text(x0, 0.19, "extra if you swing wide around every corner", family=SANS,
             weight="demibold", size=21, color=MUTED)
    fig.text(x0, 0.10, f"A 26.2-mile race becomes {certified_mi + extra_mi:.2f} miles", family=SANS, weight="bold", size=24, color=TEXT)

    fig.savefig(OUT, dpi=DPI * SCALE, facecolor=BG)
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
