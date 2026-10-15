"""Annotated unit-cell snapshots for ISO scalar derivations.

Draws ONE Ogd x Pgd unit cell of a structure from its buildsheet row, with
independently selectable diffusion, FTI, PLY, TCN, VG, VT, VCR, M0, and
miscellaneous TSDR layers. It overlays the leakage paths that
:mod:`ScalarCalculation` counts so the ``ScalarFormula`` can be checked.

Conventions (all from the buildsheet DSL, see ScalarCalculation.py):
* X: poly column ``c`` at x = c; TCN slot ``s`` (between poly s and s+1) at
  x = s + 0.5. FTI columns are poly columns.
* Y: diffusion tracks ("cells") 0 .. 2*UnitCellSizePgd-1 (same frame as the
  FTITracksUnit / DiffPatternUnit Y-axis). VG/VT via tracks, V0 landings and
  LO1/HI1 tracks are all M0 track indices: equally spaced, track 0 on the unit
  boundary, 5 tracks per cell height for CH110/CH132 and 6 for CH165
  (CH132 checked against the bundled chassis GDS).
    VCR indices mark each NP boundary (two diffusion cells apart).
    Both scales are shown separately when those layers are visible.
* Gate / TCN vertical extent comes from the PolyPlugsUnit / TcnPlugsUnit plug
  model: one segment per cell, plug ``V`` closes the gap between cells V-1
  and V (verified: PolyPlugs 3/4 -> cells 2..4, TCNPlugs 4/5 -> cells 3..5,
  PolyPlugs 3/6 -> cells 2..3 + 5..6).

Used through ``ScalarCalculation.py --snapshots DIR``; needs matplotlib.
"""

from __future__ import annotations

import csv
import math
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch, Rectangle  # noqa: E402

from ScalarCalculation import (  # noqa: E402
    Geometry, Result, _ete_pairs, _stacked_runs, ctg_sites, expand_axis, family_of,
    geometry_from_row, int_list, m0_tracks_per_height, plug_spans, segments, span_containing,
    split_spans, strip_shift,
)

C_NDIFF, C_PDIFF = "#b5dcb0", "#f6c9a0"
C_FTI, C_POLY, C_TCN = "#3c3c3c", "#d62728", "#1f77b4"
C_POLY_ACTIVE, C_TCN_ACTIVE = "#8b0a0a", "#0b3d66"
C_TCP, C_TCP_EDGE = "#00796b", "#004d40"
C_VCR, C_VCRNUB, C_VCR_EDGE = "#ff7f0e", "#ffbb78", "#9a4d10"
C_TRM, C_TRM_EDGE = "#c5b0d5", "#8c6bb1"
# Nets are hues no layer uses: LO cyan, HI magenta.
C_LO, C_HI, C_OTHER, C_PATH = "#17becf", "#d6006f", "#8c8c8c", "#7f00ff"
C_BRIDGE, C_SHORT = "#66a61e", "#000000"
C_RAIL = "#8c564b"
C_M0, C_M0_ALT, C_M0_GRID, C_M0_CUT = "#3b4650", "#7a7a52", "#8d6e63", "#bcbd22"
C_MISC = "#b8860b"
C_V0_DUMMY = "#3949ab"
VCRNUB_BASE_OGD_NM = 3 * 44.0
VCRNUB_MIN_SPACE_NM = 110.0 - 26.8 - 28.0
VCX_POR_SIZE_NM = {"vt": (12.0, 15.0), "vg": (12.0, 13.0)}
VCX_BOTTOM_NUDGE_NM = 0.4
NET_COLOR = {"LO": C_LO, "HI": C_HI, "BRIDGE": C_BRIDGE, "SHORT": C_SHORT, "": C_OTHER}
M0_WIDTH_NM = {110: 12, 132: 15, 165: 14}
M0_CUT_WIDTH_NM = 18
PXL_NOMINAL_CD_NM = 14
LAYOUT_LAYERS = (
    "NDIFF", "PDIFF", "VT flavor", "FTI", "PLY", "TCN", "TCP", "VG", "VT", "VCR", "PXL / TRM",
    "M0 lines", "M0 LO1", "M0 HI1", "M0 cuts", "V0 landings", "V0 dummy", "Current flow", "Fail paths", "Grid",
    "M0 Y grid", "Guide tracks",
)

_POLY_PITCH_DB = 440
_POLY_X_ORIGIN_DB = 160
# Debug-only IDs that do not change the template.
DEBUG_TSDR_IDS = frozenset({"poly:TSDRn1"})


def net_color(tracks: set[int], g: Geometry) -> str:
    if tracks & g.lo_tracks:
        return C_LO
    if tracks & g.hi_tracks:
        return C_HI
    return C_OTHER


def _contiguous_runs(cells: set[int]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    for cell in sorted(cells):
        if runs and cell == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], cell)
        else:
            runs.append((cell, cell))
    return runs


def _misc_segments(row: dict[str, str]) -> list[tuple[str, list[str]]]:
    out = []
    for segment in segments(row.get("DrawMiscIDsUnit", "")):
        parts = [part.strip() for part in strip_shift(segment).split(",")]
        if len(parts) >= 4:
            out.append((f"{parts[0]}:{parts[1]}", parts))
    return out


def layout_layers(row: dict[str, str]) -> tuple[str, ...]:
    layers = list(LAYOUT_LAYERS)
    if not row.get("TCPSkewPatternUnit"):
        layers.remove("TCP")
    g = geometry_from_row(row)
    for layer, parts in _misc_segments(row):
        if any(token in layer.upper() for token in ("TSDR", "TRM", "PXL", "TCCDEBUG", "TCCGUIDE")):
            if expand_axis(parts[2], g.ogd) and expand_axis(parts[3], g.n_cells) and layer not in layers:
                layers.append(layer)
    tsdr_layers = set(tsdr_draw_layers(row))
    drawable_misc = {
        name for name, parts in _misc_segments(row)
        if expand_axis(parts[2], g.ogd) and expand_axis(parts[3], g.n_cells)
    }
    for target in re.split(r"[;,]+", row.get("IdLayerBasedSkew", "")):
        target = target.strip()
        if target in tsdr_layers:
            continue
        if target not in drawable_misc:
            continue
        skew_layer = f"Skew target: {target}" if target else ""
        if skew_layer and skew_layer not in layers:
            layers.append(skew_layer)
    for layer in tsdr_layers:
        if layer not in layers:
            layers.append(layer)
    return tuple(layers)


def tsdr_draw_layers(row: dict[str, str]) -> tuple[str, ...]:
    layers = []
    g = geometry_from_row(row)
    for layer, parts in _misc_segments(row):
        if "TSDR" not in layer.upper() or layer in DEBUG_TSDR_IDS:
            continue
        if expand_axis(parts[2], g.ogd) and expand_axis(parts[3], g.n_cells):
            layers.append(layer)
    for target in re.split(r"[;,]+", row.get("IdLayerBasedSkew", "")):
        target = target.strip()
        if ":TSDR" not in target.upper() or target in DEBUG_TSDR_IDS:
            continue
        base = target.partition(":")[0].lower()
        drawable = {
            "poly": bool(g.poly_plugs),
            "tcn": bool(g.tcn_plugs),
            "fti": bool(g.fti_cells),
            "vcr": bool(row.get("VcrPatternUnit")),
            "vcrnub": bool(row.get("VcrnubPatternUnit")),
            "tcp_mask": bool(g.tcn_plugs),
            "pxl_mask": bool(row.get("PXLSkew") or "PXL" in row.get("StructureName", "").upper()),
        }.get(base, False)
        if drawable:
            layers.append(target)
    name = row.get("StructureName", "")
    name_parts = [part.upper() for part in name.split(":")]
    variant = next((part for part in name_parts if part in {"CD", "REG"}), "")
    purpose = {"CD": "TSDRActiveCDID", "REG": "TSDRActiveMisregID"}.get(variant)
    if purpose:
        has_pxl_tag = bool(re.search(r"(?:^|[:_-])PXL(?:$|[:_-])", name, re.IGNORECASE))
        has_vcrnub_tag = any("VCRNUB" in part for part in name_parts)
        has_vcr_tag = any(part == "VCR" for part in name_parts)
        if row.get("FTICdMisregSkew") and row.get("FTITracksUnit"):
            layers.append(f"fti:{purpose}")
        if has_pxl_tag or row.get("PXLSkew"):
            layers.append(f"PXL_mask:{purpose}")
        if has_vcrnub_tag and row.get("VcrnubPatternUnit"):
            layers.append(f"vcrnub:{purpose}")
        elif has_vcr_tag and row.get("VcrPatternUnit"):
            layers.append(f"vcr:{purpose}")
        tcp_name = any(part in {"TCP", "DENTCP", "PLYFSPCR", "PLYFTCN"} for part in name_parts)
        if (row.get("TCNCdRegSkew") or tcp_name) and row.get("TcnPlugsUnit"):
            layers.append(f"TCP_mask:{purpose}")
    if "PXL_MASK" in name_parts and re.search(r"\d+", row.get("CellHeight", "")):
        layers.append("PXL_mask")
    # IDs implied by non-zero skew values (also covers skews added without CD/REG in the name).
    slot_purpose = ("TSDRActiveCDID", "TSDRActiveCDID", "TSDRActiveMisregID", "TSDRActiveMisregID")
    for field, prefix in (("FTICdMisregSkew", "fti"), ("TCNCdRegSkew", "TCP_mask"),
                          ("PolyCdSkew", "poly"), ("GLKSkew", "glk")):
        for direction, variant, _delta in _skew_geometry(row, field):
            layers.append(f"{prefix}:{slot_purpose[_SKEW_SLOTS.index((direction, variant))]}")
    pxl = row.get("PXLSkew", "").split(",")
    try:
        if abs(float(pxl[0]) * 1000 - PXL_NOMINAL_CD_NM) > 1e-9:
            layers.append("PXL_mask:TSDRActiveCDID")
        if float(pxl[1]):
            layers.append("PXL_mask:TSDRActiveMisregID")
    except (IndexError, ValueError):
        pass
    for field, prefix in (("VcrPatternUnit", "vcr"), ("VcrnubPatternUnit", "vcrnub")) if not purpose else ():
        for segment in segments(row.get(field, "")):
            for direction, variant, _delta in _skew_geometry({"v": segment.partition("|")[2]}, "v"):
                layers.append(f"{prefix}:{slot_purpose[_SKEW_SLOTS.index((direction, variant))]}")
    for segment in segments(row.get("DrawMiscIDsGlobal", "")):
        parts = [part.strip() for part in strip_shift(segment).split(",")]
        if len(parts) >= 2 and "TSDR" in parts[1].upper():
            layers.append(f"{parts[0]}:{parts[1]} (global)")
    return tuple(dict.fromkeys(layers))


def default_tsdr_draw_layer(row: dict[str, str], choices: tuple[str, ...]) -> str:
    if not choices:
        return "Off"
    explicit = [target.strip() for target in re.split(r"[;,]+", row.get("IdLayerBasedSkew", ""))]
    for target in explicit:
        if target in choices:
            return target
    name_parts = [part.upper() for part in row.get("StructureName", "").split(":")]
    variant = next((part for part in name_parts if part in {"CD", "REG"}), "")
    purpose = {"CD": "TSDRActiveCDID", "REG": "TSDRActiveMisregID"}.get(variant)
    name = row.get("StructureName", "")
    if purpose:
        if row.get("FTICdMisregSkew") and f"fti:{purpose}" in choices:
            return f"fti:{purpose}"
        if row.get("PXLSkew") and f"PXL_mask:{purpose}" in choices:
            return f"PXL_mask:{purpose}"
        if any("VCRNUB" in part for part in name_parts) and f"vcrnub:{purpose}" in choices:
            return f"vcrnub:{purpose}"
        if "VCR" in name_parts and f"vcr:{purpose}" in choices:
            return f"vcr:{purpose}"
        tcp_name = any(part in {"TCP", "DENTCP", "PLYFSPCR", "PLYFTCN"} for part in name_parts)
        if (row.get("TCNCdRegSkew") or tcp_name) and f"TCP_mask:{purpose}" in choices:
            return f"TCP_mask:{purpose}"
    if re.search(r"(?:^|[:_-])PXL(?:$|[:_-])", row.get("StructureName", ""), re.IGNORECASE):
        pxl_choice = next((choice for choice in choices if choice.startswith("PXL_mask:")), None)
        if pxl_choice:
            return pxl_choice
    if "PXL_MASK" in name_parts and "PXL_mask" in choices:
        return "PXL_mask"
    misc_layers = [layer for layer in choices if layer not in {"PXL_mask"} and ":TSDR" in layer]
    return misc_layers[0] if misc_layers else "Off"


# VT flavor in DiffPattern (4th field, after the n/p prefix) -> (structure-name flavor ID, label, hatch).
VT_FLAVORS = {
    "lvt": ("L", "LVT", ".."), "svt": ("S", "SVT", "--"), "hvt": ("H", "HVT", "xx"),
    "ulvt": ("U", "ULVT", "//"), "uvt": ("U", "UVT", "/"), "elvt": ("E", "ELVT", "\\\\"), "evt": ("E", "EVT", "\\"),
    "ulvtll": ("UL", "ULVTLL", "++"),
}
FLAVOR_BY_ID = {"L": "lvt", "S": "svt", "H": "hvt", "U": "ulvt", "E": "elvt", "UL": "ulvtll"}
_NAME_FLAVOR = re.compile(r"[A-Z_]+\d{3}-[A-Z0-9]+-([A-Z]+)-")


def name_flavor_id(name: str) -> str | None:
    """Flavor ID in the structure name, e.g. 'U' in CTG132-NZ5-U-A:..."""
    match = _NAME_FLAVOR.match(name)
    return match[1] if match and match[1] in FLAVOR_BY_ID else None


def diff_flavors(row: dict[str, str], include_global: bool = False) -> list[tuple[str, str]]:
    """(polarity, flavor) for every DiffPattern segment, e.g. ('n', 'lvt'); the unit cell is the device under test."""
    found = []
    for field in ("DiffPatternUnit", "DiffPatternGlobal") if include_global else ("DiffPatternUnit",):
        for segment in segments(row.get(field, "")):
            parts = [part.strip() for part in strip_shift(segment).split(",")]
            match = re.fullmatch(r"([np])([a-z]+)", parts[3].lower()) if len(parts) > 3 else None
            if match:
                found.append((match[1], match[2]))
    return found


def flavor_alignment(row: dict[str, str]) -> list[str]:
    """Warnings when the name flavor ID and the DiffPattern VT flavors disagree."""
    flavor_id = name_flavor_id(row.get("StructureName", ""))
    flavors = {flavor for _polarity, flavor in diff_flavors(row)}
    if not flavor_id or not flavors:
        return []
    wrong = sorted(f for f in flavors if VT_FLAVORS.get(f, ("?",))[0] != flavor_id)
    if not wrong:
        return []
    return [f"VT flavor: name says '{flavor_id}' ({VT_FLAVORS[FLAVOR_BY_ID[flavor_id]][1]}) but DiffPattern uses "
            f"{', '.join(VT_FLAVORS.get(f, ('?', f.upper()))[1] for f in wrong)}"]


def _draw_flavor(ax, row: dict[str, str], token: str, x0: float, x1: float, y0: float, height: float) -> None:
    """Hatch a diffusion band by VT flavor, tag it, and outline it red when it disagrees with the name ID."""
    match = re.fullmatch(r"([np])([a-z]+)", token.strip().lower())
    if not match:
        return
    polarity, flavor = match.groups()
    flavor_id, label, hatch = VT_FLAVORS.get(flavor, ("?", flavor.upper(), "oo"))
    name_id = name_flavor_id(row.get("StructureName", ""))
    mismatch = name_id is not None and name_id != flavor_id
    edge = "#3f6f3a" if polarity == "n" else "#a8692e"
    ax.add_patch(Rectangle((x0, y0), x1 - x0, height, facecolor="none", hatch=hatch, edgecolor=edge,
                           linewidth=0, zorder=1.05, gid=f"VTFLAV:{flavor}"))
    if mismatch:
        ax.add_patch(Rectangle((x0, y0), x1 - x0, height, facecolor="none", edgecolor="#e00000", linewidth=1.4,
                               zorder=1.06, gid="VTFLAV:mismatch"))
    ax.text(x0 + 0.62, y0 + height / 2, f"{polarity}{label}" + (f" ≠ {name_id}" if mismatch else ""),
            fontsize=6.5, va="center", ha="left", color="#e00000" if mismatch else edge, zorder=8.2,
            bbox=dict(facecolor="white", alpha=0.85, edgecolor="none", pad=0.5))


def tsdr_color(name: str) -> str:
    """Overlay colour for a TSDR / misc ID, matching the layer it influences (prefix before ':')."""
    base = name.partition(":")[0].strip().lower()
    for keys, color in ((("fti",), C_FTI), (("poly", "ply", "glk", "pc"), C_POLY), (("tcp", "tcp_mask"), C_TCP),
                        (("tcn", "tcc"), C_TCN), (("vcrnub",), "#e08a3c"), (("vcr",), C_VCR),
                        (("pxl", "pxl_mask", "trm"), C_TRM_EDGE), (("ndiff", "diff"), "#3f8f3a"),
                        (("pdiff",), "#c98a5a"), (("m0",), C_M0)):
        if base in keys:
            return color
    return C_MISC


def _draw_misc_layer(ax, row: dict[str, str], g: Geometry, layer_name: str) -> None:
    for name, parts in _misc_segments(row):
        if name != layer_name and not (layer_name == "PXL / TRM" and any(token in name.upper() for token in ("TRM", "PXL"))):
            continue
        xs = [x for x in expand_axis(parts[2], g.ogd) if -0.5 <= x <= g.ogd + 0.5]
        ys = [y for y in expand_axis(parts[3], g.n_cells) if 0 <= y < g.n_cells]
        if not xs or not ys:
            continue
        x0, x1 = max(-0.5, min(xs) - 0.5), min(g.ogd + 0.5, max(xs) + 0.5)
        y0, y1 = min(ys), max(ys) + 1
        ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, facecolor=tsdr_color(name),
                               alpha=0.22, edgecolor=tsdr_color(name), linewidth=1.2,
                               linestyle="--", zorder=5))


def tsdr_region_rects(row: dict[str, str], g: Geometry, tsdr_id: str) -> list[tuple[float, float, float, float]]:
    """Unit-cell rectangles (x0, y0, x1, y1) of the shapes a TSDR ID selects (nominal, before skew)."""
    ogd, ncell = g.ogd, g.n_cells
    for name, parts in _misc_segments(row):
        if name == tsdr_id:
            xs, ys = expand_axis(parts[2], ogd), expand_axis(parts[3], ncell)
            if xs and ys:
                return [(max(-0.5, min(xs) - 0.5), min(ys), min(ogd + 0.5, max(xs) + 0.5), max(ys) + 1)]
    base = tsdr_id.partition(":")[0].lower()
    rects: list[tuple[float, float, float, float]] = []
    if base == "fti":
        for c, cells in g.fti_cells.items():
            for a, b in _contiguous_runs(cells):
                rects += [(x - 0.2, a, x + 0.2, b + 1) for x in ((c, ogd) if c == 0 else (c,))]
    elif base in ("poly", "glk"):
        for c in range(ogd):
            rects += [(c - 0.2, a + 0.05, c + 0.2, b + 0.95)
                      for a, b in plug_spans(g.poly_plugs.get(c, set()), ncell)]
    elif base == "tcp_mask" and row.get("TCPSkewPatternUnit"):
        for segment in segments(row.get("TCPSkewPatternUnit", "")):
            parts = [part.strip() for part in strip_shift(segment).split(",")]
            if len(parts) < 2:
                continue
            for slot in {col % ogd for col in expand_axis(parts[0], ogd - 1)}:
                rects += [(slot + 0.2, y - 0.2, slot + 0.8, y + 0.2) for y in expand_axis(parts[1], ncell)
                          if 0 < y < ncell]
    elif base in ("tcn", "tcp_mask"):
        for s in range(ogd):
            rects += [(s + 0.3, a + 0.1, s + 0.7, b + 0.9) for a, b in g.tcn_spans(s)]
    elif base in ("vcr", "vcrnub"):
        field = "VcrPatternUnit" if base == "vcr" else "VcrnubPatternUnit"
        for segment in segments(row.get(field, "")):
            parts = [part.strip() for part in strip_shift(segment).split(",")]
            if len(parts) < 2:
                continue
            xs = [x for x in expand_axis(parts[0], ogd) if 0 <= x <= ogd]
            for track in expand_axis(parts[1], g.pgd):
                if xs and base == "vcr":
                    rects.append((min(xs) - 0.5, 2 * track - 0.25, max(xs) + 0.5, 2 * track + 0.25))
                elif base == "vcrnub":
                    height_match = re.search(r"\d+", row.get("CellHeight", ""))
                    pitch = int(height_match[0]) / 2 if height_match else 66
                    vcr_height = _preview_width_nm(row, "PreviewVcrWidthNm", 12) / pitch
                    for x in (x for x in xs if x < ogd):
                        rects += [(x0 - 0.05, y0 - 0.05, x1 + 0.05, y1 + 0.05)
                                  for x0, y0, x1, y1 in _vcrnub_rects(row, segment, x, 2 * track, vcr_height, pitch)]
    elif base == "pxl_mask":
        rects.append((-0.5, 0, ogd + 0.5, ncell))
    return [(x0, max(0.0, y0), x1, min(float(ncell), y1)) for x0, y0, x1, y1 in rects]


def _draw_tsdr_region(ax, row: dict[str, str], g: Geometry, tsdr_id: str) -> None:
    color = tsdr_color(tsdr_id)
    for x0, y0, x1, y1 in tsdr_region_rects(row, g, tsdr_id):
        ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, facecolor=color, alpha=0.2, hatch="////",
                               edgecolor=color, linewidth=1.3, zorder=7.5))


@lru_cache(maxsize=3)
def _trm_chassis_data(target_height: int) -> tuple[int, int, int, int, tuple[tuple[int, int, int, int], ...]]:
    generator_dir = Path(__file__).resolve().parent / "extracted"
    if str(generator_dir) not in sys.path:
        sys.path.insert(0, str(generator_dir))
    from claude_iso_gds_generator import parse_chassis

    training_path = generator_dir / "ISO_Mini_GDS.csv"
    with open(training_path, newline="", encoding="utf-8-sig") as fh:
        candidates = list(csv.DictReader(fh))
    candidates = [row for row in candidates if row.get("LayoutFiles")]
    source = min(candidates, key=lambda row: abs(int(row.get("CELLHEIGHT", 132)) - target_height))
    chassis = parse_chassis(generator_dir / "ISO-GDSNamed" / source["LayoutFiles"])
    polygons = chassis.inventory.by_layer.get((509, 0), [])
    return (
        int(source.get("CELLHEIGHT", 132)),
        int(source.get("UnitCellSizePgd", 4)),
        int(source.get("UnitCellSizeOgd", 24)),
        int(source.get("ArraySizeOgd", 1)),
        tuple(poly.bbox() for poly in polygons),
    )


def _draw_trm_layer(ax, row: dict[str, str], g: Geometry) -> bool:
    height_match = re.search(r"\d+", row.get("CellHeight", ""))
    if not height_match:
        return False
    source_height, source_pgd, source_ogd, source_array_ogd, polygons = _trm_chassis_data(int(height_match[0]))
    if not polygons or source_pgd <= 0:
        return False
    array_ogd = int(row.get("ArraySizeOgd", "1") or 1)
    buffer_left = int(row.get("BufferSizeL", "4") or 4)
    buffer_right = int(row.get("BufferSizeR", "4") or 4)
    source_width = source_array_ogd * source_ogd
    target_width = array_ogd * g.ogd
    delta = (target_width - source_width) * _POLY_PITCH_DB
    target_x0 = _POLY_X_ORIGIN_DB + buffer_left * _POLY_PITCH_DB
    target_x1 = target_x0 + g.ogd * _POLY_PITCH_DB
    source_y_cell = source_height * 5
    source_unit_y = 2 * source_pgd * source_y_cell
    target_cells = g.n_cells
    source_cells = 2 * source_pgd
    for x0, y0, x1, y1 in polygons:
        shifted_x0 = x0 + (delta if x0 >= 0 else 0)
        shifted_x1 = x1 + (delta if x1 >= 0 else 0)
        clip_x0, clip_x1 = max(shifted_x0, target_x0), min(shifted_x1, target_x1)
        if clip_x1 <= clip_x0:
            continue
        local_x0 = (clip_x0 - target_x0) / _POLY_PITCH_DB
        local_x1 = (clip_x1 - target_x0) / _POLY_PITCH_DB
        center_y = (y0 + y1) / 2
        local_cell = (center_y % source_unit_y) / source_y_cell
        y_center = local_cell * target_cells / source_cells
        target_cell_pitch_nm = int(height_match[0]) / 2
        bar_height = (y1 - y0) / 10 / target_cell_pitch_nm
        if skew := _pxl_skew_geometry(row):
            direction, variant, delta_nm = skew
            if variant == "CD" and direction == "OGD":
                local_x0 -= delta_nm / 88
                local_x1 += delta_nm / 88
            elif variant == "CD" and direction == "PGD":
                bar_height = max(0.01, bar_height + delta_nm / (int(height_match[0]) / 2))
            elif variant == "REG" and direction == "OGD":
                local_x0 += delta_nm / 44
                local_x1 += delta_nm / 44
            elif variant == "REG" and direction == "PGD":
                y_center += delta_nm / (int(height_match[0]) / 2)
        ax.add_patch(Rectangle((local_x0, y_center - bar_height / 2), local_x1 - local_x0, bar_height,
                               facecolor=C_TRM, edgecolor=C_TRM_EDGE, linewidth=0.35,
                               alpha=0.38, zorder=1.5))
    return True


def _m0_track_y(row: dict[str, str], track: int) -> float:
    n_cells = 2 * max(1, int(row.get("UnitCellSizePgd", "") or 1))
    return (track * 2 / m0_tracks_per_height(row)) % n_cells


def _via_track_y(g: Geometry, track: int, kind: str) -> float:
    ymap = g.ymap
    assert ymap is not None
    return ymap.cell_f(track, kind)


def terminal_data(row: dict[str, str], field: str, default_side: str) -> dict:
    """``LO1Data``/``HI1Data``: ``from,to,side,pin,jumpers,stride/row,offset;row,offset``.

    e.g. ``m0,m1,L,B,10,2/0,0;1,1`` -> M0->M1 V0 on the left castle, pin at the bottom, 10 M1 jumper
    columns, a V0 on every 2nd jumper starting at jumper 0 in even unit rows and 1 in odd rows
    (matches the CTG132 chassis GDS: 5 V0 per track, rows alternating by one jumper).
    """
    parts = [part.strip() for part in row.get(field, "").split(",", 5)]
    parts += [""] * (6 - len(parts))
    side = parts[2].upper() if parts[2].upper() in ("L", "R") else default_side
    jumpers = int(parts[4]) if parts[4].isdigit() and int(parts[4]) > 0 else 10
    stride_text, _sep, offset_text = parts[5].partition("/")
    stride = int(stride_text) if stride_text.strip().isdigit() and int(stride_text) > 0 else 1
    offsets: dict[int, int] = {}
    for pair in offset_text.split(";"):
        values = [value.strip() for value in pair.split(",")]
        if len(values) == 2 and all(value.lstrip("-").isdigit() for value in values):
            offsets[int(values[0])] = int(values[1])
    return {"from": parts[0] or "m0", "to": parts[1] or "m1", "side": side, "pin": parts[3].upper(),
            "jumpers": jumpers, "stride": stride, "offsets": offsets or {0: 0}}


def term_columns(term: dict, unit_row: int) -> list[int]:
    offsets = term["offsets"]
    offset = offsets.get(unit_row % len(offsets), 0)
    return [k for k in range(term["jumpers"]) if k >= offset and (k - offset) % term["stride"] == 0]


def _terminal_side(row: dict[str, str], field: str, default: str) -> str:
    parts = [part.strip().upper() for part in row.get(field, "").split(",")]
    return parts[2] if len(parts) > 2 and parts[2] in {"L", "R"} else default


def _m0_cuts(row: dict[str, str], g: Geometry) -> dict[int, list[float]]:
    """M0CutPatternUnit ``X,Y``: X in half poly pitches, Y = M0 track (``rK`` = tracks/unit - K).

    Checked on the CTG132 chassis GDS: ``2:r2[5],1:3[1]/r3:r1[1]`` cuts tracks 1/3/17/19 at poly 1, 4, 7, ...
    """
    n_tracks = m0_tracks_per_height(row) * g.pgd
    cuts: dict[int, set[float]] = {}
    for segment in segments(row.get("M0CutPatternUnit", "")):
        parts = [part.strip() for part in strip_shift(segment).split(",")]
        if len(parts) < 2:
            continue
        xs = {x / 2 for x in expand_axis(parts[0], 2 * g.ogd)}
        for track in expand_axis("/".join(parts[1:]), n_tracks):
            cuts.setdefault(track % n_tracks, set()).update(xs)
    return {track: sorted(xs) for track, xs in cuts.items()}


@dataclass
class NetTrace:
    m0: dict[int, list[tuple[float, float]]]
    net: dict[tuple, str]
    vcr: list[tuple[int, float, float]]
    warnings: list[str]
    channels: list[tuple[int, int]]

    def label(self, node: tuple) -> str:
        return self.net.get(node, "")


def _via_shifts(row: dict[str, str], g: Geometry) -> dict[tuple[str, int, int], tuple[float, float]]:
    """VcxPatternUnit ``|d1,d2,d3,d4`` -> (OGD REG, PGD REG) via offset in layout units."""
    height_match = re.search(r"\d+", row.get("CellHeight", ""))
    cell_pitch_nm = int(height_match[0]) / 2 if height_match else 66
    out: dict[tuple[str, int, int], tuple[float, float]] = {}
    for segment in segments(row.get("VcxPatternUnit", "")):
        tokens = [token.strip() for token in strip_shift(segment).split(",")]
        values = segment.partition("|")[2].partition("$")[0].split(",")
        if len(tokens) < 3 or len(values) < 4:
            continue
        try:
            dx, dy = float(values[2]) * 1000 / 44, float(values[3]) * 1000 / cell_pitch_nm
        except ValueError:
            continue
        kind = tokens[0].lower()
        for pos in expand_axis(tokens[1], g.ogd - 1 if kind == "vt" else g.ogd):
            if g.arr_ogd > 1 or not 0 <= pos <= g.ogd:
                pos %= g.ogd
            for track in int_list(tokens[2]):
                out[(kind, pos, track)] = (dx, dy)
    return out


def _vcx_site_rect(row: dict[str, str], g: Geometry, kind: str, x: int, track: int,
                   base_x: float, base_y: float, global_scope: bool = False,
                   global_width: int | None = None, global_cells: int | None = None
                   ) -> tuple[float, float, float, float]:
    """Return the POR/overridden Vcx via rectangle in layout units."""
    height_match = re.search(r"\d+", row.get("CellHeight", ""))
    cell_pitch_nm = int(height_match[0]) / 2 if height_match else 66.0
    width_nm, height_nm = VCX_POR_SIZE_NM[kind]
    center_x, center_y = base_x, base_y
    cd_x_nm = cd_y_nm = dx_nm = dy_nm = 0.0
    x_extent = global_width if global_scope and global_width is not None else g.ogd
    track_extent = (m0_tracks_per_height(row) * global_cells // 2
                    if global_scope and global_cells is not None else m0_tracks_per_height(row) * g.pgd)
    x_axis_extent = x_extent - 1 if kind == "vt" else x_extent
    pattern_field = "VcxPatternGlobal" if global_scope else "VcxPatternUnit"
    for segment in segments(row.get(pattern_field, "")):
        parts = [part.strip() for part in strip_shift(segment).split(",")]
        if len(parts) < 3 or parts[0].lower() != kind:
            continue
        if x not in expand_axis(parts[1], x_axis_extent) or track not in expand_axis(parts[2], track_extent):
            continue
        try:
            if len(parts) > 3 and parts[3]:
                width_nm = float(parts[3]) * 1000
            if len(parts) > 4 and parts[4]:
                height_nm = float(parts[4]) * 1000
        except ValueError:
            pass
        cd_x_nm, cd_y_nm, dx_nm, dy_nm = _resize_shift_nm(segment)
        center_x += dx_nm / 44.0
        center_y += dy_nm / cell_pitch_nm
        y_axis = parts[2].split("/")
        if any(token.strip().lower().endswith("-bottom")
               and track in expand_axis(token.strip()[:-7], track_extent) for token in y_axis):
            center_y += VCX_BOTTOM_NUDGE_NM / cell_pitch_nm
        break
    width_nm += cd_x_nm
    height_nm += cd_y_nm
    return center_x - width_nm / 88.0, center_y - height_nm / (2 * cell_pitch_nm), \
        center_x + width_nm / 88.0, center_y + height_nm / (2 * cell_pitch_nm)


def trace_nets(row: dict[str, str], g: Geometry) -> NetTrace:
    """Connectivity of one unit: TCN-VT-M0, PLY-VG-M0, TCN-VCRNUB-VCR-M0 rail, M0 split by cuts.

    Components reaching the LO1/HI1 V0 are LO/HI; floating components joining two or more
    conductors through M0 (e.g. DIFFCHN jumpers) are BRIDGE; LO and HI together are SHORT.
    """
    ogd, ncell = g.ogd, g.n_cells
    per_height = m0_tracks_per_height(row)
    n_tracks = per_height * g.pgd
    cuts = _m0_cuts(row, g)
    m0: dict[int, list[tuple[float, float]]] = {}
    parent: dict[tuple, tuple] = {}

    def find(node: tuple) -> tuple:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(a: tuple | None, b: tuple | None) -> None:
        if a is not None and b is not None:
            parent[find(a)] = find(b)

    def track_segments(track: int) -> tuple[int, list[tuple[float, float]]]:
        key = track % n_tracks
        if key not in m0:
            inner = [x for x in cuts.get(key, []) if 1e-6 < x < ogd - 1e-6]
            bounds = [0.0, *inner, float(ogd)]
            m0[key] = list(zip(bounds, bounds[1:]))
        return key, m0[key]

    def m0_node(track: int, x: float) -> tuple | None:
        key, segs = track_segments(track)
        x = min(max(x, 0.0), float(ogd))
        if any(abs(x - cut) < 1e-6 for cut in cuts.get(key, [])):
            return None
        for i, (x0, x1) in enumerate(segs):
            if x0 - 1e-6 <= x <= x1 + 1e-6:
                return ("m0", key, i)
        return None

    def edge_node(track: int, side: str) -> tuple | None:
        key, segs = track_segments(track)
        edge = 0.0 if side == "L" else float(ogd)
        if any(abs(edge - cut) < 1e-6 for cut in cuts.get(key, [])):
            return None
        node = ("m0", key, 0 if side == "L" else len(segs) - 1)
        find(node)
        return node

    height_match = re.search(r"\d+", row.get("CellHeight", ""))
    cell_pitch_nm = int(height_match[0]) / 2 if height_match else 66
    poly_width = _preview_width_nm(row, "PreviewPlyWidthNm", 12) / 44
    vias: list[tuple] = []
    for col, tracks in g.vg_cols.items():
        spans = _preview_gate_spans(row, g, col, poly_width, cell_pitch_nm)
        for track in tracks:
            via = ("vg", col, track)
            find(via)
            vias.append(via)
            cell = g.ymap.cell(track, "vg")
            span = next(((a, b) for a, b in spans if a <= cell <= b), None)
            if span:
                union(via, ("ply", col, *span))
            union(via, m0_node(track, col))
    for slot, tracks in g.vt_cols.items():
        for track in tracks:
            via = ("vt", slot, track)
            find(via)
            vias.append(via)
            union(via, ("tcn", slot, *span_containing(g.tcn_spans(slot), g.ymap.cell(track, "vt"))))
            union(via, m0_node(track, slot + 0.5))

    rails: list[tuple[int, float, float]] = []
    for segment in segments(row.get("VcrPatternUnit", "")):
        parts = [part.strip() for part in strip_shift(segment).split(",")]
        xs = [x for x in expand_axis(parts[0], ogd) if -0.5 <= x <= ogd + 0.5] if len(parts) > 1 else []
        if not xs:
            continue
        left, right = max(-0.5, min(xs) - 0.5), min(ogd + 0.5, max(xs) + 0.5)
        for k in expand_axis(parts[1], g.pgd):
            if not 0 <= k <= g.pgd:
                continue
            rail = ("vcr", k, left, right)
            find(rail)
            rails.append((k, left, right))
            key, segs = track_segments(k * per_height)
            for i, (x0, x1) in enumerate(segs):
                if x1 > left and x0 < right:
                    union(rail, ("m0", key, i))
            if 0 < k < g.pgd:
                for slot in range(ogd):
                    if left <= slot + 0.5 <= right:
                        a, b = span_containing(g.tcn_spans(slot), 2 * k)
                        if a <= 2 * k - 1:
                            union(rail, ("tcn", slot, a, b))
    for segment in segments(row.get("VcrnubPatternUnit", "")):
        parts = [part.strip() for part in strip_shift(segment).split(",")]
        if len(parts) < 2:
            continue
        placement = parts[2].lower() if len(parts) > 2 else "both"
        for x in (x for x in expand_axis(parts[0], ogd - 1) if 0 <= x < ogd):
            for k in (k for k in expand_axis(parts[1], g.pgd) if 0 <= k <= g.pgd):
                nub = ("nub", x, k)
                find(nub)
                vias.append(nub)
                for rk, left, right in rails:
                    if rk == k and left <= x + 0.5 <= right:
                        union(nub, ("vcr", rk, left, right))
                cells = ([(2 * k) % ncell] if placement in ("top", "both") else []) + \
                        ([(2 * k - 1) % ncell] if placement in ("bottom", "both") else [])
                for cell in cells:
                    union(nub, ("tcn", x, *span_containing(g.tcn_spans(x), cell)))

    for tracks, field, default, net in ((g.lo_tracks, "LO1Data", "L", "LO"),
                                        (g.hi_tracks, "HI1Data", "R", "HI")):
        side = _terminal_side(row, field, default)
        for track in tracks:
            union(edge_node(track, side), ("term", net))
    if g.arr_ogd > 1:
        for key in {node[1] for node in parent if node[0] == "m0"}:
            union(edge_node(key, "L"), edge_node(key, "R"))

    # Source/drain regions: TCN joins its cells; a gate on a LO/HI net is a conducting channel.
    diff_cells: set[int] = set()
    for segment in segments(row.get("DiffPatternUnit", "") or row.get("DiffPatternGlobal", "")):
        parts = [part.strip() for part in strip_shift(segment).split(",")]
        if len(parts) >= 2:
            diff_cells.update(c for c in expand_axis(parts[1], ncell) if 0 <= c < ncell)
    for slot in range(ogd):
        spans = g.tcn_spans(slot)
        for cell in diff_cells:
            union(("diff", slot, cell), ("tcn", slot, *span_containing(spans, cell)))
    lo_root = find(("term", "LO")) if ("term", "LO") in parent else None
    hi_root = find(("term", "HI")) if ("term", "HI") in parent else None
    driven = {root for root in (lo_root, hi_root) if root is not None}
    graph: dict[tuple, set[tuple]] = {}
    channel_edges: list[tuple[int, int, tuple, tuple]] = []
    for slot in range(ogd):
        if slot + 1 >= ogd and g.arr_ogd <= 1:
            continue
        col = slot + 1
        gate_spans = _preview_gate_spans(row, g, col, poly_width, cell_pitch_nm)
        for cell in sorted(diff_cells):
            if cell in g.fti_cells.get(col % ogd, set()):
                continue
            span = next(((a, b) for a, b in gate_spans if a <= cell <= b), None)
            gate = next((node for node in (("ply", col, *span), ("ply", col % ogd, *span))
                         if node in parent), None) if span else None
            if gate is None or find(gate) not in driven:
                continue
            left, right = find(("diff", slot, cell)), find(("diff", (slot + 1) % ogd, cell))
            if left != right:
                graph.setdefault(left, set()).add(right)
                graph.setdefault(right, set()).add(left)
                channel_edges.append((col, cell, left, right))

    def reach(start: tuple | None, blocked: tuple | None) -> set[tuple]:
        if start is None:
            return set()
        seen, stack = {start}, [start]
        while stack:
            for nxt in graph.get(stack.pop(), ()):
                if nxt not in seen and nxt != blocked:
                    seen.add(nxt)
                    stack.append(nxt)
        return seen

    on_path = (reach(lo_root, hi_root) & reach(hi_root, lo_root)) - driven
    path_roots = on_path | driven
    channels = [(col, cell) for col, cell, left, right in channel_edges
                if left in path_roots and right in path_roots and (left in on_path or right in on_path
                                                                   or {left, right} == driven)]

    net: dict[tuple, str] = {}
    for node in list(parent):
        root = find(node)
        net[node] = ("SHORT" if lo_root is not None and lo_root == hi_root else
                     "LO" if root == lo_root else "HI" if root == hi_root else
                     "BRIDGE" if root in on_path else "")

    warnings: list[str] = []
    intentional = g.open_cal or family_of(row.get("StructureName", "")) == "VIA_CHAIN"
    for name, tracks, label in (("LO1", g.lo_tracks, "LO"), ("HI1", g.hi_tracks, "HI")):
        if tracks and vias and not intentional and not any(net.get(via) == label for via in vias):
            via_tracks = sorted({via[2] for via in vias if via[0] in ("vg", "vt")})
            warnings.append(f"{name} M0 track {'/'.join(map(str, sorted(tracks)))} reaches no via "
                            f"(vias on M0 {'/'.join(map(str, via_tracks))})")
    if any(label == "SHORT" for label in net.values()):
        warnings.append("LO1 and HI1 are connected through the traced conductors (short)")
    if (family_of(row.get("StructureName", "")) == "DIFFCHN" and lo_root is not None
            and hi_root is not None and not channels):
        warnings.append("no conducting LO1->HI1 chain through TCN/diffusion (check TcnPlugsUnit)")
    return NetTrace(m0, net, rails, warnings, channels)


DECODED_COLUMNS = ("Field", "#", "Layer", "X spec", "X positions", "Y spec", "Y positions", "Params", "Shift")


def _fmt_positions(values: list[float], limit: int = 12, prefix: str = "") -> str:
    text = [f"{prefix}{v:g}" for v in sorted(set(values))]
    return ", ".join(text) if len(text) <= limit else ", ".join(text[:limit]) + f", ... ({len(text)})"


_PATTERN_LAYERS = {"DiffPattern": "DIFF", "FTITracks": "FTI", "PolyPlugs": "PLY plug", "TcnPlugs": "TCN plug",
                   "TcnDepop": "TCN depop", "VcrPattern": "VCR", "VcrnubPattern": "VCRNUB",
                   "TcnExtension": "TCN ext", "TCPSkewPattern": "TCP", "M0CutPattern": "M0 cut",
                   "VcxPattern": "VCX", "DrawMiscIDs": "ID"}


def decode_buildsheet(row: dict[str, str]) -> list[tuple[str, ...]]:
    """One table row per pattern segment, axes expanded as the renderer does.

    ``*Unit`` fields use unit coordinates; ``*Global`` fields use global coordinates of the whole
    library structure (buffers + array), listed with a ``g`` prefix.
    """
    g = geometry_from_row(row)
    per_height = m0_tracks_per_height(row)
    frame = global_frame(row, g, g.arr_ogd, g.arr_pgd)
    extents = {
        "Unit": {"x": g.ogd, "slot": g.ogd - 1, "cell": g.n_cells, "m0": per_height * g.pgd,
                 "vcr": g.pgd, "vcr_label": "NP boundary", "prefix": ""},
        "Global": {"x": frame["width"], "slot": frame["width"] - 1, "cell": frame["cells"] - 1,
                   "m0": per_height * frame["cells"] // 2, "vcr": per_height * frame["cells"] // 2,
                   "vcr_label": "M0 power track", "prefix": "g"},
    }
    out: list[tuple[str, ...]] = []
    for base, layer_name in _PATTERN_LAYERS.items():
        for scope in ("Unit", "Global"):
            field, ext = base + scope, extents[scope]
            prefix = ext["prefix"]

            def add(index: int, layer: str, x_spec: str, xs: list[float], y_spec: str, ys: list[float],
                    params: str, shift: str) -> None:
                out.append((field, str(index), layer, x_spec, _fmt_positions(xs, prefix=prefix), y_spec,
                            _fmt_positions(ys, prefix=prefix), params, shift))

            for index, segment in enumerate(segments(row.get(field, "")), 1):
                parts = [part.strip() for part in strip_shift(segment).split(",")]
                shift = segment.partition("|")[2]
                if base == "VcxPattern" and len(parts) >= 3:
                    kind = parts[0].lower()
                    xs = [x + (0.5 if kind == "vt" else 0)
                          for x in expand_axis(parts[1], ext["slot"] if kind == "vt" else ext["x"])]
                    tracks = int_list(parts[2]) if scope == "Unit" else expand_axis(parts[2], ext["m0"])
                    add(index, kind.upper(), parts[1], xs, parts[2] + " (M0)", tracks,
                        "x = TCN slot + 0.5" if kind == "vt" else "x = poly track", shift)
                elif base == "DrawMiscIDs" and len(parts) >= 4:
                    add(index, f"{parts[0]}:{parts[1]}", parts[2], expand_axis(parts[2], ext["x"]),
                        parts[3], expand_axis(parts[3], ext["cell"]), "", shift)
                elif base == "M0CutPattern" and len(parts) >= 2:
                    y_spec = "/".join(parts[1:])
                    add(index, "M0 cut", parts[0] + " (half pitch)",
                        [x / 2 for x in expand_axis(parts[0], 2 * ext["x"])], y_spec + " (M0)",
                        expand_axis(y_spec, ext["m0"]), f"{M0_CUT_WIDTH_NM} nm gap", shift)
                elif len(parts) >= 2:
                    slot_axis = base in ("TcnPlugs", "VcrnubPattern", "TcnDepop") or (
                        scope == "Global" and base in ("VcrPattern", "TcnExtension"))
                    xs = expand_axis(parts[0], ext["slot"] if slot_axis else ext["x"])
                    if base.startswith("Vcr") or (base == "TcnExtension" and scope == "Global"):
                        y_spec, ys = f"{parts[1]} ({ext['vcr_label']})", expand_axis(parts[1], ext["vcr"])
                        params = parts[2] if len(parts) > 2 else ""
                    elif base in ("PolyPlugs", "TcnPlugs"):
                        y_spec = "/".join(parts[1:]) + " (plugs)"
                        ys = expand_axis("/".join(parts[1:]), ext["cell"])
                        params = "plug V joins cells V-1 and V"
                    else:
                        y_spec, ys = parts[1] + " (cells)", expand_axis(parts[1], ext["cell"])
                        params = ", ".join(parts[2:])
                    if base == "DiffPattern" and len(parts) > 2:
                        try:
                            params = f"{float(parts[2]) * 1000:g} nm, {parts[3] if len(parts) > 3 else ''}"
                        except ValueError:
                            pass
                    add(index, layer_name, parts[0] + (" (slot)" if slot_axis else ""),
                        [x + 0.5 for x in xs] if slot_axis else xs, y_spec, ys, params, shift)
    for field, data_field in (("LO1ActiveTracks", "LO1Data"), ("HI1ActiveTracks", "HI1Data")):
        if row.get(field):
            tracks = int_list(row[field])
            out.append((field, "1", field[:3], "", "", row[field] + " (unit M0)",
                        _fmt_positions(tracks) + "  =  " + _fmt_positions(
                            [t + per_height * frame["B"] for t in tracks], prefix="g"),
                        f"{data_field} = {row.get(data_field, '')}", ""))
    return out


def _m0_tracks(row: dict[str, str], g: Geometry) -> list[tuple[float, str, int | None, str]]:
    cellheight = re.search(r"\d+", row.get("CellHeight", ""))
    if not cellheight:
        return []
    per_height = m0_tracks_per_height(row)
    last_track = per_height * g.pgd
    return [(track * 2 / per_height,
             "M0 rail" if track % per_height == 0 else "M0 CLR2" if track % 2 == 0 else "M0 CLR1",
             track, C_RAIL if track % per_height == 0 else C_M0_ALT if track % 2 == 0 else C_M0)
            for track in range(last_track + 1)]


def _preview_width_nm(row: dict[str, str], field: str, default: float) -> float:
    try:
        width = float(row.get(field, "") or default)
        return width if 0 < width <= 1000 else default
    except ValueError:
        return default


def _cell_pitch_nm(row: dict[str, str]) -> float:
    height_match = re.search(r"\d+", row.get("CellHeight", ""))
    return int(height_match[0]) / 2 if height_match else 66.0


_SKEW_SLOTS = (("OGD", "CD"), ("PGD", "CD"), ("OGD", "REG"), ("PGD", "REG"))


def _skew_geometry(row: dict[str, str], field: str) -> list[tuple[str, str, float]]:
    """Every non-zero slot of an ``OGD CD, PGD CD, OGD REG, PGD REG`` skew vector (um -> nm)."""
    out = []
    for (direction, variant), raw in zip(_SKEW_SLOTS, row.get(field, "").split(",")):
        try:
            delta_nm = float(raw.strip()) * 1000
        except ValueError:
            continue
        if delta_nm:
            out.append((direction, variant, delta_nm))
    return out


def _pxl_skew_geometry(row: dict[str, str]) -> tuple[str, str, float] | None:
    name_parts = [part.upper() for part in row.get("StructureName", "").replace("-", ":").split(":")]
    values = row.get("PXLSkew", "").split(",")
    direction = next((part for part in name_parts if part in {"OGD", "PGD"}), "PGD")
    variant = next((part for part in name_parts if part in {"CD", "REG"}), "")
    if not any("PXL" in part for part in name_parts) or not variant:
        # Rows without PXL CD/REG tags (e.g. natural-language TSDR edits): use whichever value is off nominal.
        try:
            cd_nm, reg_nm = float(values[0]) * 1000, float(values[1]) * 1000
        except (IndexError, ValueError):
            return None
        variant = "REG" if reg_nm else "CD" if cd_nm != PXL_NOMINAL_CD_NM else ""
        if not variant:
            return None
    index = 0 if variant == "CD" else 1
    if index >= len(values):
        return None
    try:
        value_nm = float(values[index].strip()) * 1000
    except ValueError:
        return None
    # PXLSkew CD is the absolute PXL bias (NOM = 0.014); REG is already a delta.
    return direction, variant, value_nm - PXL_NOMINAL_CD_NM if variant == "CD" else value_nm


def _apply_skew_geometry(center_x: float, center_y: float, width: float, height: float,
                        skew, x_pitch_nm: float, y_pitch_nm: float) -> tuple[float, float, float, float]:
    skews = [skew] if isinstance(skew, tuple) else (skew or [])
    for direction, variant, delta_nm in skews:
        if variant == "CD" and direction == "OGD":
            width = max(0.03, width + delta_nm / x_pitch_nm)
        elif variant == "CD" and direction == "PGD":
            height = max(0.05, height + delta_nm / y_pitch_nm)
        elif variant == "REG" and direction == "OGD":
            center_x += delta_nm / x_pitch_nm
        elif variant == "REG" and direction == "PGD":
            center_y += delta_nm / y_pitch_nm
    return center_x, center_y, width, height


def _fti_intervals(row: dict[str, str], g: Geometry, col: int, poly_width: float,
                   cell_pitch_nm: float) -> list[tuple[float, float]]:
    """Skewed FTI Y extents that overlap poly column ``col``."""
    skew = _skew_geometry(row, "FTICdMisregSkew")
    out = []
    for start, end in _contiguous_runs(g.fti_cells.get(col % g.ogd, set())):
        center_x, center_y, width, height = _apply_skew_geometry(
            col, (start + end + 1) / 2, 0.28, end - start + 1, skew, 44, cell_pitch_nm
        )
        if abs(center_x - col) < (width + poly_width) / 2:
            out.append((center_y - height / 2, center_y + height / 2))
    return out


def _clip_to_fti(y0: float, y1: float, intervals: list[tuple[float, float]]) -> tuple[float, float]:
    for fti0, fti1 in intervals:
        if fti0 <= y0 < fti1:
            y0 = fti1
        if fti0 < y1 <= fti1:
            y1 = fti0
    return y0, y1


def _preview_gate_spans(row: dict[str, str], g: Geometry, col: int,
                        poly_width: float, cell_pitch_nm: float) -> list[tuple[int, int]]:
    """Electrical gate segments: a cell is cut when the (skewed) FTI covers most of it."""
    intervals = _fti_intervals(row, g, col, poly_width, cell_pitch_nm)
    if not intervals:
        return g.gate_spans(col)
    cuts = {cell for cell in range(g.n_cells) for y0, y1 in intervals
            if min(y1, cell + 1) - max(y0, cell) > 0.5}
    spans = plug_spans(g.poly_plugs.get(col % g.ogd, set()), g.n_cells)
    return split_spans(spans, cuts)


def dummy_via_points(row: dict[str, str], width: int, m0_count: int) -> list[tuple[float, int]]:
    """``DummyViaPattern`` (X = global M1 track k at x = k - 0.5, Y = global M0 track; verified on the CTG132
    chassis V0 56/0) -> [(global x, global M0 track)]. ``rK`` resolves against ``width`` / ``m0_count``."""
    points = []
    for segment in segments(row.get("DummyViaPattern", "")):
        parts = [part.strip() for part in strip_shift(segment).split(",")]
        if len(parts) < 2:
            continue
        for k in expand_axis(parts[0], width + 1):
            points += [(k - 0.5, t) for t in expand_axis(parts[1], m0_count) if 0 <= t <= m0_count]
    return points


def _draw_v0_dummy(ax, x: float, y: float) -> None:
    ax.add_patch(Rectangle((x - 0.2, y - 0.08), 0.4, 0.16, facecolor="white", edgecolor=C_V0_DUMMY,
                           linewidth=1.3, zorder=8.8, gid="V0DUM"))


def _nominal_outline(ax, center_x: float, center_y: float, width: float, height: float) -> None:
    """Dashed outline of a shape before skew, so a TSDR CD/REG change is visible against the filled result."""
    ax.add_patch(Rectangle((center_x - width / 2, center_y - height / 2), width, height, facecolor="none",
                           edgecolor="#000000", linestyle=(0, (2, 2)), linewidth=0.7, zorder=7.6))


def _preview_ply_geometry(row: dict[str, str], col: int, start: int, end: int,
                          width: float) -> tuple[float, float, float, float]:
    height_match = re.search(r"\d+", row.get("CellHeight", ""))
    cell_pitch_nm = int(height_match[0]) / 2 if height_match else 66
    center_x, center_y = col, (start + end + 1) / 2
    height = end - start + 0.9
    center_x, center_y, width, height = _apply_skew_geometry(
        center_x, center_y, width, height, _skew_geometry(row, "PolyCdSkew"), 44, cell_pitch_nm
    )
    return center_x, center_y, width, height


def _resize_shift_nm(segment: str) -> tuple[float, float, float, float]:
    _base, marker, raw = segment.partition("|")
    if not marker:
        return 0.0, 0.0, 0.0, 0.0
    try:
        values = [float(value.strip()) * 1000 for value in raw.partition("$")[0].split(",")]
    except ValueError:
        return 0.0, 0.0, 0.0, 0.0
    return tuple((values + [0.0] * 4)[:4])


def _vcrnub_rects(row: dict[str, str], segment: str, x: int, boundary: float, vcr_height: float,
                  cell_pitch_nm: float) -> list[tuple[float, float, float, float]]:
    """Nub rectangles (x0, y0, x1, y1) for one VcrnubPattern entry at TCN slot ``x`` and VCR boundary ``boundary``.

    The cross-section dimensions are 3 poly pitches (132 nm) wide; the top/bottom PGD projections are
    26.8/28 nm. OGD CD widens it, OGD REG shifts it in X, PGD CD moves only its far edge, and
    PGD REG shifts it in Y, per the ``|d1,d2,d3,d4`` suffix.
    """
    parts = [part.strip() for part in strip_shift(segment).split(",")]
    placement = parts[2].lower() if len(parts) > 2 else "both"
    width = _preview_width_nm(row, "PreviewVcrnubWidthNm", 3 * 44) / 44
    center_x, shift_y = x + 0.5, 0.0
    skew = _pattern_skew_nm(row, segment)
    pgd_cd = 0.0
    for direction, variant, delta_nm in ([skew] if isinstance(skew, tuple) else (skew or [])):
        if (direction, variant) == ("OGD", "CD"):
            width = max(0.03, width + delta_nm / 44)
        elif (direction, variant) == ("OGD", "REG"):
            center_x += delta_nm / 44
        elif (direction, variant) == ("PGD", "CD"):
            pgd_cd += delta_nm
        else:
            shift_y += delta_nm / cell_pitch_nm
    rects = []
    for side in ((1,) if placement == "top" else (-1,) if placement == "bottom" else (1, -1)):
        nominal_length = 26.8 if side > 0 else 28.0
        length_nm = _preview_width_nm(row, "PreviewVcrnubLengthNm", nominal_length) + pgd_cd
        length = max(0.01, length_nm / cell_pitch_nm)
        y_far = boundary + side * length
        rects.append((center_x - width / 2, min(boundary, y_far) + shift_y,
                      center_x + width / 2, max(boundary, y_far) + shift_y))
    return rects


_PATTERN_SYNTAX_FIELDS = {
    "DiffPatternUnit": "DiffPattern", "DiffPatternGlobal": "DiffPattern",
    "FTITracksUnit": "FTITracks", "FTITracksGlobal": "FTITracks",
    "PolyPlugsUnit": "PolyPlugs", "PolyPlugsGlobal": "PolyPlugs",
    "TcnPlugsUnit": "TcnPlugs", "TcnPlugsGlobal": "TcnPlugs",
    "TcnDepopUnit": "TcnDepop", "TcnDepopGlobal": "TcnDepop",
    "VcrPatternUnit": "VcrPattern", "VcrPatternGlobal": "VcrPattern",
    "VcrnubPatternUnit": "VcrnubPattern", "VcrnubPatternGlobal": "VcrnubPattern",
    "TcnExtensionUnit": "TcnExtension", "TcnExtensionGlobal": "TcnExtension",
    "TCPSkewPatternUnit": "TCPSkewPattern", "TCPSkewPatternGlobal": "TCPSkewPattern",
    "M0CutPatternUnit": "M0CutPattern", "M0CutPatternGlobal": "M0CutPattern",
    "VcxPatternUnit": "VcxPattern", "VcxPatternGlobal": "VcxPattern",
    "DrawMiscIDsUnit": "DrawMiscIDs", "DrawMiscIDsGlobal": "DrawMiscIDs",
}


def buildsheet_syntax_issues(row: dict[str, str], fields: tuple[str, ...] | None = None) -> list[str]:
    """Check supported buildsheet pattern DSL fields without rejecting legacy rows on load."""
    issues: list[str] = []
    required_ints = {
        "UnitCellSizeOgd": (1, 1000), "UnitCellSizePgd": (1, 1000),
        "ArraySizeOgd": (1, 10000), "ArraySizePgd": (1, 10000),
        "BufferSizeL": (0, 10000), "BufferSizeR": (0, 10000),
        "BufferSizeB": (0, 10000), "BufferSizeT": (0, 10000),
    }
    for field, (minimum, maximum) in required_ints.items():
        if fields is not None and field not in fields:
            continue
        raw = row.get(field, "").strip()
        if not raw or not raw.isdecimal() or not minimum <= int(raw) <= maximum:
            issues.append(f"{field} must be an integer from {minimum} to {maximum}")
    if fields is None or "PolyPitch" in fields:
        poly_pitch = row.get("PolyPitch", "").strip().lower()
        if poly_pitch != "pp44":
            issues.append(f"PolyPitch {poly_pitch or '(empty)'} is unsupported; this geometry model is fixed at pp44 (44 nm)")
    if fields is None or "CellHeight" in fields:
        cell_height = row.get("CellHeight", "").strip().lower()
        if cell_height not in {"ch110", "ch132", "ch165"}:
            issues.append("CellHeight must be ch110, ch132, or ch165")

    try:
        g = geometry_from_row(row)
        frame = global_frame(row, g, g.arr_ogd, g.arr_pgd)
        per_height = m0_tracks_per_height(row)
    except (TypeError, ValueError, KeyError) as exc:
        return [*issues, f"Buildsheet dimensions: {exc}"]

    try:
        m0_max = m0_tracks_per_height(row) * max(1, int(row.get("UnitCellSizePgd", "1") or 1))
    except (TypeError, ValueError):
        m0_max = 0
    for field in ("LO1ActiveTracks", "LO2ActiveTracks", "HI1ActiveTracks", "HI2ActiveTracks"):
        if fields is not None and field not in fields:
            continue
        raw = row.get(field, "").strip()
        if not raw:
            continue
        specs = segments(raw)
        if not specs or any(not expand_axis(spec, m0_max) for spec in specs):
            issues.append(f"{field} must use track-pattern syntax (e.g. 0;4;7 or 1:8[3])")
            continue
        tracks = [track for spec in specs for track in expand_axis(spec, m0_max)]
        if family_of(row.get("StructureName", "")) != "VIA_CHAIN" and any(
            track < 0 or track > m0_max for track in tracks):
            issues.append(f"{field} tracks must be within the unit M0 range 0..{m0_max}")

    for field in ("LO1Data", "LO2Data", "HI1Data", "HI2Data"):
        if fields is not None and field not in fields:
            continue
        raw = row.get(field, "").strip()
        if not raw:
            continue
        parts = [part.strip() for part in raw.split(",", 5)]
        if (len(parts) != 6 or not re.fullmatch(r"[A-Za-z0-9_]+", parts[0])
                or not re.fullmatch(r"[A-Za-z0-9_]+", parts[1])
                or parts[2].upper() not in {"L", "R"} or parts[3].upper() not in {"B", "T"}
                or not parts[4].isdigit() or int(parts[4]) < 1):
            issues.append(f"{field} must be layer,layer,L|R,B|T,pinWidth,pinSpacing")
            continue
        stride, separator, offsets = parts[5].partition("/")
        if not stride.isdigit() or int(stride) < 1:
            issues.append(f"{field} pin spacing/stride must be a positive integer")
            continue
        if separator:
            offset_pairs = offsets.split(";")
            if any(not re.fullmatch(r"-?\d+\s*,\s*-?\d+", pair.strip()) for pair in offset_pairs):
                issues.append(f"{field} row offsets must use row,offset pairs separated by semicolons")

    for field in ("M0LegoPattern", "M0CutLegoPattern"):
        if fields is not None and field not in fields:
            continue
        raw = row.get(field, "").strip().rstrip(";")
        if not raw:
            continue
        try:
            values = [float(value.strip()) for value in raw.split(";")]
            if len(values) < 2 or len(values) % 2 or not all(math.isfinite(value) and value > 0 for value in values):
                raise ValueError
        except ValueError:
            issues.append(f"{field} must be positive CD/space pairs separated by semicolons")

    for field in ("kernelOffsetX", "kernelOffsetY"):
        if fields is not None and field not in fields:
            continue
        raw = row.get(field, "").strip()
        if raw and not _is_finite_number(raw):
            issues.append(f"{field} must be a finite number")
    if fields is None or "AddSpecialID" in fields:
        for item in segments(row.get("AddSpecialID", "")):
            if not re.fullmatch(r"digital(?::id)?", item, re.IGNORECASE):
                issues.append("AddSpecialID entries must use the supported Digital:id form")
                break
    if fields is None or "DeleteLpps" in fields:
        for item in segments(row.get("DeleteLpps", "")):
            if not re.fullmatch(r"[^,:;]+:[^,:;]+", item):
                issues.append("DeleteLpps entries must use layer:purpose syntax")
                break

    for field in ("FTICdMisregSkew", "TCNCdRegSkew", "PolyCdSkew", "GLKSkew"):
        if fields is not None and field not in fields:
            continue
        raw = row.get(field, "").strip().rstrip(";")
        if not raw:
            continue
        values = [value.strip() for value in raw.split(",")]
        try:
            for value in values[:4]:
                if not value or not math.isfinite(float(value)):
                    raise ValueError
        except ValueError:
            issues.append(f"{field} must start with comma-separated numeric OGD CD, PGD CD, OGD REG, PGD REG values")
        if any(value.upper() not in {"Y", "N", "0"} for value in values[4:]):
            issues.append(f"{field} has an invalid trailing flag")
    if fields is None or "PXLSkew" in fields:
        raw = row.get("PXLSkew", "").strip()
        if raw:
            values = [value.strip() for value in raw.split(",")]
            try:
                if len(values) < 2 or not all(math.isfinite(float(value)) for value in values[:2]):
                    raise ValueError
            except ValueError:
                issues.append("PXLSkew must start with numeric CD and REG values")
            if len(values) > 2 and not values[2]:
                issues.append("PXLSkew selector cannot be empty")

    def check_axis(spec: str, extent: int, label: str) -> None:
        if not spec or not expand_axis(spec, extent):
            issues.append(f"{label} axis is not parseable: {spec!r}")

    for field, base in _PATTERN_SYNTAX_FIELDS.items():
        if fields is not None and field not in fields:
            continue
        pattern = row.get(field, "")
        if not pattern.strip():
            continue
        global_scope = field.endswith("Global")
        width = frame["width"] if global_scope else g.ogd
        cells = frame["cells"] - 1 if global_scope else g.n_cells
        slots = width - 1
        m0_tracks = per_height * frame["cells"] // 2 if global_scope else per_height * g.pgd
        vcr_tracks = per_height * frame["cells"] // 2 if global_scope else g.pgd
        for index, segment in enumerate(segments(pattern), 1):
            parts = [part.strip() for part in strip_shift(segment).split(",")]
            label = f"{field} segment {index}"
            shift = segment.partition("|")[2]
            if shift:
                shift_values = [value.strip() for value in shift.partition("$")[0].split(",")]
                net_name = shift.partition("$")[2].strip()
                try:
                    if len(shift_values) > 4 or not all(math.isfinite(float(value)) for value in shift_values):
                        raise ValueError
                except ValueError:
                    issues.append(f"{label} resize-shift must contain up to four numeric values")
                if "$" in shift and (base != "VcxPattern" or not net_name):
                    issues.append(f"{label} net suffix is only supported as a non-empty VcxPattern $net name")
            if base == "PolyPlugs" and len(parts) != 2:
                issues.append(f"{label}: PolyPlugs requires exactly X-pattern,Y-pattern")
                continue
            if base == "PolyPlugs" and re.fullmatch(r"(-?\d+)/(-?\d+)", parts[0]):
                first, second = (int(value) for value in parts[0].split("/"))
                if abs(first - second) == 1:
                    issues.append(f"{label}: consecutive PolyPlugs tracks must use a range (e.g. {min(first, second)}:{max(first, second)})")
            if base == "VcxPattern":
                if len(parts) not in {3, 4, 5} or parts[0].lower() not in {"vt", "vg"}:
                    issues.append(f"{label}: expected vt|vg,X,Y")
                    continue
                x_extent = slots if parts[0].lower() == "vt" else width
                check_axis(parts[1], x_extent, label + " X")
                check_axis(parts[2], m0_tracks, label + " M0")
                for value in parts[3:5]:
                    try:
                        if not math.isfinite(float(value)) or float(value) <= 0:
                            raise ValueError
                    except ValueError:
                        issues.append(f"{label} optional via widths must be positive numbers")
                continue
            if base == "DrawMiscIDs":
                if len(parts) >= 4:
                    x_spec, y_spec = parts[2], parts[3]
                elif len(parts) == 3 and ":" in parts[0]:
                    x_spec, y_spec = parts[1], parts[2]
                else:
                    issues.append(f"{label}: expected layer:purpose,X,Y or layer,purpose,X,Y")
                    continue
                check_axis(x_spec, width, label + " X")
                check_axis(y_spec, cells, label + " Y")
                grow_values = parts[4:]
                if grow_values and (len(grow_values) != 4 or any(not _is_finite_number(value) for value in grow_values)):
                    issues.append(f"{label} grow values must provide four finite numbers")
                if shift and global_scope and (len(shift_values) != 4 or any(not _is_finite_number(value) for value in shift_values)):
                    issues.append(f"{label} global repeat syntax requires repeatX,repeatY,distX,distY")
                continue
            if len(parts) < 2:
                issues.append(f"{label}: expected at least X,Y")
                continue
            if base == "M0CutPattern":
                check_axis(parts[0], 2 * width, label + " X")
                check_axis("/".join(parts[1:]), m0_tracks, label + " M0")
            else:
                slot_axis = base in {"TcnPlugs", "TcnDepop", "TcnExtension", "TCPSkewPattern",
                                     "VcrPattern", "VcrnubPattern"}
                x_extent = slots if slot_axis else width
                check_axis(parts[0], x_extent, label + " X")
                if base in {"VcrPattern", "VcrnubPattern", "TcnExtension"}:
                    y_extent = vcr_tracks
                    y_spec = parts[1]
                elif base in {"PolyPlugs", "TcnPlugs", "TcnDepop"}:
                    y_extent = cells
                    y_spec = "/".join(parts[1:])
                else:
                    y_extent = cells
                    y_spec = parts[1]
                check_axis(y_spec, y_extent, label + " Y")
                if base == "DiffPattern":
                    try:
                        width_um = float(parts[2])
                    except (IndexError, ValueError):
                        width_um = -1
                    if not math.isfinite(width_um) or width_um < 0:
                        issues.append(f"{label} diffusion width must be a non-negative um value")
                    elif len(parts) not in {3, 4}:
                        issues.append(f"{label}: DiffPattern requires X,Y,width[,devflav]")
                    elif width_um > 0:
                        allowed_flavors = {polarity + flavor for polarity in "np" for flavor in VT_FLAVORS}
                        if len(parts) < 4 or parts[3].lower() not in allowed_flavors:
                            issues.append(f"{label} positive diffusion width requires a supported devflav: "
                                          + ", ".join(sorted(allowed_flavors)))
                if base == "VcrnubPattern":
                    if len(parts) < 3 or parts[2].lower() not in {"top", "bottom", "both"}:
                        issues.append(f"{label} side must be top, bottom or both")
    return list(dict.fromkeys(issues))


def _is_finite_number(value: str) -> bool:
    try:
        return math.isfinite(float(value.strip()))
    except ValueError:
        return False


def buildsheet_review_notes(row: dict[str, str], fields: tuple[str, ...] | None = None) -> list[str]:
    """Non-blocking style notes for legal but potentially confusing track patterns."""
    notes: list[str] = []
    for field in ("LO1ActiveTracks", "LO2ActiveTracks", "HI1ActiveTracks", "HI2ActiveTracks"):
        if fields is not None and field not in fields:
            continue
        for spec in segments(row.get(field, "")):
            if "/" not in spec:
                continue
            tracks = expand_axis(spec, 10000)
            if tracks and tracks != sorted(tracks):
                notes.append(f"{field} slash list {spec!r} is descending; verify this intentional track order")
    return notes


def vcx_rule_issues(row: dict[str, str], fields: tuple[str, ...] | None = None) -> list[str]:
    """Best-effort Vt/Vg via-CD range checks from the supplied X81Z skew table."""
    issues: list[str] = []
    for field in ("VcxPatternUnit", "VcxPatternGlobal"):
        if fields is not None and field not in fields:
            continue
        for index, segment in enumerate(segments(row.get(field, "")), 1):
            parts = [part.strip() for part in strip_shift(segment).split(",")]
            if len(parts) < 3 or parts[0].lower() not in VCX_POR_SIZE_NM:
                continue
            kind = parts[0].lower()
            por_ogd, por_pgd = VCX_POR_SIZE_NM[kind]
            try:
                ogd_nm = float(parts[3]) * 1000 if len(parts) > 3 and parts[3] else por_ogd
                pgd_nm = float(parts[4]) * 1000 if len(parts) > 4 and parts[4] else por_pgd
            except ValueError:
                continue
            cd_ogd_nm, cd_pgd_nm, _reg_ogd, _reg_pgd = _resize_shift_nm(segment)
            ogd_nm += cd_ogd_nm
            pgd_nm += cd_pgd_nm
            if kind == "vt":
                ogd_range = (8.0, 17.0)
                pgd_range = (22.0, 29.0) if pgd_nm >= 22.0 else (8.0, 21.0)
            else:
                ogd_range = pgd_range = (8.0, 19.0)
            for axis, value, bounds in (("OGD", ogd_nm, ogd_range), ("PGD", pgd_nm, pgd_range)):
                if not bounds[0] <= value <= bounds[1]:
                    issues.append(f"{field} segment {index} {kind.upper()} {axis} size {value:g} nm is outside "
                                  f"the X81Z table range {bounds[0]:g}..{bounds[1]:g} nm (not-final advisory)")
    return list(dict.fromkeys(issues))


def design_rule_issues(row: dict[str, str], fields: tuple[str, ...] | None = None) -> list[str]:
    """Best-effort cell-rule advisories grounded in the draft 1281 front-end deck."""
    issues: list[str] = []
    if fields is None or any(field in fields for field in ("VcrnubPatternUnit", "VcrnubPatternGlobal")):
        issues.extend(vcrnub_rule_issues(row, fields))
    if fields is None or any(field in fields for field in ("VcxPatternUnit", "VcxPatternGlobal")):
        issues.extend(vcx_rule_issues(row, fields))
    if fields is not None and not any(field in fields for field in (
            "PolyPitch", "PreviewPlyWidthNm", "PreviewTcnWidthNm", "PreviewVcrWidthNm", "PreviewM0WidthNm")):
        return list(dict.fromkeys(issues))
    if row.get("PolyPitch", "").strip().lower() not in {"", "pp44"}:
        issues.append("1281 deck advisory (not final): PolyPitch is specified as 44 nm (pp44)")
    for field, expected in (("PreviewPlyWidthNm", 12.0), ("PreviewTcnWidthNm", 12.0),
                            ("PreviewVcrWidthNm", 12.0)):
        value = row.get(field, "").strip()
        if not value:
            continue
        try:
            actual = float(value)
        except ValueError:
            continue
        if abs(actual - expected) > 1e-9:
            issues.append(f"1281 deck advisory (not final): {field}={actual:g} nm; deck working assumption is {expected:g} nm")
    m0_width = row.get("PreviewM0WidthNm", "").strip()
    height_match = re.search(r"\d+", row.get("CellHeight", ""))
    expected_m0 = M0_WIDTH_NM.get(int(height_match[0])) if height_match else None
    if expected_m0 is not None and height_match:
        actual_m0 = expected_m0
        try:
            if m0_width:
                actual_m0 = float(m0_width)
                if abs(actual_m0 - expected_m0) > 1e-9:
                    issues.append(f"1281 deck advisory (not final): M0 preview width is {m0_width} nm; CH{height_match[0]} deck assumption is {expected_m0} nm")
            track_pitch = int(height_match[0]) / m0_tracks_per_height(row)
            clear_space = track_pitch - actual_m0
            if clear_space < 9.0 - 1e-9:
                issues.append(f"1281 deck advisory (not final): M0 preview track space is {clear_space:g} nm < 9 nm at CH{height_match[0]}")
        except ValueError:
            pass
    return list(dict.fromkeys(issues))


def vcrnub_rule_issues(row: dict[str, str], fields: tuple[str, ...] | None = None) -> list[str]:
    """VCR-NUB rule violations: post-skew OGD size >= 3PP and clear space >= 55.2 nm."""
    g = geometry_from_row(row)
    issues: list[str] = []
    cell_pitch_nm = _cell_pitch_nm(row)
    vcr_height = _preview_width_nm(row, "PreviewVcrWidthNm", 12) / cell_pitch_nm
    rectangles: list[tuple[str, float, float, float, float]] = []
    pattern_fields = (("VcrnubPatternUnit", False), ("VcrnubPatternGlobal", True))
    for field, global_scope in pattern_fields:
        if fields is not None and field not in fields:
            continue
        if not row.get(field, "").strip():
            continue
        if global_scope:
            frame = global_frame(row, g, g.arr_ogd, g.arr_pgd)
            x_extent, y_extent = frame["width"] - 1, frame["cells"] // 2
            x_offset, track_offset = 0, 0
        else:
            x_extent, y_extent = g.ogd - 1, g.pgd
            x_offset, track_offset = 0, 0
        for segment in segments(row.get(field, "")):
            parts = [part.strip() for part in strip_shift(segment).split(",")]
            if len(parts) < 2:
                continue
            skew = _pattern_skew_nm(row, segment)
            delta = skew[2] if isinstance(skew, tuple) and skew[:2] == ("OGD", "CD") else 0.0
            nominal_width = _preview_width_nm(row, "PreviewVcrnubWidthNm", VCRNUB_BASE_OGD_NM)
            width_nm = nominal_width + delta
            if width_nm < VCRNUB_BASE_OGD_NM - 1e-9:
                issues.append(f"{field}: OGD CD gives {width_nm:g} nm; VCRNUB must remain at least 3PP "
                              f"({VCRNUB_BASE_OGD_NM:g} nm)")
            for x in expand_axis(parts[0], x_extent):
                for track in expand_axis(parts[1], y_extent):
                    for x0, y0, x1, y1 in _vcrnub_rects(row, segment, x + x_offset,
                                                       2 * (track + track_offset), vcr_height, cell_pitch_nm):
                        rectangles.append((field, x0, y0, x1, y1))
    sweep = sorted(rectangles, key=lambda rect: rect[1])
    active: list[tuple[str, float, float, float, float]] = []
    for current in sweep:
        field, x0, y0, x1, y1 = current
        active = [other for other in active if other[3] > x0]
        for other in active:
            other_field, ox0, oy0, ox1, oy1 = other
            if min(x1, ox1) <= max(x0, ox0):
                continue
            if y1 <= oy0:
                gap = (oy0 - y1) * cell_pitch_nm
            elif oy1 <= y0:
                gap = (y0 - oy1) * cell_pitch_nm
            else:
                continue
            if gap < VCRNUB_MIN_SPACE_NM - 1e-9:
                issues.append(f"{field}: post-skew nub space {gap:g} nm < {VCRNUB_MIN_SPACE_NM:g} nm")
        active.append(current)
    return list(dict.fromkeys(issues))


def _pattern_skew_nm(row: dict[str, str], segment: str):
    name_parts = [part.upper() for part in row.get("StructureName", "").split(":")]
    direction = next((part for part in name_parts if part in {"OGD", "PGD"}), "")
    variant = next((part for part in name_parts if part in {"CD", "REG"}), "")
    marker = segment.partition("|")[2]
    values = marker.split(",") if marker else []
    if not direction or not variant:
        # No skew tags in the name: treat the suffix as an OGD CD, PGD CD, OGD REG, PGD REG vector.
        return _skew_geometry({"v": marker}, "v")
    index = {("OGD", "CD"): 0, ("PGD", "CD"): 1,
             ("OGD", "REG"): 2, ("PGD", "REG"): 3}[(direction, variant)]
    slots = _skew_geometry({"v": marker}, "v")
    try:
        delta_nm = float(values[index].strip()) * 1000 if index < len(values) else 0.0
    except ValueError:
        delta_nm = 0.0
    if not delta_nm and len(slots) == 1:
        # Some rows (e.g. TCNTCN132 VCR CD/REG) store the named skew in another slot.
        delta_nm = slots[0][2]
    return (direction, variant, delta_nm) if delta_nm else None


def _draw_m0(ax, row: dict[str, str], g: Geometry, visible: set[str]) -> list[str]:
    draw_lines, draw_cuts = "M0 lines" in visible, "M0 cuts" in visible
    if not draw_lines and not draw_cuts:
        return []
    rules = _m0_cuts(row, g)
    n_tracks = m0_tracks_per_height(row) * g.pgd
    half_gap = M0_CUT_WIDTH_NM / 44 / 2
    height_match = re.search(r"\d+", row.get("CellHeight", ""))
    height = int(height_match[0]) if height_match else 132
    nominal_width = M0_WIDTH_NM.get(height, 15)
    line_width = min(1.25, max(0.3, 0.65 * _preview_width_nm(row, "PreviewM0WidthNm", nominal_width) / nominal_width))
    for y, layer, index, color in _m0_tracks(row, g):
        cut_xs = [x for x in rules.get(index % n_tracks, []) if -half_gap <= x <= g.ogd + half_gap]
        track_width = line_width * (1.8 if layer == "M0 rail" else 1.0)
        if draw_lines:
            cursor = 0.0
            for x in cut_xs:
                if x - half_gap > cursor:
                    ax.plot([cursor, x - half_gap], [y, y], color=color, linewidth=track_width, zorder=3.2)
                cursor = max(cursor, x + half_gap)
            if cursor < g.ogd:
                ax.plot([cursor, g.ogd], [y, y], color=color, linewidth=track_width, zorder=3.2)
            if "V0 landings" in visible:
                for active_tracks, data_field, default_side in (
                    (g.lo_tracks, "LO1Data", "L"), (g.hi_tracks, "HI1Data", "R")
                ):
                    if any(abs(y - _m0_track_y(row, track)) < 1e-6 for track in active_tracks):
                        if _terminal_side(row, data_field, default_side) == "L":
                            ax.plot([-0.48, 0], [y, y], color=color, linewidth=line_width, zorder=3.2)
                        else:
                            ax.plot([g.ogd, g.ogd + 0.48], [y, y], color=color, linewidth=line_width, zorder=3.2)
        if draw_cuts:
            for x in cut_xs:
                ax.plot([x, x], [y - 0.12, y + 0.12], color=C_M0_CUT, linewidth=1.6, zorder=7)
    return []


def _draw_current_flow(ax, row: dict[str, str], g: Geometry, trace: NetTrace, poly_width: float,
                       tcn_width: float, tcn_skew, cell_pitch_nm: float, vcr_height: float) -> None:
    for node, label in trace.net.items():
        if label not in ("LO", "HI", "BRIDGE", "SHORT"):
            continue
        color = NET_COLOR[label]
        if node[0] == "m0":
            x0, x1 = trace.m0[node[1]][node[2]]
            y = _m0_track_y(row, node[1])
            ax.plot([x0, x1], [y, y], color=color, linewidth=4.0, alpha=0.5, solid_capstyle="butt", zorder=3.3)
        elif node[0] in ("tcn", "ply"):
            _kind, pos, a, b = node
            if node[0] == "tcn":
                cx, cy, w, h = _apply_skew_geometry(pos + 0.5, (a + b + 1) / 2, tcn_width, b - a + 0.8,
                                                    tcn_skew, 44, cell_pitch_nm)
            else:
                cx, cy, w, h = _preview_ply_geometry(row, pos, a, b, poly_width)
                y0, y1 = _clip_to_fti(cy - h / 2, cy + h / 2,
                                      _fti_intervals(row, g, pos, poly_width, cell_pitch_nm))
                cy, h = (y0 + y1) / 2, max(0.0, y1 - y0)
            ax.add_patch(Rectangle((cx - w / 2, cy - h / 2), w, h, facecolor="none", edgecolor=color,
                                   linewidth=1.8, zorder=3.9))
        elif node[0] == "vcr":
            _kind, k, left, right = node
            ax.add_patch(Rectangle((left, 2 * k - vcr_height / 2), right - left, vcr_height, facecolor="none",
                                   edgecolor=color, linewidth=1.8, zorder=5.5))
    for col, cell in trace.channels:
        ax.plot([col - 0.45, col + 0.45], [cell + 0.5, cell + 0.5], color=C_BRIDGE, linewidth=2.4,
                alpha=0.85, solid_capstyle="butt", zorder=4.2)
    for tracks, field, default, label in ((g.lo_tracks, "LO1Data", "L", "LO"), (g.hi_tracks, "HI1Data", "R", "HI")):
        x0, x1 = (-0.48, 0.0) if _terminal_side(row, field, default) == "L" else (float(g.ogd), g.ogd + 0.48)
        for track in tracks:
            y = _m0_track_y(row, track)
            ax.plot([x0, x1], [y, y], color=NET_COLOR[label], linewidth=4.0, alpha=0.5,
                    solid_capstyle="butt", zorder=3.3)


# ---------------------------------------------------------------------------
# Base layout
# ---------------------------------------------------------------------------
def draw_base(ax, row: dict[str, str], g: Geometry, visible_layers: set[str] | None = None) -> dict[int, float]:
    ogd, ncell = g.ogd, g.n_cells
    visible = set(layout_layers(row)) if visible_layers is None else visible_layers
    _draw_m0(ax, row, g, visible)
    if "Guide tracks" in visible:
        for col in range(ogd + 1):
            ax.axvline(col, color="#31566e", linewidth=0.75, alpha=0.6, zorder=7.2)
        for slot in range(ogd):
            ax.axvline(slot + 0.5, color="#78858f", linewidth=0.55, alpha=0.42,
                       linestyle=(0, (2, 3)), zorder=7.1)
    if "M0 Y grid" in visible:
        for y, layer, _index, _color in _m0_tracks(row, g):
            rail = layer == "M0 rail"
            ax.axhline(y, color=C_RAIL if rail else C_M0_GRID, linewidth=1.3 if rail else 0.8,
                       linestyle="-" if rail else (0, (4, 3)), alpha=0.85, zorder=0.15)
    diff_widths: dict[int, float] = {}
    height_match = re.search(r"\d+", row.get("CellHeight", ""))
    cell_pitch_nm = int(height_match[0]) / 2 if height_match else 66
    poly_width = _preview_width_nm(row, "PreviewPlyWidthNm", 12) / 44
    tcn_width = _preview_width_nm(row, "PreviewTcnWidthNm", 12) / 44
    vcr_height = _preview_width_nm(row, "PreviewVcrWidthNm", 12) / cell_pitch_nm
    if "PXL / TRM" in visible:
        _draw_trm_layer(ax, row, g)
    trace = trace_nets(row, g)
    active_nets = {"LO", "HI", "BRIDGE", "SHORT"}

    for seg in segments(row.get("DiffPatternUnit", "") or row.get("DiffPatternGlobal", "")):
        parts = [p.strip() for p in strip_shift(seg).split(",")]
        if len(parts) < 2:
            continue
        polarity = parts[3].lower()[:1] if len(parts) > 3 else "n"
        layer_name = "PDIFF" if polarity == "p" else "NDIFF"
        if layer_name not in visible:
            continue
        xs = [x for x in expand_axis(parts[0], ogd) if 0 <= x <= ogd]
        cells = [c for c in expand_axis(parts[1], ncell) if 0 <= c < ncell]
        color = C_PDIFF if polarity == "p" else C_NDIFF
        if not xs:
            xs = list(range(ogd + 1))
        x0, x1 = max(-0.5, min(xs) - 0.5), min(ogd + 0.5, max(xs) + 0.5)
        try:
            width_nm = float(parts[2]) * 1000
        except (IndexError, ValueError):
            width_nm = cell_pitch_nm * 0.6
        band_height = max(0.03, width_nm / cell_pitch_nm)
        for c in cells:
            diff_widths[c] = width_nm
            ax.add_patch(Rectangle((x0, c + 0.5 - band_height / 2), x1 - x0, band_height,
                                   facecolor=color, edgecolor="none", zorder=1))
            if "VT flavor" in visible and len(parts) > 3:
                _draw_flavor(ax, row, parts[3], x0, x1, c + 0.5 - band_height / 2, band_height)

    if "FTI" in visible:
        fti_skew = _skew_geometry(row, "FTICdMisregSkew")
        fti_columns = [(c, cells) for c, cells in g.fti_cells.items()]
        if g.fti_cells.get(0):
            fti_columns.append((ogd, g.fti_cells[0]))
        for c, cells in fti_columns:
            for start, end in _contiguous_runs(cells):
                center_x, center_y = float(c), (start + end + 1) / 2
                width, height = 0.28, end - start + 1
                if fti_skew:
                    _nominal_outline(ax, center_x, center_y, width, height)
                center_x, center_y, width, height = _apply_skew_geometry(
                    center_x, center_y, width, height, fti_skew, 44, cell_pitch_nm
                )
                ax.add_patch(Rectangle((center_x - width / 2, center_y - height / 2), width, height,
                                       facecolor=C_FTI, edgecolor="none", zorder=3))

    tcn_skew = _skew_geometry(row, "TCNCdRegSkew")
    if "PLY" in visible:
        ply_skewed = bool(_skew_geometry(row, "PolyCdSkew"))
        # Column ogd is column 0 of the next unit; draw it so the right edge is complete.
        for c in range(ogd + 1):
            for a, b in plug_spans(g.poly_plugs.get(c % ogd, set()), ncell):
                if ply_skewed:
                    _nominal_outline(ax, c, (a + b + 1) / 2, poly_width, b - a + 0.9)
                center_x, center_y, width, height = _preview_ply_geometry(row, c, a, b, poly_width)
                ax.add_patch(Rectangle((center_x - width / 2, center_y - height / 2), width, height,
                                       facecolor=C_POLY, alpha=0.28, edgecolor="none", zorder=2))
        for node, label in trace.net.items():
            if node[0] == "ply" and label in active_nets:
                _kind, c, a, b = node
                center_x, center_y, width, height = _preview_ply_geometry(row, c, a, b, poly_width)
                y0, y1 = _clip_to_fti(center_y - height / 2, center_y + height / 2,
                                      _fti_intervals(row, g, c, poly_width, cell_pitch_nm))
                if y1 > y0:
                    ax.add_patch(Rectangle((center_x - width / 2, y0), width, y1 - y0,
                                           facecolor=C_POLY_ACTIVE, alpha=0.62, edgecolor="none", zorder=3.7))
    if "TCN" in visible:
        for s in range(ogd):
            for a, b in g.tcn_spans(s):
                center_x, center_y = s + 0.5, (a + b + 1) / 2
                width, height = tcn_width, b - a + 0.8
                if tcn_skew:
                    _nominal_outline(ax, center_x, center_y, width, height)
                center_x, center_y, width, height = _apply_skew_geometry(
                    center_x, center_y, width, height, tcn_skew, 44, cell_pitch_nm
                )
                ax.add_patch(Rectangle((center_x - width / 2, center_y - height / 2), width, height,
                                       facecolor=C_TCN, alpha=0.28, edgecolor="none", zorder=2))
        for node, label in sorted(trace.net.items()):
            if node[0] != "tcn" or label not in active_nets:
                continue
            _kind, s, a, b = node
            center_x, center_y = s + 0.5, (a + b + 1) / 2
            width, height = tcn_width, b - a + 0.8
            center_x, center_y, width, height = _apply_skew_geometry(
                center_x, center_y, width, height, tcn_skew, 44, cell_pitch_nm
            )
            ax.add_patch(Rectangle((center_x - width / 2, center_y - height / 2), width, height,
                                   facecolor=C_TCN_ACTIVE, alpha=0.62, edgecolor="none", zorder=3.6))
    if "TCP" in visible:
        # TCP fills the TCN tip-to-tip gap at boundary V of a TCN column (Y values are boundaries, like plugs).
        for segment in segments(row.get("TCPSkewPatternUnit", "")):
            parts = [part.strip() for part in strip_shift(segment).split(",")]
            if len(parts) < 2:
                continue
            ogd_cd, pgd_cd, ogd_reg, pgd_reg = _resize_shift_nm(segment)
            width = max(0.02, tcn_width + ogd_cd / 44)
            height = max(0.02, 0.2 + pgd_cd / cell_pitch_nm)
            for slot in {col % ogd for col in expand_axis(parts[0], ogd - 1)}:
                spans = g.tcn_spans(slot)
                for boundary in expand_axis(parts[1], ncell):
                    if not 0 < boundary < ncell:
                        continue
                    if span_containing(spans, boundary - 1) == span_containing(spans, boundary):
                        continue
                    center_x = slot + 0.5 + ogd_reg / 44
                    center_y = boundary + pgd_reg / cell_pitch_nm
                    ax.add_patch(Rectangle((center_x - width / 2, center_y - height / 2), width, height,
                                           facecolor=C_TCP, edgecolor=C_TCP_EDGE, linewidth=0.5,
                                           alpha=0.85, zorder=4.05))

    if "VG" in visible:
        for c, tracks in g.vg_cols.items():
            for t in tracks:
                x0, y0, x1, y1 = _vcx_site_rect(row, g, "vg", c, t, float(c), _via_track_y(g, t, "vg"))
                ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, gid="VG",
                                       facecolor=NET_COLOR[trace.label(("vg", c, t))],
                                       edgecolor="k", linewidth=0.6, zorder=6))
    if "VT" in visible:
        for s, tracks in g.vt_cols.items():
            for t in tracks:
                x0, y0, x1, y1 = _vcx_site_rect(row, g, "vt", s, t, s + 0.5, _via_track_y(g, t, "vt"))
                ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, gid="VT",
                                       facecolor=NET_COLOR[trace.label(("vt", s, t))],
                                       edgecolor="k", linewidth=0.6, zorder=6))
    if "Current flow" in visible:
        _draw_current_flow(ax, row, g, trace, poly_width, tcn_width, tcn_skew, cell_pitch_nm, vcr_height)

    if "VCR" in visible:
        for segment in segments(row.get("VcrPatternUnit", "")):
            parts = [part.strip() for part in strip_shift(segment).split(",")]
            if len(parts) < 2:
                continue
            skew = _pattern_skew_nm(row, segment)
            xs = [x for x in expand_axis(parts[0], ogd) if -0.5 <= x <= ogd + 0.5]
            tracks = [track for track in expand_axis(parts[1], g.pgd) if 0 <= track <= g.pgd]
            if xs:
                left = max(-0.5, min(xs) - 0.5)
                right = min(ogd + 0.5, max(xs) + 0.5)
                for track in tracks:
                    center_x, center_y, width, height = _apply_skew_geometry(
                        (left + right) / 2, 2 * track, right - left, vcr_height,
                        skew, 44, cell_pitch_nm
                    )
                    ax.add_patch(Rectangle((center_x - width / 2, center_y - height / 2), width, height,
                                           facecolor=C_VCR, edgecolor=C_VCR_EDGE, linewidth=0.7,
                                           alpha=0.8, zorder=5))
                nub_rule_violations = vcrnub_rule_issues(row, ("VcrnubPatternUnit",))
                nub_style = ({"facecolor": "#ffd6d6", "edgecolor": "#d00000", "linewidth": 1.5}
                         if nub_rule_violations else
                         {"facecolor": C_VCRNUB, "edgecolor": C_VCR_EDGE, "linewidth": 0.8})
        for segment in segments(row.get("VcrnubPatternUnit", "")):
            parts = [part.strip() for part in strip_shift(segment).split(",")]
            if len(parts) < 2:
                continue
            xs = [x for x in expand_axis(parts[0], ogd - 1) if 0 <= x < ogd]
            tracks = [track for track in expand_axis(parts[1], g.pgd) if 0 <= track <= g.pgd]
            for x in xs:
                for track in tracks:
                    for x0, y0, x1, y1 in _vcrnub_rects(row, segment, x, 2 * track, vcr_height, cell_pitch_nm):
                        ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, zorder=5.2, **nub_style))

    if "M0 LO1" in visible:
        for track in sorted(g.lo_tracks):
            y = _m0_track_y(row, track)
            ax.plot([0, ogd], [y, y], color=C_LO, linestyle="--", linewidth=1.4, alpha=0.9, zorder=2.5)
    if "M0 HI1" in visible:
        for track in sorted(g.hi_tracks):
            y = _m0_track_y(row, track)
            ax.plot([0, ogd], [y, y], color=C_HI, linestyle="--", linewidth=1.4, alpha=0.9, zorder=2.5)
    if "V0 landings" in visible:
        for tracks, data_field, default_side, color in (
            (g.lo_tracks, "LO1Data", "L", C_LO), (g.hi_tracks, "HI1Data", "R", C_HI)
        ):
            side = _terminal_side(row, data_field, default_side)
            x = -0.48 if side == "L" else ogd + 0.48
            for track in sorted(tracks):
                y = _m0_track_y(row, track)
                ax.add_patch(Rectangle((x - 0.24, y - 0.11), 0.48, 0.22, facecolor="white",
                                       edgecolor=color, linewidth=2.0, zorder=9))

    if "V0 dummy" in visible and row.get("DummyViaPattern"):
        library = global_frame(row, g, g.arr_ogd, g.arr_pgd)
        per_height = m0_tracks_per_height(row)
        # The pattern is global (library frame); show the part that lands on the first unit cell.
        for xg, track in dummy_via_points(row, library["width"], per_height * library["cells"] // 2):
            x, t = xg - library["x0"], track - per_height * library["B"]
            if -0.5 <= x <= ogd + 0.5 and 0 <= t <= per_height * g.pgd:
                _draw_v0_dummy(ax, x, t * 2 / per_height)

    misc_names = {name for name, _parts in _misc_segments(row)}
    for layer_name in visible:
        if layer_name.startswith("TSDR region:"):
            _draw_tsdr_region(ax, row, g, layer_name.partition(":")[2])
            continue
        if ":TSDR" in layer_name:
            continue
        if layer_name.startswith("Skew target:"):
            target = layer_name.partition(":")[2].strip()
            if target in misc_names:
                _draw_misc_layer(ax, row, g, target)
        elif layer_name in misc_names:
            _draw_misc_layer(ax, row, g, layer_name)
    return diff_widths


# ---------------------------------------------------------------------------
# Family-specific path overlays
# ---------------------------------------------------------------------------
def _harrow(ax, x0: float, x1: float, y: float) -> None:
    ax.annotate("", (x1, y), (x0, y), zorder=7,
                arrowprops=dict(arrowstyle="<->", color=C_PATH, lw=0.7, mutation_scale=7,
                                shrinkA=0, shrinkB=0))


def _varrow(ax, x: float, y0: float, y1: float) -> None:
    ax.annotate("", (x, y1), (x, y0), zorder=7,
                arrowprops=dict(arrowstyle="<->", color=C_PATH, lw=0.7, mutation_scale=7,
                                shrinkA=0, shrinkB=0))


def overlay_ctg(ax, g: Geometry, res: Result) -> str:
    sites, _ = ctg_sites(g)
    for c, _ga, cell, slots in sites:
        for slot in slots:
            # slot c-1 of column 0 wraps to the previous unit; draw it at the left edge
            x_slot = slot + 0.5 if not (slot == g.ogd - 1 and c == 0) else -0.5
            _harrow(ax, c, x_slot, cell + 0.5)
        ax.plot([c], [cell + 0.5], marker="o", ms=5, color=C_PATH, zorder=8)
    txt = (f"magenta dots = fail modes in this unit (active VG gate x diff track with an active VT-TCN beside it): "
           f"{len(sites)}; arrows show the active TCN side(s), counted once per gate/track")
    return txt


def overlay_ete(ax, g: Geometry, res: Result) -> str:
    kind = "vg" if res.family == "GATEGATE" else "vt"
    cols = g.vg_cols if kind == "vg" else g.vt_cols
    xoff = 0.0 if kind == "vg" else 0.5
    declared = g.lo_tracks | g.hi_tracks
    n = 0
    fallback = False
    for c in sorted(cols or g.poly_plug_cols or (set(range(g.ogd)) - g.fti_cols)):
        tracks = cols.get(c, set()) if cols else set()
        pairs = _ete_pairs(g, kind, c, tracks) if tracks else []
        if not pairs:
            pairs = _ete_pairs(g, kind, c, declared)
            fallback = fallback or bool(pairs)
        for a, b in pairs:
            _varrow(ax, c + xoff + 0.3, a.end + 0.9, b.start + 0.1)
            n += 1
    txt = f"magenta arrows = counted LO<->HI tip-to-tip paths in this unit: {n}"
    if fallback:
        txt += "  (vias removed on this variant: SDR-equivalent pairs from LO1/HI1ActiveTracks)"
    return txt


def overlay_diffogd(ax, g: Geometry, res: Result) -> str:
    n = 0
    for cols, cells in g.fti_segs:
        cut = [c for c in cells if c in g.device_cells] if g.device_cells else list(cells)
        for c in cols:
            if c % g.ogd == 0 or not 0 <= c < g.ogd:
                continue
            for k in cut:
                _harrow(ax, c - 0.5, c + 0.5, k + 0.5)
                n += 1
    return f"magenta arrows = counted diff->FTI->diff leakage paths in this unit: {n}"


def overlay_diffchn(ax, g: Geometry, res: Result) -> str:
    runs = _stacked_runs(g)
    idx = 0
    for run in runs:
        for c in run:
            idx += 1
        if len(run) > 1:
            ax.plot([run[0] - 0.3, run[-1] + 0.3], [g.n_cells + 0.4, g.n_cells + 0.4],
                    color=C_PATH, lw=1.2)
    return (f"numbers = active VG gates (transistors in series) counted in the chain: {idx}; "
            f"grey diamonds = VT on M0 jumper tracks (not LO1/HI1)")


OVERLAYS = {"CTG": overlay_ctg, "GATEGATE": overlay_ete, "EPIEPI": overlay_ete,
            "TCNTCN": overlay_ete, "DIFFOGD": overlay_diffogd, "DIFFCHN": overlay_diffchn,
            "VIA_CHAIN": lambda ax, g, res: ""}


# ---------------------------------------------------------------------------
# Global (full-structure) view
# ---------------------------------------------------------------------------
def global_frame(row: dict[str, str], g: Geometry, units_ogd: int = 1, units_pgd: int = 1) -> dict[str, int]:
    """Global frame with ``units_ogd x units_pgd`` unit cells: X = poly tracks 0..width, Y = diff tracks 0..cells-1."""
    buf = {side: max(0, int(row.get(f"BufferSize{side}", "0") or 0)) for side in "LRBT"}
    return {
        **buf,
        "width": buf["L"] + units_ogd * g.ogd + buf["R"],
        "cells": 2 * (buf["B"] + units_pgd * g.pgd + buf["T"]),
        "x0": buf["L"],
        "y0": 2 * buf["B"],
    }


def _outside_rect(x0: float, x1: float, y0: float, y1: float,
                  unit: tuple[float, float, float, float]) -> list[tuple[float, float, float, float]]:
    """Pieces of a rectangle that lie outside the unit-cell region (global inputs only apply there)."""
    ux0, ux1, uy0, uy1 = unit
    if x1 <= ux0 or x0 >= ux1 or y1 <= uy0 or y0 >= uy1:
        return [(x0, x1, y0, y1)]
    pieces = []
    if y0 < uy0:
        pieces.append((x0, x1, y0, uy0))
    if y1 > uy1:
        pieces.append((x0, x1, uy1, y1))
    mid0, mid1 = max(y0, uy0), min(y1, uy1)
    if x0 < ux0:
        pieces.append((x0, ux0, mid0, mid1))
    if x1 > ux1:
        pieces.append((ux1, x1, mid0, mid1))
    return [piece for piece in pieces if piece[1] > piece[0] and piece[3] > piece[2]]


def _shift_new_artists(ax, before: set, dx: float, dy: float) -> None:
    from matplotlib.collections import Collection
    from matplotlib.patches import Patch as _Patch
    from matplotlib.text import Text
    from matplotlib.transforms import Affine2D

    shifted = Affine2D().translate(dx, dy) + ax.transData
    for artist in ax.get_children():
        if artist in before:
            continue
        if isinstance(artist, Collection):
            artist.set_offset_transform(shifted)
        elif isinstance(artist, (_Patch, Line2D, Text)):
            artist.set_transform(shifted)


def render_global_snapshot(row: dict[str, str], out_path: Path, dpi: int = 110,
                           visible_layers: set[str] | None = None, units_ogd: int = 1, units_pgd: int = 1,
                           coords: str = "both", routing: bool = True, buffer: bool = True,
                           scale: int = 1, panel: bool = False) -> None:
    """Buffer ring around ``units_ogd x units_pgd`` unit cells (default one).

    Unit cells are drawn from the ``*Unit`` fields. ``*Global`` fields use global coordinates (``g``
    prefix) and overwrite the unit cell where they define a shape; the default buffer (poly, TCN)
    fills only the area outside the unit cells. ``rK`` counts from the end of the shown frame.
    """
    g = geometry_from_row(row)
    unit_row = row
    if not buffer:
        # Tiled unit cells only: no buffer ring and no global inputs.
        row = {key: "" if key.endswith("Global") else value for key, value in row.items()}
        row.update({f"BufferSize{side}": "0" for side in "LRBT"})
    ogd, ncell = g.ogd, g.n_cells
    units_ogd, units_pgd = max(1, units_ogd), max(1, units_pgd)
    frame = global_frame(row, g, units_ogd, units_pgd)
    width, cells, x_origin, y_origin = frame["width"], frame["cells"], frame["x0"], frame["y0"]
    unit_rect = (x_origin, x_origin + units_ogd * ogd, y_origin, y_origin + units_pgd * ncell)
    visible = set(LAYOUT_LAYERS) if visible_layers is None else set(visible_layers)
    if not routing:
        visible.discard("V0 landings")
    per_height = m0_tracks_per_height(row)
    m0_count = per_height * cells // 2
    height_match = re.search(r"\d+", row.get("CellHeight", ""))
    cell_pitch_nm = int(height_match[0]) / 2 if height_match else 66
    fig_w = min(44.0, max(10.5, 0.30 * width + 5.0))
    fig_h = max(4.0, min(12.0, (fig_w - 3.5) * (cells + 2) / (width + 2) + 1.2))
    fig = plt.figure(figsize=(fig_w, fig_h))
    ax = fig.add_axes((0.05, 0.06, 0.76, 0.88))
    legend_ax = fig.add_axes((0.86, 0.06, 0.13, 0.88))
    legend_ax.axis("off")
    C_BUFFER = "#e3efd3"

    def add_outside(x0: float, x1: float, y0: float, y1: float, **style) -> None:
        for a, b, c, d in _outside_rect(x0, x1, y0, y1, unit_rect):
            ax.add_patch(Rectangle((a, c), b - a, d - c, **style))

    add_outside(-0.5, width + 0.5, 0, cells, facecolor=C_BUFFER, edgecolor="none", zorder=0)

    # Unit cells: the normal unit-cell renderer, shifted into place.
    unit_visible = visible - {"Fail paths", "V0 landings", "V0 dummy", "Grid", "M0 Y grid", "Guide tracks", "M0 LO1",
                              "M0 HI1"}
    for u in range(units_ogd):
        for v in range(units_pgd):
            before = set(ax.get_children())
            draw_base(ax, unit_row, g, unit_visible)
            _shift_new_artists(ax, before, x_origin + u * ogd, y_origin + v * ncell)

    # Global (buffer) inputs.
    if visible & {"NDIFF", "PDIFF"}:
        for segment in segments(row.get("DiffPatternGlobal", "")):
            parts = [part.strip() for part in strip_shift(segment).split(",")]
            if len(parts) < 2:
                continue
            polarity = parts[3].lower()[:1] if len(parts) > 3 else "n"
            if ("PDIFF" if polarity == "p" else "NDIFF") not in visible:
                continue
            try:
                width_nm = float(parts[2]) * 1000
            except (IndexError, ValueError):
                width_nm = cell_pitch_nm * 0.6
            if width_nm <= 0:
                continue
            band = max(0.03, width_nm / cell_pitch_nm)
            for cell in expand_axis(parts[1], cells - 1):
                for a, b in _contiguous_runs(set(expand_axis(parts[0], width))):
                    ux0, ux1, uy0, uy1 = unit_rect
                    mx0, mx1 = max(a - 0.5, ux0), min(b + 0.5, ux1)
                    if mx1 > mx0 and uy0 <= cell < uy1:
                        ax.add_patch(Rectangle((mx0, cell), mx1 - mx0, 1, facecolor="white", edgecolor="none",
                                               zorder=1.05))
                    ax.add_patch(Rectangle((a - 0.5, cell + 0.5 - band / 2), b - a + 1, band,
                                           facecolor=C_PDIFF if polarity == "p" else C_NDIFF, edgecolor="none",
                                           zorder=1.1))
    if "PLY" in visible:
        plugs: dict[int, set[int]] = {}
        for segment in segments(row.get("PolyPlugsGlobal", "")):
            parts = [part.strip() for part in strip_shift(segment).split(",")]
            if len(parts) >= 2:
                for col in expand_axis(parts[0], width):
                    plugs.setdefault(col, set()).update(expand_axis("/".join(parts[1:]), cells - 1))
        for col in range(width + 1):
            if col in plugs:
                for a, b in plug_spans(plugs[col], cells):
                    ax.add_patch(Rectangle((col - 0.14, a + 0.05), 0.28, b - a + 0.9, facecolor=C_POLY, alpha=0.35,
                                           edgecolor="none", zorder=2.1))
            else:
                for a, b in plug_spans(set(), cells):
                    add_outside(col - 0.14, col + 0.14, a + 0.05, b + 0.95, facecolor=C_POLY, alpha=0.25,
                                edgecolor="none", zorder=2)
    if "FTI" in visible:
        for segment in segments(row.get("FTITracksGlobal", "")):
            parts = [part.strip() for part in strip_shift(segment).split(",")]
            fti_cells = expand_axis(parts[1], cells - 1) if len(parts) > 1 else list(range(cells))
            for col in expand_axis(parts[0], width):
                for a, b in _contiguous_runs({c for c in fti_cells if 0 <= c < cells}):
                    ax.add_patch(Rectangle((col - 0.14, a), 0.28, b - a + 1, facecolor=C_FTI, edgecolor="none",
                                           zorder=3.1))
    if "TCN" in visible:
        tcn_width = _preview_width_nm(row, "PreviewTcnWidthNm", 12) / 44
        tcn_plugs: dict[int, set[int]] = {}
        for segment in segments(row.get("TcnPlugsGlobal", "")):
            parts = [part.strip() for part in strip_shift(segment).split(",")]
            if len(parts) >= 2:
                for slot in expand_axis(parts[0], width - 1):
                    tcn_plugs.setdefault(slot, set()).update(expand_axis("/".join(parts[1:]), cells - 1))
        depop: set[tuple[int, int]] = set()
        for segment in segments(row.get("TcnDepopGlobal", "")):
            parts = [part.strip() for part in strip_shift(segment).split(",")]
            if len(parts) >= 2:
                depop.update((s, c) for s in expand_axis(parts[0], width - 1) for c in expand_axis(parts[1], cells - 1))
        for slot in range(width):
            explicit = slot in tcn_plugs
            for a, b in plug_spans(tcn_plugs.get(slot, set()), cells):
                for c0, c1 in _contiguous_runs({c for c in range(a, b + 1) if (slot, c) not in depop}):
                    if explicit:
                        ax.add_patch(Rectangle((slot + 0.5 - tcn_width / 2, c0 + 0.1), tcn_width, c1 - c0 + 0.8,
                                               facecolor=C_TCN, alpha=0.45, edgecolor="none", zorder=2.1))
                    else:
                        add_outside(slot + 0.5 - tcn_width / 2, slot + 0.5 + tcn_width / 2, c0 + 0.1, c1 + 0.9,
                                    facecolor=C_TCN, alpha=0.3, edgecolor="none", zorder=2)
    if "VCR" in visible:
        vcr_height = _preview_width_nm(row, "PreviewVcrWidthNm", 12) / cell_pitch_nm
        # Same axes as the unit fields: X = poly columns (bar) / TCN slots (nub), Y = CRL (cell-height boundary).
        for segment in segments(row.get("VcrPatternGlobal", "")):
            parts = [part.strip() for part in strip_shift(segment).split(",")]
            xs = [x for x in expand_axis(parts[0], width) if -0.5 <= x <= width + 0.5] if len(parts) > 1 else []
            for track in (expand_axis(parts[1], cells // 2) if xs else []):
                ax.add_patch(Rectangle((min(xs) - 0.5, 2 * track - vcr_height / 2), max(xs) - min(xs) + 1,
                                       vcr_height, facecolor=C_VCR, edgecolor=C_VCR_EDGE, linewidth=0.7, alpha=0.8,
                                       zorder=5.1))
            nub_rule_violations = vcrnub_rule_issues(row, ("VcrnubPatternGlobal",))
            nub_style = ({"facecolor": "#ffd6d6", "edgecolor": "#d00000", "linewidth": 1.5}
                     if nub_rule_violations else
                     {"facecolor": C_VCRNUB, "edgecolor": C_VCR_EDGE, "linewidth": 0.8})
        for segment in segments(row.get("VcrnubPatternGlobal", "")):
            parts = [part.strip() for part in strip_shift(segment).split(",")]
            if len(parts) < 2:
                continue
            for x in (x for x in expand_axis(parts[0], width - 1) if 0 <= x < width):
                for track in expand_axis(parts[1], cells // 2):
                    for x0, y0, x1, y1 in _vcrnub_rects(row, segment, x, 2 * track, vcr_height, cell_pitch_nm):
                        ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, zorder=6.1, **nub_style))

    global_cuts: dict[int, list[float]] = {}
    for segment in segments(row.get("M0CutPatternGlobal", "")):
        parts = [part.strip() for part in strip_shift(segment).split(",")]
        if len(parts) >= 2:
            for track in expand_axis("/".join(parts[1:]), m0_count):
                global_cuts.setdefault(track, []).extend(x / 2 for x in expand_axis(parts[0], 2 * width))
    if "M0 lines" in visible:
        half_gap = M0_CUT_WIDTH_NM / 44 / 2
        for track in range(m0_count + 1):
            y = track * 2 / per_height
            rail = track % per_height == 0
            bounds = [-0.5, *sorted(x for x in global_cuts.get(track, []) if 0 <= x <= width), width + 0.5]
            for a, b in zip(bounds, bounds[1:]):
                x0, x1 = a + (half_gap if a > -0.5 else 0), b - (half_gap if b < width + 0.5 else 0)
                for c0, c1, _y0, _y1 in _outside_rect(x0, x1, y - 0.01, y + 0.01, unit_rect):
                    ax.plot([c0, c1], [y, y], color=C_RAIL if rail else "#3b4650",
                            linewidth=1.1 if rail else 0.5, alpha=0.85, zorder=3.2)

    for segment in segments(row.get("VcxPatternGlobal", "")):
        tokens = [token.strip() for token in strip_shift(segment).split(",")]
        if len(tokens) < 3 or tokens[0].lower() not in ("vg", "vt"):
            continue
        kind = tokens[0].lower()
        if kind.upper() not in visible:
            continue
        for pos in expand_axis(tokens[1], width - 1 if kind == "vt" else width):
            for track in expand_axis(tokens[2], m0_count):
                center_x = pos + (0.5 if kind == "vt" else 0.0)
                x0, y0, x1, y1 = _vcx_site_rect(row, g, kind, pos, track, center_x,
                                                track * 2 / per_height, global_scope=True,
                                                global_width=width, global_cells=cells)
                add_outside(x0, x1, y0, y1, facecolor=C_OTHER, edgecolor="k", linewidth=0.5, zorder=6.1)

    # LO1/HI1 tracks are unit M0 coordinates; shown on the global M0 track of every unit row, routed into the
    # castle where V0 lands on M1 jumpers per LO1Data/HI1Data.
    unit_m0 = per_height * frame["B"]
    castle_extent = 0
    for tracks, field, default, color, layer, name in ((g.lo_tracks, "LO1Data", "L", C_LO, "M0 LO1", "LO1"),
                                                       (g.hi_tracks, "HI1Data", "R", C_HI, "M0 HI1", "HI1")):
        term = terminal_data(row, field, default)
        n = term["jumpers"]
        jumper_x = ([-2.5 - (n - 1) + k for k in range(n)] if term["side"] == "L"
                    else [width + 2.5 + k for k in range(n)])
        castle_extent = max(castle_extent, n + 3)
        if "V0 landings" in visible and tracks:
            for x in jumper_x:
                ax.plot([x, x], [-0.2, cells + 0.2], color="#9aa3ab", linewidth=2.2, alpha=0.6, zorder=0.5)
            pin_y = -0.3 if term["pin"] != "T" else cells + 0.3
            ax.text(sum(jumper_x) / n, pin_y, f"Pin {name} ({term['to'].upper()})", ha="center",
                    va="top" if term["pin"] != "T" else "bottom", fontsize=7, color=color,
                    bbox=dict(facecolor="white", edgecolor=color, pad=1.5), zorder=9)
        for v in range(units_pgd):
            cols = term_columns(term, v)
            for track in sorted(tracks):
                global_track = track + unit_m0 + v * per_height * g.pgd
                y = global_track * 2 / per_height
                if layer in visible:
                    ax.plot([-0.5, width + 0.5], [y, y], color=color, linestyle="--", linewidth=1.3, zorder=3.3)
                if "V0 landings" in visible:
                    edge = -0.5 if term["side"] == "L" else width + 0.5
                    ax.plot([min(jumper_x[0], edge), max(jumper_x[-1], edge)], [y, y], color=color, linewidth=1.2,
                            zorder=8.5)
                    for k in cols:
                        ax.add_patch(Rectangle((jumper_x[k] - 0.24, y - 0.11), 0.48, 0.22, facecolor="white",
                                               edgecolor=color, linewidth=1.8, zorder=9))
                    ax.text(jumper_x[-1] + 0.6 if term["side"] == "L" else jumper_x[0] - 0.6, y + 0.12,
                            coord_label(track, global_track, coords), ha="left" if term["side"] == "L" else "right",
                            va="bottom", fontsize=6.5, color=color, zorder=9)

    if "V0 dummy" in visible:
        for x, track in dummy_via_points(row, width, m0_count):
            _draw_v0_dummy(ax, x, track * 2 / per_height)

    misc_names: list[str] = []
    for segment in segments(row.get("DrawMiscIDsGlobal", "")):
        parts = [part.strip() for part in strip_shift(segment).split(",")]
        if len(parts) < 4:
            continue
        xs, ys = expand_axis(parts[2], width), expand_axis(parts[3], cells - 1)
        if not xs or not ys:
            continue
        color = tsdr_color(parts[0])
        misc_names.append(f"{parts[0]}:{parts[1]}")
        ax.add_patch(Rectangle((min(xs) - 0.5, min(ys)), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1,
                               facecolor=color, alpha=0.12, edgecolor=color, linestyle="--", linewidth=1.0, zorder=5))

    for u in range(units_ogd):
        for v in range(units_pgd):
            x0, y0 = x_origin + u * ogd, y_origin + v * ncell
            ax.add_patch(Rectangle((x0, y0), ogd, ncell, facecolor="none", edgecolor="#1f4e79",
                                   linestyle="--", linewidth=1.3, zorder=8))
            ax.text(x0 + ogd / 2, y0 + ncell - 0.15, f"Unit cell {u + 1}.{v + 1}", ha="center", va="top",
                    fontsize=8, color="#1f4e79", zorder=9,
                    bbox=dict(facecolor="white", alpha=0.7, edgecolor="none", pad=1))
    ax.add_patch(Rectangle((-0.5, 0), width + 1, cells, facecolor="none", edgecolor="#5d7a3a", linewidth=1.4, zorder=8))
    ax.text(width / 2, cells + 0.2,
            (f"Buffer L{frame['L']} R{frame['R']} B{frame['B']} T{frame['T']} around {units_ogd} x {units_pgd} "
             f"unit cell(s) (library array {g.arr_ogd} x {g.arr_pgd}); g = global coordinates; global inputs overwrite the unit cell")
            if buffer else f"{units_ogd} x {units_pgd} unit cells (library array {g.arr_ogd} x {g.arr_pgd}); no buffer",
            ha="center", va="bottom", fontsize=8, color="#34495e")
    ax.set_xlim(-1.5 - (castle_extent if "V0 landings" in visible else 0),
                width + 1.5 + (castle_extent if "V0 landings" in visible else 0))
    ax.set_ylim(-0.4, cells + 1.0)
    x_ticks = list(range(0, width + 1, max(1 if coords != "both" else 2, width // 40)))
    ax.set_xticks(x_ticks, [coord_label(x - x_origin, x, coords) for x in x_ticks])
    ax.set_yticks([k + 0.5 for k in range(cells)], [coord_label(k - y_origin, k, coords) for k in range(cells)],
                  fontsize=8)
    ax.tick_params(axis="x", labelsize=8)
    fig._iso_ruler = {"x_unit0": x_origin, "x_glob0": 0, "y_unit0": y_origin, "y_glob0": 0, "m0_unit0": unit_m0,
                      "m0_glob0": 0, "per_height": per_height, "coords": coords,
                      "x_max": width, "cells": cells, "m0_max": m0_count}
    if panel:
        ax.tick_params(labelbottom=False, labelleft=False)
    if "M0 lines" in visible and not panel:
        m0_axis = ax.secondary_yaxis("right")
        active = {t + unit_m0 + v * per_height * g.pgd for v in range(units_pgd) for t in g.lo_tracks | g.hi_tracks}
        shown = [t for t in range(m0_count + 1)
                 if m0_count <= 30 or t % per_height == 0 or t in active]
        m0_axis.set_yticks([t * 2 / per_height for t in shown],
                           [coord_label(t - unit_m0, t, coords) + (f"  CRL{t // per_height}" if t % per_height == 0
                                                                    else "") for t in shown])
        m0_axis.tick_params(axis="y", colors="#59636b", labelsize=7, length=2, pad=2)
        for label, t in zip(m0_axis.get_yticklabels(), shown):
            if t % per_height == 0:
                label.set_color(C_RAIL)
                label.set_fontweight("bold")
    ax.set_aspect("equal", adjustable="box")
    legend = [Patch(facecolor=C_BUFFER, edgecolor="#5d7a3a", label="buffer (global inputs)")] if buffer else []
    legend += [Line2D([], [], color="#1f4e79", ls="--", label="unit cell (unit inputs)"),
              Patch(facecolor=C_NDIFF, label="NDIFF"), Patch(facecolor=C_PDIFF, label="PDIFF"),
              Patch(facecolor=C_FTI, label="FTI"), Patch(facecolor=C_POLY, alpha=0.3, label="PLY"),
              Patch(facecolor=C_TCN, alpha=0.3, label="TCN"),
              Line2D([], [], color=C_RAIL, lw=1.1, label="M0 rail / CRL")]
    if "M0 lines" not in visible:
        legend.pop()
    if "M0 LO1" in visible:
        legend.append(Line2D([], [], color=C_LO, ls="--", label="LO1 active track (M0)"))
    if "M0 HI1" in visible:
        legend.append(Line2D([], [], color=C_HI, ls="--", label="HI1 active track (M0)"))
    if "V0 dummy" in visible and row.get("DummyViaPattern"):
        legend.append(Line2D([], [], marker="s", ls="", color="white", markeredgecolor=C_V0_DUMMY,
                             markeredgewidth=1.5, label="V0 dummy (DummyViaPattern)"))
    if "VCR" in visible and row.get("VcrnubPatternGlobal") and vcrnub_rule_issues(row):
        legend.append(Patch(facecolor="#ffd6d6", edgecolor="#d00000", linewidth=1.5,
                            label="VCRNUB rule violation"))
    legend += [Patch(facecolor=tsdr_color(layer.partition(':')[2]), alpha=0.25, hatch="////",
                     edgecolor=tsdr_color(layer.partition(':')[2]), label=f"TSDR region {layer.partition(':')[2]}")
               for layer in visible if layer.startswith("TSDR region:")]
    legend += [Patch(facecolor=tsdr_color(name), alpha=0.3, label=name)
               for name in misc_names]
    legend_ax.legend(handles=legend, loc="upper left", fontsize=8, frameon=False, borderaxespad=0)
    if panel:
        fig._iso_legend = (legend_ax, legend)
    _save_figure(fig, ax, out_path, dpi, True, row, "global", scale)
    plt.close(fig)


# ---------------------------------------------------------------------------
# GDS export (KLayout)
# ---------------------------------------------------------------------------
# (name, layer, datatype, colour). Mask layers use the chassis numbers; FTI/TCP/VCR and overlays use 1000+.
GDS_LAYERS = (
    ("NDIFF", 1, 0, C_NDIFF), ("POLY", 2, 0, C_POLY), ("TCN", 5, 0, C_TCN), ("PDIFF", 8, 0, C_PDIFF),
    ("VT", 31, 0, "#7f7f7f"), ("VG", 32, 0, "#4d4d4d"), ("V0", 56, 0, "#ffffff"),
    ("M0 CLR1", 241, 0, C_M0), ("M0 CLR2", 120, 0, C_M0_ALT), ("TRM", 509, 0, C_TRM),
    ("FTI", 1001, 0, C_FTI), ("TCP", 1002, 0, C_TCP), ("VCR", 1003, 0, C_VCR), ("VCRNUB", 1004, 0, C_VCRNUB),
    ("M0 rail (CRL)", 1005, 0, C_RAIL), ("M0 cut", 1006, 0, C_M0_CUT),
    ("POLY active net", 2, 1, C_POLY_ACTIVE), ("TCN active net", 5, 1, C_TCN_ACTIVE),
    ("LO1 net / track", 1100, 0, C_LO), ("HI1 net / track", 1101, 0, C_HI), ("M0 jumper net", 1102, 0, C_BRIDGE),
    ("LO-HI short", 1103, 0, C_SHORT), ("Nominal (before skew)", 1200, 0, "#000000"),
    ("TSDR region", 1300, 0, C_MISC), ("Misc / TSDR ID box", 1301, 0, "#cc79a7"),
    ("Unit cell / frame", 1400, 0, "#1f4e79"), ("Buffer region", 1401, 0, "#e3efd3"), ("Other", 1999, 0, "#999999"),
    *((f"VT {label}", 1500 + index, 0, "#555555") for index, (_id, label, _h) in enumerate(VT_FLAVORS.values())),
    ("VT flavor != name ID", 1599, 0, "#e00000"),
)
_GDS_BY_NAME = {name: (layer, datatype) for name, layer, datatype, _color in GDS_LAYERS}
_GDS_BY_COLOR = {color.lower(): name for name, _l, _d, color in GDS_LAYERS
                 if name not in ("VT", "VG", "V0", "Nominal (before skew)", "Unit cell / frame", "Buffer region",
                                 "Other", "VT flavor != name ID") and not name.startswith("VT ")}


def _gds_real8(value: float) -> bytes:
    """GDSII 8-byte excess-64 base-16 real."""
    if value == 0:
        return bytes(8)
    sign = 0x80 if value < 0 else 0
    value = abs(value)
    exponent = 64
    while value >= 1:
        value /= 16
        exponent += 1
    while value < 1 / 16:
        value *= 16
        exponent -= 1
    mantissa = int(round(value * (1 << 56)))
    if mantissa >= 1 << 56:
        mantissa >>= 4
        exponent += 1
    return bytes([sign | exponent]) + mantissa.to_bytes(7, "big")


def _gds_record(kind: int, dtype: int, payload: bytes = b"") -> bytes:
    import struct

    if len(payload) % 2:
        payload += b"\0"
    return struct.pack(">HBB", 4 + len(payload), kind, dtype) + payload


def _figure_shapes(ax, nm_per_unit: tuple[float, float]) -> list[tuple[str, str, list[tuple[float, float]], float]]:
    """(layer name, 'poly'|'path', points in nm, path width nm) for every drawn shape, classified by colour."""
    from matplotlib.collections import Collection
    from matplotlib.colors import to_hex

    to_data = ax.transData.inverted()
    sx, sy = nm_per_unit
    shapes = []

    def nm(points) -> list[tuple[float, float]]:
        return [(float(x) * sx, float(y) * sy) for x, y in points]

    for artist in ax.get_children():
        if not artist.get_visible():
            continue
        if isinstance(artist, Rectangle):
            corners = to_data.transform(artist.get_transform().transform(artist.get_path().vertices))[:4]
            face = artist.get_facecolor()
            face_hex = to_hex(face).lower()
            edge = to_hex(artist.get_edgecolor()).lower()
            dashed = artist.get_linestyle() not in ("-", "solid")
            gid = artist.get_gid() or ""
            if gid.startswith("VTFLAV:"):
                flavor = gid.partition(":")[2]
                name = ("VT flavor != name ID" if flavor == "mismatch" else
                        f"VT {VT_FLAVORS[flavor][1]}" if flavor in VT_FLAVORS else "Other")
            elif artist.get_hatch():
                name = "TSDR region"
            elif dashed:
                name = ("Nominal (before skew)" if edge == "#000000" else
                        "Unit cell / frame" if edge == "#1f4e79" else "Misc / TSDR ID box")
            elif face[3] == 0:
                name = _GDS_BY_COLOR.get(edge, "Unit cell / frame")
            elif face_hex == "#ffffff":
                name = "V0" if edge in (C_LO, C_HI, C_V0_DUMMY) else ""
            elif face_hex == "#e3efd3":
                name = "Buffer region"
            else:
                name = _GDS_BY_COLOR.get(face_hex, "Other")
            if name:
                shapes.append((name, "poly", nm(corners), 0.0))
        elif isinstance(artist, Line2D):
            color = to_hex(artist.get_color()).lower()
            name = _GDS_BY_COLOR.get(color)
            points = to_data.transform(artist.get_transform().transform(artist.get_xydata()))
            if name and len(points) >= 2 and name not in ("Nominal (before skew)",):
                width_nm = {"M0 CLR1": 15.0, "M0 CLR2": 15.0, "M0 rail (CRL)": 15.0, "M0 cut": 2.0}.get(name, 6.0)
                shapes.append((name, "path", nm(points), width_nm))
        elif isinstance(artist, Collection) and artist.get_gid() in ("VG", "VT"):
            offsets = to_data.transform(artist.get_offset_transform().transform(artist.get_offsets()))
            half = 7.0
            for x, y in nm(offsets):
                shapes.append((artist.get_gid(), "poly",
                               [(x - half, y - half), (x + half, y - half), (x + half, y + half), (x - half, y + half)],
                               0.0))
    return shapes


@lru_cache(maxsize=1)
def _generator_corpus():
    generator_dir = Path(__file__).resolve().parent / "extracted"
    if str(generator_dir) not in sys.path:
        sys.path.insert(0, str(generator_dir))
    import claude_iso_gds_generator as generator

    training = generator.load_csv(generator_dir / "ISO_Mini_GDS.csv")
    decoder_rows = {r["LayoutFiles"]: r for r in training if r.get("LayoutFiles")}
    corpus = generator.load_corpus(generator_dir / "ISO-GDSNamed", decoder_rows.values())
    layer_map = generator.load_layer_map(generator_dir / "svrf_layer_mapping.csv")
    return generator, decoder_rows, corpus, layer_map


def generator_decoder_row(row: dict[str, str]) -> dict[str, str]:
    """Buildsheet row -> the generator's decoder row (CELLHEIGHT, DEVWIDTH, MG, patterns from the *Unit fields)."""
    unit = [[p.strip() for p in strip_shift(s).split(",")] for s in segments(row.get("DiffPatternUnit", ""))]
    unit = [p for p in unit if len(p) > 3]
    try:
        devwidth = str(round(float(unit[0][2]) * 1000)) if unit else ""
    except ValueError:
        devwidth = ""
    flavor = re.sub(r"^[np]", "", unit[0][3].lower()) if unit else ""
    mos = re.match(r"[A-Z_]+\d{3}-(NP|N|P)", row.get("StructureName", ""))
    return {
        "StructureName": row.get("StructureName", ""), "MOS": mos[1] if mos else "",
        "MG": VT_FLAVORS.get(flavor, ("", flavor.upper(), ""))[1],
        "CELLHEIGHT": re.sub(r"\D", "", row.get("CellHeight", "")), "DEVWIDTH": devwidth,
        **{k: row.get(k, "") for k in ("UnitCellSizeOgd", "UnitCellSizePgd", "ArraySizeOgd", "ArraySizePgd")},
        "FTIPattern": row.get("FTITracksUnit", ""), "PolyPlugs": row.get("PolyPlugsUnit", ""),
        "TCNPlugs": row.get("TcnPlugsUnit", ""), "VcxPattern": row.get("VcxPatternUnit", ""),
        "M0CutPattern": row.get("M0CutPatternUnit", ""),
    }


def generator_gds(row: dict[str, str], out_dir: Path) -> tuple[Path, str]:
    """Full-array GDS (GDS2 text, KLayout-readable) from claude_iso_gds_generator plus a named ``.lyp``."""
    import contextlib
    import io as _io

    height = int(re.sub(r"\D", "", row.get("CellHeight", "")) or 0)
    if height != 132:
        raise ValueError(f"Generator chassis is CTG132-only; refusing approximate CH{height} GDS. "
                         "Select 'Preview drawing' or provide a matching chassis.")
    generator, decoder_rows, corpus, layer_map = _generator_corpus()
    target = generator_decoder_row(row)
    chassis, _file = generator._pick_chassis(target, corpus, decoder_rows)
    with contextlib.redirect_stderr(_io.StringIO()):
        path = generator.generate_one(target, row, corpus, decoder_rows, layer_map, out_dir)
    colors = {(1, 0): C_NDIFF, (8, 0): C_PDIFF, (2, 0): C_POLY, (5, 0): C_TCN, (184, 0): C_FTI, (31, 0): "#7f7f7f",
              (32, 0): "#4d4d4d", (56, 0): C_V0_DUMMY, (4, 0): "#00838f", (120, 0): C_M0_ALT, (241, 0): C_M0,
              (509, 0): C_TRM}
    used = sorted({(int(a), int(b)) for a, b in re.findall(r"LAYER (\d+)\s*\n\s*DATATYPE (\d+)", path.read_text())})
    entries = []
    for key in used:
        info = layer_map.get(key)
        color = colors.get(key, "#9e9e9e")
        name = f"{info.layer_name if info else 'L'} {key[0]}/{key[1]}"
        entries.append(f" <properties><frame-color>{color}</frame-color><fill-color>{color}</fill-color>"
                       f"<dither-pattern>{'I5' if key in colors else 'I1'}</dither-pattern><valid>true</valid>"
                       f"<visible>{'true' if key in colors else 'false'}</visible><width>1</width>"
                       f"<name>{name}</name><source>{key[0]}/{key[1]}@1</source></properties>\n")
    path.with_suffix(".lyp").write_text('<?xml version="1.0" encoding="utf-8"?>\n<layer-properties>\n'
                                        + "".join(entries) + "</layer-properties>\n", encoding="utf-8")
    return path, chassis


def write_gds(ax, out_path: Path, row: dict[str, str], cell_name: str) -> None:
    """Write the drawn layout as GDSII (database unit 0.1 nm) plus a KLayout ``.lyp`` with names and colours."""
    import struct
    import time

    height_match = re.search(r"\d+", row.get("CellHeight", ""))
    nm_per_unit = (44.0, int(height_match[0]) / 2 if height_match else 66.0)
    shapes = _figure_shapes(ax, nm_per_unit)
    now = time.localtime()[:6]
    stamp = struct.pack(">12h", *now, *now)
    name = re.sub(r"[^A-Za-z0-9_$]", "_", cell_name)[:120] or "ISO"
    data = [_gds_record(0x00, 0x02, struct.pack(">h", 600)), _gds_record(0x01, 0x02, stamp),
            _gds_record(0x02, 0x06, b"ISO_PREVIEW"), _gds_record(0x03, 0x05, _gds_real8(1e-4) + _gds_real8(1e-10)),
            _gds_record(0x05, 0x02, stamp), _gds_record(0x06, 0x06, name.encode())]
    for layer_name, kind, points, width_nm in shapes:
        layer, datatype = _GDS_BY_NAME[layer_name]
        xy = [(round(x * 10), round(y * 10)) for x, y in points]
        if kind == "poly":
            if len(xy) < 3:
                continue
            xy = xy + [xy[0]]
            head = _gds_record(0x08, 0x00)
        else:
            head = _gds_record(0x09, 0x00)
        body = [head, _gds_record(0x0D, 0x02, struct.pack(">h", layer)),
                _gds_record(0x0E, 0x02, struct.pack(">h", datatype))]
        if kind == "path":
            body.append(_gds_record(0x0F, 0x03, struct.pack(">i", max(1, round(width_nm * 10)))))
        body += [_gds_record(0x10, 0x03, b"".join(struct.pack(">ii", x, y) for x, y in xy)), _gds_record(0x11, 0x00)]
        data += body
    data += [_gds_record(0x07, 0x00), _gds_record(0x04, 0x00)]
    Path(out_path).write_bytes(b"".join(data))

    used = {layer_name for layer_name, *_rest in shapes}
    entries = []
    for layer_name, layer, datatype, color in GDS_LAYERS:
        if layer_name not in used:
            continue
        outline = layer_name in ("Nominal (before skew)", "Misc / TSDR ID box", "Unit cell / frame", "V0",
                                 "Buffer region")
        frame = "#000000" if color.lower() == "#ffffff" else color
        entries.append(
            "  <properties>\n"
            f"   <frame-color>{frame}</frame-color><fill-color>{color}</fill-color>\n"
            "   <frame-brightness>0</frame-brightness><fill-brightness>0</fill-brightness>\n"
            f"   <dither-pattern>{'I1' if outline else 'I0' if layer_name in ('VG', 'VT', 'FTI') else 'I5'}</dither-pattern>\n"
            "   <valid>true</valid><visible>true</visible><transparent>false</transparent><width>1</width>\n"
            "   <marked>false</marked><animation>0</animation>\n"
            f"   <name>{layer_name} {layer}/{datatype}</name><source>{layer}/{datatype}@1</source>\n"
            "  </properties>\n")
    Path(out_path).with_suffix(".lyp").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<layer-properties>\n' + "".join(entries) + "</layer-properties>\n",
        encoding="utf-8")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def _snap_coordinates(ax) -> tuple[list[float], list[float]]:
    """Data-unit X/Y of drawn layer edges, M0/LO/HI track centres and via centres (not grids, overlays or labels)."""
    import numpy as np
    from matplotlib.collections import Collection
    from matplotlib.colors import to_hex

    track_colors = {C_M0, C_M0_ALT, C_RAIL, C_LO, C_HI, C_M0_CUT}
    to_data = ax.transData.inverted()
    (x_lo, x_hi), (y_lo, y_hi) = sorted(ax.get_xlim()), sorted(ax.get_ylim())
    chunks = []
    for artist in ax.get_children():
        if not artist.get_visible():
            continue
        try:
            if isinstance(artist, Rectangle):
                gid = artist.get_gid() or ""
                overlay = (artist.get_hatch() or artist.get_linestyle() not in ("-", "solid")
                           or gid.startswith("VTFLAV") or (artist.get_facecolor()[3] == 0 and not gid))
                if overlay:
                    continue
                corners = to_data.transform(artist.get_transform().transform(artist.get_path().vertices))
                chunks.append(corners)
                chunks.append(((corners.min(axis=0) + corners.max(axis=0)) / 2)[None, :])
            elif isinstance(artist, Line2D) and to_hex(artist.get_color()).lower() in track_colors:
                chunks.append(to_data.transform(artist.get_transform().transform(artist.get_xydata())))
            elif isinstance(artist, Collection) and artist.get_gid() in ("VG", "VT"):
                chunks.append(to_data.transform(artist.get_offset_transform().transform(artist.get_offsets())))
        except (ValueError, TypeError, AttributeError):
            continue
    if not chunks:
        return [], []
    points = np.concatenate([chunk for chunk in chunks if len(chunk)])
    points = points[np.isfinite(points).all(axis=1)]
    xs = np.unique(np.round(points[:, 0], 4))
    ys = np.unique(np.round(points[:, 1], 4))
    return ([float(x) for x in xs if x_lo <= x <= x_hi], [float(y) for y in ys if y_lo <= y <= y_hi])


def _save_figure(fig, ax, out_path: Path, dpi: int, tight: bool, row: dict[str, str], frame: str,
                 scale: int = 1) -> None:
    """Save the PNG plus ``<name>.json``: data (poly pitch x diffusion cell) -> image pixel calibration.

    ``scale`` enlarges the figure (not the DPI), so shapes grow while lines and text keep their size.
    """
    import json

    if Path(out_path).suffix.lower() == ".gds":
        write_gds(ax, Path(out_path), row, row.get("StructureName", "ISO"))
        return
    pad = 0.08
    legend_info = getattr(fig, "_iso_legend", None)
    if legend_info:
        legend_ax, handles = legend_info
        legend_ax.set_visible(False)
        legend_path = Path(out_path).with_suffix(".legend.png")
        if handles and not legend_path.exists():
            legend_fig = plt.figure(figsize=(3.4, 0.24 * len(handles) + 0.3))
            legend_fig.legend(handles=handles, loc="upper left", fontsize=8, frameon=False, handlelength=1.4)
            legend_fig.savefig(legend_path, dpi=110, bbox_inches="tight", pad_inches=0.05)
            plt.close(legend_fig)
    if scale > 1:
        width, height = fig.get_size_inches()
        fig.set_size_inches(width * scale, height * scale)
    fig.set_dpi(dpi)
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    bbox = fig.get_tightbbox(renderer).padded(pad) if tight else fig.bbox_inches
    fig.savefig(out_path, dpi=dpi, **({"bbox_inches": "tight", "pad_inches": pad} if tight else {}))

    def to_image(x: float, y: float) -> tuple[float, float]:
        disp_x, disp_y = ax.transData.transform((x, y))
        return disp_x - bbox.x0 * dpi, bbox.y1 * dpi - disp_y

    (ox, oy), (ux, _uy), (_vx, vy) = to_image(0, 0), to_image(1, 0), to_image(0, 1)
    x_limits, y_limits = ax.get_xlim(), ax.get_ylim()
    ax0, ay1 = to_image(x_limits[0], y_limits[0])
    ax1, ay0 = to_image(x_limits[1], y_limits[1])
    height_match = re.search(r"\d+", row.get("CellHeight", ""))
    snap_x, snap_y = _snap_coordinates(ax)
    calibration = {
        "origin_px": [ox, oy], "px_per_unit": [ux - ox, oy - vy],
        "nm_per_unit": [44.0, (int(height_match[0]) / 2) if height_match else 66.0],
        "axes_px": [ax0, ay0, ax1, ay1], "frame": frame, "scale": scale,
        "snap_x": snap_x, "snap_y": snap_y, "ruler": getattr(fig, "_iso_ruler", None),
        "xlim": list(x_limits), "ylim": list(y_limits),
    }
    Path(out_path).with_suffix(".json").write_text(json.dumps(calibration), encoding="utf-8")


def coord_label(unit_value: int, global_value: int, coords: str) -> str:
    """Axis label in unit coordinates, global coordinates (``g`` prefix) or both."""
    if coords == "global":
        return f"g{global_value}"
    if coords == "both":
        return f"{unit_value}|g{global_value}"
    return str(unit_value)


def render_snapshot(row: dict[str, str], res: Result, out_path: Path, subtitle: str = "",
                    dpi: int = 140, preview: bool = False,
                    visible_layers: set[str] | None = None, coords: str = "unit", scale: int = 1,
                    panel: bool = False) -> None:
    """``panel``: GUI mode - axis labels go to the GUI rulers and the legend to a separate PNG."""
    g = geometry_from_row(row)
    visible = set(layout_layers(row)) if visible_layers is None else visible_layers
    ogd, ncell = g.ogd, g.n_cells
    frame = global_frame(row, g)
    dx, dy, dm = frame["x0"], frame["y0"], m0_tracks_per_height(row) * frame["B"]
    fig_width = max(10.5, 0.42 * ogd + 5.0)
    fig_height = max(3.8, min(7.2, fig_width * (ncell + 1) / (ogd + 1))) if preview else 7.2
    fig = plt.figure(figsize=(fig_width, fig_height))
    ax = fig.add_axes((0.06, 0.07, 0.71, 0.88))
    legend_ax = fig.add_axes((0.88, 0.07, 0.11, 0.88))
    legend_ax.axis("off")
    draw_base(ax, row, g, visible)
    trace = trace_nets(row, g)
    if trace.warnings:
        fig.text(0.06, 0.985, "Buildsheet check: " + "; ".join(trace.warnings), color="#b03a2e",
                 fontsize=8, va="top")
    if "Fail paths" in visible:
        OVERLAYS[res.family](ax, g, res)

    ax.set_xlim(-0.8, ogd + 0.8)
    ax.set_ylim(-0.6, ncell + 0.8)
    x_ticks = list(range(0, ogd + 1, 1 if ogd <= 30 and coords != "both" else 2))
    ax.set_xticks(x_ticks, [coord_label(x, x + dx, coords) for x in x_ticks])
    ax.set_yticks([k + 0.5 for k in range(ncell)])
    ax.set_yticklabels([coord_label(k, k + dy, coords) for k in range(ncell)], fontsize=9)
    per_height = m0_tracks_per_height(row)
    fig._iso_ruler = {"x_unit0": 0, "x_glob0": -dx, "y_unit0": 0, "y_glob0": -dy, "m0_unit0": 0,
                      "m0_glob0": -dm, "per_height": per_height, "coords": coords,
                      "x_max": ogd, "cells": ncell, "m0_max": per_height * g.pgd}
    if panel:
        ax.tick_params(labelbottom=False, labelleft=False)
    if "VCR" in visible and row.get("VcrPatternUnit") and not panel:
        vcr_axis = ax.secondary_yaxis("right")
        vcr_axis.set_yticks([2 * track for track in range(g.pgd + 1)],
                           [str(track) for track in range(g.pgd + 1)])
        vcr_axis.tick_params(axis="y", colors="#9a4d10", labelsize=9)
    if visible & {"M0 lines", "M0 cuts", "M0 Y grid"} and not panel:
        tracks = _m0_tracks(row, g)
        if tracks:
            m0_axis = ax.secondary_yaxis(1.09 if "VCR" in visible and row.get("VcrPatternUnit") else "right")
            m0_axis.set_yticks([y for y, _layer, _index, _color in tracks],
                               [coord_label(index, index + dm, coords)
                                + (f"  CRL{index // m0_tracks_per_height(row)}" if layer == "M0 rail" else "")
                                for _y, layer, index, _color in tracks])
            m0_axis.tick_params(axis="y", colors="#59636b", labelsize=7, length=2, pad=2)
            for label, (_y, layer, _index, _color) in zip(m0_axis.get_yticklabels(), tracks):
                if layer == "M0 rail":
                    label.set_color(C_RAIL)
                    label.set_fontweight("bold")
    ax.tick_params(axis="x", labelsize=9)
    if "Grid" in visible:
        ax.grid(True, axis="both", color="#b8bec4", linewidth=0.6, alpha=0.85)
    else:
        ax.grid(False)
    ax.set_aspect("equal", adjustable="box")

    legend = []
    if "NDIFF" in visible:
        legend.append(Patch(facecolor=C_NDIFF, label="NDIFF"))
    if "PDIFF" in visible:
        legend.append(Patch(facecolor=C_PDIFF, label="PDIFF"))
    if "VT flavor" in visible:
        name_id = name_flavor_id(row.get("StructureName", ""))
        for flavor in sorted({flavor for _polarity, flavor in diff_flavors(row)}):
            flavor_id, label, hatch = VT_FLAVORS.get(flavor, ("?", flavor.upper(), "oo"))
            legend.append(Patch(facecolor="white", hatch=hatch, edgecolor="#555555",
                                label=f"{label} (flavor ID {flavor_id})"))
        if flavor_alignment(row):
            legend.append(Patch(facecolor="none", edgecolor="#e00000", linewidth=1.4,
                                label=f"VT flavor != name ID {name_id}"))
    if "FTI" in visible:
        legend.append(Patch(facecolor=C_FTI, label="FTI"))
    if "PLY" in visible:
        legend.append(Patch(facecolor=C_POLY, alpha=0.28,
                            label=f"PLY ({_preview_width_nm(row, 'PreviewPlyWidthNm', 12):g} nm)"))
        if g.vg_cols:
            legend.append(Patch(facecolor=C_POLY_ACTIVE, alpha=0.62, label="PLY active (traced net)"))
    if "TCN" in visible:
        legend.append(Patch(facecolor=C_TCN, alpha=0.28,
                            label=f"TCN ({_preview_width_nm(row, 'PreviewTcnWidthNm', 12):g} nm)"))
        if g.vt_cols:
            legend.append(Patch(facecolor=C_TCN_ACTIVE, alpha=0.62, label="TCN active (traced net)"))
    if "TCP" in visible and row.get("TCPSkewPatternUnit"):
        legend.append(Patch(facecolor=C_TCP, edgecolor=C_TCP_EDGE, alpha=0.85, label="TCP (TCN-TCN gap)"))
    if "VG" in visible:
        legend.append(Line2D([], [], marker="s", ls="", color=C_OTHER, markeredgecolor="k", label="VG active"))
    if "VT" in visible:
        legend.append(Line2D([], [], marker="D", ls="", color=C_OTHER, markeredgecolor="k", label="VT active"))
    if "VCR" in visible and (row.get("VcrPatternUnit") or row.get("VcrnubPatternUnit")):
        legend.append(Patch(facecolor=C_VCR, edgecolor=C_VCR_EDGE,
                            label=f"VCR ({_preview_width_nm(row, 'PreviewVcrWidthNm', 12):g} nm)"))
        if row.get("VcrnubPatternUnit"):
            legend.append(Patch(facecolor=C_VCRNUB, edgecolor=C_VCR_EDGE, label="VCRNUB"))
            if vcrnub_rule_issues(row):
                legend.append(Patch(facecolor="#ffd6d6", edgecolor="#d00000", linewidth=1.5,
                                    label="VCRNUB rule violation"))
    if "M0 lines" in visible:
        cell_height = re.search(r"\d+", row.get("CellHeight", ""))
        height_nm = int(cell_height[0]) if cell_height else 132
        width_nm = _preview_width_nm(row, "PreviewM0WidthNm", M0_WIDTH_NM.get(height_nm, 15))
        legend.append(Line2D([], [], color=C_M0, lw=0.65, label=f"M0 line ({width_nm:g} nm)"))
        legend.append(Line2D([], [], color=C_RAIL, lw=1.2,
                             label=f"M0 rail / CRL (every {m0_tracks_per_height(row)} tracks, VCR site)"))
    if "M0 Y grid" in visible:
        legend.append(Line2D([], [], color=C_M0_GRID, lw=0.8, ls=(0, (4, 3)), label="M0 Y grid (M0 track centres)"))
    if "M0 LO1" in visible:
        legend.append(Line2D([], [], color=C_LO, ls="--", label="LO1 active track (M0)"))
    if "M0 HI1" in visible:
        legend.append(Line2D([], [], color=C_HI, ls="--", label="HI1 active track (M0)"))
    if "M0 cuts" in visible:
        legend.append(Line2D([], [], color="white", marker="|", markeredgecolor=C_M0_CUT,
                             markeredgewidth=1.5, label="M0 cut gap"))
    if "V0 landings" in visible:
        for field, default, color, name in (("LO1Data", "L", C_LO, "LO1"), ("HI1Data", "R", C_HI, "HI1")):
            term = terminal_data(row, field, default)
            legend.append(Line2D([], [], marker="s", ls="", color="white", markeredgecolor=color, markeredgewidth=1.8,
                                 label=f"{name} V0 {term['side']}: {len(term_columns(term, 0))}/{term['jumpers']} "
                                       f"jumpers, every {term['stride']}"))
    if "PXL / TRM" in visible:
        legend.append(Patch(facecolor=C_TRM, alpha=0.38, label="TRM / misc"))
    if "V0 dummy" in visible and row.get("DummyViaPattern"):
        legend.append(Line2D([], [], marker="s", ls="", color="white", markeredgecolor=C_V0_DUMMY,
                             markeredgewidth=1.5, label="V0 dummy (DummyViaPattern)"))
    applied = [f"{name} {direction} {variant} {delta:+g} nm"
               for field, name in (("FTICdMisregSkew", "FTI"), ("TCNCdRegSkew", "TCN"), ("PolyCdSkew", "PLY"),
                                   ("GLKSkew", "GLK"))
               for direction, variant, delta in _skew_geometry(row, field)]
    if applied:
        legend.append(Patch(facecolor="none", edgecolor="#000000", linestyle=(0, (2, 2)),
                            label="nominal (before skew)\nskews: " + "\n".join(applied)))
    misc_names = {name for name, _parts in _misc_segments(row)}
    for layer in visible:
        if layer.startswith("Skew target:"):
            target = layer.partition(":")[2].strip()
            if target in misc_names:
                legend.append(Patch(facecolor=tsdr_color(target), alpha=0.22, label=target))
        elif layer in misc_names and not ":TSDR" in layer:
            legend.append(Patch(facecolor=tsdr_color(layer), alpha=0.22, label=layer))
        elif layer.startswith("TSDR region:"):
            legend.append(Patch(facecolor=tsdr_color(layer.partition(':')[2]), alpha=0.25, hatch="////",
                                edgecolor=tsdr_color(layer.partition(':')[2]),
                                label=f"TSDR region {layer.partition(':')[2]}"))
    if "Fail paths" in visible:
        legend.append(Line2D([], [], color=C_PATH, lw=1.6, label="counted LKG path"))
    if "Current flow" in visible:
        labels = set(trace.net.values())
        for label, text in (("LO", "LO1 net"), ("HI", "HI1 net"), ("BRIDGE", "M0 jumper net"),
                            ("SHORT", "LO1-HI1 short")):
            if label in labels:
                legend.append(Line2D([], [], color=NET_COLOR[label], lw=4, alpha=0.6, label=text))
        if trace.channels:
            legend.append(Line2D([], [], color=C_BRIDGE, lw=2.4, label="conducting channel"))
    if legend:
        legend_ax.legend(handles=legend, loc="upper left", bbox_to_anchor=(0, 1), fontsize=8, frameon=False,
                         handlelength=1.3, borderaxespad=0)
    if panel:
        fig._iso_legend = (legend_ax, legend)
    _save_figure(fig, ax, out_path, dpi, preview, row, "unit", scale)
    plt.close(fig)


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")


def render_snapshots(picked: dict[str, tuple[dict[str, str], Result, str]], out_dir: Path) -> dict[str, Path]:
    """``picked``: key -> (row, result, subtitle). Writes ``<StructureName>.png`` per key."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for key, (row, res, subtitle) in picked.items():
        path = out_dir / f"{safe_name(row.get('StructureName') or key)}.png"
        render_snapshot(row, res, path, subtitle)
        written[key] = path
    return written


if __name__ == "__main__":
    # This module is a library; running it directly opens the GUI.
    from iso_ai_gui import App

    App().mainloop()
