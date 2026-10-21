"""Desktop browser for the existing 81Z ISO structure library."""

from __future__ import annotations

import csv
import bisect
import difflib
import io
import json
import math
import os
import queue
import re
import threading
import tkinter as tk
import tkinter.font as tkfont
from collections import Counter
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageTk

from iso_structure_ai import (FLAVOR_LETTER, STOPWORDS, Design, StructureDesigner, _name_type,
                              has_generic_layout_geometry, parse_prompt, template_active_area_size)
from pft_layout import PFT_LAYERS, PFT_LAYERS_OFF, is_pft_row
from template_layouts import TEMPLATE_LAYERS, TEMPLATE_LAYERS_OFF, TEMPLATE_TITLES, template_kind
from ScalarCalculation import (FAMILY_PREFIXES, expand_axis, family_of, geometry_from_row, int_list, is_miller_row,
                               m0_tracks_per_height, mos_of, segments, strip_shift)

LAYERS = (
    "NDIFF", "PDIFF", "FTI", "PLY", "TCN", "TCP", "VG", "VT", "VCR", "PXL / TRM",
    "M0 lines", "M0 LO1", "M0 HI1", "M0 cuts", "V0 landings", "Current flow", "Fail paths", "Grid",
    "M0 Y grid", "Guide tracks",
)
LAYERS_OFF_BY_DEFAULT = frozenset({"VT flavor", "PXL / TRM", "M0 lines", "M0 cuts", "Fail paths"})
C_RAIL_UI = "#8c564b"
READ_ONLY_FIELDS = frozenset({"StructureName", "TestRow", "Scalar", "ScalarFormula", "Force", "Test Type"})
PREVIEW_WIDTH_FIELDS = ("PreviewPlyWidthNm", "PreviewTcnWidthNm", "PreviewVcrWidthNm", "PreviewVcrnubWidthNm",
                        "PreviewVcrnubLengthNm", "PreviewM0WidthNm")
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
               "vcr": "VcrPatternUnit", "tcp": "TCPSkewPatternUnit", "vt": "VcxPatternUnit", "vg": "VcxPatternUnit"}
TSDR_SLOTS = {("OGD", "CD"): 0, ("PGD", "CD"): 1, ("OGD", "REG"): 2, ("PGD", "REG"): 3}
TSDR_HINT = (
    "Describe a TSDR skew or buildsheet change in plain words, then press Enter or Apply.\n"
    "TSDR skews (unit coordinates):\n"
    "  fti mode ml | fti mode sl   copies the matching library variant's FTI tracks\n"
    "  fti ogd cd -3nm             FTICdMisregSkew, OGD CD slot\n"
    "  tcn pgd reg +2              TCNCdRegSkew, PGD REG slot\n"
    "  poly ogd cd n2              n = negative, p = positive; nm by default (um allowed)\n"
    "  glk pgd cd 4  |  pxl cd p5  |  pxl reg n3  |  vcr pgd cd -4  |  tcp pgd cd 3\n"
    "  vt pgd cd +2  |  vg size 12x13nm  |  vt bottom 14  |  vt top 14\n"
    "  id tcn TSDRActiveL x 0:50 y 2:5   adds a DrawMiscIDsUnit box\n"
    "  reset fti                   clears that skew\n"
    "Structure:\n"
    "  buffer L2 R2 B1 T1  |  buffer 3  |  buffer left 6\n"
    "  array 5x60  |  unit 24x4  |  cell height 165 z4 (diffusion width from the CH/Z table; name follows)\n"
    "  ch132 z5  |  z3  |  diff width 40\n"
    "  lo 6;14  |  hi 9 11       LO1/HI1ActiveTracks (unit M0 tracks)\n"
    "  flavor ulvt | flavor U | flavor pulvt   DiffPattern VT flavor; the name flavor ID follows\n"
    "Patterns (global = global coordinates, overwrites the unit cell; unit = unit coordinates):\n"
    "  global diff 0:r0,0/r0,0.046,nlvt   global fti 0/2/r2/r0,0:r0   global tcn 0:3,3:r3[1]\n"
    "  global poly 0:3,2/4   global vt 1:3,8   global vg 2,5   global m0cut 2:r2[5],1/3\n"
    "  global vcr 0:r0,6   global vcrnub 3,6,top   global id DBI_mask,drawing,4:r4,3:r3\n"
    "  unit fti 3/6,2:3   (same layer words, appended to the *Unit field)\n"
    "Any field:  set FIELD = value   |   append FIELD += segment\n"
    "Remove shapes at a location (edits the *Unit pattern; right-click a shape for the command):\n"
    "  remove fti at ply 4 diff 3      drop vg at ply 2 m0 5      delete vt at slot 3 m0 7\n"
    "  remove m0 cut at ply 4.5 m0 8   remove tcn plug at slot 3 boundary 4   remove poly plug at ply 2 boundary 3\n"
    "  remove vcr at ply 0:5 vcr 1     remove vcrnub at slot 3 vcr 2   remove tcp at slot 3 boundary 4\n"
    "  positions accept ranges (ply 2:6, m0 3/5); omit an axis to remove along all of it\n"
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
    times = re.search(r"\brepeat\w*\s+(\d+)\s*(?:x|times)?\b|\b(\d+)\s*times\b", words)
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


def _encode_axis(values) -> str:
    """Sorted positions -> compact buildsheet axis (``a:b``, ``a:b[s]``, ``a/b``)."""
    vals = sorted(set(values))
    parts, i = [], 0
    while i < len(vals):
        j = i
        step = vals[i + 1] - vals[i] if i + 1 < len(vals) else 0
        while step and j + 1 < len(vals) and vals[j + 1] - vals[j] == step:
            j += 1
        if j > i and (step == 1 or j - i >= 2):
            parts.append(f"{vals[i]}:{vals[j]}" + (f"[{step - 1}]" if step > 1 else ""))
            i = j + 1
        else:
            parts.append(str(vals[i]))
            i += 1
    return "/".join(parts)


# layer words -> (field, Vcx kind, X axis, Y axis, Y takes all remaining comma parts)
REMOVE_LAYERS = (
    (r"m0\s*cuts?", "M0CutPatternUnit", "", "half", "m0", True),
    (r"(?:poly|ply|gate)\s*plugs?", "PolyPlugsUnit", "", "poly", "boundary", True),
    (r"tcn\s*plugs?", "TcnPlugsUnit", "", "poly", "boundary", True),
    (r"vcrnubs?", "VcrnubPatternUnit", "", "slot", "vcr", False),
    (r"vcrs?", "VcrPatternUnit", "", "poly", "vcr", False),
    (r"tcps?", "TCPSkewPatternUnit", "", "slot", "boundary", False),
    (r"ftis?", "FTITracksUnit", "", "poly", "cell", False),
    (r"vgs?", "VcxPatternUnit", "vg", "poly", "m0", False),
    (r"vts?", "VcxPatternUnit", "vt", "slot", "m0", False),
)
_REMOVE_RE = re.compile(r"^\s*(?:remove|drop|delete|del|erase|take\s+out)\s+(?:the\s+|a\s+|an\s+)?(?P<layer>"
                        + "|".join(pattern for pattern, *_rest in REMOVE_LAYERS) + r")\b(?P<rest>.*)$",
                        re.IGNORECASE)
_AXIS_VALUE = r"([\dr][\dr.:\[\]/]*)"
_X_WORDS = re.compile(r"\b(?:ply|poly|pc|gate|col(?:umn)?|x|tcn\s*slot|slot|tcn)\s*(?:track\s*)?[:=#]?\s*" + _AXIS_VALUE,
                      re.IGNORECASE)
_Y_WORDS = re.compile(r"\b(?:diff(?:usion)?|cell|row|y|m0|metal\s*0|track|boundary|bnd|plug|vcr|crl)"
                      r"\s*(?:track\s*)?[:=#]?\s*" + _AXIS_VALUE, re.IGNORECASE)


def _remove_request(clause: str, row: dict[str, str], changes: dict[str, str]) -> str | None:
    """'remove fti at ply 4 diff 3', 'drop vg at ply 2 m0 5', 'remove m0 cut at ply 4.5 m0 8',
    'remove tcn plug at slot 3 boundary 4': delete those positions from the *Unit buildsheet pattern."""
    match = _REMOVE_RE.match(clause)
    if not match:
        return None
    rest = match["rest"]
    x_match = _X_WORDS.search(rest)
    y_match = _Y_WORDS.search(rest[:x_match.start()] + " " * len(x_match[0]) + rest[x_match.end():]
                              if x_match else rest)
    if not x_match and not y_match:
        return None  # e.g. 'remove fti' = reset the FTI skew
    layer_word = match["layer"].lower()
    _p, field, kind, x_axis, y_axis, y_rest = next(item for item in REMOVE_LAYERS
                                                   if re.fullmatch(item[0], layer_word))
    merged = {**row, **changes}
    if field not in merged:
        raise ValueError(f"{field} is not a buildsheet column.")
    g = geometry_from_row(merged)
    ogd, ncell = g.ogd, g.n_cells
    n_tracks = m0_tracks_per_height(merged) * g.pgd
    x_extent = {"poly": ogd, "slot": ogd - 1, "half": 2 * ogd}[x_axis]
    y_extent = {"m0": n_tracks, "cell": ncell, "boundary": ncell, "vcr": g.pgd}[y_axis]

    def targets(spec: str, extent: int, half: bool = False) -> set[int]:
        if half:
            if re.fullmatch(r"\d+(?:\.5)?", spec):
                return {round(2 * float(spec))}
            return {2 * v for v in expand_axis(spec, extent // 2)}
        values = set(expand_axis(spec, extent))
        if not values:
            raise ValueError(f"Cannot read position '{spec}'.")
        return values

    want_x = targets(x_match[1], x_extent, x_axis == "half") if x_match else None
    want_y = targets(y_match[1], y_extent) if y_match else None
    wrap = ogd if x_axis == "poly" and ogd else 0

    def x_hit(x: int) -> bool:
        return want_x is None or x in want_x or bool(wrap and any(x % wrap == t % wrap for t in want_x))

    def y_hit(y: int) -> bool:
        return want_y is None or y in want_y

    removed = 0
    out: list[str] = []
    for segment in (merged.get(field, "") or "").split(";"):
        if not segment.strip():
            continue
        base, sep, marker = segment.partition("|")
        tag = re.search(r"\?[A-Za-z]\w*", base)
        parts = [p.strip() for p in re.sub(r"\?[A-Za-z]\w*", "", base).split(",")]
        head = parts[:1] if kind else []
        if (kind and parts[0].lower() != kind) or len(parts) < len(head) + 2:
            out.append(segment)
            continue
        x_text = parts[len(head)]
        y_parts = parts[len(head) + 1:] if y_rest else parts[len(head) + 1:len(head) + 2]
        tail = [] if y_rest else parts[len(head) + 2:]
        y_text = "/".join(y_parts)
        xs = expand_axis(x_text, x_extent)
        hit_x = [x for x in xs if x_hit(x)]
        fragments = [(frag, re.search(r"-[A-Za-z]+$", frag.strip()), expand_axis(frag, y_extent))
                     for frag in y_text.split("/") if frag.strip()]
        hit_y = [y for _f, _m, values in fragments for y in values if y_hit(y)]
        if not hit_x or not hit_y:
            out.append(segment)
            continue
        removed += len(set(hit_x)) * len(set(hit_y))
        suffix = sep + marker if sep else ""

        def build(x_spec: str, y_spec: str) -> str:
            return ",".join([*head, x_spec, y_spec + (tag[0] if tag else ""), *tail]) + suffix

        kept_x = [x for x in xs if x not in hit_x]
        if kept_x:
            out.append(build(_encode_axis(kept_x), y_text))
        kept_frags = []
        for frag, modifier, values in fragments:
            remaining = [y for y in values if not y_hit(y)]
            if remaining == values:
                kept_frags.append(frag.strip())
            elif remaining and modifier:
                kept_frags.extend(f"{y}{modifier[0]}" for y in remaining)
            elif remaining:
                kept_frags.append(_encode_axis(remaining))
        if kept_frags:
            out.append(build(_encode_axis(sorted(set(hit_x))), "/".join(kept_frags)))
    if not removed:
        where = " ".join(m[0] for m in (x_match, y_match) if m)
        raise ValueError(f"No {layer_word.upper()} at {where} in {field}.")
    changes[field] = ";".join(out) + (";" if out else "")
    where = " ".join(m[0].strip() for m in (x_match, y_match) if m)
    return f"removed {removed} {layer_word.upper()} position(s) at {where} from {field}"


def set_name_flavor(name: str, flavor_id: str) -> str:
    """Replace the flavor ID token (3rd dash field) of a structure name."""
    return re.sub(r"^([A-Z_]+\d{3}-[A-Z0-9]+-)([A-Z]+)(-)", rf"\g<1>{flavor_id}\g<3>", name, count=1)


def _flavor_edit(polarity: str | None, flavor: str, row: dict[str, str], changes: dict[str, str]) -> str:
    """'flavor ulvt' / 'flavor U' / 'flavor pulvt': set the DiffPattern VT flavor and keep the name flavor ID aligned."""
    from scalar_snapshot import FLAVOR_BY_ID, VT_FLAVORS, diff_flavors, name_flavor_id

    flavor = FLAVOR_BY_ID.get(flavor.upper(), flavor) if polarity is None else flavor
    if flavor not in VT_FLAVORS:
        raise ValueError(f"Unknown VT flavor '{flavor}'. Use {', '.join(VT_FLAVORS)} or a name ID "
                         f"({', '.join(FLAVOR_BY_ID)}).")
    touched = 0
    # Buffer (global) diffusion follows only where it matched the device flavor; deliberate buffer flavors stay.
    device_flavors = {f for p, f in diff_flavors({**row, **changes}) if polarity is None or p == polarity}
    for field in ("DiffPatternUnit", "DiffPatternGlobal"):
        value = changes.get(field, row.get(field, "") or "")
        if not value.strip():
            continue
        updated = []
        for segment in value.split(";"):
            parts = segment.split(",")
            token = re.fullmatch(r"\s*([np])([a-z]+)(.*)", parts[3], re.IGNORECASE) if len(parts) > 3 else None
            if token and (polarity is None or token[1].lower() == polarity) and \
                    (field == "DiffPatternUnit" or token[2].lower() in device_flavors):
                parts[3] = f"{token[1]}{flavor}{token[3]}"
                touched += 1
            updated.append(",".join(parts))
        changes[field] = ";".join(updated)
    if not touched:
        raise ValueError("No DiffPattern segment with a VT flavor to change.")
    note = f"VT flavor -> {VT_FLAVORS[flavor][1]} on {touched} diffusion segment(s)"
    name = changes.get("StructureName", row.get("StructureName", ""))
    flavor_id = VT_FLAVORS[flavor][0]
    remaining = {f for _p, f in diff_flavors({**row, **changes})}
    if name_flavor_id(name) and name_flavor_id(name) != flavor_id and remaining == {flavor}:
        changes["StructureName"] = set_name_flavor(name, flavor_id)
        note += f"; name flavor ID {name_flavor_id(name)} -> {flavor_id}"
    elif len(remaining) > 1:
        note += f"; mixed unit flavors ({', '.join(sorted(remaining))}), name flavor ID left as {name_flavor_id(name)}"
    return note


def _structure_edit(clause: str, row: dict[str, str], changes: dict[str, str]) -> str | None:
    """Buffer/array/terminal/pattern/field edits; returns a note, or None when the clause is not one."""
    words = clause.lower()
    note = _remove_request(clause, row, changes)
    if note:
        return note
    if re.search(r"\b(keep|skip|repeat\w*|repetition)\b", words):
        return _pattern_request(clause, row, changes)

    def current(field: str) -> str:
        return changes.get(field, row.get(field, "") or "")

    match = re.match(r"(?:vt\s+)?flavou?r\s+([np])?([a-z]+)$", words.strip())
    if match:
        return _flavor_edit(match[1], match[2], row, changes)

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
    match = re.match(r"(?:cell\s*height|ch)\s*(?:ch)?(\d{3})(?:\s*(?:,|and|with)?\s*z\s*(\d+))?$", words.strip()) \
        or re.match(r"()z\s*(\d+)$", words.strip())
    if match:
        return _ch_z_edit(int(match[1]) if match[1] else None, int(match[2]) if match[2] else None, None, row, changes)
    match = re.match(r"(?:diff(?:usion)?|dev(?:ice)?)\s*width\s*(\d+)\s*(?:nm)?$", words.strip())
    if match:
        return _ch_z_edit(None, None, int(match[1]), row, changes)
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


# x81Z library offering (standard + turbo logic): cell height -> {Z: diffusion width nm}.
DIFF_WIDTH_TABLE = {
    110: {1: 27, 2: 31, 3: 35, 4: 40, 8: 72},
    132: {2: 31, 3: 35, 4: 40, 5: 46, 6: 49, 8: 72, 9: 86},
    165: {2: 31, 4: 40, 6: 52, 7: 62, 9: 86},
}
_NAME_CH_Z = re.compile(r"^([A-Z_]+)(\d{3})(-(?:NP|N|P))Z(\d+)")


def device_width_nm(row: dict[str, str]) -> int | None:
    for segment in segments(row.get("DiffPatternUnit", "")):
        parts = segment.split(",")
        try:
            return round(float(parts[2]) * 1000) if len(parts) > 2 else None
        except ValueError:
            continue
    return None


def _ch_z_edit(ch: int | None, z: int | None, width: int | None, row: dict[str, str],
               changes: dict[str, str]) -> str:
    """Cell height / Z / diffusion width per the library offering table; DiffPattern and name follow."""
    merged = {**row, **changes}
    old_ch = int(re.sub(r"\D", "", merged.get("CellHeight", "")) or 0)
    old_width = device_width_nm(merged)
    name = merged.get("StructureName", "")
    name_match = _NAME_CH_Z.match(name)
    old_z = int(name_match[4]) if name_match else None
    new_ch = ch or old_ch
    if new_ch not in DIFF_WIDTH_TABLE:
        raise ValueError(f"Cell height {new_ch} is not in the library table ({', '.join(map(str, DIFF_WIDTH_TABLE))}).")
    table = DIFF_WIDTH_TABLE[new_ch]
    offered = ", ".join(f"Z{k}={v}" for k, v in table.items())
    if z is not None:
        if z not in table:
            raise ValueError(f"Z{z} is not offered at CH{new_ch}; use {offered} (nm).")
        new_width = table[z]
    elif width is not None:
        new_width = width
        z = next((k for k, v in table.items() if v == width), None)
        if z is None:
            raise ValueError(f"{width} nm is not offered at CH{new_ch}; use {offered}.")
    elif old_z in table:
        z, new_width = old_z, table[old_z]
    else:
        z = min(table, key=lambda k: abs(table[k] - (old_width or 0)))
        new_width = table[z]
    notes = []
    if new_ch != old_ch:
        changes["CellHeight"] = f"ch{new_ch}"
        notes.append(f"CellHeight ch{old_ch} -> ch{new_ch}")
    if new_width != old_width:
        touched = 0
        for field in ("DiffPatternUnit", "DiffPatternGlobal"):
            value = merged.get(field, "") or ""
            if not value.strip():
                continue
            updated = []
            for segment in value.split(";"):
                parts = segment.split(",")
                try:
                    seg_width = round(float(parts[2]) * 1000) if len(parts) > 2 else None
                except ValueError:
                    seg_width = None
                # Buffer diffusion follows only where it had the device width.
                if seg_width is not None and (field == "DiffPatternUnit" or seg_width == old_width):
                    parts[2] = _um(new_width)
                    touched += 1
                updated.append(",".join(parts))
            changes[field] = ";".join(updated)
        notes.append(f"diffusion width {old_width} -> {new_width} nm on {touched} segment(s)")
    if name_match and (new_ch, z) != (int(name_match[2]), old_z):
        changes["StructureName"] = _NAME_CH_Z.sub(rf"\g<1>{new_ch}\g<3>Z{z}", name, count=1)
        notes.append(f"name {name_match[0]} -> {_NAME_CH_Z.match(changes['StructureName'])[0]}")
    return "; ".join(notes) or f"CH{new_ch} Z{z} ({new_width} nm) already set"


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
        if layer in ("vt", "vg"):
            current_pattern = current("VcxPatternUnit")
            if not current_pattern.strip():
                raise ValueError("VcxPatternUnit is empty in this row; add the VT/VG pattern first.")
            size_match = re.search(r"\bsize\s+(\d+(?:\.\d+)?)\s*[xX]\s*(\d+(?:\.\d+)?)\s*(nm|um)?\b",
                                   clause, re.IGNORECASE)
            if size_match:
                multiplier = 1000 if (size_match[3] or "nm").lower() == "um" else 1
                width_um, height_um = float(size_match[1]) * multiplier / 1000, float(size_match[2]) * multiplier / 1000
                updated, touched = [], 0
                for segment in current_pattern.split(";"):
                    base, separator, marker = segment.partition("|")
                    parts = [part.strip() for part in base.split(",")]
                    if parts and parts[0].lower() == layer:
                        parts.extend([""] * (5 - len(parts)))
                        parts[3], parts[4] = _um(width_um * 1000), _um(height_um * 1000)
                        touched += 1
                        base = ",".join(parts)
                    updated.append(base + (separator + marker if separator else ""))
                if not touched:
                    raise ValueError(f"No {layer.upper()} segments found in VcxPatternUnit.")
                changes["VcxPatternUnit"] = ";".join(updated)
                notes.append(f"{layer.upper()} drawn size {width_um * 1000:g}x{height_um * 1000:g} nm on {touched} segment(s)")
                continue
            align_match = re.search(rf"\b{layer}\s+(top|bottom)(?:\s+([\d:r/\[\]-]+|all))?\b", clause, re.IGNORECASE)
            if align_match:
                alignment = align_match[1].lower()
                row = dict(row)
                try:
                    from ScalarCalculation import geometry_from_row, m0_tracks_per_height

                    geometry = geometry_from_row(row)
                    m0_extent = m0_tracks_per_height(row) * geometry.pgd
                except (TypeError, ValueError):
                    m0_extent = 0
                selected = None if not align_match[2] or align_match[2].lower() == "all" else set(
                    expand_axis(align_match[2], m0_extent))
                updated, touched = [], 0
                for segment in current_pattern.split(";"):
                    base, separator, marker = segment.partition("|")
                    parts = [part.strip() for part in base.split(",")]
                    if len(parts) >= 3 and parts[0].lower() == layer:
                        rebuilt = []
                        for fragment in parts[2].split("/"):
                            token = fragment.strip()
                            is_bottom = token.lower().endswith("-bottom")
                            axis = token[:-7] if is_bottom else token
                            tracks = set(expand_axis(axis, m0_extent))
                            chosen = selected is None or bool(tracks & selected)
                            if chosen:
                                touched += 1
                                rebuilt.extend(f"{track}-bottom" if alignment == "bottom" else str(track)
                                               for track in sorted(tracks))
                            else:
                                rebuilt.append(token)
                        parts[2] = "/".join(rebuilt)
                        base = ",".join(parts)
                    updated.append(base + (separator + marker if separator else ""))
                if not touched:
                    raise ValueError(f"No matching {layer.upper()} M0 track found for {alignment} alignment.")
                changes["VcxPatternUnit"] = ";".join(updated)
                notes.append(f"{layer.upper()} M0 via alignment set to {alignment} on {touched} track fragment(s)")
                continue
        if re.search(r"\b(reset|clear|remove|nominal|nom)\b", words):
            if pattern_field:
                if layer in ("vt", "vg"):
                    changes[field] = ";".join(
                        seg.split("|")[0] if seg.split(",", 1)[0].strip().lower() == layer else seg
                        for seg in current(field).split(";"))
                else:
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
                if layer in ("vt", "vg") and base.split(",", 1)[0].strip().lower() != layer:
                    updated.append(segment)
                    continue
                net = "$" + marker.partition("$")[2] if "$" in marker else ""
                marker_values = marker.partition("$")[0]
                updated.append(f"{base}|{_set_slot(marker_values, index, value_nm)}{net}")
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


FEATURE_LAYERS = {"FTI": ("FTI",), "PXL": ("PXL",), "VCRNUB": ("VCRNUB",), "VCR": ("VCR",),
                  "TCN": ("TCN", "TCP", "PLYFSPCR", "PLYFTCN"), "TCP": ("TCP",), "PLY": ("PLY", "GLK"), "GLK": ("GLK",)}
# Spacing features: a larger shape means a smaller space, so the N/P sign is inverted.
SPACING_FEATURES = ("PLYFSPCR",)


def _row_skews(row: dict[str, str]) -> list[tuple[str, str, str, float]]:
    """(layer, OGD|PGD, CD|REG, nm) for every non-zero skew in the row."""
    from scalar_snapshot import PXL_NOMINAL_CD_NM, _resize_shift_nm, _skew_geometry

    found: list[tuple[str, str, str, float]] = []
    for field, layer in (("FTICdMisregSkew", "FTI"), ("TCNCdRegSkew", "TCN"), ("PolyCdSkew", "PLY"),
                         ("GLKSkew", "GLK")):
        found += [(layer, d, v, x) for d, v, x in _skew_geometry(row, field)]
    pxl = row.get("PXLSkew", "").split(",")
    try:
        if abs(float(pxl[0]) * 1000 - PXL_NOMINAL_CD_NM) > 1e-9:
            found.append(("PXL", "PGD", "CD", round(float(pxl[0]) * 1000 - PXL_NOMINAL_CD_NM, 3)))
        if float(pxl[1]):
            found.append(("PXL", "PGD", "REG", round(float(pxl[1]) * 1000, 3)))
    except (IndexError, ValueError):
        pass
    for field, layer in (("VcrPatternUnit", "VCR"), ("VcrnubPatternUnit", "VCRNUB")):
        for segment in segments(row.get(field, "")):
            if "|" in segment:
                found += [(layer, d, v, x) for d, v, x in _skew_geometry({"v": segment.partition("|")[2]}, "v")]
    for segment in segments(row.get("TCPSkewPatternUnit", "")):
        for (direction, variant), value in zip((("OGD", "CD"), ("PGD", "CD"), ("OGD", "REG"), ("PGD", "REG")),
                                               _resize_shift_nm(segment)):
            if value:
                found.append(("TCP", direction, variant, value))
    unique: dict[tuple[str, str, str], float] = {}
    for layer, direction, variant, value in found:
        unique.setdefault((layer, direction, variant), value)
    return [(*key, value) for key, value in unique.items()]


def recommend_structure_name(row: dict[str, str], baseline: dict[str, str] | None,
                             library_rows: list[dict[str, str]], taken: set[str]) -> tuple[str, list[str]]:
    """BKM name for an edited structure, following the 81Z library grammar
    ``<FAMILY><CH>-<MOS><W>-<VT>-<TYPE>:<FEATURE>:<OGD|PGD>:<CD|REG|REF>:<N|P><nm>|NOM``.

    Only name tokens whose buildsheet inputs changed against ``baseline`` are rewritten, so library naming
    quirks are kept; the N/P token is the signed skew in nm (inverted for spacing features such as PLYFSPCR).
    """
    name = row.get("StructureName", "")
    base = baseline or row
    reasons: list[str] = []
    head, *tail = name.split(":")
    match = re.match(r"([A-Z]+)(\d{3})(-)(NP|N|P)([^-]*)(.*)$", head)
    height = re.search(r"\d{3}", row.get("CellHeight", ""))
    if match and height and (row.get("CellHeight") != base.get("CellHeight")
                             or device_width_nm(row) != device_width_nm(base)):
        family, ch, dash, mos, width, rest = match.groups()
        if height[0] != ch:
            reasons.append(f"cell height {ch} -> {height[0]} (CellHeight changed)")
        table = DIFF_WIDTH_TABLE.get(int(height[0]), {})
        device = device_width_nm(row)
        z_new = next((k for k, v in table.items() if v == device), None)
        if width.startswith("Z") and z_new is not None and f"Z{z_new}" != width:
            reasons.append(f"width code {width} -> Z{z_new} ({device} nm at CH{height[0]}, library offering table)")
            width = f"Z{z_new}"
        elif width.startswith("Z") and z_new is None:
            reasons.append(f"{device} nm is not in the CH{height[0]} offering table; width code {width} kept")
        head = f"{family}{height[0]}{dash}{mos}{width}{rest}"
    from scalar_snapshot import VT_FLAVORS, diff_flavors, name_flavor_id

    flavors = {flavor for _polarity, flavor in diff_flavors(row)}
    if name_flavor_id(head + "-") and flavors != {flavor for _polarity, flavor in diff_flavors(base)}:
        ids = {VT_FLAVORS[flavor][0] for flavor in flavors if flavor in VT_FLAVORS}
        if len(ids) == 1 and (new_id := ids.pop()) != name_flavor_id(head + "-"):
            reasons.append(f"flavor ID {name_flavor_id(head + '-')} -> {new_id} (DiffPattern VT flavor changed)")
            head = set_name_flavor(head + "-", new_id)[:-1]
    skews, base_skews = _row_skews(row), _row_skews(base)
    if len(tail) == 4 and sorted(skews) != sorted(base_skews):
        feature, direction, variant, value = tail
        changed = [s for s in skews if s not in base_skews]
        if changed:
            matching = [s for s in changed if any(t in feature.upper() for t in FEATURE_LAYERS[s[0]])]
            layer, new_dir, new_variant, nm = (matching or changed)[0]
            if not matching:
                default = "FTIML" if layer == "FTI" and head.startswith(("DIFFOGD", "GATEGATE")) else layer
                reasons.append(f"feature {feature} -> {default} (new skew is on {layer})")
                feature = default
            negative = (nm < 0) != feature.upper().startswith(SPACING_FEATURES)
            new_value = f"{'N' if negative else 'P'}{abs(nm):g}"
            for label, old, new in (("direction", direction, new_dir), ("variant", variant, new_variant),
                                    ("value", value, new_value)):
                if old != new:
                    reasons.append(f"{label} {old} -> {new} ({layer} {new_dir} {new_variant} {nm:+g} nm)")
            direction, variant, value = new_dir, new_variant, new_value
            others = [s for s in skews if s[:3] != (layer, new_dir, new_variant)]
            if others:
                reasons.append("other skews also set: " + ", ".join(f"{s[0]} {s[1]} {s[2]} {s[3]:+g}" for s in others))
        elif not skews and re.fullmatch(r"[NP]\d+(\.\d+)?", value):
            reasons.append(f"skew removed: {value} -> NOM")
            value = "NOM"
        tail = [feature, direction, variant, value]
    recommended = ":".join([head, *tail])
    library_names = {r.get("StructureName", "") for r in library_rows}
    edited = baseline is not None and any(row.get(k, "") != baseline.get(k, "") for k in row if k != "StructureName")
    if recommended in taken or (recommended in library_names and (recommended != name or edited)):
        stem, index = recommended, 2
        while f"{stem}_V{index}" in library_names | taken:
            index += 1
        recommended = f"{stem}_V{index}"
        reasons.append(f"name already used by {'another test-row entry' if stem in taken else 'a library structure'}; "
                       f"added _V{index}")
    return recommended, reasons


def tsdr_suggest(text: str, row: dict[str, str] | None) -> str:
    """One-line hint for the Edit box: a dry-run result when the text parses, else the syntax to finish it."""
    clause = re.split(r";\s+", text)[-1].strip()
    words = clause.lower()
    if re.fullmatch(r"fti\s+mode\s+(ml|sl)", clause, re.IGNORECASE):
        return "Enter to apply: copy the matching FTIML/FTISL library variant's FTI tracks and feature name"
    if not words:
        return ("Try:  fti pgd cd -3nm   |   tcn ogd reg +2   |   buffer 3   |   "
                "tcn plug start 4 keep 3 more skip 1 repeat   |   set FIELD = value")
    if row is not None:
        try:
            _changes, notes = parse_tsdr_request(text, row)
            from scalar_snapshot import (buildsheet_review_notes, buildsheet_syntax_issues,
                                         design_rule_issues, vcrnub_rule_issues)

            edited = {**row, **_changes}
            syntax_issues = buildsheet_syntax_issues(edited, tuple(_changes))
            if syntax_issues:
                label = "Unsupported edit: " if any(issue.startswith("PolyPitch") for issue in syntax_issues) else "Syntax error: "
                return label + "; ".join(syntax_issues)
            if _changes.get("PolyPitch", "pp44").strip().lower() != "pp44":
                return "Unsupported edit: this layout model is fixed at pp44 (44 nm)"
            if any(field.startswith("VcrnubPattern") for field in _changes):
                issues = vcrnub_rule_issues(edited)
                if issues:
                    return "Blocked: " + "; ".join(issues)
            issues = design_rule_issues(edited, tuple(_changes))
            from tsdr_rules import review_rules

            coverage = review_rules(edited, tuple(_changes))
            notes = [*notes, *buildsheet_review_notes(edited, tuple(_changes))]
            if issues:
                return "Enter to apply:  " + ";  ".join(notes) + "   |   Rule review: " + "; ".join(issues[:2])
            return "Enter to apply:  " + ";  ".join(notes) + "   |   " + coverage.plain_summary
        except ValueError:
            pass
    first = words.split()[0]
    if re.search(r"\b(keep|skip|repeat\w*|repetition)\b", words) or first in ("start", "from"):
        return "<layer> start N keep K [more] skip S repeat [N times | until N] [y: 2/4 | y: start … repeat] [replace]"
    if first in ("set", "append"):
        typed = words.split()[1] if len(words.split()) > 1 else ""
        fields = [f for f in (row or {}) if typed and f.lower().startswith(typed.replace("=", ""))][:4]
        verb = "set FIELD = value" if first == "set" else "append FIELD += segment"
        return verb + (f"     fields: {', '.join(fields)}" if fields else "")
    if first == "buffer":
        return "buffer 3   |   buffer L2 R2 B1 T1   |   buffer left 6"
    if first in ("flavor", "flavour") or words.startswith("vt flav"):
        return ("flavor ulvt | flavor U | flavor pulvt   (lvt svt hvt ulvt uvt elvt evt ulvtll; "
                "name flavor ID follows: L S H U E UL)")
    if first in ("array", "unit") and not re.search(r"\b(global|unit)\s+\w+\s+\S", words):
        return f"{first} OGDxPGD   e.g. {first} {'5x60' if first == 'array' else '24x4'}"
    if re.match(r"(cell|ch\d|ch$|z\d|z$|diff(usion)?\s*w|dev)", words):
        return "cell height 165 z4  |  ch132 z5  |  z3  |  diff width 40   (" + "; ".join(
            f"CH{ch}: " + " ".join(f"Z{z}={w}" for z, w in table.items()) for ch, table in DIFF_WIDTH_TABLE.items()) + ")"
    if first in ("lo", "hi", "lo1", "hi1"):
        return f"{first[:2]} 6;14   (unit M0 track numbers for {first[:2].upper()}1ActiveTracks)"
    if first in ("reset", "clear", "nominal"):
        return "reset fti | tcn | poly | glk | pxl | vcr | vcrnub | tcp"
    if first in ("remove", "drop", "delete", "del", "erase"):
        return ("remove fti at ply 4 diff 3  |  remove vg at ply 2 m0 5  |  remove vt at slot 3 m0 7  |  "
                "remove m0 cut at ply 4.5 m0 8  |  remove tcn plug at slot 3 boundary 4   "
                "(no position: 'remove fti' resets the FTI skew)")
    if first in ("id", "add", "draw"):
        return "id <layer> <name> x 0:50 y 2:5     e.g. id tcn TSDRActiveL x 0:50 y 2:5"
    if first in ("global", "unit"):
        layer = words.split()[1] if len(words.split()) > 1 else ""
        examples = {"diff": "0:r0,0/r0,0.046,nlvt", "fti": "0/2/r2/r0,0:r0", "tcn": "0:3,3:r3[1]",
                    "poly": "0:3,2/4", "vt": "1:3,8", "vg": "2,5", "m0cut": "2:r2[5],1/3", "vcr": "0:r0,6",
                    "vcrnub": "3,6,top", "id": "DBI_mask,drawing,4:r4,3:r3"}
        if layer in examples:
            return f"{first} {layer} X,Y   e.g. {first} {layer} {examples[layer]}"
        return f"{first} <diff|fti|tcn|poly|vt|vg|m0cut|vcr|vcrnub|id> X,Y"
    layer = next((key for key in sorted(TSDR_FIELDS, key=len, reverse=True) if re.search(rf"\b{key}\b", words)), None)
    if layer:
        if layer in ("pxl", "trm"):
            return f"{layer} cd ±N  |  {layer} reg ±N     (nm; n/p prefix also works, e.g. pxl cd p5)"
        missing = [part for part, pattern in (("ogd|pgd", r"\b(ogd|pgd)\b"),
                                              ("cd|reg", r"\b(cd|reg|misreg|width|shift)\b"),
                                              ("±N nm", r"\d")) if not re.search(pattern, words)]
        hint = f"{layer} ogd|pgd cd|reg ±N nm"
        if layer in ("vcr", "vcrnub", "tcp") and row is not None and not row.get(TSDR_FIELDS[layer], "").strip():
            hint += f"   ({TSDR_FIELDS[layer]} is empty in this row)"
        return hint + (f"     still needed: {', '.join(missing)}" if missing else "")
    return ("Start with a layer (fti, tcn, poly, glk, pxl, vcr, vcrnub, tcp), or buffer / array / unit / lo / hi / "
            "global / set / reset / id")


ATLANTIS_SEARCH_NOISE = STOPWORDS | frozenset({
    "find", "search", "show", "list", "rows", "row", "testrow", "latest", "current", "matching", "track",
    "library", "libraries", "database", "project", "projects", "structure", "structures", "direct",
    "owner", "owned", "status", "state", "modified", "updated", "named", "called", "containing",
})
ATLANTIS_FAMILY_ALIASES = {
    "CTG": ("ctg", "contacttogate", "contactgate"),
    "GATEGATE": ("gategate", "gatetoend", "ete"),
    "EPIEPI": ("epiepi", "epitoepi", "epi"),
    "TCNTCN": ("tcntcn", "tcntotcn", "tcn"),
    "DIFFOGD": ("diffogd", "diffusionogd", "diffusionisolation", "isolation"),
    "DIFFCHN": ("diffchn", "diffusionchain", "chain"),
}


ATLANTIS_IDENTITY_KEYS = ("TestRowName", "TestRow", "StructureName", "PadRowName", "Description",
                          "StructureComments")
ATLANTIS_FAMILY_TERMS = {
    "CTG": {"ctg", "contact", "gate", "to"},
    "GATEGATE": {"gate", "ete", "end"},
    "EPIEPI": {"epi"},
    "TCNTCN": {"tcn", "trench", "contact"},
    "DIFFOGD": {"diff", "diffusion", "isolation"},
    "DIFFCHN": {"diff", "diffusion", "chain"},
}


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.casefold())


def _match_rank(query: str, text: str) -> int | None:
    """0 = contains the query, 1 = contains every word of it (any order), 2 = its characters appear in order
    (e.g. 'iso 81z' -> isox81z_x81_lay); None = no match."""
    words = re.findall(r"[a-z0-9]+", query.casefold())
    target = _normalize(text)
    joined = "".join(words)
    if not joined or joined in target:
        return 0
    if all(word in target for word in words):
        return 1
    position = 0
    for char in joined:
        position = target.find(char, position) + 1
        if not position:
            return None
    return 2


def _atlantis_row_matcher(query: str):
    """Apply simple word matching while anchoring family/height/direction to row identity."""
    words = [word for word in re.findall(r"[a-z0-9]+", query.casefold())
             if word not in ATLANTIS_SEARCH_NOISE]
    terms = {_normalize(word) for word in words} - {""}
    normalized_query = _normalize(query)
    family_aliases = next(((family, aliases) for family, aliases in ATLANTIS_FAMILY_ALIASES.items()
                           if any(_normalize(alias) in normalized_query for alias in aliases)), None)
    identity_keys = ("TestRowName", "Description", "PadRowTemplate", "TemplatesUsed")
    identity_terms: list[tuple[str, ...]] = []
    if family_aliases:
        family, aliases = family_aliases
        identity_terms.append(tuple(_normalize(alias) for alias in aliases))
        terms.difference_update({_normalize(term) for term in ATLANTIS_FAMILY_TERMS[family]})
    for direction in ("ogd", "pgd"):
        if direction in terms:
            identity_terms.append((direction,))
            terms.discard(direction)
    for height in ("110", "132", "165"):
        if height in terms:
            identity_terms.append((height,))
            terms.discard(height)
    if not terms and not identity_terms:
        return lambda _item: True

    def match(item: dict) -> bool:
        if any(_match_rank(query, str(item.get(key) or "")) in (0, 1) for key in ("TestRowName", "Library")):
            return True
        if normalized_query and any(normalized_query in _normalize(str(value)) for key, value in item.items()
                                    if key != "BatchResult" and value not in (None, "")):
            return True
        identity = [_normalize(str(item.get(key) or "")) for key in identity_keys]
        if any(not any(alias in value for alias in alternatives for value in identity)
               for alternatives in identity_terms):
            return False
        searchable = _normalize(" ".join(str(value) for key, value in item.items()
                                           if key != "BatchResult" and value not in (None, "")))
        return all(term in searchable for term in terms)

    return match


def _atlantis_matcher(query: str, fields: list[str]):
    """Predicate for Atlantis rows/structures: exact-fragment match first, then natural-language criteria."""
    query = query.strip().casefold()
    if not query:
        return lambda _item: True
    fragment = _normalize(query)
    try:
        spec, _notes = parse_prompt(query, fields)
    except ValueError:
        spec = None
    terms = {word for word in re.findall(r"[a-z0-9]+", query)
             if len(word) > 1 and not word.isdigit() and word not in ATLANTIS_SEARCH_NOISE}
    aliases: tuple[str, ...] = ()
    height = direction = ""
    flavor = None
    if spec:
        terms.update(token for token in spec.tokens if token not in ATLANTIS_SEARCH_NOISE)
        if spec.type_letter:
            terms.add(spec.type_letter.casefold())
        if spec.family:
            aliases = tuple(_normalize(a) for a in ATLANTIS_FAMILY_ALIASES.get(spec.family, (spec.family,)))
            terms.difference_update(ATLANTIS_FAMILY_TERMS.get(spec.family, set()))
        height = str(spec.cell_height or "")
        direction = (spec.direction or "").casefold()
        terms.discard(height)
        terms.discard(direction)
        if spec.flavor in FLAVOR_LETTER:
            # Names carry the VT flavor as a letter field, e.g. ULVT -> "-U-".
            flavor = (spec.flavor, re.compile(rf"-{FLAVOR_LETTER[spec.flavor]}-", re.IGNORECASE))
            terms.discard(spec.flavor)
    terms = {_normalize(term) for term in terms} - {""}

    def match(item: dict) -> bool:
        searchable = [_normalize(str(value)) for value in item.values() if value not in (None, "")]
        if fragment and any(fragment in value for value in searchable):
            return True
        # Family/height/direction must come from names or descriptions, not from paths or dates.
        raw_identity = [str(item[key]) for key in ATLANTIS_IDENTITY_KEYS if item.get(key)]
        identity = [_normalize(value) for value in raw_identity] or searchable
        if aliases and not any(alias in value for alias in aliases for value in identity):
            return False
        if height and not any(height in value for value in identity):
            return False
        if direction and not any(direction in value for value in identity):
            return False
        if flavor and not any(flavor[0] in value or flavor[1].search(raw) for value, raw in zip(identity, raw_identity)):
            return False
        return all(any(term in value for value in searchable) for term in terms)

    return match


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


ALL_OPEN_ROWS = "All open test rows"


class _HeaderFilter:
    """Excel-style filters on a ttk.Treeview: click a column header to tick values, search them, sort or clear."""

    BLANK = "(blank)"

    def __init__(self, tree: ttk.Treeview, labels: dict[str, str], rows, on_change) -> None:
        self.tree, self.labels, self.rows, self.on_change = tree, labels, rows, on_change
        self.selected: dict[str, set[str]] = {}
        self.sort: tuple[str, bool] | None = None
        self.popup: tk.Toplevel | None = None
        tree.bind("<Button-1>", self._clicked, add="+")
        self._headings()

    def accepts(self, values: dict[str, str], skip: str | None = None) -> bool:
        return all((values.get(key) or self.BLANK) in chosen for key, chosen in self.selected.items() if key != skip)

    def order(self, items: list[tuple[str, dict[str, str]]]) -> list[tuple[str, dict[str, str]]]:
        if not self.sort:
            return items
        key, reverse = self.sort
        return sorted(items, key=lambda item: str(item[1].get(key) or "").casefold(), reverse=reverse)

    def clear(self) -> None:
        self.selected.clear()
        self.sort = None
        self._headings()
        self.on_change()

    def summary(self) -> str:
        return ", ".join(f"{self.labels[key]} ({len(values)})" for key, values in self.selected.items())

    def _headings(self) -> None:
        for key, label in self.labels.items():
            mark = " \u25bc" if key in self.selected else " \u25be"
            if self.sort and self.sort[0] == key:
                mark += " \u2193" if self.sort[1] else " \u2191"
            self.tree.heading(key, text=label + mark)

    def _clicked(self, event):
        if self.tree.identify_region(event.x, event.y) != "heading":
            return None
        column = self.tree.identify_column(event.x)
        shown = self.tree["displaycolumns"]
        columns = self.tree["columns"] if not shown or shown[0] == "#all" else shown
        try:
            key = columns[int(column.lstrip("#")) - 1]
        except (ValueError, IndexError):
            return None
        if key in self.labels:
            self._open(key, event.x_root, event.y_root)
            return "break"
        return None

    def _open(self, key: str, x: int, y: int) -> None:
        if self.popup is not None and self.popup.winfo_exists():
            self.popup.destroy()
        counts: dict[str, int] = {}
        for _iid, values in self.rows():
            if self.accepts(values, skip=key):
                value = values.get(key) or self.BLANK
                counts[value] = counts.get(value, 0) + 1
        options = sorted(counts, key=str.casefold)
        chosen = set(self.selected.get(key, options))
        popup = self.popup = tk.Toplevel(self.tree)
        popup.title(f"Filter: {self.labels[key]}")
        popup.transient(self.tree.winfo_toplevel())
        popup.geometry(f"+{x}+{y + 8}")
        frame = ttk.Frame(popup, padding=6)
        frame.pack(fill="both", expand=True)
        sort_bar = ttk.Frame(frame)
        sort_bar.pack(fill="x")

        def sort(reverse: bool) -> None:
            self.sort = (key, reverse)
            self._headings()
            popup.destroy()
            self.on_change()

        ttk.Button(sort_bar, text="Sort A \u2192 Z", command=lambda: sort(False)).pack(side="left")
        ttk.Button(sort_bar, text="Sort Z \u2192 A", command=lambda: sort(True)).pack(side="left", padx=(4, 0))
        search = tk.StringVar()
        ttk.Entry(frame, textvariable=search).pack(fill="x", pady=(6, 4))
        all_var = tk.BooleanVar(value=len(chosen) == len(options))
        box = ttk.Frame(frame)
        box.pack(fill="both", expand=True)
        listbox = tk.Listbox(box, selectmode="multiple", exportselection=False, height=14, width=46,
                             activestyle="none", font=("Segoe UI", 9))
        scroll = ttk.Scrollbar(box, orient="vertical", command=listbox.yview)
        listbox.configure(yscrollcommand=scroll.set)
        listbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        shown: list[str] = []

        def remember() -> None:
            picked = {shown[index] for index in listbox.curselection()}
            chosen.difference_update(set(shown) - picked)
            chosen.update(picked)

        def fill(*_args) -> None:
            text = search.get().strip().casefold()
            shown[:] = [value for value in options if text in value.casefold()]
            listbox.delete(0, "end")
            for index, value in enumerate(shown):
                listbox.insert("end", f"{value}   ({counts[value]})")
                if value in chosen:
                    listbox.selection_set(index)

        def toggle_all() -> None:
            if all_var.get():
                chosen.update(shown)
            else:
                chosen.difference_update(shown)
            fill()

        def apply() -> None:
            remember()
            if chosen >= set(options):
                self.selected.pop(key, None)
            else:
                self.selected[key] = set(chosen)
            self._headings()
            popup.destroy()
            self.on_change()

        def clear() -> None:
            self.selected.pop(key, None)
            self._headings()
            popup.destroy()
            self.on_change()

        listbox.bind("<<ListboxSelect>>", lambda _e: remember())
        search.trace_add("write", fill)
        ttk.Checkbutton(frame, text="(Select all shown)", variable=all_var, command=toggle_all).pack(
            anchor="w", before=box)
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(buttons, text="OK", command=apply).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=popup.destroy).pack(side="right", padx=(0, 4))
        ttk.Button(buttons, text="Clear filter", command=clear).pack(side="left")
        popup.bind("<Return>", lambda _e: apply())
        popup.bind("<Escape>", lambda _e: popup.destroy())
        fill()
        popup.focus_set()


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Structure Library")
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
        try:
            self.state("zoomed")
        except tk.TclError:
            pass
        self.designer: StructureDesigner | None = None
        self.schema_designer: StructureDesigner | None = None
        self.atlantis_designer: StructureDesigner | None = None
        self.atlantis_client = None
        self.atlantis_window: ttk.Frame | None = None
        self.atlantis_choice: tuple[str, str] = ("", "")
        self.atlantis_results: "queue.Queue[tuple]" = queue.Queue()
        self.atlantis_tokens: dict[str, int] = {}
        self.library_source = "Atlantis"
        self.design: Design | None = None
        self.baseline: Design | None = None
        self.edit_field = tk.StringVar()
        self.edit_value = tk.StringVar()
        self.edit_feedback = tk.StringVar()
        self.edit_field.trace_add("write", lambda *_args: self._edit_feedback())
        self.edit_value.trace_add("write", lambda *_args: self._edit_feedback())
        self.photo: ImageTk.PhotoImage | None = None
        self.full_photo: ImageTk.PhotoImage | None = None
        self.full_image: Image.Image | None = None
        self.current_png: Path | None = None
        self.zoom_var = tk.StringVar(value="Fit")
        self.zoom_percent = tk.StringVar(value="100%")
        self.zoom_scale = 1.0
        self.tsdr_var = tk.StringVar(value="Off")
        self.query_var = tk.StringVar()
        self.testrow_var = tk.StringVar(value=ALL_OPEN_ROWS)
        self.library_source_var = tk.StringVar(value="Source: Atlantis (open a test row)")
        self.layer_vars: dict[str, tk.BooleanVar] = {}
        self.layer_buttons: list[ttk.Checkbutton] = []
        self.library_iids: dict[str, dict[str, str]] = {}
        self.results: "queue.Queue[tuple]" = queue.Queue()
        self.sharp_results: "queue.Queue[tuple]" = queue.Queue()
        self.render_scale = 1
        self._wanted_scale = 1
        self.base_size = (1, 1)
        self.base_ppu = 0.0
        self.base_png: Path | None = None
        self._sharp_after: str | None = None
        self.snapped = [False, False]
        self.testrow_items: list[dict] = []
        self.busy = False
        self.search_visible = True
        self.trm_forced = False
        self._build()
        self._poll_after = self.after(100, self._poll)
        self.after(150, self._load_atlantis)
        self.protocol("WM_DELETE_WINDOW", self._close)

    # -- layout --------------------------------------------------------------
    def _build(self) -> None:
        self.workspace_tabs = ttk.Notebook(self)
        self.workspace_tabs.pack(fill="both", expand=True)
        self.structure_workspace = ttk.Frame(self.workspace_tabs)
        self.workspace_tabs.add(self.structure_workspace, text="Structure viewer")
        top = ttk.Frame(self.structure_workspace, padding=6)
        top.pack(fill="x")
        self.search_toggle = ttk.Button(top, text="Hide search", command=self._toggle_search)
        self.search_toggle.pack(side="left", padx=(0, 8))
        filters = ttk.Frame(top)
        filters.pack(side="left", fill="x", expand=True)
        self.search_filters = filters
        ttk.Label(filters, text="Search structure").pack(side="left", padx=(0, 6))
        self.query_entry = ttk.Entry(filters, textvariable=self.query_var, width=36)
        self.query_entry.pack(side="left")
        self.query_entry.bind("<Return>", self._filter_library)
        ttk.Label(filters, text="Test row").pack(side="left", padx=(12, 4))
        self.testrow_filter = ttk.Combobox(
            filters, textvariable=self.testrow_var, state="readonly", values=(ALL_OPEN_ROWS,), width=28,
        )
        self.testrow_filter.pack(side="left")
        self.testrow_filter.bind("<<ComboboxSelected>>", self._switch_test_row)
        _Tooltip(self.testrow_filter, "Switch between the open test rows (select several rows in Atlantis lookup\n"
                                      "and press Open to batch-open them).")
        self.result_count = tk.StringVar(value="Open an Atlantis test row")
        ttk.Label(filters, textvariable=self.result_count).pack(side="right", padx=(8, 0))
        ttk.Label(filters, textvariable=self.library_source_var).pack(side="right", padx=(8, 0))
        self.query_var.trace_add("write", self._filter_library)

        main_split = ttk.PanedWindow(self.structure_workspace, orient="vertical")
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
        self.library_filter = _HeaderFilter(
            self.library_table, {"testrow": "Test row", "family": "Family", "name": "Structure name"},
            self._library_items, self._filter_library)
        self.library_iids = {}

        preview = ttk.Frame(body)
        body.add(preview, weight=5)
        header = ttk.Frame(preview)
        header.pack(fill="x")
        self.add_testrow_btn = ttk.Button(header, text="+ Add to test row", command=self._testrow_add_current)
        self.add_testrow_btn.pack(side="right")
        paste_btn = ttk.Button(header, text="Paste buildsheet", command=self._testrow_paste)
        paste_btn.pack(side="right", padx=(0, 4))
        _Tooltip(paste_btn, "Paste rows copied from Excel / CSV / another tool (header matched by name)\n"
                            "into the test row and open the first one for viewing and editing.")
        atlantis_btn = ttk.Button(header, text="Atlantis lookup", command=self._load_atlantis)
        atlantis_btn.pack(side="right", padx=(0, 4))
        _Tooltip(atlantis_btn, "Browse Atlantis projects, libraries, test rows, and structures using your Windows login.")
        self.testrow_count = tk.StringVar(value="Test row: 0")
        ttk.Label(header, textvariable=self.testrow_count, foreground="#1f4e79").pack(side="right", padx=(0, 8))
        self.title_var = tk.StringVar(value="Select a structure")
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
        self.tsdr_hint = tk.StringVar(value=tsdr_suggest("", None))
        ttk.Label(preview_tools, textvariable=self.tsdr_hint, foreground="#1f6fb2", font=("Consolas", 9)).pack(
            side="bottom", anchor="w", fill="x", pady=(2, 0))
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
        self.tsdr_request.trace_add("write", lambda *_a: self.tsdr_hint.set(
            tsdr_suggest(self.tsdr_request.get(), self.design.row if self.design else None)))
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
        self.copy_legend_var = tk.BooleanVar(value=True)
        legend_check = ttk.Checkbutton(zoom_tools, text="Include legend", variable=self.copy_legend_var)
        legend_check.pack(side="right", padx=(0, 6))
        _Tooltip(legend_check, "Include the layout legend beside the image when copying PNG.")
        self.copy_image_btn = ttk.Button(zoom_tools, text="Copy PNG", command=self._copy_layout_image)
        self.copy_image_btn.pack(side="right")
        catalog_btn = ttk.Button(zoom_tools, text="Catalog", command=self._show_catalog)
        catalog_btn.pack(side="right", padx=(0, 4))
        _Tooltip(catalog_btn, "Show the slides of the structure catalogs (81Z Catalog folder) that name this\n"
                              "structure, its test row or its skew feature, to compare with the preview.")
        rules_btn = ttk.Button(zoom_tools, text="Design rules", command=self._show_design_rules)
        rules_btn.pack(side="right", padx=(0, 4))
        _Tooltip(rules_btn, "Search the supplied TSDR outsheet by rule label, layer or description.")
        klayout_btn = ttk.Button(zoom_tools, text="Open in KLayout", command=self._open_klayout)
        klayout_btn.pack(side="right", padx=(0, 4))
        self.gds_source = tk.StringVar(value="Generator (buildsheet)")
        gds_box = ttk.Combobox(zoom_tools, textvariable=self.gds_source, state="readonly", width=20,
                               values=("Generator (buildsheet)", "Preview drawing"))
        gds_box.pack(side="right", padx=(0, 4))
        _Tooltip(gds_box, "Generator: full-array GDS from claude_iso_gds_generator (chassis + buildsheet transforms,\n"
                          "real layers incl. M1/V0/dummies; chassis library is CTG132).\n"
                          "Preview drawing: exactly what the preview shows (current view and layers).")
        _Tooltip(klayout_btn, "Write the working layout (current view, units and visible layers) as GDS in nm\n"
                              "and open it in KLayout with named, coloured layers (.lyp).\n"
                              "Mask layers use the chassis numbers (POLY 2/0, TCN 5/0, VG 32/0, ...);\n"
                              "FTI/TCP/VCR and overlays (nets, TSDR, nominal outlines) are on 1000+ layers.")
        measure_tools = ttk.LabelFrame(controls, text="Measure", style="Tool.TLabelframe", padding=(6, 2))
        measure_tools.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(2, 0))
        self.measure_var = tk.BooleanVar(value=False)
        measure_check = ttk.Checkbutton(measure_tools, text="Measure", variable=self.measure_var,
                        command=self._measure_toggled, style="Toolbutton")
        measure_check.pack(side="left")
        self.dr_highlight_var = tk.BooleanVar(value=False)
        dr_check = ttk.Checkbutton(measure_tools, text="DR highlights", variable=self.dr_highlight_var,
                       command=self._draw_dr_overlay)
        dr_check.pack(side="left", padx=(8, 0))
        self.dr_details_var = tk.BooleanVar(value=False)
        self._dr_detail_listeners: list = []
        dr_details = ttk.Checkbutton(measure_tools, text="DR details", variable=self.dr_details_var,
                                     command=self._dr_details_changed)
        dr_details.pack(side="left", padx=(6, 0))
        _Tooltip(dr_details, "Off: design rules and DR warnings in plain language.\n"
                             "On: original outsheet rule text, sources and the list of unchecked rule groups.")
        self.dr_highlight_count = tk.StringVar()
        ttk.Label(measure_tools, textvariable=self.dr_highlight_count, foreground="#b00020").pack(side="left")
        _Tooltip(dr_check, "Numbered boxes locate geometric advisories in Unit view (1 x 1).\n"
                   "Design rules > Review structure lists matching rule labels and coordinates.")
        _Tooltip(measure_check, "Toggle measurement mode with K. The shortcut is ignored while typing in a text field.\n"
                    "Measurement grid in nm with minor lines (labels adapt to zoom).\n"
                                "Click two points per measurement; measurements are kept and numbered.\n"
                                "x: 44 nm per poly pitch, y: CH/2 per diffusion track.")
        self.snap_var = tk.BooleanVar(value=True)
        snap_check = ttk.Checkbutton(measure_tools, text="Snap", variable=self.snap_var)
        snap_check.pack(side="left", padx=(10, 0))
        self.keep_measure_var = tk.BooleanVar(value=True)
        keep_check = ttk.Checkbutton(measure_tools, text="Keep", variable=self.keep_measure_var)
        keep_check.pack(side="left", padx=(10, 0))
        self.straight_var = tk.BooleanVar(value=True)
        straight_check = ttk.Checkbutton(measure_tools, text="Straight (H/V)", variable=self.straight_var)
        straight_check.pack(side="left", padx=(10, 0), before=keep_check)
        _Tooltip(straight_check, "Lock the second point horizontally or vertically to the first,\n"
                                 "so every measurement is a pure dx or dy (no angled distances).")
        _Tooltip(keep_check, "Keep measurements when opening another structure, changing layers or zooming.\n"
                             "Off: measurements are cleared when a different structure is opened.")
        self.measure_menu_btn = ttk.Menubutton(measure_tools, text="Measurements (0)")
        self.measure_menu = tk.Menu(self.measure_menu_btn, tearoff=False)
        self.measure_menu_btn.configure(menu=self.measure_menu)
        self.measure_menu_btn.pack(side="left", padx=(10, 0))
        _Tooltip(self.measure_menu_btn, "List, copy or erase individual measurements.\n"
                                        "Right-click a measurement on the layout to erase just that one;\n"
                                        "right-click elsewhere or Esc cancels a half-finished pick.")
        ttk.Button(measure_tools, text="Erase all", command=self._measure_clear_all).pack(side="left", padx=(4, 0))
        remove_hint = ttk.Label(measure_tools, foreground="#6b7680",
                                text="right-click a shape to remove it from the buildsheet, or a measurement to erase it")
        remove_hint.pack(side="left", padx=(10, 0))
        _Tooltip(remove_hint, "Right-click an FTI / VG / VT / M0 cut / PLY or TCN plug in the Unit view to remove it.\n"
                              "This runs the matching Edit command (e.g. 'remove fti at ply 4 diff 3') on the working\n"
                              "copy and recalculates; 'Reset working copy' undoes it.")
        _Tooltip(snap_check, "Snap measurement picks to the nearest drawn edge, line, track or via centre\n"
                             "(within 8 screen pixels, each axis independently) for exact distances.")
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
            ttk.Spinbox(view_tools, from_=1, to=500, width=3, textvariable=variable,
                        command=self._global_view_changed).pack(side="left")
            if variable is self.global_units_ogd:
                ttk.Label(view_tools, text="x").pack(side="left")
        full_btn = ttk.Button(view_tools, text="Full structure", command=self._show_full_structure)
        full_btn.pack(side="left", padx=(6, 0))
        _Tooltip(full_btn, "Global view of the whole structure as built: the library array\n"
                           "(ArraySizeOgd x ArraySizePgd unit cells) inside its buffer ring.")
        unit_btn = ttk.Button(view_tools, text="Unit cell", command=self._show_unit_cell)
        unit_btn.pack(side="left", padx=(4, 0))
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
        layer_box.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=(2, 2))
        self.layer_control_frame = ttk.Frame(layer_box)
        self.layer_control_frame.pack(fill="x")
        self._layer_reflow_after = None
        self.layer_control_frame.bind("<Configure>", self._schedule_layer_reflow)
        self._layer_columns = 0
        self._set_layer_controls(None)
        view_area = ttk.Frame(preview)
        view_area.pack(fill="both", expand=True)
        legend_pane = ttk.LabelFrame(view_area, text="Legend", style="Tool.TLabelframe", padding=(4, 2))
        legend_pane.pack(side="right", fill="y", padx=(4, 0))
        self.legend_canvas = tk.Canvas(legend_pane, width=250, background="white", highlightthickness=0)
        legend_scroll = ttk.Scrollbar(legend_pane, orient="vertical", command=self.legend_canvas.yview)
        self.legend_canvas.configure(yscrollcommand=legend_scroll.set)
        self.legend_canvas.pack(side="left", fill="y", expand=True)
        legend_scroll.pack(side="left", fill="y")
        self.legend_photo: ImageTk.PhotoImage | None = None
        image_frame = ttk.Frame(view_area)
        image_frame.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(image_frame, background="white", highlightthickness=0,
                    xscrollcommand=lambda *args: (xscroll.set(*args), self._draw_rulers(), self._draw_guide_extensions()),
                    yscrollcommand=lambda *args: (yscroll.set(*args), self._draw_rulers(), self._draw_guide_extensions()))
        xscroll = ttk.Scrollbar(image_frame, orient="horizontal", command=self.canvas.xview)
        yscroll = ttk.Scrollbar(image_frame, orient="vertical", command=self.canvas.yview)
        ruler_font = ("Segoe UI", 8)
        self.ruler_top = tk.Canvas(image_frame, height=20, background="#f4f6f8", highlightthickness=0)
        self.ruler_left = tk.Canvas(image_frame, width=54, background="#f4f6f8", highlightthickness=0)
        self.ruler_right = tk.Canvas(image_frame, width=92, background="#f4f6f8", highlightthickness=0)
        self.ruler_font = ruler_font
        ttk.Label(image_frame, text="diff", font=ruler_font, foreground="#4a5560").grid(row=0, column=0)
        ttk.Label(image_frame, text="M0", font=ruler_font, foreground=C_RAIL_UI).grid(row=0, column=2)
        self.ruler_top.grid(row=0, column=1, sticky="ew")
        self.ruler_left.grid(row=1, column=0, sticky="ns")
        self.canvas.grid(row=1, column=1, sticky="nsew")
        self.ruler_right.grid(row=1, column=2, sticky="ns")
        yscroll.grid(row=1, column=3, sticky="ns")
        xscroll.grid(row=2, column=1, sticky="ew")
        image_frame.rowconfigure(1, weight=1)
        image_frame.columnconfigure(1, weight=1)
        for ruler in (self.ruler_top, self.ruler_left, self.ruler_right):
            ruler.bind("<Configure>", self._draw_rulers)
        self._fit_after = None
        self.canvas.bind("<Configure>", self._schedule_fit_image)
        self.canvas.bind("<Control-MouseWheel>", self._wheel_zoom)
        self.canvas.bind("<Double-1>", self._reset_fit)
        self.canvas.bind("<Motion>", self._measure_motion)
        self.canvas.bind("<Button-1>", self._canvas_press)
        self.canvas.bind("<B1-Motion>", self._canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._canvas_release)
        self._box_start: tuple[float, float] | None = None
        self.canvas.bind("<Button-3>", self._measure_right_click)
        self.bind("<Escape>", self._measure_cancel)
        self.bind_all("<KeyPress-k>", self._measure_key_toggle)
        self.bind_all("<KeyPress-K>", self._measure_key_toggle)
        self.calibration: dict | None = None
        self.measure_points: list[tuple[float, float]] = []
        self.measurements: list[tuple[tuple[float, float], tuple[float, float]]] = []
        self._measure_structure = ""
        self._refresh_measure_menu()
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
        self.sheet_view = tk.StringVar(value="template")
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
        self.clear_edit_btn = ttk.Button(editor, text="Clear field", command=self._clear_edit_field)
        self.clear_edit_btn.pack(side="left", padx=(6, 0))
        self.apply_btn = ttk.Button(editor, text="Apply & recalculate", command=self._apply_edit)
        self.apply_btn.pack(side="left", padx=(8, 0))
        self.comment_suggest_btn = ttk.Button(editor, text="Suggest type comment",
                              command=self._suggest_structure_comment)
        self.comment_suggest_btn.pack(side="left", padx=(4, 0))
        self.edit_feedback_label = ttk.Label(sheet, textvariable=self.edit_feedback, anchor="w", wraplength=900)
        self.edit_feedback_label.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 4))
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
        self.table.grid(row=3, column=0, sticky="nsew")
        scroll.grid(row=3, column=1, sticky="ns")
        xscroll.grid(row=4, column=0, sticky="ew")
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
            for widget, (row_index, column_index) in zip(self.sheet_widgets[mode], ((3, 0), (3, 1), (4, 0))):
                widget.grid(row=row_index, column=column_index, sticky="nsew")
        self._switch_sheet_view()
        sheet.rowconfigure(3, weight=1)
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

        testrow = ttk.Frame(tabs)
        tabs.add(testrow, text="Test row")
        self.tabs = tabs
        self.testrow_tab = testrow
        testrow_actions = ttk.Frame(testrow, padding=(0, 4, 0, 4))
        testrow_actions.pack(fill="x")
        ttk.Label(testrow_actions, text="Test row name:").pack(side="left")
        self.testrow_name = tk.StringVar()
        self.suggest_names_var = tk.BooleanVar(value=True)
        name_entry = ttk.Entry(testrow_actions, textvariable=self.testrow_name, width=18)
        name_entry.pack(side="left", padx=(4, 10))
        _Tooltip(name_entry, "Written to the TestRow column of every structure on export (leave empty to keep each row's own).")
        suggest_check = ttk.Checkbutton(testrow_actions, text="Suggest BKM names", variable=self.suggest_names_var)
        suggest_check.pack(side="left", padx=(0, 10))
        _Tooltip(suggest_check, "When adding or updating an entry, propose a structure name derived from the buildsheet\n"
                                "(cell height, MOS, skew layer/direction/CD-REG/N|P nm) for you to accept, edit or keep.")
        for text, command, tip in (
                ("Add current", self._testrow_add_current, "Add the structure shown in the layout (incl. working edits)."),
                ("Update selected", self._testrow_update_selected,
                 "Replace the selected test-row entry with the structure currently shown (after edits)."),
                ("Paste buildsheet", self._testrow_paste,
                 "Paste rows from Excel / CSV / another tool. Columns are matched to the template by header name\n"
                 "(case, spaces and underscores ignored); without a header, template column order is assumed.\n"
                 "Two-column 'Field<TAB>Value' pastes are read as one structure."),
                ("Remove", self._testrow_remove, "Remove the selected entries."),
                ("Up", lambda: self._testrow_move(-1), "Move the selected entry up."),
                ("Down", lambda: self._testrow_move(1), "Move the selected entry down."),
                ("Clear", self._testrow_clear, "Remove every entry.")):
            button = ttk.Button(testrow_actions, text=text, command=command)
            button.pack(side="left", padx=(0, 4))
            _Tooltip(button, tip)
        ttk.Button(testrow_actions, text="Export test row...", command=self._testrow_export).pack(side="right")
        ttk.Button(testrow_actions, text="Copy test row", command=self._testrow_copy).pack(side="right", padx=(0, 4))
        testrow_body = ttk.Frame(testrow)
        testrow_body.pack(fill="both", expand=True)
        columns = ("index", "name", "family", "testrow", "fails", "scalar", "source")
        self.testrow_tree = ttk.Treeview(testrow_body, columns=columns, show="headings", style="Sheet.Treeview",
                                         selectmode="extended")
        for column, text, width, stretch in (("index", "#", 40, False), ("name", "Structure name", 420, True),
                                             ("family", "Family", 90, False), ("testrow", "TestRow", 110, False),
                                             ("fails", "Fail modes", 80, False), ("scalar", "Scalar", 110, False),
                                             ("source", "Source", 90, False)):
            self.testrow_tree.heading(column, text=text)
            self.testrow_tree.column(column, width=width, minwidth=30, stretch=stretch,
                                     anchor="e" if column in ("index", "fails") else "w")
        testrow_y = ttk.Scrollbar(testrow_body, orient="vertical", command=self.testrow_tree.yview)
        self.testrow_tree.configure(yscrollcommand=testrow_y.set)
        self.testrow_tree.pack(side="left", fill="both", expand=True)
        testrow_y.pack(side="left", fill="y")
        self.testrow_tree.tag_configure("stripe", background="#f3f6f9")
        self.testrow_tree.tag_configure("active", background="#dbeafe")
        self.testrow_tree.bind("<Double-1>", self._testrow_open)
        self.testrow_tree.bind("<Return>", self._testrow_open)
        self.testrow_tree.bind("<Delete>", lambda _e: self._testrow_remove())
        self.testrow_tree.bind("<Control-v>", lambda _e: self._testrow_paste())
        self.testrow_active: int | None = None
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
        geometry_supported = bool(self.design and family_of(self.design.row.get("StructureName", "")) in FAMILY_PREFIXES)
        geometry_layers_available = bool(self.design and (geometry_supported or
                                         has_generic_layout_geometry(self.design.row) or
                                         self._special_layers(self.design.row)))
        self.query_entry.state(["!disabled"] if ready else ["disabled"])
        self.testrow_filter.state(["!disabled"] if ready else ["disabled"])
        self.library_table.state(["!disabled"] if ready else ["disabled"])
        self.copy_btn.state(["!disabled"] if ready and self.design else ["disabled"])
        self.export_btn.state(["!disabled"] if ready and self.design else ["disabled"])
        self.add_testrow_btn.state(["!disabled"] if ready and geometry_supported else ["disabled"])
        self.copy_image_btn.state(["!disabled"] if ready and self.current_png else ["disabled"])
        self.tsdr_combo.state(["!disabled"] if ready and geometry_supported else ["disabled"])
        self.reset_btn.state(["!disabled"] if ready and geometry_supported and self.baseline
                     and self.design and self.design is not self.baseline else ["disabled"])
        editable = ready and geometry_supported and bool(self.design and self.designer
                  and self.edit_field.get() in self.designer.output_fields
                      and self.edit_field.get() not in READ_ONLY_FIELDS)
        self.edit_entry.state(["!disabled"] if editable else ["disabled"])
        self.clear_edit_btn.state(["!disabled"] if editable else ["disabled"])
        self.apply_btn.state(["!disabled"] if editable else ["disabled"])
        comment_editable = editable and self.edit_field.get() == "StructureComments"
        self.comment_suggest_btn.state(["!disabled"] if comment_editable else ["disabled"])
        for b in self.layer_buttons:
            b.state(["!disabled"] if ready and geometry_layers_available else ["disabled"])

    def _suggest_structure_comment(self) -> None:
        if not self.design:
            return
        row = self.design.row
        name = row.get("StructureName", "")
        family = family_of(name)
        descriptions = {
            "CTG": "contact-to-gate isolation",
            "GATEGATE": "gate-to-gate end-to-end isolation",
            "EPIEPI": "epi-to-epi end-to-end isolation",
            "TCNTCN": "trench-contact-to-trench-contact end-to-end isolation",
            "DIFFOGD": "diffusion isolation along OGD",
            "DIFFCHN": "diffusion-chain connectivity",
            "VIA_CHAIN": "via-chain connectivity",
        }
        parts = [descriptions.get(family, "ISO structure")]
        type_name = _name_type(name)
        if type_name:
            parts.append(f"type {type_name}")
        direction = re.search(r":(OGD|PGD):", name, re.IGNORECASE)
        if direction:
            parts.append(f"{direction[1].upper()} axis")
        height = re.search(r"\d+", row.get("CellHeight", ""))
        if height:
            parts.append(f"CH{height[0]}")
        if re.search(r"(?:^|:)SDR(?::|$)|(?:^|:)REF(?::|$)", name, re.IGNORECASE):
            parts.append("reference structure")
        self.edit_field.set("StructureComments")
        self.edit_value.set("; ".join(parts) + ".")
        self.edit_entry.focus_set()
        self._set_enabled()
        self.status.set("Suggested a type-based comment for the working copy; apply to keep it.")

    def _visible_layers_for_row(self, row: dict[str, str]) -> set[str]:
        from scalar_snapshot import layout_layers, tsdr_draw_layers

        special = self._special_layers(row)
        layers = special[0] if special else layout_layers(row)
        tsdr_layers = set() if special else set(tsdr_draw_layers(row))
        visible = {layer for layer in layers if layer not in tsdr_layers and layer in self.layer_vars
                   and self.layer_vars[layer].get()}
        visible.update(layer for layer in layers if layer not in tsdr_layers and layer not in self.layer_vars
                       and layer not in (special[1] if special else LAYERS_OFF_BY_DEFAULT))
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

    def _library_items(self) -> list[tuple[str, dict[str, str]]]:
        """(iid, column values) of the structures in the selected open test row(s)."""
        if not self.designer:
            return []
        selected_testrow = self.testrow_var.get()
        return [(str(index), {"testrow": row.get("TestRow", ""), "family": family_of(row.get("StructureName", "")),
                              "name": row.get("StructureName", "")})
                for index, row in enumerate(self.designer.library_rows)
                if selected_testrow == ALL_OPEN_ROWS or row.get("TestRow", "") == selected_testrow]

    def _switch_test_row(self, _event=None) -> None:
        self._filter_library()
        current = self.design.row.get("StructureName") if self.design else None
        children = self.library_table.get_children()
        if children and not any(self.library_iids[iid].get("StructureName") == current for iid in children):
            self.library_table.selection_set(children[0])
            self.library_table.see(children[0])

    def _filter_library(self, _event=None, *_args) -> None:
        if not self.designer or not hasattr(self, "library_table"):
            return
        query = self.query_var.get().strip().lower()
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
        is_atlantis = self.library_source == "Atlantis"
        atlantis_match = _atlantis_matcher(query, self.designer.prompt_fields) if is_atlantis else None
        for iid, values in self._library_items():
            if not self.library_filter.accepts(values):
                continue
            index = int(iid)
            row = self.designer.library_rows[index]
            name, testrow = values["name"], values["testrow"]
            if is_atlantis and query:
                if not atlantis_match({"TestRow": testrow, "StructureName": name,
                                       "StructureComments": row.get("StructureComments", "")}):
                    continue
                score = None
            else:
                score = self._search_score(row, query, query_spec) if query else None
            matches.append((index, row, score))
        if is_atlantis:
            matches.sort(key=lambda item: item[1].get("StructureName", "").casefold())
        elif query:
            matches.sort(key=lambda item: (-(item[2] or 0.0), item[0]))
        if self.library_filter.sort:
            key, reverse = self.library_filter.sort
            field = {"testrow": "TestRow", "name": "StructureName"}.get(key)
            matches.sort(key=lambda item: (item[1].get(field, "") if field else
                                           family_of(item[1].get("StructureName", ""))).casefold(), reverse=reverse)
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
        if is_atlantis:
            shown = len(self._library_items())
            filtered = self.library_filter.summary()
            self.result_count.set(f"{len(matches)} of {shown} structures"
                                  + (f" · column filters: {filtered}" if filtered else ""))
            self.status.set("Search Atlantis test-row names, structure names, or comments; select a result to inspect its layout and buildsheet.")
        elif query and matches:
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
            if self.testrow_active is not None:
                self.testrow_active = None
                self._testrow_refresh()
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

    def _clear_edit_field(self) -> None:
        if not (self.design and self.designer and self.edit_field.get() in self.designer.output_fields
                and self.edit_field.get() not in READ_ONLY_FIELDS):
            return
        self.edit_value.set("")
        self.status.set(f"{self.edit_field.get()} cleared in the editor; Apply & recalculate to remove its geometry from the working copy.")

    def _edit_feedback(self) -> None:
        if not self.design:
            self.edit_feedback.set("")
            return
        field = self.edit_field.get()
        if not field or field not in self.design.row:
            self.edit_feedback.set("")
            return
        edited = dict(self.design.row)
        edited[field] = self.edit_value.get()
        try:
            from scalar_snapshot import buildsheet_review_notes, buildsheet_syntax_issues, design_rule_issues

            if field == "PolyPitch" and edited[field].strip().lower() != "pp44":
                self.edit_feedback_label.configure(foreground="#a12622")
                self.edit_feedback.set("Unsupported edit: this layout model is fixed at pp44 (44 nm)")
                return
            syntax_issues = buildsheet_syntax_issues(edited, (field,))
            if syntax_issues:
                self.edit_feedback_label.configure(foreground="#a12622")
                label = "Unsupported edit: " if any(issue.startswith("PolyPitch") for issue in syntax_issues) else "Syntax error: "
                self.edit_feedback.set(label + "; ".join(syntax_issues[:2]))
                return
            rule_issues = design_rule_issues(edited)
            from tsdr_rules import review_rules

            coverage = review_rules(edited, (field,))
            review_notes = buildsheet_review_notes(edited, (field,))
        except Exception as exc:
            self.edit_feedback_label.configure(foreground="#8a5a00")
            self.edit_feedback.set(f"Rule review unavailable: {exc}")
            return
        self.edit_feedback_label.configure(foreground="#8a5a00")
        if rule_issues or review_notes:
            self.edit_feedback.set("Rule review (best effort): " + "; ".join([*rule_issues[:2], *review_notes[:2]]))
        else:
            self.edit_feedback.set(coverage.display_summary(self.dr_details_var.get())
                                   + (" " + coverage.skipped[0] if coverage.skipped and self.dr_details_var.get() else ""))

    def _apply_edit(self, _event=None) -> None:
        if not (self.designer and self.design and self.baseline) or self.busy:
            return
        field = self.edit_field.get()
        if field not in self.designer.output_fields or field in READ_ONLY_FIELDS:
            return
        value = self.edit_value.get()
        if field == "PolyPitch" and value.strip().lower() != "pp44":
            self.status.set("Unsupported edit: this layout model is fixed at pp44 (44 nm); pp40 cannot be rendered accurately")
            return
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
        from scalar_snapshot import buildsheet_syntax_issues

        syntax_issues = buildsheet_syntax_issues(edited, (field,))
        if syntax_issues:
            label = "Unsupported edit: " if any(issue.startswith("PolyPitch") for issue in syntax_issues) else "Buildsheet syntax error: "
            self.status.set(label + "; ".join(syntax_issues))
            return
        if field in {"VcrnubPatternUnit", "VcrnubPatternGlobal", "PreviewVcrnubWidthNm", "PreviewVcrnubLengthNm",
                     "PreviewVcrWidthNm"}:
            from scalar_snapshot import vcrnub_rule_issues

            issues = vcrnub_rule_issues(edited)
            if issues:
                self.status.set("VCR-NUB rule violation; edit not applied: " + "; ".join(issues))
                return
        axis_fields = {"FTITracksUnit", "DiffPatternUnit", "PolyPlugsUnit", "TcnPlugsUnit",
                       "VcrPatternUnit", "VcrnubPatternUnit", "VcxPatternUnit"}
        if field in axis_fields and value:
            ogd = int(edited.get("UnitCellSizeOgd", "1") or 1)
            pgd = 2 * int(edited.get("UnitCellSizePgd", "1") or 1)
            if field == "VcrnubPatternUnit":
                from ScalarCalculation import geometry_from_row

                nub_geometry = geometry_from_row(edited)
            for segment in segments(value):
                parts = [part.strip() for part in strip_shift(segment).split(",")]
                x_index = 1 if field == "VcxPatternUnit" else 0
                x_extent = nub_geometry.ogd - 1 if field == "VcrnubPatternUnit" else ogd
                y_extent = nub_geometry.pgd if field == "VcrnubPatternUnit" else pgd
                if len(parts) <= x_index + 1 or not expand_axis(parts[x_index], x_extent):
                    self.status.set(f"Invalid {field} X axis in: {segment}")
                    return
                if field == "VcxPatternUnit":
                    if len(parts) < 3 or not int_list(parts[2]):
                        self.status.set(f"Invalid {field} track list in: {segment}")
                        return
                elif not expand_axis(parts[1], y_extent):
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
                                                global_units=self.global_units, coords=self.coords_mode,
                                                panel=True)

    def _is_pft_preview(self, row: dict[str, str] | None) -> bool:
        """XTRPFX rows are drawn from the PFT GDS generator, not from the ISO buildsheet DSL."""
        return bool(row) and family_of(row.get("StructureName", "")) not in FAMILY_PREFIXES \
            and not template_kind(row) and not has_generic_layout_geometry(row) and is_pft_row(row)

    def _special_layers(self, row: dict[str, str] | None) -> tuple[tuple[str, ...], frozenset[str]] | None:
        """(layer toggles, default-off layers) for previews that are not drawn by the ISO renderer."""
        kind = template_kind(row) if row else ""
        if kind:
            return TEMPLATE_LAYERS[kind], TEMPLATE_LAYERS_OFF
        return (PFT_LAYERS, PFT_LAYERS_OFF) if self._is_pft_preview(row) else None

    def _set_layer_controls(self, row: dict[str, str] | None) -> None:
        from scalar_snapshot import default_tsdr_draw_layer, layout_layers, tsdr_draw_layers

        selected = {layer: variable.get() for layer, variable in self.layer_vars.items()}
        special = self._special_layers(row)
        layers = special[0] if special else layout_layers(row) if row else LAYERS
        if row and not special and template_active_area_size(row) and not has_generic_layout_geometry(row) \
                and family_of(row.get("StructureName", "")) not in FAMILY_PREFIXES:
            layers = ()
        if row and family_of(row.get("StructureName", "")) not in FAMILY_PREFIXES \
                and has_generic_layout_geometry(row):
            layers = tuple(layer for layer in layers if layer != "Fail paths")
        tsdr_options = tsdr_draw_layers(row) if row and not special else ()
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
            default_visible = layer not in (special[1] if special else LAYERS_OFF_BY_DEFAULT)
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

    def _schedule_layer_reflow(self, _event=None) -> None:
        if self._layer_reflow_after:
            self.after_cancel(self._layer_reflow_after)
        self._layer_reflow_after = self.after_idle(self._run_layer_reflow)

    def _run_layer_reflow(self) -> None:
        self._layer_reflow_after = None
        self._reflow_layer_buttons()

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
        try:
            while True:
                self._apply_sharp(*self.sharp_results.get_nowait())
        except queue.Empty:
            pass
        try:
            while True:
                kind, token, done, value, error = self.atlantis_results.get_nowait()
                if token != self.atlantis_tokens.get(kind) or not (
                        self.atlantis_window is not None and self.atlantis_window.winfo_exists()):
                    continue
                if error:
                    self.atlantis_lookup_status.set(f"Atlantis lookup failed: {error}")
                    messagebox.showerror("Atlantis lookup", str(error), parent=self.atlantis_window)
                else:
                    done(value)
        except queue.Empty:
            pass
        self._poll_after = self.after(100, self._poll)

    def _load_atlantis(self) -> None:
        if self.busy:
            return
        if self.atlantis_window is not None and self.atlantis_window.winfo_exists():
            self.workspace_tabs.select(self.atlantis_window)
            return
        from atlantis_wcf import AtlantisWcfClient

        def connect():
            client = AtlantisWcfClient()
            return client, client.list_projects()

        self._run(connect, self._atlantis_browser, "Connecting to Atlantis with Windows credentials...")

    def _atlantis_browser(self, value) -> None:
        self.atlantis_client, projects = value
        win = ttk.Frame(self.workspace_tabs)
        self.atlantis_window = win
        self.workspace_tabs.add(win, text="Atlantis lookup")
        self.workspace_tabs.select(win)
        self.atlantis_rows: list[dict] = []
        self.atlantis_libraries_all: list[str] = []
        self.atlantis_libraries_shown: list[str] = []
        self.atlantis_library_structures: list[dict] = []
        self.atlantis_row_structures: list[dict] = []
        self.atlantis_structure_cache: dict[tuple[str, str], list[dict]] = {}
        self.atlantis_row_structure_cache: dict[tuple[str, str, str], list[dict]] = {}
        self.atlantis_row_cache: dict[tuple[str, str, str], StructureDesigner] = {}
        self.atlantis_filter_after: dict[str, str] = {}

        top = ttk.Frame(win, padding=(8, 8, 8, 4))
        top.pack(fill="x")
        ttk.Label(top, text="Project / node").pack(side="left")
        self.atlantis_project_var = tk.StringVar()
        project_combo = ttk.Combobox(top, textvariable=self.atlantis_project_var, values=projects,
                                     state="readonly", width=12)
        project_combo.pack(side="left", padx=(6, 0))
        project_combo.bind("<<ComboboxSelected>>", lambda _e: self._atlantis_project_selected())
        self.atlantis_lookup_status = tk.StringVar(value=f"{len(projects)} projects")
        ttk.Label(top, textvariable=self.atlantis_lookup_status, foreground="#4a5560").pack(side="right")

        split = ttk.PanedWindow(win, orient="horizontal")
        split.pack(fill="both", expand=True, padx=8)
        left = ttk.Frame(split, width=270)
        split.add(left, weight=0)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(2, weight=1)
        ttk.Label(left, text="Library or test row (contains; Enter to open)").grid(row=0, column=0, columnspan=2,
                                                                                    sticky="w")
        self.atlantis_library_filter = tk.StringVar()
        self.atlantis_row_hits: dict[str, list[str]] = {}
        self.atlantis_row_hits_query = ""
        library_entry = ttk.Entry(left, textvariable=self.atlantis_library_filter)
        library_entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 4))
        _Tooltip(library_entry, "Type part of a library name or a test-row name. Libraries whose name contains it\n"
                                "are listed first; then (after a short search of the whole project) libraries that\n"
                                "contain matching test rows, with the rows shown. Opening one filters its rows.")
        self.atlantis_library_filter.trace_add("write", lambda *_a: self._atlantis_library_text_changed())
        library_entry.bind("<Return>", lambda _e: self._atlantis_library_chosen())
        library_entry.bind("<Down>", lambda _e: self.atlantis_library_list.focus_set())
        self.atlantis_library_list = tk.Listbox(left, exportselection=False, activestyle="none",
                                                font=("Segoe UI", 10))
        library_scroll = ttk.Scrollbar(left, orient="vertical", command=self.atlantis_library_list.yview)
        self.atlantis_library_list.configure(yscrollcommand=library_scroll.set)
        self.atlantis_library_list.grid(row=2, column=0, sticky="nsew")
        library_scroll.grid(row=2, column=1, sticky="ns")
        self.atlantis_library_list.bind("<<ListboxSelect>>", lambda _e: self._atlantis_library_chosen())

        tabs = ttk.Notebook(split)
        split.add(tabs, weight=1)
        self.atlantis_tabs = tabs
        rows_tab = ttk.Frame(tabs, padding=(6, 6, 0, 0))
        structures_tab = ttk.Frame(tabs, padding=(6, 6, 0, 0))
        tabs.add(rows_tab, text="Test rows")
        tabs.add(structures_tab, text="Structures (whole library)")
        tabs.bind("<<NotebookTabChanged>>", lambda _e: self._atlantis_tab_changed())

        rows_tab.columnconfigure(0, weight=1)
        rows_tab.rowconfigure(4, weight=1)
        ttk.Label(rows_tab, text="Search rows: name, owner, status or plain words (e.g. ctg 110 ogd, vcr skew)").grid(
            row=0, column=0, columnspan=2, sticky="w")
        self.atlantis_row_filter_var = tk.StringVar()
        row_entry = ttk.Entry(rows_tab, textvariable=self.atlantis_row_filter_var)
        row_entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 4))
        self.atlantis_row_filter_var.trace_add(
            "write", lambda *_a: self._atlantis_schedule("rows", self._atlantis_filter_rows))
        row_entry.bind("<Return>", lambda _e: self._atlantis_open_row())
        row_entry.bind("<Down>", lambda _e: self.atlantis_row_tree.focus_set())
        filter_bar = ttk.Frame(rows_tab)
        filter_bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 3))
        ttk.Label(filter_bar, text="Click a column header to filter or sort; Ctrl/Shift-click to pick several rows",
                  foreground="#4a5560").pack(side="left")
        ttk.Button(filter_bar, text="Clear column filters",
                   command=lambda: self.atlantis_row_filter.clear()).pack(side="right")
        open_rows_btn = ttk.Button(filter_bar, text="Open selected rows", command=lambda: self._atlantis_open_row())
        open_rows_btn.pack(side="right", padx=(0, 4))
        _Tooltip(open_rows_btn, "Open every selected test row in the Structure viewer;\n"
                                "switch between them with its Test row dropdown.")
        row_columns = (("name", "Test row", "TestRowName", 175, False),
                       ("status", "State / status", "Status", 150, False),
                       ("modified", "Last modified", "LastModified", 125, False),
                       ("owner", "Owner", "Owner", 115, False),
                       ("modified_by", "Modified by", "ModifiedBy", 115, False),
                       ("version", "Version", "Version", 65, False),
                       ("metadata", "Metadata version", "MetadataVersion", 85, False),
                       ("locked", "Locked", "Locked", 55, False),
                       ("locked_by", "Locked by", "LockedBy", 105, False),
                       ("batch", "Batch result", "BatchResult", 80, False),
                       ("bus", "Bus type", "BusType", 75, False),
                       ("pad", "Pad-row template", "PadRowTemplate", 145, False),
                       ("description", "Description", "Description", 280, True),
                       ("templates", "Templates used", "TemplatesUsed", 190, True),
                       ("tapeout", "Tapeout label", "TapeoutLabel", 130, False))
        self.atlantis_column_filter_fields = row_columns

        row_split = ttk.PanedWindow(rows_tab, orient="horizontal")
        row_split.grid(row=4, column=0, columnspan=2, sticky="nsew")
        row_table_panel = ttk.Frame(row_split)
        row_structure_panel = ttk.Frame(row_split, padding=(8, 0, 0, 0))
        row_split.add(row_table_panel, weight=3)
        row_split.add(row_structure_panel, weight=2)
        row_table_panel.columnconfigure(0, weight=1)
        row_table_panel.rowconfigure(0, weight=1)
        self.atlantis_row_tree = ttk.Treeview(row_table_panel, columns=[c[0] for c in row_columns], show="headings",
                                              selectmode="extended")
        for key, label, _data_key, width, stretch in row_columns:
            self.atlantis_row_tree.heading(key, text=label)
            self.atlantis_row_tree.column(key, width=width, minwidth=50, stretch=stretch)
        row_y = ttk.Scrollbar(row_table_panel, orient="vertical", command=self.atlantis_row_tree.yview)
        row_x = ttk.Scrollbar(row_table_panel, orient="horizontal", command=self.atlantis_row_tree.xview)
        self.atlantis_row_tree.configure(yscrollcommand=row_y.set, xscrollcommand=row_x.set)
        self.atlantis_row_tree.grid(row=0, column=0, sticky="nsew")
        self.atlantis_row_filter = _HeaderFilter(
            self.atlantis_row_tree, {key: label for key, label, *_rest in row_columns},
            self._atlantis_row_items, self._atlantis_filter_rows)
        row_y.grid(row=0, column=1, sticky="ns")
        row_x.grid(row=1, column=0, sticky="ew")
        row_structure_panel.columnconfigure(0, weight=1)
        row_structure_panel.rowconfigure(2, weight=1)
        ttk.Label(row_structure_panel, text="Structures in selected row").grid(row=0, column=0, sticky="w")
        self.atlantis_row_structure_filter_var = tk.StringVar()
        row_structure_entry = ttk.Entry(row_structure_panel, textvariable=self.atlantis_row_structure_filter_var)
        row_structure_entry.grid(row=1, column=0, sticky="ew", pady=(2, 4))
        self.atlantis_row_structure_filter_var.trace_add(
            "write", lambda *_a: self._atlantis_filter_row_structures())
        self.atlantis_row_struct_tree = ttk.Treeview(
            row_structure_panel, columns=("name", "pad", "description"), show="headings", selectmode="browse")
        for key, label, width, stretch in (("name", "Structure name", 320, True),
                                           ("pad", "Pad-row template", 150, False),
                                           ("description", "Description", 220, True)):
            self.atlantis_row_struct_tree.heading(key, text=label)
            self.atlantis_row_struct_tree.column(key, width=width, minwidth=60, stretch=stretch)
        row_struct_y = ttk.Scrollbar(row_structure_panel, orient="vertical", command=self.atlantis_row_struct_tree.yview)
        self.atlantis_row_struct_tree.configure(yscrollcommand=row_struct_y.set)
        self.atlantis_row_struct_tree.grid(row=2, column=0, sticky="nsew")
        row_struct_y.grid(row=2, column=1, sticky="ns")
        self.atlantis_row_struct_filter = _HeaderFilter(
            self.atlantis_row_struct_tree,
            {"name": "Structure name", "pad": "Pad-row template", "description": "Description"},
            self._atlantis_row_structure_items, self._atlantis_filter_row_structures)
        self.atlantis_row_struct_tree.bind("<Double-Button-1>", lambda _e: self._atlantis_open_row_structure())
        self.atlantis_row_tree.bind("<<TreeviewSelect>>", lambda _e: self._atlantis_schedule(
            "row-structures", self._atlantis_row_selected))
        self.atlantis_row_tree.bind("<Double-Button-1>", lambda _e: self._atlantis_open_row())
        self.atlantis_row_tree.bind("<Return>", lambda _e: self._atlantis_open_row())

        structures_tab.columnconfigure(0, weight=1)
        structures_tab.rowconfigure(2, weight=1)
        ttk.Label(structures_tab, text="Search structures across the library (e.g. ctg110 npz1, gategate oc1 pgd)").grid(
            row=0, column=0, columnspan=2, sticky="w")
        self.atlantis_structure_filter_var = tk.StringVar()
        structure_entry = ttk.Entry(structures_tab, textvariable=self.atlantis_structure_filter_var)
        structure_entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 4))
        self.atlantis_structure_filter_var.trace_add(
            "write", lambda *_a: self._atlantis_schedule("structures", self._atlantis_filter_structures))
        structure_entry.bind("<Return>", lambda _e: self._atlantis_open_structure())
        structure_entry.bind("<Down>", lambda _e: self.atlantis_struct_tree.focus_set())
        self.atlantis_struct_tree = ttk.Treeview(structures_tab, columns=("name", "row", "pad", "description"),
                                                 show="headings", selectmode="browse")
        for key, label, width, stretch in (("name", "Structure name", 380, True), ("row", "Test row", 180, False),
                                           ("pad", "Pad row", 160, False), ("description", "Description", 240, True)):
            self.atlantis_struct_tree.heading(key, text=label)
            self.atlantis_struct_tree.column(key, width=width, minwidth=60, stretch=stretch)
        self.atlantis_struct_filter = _HeaderFilter(
            self.atlantis_struct_tree,
            {"name": "Structure name", "row": "Test row", "pad": "Pad row", "description": "Description"},
            self._atlantis_structure_items, self._atlantis_filter_structures)
        struct_y = ttk.Scrollbar(structures_tab, orient="vertical", command=self.atlantis_struct_tree.yview)
        self.atlantis_struct_tree.configure(yscrollcommand=struct_y.set)
        self.atlantis_struct_tree.grid(row=2, column=0, sticky="nsew")
        struct_y.grid(row=2, column=1, sticky="ns")
        self.atlantis_struct_tree.bind("<ButtonRelease-1>", lambda e: self._atlantis_tree_clicked(
            e, self.atlantis_struct_tree, self._atlantis_open_structure))
        self.atlantis_struct_tree.bind("<Return>", lambda _e: self._atlantis_open_structure())

        ttk.Label(win, foreground="#4a5560", padding=(8, 4, 8, 8),
                   text="Select a test row to see its structures beside it. Ctrl/Shift-click several rows and press "
                       "Open selected rows (or Enter / double-click) to open them together; switch rows with the "
                       "Structure viewer's Test row dropdown. Double-click a structure to open it directly.",
                  ).pack(fill="x")
        project, _library = self.atlantis_choice
        self.atlantis_project_var.set(project if project in projects else "X81" if "X81" in projects
                                      else projects[0] if projects else "")
        self._atlantis_project_selected()
        library_entry.focus_set()
        self.status.set("Connected to Atlantis using your Windows login.")

    def _atlantis_run(self, kind: str, func, done, message: str) -> None:
        """Run an Atlantis lookup off the UI thread; only the newest request of each kind is applied."""
        token = self.atlantis_tokens.get(kind, 0) + 1
        self.atlantis_tokens[kind] = token
        self.atlantis_lookup_status.set(message)

        def worker() -> None:
            try:
                self.atlantis_results.put((kind, token, done, func(), None))
            except Exception as exc:  # surfaced in the UI thread
                self.atlantis_results.put((kind, token, done, None, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _atlantis_schedule(self, kind: str, func) -> None:
        if pending := self.atlantis_filter_after.get(kind):
            self.after_cancel(pending)
        self.atlantis_filter_after[kind] = self.after(120, func)

    @staticmethod
    def _atlantis_tree_clicked(event, tree: ttk.Treeview, action) -> None:
        if tree.identify_region(event.x, event.y) in ("cell", "tree"):
            action()

    def _schema_designer(self) -> StructureDesigner:
        """Empty library carrying the buildsheet column schema (search fields, pasted rows) before Atlantis loads."""
        if self.schema_designer is None:
            from atlantis_wcf import AtlantisWcfClient

            self.schema_designer = StructureDesigner(use_llm=False, source=(AtlantisWcfClient._csv_fields(), []))
        return self.schema_designer

    def _atlantis_fields(self) -> list[str]:
        return (self.designer or self._schema_designer()).prompt_fields

    def _atlantis_project_selected(self) -> None:
        project = self.atlantis_project_var.get()
        self.atlantis_libraries_all = []
        self.atlantis_row_hits, self.atlantis_row_hits_query = {}, ""
        self.atlantis_rows = []
        self.atlantis_library_structures = []
        self._atlantis_filter_libraries()
        self._atlantis_filter_rows()
        self._atlantis_filter_structures()
        if project:
            self._atlantis_run("libraries", lambda: self.atlantis_client.list_libraries(project),
                               lambda libraries: self._atlantis_libraries_loaded(project, libraries),
                               f"Listing libraries in {project}...")

    def _atlantis_libraries_loaded(self, project: str, libraries: list[str]) -> None:
        if project != self.atlantis_project_var.get():
            return
        self.atlantis_libraries_all = sorted(libraries, key=str.casefold)
        self._atlantis_filter_libraries()
        self.atlantis_lookup_status.set(f"{len(libraries)} libraries in {project}; type to filter")
        saved_project, saved_library = self.atlantis_choice
        if saved_project == project and saved_library in self.atlantis_libraries_all:
            self.atlantis_library_filter.set(saved_library)
            self._atlantis_library_chosen(saved_library, force=True)

    def _atlantis_library_text_changed(self) -> None:
        self._atlantis_filter_libraries()
        text = self.atlantis_library_filter.get().strip()
        if len(text) >= 3 and text not in self.atlantis_libraries_all:
            self._atlantis_schedule("find-rows", self._atlantis_find_rows)

    def _atlantis_find_rows(self) -> None:
        text, project = self.atlantis_library_filter.get().strip(), self.atlantis_project_var.get()
        if len(text) < 3 or not project:
            return

        def done(rows: list[dict]) -> None:
            if text != self.atlantis_library_filter.get().strip():
                return
            hits: dict[str, list[str]] = {}
            for row in rows:
                hits.setdefault(str(row.get("Library") or ""), []).append(str(row.get("TestRowName") or ""))
            hits.pop("", None)
            self.atlantis_row_hits, self.atlantis_row_hits_query = hits, text
            self._atlantis_filter_libraries()
            self.atlantis_lookup_status.set(f"'{text}': {sum(map(len, hits.values()))} test row(s) in "
                                            f"{len(hits)} librar{'y' if len(hits) == 1 else 'ies'}")

        def search() -> list[dict]:
            # Server-side by the most selective word; the rest may sit in the row or the library name.
            words = re.findall(r"[a-z0-9]+", text.casefold())
            if not words:
                return []
            rows = self.atlantis_client.find_rows(project, max(words, key=len) if len(words) > 1 else text)
            return [row for row in rows if _match_rank(text, f"{row.get('Library')} {row.get('TestRowName')}")
                    in (0, 1)]

        self._atlantis_run("find-rows", search, done,
                           f"Searching every library in {project} for test rows matching '{text}'...")

    def _atlantis_filter_libraries(self) -> None:
        raw = self.atlantis_library_filter.get().strip()
        hits = self.atlantis_row_hits if raw and raw == self.atlantis_row_hits_query else {}
        ranked = sorted(((rank, name.casefold(), name) for name in self.atlantis_libraries_all
                         if (rank := _match_rank(raw, name)) is not None))
        # Loose in-order matches only when nothing matches by word.
        best = ranked[0][0] if ranked else None
        shown = [name for rank, _key, name in ranked if rank < 2 or best == 2]
        self.atlantis_library_name_matches = set(shown)
        shown += [name for name in sorted(hits, key=str.casefold) if name not in shown]
        self.atlantis_libraries_shown = shown
        listbox = self.atlantis_library_list
        listbox.delete(0, "end")
        for name in shown:
            rows = hits.get(name, [])
            listbox.insert("end", name if not rows else
                           f"{name}   ← {len(rows)} row(s): {', '.join(rows[:3])}{' …' if len(rows) > 3 else ''}")
        current = self.atlantis_choice[1]
        highlight = (self.atlantis_libraries_shown.index(current) if current in self.atlantis_libraries_shown
                     else 0 if raw and self.atlantis_libraries_shown else None)
        if highlight is not None:
            listbox.selection_set(highlight)
            listbox.see(highlight)

    def _atlantis_library_chosen(self, library: str | None = None, force: bool = False) -> None:
        if library is None:
            selection = self.atlantis_library_list.curselection()
            if not selection:
                return
            library = self.atlantis_libraries_shown[selection[0]]
        project = self.atlantis_project_var.get()
        key = (project, library)
        query = self.atlantis_library_filter.get().strip()
        if (library in self.atlantis_row_hits and query == self.atlantis_row_hits_query
                and library not in getattr(self, "atlantis_library_name_matches", set())):
            self.atlantis_row_filter_var.set(query)
        if key == self.atlantis_choice and self.atlantis_rows and not force:
            return
        self.atlantis_choice = key
        self.atlantis_rows = []
        self.atlantis_library_structures = []
        self._atlantis_filter_rows()
        self._atlantis_filter_structures()
        self._atlantis_run("rows", lambda: self.atlantis_client.list_rows(project, library),
                           lambda rows: self._atlantis_rows_loaded(key, rows), f"Listing test rows in {library}...")
        if self.atlantis_tabs.index("current") == 1:
            self._atlantis_load_library_structures()

    def _atlantis_rows_loaded(self, key: tuple[str, str], rows: list[dict]) -> None:
        if key != self.atlantis_choice:
            return
        self.atlantis_rows = sorted(rows, key=lambda row: str(row.get("TestRowName") or "").casefold())
        self._atlantis_filter_rows()

    def _atlantis_row_items(self) -> list[tuple[str, dict[str, str]]]:
        """(iid, column values) for every test row of the open library, as shown in the table."""
        items = []
        for index, row in enumerate(getattr(self, "atlantis_rows", [])):
            values = {}
            for key, _label, data_key, _width, _stretch in self.atlantis_column_filter_fields:
                value = row.get(data_key)
                values[key] = ("Yes" if value else "No") if data_key == "Locked" else \
                    " · ".join(str(value or "").split())
            items.append((str(index), values))
        return items

    def _atlantis_row_structure_items(self) -> list[tuple[str, dict[str, str]]]:
        return [(str(index), {"name": str(item.get("StructureName") or ""), "pad": str(item.get("PadRowName") or ""),
                              "description": " · ".join(str(item.get("Description") or "").split())})
                for index, item in enumerate(getattr(self, "atlantis_row_structures", []))]

    def _atlantis_structure_items(self) -> list[tuple[str, dict[str, str]]]:
        return [(str(index), {"name": str(item.get("StructureName") or ""), "row": str(item.get("TestRowName") or ""),
                              "pad": str(item.get("PadRowName") or ""),
                              "description": " · ".join(str(item.get("Description") or "").split())})
                for index, item in enumerate(getattr(self, "atlantis_library_structures", []))]

    def _atlantis_row_selected(self) -> None:
        selection = self.atlantis_row_tree.selection()
        if not selection:
            self.atlantis_selected_row = ""
            self.atlantis_row_structures = []
            self._atlantis_filter_row_structures()
            return
        focus = self.atlantis_row_tree.focus()
        row = self.atlantis_rows[int(focus if focus in selection else selection[-1])]
        test_row = str(row.get("TestRowName") or "")
        self.atlantis_selected_row = test_row
        key = (*self.atlantis_choice, test_row)
        if key in self.atlantis_row_structure_cache:
            self.atlantis_row_structures = self.atlantis_row_structure_cache[key]
            self._atlantis_filter_row_structures()
            self.atlantis_lookup_status.set(f"{len(self.atlantis_row_structures)} structures in {test_row}")
            return
        project, library = self.atlantis_choice
        self.atlantis_row_structures = []
        self._atlantis_filter_row_structures()
        self._atlantis_run("row-structures", lambda: self.atlantis_client.list_structures(project, library, test_row),
                           lambda structures: self._atlantis_row_structures_loaded(key, structures),
                           f"Loading structures in {test_row}...")

    def _atlantis_row_structures_loaded(self, key: tuple[str, str, str], structures: list[dict]) -> None:
        if key[:2] != self.atlantis_choice or key[2] != self.atlantis_selected_row:
            return
        self.atlantis_row_structure_cache[key] = structures
        self.atlantis_row_structures = structures
        self._atlantis_filter_row_structures()
        self.atlantis_lookup_status.set(f"{len(structures)} structures in {key[2]}")

    def _atlantis_filter_row_structures(self) -> None:
        if not hasattr(self, "atlantis_row_struct_filter"):
            return
        tree = self.atlantis_row_struct_tree
        tree.delete(*tree.get_children())
        query = self.atlantis_row_structure_filter_var.get()
        match = _atlantis_matcher(query, self._atlantis_fields())
        items = [(iid, values) for iid, values in self._atlantis_row_structure_items()
                 if match(self.atlantis_row_structures[int(iid)]) and self.atlantis_row_struct_filter.accepts(values)]
        for iid, values in self.atlantis_row_struct_filter.order(items):
            tree.insert("", "end", iid=iid, values=(values["name"], values["pad"], values["description"]))

    def _atlantis_open_row_structure(self) -> None:
        selection = self.atlantis_row_struct_tree.selection()
        if selection and self.atlantis_selected_row:
            item = self.atlantis_row_structures[int(selection[0])]
            self._atlantis_open_row(self.atlantis_selected_row, str(item.get("StructureName") or ""))

    def _atlantis_filter_rows(self) -> None:
        if not hasattr(self, "atlantis_row_filter"):
            return
        tree = self.atlantis_row_tree
        tree.delete(*tree.get_children())
        query = self.atlantis_row_filter_var.get()
        match = _atlantis_row_matcher(query)
        columns = [key for key, *_rest in self.atlantis_column_filter_fields]
        items = [(iid, values) for iid, values in self._atlantis_row_items()
                 if match(self.atlantis_rows[int(iid)]) and self.atlantis_row_filter.accepts(values)]
        # When the words match test-row names (or library + name), don't pad with hits from dates or owners.
        library = self.atlantis_choice[1]
        named = [item for item in items
                 if _match_rank(query, f"{library} {self.atlantis_rows[int(item[0])].get('TestRowName')}") in (0, 1)]
        if query.strip() and named:
            items = named
        first = None
        for iid, values in self.atlantis_row_filter.order(items):
            first = first or iid
            tree.insert("", "end", iid=iid, values=[values[key] for key in columns])
        if query.strip() and first:
            tree.selection_set(first)
            tree.see(first)
        if self.atlantis_rows:
            library = self.atlantis_choice[1]
            filtered = self.atlantis_row_filter.summary()
            self.atlantis_lookup_status.set(f"{len(tree.get_children())} of {len(self.atlantis_rows)} test rows in "
                                            f"{library}" + (f"; column filters: {filtered}" if filtered else "")
                                            + ("; Enter or click to list its structures" if first else ""))

    def _atlantis_tab_changed(self) -> None:
        if self.atlantis_tabs.index("current") == 1:
            self._atlantis_load_library_structures()

    def _atlantis_load_library_structures(self) -> None:
        key = self.atlantis_choice
        if not all(key):
            return
        if key in self.atlantis_structure_cache:
            self.atlantis_library_structures = self.atlantis_structure_cache[key]
            self._atlantis_filter_structures()
            return
        project, library = key

        def load() -> list[dict]:
            structures = self.atlantis_client.list_library_structures(project, library)
            return sorted(structures, key=lambda s: (str(s.get("StructureName") or "").casefold(),
                                                     str(s.get("TestRowName") or "").casefold()))

        def done(structures: list[dict]) -> None:
            self.atlantis_structure_cache[key] = structures
            if key == self.atlantis_choice:
                self.atlantis_library_structures = structures
                self._atlantis_filter_structures()

        self._atlantis_run("structures", load, done, f"Loading every structure in {library}...")

    def _atlantis_filter_structures(self) -> None:
        if not hasattr(self, "atlantis_struct_filter"):
            return
        tree = self.atlantis_struct_tree
        tree.delete(*tree.get_children())
        query = self.atlantis_structure_filter_var.get()
        match = _atlantis_matcher(query, self._atlantis_fields())
        items = [(iid, values) for iid, values in self._atlantis_structure_items()
                 if match(self.atlantis_library_structures[int(iid)]) and self.atlantis_struct_filter.accepts(values)]
        first = None
        for iid, values in self.atlantis_struct_filter.order(items):
            first = first or iid
            tree.insert("", "end", iid=iid, values=(values["name"], values["row"], values["pad"], values["description"]))
        if query.strip() and first:
            tree.selection_set(first)
            tree.see(first)
        if self.atlantis_library_structures:
            self.atlantis_lookup_status.set(f"{len(tree.get_children())} of {len(self.atlantis_library_structures)} "
                                            f"structures in {self.atlantis_choice[1]}")

    def _atlantis_open_structure(self) -> None:
        selection = self.atlantis_struct_tree.selection()
        if selection:
            item = self.atlantis_library_structures[int(selection[0])]
            self._atlantis_open_row(str(item.get("TestRowName") or ""), str(item.get("StructureName") or ""))

    def _atlantis_open_row(self, test_row: str | None = None, focus_structure: str | None = None) -> None:
        if test_row is None:
            selection = self.atlantis_row_tree.selection()
            if not selection:
                return
            test_rows = [str(self.atlantis_rows[int(iid)].get("TestRowName") or "") for iid in selection]
        else:
            test_rows = [test_row]
        project, library = self.atlantis_choice
        keys = [(project, library, name) for name in dict.fromkeys(test_rows) if name]
        missing = [key for key in keys if key not in self.atlantis_row_cache]
        if not missing:
            self._atlantis_show_rows(keys, focus_structure)
            return
        summaries = {item.get("TestRowName"): item for item in self.atlantis_rows}

        def fetch(test_row: str) -> StructureDesigner | None:
            fields, rows = self.atlantis_client.fetch_structures(project, library, test_row, [])
            if not rows:
                return None
            summary = summaries.get(test_row, {})
            metadata = {
                "AtlantisProject": project,
                "AtlantisLibrary": library,
                "AtlantisTemplates": summary.get("TemplatesUsed", ""),
                "PadRowTemplate": summary.get("PadRowTemplate", ""),
                "AtlantisRowStatus": summary.get("Status", ""),
                "AtlantisRowOwner": summary.get("Owner", ""),
                "AtlantisLastModified": summary.get("LastModified", ""),
            }
            for field in metadata:
                if field not in fields:
                    fields.append(field)
            for row in rows:
                row.update({field: str(value or "") for field, value in metadata.items()})
            return StructureDesigner(use_llm=False, source=(fields, rows))

        def load() -> dict:
            return {key: fetch(key[2]) for key in missing}

        def done(loaded: dict) -> None:
            self.atlantis_row_cache.update(loaded)
            self._atlantis_show_rows(keys, focus_structure)

        self._atlantis_run("row", load, done, f"Loading structures in {', '.join(key[2] for key in missing)}...")

    def _atlantis_show_rows(self, keys: list[tuple[str, str, str]], focus_structure: str | None) -> None:
        loaded = [(key, self.atlantis_row_cache[key]) for key in keys if self.atlantis_row_cache.get(key)]
        empty = [key[2] for key in keys if not self.atlantis_row_cache.get(key)]
        if not loaded:
            self.atlantis_lookup_status.set(f"No structures in {', '.join(empty)}")
            return
        project, library = keys[0][:2]
        if len(loaded) == 1:
            designer = loaded[0][1]
            label = f"Atlantis {project} / {library} / {loaded[0][0][2]}"
        else:
            fields = list(dict.fromkeys(field for _key, d in loaded for field in d.fields))
            rows = [row for _key, d in loaded for row in d.library_rows]
            designer = StructureDesigner(use_llm=False, source=(fields, rows))
            designer.library_rows = [dict(row) for row in rows]
            label = f"Atlantis {project} / {library} / {len(loaded)} test rows"
        self.atlantis_designer = designer
        self._activate_library(designer, "Atlantis", focus_structure=focus_structure, label=label)
        self.atlantis_lookup_status.set(f"{len(designer.library_rows)} structures from {len(loaded)} test row(s) "
                                        "listed in the main window"
                                        + (f"; no structures in {', '.join(empty)}" if empty else ""))

    def _activate_library(self, designer: StructureDesigner, source: str,
                          focus_structure: str | None = None, label: str | None = None) -> None:
        switching = self.designer is not None
        self.designer = designer
        self.library_source = source
        self.library_source_var.set(f"Source: {label or source}")
        self.query_var.set("")
        self.library_filter.selected.clear()
        self.library_filter.sort = None
        self.library_filter._headings()
        if switching:
            self.design = None
            self.baseline = None
            self.edit_field.set("")
            self.edit_value.set("")
        designer.output_fields.extend(field for field in PREVIEW_WIDTH_FIELDS if field not in designer.output_fields)
        testrows = list(dict.fromkeys(row.get("TestRow", "") for row in designer.library_rows if row.get("TestRow", "")))
        self.testrow_filter.configure(values=(ALL_OPEN_ROWS, *testrows) if len(testrows) > 1 else tuple(testrows))
        focus_row = next((row.get("TestRow", "") for row in designer.library_rows
                          if focus_structure and row.get("StructureName") == focus_structure), "")
        self.testrow_var.set(focus_row or (testrows[0] if testrows else ALL_OPEN_ROWS))
        self._filter_library()
        if switching and (children := self.library_table.get_children()):
            target = next((iid for iid, row in self.library_iids.items()
                           if row.get("StructureName") == focus_structure), None)
            self.library_table.selection_set(target or children[0])
            self._select_library_row()
        self.workspace_tabs.select(self.structure_workspace)
        if source == "Atlantis":
            self.status.set(f"Atlantis: {len(designer.library_rows)} structures across {len(testrows)} test rows. "
                            "Modeled ISO families render geometry; other templates open as raw buildsheet data.")
        else:
            self.status.set(f"{source}: {len(designer.library_rows)} 81Z rows across {len(testrows)} test structures. "
                            "Select a row to inspect its layout and buildsheet.")

    # -- actions -------------------------------------------------------------
    def _layers_changed(self) -> None:
        if not (self.designer and self.design) or self.busy:
            return
        visible = self._current_visible()
        self._run(self._render_layer_preview, self._show_layer_preview,
                  "Updating visible layout layers...", self.design.id, visible)

    def _current_visible(self) -> set[str]:
        return {layer for layer, variable in self.layer_vars.items() if variable.get()} | self._tsdr_region_layers()

    def _remove_shape(self, command: str) -> None:
        self.tsdr_request.set(command)
        self._apply_tsdr_request()

    def _element_menu(self, event) -> None:
        """Right-click on the unit-cell layout: remove a shape under the cursor from the working buildsheet."""
        cal = self.calibration
        if not (self.design and cal and self.full_image) or self.busy:
            return
        if self.global_flag or self.global_units != (1, 1) or self._special_layers(self.design.row):
            self.status.set("Right-click removal works in the Unit view (1 x 1 units) of ISO-style layouts.")
            return
        from scalar_snapshot import elements_at

        ix = (self.canvas.canvasx(event.x) - self.image_origin[0]) / self.image_scale
        iy = (self.canvas.canvasy(event.y) - self.image_origin[1]) / self.image_scale
        x = (ix - cal["origin_px"][0]) / cal["px_per_unit"][0]
        y = (cal["origin_px"][1] - iy) / cal["px_per_unit"][1]
        hits = elements_at(self.design.row, x, y, self._current_visible())
        if not hits:
            return
        menu = tk.Menu(self, tearoff=False)
        for command, text in hits:
            menu.add_command(label=f"Remove {text}   [{command}]", command=lambda c=command: self._remove_shape(c))
        menu.tk_popup(event.x_root, event.y_root)
        menu.grab_release()

    def _tsdr_region_layers(self) -> set[str]:
        tsdr_id = self.tsdr_var.get()
        return {f"TSDR region:{tsdr_id}"} if self.tsdr_region.get() and tsdr_id != "Off" else set()

    def _render_layer_preview(self, design_id: str, visible_layers: set[str]) -> Path | None:
        assert self.designer is not None
        return self.designer.layout_png(design_id, visible_layers=visible_layers, global_view=self.global_flag,
                                        routing=self.global_routing, panel=True,
                                        global_units=self.global_units, coords=self.coords_mode)

    def _draw_rulers(self, _event=None) -> None:
        """Static rulers outside the layout: poly tracks (top), diffusion tracks (left), M0 tracks / CRL (right)."""
        if not hasattr(self, "ruler_top"):
            return
        for ruler in (self.ruler_top, self.ruler_left, self.ruler_right):
            ruler.delete("all")
        cal = self.calibration
        info = cal.get("ruler") if cal else None
        if not (info and self.full_image):
            return
        coords = info["coords"]

        def label(value: float, unit0: float, glob0: float) -> str:
            unit, glob = round(value - unit0), round(value - glob0)
            return str(unit) if coords == "unit" else f"g{glob}" if coords == "global" else f"{unit}|g{glob}"

        view_x, view_y = self.canvas.canvasx(0), self.canvas.canvasy(0)
        px_x = cal["px_per_unit"][0] * self.image_scale
        px_y = cal["px_per_unit"][1] * self.image_scale

        def screen_x(x: float) -> float:
            return self.image_origin[0] + (cal["origin_px"][0] + x * cal["px_per_unit"][0]) * self.image_scale - view_x

        def screen_y(y: float) -> float:
            return self.image_origin[1] + (cal["origin_px"][1] - y * cal["px_per_unit"][1]) * self.image_scale - view_y

        width_label = 7 * len(label(info["x_max"], info["x_unit0"], info["x_glob0"])) + 8
        step = next((s for s in (1, 2, 4, 5, 10, 20, 50, 100) if s * px_x >= width_label), 100)
        top_w, side_h = self.ruler_top.winfo_width(), self.ruler_left.winfo_height()
        x_stride = step if px_x < 4 else 1
        for x in range(0, int(info["x_max"]) + 1, x_stride):
            sx = screen_x(x)
            if not 0 <= sx <= top_w:
                continue
            major = x % step == 0
            self.ruler_top.create_line(sx, 20, sx, 12 if major else 16, fill="#4a5560")
            if major:
                self.ruler_top.create_text(sx, 7, text=label(x, info["x_unit0"], info["x_glob0"]),
                                           font=self.ruler_font, fill="#2c3e50")
        cell_step = next((s for s in (1, 2, 4, 5, 10, 20) if s * px_y >= 13), 20)
        cell_stride = cell_step if px_y < 4 else 1
        for k in range(0, int(info["cells"]), cell_stride):
            sy = screen_y(k + 0.5)
            if 0 <= sy <= side_h:
                major = k % cell_step == 0
                self.ruler_left.create_line(50 if major else 52, sy, 54, sy, fill="#4a5560")
                if major:
                    self.ruler_left.create_text(48, sy, anchor="e", font=self.ruler_font, fill="#2c3e50",
                                                text=label(k, info["y_unit0"], info["y_glob0"]))
        per_height = info["per_height"]
        track_px = px_y * 2 / per_height
        track_step = next((s for s in (1, 2, 5, 10, 20, 50) if s * track_px >= 12), 50)
        track_stride = per_height if track_px < 4 else 1
        rail_step = max(1, math.ceil(13 / max(track_px * per_height, 0.001)))
        for t in range(0, int(info["m0_max"]) + 1, track_stride):
            sy = screen_y(t * 2 / per_height)
            rail = t % per_height == 0
            if not 0 <= sy <= side_h:
                continue
            major = (rail and (t // per_height) % rail_step == 0) or (not rail and t % track_step == 0)
            self.ruler_right.create_line(0, sy, 8 if rail else 6 if major else 3, sy,
                                         fill=C_RAIL_UI if rail else "#59636b", width=2 if rail else 1)
            if major:
                text = label(t, info["m0_unit0"], info["m0_glob0"]) + (f" CRL{t // per_height}" if rail else "")
                self.ruler_right.create_text(10, sy, anchor="w", text=text,
                                             fill=C_RAIL_UI if rail else "#59636b",
                                             font=(self.ruler_font[0], 8, "bold") if rail else self.ruler_font)

    def _show_legend(self, png: Path | None) -> None:
        self.legend_canvas.delete("all")
        legend = png.with_suffix(".legend.png") if png else None
        if not (legend and legend.exists()):
            self.legend_photo = None
            return
        with Image.open(legend) as image:
            self.legend_photo = ImageTk.PhotoImage(image.convert("RGBA"))
        self.legend_canvas.create_image(4, 4, anchor="nw", image=self.legend_photo)
        self.legend_canvas.configure(scrollregion=(0, 0, self.legend_photo.width() + 8, self.legend_photo.height() + 8),
                                     width=min(320, self.legend_photo.width() + 8))

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

    # -- test row --------------------------------------------------------------
    def _testrow_item(self, design: Design, source: str) -> dict:
        return {"row": dict(design.row), "source": source, "fails": design.fail_modes, "scalar": design.scalar}

    def _testrow_refresh(self, select: int | None = None) -> None:
        tree = self.testrow_tree
        tree.delete(*tree.get_children())
        for index, item in enumerate(self.testrow_items):
            row = item["row"]
            name = row.get("StructureName", "")
            tags = ("active",) if index == self.testrow_active else ("stripe",) if index % 2 else ()
            tree.insert("", "end", iid=str(index), tags=tags,
                        values=(index + 1, name, family_of(name) or "-", row.get("TestRow", ""),
                                "-" if item["fails"] is None else item["fails"], item["scalar"] or "-",
                                item["source"]))
        if select is not None and 0 <= select < len(self.testrow_items):
            tree.selection_set(str(select))
            tree.see(str(select))
        self.testrow_count.set(f"Test row: {len(self.testrow_items)}")
        self.tabs.tab(self.testrow_tab, text=f"Test row ({len(self.testrow_items)})")

    def _testrow_selected(self) -> list[int]:
        return sorted(int(iid) for iid in self.testrow_tree.selection())

    def _testrow_add_current(self) -> None:
        if not self.design:
            self.status.set("Open a structure first, then add it to the test row")
            return
        if family_of(self.design.row.get("StructureName", "")) not in FAMILY_PREFIXES:
            self.status.set("This template is available for raw inspection only; geometry is not modeled for test-row building.")
            return
        source = "library" if self.design is self.baseline else "edited"
        item = self._testrow_item(self.design, source)
        if not self._apply_name_suggestion(item):
            return
        self.testrow_items.append(item)
        self.testrow_active = len(self.testrow_items) - 1
        self._testrow_refresh(self.testrow_active)
        self.status.set(f"Added {item['row'].get('StructureName', '')} to the test row "
                        f"({len(self.testrow_items)} structures)")

    def _apply_name_suggestion(self, item: dict, replacing: int | None = None) -> bool:
        """Offer the BKM name for a test-row entry; False if the user cancels the add."""
        if not (self.suggest_names_var.get() and self.designer):
            return True
        row = item["row"]
        taken = {entry["row"].get("StructureName", "") for index, entry in enumerate(self.testrow_items)
                 if index != replacing}
        baseline = self.baseline.row if self.baseline else None
        recommended, reasons = recommend_structure_name(row, baseline, self.designer.library_rows, taken)
        if recommended == row.get("StructureName", ""):
            return True
        chosen = self._ask_name(row.get("StructureName", ""), recommended, reasons)
        if chosen is None:
            self.status.set("Add to test row cancelled")
            return False
        if chosen != row.get("StructureName", ""):
            row["StructureName"] = chosen
            item["source"] += " (renamed)"
        return True

    def _ask_name(self, current: str, recommended: str, reasons: list[str]) -> str | None:
        dialog = tk.Toplevel(self)
        dialog.title("Structure name (BKM)")
        dialog.transient(self)
        dialog.resizable(True, False)
        frame = ttk.Frame(dialog, padding=10)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Current name:").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text=current, font=("Consolas", 10)).grid(row=0, column=1, sticky="w")
        ttk.Label(frame, text="Recommended:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        value = tk.StringVar(value=recommended)
        entry = ttk.Entry(frame, textvariable=value, width=70, font=("Consolas", 10))
        entry.grid(row=1, column=1, sticky="ew", pady=(6, 0))
        ttk.Label(frame, text="Why:\n" + "\n".join(f"- {reason}" for reason in reasons) +
                  "\n\nGrammar: <FAMILY><CH>-<MOS><W>-<VT>-<TYPE>:<FEATURE>:<OGD|PGD>:<CD|REG|REF>:<N|P><nm>",
                  foreground="#4a5560", justify="left").grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 8))
        result: list[str | None] = [None]

        def close(choice: str | None) -> None:
            result[0] = choice
            dialog.destroy()

        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text="Accept", command=lambda: close(value.get().strip() or recommended)).pack(
            side="left", padx=(0, 4))
        ttk.Button(buttons, text="Keep current", command=lambda: close(current)).pack(side="left", padx=(0, 4))
        ttk.Button(buttons, text="Cancel", command=lambda: close(None)).pack(side="left")
        frame.columnconfigure(1, weight=1)
        entry.bind("<Return>", lambda _e: close(value.get().strip() or recommended))
        dialog.bind("<Escape>", lambda _e: close(None))
        entry.focus_set()
        entry.select_range(0, "end")
        dialog.grab_set()
        self.wait_window(dialog)
        return result[0]

    def _testrow_update_selected(self) -> None:
        selected = self._testrow_selected()
        if not (self.design and len(selected) == 1):
            self.status.set("Select one test-row entry and open/edit a structure to update it")
            return
        item = self._testrow_item(self.design, "edited")
        if not self._apply_name_suggestion(item, replacing=selected[0]):
            return
        self.testrow_items[selected[0]] = item
        self.testrow_active = selected[0]
        self._testrow_refresh(selected[0])
        self.status.set(f"Test-row entry {selected[0] + 1} updated from the working structure")

    def _testrow_remove(self) -> None:
        selected = self._testrow_selected()
        for index in reversed(selected):
            del self.testrow_items[index]
        if self.testrow_active is not None and selected:
            self.testrow_active = None
        self._testrow_refresh(min(selected[0], len(self.testrow_items) - 1) if selected else None)

    def _testrow_move(self, step: int) -> None:
        selected = self._testrow_selected()
        if len(selected) != 1:
            return
        index, target = selected[0], selected[0] + step
        if not 0 <= target < len(self.testrow_items):
            return
        items = self.testrow_items
        items[index], items[target] = items[target], items[index]
        if self.testrow_active in (index, target):
            self.testrow_active = target if self.testrow_active == index else index
        self._testrow_refresh(target)

    def _testrow_clear(self) -> None:
        if self.testrow_items and messagebox.askyesno("Test row", f"Remove all {len(self.testrow_items)} structures?",
                                                      parent=self):
            self.testrow_items.clear()
            self.testrow_active = None
            self._testrow_refresh()

    def _testrow_open(self, _event=None) -> None:
        selected = self._testrow_selected()
        if not (selected and self.designer) or self.busy:
            return
        index = selected[0]
        row = self.testrow_items[index]["row"]
        self.testrow_active = index
        self._run(self._open_library_row, self._show_library, f"Opening test-row entry {index + 1}...",
                  row, self._visible_layers_for_row(row))
        self._testrow_refresh(index)

    def _parse_pasted_buildsheet(self, text: str) -> tuple[list[dict[str, str]], list[str]]:
        """Rows from pasted TSV/CSV; columns matched to the template by normalised header name."""
        assert self.designer is not None
        fields = self.designer.output_fields
        normalise = lambda name: re.sub(r"[^a-z0-9]", "", name.lower())  # noqa: E731
        by_key = {normalise(field): field for field in fields}
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            raise ValueError("clipboard is empty")
        delimiter = "\t" if "\t" in lines[0] else ","
        table = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter))
        notes: list[str] = []
        if all(len(cells) == 2 for cells in table) and sum(normalise(c[0]) in by_key for c in table) >= 3:
            row = {by_key[normalise(key)]: value for key, value in table if normalise(key) in by_key}
            notes.append(f"field/value paste: {len(row)} fields")
            return [row], notes
        header = table[0]
        matched = [by_key.get(normalise(cell)) for cell in header]
        hits = sum(1 for field in matched if field)
        if hits >= max(3, len([c for c in header if c.strip()]) * 0.3):
            unknown = [cell for cell, field in zip(header, matched) if cell.strip() and not field]
            data = table[1:]
            notes.append(f"header matched {hits} of {len(header)} columns")
            if unknown:
                notes.append(f"ignored {len(unknown)} unknown column(s): {', '.join(unknown[:6])}"
                             f"{' ...' if len(unknown) > 6 else ''}")
            missing = [field for field in fields if field not in matched]
            if missing:
                notes.append(f"{len(missing)} template field(s) not in the paste were left empty")
        else:
            matched = list(fields[:len(header)])
            data = table
            notes.append(f"no header found; assumed template column order ({len(header)} columns)")
            if len(header) > len(fields):
                notes.append(f"{len(header) - len(fields)} extra trailing column(s) ignored")
        rows = []
        for cells in data:
            if not any(cell.strip() for cell in cells):
                continue
            row = {field: "" for field in fields}
            row.update({field: cell.strip() for field, cell in zip(matched, cells) if field})
            if row.get("StructureName"):
                rows.append(row)
        if not rows:
            raise ValueError("no structure rows with a StructureName were found")
        return rows, notes

    def _testrow_paste(self) -> None:
        if self.busy:
            return
        if not self.designer:
            self._activate_library(self._schema_designer(), "Pasted", label="pasted buildsheet (no Atlantis row open)")
        try:
            rows, notes = self._parse_pasted_buildsheet(self.clipboard_get())
        except (tk.TclError, csv.Error, ValueError) as exc:
            self.status.set(f"Paste buildsheet failed: {exc}")
            return
        designer = self.designer

        def evaluate() -> tuple[list[Design], list[str]]:
            designs = [designer.open_library_row(row) for row in rows]
            from scalar_snapshot import buildsheet_review_notes, buildsheet_syntax_issues

            invalid = 0
            for design in designs:
                issues = buildsheet_syntax_issues(design.row)
                if issues:
                    invalid += 1
                    design.warnings.extend(f"Buildsheet syntax: {issue}" for issue in issues)
                design.warnings.extend(f"Buildsheet review: {note}" for note in buildsheet_review_notes(design.row))
            if invalid:
                notes.append(f"syntax warnings on {invalid} pasted row(s); rows remain viewable")
            return designs, notes

        self._run(evaluate, self._testrow_pasted, f"Evaluating {len(rows)} pasted structure(s)...")

    def _testrow_pasted(self, value: tuple[list[Design], list[str]]) -> None:
        designs, notes = value
        start = len(self.testrow_items)
        self.testrow_items.extend(self._testrow_item(design, "pasted") for design in designs)
        self._testrow_refresh(start)
        self.tabs.select(self.testrow_tab)
        failed = sum(1 for design in designs if design.result is None)
        self.status.set(f"Pasted {len(designs)} structure(s) into the test row; " + "; ".join(notes)
                        + (f"; {failed} could not be evaluated (check the buildsheet)" if failed else "")
                        + ". Double-click an entry to view it.")
        self._testrow_open()

    def _testrow_rows(self) -> list[dict[str, str]]:
        name = self.testrow_name.get().strip()
        return [{**item["row"], **({"TestRow": name} if name else {})} for item in self.testrow_items]

    def _testrow_copy(self) -> None:
        if not (self.designer and self.testrow_items):
            return
        output = io.StringIO(newline="")
        writer = csv.writer(output, delimiter="\t", lineterminator="\n")
        writer.writerow(self.designer.output_fields)
        writer.writerows([row.get(field, "") for field in self.designer.output_fields] for row in self._testrow_rows())
        self._copy_to_clipboard(output.getvalue())
        self.status.set(f"Copied {len(self.testrow_items)} test-row structures with header (tab-separated)")

    def _testrow_export(self) -> None:
        if not (self.designer and self.testrow_items):
            self.status.set("The test row is empty")
            return
        from scalar_snapshot import safe_name

        path = filedialog.asksaveasfilename(parent=self, defaultextension=".csv", filetypes=[("CSV", "*.csv")],
                                            initialfile=f"{safe_name(self.testrow_name.get() or 'test_row')}.csv",
                                            title="Export test row buildsheet")
        if path:
            self.designer.export(self._testrow_rows(), Path(path))
            self.status.set(f"Exported {len(self.testrow_items)} structures to {path}")

    # -- display -------------------------------------------------------------
    def _show(self, value: tuple[Design, Path | None], keep_image: bool = False) -> None:
        design, png = value
        self.design = design
        cell_height = re.search(r"\d+", design.row.get("CellHeight", ""))
        if (cell_height and int(cell_height[0]) != 132) or is_miller_row(design.row):
            self.gds_source.set("Preview drawing")
        name = design.row.get("StructureName", "")
        geometry_supported = family_of(name) in FAMILY_PREFIXES
        template_names = (design.row.get("AtlantisTemplates") or design.row.get("TemplatesUsed")
                          or design.row.get("TemplateName") or "")
        if not template_names and not geometry_supported:
            template_text = " ".join((design.row.get("StructureName", ""),
                                      design.row.get("AtlantisLibrary", ""))).casefold()
            inferred = next((label for label in ("ROSPFT", "XTRPFX", "XTRCAP", "PFT", "MLR", "ROS")
                             if label.casefold() in template_text), None)
            template_names = f"{inferred} (inferred from library/structure name)" if inferred else \
                "not identified by Atlantis metadata"
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
        generic_geometry = has_generic_layout_geometry(design.row)
        template_area = template_active_area_size(design.row)
        pft_preview = self._is_pft_preview(design.row) and png is not None and "_pft_" in png.name
        kind = template_kind(design.row)
        miller = is_miller_row(design.row) and generic_geometry
        tagged = not miller and generic_geometry and any(
            "?" in design.row.get(field, "") for field in ("VcxPatternUnit", "VcrPatternUnit"))
        geometry_renderable = geometry_supported or generic_geometry or template_area is not None
        if not geometry_supported:
            pad_template = design.row.get("PadRowTemplate") or "not specified"
            area_hint = (f"{TEMPLATE_TITLES[kind]} preview" if kind else
                         "PFT generator layout + buildsheet pins" if pft_preview else
                         "Miller unit cell (DF/SF/GF/TAP)" if miller else
                         "unit cell with tagged nets" if tagged else
                         f"template active area {template_area[0]} x {template_area[1]} tracks"
                         if template_area else "generic base layers only" if generic_geometry else
                         "raw view only; geometry not modeled")
            details.extend((f"Templates {template_names}", f"Pad-row template {pad_template}",
                            area_hint))
        self.meta_var.set(" | ".join(details) + f" | v{design.version} {design.status}")
        if not keep_image:
            self._show_image(png)

        if geometry_supported:
            lines = [f"Test row: {design.row.get('TestRow', '-')}",
                     f"Comments: {design.row.get('StructureComments', '-') or '-'}",
                     f"Fail modes: {design.fail_modes or '-'}", f"Scalar: {design.scalar or '-'}"]
        else:
            lines = [f"Test row: {design.row.get('TestRow', '-')}", f"Atlantis template(s): {template_names}",
                     f"Pad-row template: {design.row.get('PadRowTemplate') or '-'}",
                     f"Project / library: {design.row.get('AtlantisProject') or '-'} / "
                     f"{design.row.get('AtlantisLibrary') or '-'}",
                     f"Row state: {design.row.get('AtlantisRowStatus') or '-'}",
                     f"Owner: {design.row.get('AtlantisRowOwner') or '-'}",
                     f"Last modified: {design.row.get('AtlantisLastModified') or '-'}",
                     f"Comments: {design.row.get('StructureComments', '-') or '-'}",
                     (f"{TEMPLATE_TITLES[kind]} drawn from its Atlantis template parameters (meanings from the "
                      "template help text); line CD/pitch from the structure name where encoded. Active chain / "
                      "line / via is dark, dummy lines are light."
                      if kind else
                      "PFT generator layout (PFT_LLM.bat trained layouts) with the pin vias redrawn from this row's "
                      "DrawGATE/SS/SF/DS/DF/TAP (coloured by pin); view is focused on the active PFT area - tick "
                      "'Full cell' for the whole structure. Metals, fills and IDs are off by default."
                      if pft_preview else
                      "Miller (XTRCAP) unit cell drawn from the MLR buildsheet: DiffPatternGlobal stack, "
                      "DF (drain) / SF (source, via VCRNUB-VCR) / GF (gate) / TAP (body) terminals coloured by net."
                      if miller else
                      "Unit cell drawn from the buildsheet patterns; vias / VCR tagged ?NET (e.g. GNAC, CNODE) are "
                      "traced and coloured by net."
                      if tagged else
                      f"Template active-area extent shown as {template_area[0]} x {template_area[1]} tracks; "
                      "this is an outline, not detailed layer geometry."
                      if template_area and not generic_geometry else
                      "Generic base layers shown; no family-specific paths or scalar rules are modeled."
                      if generic_geometry else
                      "Geometry: not modeled for this template; buildsheet data is available for inspection."),
                     f"Raw parameter fields: {sum(1 for value in design.row.values() if value not in ('', None))}"]
        if self.baseline and design is not self.baseline:
            changed = [field for field in self.designer.output_fields if field not in READ_ONLY_FIELDS
                       and design.row.get(field, "") != self.baseline.row.get(field, "")]
            lines.extend((f"Library baseline: {self.baseline.fail_modes or '-'} fail modes, scalar {self.baseline.scalar or '-'}",
                          f"Edited fields ({len(changed)}): {', '.join(changed) or '-'}"))
        if design.warnings:
            lines += ["", "Warnings:"] + [f"- {w}" for w in design.warnings]
        try:
            if geometry_supported:
                from scalar_snapshot import trace_nets, buildsheet_review_notes, design_rule_issues
                from ScalarCalculation import geometry_from_row

                layout_checks = trace_nets(design.row, geometry_from_row(design.row)).warnings
                from scalar_snapshot import flavor_alignment

                layout_checks = [*layout_checks, *flavor_alignment(design.row), *design_rule_issues(design.row),
                                 *buildsheet_review_notes(design.row)]
            else:
                layout_checks = []
        except Exception as exc:  # informational only
            layout_checks = [f"connectivity trace failed: {exc}"]
        from tsdr_rules import review_rules

        self._summary_state = (lines, review_rules(design.row), layout_checks, geometry_supported)
        self._render_summary()

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
        self.base_png = png
        self.render_scale = 1
        self._show_legend(png)
        if not png:
            self.photo = None
            self.full_photo = None
            self.full_image = None
            self.calibration = None
            self._draw_rulers()
            self.canvas.create_text(20, 20, anchor="nw", text="No layout preview for this structure")
            return
        self._load_image(png, 1)
        self.base_size = self.full_image.size
        self.base_ppu = (self.calibration or {}).get("px_per_unit", [0.0])[0]
        self.measure_points = []
        structure = self.design.row.get("StructureName", "") if self.design else ""
        if not self.keep_measure_var.get() and structure != self._measure_structure:
            self.measurements = []
        self._measure_structure = structure
        self._refresh_measure_menu()
        self.after_idle(self._fit_image)

    def _load_image(self, png: Path, render_scale: int) -> None:
        with Image.open(png) as image:
            self.full_image = image.convert("RGBA")
        self.full_photo = None
        self.render_scale = render_scale
        try:
            self.calibration = json.loads(png.with_suffix(".json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.calibration = None

    def _schedule_sharp(self, scale: float) -> None:
        """Re-render with a larger figure when zoomed in, so shapes stay crisp and lines stay thin."""
        wanted = 1 if self.global_units[0] * self.global_units[1] > 16 else min(4, max(1, math.ceil(scale - 0.05)))
        if self._sharp_after:
            self.after_cancel(self._sharp_after)
            self._sharp_after = None
        self._wanted_scale = wanted
        if wanted == self.render_scale or not (self.designer and self.design and self.base_png):
            return
        if wanted == 1:
            # Zoomed back out: the 1x render is already on disk, so text and lines return to normal size.
            self._sharp_after = self.after(150, lambda: self._apply_sharp(self.base_png, 1, self.base_png))
            return
        self._sharp_after = self.after(350, lambda: self._start_sharp(wanted))

    def _start_sharp(self, wanted: int) -> None:
        self._sharp_after = None
        if not (self.designer and self.design and self.base_png):
            return
        token, designer, design_id = self.base_png, self.designer, self.design.id
        kwargs = dict(visible_layers=self._current_visible(), global_view=self.global_flag,
                      routing=self.global_routing, global_units=self.global_units, coords=self.coords_mode,
                      scale=wanted, panel=True)
        self.status.set(f"Rendering sharper layout ({wanted}x)...")

        def worker() -> None:
            try:
                self.sharp_results.put((token, wanted, designer.layout_png(design_id, **kwargs)))
            except Exception:  # sharp render is optional; keep the upscaled view
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _view_centre_units(self) -> tuple[float, float] | None:
        cal = self.calibration
        if not (cal and self.full_image):
            return None
        cx = self.canvas.canvasx(self.canvas.winfo_width() / 2)
        cy = self.canvas.canvasy(self.canvas.winfo_height() / 2)
        ix, iy = (cx - self.image_origin[0]) / self.image_scale, (cy - self.image_origin[1]) / self.image_scale
        return ((ix - cal["origin_px"][0]) / cal["px_per_unit"][0], (cal["origin_px"][1] - iy) / cal["px_per_unit"][1])

    def _centre_on_units(self, units: tuple[float, float]) -> None:
        cal = self.calibration
        if not cal:
            return
        cx = self.image_origin[0] + (cal["origin_px"][0] + units[0] * cal["px_per_unit"][0]) * self.image_scale
        cy = self.image_origin[1] + (cal["origin_px"][1] - units[1] * cal["px_per_unit"][1]) * self.image_scale
        region = [float(v) for v in str(self.canvas.cget("scrollregion")).split()]
        if len(region) == 4:
            self.canvas.xview_moveto(max(0.0, (cx - self.canvas.winfo_width() / 2) / max(1.0, region[2])))
            self.canvas.yview_moveto(max(0.0, (cy - self.canvas.winfo_height() / 2) / max(1.0, region[3])))

    def _apply_sharp(self, token: Path, wanted: int, png: Path | None) -> None:
        self._sharp_after = None
        if png is None or token != self.base_png or wanted != self._wanted_scale or wanted == self.render_scale:
            return
        # Text and margins do not scale with the render, so anchor on a layout point, not on scroll fractions.
        centre = self._view_centre_units()
        self._load_image(png, wanted)
        self.current_png = png
        self._fit_image()
        if centre:
            self._centre_on_units(centre)
        self.status.set(f"Layout rendered at {wanted}x" if wanted > 1 else "Layout back at 1x")

    def _copy_layout_image(self) -> None:
        if not self.current_png:
            return
        try:
            import win32clipboard
            import win32con

            with Image.open(self.current_png) as image:
                copied = image.convert("RGBA")
            legend_path = self.current_png.with_suffix(".legend.png")
            include_legend = self.copy_legend_var.get() and legend_path.is_file()
            if include_legend:
                with Image.open(legend_path) as image:
                    legend = image.convert("RGBA")
                composite = Image.new("RGBA", (copied.width + legend.width + 16,
                                                max(copied.height, legend.height)), "white")
                composite.alpha_composite(copied, (0, 0))
                composite.alpha_composite(legend, (copied.width + 16, 0))
                copied = composite
            png_buffer = io.BytesIO()
            copied.save(png_buffer, format="PNG")
            bmp_buffer = io.BytesIO()
            copied.convert("RGB").save(bmp_buffer, format="BMP")
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_DIB, bmp_buffer.getvalue()[14:])
                win32clipboard.SetClipboardData(win32clipboard.RegisterClipboardFormat("PNG"),
                                                png_buffer.getvalue())
            finally:
                win32clipboard.CloseClipboard()
            self.status.set("Copied layout and legend to clipboard" if include_legend else
                            "Copied layout image to clipboard")
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
        if self.busy:
            self.view_var.set("Global + routing" if self.global_routing else "Global" if self.global_flag else "Unit")
            self.global_units_ogd.set(self.global_units[0])
            self.global_units_pgd.set(self.global_units[1])
            return
        returning_to_unit = self.global_flag and self.view_var.get() == "Unit"
        self.global_flag = self.view_var.get() != "Unit"
        self.global_routing = self.view_var.get() == "Global + routing"
        if returning_to_unit:
            self.global_units_ogd.set(1)
            self.global_units_pgd.set(1)
        self.coords_box.configure(state="readonly" if self.global_flag else "disabled")
        if self.global_flag and self.coords_var.get() == "Unit":
            self.coords_var.set("Both")
        self.coords_mode = ({"Unit": "unit", "Global (g)": "global", "Both": "both"}[self.coords_var.get()]
                            if self.global_flag else "unit")
        try:
            limit_ogd, limit_pgd = self._array_size()
            self.global_units = (min(max(4, limit_ogd), max(1, self.global_units_ogd.get())),
                                 min(max(4, limit_pgd), max(1, self.global_units_pgd.get())))
        except tk.TclError:
            self.global_units = (1, 1)
        self.zoom_var.set("Fit")
        if self._sharp_after:
            self.after_cancel(self._sharp_after)
            self._sharp_after = None
        self._wanted_scale = 1
        self._layers_changed()

    def _show_unit_cell(self) -> None:
        if not self.design or self.busy:
            return
        self.global_units_ogd.set(1)
        self.global_units_pgd.set(1)
        for layer, state in getattr(self, "_grid_saved", {}).items():
            if layer in self.layer_vars:
                self.layer_vars[layer].set(state)
        self._grid_saved = {}
        self.view_var.set("Unit")
        self._global_view_changed()

    def _array_size(self) -> tuple[int, int]:
        row = self.design.row if self.design else {}
        values = []
        for field in ("ArraySizeOgd", "ArraySizePgd"):
            try:
                values.append(max(1, int(float(row.get(field, "") or 1))))
            except ValueError:
                values.append(1)
        return values[0], values[1]

    def _show_full_structure(self) -> None:
        if not self.design or self.busy:
            return
        if self._special_layers(self.design.row):
            self.status.set("Full structure applies to ISO-style buildsheets; this template preview already "
                            "shows its whole drawn area.")
            return
        ogd, pgd = self._array_size()
        if not getattr(self, "_grid_saved", None):
            self._grid_saved = {layer: self.layer_vars[layer].get()
                                for layer in ("Grid", "M0 Y grid", "Guide tracks") if layer in self.layer_vars}
        for layer in self._grid_saved:
            self.layer_vars[layer].set(False)
        self.global_units_ogd.set(ogd)
        self.global_units_pgd.set(pgd)
        self.view_var.set("Global")
        self._global_view_changed()
        self.status.set(f"Full structure: {ogd} x {pgd} unit cells (library array) inside the buffer ring")

    def _apply_tsdr_request(self, _event=None) -> None:
        if not (self.designer and self.design and self.baseline) or self.busy:
            return
        fti_mode = re.fullmatch(r"fti\s+mode\s+(ml|sl)", self.tsdr_request.get().strip(), re.IGNORECASE)
        if fti_mode:
            self._apply_fti_mode(fti_mode[1].upper())
            return
        try:
            changes, notes = parse_tsdr_request(self.tsdr_request.get(), self.design.row)
        except ValueError as exc:
            self.status.set(f"TSDR request not applied: {exc}")
            return
        edited = dict(self.design.row)
        edited.update(changes)
        from scalar_snapshot import buildsheet_syntax_issues

        syntax_issues = buildsheet_syntax_issues(edited, tuple(changes))
        if syntax_issues:
            self.status.set("TSDR buildsheet syntax error: " + "; ".join(syntax_issues))
            return
        if any(field.startswith(("VcrnubPattern", "M0CutPattern")) for field in changes):
            from scalar_snapshot import vcrnub_rule_issues

            issues = vcrnub_rule_issues(edited) if any(field.startswith("VcrnubPattern") for field in changes) else []
            if issues:
                self.status.set("VCR-NUB rule violation; edit not applied: " + "; ".join(issues))
                return
        self.edit_field.set(next(iter(changes)))
        self.edit_value.set(changes[self.edit_field.get()])
        visible = self._visible_layers_for_row(edited)
        if "PXLSkew" in changes:
            visible.add("PXL / TRM")
        self._run(self._open_edited_row, self._show_working, "Applying TSDR: " + "; ".join(notes), edited, visible)

    def _apply_fti_mode(self, mode: str) -> None:
        if not (self.designer and self.design) or self.busy:
            return
        name = self.design.row.get("StructureName", "")
        match = re.search(r":FTI(?:ML|SL):", name, re.IGNORECASE)
        if not match:
            self.status.set("This structure has no FTIML/FTISL library pair; edit FTITracksUnit directly.")
            return
        target_name = name[:match.start()] + f":FTI{mode}:" + name[match.end():]
        partner = next((row for row in self.designer.library_rows
                        if row.get("StructureName", "").casefold() == target_name.casefold()), None)
        if partner is None:
            self.status.set(f"No matching FTI{mode} library variant was found.")
            return
        edited = dict(self.design.row)
        edited["StructureName"] = target_name
        for field in ("FTITracksUnit", "FTITracksGlobal"):
            if field in partner:
                edited[field] = partner.get(field, "")
        from scalar_snapshot import buildsheet_syntax_issues

        issues = buildsheet_syntax_issues(edited, ("FTITracksUnit", "FTITracksGlobal"))
        if issues:
            self.status.set("FTI mode switch not applied: " + "; ".join(issues))
            return
        self.edit_field.set("FTITracksUnit")
        self.edit_value.set(edited.get("FTITracksUnit", ""))
        visible = self._visible_layers_for_row(edited)
        self._run(self._open_edited_row, self._show_working, f"Applying FTI{mode} from paired library variant...",
                  edited, visible)

    def _zoom(self, direction: int) -> None:
        if not self.full_image:
            return
        scale = self._image_scale()
        self.zoom_scale = max(0.2, min(8.0, scale * (1.25 if direction > 0 else 0.8)))
        self.zoom_var.set("Custom")
        self._fit_image()

    def _wheel_zoom(self, event) -> None:
        self._zoom(1 if event.delta > 0 else -1)

    def _image_scale(self) -> float:
        """Zoom relative to the 1x render (the loaded image may be a sharper Nx render)."""
        if not self.full_image:
            return 1.0
        if self.zoom_var.get() == "Fit":
            base_w, base_h = self.base_size
            return min(1.0, max(1, self.canvas.winfo_width() - 12) / base_w,
                       max(1, self.canvas.winfo_height() - 12) / base_h)
        if self.zoom_var.get() == "Actual size":
            return 1.0
        return self.zoom_scale

    def _fit_image(self, _event=None) -> None:
        self._fit_after = None
        if not self.full_image:
            return
        scale = self._image_scale()
        self.zoom_percent.set(f"{scale:.0%}")
        # Scale by layout pixels-per-track so every render shows the layout at the same size.
        ppu = (self.calibration or {}).get("px_per_unit", [0.0])[0]
        display = scale * self.base_ppu / ppu if self.base_ppu and ppu else scale / self.render_scale
        if self.global_units[0] * self.global_units[1] > 16:
            display = min(display, 8192 / max(self.full_image.size),
                          math.sqrt(16_000_000 / (self.full_image.width * self.full_image.height)))
        if abs(display - 1.0) < 1e-6:
            if self.full_photo is None:
                self.full_photo = ImageTk.PhotoImage(self.full_image)
            self.photo = self.full_photo
        else:
            size = (max(1, round(self.full_image.width * display)), max(1, round(self.full_image.height * display)))
            resample = Image.Resampling.BILINEAR
            self.photo = ImageTk.PhotoImage(self.full_image.resize(size, resample))
        self.canvas.delete("preview")
        width = max(self.canvas.winfo_width(), self.photo.width())
        height = max(self.canvas.winfo_height(), self.photo.height())
        self.canvas.create_image(width // 2, height // 2,
                                 anchor="center", image=self.photo, tags="preview")
        self.canvas.configure(scrollregion=(0, 0, width, height))
        self.image_origin = (width // 2 - self.photo.width() // 2, height // 2 - self.photo.height() // 2)
        self.image_scale = self.photo.width() / self.full_image.width
        self._draw_guide_extensions()
        self._draw_measure_overlay()
        self._draw_dr_overlay()
        self._schedule_sharp(scale)
        self._draw_rulers()

    def _schedule_fit_image(self, _event=None) -> None:
        if self._fit_after:
            self.after_cancel(self._fit_after)
        self._fit_after = self.after(40, self._fit_image)

    def _draw_guide_extensions(self) -> None:
        self.canvas.delete("guide-extension")
        layers = getattr(self, "layer_vars", {})
        guide_layer = layers.get("Guide tracks")
        m0_layer = layers.get("M0 Y grid")
        draw_guides = bool(guide_layer and guide_layer.get())
        draw_m0 = bool(m0_layer and m0_layer.get())
        cal = self.calibration
        if not ((draw_guides or draw_m0) and cal and self.full_image):
            return
        px_per_unit, px_per_y = cal.get("px_per_unit", [0, 0])
        if px_per_unit <= 0 or px_per_y <= 0:
            return
        origin_x, origin_y = cal["origin_px"]
        x0, y0, x1, y1 = cal["axes_px"]
        ruler = cal.get("ruler") or {}
        layout_width = float(ruler.get("x_max", cal.get("xlim", [0, 0])[1]))
        view_left = self.canvas.canvasx(0)
        view_right = self.canvas.canvasx(self.canvas.winfo_width())
        low = ((view_left - self.image_origin[0]) / self.image_scale - origin_x) / px_per_unit
        high = ((view_right - self.image_origin[0]) / self.image_scale - origin_x) / px_per_unit
        first, last = (math.floor(2 * low), math.ceil(2 * high)) if draw_guides else (1, 0)
        top = self.image_origin[1] + y0 * self.image_scale
        bottom = self.image_origin[1] + y1 * self.image_scale
        for half_track in range(first, last + 1):
            x = half_track / 2
            if 0 <= x <= layout_width:
                continue
            screen_x = self.image_origin[0] + (origin_x + x * px_per_unit) * self.image_scale
            if not view_left <= screen_x <= view_right:
                continue
            major = half_track % 2 == 0
            self.canvas.create_line(screen_x, top, screen_x, bottom,
                                    fill="#31566e" if major else "#78858f",
                                    width=1, dash=() if major else (2, 3), tags="guide-extension")
        if draw_m0:
            per_height = int(ruler.get("per_height", 0))
            track_count = int(ruler.get("m0_max", 0))
            if per_height < 1 or track_count < 1:
                return
            view_top = self.canvas.canvasy(0)
            view_bottom = self.canvas.canvasy(self.canvas.winfo_height())
            left_edge = self.image_origin[0] + origin_x * self.image_scale
            right_edge = self.image_origin[0] + (origin_x + layout_width * px_per_unit) * self.image_scale
            for track in range(track_count + 1):
                y = 2 * track / per_height
                screen_y = self.image_origin[1] + (origin_y - y * px_per_y) * self.image_scale
                if not view_top <= screen_y <= view_bottom:
                    continue
                rail = track % per_height == 0
                color = C_RAIL_UI if rail else "#8d6e63"
                dash = () if rail else (4, 3)
                if view_left < left_edge:
                    self.canvas.create_line(view_left, screen_y, min(left_edge, view_right), screen_y,
                                            fill=color, width=2 if rail else 1, dash=dash,
                                            tags="guide-extension")
                if right_edge < view_right:
                    self.canvas.create_line(max(right_edge, view_left), screen_y, view_right, screen_y,
                                            fill=color, width=2 if rail else 1, dash=dash,
                                            tags="guide-extension")

    # -- measurement ---------------------------------------------------------
    def _canvas_to_nm(self, cx: float, cy: float, snap: bool = False) -> tuple[float, float] | None:
        cal = self.calibration
        if not cal:
            return None
        ix = (cx - self.image_origin[0]) / self.image_scale
        iy = (cy - self.image_origin[1]) / self.image_scale
        units = [(ix - cal["origin_px"][0]) / cal["px_per_unit"][0], (cal["origin_px"][1] - iy) / cal["px_per_unit"][1]]
        self.snapped = [False, False]
        if snap and self.snap_var.get():
            for axis, key in ((0, "snap_x"), (1, "snap_y")):
                values = cal.get(key) or []
                if not values:
                    continue
                tolerance = 8 / (cal["px_per_unit"][axis] * self.image_scale)
                index = bisect.bisect_left(values, units[axis])
                nearest = min(values[max(0, index - 1):index + 1], key=lambda v: abs(v - units[axis]))
                if abs(nearest - units[axis]) <= tolerance:
                    units[axis] = nearest
                    self.snapped[axis] = True
        return round(units[0] * cal["nm_per_unit"][0], 1), round(units[1] * cal["nm_per_unit"][1], 1)

    def _nm_to_canvas(self, x_nm: float, y_nm: float) -> tuple[float, float]:
        cal = self.calibration
        assert cal is not None
        ix = cal["origin_px"][0] + x_nm / cal["nm_per_unit"][0] * cal["px_per_unit"][0]
        iy = cal["origin_px"][1] - y_nm / cal["nm_per_unit"][1] * cal["px_per_unit"][1]
        return self.image_origin[0] + ix * self.image_scale, self.image_origin[1] + iy * self.image_scale

    def _render_summary(self) -> None:
        state = getattr(self, "_summary_state", None)
        if not state:
            return
        from tsdr_rules import source_status

        lines, coverage, layout_checks, geometry_supported = state
        details = self.dr_details_var.get()
        lines = [*lines, ""]
        if details:
            lines += [source_status(), coverage.summary]
            lines.extend(f"Not checked: {reason}" for reason in coverage.skipped)
        else:
            lines.append(coverage.plain_summary)
            if coverage.skipped:
                lines.append(f"{len(coverage.skipped)} rule group(s) could not be checked - turn on DR details to list them.")
        if not geometry_supported:
            layout_checks = list(coverage.display_findings(details))
        if layout_checks:
            title = ("Layout / design-rule review (best effort; 1281 deck is not final):" if details
                     else "Possible layout / rule problems:")
            lines += ["", title] + [f"- {w}" for w in layout_checks]
        if geometry_supported and details:
            lines += ["", "DRC scope: modeled ISO cell patterns, connectivity, selected width/space assumptions and VCRNUB only; "
                      "no full KLayout rule deck is configured."]
        self.summary.configure(state="normal")
        self.summary.delete("1.0", "end")
        self.summary.insert("1.0", "\n".join(lines))
        self.summary.configure(state="disabled")

    def _dr_details_changed(self) -> None:
        self._render_summary()
        if self.design and self.edit_feedback.get():
            self._edit_feedback()
        for listener in list(self._dr_detail_listeners):
            listener()

    def _draw_dr_overlay(self) -> None:
        self.canvas.delete("dr-finding")
        self.dr_highlight_count.set("")
        if not self.dr_highlight_var.get() or not (self.design and self.full_image and self.calibration):
            return
        if self.global_flag or self.global_units != (1, 1) or self._special_layers(self.design.row):
            self.dr_highlight_count.set("Unit geometry only")
            return
        from dr_geometry import review_geometry

        try:
            report = review_geometry(self.design.row)
        except (OSError, ValueError, TypeError, KeyError, ImportError) as exc:
            self.dr_highlight_count.set("Review unavailable")
            self.status.set(str(exc))
            return
        self.dr_highlight_count.set((f"{len(report.findings)} findings" + (" (first 200 shown)" if len(report.findings) > 200 else ""))
                        if report.checks else "No geometry evaluated")
        for index, finding in enumerate(report.findings[:200], 1):
            x0, y0, x1, y1 = finding.bounds
            left, top = self._nm_to_canvas(x0, y1)
            right, bottom = self._nm_to_canvas(x1, y0)
            color = "#b00020" if finding.reference else "#a65b00"
            tags = ("dr-finding", f"dr-finding-{index}")
            self.canvas.create_rectangle(left - 3, top - 3, right + 3, bottom + 3,
                                         outline=color, width=2, dash=(5, 2), tags=tags)
            self.canvas.create_text(left - 3, top - 4, text=f"#{index}", anchor="sw", fill=color,
                                    font=("Segoe UI", 9, "bold"), tags=tags)
            text = (f"#{index} {finding.label}: {finding.message}" if self.dr_details_var.get() or not finding.plain
                    else f"#{index} {finding.plain}")
            self.canvas.tag_bind(tags[1], "<Enter>", lambda _event, text=text: self.status.set(text))

    def _measure_toggled(self) -> None:
        self.measure_points = []
        self._draw_measure_overlay()
        if self.measure_var.get() and not self.calibration:
            self.status.set("Measurement needs a freshly rendered layout; toggle a layer to re-render.")

    def _measure_key_toggle(self, event=None):
        focused = self.focus_get()
        if focused and focused.winfo_class() in {"Entry", "TEntry", "Text", "TCombobox", "TSpinbox", "Spinbox"}:
            return None
        self.measure_var.set(not self.measure_var.get())
        self._measure_toggled()
        return "break"

    def _draw_measure_overlay(self) -> None:
        self.canvas.delete("measure")
        cal = self.calibration
        if not (self.measure_var.get() and cal and self.full_image):
            self._draw_measure_points()
            return
        x0, y0, x1, y1 = cal["axes_px"]
        left, top = self.image_origin[0] + x0 * self.image_scale, self.image_origin[1] + y0 * self.image_scale
        right, bottom = self.image_origin[0] + x1 * self.image_scale, self.image_origin[1] + y1 * self.image_scale
        steps = (0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000)
        low = self._canvas_to_nm(left, bottom)
        high = self._canvas_to_nm(right, top)
        assert low is not None and high is not None
        labels = []
        for axis in (0, 1):
            px_per_nm = cal["px_per_unit"][axis] * self.image_scale / cal["nm_per_unit"][axis]
            step = next((s for s in steps if s * px_per_nm >= 60), steps[-1])
            minor = step / (4 if str(step).lstrip("0.").startswith("2") else 5)
            for spacing, is_major in ((minor, False), (step, True)):
                if not is_major and minor * px_per_nm < 8:
                    continue
                value = math.floor(low[axis] / spacing) * spacing
                while value <= high[axis] + 1e-9:
                    major_hit = abs(value / step - round(value / step)) < 1e-6
                    if is_major or not major_hit:
                        style = dict(fill="#1f6fb2", width=1) if is_major else dict(fill="#6f9cc4", dash=(2, 3))
                        if axis == 0:
                            cx, _ = self._nm_to_canvas(value, 0)
                            if left <= cx <= right:
                                self.canvas.create_line(cx, top, cx, bottom, tags="measure", **style)
                                if is_major:
                                    labels.append((cx, top - 2, f"{value:g}", "s"))
                        else:
                            _, cy = self._nm_to_canvas(0, value)
                            if top <= cy <= bottom:
                                self.canvas.create_line(left, cy, right, cy, tags="measure", **style)
                                if is_major:
                                    labels.append((left - 3, cy, f"{value:g}", "e"))
                    value = round(value + spacing, 6)
        for cx, cy, text, anchor in labels:
            label = self.canvas.create_text(cx, cy, text=text, anchor=anchor, fill="#123b5e",
                                            font=("Segoe UI", 8, "bold"), tags="measure")
            box = self.canvas.bbox(label)
            background = self.canvas.create_rectangle(box[0] - 2, box[1] - 1, box[2] + 2, box[3] + 1,
                                                      fill="#eaf2fa", outline="#1f6fb2", tags="measure")
            self.canvas.tag_lower(background, label)
        self.canvas.create_rectangle(left, top, right, bottom, outline="#1f6fb2", width=2, tags="measure")
        self.canvas.create_text(right, top - 18, anchor="se", fill="#1b4f72", font=("Segoe UI", 8, "bold"),
                                text=f"nm ({cal.get('frame', 'unit')} origin)", tags="measure")
        self._draw_measure_points()

    @staticmethod
    def _measure_text(p1: tuple[float, float], p2: tuple[float, float]) -> str:
        dx, dy = round(p2[0] - p1[0], 1), round(p2[1] - p1[1], 1)
        if dy == 0:
            return f"dx {abs(dx):.1f} nm"
        if dx == 0:
            return f"dy {abs(dy):.1f} nm"
        return f"dx {dx:+.1f}  dy {dy:+.1f}  d {math.hypot(dx, dy):.1f} nm"

    def _draw_measure_points(self) -> None:
        self.canvas.delete("measure_pts")
        if not (self.measure_var.get() and self.calibration):
            return
        for index, (p1, p2) in enumerate(self.measurements):
            tags = ("measure_pts", f"meas{index}")
            (ax_, ay_), (bx, by) = self._nm_to_canvas(*p1), self._nm_to_canvas(*p2)
            for cx, cy in ((ax_, ay_), (bx, by)):
                self.canvas.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, outline="#111111", width=2, tags=tags)
            self.canvas.create_line(ax_, ay_, bx, by, fill="#111111", width=2, arrow="both", tags=tags)
            label = self.canvas.create_text((ax_ + bx) / 2, (ay_ + by) / 2 - 12,
                                            text=f"#{index + 1}  {self._measure_text(p1, p2)}", fill="#111111",
                                            font=("Segoe UI", 9, "bold"), tags=tags)
            box = self.canvas.bbox(label)
            background = self.canvas.create_rectangle(box[0] - 3, box[1] - 2, box[2] + 3, box[3] + 2,
                                                      fill="#fffde7", outline="#111111", tags=tags)
            self.canvas.tag_lower(background, label)
        for point in self.measure_points:
            cx, cy = self._nm_to_canvas(*point)
            self.canvas.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, outline="#111111", width=2, dash=(2, 2), tags="measure_pts")

    def _refresh_measure_menu(self) -> None:
        menu = self.measure_menu
        menu.delete(0, "end")
        for index, (p1, p2) in enumerate(self.measurements):
            menu.add_command(label=f"Erase #{index + 1}:  {self._measure_text(p1, p2)}",
                             command=lambda i=index: self._measure_delete(i))
        if self.measurements:
            menu.add_separator()
            menu.add_command(label="Copy all (tab-separated)", command=self._measure_copy)
            menu.add_command(label="Erase all", command=self._measure_clear_all)
        else:
            menu.add_command(label="No measurements yet", state="disabled")
        self.measure_menu_btn.configure(text=f"Measurements ({len(self.measurements)})")

    def _measure_delete(self, index: int) -> None:
        if 0 <= index < len(self.measurements):
            del self.measurements[index]
            self._refresh_measure_menu()
            self._draw_measure_points()
            self.status.set(f"Erased measurement #{index + 1}; {len(self.measurements)} left")

    def _measure_clear_all(self) -> None:
        self.measurements = []
        self.measure_points = []
        self._refresh_measure_menu()
        self._draw_measure_points()
        self.status.set("All measurements erased")

    def _measure_copy(self) -> None:
        lines = ["#\tx1 nm\ty1 nm\tx2 nm\ty2 nm\tdx nm\tdy nm\td nm"]
        for index, ((x1, y1), (x2, y2)) in enumerate(self.measurements, 1):
            dx, dy = round(x2 - x1, 1), round(y2 - y1, 1)
            lines.append(f"{index}\t{x1:.1f}\t{y1:.1f}\t{x2:.1f}\t{y2:.1f}\t{dx:.1f}\t{dy:.1f}\t{math.hypot(dx, dy):.1f}")
        self._copy_to_clipboard("\n".join(lines) + "\n")
        self.status.set(f"Copied {len(self.measurements)} measurement(s)")

    def _measure_right_click(self, event) -> None:
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        for item in reversed(self.canvas.find_overlapping(x - 5, y - 5, x + 5, y + 5)):
            tag = next((t for t in self.canvas.gettags(item) if t.startswith("meas") and t[4:].isdigit()), None)
            if tag:
                self._measure_delete(int(tag[4:]))
                return
        if self.measure_points:
            self._measure_cancel()
            return
        self._element_menu(event)

    def _measure_cancel(self, _event=None) -> None:
        if self.measure_points:
            self.measure_points = []
            self._draw_measure_points()
            self.status.set("Pick cancelled")

    def _straighten(self, point: tuple[float, float]) -> tuple[float, float]:
        """Lock the second point horizontally or vertically to the first (whichever is longer on screen)."""
        if not (self.measure_points and self.straight_var.get() and self.calibration):
            return point
        (x1, y1), (x2, y2) = self.measure_points[0], point
        cal = self.calibration
        dx_px = abs(x2 - x1) / cal["nm_per_unit"][0] * cal["px_per_unit"][0]
        dy_px = abs(y2 - y1) / cal["nm_per_unit"][1] * cal["px_per_unit"][1]
        return (x2, y1) if dx_px >= dy_px else (x1, y2)

    def _measure_motion(self, event) -> None:
        self.canvas.delete("measure_hover")
        if not (self.measure_var.get() and self.calibration):
            return
        point = self._canvas_to_nm(self.canvas.canvasx(event.x), self.canvas.canvasy(event.y), snap=True)
        if point:
            point = self._straighten(point)
            cx, cy = self._nm_to_canvas(*point)
            if self.measure_points:
                ax_, ay_ = self._nm_to_canvas(*self.measure_points[0])
                self.canvas.create_line(ax_, ay_, cx, cy, fill="#111111", dash=(4, 3), tags="measure_hover")
                self.canvas.create_text(cx + 8, cy - 8, anchor="sw", fill="#111111", font=("Segoe UI", 9, "bold"),
                                        text=self._measure_text(self.measure_points[0], point), tags="measure_hover")
            color = "#16a085" if any(self.snapped) else "#7f8c8d"
            if self.snapped[0]:
                self.canvas.create_line(cx, cy - 14, cx, cy + 14, fill=color, width=1, tags="measure_hover")
            if self.snapped[1]:
                self.canvas.create_line(cx - 14, cy, cx + 14, cy, fill=color, width=1, tags="measure_hover")
            self.canvas.create_rectangle(cx - 3, cy - 3, cx + 3, cy + 3, outline=color, tags="measure_hover")
            nm_per_px = self.calibration["nm_per_unit"][0] / (self.calibration["px_per_unit"][0] * self.image_scale)
            snap_text = "snapped " + "+".join(axis for axis, hit in zip("xy", self.snapped) if hit) if any(self.snapped) \
                else f"free pick, {nm_per_px:.1f} nm per screen pixel"
            self.status.set(f"x {point[0]:.1f} nm   y {point[1]:.1f} nm   ({self.calibration.get('frame', 'unit')} "
                            f"origin; {snap_text}; click two points to measure)")

    def _canvas_press(self, event) -> None:
        if self.measure_var.get():
            self._measure_click(event)
            return
        self._box_start = (self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))

    def _canvas_drag(self, event) -> None:
        if self._box_start is None:
            return
        x0, y0 = self._box_start
        x1, y1 = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        self.canvas.delete("zoombox")
        self.canvas.create_rectangle(x0, y0, x1, y1, outline="#1f6fb2", width=2, dash=(4, 2), tags="zoombox")

    def _canvas_release(self, event) -> None:
        """Drag a box on the layout to zoom into it (when not measuring)."""
        start, self._box_start = self._box_start, None
        self.canvas.delete("zoombox")
        if start is None or not self.full_image:
            return
        x0, y0 = start
        x1, y1 = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        box_w, box_h = abs(x1 - x0), abs(y1 - y0)
        if box_w < 10 or box_h < 10:
            return
        # Box centre in full-image pixels, which stay valid across the re-fit.
        centre_x = ((x0 + x1) / 2 - self.image_origin[0]) / self.image_scale
        centre_y = ((y0 + y1) / 2 - self.image_origin[1]) / self.image_scale
        view_w, view_h = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        factor = min(view_w / box_w, view_h / box_h)
        self.zoom_scale = max(0.2, min(8.0, self._image_scale() * factor))
        self.zoom_var.set("Custom")
        self._fit_image()
        region = [float(v) for v in str(self.canvas.cget("scrollregion")).split()] or [0, 0, view_w, view_h]
        total_w, total_h = max(1.0, region[2]), max(1.0, region[3])
        target_x = self.image_origin[0] + centre_x * self.image_scale
        target_y = self.image_origin[1] + centre_y * self.image_scale
        self.canvas.xview_moveto(max(0.0, (target_x - view_w / 2) / total_w))
        self.canvas.yview_moveto(max(0.0, (target_y - view_h / 2) / total_h))
        self.status.set(f"Zoomed to box ({self.zoom_scale:.0%}); double-click or Fit to reset")

    def _measure_click(self, event) -> None:
        if not (self.measure_var.get() and self.calibration):
            return
        point = self._canvas_to_nm(self.canvas.canvasx(event.x), self.canvas.canvasy(event.y), snap=True)
        if point is None:
            return
        if not self.measure_points:
            self.measure_points = [point]
            self.status.set(f"First point x {point[0]:.1f} y {point[1]:.1f} nm; click the second point (Esc cancels)")
        else:
            self.measurements.append((self.measure_points[0], self._straighten(point)))
            self.measure_points = []
            self.canvas.delete("measure_hover")
            self._refresh_measure_menu()
            self.status.set(f"Measurement #{len(self.measurements)}: {self._measure_text(*self.measurements[-1])}")
        self._draw_measure_points()

    def _close(self) -> None:
        self.after_cancel(self._poll_after)
        for after_id in self.atlantis_filter_after.values():
            try:
                self.after_cancel(after_id)
            except tk.TclError:
                pass
        self.destroy()

    # -- KLayout ------------------------------------------------------------------
    def _klayout_exe(self) -> str | None:
        import os
        import shutil

        settings = Path.home() / ".iso_ai_gui.json"
        try:
            saved = json.loads(settings.read_text(encoding="utf-8")).get("klayout")
        except (OSError, ValueError):
            saved = None
        candidates = [saved, shutil.which("klayout_app"), shutil.which("klayout"),
                      os.path.expandvars(r"%APPDATA%\KLayout\klayout_app.exe"),
                      os.path.expandvars(r"%LOCALAPPDATA%\KLayout\klayout_app.exe"),
                      r"C:\Program Files\KLayout\klayout_app.exe", r"C:\Program Files (x86)\KLayout\klayout_app.exe"]
        found = next((c for c in candidates if c and Path(c).is_file()), None)
        if found:
            return found
        chosen = filedialog.askopenfilename(parent=self, title="Locate klayout_app.exe",
                                            filetypes=[("KLayout", "klayout_app.exe klayout.exe"), ("All", "*.*")])
        if chosen:
            try:
                settings.write_text(json.dumps({"klayout": chosen}), encoding="utf-8")
            except OSError:
                pass
        return chosen or None

    def _show_design_rules(self) -> None:
        from dr_plain import format_value, glossary_for
        from tsdr_rules import load_rules, review_rules

        try:
            book = load_rules()
        except (OSError, ValueError, KeyError, ImportError) as exc:
            messagebox.showerror("TSDR outsheet", str(exc), parent=self)
            return
        window = tk.Toplevel(self)
        window.title("TSDR Design Rules")
        window.geometry("1200x780")
        bar = ttk.Frame(window, padding=6)
        bar.pack(fill="x")
        query = tk.StringVar()
        ttk.Label(bar, text="Search").pack(side="left")
        entry = ttk.Entry(bar, textvariable=query, width=50)
        entry.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(bar, text="Open outsheet", command=lambda: os.startfile(str(book.source))).pack(side="right")
        ttk.Checkbutton(bar, text="Show details", variable=self.dr_details_var,
                        command=self._dr_details_changed).pack(side="right", padx=6)
        ttk.Label(window, text=book.scope, wraplength=1100, padding=6).pack(fill="x")
        count = tk.StringVar()
        ttk.Label(window, textvariable=count, padding=(6, 0)).pack(anchor="w")
        split = ttk.PanedWindow(window, orient="vertical")
        split.pack(fill="both", expand=True, padx=6, pady=6)
        table_panel = ttk.Frame(split)
        split.add(table_panel, weight=3)
        table = ttk.Treeview(table_panel, columns=("label", "value", "gating", "text"),
                             show="headings", selectmode="browse")
        for key, width in (("label", 170), ("value", 110), ("gating", 210), ("text", 600)):
            table.column(key, width=width, stretch=key == "text")
        scroll = ttk.Scrollbar(table_panel, orient="vertical", command=table.yview)
        table.configure(yscrollcommand=scroll.set)
        table.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        detail = tk.Text(split, wrap="word", height=12, state="disabled")
        split.add(detail, weight=2)
        state = {"rules": [], "after": None, "view": "rule"}

        def show_text(lines: list[str]) -> None:
            detail.configure(state="normal")
            detail.delete("1.0", "end")
            detail.insert("1.0", "\n".join(lines))
            detail.configure(state="disabled")

        def review_structure() -> None:
            if not self.design:
                return
            state["view"] = "review"
            details = self.dr_details_var.get()
            report = review_rules(self.design.row)
            from dr_geometry import review_geometry

            geometric = review_geometry(self.design.row)
            text = [self.design.row.get("StructureName", ""), report.display_summary(details), ""]
            if details:
                text.append("Findings:")
                text.extend(report.findings or ("No dimensional finding in the evaluated subset. Other rules remain unchecked.",))
                text.extend(("", "Not checked:"))
                text.extend(report.skipped)
                text.extend(("", "Evaluated dimensional comparisons (TSDR ID conditions unverified):"))
                text.extend(report.checks or ("None",))
                text.extend(("", "Preview highlights (Unit view, physical nm bounds):"))
                text.extend(f"#{index} {finding.layer} {finding.label} [{finding.reference or 'geometry advisory'}] "
                            f"{finding.message}; bounds {finding.bounds}"
                            for index, finding in enumerate(geometric.findings, 1))
            else:
                text.append("Possible problems:")
                text.extend(f"- {finding}" for finding in report.display_findings())
                if not report.findings:
                    text.append("- None found in the rules that could be checked.")
                if geometric.findings:
                    text.extend(("", "Numbered boxes on the Unit view:"))
                    text.extend(f"#{index} {finding.plain or finding.message}"
                                for index, finding in enumerate(geometric.findings, 1))
                text.extend(("", f"{len(report.checks)} check groups ran; {len(report.skipped)} rule group(s) could not be "
                             "checked from the preview. Tick Show details for the full list and rule references."))
            self.dr_highlight_var.set(True)
            self._draw_dr_overlay()
            show_text(text)

        ttk.Button(bar, text="Review structure", command=review_structure,
                   state="normal" if self.design else "disabled").pack(side="right", padx=6)

        def describe(_event=None) -> None:
            selected = table.selection()
            if not selected:
                return
            state["view"] = "rule"
            rule = state["rules"][int(selected[0])]
            nearby = [item for item in book.rules if item.sheet == rule.sheet and item.section == rule.section
                      and item.subsection == rule.subsection and item.label != rule.label]
            if not self.dr_details_var.get():
                text = [f"{rule.label} - {rule.plain_gating}", "", rule.plain, "",
                        f"What this group of rules is about: {rule.plain_section}"]
                if rule.numeric_um is not None:
                    text.append(f"Limit: {format_value(rule.drawn, rule.description)}")
                terms = glossary_for(rule.description, rule.section)
                if terms:
                    text += ["", "Terms used:"] + [f"- {term}" for term in terms]
                if nearby:
                    text += ["", "Related rules in the same group:"]
                    text += [f"- {item.label}: {item.plain}" for item in nearby[:20]]
                    if len(nearby) > 20:
                        text.append(f"... {len(nearby) - 20} more (search the group name to list them)")
                text += ["", "Plain wording is generated from the outsheet; tick Show details for the exact rule text."]
                show_text(text)
                return
            numeric = "scalar numeric value; units per description" if rule.numeric_um is not None else "not a scalar numeric limit"
            text = [rule.label, rule.description, "", f"Plain: {rule.plain}", "",
                    f"Source: {book.source.name} / {rule.reference}",
                    f"Section: {rule.section}", f"Subsection: {rule.subsection}", f"Origin: {rule.origin or 'not stated'}",
                    f"Classification: {rule.gating}", f"Operator: {rule.operator or 'see description'}",
                    f"Drawn: {rule.drawn or '(blank)'}; {numeric}", f"SLV: {rule.slv or '(blank)'}",
                    f"SLL: {rule.sll or '(blank)'}", "",
                    "Conditional reference only. Numeric values may be counts or ratios; units and ID/region "
                    "conditions must be read from the description. This is not a signoff deck.", "", "Related subsection rules / notes:"]
            text.extend(f"{item.label}: {item.description} [Drawn: {item.drawn or '-'}]" for item in nearby)
            show_text(text)

        def fill() -> None:
            state["after"] = None
            details = self.dr_details_var.get()
            headings = (("Rule", "Drawn (raw)", "Classification", "Description / Conditions") if details
                        else ("Rule", "Limit", "Status", "What it means"))
            for key, title in zip(("label", "value", "gating", "text"), headings):
                table.heading(key, text=title)
            matches = book.search(query.get())
            state["rules"] = matches[:400]
            table.delete(*table.get_children())
            for index, rule in enumerate(state["rules"]):
                values = ((rule.label, rule.drawn, rule.gating, rule.description) if details else
                          (rule.label, format_value(rule.drawn, rule.description) if rule.numeric_um is not None else "",
                           rule.plain_gating, rule.plain))
                table.insert("", "end", iid=str(index), values=values)
            count.set(f"{len(matches)} matching records; showing {len(state['rules'])} of {len(book.rules)} total")
            if state["rules"]:
                table.selection_set("0")
                describe()
            else:
                show_text([])

        def schedule(*_args) -> None:
            if state["after"]:
                window.after_cancel(state["after"])
            state["after"] = window.after(180, fill)

        def details_changed() -> None:
            if not window.winfo_exists():
                return
            selected = table.selection()
            view = state["view"]
            fill()
            if selected and selected[0] in table.get_children():
                table.selection_set(selected[0])
                table.see(selected[0])
            if view == "review":
                review_structure()

        self._dr_detail_listeners.append(details_changed)
        window.bind("<Destroy>", lambda event: event.widget is window and details_changed in self._dr_detail_listeners
                    and self._dr_detail_listeners.remove(details_changed))
        table.bind("<<TreeviewSelect>>", describe)
        query.trace_add("write", schedule)
        fill()
        entry.focus_set()

    def _show_catalog(self) -> None:
        if not self.design:
            return
        import catalog_refs

        pending = catalog_refs.pending_decks()
        if pending:
            if not messagebox.askyesno(
                    "Structure catalogs",
                    f"{len(pending)} catalog deck(s) in '{catalog_refs.CATALOG_DIR.name}' have not been indexed yet.\n"
                    "Export their slides now through PowerPoint (read-only; can take several minutes)?", parent=self):
                if not catalog_refs._index():
                    return
            else:
                row = dict(self.design.row)

                def worker() -> None:
                    try:
                        catalog_refs.extract(pending, progress=lambda text: self.after(0, self.status.set, text))
                        self.after(0, self._open_catalog_window, row)
                    except Exception as exc:  # PowerPoint missing or a deck failed to open
                        self.after(0, messagebox.showerror, "Structure catalogs", str(exc))

                threading.Thread(target=worker, daemon=True).start()
                return
        self._open_catalog_window(dict(self.design.row))

    def _open_catalog_window(self, row: dict[str, str]) -> None:
        import catalog_refs

        slides = catalog_refs.find_slides(row)
        name = row.get("StructureName", "")
        if not slides:
            self.status.set(f"No catalog slide mentions {name}, its test row or its skew feature.")
            return
        window = tk.Toplevel(self)
        window.title(f"Catalog: {name}")
        window.geometry("1250x820")
        bar = ttk.Frame(window, padding=4)
        bar.pack(fill="x")
        caption = tk.StringVar()
        canvas = tk.Canvas(window, background="white", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        state = {"index": 0, "photo": None}

        def show(step: int = 0) -> None:
            state["index"] = (state["index"] + step) % len(slides)
            slide = slides[state["index"]]
            image = Image.open(slide["png"])
            scale = min(max(1, canvas.winfo_width()) / image.width, max(1, canvas.winfo_height()) / image.height)
            if scale > 0:
                image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                                     Image.Resampling.LANCZOS)
            state["photo"] = ImageTk.PhotoImage(image)
            canvas.delete("all")
            canvas.create_image(canvas.winfo_width() // 2, canvas.winfo_height() // 2, image=state["photo"])
            match = ("structure name" if slide["score"] >= 100 else "name without skew value" if slide["score"] >= 60
                     else "test row" if slide["score"] >= 40 else "skew feature")
            caption.set(f"{state['index'] + 1}/{len(slides)}   {slide['deck']}  -  slide {slide['slide']}   "
                        f"(matched on {match})")

        ttk.Button(bar, text="< Prev", command=lambda: show(-1)).pack(side="left")
        ttk.Button(bar, text="Next >", command=lambda: show(1)).pack(side="left", padx=(4, 0))
        ttk.Button(bar, text="Open PNG", command=lambda: os.startfile(slides[state["index"]]["png"])).pack(
            side="right")
        ttk.Label(bar, textvariable=caption).pack(side="left", padx=(10, 0))
        canvas.bind("<Configure>", lambda _e: show())
        window.bind("<Left>", lambda _e: show(-1))
        window.bind("<Right>", lambda _e: show(1))

    def _open_klayout(self) -> None:
        if not (self.designer and self.design) or self.busy:
            self.status.set("Open a structure first")
            return
        cell_height = re.search(r"\d+", self.design.row.get("CellHeight", ""))
        designer, design_id = self.designer, self.design.id
        if self._is_pft_preview(self.design.row):
            self._run(lambda: designer.layout_png(design_id, fmt="gds"), self._launch_klayout,
                      "Generating XTRPFX GDS with the PFT generator...")
            return
        if template_kind(self.design.row):
            self.status.set("KLayout export is not available for CHN / ECD / KLV template previews "
                            "(no template generator); use Copy PNG.")
            return
        if self.gds_source.get().startswith("Generator") and cell_height and int(cell_height[0]) != 132:
            self.gds_source.set("Preview drawing")
            self.status.set("Generator chassis is CTG132-only; switched to Preview drawing for this cell height")
        if self.gds_source.get().startswith("Generator"):
            self._run(lambda: designer.generator_gds(design_id), self._launch_generator_gds,
                      "Building GDS with claude_iso_gds_generator...")
            return
        kwargs = dict(visible_layers=self._current_visible(), global_view=self.global_flag, routing=self.global_routing,
                      global_units=self.global_units, coords=self.coords_mode, fmt="gds")
        self._run(lambda: designer.layout_png(design_id, **kwargs), self._launch_klayout, "Writing GDS for KLayout...")

    def _launch_generator_gds(self, value: tuple[Path, str]) -> None:
        path, chassis = value
        self._launch_klayout(path)
        row = self.design.row if self.design else {}
        approximate = "" if row.get("StructureName", "").startswith("CTG132") else \
            "; APPROXIMATE - the generator only has CTG132 chassis, use 'Preview drawing' for other families/heights"
        self.status.set(f"Opened generator GDS {path.name} (chassis {chassis}; full array from the buildsheet){approximate}")

    def _launch_klayout(self, gds: Path | None) -> None:
        import subprocess

        if not gds:
            self.status.set("GDS export failed: " + "; ".join(self.design.warnings[-1:] if self.design else []))
            return
        row = self.design.row if self.design else {}
        try:
            from scalar_snapshot import design_rule_issues
            issues = design_rule_issues(row)
        except Exception:
            issues = []
        exe = self._klayout_exe()
        if not exe:
            self._copy_to_clipboard(str(gds))
            note = (" | backend rule warnings: " + "; ".join(issues[:2])) if issues else ""
            self.status.set(f"KLayout not found; GDS written to {gds} (path copied){note}")
            return
        lyp = gds.with_suffix(".lyp")
        subprocess.Popen([exe, "-e", str(gds), *(["-l", str(lyp)] if lyp.exists() else [])])
        drc_note = (" | backend rule warnings: " + "; ".join(issues[:2])) if issues else ""
        self.status.set(f"Opened in KLayout (editor mode): {gds.name}{drc_note}")


if __name__ == "__main__":
    App().mainloop()
