"""Print X positions of FTI / POLY / TCN / VG / VT columns in one chassis file (study aid)."""
import re
import sys
from collections import Counter

path = sys.argv[1]
want = {184: "FTI", 2: "POLY", 5: "TCN", 32: "VG", 31: "VT"}
xs = {v: Counter() for v in want.values()}
ys = {v: Counter() for v in want.values()}
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
            if layer in want and pts:
                x0 = min(p[0] for p in pts); x1 = max(p[0] for p in pts)
                y0 = min(p[1] for p in pts); y1 = max(p[1] for p in pts)
                xs[want[layer]][(x0, x1)] += 1
                ys[want[layer]][(y0, y1)] += 1
        elif inb:
            for m in re.finditer(r"(-?\d+)\s*:\s*(-?\d+)", s):
                pts.append((int(m.group(1)), int(m.group(2))))

for name in want.values():
    col = sorted(xs[name])
    print(f"\n{name}: {len(col)} distinct X spans; first 30:")
    print("  ", [(a, b, xs[name][(a, b)]) for a, b in col[:30]])
    if name in ("VG", "VT"):
        yy = sorted(ys[name])
        print(f"  {name} Y spans (first 20):", yy[:20])
