"""Draw the 1200x627 link-preview image (Open Graph) from the site's own data: web/public/preview.png."""

import json

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from lion import ROOT  # noqa: E402

DATA = ROOT / "web/public/data"
OUT = ROOT / "web/public/preview.png"
W, H, DPI = 1200, 627, 100
BG, TEXT, MUTED, LINE = "#0d0f14", "#e9ecf2", "#8a93a6", "#262b36"
OUTER, COURSE = "#ff6a3d", "#f4f1ea"
SANS, MONO = "Avenir Next", "Menlo"
SHORT = {"nyc": "NYC", "capetown": "Cape Town"}


def text_width(fig, t):
    fig.canvas.draw()
    return t.get_window_extent().width / W


def main():
    course = json.loads((DATA / "nyc/course.geojson").read_text())
    turns = json.loads((DATA / "nyc/turns.geojson").read_text())
    cities = json.loads((DATA / "cities.json").read_text())

    fig = plt.figure(figsize=(W / DPI, H / DPI), dpi=DPI, facecolor=BG)

    # Left: the NYC course, glowing, with every turn sized by what it costs.
    ax = fig.add_axes([0.02, 0.06, 0.36, 0.88], facecolor=BG)
    lat0 = np.radians(40.72)
    for f in course["features"]:
        xy = np.array(f["geometry"]["coordinates"])
        x, y = xy[:, 0] * np.cos(lat0), xy[:, 1]
        ax.plot(x, y, color=OUTER, lw=9, alpha=0.18, solid_capstyle="round")
        ax.plot(x, y, color=COURSE, lw=1.8, solid_capstyle="round")
    pts = np.array([f["geometry"]["coordinates"] for f in turns["features"]])
    extra = np.array([f["properties"]["extra_arc_ft"] for f in turns["features"]])
    ax.scatter(pts[:, 0] * np.cos(lat0), pts[:, 1], s=extra * 1.1, color=OUTER, alpha=0.85,
               edgecolors=BG, linewidths=0.8, zorder=3)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.text(0.035, 0.035, "The NYC course · every dot is a turn", family=SANS, weight="medium", size=12, color=MUTED)

    # Right: headline + the eight Majors.
    x0 = 0.43
    fig.text(x0, 0.875, "THE WORLD MARATHON MAJORS", family=MONO, weight="bold", size=13, color=OUTER)
    fig.text(x0, 0.75, "What if you took", family=SANS, weight="bold", size=40, color=TEXT)
    every = fig.text(x0, 0.645, "every", family=SANS, weight="bold", size=40, color=OUTER)
    fig.text(x0 + text_width(fig, every) + 0.012, 0.645, "turn wide?", family=SANS, weight="bold", size=40,
             color=TEXT)
    fig.text(x0, 0.575, "Extra miles from swinging around the outside of every corner",
             family=SANS, weight="medium", size=15, color=MUTED)

    rows = sorted(cities, key=lambda c: -c["extra_arc_ft"])
    top, step, bar_h = 0.50, 0.054, 0.032
    label_w, bar_x, bar_max = 0.10, x0 + 0.105, 0.33
    vmax = max(c["extra_arc_ft"] for c in rows)
    for i, c in enumerate(rows):
        y = top - i * step
        nyc = c["slug"] == "nyc"
        miles = c["extra_arc_ft"] / 5280
        w = bar_max * c["extra_arc_ft"] / vmax
        fig.text(bar_x - 0.012, y + bar_h / 2, SHORT.get(c["slug"], c["city"]), ha="right", va="center",
                 family=SANS, weight="bold" if nyc else "medium", size=14, color=TEXT if nyc else MUTED)
        fig.patches.append(matplotlib.patches.FancyBboxPatch(
            (bar_x, y), w, bar_h, boxstyle="round,pad=0,rounding_size=0.004", transform=fig.transFigure,
            facecolor=OUTER, alpha=1 if nyc else 0.45, edgecolor="none"))
        fig.text(bar_x + w + 0.01, y + bar_h / 2, f"+{miles:.2f} mi", va="center", family=MONO,
                 weight="bold", size=13, color=OUTER if nyc else MUTED)

    fig.text(x0, 0.035, "dkraushar.github.io/marathon-turns", family=MONO, size=12, color=MUTED)
    fig.text(0.975, 0.035, "Dana Kraushar", ha="right", family=SANS, weight="demibold", size=13, color=TEXT)
    fig.savefig(OUT, dpi=DPI, facecolor=BG)
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
