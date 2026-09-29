import collections
import csv
import sys

v = list(csv.DictReader(open("ISO_Scalar_Variants.csv", newline="", encoding="utf-8-sig")))
print(collections.Counter(r["Family"] for r in v))
fam = sys.argv[1] if len(sys.argv) > 1 else None
for r in v:
    if r["Family"] == "VIA_CHAIN" or (fam and r["Family"] != fam):
        continue
    print(f"{r['Snapshot']:16s} {r['Rows']:>4} {r['FailModes']:>6}  {r['CellHeights']:18s} "
          f"{r['Variant']:58s} {r['Notes'][:110]}")
