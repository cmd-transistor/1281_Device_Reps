"""Enumerate distinct structure variants in ISO_Metadata_Input.csv (study aid).

A variant = (family, TYPE letter, name suffix pattern, geometry signature) where
the geometry signature is the tuple of buildsheet columns that drive the scalar.
"""
import csv
import re
import sys
from collections import OrderedDict

sys.path.insert(0, ".")
from ScalarCalculation import compute, family_of, type_of  # noqa: E402

SIG_COLS = ["UnitCellSizeOgd", "UnitCellSizePgd", "ArraySizeOgd", "ArraySizePgd",
            "BufferSizeL", "BufferSizeR", "BufferSizeB", "BufferSizeT",
            "FTITracksUnit", "FTITracksGlobal", "PolyPlugsUnit", "TcnPlugsUnit", "TcnPlugsGlobal",
            "TcnCutsUnit", "TcnDepopUnit", "VcrPatternUnit", "VcrnubPatternUnit", "TcnExtensionUnit",
            "M0CutPatternUnit", "VcxPatternUnit", "LO1ActiveTracks", "HI1ActiveTracks",
            "DrawMiscIDsUnit"]


def norm_val(c: str, v: str) -> str:
    v = re.sub(r"\s+", "", v)
    if c in ("VcrPatternUnit", "VcrnubPatternUnit", "VcxPatternUnit", "DrawMiscIDsUnit"):
        v = re.sub(r"\|[-0-9.,]+", "|<shift>", v)              # skew magnitudes
    return v


def generic_name(name: str) -> str:
    """Strip per-row numeric detail (cell height, Z, VT letter, width, skew value) to a pattern."""
    n = re.sub(r"^([A-Z]+)\d+", r"\1<CH>", name)                   # CTG110 -> CTG<CH>
    n = re.sub(r"-(NP|N|P)(Z\d+|\d+)-(UL|U|L|S|H|E)-", r"-<MOS><Z>-<VT>-", n)
    n = re.sub(r":(N|P)(\d+)$", r":<SKEW>", n)                      # :N4 / :P8
    n = re.sub(r"(?<![A-Za-z])(N|P)(\d+)$", r"<SKEW>", n)
    n = re.sub(r"LEN\d+", "LEN<n>", n)
    n = re.sub(r"_\d+_(N|P)_\d+CH$", "_<i>_<mos>_<CH>", n)
    return n


rows = list(csv.DictReader(open("ISO_Metadata_Input.csv", newline="", encoding="utf-8-sig")))
by_fam: "OrderedDict[str, OrderedDict[tuple, dict]]" = OrderedDict()
for r in rows:
    name = r["StructureName"]
    fam = family_of(name)
    key = (type_of(name), generic_name(name), tuple(norm_val(c, r.get(c, "")) for c in SIG_COLS))
    d = by_fam.setdefault(fam, OrderedDict())
    if key not in d:
        res = compute(r)
        d[key] = {"n": 0, "example": name, "comment": r.get("StructureComments", ""),
                  "testrows": set(), "fail": res.fail_modes, "notes": res.notes, "row": r}
    d[key]["n"] += 1
    d[key]["testrows"].add(r["TestRow"])

out = open("study/variants.txt", "w", encoding="utf-8")
for fam, d in by_fam.items():
    print(f"\n{'#' * 100}\n# {fam}: {len(d)} distinct variants, {sum(v['n'] for v in d.values())} rows", file=out)
    prev_sig = None
    for (typ, gname, sig), v in d.items():
        print(f"\n== [{typ}] {gname}   rows={v['n']}  testrows={sorted(v['testrows'])}", file=out)
        print(f"   example : {v['example']}", file=out)
        print(f"   comment : {v['comment']}", file=out)
        print(f"   FailModes={v['fail']}  notes={v['notes']}", file=out)
        for c, val in zip(SIG_COLS, sig):
            if val:
                print(f"   {c:20s}= {val}", file=out)
out.close()
print("families:", {f: len(d) for f, d in by_fam.items()})
