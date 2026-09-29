"""ISO E-test Scalar calculator (1281 node, ISO module).

Reads the ISO metadata buildsheet (``ISO_Metadata_Input.csv``) and fills the
``Scalar`` column for every ISO structure, following the methodology in
``x1281z - ISO E-test Scalar calculations suggestion.pptx``::

    Scalar = 1 / FailModeCount

    PGD / end-to-end (GATEGATE, EPIEPI, TCNTCN)
        FailModeCount = (#LO<->HI tip-to-tip pairs per unit cell) x (#unit cells)
    OGD contact-to-gate (CTG)
        FailModeCount = (#active PLY) x (#diff tracks on which an active (VT) TCN
                         flanks the gate) x (#unit cells)   -- one per gate per
                         track; leakage on either side of the gate is one path
    OGD diffusion (DIFFOGD)
        FailModeCount = (#active FTI) x (#device diff tracks cut) x (#unit cells)
    Diffusion chain (DIFFCHN)
        FailModeCount = #active VG gates in series (x #chains for stacked arrays)

    #unit cells = ArraySizeOgd x ArraySizePgd

Per slide 4 of the deck, GATEGATE rows whose vias were removed (OC1/OC2/OC3)
keep the SDR count from the declared LO1/HI1 tracks; every CTG variant is
counted from its own geometry.

Everything is derived from the buildsheet row (unit-cell / array sizes, FTI
grid, plug patterns, VG / VT columns and tracks, LO1 / HI1 net assignment).
The via-track -> diffusion-cell mapping and the plug model were calibrated on
the chassis GDS files bundled in ISO_LLM.bat.

Outputs
-------
* ``ISO_Metadata_Output.csv``  -- input columns, ``Scalar`` filled, plus a new
                                  ``ScalarFormula`` column (plain-text derivation).
* ``ISO_Scalar_Review.csv``    -- per-row derivation (family, type, counts,
                                  formula, notes) for review.
* ``ISO_Scalar_Variants.csv``  -- one line per distinct structure variant
                                  (design pattern + scalar-relevant geometry),
                                  with row counts and the snapshot file name.
* ``--snapshots DIR``          -- annotated unit-cell PNG per variant, named
                                  after the example structure, showing the
                                  counted leakage paths (needs matplotlib).
* ``--pptx FILE``              -- PowerPoint catalog: executive summary,
                                  coverage, methodology, summary table, one
                                  slide per snapshot (python-pptx); snapshots
                                  are deleted afterwards unless
                                  ``--keep-snapshots`` is given.
* ``--summary-pptx FILE``      -- the same deck without the snapshot slides.
A per-(TestRow, family, type) summary is printed to stdout.

Usage::

    python ScalarCalculation.py [--input ISO_Metadata_Input.csv]
                                [--output ISO_Metadata_Output.csv]
                                [--review ISO_Scalar_Review.csv]
                                [--snapshots snapshots] [--snapshot NAME ...]
                                [--pptx ISO_Scalar_Catalog.pptx]
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import sys
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = BASE_DIR / "ISO_Metadata_Input.csv"
DEFAULT_OUTPUT = BASE_DIR / "ISO_Metadata_Output.csv"
DEFAULT_REVIEW = BASE_DIR / "ISO_Scalar_Review.csv"
DEFAULT_VARIANTS = BASE_DIR / "ISO_Scalar_Variants.csv"
DEFAULT_NAME_VALIDATION = BASE_DIR / "ISO_StructureName_Validation.csv"

# ---------------------------------------------------------------------------
# CTG (contact-to-gate OGD) reference: an SDR type-A gate column has one active
# VG gate that runs beside an active (VT) TCN over 2 diffusion tracks -> 2 fail
# modes per Pgd unit (one per gate per track; leakage on either side of the
# gate is one fail mode). Used only to flag variants whose geometric count
# differs from the reference.
# ---------------------------------------------------------------------------
CTG_SDR_PER_GATE_COL = 2

# Two single-cell tip-to-tip segments count as adjacent when their via tracks
# are closer than this many diffusion cells (guards the int() cell rounding).
ETE_ADJ_CELLS = 1.75

FAMILY_PREFIXES = ("CTG", "GATEGATE", "EPIEPI", "TCNTCN", "DIFFOGD", "DIFFCHN")
VIA_CHAIN_PREFIXES = ("VT_MV", "VG_MV", "VCR_MV")
FORMULA_COL = "ScalarFormula"     # new output column, inserted right after ``Scalar``
BIAS_CONDITIONS = ("-0.65", "0.65", "-0.75", "0.75", "-1.1", "1.1")
TEST_TYPES = ("I2",) * len(BIAS_CONDITIONS)


# ---------------------------------------------------------------------------
# Buildsheet DSL helpers
# ---------------------------------------------------------------------------
_R_TOKEN = re.compile(r"^r(-?\d+)$", re.IGNORECASE)
_STRIDE = re.compile(r"^(?P<a>-?\w+)\s*:\s*(?P<b>-?\w+)\s*\[(?P<s>\d+)\]$")
_MODIFIER = re.compile(r"-[A-Za-z]+$")   # e.g. ``7-bottom`` -> ``7``


def _resolve(tok: str, extent: int) -> int:
    tok = _MODIFIER.sub("", tok.strip())
    m = _R_TOKEN.match(tok)
    if m:
        return extent - int(m.group(1))
    return int(tok)


def expand_axis(axis: str, extent: int) -> list[int]:
    """Expand a buildsheet axis expression into concrete integer positions.

    ``a`` | ``a:b`` | ``a:b[N]`` (step N+1) | ``x/y/z`` fragments | ``rK`` -> extent-K.
    Returns [] for empty / unparseable input.
    """
    axis = (axis or "").strip()
    if not axis:
        return []
    if "/" in axis:
        out: list[int] = []
        for frag in axis.split("/"):
            out.extend(expand_axis(frag, extent))
        return out
    try:
        m = _STRIDE.match(axis)
        if m:
            a = _resolve(m.group("a"), extent)
            b = _resolve(m.group("b"), extent)
            step = int(m.group("s")) + 1
            return list(range(a, b + 1, step)) if a <= b else []
        if ":" in axis:
            a, b = axis.split(":", 1)
            lo, hi = _resolve(a, extent), _resolve(b, extent)
            if lo > hi:
                lo, hi = hi, lo
            return list(range(lo, hi + 1))
        return [_resolve(axis, extent)]
    except ValueError:
        return []


def segments(pattern: str) -> list[str]:
    return [s.strip() for s in (pattern or "").split(";") if s.strip()]


def strip_shift(seg: str) -> str:
    """Drop a trailing ``|d1,d2,d3,d4`` ResizeShift marker."""
    return seg.split("|", 1)[0].strip()


def int_list(spec: str) -> list[int]:
    """``"6;14"`` / ``"6; 16"`` / ``"9;11;"`` -> [6, 14] ..."""
    out: list[int] = []
    for tok in re.split(r"[;,/\s]+", spec or ""):
        tok = _MODIFIER.sub("", tok.strip())
        if tok.lstrip("-").isdigit():
            out.append(int(tok))
    return out


def as_int(v: str, default: int = 0) -> int:
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Y model: diffusion cells, plug spans, via track -> cell
# ---------------------------------------------------------------------------
def plug_spans(plugs: set[int], ncell: int) -> list[tuple[int, int]]:
    """Cell spans after applying plugs (plug V closes the gap between cells V-1 and V).

    Verified on the chassis GDS: PolyPlugs 3/4 -> cells 2..4, TCNPlugs 4/5 ->
    cells 3..5, PolyPlugs 3/6 -> cells 2..3 + 5..6.
    """
    spans: list[tuple[int, int]] = []
    i = 0
    while i < ncell:
        j = i
        while j + 1 < ncell and (j + 1) in plugs:
            j += 1
        spans.append((i, j))
        i = j + 1
    return spans


def split_spans(spans: list[tuple[int, int]], cut: set[int]) -> list[tuple[int, int]]:
    """Remove ``cut`` cells (e.g. an FTI in the same column) from the spans."""
    out: list[tuple[int, int]] = []
    for a, b in spans:
        cur: int | None = None
        for k in range(a, b + 1):
            if k in cut:
                if cur is not None:
                    out.append((cur, k - 1))
                    cur = None
            elif cur is None:
                cur = k
        if cur is not None:
            out.append((cur, b))
    return out


def span_containing(spans: list[tuple[int, int]], cell: int) -> tuple[int, int]:
    for a, b in spans:
        if a <= cell <= b:
            return a, b
    return cell, cell


def parse_plugs(row: dict[str, str], col: str, ogd: int, ncell: int) -> dict[int, set[int]]:
    """``X,Y`` plug segments -> {x position within the unit: plug values}."""
    out: dict[int, set[int]] = {}
    for seg in segments(row.get(col, "")):
        toks = [t.strip() for t in strip_shift(seg).split(",")]
        if len(toks) < 2:
            continue
        ys = set(expand_axis("/".join(toks[1:]), ncell))
        for x in expand_axis(toks[0], ogd):
            out.setdefault(x % ogd, set()).update(ys)
    return out


class TrackMap:
    """Routing-track index -> diffusion cell (unit-relative).

    Linear map ``cell = base + t * cells_per_track`` calibrated on the chassis
    GDS (ch132: VG t=6 -> cell 2, VT t=14 -> cell 5; ch165 needs a larger
    offset so that 7/11/13/17 land on cells 2/3/4/5). 1TRK designs put one
    gate and one TCN per device diffusion cell; their VG tracks carry
    ``-bottom`` nudges that break the linear map, so VG tracks are ranked onto
    the VT cells (or the device cells) instead.
    """

    BASE_BY_CH = {165: 0.6}
    DEFAULT_BASE = 0.2

    def __init__(self, row: dict[str, str], ncell: int, device_cells: set[int],
                 vg_cols: dict[int, set[int]], vt_cols: dict[int, set[int]]) -> None:
        self.ncell = ncell
        ch_nm = as_int(re.sub(r"\D", "", row.get("CellHeight", "")), 132) or 132
        self.cpt = min(0.4, 240 / (ch_nm * 5))        # one diffusion cell = CELLHEIGHT/2
        self.base = self.BASE_BY_CH.get(ch_nm, self.DEFAULT_BASE)
        self.rank: dict[str, dict[int, int]] = {}
        if "1TRK" in (row.get("StructureName") or "").upper():
            # VT tracks map linearly; VG tracks (``-bottom`` nudged) are ranked
            # onto the VT cells so every gate pairs with its TCN.
            vt_tracks = sorted(set().union(*vt_cols.values())) if vt_cols else []
            vg_tracks = sorted(set().union(*vg_cols.values())) if vg_cols else []
            vt_cells = sorted({self._lin(t) for t in vt_tracks})
            target = vt_cells if len(vt_cells) == len(vt_tracks) else sorted(device_cells)
            if vg_tracks and len(vg_tracks) == len(target):
                self.rank["vg"] = dict(zip(vg_tracks, target))

    def _lin(self, t: int) -> int:
        return int(self.base + t * self.cpt) % self.ncell

    def cell(self, t: int, kind: str = "vg") -> int:
        if t in self.rank.get(kind, {}):
            return self.rank[kind][t]
        return self._lin(t)

    def cell_f(self, t: int, kind: str = "vg") -> float:
        if t in self.rank.get(kind, {}):
            return float(self.rank[kind][t])
        return self.base + t * self.cpt


# ---------------------------------------------------------------------------
# Row geometry
# ---------------------------------------------------------------------------
@dataclass
class Geometry:
    ogd: int
    pgd: int
    arr_ogd: int
    arr_pgd: int
    n_cells: int                                  # diff tracks per Pgd unit (2 x Pgd)
    fti_cols: set[int] = field(default_factory=set)          # FTI poly cols inside unit
    fti_segs: list[tuple[list[int], list[int]]] = field(default_factory=list)  # (cols, cells)
    fti_cells: dict[int, set[int]] = field(default_factory=dict)  # col -> cells with FTI
    vg_cols: dict[int, set[int]] = field(default_factory=dict)  # poly col -> VG tracks
    vt_cols: dict[int, set[int]] = field(default_factory=dict)  # TCN slot -> VT tracks
    poly_plugs: dict[int, set[int]] = field(default_factory=dict)  # poly col -> plug values
    tcn_plugs: dict[int, set[int]] = field(default_factory=dict)   # TCN slot -> plug values
    poly_plug_cols: set[int] = field(default_factory=set)
    lo_tracks: set[int] = field(default_factory=set)
    hi_tracks: set[int] = field(default_factory=set)
    device_cells: set[int] = field(default_factory=set)         # cells carrying device diff
    ymap: TrackMap | None = None

    @property
    def units(self) -> int:
        return self.arr_ogd * self.arr_pgd

    @property
    def n_intervals(self) -> int:
        """FTI-bounded intervals per Ogd unit (FTI grid tiles across units)."""
        cols = sorted({c % self.ogd for c in self.fti_cols}) if self.ogd else []
        return len(cols) if cols else 1

    def net(self, track: int) -> str:
        if track in self.lo_tracks:
            return "LO"
        if track in self.hi_tracks:
            return "HI"
        return ""

    def via_tracks(self, kind: str) -> set[int]:
        cols = self.vg_cols if kind == "vg" else self.vt_cols
        return set().union(*cols.values()) if cols else set()

    def gate_spans(self, col: int) -> list[tuple[int, int]]:
        """Poly segments in ``col`` (plug model, split by any FTI in that column)."""
        spans = plug_spans(self.poly_plugs.get(col % self.ogd, set()), self.n_cells)
        return split_spans(spans, self.fti_cells.get(col % self.ogd, set()))

    def tcn_spans(self, slot: int) -> list[tuple[int, int]]:
        return plug_spans(self.tcn_plugs.get(slot % self.ogd, set()), self.n_cells)


def _vcx_cols(row: dict[str, str], prefix: str, ogd: int, tiled: bool = True) -> dict[int, set[int]]:
    """Columns (within one Ogd unit) carrying ``prefix`` vias -> set of Y tracks.

    ``vg`` -> poly column index 0..ogd-1.
    ``vt`` -> TCN slot index 0..ogd-1; slot ``s`` is the TCN between poly
    columns ``s`` and ``s+1`` (same convention as the ``TcnPlugsUnit`` X-axis,
    verified on the chassis GDS: ``vt,0`` is the first slot right of the FTI).
    Position ``ogd`` (``r0``) is the next unit's 0 when the array is tiled and
    is folded back; for a single-unit structure it is the real right edge.
    """
    out: dict[int, set[int]] = {}
    for seg in segments(row.get("VcxPatternUnit", "")):
        toks = [t.strip() for t in strip_shift(seg).split(",")]
        if len(toks) < 3 or toks[0].lower() != prefix:
            continue
        tracks = set(int_list(toks[2]))
        for c in expand_axis(toks[1], ogd):
            if tiled or not 0 <= c <= ogd:
                c %= ogd
            out.setdefault(c, set()).update(tracks)
    return out


_MOS_RE = re.compile(r"^[^-]+-(?P<mos>NP|N|P)")


def mos_of(name: str) -> str:
    """``CTG132-NZ5-L-A`` -> ``N``; ``...-NPZ5-...`` -> ``NP``; ``DIFFOGD132-N46-...`` -> ``N``."""
    m = _MOS_RE.match(name or "")
    return m.group("mos") if m else ""


def _device_cells(row: dict[str, str], n_cells: int) -> set[int]:
    """Diffusion cells of the DEVICE polarity (N cells for an N structure, ...)."""
    mos = mos_of(row.get("StructureName", ""))
    cells: set[int] = set()
    for seg in segments(row.get("DiffPatternUnit", "")):
        parts = [p.strip() for p in strip_shift(seg).split(",")]
        if len(parts) < 2:
            continue
        flav = parts[3].lower() if len(parts) > 3 else ""
        if mos in ("N", "P") and flav and not flav.startswith(mos.lower()):
            continue
        cells.update(c for c in expand_axis(parts[1], n_cells) if 0 <= c < n_cells)
    return cells


def _fti(row: dict[str, str], ogd: int, n_cells: int) -> tuple[set[int], list[tuple[list[int], list[int]]]]:
    cols_all: set[int] = set()
    segs: list[tuple[list[int], list[int]]] = []
    for seg in segments(row.get("FTITracksUnit", "")):
        parts = [p.strip() for p in strip_shift(seg).split(",")]
        cols = expand_axis(parts[0], ogd)
        cells = expand_axis(parts[1], n_cells) if len(parts) > 1 else list(range(n_cells))
        cols_all.update(c % ogd for c in cols if 0 <= c <= ogd)
        segs.append((cols, cells))
    return cols_all, segs


def geometry_from_row(row: dict[str, str]) -> Geometry:
    ogd = as_int(row.get("UnitCellSizeOgd"), 1) or 1
    pgd = as_int(row.get("UnitCellSizePgd"), 1) or 1
    n_cells = 2 * pgd
    g = Geometry(
        ogd=ogd, pgd=pgd,
        arr_ogd=as_int(row.get("ArraySizeOgd"), 1) or 1,
        arr_pgd=as_int(row.get("ArraySizePgd"), 1) or 1,
        n_cells=n_cells,
    )
    g.fti_cols, g.fti_segs = _fti(row, ogd, n_cells)
    for cols, cells in g.fti_segs:
        for c in cols:
            if 0 <= c <= ogd:
                g.fti_cells.setdefault(c % ogd, set()).update(x for x in cells if 0 <= x < n_cells)
    g.vg_cols = _vcx_cols(row, "vg", ogd, tiled=g.arr_ogd > 1)
    g.vt_cols = _vcx_cols(row, "vt", ogd, tiled=g.arr_ogd > 1)
    g.poly_plugs = parse_plugs(row, "PolyPlugsUnit", ogd, n_cells)
    g.tcn_plugs = parse_plugs(row, "TcnPlugsUnit", ogd, n_cells)
    g.poly_plug_cols = set(g.poly_plugs)
    g.lo_tracks = set(int_list(row.get("LO1ActiveTracks", "")))
    g.hi_tracks = set(int_list(row.get("HI1ActiveTracks", "")))
    g.device_cells = _device_cells(row, n_cells)
    g.ymap = TrackMap(row, n_cells, g.device_cells, g.vg_cols, g.vt_cols)
    return g


# ---------------------------------------------------------------------------
# Structure classification
# ---------------------------------------------------------------------------
_TYPE_RE = re.compile(r"^[^-]+-[^-]+-[^-]+-(?P<type>[A-Za-z0-9_]+)")
_LEN_RE = re.compile(r"LEN(\d+)", re.IGNORECASE)


def family_of(name: str) -> str:
    for p in VIA_CHAIN_PREFIXES:
        if name.startswith(p):
            return "VIA_CHAIN"
    m = re.match(r"^([A-Za-z]+)", name)
    fam = m.group(1).upper() if m else ""
    return fam if fam in FAMILY_PREFIXES else "UNKNOWN"


def type_of(name: str) -> str:
    m = _TYPE_RE.match(name)
    return m.group("type") if m else ""


# ---------------------------------------------------------------------------
# Fail-mode counting rules
# ---------------------------------------------------------------------------
@dataclass
class Result:
    family: str
    stype: str
    fail_modes: int | None
    formula: str
    notes: list[str] = field(default_factory=list)
    detail: dict[str, object] = field(default_factory=dict)

    @property
    def scalar(self) -> str:
        if not self.fail_modes:
            return ""
        return f"{1.0 / self.fail_modes:.6g}"


@dataclass
class Segment:
    """One active gate/TCN segment: cell span, driving net, float via position."""
    start: int
    end: int
    net: str
    y: float
    track: int

    @property
    def single(self) -> bool:
        return self.start == self.end


def _segments_for(g: Geometry, kind: str, pos: int, tracks: set[int]) -> list[Segment]:
    """Active segments (one per distinct span) in a VG column / VT slot.

    Several vias landing in the same plug span are the same physical segment
    (e.g. two HI VGs on a 2-track gate); the segment keeps the first via.
    Vias of DIFFERENT nets in one span can only be a cell-rounding artefact
    of two neighbouring single-cell tips, so they stay separate segments.
    """
    ym = g.ymap
    assert ym is not None
    spans = g.gate_spans(pos) if kind == "vg" else g.tcn_spans(pos)
    out: dict[tuple[int, int, str], Segment] = {}
    for t in sorted(tracks):
        cell = ym.cell(t, kind)
        a, b = span_containing(spans, cell)
        key = (a, b, g.net(t))
        if key not in out:
            out[key] = Segment(a, b, g.net(t), ym.cell_f(t, kind), t)
    return sorted(out.values(), key=lambda s: (s.start, s.y))


def _tip_to_tip(a: Segment, b: Segment, fti: set[int]) -> bool:
    """Do two consecutive segments face each other end-to-end?

    Adjacent cells, or separated only by FTI cells in that column (gate ETE
    through FTI), or -- for two single-cell segments -- via tracks closer than
    ``ETE_ADJ_CELLS`` (guards cell rounding at unit boundaries).
    """
    gap = b.start - a.end
    if gap == 1:
        return True
    if gap > 1 and all(k in fti for k in range(a.end + 1, b.start)):
        return True
    if a.single and b.single and abs(b.y - a.y) < ETE_ADJ_CELLS:
        return True
    return False


def _ete_pairs(g: Geometry, kind: str, pos: int, tracks: set[int]) -> list[tuple[Segment, Segment]]:
    segs = _segments_for(g, kind, pos, tracks)
    fti = g.fti_cells.get(pos % g.ogd, set()) if kind == "vg" else set()
    pairs: list[tuple[Segment, Segment]] = []
    for a, b in zip(segs, segs[1:]):
        if a.net and b.net and a.net != b.net and _tip_to_tip(a, b, fti):
            pairs.append((a, b))
    return pairs


def rule_ete(g: Geometry, fam: str, stype: str) -> Result:
    """PGD end-to-end families: GATEGATE (vg columns), EPIEPI / TCNTCN (vt columns).

    Per active column: build the gate/TCN segments that carry a via (plug
    model), then count consecutive LO<->HI segments that face each other
    tip-to-tip. Rows whose vias were removed (``OC1/OC2/OC3``) are fail-mode
    variants and keep the SDR count computed from the declared LO1/HI1 tracks
    (slide 4).
    """
    kind = "vg" if fam == "GATEGATE" else "vt"
    cols = dict(g.vg_cols if kind == "vg" else g.vt_cols)
    notes: list[str] = []
    declared = g.lo_tracks | g.hi_tracks
    if not declared:
        notes.append("LO1/HI1ActiveTracks empty -- cannot assign nets")
    if not cols:
        fallback_cols = g.poly_plug_cols or set(range(g.ogd)) - g.fti_cols
        cols = {c: set() for c in fallback_cols}
        notes.append("no vias in VcxPatternUnit; SDR-equivalent using plug/gate columns")
    via_tracks = set().union(*cols.values()) if cols else set()
    if via_tracks and not declared <= via_tracks:
        notes.append("vias missing on some LO1/HI1 tracks (variant); SDR-equivalent track set used")

    per_col: dict[int, int] = {}
    for c, tracks in cols.items():
        n_pairs = len(_ete_pairs(g, kind, c, tracks)) if tracks else 0
        if n_pairs == 0:
            n_pairs = len(_ete_pairs(g, kind, c, declared))   # SDR-equivalent
        per_col[c] = n_pairs
    paths = sum(per_col.values())
    if paths == 0:
        notes.append("no LO<->HI tip-to-tip pair found -- check LO1/HI1ActiveTracks")
    n = paths * g.units
    layer = {"GATEGATE": "gate (VG)", "EPIEPI": "epi/TCN (VT)", "TCNTCN": "TCN (VT)"}.get(fam, fam)
    per_col_txt = (f"{next(iter(set(per_col.values())))} LO<->HI tip-to-tip pair(s) each"
                   if len(set(per_col.values())) == 1 else "LO<->HI tip-to-tip pairs summed per column")
    lo = ";".join(map(str, sorted(g.lo_tracks))) or "?"
    hi = ";".join(map(str, sorted(g.hi_tracks))) or "?"
    formula = (f"PGD ETE: {len(cols)} active {layer} columns/unit x {per_col_txt} "
               f"(LO1 tracks {lo} vs HI1 tracks {hi}) = {paths} LKG paths/unit; "
               f"x {g.arr_ogd}x{g.arr_pgd} unit cells = {n}; Scalar = 1/{n}")
    return Result(
        fam, stype, n or None, formula, notes,
        {"active_cols_per_unit": len(cols), "ete_pairs_per_unit": paths,
         "pairs_per_col": sorted(set(per_col.values())),
         "lo_tracks": sorted(g.lo_tracks), "hi_tracks": sorted(g.hi_tracks)},
    )


def ctg_sites(g: Geometry) -> tuple[list[tuple[int, int, int, list[int]]], list[str]]:
    """Gate||TCN leakage sites of one CTG unit.

    Returns ``[(gate_col, gate_span_start, diff_cell, active_slots), ...]`` --
    one entry per (active gate segment, diffusion track) on which the gate
    runs beside an ACTIVE TCN on at least one side; ``active_slots`` lists
    the flanking TCN slot(s) that are active there. Each entry is ONE fail
    mode: leakage happens on either side of the gate, not both.

    * Active gate = plug-model poly span holding a VG (several VGs in one span
      are one gate).
    * Active TCN = plug-model TCN span at the flanking slot holding a VT (on a
      LO1 track). TCN segments without a VT are not counted -- e.g. the TCN
      next to the FTI in type A, or the single-cell TCN above the 3-track TCN
      (which is why the 3-track gate beside the 3-track TCN gives 2 tracks).
    """
    notes: list[str] = []
    ym = g.ymap
    assert ym is not None
    gate_cols = dict(g.vg_cols)
    if not gate_cols:
        fallback = g.poly_plug_cols or set(range(g.ogd)) - g.fti_cols
        gate_cols = {c: set() for c in fallback}
        notes.append("no vg in VcxPatternUnit; using plug/gate columns as active PLY")

    vt_all = g.via_tracks("vt")
    lo_vt = {t for t in vt_all if g.net(t) == "LO"}
    if vt_all and not lo_vt:
        lo_vt = vt_all
        notes.append(f"VT tracks {sorted(vt_all)} are not in LO1ActiveTracks {sorted(g.lo_tracks)}; "
                     "all VT treated as LO")
    if not g.vt_cols:
        notes.append("no vt in VcxPatternUnit; every TCN treated as active")

    def active_tcn_cells(slot: int) -> set[int]:
        spans = g.tcn_spans(slot)
        if not g.vt_cols:
            return set(range(g.n_cells))
        cells: set[int] = set()
        for t in g.vt_cols.get(slot % g.ogd, set()):
            if t in lo_vt:
                a, b = span_containing(spans, ym.cell(t, "vt"))
                cells.update(range(a, b + 1))
        return cells

    sites: list[tuple[int, int, int, list[int]]] = []
    for c, tracks in sorted(gate_cols.items()):
        spans = g.gate_spans(c)
        if tracks:
            gsegs = sorted({span_containing(spans, ym.cell(t, "vg")) for t in tracks})
        else:
            gsegs = [s for s in spans if set(range(s[0], s[1] + 1)) & g.device_cells] or spans
        for ga, gb in gsegs:
            per_cell: dict[int, list[int]] = {}
            for slot in (c - 1, c):
                for k in set(range(ga, gb + 1)) & active_tcn_cells(slot):
                    per_cell.setdefault(k, []).append(slot % g.ogd)
            for k in sorted(per_cell):
                sites.append((c, ga, k, per_cell[k]))
    return sites, notes


def rule_ctg(g: Geometry, stype: str) -> Result:
    """CTG OGD: number of active PLY x diffusion tracks on which an active
    (VT) TCN flanks the gate. One fail mode per gate per track -- leakage may
    occur on either side of the gate, so the two sides are not added. Every
    type (incl. VCR/VCRNUB variants E/F) uses its own geometry.
    """
    sites, notes = ctg_sites(g)
    vg_all = g.via_tracks("vg")
    if vg_all and not (vg_all & g.hi_tracks):
        notes.append(f"VG tracks {sorted(vg_all)} are not in HI1ActiveTracks {sorted(g.hi_tracks)}")
    gate_cols = sorted(set(g.vg_cols) or {s[0] for s in sites})
    gates = {(s[0], s[1]) for s in sites}
    n_gates = len(gates)
    per_gate = sorted({sum(1 for s in sites if (s[0], s[1]) == gk) for gk in gates})
    per_unit = len(sites)
    sdr_per_unit = CTG_SDR_PER_GATE_COL * len(gate_cols)
    ov_txt = f"{per_gate[0]}" if len(per_gate) == 1 else "/".join(map(str, per_gate)) if per_gate else "0"
    if per_unit != sdr_per_unit:
        notes.append(f"geometric count {per_unit}/unit differs from type-A SDR reference {sdr_per_unit}/unit")
    formula_core = (f"OGD CTG: {n_gates} active VG gates/unit on {len(gate_cols)} columns "
                    f"({g.n_intervals} FTI intervals) x {ov_txt} diff tracks on which an active (VT) TCN "
                    f"flanks the gate = {per_unit} fail modes/unit (one per gate per track, either side)")
    n = per_unit * g.units
    formula = f"{formula_core}; x {g.arr_ogd}x{g.arr_pgd} unit cells = {n}; Scalar = 1/{n}"
    return Result(
        "CTG", stype, n or None, formula, notes,
        {"fti_intervals_per_unit": g.n_intervals, "gate_cols_per_unit": len(gate_cols),
         "gates_per_unit": n_gates, "overlap_tracks_per_gate": per_gate,
         "sdr_ref_per_unit": sdr_per_unit, "fail_modes_per_unit": per_unit},
    )


def rule_diffogd(g: Geometry, stype: str) -> Result:
    """DIFFOGD: active (interior) FTI cuts x device diff tracks cut x units."""
    notes: list[str] = []
    cuts_per_col: dict[int, int] = {}
    boundary = 0
    for cols, cells in g.fti_segs:
        cut = len(set(cells) & g.device_cells) if g.device_cells else len(cells)
        for c in cols:
            if c % g.ogd == 0:
                boundary += 1            # unit-boundary FTI (shared between units)
                continue
            cuts_per_col[c % g.ogd] = cuts_per_col.get(c % g.ogd, 0) + cut
    interior_cols = len(cuts_per_col)
    crossings = sum(cuts_per_col.values())
    if boundary:
        notes.append(f"{boundary} unit-boundary FTI column(s) excluded from active FTI count")
    n = crossings * g.units
    per_fti = crossings / interior_cols if interior_cols else 0
    formula = (f"OGD DIFF: {interior_cols} active FTI columns/unit x {per_fti:g} device diff tracks cut "
               f"by each FTI (LO diff one side, HI diff other side) = {crossings} LKG paths/unit; "
               f"x {g.arr_ogd}x{g.arr_pgd} unit cells = {n}; Scalar = 1/{n}")
    return Result(
        "DIFFOGD", stype, n or None, formula, notes,
        {"interior_fti_cols_per_unit": interior_cols, "device_cells": sorted(g.device_cells),
         "fti_track_cuts_per_unit": crossings},
    )


def _stacked_runs(g: Geometry) -> list[list[int]]:
    """Runs of adjacent active VG columns sharing the same VG track (stacked gates)."""
    runs: list[list[int]] = []
    for c in sorted(g.vg_cols):
        if runs and c == runs[-1][-1] + 1 and g.vg_cols[c] == g.vg_cols[runs[-1][-1]]:
            runs[-1].append(c)
        else:
            runs.append([c])
    return runs


def rule_diffchn(g: Geometry, name: str, stype: str) -> Result:
    """DIFFCHN: every active VG gate in the chain is one transistor in series.

    The ``LENnn`` token is only the nominal chain length; stacked variants
    (``STKx1``, ``1FC``) carry more active gates than the token says, so the
    count comes from the buildsheet VG columns (``vg`` segments).
    """
    notes: list[str] = []
    m = _LEN_RE.search(name)
    nominal = int(m.group(1)) if m else None
    n_gates = len(g.vg_cols)
    runs = _stacked_runs(g)
    stacked = [r for r in runs if len(r) > 1]
    if n_gates == 0:
        if nominal is None:
            return Result("DIFFCHN", stype, None, "no active VG and no LEN token",
                          ["cannot determine chain length"])
        notes.append("no vg in VcxPatternUnit; LEN token used")
        n_gates = nominal
    off_track = [c for c, t in g.vg_cols.items() if not (t & (g.hi_tracks | g.lo_tracks))]
    if off_track:
        notes.append(f"{len(off_track)} VG column(s) not on a LO1/HI1 track")
    if nominal is not None and nominal != n_gates:
        notes.append(f"name says LEN{nominal} but {n_gates} active VG gates in series (stacked)")
    n = n_gates * g.units
    if g.units > 1:
        notes.append(f"{g.units} chains stacked in a {g.arr_ogd}x{g.arr_pgd} array (multi-instance); "
                     "every transistor site is counted")
    stack_txt = (f" as {len(stacked)} stacked groups of {len(stacked[0])}" if stacked and
                 len({len(r) for r in stacked}) == 1 else (f"; {len(stacked)} stacked groups" if stacked else ""))
    chains = f"; x {g.arr_ogd}x{g.arr_pgd} chains in the array = {n}" if g.units > 1 else f" = {n}"
    formula = (f"CHN: {n_gates} active VG gates in series{stack_txt} (name LEN{nominal}) "
               f"= {n_gates} transistors in the current path{chains}; Scalar = 1/{n}")
    return Result(
        "DIFFCHN", stype, n or None, formula, notes,
        {"chain_len": nominal, "active_vg_gates": n_gates, "stacked_groups": len(stacked),
         "vg_cols": sorted(g.vg_cols)},
    )


def compute(row: dict[str, str]) -> Result:
    name = (row.get("StructureName") or "").strip()
    fam = family_of(name)
    stype = type_of(name)
    if fam == "VIA_CHAIN":
        return Result(fam, stype, None, "", ["via chain -- out of scope"])
    if fam == "UNKNOWN":
        return Result(fam, stype, None, "", ["unrecognised structure family"])
    g = geometry_from_row(row)
    if fam == "CTG":
        return rule_ctg(g, stype)
    if fam == "DIFFOGD":
        return rule_diffogd(g, stype)
    if fam == "DIFFCHN":
        return rule_diffchn(g, name, stype)
    return rule_ete(g, fam, stype)


# ---------------------------------------------------------------------------
# Variant grouping -- rows that share the same scalar-relevant geometry
# ---------------------------------------------------------------------------
VARIANT_COLS = (
    "UnitCellSizeOgd", "UnitCellSizePgd", "ArraySizeOgd", "ArraySizePgd",
    "FTITracksUnit", "PolyPlugsUnit", "TcnPlugsUnit", "VcxPatternUnit",
    "LO1ActiveTracks", "HI1ActiveTracks", "DiffPatternUnit",
)


def generic_name(name: str) -> str:
    """Collapse the per-row detail of a StructureName into its design pattern.

    ``CTG110-NZ1-H-A:PLYFTCN:OGD:CD:N2`` -> ``CTG-<MOS>-A:PLYFTCN:OGD:CD:<SKEW>``
    """
    n = re.sub(r"^([A-Z]+)\d+", r"\1", name)
    n = re.sub(r"-(NP|N|P)(Z\d+|\d+)-(UL|U|L|S|H|E)-", r"-<MOS>-", n)
    n = re.sub(r":(N|P)\d+$", ":<SKEW>", n)
    n = re.sub(r"LEN\d+", "LEN<n>", n)
    return n


def variant_key(row: dict[str, str], res: Result) -> tuple:
    def norm(c: str) -> str:
        v = re.sub(r"\s+", "", row.get(c, "") or "")
        v = re.sub(r"\|[-0-9.,]+", "|<shift>", v)                 # skew magnitudes
        if c == "DiffPatternUnit":                                 # keep cells + polarity only
            v = re.sub(r",\d+\.\d+,([np])\w*", r",<W>,\1", v)
        return v
    return (res.family, res.stype, generic_name(row.get("StructureName", "")),
            tuple(norm(c) for c in VARIANT_COLS))


def structure_name_issues(rows: list[dict[str, str]], results: list[Result]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    name_pattern = re.compile(r"^[A-Z]+(?P<height>[0-9]+)-(?P<mos>NP|N|P)(?:Z?[0-9]+)-(?P<variant>UL|U|L|S|H|E)-")
    by_name: dict[str, list[tuple[dict[str, str], tuple]]] = {}

    def add(row: dict[str, str], check: str, expected: str, observed: str, note: str) -> None:
        issues.append({
            "TestRow": row.get("TestRow", ""),
            "StructureName": row.get("StructureName", ""),
            "Check": check,
            "Status": "REVIEW",
            "Expected": expected,
            "Observed": observed,
            "Note": note,
        })

    for row, result in zip(rows, results):
        name = row.get("StructureName", "") or ""
        family = family_of(name)
        if not name:
            add(row, "StructureName", "non-empty name", "empty", "Structure name is missing.")
            continue
        if family == "UNKNOWN":
            add(row, "FamilyPrefix", ", ".join(FAMILY_PREFIXES + VIA_CHAIN_PREFIXES), name,
                "Name does not start with a recognized structure-family prefix.")
        if family != "VIA_CHAIN":
            match = name_pattern.match(name)
            if not match:
                add(row, "NameFormat", "family + height + MOS/flavor + variant", name,
                    "Name does not match the established non-via structure-name pattern.")
            else:
                encoded_height = match.group("height")
                buildsheet_height = re.sub(r"\D", "", row.get("CellHeight", ""))
                if not buildsheet_height:
                    add(row, "CellHeight", "height encoded in StructureName", row.get("CellHeight", ""),
                        "Buildsheet CellHeight is missing or has no numeric value.")
                elif encoded_height != buildsheet_height:
                    add(row, "CellHeight", encoded_height, buildsheet_height,
                        "StructureName height token differs from buildsheet CellHeight.")
        result_signature = (result.family, result.stype, result.fail_modes, result.formula)
        by_name.setdefault(name, []).append((row, result_signature))

    for name, entries in by_name.items():
        distinct_results = {signature for _, signature in entries}
        if len(distinct_results) > 1:
            for row, _ in entries:
                add(row, "RepeatedNameCalculation", "one scalar calculation per exact name",
                    f"{len(distinct_results)} calculations", "Exact StructureName produces conflicting normalized scalar calculations.")
    return issues


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--review", type=Path, default=DEFAULT_REVIEW)
    ap.add_argument("--variants", type=Path, default=DEFAULT_VARIANTS,
                    help="CSV listing every distinct structure variant (geometry) with its derivation")
    ap.add_argument("--name-validation", type=Path, default=DEFAULT_NAME_VALIDATION,
                    help="CSV report of structure-name/buildsheet anomalies")
    ap.add_argument("--snapshots", type=Path, metavar="DIR",
                    help="render one annotated unit-cell PNG per structure variant into DIR")
    ap.add_argument("--snapshot", action="append", default=[], metavar="NAME",
                    help="also render the structure whose name contains NAME (repeatable)")
    ap.add_argument("--pptx", type=Path, metavar="FILE",
                    help="build a PowerPoint catalog (summary table + one slide per snapshot); implies --snapshots")
    ap.add_argument("--keep-snapshots", action="store_true",
                    help="keep the snapshot PNGs after the PowerPoint has been built (default: delete them)")
    ap.add_argument("--summary-pptx", type=Path, metavar="FILE",
                    help="executive-summary deck only (how it works, coverage, methodology, summary table)")
    args = ap.parse_args()
    if args.pptx and not args.snapshots:
        args.snapshots = BASE_DIR / "snapshots"

    with open(args.input, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fields = list(reader.fieldnames or [])
        rows = [dict(r) for r in reader]
    if "Scalar" not in fields:
        sys.exit("input has no 'Scalar' column")
    if FORMULA_COL not in fields:
        fields.insert(fields.index("Scalar") + 1, FORMULA_COL)

    for required in ("Test Type", "Force"):
        if required not in fields:
            fields.append(required)

    review_rows: list[dict[str, object]] = []
    summary: "OrderedDict[tuple[str, str, str], dict[str, object]]" = OrderedDict()
    variants: "OrderedDict[tuple, dict[str, object]]" = OrderedDict()
    results: list[Result] = []
    for row in rows:
        res = compute(row)
        results.append(res)
        row["Scalar"] = ",".join([res.scalar] * len(BIAS_CONDITIONS)) if res.scalar else ""
        row["Test Type"] = ",".join(TEST_TYPES) if res.scalar else ""
        row["Force"] = ",".join(BIAS_CONDITIONS) if res.scalar else ""
        row[FORMULA_COL] = res.formula if res.fail_modes else ""
        review_rows.append({
            "TestRow": row.get("TestRow", ""),
            "StructureName": row.get("StructureName", ""),
            "Family": res.family,
            "Type": res.stype,
            "Variant": generic_name(row.get("StructureName", "")),
            "FailModes": res.fail_modes if res.fail_modes else "",
            "Scalar": res.scalar,
            "Formula": res.formula,
            "Notes": " | ".join(res.notes),
            **{k: (v if not isinstance(v, list) else "/".join(map(str, v))) for k, v in res.detail.items()},
        })
        key = (row.get("TestRow", ""), res.family, res.stype)
        s = summary.setdefault(key, {"rows": 0, "fail_modes": set(), "notes": set()})
        s["rows"] += 1
        s["fail_modes"].add(res.fail_modes)
        s["notes"].update(res.notes)
        vk = variant_key(row, res)
        v = variants.setdefault(vk, {"row": row, "res": res, "rows": 0, "testrows": set(), "heights": set()})
        v["rows"] += 1
        v["testrows"].add(row.get("TestRow", ""))
        v["heights"].add(row.get("CellHeight", ""))

    name_issues = structure_name_issues(rows, results)
    with open(args.name_validation, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["TestRow", "StructureName", "Check", "Status",
                                                "Expected", "Observed", "Note"])
        writer.writeheader()
        writer.writerows(name_issues)

    with open(args.output, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    review_fields: list[str] = []
    for r in review_rows:
        for k in r:
            if k not in review_fields:
                review_fields.append(k)
    with open(args.review, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=review_fields)
        w.writeheader()
        w.writerows(review_rows)

    # one line per distinct geometry variant (what the snapshots are keyed on)
    counters: dict[tuple[str, str], int] = {}
    var_ids: dict[tuple, str] = {}
    for vk in variants:
        fam, st = vk[0], vk[1]
        counters[(fam, st)] = counters.get((fam, st), 0) + 1
        var_ids[vk] = f"{fam}-{st or 'x'}-{counters[(fam, st)]:02d}"

    snap_files: dict[str, Path] = {}
    if args.snapshots or args.snapshot:
        from scalar_snapshot import render_snapshots
        out_dir = args.snapshots or (BASE_DIR / "snapshots")
        picked: "OrderedDict[str, tuple[dict[str, str], Result, str]]" = OrderedDict()
        if args.snapshots:
            for vk, v in variants.items():
                res = v["res"]
                if res.fail_modes is None:
                    continue
                sub = (f"variant {var_ids[vk]}: {vk[2]}  ({v['rows']} rows, "
                       f"{' '.join(sorted(v['heights']))}, testrows {' '.join(sorted(v['testrows']))})")
                picked[var_ids[vk]] = (v["row"], res, sub)
        for row, res in zip(rows, results):
            if res.fail_modes is not None and any(s.lower() in row["StructureName"].lower() for s in args.snapshot):
                picked[row["StructureName"]] = (row, res, "")
        snap_files = render_snapshots(picked, out_dir)

    with open(args.variants, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["VariantId", "Snapshot", "Family", "Type", "Variant", "Rows", "TestRows", "CellHeights",
                    "ExampleStructure", "StructureComments", "FailModes", "Scalar", "Formula", "Notes",
                    *VARIANT_COLS])
        for vk, v in variants.items():
            row, res = v["row"], v["res"]
            snap = snap_files.get(var_ids[vk])
            w.writerow([var_ids[vk], snap.name if snap else "", res.family, res.stype, vk[2], v["rows"],
                        " ".join(sorted(v["testrows"])), " ".join(sorted(v["heights"])),
                        row.get("StructureName", ""), row.get("StructureComments", ""),
                        res.fail_modes or "", res.scalar, res.formula, " | ".join(res.notes),
                        *[row.get(c, "") for c in VARIANT_COLS]])

    print(f"{len(rows)} rows -> {args.output.name}, review -> {args.review.name}, "
          f"{len(variants)} variants -> {args.variants.name}, "
          f"{len(name_issues)} name/buildsheet findings -> {args.name_validation.name}\n")
    print(f"{'TestRow':<20} {'Family':<10} {'Type':<14} {'rows':>4}  {'FailModes':<22} notes")
    for (tr, fam, st), s in summary.items():
        fm = "/".join(str(v) for v in sorted(s["fail_modes"], key=lambda x: (x is None, x or 0)))
        note = "; ".join(sorted(s["notes"]))[:90]
        print(f"{tr:<20} {fam:<10} {st:<14} {s['rows']:>4}  {fm:<22} {note}")

    if snap_files:
        print(f"\n{len(snap_files)} snapshot(s) -> {args.snapshots or BASE_DIR / 'snapshots'}  "
              f"(index: {args.variants.name}, column 'Snapshot')")
    if args.pptx:
        from scalar_catalog import build_catalog
        n_slides = build_catalog(args.variants, args.snapshots, args.pptx)
        print(f"catalog: {n_slides} slides -> {args.pptx}")
        if not args.keep_snapshots:
            shutil.rmtree(args.snapshots, ignore_errors=True)
            print(f"snapshot folder removed: {args.snapshots}")
    if args.summary_pptx:
        from scalar_catalog import build_catalog
        n_slides = build_catalog(args.variants, None, args.summary_pptx, with_snapshots=False)
        print(f"summary deck: {n_slides} slides -> {args.summary_pptx}")


if __name__ == "__main__":
    main()
