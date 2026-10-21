"""Layout previews for Atlantis templates outside the ISO buildsheet DSL.

Parameter meanings follow the Atlantis template definitions (``GetTemplateParameters`` help text):

* ``CTM_GNAC_X81Z`` (GNAC) and ``FE_ECD_X81Z`` (gate / poly KLV) use ISO-style unit-cell patterns; their rows are
  mapped onto the ISO fields (``normalize_row``) and drawn by the ISO renderer.
* ``VIA_CHN_X81Z`` (via / poly / TCN chains): ``OgdLay``/``PgdLay`` lines (``LineNum`` = ogd;pgd), vias at
  ``ViaUnitPattern`` ``pgdLine,ogdLine`` in chain order, VCR/VCRNUB for TCN-VCR chains.
* ``BE_ECD_X81Z`` (metal ECD): ``AmeLegoPattern`` line stack, active line ``AmeLineNum``, ``EcdLen`` µm, force/sense
  vias at ``ForceViaPattern``/``SenseViaPattern``.
* ``VIA_KLV_X81Z`` (via Kelvin): ogd/pgd line grids, active via ``ActViaPattern``, dummy vias, iLO/iHI/vLO/vHI pins.

Widths come from the CD/space pairs in the structure name (``..._M0C/M1_12/9_24/20_24X12_...``) or the preview
defaults; they are previews, not signoff geometry. Units match the ISO preview (x: 44 nm, y: CH/2 nm).
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from ScalarCalculation import expand_axis

ACTIVE, PASSIVE = 0.85, 0.22
LAYER_COLOR = {"poly": "#d62728", "tcn": "#1f77b4", "m0": "#3b4650", "m1": "#0088ff", "m2": "#10d7d6",
               "m3": "#8c6bb1", "vcr": "#ff7f0e"}
VIA_COLOR = {"v0": "#3949ab", "vt": "#7f7f7f", "vg": "#4d4d4d", "v1": "#00aaff", "v2": "#e377c2"}
PIN_COLOR = {"iLO": "#17becf", "iHI": "#d6006f", "vLO": "#2ca02c", "vHI": "#9467bd", "FORCE": "#d6006f",
             "SENSE": "#17becf"}

TEMPLATE_LAYERS = {
    "VIA_CHN": ("OGD lines", "PGD lines", "Vias", "Chain path", "VCR / VCRNUB", "Grid"),
    "BE_ECD": ("Active line", "Dummy lines", "Force / sense vias", "Route stubs", "Full length", "Grid"),
    "VIA_KLV": ("OGD lines", "PGD lines", "Diffusion / TCN", "PGD cuts", "Active via", "Dummy vias", "Pins",
                "Grid"),
    "ROSPFT": ("Cell context", "Diffusion", "Pin nets", "Vias", "M1", "Dummy V0", "Labels", "Grid"),
}
TEMPLATE_LAYERS_OFF = frozenset({"Full length", "Grid"})
TEMPLATE_TITLES = {"VIA_CHN": "VIA_CHN_X81Z via / line chain", "BE_ECD": "BE_ECD_X81Z metal ECD",
                   "VIA_KLV": "VIA_KLV_X81Z via Kelvin", "ROSPFT": "CTM_PFT_X81Z ring-oscillator PFT (ROSPFT)"}
ROS_PIN_COLOR = {"GATE": "#7f00ff", "SS": "#17becf", "SF": "#1f77b4", "DS": "#e377c2", "DF": "#d6006f",
                 "DV": "#9e9e9e"}


def _f(row: dict[str, str], name: str) -> str:
    key = re.sub(r"[^a-z0-9]", "", name.casefold())
    return next((str(value).strip() for field, value in row.items()
                 if value not in (None, "") and re.sub(r"[^a-z0-9]", "", str(field).casefold()) == key), "")


def _height(row: dict[str, str]) -> int:
    match = re.search(r"\d+", _f(row, "CellHeight")) or re.search(r"(?<=[A-Z])(110|132|165)(?=-)",
                                                                  row.get("StructureName", ""))
    return int(match[0]) if match and int(match[0]) in (110, 132, 165) else 132


def template_kind(row: dict[str, str]) -> str:
    if _f(row, "Connections") and _f(row, "cellName") and _f(row, "ReferenceTrackOgd"):
        return "ROSPFT"
    if _f(row, "LineNum") and (_f(row, "ViaUnitPattern") or _f(row, "VcrUnitPattern") or _f(row, "VcrnubUnitPattern")):
        return "VIA_CHN"
    if _f(row, "viaklvType") or (_f(row, "ActViaPattern") and _f(row, "ogdLay")):
        return "VIA_KLV"
    if _f(row, "AmeLegoPattern") or (_f(row, "AmeLayer").lower().startswith("m") and _f(row, "EcdLen")):
        return "BE_ECD"
    return ""


# ---------------------------------------------------------------------------------------------- ISO-DSL mapping
def normalize_row(row: dict[str, str]) -> dict[str, str]:
    """Fill ISO unit-cell fields for GNAC (CTM_GNAC) and gate-KLV (FE_ECD) rows; existing values win."""
    out = dict(row)

    def fill(field: str, value: str) -> None:
        if value and not str(out.get(field) or "").strip():
            out[field] = value

    if _f(row, "NumTcnTracksUnitCell") and _f(row, "NumDiffPairsUnitCell"):
        fill("UnitCellSizeOgd", _f(row, "NumTcnTracksUnitCell"))
        fill("UnitCellSizePgd", _f(row, "NumDiffPairsUnitCell"))
        array = re.fullmatch(r"\s*(\d+)\s*[xX]\s*(\d+)\s*", _f(row, "Array2D"))
        if array:
            fill("ArraySizeOgd", array[1])
            fill("ArraySizePgd", array[2])
        fill("PolyPitch", "pp44")
    elif _f(row, "AmeLayer").lower() in ("poly", "tcn") and _f(row, "DiffPattern"):
        height = _height(row)
        layer = _f(row, "AmeLayer").lower()
        via = "vg" if layer == "poly" else "vt"
        try:
            length_nm = float(_f(row, "EcdLen")) * 1000
        except ValueError:
            length_nm = 4 * height
        cells = max(2, 2 * math.ceil(length_nm / height))
        line = next((int(part.split(",")[1]) for part in _f(row, "VcxPatternUnit").split(";")
                     if part.strip().lower().startswith(f"{via},") and part.split(",")[1].strip().isdigit()), 5)
        # TCN slot s sits between poly s and s+1: an odd unit width keeps it centred like the gate line.
        fill("UnitCellSizeOgd", str(2 * line + (layer == "tcn")))
        fill("UnitCellSizePgd", str(cells // 2))
        fill("ArraySizeOgd", _f(row, "ArrayNum") or "1")
        fill("ArraySizePgd", "1")
        fill("DiffPatternUnit", _f(row, "DiffPattern"))
        fill("FTITracksUnit", _f(row, "FTIPattern"))
        fill("PolyPlugsUnit" if layer == "poly" else "TcnPlugsUnit", f"{line},1:{cells - 1}")
        fill("CellHeight", f"ch{height}")
        fill("PolyPitch", "pp44")
    return out


# ---------------------------------------------------------------------------------------------- dimensions
def _name_lego(row: dict[str, str]) -> dict[str, tuple[float, float]]:
    """``V0_VCHN_M0C/M1_12/9_24/20`` -> {"m0": (12, 21), "m1": (24, 44)} (CD, pitch in nm)."""
    match = re.search(r"_([A-Z]+\d?[A-Z]?(?:/[A-Z]+\d?[A-Z]?)+)_((?:\d+(?:p\d+)?/\d+(?:p\d+)?_?)+)",
                      row.get("StructureName", ""), re.IGNORECASE)
    if not match:
        return {}
    names = [_layer_key(token) for token in match[1].split("/")]
    pairs = re.findall(r"(\d+(?:p\d+)?)/(\d+(?:p\d+)?)", match[2])
    out = {}
    for name, (cd, space) in zip(names, pairs):
        cd_nm, space_nm = (float(value.replace("p", ".")) for value in (cd, space))
        out[name] = (cd_nm, cd_nm + space_nm)
    return out


def _layer_key(token: str) -> str:
    token = token.strip().lower()
    if token.startswith("pol"):
        return "poly"
    match = re.match(r"(m\d|tcn|vcr)", token)
    return match[1] if match else token


def _lego(row: dict[str, str], layer: str) -> tuple[float, float]:
    """(CD, pitch) nm of a line layer: structure-name values first, then preview defaults."""
    layer = _layer_key(layer)
    named = _name_lego(row)
    if layer in named:
        return named[layer]
    height = _height(row)
    if layer in ("poly", "tcn"):
        return 12.0, 44.0
    if layer == "m0":
        return {110: 12.0, 132: 15.0, 165: 14.0}[height], height / (6 if height == 165 else 5)
    if layer == "m1":
        return 24.0, 44.0
    if layer == "m2":
        return 13.0, 24.0
    return 20.0, 44.0


def _via_size(row: dict[str, str], default: tuple[float, float]) -> tuple[float, float]:
    match = re.search(r"_(\d+(?:p\d+)?)[xX](\d+(?:p\d+)?)_", row.get("StructureName", "") + "_")
    if match:
        return float(match[1].replace("p", ".")), float(match[2].replace("p", "."))
    return default


def _pairs(spec: str, x_extent: int, y_extent: int) -> list[tuple[int, int, str]]:
    """``x,y;x2,y2`` (ranges allowed, ``|``/``/`` size suffixes ignored) -> [(x, y, raw segment)]."""
    out = []
    for segment in (spec or "").split(";"):
        core = segment.split("|")[0].strip()
        parts = [part.split("/")[0].strip() if part.count("/") == 1 and "." in part else part.strip()
                 for part in core.split(",")]
        if len(parts) < 2:
            continue
        for x in expand_axis(parts[0], x_extent):
            for y in expand_axis(parts[1], y_extent):
                out.append((x, y, segment.strip()))
    return out


# ---------------------------------------------------------------------------------------------- drawing helpers
class _Canvas:
    def __init__(self, row: dict[str, str], visible: set[str]) -> None:
        self.row, self.visible = row, visible
        self.cell_nm = _height(row) / 2
        self.shapes: list[tuple] = []
        self.legend: dict[str, dict] = {}
        self.labels: list[tuple[float, float, str, str]] = []

    def text(self, x: float, y: float, text: str, color: str) -> None:
        self.labels.append((x, y, text, color))

    def rect(self, x0: float, y0: float, x1: float, y1: float, legend: str, color: str, alpha: float,
             edge: str = "none", width: float = 0.0, hatch: str | None = None, z: float = 2.0) -> None:
        self.shapes.append((min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0), color, alpha, edge, width, hatch, z))
        self.legend.setdefault(legend, {"facecolor": color, "alpha": alpha, "edgecolor": edge, "linewidth": width,
                                        "hatch": hatch})

    def render(self, out_path: Path, title: str, dpi: int, scale: int, panel: bool,
               xlim: tuple[float, float] | None = None, ylim: tuple[float, float] | None = None,
               notes: list[str] | None = None) -> None:
        import matplotlib.pyplot as plt
        from matplotlib.collections import PatchCollection
        from matplotlib.patches import Patch, Rectangle

        from scalar_snapshot import _save_figure

        xs = [s[0] for s in self.shapes] + [s[0] + s[2] for s in self.shapes] or [0, 44]
        ys = [s[1] for s in self.shapes] + [s[1] + s[3] for s in self.shapes] or [0, 66]
        x0, x1 = xlim or (min(xs) - 20, max(xs) + 20)
        y0, y1 = ylim or (min(ys) - 20, max(ys) + 20)
        width_u, height_u = (x1 - x0) / 44, (y1 - y0) / self.cell_nm
        aspect = self.cell_nm / 44
        fig_w = 13.0
        fig_h = max(4.0, min(11.0, (fig_w - 3.2) * height_u * aspect / max(width_u, 1e-6) + 1.0))
        fig = plt.figure(figsize=(fig_w, fig_h))
        ax = fig.add_axes((0.05, 0.07, 0.75, 0.86))
        legend_ax = fig.add_axes((0.83, 0.07, 0.16, 0.86))
        legend_ax.axis("off")
        groups: dict[tuple, list] = {}
        for sx, sy, sw, sh, color, alpha, edge, lw, hatch, z in self.shapes:
            groups.setdefault((color, alpha, edge, lw, hatch, z), []).append(
                Rectangle((sx / 44, sy / self.cell_nm), sw / 44, sh / self.cell_nm))
        for (color, alpha, edge, lw, hatch, z), patches in sorted(groups.items(), key=lambda item: item[0][5]):
            collection = PatchCollection(patches, facecolor=color, alpha=alpha, edgecolor=edge, linewidth=lw,
                                         zorder=z, gid="GDS:snap")
            if hatch:
                collection.set_hatch(hatch)
            ax.add_collection(collection)
        ax.set_xlim(x0 / 44, x1 / 44)
        ax.set_ylim(y0 / self.cell_nm, y1 / self.cell_nm)
        for lx, ly, text, color in self.labels:
            ax.text(lx / 44, ly / self.cell_nm, text, fontsize=8, fontweight="bold", color=color, ha="center",
                    va="bottom", zorder=9, clip_on=True,
                    bbox=dict(facecolor="white", alpha=0.75, edgecolor="none", pad=0.8))
        ax.set_aspect(aspect, adjustable="box")
        if "Grid" in self.visible:
            ax.grid(True, color="#b8bec4", linewidth=0.4, alpha=0.7)
        if panel:
            ax.tick_params(labelbottom=False, labelleft=False)
        ax.set_title(title, fontsize=9, loc="left")
        if notes:
            fig.text(0.05, 0.015, " | ".join(notes), fontsize=7, color="#7b3f00")
        handles = [Patch(label=label, **style) for label, style in self.legend.items()]
        legend_ax.legend(handles=handles, loc="upper left", fontsize=8, frameon=False, borderaxespad=0)
        if panel:
            fig._iso_legend = (legend_ax, handles)
        height = _height(self.row)
        per_height = 6 if height == 165 else 5
        fig._iso_ruler = {"x_unit0": x0 / 44, "x_glob0": x0 / 44, "y_unit0": y0 / self.cell_nm,
                          "y_glob0": y0 / self.cell_nm, "m0_unit0": 0, "m0_glob0": 0, "per_height": per_height,
                          "coords": "unit", "x_max": x1 / 44, "cells": y1 / self.cell_nm, "m0_max": 0}
        _save_figure(fig, ax, out_path, dpi, True, {**self.row, "CellHeight": f"ch{height}"}, "template", scale)
        plt.close(fig)


# ---------------------------------------------------------------------------------------------- VIA_CHN
def _via_chn(row: dict[str, str], canvas: _Canvas) -> tuple[str, list[str]]:
    ogd_lay, pgd_lay = _layer_key(_f(row, "OgdLay") or "m0"), _layer_key(_f(row, "PgdLay") or "m1")
    via_lay = _f(row, "ViaLay").lower() or "via"
    n_ogd, n_pgd = (int(v) for v in (_f(row, "LineNum") + ";20;20").split(";")[:2])
    ogd_cd, ogd_pitch = _lego(row, ogd_lay)
    pgd_cd, pgd_pitch = _lego(row, pgd_lay)
    width, height = n_pgd * pgd_pitch, n_ogd * ogd_pitch
    via_w, via_h = _via_size(row, (pgd_cd, ogd_cd))
    visible = canvas.visible
    patterns = [_pairs(pattern, n_pgd, n_ogd) for pattern in _f(row, "ViaUnitPattern").split("|")]
    vias = [(p, o) for pattern in patterns for p, o, _raw in pattern]
    chain: list[tuple[str, int, float, float]] = []
    for pattern in patterns:
        for (p1, o1, _a), (p2, o2, _b) in zip(pattern, pattern[1:]):
            if p1 == p2:
                chain.append(("pgd", p1, min(o1, o2) * ogd_pitch, max(o1, o2) * ogd_pitch))
            elif o1 == o2:
                chain.append(("ogd", o1, min(p1, p2) * pgd_pitch, max(p1, p2) * pgd_pitch))
    ogd_color, pgd_color = LAYER_COLOR.get(ogd_lay, "#555555"), LAYER_COLOR.get(pgd_lay, "#555555")
    if "OGD lines" in visible:
        for o in range(n_ogd + 1):
            y = o * ogd_pitch
            canvas.rect(-pgd_pitch / 2, y - ogd_cd / 2, width + pgd_pitch / 2, y + ogd_cd / 2,
                        f"{ogd_lay.upper()} (OGD) dummy / unused", ogd_color, PASSIVE, z=1)
    if "PGD lines" in visible:
        for p in range(n_pgd + 1):
            x = p * pgd_pitch
            canvas.rect(x - pgd_cd / 2, -ogd_pitch / 2, x + pgd_cd / 2, height + ogd_pitch / 2,
                        f"{pgd_lay.upper()} (PGD) dummy / unused", pgd_color, PASSIVE, z=1.1)
    if "Chain path" in visible:
        for kind, line, a, b in chain:
            if kind == "pgd":
                x = line * pgd_pitch
                canvas.rect(x - pgd_cd / 2, a - ogd_cd / 2, x + pgd_cd / 2, b + ogd_cd / 2,
                            f"{pgd_lay.upper()} chain segment (active)", pgd_color, ACTIVE, z=3)
            else:
                y = line * ogd_pitch
                canvas.rect(a - pgd_cd / 2, y - ogd_cd / 2, b + pgd_cd / 2, y + ogd_cd / 2,
                            f"{ogd_lay.upper()} chain segment (active)", ogd_color, ACTIVE, z=3.1)
    vcr_notes = _tcn_vcr(row, canvas, pgd_pitch, ogd_pitch) if "VCR / VCRNUB" in visible else []
    if "Vias" in visible:
        for p, o in vias:
            x, y = p * pgd_pitch, o * ogd_pitch
            canvas.rect(x - via_w / 2, y - via_h / 2, x + via_w / 2, y + via_h / 2, f"{via_lay.upper()} chain via",
                        VIA_COLOR.get(via_lay, "#444444"), 1.0, "k", 0.6, z=5)
    unit = _f(row, "UnitCellNum").replace(";", " x ")
    notes = [f"unit cell {n_pgd} PGD x {n_ogd} OGD lines; array {unit or '?'} unit cells",
             "chain from ViaUnitPattern order; cut guides not drawn", *vcr_notes]
    return f"{TEMPLATE_TITLES['VIA_CHN']}: {ogd_lay.upper()} / {pgd_lay.upper()} / {via_lay.upper()}", notes


def _tcn_vcr(row: dict[str, str], canvas: _Canvas, tcn_pitch: float, ogd_pitch: float) -> list[str]:
    """TCN-VCR chains: ``VcrUnitPattern`` TCN-range,VCR-track and ``VcrnubUnitPattern`` TCN,VCR,top|bottom."""
    height = _height(row)
    rails = _pairs(_f(row, "VcrUnitPattern"), 200, 20)
    nubs = _pairs(_f(row, "VcrnubUnitPattern"), 200, 20)
    if not rails and not nubs:
        return []
    spans: dict[int, list[int]] = {}
    for tcn, vcr, _raw in rails:
        spans.setdefault(vcr, []).append(tcn)
    for vcr, tcns in spans.items():
        for start, end in _runs(sorted(set(tcns))):
            canvas.rect(start * tcn_pitch - tcn_pitch / 2, vcr * height - 6, end * tcn_pitch + tcn_pitch / 2,
                        vcr * height + 6, "VCR (CRL rail)", LAYER_COLOR["vcr"], ACTIVE, "#9a4d10", 0.5, z=4)
    for tcn, vcr, raw in nubs:
        side = 1 if "top" in raw.lower() else -1
        shift = 0.0
        values = raw.partition("|")[2].split(",")
        if len(values) == 4:
            try:
                shift = float(values[3]) * 1000
            except ValueError:
                shift = 0.0
        y = vcr * height + shift
        canvas.rect(tcn * tcn_pitch - 22, y, tcn * tcn_pitch + 22, y + side * 13.4, "VCRNUB (1PP, 13.4 nm)",
                    "#ffbb78", 1.0, "#9a4d10", 0.6, z=4.5)
    return [f"VCR rails on CRL = VCR track x {height} nm"]


def _runs(values: list[int]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    for value in values:
        if runs and value == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], value)
        else:
            runs.append((value, value))
    return runs


# ---------------------------------------------------------------------------------------------- BE_ECD
def _lego_lines(spec: str) -> list[tuple[float, float]]:
    """``CD;SPC;CD;SPC...:repeat`` (µm) -> [(centre nm, CD nm)] from the bottom of the stack."""
    lines: list[tuple[float, float]] = []
    cursor = 0.0
    for group in (spec or "").split(","):
        body, _sep, repeat = group.partition(":")
        values = [float(v) * 1000 for v in body.split(";") if v.strip()]
        count = int(repeat) if repeat.strip().isdigit() else 1
        for _ in range(max(1, count)):
            for index, value in enumerate(values):
                if index % 2 == 0:
                    lines.append((cursor + value / 2, value))
                cursor += value
    return lines


def _be_ecd(row: dict[str, str], canvas: _Canvas) -> tuple[str, list[str], tuple | None]:
    layer = _layer_key(_f(row, "AmeLayer") or "m0")
    lines = _lego_lines(_f(row, "AmeLegoPattern"))
    if not lines:
        cd, pitch = _lego(row, layer)
        count = int(_f(row, "AmeLineNum") or 19) * 2 - 1
        lines = [(index * pitch, cd) for index in range(count)]
    try:
        length = float(_f(row, "EcdLen")) * 1000
    except ValueError:
        length = 30000.0
    active = min(len(lines) - 1, max(0, int(_f(row, "AmeLineNum") or 1) - 1))
    color = LAYER_COLOR.get(layer, "#3b4650")
    visible = canvas.visible
    for index, (y, cd) in enumerate(lines):
        if index == active and "Active line" in visible:
            canvas.rect(0, y - cd / 2, length, y + cd / 2, f"{layer.upper()} active ECD line", color, ACTIVE, z=3)
        elif index != active and "Dummy lines" in visible:
            canvas.rect(0, y - cd / 2, length, y + cd / 2, f"{layer.upper()} dummy lines", color, PASSIVE, z=1)
    route = _layer_key(_f(row, "Routelayers").split(";")[0] or "m1")
    route_cd, route_pitch = _lego(row, route)
    top = lines[-1][0] + lines[-1][1]
    for name, field in (("FORCE", "ForceViaPattern"), ("SENSE", "SenseViaPattern")):
        for column, line, _raw in _pairs(_f(row, field), 400, len(lines) - 1):
            if not 0 <= line < len(lines):
                continue
            y, cd = lines[line]
            for x in (column * route_pitch + route_pitch / 2, length - column * route_pitch - route_pitch / 2):
                if "Route stubs" in visible:
                    canvas.rect(x - route_cd / 2, y, x + route_cd / 2, top + 4 * route_pitch,
                                f"{route.upper()} {name.lower()} route", PIN_COLOR[name], 0.45, z=4)
                if "Force / sense vias" in visible:
                    canvas.rect(x - cd / 2, y - cd / 2, x + cd / 2, y + cd / 2, f"{name.lower()} via",
                                PIN_COLOR[name], 1.0, "k", 0.6, z=5)
    window = None if "Full length" in visible else (-40.0, min(length, 1500.0))
    notes = [f"{len(lines)} {layer.upper()} lines, active line {active + 1} (AmeLineNum), EcdLen {length / 1000:g} um",
             "showing the left 1.5 um tap region (Full length shows the whole line)" if window else "full length"]
    return f"{TEMPLATE_TITLES['BE_ECD']}: {layer.upper()} with {route.upper()} force/sense", notes, window


# ---------------------------------------------------------------------------------------------- VIA_KLV
def _count(spec: str, default: int) -> int:
    match = re.search(r":(\d+)\s*$", spec or "")
    return int(match[1]) if match else default


def _via_klv(row: dict[str, str], canvas: _Canvas) -> tuple[str, list[str]]:
    ogd_lay, pgd_lay = _layer_key(_f(row, "ogdLay") or "m0"), _layer_key(_f(row, "pgdLay") or "m1")
    ogd_cd, ogd_pitch = _lego(row, ogd_lay)
    pgd_cd, pgd_pitch = _lego(row, pgd_lay)
    jumpers = [int(v) for v in re.findall(r"\d+", _f(row, "ogdJumperLinesTB"))] or [0, 0]
    top_jumpers, bottom_jumpers = (jumpers + [jumpers[0]])[:2]
    n_ogd = bottom_jumpers + _count(_f(row, "ogdLegoList"), 1) + top_jumpers
    n_pgd = _count(_f(row, "pgdLegoList"), 10)
    width, height = (n_pgd - 1) * pgd_pitch, (n_ogd - 1) * ogd_pitch
    via_lay = re.match(r"(v\d|vt|vg)", row.get("StructureName", "").lower())
    via_name = (via_lay[1] if via_lay else "via").upper()
    visible = canvas.visible
    active = _pairs(_f(row, "ActViaPattern"), n_pgd, n_ogd)
    active_lines = {o for _p, o, _raw in active}
    ogd_color, pgd_color = LAYER_COLOR.get(ogd_lay, "#555555"), LAYER_COLOR.get(pgd_lay, "#555555")
    if "OGD lines" in visible:
        for o in range(n_ogd):
            y = o * ogd_pitch
            label = (f"{ogd_lay.upper()} active line" if o in active_lines else
                     f"{ogd_lay.upper()} jumper / dummy lines")
            canvas.rect(-pgd_pitch, y - ogd_cd / 2, width + pgd_pitch, y + ogd_cd / 2, label, ogd_color,
                        ACTIVE if o in active_lines else PASSIVE, z=1 + (o in active_lines))
    pin_tracks: dict[int, str] = {}
    pins = []
    for pin in ("iLOPin", "iHIPin", "vLOPin", "vHIPin"):
        for entry in _f(row, pin).split("/"):
            parts = [part.strip() for part in entry.split(";")]
            if len(parts) >= 3:
                tracks = expand_axis(parts[1], n_pgd - 1)
                guides = expand_axis(re.sub(r"\[.*\]", "", parts[2]).replace(",", "/"), n_ogd - 1)
                kind = "J" if "[J]" in parts[2].upper() else "G"
                pins.append((pin[:-3], tracks, guides, kind))
                for track in tracks:
                    pin_tracks.setdefault(track, pin[:-3])
    if "PGD lines" in visible:
        cuts: dict[int, list[tuple[float, float]]] = {}
        for entry in _f(row, "pgdCutPattern").split(";"):
            parts = [part.strip() for part in entry.split(",")]
            if len(parts) >= 2 and parts[1]:
                span = expand_axis(parts[1], n_ogd - 1)
                if span:
                    for p in expand_axis(parts[0], n_pgd - 1):
                        cuts.setdefault(p, []).append((min(span) * ogd_pitch, max(span) * ogd_pitch))
        for p in range(n_pgd):
            x = p * pgd_pitch
            start = -ogd_pitch
            for low, high in sorted(cuts.get(p, [])):
                if low > start:
                    canvas.rect(x - pgd_cd / 2, start, x + pgd_cd / 2, low, f"{pgd_lay.upper()} lines", pgd_color,
                                PASSIVE + 0.1, z=1.5)
                if "PGD cuts" in visible:
                    canvas.rect(x - pgd_cd, low, x + pgd_cd, high, f"{pgd_lay.upper()} removed (pgdCutPattern)",
                                "#ffffff", 0.9, "#e6550d", 0.8, "xx", z=1.6)
                start = max(start, high)
            if start < height + ogd_pitch:
                canvas.rect(x - pgd_cd / 2, start, x + pgd_cd / 2, height + ogd_pitch, f"{pgd_lay.upper()} lines",
                            pgd_color, PASSIVE + 0.1, z=1.5)
    if "Diffusion / TCN" in visible:
        for entry in _f(row, "DrawDiff").split(";"):
            parts = [part.strip() for part in entry.split(",")]
            if len(parts) < 3:
                continue
            try:
                width_nm = float(parts[3]) * 1000 if len(parts) > 3 and parts[3] else 46.0
            except ValueError:
                width_nm = 46.0
            columns = expand_axis(parts[0], n_pgd - 1)
            if not columns:
                continue
            n_type = parts[2].lower().startswith("n")
            # DrawDiff rows are diffusion tracks (CH/2 pitch); keep the ones inside the drawn line field.
            track_nm = _height(row) / 2
            for o in expand_axis(parts[1], 400):
                y = (o + 0.5) * track_nm - ogd_pitch / 2
                if y - width_nm / 2 > height + ogd_pitch / 2:
                    continue
                canvas.rect(min(columns) * pgd_pitch, y - width_nm / 2, max(columns) * pgd_pitch, y + width_nm / 2,
                            "N diffusion (DrawDiff)" if n_type else "P diffusion (DrawDiff)",
                            "#2ca25f" if n_type else "#f4a582", 0.3, z=0.8)
                if pgd_lay == "poly":
                    for p in columns[:-1]:
                        canvas.rect((p + 0.5) * pgd_pitch - 6, y - width_nm / 2, (p + 0.5) * pgd_pitch + 6,
                                    y + width_nm / 2, "TCN (between poly)", LAYER_COLOR["tcn"], 0.3, z=1.2)
    if "Pins" in visible:
        for name, tracks, guides, kind in pins:
            for track in tracks:
                for guide in guides:
                    x, y = track * pgd_pitch, guide * ogd_pitch
                    canvas.rect(x - pgd_cd / 2, y - ogd_cd / 2, x + pgd_cd / 2, y + ogd_cd / 2,
                                f"{name} pin ({'jumper' if kind == 'J' else 'guide'} contact)", PIN_COLOR[name],
                                0.9, "k", 0.3, z=4)
    if "Dummy vias" in visible:
        for p, o, _raw in _pairs(_f(row, "DumViaPattern"), n_pgd, n_ogd):
            x, y = p * pgd_pitch, o * ogd_pitch
            canvas.rect(x - pgd_cd / 2, y - ogd_cd / 2, x + pgd_cd / 2, y + ogd_cd / 2, f"{via_name} dummy via",
                        "#d9d9d9", 0.9, "#7f7f7f", 0.6, "////", z=5)
    if "Active via" in visible:
        for p, o, raw in active:
            size = [part.strip() for part in raw.split("|")[0].split(",")[2:4]]
            try:
                w, h = (float(v) * 1000 for v in size)
            except ValueError:
                w, h = _via_size(row, (pgd_cd, ogd_cd))
            x, y = p * pgd_pitch, o * ogd_pitch
            canvas.rect(x - w / 2, y - h / 2, x + w / 2, y + h / 2, f"{via_name} active (Kelvin) via", "#d62728",
                        1.0, "k", 0.9, z=6)
    notes = [f"{n_pgd} {pgd_lay.upper()} x {n_ogd} {ogd_lay.upper()} lines ({_f(row, 'viaklvType') or '4pad'})",
             "pins shown at their guide / jumper contacts; ogd/pgd cut patterns not drawn"]
    return f"{TEMPLATE_TITLES['VIA_KLV']}: {via_name} between {ogd_lay.upper()} and {pgd_lay.upper()}", notes


# ---------------------------------------------------------------------------------------------- ROSPFT
def _ros_connections(row: dict[str, str]) -> list[tuple[str, int, str, int]]:
    """``Connections`` ``net,m0,track,layer,index`` (relative to ReferenceTrackOgd/Pgd) -> [(net, m0, layer, index)]."""
    out = []
    for entry in _f(row, "Connections").split(";"):
        parts = [part.strip() for part in entry.split(",")]
        if len(parts) < 5 or parts[1].lower() != "m0":
            continue
        for track in expand_axis(parts[2], 400):
            for index in expand_axis(parts[4], 400):
                out.append((parts[0].lower(), track, parts[3].lower(), index))
    return out


def _rospft(row: dict[str, str], canvas: _Canvas) -> tuple[str, list[str], tuple, tuple]:
    """Pin connections drawn onto the probed device of a library ring-oscillator cell.

    Indices are relative to ReferenceTrackPgd (poly / TCN / M1 share the 44 nm pitch; TCN t lies between
    poly t and t+1) and ReferenceTrackOgd (M0). The surrounding standard cell comes from ``cellName`` in
    ``cellLibrary`` and is not part of the buildsheet, so it is shown as a neutral track grid only.
    """
    height = _height(row)
    per_height = 6 if height == 165 else 5
    m0_pitch = height / per_height
    m0_cd = {110: 12.0, 132: 15.0, 165: 14.0}[height]
    connections = _ros_connections(row)
    if not connections:
        raise ValueError("ROSPFT row has no Connections entries")
    visible = canvas.visible

    def x_of(layer: str, index: int) -> float:
        return (index + 0.5) * 44 if layer == "tcn" else index * 44

    def base(net: str) -> str:
        return "DV" if net.startswith("dv") else re.sub(r"\d+$", "", net).upper()

    xs = [x_of(layer, index) for net, _t, layer, index in connections if not net.startswith("dv")]
    tracks = [track for net, track, _l, _i in connections if not net.startswith("dv")]
    x_lo, x_hi = min(xs) - 4 * 44, max(xs) + 4 * 44
    t_lo, t_hi = min(tracks) - 2, max(tracks) + 2
    y_lo, y_hi = t_lo * m0_pitch, t_hi * m0_pitch
    if "Cell context" in visible:
        for p in range(math.floor(x_lo / 44), math.ceil(x_hi / 44) + 1):
            canvas.rect(p * 44 - 6, y_lo, p * 44 + 6, y_hi, "Poly (library cell, schematic)", LAYER_COLOR["poly"],
                        0.12, z=1)
            canvas.rect(p * 44 + 16, y_lo, p * 44 + 28, y_hi, "TCN (library cell, schematic)", LAYER_COLOR["tcn"],
                        0.1, z=1)
        for track in range(t_lo, t_hi + 1):
            canvas.rect(x_lo, track * m0_pitch - m0_cd / 2, x_hi, track * m0_pitch + m0_cd / 2,
                        "M0 tracks (library cell)", LAYER_COLOR["m0"], 0.1, z=1.1)
    tcn_vias = [(track, index) for net, track, layer, index in connections if layer == "tcn" and not net.startswith("dv")]
    instances: dict[int, list[int]] = {}
    for _track, index in tcn_vias:
        instances.setdefault(index // 20, []).append(index)
    mos = (_f(row, "TYPE") or row.get("StructureName", "ROSN")[3:4]).upper()[:1]
    if "Diffusion" in visible and tcn_vias:
        low = min(track for track, _i in tcn_vias) * m0_pitch - m0_pitch / 2
        high = max(track for track, _i in tcn_vias) * m0_pitch + m0_pitch / 2
        for indices in instances.values():
            canvas.rect(min(indices) * 44, low, (max(indices) + 1) * 44, high,
                        f"{'N' if mos == 'N' else 'P'} diffusion (device under test)",
                        "#2ca25f" if mos == "N" else "#f4a582", 0.3, z=1.5)
            for tcn in range(min(indices), max(indices) + 1):
                canvas.rect((tcn + 0.5) * 44 - 7, low, (tcn + 0.5) * 44 + 7, high, "TCN on the device",
                            LAYER_COLOR["tcn"], 0.45, z=1.7)
    nets: dict[str, list[tuple[int, str, int]]] = {}
    for net, track, layer, index in connections:
        nets.setdefault(net, []).append((track, layer, index))
    routes = {}
    for entry in _f(row, "Routes").split(";"):
        parts = [part.strip() for part in entry.split(",")]
        if len(parts) >= 3:
            routes[parts[1].lower()] = (parts[0], parts[2].lower())
    for net, points in nets.items():
        name = base(net)
        color = ROS_PIN_COLOR.get(name, "#555555")
        if name == "DV":
            if "Dummy V0" in visible:
                for track, _layer, index in points:
                    y = track * m0_pitch
                    canvas.rect(index * 44 - 8, y - 8, index * 44 + 8, y + 8, "Dummy V0 (dv*)", color, 0.9, "k", 0.3, z=5)
            continue
        pin, side = routes.get(net, (net.upper(), ""))
        for track in sorted({track for track, _l, _i in points}):
            on_track = [x_of(layer, index) for t, layer, index in points if t == track]
            y = track * m0_pitch
            left, right = min(on_track), max(on_track)
            if side == "left":
                left = x_lo
            elif side == "right":
                right = x_hi
            if "Pin nets" in visible:
                canvas.rect(left - 8, y - m0_cd / 2, right + 8, y + m0_cd / 2, f"M0 {name} net", color, 0.75, z=3)
            if "Labels" in visible:
                canvas.text(left + 30 if side == "left" else right - 30 if side == "right" else (left + right) / 2,
                            y + m0_cd / 2 + 1, f"{pin} -> {side or 'pin'}", color)
        for track, layer, index in points:
            x, y = x_of(layer, index), track * m0_pitch
            if layer == "m1" and "M1" in visible:
                canvas.rect(x - 12, y_lo, x + 12, y_hi, f"M1 {name} strap", LAYER_COLOR["m1"], 0.35, z=2.5)
            if "Vias" in visible:
                via = {"tcn": "VT", "poly": "VG", "m1": "V0"}.get(layer, layer.upper())
                size = (12, 15) if via in ("VT", "VG") else (20, 12)
                canvas.rect(x - size[0] / 2, y - size[1] / 2, x + size[0] / 2, y + size[1] / 2,
                            f"{via} {name}", color, 1.0, "k", 0.7, z=6)
    cell = _f(row, "cellName")
    notes = [f"{_f(row, 'INSTANCES') or '1'} instance(s), {_f(row, 'LEGS_SHARED') or '?'} leg(s); "
             f"indices relative to {_f(row, 'ReferenceTrackPgd')} / {_f(row, 'ReferenceTrackOgd')}",
             f"standard-cell content from {_f(row, 'cellLibrary')}/{cell[:48]}{'...' if len(cell) > 48 else ''} "
             "is not in the buildsheet (grid only); cuts / deletes not drawn"]
    title = f"{TEMPLATE_TITLES['ROSPFT']}: {row.get('StructureName', '')}"
    return title, notes, (x_lo - 20, x_hi + 20), (y_lo - 10, y_hi + 10)


# ---------------------------------------------------------------------------------------------- entry point
def render_template(row: dict[str, str], out_path: Path, visible_layers: set[str] | None = None, dpi: int = 120,
                    scale: int = 1, panel: bool = False) -> None:
    kind = template_kind(row)
    layers = TEMPLATE_LAYERS[kind]
    visible = set(layers) - TEMPLATE_LAYERS_OFF if visible_layers is None else set(visible_layers)
    canvas = _Canvas(row, visible)
    window = ywindow = None
    if kind == "VIA_CHN":
        title, notes = _via_chn(row, canvas)
    elif kind == "BE_ECD":
        title, notes, window = _be_ecd(row, canvas)
    elif kind == "ROSPFT":
        title, notes, window, ywindow = _rospft(row, canvas)
    else:
        title, notes = _via_klv(row, canvas)
    canvas.render(out_path, title, dpi, scale, panel, xlim=window, ylim=ywindow, notes=notes)
