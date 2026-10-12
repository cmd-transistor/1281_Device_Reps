"""Desktop browser for the existing 81Z ISO structure library."""

from __future__ import annotations

import csv
import difflib
import io
import json
import math
import queue
import re
import threading
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from iso_structure_ai import Design, StructureDesigner, _name_type, parse_prompt
from ScalarCalculation import (FAMILY_PREFIXES, expand_axis, family_of, geometry_from_row, int_list,
                               m0_tracks_per_height, mos_of, segments, strip_shift)

LAYERS = (
    "NDIFF", "PDIFF", "FTI", "PLY", "TCN", "TCP", "VG", "VT", "VCR", "PXL / TRM",
    "M0 lines", "M0 LO1", "M0 HI1", "M0 cuts", "V0 landings", "Current flow", "Fail paths", "Grid",
    "M0 Y grid", "Guide tracks",
)
LAYERS_OFF_BY_DEFAULT = frozenset({"PXL / TRM", "M0 lines", "M0 cuts", "Fail paths", "Grid", "M0 Y grid",
                                   "Guide tracks"})
READ_ONLY_FIELDS = frozenset({"StructureName", "TestRow", "Scalar", "ScalarFormula", "Force", "Test Type"})
PREVIEW_WIDTH_FIELDS = ("PreviewPlyWidthNm", "PreviewTcnWidthNm", "PreviewVcrWidthNm", "PreviewM0WidthNm")
FIELD_SECTIONS = (
    ("Identity & results", r"^(TestRow|StructureName|StructureComments|Group|DesignContext|Scalar|ScalarFormula|Force|Test Type)$"),
    ("Unit cell & array", r"^(CellHeight|PolyPitch|UnitCellSize|ArraySize|BufferSize|kernel|zone|orient)"),
    ("Layer patterns", r"(PatternUnit|PatternGlobal|TracksUnit|TracksGlobal|PlugsUnit|PlugsGlobal|ExtensionUnit|DrawMiscIDs)"),
    ("Skews & TSDR", r"(Skew|ResizeShift|TSDR)"),
    ("LO1 / HI1 terminals", r"^(LO1|HI1|FRT_|PAD_|skipPins|frtMethod)"),
    ("Preview widths", r"^Preview"),
)
TSDR_FIELDS = {"fti": "FTICdMisregSkew", "tcn": "TCNCdRegSkew", "poly": "PolyCdSkew", "ply": "PolyCdSkew",
               "glk": "GLKSkew", "pxl": "PXLSkew", "trm": "PXLSkew", "vcrnub": "VcrnubPatternUnit",
               "vcr": "VcrPatternUnit", "tcp": "TCPSkewPatternUnit"}
TSDR_SLOTS = {("OGD", "CD"): 0, ("PGD", "CD"): 1, ("OGD", "REG"): 2, ("PGD", "REG"): 3}
TSDR_HINT = (
    "Describe a TSDR skew or buildsheet change in plain words, then press Enter or Apply.\n"
    "TSDR skews (unit coordinates):\n"
    "  fti ogd cd -3nm             FTICdMisregSkew, OGD CD slot\n"
    "  tcn pgd reg +2              TCNCdRegSkew, PGD REG slot\n"
    "  poly ogd cd n2              n = negative, p = positive; nm by default (um allowed)\n"
    "  glk pgd cd 4  |  pxl cd p5  |  pxl reg n3  |  vcr pgd cd -4  |  tcp pgd cd 3\n"
    "  id tcn TSDRActiveL x 0:50 y 2:5   adds a DrawMiscIDsUnit box\n"
    "  reset fti                   clears that skew\n"
    "Structure:\n"
    "  buffer L2 R2 B1 T1  |  buffer 3  |  buffer left 6\n"
    "  array 5x60  |  unit 24x4  |  cell height 165\n"
    "  lo 6;14  |  hi 9 11       LO1/HI1ActiveTracks (unit M0 tracks)\n"
    "Patterns (global = global coordinates, overwrites the unit cell; unit = unit coordinates):\n"
    "  global diff 0:r0,0/r0,0.046,nlvt   global fti 0/2/r2/r0,0:r0   global tcn 0:3,3:r3[1]\n"
    "  global poly 0:3,2/4   global vt 1:3,8   global vg 2,5   global m0cut 2:r2[5],1/3\n"
    "  global vcr 0:r0,6   global vcrnub 3,6,top   global id DBI_mask,drawing,4:r4,3:r3\n"
    "  unit fti 3/6,2:3   (same layer words, appended to the *Unit field)\n"
    "Any field:  set FIELD = value   |   append FIELD += segment\n"
    "Repetition (keep / skip / repeat, track numbers as in the buildsheet, 0-based):\n"
    "  tcn plug starting from 4th track, keep for 3 more, skip 1, repeat     -> TcnPlugsUnit += 4:7/9:12/...,<plugs>\n"
    "  global poly plug start 0 keep 2 skip 2 repeat y: 2/4\n"
    "  fti start 3 keep 1 skip 2 repeat 4 times y: start 2 keep 2 skip 2 repeat\n"
    "  vt start 1 keep 2 skip 2 repeat until 22 y: 14     (y = M0 track for vt/vg)\n"
    "  add 'replace' to overwrite the field instead of appending; Y defaults to the layer's existing Y\n"
    "Separate requests with '; '. Skew vector slots: OGD CD, PGD CD, OGD REG, PGD REG (um)."
)
PATTERN_WORDS = {"diff": "DiffPattern", "diffusion": "DiffPattern", "fti": "FTITracks", "poly": "PolyPlugs",
                 "ply": "PolyPlugs", "tcn": "TcnPlugs", "tcndepop": "TcnDepop", "depop": "TcnDepop",
                 "vt": "VcxPattern", "vg": "VcxPattern", "vcr": "VcrPattern", "vcrnub": "VcrnubPattern",
                 "tcnext": "TcnExtension", "m0cut": "M0CutPattern", "id": "DrawMiscIDs"}


def _run_pattern(text: str, extent: int) -> tuple[str, str] | None:
    """'start 4 keep 3 more skip 1 repeat' -> ('4:7/9:12/...', description); None if no keep/skip/repeat."""
    words = text.lower()
    if not re.search(r"\b(keep|skip|repeat\w*|repetition)\b", words):
        return None
    ordinal = r"(\d+)(?:st|nd|rd|th)?"
    start = re.search(rf"\b(?:start\w*|from|begin\w*|at)\s+(?:from\s+|at\s+)?(?:the\s+)?(?:track\s+)?{ordinal}", words)
    keep = re.search(rf"\bkeep\w*\s+(?:for\s+)?(?:it\s+)?{ordinal}\s*(more|additional|extra)?", words)
    skip = re.search(rf"\bskip\w*\s+(?:for\s+)?(?:every\s+)?{ordinal}", words)
    end = re.search(rf"\b(?:until|up\s*to|to|till|end\w*\s+at)\s+(?:track\s+)?{ordinal}", words)
    times = re.search(r"\brepeat\w*\s+(\d+)\s*(?:x|times)\b|\b(\d+)\s*times\b", words)
    first = int(start[1]) if start else 0
    run = (int(keep[1]) + (1 if keep[2] else 0)) if keep else 1
    gap = int(skip[1]) if skip else 0
    last = min(extent, int(end[1])) if end else extent
    repeat = bool(re.search(r"\brepe(?:at|tition)", words)) or bool(skip)
    count = int(times[1] or times[2]) if times else (10 ** 6 if repeat else 1)
    if run < 1 or first > last:
        raise ValueError(f"'{text}': nothing to draw (start {first}, keep {run}, end {last}).")
    runs, position = [], first
    while position <= last and len(runs) < count:
        runs.append((position, min(last, position + run - 1)))
        position += run + gap
    if run == 1 and len(runs) > 1:
        spec = f"{runs[0][0]}:{runs[-1][0]}[{gap}]"
    else:
        spec = "/".join(f"{a}:{b}" if b > a else str(a) for a, b in runs)
    return spec, f"start {first}, keep {run}, skip {gap}, {len(runs)} run(s) to {last}"


PATTERN_LAYER_FIELDS = (
    (r"tcn\s*plugs?|tcn", "TcnPlugs", "slot"), (r"(?:poly|ply|gate)\s*plugs?|poly|ply", "PolyPlugs", "poly"),
    (r"fti", "FTITracks", "poly"), (r"tcp", "TCPSkewPattern", "slot"), (r"m0\s*cuts?", "M0CutPattern", "half"),
    (r"\bvt\b", "VcxPattern", "slot"), (r"\bvg\b", "VcxPattern", "poly"),
)


def _pattern_request(clause: str, row: dict[str, str], changes: dict[str, str]) -> str:
    """Natural-language repetition: '[global|unit] <layer> [x:] start N keep K [more] skip S repeat [y: ...]'."""
    words = clause.lower()
    scope = "Global" if re.search(r"\bglobal\b", words) else "Unit"
    match = next(((base, axis, m) for pattern, base, axis in PATTERN_LAYER_FIELDS
                  if (m := re.search(pattern, words))), None)
    if match is None:
        raise ValueError(f"'{clause}': name the layer (tcn plug, poly plug, fti, tcp, m0 cut, vt or vg).")
    base, axis, layer_match = match
    field = base + scope
    if field not in row:
        raise ValueError(f"{field} is not a buildsheet column.")
    g = geometry_from_row({**row, **changes})
    per_height = m0_tracks_per_height({**row, **changes})
    if scope == "Unit":
        x_extent = {"slot": g.ogd - 1, "poly": g.ogd, "half": 2 * g.ogd}[axis]
        y_extent = per_height * g.pgd if base in ("VcxPattern", "M0CutPattern") else g.n_cells
    else:
        width = (int(row.get("BufferSizeL", 0) or 0) + g.arr_ogd * g.ogd + int(row.get("BufferSizeR", 0) or 0))
        cells = 2 * (int(row.get("BufferSizeB", 0) or 0) + g.arr_pgd * g.pgd + int(row.get("BufferSizeT", 0) or 0))
        x_extent = {"slot": width - 1, "poly": width, "half": 2 * width}[axis]
        y_extent = per_height * cells // 2 if base in ("VcxPattern", "M0CutPattern") else cells - 1
    body = clause[layer_match.end():]
    y_split = re.split(r"\b(?:y|cut\s*tracks?|plugs?\s*(?:at|on)|on\s*m0(?:\s*tracks?)?|rows?)\s*[:=]?\s*",
                       body, maxsplit=1, flags=re.IGNORECASE)
    x_text = re.sub(r"^\s*(?:x|tracks?)\s*[:=]?\s*", "", y_split[0], flags=re.IGNORECASE)
    y_text = y_split[1].strip() if len(y_split) > 1 else ""
    y_text = re.sub(r"\b(replace|only|instead)\b", "", y_text, flags=re.IGNORECASE).strip()
    x_decoded = _run_pattern(x_text, x_extent)
    y_decoded = _run_pattern(y_text, y_extent) if y_text else None
    if x_decoded is None and y_decoded is None:
        raise ValueError(f"'{clause}': describe keep / skip / repeat.")
    x_spec = x_decoded[0] if x_decoded else x_text.strip().strip(",")
    if y_decoded:
        y_spec = y_decoded[0]
    elif y_text:
        y_spec = y_text.strip().rstrip(";")
    else:
        existing = [seg for seg in segments(changes.get(field, row.get(field, "")) or
                                            row.get(base + "Unit", "")) if "," in seg]
        if base == "VcxPattern":
            kind = layer_match[0].strip()
            existing = [seg for seg in existing if seg.lower().startswith(kind)]
            y_spec = strip_shift(existing[0]).split(",")[2] if existing else ""
        else:
            y_spec = strip_shift(existing[0]).split(",", 1)[1] if existing else ""
        if not y_spec:
            raise ValueError(f"'{clause}': give the {'M0 track' if base == 'VcxPattern' else 'Y'} axis, "
                             "e.g. '... y: 2/4/6' or '... y: start 1 keep 2 skip 1 repeat'.")
    segment = f"{layer_match[0].strip()},{x_spec},{y_spec}" if base == "VcxPattern" else f"{x_spec},{y_spec}"
    replace = bool(re.search(r"\b(replace|only|instead)\b", words))
    existing_value = changes.get(field, row.get(field, "")) or ""
    changes[field] = f"{segment};" if replace or not existing_value.strip() else \
        f"{existing_value.strip().rstrip(';')};{segment};"
    described = "; ".join(d for d in (x_decoded and f"x {x_decoded[1]}", y_decoded and f"y {y_decoded[1]}") if d)
    return f"{field} {'=' if replace else '+='} {segment}  ({described})"


def _structure_edit(clause: str, row: dict[str, str], changes: dict[str, str]) -> str | None:
    """Buffer/array/terminal/pattern/field edits; returns a note, or None when the clause is not one."""
    words = clause.lower()
    if re.search(r"\b(keep|skip|repeat\w*|repetition)\b", words):
        return _pattern_request(clause, row, changes)

    def current(field: str) -> str:
        return changes.get(field, row.get(field, "") or "")

    match = re.match(r"set\s+(\w+)\s*=\s*(.*)$", clause, re.IGNORECASE)
    if match:
        field = next((name for name in row if name.lower() == match[1].lower()), None)
        if field is None:
            raise ValueError(f"Unknown field '{match[1]}'.")
        changes[field] = match[2].strip()
        return f"{field} = {changes[field]}"
    match = re.match(r"append\s+(\w+)\s*\+?=\s*(.*)$", clause, re.IGNORECASE)
    if match:
        field = next((name for name in row if name.lower() == match[1].lower()), None)
        if field is None:
            raise ValueError(f"Unknown field '{match[1]}'.")
        existing = current(field).strip().rstrip(";")
        changes[field] = f"{existing};{match[2].strip()};" if existing else f"{match[2].strip()};"
        return f"{field} += {match[2].strip()}"
    if words.startswith("buffer"):
        sides = {"l": "L", "left": "L", "r": "R", "right": "R", "b": "B", "bottom": "B", "t": "T", "top": "T"}
        pairs = re.findall(r"\b(left|right|bottom|top|[lrbt])\s*=?\s*(\d+)", words[6:])
        if not pairs and (number := re.search(r"\d+", words)):
            pairs = [(side, number[0]) for side in "lrbt"]
        if not pairs:
            raise ValueError("Buffer needs sizes, e.g. 'buffer L2 R2 B1 T1' or 'buffer 3'.")
        for side, value in pairs:
            changes[f"BufferSize{sides[side]}"] = value
        return "buffer " + " ".join(f"{sides[s]}{v}" for s, v in pairs)
    match = re.match(r"(array|unit)\s+(\d+)\s*[x*,\s]\s*(\d+)$", words)
    if match:
        prefix = "ArraySize" if match[1] == "array" else "UnitCellSize"
        changes[f"{prefix}Ogd"], changes[f"{prefix}Pgd"] = match[2], match[3]
        return f"{prefix} {match[2]} x {match[3]}"
    match = re.match(r"cell\s*height\s+(?:ch)?(\d+)$", words)
    if match:
        changes["CellHeight"] = f"ch{match[1]}"
        return f"CellHeight = ch{match[1]}"
    match = re.match(r"(lo|hi)1?\s+((?:\d+[\s;,/]*)+)$", words)
    if match:
        field = "LO1ActiveTracks" if match[1] == "lo" else "HI1ActiveTracks"
        changes[field] = ";".join(re.findall(r"\d+", match[2]))
        return f"{field} = {changes[field]}"
    match = re.match(r"(global|unit)\s+(\w+)\s+(.+)$", clause.strip(), re.IGNORECASE)
    if match and match[2].lower() in PATTERN_WORDS:
        word, segment = match[2].lower(), match[3].strip().rstrip(";")
        field = PATTERN_WORDS[word] + match[1].capitalize()
        if field not in row:
            raise ValueError(f"{field} is not a buildsheet column.")
        if word in ("vt", "vg"):
            segment = f"{word},{segment}"
        existing = current(field).strip().rstrip(";")
        changes[field] = f"{existing};{segment};" if existing else f"{segment};"
        return f"{field} += {segment}"
    return None


def _um(value_nm: float) -> str:
    return format(round(value_nm / 1000, 6), "g")


def _set_slot(vector: str, index: int, value_nm: float, length: int = 4) -> str:
    values = [part.strip() for part in vector.split(",")] if vector.strip() else []
    values += ["0"] * (length - len(values))
    values[index] = _um(value_nm)
    return ",".join(values)


def parse_tsdr_request(text: str, row: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Natural-language TSDR skew request -> {field: new value} plus a short description per change."""
    changes: dict[str, str] = {}
    notes: list[str] = []

    def current(field: str) -> str:
        return changes.get(field, row.get(field, "") or "")

    # Requests are separated by '; ' (with a space) so segment values such as '6;14' stay intact.
    for clause in (part.strip() for part in re.split(r";\s+|\s+;", text)):
        if not clause:
            continue
        words = clause.lower()
        note = _structure_edit(clause, row, changes)
        if note:
            notes.append(note)
            continue
        id_match = re.match(r"(?:add\s+|draw\s+)?id\s+(\w+)[\s:]+(\w+)\s+x\s+(\S+)\s+y\s+(\S+)$", clause, re.IGNORECASE)
        if id_match:
            segment = ",".join(id_match.groups())
            existing = current("DrawMiscIDsUnit").strip().rstrip(";")
            changes["DrawMiscIDsUnit"] = f"{existing};{segment};" if existing else f"{segment};"
            notes.append(f"DrawMiscIDsUnit += {segment}")
            continue
        layer = next((key for key in sorted(TSDR_FIELDS, key=len, reverse=True)
                      if re.search(rf"\b{key}\b", words)), None)
        if layer is None:
            raise ValueError(f"No layer in '{clause}'. Use fti, tcn, poly, glk, pxl, vcr, vcrnub, tcp or id.")
        field = TSDR_FIELDS[layer]
        pattern_field = field.endswith("PatternUnit")
        if re.search(r"\b(reset|clear|remove|nominal|nom)\b", words):
            if pattern_field:
                changes[field] = ";".join(seg.split("|")[0] for seg in current(field).split(";"))
            elif field == "PXLSkew":
                changes[field] = ""
            else:
                changes[field] = "0,0,0,0,Y" if field == "FTICdMisregSkew" else "0,0,0,0"
            notes.append(f"{field} reset")
            continue
        variant = "REG" if re.search(r"\b(reg|misreg|shift|misalign\w*)\b", words) else \
            "CD" if re.search(r"\b(cd|width|bias|size)\b", words) else None
        direction = "PGD" if re.search(r"\bpgd\b", words) else "OGD" if re.search(r"\bogd\b", words) else None
        number = re.search(r"(?:^|\s)([+-]|[np](?=\d))?\s*(\d+(?:\.\d+)?)\s*(nm|um)?(?:\b|$)",
                           re.sub(rf"\b{layer}\b", " ", words))
        if variant is None or number is None:
            raise ValueError(f"'{clause}': give CD or REG and a value, e.g. '{layer} pgd cd -3nm'.")
        value_nm = float(number[2]) * (1000 if number[3] == "um" else 1) * (-1 if number[1] in ("-", "n") else 1)
        if field == "PXLSkew":
            parts = [p.strip() for p in current(field).split(",")] if current(field) else []
            parts += ["0.014", "0", "all"][len(parts):]
            parts[0 if variant == "CD" else 1] = _um(14 + value_nm) if variant == "CD" else _um(value_nm)
            changes[field] = ",".join(parts[:3])
            notes.append(f"PXLSkew {variant} {value_nm:+g} nm -> {changes[field]}")
            continue
        if direction is None:
            raise ValueError(f"'{clause}': say OGD or PGD.")
        index = TSDR_SLOTS[(direction, variant)]
        if pattern_field:
            if not current(field).strip():
                raise ValueError(f"{field} is empty in this row; add the {layer.upper()} pattern first.")
            updated = []
            for segment in current(field).split(";"):
                if not segment.strip():
                    updated.append(segment)
                    continue
                base, _sep, marker = segment.partition("|")
                updated.append(f"{base}|{_set_slot(marker, index, value_nm)}")
            changes[field] = ";".join(updated)
        else:
            vector = current(field)
            length = 5 if field == "FTICdMisregSkew" else 4
            new = _set_slot(vector, index, value_nm, length)
            if field == "FTICdMisregSkew" and new.split(",")[4] in ("", "0"):
                new = ",".join(new.split(",")[:4] + ["Y"])
            changes[field] = new
        notes.append(f"{field} {direction} {variant} {value_nm:+g} nm")
    if not changes:
        raise ValueError("Nothing to apply. Hover over the box for the syntax.")
    return changes, notes


class _Tooltip:
    def __init__(self, widget: tk.Widget, text) -> None:
        self.widget, self.text, self.window = widget, text, None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _event=None) -> None:
        text = self.text() if callable(self.text) else self.text
        if self.window or not text:
            return
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.wm_geometry(f"+{self.widget.winfo_rootx()}+{self.widget.winfo_rooty() + self.widget.winfo_height() + 4}")
        tk.Label(self.window, text=text, justify="left", background="#fffbe6", relief="solid", borderwidth=1,
                 font=("Consolas", 9), padx=6, pady=4).pack()

    def _hide(self, _event=None) -> None:
        if self.window:
            self.window.destroy()
            self.window = None
class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("81Z ISO Structure Library")
        tkfont.nametofont("TkDefaultFont").configure(family="Segoe UI", size=11)
        tkfont.nametofont("TkTextFont").configure(family="Segoe UI", size=11)
        style = ttk.Style(self)
        style.configure(".", font=("Segoe UI", 11))
        style.configure("Treeview", font=("Segoe UI", 11), rowheight=29)
        style.configure("Treeview.Heading", font=("Segoe UI", 11, "bold"))
        style.configure("Library.Treeview", font=("Segoe UI", 9), rowheight=22)
        style.configure("Library.Treeview.Heading", font=("Segoe UI", 9, "bold"))
        self.geometry("1800x1100")
        self.minsize(1280, 800)
        self.resizable(True, True)
        self.designer: StructureDesigner | None = None
        self.design: Design | None = None
        self.baseline: Design | None = None
        self.edit_field = tk.StringVar()
        self.edit_value = tk.StringVar()
        self.photo: ImageTk.PhotoImage | None = None
        self.full_photo: ImageTk.PhotoImage | None = None
        self.full_image: Image.Image | None = None
        self.current_png: Path | None = None
        self.zoom_var = tk.StringVar(value="Fit")
        self.zoom_percent = tk.StringVar(value="100%")
        self.zoom_scale = 1.0
        self.tsdr_var = tk.StringVar(value="Off")
        self.query_var = tk.StringVar()
        self.family_var = tk.StringVar(value="All families")
        self.testrow_var = tk.StringVar(value="All test rows")
        self.layer_vars: dict[str, tk.BooleanVar] = {}
        self.layer_buttons: list[ttk.Checkbutton] = []
        self.library_iids: dict[str, dict[str, str]] = {}
        self.results: "queue.Queue[tuple]" = queue.Queue()
        self.busy = False
        self.search_visible = True
        self.trm_forced = False
        self._build()
        self._run(lambda: StructureDesigner(use_llm=False), self._loaded, "Loading 81Z structure library...")
        self._poll_after = self.after(100, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._close)

    # -- layout --------------------------------------------------------------
    def _build(self) -> None:
        top = ttk.Frame(self, padding=6)
        top.pack(fill="x")
        self.search_toggle = ttk.Button(top, text="Hide search", command=self._toggle_search)
        self.search_toggle.pack(side="left", padx=(0, 8))
        filters = ttk.Frame(top)
        filters.pack(side="left", fill="x", expand=True)
        self.search_filters = filters
        ttk.Label(filters, text="Search").pack(side="left", padx=(0, 6))
        self.query_entry = ttk.Entry(filters, textvariable=self.query_var, width=36)
        self.query_entry.pack(side="left")
        self.query_entry.bind("<Return>", self._filter_library)
        self.family_filter = ttk.Combobox(
            filters, textvariable=self.family_var, state="readonly",
            values=("All families", *FAMILY_PREFIXES, "VIA_CHAIN"), width=18,
        )
        self.family_filter.pack(side="left", padx=(6, 0))
        self.family_filter.bind("<<ComboboxSelected>>", self._filter_library)
        self.testrow_filter = ttk.Combobox(
            filters, textvariable=self.testrow_var, state="readonly", values=("All test rows",), width=24,
        )
        self.testrow_filter.pack(side="left", padx=(6, 0))
        self.testrow_filter.bind("<<ComboboxSelected>>", self._filter_library)
        self.result_count = tk.StringVar(value="Loading library...")
        ttk.Label(filters, textvariable=self.result_count).pack(side="right", padx=(8, 0))
        self.query_var.trace_add("write", self._filter_library)

        main_split = ttk.PanedWindow(self, orient="vertical")
        main_split.pack(fill="both", expand=True, padx=6)
        self.main_split = main_split
        body = ttk.PanedWindow(main_split, orient="horizontal")
        main_split.add(body, weight=3)
        self.body = body
        library = ttk.Frame(body, width=440)
        library.grid_propagate(False)
        self.library_pane = library
        body.add(library, weight=0)
        self.library_table = ttk.Treeview(library, columns=("match", "testrow", "family", "name"),
                  displaycolumns=("match", "testrow", "family", "name"), show="headings", height=14,
                  style="Library.Treeview")
        for key, label in (("match", "Match"), ("testrow", "Test row"), ("family", "Family"), ("name", "Structure name")):
            self.library_table.heading(key, text=label)
        self.library_table.column("match", width=46, minwidth=40, stretch=False, anchor="e")
        self.library_table.column("testrow", width=78, minwidth=50, stretch=False)
        self.library_table.column("family", width=62, minwidth=50, stretch=False)
        self.library_table.column("name", width=250, minwidth=140, stretch=True)
        library_y = ttk.Scrollbar(library, orient="vertical", command=self.library_table.yview)
        library_x = ttk.Scrollbar(library, orient="horizontal", command=self.library_table.xview)
        self.library_table.configure(yscrollcommand=library_y.set, xscrollcommand=library_x.set)
        self.library_table.grid(row=0, column=0, sticky="nsew")
        library_y.grid(row=0, column=1, sticky="ns")
        library_x.grid(row=1, column=0, sticky="ew")
        library.rowconfigure(0, weight=1)
        library.columnconfigure(0, weight=1)
        self.library_table.bind("<<TreeviewSelect>>", self._select_library_row)
        self.library_iids = {}

        preview = ttk.Frame(body)
        body.add(preview, weight=5)
        header = ttk.Frame(preview)
        header.pack(fill="x")
        self.title_var = tk.StringVar(value="Select an 81Z structure")
        ttk.Label(header, textvariable=self.title_var, font=("Segoe UI", 14, "bold")).pack(side="left")
        self.meta_var = tk.StringVar(value="")
        ttk.Label(header, textvariable=self.meta_var, font=("Segoe UI", 10), foreground="#4a5560").pack(
            side="left", padx=(12, 0), pady=(4, 0))
        style = ttk.Style(self)
        style.configure("Tool.TLabelframe.Label", font=("Segoe UI", 9, "bold"), foreground="#34495e")
        style.configure("Layer.TCheckbutton", font=("Segoe UI", 10))
        controls = ttk.Frame(preview)
        controls.pack(fill="x", pady=(2, 0))
        controls.columnconfigure(0, weight=0)
        controls.columnconfigure(1, weight=1)
        preview_tools = ttk.LabelFrame(controls, text="TSDR / edit", style="Tool.TLabelframe", padding=(6, 2))
        preview_tools.grid(row=0, column=0, columnspan=2, sticky="nsew")
        ttk.Label(preview_tools, text="TSDR:").pack(side="left", padx=(0, 4))
        self.tsdr_combo = ttk.Combobox(preview_tools, textvariable=self.tsdr_var, state="readonly", width=30,
                           values=("Off",))
        self.tsdr_combo.pack(side="left")
        self.tsdr_count = tk.StringVar(value="")
        tsdr_count_label = ttk.Label(preview_tools, textvariable=self.tsdr_count, foreground="#1f4e79")
        tsdr_count_label.pack(side="left", padx=(4, 0))
        _Tooltip(tsdr_count_label, lambda: "\n".join(self.tsdr_combo.cget("values")[1:]))
        _Tooltip(self.tsdr_combo, lambda: "TSDR IDs in this structure:\n" + "\n".join(self.tsdr_combo.cget("values")[1:]))
        self.tsdr_combo.bind("<<ComboboxSelected>>", lambda _e: self.tsdr_region.get() and self._layers_changed())
        self.tsdr_region = tk.BooleanVar(value=False)
        region_check = ttk.Checkbutton(preview_tools, text="Region", variable=self.tsdr_region,
                                       command=self._layers_changed)
        region_check.pack(side="left", padx=(6, 0))
        _Tooltip(region_check, "Hatch the shapes the selected TSDR ID applies to\n"
                               "(misc ID box, or the FTI / PLY / TCN / TCP / VCR / VCRNUB / PXL shapes it selects).")
        self.tsdr_request = tk.StringVar()
        ttk.Label(preview_tools, text="Edit:").pack(side="left", padx=(10, 4))
        self.tsdr_apply_btn = ttk.Button(preview_tools, text="Apply", width=6, command=self._apply_tsdr_request)
        self.tsdr_apply_btn.pack(side="right", padx=(4, 0))
        self.tsdr_entry = ttk.Entry(preview_tools, textvariable=self.tsdr_request, width=20)
        self.tsdr_entry.pack(side="left", fill="x", expand=True)
        self.tsdr_entry.bind("<Return>", self._apply_tsdr_request)
        _Tooltip(self.tsdr_entry, TSDR_HINT)
        _Tooltip(self.tsdr_apply_btn, TSDR_HINT)
        view_tools = ttk.LabelFrame(controls, text="View", style="Tool.TLabelframe", padding=(6, 2))
        view_tools.grid(row=1, column=0, sticky="nsew", padx=(0, 4), pady=(2, 0))
        zoom_tools = ttk.LabelFrame(controls, text="Zoom / measure", style="Tool.TLabelframe", padding=(6, 2))
        zoom_tools.grid(row=1, column=1, sticky="nsew", pady=(2, 0))
        ttk.Button(zoom_tools, text="-", width=3, command=lambda: self._zoom(-1)).pack(side="left")
        ttk.Button(zoom_tools, text="+", width=3, command=lambda: self._zoom(1)).pack(side="left")
        ttk.Button(zoom_tools, text="Fit", width=4, command=self._reset_fit).pack(side="left", padx=(4, 0))
        ttk.Label(zoom_tools, textvariable=self.zoom_percent, width=5).pack(side="left", padx=(4, 0))
        zoom = ttk.Combobox(zoom_tools, textvariable=self.zoom_var, values=("Fit", "Actual size", "Custom"),
                    state="readonly", width=10)
        zoom.pack(side="left", padx=(4, 8))
        zoom.bind("<<ComboboxSelected>>", self._zoom_mode_changed)
        self.measure_var = tk.BooleanVar(value=False)
        measure_check = ttk.Checkbutton(zoom_tools, text="Measure (nm grid)", variable=self.measure_var,
                                        command=self._measure_toggled)
        measure_check.pack(side="left", padx=(8, 0))
        _Tooltip(measure_check, "Measurement grid in nm (labels adapt to zoom).\n"
                                "Move the mouse for x/y at 0.1 nm; click two points to measure dx, dy and distance;\n"
                                "right-click or Esc clears. x: 44 nm per poly pitch, y: CH/2 per diffusion track.")
        self.copy_image_btn = ttk.Button(zoom_tools, text="Copy PNG", command=self._copy_layout_image)
        self.copy_image_btn.pack(side="right")
        self.view_var = tk.StringVar(value="Unit")
        self.global_flag = False
        self.global_routing = False
        view_box = ttk.Combobox(view_tools, textvariable=self.view_var, state="readonly", width=15,
                                values=("Unit", "Global", "Global + routing"))
        view_box.pack(side="left")
        view_box.bind("<<ComboboxSelected>>", lambda _e: self._global_view_changed())
        _Tooltip(view_box, "Unit = unit cell only (units tile N x M cells, no buffer)\n"
                           "Global = buffer ring around the unit cell(s)\n"
                           "Global + routing = also the LO/HI V0 castle (M1 jumpers, V0 landings, pins)")
        self.global_units_ogd = tk.IntVar(value=1)
        self.global_units_pgd = tk.IntVar(value=1)
        self.global_units = (1, 1)
        ttk.Label(view_tools, text="units").pack(side="left", padx=(6, 2))
        for variable in (self.global_units_ogd, self.global_units_pgd):
            ttk.Spinbox(view_tools, from_=1, to=4, width=2, textvariable=variable,
                        command=self._global_view_changed).pack(side="left")
            if variable is self.global_units_ogd:
                ttk.Label(view_tools, text="x").pack(side="left")
        self.coords_var = tk.StringVar(value="Unit")
        self.coords_mode = "unit"
        ttk.Label(view_tools, text="Coords:").pack(side="left", padx=(10, 2))
        coords_box = ttk.Combobox(view_tools, textvariable=self.coords_var, state="disabled", width=10,
                                  values=("Unit", "Global (g)", "Both"))
        self.coords_box = coords_box
        coords_box.pack(side="left")
        coords_box.bind("<<ComboboxSelected>>", lambda _e: self._coords_changed())
        _Tooltip(coords_box, "Axis numbering for poly tracks, diffusion tracks and M0 tracks:\n"
                             "Unit = unit-cell coordinates (fields without 'Global')\n"
                             "Global (g) = whole-structure coordinates incl. buffers (fields with 'Global')\n"
                             "Both = 'unit|gN'")
        layer_box = ttk.LabelFrame(controls, text="Layers", style="Tool.TLabelframe", padding=(6, 0))
        layer_box.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(2, 2))
        self.layer_control_frame = ttk.Frame(layer_box)
        self.layer_control_frame.pack(fill="x")
        self.layer_control_frame.bind("<Configure>", self._reflow_layer_buttons)
        self._layer_columns = 0
        self._set_layer_controls(None)
        image_frame = ttk.Frame(preview)
        image_frame.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(image_frame, background="white", highlightthickness=0,
                    xscrollcommand=lambda *args: xscroll.set(*args),
                    yscrollcommand=lambda *args: yscroll.set(*args))
        xscroll = ttk.Scrollbar(image_frame, orient="horizontal", command=self.canvas.xview)
        yscroll = ttk.Scrollbar(image_frame, orient="vertical", command=self.canvas.yview)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        image_frame.rowconfigure(0, weight=1)
        image_frame.columnconfigure(0, weight=1)
        self.canvas.bind("<Configure>", self._fit_image)
        self.canvas.bind("<Control-MouseWheel>", self._wheel_zoom)
        self.canvas.bind("<Double-1>", self._reset_fit)
        self.canvas.bind("<Motion>", self._measure_motion)
        self.canvas.bind("<Button-1>", self._measure_click)
        self.canvas.bind("<Button-3>", self._measure_clear)
        self.bind("<Escape>", self._measure_clear)
        self.calibration: dict | None = None
        self.measure_points: list[tuple[float, float]] = []
        self.image_origin = (0.0, 0.0)
        self.image_scale = 1.0

        tabs = ttk.Notebook(main_split)
        main_split.add(tabs, weight=2)
        def set_initial_split(event) -> None:
            if event.height < 500:
                return
            main_split.unbind("<Configure>", split_binding)
            main_split.after_idle(lambda: main_split.sashpos(0, round(main_split.winfo_height() * 0.6)))

        split_binding = main_split.bind("<Configure>", set_initial_split)
        self.summary = tk.Text(tabs, wrap="word", font=("Segoe UI", 11), height=7)
        tabs.add(self.summary, text="Summary")
        self.details = tk.Text(tabs, wrap="word", font=("Consolas", 11), height=7)
        tabs.add(self.details, text="Selection details")
        sheet = ttk.Frame(tabs)
        tabs.add(sheet, text="Buildsheet")
        sheet_actions = ttk.Frame(sheet, padding=(0, 4, 0, 0))
        sheet_actions.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.sheet_view = tk.StringVar(value="decoded")
        for label, mode in (("Decoded layers", "decoded"), ("Fields by section", "fields"),
                            ("Template order", "template")):
            ttk.Radiobutton(sheet_actions, text=label, value=mode, variable=self.sheet_view, style="Toolbutton",
                            command=self._switch_sheet_view).pack(side="left", padx=(0, 2), ipadx=6)
        self.find_var = tk.StringVar()
        self.find_count = tk.StringVar()
        ttk.Label(sheet_actions, text="Find:").pack(side="left", padx=(16, 4))
        find_entry = ttk.Entry(sheet_actions, textvariable=self.find_var, width=24)
        find_entry.pack(side="left")
        find_entry.bind("<Return>", lambda _e: self._find_in_sheet(1))
        find_entry.bind("<Shift-Return>", lambda _e: self._find_in_sheet(-1))
        self.find_var.trace_add("write", lambda *_a: self._find_in_sheet(0))
        ttk.Button(sheet_actions, text="<", width=3, command=lambda: self._find_in_sheet(-1)).pack(side="left", padx=(4, 0))
        ttk.Button(sheet_actions, text=">", width=3, command=lambda: self._find_in_sheet(1)).pack(side="left")
        ttk.Label(sheet_actions, textvariable=self.find_count, width=9).pack(side="left", padx=(4, 0))
        self.bind_all("<Control-f>", lambda _e: find_entry.focus_set())
        self.reset_btn = ttk.Button(sheet_actions, text="Reset working copy", command=self._reset_edit)
        self.reset_btn.pack(side="right")
        ttk.Button(sheet_actions, text="Copy with header", command=self._copy_buildsheet).pack(side="right", padx=(0, 4))
        editor = ttk.Frame(sheet)
        editor.grid(row=1, column=0, sticky="ew", pady=(4, 4))
        ttk.Label(editor, textvariable=self.edit_field, width=26, font=("Segoe UI", 10, "bold"),
                  foreground="#1f4e79").pack(side="left")
        self.edit_entry = ttk.Entry(editor, textvariable=self.edit_value)
        self.edit_entry.pack(side="left", fill="x", expand=True)
        self.edit_entry.bind("<Return>", self._apply_edit)
        _Tooltip(self.edit_entry, "Select a row, edit its value, then press Enter or Apply & recalculate.")
        self.apply_btn = ttk.Button(editor, text="Apply & recalculate", command=self._apply_edit)
        self.apply_btn.pack(side="left", padx=(8, 0))
        style.configure("Sheet.Treeview", font=("Segoe UI", 10), rowheight=24)
        style.configure("Sheet.Treeview.Heading", font=("Segoe UI", 10, "bold"))
        self.table = ttk.Treeview(sheet, columns=("field", "original", "value"), show="tree headings",
                                  style="Sheet.Treeview")
        self.table.column("#0", width=22, minwidth=22, stretch=False)
        self.table.heading("field", text="Field")
        self.table.heading("original", text="Library value")
        self.table.heading("value", text="Working value")
        self.table.column("field", width=190, minwidth=130, stretch=False)
        self.table.column("original", width=350, minwidth=180, stretch=True)
        self.table.column("value", width=350, minwidth=180, stretch=True)
        scroll = ttk.Scrollbar(sheet, orient="vertical", command=self.table.yview)
        xscroll = ttk.Scrollbar(sheet, orient="horizontal", command=self.table.xview)
        self.table.configure(yscrollcommand=scroll.set, xscrollcommand=xscroll.set)
        self.table.grid(row=2, column=0, sticky="nsew")
        scroll.grid(row=2, column=1, sticky="ns")
        xscroll.grid(row=3, column=0, sticky="ew")
        from scalar_snapshot import DECODED_COLUMNS

        self.decoded = ttk.Treeview(sheet, columns=DECODED_COLUMNS, show="headings", style="Sheet.Treeview")
        for column, width in zip(DECODED_COLUMNS, (150, 34, 110, 120, 200, 140, 200, 220, 120)):
            self.decoded.heading(column, text=column)
            self.decoded.column(column, width=width, minwidth=30, stretch=column in ("X positions", "Y positions", "Params"))
        decoded_y = ttk.Scrollbar(sheet, orient="vertical", command=self.decoded.yview)
        decoded_x = ttk.Scrollbar(sheet, orient="horizontal", command=self.decoded.xview)
        self.decoded.configure(yscrollcommand=decoded_y.set, xscrollcommand=decoded_x.set)
        self.decoded.bind("<Double-1>", self._copy_decoded_cell)
        self.decoded.bind("<<TreeviewSelect>>", self._decoded_selected)
        self.decoded.tag_configure("stripe", background="#f3f6f9")
        self.table.tag_configure("stripe", background="#f3f6f9")
        self.table.tag_configure("group", background="#dfe7ef", font=("Segoe UI", 9, "bold"))
        self.table.tag_configure("edited", background="#e8f5ed")
        self.template_table = ttk.Treeview(sheet, columns=("index", "field", "original", "value"), show="headings",
                                           style="Sheet.Treeview")
        for column, text, width, stretch in (("index", "#", 44, False), ("field", "Field", 190, False),
                                             ("original", "Library value", 350, True),
                                             ("value", "Working value", 350, True)):
            self.template_table.heading(column, text=text)
            self.template_table.column(column, width=width, minwidth=40, stretch=stretch,
                                       anchor="e" if column == "index" else "w")
        template_y = ttk.Scrollbar(sheet, orient="vertical", command=self.template_table.yview)
        template_x = ttk.Scrollbar(sheet, orient="horizontal", command=self.template_table.xview)
        self.template_table.configure(yscrollcommand=template_y.set, xscrollcommand=template_x.set)
        self.template_table.tag_configure("stripe", background="#f3f6f9")
        self.template_table.tag_configure("empty", foreground="#9aa3ab")
        self.template_table.tag_configure("edited", background="#e8f5ed")
        self.template_table.bind("<<TreeviewSelect>>", self._template_selected)
        self.template_table.bind("<Double-1>", self._copy_clicked_cell)
        for tree in (self.table, self.decoded, self.template_table):
            tree.tag_configure("match", background="#fff1a8")
            tree.tag_configure("current", background="#ffd24d")
        self.sheet_widgets = {"fields": (self.table, scroll, xscroll), "decoded": (self.decoded, decoded_y, decoded_x),
                              "template": (self.template_table, template_y, template_x)}
        for mode in ("decoded", "template"):
            for widget, (row_index, column_index) in zip(self.sheet_widgets[mode], ((2, 0), (2, 1), (3, 0))):
                widget.grid(row=row_index, column=column_index, sticky="nsew")
        self._switch_sheet_view()
        sheet.rowconfigure(2, weight=1)
        sheet.columnconfigure(0, weight=1)
        self.table.bind("<<TreeviewSelect>>", self._edit_field_selected)
        self.table.bind("<Control-c>", self._copy_selected_cell)
        self.table.bind("<Control-v>", self._paste_selected_cell)
        self.table.bind("<Double-1>", self._copy_clicked_cell)
        sheet_menu = tk.Menu(self.table, tearoff=False)
        sheet_menu.add_command(label="Copy working value", command=self._copy_selected_cell)
        sheet_menu.add_command(label="Copy field and value", command=self._copy_selected_row)
        sheet_menu.add_command(label="Paste working value", command=self._paste_selected_cell)
        self.sheet_menu = sheet_menu
        self.table.bind("<Button-3>", self._show_sheet_menu)

        bottom = ttk.Frame(self, padding=6)
        bottom.pack(fill="x")
        self.copy_btn = ttk.Button(bottom, text="Copy buildsheet with header", command=self._copy_buildsheet)
        self.copy_btn.pack(side="left", padx=(0, 6))
        self.export_btn = ttk.Button(bottom, text="Export working buildsheet...", command=self._export)
        self.export_btn.pack(side="left")
        self.status = tk.StringVar()
        ttk.Label(self, textvariable=self.status, relief="sunken", anchor="w", padding=3).pack(fill="x")
        self._set_enabled()

    def _toggle_search(self) -> None:
        self.search_visible = not self.search_visible
        if self.search_visible:
            self.search_filters.pack(side="left", fill="x", expand=True)
            self.body.insert(0, self.library_pane, weight=0)
        else:
            self.search_filters.pack_forget()
            self.body.forget(self.library_pane)
        self.search_toggle.configure(text="Hide search" if self.search_visible else "Show search")

    def _switch_sheet_view(self) -> None:
        for mode, widgets in self.sheet_widgets.items():
            for widget in widgets:
                if mode == self.sheet_view.get():
                    widget.grid()
                else:
                    widget.grid_remove()
        if hasattr(self, "find_var") and self.find_var.get().strip():
            self._find_in_sheet(0)

    def _active_sheet_tree(self) -> ttk.Treeview:
        return {"decoded": self.decoded, "fields": self.table, "template": self.template_table}[self.sheet_view.get()]

    def _find_in_sheet(self, step: int) -> None:
        """Highlight every row containing the text and jump to the next (step 1), previous (-1) or first (0)."""
        tree = self._active_sheet_tree()
        query = self.find_var.get().strip().lower()
        items = [item for parent in tree.get_children() for item in (parent, *tree.get_children(parent))]
        matches = []
        for item in items:
            tags = [tag for tag in tree.item(item, "tags") if tag not in ("match", "current")]
            hit = bool(query) and not item.startswith("group:") and any(
                query in str(value).lower() for value in tree.item(item, "values"))
            if hit:
                matches.append(item)
            tree.item(item, tags=(*tags, "match") if hit else tags)
        if not matches:
            for mode in ("template", "fields", "decoded"):
                other = self.sheet_widgets[mode][0]
                if query and mode != self.sheet_view.get() and any(
                        query in str(value).lower() for parent in other.get_children()
                        for item in (parent, *other.get_children(parent)) if not item.startswith("group:")
                        for value in other.item(item, "values")):
                    self.sheet_view.set(mode)
                    self._switch_sheet_view()
                    self.status.set(f"'{query}' not in that view; switched to {mode} view")
                    return
            self.find_count.set("0 found" if query else "")
            return
        selection = tree.selection()
        current = selection[0] if selection else None
        if step and current in matches:
            index = (matches.index(current) + step) % len(matches)
        elif step and current in items:
            position = items.index(current)
            after = [i for i, item in enumerate(matches) if items.index(item) > position]
            before = [i for i, item in enumerate(matches) if items.index(item) < position]
            index = (after[0] if after else 0) if step > 0 else (before[-1] if before else len(matches) - 1)
        else:
            index = 0 if step >= 0 else len(matches) - 1
        item = matches[index]
        if tree.parent(item):
            tree.item(tree.parent(item), open=True)
        tree.item(item, tags=(*tree.item(item, "tags"), "current"))
        tree.selection_set(item)
        tree.focus(item)
        tree.see(item)
        self.find_count.set(f"{index + 1} of {len(matches)}")

    def _template_selected(self, _event=None) -> None:
        selection = self.template_table.selection()
        if selection and self.design:
            self.edit_field.set(selection[0])
            self.edit_value.set(self.design.row.get(selection[0], ""))
        self._set_enabled()

    def _decoded_selected(self, _event=None) -> None:
        selection = self.decoded.selection()
        if selection and self.design and self.designer:
            field = str(self.decoded.item(selection[0], "values")[0])
            if field in self.designer.output_fields:
                self.edit_field.set(field)
                self.edit_value.set(self.design.row.get(field, ""))
        self._set_enabled()

    def _copy_decoded_cell(self, event) -> None:
        item = self.decoded.identify_row(event.y)
        column = self.decoded.identify_column(event.x)
        if item and column:
            self._copy_to_clipboard(str(self.decoded.item(item, "values")[int(column[1:]) - 1]))
            self.status.set("Copied decoded cell")

    def _copy_to_clipboard(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()

    def _copy_selected_cell(self, _event=None) -> None:
        selection = self.table.selection()
        if selection and not selection[0].startswith("group:"):
            field = selection[0]
            self._copy_to_clipboard(self.design.row.get(field, "") if self.design else "")
            self.status.set(f"Copied working value for {field}")

    def _copy_clicked_cell(self, event) -> None:
        tree = event.widget
        field = tree.identify_row(event.y)
        column = tree.identify_column(event.x)
        if field and column != "#0" and not field.startswith("group:"):
            self._copy_to_clipboard(str(tree.item(field, "values")[int(column[1:]) - 1]))
            self.status.set(f"Copied {field} cell")

    def _copy_selected_row(self) -> None:
        selection = self.table.selection()
        if selection and self.design and not selection[0].startswith("group:"):
            field = selection[0]
            output = io.StringIO(newline="")
            csv.writer(output, delimiter="\t", lineterminator="\n").writerow((field, self.design.row.get(field, "")))
            self._copy_to_clipboard(output.getvalue())
            self.status.set(f"Copied {field} and working value")

    def _paste_selected_cell(self, _event=None) -> None:
        if not (self.design and self.designer and self.edit_field.get() in self.designer.output_fields
                and self.edit_field.get() not in READ_ONLY_FIELDS) or self.busy:
            return
        try:
            rows = list(csv.reader(io.StringIO(self.clipboard_get()), delimiter="\t"))
        except (tk.TclError, csv.Error):
            self.status.set("Clipboard has no readable text")
            return
        if len(rows) != 1 or not rows[0] or (len(rows[0]) > 1 and rows[0][0] != self.edit_field.get()):
            self.status.set("Paste one value, or a matching field and value row")
            return
        self.edit_value.set(rows[0][-1])
        self.status.set(f"Pasted {self.edit_field.get()}; apply to recalculate")

    def _show_sheet_menu(self, event) -> None:
        field = self.table.identify_row(event.y)
        if not field:
            return
        self.table.selection_set(field)
        self.sheet_menu.tk_popup(event.x_root, event.y_root)
        self.sheet_menu.grab_release()

    def _set_enabled(self) -> None:
        ready = self.designer is not None and not self.busy
        self.query_entry.state(["!disabled"] if ready else ["disabled"])
        self.family_filter.state(["!disabled"] if ready else ["disabled"])
        self.testrow_filter.state(["!disabled"] if ready else ["disabled"])
        self.library_table.state(["!disabled"] if ready else ["disabled"])
        self.copy_btn.state(["!disabled"] if ready and self.design else ["disabled"])
        self.export_btn.state(["!disabled"] if ready and self.design else ["disabled"])
        self.copy_image_btn.state(["!disabled"] if ready and self.current_png else ["disabled"])
        self.tsdr_combo.state(["!disabled"] if ready and self.design else ["disabled"])
        self.reset_btn.state(["!disabled"] if ready and self.baseline and self.design and self.design is not self.baseline else ["disabled"])
        editable = ready and bool(self.design and self.designer and self.edit_field.get() in self.designer.output_fields
                      and self.edit_field.get() not in READ_ONLY_FIELDS)
        self.edit_entry.state(["!disabled"] if editable else ["disabled"])
        self.apply_btn.state(["!disabled"] if editable else ["disabled"])
        for b in self.layer_buttons:
            b.state(["!disabled"] if ready and self.design else ["disabled"])

    def _visible_layers_for_row(self, row: dict[str, str]) -> set[str]:
        from scalar_snapshot import layout_layers, tsdr_draw_layers

        layers = layout_layers(row)
        tsdr_layers = set(tsdr_draw_layers(row))
        visible = {layer for layer in layers if layer not in tsdr_layers and layer in self.layer_vars
                   and self.layer_vars[layer].get()}
        visible.update(layer for layer in layers if layer not in tsdr_layers and layer not in self.layer_vars
                       and layer not in LAYERS_OFF_BY_DEFAULT)
        if self.design is None or self.design.row.get("StructureName") != row.get("StructureName"):
            if row.get("PXLSkew"):
                visible.add("PXL / TRM")
            elif self.trm_forced:
                visible.discard("PXL / TRM")
        return visible | self._tsdr_region_layers()

    def _apply_search_negations(self, query: str, query_spec) -> None:
        phrases = re.findall(
            r"\b(?:not|no|without|exclude|excluding|except)\s+([a-z0-9]+(?:[\s_-]+[a-z0-9]+)?)",
            query.lower(),
        )
        negative_terms = set(query_spec.negative_tokens)
        for phrase in phrases:
            negative_terms.update(re.findall(r"[a-z]+|\d+", phrase))
        query_spec.negative_tokens = sorted(negative_terms)

        if query_spec.flavor and query_spec.flavor.lower() in negative_terms:
            query_spec.flavor = None
        if query_spec.type_letter and query_spec.type_letter.lower() in negative_terms:
            query_spec.type_letter = None
        if query_spec.mos and query_spec.mos.lower() in negative_terms:
            query_spec.mos = None
        if query_spec.direction and query_spec.direction.lower() in negative_terms:
            query_spec.direction = None
        if query_spec.cell_height and str(query_spec.cell_height) in negative_terms:
            query_spec.cell_height = None
        if re.search(r"\b(?:not|no|without|exclude|excluding|except)\s+open[\s_-]*cal\b", query, re.IGNORECASE):
            query_spec.exclude_open_cal = True
            query_spec.include_open_cal = False

    def _search_score(self, row: dict[str, str], query: str, query_spec) -> float:
        name = row.get("StructureName", "")
        family = family_of(name)
        comments = row.get("StructureComments", "")
        direction_match = re.search(r":(PGD|OGD):", name, re.IGNORECASE)
        height_match = re.search(r"(\d+)", row.get("CellHeight", ""))
        type_letter = _name_type(name)
        mos = mos_of(name)
        name_flavor = re.search(r"-(UL|U|L|S|H|E)-", name)
        flavor_names = {"H": "hvt", "S": "svt", "L": "lvt", "U": "ulvt", "UL": "ulvtll", "E": "elvt"}
        flavor = flavor_names.get(name_flavor[1]) if name_flavor else ""
        width_match = re.search(r",\s*(\d*\.\d+)\s*,\s*[np][a-z]+", row.get("DiffPatternUnit", ""), re.IGNORECASE)
        width_nm = round(float(width_match[1]) * 1000) if width_match else None
        array = f"{row.get('ArraySizeOgd', '')}x{row.get('ArraySizePgd', '')}"
        unit = f"{row.get('UnitCellSizeOgd', '')}x{row.get('UnitCellSizePgd', '')}"

        text = " ".join((name, comments, row.get("DesignContext", ""), row.get("DrawMiscIDsUnit", ""))).lower()
        document = set(re.findall(r"[a-z]+|\d+", text))
        document.update({family.lower(), str(height_match[1]) if height_match else "", f"ch{height_match[1]}" if height_match else "",
                         mos.lower(), type_letter.lower(), (direction_match[1].lower() if direction_match else ""),
                         flavor, f"{width_nm}nm" if width_nm else "", array.lower(), unit.lower(),
                         row.get("TestRow", "").lower()})
        if family == "VIA_CHAIN":
            document.update(("via", "chain", "via_chain"))
        if re.search(r"\b(?:OC\s*[1-9]?|open[\s_-]*cal)\b", name + " " + comments, re.IGNORECASE):
            document.add("open_cal")
        if "sdr" in text or "ref" in text:
            document.update(("standard", "reference"))
        document.discard("")
        test_row = row.get("TestRow", "").lower()
        normalized_test_row = re.sub(r"[^a-z0-9]", "", test_row)
        normalized_query = re.sub(r"[^a-z0-9]", "", query.lower())
        test_row_match = bool(normalized_test_row and normalized_test_row == normalized_query)

        query_text = query.lower()
        query_words = set(re.findall(r"[a-z]+|\d+", query_text))
        stopwords = {"a", "an", "the", "and", "or", "to", "of", "with", "for", "please", "structure", "structures",
                     "create", "find", "show", "want", "need", "test", "etest", "iso", "device", "type", "cell", "height"}
        query_terms = query_words - stopwords
        query_family = query_spec.family
        if query_family:
            query_terms.add(query_family.lower())
            aliases = {
                "GATEGATE": {"gate", "ete"}, "EPIEPI": {"epi"}, "TCNTCN": {"tcn"},
                "DIFFOGD": {"diff", "diffusion", "isolation"}, "DIFFCHN": {"diff", "chain"},
                "CTG": {"contact", "gate"},
            }
            query_terms.update(aliases.get(query_family, set()))
            if query_family == "VIA_CHAIN":
                query_terms.update(("via", "chain", "via_chain"))
        if "standard" in query_words or "reference" in query_words:
            query_terms.update(("sdr", "ref"))
        if "pxl" in query_words or "trm" in query_words:
            query_terms.update(("pxl", "trm"))
        if query_spec.exclude_open_cal:
            query_terms.update(("standard", "sdr", "ref"))
        if query_spec.include_open_cal:
            query_terms.add("open_cal")
        negative_terms = set(query_spec.negative_tokens)
        if query_spec.exclude_open_cal:
            negative_terms.add("open_cal")
        positive_terms = query_terms - negative_terms - {"not", "no", "without", "exclude", "except"}
        query_text_norm = " ".join(sorted(positive_terms))
        document_text = " ".join(sorted(document))
        term_score = len(positive_terms & document) / len(positive_terms) if positive_terms else 0.0
        fuzzy_score = difflib.SequenceMatcher(None, query_text_norm, document_text).ratio() if positive_terms else 0.0
        negative_hits = len(negative_terms & document)

        checks = (
            (query_spec.family, family, 3.0),
            (query_spec.cell_height, int(height_match[1]) if height_match else None, 2.0),
            (query_spec.mos, mos, 2.0),
            (query_spec.type_letter, type_letter, 2.5),
            (query_spec.direction, direction_match[1].upper() if direction_match else None, 1.5),
            (query_spec.flavor, flavor, 1.5),
            (query_spec.devwidth_nm, width_nm, 1.0),
            (f"{query_spec.array_ogd}x{query_spec.array_pgd}" if query_spec.array_ogd and query_spec.array_pgd else None, array, 1.0),
            (f"{query_spec.unit_ogd}x{query_spec.unit_pgd}" if query_spec.unit_ogd and query_spec.unit_pgd else None, unit, 1.0),
        )
        active_checks = [(expected, actual, weight) for expected, actual, weight in checks if expected is not None]
        if active_checks:
            total = sum(weight for _expected, _actual, weight in active_checks)
            attributes = sum(weight if str(expected).lower() == str(actual).lower() else 0.0
                             for expected, actual, weight in active_checks) / total
            score = 55 * attributes + 35 * term_score + 10 * fuzzy_score
        else:
            score = 78 * term_score + 22 * fuzzy_score
        if test_row_match:
            score = max(score, 98.0)
        return max(0.0, min(100.0, score - min(40, negative_hits * 24)))

    def _search_mismatches(self, row: dict[str, str], query_spec) -> list[str]:
        name = row.get("StructureName", "")
        checks = (
            ("family", query_spec.family, family_of(name)),
            ("cell height", query_spec.cell_height, int(m.group(1)) if (m := re.search(r"(\d+)", row.get("CellHeight", ""))) else None),
            ("MOS", query_spec.mos, mos_of(name)),
            ("type", query_spec.type_letter, _name_type(name)),
            ("direction", query_spec.direction, (m.group(1).upper() if (m := re.search(r":(PGD|OGD):", name, re.IGNORECASE)) else None)),
        )
        mismatches = [f"{label} {expected} not present (closest has {actual or 'unknown'})"
                      for label, expected, actual in checks
                      if expected is not None and str(expected).lower() != str(actual).lower()]
        if query_spec.exclude_open_cal and re.search(r"\bOC\s*[1-9]?\b", name, re.IGNORECASE):
            mismatches.append("requested standard/non-open-cal, closest is open-cal")
        return mismatches

    def _filter_library(self, _event=None, *_args) -> None:
        if not self.designer or not hasattr(self, "library_table"):
            return
        query = self.query_var.get().strip().lower()
        selected_family = self.family_var.get()
        selected_testrow = self.testrow_var.get()
        self.library_table.delete(*self.library_table.get_children())
        self.library_iids.clear()
        matches = []
        parser_note = ""
        try:
            query_spec, _notes = parse_prompt(query, self.designer.prompt_fields) if query else (None, [])
        except ValueError:
            query_spec, _notes = parse_prompt("", self.designer.prompt_fields)
            if re.search(r"via[\s_-]*chain|\b(?:vt|vg|vcr)_mv", query, re.IGNORECASE):
                query_spec.family = "VIA_CHAIN"
            else:
                parser_note = "some query details are unsupported; results use text matching"
        if query:
            self._apply_search_negations(query, query_spec)
        for index, row in enumerate(self.designer.library_rows):
            name = row.get("StructureName", "")
            testrow = row.get("TestRow", "")
            family = family_of(name)
            if selected_family != "All families" and family != selected_family:
                continue
            if selected_testrow != "All test rows" and testrow != selected_testrow:
                continue
            score = self._search_score(row, query, query_spec) if query else None
            matches.append((index, row, score))
        if query:
            matches.sort(key=lambda item: (-(item[2] or 0.0), item[0]))
        for index, row, score in matches:
            testrow = row.get("TestRow", "")
            name = row.get("StructureName", "")
            family = family_of(name)
            iid = str(index)
            self.library_table.insert("", "end", iid=iid,
                                      values=(f"{score:.0f}%" if score is not None else "", testrow, family, name))
            self.library_iids[iid] = row
        if matches:
            name_font = tkfont.Font(family="Segoe UI", size=9)
            longest = max(name_font.measure(row.get("StructureName", "")) for _i, row, _s in matches[:400])
            self.library_table.column("name", width=max(250, longest + 16))
        if query and matches:
            best = matches[0][2] or 0.0
            second = matches[1][2] if len(matches) > 1 else best
            ordered = sorted((best, second))
            mismatch_spec = query_spec
            if mismatch_spec is None:
                mismatch_spec, _notes = parse_prompt("", self.designer.prompt_fields)
            mismatches = self._search_mismatches(matches[0][1], mismatch_spec)
            self.result_count.set(f"{len(matches)} results · closest {best:.0f}% · next {second:.0f}%")
            message = (f"Closest: {matches[0][1].get('StructureName', '')} · match {best:.0f}% · "
                       f"top-two score band {ordered[0]:.0f}-{ordered[1]:.0f}% (heuristic, not a calibrated probability)")
            if mismatches:
                message += " · constraints not matched: " + "; ".join(mismatches)
            if parser_note:
                message += " · " + parser_note
            self.status.set(message)
        else:
            self.result_count.set(f"{len(matches)} of {len(self.designer.library_rows)} structures")

    def _select_library_row(self, _event=None) -> None:
        if not self.designer or self.busy:
            return
        selection = self.library_table.selection()
        if not selection:
            return
        row = self.library_iids.get(selection[0])
        if row is not None:
            from scalar_snapshot import default_tsdr_draw_layer, tsdr_draw_layers

            self.tsdr_var.set(default_tsdr_draw_layer(row, tsdr_draw_layers(row)))
            visible_layers = self._visible_layers_for_row(row)
            self._run(self._open_library_row, self._show_library, "Opening library structure...", row, visible_layers)

    def _show_library(self, value: tuple[Design, Path | None]) -> None:
        self.baseline = value[0]
        self.edit_field.set("")
        self.edit_value.set("")
        from scalar_snapshot import default_tsdr_draw_layer, tsdr_draw_layers

        choices = tsdr_draw_layers(self.baseline.row)
        self.tsdr_var.set(default_tsdr_draw_layer(self.baseline.row, choices))
        self._show(value)

    def _edit_field_selected(self, _event=None) -> None:
        selection = self.table.selection()
        if selection and self.design and not selection[0].startswith("group:"):
            field = selection[0]
            self.edit_field.set(field)
            self.edit_value.set(self.design.row.get(field, ""))
        self._set_enabled()

    def _apply_edit(self, _event=None) -> None:
        if not (self.designer and self.design and self.baseline) or self.busy:
            return
        field = self.edit_field.get()
        if field not in self.designer.output_fields or field in READ_ONLY_FIELDS:
            return
        value = self.edit_value.get()
        if field in PREVIEW_WIDTH_FIELDS and value:
            try:
                width_nm = float(value)
            except ValueError:
                width_nm = 0
            if not math.isfinite(width_nm) or not 0 < width_nm <= 1000:
                self.status.set(f"{field} must be a positive width in nm (up to 1000)")
                return
        if field in {"UnitCellSizeOgd", "UnitCellSizePgd", "ArraySizeOgd", "ArraySizePgd"}:
            maximum = 10000 if field.startswith("ArraySize") else 1000
            if not value.isdecimal() or not 1 <= int(value) <= maximum:
                self.status.set(f"{field} must be an integer between 1 and {maximum}")
                return
        if value == self.design.row.get(field, ""):
            self.status.set(f"{field} is unchanged")
            return
        edited = dict(self.design.row)
        edited[field] = value
        axis_fields = {"FTITracksUnit", "DiffPatternUnit", "PolyPlugsUnit", "TcnPlugsUnit",
                       "VcrPatternUnit", "VcrnubPatternUnit", "VcxPatternUnit"}
        if field in axis_fields and value:
            ogd = int(edited.get("UnitCellSizeOgd", "1") or 1)
            pgd = 2 * int(edited.get("UnitCellSizePgd", "1") or 1)
            for segment in segments(value):
                parts = [part.strip() for part in strip_shift(segment).split(",")]
                x_index = 1 if field == "VcxPatternUnit" else 0
                if len(parts) <= x_index + 1 or not expand_axis(parts[x_index], ogd):
                    self.status.set(f"Invalid {field} X axis in: {segment}")
                    return
                if field == "VcxPatternUnit":
                    if len(parts) < 3 or not int_list(parts[2]):
                        self.status.set(f"Invalid {field} track list in: {segment}")
                        return
                elif not expand_axis(parts[1], pgd):
                    self.status.set(f"Invalid {field} Y axis in: {segment}")
                    return
                if field == "DiffPatternUnit":
                    try:
                        width = float(parts[2])
                    except (IndexError, ValueError):
                        width = 0
                    if not math.isfinite(width) or width <= 0:
                        self.status.set(f"DiffPatternUnit width must be a positive number in: {segment}")
                        return
        visible = self._visible_layers_for_row(edited)
        self._run(self._open_edited_row, self._show_working, f"Recalculating {field}...", edited, visible)

    def _open_edited_row(self, row: dict[str, str], visible: set[str]) -> tuple[Design, Path | None]:
        design, png = self._open_library_row(row, visible)
        if design.result is None and family_of(row.get("StructureName", "")) in FAMILY_PREFIXES:
            raise ValueError("; ".join(design.warnings) or "Scalar calculation failed")
        design.status = "working copy"
        return design, png

    def _show_working(self, value: tuple[Design, Path | None]) -> None:
        field = self.edit_field.get()
        self._show(value)
        if field and self.table.exists(field):
            self.table.selection_set(field)
            self.table.see(field)
        self.status.set(f"Working copy updated: {field}. Library row unchanged; export to save this experiment.")

    def _reset_edit(self) -> None:
        if not (self.baseline and self.design) or self.busy:
            return
        visible = self._visible_layers_for_row(self.baseline.row)
        self._run(self._render_layer_preview, self._show_baseline,
                  "Restoring library row...", self.baseline.id, visible)

    def _show_baseline(self, png: Path | None) -> None:
        assert self.baseline is not None
        field = self.edit_field.get()
        self._show((self.baseline, png))
        if field and self.table.exists(field):
            self.table.selection_set(field)
            self.edit_value.set(self.baseline.row.get(field, ""))
        self.status.set("Working copy reset to the original library row")

    def _open_library_row(self, row: dict[str, str], visible_layers: set[str]) -> tuple[Design, Path | None]:
        assert self.designer is not None
        design = self.designer.open_library_row(row)
        return design, self.designer.layout_png(design.id, visible_layers=visible_layers, global_view=self.global_flag,
                                                routing=self.global_routing,
                                                global_units=self.global_units, coords=self.coords_mode)

    def _set_layer_controls(self, row: dict[str, str] | None) -> None:
        from scalar_snapshot import default_tsdr_draw_layer, layout_layers, tsdr_draw_layers

        selected = {layer: variable.get() for layer, variable in self.layer_vars.items()}
        layers = layout_layers(row) if row else LAYERS
        tsdr_options = tsdr_draw_layers(row) if row else ()
        tsdr_layers = set(tsdr_options)
        self.tsdr_combo.configure(values=("Off", *tsdr_options))
        self.tsdr_count.set(f"{len(tsdr_options)} IDs" if len(tsdr_options) != 1 else "1 ID")
        if self.tsdr_var.get() not in tsdr_layers and row:
            self.tsdr_var.set(default_tsdr_draw_layer(row, tsdr_options))
        elif not row:
            self.tsdr_var.set("Off")
        for button in self.layer_buttons:
            button.destroy()
        self.layer_vars = {}
        self.layer_buttons = []
        for layer in layers:
            if layer in tsdr_layers:
                continue
            default_visible = layer not in LAYERS_OFF_BY_DEFAULT
            # PXL skew rows change only TRM geometry, so show it for those rows.
            pxl_row = layer == "PXL / TRM" and bool(row and row.get("PXLSkew"))
            if layer == "PXL / TRM" and self.trm_forced and not pxl_row:
                selected.pop(layer, None)
            variable = tk.BooleanVar(value=True if pxl_row else selected[layer] if layer in selected else default_visible)
            label = layer.removeprefix("Skew target: ")
            if layer.startswith("Skew target:"):
                label = f"TSDR {label}"
            self.layer_vars[layer] = variable
            button = ttk.Checkbutton(self.layer_control_frame, text=label, variable=variable,
                                     command=self._layers_changed, style="Layer.TCheckbutton")
            self.layer_buttons.append(button)
        self.trm_forced = bool(row and row.get("PXLSkew"))
        self._layer_columns = 0
        self._reflow_layer_buttons()

    def _reflow_layer_buttons(self, _event=None) -> None:
        """Wrap the layer checkboxes into an even grid that fits the pane width (no scrolling)."""
        if not self.layer_buttons:
            return
        cell = max(button.winfo_reqwidth() for button in self.layer_buttons) + 10
        width = self.layer_control_frame.winfo_width()
        columns = max(1, min(len(self.layer_buttons), (width if width > 1 else 1200) // cell))
        rows = -(-len(self.layer_buttons) // columns)
        columns = -(-len(self.layer_buttons) // rows)
        if columns == self._layer_columns:
            return
        self._layer_columns = columns
        for index, button in enumerate(self.layer_buttons):
            button.grid(row=index // columns, column=index % columns, sticky="w", padx=(2, 8))
        for column in range(len(self.layer_buttons)):
            self.layer_control_frame.columnconfigure(column, weight=0, minsize=cell if column < columns else 0)

    # -- background work -----------------------------------------------------
    def _run(self, func, done, message: str, *args) -> None:
        self.busy = True
        self.status.set(message)
        self._set_enabled()

        def worker() -> None:
            try:
                self.results.put((done, func(*args), None))
            except Exception as exc:  # surfaced in the UI thread
                self.results.put((done, None, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                done, value, error = self.results.get_nowait()
                self.busy = False
                if error:
                    self.status.set(f"Library action failed: {error}")
                    messagebox.showerror("81Z Structure Library", str(error), parent=self)
                else:
                    done(value)
                self._set_enabled()
        except queue.Empty:
            pass
        self._poll_after = self.after(100, self._poll)

    def _loaded(self, designer: StructureDesigner) -> None:
        self.designer = designer
        designer.output_fields.extend(field for field in PREVIEW_WIDTH_FIELDS if field not in designer.output_fields)
        testrows = sorted({row.get("TestRow", "") for row in designer.library_rows if row.get("TestRow", "")})
        self.testrow_filter.configure(values=("All test rows", *testrows))
        self._filter_library()
        self.status.set(f"Loaded {len(designer.library_rows)} 81Z rows across {len(testrows)} test structures. Select a row to inspect its layout and buildsheet.")

    # -- actions -------------------------------------------------------------
    def _layers_changed(self) -> None:
        if not (self.designer and self.design) or self.busy:
            return
        visible = {layer for layer, variable in self.layer_vars.items() if variable.get()}
        visible |= self._tsdr_region_layers()
        self._run(self._render_layer_preview, self._show_layer_preview,
                  "Updating visible layout layers...", self.design.id, visible)

    def _tsdr_region_layers(self) -> set[str]:
        tsdr_id = self.tsdr_var.get()
        return {f"TSDR region:{tsdr_id}"} if self.tsdr_region.get() and tsdr_id != "Off" else set()

    def _render_layer_preview(self, design_id: str, visible_layers: set[str]) -> Path | None:
        assert self.designer is not None
        return self.designer.layout_png(design_id, visible_layers=visible_layers, global_view=self.global_flag,
                                        routing=self.global_routing,
                                        global_units=self.global_units, coords=self.coords_mode)

    def _show_layer_preview(self, png: Path | None) -> None:
        self._show_image(png)
        selected = sum(variable.get() for variable in self.layer_vars.values())
        self.status.set(f"Preview layers: {selected} of {len(self.layer_vars)} visible; scalar and buildsheet unchanged")

    def _copy_buildsheet(self) -> None:
        if not (self.designer and self.design):
            return
        output = io.StringIO(newline="")
        writer = csv.writer(output, delimiter="\t", lineterminator="\n")
        fields = self.designer.output_fields
        writer.writerow(fields)
        writer.writerow([self.design.row.get(field, "") for field in fields])
        self.clipboard_clear()
        self.clipboard_append(output.getvalue())
        self.update_idletasks()
        self.status.set(f"Copied buildsheet row and header ({len(fields)} columns) as tab-separated data")

    def _export(self) -> None:
        if not (self.designer and self.design):
            return
        from scalar_snapshot import safe_name

        rows = [dict(self.design.row)]
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".csv", filetypes=[("CSV", "*.csv")],
                                            initialfile=f"{safe_name(self.design.row.get('StructureName', '81Z_structure'))}.csv",
                                            title="Export working 81Z buildsheet row")
        if path:
            self.designer.export(rows, Path(path))
            self.status.set(f"Exported working buildsheet to {path}")

    # -- display -------------------------------------------------------------
    def _show(self, value: tuple[Design, Path | None], keep_image: bool = False) -> None:
        design, png = value
        self.design = design
        name = design.row.get("StructureName", "")
        self._set_layer_controls(design.row)
        self.title_var.set(name)
        variant_type = design.spec.type_letter or _name_type(name) or "not encoded"
        details = [f"Type {variant_type}", design.spec.family or "family not specified"]
        if design.spec.direction:
            details.append(design.spec.direction)
        if design.spec.cell_height:
            details.append(f"CH{design.spec.cell_height}")
        if design.spec.mos:
            details.append(f"MOS {design.spec.mos}")
        if design.spec.flavor:
            details.append(design.spec.flavor.upper())
        if design.spec.devwidth_nm:
            details.append(f"{design.spec.devwidth_nm} nm")
        if design.spec.unit_ogd and design.spec.unit_pgd:
            details.append(f"unit {design.spec.unit_ogd}x{design.spec.unit_pgd}")
        if design.spec.array_ogd and design.spec.array_pgd:
            details.append(f"array {design.spec.array_ogd}x{design.spec.array_pgd}")
        self.meta_var.set(" | ".join(details) + f" | v{design.version} {design.status}")
        if not keep_image:
            self._show_image(png)

        lines = [f"Test row: {design.row.get('TestRow', '-')}",
             f"Comments: {design.row.get('StructureComments', '-') or '-'}",
             f"Fail modes: {design.fail_modes or '-'}", f"Scalar: {design.scalar or '-'}"]
        if self.baseline and design is not self.baseline:
            changed = [field for field in self.designer.output_fields if field not in READ_ONLY_FIELDS
                       and design.row.get(field, "") != self.baseline.row.get(field, "")]
            lines.extend((f"Library baseline: {self.baseline.fail_modes or '-'} fail modes, scalar {self.baseline.scalar or '-'}",
                          f"Edited fields ({len(changed)}): {', '.join(changed) or '-'}"))
        if design.warnings:
            lines += ["", "Warnings:"] + [f"- {w}" for w in design.warnings]
        try:
            from scalar_snapshot import trace_nets
            from ScalarCalculation import geometry_from_row

            layout_checks = trace_nets(design.row, geometry_from_row(design.row)).warnings
        except Exception as exc:  # informational only
            layout_checks = [f"connectivity trace failed: {exc}"]
        if layout_checks:
            lines += ["", "Layout connectivity check:"] + [f"- {w}" for w in layout_checks]
        self.summary.configure(state="normal")
        self.summary.delete("1.0", "end")
        self.summary.insert("1.0", "\n".join(lines))
        self.summary.configure(state="disabled")

        detail_lines = ["Library row:", design.row.get("StructureName", "-"),
                f"Test row: {design.row.get('TestRow', '-')}", "",
                "Buildsheet metadata:"]
        detail_lines += [f"- {key}: {value}" for key, value in (
            ("family", design.spec.family), ("direction", design.spec.direction),
            ("type", design.spec.type_letter), ("cell height", design.spec.cell_height),
            ("MOS", design.spec.mos), ("VT flavor", design.spec.flavor),
            ("device width (nm)", design.spec.devwidth_nm),
            ("unit cell", f"{design.spec.unit_ogd}x{design.spec.unit_pgd}" if design.spec.unit_ogd and design.spec.unit_pgd else None),
            ("array", f"{design.spec.array_ogd}x{design.spec.array_pgd}" if design.spec.array_ogd and design.spec.array_pgd else None),
        ) if value is not None]
        detail_lines += ["", "Formula:", design.formula or "-"]
        detail_lines += [f"- {r.strip()}" for r in design.reasoning]
        if design.alternatives:
            detail_lines += ["", "Next options:"] + [f"- {a}" for a in design.alternatives]
        if design.history:
            detail_lines += ["", "Recent feedback:"]
            detail_lines += [f"- {h['action']}: {h['text']}" for h in design.history[-8:]]
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", "\n".join(detail_lines))
        self.details.configure(state="disabled")

        self.table.delete(*self.table.get_children())
        assert self.designer is not None
        groups: dict[str, list[str]] = {name: [] for name, _pattern in FIELD_SECTIONS}
        groups["Other"] = []
        groups["Empty fields"] = []
        edited_fields: set[str] = set()
        for column in self.designer.output_fields:
            value = design.row.get(column, "")
            original = self.baseline.row.get(column, "") if self.baseline else value
            if value != original:
                edited_fields.add(column)
            section = next((name for name, pattern in FIELD_SECTIONS if re.search(pattern, column)), "Other")
            groups["Empty fields" if not value and not original else section].append(column)
        for section, columns in groups.items():
            if not columns:
                continue
            parent = f"group:{section}"
            self.table.insert("", "end", iid=parent, values=(f"{section} ({len(columns)})", "", ""),
                              open=section != "Empty fields", tags=("group",))
            for index, column in enumerate(columns):
                value = design.row.get(column, "")
                original = self.baseline.row.get(column, "") if self.baseline else value
                tags = ("edited",) if column in edited_fields else ("stripe",) if index % 2 else ()
                self.table.insert(parent, "end", iid=column, values=(column, original, value), tags=tags)
        from scalar_snapshot import decode_buildsheet

        self.template_table.delete(*self.template_table.get_children())
        self.find_count.set("")
        for index, column in enumerate(self.designer.output_fields, 1):
            value = design.row.get(column, "")
            original = self.baseline.row.get(column, "") if self.baseline else value
            tags = ("edited",) if column in edited_fields else ("empty",) if not value and not original else ()
            if index % 2 == 0 and not tags:
                tags = ("stripe",)
            self.template_table.insert("", "end", iid=column, values=(index, column, original, value), tags=tags)
        self.decoded.delete(*self.decoded.get_children())
        self.decoded.tag_configure("edited", background="#e8f5ed")
        try:
            stripe, previous = False, None
            for values in decode_buildsheet(design.row):
                if values[0] != previous:
                    stripe, previous = not stripe, values[0]
                tags = ("edited",) if values[0] in edited_fields else ("stripe",) if stripe else ()
                self.decoded.insert("", "end", values=values, tags=tags)
        except Exception as exc:  # decoding is informational; a malformed edit must not break the view
            self.decoded.insert("", "end", values=("decode error", "", "", str(exc)))
        self.status.set(f"{name}: fail modes {design.fail_modes or '-'}, scalar {design.scalar or '-'}"
                        f"{'  (' + str(len(design.warnings)) + ' warning(s))' if design.warnings else ''}")

    def _show_image(self, png: Path | None) -> None:
        self.canvas.delete("all")
        self.current_png = png
        if not png:
            self.photo = None
            self.full_photo = None
            self.full_image = None
            self.canvas.create_text(20, 20, anchor="nw", text="No layout preview for this structure")
            return
        with Image.open(png) as image:
            self.full_image = image.convert("RGBA")
        self.full_photo = ImageTk.PhotoImage(self.full_image)
        calibration_path = png.with_suffix(".json")
        try:
            self.calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.calibration = None
        self.measure_points = []
        self.after_idle(self._fit_image)

    def _copy_layout_image(self) -> None:
        if not self.current_png:
            return
        try:
            import win32clipboard
            import win32con

            with Image.open(self.current_png) as image:
                buffer = io.BytesIO()
                image.convert("RGB").save(buffer, format="BMP")
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_DIB, buffer.getvalue()[14:])
                win32clipboard.SetClipboardData(win32clipboard.RegisterClipboardFormat("PNG"),
                                                self.current_png.read_bytes())
            finally:
                win32clipboard.CloseClipboard()
            self.status.set("Copied layout image to clipboard")
        except Exception as exc:
            messagebox.showerror("Copy layout image", str(exc), parent=self)

    def _zoom_mode_changed(self, _event=None) -> None:
        if self.zoom_var.get() == "Actual size":
            self.zoom_scale = 1.0
        self._fit_image()

    def _coords_changed(self) -> None:
        if not self.global_flag:
            return
        self.coords_mode = {"Unit": "unit", "Global (g)": "global", "Both": "both"}[self.coords_var.get()]
        self._layers_changed()

    def _reset_fit(self, _event=None) -> None:
        if _event is not None and self.measure_var.get():
            return
        self.zoom_var.set("Fit")
        self._fit_image()
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)

    def _global_view_changed(self) -> None:
        self.global_flag = self.view_var.get() != "Unit"
        self.global_routing = self.view_var.get() == "Global + routing"
        self.coords_box.configure(state="readonly" if self.global_flag else "disabled")
        if self.global_flag and self.coords_var.get() == "Unit":
            self.coords_var.set("Both")
        self.coords_mode = ({"Unit": "unit", "Global (g)": "global", "Both": "both"}[self.coords_var.get()]
                            if self.global_flag else "unit")
        try:
            self.global_units = (min(4, max(1, self.global_units_ogd.get())), min(4, max(1, self.global_units_pgd.get())))
        except tk.TclError:
            self.global_units = (1, 1)
        self._layers_changed()

    def _apply_tsdr_request(self, _event=None) -> None:
        if not (self.designer and self.design and self.baseline) or self.busy:
            return
        try:
            changes, notes = parse_tsdr_request(self.tsdr_request.get(), self.design.row)
        except ValueError as exc:
            self.status.set(f"TSDR request not applied: {exc}")
            return
        edited = dict(self.design.row)
        edited.update(changes)
        self.edit_field.set(next(iter(changes)))
        self.edit_value.set(changes[self.edit_field.get()])
        visible = self._visible_layers_for_row(edited)
        if "PXLSkew" in changes:
            visible.add("PXL / TRM")
        self._run(self._open_edited_row, self._show_working, "Applying TSDR: " + "; ".join(notes), edited, visible)

    def _zoom(self, direction: int) -> None:
        if not self.full_image:
            return
        scale = self._image_scale()
        self.zoom_scale = max(0.2, min(4.0, scale * (1.25 if direction > 0 else 0.8)))
        self.zoom_var.set("Custom")
        self._fit_image()

    def _wheel_zoom(self, event) -> None:
        self._zoom(1 if event.delta > 0 else -1)

    def _image_scale(self) -> float:
        if not self.full_image:
            return 1.0
        if self.zoom_var.get() == "Fit":
            return min(1.0, max(1, self.canvas.winfo_width() - 12) / self.full_image.width,
                       max(1, self.canvas.winfo_height() - 12) / self.full_image.height)
        if self.zoom_var.get() == "Actual size":
            return 1.0
        return self.zoom_scale

    def _fit_image(self, _event=None) -> None:
        if not self.full_image or not self.full_photo:
            return
        scale = self._image_scale()
        self.zoom_percent.set(f"{scale:.0%}")
        if scale == 1.0:
            self.photo = self.full_photo
        else:
            size = (max(1, round(self.full_image.width * scale)), max(1, round(self.full_image.height * scale)))
            self.photo = ImageTk.PhotoImage(self.full_image.resize(size, Image.Resampling.LANCZOS))
        self.canvas.delete("preview")
        width = max(self.canvas.winfo_width(), self.photo.width())
        height = max(self.canvas.winfo_height(), self.photo.height())
        self.canvas.create_image(width // 2, height // 2,
                                 anchor="center", image=self.photo, tags="preview")
        self.canvas.configure(scrollregion=(0, 0, width, height))
        self.image_origin = (width // 2 - self.photo.width() // 2, height // 2 - self.photo.height() // 2)
        self.image_scale = self.photo.width() / self.full_image.width
        self._draw_measure_overlay()

    # -- measurement ---------------------------------------------------------
    def _canvas_to_nm(self, cx: float, cy: float) -> tuple[float, float] | None:
        cal = self.calibration
        if not cal:
            return None
        ix = (cx - self.image_origin[0]) / self.image_scale
        iy = (cy - self.image_origin[1]) / self.image_scale
        units_x = (ix - cal["origin_px"][0]) / cal["px_per_unit"][0]
        units_y = (cal["origin_px"][1] - iy) / cal["px_per_unit"][1]
        return round(units_x * cal["nm_per_unit"][0], 1), round(units_y * cal["nm_per_unit"][1], 1)

    def _nm_to_canvas(self, x_nm: float, y_nm: float) -> tuple[float, float]:
        cal = self.calibration
        assert cal is not None
        ix = cal["origin_px"][0] + x_nm / cal["nm_per_unit"][0] * cal["px_per_unit"][0]
        iy = cal["origin_px"][1] - y_nm / cal["nm_per_unit"][1] * cal["px_per_unit"][1]
        return self.image_origin[0] + ix * self.image_scale, self.image_origin[1] + iy * self.image_scale

    def _measure_toggled(self) -> None:
        self.measure_points = []
        self._draw_measure_overlay()
        if self.measure_var.get() and not self.calibration:
            self.status.set("Measurement needs a freshly rendered layout; toggle a layer to re-render.")

    def _draw_measure_overlay(self) -> None:
        self.canvas.delete("measure")
        cal = self.calibration
        if not (self.measure_var.get() and cal and self.full_image):
            return
        x0, y0, x1, y1 = cal["axes_px"]
        left, top = self.image_origin[0] + x0 * self.image_scale, self.image_origin[1] + y0 * self.image_scale
        right, bottom = self.image_origin[0] + x1 * self.image_scale, self.image_origin[1] + y1 * self.image_scale
        steps = (0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000)
        for axis in (0, 1):
            px_per_nm = cal["px_per_unit"][axis] * self.image_scale / cal["nm_per_unit"][axis]
            step = next((s for s in steps if s * px_per_nm >= 45), steps[-1])
            low = self._canvas_to_nm(left, bottom)
            high = self._canvas_to_nm(right, top)
            assert low is not None and high is not None
            start = math.floor(low[axis] / step) * step
            value = start
            while value <= high[axis] + 1e-9:
                if axis == 0:
                    cx, _ = self._nm_to_canvas(value, 0)
                    if left <= cx <= right:
                        self.canvas.create_line(cx, top, cx, bottom, fill="#2e86c1", dash=(2, 4), tags="measure")
                        self.canvas.create_text(cx + 2, top + 2, text=f"{value:g}", anchor="nw", fill="#1b4f72",
                                                font=("Segoe UI", 7), tags="measure")
                else:
                    _, cy = self._nm_to_canvas(0, value)
                    if top <= cy <= bottom:
                        self.canvas.create_line(left, cy, right, cy, fill="#2e86c1", dash=(2, 4), tags="measure")
                        self.canvas.create_text(left + 2, cy - 1, text=f"{value:g}", anchor="sw", fill="#1b4f72",
                                                font=("Segoe UI", 7), tags="measure")
                value = round(value + step, 6)
        self.canvas.create_text(right, top - 14, anchor="se", fill="#1b4f72", font=("Segoe UI", 8, "bold"),
                                text=f"nm ({cal.get('frame', 'unit')} origin)", tags="measure")
        self._draw_measure_points()

    def _draw_measure_points(self) -> None:
        self.canvas.delete("measure_pts")
        if not (self.measure_var.get() and self.calibration):
            return
        canvas_points = [self._nm_to_canvas(*point) for point in self.measure_points]
        for cx, cy in canvas_points:
            self.canvas.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, outline="#c0392b", width=2, tags="measure_pts")
        if len(canvas_points) == 2:
            (ax_, ay_), (bx, by) = canvas_points
            (p1x, p1y), (p2x, p2y) = self.measure_points
            dx, dy = round(p2x - p1x, 1), round(p2y - p1y, 1)
            text = f"dx {dx:+.1f} nm   dy {dy:+.1f} nm   d {math.hypot(dx, dy):.1f} nm"
            self.canvas.create_line(ax_, ay_, bx, by, fill="#c0392b", width=2, arrow="both", tags="measure_pts")
            label = self.canvas.create_text((ax_ + bx) / 2, (ay_ + by) / 2 - 12, text=text, fill="#922b21",
                                            font=("Segoe UI", 9, "bold"), tags="measure_pts")
            box = self.canvas.bbox(label)
            background = self.canvas.create_rectangle(box[0] - 3, box[1] - 2, box[2] + 3, box[3] + 2,
                                                      fill="#fdfefe", outline="#c0392b", tags="measure_pts")
            self.canvas.tag_lower(background, label)
            self.status.set(f"Measured: {text}")

    def _measure_motion(self, event) -> None:
        if not (self.measure_var.get() and self.calibration):
            return
        point = self._canvas_to_nm(self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))
        if point:
            nm_per_px = self.calibration["nm_per_unit"][0] / (self.calibration["px_per_unit"][0] * self.image_scale)
            self.status.set(f"x {point[0]:.1f} nm   y {point[1]:.1f} nm   ({self.calibration.get('frame', 'unit')} "
                            f"origin; {nm_per_px:.1f} nm per screen pixel - zoom in for finer picks; "
                            "click two points to measure)")

    def _measure_click(self, event) -> None:
        if not (self.measure_var.get() and self.calibration):
            return
        point = self._canvas_to_nm(self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))
        if point is None:
            return
        self.measure_points = [point] if len(self.measure_points) != 1 else [*self.measure_points, point]
        self._draw_measure_points()

    def _measure_clear(self, _event=None) -> None:
        self.measure_points = []
        self.canvas.delete("measure_pts")

    def _close(self) -> None:
        self.after_cancel(self._poll_after)
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
