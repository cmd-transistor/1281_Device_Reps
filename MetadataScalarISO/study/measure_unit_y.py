"""Measure Y layout of one Pgd unit in a chassis GDS (diff rows, poly/TCN segments, VG/VT) — study aid."""
import re
import sys
from collections import Counter, defaultdict

path = sys.argv[1]
xcol = int(sys.argv[2])          # poly column index to inspect (gate column)
ycell = int(sys.argv[3]) if len(sys.argv) > 3 else 660
names = {1: "NDIFF", 8: "PDIFF", 2: "POLY", 5: "TCN", 32: "VG", 31: "VT", 184: "FTI"}
xl = 160 + xcol * 440
want_x = {"POLY": (xl, xl + 120), "VG": (xl, xl + 120), "TCN": (xl + 220, xl + 340), "VT": (xl + 220, xl + 340),
          "FTI": (xl - 440 * (xcol % 4), xl - 440 * (xcol % 4) + 120)}
ys = defaultdict(list)
layer = None
pts = []
inb = False
with open(path, encoding="utf-8", errors="replace") as f:
    for line in f:
        s = line.strip()
        if s.startswith("BOUNDARY"):
            inb, pts, layer = True, [], None
        elif inb and s.startswith("LAYER"):
            layer = int(s.split()[1])
        elif inb and s.startswith("ENDEL"):
            inb = False
            if layer in names and pts:
                x0 = min(p[0] for p in pts); x1 = max(p[0] for p in pts)
                y0 = min(p[1] for p in pts); y1 = max(p[1] for p in pts)
                n = names[layer]
                if n in ("NDIFF", "PDIFF"):
                    if x0 <= xl + 500 and x1 >= xl - 500:
                        ys[n].append((y0, y1))
                elif want_x[n] == (x0, x1):
                    ys[n].append((y0, y1))
        elif inb:
            for m in re.finditer(r"(-?\d+)\s*:\s*(-?\d+)", s):
                pts.append((int(m.group(1)), int(m.group(2))))

lo, hi = 2 * 5280, 3 * 5280   # look at the third Pgd unit (well inside the array)
print(f"window y=[{lo},{hi}] cell={ycell}")
for n in ("FTI", "NDIFF", "PDIFF", "POLY", "TCN", "VG", "VT"):
    segs = sorted({(a, b) for a, b in ys[n] if b > lo - 100 and a < hi + 100})
    print(f"{n:6s}", [(a, b, f"cell {(a - lo) / ycell:.2f}-{(b - lo) / ycell:.2f}") for a, b in segs])
