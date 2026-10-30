"""Draw the link-preview image (1200x627 layout, saved at 2x): the NYC course and its headline number (Open Graph) from the site's own data: web/public/preview.png."""

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
BG, TEXT, MUTED = "#0d0f14", "#e9ecf2", "#b3bacb"
OUTER, COURSE = "#ff6a3d", "#f4f1ea"
SANS = "Avenir Next"


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

    # Left: the NYC course, glowing, with every turn sized by what it costs.
    ax = fig.add_axes([0.03, 0.05, 0.36, 0.90], facecolor=BG)
    lat0 = np.radians(40.72)
    for f in course["features"]:
        xy = np.array(f["geometry"]["coordinates"])
        x, y = xy[:, 0] * np.cos(lat0), xy[:, 1]
        ax.plot(x, y, color=OUTER, lw=16, alpha=0.2, solid_capstyle="round")
        ax.plot(x, y, color=COURSE, lw=3.5, solid_capstyle="round")
    pts = np.array([f["geometry"]["coordinates"] for f in turns["features"]])
    extra = np.array([f["properties"]["extra_arc_ft"] for f in turns["features"]])
    ax.scatter(pts[:, 0] * np.cos(lat0), pts[:, 1], s=extra * 2.4, color=OUTER, alpha=0.9,
               edgecolors=BG, linewidths=1.2, zorder=3)
    ax.set_aspect("equal")
    ax.axis("off")

    # Right: the question, then the answer.
    x0 = 0.43
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
