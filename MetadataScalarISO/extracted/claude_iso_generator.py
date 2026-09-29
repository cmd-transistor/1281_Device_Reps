"""ISO Generator from Decoder input — Dynamic Model (1281 Node / ISO Module).

Reads ISO_Decoder.csv and ISO_Mini.csv as training data, translates new decoder
input rows into ISO buildsheet format using a dynamic tuned engine.

Key features
------------
* **Reusable DSL expander** for pattern columns: any decoder column paired
  with a buildsheet column via ``PATTERN_COL_PAIRS`` is expanded by
  :func:`_expand_pattern`. The grammar is axis-aware — X-axis expansions
  reference ``UnitCellSizeOgd``, Y-axis expansions reference ``UnitCellSizePgd``.
* **Dynamic mapping** for other varying columns (StructureComments, CellHeight,
  DiffPatternUnit, DiffPatternGlobal, M0CutPatternUnit, DummyViaPattern,
  VcxPatternUnit). LO1ActiveTracks / HI1ActiveTracks are sourced directly
  from optional same-named decoder columns when supplied, otherwise derived
  from the expanded VcxPatternUnit or the training index. Values derived
  from a minimal set of decoder-driven keys — no hard-coded per-row lookup.
* **Auto-discovered constants** for the 40+ empty/constant tail columns.

Adding a new pattern column
---------------------------
Append the (decoder_col, buildsheet_col) tuple to :data:`PATTERN_COL_PAIRS`.
No other change required — the DSL parser handles the rest.

Decoder DSL grammar (per axis, X uses Ogd, Y uses Pgd cell size)
----------------------------------------------------------------
* ``All``                              -> ``0:r0`` (entire array)
* ``START``                            -> literal (single position)
* ``START:END``                        -> literal range (e.g. ``2:5``)
* ``A/B`` or ``A/B/C``                 -> literal (e.g. ``3/4``, ``3/5``)
* ``START:Skip:N:Repeat``              -> ``START:<Cell>[N]`` (stride form)
* ``START:END:Skip:GAP:Repeat``        -> repeated segments filling one Cell

ResizeShift column (physical-shift markers)
-------------------------------------------
Optional decoder column ``ResizeShift`` supplies per-layer 4-tuple shifts
that are appended (as ``|d1,d2,d3,d4``) to matching segments in the
expanded buildsheet columns. Grammar::

    ResizeShift = "layer,d1,d2,d3,d4[;layer,d1,d2,d3,d4]..."

Layer -> target column mapping:

* ``vg``     -> VcxPatternUnit (only ``vg,`` prefixed segments)
* ``vt``     -> VcxPatternUnit (only ``vt,`` prefixed segments)
* ``vcr``    -> VcrPatternUnit (all segments)
* ``vcrnub`` -> VcrnubPatternUnit (all segments)

Example
-------
::

    FTIPattern = "0:Skip:3:Repeat,All"    with Ogd=24, Pgd=4
                -> FTITracksUnit = "0:24[3],0:r0"

    PolyPlugs  = "1:3:Skip:1:Repeat,3/4"  with Ogd=24
                -> PolyPlugsUnit = "1:3/5:7/9:11/13:15/17:19/21:23,3/4"

    VcrNubPattern = "2:Skip:3:Repeat,4/5,bottom" with Ogd=24
                -> VcrnubPatternUnit = "2/6/10/14/18/22,4/5,bottom"

    ResizeShift = "vg,0.002,0.0,0.0,0.0" applied to
                VcxPatternUnit = "vg,1:3/...,6; vt,0:r0[3],14; ..."
                -> "vg,1:3/...,6|0.002,0.0,0.0,0.0; vt,0:r0[3],14; ..."
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_TRAINING_DECODER = BASE_DIR / "ISO_Decoder.csv"
DEFAULT_TRAINING_ISO = BASE_DIR / "ISO_Mini.csv"
DEFAULT_PADCONFIG = BASE_DIR / "ISO_PadConfig.csv"
DEFAULT_OUTPUT = BASE_DIR / "ISO_generated.csv"

ENGINES = ["tuned"]

# ---------------------------------------------------------------------------
# Decoder columns (input schema)
# ---------------------------------------------------------------------------
# LO1ActiveTracks / HI1ActiveTracks are OPTIONAL decoder columns. When
# supplied (non-empty), they override the value derived from VcxPatternUnit
# and become the authoritative buildsheet value for that row. This lets
# users author custom active-track lists for structures whose VcxPattern
# has only ``vg`` or only ``vt`` segments (e.g. the ``GATEETE``/``TCNETE``
# training rows), or reorder tracks independently of the DSL expansion.
DECODER_COLS = [
    "StructureName", "MOS", "MG", "CELLHEIGHT", "DEVWIDTH",
    "UnitCellSizeOgd", "UnitCellSizePgd", "ArraySizeOgd", "ArraySizePgd",
    "FTIPattern", "PolyPlugs", "TCNPlugs", "VcxPattern", "M0CutPattern",
    "LO1ActiveTracks", "HI1ActiveTracks",
    "VcrPattern", "VcrNubPattern",
    # ResizeShift — optional 4-tuple physical-shift markers appended to
    # matching segments in VcxPatternUnit / VcrPatternUnit / VcrnubPatternUnit.
    # See :func:`_parse_resize_shift` for grammar.
    "ResizeShift",
]

# ---------------------------------------------------------------------------
# Pattern column pairs — decoder DSL -> buildsheet syntax.
# Adding a new pattern column: append (decoder_col, buildsheet_col). Nothing
# else has to change; the DSL parser is generic across axes.
# ---------------------------------------------------------------------------
PATTERN_COL_PAIRS: list[tuple[str, str]] = [
    ("FTIPattern", "FTITracksUnit"),
    ("PolyPlugs", "PolyPlugsUnit"),
    ("TCNPlugs", "TcnPlugsUnit"),
    ("VcxPattern", "VcxPatternUnit"),
    ("M0CutPattern", "M0CutPatternUnit"),
    ("VcrPattern", "VcrPatternUnit"),
    ("VcrNubPattern", "VcrnubPatternUnit"),
]

# Buildsheet columns whose STRIDE-form DSL (``START:Skip:N:Repeat``) expands
# to a literal expanded list (``START/START+(N+1)/START+2(N+1)/...``) rather
# than the compact stride notation (``START:cell[N]``) used by FTIPattern etc.
# Physically ``Skip:N`` in this style means "gap of N slots between successive
# positions", so consecutive positions are spaced by ``N + 1``.
_EXPANDED_STRIDE_COLS: frozenset[str] = frozenset({"VcrnubPatternUnit"})

# ---------------------------------------------------------------------------
# Validation sets
# ---------------------------------------------------------------------------
VALID_MOS = {"N", "NP", "P"}
VALID_MG = {"LVT", "SVT", "HVT", "ULVT", "ULVTLL", "ELVT"}
VALID_CELLHEIGHT = {110, 132, 165}


# ---------------------------------------------------------------------------
# DSL EXPANDER (the star of the model)
# ---------------------------------------------------------------------------

_SKIP_REPEAT_STRIDE_RE = re.compile(
    r"^\s*(?P<start>-?\d+)\s*:\s*Skip\s*:\s*(?P<stride>-?\d+)\s*:\s*Repeat\s*$",
    re.IGNORECASE,
)
_SKIP_REPEAT_SEG_RE = re.compile(
    r"^\s*(?P<start>-?\d+)\s*:\s*(?P<end>-?\d+)\s*:\s*Skip\s*:\s*(?P<gap>-?\d+)\s*:\s*Repeat\s*$",
    re.IGNORECASE,
)
# Stride-literal form (no ``Repeat`` keyword): ``START:END:Skip:N`` -> ``START:END[N]``.
# START and END may be integers or reverse tokens like ``r0``, ``r1``.
_STRIDE_LITERAL_RE = re.compile(
    r"^\s*(?P<start>-?[\w]+)\s*:\s*(?P<end>-?[\w]+)\s*:\s*Skip\s*:\s*(?P<stride>-?\d+)\s*$",
    re.IGNORECASE,
)

# Y-value modifiers that appear on active-track selectors (e.g. ``7-bottom``).
# These are stripped when deriving LO1/HI1 active-track lists.
_Y_MODIFIER_RE = re.compile(r"-[A-Za-z]+")


def _expand_axis(spec: str, cell_size: int, iso_col: str = "") -> str:
    """Expand a single-axis DSL fragment.

    Rules (checked in order):

    * ``"All"`` (case-insensitive)  -> ``"0:r0"``.
    * Empty spec                    -> ``""`` (caller decides how to render).
    * ``"START:Skip:N:Repeat"``     -> ``"START:{cell_size}[N]"`` (stride
      notation), OR — when ``iso_col`` is in :data:`_EXPANDED_STRIDE_COLS` —
      the literal expanded list ``"START/START+(N+1)/..."`` up to
      ``cell_size - 1`` (used by ``VcrnubPatternUnit``).
    * ``"START:END:Skip:GAP:Repeat"`` -> segmented list of ranges filling
      the axis up to ``cell_size - 1``:
      first segment starts at ``START``, spans ``END-START+1`` positions,
      next segment starts at ``END + 1 + GAP``, and so on.
    * ``"START:END:Skip:N"`` (no Repeat) -> ``"START:END[N]"`` (stride notation).
      END may be a reverse token like ``r0``. Used by VcxPattern-style specs.
    * Anything else is treated as a literal and passed through unchanged
      (e.g. ``"2:5"``, ``"3/4"``, ``"0:r0"``, single track number).

    Multiple fragments joined by ``/`` (e.g. ``"1:3:Skip:1/r3:r1:Skip:1"``)
    are expanded independently and rejoined with ``/``. This lets a single
    axis emit multiple stride-literal ranges (used by ``M0CutPattern``).

    ``iso_col`` — target buildsheet column, used only to opt into
    column-specific expansion styles (see :data:`_EXPANDED_STRIDE_COLS`).
    """
    s = spec.strip()
    if not s:
        return ""
    # Multi-fragment axis: split on '/' and expand each piece independently.
    # Backward compatible because literal '/-values (e.g. "3/4", "6/14") pass
    # through the fragment expander unchanged.
    if "/" in s:
        return "/".join(
            _expand_axis_fragment(p, cell_size, iso_col) for p in s.split("/")
        )
    return _expand_axis_fragment(s, cell_size, iso_col)


def _expand_axis_fragment(part: str, cell_size: int, iso_col: str = "") -> str:
    """Expand a single (non-``/``-separated) DSL axis fragment."""
    s = part.strip()
    if not s:
        return ""
    if s.lower() == "all":
        return "0:r0"

    m = _SKIP_REPEAT_STRIDE_RE.match(s)
    if m:
        start = int(m.group("start"))
        stride = int(m.group("stride"))
        if iso_col in _EXPANDED_STRIDE_COLS:
            # Expanded-literal form: emit START, START+(N+1), START+2(N+1), ...
            # while position stays within the cell (<= cell_size - 1).
            # Skip:N here means "N vacant slots between positions", so the
            # physical step is N + 1.
            step = stride + 1
            positions: list[str] = []
            cur = start
            while cur <= cell_size - 1:
                positions.append(str(cur))
                cur += step
            return "/".join(positions)
        return f"{start}:{cell_size}[{stride}]"

    m = _SKIP_REPEAT_SEG_RE.match(s)
    if m:
        start = int(m.group("start"))
        end = int(m.group("end"))
        gap = int(m.group("gap"))
        seg_len = end - start + 1
        segments: list[str] = []
        cur = start
        # emit while the segment's END stays within the cell (<= cell_size - 1)
        while cur + seg_len - 1 <= cell_size - 1:
            segments.append(f"{cur}:{cur + seg_len - 1}")
            cur = cur + seg_len + gap
        return "/".join(segments)

    m = _STRIDE_LITERAL_RE.match(s)
    if m:
        return f"{m.group('start')}:{m.group('end')}[{m.group('stride')}]"

    # Literal (e.g. "2:5", "0:r0", "14", "3", "7-bottom", etc.)
    return s


# First token of a 3-token segment is treated as an alpha prefix (VcxPattern
# ``vg``/``vt`` style) only when it is entirely alphabetic. Otherwise the
# segment is parsed as ``X,Y,modifier`` — the third token is a placement
# annotation (``top``/``bottom``/``both``) used by VcrNubPattern.
_ALPHA_PREFIX_RE = re.compile(r"^[A-Za-z]+$")


def _expand_segment(
    segment: str,
    unit_ogd: int,
    unit_pgd: int,
    iso_col: str = "",
) -> str:
    """Expand a single DSL segment into buildsheet syntax.

    Supported shapes:

    * 2 comma-separated tokens -> ``"X,Y"``: X expanded with Ogd, Y with Pgd.
    * 3 comma-separated tokens with an ALPHA first token (e.g. ``vg``,
      ``vt``) -> ``"prefix,X,Y"``: prefix passed through, X expanded with
      Ogd, Y expanded with Pgd. Used by ``VcxPattern``.
    * 3 comma-separated tokens whose first token is an axis spec ->
      ``"X,Y,modifier"``: X expanded with Ogd, Y expanded with Pgd, third
      token passed through as a placement modifier (``top``/``bottom``/
      ``both``). Used by ``VcrNubPattern``.
    * Otherwise passed through unchanged.
    """
    seg = segment.strip()
    if not seg:
        return ""
    tokens = [t.strip() for t in seg.split(",")]
    if len(tokens) == 2:
        x = _expand_axis(tokens[0], unit_ogd, iso_col)
        y = _expand_axis(tokens[1], unit_pgd, iso_col)
        return f"{x},{y}" if y else x
    if len(tokens) == 3:
        first, second, third = tokens
        if _ALPHA_PREFIX_RE.match(first):
            # prefix,X,Y (VcxPattern style)
            x = _expand_axis(second, unit_ogd, iso_col)
            y = _expand_axis(third, unit_pgd, iso_col)
            return f"{first},{x},{y}"
        # X,Y,modifier (VcrNubPattern style)
        x = _expand_axis(first, unit_ogd, iso_col)
        y = _expand_axis(second, unit_pgd, iso_col)
        return f"{x},{y},{third}"
    return seg


def _expand_pattern(
    pattern: str,
    unit_cell_size_ogd: int,
    unit_cell_size_pgd: int,
    iso_col: str = "",
) -> str:
    """Expand a decoder DSL string into buildsheet syntax.

    Supports both single-segment (``X,Y``, ``prefix,X,Y``, or ``X,Y,modifier``)
    and multi-segment (``seg1;seg2;seg3``) forms. Segments are trimmed and
    rejoined with ``"; "``. Trailing empty segments are dropped so an
    input ending with ``;`` produces the same output as one without.

    Empty pattern -> empty output (represents a column intentionally
    disabled for a given row, e.g. ``PolyPlugs`` for TYPE B rows).

    The X-axis (before the first comma of each segment) uses
    ``UnitCellSizeOgd`` when expanding Skip:Repeat forms; the Y-axis
    uses ``UnitCellSizePgd``. Both axes are parsed with identical
    grammar.

    ``iso_col`` — the target buildsheet column, used to opt into
    column-specific expansion styles (e.g. VcrnubPatternUnit's expanded
    stride form). Optional; default preserves the historical behavior.
    """
    if pattern is None or not str(pattern).strip():
        return ""

    # Multi-segment: split on ';' (any trailing empty segments discarded).
    if ";" in pattern:
        segs = [s.strip() for s in pattern.split(";") if s.strip()]
        return "; ".join(
            _expand_segment(s, unit_cell_size_ogd, unit_cell_size_pgd, iso_col)
            for s in segs
        )

    return _expand_segment(
        pattern, unit_cell_size_ogd, unit_cell_size_pgd, iso_col
    )


def _derive_active_tracks_from_vcx(vcx_pattern_unit: str) -> tuple[str, str]:
    """Extract ``(HI1, LO1)`` active-track lists from an expanded VcxPatternUnit.

    * HI1 = Y-value of the first ``vg`` segment.
    * LO1 = Y-value of the first ``vt`` segment.

    In both cases the raw Y is normalised:
      * ``/`` separators become ``;`` (buildsheet active-tracks convention);
      * modifiers like ``-bottom``, ``-top`` are stripped (they annotate
        placement inside a shape, not the track list);
      * any trailing ResizeShift marker ``|d1,d2,d3,d4`` is stripped BEFORE
        the Y-value is read (otherwise the last shift delta would be
        misparsed as the Y value).

    Returns ``("", "")`` if the pattern is empty or unparseable.
    """
    if not vcx_pattern_unit or not vcx_pattern_unit.strip():
        return "", ""
    hi1, lo1 = "", ""
    for seg in vcx_pattern_unit.split(";"):
        s = seg.strip()
        if not s:
            continue
        # Drop any "|d1,d2,d3,d4" ResizeShift suffix so the Y value below
        # is the segment's true Y and not a shift delta.
        s = s.split("|", 1)[0].rstrip()
        # Segment shape: "<prefix>,<X>,<Y>". Grab Y after the last comma.
        parts = s.rsplit(",", 1)
        if len(parts) != 2:
            continue
        prefix_part, y_raw = parts[0], parts[1].strip()
        y_clean = _Y_MODIFIER_RE.sub("", y_raw).replace("/", ";")
        prefix = prefix_part.split(",", 1)[0].strip().lower()
        if not hi1 and prefix == "vg":
            hi1 = y_clean
        elif not lo1 and prefix == "vt":
            lo1 = y_clean
        if hi1 and lo1:
            break
    return hi1, lo1


# ---------------------------------------------------------------------------
# ResizeShift DSL — 4-tuple physical-shift markers per patterning layer
# ---------------------------------------------------------------------------
#
# Decoder syntax
# --------------
# ``ResizeShift = "layer,d1,d2,d3,d4[;layer2,d1,d2,d3,d4]..."``
#
# Each group is a ``layer`` name followed by four floating-point deltas.
# Groups are joined by ``;``. Empty / missing cell -> no shift applied.
#
# Layer -> target buildsheet column
# ---------------------------------
# * ``vg``     -> :data:`VcxPatternUnit` (only ``vg,...`` prefixed segments)
# * ``vt``     -> :data:`VcxPatternUnit` (only ``vt,...`` prefixed segments)
# * ``vcr``    -> :data:`VcrPatternUnit` (every segment)
# * ``vcrnub`` -> :data:`VcrnubPatternUnit` (every segment)
#
# Application
# -----------
# The 4-tuple is appended after each matching segment's base spec, joined
# by ``|`` — this matches the buildsheet convention seen in training data:
#   ``vg,1:3/5:7/9:11/13:15/17:19/21:23,6|0.002,0.0,0.0,0.0``
#
# When a segment already carries a ``|...`` suffix (e.g. from a training
# index fallback that baked in the shift), that suffix is REPLACED with
# the current decoder-supplied shift — so decoder edits always win.

_RESIZE_SHIFT_TARGETS: dict[str, str] = {
    "vg":     "VcxPatternUnit",
    "vt":     "VcxPatternUnit",
    "vcr":    "VcrPatternUnit",
    "vcrnub": "VcrnubPatternUnit",
}

# Buildsheet columns whose segments use an alpha prefix as the first token
# (e.g. ``vg,``/``vt,``). For these columns each segment's shift is picked
# by matching that prefix against the ResizeShift layer name. For columns
# not listed here (VcrPatternUnit, VcrnubPatternUnit) every segment
# receives the single shift declared for that column's layer.
_PREFIXED_SEGMENT_COLS: frozenset[str] = frozenset({"VcxPatternUnit"})


def _parse_resize_shift(spec: str) -> dict[str, str]:
    """Parse a ResizeShift DSL string into ``{layer: "d1,d2,d3,d4"}``.

    Malformed groups (fewer than 5 tokens) are silently skipped. Layer
    names are lower-cased so decoder authoring is case-insensitive.
    """
    if not spec or not str(spec).strip():
        return {}
    shifts: dict[str, str] = {}
    for group in str(spec).split(";"):
        g = group.strip()
        if not g:
            continue
        parts = [p.strip() for p in g.split(",")]
        if len(parts) < 5:
            continue
        layer = parts[0].lower()
        if layer not in _RESIZE_SHIFT_TARGETS:
            continue
        shifts[layer] = ",".join(parts[1:5])
    return shifts


def _apply_resize_shift(
    value: str, iso_col: str, shifts: dict[str, str]
) -> str:
    """Apply parsed ResizeShift entries to a buildsheet column value.

    * Only shifts whose target column matches ``iso_col`` are considered.
    * For prefixed-segment columns (VcxPatternUnit) each segment's leading
      alpha token (``vg``/``vt``) picks the shift; segments whose prefix
      isn't in the shifts dict pass through unchanged.
    * For plain columns (VcrPatternUnit / VcrnubPatternUnit) every segment
      receives the shift declared for that column's layer.
    * Any pre-existing ``|...`` suffix on a segment is replaced.
    * Original whitespace / trailing separator quirks are preserved.
    """
    if not value or not shifts:
        return value
    applicable = {
        layer: sh for layer, sh in shifts.items()
        if _RESIZE_SHIFT_TARGETS.get(layer) == iso_col
    }
    if not applicable:
        return value

    is_prefixed = iso_col in _PREFIXED_SEGMENT_COLS
    default_shift = "" if is_prefixed else next(iter(applicable.values()))

    out_parts: list[str] = []
    for raw_seg in value.split(";"):
        stripped = raw_seg.strip()
        if not stripped:
            out_parts.append(raw_seg)
            continue
        # Preserve leading whitespace so multi-segment formatting is stable.
        leading = raw_seg[: len(raw_seg) - len(raw_seg.lstrip())]
        # Strip any existing "|d1,d2,d3,d4" suffix (replace-on-apply).
        base, sep, _existing = stripped.partition("|")
        base = base.rstrip()

        shift_for_seg: str
        if is_prefixed:
            first_tok = base.split(",", 1)[0].strip().lower()
            shift_for_seg = applicable.get(first_tok, "")
        else:
            shift_for_seg = default_shift

        if shift_for_seg:
            out_parts.append(f"{leading}{base}|{shift_for_seg}")
        elif sep:
            # No shift for this segment but it had one before -> keep original.
            out_parts.append(raw_seg)
        else:
            out_parts.append(raw_seg)
    return ";".join(out_parts)




# StructureName format: "CTG{CH}-{MOS+Z}-{VT}-{TYPE}:{VARIANT}:OGD:REF:{TRACKS}"
# Grab the segment after the third dash, then the letter before the first colon.
_TYPE_RE = re.compile(r"^[^-]+-[^-]+-[^-]+-(?P<type>[A-Z])(?::|$)")


def _extract_type(structure_name: str) -> str:
    """Return TYPE letter (``A/B/C/D``) parsed from StructureName, else ``""``."""
    m = _TYPE_RE.match(structure_name or "")
    return m.group("type") if m else ""


# ---------------------------------------------------------------------------
# Dynamic mapping tables for the other varying columns
# ---------------------------------------------------------------------------

# NOTE ON APPROACH ----------------------------------------------------------
# The generator combines two strategies for the non-DSL varying columns:
#
#  1. A *decoder-parameter-keyed training index*, built once at engine start,
#     that maps ``(MOS, MG, CELLHEIGHT, TYPE)`` (or similar tuples) to the
#     exact buildsheet value observed in the training set. This gives an
#     exact match for any input that already exists in training data without
#     hard-coding row-level lookups.
#
#  2. A *dynamic formula fallback* (implemented in the ``_generate_*``
#     helpers below) that composes the buildsheet value from decoder
#     attributes when the input tuple is unseen. The formulas capture the
#     rules extracted from the training data (e.g. TOP/BOT devflav swap on
#     MOS polarity, ``0:4/r3:r0`` insertion on odd primary tracks in
#     TYPE B/C/D, stride ``[3]`` for NP-A vs ``[7]`` otherwise).
#
# This is *not* a per-row lookup table: the keys are compact and reflect the
# actual physical drivers. New (MOS, MG, CH, TYPE) tuples that were never
# trained produce a synthesised value from the formula, not an error.

# DiffPatternUnit MOS mapping to primary/secondary devflav ordering.
# For each MOS the pattern is a 2-entry template; the devflav prefix comes
# from MG (lowercased) with n/p prefix from the diffusion side.
def _generate_diff_pattern_unit(mos: str, mg: str, devwidth: int, dtype: str) -> str:
    """DiffPatternUnit is (MOS, MG, DEVWIDTH, TYPE)-driven.

    The pattern is dynamic per axis:

    * TYPE A / B / C with MOS in {N, P} -> reference lattice
      "0:r0,0:1/6:7,{w},{sec}{mg};0:r0,2:5,{w},{prim}{mg};"
    * TYPE A / B / C with MOS NP        -> split-diff
      "0:r0,2:3/6:7,{w},{prim}{mg};0:r0,0:1/4:5,{w},{sec}{mg};"
    * TYPE D with MOS N or NP           -> per-track split
      "0:r0,0/3:4/7,{w},{sec}{mg};0:r0,1:2/5:6,{w},{prim}{mg};"
    * TYPE D with MOS P                 -> per-track split (flipped)
      "0:r0,0/3:4/7,{w},{prim}{mg};0:r0,1:2/5:6,{w},{sec}{mg};"

    prim = same polarity as MOS (n for N, p for P); NP treats N as primary.
    """
    w = _format_width(devwidth)
    mg_low = mg.lower()

    # primary (own device) and secondary (opposite) polarity letters
    if mos == "P":
        prim, sec = "p", "n"
    else:
        prim, sec = "n", "p"

    if dtype == "D":
        # per-track "0/3:4/7" grid; NP behaves like N here
        if mos == "P":
            return (
                f"0:r0,0/3:4/7,{w},{prim}{mg_low};"
                f"0:r0,1:2/5:6,{w},{sec}{mg_low};"
            )
        return (
            f"0:r0,0/3:4/7,{w},{sec}{mg_low};"
            f"0:r0,1:2/5:6,{w},{prim}{mg_low};"
        )

    if mos == "NP":
        return (
            f"0:r0,2:3/6:7,{w},{prim}{mg_low};"
            f"0:r0,0:1/4:5,{w},{sec}{mg_low};"
        )
    # MOS in {N, P}
    return (
        f"0:r0,0:1/6:7,{w},{sec}{mg_low};"
        f"0:r0,2:5,{w},{prim}{mg_low};"
    )


def _generate_diff_pattern_global(mos: str, mg: str, devwidth: int, dtype: str) -> str:
    """DiffPatternGlobal is (MOS, MG, DEVWIDTH, TYPE)-driven.

    Consists of two boundary tracks + a per-column expansion whose stride
    depends on TYPE (A: [7], NP-A: [3], B/C/D: [7]) and whose track set
    depends on MOS. Type D uses ``0/r0`` for the boundary tracks in NP mode.
    """
    w = _format_width(devwidth)
    mg_low = mg.lower()

    if mos == "P":
        prim, sec = "p", "n"
    else:
        prim, sec = "n", "p"

    # boundary tracks (top/bottom of array)
    if dtype == "D" and mos == "NP":
        top = f"0:r0,0/r0,{w},{sec}{mg_low}"
        bot = f"0:r0,1/r1,{w},{prim}{mg_low}"
    else:
        top = f"0:r0,0:1,{w},{sec}{mg_low}"
        bot = f"0:r0,r1:r0,{w},{prim}{mg_low}"

    # stride: NP-A -> [3]; else [7]
    stride = 3 if (mos == "NP" and dtype == "A") else 7

    # column expansion (tracks 2..3 primary side, 4..5 secondary side for A,
    # or 2..3 + 8..9 primary / 4..7 secondary for non-A). Values verified.
    if dtype == "A":
        prim_tracks = (2, 3)
        sec_tracks = (4, 5)
    else:
        # B / C / D use the same 10-track band 2..9
        prim_tracks = (2, 3, 8, 9)
        sec_tracks = (4, 5, 6, 7)

    if dtype == "D" and mos == "P":
        # flipped variant
        prim_tracks, sec_tracks = sec_tracks, prim_tracks

    parts: list[str] = [top + ";", " " + bot + ";"]
    for t in prim_tracks:
        parts.append(f" 0:3/r3:r0,{t}:r2[{stride}],{w},{prim}{mg_low};")
    for t in sec_tracks:
        parts.append(f" 0:3/r3:r0,{t}:r2[{stride}],{w},{sec}{mg_low};")

    return "".join(parts)


# --- StructureComments template ---
_COMMENTS_TEMPLATE: dict[str, str] = {
    "A": "SDR CTGOGD, {mos}-Diff, type a",
    "B": (
        "SDR CTGOGD,  {mos}-Diff,type b - Leakage in OGD between single diff "
        "length gate, and 2 diff length TCN/epi"
    ),
    "C": (
        "SDR CTGOGD,  {mos}-Diff,type c  - Leakage in OGD between double diff "
        "length gate, and single diff length TCN/epi"
    ),
    "D": (
        "SDR CTGOGD,  {mos}-Diff,type d - Leakage in OGD between single diff "
        "length gate, and single diff length TCN/epi"
    ),
}


def _generate_structure_comments(mos: str, dtype: str) -> str:
    tmpl = _COMMENTS_TEMPLATE.get(dtype, "")
    return tmpl.format(mos=mos) if tmpl else ""


# --- M0CutPatternUnit dynamic (CELLHEIGHT, TYPE)-driven ---
def _generate_m0_cut_pattern_unit(cellheight: int, dtype: str) -> str:
    if cellheight == 165:
        return "2:r2[5],2:4[1]/r4:r2[1]"
    if cellheight == 132 and dtype == "D":
        return "2:r2[5],1/r1"
    return "2:r2[5],1:3[1]/r3:r1[1]"


# --- DummyViaPattern dynamic (CELLHEIGHT)-driven ---
def _generate_dummy_via_pattern(cellheight: int) -> str:
    if cellheight >= 165:
        return "0:r0[3],1:5[3];  0:r0[3],r5:r1[3]; 2:r0[3],7:r6[23]; 2:r0[3],29:r6[23];"
    return "0:r0[3],0:4[3];  0:r0[3],r4:r0[3]; 2:r0[3],7:r5[19]; 2:r0[3],23:r5[19];"


# --- VcxPatternUnit dynamic (CELLHEIGHT, TYPE)-driven ---
def _generate_vcx_pattern_unit(cellheight: int, dtype: str) -> str:
    key = (cellheight, dtype)
    # (vg tracks, vt track set) per (CH, TYPE)
    if dtype == "A":
        vg = 6 if cellheight <= 132 else 7
        vt = 14 if cellheight <= 132 else 17
        return (
            f"vg,1:3/5:7/9:11/13:15/17:19/21:23,{vg}; "
            f"vt,0:r0[3],{vt}; vt,1:r0[3],{vt}; vt,2:r0[3],{vt};"
        )
    if dtype in {"B", "C"}:
        if cellheight <= 132:
            return (
                "vg,1:3/5:7/9:11/13:15/17:19/21:23,9/11; "
                "vt,0:r0[3],6/14; vt,1:r0[3],6/14; vt,2:r0[3],6/14"
            )
        return (
            "vg,1:3/5:7/9:11/13:15/17:19/21:23,11/13; "
            "vt,0:r0[3],7/17; vt,1:r0[3],7/17; vt,2:r0[3],7/17"
        )
    if dtype == "D":
        if cellheight <= 132:
            return (
                "vg,1:3/5:7/9:11/13:15/17:19/21:23,3/7-bottom/13/17-bottom; "
                "vt,0:r0[3],4/6/14/16; vt,1:r0[3],4/6/14/16; "
                "vt,2:r0[3],4/6/14/16"
            )
        return (
            "vg,1:3/5:7/9:11/13:15/17:19/21:23,8-bottom/10/14-bottom/16; "
            "vt,0:r0[3],7/11/13/17; vt,1:r0[3],7/11/13/17; "
            "vt,2:r0[3],7/11/13/17"
        )
    return ""


# --- LO1ActiveTracks & HI1ActiveTracks dynamic ---
# Sub-variations within TYPE A on ch165 depend on MOS (NP -> shifted).
_ACTIVE_TRACKS_A: dict[tuple[int, str], tuple[str, str]] = {
    # (CH, MOS) -> (LO1, HI1)
    (110, "N"): ("14", "6"), (110, "P"): ("14", "6"), (110, "NP"): ("14", "6"),
    (132, "N"): ("14", "6"), (132, "P"): ("14", "6"), (132, "NP"): ("14", "6"),
    (165, "N"): ("15", "6"), (165, "P"): ("15", "6"), (165, "NP"): ("17", "7"),
}


def _generate_lo1_active_tracks(cellheight: int, mos: str, dtype: str) -> str:
    if dtype == "A":
        return _ACTIVE_TRACKS_A.get((cellheight, mos), ("14", "6"))[0]
    if dtype in {"B", "C"}:
        return "6;14" if cellheight <= 132 else "7;17"
    if dtype == "D":
        return "4;6;14;16" if cellheight <= 132 else "7;11;13;17"
    return ""


def _generate_hi1_active_tracks(cellheight: int, mos: str, dtype: str) -> str:
    if dtype == "A":
        return _ACTIVE_TRACKS_A.get((cellheight, mos), ("14", "6"))[1]
    if dtype in {"B", "C"}:
        return "9;11" if cellheight <= 132 else "11;13"
    if dtype == "D":
        return "3;7;13;17" if cellheight <= 132 else "8;10;14;16"
    return ""


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _format_width(devwidth: int) -> str:
    """Format DEVWIDTH nm -> um string like '0.027' (drops fluff trailing zero
    only when integer scale). Kept in nm-to-um with 3 decimals to match source."""
    if devwidth <= 0:
        return ""
    s = f"{devwidth / 1000:.3f}"
    return s


def as_int(v: str, default: int = 0) -> int:
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return default


def norm(v: str) -> str:
    return str(v).strip().upper()


def _lookup_ci(row: dict[str, str], column: str) -> str:
    """Return ``row[column]`` with a case-insensitive header fallback.

    Preserves the fast path (exact match) for the common case and only
    falls back to a scan of the row's keys when the exact name is absent.
    Used for optional decoder columns whose header spelling has varied
    across authored decoder CSVs (e.g. ``ResizeShift`` vs ``ReSizeShift``).
    """
    v = row.get(column)
    if v is not None:
        return v
    target = column.lower()
    for k, val in row.items():
        if k and k.lower() == target:
            return val or ""
    return ""


def read_csv_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        rows = [{k: (v or "") for k, v in row.items()} for row in reader]
    return fields, rows


def write_csv_output(
    path: Path, fieldnames: list[str], rows: list[dict[str, str]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

@dataclass
class ISOValidationResult:
    input_errors: list = field(default_factory=list)
    range_errors: list = field(default_factory=list)
    consistency_errors: list = field(default_factory=list)

    @property
    def has_blocking_errors(self) -> bool:
        return bool(self.input_errors or self.range_errors)

    @property
    def is_clean(self) -> bool:
        return not self.has_blocking_errors and not self.consistency_errors


def validate_decoder_row(row: dict[str, str]) -> ISOValidationResult:
    r = ISOValidationResult()
    mos = norm(row.get("MOS", ""))
    mg = norm(row.get("MG", ""))
    ch_raw = row.get("CELLHEIGHT", "").strip()

    if not mos:
        r.input_errors.append("MOS is required")
    elif mos not in VALID_MOS:
        r.input_errors.append(f"MOS '{mos}' not in {VALID_MOS}")

    if not mg:
        r.input_errors.append("MG is required")
    elif mg not in VALID_MG:
        r.input_errors.append(f"MG '{mg}' not in {VALID_MG}")

    if ch_raw:
        ch = as_int(ch_raw)
        if ch not in VALID_CELLHEIGHT:
            r.range_errors.append(f"CELLHEIGHT {ch} not in {VALID_CELLHEIGHT}")

    return r


# ---------------------------------------------------------------------------
# Constant-map & training-index auto-discovery
# ---------------------------------------------------------------------------

# Buildsheet columns whose training values are exposed to the row generator
# via ``(MOS, MG, CELLHEIGHT, TYPE)``-keyed indexes. The formula helpers
# above are consulted as a fallback when a key is not in the index.
INDEXED_COLS: tuple[str, ...] = (
    "DiffPatternUnit",
    "DiffPatternGlobal",
    "M0CutPatternUnit",
    "DummyViaPattern",
    "VcxPatternUnit",
    "LO1ActiveTracks",
    "HI1ActiveTracks",
)
# NOTE: VcrPatternUnit / VcrnubPatternUnit are intentionally NOT indexed.
# These represent opt-in per-row features (populated in only a few training
# rows); we do not want a blank decoder cell to silently inherit a trained
# Vcr value from another row that happens to share (MOS,MG,CH,TYPE). Blank
# decoder cell -> blank buildsheet cell. Users must supply the DSL
# explicitly to produce a value.

# Columns whose stored training value carries a DEVWIDTH literal (formatted as
# a floating-point um value like ``0.027``). For these columns the index
# stores a *width-templated* string with the trained width substituted by
# ``{W}``. At row-generation time :func:`_from_index_or` substitutes the
# CURRENT decoder DEVWIDTH into the placeholder — so editing DEVWIDTH in the
# decoder produces a dynamically-rewidened pattern instead of the stale
# trained value. This preserves the trained *shape* (byte-identical when
# DEVWIDTH is unchanged) while keeping the width mapping dynamic.
WIDTH_TEMPLATE_COLS: frozenset[str] = frozenset({
    "DiffPatternUnit",
    "DiffPatternGlobal",
})

_WIDTH_TEMPLATE_TOKEN = "{W}"


def _templatize_width(value: str, devwidth: int) -> str:
    """Return ``value`` with the trained DEVWIDTH literal replaced by ``{W}``.

    Falls back to a regex substitution if the exact literal is not present
    (defensive — training values observed so far always carry the formatted
    width verbatim between commas).
    """
    if not value or devwidth <= 0:
        return value
    w = _format_width(devwidth)
    if w and w in value:
        return value.replace(w, _WIDTH_TEMPLATE_TOKEN)
    # Fallback: any ",<float>," segment (widths are always comma-delimited).
    return re.sub(r",(\d+\.\d+),", f",{_WIDTH_TEMPLATE_TOKEN},", value)


def _apply_width_template(template: str, devwidth: int) -> str:
    """Substitute the current DEVWIDTH into a ``{W}``-templated value."""
    if _WIDTH_TEMPLATE_TOKEN not in template:
        return template
    return template.replace(_WIDTH_TEMPLATE_TOKEN, _format_width(devwidth))


def _build_indexes(
    train_dec: list[dict[str, str]],
    train_iso: list[dict[str, str]],
) -> dict[str, dict[tuple[str, str, int, str], str]]:
    """Build per-column indexes keyed on ``(MOS, MG, CELLHEIGHT, TYPE)``.

    Only buildsheet columns listed in :data:`INDEXED_COLS` are indexed.
    For columns in :data:`WIDTH_TEMPLATE_COLS` the stored value is
    width-templated using the training decoder row's DEVWIDTH so that the
    DEVWIDTH dimension becomes dynamic at row-generation time.
    """
    indexes: dict[str, dict[tuple[str, str, int, str], str]] = {
        c: {} for c in INDEXED_COLS
    }
    iso_by_name = {r.get("StructureName", ""): r for r in train_iso}
    for d in train_dec:
        name = d.get("StructureName", "")
        iso = iso_by_name.get(name)
        if not iso:
            continue
        key = (
            norm(d.get("MOS", "")),
            norm(d.get("MG", "")),
            as_int(d.get("CELLHEIGHT", "0")),
            _extract_type(name),
        )
        train_devwidth = as_int(d.get("DEVWIDTH", "0"))
        for col in INDEXED_COLS:
            v = iso.get(col, "")
            if not v or key in indexes[col]:
                continue
            if col in WIDTH_TEMPLATE_COLS:
                v = _templatize_width(v, train_devwidth)
            indexes[col][key] = v
    return indexes


def _build_constant_map(
    train_iso: list[dict[str, str]], iso_fields: list[str]
) -> dict[str, str]:
    """Find columns that are constant across all training rows."""
    constants: dict[str, str] = {}
    for col in iso_fields:
        vals = set(row.get(col, "") for row in train_iso)
        if len(vals) == 1:
            constants[col] = vals.pop()
    return constants


# ---------------------------------------------------------------------------
# Tuned engine — row generator
# ---------------------------------------------------------------------------

def _generate_iso_row_tuned(
    input_row: dict[str, str],
    constants: dict[str, str],
    iso_fields: list[str],
    indexes: dict[str, dict[tuple[str, str, int, str], str]] | None = None,
) -> dict[str, str]:
    """Build a single ISO buildsheet row from decoder input."""
    mos = norm(input_row.get("MOS", "N"))
    mg = norm(input_row.get("MG", "LVT"))
    cellheight = as_int(input_row.get("CELLHEIGHT", "132"))
    devwidth = as_int(input_row.get("DEVWIDTH", "46"))
    unit_ogd = as_int(input_row.get("UnitCellSizeOgd", "24"))
    unit_pgd = as_int(input_row.get("UnitCellSizePgd", "4"))
    array_ogd = as_int(input_row.get("ArraySizeOgd", "5"))
    array_pgd = as_int(input_row.get("ArraySizePgd", "10"))

    structure_name = input_row.get("StructureName", "")
    dtype = _extract_type(structure_name)
    idx_key = (mos, mg, cellheight, dtype)
    indexes = indexes or {}

    def _from_index_or(col: str, formula_value: str) -> str:
        """Prefer training-index exact match; else return formula value.

        For :data:`WIDTH_TEMPLATE_COLS` the stored value carries a ``{W}``
        placeholder — substitute the current row's DEVWIDTH so DEVWIDTH
        edits in the decoder propagate dynamically to the buildsheet.
        """
        col_index = indexes.get(col, {})
        if idx_key in col_index:
            v = col_index[idx_key]
            if col in WIDTH_TEMPLATE_COLS:
                v = _apply_width_template(v, devwidth)
            return v
        return formula_value

    # Start with auto-discovered constants
    output: dict[str, str] = {col: constants.get(col, "") for col in iso_fields}

    # Identity
    output["StructureName"] = structure_name
    output["StructureComments"] = _generate_structure_comments(mos, dtype)

    # Cell / array sizes
    output["CellHeight"] = f"ch{cellheight}"
    output["PolyPitch"] = "pp44"
    output["DesignContext"] = "digital"
    output["UnitCellSizeOgd"] = str(unit_ogd)
    output["UnitCellSizePgd"] = str(unit_pgd)
    output["ArraySizeOgd"] = str(array_ogd)
    output["ArraySizePgd"] = str(array_pgd)
    output["BufferSizeL"] = "4"
    output["BufferSizeR"] = "4"
    output["BufferSizeB"] = "1"
    output["BufferSizeT"] = "1"

    # Diff patterns (training-index preferred, formula fallback)
    output["DiffPatternUnit"] = _from_index_or(
        "DiffPatternUnit",
        _generate_diff_pattern_unit(mos, mg, devwidth, dtype),
    )
    output["DiffPatternGlobal"] = _from_index_or(
        "DiffPatternGlobal",
        _generate_diff_pattern_global(mos, mg, devwidth, dtype),
    )

    # ------------------------------------------------------------------
    # DSL-expanded pattern pairs (FTIPattern, PolyPlugs, TCNPlugs,
    # VcxPattern, ...). This block scales linearly with PATTERN_COL_PAIRS.
    #
    # Precedence (per pair):
    #   1. If the decoder cell is NON-EMPTY -> DSL expansion is authoritative.
    #      This lets a user override any training row by simply editing the
    #      decoder DSL (e.g. changing ``VcxPattern`` or ``UnitCellSizeOgd``
    #      produces a fresh expansion, not a cached training lookup).
    #   2. If the decoder cell is empty -> fall back to the training index
    #      keyed on (MOS, MG, CH, TYPE) so training-authored rows without
    #      DSL still round-trip byte-identically (preserves quirks like the
    #      TYPE-A trailing ``;`` in ``VcxPatternUnit``).
    # ------------------------------------------------------------------
    def _resolve_pair(dec_col: str, iso_col: str) -> str:
        raw = input_row.get(dec_col, "")
        if raw and raw.strip():
            return _expand_pattern(raw, unit_ogd, unit_pgd, iso_col)
        # Empty decoder cell -> defer to training index (if any).
        return indexes.get(iso_col, {}).get(idx_key, "")

    vcx_from_decoder = bool(input_row.get("VcxPattern", "").strip())
    for dec_col, iso_col in PATTERN_COL_PAIRS:
        output[iso_col] = _resolve_pair(dec_col, iso_col)

    # ------------------------------------------------------------------
    # ResizeShift — decoder-authored 4-tuple physical shifts appended to
    # matching segments in VcxPatternUnit / VcrPatternUnit / VcrnubPatternUnit.
    # Parsed once per row; applied to every target column (unmatched columns
    # pass through untouched). When the decoder cell is empty, any shift
    # markers already baked into a training-index fallback value are left
    # in place. When the decoder cell is set, its shifts REPLACE existing
    # ones on matching segments.
    #
    # Header spelling is intentionally tolerant: ``ResizeShift`` /
    # ``ReSizeShift`` / ``RESIZESHIFT`` etc. all resolve to the same cell
    # so a casing typo in the decoder header does not silently drop shifts.
    # ------------------------------------------------------------------
    shifts = _parse_resize_shift(_lookup_ci(input_row, "ResizeShift"))
    if shifts:
        for iso_col in {_RESIZE_SHIFT_TARGETS[l] for l in shifts}:
            if iso_col in output:
                output[iso_col] = _apply_resize_shift(
                    output[iso_col], iso_col, shifts
                )

    # Fixed global (constant across all training rows)
    output["FTITracksGlobal"] = "0/2/r2/r0,0:r0;"

    # Other varying columns (training-index preferred, formula fallback).
    # NOTE: M0CutPatternUnit is now decoder-driven via M0CutPattern DSL —
    # it is set by the PATTERN_COL_PAIRS loop above, not here.
    output["DummyViaPattern"] = _from_index_or(
        "DummyViaPattern", _generate_dummy_via_pattern(cellheight)
    )

    # LO1 / HI1 active tracks precedence:
    #   1. Explicit decoder column (user-authored override, highest priority).
    #      Necessary for structures whose VcxPattern has only ``vg`` or only
    #      ``vt`` segments (e.g. GATEETE / TCNETE rows), or when the author
    #      wants a specific ordering that differs from the DSL expansion.
    #   2. Derivation from VcxPatternUnit (when decoder supplied VcxPattern).
    #   3. Training index (MOS, MG, CH, TYPE) exact match.
    #   4. Formula fallback.
    lo1_from_decoder = input_row.get("LO1ActiveTracks", "").strip()
    hi1_from_decoder = input_row.get("HI1ActiveTracks", "").strip()
    hi1_from_vcx, lo1_from_vcx = _derive_active_tracks_from_vcx(
        output["VcxPatternUnit"]
    )

    if lo1_from_decoder:
        output["LO1ActiveTracks"] = lo1_from_decoder
    elif vcx_from_decoder:
        # DSL-driven derivation is authoritative for user-supplied rows.
        output["LO1ActiveTracks"] = lo1_from_vcx or _generate_lo1_active_tracks(
            cellheight, mos, dtype
        )
    else:
        # Decoder was blank -> prefer training index, then derivation, then formula.
        output["LO1ActiveTracks"] = _from_index_or(
            "LO1ActiveTracks",
            lo1_from_vcx
            or _generate_lo1_active_tracks(cellheight, mos, dtype),
        )

    if hi1_from_decoder:
        output["HI1ActiveTracks"] = hi1_from_decoder
    elif vcx_from_decoder:
        output["HI1ActiveTracks"] = hi1_from_vcx or _generate_hi1_active_tracks(
            cellheight, mos, dtype
        )
    else:
        output["HI1ActiveTracks"] = _from_index_or(
            "HI1ActiveTracks",
            hi1_from_vcx
            or _generate_hi1_active_tracks(cellheight, mos, dtype),
        )

    # Constants
    output["LO1Data"] = "m0,m1,L,B,10,2/0,0;1,1"
    output["HI1Data"] = "m0,m1,R,B,10,2/0,0;1,1"
    output["UseFUT2ID"] = "N"
    output["kernelOffsetX"] = "0"
    output["kernelOffsetY"] = "0"
    output["flattenHier"] = "N"

    return output


def engine_tuned(
    input_rows: list[dict[str, str]],
    iso_fields: list[str],
    train_dec: list[dict[str, str]],
    train_iso: list[dict[str, str]],
) -> list[dict[str, str]]:
    constants = _build_constant_map(train_iso, iso_fields)
    print(f"  Auto-discovered {len(constants)} constant columns from training data")

    indexes = _build_indexes(train_dec, train_iso)
    idx_sizes = ", ".join(f"{c}:{len(indexes[c])}" for c in INDEXED_COLS)
    print(f"  Built training indexes ({idx_sizes})")

    results: list[dict[str, str]] = []
    for row in input_rows:
        gen = _generate_iso_row_tuned(row, constants, iso_fields, indexes)
        results.append({col: gen.get(col, "") for col in iso_fields})
    return results


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------

def load_training(
    decoder_path: Path, iso_path: Path
) -> tuple[list[str], list[dict[str, str]], list[dict[str, str]]]:
    _, decoder_rows = read_csv_rows(decoder_path)
    iso_fields, iso_rows = read_csv_rows(iso_path)
    iso_fields = [c for c in iso_fields if c.strip()]  # drop trailing empty header cells

    iso_index = {r.get("StructureName", ""): r for r in iso_rows}
    matched_dec: list[dict[str, str]] = []
    matched_iso: list[dict[str, str]] = []
    for d in decoder_rows:
        name = d.get("StructureName", "")
        if name in iso_index:
            matched_dec.append(d)
            matched_iso.append(iso_index[name])
    return iso_fields, matched_dec, matched_iso


def append_padconfig(
    iso_rows: list[dict[str, str]], padconfig_path: Path
) -> tuple[list[str], list[dict[str, str]]]:
    pc_fields, pc_rows = read_csv_rows(padconfig_path)
    if not pc_rows:
        return [], iso_rows
    combined: list[dict[str, str]] = []
    for i, r in enumerate(iso_rows):
        pc = pc_rows[i % len(pc_rows)]
        merged = dict(r)
        for c in pc_fields:
            merged[c] = pc.get(c, "")
        combined.append(merged)
    return pc_fields, combined


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generate ISO buildsheet from decoder input (1281 node)."
    )
    p.add_argument("--engine", choices=ENGINES, default="tuned")
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--training-decoder", type=Path, default=DEFAULT_TRAINING_DECODER)
    p.add_argument("--training-iso", type=Path, default=DEFAULT_TRAINING_ISO)
    p.add_argument("--padconfig", type=Path, default=DEFAULT_PADCONFIG)
    p.add_argument("--no-padconfig", action="store_true")
    p.add_argument("--no-validate", action="store_true")
    return p


def main() -> None:
    args = build_parser().parse_args()

    if not args.input.exists():
        print(f"ERROR: input not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    _, input_rows = read_csv_rows(args.input)
    print(f"Loaded {len(input_rows)} input rows from {args.input}")

    if not args.no_validate:
        blocked = 0
        for i, r in enumerate(input_rows, 1):
            v = validate_decoder_row(r)
            if v.has_blocking_errors:
                blocked += 1
                print(f"  row {i}: {v.input_errors + v.range_errors}", file=sys.stderr)
        if blocked == 0:
            print(f"  All {len(input_rows)} input rows passed validation")

    iso_fields, train_dec, train_iso = load_training(
        args.training_decoder, args.training_iso
    )
    print(f"Training: {len(train_iso)} rows, {len(iso_fields)} ISO columns")

    print(f"Engine: {args.engine}")
    results = engine_tuned(input_rows, iso_fields, train_dec, train_iso)
    print(f"Generated {len(results)} ISO rows")

    out_fields = list(iso_fields)
    out_rows = results
    if not args.no_padconfig and args.padconfig.exists():
        pc_fields, out_rows = append_padconfig(results, args.padconfig)
        out_fields.extend(pc_fields)
        print(f"Appended {len(pc_fields)} PadConfig columns from {args.padconfig.name}")

    write_csv_output(args.output, out_fields, out_rows)
    print(f"Wrote {len(out_rows)} rows x {len(out_fields)} columns to {args.output}")


if __name__ == "__main__":
    main()
