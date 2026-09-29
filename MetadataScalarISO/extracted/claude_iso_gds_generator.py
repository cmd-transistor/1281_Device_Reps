"""ISO GDS/txt Generator — Dynamic Chassis+Delta Model (1281 Node, ISO Module).

Reads
-----
* ``ISO_Mini_GDS.csv``   — TRAINING corpus. MUST have a ``LayoutFiles``
                           column pointing at the chassis .txt filenames
                           under ``--training-dir``. Used to learn per-layer
                           geometry.
* ``ISO_Decoder2.csv``   — GENERATION input (any name; default = training
                           file for self-training). Rows to produce GDS
                           files for; ``LayoutFiles`` is optional here.
* ``ISO_generated.csv``  — buildsheet emitted by :mod:`claude_iso_generator`,
                           consulted for pattern strings and derived widths.
* ``ISO-GDSNamed/*.txt`` — training GDS ASCII files WITH ``# LAYERNAME``
                           comments already inserted by
                           :mod:`convert_layers_to_names`.
* ``svrf_layer_mapping.csv`` — layer/datatype → layer-name resolver used to
                               (re-)emit the ``# LAYERNAME (base;purpose)``
                               annotation on every BOUNDARY of the output.

Writes
------
* ``ISO_GDS_named/<StructureName-safe>.txt`` — one file per input decoder row,
  layer-name-annotated, ready to consume downstream.

Architecture
------------
The training corpus is small (12 rows) and the geometry is dominated by
periodic grids. A pure decoder-DSL-only generator would need
polygon-by-polygon models for eight DRAWN layers, which is not yet fully
mined. This generator instead uses a **chassis + delta** strategy:

  1. **Parse phase** — every training file is streamed into a
     :class:`LayerInventory` (BOUNDARY blocks bucketed per (layer, dt)).
  2. **Chassis pick** — for a new decoder row we pick the training file
     whose (CELLHEIGHT, MOS, TYPE) tuple best matches the target.
  3. **Transform phase** — a small pluggable registry of transforms is
     invoked; each transform inspects the decoder row + buildsheet row +
     chassis polygons and returns a rewritten polygon list.
     * ``diff_devwidth_transform`` (VERIFIED) rescales NDIFF/PDIFF polygon
       heights to match the target DEVWIDTH (height_db == devwidth_nm * 10).
     * Additional transforms (``fti_pattern_transform``,
       ``poly_plugs_transform``, ``tcn_plugs_transform``,
       ``vcx_pattern_transform``, ``m0cut_pattern_transform``) are stubbed
       with the decoder-DSL parsing plumbed through :mod:`claude_iso_generator`
       (via ``_expand_pattern``) — they currently pass polygons through
       unchanged and log the intended column set for verification.
  4. **Emit phase** — chassis is streamed to output; every parametric
     layer's BOUNDARY blocks are replaced with the transform output;
     STRNAME and header timestamps are rewritten; every BOUNDARY gets a
     ``# LAYERNAME (base;purpose)`` annotation from the layer map.

Adding a new pattern-driven layer transform
-------------------------------------------
1. Write a function ``def my_transform(ctx: TransformCtx, polys:
   list[Polygon]) -> list[Polygon]`` in the TRANSFORMS section below.
2. Append ``(target_layer_key, my_transform)`` to :data:`LAYER_TRANSFORMS`.
No plumbing changes required — the emit phase auto-invokes it.

CLI
---
::

    python claude_iso_gds_generator.py \\
        --training-decoder ISO_Mini_GDS.csv \\
        --decoder ISO_Decoder2.csv \\
        --buildsheet ISO_generated.csv \\
        --training-dir ISO-GDSNamed \\
        --output-dir ISO_GDS_named \\
        --layer-map svrf_layer_mapping.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

# The decoder DSL expander already ships with the buildsheet generator; we
# reuse it so decoder pattern columns feed the transform layer verbatim.
try:  # pragma: no cover — importlib guard for CLI use.
    from claude_iso_generator import _expand_pattern as _decoder_expand
except Exception:  # pragma: no cover
    _decoder_expand = None  # type: ignore[assignment]


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DECODER = BASE_DIR / "ISO_Mini_GDS.csv"
DEFAULT_TRAINING_DECODER = BASE_DIR / "ISO_Mini_GDS.csv"
DEFAULT_BUILDSHEET = BASE_DIR / "ISO_generated.csv"
DEFAULT_TRAINING = BASE_DIR / "ISO-GDSNamed"
DEFAULT_OUTPUT = BASE_DIR / "ISO_GDS_named"
DEFAULT_LAYER_MAP = BASE_DIR / "svrf_layer_mapping.csv"


# ---------------------------------------------------------------------------
# Layers of interest — the eight parametric DRAWN layers the user called out,
# expanded to nine because M0 splits into two color layers in this node.
# ---------------------------------------------------------------------------
LAYER_KEYS: dict[tuple[int, int], str] = {
    (1, 0):   "NDIFFDRAWN",
    (2, 0):   "POLYDRAWN",
    (5, 0):   "TCNDRAWN",
    (8, 0):   "PDIFFDRAWN",
    (31, 0):  "VTDRAWN",
    (32, 0):  "VGDRAWN",
    (55, 0):  "M0DRAWN",       # not present in current training set — see docs
    (120, 0): "M0CLR1DRAWN",   # M0 color 1 (real M0 stripes)
    (184, 0): "FTIDRAWN",
    (241, 0): "M0CLR2DRAWN",   # M0 color 2
}

# Diffusion layers are the only ones whose height encodes DEVWIDTH.
DIFF_LAYERS: frozenset[tuple[int, int]] = frozenset({(1, 0), (8, 0)})

# 1 database unit = 0.1 nm, so a width in nm scales by 10 to db.
_DB_PER_NM = 10


# ---------------------------------------------------------------------------
# GDS ASCII parsing
# ---------------------------------------------------------------------------

_LAYER_RE = re.compile(r"^\s*LAYER\s+(\d+)\s*$")
_DT_RE = re.compile(r"^\s*DATATYPE\s+(\d+)\s*$")
_STRNAME_RE = re.compile(r"^\s*STRNAME\s+(\S+)\s*$")
_BGN_RE = re.compile(r"^\s*(BGNLIB|BGNSTR)\s+")
# ``convert_layers_to_names.py`` writes lines like
#   # NDIFFDRAWN (ndiff;drawing)
# directly above each BOUNDARY. When the input chassis already carries
# these comments we must NOT re-emit them, otherwise the output ends up
# with duplicates.
_LAYER_COMMENT_RE = re.compile(r"^\s*#\s*[A-Z0-9_]+\s*\([^)]+\)\s*$")


@dataclass
class Polygon:
    """One BOUNDARY block from the GDS ASCII text."""
    layer: int
    datatype: int
    xy_lines: list[str] = field(default_factory=list)  # raw XY body lines

    @property
    def key(self) -> tuple[int, int]:
        return (self.layer, self.datatype)

    def coords(self) -> list[tuple[int, int]]:
        pts: list[tuple[int, int]] = []
        for line in self.xy_lines:
            # strip leading "XY " on the first line
            s = line.strip()
            if s.upper().startswith("XY"):
                s = s[2:].strip()
            for m in re.finditer(r"(-?\d+)\s*:\s*(-?\d+)", s):
                pts.append((int(m.group(1)), int(m.group(2))))
        return pts

    def bbox(self) -> tuple[int, int, int, int]:
        pts = self.coords()
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)

    def set_rectangle(self, x0: int, y0: int, x1: int, y1: int) -> None:
        """Rewrite ``xy_lines`` for an axis-aligned rectangle.

        The training corpus writes rectangles as five points (closed ring)
        with the first line prefixed by ``XY``. We match that format exactly
        so downstream tooling sees byte-similar output.
        """
        pts = [(x0, y0), (x0, y1), (x1, y1), (x1, y0), (x0, y0)]
        lines = [f"XY {pts[0][0]}: {pts[0][1]}"]
        for p in pts[1:]:
            lines.append(f"{p[0]}: {p[1]}")
        self.xy_lines = lines


@dataclass
class LayerInventory:
    """All BOUNDARY blocks grouped by (layer, datatype)."""
    by_layer: dict[tuple[int, int], list[Polygon]] = field(default_factory=dict)

    def add(self, poly: Polygon) -> None:
        self.by_layer.setdefault(poly.key, []).append(poly)


@dataclass
class ChassisFile:
    """Streamed representation of a training file.

    ``line_events`` is a lightweight replay list of tuples:
      * ``('text', str)``  — verbatim line to emit.
      * ``('poly', key)``  — placeholder; emitter substitutes current polygon
        list for that (layer, datatype).

    This lets us mutate polygon lists per layer without rewriting the entire
    file structure.
    """
    header_lines: list[str]
    line_events: list[tuple[str, object]]
    inventory: LayerInventory
    strname: str
    source_path: Path


# ---------------------------------------------------------------------------
# Layer name resolution
# ---------------------------------------------------------------------------

@dataclass
class LayerInfo:
    layer_name: str
    base_layer: str
    purpose: str

    @property
    def comment(self) -> str:
        return f"# {self.layer_name} ({self.base_layer};{self.purpose})"


def load_layer_map(path: Path) -> dict[tuple[int, int], LayerInfo]:
    m: dict[tuple[int, int], LayerInfo] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try:
                key = (int(row["gds_layer"]), int(row["datatype"]))
            except (KeyError, ValueError):
                continue
            m[key] = LayerInfo(
                layer_name=row.get("layer_name", "").strip(),
                base_layer=row.get("base_layer", "").strip(),
                purpose=row.get("purpose", "").strip(),
            )
    return m


# ---------------------------------------------------------------------------
# Chassis parser
# ---------------------------------------------------------------------------

def parse_chassis(path: Path) -> ChassisFile:
    """Parse a training GDS ASCII file into a replayable chassis.

    We deliberately keep raw lines for non-parametric content and split out
    BOUNDARY blocks so the emit phase can substitute them by layer.
    """
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    header: list[str] = []
    events: list[tuple[str, object]] = []
    inventory = LayerInventory()
    strname = ""
    # Track which (layer, dt) keys have already been "reserved" (placeholder
    # emitted); further polygons of same key are folded into that slot.
    reserved: set[tuple[int, int]] = set()

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        m_str = _STRNAME_RE.match(line)
        if m_str:
            strname = m_str.group(1)

        # Detect BOUNDARY start.
        if stripped == "BOUNDARY" and i + 2 < n:
            m_l = _LAYER_RE.match(lines[i + 1])
            m_d = _DT_RE.match(lines[i + 2])
            if m_l and m_d:
                layer = int(m_l.group(1))
                dt = int(m_d.group(1))
                key = (layer, dt)

                # Drop a preceding "# LAYERNAME (...)" comment we already
                # queued as pass-through — the emitter will rebuild it.
                if events and events[-1][0] == "text":
                    prev = events[-1][1]
                    if isinstance(prev, str) and _LAYER_COMMENT_RE.match(prev):
                        events.pop()
                        if header and header[-1] == prev:
                            header.pop()

                # Consume XY body up to ENDEL.
                j = i + 3
                xy_body: list[str] = []
                while j < n and lines[j].strip() != "ENDEL":
                    xy_body.append(lines[j])
                    j += 1
                # j now points to ENDEL (or EOF)
                poly = Polygon(layer=layer, datatype=dt, xy_lines=xy_body)
                inventory.add(poly)

                if key not in reserved:
                    events.append(("poly", key))
                    reserved.add(key)
                # else: this polygon has been folded into the earlier slot.

                i = j + 1  # skip past ENDEL
                continue

        events.append(("text", line))
        # Header cutoff: capture lines up to and including STRNAME for header
        # rewriting later.
        if not header or (header and _STRNAME_RE.match(header[-1]) is None):
            header.append(line)
        i += 1

    return ChassisFile(
        header_lines=header,
        line_events=events,
        inventory=inventory,
        strname=strname,
        source_path=path,
    )


# ---------------------------------------------------------------------------
# Chassis picker — match target row to a training file.
# ---------------------------------------------------------------------------

_TYPE_RE = re.compile(r"-([A-D])(?::|$)")


def _extract_type(structure_name: str) -> str:
    m = _TYPE_RE.search(structure_name)
    return m.group(1).upper() if m else ""


def _pick_chassis(
    target: dict[str, str],
    corpus: dict[str, ChassisFile],
    decoder_rows: dict[str, dict[str, str]],
) -> tuple[str, ChassisFile]:
    """Return (LayoutFiles-name, ChassisFile) best matching target row.

    Priority:
      1. Exact ``StructureName`` match.
      2. Same (CELLHEIGHT, MOS, TYPE) tuple.
      3. Same CELLHEIGHT.
      4. Any file (first one).
    """
    tname = target.get("StructureName", "")
    tch = target.get("CELLHEIGHT", "")
    tmos = target.get("MOS", "")
    ttype = _extract_type(tname)

    # 1: identical StructureName
    for lf, drow in decoder_rows.items():
        if drow.get("StructureName") == tname and lf in corpus:
            return lf, corpus[lf]

    # 2: (CH, MOS, TYPE)
    for lf, drow in decoder_rows.items():
        if (
            drow.get("CELLHEIGHT") == tch
            and drow.get("MOS") == tmos
            and _extract_type(drow.get("StructureName", "")) == ttype
            and lf in corpus
        ):
            return lf, corpus[lf]

    # 3: same CELLHEIGHT
    for lf, drow in decoder_rows.items():
        if drow.get("CELLHEIGHT") == tch and lf in corpus:
            return lf, corpus[lf]

    # 4: any
    lf = next(iter(corpus))
    return lf, corpus[lf]


# ---------------------------------------------------------------------------
# Transform context — everything a transform needs on one object.
# ---------------------------------------------------------------------------

@dataclass
class TransformCtx:
    decoder_row: dict[str, str]         # target row from ISO_Mini_GDS.csv
    buildsheet_row: dict[str, str]      # matching row from ISO_generated.csv
    chassis_decoder_row: dict[str, str]  # decoder row of the chassis file
    chassis: ChassisFile

    # convenient parsed integers ----------------------------------------
    @property
    def target_devwidth_nm(self) -> int:
        return int(self.decoder_row.get("DEVWIDTH", "0") or 0)

    @property
    def chassis_devwidth_nm(self) -> int:
        return int(self.chassis_decoder_row.get("DEVWIDTH", "0") or 0)

    @property
    def target_ogd(self) -> int:
        return int(self.decoder_row.get("UnitCellSizeOgd", "0") or 0)

    @property
    def target_pgd(self) -> int:
        return int(self.decoder_row.get("UnitCellSizePgd", "0") or 0)

    @property
    def target_array_ogd(self) -> int:
        return int(self.decoder_row.get("ArraySizeOgd", "0") or 0)

    @property
    def target_array_pgd(self) -> int:
        return int(self.decoder_row.get("ArraySizePgd", "0") or 0)

    @property
    def target_cellheight_db(self) -> int:
        return int(self.decoder_row.get("CELLHEIGHT", "0") or 0) * _DB_PER_NM

    # Buffer sizes are on the buildsheet row (auto-discovered constants).
    @property
    def buffer_l(self) -> int:
        return int(self.buildsheet_row.get("BufferSizeL", "4") or 4)

    @property
    def buffer_r(self) -> int:
        return int(self.buildsheet_row.get("BufferSizeR", "4") or 4)

    @property
    def buffer_b(self) -> int:
        return int(self.buildsheet_row.get("BufferSizeB", "1") or 1)

    @property
    def buffer_t(self) -> int:
        return int(self.buildsheet_row.get("BufferSizeT", "1") or 1)

    @property
    def total_ogd_extent(self) -> int:
        """Total number of poly-column slots across the padded array."""
        return self.buffer_l + self.target_array_ogd * self.target_ogd + self.buffer_r


# ---------------------------------------------------------------------------
# Verified geometry constants (from discovery pass; see module docstring).
# ---------------------------------------------------------------------------

# Poly / FTI stripe base geometry.
_POLY_X_ORIGIN_DB = 160        # X_left of poly column 0 in database units
_POLY_PITCH_DB = 440           # 44 nm pitch
_POLY_CD_DB = 120              # 12 nm poly critical dimension (== stripe width)


def _poly_col_x_left(col: int) -> int:
    """Absolute X of the LEFT edge of poly column ``col`` (db)."""
    return _POLY_X_ORIGIN_DB + col * _POLY_PITCH_DB


def _poly_col_x_right(col: int) -> int:
    """Absolute X of the RIGHT edge of poly column ``col`` (db)."""
    return _poly_col_x_left(col) + _POLY_CD_DB


# ---------------------------------------------------------------------------
# TRANSFORMS
# ---------------------------------------------------------------------------

TransformFn = Callable[[TransformCtx, list[Polygon]], list[Polygon]]


# --- DSL helpers: resolve buildsheet positions into concrete column indices --

_R_TOKEN_RE = re.compile(r"^r(\d+)$", re.IGNORECASE)
_STRIDE_RE = re.compile(r"^(?P<start>-?\d+)\s*:\s*(?P<end>[^\[]+)\[(?P<stride>\d+)\]$")


def _resolve_r_token(tok: str, extent: int) -> int:
    """Resolve a buildsheet ``rN`` end-relative token into an absolute int.

    ``r0`` -> ``extent``; ``r2`` -> ``extent - 2``; etc. Plain integers pass
    through. ``extent`` is the total number of positions along the axis
    (e.g. ``total_ogd_extent`` for X, ``array_pgd × unit_pgd`` for Y-cell).
    """
    tok = tok.strip()
    m = _R_TOKEN_RE.match(tok)
    if m:
        return extent - int(m.group(1))
    return int(tok)


def _expand_axis_positions(axis: str, extent: int) -> list[int]:
    """Expand a buildsheet-format axis expression into concrete indices.

    Supports (per ``claude_iso_generator._expand_axis``):
      * ``"a"``                    -> [a]
      * ``"a:b"``                  -> [a, a+1, ..., b]
      * ``"a:b[N]"``               -> [a, a+N, a+2N, ...] up to (and
                                       possibly including) ``b``
      * ``"a1/a2/..."``            -> concatenation of each fragment
      * ``rK`` tokens              -> ``extent - K``
    """
    axis = axis.strip()
    if not axis:
        return []
    if "/" in axis:
        out: list[int] = []
        for frag in axis.split("/"):
            out.extend(_expand_axis_positions(frag, extent))
        return out
    m = _STRIDE_RE.match(axis)
    if m:
        start = _resolve_r_token(m.group("start"), extent)
        end = _resolve_r_token(m.group("end"), extent)
        stride = int(m.group("stride"))
        # Empirical: stride N in ``a:b[N]`` means DSL "Skip N" -> the
        # PHYSICAL step is N + 1 (skip N cols between marks).
        step = stride + 1
        out = []
        cur = start
        while cur <= end:
            out.append(cur)
            cur += step
        return out
    if ":" in axis:
        a, b = axis.split(":", 1)
        lo = _resolve_r_token(a, extent)
        hi = _resolve_r_token(b, extent)
        if lo > hi:
            lo, hi = hi, lo
        return list(range(lo, hi + 1))
    # Bare literal.
    return [_resolve_r_token(axis, extent)]


def _iter_segments(pattern: str) -> list[str]:
    """Split a ``segA;segB;...`` buildsheet pattern into non-empty segments."""
    return [s.strip() for s in pattern.split(";") if s.strip()]


# --- FTI ---------------------------------------------------------------------

def _fti_columns(ctx: TransformCtx) -> tuple[list[int], set[int]]:
    """Return ``(all_cols_sorted, boundary_cols)`` for the target row.

    Boundary cols get the full-height Y span (BufferSizeB + array + BufferSizeT
    unit-cell heights); the rest get the interior span.

    Column-selection is driven by TARGET decoder ``FTIPattern`` (expanded
    with target ``UnitCellSizeOgd`` / ``UnitCellSizePgd`` via the shared
    DSL) when available, so the model tracks decoder edits directly. It
    falls back to the buildsheet's ``FTITracksUnit`` / ``FTITracksGlobal``
    when the decoder cell is empty (buildsheet-only round-trip case).
    """
    extent = ctx.total_ogd_extent
    unit = ctx.target_ogd
    array = ctx.target_array_ogd
    bl = ctx.buffer_l

    # Preferred source: raw decoder FTIPattern re-expanded with TARGET
    # Ogd/Pgd. When a new dataset overrides UnitCellSizeOgd the buildsheet
    # (generated from a different Ogd) becomes stale for this column list,
    # so we always start from the decoder's DSL when we can.
    dec_pattern = ctx.decoder_row.get("FTIPattern", "").strip()
    if dec_pattern and _decoder_expand is not None:
        try:
            expanded = _decoder_expand(dec_pattern, ctx.target_ogd, ctx.target_pgd)
        except Exception:
            expanded = ""
        # ``_expand_pattern`` returns e.g. ``"0:12[3],0:r0"`` for a
        # single-segment FTI unit-cell pattern. Global positions come from
        # the buildsheet since decoder doesn't carry FTITracksGlobal
        # explicitly.
        fti_unit = expanded or ctx.buildsheet_row.get("FTITracksUnit", "").strip()
    else:
        fti_unit = ctx.buildsheet_row.get("FTITracksUnit", "").strip()
    fti_global = ctx.buildsheet_row.get("FTITracksGlobal", "").strip()

    all_cols: set[int] = set()
    boundary: set[int] = set()

    # ---- unit-driven FTIs (interior) --------------------------------------
    if fti_unit:
        # The X-axis of an FTI segment is the first comma-separated token.
        x_axis = fti_unit.split(",", 1)[0].strip()
        offsets_within_unit: list[int] = []
        m = _STRIDE_RE.match(x_axis)
        if m:
            start = int(m.group("start"))
            # The DSL end (e.g. ``0:12[3]`` -> 12 == UnitCellSizeOgd) is the
            # unit-cell size and is INCLUSIVE. When the stride divides the
            # unit evenly (Ogd=12, step 4) the endpoint lands on the grid and
            # produces the closing FTI that is shared with the next unit /
            # forms the array's right-edge column (col = BufferL +
            # ArraySizeOgd*Ogd). When it does not divide evenly (Ogd=30,
            # step 7) the endpoint is never hit, so no closing FTI is drawn
            # — matching the training corpus exactly.
            end = _resolve_r_token(m.group("end").strip(), unit)
            step = int(m.group("stride")) + 1
            off = start
            while off <= end:
                offsets_within_unit.append(off)
                off += step
        else:
            # Non-stride form (rare for FTITracksUnit) — fall back to the
            # generic expander applied against the unit extent.
            offsets_within_unit = _expand_axis_positions(x_axis, unit)

        for u in range(array):
            base = bl + u * unit
            for off in offsets_within_unit:
                col = base + off
                if 0 <= col <= extent:
                    all_cols.add(col)

    # ---- global (boundary) FTIs -------------------------------------------
    if fti_global:
        x_axis = fti_global.split(",", 1)[0].strip()
        # Global uses ``0/2/r2/r0`` — plain fragment list with rN tokens.
        for pos in _expand_axis_positions(x_axis, extent):
            if 0 <= pos <= extent:
                all_cols.add(pos)
                boundary.add(pos)

    return sorted(all_cols), boundary


def _is_column_fti(p: Polygon, ch_db: int, buffer_b: int, buffer_t: int,
                   array_pgd: int, unit_pgd: int) -> bool:
    """A column-FTI stripe spans the full array height (interior) or the
    full array plus B/T buffers (boundary). Everything else — short in-cell
    FTIs, gate FTIs, etc. — is left untouched by the transform.
    """
    _, y0, _, y1 = p.bbox()
    h = y1 - y0
    interior_h = array_pgd * unit_pgd * ch_db
    boundary_h = interior_h + (buffer_b + buffer_t) * ch_db
    return h == interior_h or h == boundary_h


def _fti_y_axis(ctx: TransformCtx) -> str:
    """Return the Y-axis token of the FTI unit pattern (the part after the
    first comma of ``FTITracksUnit`` / the expanded decoder ``FTIPattern``).

    ``0:r0`` / ``All`` means the FTI stripe spans the full array height;
    anything else (e.g. ``2:5``) means SHORT per-Pgd-unit segments.
    """
    dec_pattern = ctx.decoder_row.get("FTIPattern", "").strip()
    fti_unit = ""
    if dec_pattern and _decoder_expand is not None:
        try:
            fti_unit = _decoder_expand(dec_pattern, ctx.target_ogd, ctx.target_pgd)
        except Exception:
            fti_unit = ""
    if not fti_unit:
        fti_unit = ctx.buildsheet_row.get("FTITracksUnit", "").strip()
    if "," not in fti_unit:
        return ""
    return fti_unit.split(",", 1)[1].strip()


def _fti_is_full_height(y_axis: str) -> bool:
    """True when the FTI Y-axis means full array height (``0:r0`` / ``All``)."""
    y = y_axis.strip().lower()
    return y in ("", "0:r0", "all")


def _emit_full_fti(ctx: TransformCtx, cols: list[int],
                   boundary: set[int]) -> list[Polygon]:
    """Analytically emit full-height FTI column stripes.

    Interior columns span ``[BufferB*CH, (BufferB + ArrayPgd*UnitPgd)*CH]``;
    boundary columns extend to ``[0, that + BufferT*CH]``. Derived purely from
    the decoder/buildsheet dimensions so a change of the FTI Y-axis to ``All``
    is reflected immediately.
    """
    ch = ctx.target_cellheight_db
    interior_y0 = ctx.buffer_b * ch
    interior_y1 = interior_y0 + ctx.target_array_pgd * ctx.target_pgd * ch
    boundary_y1 = interior_y1 + ctx.buffer_t * ch
    out: list[Polygon] = []
    for col in cols:
        y0, y1 = (0, boundary_y1) if col in boundary else (interior_y0, interior_y1)
        x0 = _poly_col_x_left(col)
        p = Polygon(184, 0, [])
        p.set_rectangle(x0, y0, x0 + _POLY_CD_DB, y1)
        out.append(p)
    return out


def _emit_short_fti(ctx: TransformCtx, cols: list[int], boundary: set[int],
                    y_axis: str) -> list[Polygon] | None:
    """Analytically emit SHORT (per-Pgd-unit) FTI stripes from the Y-axis.

    Verified model (chassis inst2, ``FTITracksUnit`` Y == ``2:5``): each
    interior FTI column carries one segment per Pgd unit spanning Y-cells
    ``[a + 2*BufferB, b + 1 + 2*BufferB]`` (with ``y_cell = CELLHEIGHT/2``),
    tiled every ``2*UnitCellSizePgd`` cells across ``ArraySizePgd`` units.
    Boundary columns stay full-height. Returns ``None`` for Y-axis forms this
    simple model does not cover (e.g. multi-fragment ``a/b-bottom/...``), so
    the caller can fall back to chassis donation.
    """
    per_unit = 2 * ctx.target_pgd
    if per_unit <= 0 or ("/" in y_axis) or ("-" in y_axis):
        return None
    positions = _expand_axis_positions(y_axis, per_unit)
    if not positions:
        return None
    a, b = min(positions), max(positions)

    ch = ctx.target_cellheight_db
    y_cell = ch // 2
    bottom_cell = a + 2 * ctx.buffer_b
    top_cell = b + 1 + 2 * ctx.buffer_b

    interior_y0 = ctx.buffer_b * ch
    interior_y1 = interior_y0 + ctx.target_array_pgd * ctx.target_pgd * ch
    boundary_y1 = interior_y1 + ctx.buffer_t * ch

    out: list[Polygon] = []
    for col in cols:
        x0 = _poly_col_x_left(col)
        if col in boundary:
            p = Polygon(184, 0, [])
            p.set_rectangle(x0, 0, x0 + _POLY_CD_DB, boundary_y1)
            out.append(p)
        else:
            for u in range(ctx.target_array_pgd):
                y0 = (bottom_cell + u * per_unit) * y_cell
                y1 = (top_cell + u * per_unit) * y_cell
                p = Polygon(184, 0, [])
                p.set_rectangle(x0, y0, x0 + _POLY_CD_DB, y1)
                out.append(p)
    return out


def fti_pattern_transform(ctx: TransformCtx, polys: list[Polygon]) -> list[Polygon]:
    """Regenerate FTIDRAWN from the TARGET decoder-derived X columns.

    Two axes, decoupled:

      * **X (columns) — target-driven.** Column positions come from the
        target decoder ``FTIPattern`` (re-expanded with target Ogd) plus
        ``FTITracksGlobal`` for the array boundary, via :func:`_fti_columns`.

      * **Y (segments) — DERIVED from the FTI Y-axis.** ``0:r0`` / ``All`` ->
        analytical FULL height (:func:`_emit_full_fti`); ``a:b`` -> analytical
        SHORT per-Pgd-unit segments (:func:`_emit_short_fti`). Both are derived
        from the decoder/buildsheet dimensions, so editing the FTI Y-axis is
        reflected immediately. Only Y-axis forms the analytical model does not
        cover fall back to copying the chassis per-column Y-segments.
    """
    cols, boundary = _fti_columns(ctx)
    if not cols:
        return polys
    cols = sorted(cols)

    y_axis = _fti_y_axis(ctx)
    if _fti_is_full_height(y_axis):
        return _emit_full_fti(ctx, cols, boundary)

    short = _emit_short_fti(ctx, cols, boundary, y_axis)
    if short is not None:
        return short

    # Fallback: donate the chassis per-column Y-segments (Y-axis forms not
    # covered by the analytical short model).
    chassis_by_col: dict[int, list[tuple[int, int]]] = {}
    for p in polys:
        x0, y0, x1, y1 = p.bbox()
        c = round((x0 - _POLY_X_ORIGIN_DB) / _POLY_PITCH_DB)
        chassis_by_col.setdefault(c, []).append((y0, y1))
    chassis_cols = sorted(chassis_by_col)

    n_t = len(cols)
    n_c = len(chassis_cols)
    if n_c == 0:
        return _emit_full_fti(ctx, cols, boundary)

    def remap(i: int) -> int:
        j = i if i < n_t / 2 else n_c - (n_t - i)
        return max(0, min(n_c - 1, int(j)))

    out: list[Polygon] = []
    for i, col in enumerate(cols):
        cc = chassis_cols[remap(i)]
        x0 = _poly_col_x_left(col)
        x1 = x0 + _POLY_CD_DB
        for (cy0, cy1) in chassis_by_col[cc]:
            p = Polygon(184, 0, [])
            p.set_rectangle(x0, cy0, x1, cy1)
            out.append(p)
    return out


# --- Diffusion (NDIFF / PDIFF) ----------------------------------------------

# devflav prefix -> layer key for the DRAWN diffusion.
_DEVFLAV_TO_LAYER: dict[str, tuple[int, int]] = {"n": (1, 0), "p": (8, 0)}


def _contiguous_runs(sorted_ints: list[int]) -> list[list[int]]:
    """Split a sorted list of ints into maximal contiguous runs.

    ``[0,1,2,5,6]`` -> ``[[0,1,2],[5,6]]``. Used to distinguish an interior
    "full" diffusion row (one long run -> dense fill) from an edge row
    (several short runs pinned to the array edges).
    """
    runs: list[list[int]] = []
    for v in sorted_ints:
        if runs and v == runs[-1][-1] + 1:
            runs[-1].append(v)
        else:
            runs.append([v])
    return runs


def _merge_regions(bars: list[tuple[int, int]],
                   poly_cd: int = _POLY_CD_DB) -> list[tuple[int, int]]:
    """Merge diffusion bars separated by exactly one FTI-column width into
    continuous regions.

    A gap of exactly ``poly_cd`` (120 db) between adjacent bars means they
    are the SAME continuous diffusion region cut by an FTI column; a larger
    gap is a genuine N/P boundary. Reconstructing the region lets the NP
    model re-cut it at the TARGET FTI grid.
    """
    if not bars:
        return []
    bars = sorted(bars)
    regions: list[list[int]] = [list(bars[0])]
    for x0, x1 in bars[1:]:
        if x0 - regions[-1][1] == poly_cd:
            regions[-1][1] = x1
        else:
            regions.append([x0, x1])
    return [(a, b) for a, b in regions]


def _recut_region(S: int, E: int,
                  footprints: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Cut a continuous region ``[S,E]`` at the FTI-column footprints that
    fall inside it. Each footprint ``(col_left, col_right)`` that overlaps the
    region interior removes that slice; the surviving pieces are the drawn
    diffusion segments.
    """
    removes: list[tuple[int, int]] = []
    for cl, cr in footprints:
        if cr > S and cl < E:
            removes.append((max(cl, S), min(cr, E)))
    removes.sort()
    segs: list[tuple[int, int]] = []
    cur = S
    for rl, rr in removes:
        if rl > cur:
            segs.append((cur, rl))
        cur = max(cur, rr)
    if cur < E:
        segs.append((cur, E))
    return segs


def _target_fti_segments(ctx: TransformCtx) -> dict[int, list[tuple[int, int]]]:
    """Return ``{target_col_value: [(y0,y1), ...]}`` for the generated FTI —
    the same column/Y-segment assignment :func:`fti_pattern_transform`
    produces (chassis Y-segments mapped onto target columns).
    """
    cols = sorted(_fti_columns(ctx)[0])
    chassis_by_col: dict[int, list[tuple[int, int]]] = {}
    for p in ctx.chassis.inventory.by_layer.get((184, 0), []):
        x0, y0, x1, y1 = p.bbox()
        c = round((x0 - _POLY_X_ORIGIN_DB) / _POLY_PITCH_DB)
        chassis_by_col.setdefault(c, []).append((y0, y1))
    chassis_cols = sorted(chassis_by_col)
    n_t = len(cols)
    n_c = len(chassis_cols)
    if n_c == 0:
        return {}

    def remap(i: int) -> int:
        j = i if i < n_t / 2 else n_c - (n_t - i)
        return max(0, min(n_c - 1, int(j)))

    return {col: chassis_by_col[chassis_cols[remap(i)]]
            for i, col in enumerate(cols)}


def _parse_diff_segment(segment: str) -> tuple[str, str, float, str] | None:
    """Return ``(x_axis, y_axis, width_um, devflav)`` for a segment.

    Buildsheet segment format: ``X,Y,W,devflav`` where devflav is
    e.g. ``nsvt``, ``phvt``, etc.
    """
    parts = [p.strip() for p in segment.split(",")]
    if len(parts) != 4:
        return None
    try:
        width_um = float(parts[2])
    except ValueError:
        return None
    return parts[0], parts[1], width_um, parts[3]


def _diff_bar_intervals(ctx: TransformCtx) -> list[tuple[int, int]]:
    """Return ``(x_left, x_right)`` pairs for every diffusion bar interval.

    Diffusion bars run between adjacent FTI columns — same list the FTI
    transform produces. This is the physical realisation of the ``X-cell``
    axis in ``DiffPatternUnit`` (X-cell k == the k-th interval).
    """
    cols, _ = _fti_columns(ctx)
    if len(cols) < 2:
        return []
    return [
        (_poly_col_x_right(cols[i]), _poly_col_x_left(cols[i + 1]))
        for i in range(len(cols) - 1)
    ]


def _chassis_fti_columns(ctx: TransformCtx) -> list[int]:
    """Recover the chassis's FTI column indices from its polygons.

    Uses the same X=160+col*440 model so we can map chassis diffusion bars
    (positioned between chassis FTI cols) onto TARGET intervals.
    """
    fti_polys = ctx.chassis.inventory.by_layer.get((184, 0), [])
    cols = set()
    for p in fti_polys:
        x0, _, _, _ = p.bbox()
        col = round((x0 - _POLY_X_ORIGIN_DB) / _POLY_PITCH_DB)
        cols.add(col)
    return sorted(cols)


def _diff_pattern_np(ctx: TransformCtx, polys: list[Polygon],
                     target_layer: tuple[int, int]) -> list[Polygon]:
    """NP dual-diffusion NDIFF/PDIFF regeneration via region-recut.

    Verified model (chassis inst2 vs ground-truth NPZ5):

      1. **Reconstruct continuous regions.** Within each Y-cell, chassis bars
         separated by exactly one FTI-column width are merged back into the
         continuous N (or P) diffusion region they came from
         (:func:`_merge_regions`). Because the picker matches (CELLHEIGHT,
         MOS, TYPE) and Ogd, these regions — whose ends sit at the N/P
         boundaries (FTI-column centres) — are already correct for the target.

      2. **Re-cut at the TARGET FTI grid.** Each region is cut only at the
         FTI columns that are PRESENT at that Y-cell. NP FTIs are SHORT, so a
         Y-cell with no interior FTI keeps its region as one wide bar, while a
         Y-cell fully covered by FTIs is cut into per-interval segments. FTI
         presence + footprints come from :func:`_target_fti_segments` (the
         same columns/Y-segments the FTI transform emits), keeping FTI and
         diffusion registered.

      3. **Y** — each row is re-centred for the target DEVWIDTH.
    """
    y_cell_db = ctx.target_cellheight_db // 2
    target_devwidth_db = ctx.target_devwidth_nm * _DB_PER_NM
    if target_devwidth_db <= 0:
        target_devwidth_db = ctx.chassis_devwidth_nm * _DB_PER_NM
    chassis_devwidth_db = ctx.chassis_devwidth_nm * _DB_PER_NM or target_devwidth_db
    chassis_margin = (y_cell_db - chassis_devwidth_db) / 2 if y_cell_db else 0
    target_margin = (y_cell_db - target_devwidth_db) / 2 if y_cell_db else 0

    # Target FTI columns -> Y-segments (+ X footprints) for re-cutting.
    fti_segs = _target_fti_segments(ctx)
    fti_footprints = {
        col: (_poly_col_x_left(col), _poly_col_x_right(col)) for col in fti_segs
    }

    # Group chassis bars of THIS layer by Y-cell.
    cells: dict[int, list[tuple[int, int]]] = {}
    passthrough: list[Polygon] = []
    for p in polys:
        if p.key != target_layer:
            passthrough.append(p)
            continue
        x0, y0, x1, y1 = p.bbox()
        if y_cell_db <= 0:
            passthrough.append(p)
            continue
        k = round((y0 - chassis_margin) / y_cell_db)
        cells.setdefault(k, []).append((x0, x1))

    if not cells:
        return polys

    out: list[Polygon] = list(passthrough)
    for k, barlist in cells.items():
        ny0 = int(round(k * y_cell_db + target_margin))
        ny1 = ny0 + target_devwidth_db
        ybar = k * y_cell_db + target_margin
        # FTI footprints present at this Y-cell (their Y-segment covers ybar).
        present = sorted(
            fp for col, fp in fti_footprints.items()
            if any(fy0 <= ybar < fy1 for fy0, fy1 in fti_segs[col])
        )
        for S, E in _merge_regions(barlist):
            for (s, e) in _recut_region(S, E, present):
                poly = Polygon(target_layer[0], target_layer[1], [])
                poly.set_rectangle(s, ny0, e, ny1)
                out.append(poly)
    return out


def diff_pattern_transform(ctx: TransformCtx, polys: list[Polygon]) -> list[Polygon]:
    """Regenerate NDIFFDRAWN / PDIFFDRAWN bars with an analytical model that
    preserves the chassis per-cell X-structure while retargeting geometry.

      * **Y-cell grid.** ``y_cell_db = CELLHEIGHT_db / 2``; each row of bars
        sits in a Y-cell, CENTERED for the target DEVWIDTH::

            y_bottom = k * y_cell_db + (y_cell_db - devwidth_db) / 2

      * **X — edge-anchored interval remap.** Diffusion bars live in the
        gaps between FTI columns (``_diff_bar_intervals``). Every chassis bar
        occupies a contiguous span of chassis intervals ``[a..b]``; each end
        is remapped to the TARGET interval grid by anchoring to whichever
        array edge it is closest to::

            i' = i                    if i <  n_chassis/2   (left-anchored)
            i' = n_target-(n_chassis-i) otherwise           (right-anchored)

        This single rule reproduces ALL observed cell shapes:
          - *full-width* rows (N-type: every interval) collapse to every
            target interval,
          - *edge* rows (P/NP-type: only the first/last couple intervals)
            stay pinned to the target edges,
          - *middle-fill* rows map to the target middle,
          - *spanning* rows keep their narrow-edge + wide-centre split.
        Single-interval bars are de-duplicated (the left/right anchor ranges
        overlap for full rows); multi-interval (wide) bars are emitted with
        both endpoints remapped.

      * **Height** == ``DEVWIDTH_nm * 10`` db (target).

      * **Layer** (n vs p) preserved — each chassis polygon stays on its own
        layer key, so the MOS-driven split carried by the (CELLHEIGHT, MOS,
        TYPE)-matched chassis is retained.

    Self-training (target == chassis) reproduces the chassis byte-for-byte
    (identity remap); a new dataset with a different Ogd / DEVWIDTH / FTI
    stride is retargeted onto its own FTI grid.
    """
    if not polys:
        return polys

    target_layer = polys[0].key
    target_intervals = _diff_bar_intervals(ctx)
    if not target_intervals:
        return polys

    # NP dual-diffusion has a fundamentally different topology: continuous
    # N/P diffusion regions cut into segments ONLY where a (short) FTI is
    # present in Y. Handle it with the region-reconstruct + re-cut model.
    if ctx.decoder_row.get("MOS", "").strip().upper() == "NP":
        return _diff_pattern_np(ctx, polys, target_layer)

    chassis_cols = _chassis_fti_columns(ctx)
    if len(chassis_cols) < 2:
        return polys
    chassis_intervals = [
        (_poly_col_x_right(chassis_cols[i]), _poly_col_x_left(chassis_cols[i + 1]))
        for i in range(len(chassis_cols) - 1)
    ]
    n_c = len(chassis_intervals)
    n_t = len(target_intervals)

    def cover(x0: int, x1: int) -> list[int]:
        """Chassis interval indices the bar [x0,x1] overlaps."""
        return [k for k, (lo, hi) in enumerate(chassis_intervals)
                if x0 <= hi and x1 >= lo]

    def remap(i: int) -> int:
        j = i if i < n_c / 2 else n_t - (n_c - i)
        return max(0, min(n_t - 1, int(j)))

    # --- Y model ------------------------------------------------------------
    y_cell_db = ctx.target_cellheight_db // 2
    target_devwidth_db = ctx.target_devwidth_nm * _DB_PER_NM
    if target_devwidth_db <= 0:
        target_devwidth_db = ctx.chassis_devwidth_nm * _DB_PER_NM
    chassis_devwidth_db = ctx.chassis_devwidth_nm * _DB_PER_NM or target_devwidth_db
    chassis_margin = (y_cell_db - chassis_devwidth_db) / 2 if y_cell_db else 0
    target_margin = (y_cell_db - target_devwidth_db) / 2 if y_cell_db else 0

    # --- Group chassis bars of THIS layer by Y-cell index -------------------
    cells: dict[int, list[tuple[int, int]]] = {}
    passthrough: list[Polygon] = []
    for p in polys:
        if p.key != target_layer:
            passthrough.append(p)
            continue
        x0, y0, x1, y1 = p.bbox()
        if y_cell_db <= 0:
            passthrough.append(p)
            continue
        k = round((y0 - chassis_margin) / y_cell_db)
        cells.setdefault(k, []).append((x0, x1))

    if not cells:
        return polys

    def make(nx0: int, nx1: int, ny0: int, ny1: int) -> Polygon:
        poly = Polygon(layer=target_layer[0], datatype=target_layer[1], xy_lines=[])
        poly.set_rectangle(nx0, ny0, nx1, ny1)
        return poly

    out: list[Polygon] = list(passthrough)
    for k, barlist in cells.items():
        ny0 = int(round(k * y_cell_db + target_margin))
        ny1 = ny0 + target_devwidth_db
        single_ivs: set[int] = set()
        wides: list[tuple[int, int]] = []
        for x0, x1 in barlist:
            ivs = cover(x0, x1)
            if not ivs:
                continue
            if len(ivs) == 1:
                single_ivs.add(ivs[0])
            else:
                wides.append((ivs[0], ivs[-1]))
        # Multi-interval (wide) bars: remap both endpoints, emit as one bar.
        for a, b in wides:
            a2, b2 = remap(a), remap(b)
            out.append(make(target_intervals[min(a2, b2)][0],
                            target_intervals[max(a2, b2)][1], ny0, ny1))
        # Single-interval bars: map each CONTIGUOUS run and DENSELY fill the
        # target interval range it maps to. Dense fill is what makes an
        # interior "full" row expand to every target interval when the target
        # has more FTI columns than the chassis (and collapse when fewer),
        # while edge rows (separate short runs) stay pinned to the edges.
        target_singles: set[int] = set()
        for run in _contiguous_runs(sorted(single_ivs)):
            ta, tb = remap(run[0]), remap(run[-1])
            if ta > tb:
                ta, tb = tb, ta
            target_singles.update(range(ta, tb + 1))
        for iv in sorted(target_singles):
            nx0, nx1 = target_intervals[iv]
            out.append(make(nx0, nx1, ny0, ny1))
    return out


def _log_pattern_intent(name: str, ctx: TransformCtx, dec_col: str, iso_col: str) -> None:
    """Emit an informational message so the operator can see the expanded
    pattern the transform *would* apply once its geometry model is wired up.
    """
    raw = ctx.decoder_row.get(dec_col, "") or ctx.buildsheet_row.get(iso_col, "")
    if not raw.strip() or _decoder_expand is None:
        return
    try:
        expanded = _decoder_expand(raw, ctx.target_ogd, ctx.target_pgd)
    except Exception:
        expanded = raw
    print(
        f"    [pattern] {name}: {dec_col}={raw!r} -> {iso_col}~{expanded!r}",
        file=sys.stderr,
    )


# ---------------------------------------------------------------------------
# Column-layer regeneration (POLY / VG / VT).
#
# These layers are drawn as vertical stripes at poly columns (X = 160+col*440,
# width = POLY_CD). A stripe's Y-segment list depends only on the column's
# ROLE, not its absolute position, and the Y geometry is a function of the Pgd
# dimensions (CELLHEIGHT / UnitCellSizePgd / ArraySizePgd) which the chassis
# picker holds fixed. So a column stripe is regenerated by:
#   1. classifying every chassis column into a role,
#   2. capturing one representative Y-segment signature per role,
#   3. emitting, for each TARGET column, the signature of its role.
# ---------------------------------------------------------------------------

def _chassis_extent(ctx: TransformCtx) -> int:
    ogd = int(ctx.chassis_decoder_row.get("UnitCellSizeOgd", "0") or 0)
    arr = int(ctx.chassis_decoder_row.get("ArraySizeOgd", "0") or 0)
    return ctx.buffer_l + arr * ogd + ctx.buffer_r


def _columns_by_signature(polys: list[Polygon], x_shift: int = 0
                          ) -> dict[int, tuple[tuple[int, int], ...]]:
    """Group column-stripe polygons by column index -> sorted Y-segments.

    ``x_shift`` is the stripe's sub-pitch X offset from the poly grid (0 for
    poly-aligned layers like POLY/VG; -POLY_PITCH/2 for VT vias, which sit on
    a half-pitch grid).
    """
    by_col: dict[int, list[tuple[int, int]]] = {}
    for p in polys:
        x0, y0, x1, y1 = p.bbox()
        c = round((x0 - _POLY_X_ORIGIN_DB - x_shift) / _POLY_PITCH_DB)
        by_col.setdefault(c, []).append((y0, y1))
    return {c: tuple(sorted(v)) for c, v in by_col.items()}


def _same_fti_grid(ctx: TransformCtx) -> bool:
    """True when the target and chassis share the same FTI column grid — i.e.
    same Ogd AND same FTI stride/buffers. In that case column-based layers
    (POLY / VG / VT) are byte-identical to the chassis, so the transforms can
    pass the chassis polygons through untouched (keeps self-training exact and
    avoids re-deriving structure that is already correct)."""
    return sorted(_fti_columns(ctx)[0]) == _chassis_fti_columns(ctx)


def _plug_xaxis(spec: str) -> str:
    """Return the normalised X-axis (part before the last comma) of a plug
    spec, used to detect a real column-selection change between decoders."""
    x = spec.rsplit(",", 1)[0] if "," in spec else spec
    return re.sub(r"\s+", "", x)


def _plug_columns(ctx: TransformCtx, field: str) -> set[int]:
    """Target columns selected by a plug spec's X-axis (buildsheet ``field``).

    The X-axis (the part before the last comma) is a one-Ogd-cell offset
    pattern tiled across the array — the same coordinate space as
    ``VcxPatternUnit`` — so column ``BufferL + u*Ogd + off`` is selected for
    every unit ``u`` and every expanded offset ``off``. Returns an empty set
    when no X-axis is present (caller then treats all interior columns as
    selected).
    """
    pattern = ctx.buildsheet_row.get(field, "")
    if "," not in pattern:
        return set()
    xaxis = pattern.rsplit(",", 1)[0].strip()
    if not xaxis:
        return set()
    unit = ctx.target_ogd
    offsets = _expand_axis_positions(xaxis, unit)
    bl = ctx.buffer_l
    extent = ctx.total_ogd_extent
    cols: set[int] = set()
    for u in range(ctx.target_array_ogd):
        base = bl + u * unit
        for off in offsets:
            col = base + off
            if 0 <= col <= extent:
                cols.add(col)
    return cols


def poly_plugs_transform(ctx: TransformCtx, polys: list[Polygon]) -> list[Polygon]:
    """Regenerate POLYDRAWN vertical stripes on the target poly grid.

    POLY exists at EVERY poly column ``0..extent``. Each column carries one
    of two Y-segment signatures — a longer 'special' one at FTI columns and
    in the L/R buffers (the unplugged base grid), and a shorter 'regular' one
    at PLUGGED interior columns (where ``PolyPlugs`` merge track pairs).

    ``PolyPlugs`` has two axes that both drive geometry:
      * the **Y-axis** (``3/4``) selects which track pairs merge — regenerated
        from the base grid via the TCN track-merge model;
      * the **X-axis** (``1:3:Skip:1:Repeat``) selects WHICH interior columns
        are plugged — unselected interior columns revert to the full base grid.
    When neither differs from the chassis the chassis is donated exactly.
    """
    if not polys:
        return polys
    layer = polys[0].key
    by_col = _columns_by_signature([p for p in polys if p.key == layer])
    if not by_col:
        return polys

    # Capture the two signatures by ACTUAL segment count (longest = special,
    # shortest = regular) rather than by a position rule — the chassis may
    # place its special columns on a different stride than the target.
    distinct = sorted(set(by_col.values()), key=len)
    if not distinct:
        return polys
    regular_sig = distinct[0]
    special_sig = distinct[-1]

    extent = ctx.total_ogd_extent
    target_fti = set(_fti_columns(ctx)[0])
    bl, br = ctx.buffer_l, ctx.buffer_r
    interior = {c for c in range(0, extent + 1)
                if c not in target_fti and bl <= c <= extent - br}

    tgt_plugs = _tcn_plugs(ctx.buildsheet_row.get("PolyPlugsUnit", ""))
    chs_plugs = _tcn_plugs(ctx.chassis_decoder_row.get("PolyPlugs", ""))
    y_changed = (tgt_plugs is not None and chs_plugs is not None
                 and tgt_plugs != chs_plugs)
    # The X-axis selects WHICH interior columns are plugged. Detect a real
    # change by comparing the target vs chassis decoder X-axis (DSL form) —
    # the corpus plugs ALL interior columns, so an X-selection is only applied
    # when the decoder X actually differs from the chassis (keeps self-
    # training exact, since target X == chassis X there).
    x_changed = _plug_xaxis(ctx.decoder_row.get("PolyPlugs", "")) != \
        _plug_xaxis(ctx.chassis_decoder_row.get("PolyPlugs", ""))
    plug_cols = (_plug_columns(ctx, "PolyPlugsUnit") & interior) if x_changed else interior

    if _same_fti_grid(ctx) and not y_changed and not x_changed:
        return polys  # identical to chassis — pass through

    if y_changed:
        y_cell = ctx.target_cellheight_db // 2
        unit_step = 2 * ctx.target_pgd * y_cell
        per_unit = sum(1 for y0, _ in special_sig
                       if y0 < special_sig[0][0] + unit_step)
        interior_sig = (_tcn_apply_plugs(special_sig, tgt_plugs, per_unit)
                        if per_unit > 0 else regular_sig)
    else:
        interior_sig = regular_sig

    out: list[Polygon] = [p for p in polys if p.key != layer]
    for col in range(0, extent + 1):
        if col not in interior or (x_changed and col not in plug_cols):
            sig = special_sig       # FTI / buffer / unselected interior — base
        else:
            sig = interior_sig      # plugged interior
        x0 = _poly_col_x_left(col)
        x1 = x0 + _POLY_CD_DB
        for (y0, y1) in sig:
            p = Polygon(layer[0], layer[1], [])
            p.set_rectangle(x0, y0, x1, y1)
            out.append(p)
    return out


def _tcn_plugs(spec: str) -> frozenset[int] | None:
    """Parse the Y-axis of a TCN plug spec into the set of plug track values.

    The spec is ``Xaxis,Yaxis`` (decoder ``TCNPlugs`` = ``All,4/5``; buildsheet
    ``TcnPlugsUnit`` = ``0:r0,4/5``). The Y-axis lists the plug values, a plug
    value ``V`` merging the TCN track pair whose lower track sits at position
    ``V+1`` within the Pgd unit. Supports ``a/b`` lists and ``a:b`` inclusive
    ranges. Returns ``None`` for the un-modelled ``All`` form or an empty /
    unparseable axis, so the caller falls back to chassis donation.
    """
    if not spec or "," not in spec:
        return None
    y = spec.rsplit(",", 1)[-1].strip()
    if not y:
        return None
    out: set[int] = set()
    for frag in y.split("/"):
        frag = frag.strip()
        if not frag:
            continue
        if frag.lower() == "all":
            return None  # continuous stripe — not modelled, donate chassis Y
        if ":" in frag:
            nums = [int(t) for t in frag.split(":") if t.strip().isdigit()]
            if not nums:
                return None
            for v in range(min(nums), max(nums) + 1):
                out.add(v)
        elif frag.isdigit():
            out.add(int(frag))
        else:
            return None
    return frozenset(out) if out else None


def _tcn_apply_plugs(base_sig: tuple, plugs: frozenset[int],
                     per_unit: int) -> list[tuple[int, int]]:
    """Apply TCN plugs to the unplugged base column signature.

    The base column has ``per_unit`` short TCN tracks per Pgd unit. A plug at
    value ``V`` fills the gap after the track at unit-position ``V+1`` (i.e.
    merges that track with the next), which may chain and cross unit
    boundaries. Verified byte-exact against the training corpus for ``4/5``,
    ``4/5/6`` and ``3/5``.
    """
    segs = sorted(base_sig)
    n = len(segs)
    res: list[tuple[int, int]] = []
    i = 0
    while i < n:
        y0, y1 = segs[i][0], segs[i][1]
        while i + 1 < n and ((i % per_unit) - 1) in plugs:
            i += 1
            y1 = segs[i][1]
        res.append((y0, y1))
        i += 1
    return res


def tcn_plugs_transform(ctx: TransformCtx, polys: list[Polygon]) -> list[Polygon]:
    """Regenerate TCNDRAWN (5/0) contact stripes.

    TCN stripes sit on the HALF-pitch grid (like VT vias) at EVERY column
    ``1..extent``. Each column carries one of two Y signatures — a longer
    'special' one in the L/R buffers and a shorter 'regular' one in the
    interior (verified: special columns are exactly ``col <= BufferL`` and
    ``col > extent-BufferR``; no FTI dependence). Signatures are captured
    from the chassis by segment count and re-emitted on the target grid.

    When the ``TcnPlugsUnit`` plug set differs from the chassis ``TCNPlugs``
    the interior signature is REGENERATED from the buffer (unplugged) base
    grid by applying the target plugs (Y-axis). The X-axis selects WHICH
    interior columns are plugged — unselected interior columns revert to the
    unplugged base grid. When neither differs from the chassis the chassis is
    donated exactly.
    """
    if not polys:
        return polys
    layer = polys[0].key
    x_shift = -(_POLY_PITCH_DB // 2)
    by_col = _columns_by_signature([p for p in polys if p.key == layer], x_shift)
    if not by_col:
        return polys
    distinct = sorted(set(by_col.values()), key=len)
    regular_sig = distinct[0]           # fewest segments = plugged interior
    special_sig = distinct[-1]          # most segments = unplugged base grid

    extent = ctx.total_ogd_extent
    bl, br = ctx.buffer_l, ctx.buffer_r
    interior = {c for c in range(1, extent + 1) if bl < c <= extent - br}

    tgt_plugs = _tcn_plugs(ctx.buildsheet_row.get("TcnPlugsUnit", ""))
    chs_plugs = _tcn_plugs(ctx.chassis_decoder_row.get("TCNPlugs", ""))
    y_changed = (tgt_plugs is not None and chs_plugs is not None
                 and tgt_plugs != chs_plugs)
    x_changed = _plug_xaxis(ctx.decoder_row.get("TCNPlugs", "")) != \
        _plug_xaxis(ctx.chassis_decoder_row.get("TCNPlugs", ""))
    plug_cols = (_plug_columns(ctx, "TcnPlugsUnit") & interior) if x_changed else interior

    if _same_fti_grid(ctx) and not y_changed and not x_changed:
        return polys  # identical to chassis — pass through

    if y_changed:
        y_cell = ctx.target_cellheight_db // 2
        unit_step = 2 * ctx.target_pgd * y_cell
        per_unit = sum(1 for y0, _ in special_sig
                       if y0 < special_sig[0][0] + unit_step)
        interior_sig = (_tcn_apply_plugs(special_sig, tgt_plugs, per_unit)
                        if per_unit > 0 else regular_sig)
    else:
        interior_sig = regular_sig

    out: list[Polygon] = [p for p in polys if p.key != layer]
    for col in range(1, extent + 1):
        if col <= bl or col > extent - br or (x_changed and col not in plug_cols):
            sig = special_sig       # buffer / unselected interior — base grid
        else:
            sig = interior_sig      # plugged interior
        x0 = _poly_col_x_left(col) + x_shift
        x1 = x0 + _POLY_CD_DB
        for (y0, y1) in sig:
            p = Polygon(layer[0], layer[1], [])
            p.set_rectangle(x0, y0, x1, y1)
            out.append(p)
    return out


def _vcx_columns(ctx: TransformCtx, prefix: str) -> list[int]:
    """Target columns for a Vcx sub-layer (``vg`` or ``vt``) derived from the
    buildsheet ``VcxPatternUnit``.

    Each ``prefix,Xaxis,Y`` segment contributes columns
    ``BufferL + u*Ogd + off`` for every unit ``u`` and every offset ``off``
    the X-axis expands to within one unit cell.
    """
    unit = ctx.target_ogd
    array = ctx.target_array_ogd
    bl = ctx.buffer_l
    extent = ctx.total_ogd_extent
    pattern = ctx.buildsheet_row.get("VcxPatternUnit", "")
    cols: set[int] = set()
    for seg in pattern.split(";"):
        toks = [t.strip() for t in seg.split(",")]
        if len(toks) < 2 or toks[0].lower() != prefix:
            continue
        offsets = _expand_axis_positions(toks[1], unit)
        for u in range(array):
            base = bl + u * unit
            for off in offsets:
                col = base + off
                if 0 <= col <= extent:
                    cols.add(col)
    return sorted(cols)


def _regen_stripe_layer(polys: list[Polygon], layer: tuple[int, int],
                        target_cols: list[int], x_shift: int = 0,
                        y_shift: int = 0) -> list[Polygon]:
    """Regenerate a column-stripe layer at ``target_cols`` by copying each
    chassis column's Y-segment signature via edge-anchored index mapping.

    Used for VG / VT where each column carries a per-column Y signature and
    the column SET is derived from the buildsheet pattern / geometry.
    ``x_shift`` is the stripe's sub-pitch X offset (0 for poly-aligned,
    -POLY_PITCH/2 for the half-pitch VT via grid). ``y_shift`` translates the
    donated Y-segments (used to retarget the vg/vt track without disturbing
    the per-column via structure).
    """
    by_col = _columns_by_signature([p for p in polys if p.key == layer], x_shift)
    chassis_cols = sorted(by_col)
    n_c = len(chassis_cols)
    n_t = len(target_cols)
    if n_c == 0 or n_t == 0:
        return polys

    def remap(i: int) -> int:
        """Proportional index mapping: target-col ``i`` -> chassis-col
        ``round(i * (n_c - 1) / (n_t - 1))``.

        This preserves any per-column signature ratios (e.g. the ~20% of
        chassis vt columns in Ogd unit 0 that carry a redundant Pgd-unit-0
        via) by uniformly sampling the chassis column set. An earlier
        edge-anchored mapping (first ``n_t//2`` + last ``n_t//2``) biased
        the sample toward the doubled-via cluster at either end.
        """
        if n_t == 1:
            return 0
        return max(0, min(n_c - 1, round(i * (n_c - 1) / (n_t - 1))))

    out: list[Polygon] = [p for p in polys if p.key != layer]
    for i, col in enumerate(sorted(target_cols)):
        sig = by_col[chassis_cols[remap(i)]]
        x0 = _poly_col_x_left(col) + x_shift
        x1 = x0 + _POLY_CD_DB
        for (y0, y1) in sig:
            p = Polygon(layer[0], layer[1], [])
            p.set_rectangle(x0, y0 + y_shift, x1, y1 + y_shift)
            out.append(p)
    return out


# vg/vt via track pitch (db per routing track) — node constants derived from
# the corpus (VG: t=6->2885, t=9->3605 => 240; VT: t=14->4905, t=6->2865 => 255).
_VG_TRACK_PITCH = 240
_VT_TRACK_PITCH = 255


def _has_vcx_prefix(vcx: str, prefix: str) -> bool:
    """Return True when ``vcx`` (buildsheet ``VcxPatternUnit``) contains at
    least one non-empty segment whose first comma-token equals ``prefix``
    (``vg`` or ``vt``).

    Used as a polarity guard: a target buildsheet whose ``VcxPatternUnit``
    carries only ``vt,...`` segments must not emit any VG (32/0) polygons —
    even when the chassis .txt has them — because the target physically
    lacks that layer's pattern. Without this check the transform passes
    chassis VG polys through untouched (identical FTI grid) or via-donates
    them via ``_regen_stripe_layer`` (empty target-col set is a no-op).
    """
    if not vcx:
        return False
    p = prefix.lower()
    for seg in vcx.split(";"):
        toks = seg.strip().split(",", 1)
        if toks and toks[0].strip().lower() == p:
            return True
    return False


def _vcx_track_entries(vcx: str, prefix: str) -> list[tuple[int, bool]] | None:
    """Parse the Y-axis track list of a ``prefix`` (vg/vt) Vcx segment into a
    sorted list of ``(track, is_bottom)`` entries.

    Handles plain tracks (``13/14`` -> ``[(13, False), (14, False)]``) and the
    ``…-bottom`` special form (``3/7-bottom/13/17-bottom`` ->
    ``[(3, False), (7, True), (13, False), (17, True)]``). The bottom flag is
    preserved so an edit that only toggles ``-bottom`` still registers as a
    change (triggering regeneration rather than chassis donation). Returns
    ``None`` when no numeric track is present or a fragment is unparseable
    (unknown suffix), so the caller can fall back to chassis donation.
    """
    for seg in vcx.split(";"):
        toks = [t.strip() for t in seg.split(",")]
        if len(toks) >= 3 and toks[0].lower() == prefix:
            y = toks[-1].strip()
            if not y:
                return None
            entries: list[tuple[int, bool]] = []
            for frag in y.split("/"):
                frag = frag.strip()
                if not frag:
                    continue
                bottom = False
                if "-" in frag:
                    head, _, tail = frag.partition("-")
                    if tail.strip().lower() != "bottom":
                        return None  # unknown suffix — donate chassis Y
                    bottom = True
                    frag = head.strip()
                try:
                    entries.append((int(frag), bottom))
                except ValueError:
                    return None
            return sorted(set(entries)) if entries else None
    return None


def _vcx_calibrate(polys: list[Polygon], layer: tuple[int, int], x_shift: int,
                   chs_entries: list[tuple[int, bool]], pitch: int,
                   unit_step: int) -> tuple[int, int, int, int] | None:
    """Derive ``(pitch, base, bottom_nudge, via_h)`` from the chassis vias.

    The vg/vt routing pitch differs between node variants (2TRK ~240/255 db,
    1TRK ~264 db), so it is fit from the chassis geometry when at least two
    distinct NORMAL tracks are present; otherwise the caller's constant
    ``pitch`` is kept. ``base`` and the small ``-bottom`` nudge (~+4 db) are
    measured from the chassis first Pgd unit so a regenerated pattern lands on
    exactly the grid the chassis used. Returns ``None`` when the chassis first
    unit does not present one via per declared track (caller then donates).
    """
    by_col = _columns_by_signature([p for p in polys if p.key == layer], x_shift)
    all_y = [seg for sig in by_col.values() for seg in sig]
    if not all_y or not chs_entries:
        return None
    via_h = Counter(y1 - y0 for y0, y1 in all_y).most_common(1)[0][0]
    y0_min = min(y0 for y0, _ in all_y)
    # First-unit distinct via y0 values (expected: one per declared track).
    first = sorted({y0 for y0, _ in all_y if y0 < y0_min + unit_step})
    if len(first) != len(chs_entries):
        return None
    paired = list(zip(chs_entries, first))
    normals = [(t, y) for (t, b), y in paired if not b]
    bottoms = [(t, y) for (t, b), y in paired if b]
    ref = normals or [(t, y) for (t, _), y in paired]
    if len({t for t, _ in ref}) >= 2:
        (t0, y0f), (t1, y1f) = ref[0], ref[-1]
        pitch = round((y1f - y0f) / (t1 - t0))
    base = round(sum(y - t * pitch for t, y in ref) / len(ref))
    nudge = 0
    if bottoms:
        nudge = round(sum(y - (base + t * pitch) for t, y in bottoms) / len(bottoms))
    return pitch, base, nudge, via_h


def _regen_vcx_layer(ctx: TransformCtx, polys: list[Polygon],
                     layer: tuple[int, int], target_cols: list[int],
                     x_shift: int, pitch: int, prefix: str) -> list[Polygon]:
    """Regenerate a vg/vt via layer at ``target_cols`` with the target track(s).

    Precedence:
      * track list unchanged / unparsable   -> donate chassis Y (exact).
      * single -> single plain track change -> shift chassis Y by the delta.
      * anything else (e.g. ``14`` -> ``13/14``, or a ``-bottom`` edit) ->
        FULL regenerate: one via per (track, unit) at
        ``base + track*pitch + bottom_nudge + unit*unit_step``. The pitch,
        base and bottom nudge are calibrated from the chassis geometry so the
        result lands on the same grid (honouring the ~264 db 1TRK vs 240/255
        db 2TRK pitch difference and the ~+4 db ``-bottom`` offset).
    """
    tgt = _vcx_track_entries(ctx.buildsheet_row.get("VcxPatternUnit", ""), prefix)
    chs = _vcx_track_entries(ctx.chassis_decoder_row.get("VcxPattern", ""), prefix)

    if tgt is None or chs is None or tgt == chs:
        return _regen_stripe_layer(polys, layer, target_cols, x_shift, 0)
    if (len(tgt) == 1 and len(chs) == 1 and not tgt[0][1] and not chs[0][1]):
        return _regen_stripe_layer(polys, layer, target_cols, x_shift,
                                   (tgt[0][0] - chs[0][0]) * pitch)

    # Multi-track, via-count change, or a -bottom edit: full regeneration.
    y_cell = ctx.target_cellheight_db // 2
    unit_step = 2 * ctx.target_pgd * y_cell
    calib = _vcx_calibrate(polys, layer, x_shift, chs, pitch, unit_step)
    if calib is None:
        return _regen_stripe_layer(polys, layer, target_cols, x_shift, 0)
    pitch, base, nudge, via_h = calib
    n_units = ctx.target_array_pgd

    # Cols in the FIRST Ogd unit block carry a redundant Pgd-unit-0 via —
    # a boundary/landing artifact universally present in the chassis
    # corpus (see ISO_GDS_Correct diffs: NPZ5-S-A/PZ5-S-A/NZ5-S-A rows all
    # show a 20% doubled-col ratio matching the first-Ogd-unit width).
    # For each target col, ``poly_col = col`` for VG and ``col - 1`` for VT
    # (VT sits at the half-pitch column). A col is in the first Ogd unit
    # when ``1 <= poly_col - buffer_l <= target_ogd``.
    poly_col_shift = 0 if prefix == "vg" else -1
    bl = ctx.buffer_l
    first_unit_end = bl + ctx.target_ogd

    out: list[Polygon] = [p for p in polys if p.key != layer]
    for col in sorted(target_cols):
        x0 = _poly_col_x_left(col) + x_shift
        x1 = x0 + _POLY_CD_DB
        poly_col = col + poly_col_shift
        in_first_ogd_unit = bl < poly_col <= first_unit_end
        for u in range(n_units):
            for track, bottom in tgt:
                y0 = base + track * pitch + (nudge if bottom else 0) + u * unit_step
                p = Polygon(layer[0], layer[1], [])
                p.set_rectangle(x0, y0, x1, y0 + via_h)
                out.append(p)
                if u == 0 and in_first_ogd_unit:
                    # Redundant Pgd-unit-0 via for first-Ogd-unit cols.
                    p2 = Polygon(layer[0], layer[1], [])
                    p2.set_rectangle(x0, y0, x1, y0 + via_h)
                    out.append(p2)
    return out


def _vcx_target_cols(ctx: TransformCtx, prefix: str) -> list[int]:
    """Return target buildsheet-driven column set for a Vcx sub-layer.

    * ``prefix='vg'`` → poly columns from the ``vg`` segment X-axis.
    * ``prefix='vt'`` → half-pitch columns (``poly_col + 1``) from the
      ``vt`` segment X-axis, clipped to the right buffer.

    Empty result means the buildsheet has no matching prefix segment (or
    the X-axis expanded to nothing); the caller falls back to donating
    the chassis's own columns.
    """
    if prefix == "vg":
        return _vcx_columns(ctx, "vg")
    right = ctx.total_ogd_extent - ctx.buffer_r
    return sorted({c + 1 for c in _vcx_columns(ctx, "vt") if c + 1 <= right})


def vcx_pattern_transform(ctx: TransformCtx, polys: list[Polygon]) -> list[Polygon]:
    """Regenerate VGDRAWN (32/0) and VTDRAWN (31/0) column stripes.

    * **VG** columns come from the buildsheet ``VcxPatternUnit`` ``vg``
      segments (poly-column offsets tiled across the array units).
    * **VT** columns come from the ``vt`` segments: a VT via for poly column
      ``P`` sits at half-pitch column ``P+1``.
    * **Y track(s)** — a single-track edit shifts the via row by
      ``(target_track - chassis_track) * pitch``; a multi-track edit (e.g.
      ``,14`` -> ``,13/14``) fully regenerates one via per track per Pgd unit.
      Unchanged tracks reproduce the chassis exactly.
    * **Polarity guard** — a target whose ``VcxPatternUnit`` has NO segment
      for this layer's prefix (``vg`` for 32/0, ``vt`` for 31/0) emits an
      empty layer. Prevents chassis vias from leaking through when the
      target row disables one polarity (rows like ``vt,...`` only or
      ``vg,...`` only in the buildsheet).
    * **X-axis change** — the target column set is ALWAYS derived from the
      buildsheet ``VcxPatternUnit`` X-axis (``_vcx_target_cols``). When the
      target and chassis share the same FTI grid AND the same Vcx X-axis for
      this prefix AND the same Y tracks, the chassis polygons pass through
      untouched (byte-identical fast path). Any change — column set, track
      list, or FTI grid — triggers a regeneration.
    """
    if not polys:
        return polys
    layer0 = polys[0].key
    prefix = "vg" if layer0 == (32, 0) else "vt"
    pitch = _VG_TRACK_PITCH if prefix == "vg" else _VT_TRACK_PITCH
    x_shift = 0 if prefix == "vg" else -(_POLY_PITCH_DB // 2)

    # Polarity guard: drop the entire layer when the target buildsheet has
    # no matching prefix segment. Without this the byte-identical fast path
    # below (and the empty-target-cols path in ``_regen_stripe_layer``)
    # would leak chassis polys.
    vcx = ctx.buildsheet_row.get("VcxPatternUnit", "")
    if not _has_vcx_prefix(vcx, prefix):
        return [p for p in polys if p.key != layer0]

    # Target column set is always driven by the buildsheet (not the chassis).
    # This is the core fix for the X-axis change case: when the target's
    # ``VcxPatternUnit`` X-axis differs from the chassis's ``VcxPattern``
    # X-axis (e.g. target ``1:3/5:7/…`` vs chassis ``0:r0[3]``), we must
    # emit vias at TARGET column positions — not at chassis columns.
    target_cols = _vcx_target_cols(ctx, prefix)
    chassis_cols = sorted(
        _columns_by_signature([p for p in polys if p.key == layer0], x_shift)
    )
    if not target_cols:
        # Empty target X-axis for this prefix — donate chassis geometry as
        # a defensive fallback (should not normally happen after the
        # polarity guard).
        target_cols = chassis_cols

    # Byte-identical fast path: chassis and target agree on FTI grid,
    # column set, AND Y tracks. Preserve the original polygons verbatim
    # (avoids any subtle rounding when re-emitting the same geometry).
    tgt_tracks = _vcx_track_entries(vcx, prefix)
    chs_tracks = _vcx_track_entries(
        ctx.chassis_decoder_row.get("VcxPattern", ""), prefix
    )
    if (target_cols == chassis_cols
            and tgt_tracks is not None
            and tgt_tracks == chs_tracks
            and _same_fti_grid(ctx)):
        return polys

    return _regen_vcx_layer(
        ctx, polys, layer0, target_cols, x_shift, pitch, prefix
    )


def _m0_dash_period(ctx: TransformCtx) -> int | None:
    """Interior M0-cut dash period (db) from the ``M0CutPatternUnit`` X-axis.

    Verified: stride form ``a:b[N]`` → period ``(N+1)*220`` (half-poly-pitch
    grid): NPZ5 ``[3]``→880, PZ5 ``[5]``→1320. Returns ``None`` for the range
    (``2:5``) and explicit-list (``1:3/6:8/…``) forms, which the caller then
    donates from the chassis (not yet modelled).
    """
    x = ctx.buildsheet_row.get("M0CutPatternUnit", "").split(",")[0].strip()
    m = _STRIDE_RE.match(x)
    if not m:
        return None
    return (int(m.group("stride")) + 1) * 220


_M0_GAP_DB = 180                 # cut width between interior dashes
_M0_UNIT_DB = 5280               # Pgd unit height (2*UnitPgd*y_cell, 2TRK family)
_M0_ANCHOR = 5745                # interior-track grid anchor (y0 of unit idx 0)
_M0_GRID = [0, 480, 1080, 1560, 2040, 2640, 3120, 3720, 4200, 4680]


def _m0_frag2_kvals(frag: str) -> list[int]:
    """Expand a ``frag2`` (top-referenced) M0 Y-fragment into its ``K`` values
    (the ``r``-token numbers). Handles ``rK``, ``rA:rB`` and ``rA:rB[s]``."""
    frag = frag.strip()
    m = re.match(r"^r(\d+):r(\d+)\[(\d+)\]$", frag)
    if m:
        a, b, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
        step = s + 1
        out, k = [], a
        while k >= b:
            out.append(k)
            k -= step
        return out
    m = re.match(r"^r(\d+):r(\d+)$", frag)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return list(range(a, b - 1, -1)) if a >= b else list(range(a, b + 1))
    m = re.match(r"^r(\d+)$", frag)
    return [int(m.group(1))] if m else []


def _m0_interior_idx(yaxis: str, odd: bool = True) -> set[int]:
    """Interior-cut track indices (within a Pgd unit) from the M0 Y-axis.

    Decoded (byte-consistent over the controlled corpus): the two M0 colors
    use opposite parities — **L241 (CLR2) selects ODD** values, **L120 (CLR1)
    selects EVEN** values:
      * ``frag1`` (``1:a[s]``): for each value ``v`` of the chosen parity →
        idx ``2 + (v-1)//2`` (fills upward from idx 2).
      * ``frag2`` (``rK…`` after ``/``): for each ``K`` of the chosen parity →
        idx ``(3-K)//2`` if ≥0 (fills downward toward idx 0).
    """
    parity = 1 if odd else 0
    parts = yaxis.split("/")
    idx: set[int] = set()
    for v in _expand_axis_positions(parts[0].strip(), 10):
        if v % 2 == parity:
            idx.add(2 + (v - 1) // 2)
    if len(parts) > 1:
        for k in _m0_frag2_kvals(parts[1]):
            if k % 2 == parity and (3 - k) // 2 >= 0:
                idx.add((3 - k) // 2)
    return idx


def _m0_track_idx(y0: int, shift: int = 0) -> int | None:
    """Grid index (0..9) of an interior track within its Pgd unit. ``shift``
    is the per-layer offset (0 for L241/CLR2, 240 for L120/CLR1)."""
    off = (y0 - _M0_ANCHOR - shift) % _M0_UNIT_DB
    return _M0_GRID.index(off) if off in _M0_GRID else None


# Analytic M0 geometry (verified vs ISO_GDS_Correct, all widths):
#   mid_end = arraypp*440 + 10470   (arraypp = ArraySizeOgd * UnitCellSizeOgd)
_M0_WIDEFIRST = (-8710, 2330)       # interior left periphery (constant)
_M0_DASH0 = 2510                    # first interior dash x0 (= widefirst end + gap)
_M0_ARRAY_Y0 = _M0_ANCHOR - _M0_UNIT_DB   # array first-unit base (465)


def _m0_caps(layer: tuple[int, int], height: int, mid_end: int
             ) -> tuple[tuple[int, int], tuple[int, int]]:
    """(left cap, right cap) for a track — depends on layer & track height
    (verified: only L120 height-150 tracks use the wide 1580 cap)."""
    if layer == (120, 0) and height == 150:
        return (-10470, -8890), (mid_end + 180, mid_end + 1760)
    return (-10470, -9330), (mid_end + 620, mid_end + 1760)


def _m0_coarse_segs(layer: tuple[int, int], height: int, mid_end: int
                    ) -> list[tuple[int, int]]:
    """Uncut track: left cap + continuous middle + right cap."""
    lc, rc = _m0_caps(layer, height, mid_end)
    return [lc, (-8710, mid_end), rc]


def _m0_interior_segs(layer: tuple[int, int], height: int, mid_end: int,
                      period: int) -> list[tuple[int, int]]:
    """Interior-cut track: left cap + wide-first + dashes at ``period`` (seg =
    period-180, gap 180) + wide-last + right cap. Verified byte-exact."""
    lc, rc = _m0_caps(layer, height, mid_end)
    arr = mid_end - 10470                        # = arraypp * 440
    n = arr // period - 1                         # number of uniform dashes
    segs = [lc, _M0_WIDEFIRST]
    for k in range(n):
        x = _M0_DASH0 + k * period
        segs.append((x, x + period - _M0_GAP_DB))
    segs.append((_M0_DASH0 + n * period, mid_end))      # wide-last
    segs.append(rc)                                     # right cap
    return segs


def _m0_edge_segs(layer: tuple[int, int], height: int, mid_end: int
                  ) -> list[tuple[int, int]]:
    """Edge power-rail track (full-width dashes). Pattern depends on
    ``(layer, height)`` (same key as the caps): L120 height-150 tracks use
    uniform seg-1580 dashes; every other edge track uses a first/last special
    (1450) with seg-1320 dashes between."""
    segs: list[tuple[int, int]] = []
    if layer == (120, 0) and height == 150:
        x = -10470
        while x < mid_end + 1760:
            segs.append((x, x + 1580))
            x += 1760
    else:
        segs.append((-10470, -9020))
        x = -8580
        while x < mid_end:
            segs.append((x, x + 1320))
            x += 1760
        segs.append((mid_end + 310, mid_end + 1760))
    return segs


def m0cut_pattern_transform(ctx: TransformCtx, polys: list[Polygon]) -> list[Polygon]:
    """Regenerate M0CLR1 (120/0) / M0CLR2 (241/0) horizontal-metal cuts.

    Fully analytic rebuild (reverse-engineered & verified vs ISO_GDS_Correct):
    the track Y-grid is donated from the chassis (constant per 2TRK family) and
    every track's X is rebuilt at the TARGET width. Each track is one of:
      * **edge** power-rail (5 lowest + 5 highest Y) — full-width dashes;
      * **interior-cut** (only L241, at Y-axis-selected per-unit indices) —
        wide-first + dashes at the X-axis period + wide-last;
      * **coarse** — uncut middle.

    ``mid_end = arraypp*440 + 10470``; interior indices from
    ``_m0_interior_idx``; dash period from ``_m0_dash_period``. Gated: donates
    unchanged when ``M0CutPattern`` matches the chassis, or when the X-axis is
    an unmodelled (range/list) form.
    """
    if not polys:
        return polys
    layer = polys[0].key

    tgt = re.sub(r"\s+", "", ctx.decoder_row.get("M0CutPattern", ""))
    chs = re.sub(r"\s+", "", ctx.chassis_decoder_row.get("M0CutPattern", ""))
    if _same_fti_grid(ctx) and tgt == chs:
        return polys

    unit = ctx.buildsheet_row.get("M0CutPatternUnit", "")
    yaxis = unit.split(",", 1)[1] if "," in unit else ""
    period = _m0_dash_period(ctx)
    if not yaxis or period is None:
        return polys  # range/list X-form or no Y — donate chassis

    arraypp = ctx.target_array_ogd * ctx.target_ogd
    if arraypp <= 0:
        return polys
    if arraypp % 2:                     # metal middle rounds up to an even width
        arraypp += 1
    mid_end = arraypp * 440 + 10470
    # L241 (CLR2) selects ODD Y-values at the grid; L120 (CLR1) selects EVEN
    # Y-values at the grid shifted by +240.
    if layer == (241, 0):
        interior_idx, idx_shift = _m0_interior_idx(yaxis, odd=True), 0
    else:
        interior_idx, idx_shift = _m0_interior_idx(yaxis, odd=False), 240
    # Interior tracks occupy the array Y-window (ArrayPgd units). The window
    # starts one unit below the anchor at the idx-2 offset, so the bottom
    # unit's idx-0/1 tracks fall below it and its idx-2/3 tracks fall inside.
    array_lo = _M0_ANCHOR - _M0_UNIT_DB + _M0_GRID[2]
    array_hi = array_lo + ctx.target_array_pgd * _M0_UNIT_DB

    # Group chassis tracks (donate the Y-grid — constant per 2TRK family).
    bt: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    for p in polys:
        if p.key != layer:
            continue
        x0, y0, x1, y1 = p.bbox()
        bt[(y0, y1)].append((x0, x1))
    ygrid = sorted(bt)
    if len(ygrid) < 12:
        return polys
    edge = set(ygrid[:5]) | set(ygrid[-5:])

    out: list[Polygon] = [p for p in polys if p.key != layer]
    for (y0, y1) in ygrid:
        h = y1 - y0
        if (y0, y1) in edge:
            segs = _m0_edge_segs(layer, h, mid_end)      # analytic edge rails
        elif (h == 150 and _m0_track_idx(y0, idx_shift) in interior_idx
              and array_lo <= y0 < array_hi):
            segs = _m0_interior_segs(layer, h, mid_end, period)
        else:
            segs = _m0_coarse_segs(layer, h, mid_end)
        for (sx0, sx1) in segs:
            p = Polygon(layer[0], layer[1], [])
            p.set_rectangle(sx0, y0, sx1, y1)
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# V0 (56/0) and M1 (4/0) — Metal-1 stripes + Via-0 landings
# ---------------------------------------------------------------------------
# Both layers span from a fixed left origin (x=-4960) to a right edge
# determined by the target array size:
#
#     last_x = -4960 + (ArrayOgd * Ogd + 31) * 440
#
# M1 is a CONTIGUOUS grid at delta 440 (poly pitch) from -4960 to last_x.
# V0 has a 3-region grid:
#   * 10 left-buffer cols at delta 440   (x = -4960 .. -1000)
#   * N body cols at delta 880           (x = -120  .. right_x0 [- 1320 even])
#   * 10 right-buffer cols at delta 440  (ending at last_x)
# For EVEN Ogd a delta-1320 jump sits between body-end and right-buffer.
# For ODD Ogd the body's last col coincides with right-buffer's first col.
#
# Y-tracks are DONATED from the chassis via `_columns_by_signature` — same
# Pgd-track grid, same edge rails. This is byte-exact when the target
# LO1/HI1 active tracks match the chassis; when they differ, the retarget
# path in `_v0_retarget_active_tracks` shifts the LO1/HI1 track rows.

_V0M1_ORIGIN = -4960          # x_left of position 0 (constant across corpus)
_V0M1_TAIL = 31               # (n_M1_cols - 1) - Array*Ogd = ArrayOgd*Ogd + 31
_V0_LEFT_BUFFER = 10          # 10 cols delta 440 on the left (all Ogd)
_V0_RIGHT_BUFFER = 10         # 10 cols delta 440 on the right (all Ogd)
_V0_BODY_DELTA = 880          # body-region delta between vias
_V0_BUFFER_DELTA = 440        # buffer-region delta = poly pitch
_V0_JUMP = 1320               # body -> right buffer transition (even Ogd only)


def _v0m1_last_x(ctx: TransformCtx) -> int:
    """Right-edge x_left for both V0 and M1 = origin + (Array*Ogd + 31) * 440."""
    return _V0M1_ORIGIN + (ctx.target_array_ogd * ctx.target_ogd + _V0M1_TAIL) * _V0_BUFFER_DELTA


def _v0_target_cols(ctx: TransformCtx) -> list[int]:
    """Compute V0 x_left positions from target array/Ogd + fixed grid rules.

    Returns the sorted list of unique x_left values expected for the target's
    V0 (56/0) layer, following the 3-region structure described above.
    """
    last_x = _v0m1_last_x(ctx)
    # Left buffer: 10 cols at delta 440 starting at origin.
    left = [_V0M1_ORIGIN + i * _V0_BUFFER_DELTA for i in range(_V0_LEFT_BUFFER)]
    body_start = left[-1] + _V0_BODY_DELTA           # = -120
    right_x0 = last_x - (_V0_RIGHT_BUFFER - 1) * _V0_BUFFER_DELTA
    # Right buffer: 10 cols at delta 440 ending at last_x.
    right = [right_x0 + i * _V0_BUFFER_DELTA for i in range(_V0_RIGHT_BUFFER)]
    is_even = (ctx.target_ogd % 2 == 0)
    # Body last col: for even Ogd sits `_V0_JUMP` (= 1320) before right buffer.
    # For odd Ogd it coincides with the right buffer's first col (no jump).
    body_end = right_x0 - (_V0_JUMP if is_even else 0)
    n_body = (body_end - body_start) // _V0_BODY_DELTA + 1
    body = [body_start + i * _V0_BODY_DELTA for i in range(n_body)]
    return sorted(set(left) | set(body) | set(right))


def _m1_target_cols(ctx: TransformCtx) -> list[int]:
    """Contiguous M1 x_left grid at delta 440 from origin to last_x."""
    last_x = _v0m1_last_x(ctx)
    n = (last_x - _V0M1_ORIGIN) // _V0_BUFFER_DELTA + 1
    return [_V0M1_ORIGIN + i * _V0_BUFFER_DELTA for i in range(n)]


# Chassis-corpus anchor Y positions for LO1 / HI1 tracks (interior unit 0).
# Derived from the training corpus: chassis LO1 (=vt-track 14) sits at
# Y-residue 4905, chassis HI1 (=vg-track 6) sits at Y-residue 2865.
#
# The active-track Y grid is BIMODAL (verified against ISO_GDS_Correct across
# all NPZ5 / NZ5 multi-track rows): a HI band and a LO band separated by a
# +120 db offset. Within a band the pitch is 240 db/track.
#
#     track t <= 7  ->  residue = 1425 + t*240     (HI band)
#     track t >= 8  ->  residue = 1545 + t*240     (LO band)
#
# Endpoint check: track 6 -> 2865, track 7 -> 3105, track 12 -> 4425,
# track 13 -> 4665, track 14 -> 4905. All match the correct corpus exactly.
_V0_LO1_ANCHOR = 4905          # chassis LO1 (track 14) residue — identifies LO1 vias
_V0_HI1_ANCHOR = 2865          # chassis HI1 (track 6)  residue — identifies HI1 vias
_V0_TRACK_PITCH = 240
_V0_BAND_ANCHOR_HI = 1425      # tracks <= _V0_BAND_SPLIT
_V0_BAND_ANCHOR_LO = 1545      # tracks >  _V0_BAND_SPLIT
_V0_BAND_SPLIT = 7             # last track in the HI band


def _v0_track_residue(track: int) -> int:
    """Bimodal LO1 / HI1 track -> Y-residue (mod Pgd unit).

    Tracks in the HI band (``<= _V0_BAND_SPLIT``) use anchor 1425; tracks in
    the LO band (``> _V0_BAND_SPLIT``) use anchor 1545. Both bands share the
    240 db/track pitch. The 120 db inter-band offset is what makes a plain
    linear ``(t - chassis_t) * 240`` shift wrong when a target track crosses
    the band boundary.
    """
    anchor = _V0_BAND_ANCHOR_HI if track <= _V0_BAND_SPLIT else _V0_BAND_ANCHOR_LO
    return anchor + track * _V0_TRACK_PITCH


def _v0_retarget_active_tracks(
    ctx: TransformCtx,
    y_sig: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Shift chassis V0 vias landing on the chassis LO1 / HI1 tracks so they
    match the target buildsheet's ``LO1ActiveTracks`` / ``HI1ActiveTracks``.

    Each chassis V0 via at ``residue % unit_step == LO1_anchor`` is a LO1
    landing; same for the HI1 anchor. Each such via is retargeted to the
    target track's BIMODAL residue (:func:`_v0_track_residue`) — so a target
    track in the opposite band picks up the +120 db inter-band offset that a
    plain linear shift would miss. When the target lists MULTIPLE tracks for
    LO1 (or HI1), each chassis via is FANNED OUT into one via per target
    track.

    Vias that don't match either anchor (structural / boundary vias) pass
    through unchanged. Chassis LO1 / HI1 are derived from the chassis
    decoder's ``VcxPattern`` and default to the corpus values ``LO1=[14]``,
    ``HI1=[6]``.

    Returns the input ``y_sig`` unchanged when the target LO1 / HI1 equal
    the chassis's, or when either list is empty / unparseable.
    """
    def _parse(s: str) -> list[int]:
        return [int(t) for t in s.split(";") if t.strip().lstrip("-").isdigit()]

    tgt_lo = _parse(ctx.buildsheet_row.get("LO1ActiveTracks", ""))
    tgt_hi = _parse(ctx.buildsheet_row.get("HI1ActiveTracks", ""))
    chs_lo, chs_hi = _chassis_lo1_hi1(ctx)

    if not (tgt_lo and tgt_hi and chs_lo and chs_hi):
        return y_sig
    if tgt_lo == chs_lo and tgt_hi == chs_hi:
        return y_sig
    if len(chs_lo) != 1 or len(chs_hi) != 1:
        # Multi-track chassis is not currently modelled — pass through.
        return y_sig

    y_cell = ctx.target_cellheight_db // 2
    unit_step = 2 * ctx.target_pgd * y_cell

    # Residues (mod unit_step) that identify chassis LO1 / HI1 vias.
    r_lo = _V0_LO1_ANCHOR % unit_step
    r_hi = _V0_HI1_ANCHOR % unit_step

    # Bimodal delta: shift each chassis via to the target track's BIMODAL
    # residue. This picks up the +120 db inter-band offset when a target
    # track sits in the opposite band from the chassis reference track.
    chs_lo_res = _v0_track_residue(chs_lo[0])
    chs_hi_res = _v0_track_residue(chs_hi[0])
    lo_deltas = [_v0_track_residue(t) - chs_lo_res for t in tgt_lo]
    hi_deltas = [_v0_track_residue(t) - chs_hi_res for t in tgt_hi]

    out: list[tuple[int, int]] = []
    for (y0, y1) in y_sig:
        h = y1 - y0
        r = y0 % unit_step
        # Height-150 interior tracks are candidates; other heights are
        # edge rails / boundary landings that must pass through verbatim.
        if h != 150:
            out.append((y0, y1))
            continue
        if r == r_lo:
            for d in lo_deltas:
                out.append((y0 + d, y0 + h + d))
        elif r == r_hi:
            for d in hi_deltas:
                out.append((y0 + d, y0 + h + d))
        else:
            # Structural interior via (not LO1 / HI1) — keep as-is.
            out.append((y0, y1))
    return sorted(out)


def _chassis_lo1_hi1(ctx: TransformCtx) -> tuple[list[int], list[int]]:
    """Derive chassis LO1 / HI1 active-track lists from chassis VcxPattern.

    Uses the shared helper :func:`_derive_active_tracks_from_vcx` semantics
    (Y-token of the first ``vt`` segment -> LO1; Y-token of the first ``vg``
    segment -> HI1). Falls back to the corpus-wide default ``LO1=[14]``,
    ``HI1=[6]`` when the chassis VcxPattern cannot be parsed.
    """
    def _parse_y(seg_prefix: str) -> list[int]:
        pattern = ctx.chassis_decoder_row.get("VcxPattern", "")
        for seg in pattern.split(";"):
            toks = [t.strip() for t in seg.split(",")]
            if len(toks) >= 3 and toks[0].lower() == seg_prefix:
                y = toks[-1].strip()
                tracks: list[int] = []
                for frag in y.split("/"):
                    frag = frag.strip().split("-", 1)[0].strip()
                    if frag.lstrip("-").isdigit():
                        tracks.append(int(frag))
                return sorted(set(tracks))
        return []

    lo1 = _parse_y("vt") or [14]
    hi1 = _parse_y("vg") or [6]
    return lo1, hi1


def v0m1_transform(ctx: TransformCtx, polys: list[Polygon]) -> list[Polygon]:
    """Regenerate V0 (56/0) and M1 (4/0) column stripes for the target size.

    Precedence:
      * If the target and chassis share the same array X-extent AND the
        target LO1 / HI1 active tracks match the chassis, pass through
        chassis polys unchanged (byte-identical fast path).
      * Otherwise rebuild columns using the analytic grid formulas
        (:func:`_v0_target_cols` / :func:`_m1_target_cols`) and donate the
        chassis Y-signature per-column via a proportional remap (chassis
        col-index ``i`` -> target col-index ``i * (n_c - 1) / (n_t - 1)``).
        This preserves the chassis's 3-region signature (left buffer /
        interior body / right buffer) because the remap picks chassis
        cols positionally.
      * For V0 layers the interior Y-signature is retargeted onto the
        target LO1 / HI1 tracks by :func:`_v0_retarget_active_tracks`.
    """
    if not polys:
        return polys
    layer = polys[0].key
    layer_polys = [p for p in polys if p.key == layer]
    if not layer_polys:
        return polys

    # Fast path: target and chassis share the same last_x (implies same
    # ArrayOgd*Ogd) and — for V0 — the same LO1/HI1 active tracks.
    chassis_last_x = max(p.bbox()[0] for p in layer_polys)
    if chassis_last_x == _v0m1_last_x(ctx):
        if layer == (56, 0):
            def _parse(s: str) -> list[int]:
                return [int(t) for t in s.split(";") if t.strip().lstrip("-").isdigit()]
            tgt_lo = _parse(ctx.buildsheet_row.get("LO1ActiveTracks", ""))
            tgt_hi = _parse(ctx.buildsheet_row.get("HI1ActiveTracks", ""))
            chs_lo, chs_hi = _chassis_lo1_hi1(ctx)
            if tgt_lo == chs_lo and tgt_hi == chs_hi:
                return polys
        else:
            return polys

    # Rebuild path: compute target cols + donate chassis Y-signatures by
    # BUFFER-PRESERVING remap: target's 10 left-buffer cols map 1:1 to
    # chassis's 10 left-buffer cols, target's 10 right-buffer cols map 1:1
    # to chassis's 10 right-buffer cols, and target's interior cols are
    # proportionally remapped to chassis's interior. This preserves the
    # chassis's boundary col count (which carries LO1 / HI1 landings) and
    # the chassis's 3-region col-type distribution.
    if layer == (56, 0):
        target_cols = _v0_target_cols(ctx)
    else:
        target_cols = _m1_target_cols(ctx)

    by_col: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    for p in layer_polys:
        x0, y0, x1, y1 = p.bbox()
        by_col[(x0, x1)].append((y0, y1))
    chassis_cols = sorted(by_col)
    if not chassis_cols:
        return polys
    for c in chassis_cols:
        by_col[c].sort()

    poly_w = chassis_cols[0][1] - chassis_cols[0][0]
    n_c = len(chassis_cols)
    n_t = len(target_cols)
    # Preserve the 10-col left and right buffer regions (see _V0_LEFT_BUFFER /
    # _V0_RIGHT_BUFFER) 1:1 with the chassis; proportionally remap the
    # interior.
    bl_cols = _V0_LEFT_BUFFER   # = 10, same value for V0 and M1
    br_cols = _V0_RIGHT_BUFFER  # = 10

    def remap(i: int) -> int:
        # Left buffer: identity mapping preserves chassis boundary sig.
        if i < bl_cols:
            return min(i, n_c - 1)
        # Right buffer: mirror from the end.
        if i >= n_t - br_cols:
            return max(0, n_c - (n_t - i))
        # Interior: proportional between chassis interior indices, using
        # the endpoint-inclusive formula ``round(i * (N-1) / (K-1))``
        # (target[0] -> chassis[0], target[K-1] -> chassis[N-1]).
        n_c_int = max(1, n_c - bl_cols - br_cols)
        n_t_int = max(1, n_t - bl_cols - br_cols)
        ti = i - bl_cols
        if n_t_int == 1:
            return bl_cols
        ci = round(ti * (n_c_int - 1) / (n_t_int - 1))
        return max(bl_cols, min(n_c - br_cols - 1, bl_cols + ci))

    out: list[Polygon] = [p for p in polys if p.key != layer]
    for i, x0 in enumerate(sorted(target_cols)):
        x1 = x0 + poly_w
        sig = by_col[chassis_cols[remap(i)]]
        if layer == (56, 0):
            sig = _v0_retarget_active_tracks(ctx, sig)
        for (y0, y1) in sig:
            p = Polygon(layer[0], layer[1], [])
            p.set_rectangle(x0, y0, x1, y1)
            out.append(p)
    return out


def _glk_regen(ctx: TransformCtx, polys: list[Polygon], layer: tuple[int, int],
               chs_plugs: frozenset[int], tgt_plugs: frozenset[int]
               ) -> list[tuple[int, int]] | None:
    """Rebuild the per-column GLK gap signature for the TARGET poly plugs.

    GLK marks the gate-link at each plugged poly gap: a short bar sitting in
    the gap after track ``V+1`` for every plug value ``V``, tiled once per Pgd
    unit. The gap-Y line ``y0 = A + (V+1)*step`` and the bar height / unit
    stride are calibrated from the chassis GLK geometry (paired with the
    chassis plug set), then re-evaluated for the target plug set. Returns
    ``None`` when the chassis signature cannot be matched to its plugs (caller
    then donates).
    """
    by_col = _columns_by_signature([p for p in polys if p.key == layer])
    if not by_col or not chs_plugs or not tgt_plugs:
        return None
    rep = max(by_col.values(), key=len)
    n_chs = len(chs_plugs)
    if n_chs == 0 or len(rep) % n_chs != 0:
        return None
    n_units = len(rep) // n_chs
    segs = sorted(rep)
    first = segs[:n_chs]
    h = first[0][1] - first[0][0]
    unit_step = (segs[n_chs][0] - segs[0][0]) if len(segs) > n_chs \
        else 2 * ctx.target_pgd * (ctx.target_cellheight_db // 2)

    chs_sorted = sorted(chs_plugs)
    tracks = [v + 1 for v in chs_sorted]
    y0s = [s[0] for s in first]
    if len({*tracks}) >= 2:
        step = round((y0s[-1] - y0s[0]) / (tracks[-1] - tracks[0]))
    else:
        step = ctx.target_cellheight_db // 2
    base = round(y0s[0] - tracks[0] * step)

    new_sig: list[tuple[int, int]] = []
    for u in range(n_units):
        for v in sorted(tgt_plugs):
            y0 = base + (v + 1) * step + u * unit_step
            new_sig.append((y0, y0 + h))
    new_sig.sort()
    return new_sig


def glk_transform(ctx: TransformCtx, polys: list[Polygon]) -> list[Polygon]:
    """Regenerate GLKDRAWN (65/0) gate-link stripes.

    GLK marks the gate-link inside each plugged poly gap, so it TRACKS
    ``PolyPlugs`` on both axes: the **Y-axis** sets the gap positions and the
    **X-axis** sets which poly columns are plugged (GLK appears only there).
    When neither differs from the chassis the chassis signature is donated.
    """
    if not polys:
        return polys
    layer = polys[0].key

    extent = ctx.total_ogd_extent
    target_fti = set(_fti_columns(ctx)[0])
    bl, br = ctx.buffer_l, ctx.buffer_r
    interior = {c for c in range(0, extent + 1)
                if c not in target_fti and bl <= c <= extent - br}

    tgt_plugs = _tcn_plugs(ctx.buildsheet_row.get("PolyPlugsUnit", ""))
    chs_plugs = _tcn_plugs(ctx.chassis_decoder_row.get("PolyPlugs", ""))
    y_changed = (tgt_plugs is not None and chs_plugs is not None
                 and tgt_plugs != chs_plugs)
    x_changed = _plug_xaxis(ctx.decoder_row.get("PolyPlugs", "")) != \
        _plug_xaxis(ctx.chassis_decoder_row.get("PolyPlugs", ""))
    plug_cols = (_plug_columns(ctx, "PolyPlugsUnit") & interior) if x_changed else interior

    if _same_fti_grid(ctx) and not y_changed and not x_changed:
        return polys  # identical to chassis — pass through

    target_cols = sorted(plug_cols) if x_changed else _vcx_columns(ctx, "vg")
    if not target_cols:
        return polys

    if y_changed:
        new_sig = _glk_regen(ctx, polys, layer, chs_plugs, tgt_plugs)
        if new_sig is not None:
            out: list[Polygon] = [p for p in polys if p.key != layer]
            for col in target_cols:
                x0 = _poly_col_x_left(col)
                x1 = x0 + _POLY_CD_DB
                for (y0, y1) in new_sig:
                    p = Polygon(layer[0], layer[1], [])
                    p.set_rectangle(x0, y0, x1, y1)
                    out.append(p)
            return out

    return _regen_stripe_layer(polys, layer, target_cols)


def trm_transform(ctx: TransformCtx, polys: list[Polygon]) -> list[Polygon]:
    """Regenerate TRMDRAWN (509/0) peripheral trim bars.

    TRM is a frame of horizontal bars: a FIXED left periphery (negative X) and
    a right periphery / core whose right edge tracks the array width. Every X
    coordinate >= 0 shifts by ``delta = (target_extent - chassis_extent) *
    POLY_PITCH``; coordinates < 0 (the left periphery) stay put. Verified:
    chassis (-440, 70400) -> (-440, 30800) at Ogd30->12.
    """
    if not polys:
        return polys
    layer = polys[0].key
    delta = (ctx.total_ogd_extent - _chassis_extent(ctx)) * _POLY_PITCH_DB
    if delta == 0:
        return polys  # same array width — identical to chassis
    out: list[Polygon] = []
    for p in polys:
        if p.key != layer:
            out.append(p)
            continue
        x0, y0, x1, y1 = p.bbox()
        nx0 = x0 + delta if x0 >= 0 else x0
        nx1 = x1 + delta if x1 >= 0 else x1
        np = Polygon(layer[0], layer[1], [])
        np.set_rectangle(nx0, y0, nx1, y1)
        out.append(np)
    return out


def ctcn_transform(ctx: TransformCtx, polys: list[Polygon]) -> list[Polygon]:
    """Regenerate CTCNDRAWN (66/0) contacted-TCN half-pitch stripes.

    Structure (verified): a FIXED left periphery (24 half-pitch columns at
    negative index), a TCN-like ARRAY region (columns ``1..extent`` with a
    'special' 169-seg signature in the L/R buffers and 'regular' 129-seg in
    the interior), and a RIGHT periphery (24 columns just past the array that
    shift with the array width). Signatures are donated from the chassis.
    """
    if not polys:
        return polys
    if _same_fti_grid(ctx):
        return polys  # identical to chassis — pass through
    layer = polys[0].key
    x_shift = -(_POLY_PITCH_DB // 2)
    by_col = _columns_by_signature([p for p in polys if p.key == layer], x_shift)
    if not by_col:
        return polys
    ch_extent = _chassis_extent(ctx)
    extent = ctx.total_ogd_extent
    bl, br = ctx.buffer_l, ctx.buffer_r

    array_sigs = [s for c, s in by_col.items() if 1 <= c <= ch_extent]
    distinct = sorted(set(array_sigs), key=len)
    if not distinct:
        return polys
    regular_sig = distinct[0]
    special_sig = distinct[-1]

    out: list[Polygon] = [p for p in polys if p.key != layer]

    def emit(col: int, sig: tuple[tuple[int, int], ...]) -> None:
        x0 = _poly_col_x_left(col) + x_shift
        x1 = x0 + _POLY_CD_DB
        for (y0, y1) in sig:
            p = Polygon(layer[0], layer[1], [])
            p.set_rectangle(x0, y0, x1, y1)
            out.append(p)

    for c, sig in by_col.items():
        if c <= 0:                      # fixed left periphery
            emit(c, sig)
        elif c > ch_extent:             # right periphery — shift to target edge
            emit(c - ch_extent + extent, sig)
    for col in range(1, extent + 1):    # TCN-like array region
        special = col <= bl or col > extent - br
        emit(col, special_sig if special else regular_sig)
    return out


def lvsdum_transform(ctx: TransformCtx, polys: list[Polygon]) -> list[Polygon]:
    """Regenerate LVS_DUMMYGATE (81/115) dummy-gate ID stripes.

    Dummy-gate markers sit on POLY columns ``0..extent`` EXCEPT the FTI
    boundary columns (``FTITracksGlobal`` = ``0/2/r2/r0``). Each column's
    signature is 'short' (a few segments) at INTERIOR FTI columns — where the
    dummy poly is cut by the FTI — and 'full' elsewhere. Both signatures are
    donated from the chassis by segment count.
    """
    if not polys:
        return polys
    if _same_fti_grid(ctx):
        return polys  # identical to chassis — pass through
    layer = polys[0].key
    by_col = _columns_by_signature([p for p in polys if p.key == layer])
    if not by_col:
        return polys
    distinct = sorted(set(by_col.values()), key=len)
    if not distinct:
        return polys
    short_sig = distinct[0]
    full_sig = distinct[-1]

    all_fti, boundary = _fti_columns(ctx)
    interior_fti = set(all_fti) - boundary
    extent = ctx.total_ogd_extent

    # Dummy-gate stripes are cut between NDIFF/PDIFF, so their Y-segments sit
    # on the diffusion cell grid (height == DEVWIDTH, cell-centered). Recenter
    # each segment for the target DEVWIDTH so a new DEVWIDTH reshapes them
    # (identity when target and chassis DEVWIDTH match).
    y_cell = ctx.target_cellheight_db // 2
    ch_dw = ctx.chassis_devwidth_nm * _DB_PER_NM
    tgt_dw = ctx.target_devwidth_nm * _DB_PER_NM or ch_dw
    ch_margin = (y_cell - ch_dw) / 2 if y_cell else 0
    tgt_margin = (y_cell - tgt_dw) / 2 if y_cell else 0

    def recenter(sig: tuple[tuple[int, int], ...]) -> tuple[tuple[int, int], ...]:
        if y_cell <= 0 or ch_dw == tgt_dw:
            return sig
        out_segs = []
        for (cy0, cy1) in sig:
            k = round((cy0 - ch_margin) / y_cell)
            ny0 = int(round(k * y_cell + tgt_margin))
            out_segs.append((ny0, ny0 + tgt_dw))
        return tuple(out_segs)

    short_sig = recenter(short_sig)
    full_sig = recenter(full_sig)

    out: list[Polygon] = [p for p in polys if p.key != layer]
    for col in range(0, extent + 1):
        if col in boundary:
            continue  # no dummy-gate marker at FTI boundary columns
        sig = short_sig if col in interior_fti else full_sig
        x0 = _poly_col_x_left(col)
        x1 = x0 + _POLY_CD_DB
        for (y0, y1) in sig:
            p = Polygon(layer[0], layer[1], [])
            p.set_rectangle(x0, y0, x1, y1)
            out.append(p)
    return out


# VT-flavor (MG) → layer-273 device flavor-pair datatypes. SVT == the base
# (104/105) datatype, so its markers are indistinguishable from the peripheral
# base and are left untouched.
_VT_DT: dict[str, tuple[int, int]] = {
    "SVT": (104, 105),
    "HVT": (106, 107),
    "LVT": (102, 103),
    "ULVT": (170, 171),
    "ELVT": (184, 185),
}


def vtflavor_transform(ctx: TransformCtx, polys: list[Polygon]) -> list[Polygon]:
    """Retag layer-273 VT-flavor ID markers from the chassis MG flavor to the
    target MG flavor.

    The device markers carry a flavor-specific datatype pair (e.g. HVT →
    106/107, ULVT → 170/171); only the datatype changes with MG, the marker
    geometry is identical. Each registered datatype is one side of the chassis
    flavor pair, remapped to the matching side of the target pair. Donates
    unchanged when MG is unchanged or unknown.
    """
    if not polys:
        return polys
    cp = _VT_DT.get(ctx.chassis_decoder_row.get("MG", ""))
    tp = _VT_DT.get(ctx.decoder_row.get("MG", ""))
    if cp is None or tp is None or cp == tp:
        return polys
    dt = polys[0].datatype
    if dt == cp[0]:
        new = tp[0]
    elif dt == cp[1]:
        new = tp[1]
    else:
        return polys
    return [Polygon(p.layer, new, list(p.xy_lines)) for p in polys]


# Order matters: earlier transforms see chassis polygons; later transforms
# see the output of earlier ones (all currently operate on independent
# layer buckets, so order is not observable yet).
LAYER_TRANSFORMS: list[tuple[tuple[int, int], TransformFn]] = [
    ((1, 0),   diff_pattern_transform),    # NDIFFDRAWN — dynamic model
    ((8, 0),   diff_pattern_transform),    # PDIFFDRAWN — dynamic model
    ((184, 0), fti_pattern_transform),     # FTIDRAWN — dynamic model
    ((2, 0),   poly_plugs_transform),      # POLYDRAWN — column stripes
    ((5, 0),   tcn_plugs_transform),       # TCNDRAWN — half-pitch stripes
    ((31, 0),  vcx_pattern_transform),     # VTDRAWN — half-pitch vias
    ((32, 0),  vcx_pattern_transform),     # VGDRAWN — vg columns
    ((65, 0),  glk_transform),             # GLKDRAWN — vg columns
    ((66, 0),  ctcn_transform),            # CTCNDRAWN — half-pitch + periphery
    ((81, 115), lvsdum_transform),         # LVS_DUMMYGATE — poly cols minus FTI-boundary
    ((509, 0), trm_transform),             # TRMDRAWN — peripheral trim frame
    ((55, 0),  m0cut_pattern_transform),
    ((120, 0), m0cut_pattern_transform),
    ((241, 0), m0cut_pattern_transform),
    ((4, 0),   v0m1_transform),            # M1DRAWN — metal-1 contiguous grid
    ((56, 0),  v0m1_transform),            # V0DRAWN — 3-region via-0 grid
] + [
    # VT-flavor ID markers (layer 273): remap the device flavor-pair datatype
    # from the chassis MG flavor to the target MG flavor.
    ((273, dt), vtflavor_transform)
    for dt in (102, 103, 106, 107, 170, 171, 184, 185)
]


# ---------------------------------------------------------------------------
# Emitter — replays chassis with per-layer polygon substitution.
# ---------------------------------------------------------------------------

def _rewrite_header(lines: list[str], strname: str) -> list[str]:
    """Rewrite BGNLIB / BGNSTR timestamps and STRNAME."""
    ts = time.strftime("%m/%d/%Y %H:%M:%S")
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if _BGN_RE.match(line):
            head = s.split()[0]
            out.append(f"{head} {ts} {ts} ")
        elif _STRNAME_RE.match(line):
            out.append(f"STRNAME {strname}")
        else:
            out.append(line)
    return out


def _emit_polygon(poly: Polygon, layer_info: LayerInfo | None) -> list[str]:
    lines: list[str] = []
    if layer_info is not None:
        lines.append(layer_info.comment)
    lines.append("BOUNDARY ")
    lines.append(f"LAYER {poly.layer} ")
    lines.append(f"DATATYPE {poly.datatype} ")
    lines.extend(poly.xy_lines)
    lines.append("ENDEL ")
    return lines


def _safe_filename(structure_name: str) -> str:
    """Sanitise a StructureName to a filesystem-friendly stem."""
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", structure_name).strip("_")
    return f"{stem}.txt"


def generate_one(
    target_row: dict[str, str],
    buildsheet_row: dict[str, str],
    corpus: dict[str, ChassisFile],
    decoder_rows: dict[str, dict[str, str]],
    layer_map: dict[tuple[int, int], LayerInfo],
    out_dir: Path,
) -> Path:
    """Generate one output txt file for ``target_row``.

    Returns the written path.
    """
    lf, chassis = _pick_chassis(target_row, corpus, decoder_rows)
    chassis_decoder_row = next(
        (d for f, d in decoder_rows.items() if f == lf), {}
    )
    ctx = TransformCtx(
        decoder_row=target_row,
        buildsheet_row=buildsheet_row,
        chassis_decoder_row=chassis_decoder_row,
        chassis=chassis,
    )

    # Apply per-layer transforms (deep-copy polygons so we don't mutate
    # the shared chassis inventory).
    working: dict[tuple[int, int], list[Polygon]] = {
        key: [Polygon(p.layer, p.datatype, list(p.xy_lines)) for p in polys]
        for key, polys in chassis.inventory.by_layer.items()
    }
    for key, fn in LAYER_TRANSFORMS:
        if key in working:
            working[key] = fn(ctx, working[key])

    # Rewrite header + emit stream.
    strname = _safe_filename(target_row.get("StructureName", "unnamed"))[:-4]
    out_lines: list[str] = []
    header_done = False
    for kind, payload in chassis.line_events:
        if kind == "text":
            line = payload  # type: ignore[assignment]
            s = line.strip() if isinstance(line, str) else ""  # type: ignore[union-attr]
            if _BGN_RE.match(s or ""):
                ts = time.strftime("%m/%d/%Y %H:%M:%S")
                head = s.split()[0]
                out_lines.append(f"{head} {ts} {ts} ")
                continue
            if _STRNAME_RE.match(s or ""):
                out_lines.append(f"STRNAME {strname}")
                header_done = True
                continue
            out_lines.append(line)  # type: ignore[arg-type]
        else:  # kind == 'poly'
            key = payload  # type: ignore[assignment]
            layer_info = layer_map.get(key)  # type: ignore[arg-type]
            for poly in working.get(key, []):  # type: ignore[arg-type]
                out_lines.extend(_emit_polygon(poly, layer_info))

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / _safe_filename(target_row.get("StructureName", "unnamed"))
    out_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def load_csv(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_corpus(training_dir: Path, decoder_rows: Iterable[dict[str, str]]) -> dict[str, ChassisFile]:
    corpus: dict[str, ChassisFile] = {}
    for drow in decoder_rows:
        lf = drow.get("LayoutFiles", "").strip()
        if not lf:
            continue
        p = training_dir / lf
        if not p.exists():
            print(f"[warn] chassis file missing: {p}", file=sys.stderr)
            continue
        corpus[lf] = parse_chassis(p)
    return corpus


def index_buildsheet(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {r.get("StructureName", ""): r for r in rows}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--decoder", type=Path, default=DEFAULT_DECODER,
        help=(
            "Generation input CSV: rows to produce GDS files for. May omit "
            "the LayoutFiles column. Defaults to the training corpus so "
            "self-training round-trips out-of-the-box."
        ),
    )
    ap.add_argument(
        "--training-decoder", type=Path, default=DEFAULT_TRAINING_DECODER,
        help=(
            "Training corpus CSV: MUST have a LayoutFiles column pointing "
            "at chassis .txt files under --training-dir. Used to learn "
            "geometry; separate from --decoder so you can generate for new "
            "datasets that don't have LayoutFiles."
        ),
    )
    ap.add_argument("--buildsheet", type=Path, default=DEFAULT_BUILDSHEET)
    ap.add_argument("--training-dir", type=Path, default=DEFAULT_TRAINING)
    ap.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--layer-map", type=Path, default=DEFAULT_LAYER_MAP)
    ap.add_argument(
        "--only", nargs="*", default=None,
        help="Only generate for these StructureName values (default: all).",
    )
    args = ap.parse_args(argv)

    for label, p in (
        ("decoder", args.decoder),
        ("training-decoder", args.training_decoder),
        ("buildsheet", args.buildsheet),
        ("layer-map", args.layer_map),
    ):
        if not p.exists():
            print(f"[error] {label} not found: {p}", file=sys.stderr)
            return 2

    decoder_rows = load_csv(args.decoder)
    training_rows = load_csv(args.training_decoder)
    buildsheet_rows = load_csv(args.buildsheet)
    layer_map = load_layer_map(args.layer_map)

    # index TRAINING rows by LayoutFiles for corpus parsing + chassis picker.
    dec_by_lf: dict[str, dict[str, str]] = {
        r.get("LayoutFiles", ""): r for r in training_rows if r.get("LayoutFiles")
    }
    if not dec_by_lf:
        print(
            f"[error] {args.training_decoder.name} has no rows with a "
            f"non-empty LayoutFiles column — cannot build training corpus",
            file=sys.stderr,
        )
        return 3
    bs_by_name = index_buildsheet(buildsheet_rows)

    print(f"Loading corpus from {args.training_dir} ...", file=sys.stderr)
    corpus = load_corpus(args.training_dir, dec_by_lf.values())
    if not corpus:
        print(
            f"[error] no training chassis files parsed from "
            f"{args.training_dir} (checked {len(dec_by_lf)} LayoutFiles "
            f"entries in {args.training_decoder.name})",
            file=sys.stderr,
        )
        return 3
    print(f"  parsed {len(corpus)} chassis file(s)", file=sys.stderr)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    only = set(args.only) if args.only else None

    generated = 0
    for target in decoder_rows:
        name = target.get("StructureName", "")
        if only is not None and name not in only:
            continue
        bs = bs_by_name.get(name, {})
        if not bs:
            print(f"[warn] {name}: no buildsheet row — using empty context",
                  file=sys.stderr)
        try:
            out_path = generate_one(
                target_row=target,
                buildsheet_row=bs,
                corpus=corpus,
                decoder_rows=dec_by_lf,
                layer_map=layer_map,
                out_dir=args.output_dir,
            )
        except Exception as exc:  # pragma: no cover — defensive
            print(f"[error] {name}: {exc}", file=sys.stderr)
            continue
        # Prefer a workspace-relative path for readability; fall back to
        # absolute when the output is outside BASE_DIR (e.g. user passed
        # --output-dir as an absolute path elsewhere on disk).
        try:
            display = out_path.resolve().relative_to(BASE_DIR)
        except ValueError:
            display = out_path
        print(f"  wrote {display}", file=sys.stderr)
        generated += 1

    print(f"Generated {generated} file(s) to {args.output_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
