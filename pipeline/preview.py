"""Draw the link-preview image (1200x627 layout, saved at 2x) (Open Graph) from the site's own data: web/public/preview.png."""

import json

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from lion import ROOT  # noqa: E402

DATA = ROOT / "web/public/data"
OUT = ROOT / "web/public/preview.png"
W, H, DPI = 1200, 627, 100
SCALE = 2  # save at 2400x1254 so it stays sharp on high-resolution screens (same layout)
BG, TEXT = "#0d0f14", "#e9ecf2"
OUTER, COURSE = "#ff6a3d", "#f4f1ea"
SANS = "Avenir Next"
SHORT = {"nyc": "NYC", "capetown": "Cape Town"}


def text_width(fig, t):
    fig.canvas.draw()
    return t.get_window_extent().width / W


def main():
    course = json.loads((DATA / "nyc/course.geojson").read_text())
    turns = json.loads((DATA / "nyc/turns.geojson").read_text())
    cities = json.loads((DATA / "cities.json").read_text())

    fig = plt.figure(figsize=(W / DPI, H / DPI), dpi=DPI, facecolor=BG)

    # LinkedIn shows this card ~520 px wide (less than half size) and re-compresses it, so everything is
    # sized to survive that: no text under ~19 pt here (≈11 px on the card), bold weights, no fine print.

    # Left: the NYC course, glowing, with every turn sized by what it costs.
    ax = fig.add_axes([0.03, 0.05, 0.34, 0.90], facecolor=BG)
    lat0 = np.radians(40.72)
    for f in course["features"]:
        xy = np.array(f["geometry"]["coordinates"])
        x, y = xy[:, 0] * np.cos(lat0), xy[:, 1]
        ax.plot(x, y, color=OUTER, lw=16, alpha=0.2, solid_capstyle="round")
        ax.plot(x, y, color=COURSE, lw=3.5, solid_capstyle="round")
    pts = np.array([f["geometry"]["coordinates"] for f in turns["features"]])
    extra = np.array([f["properties"]["extra_arc_ft"] for f in turns["features"]])
    ax.scatter(pts[:, 0] * np.cos(lat0), pts[:, 1], s=extra * 2.2, color=OUTER, alpha=0.9,
               edgecolors=BG, linewidths=1.2, zorder=3)
    ax.set_aspect("equal")
    ax.axis("off")

    # Right: headline + the eight Majors in miles.
    x0 = 0.42
    fig.text(x0, 0.88, "EXTRA MILES AT THE 8 MARATHON MAJORS", family=SANS, weight="bold", size=19, color=OUTER)
    fig.text(x0, 0.75, "What if you took", family=SANS, weight="bold", size=46, color=TEXT)
    every = fig.text(x0, 0.63, "every", family=SANS, weight="bold", size=46, color=OUTER)
    fig.text(x0 + text_width(fig, every) + 0.014, 0.63, "turn wide?", family=SANS, weight="bold", size=46,
             color=TEXT)

    rows = sorted(cities, key=lambda c: -c["extra_arc_ft"])
    top, step, bar_h = 0.505, 0.063, 0.042
    bar_x, bar_max = x0 + 0.135, 0.30
    vmax = max(c["extra_arc_ft"] for c in rows)
    for i, c in enumerate(rows):
        y = top - i * step
        nyc = c["slug"] == "nyc"
        w = bar_max * c["extra_arc_ft"] / vmax
        fig.text(bar_x - 0.014, y + bar_h / 2, SHORT.get(c["slug"], c["city"]), ha="right", va="center",
                 family=SANS, weight="bold" if nyc else "demibold", size=19, color=TEXT if nyc else "#b3bacb")
        fig.patches.append(matplotlib.patches.FancyBboxPatch(
            (bar_x, y), w, bar_h, boxstyle="round,pad=0,rounding_size=0.005", transform=fig.transFigure,
            facecolor=OUTER, alpha=1 if nyc else 0.5, edgecolor="none"))
        fig.text(bar_x + w + 0.012, y + bar_h / 2, f"+{c['extra_arc_ft'] / 5280:.2f} mi", va="center", family=SANS,
                 weight="bold", size=19, color=OUTER if nyc else TEXT)

    fig.savefig(OUT, dpi=DPI * SCALE, facecolor=BG)
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
