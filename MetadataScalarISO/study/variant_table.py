"""Compact per-family variant table: only columns that differ within the family are shown."""
import csv
import re
import sys
from collections import OrderedDict

sys.path.insert(0, ".")
sys.path.insert(0, "study")
from list_variants import SIG_COLS, generic_name, norm_val  # noqa: E402
from ScalarCalculation import compute, family_of, type_of  # noqa: E402

fam_filter = sys.argv[1] if len(sys.argv) > 1 else None
rows = list(csv.DictReader(open("ISO_Metadata_Input.csv", newline="", encoding="utf-8-sig")))
by_fam = OrderedDict()
for r in rows:
    name = r["StructureName"]
    fam = family_of(name)
    if fam_filter and fam != fam_filter:
        continue
    sig = tuple(norm_val(c, r.get(c, "")) for c in SIG_COLS)
    key = (type_of(name), generic_name(name), sig)
    d = by_fam.setdefault(fam, OrderedDict())
    if key not in d:
        res = compute(r)
        d[key] = {"n": 0, "ex": name, "cm": r.get("StructureComments", ""), "fail": res.fail_modes,
                  "notes": "; ".join(res.notes)}
    d[key]["n"] += 1

for fam, d in by_fam.items():
    keys = list(d)
    diff_cols = [i for i, c in enumerate(SIG_COLS) if len({k[2][i] for k in keys}) > 1]
    print(f"\n{'#' * 110}\n# {fam}: {len(d)} variants; differing columns: {[SIG_COLS[i] for i in diff_cols]}")
    for (typ, gname, sig), v in d.items():
        print(f"\n[{typ}] {gname}  rows={v['n']}  FailModes={v['fail']}")
        print(f"    ex: {v['ex']} | {v['cm'][:110]}")
        if v["notes"]:
            print(f"    notes: {v['notes'][:150]}")
        for i in diff_cols:
            if sig[i]:
                print(f"    {SIG_COLS[i]:18s}= {sig[i][:150]}")
