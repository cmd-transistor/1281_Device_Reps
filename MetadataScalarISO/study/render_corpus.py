"""Render a sample of cached Atlantis rows per category and report which ones produce no preview."""

import json
import sys
from collections import defaultdict

from iso_structure_ai import StructureDesigner
from pft_layout import is_pft_row
from template_layouts import template_kind

data = json.load(open("extracted/category_rows.json"))
only = sys.argv[1:]
summary = defaultdict(list)
for test_row, item in data.items():
    if only and not any(token in test_row for token in only):
        continue
    rows = item["rows"]
    if not rows:
        continue
    fields = list(dict.fromkeys(key for row in rows for key in row))
    designer = StructureDesigner(use_llm=False, source=(fields, rows))
    picks = sorted({0, len(rows) // 2, len(rows) - 1})
    for index in picks:
        row = designer.library_rows[index]
        try:
            design = designer.open_library_row(row)
            png = designer.layout_png(design.id, panel=True)
            kind = template_kind(design.row) or ("PFT" if is_pft_row(design.row) else "ISO")
            failed = [w for w in design.warnings if "fail" in w.lower() and "fail modes" not in w.lower()]
            summary[test_row].append((row["StructureName"], kind, png.name if png else None, failed[-1:] if failed else []))
        except Exception as exc:  # report and continue
            summary[test_row].append((row.get("StructureName"), "EXC", None, [repr(exc)[:200]]))
for test_row, results in summary.items():
    library = data[test_row]["rows"][0].get("AtlantisLibrary", "")
    for name, kind, png, failed in results:
        flag = "OK " if png else "NO "
        print(f"{flag}{library[:12]:12} {test_row:28} {kind:8} {name[:58]:58} {png or ''} {failed}")
