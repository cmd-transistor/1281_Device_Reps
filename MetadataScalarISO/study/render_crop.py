"""Quick-look renderer for a crop of an ISO chassis GDS ASCII file (study aid)."""
import re
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

LAYERS = {
    (1, 0): ("NDIFF", "#7fbf7f", 0.6),
    (8, 0): ("PDIFF", "#f4a582", 0.6),
    (184, 0): ("FTI", "#555555", 0.9),
    (2, 0): ("POLY", "#d62728", 0.55),
    (5, 0): ("TCN", "#1f77b4", 0.55),
    (32, 0): ("VG", "#ff00ff", 1.0),
    (31, 0): ("VT", "#00cccc", 1.0),
    (120, 0): ("M0C1", "#999900", 0.25),
    (241, 0): ("M0C2", "#009999", 0.25),
}


def parse(path, xr, yr):
    polys = []
    layer = dt = None
    pts = []
    inb = False
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if s.startswith("BOUNDARY"):
                inb, pts, layer, dt = True, [], None, None
            elif inb and s.startswith("LAYER"):
                layer = int(s.split()[1])
            elif inb and s.startswith("DATATYPE"):
                dt = int(s.split()[1])
            elif inb and s.startswith("ENDEL"):
                inb = False
                if (layer, dt) in LAYERS and pts:
                    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
                    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
                    if x1 >= xr[0] and x0 <= xr[1] and y1 >= yr[0] and y0 <= yr[1]:
                        polys.append(((layer, dt), x0, y0, x1, y1))
            elif inb:
                for m in re.finditer(r"(-?\d+)\s*:\s*(-?\d+)", s):
                    pts.append((int(m.group(1)), int(m.group(2))))
    return polys


def main():
    path = Path(sys.argv[1])
    x0, x1, y0, y1 = (int(v) for v in sys.argv[2:6])
    out = sys.argv[6] if len(sys.argv) > 6 else path.stem + "_crop.png"
    polys = parse(path, (x0, x1), (y0, y1))
    fig, ax = plt.subplots(figsize=(14, 10))
    order = [(1, 0), (8, 0), (184, 0), (2, 0), (5, 0), (120, 0), (241, 0), (32, 0), (31, 0)]
    for key in order:
        name, color, alpha = LAYERS[key]
        for k, a, b, c, d in polys:
            if k == key:
                ax.add_patch(Rectangle((a, b), c - a, d - b, facecolor=color, alpha=alpha,
                                       edgecolor="k", linewidth=0.3))
    for key in order:
        name, color, alpha = LAYERS[key]
        ax.plot([], [], color=color, lw=6, label=name)
    ax.set_xlim(x0, x1); ax.set_ylim(y0, y1); ax.set_aspect("equal")
    ax.legend(loc="upper right", fontsize=8)
    ax.set_title(f"{path.name}  x[{x0},{x1}] y[{y0},{y1}] (db = 0.1nm)")
    ax.grid(True, alpha=0.2)
    fig.tight_layout(); fig.savefig(out, dpi=130)
    print("wrote", out, "polys:", len(polys))


if __name__ == "__main__":
    main()
