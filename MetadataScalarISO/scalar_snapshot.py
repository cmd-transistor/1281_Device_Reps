"""Annotated unit-cell snapshots for ISO scalar derivations.

Draws ONE Ogd x Pgd unit cell of a structure straight from its buildsheet row
(FTI / diffusion / poly gates / TCN / VG / VT) and overlays the leakage paths
that :mod:`ScalarCalculation` counts, so the ``ScalarFormula`` text can be
checked against a picture.

Conventions (all from the buildsheet DSL, see ScalarCalculation.py):
* X: poly column ``c`` at x = c; TCN slot ``s`` (between poly s and s+1) at
  x = s + 0.5. FTI columns are poly columns.
* Y: diffusion tracks ("cells") 0 .. 2*UnitCellSizePgd-1 (same frame as the
  FTITracksUnit / DiffPatternUnit Y-axis). Via Y values are routing tracks
  (24 nm pitch for 2TRK, ~26.4 nm for 1TRK designs) and are mapped onto cells
  with :class:`YMap`; calibrated on the bundled CTG132 chassis GDS
  (VG t=6 -> cell 2, VT t=14 -> cell 5, ...). Schematic, not GDS-accurate.
* Gate / TCN vertical extent comes from the PolyPlugsUnit / TcnPlugsUnit plug
  model: one segment per cell, plug ``V`` closes the gap between cells V-1
  and V (verified: PolyPlugs 3/4 -> cells 2..4, TCNPlugs 4/5 -> cells 3..5,
  PolyPlugs 3/6 -> cells 2..3 + 5..6).

Used through ``ScalarCalculation.py --snapshots DIR``; needs matplotlib.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch, Rectangle  # noqa: E402

from ScalarCalculation import (  # noqa: E402
    Geometry, Result, _ete_pairs, _stacked_runs, ctg_sites, expand_axis,
    geometry_from_row, segments, span_containing, strip_shift,
)

C_NDIFF, C_PDIFF = "#b5dcb0", "#f6c9a0"
C_FTI, C_POLY, C_TCN = "#3c3c3c", "#d62728", "#1f77b4"
C_LO, C_HI, C_OTHER, C_PATH = "#1f5fbf", "#c0392b", "#8c8c8c", "#b000b0"
NET_COLOR = {"LO": C_LO, "HI": C_HI, "": C_OTHER}


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


# ---------------------------------------------------------------------------
# Base layout
# ---------------------------------------------------------------------------
def draw_base(ax, row: dict[str, str], g: Geometry) -> None:
    ogd, ncell = g.ogd, g.n_cells
    ym = g.ymap
    assert ym is not None

    # diffusion tracks
    for seg in segments(row.get("DiffPatternUnit", "")):
        parts = [p.strip() for p in strip_shift(seg).split(",")]
        if len(parts) < 2:
            continue
        xs = [x for x in expand_axis(parts[0], ogd) if 0 <= x <= ogd]
        cells = [c for c in expand_axis(parts[1], ncell) if 0 <= c < ncell]
        color = C_PDIFF if (len(parts) > 3 and parts[3].lower().startswith("p")) else C_NDIFF
        if not xs:
            xs = list(range(ogd + 1))
        x0, x1 = max(-0.5, min(xs) - 0.5), min(ogd + 0.5, max(xs) + 0.5)
        for c in cells:
            ax.add_patch(Rectangle((x0, c + 0.2), x1 - x0, 0.6, facecolor=color, edgecolor="none", zorder=1))

    # FTI
    for c, cells in g.fti_cells.items():
        for start, end in _contiguous_runs(cells):
            ax.add_patch(Rectangle((c - 0.14, start), 0.28, end - start + 1,
                                   facecolor=C_FTI, edgecolor="none", zorder=3))
    if 0 in g.fti_cells and g.fti_cells[0]:            # right edge = next unit's column 0
        for start, end in _contiguous_runs(g.fti_cells[0]):
            ax.add_patch(Rectangle((ogd - 0.14, start), 0.28, end - start + 1,
                                   facecolor=C_FTI, edgecolor="none", zorder=3))

    # poly gates (light) at every column, TCN (light) at every slot
    for c in range(ogd):
        for a, b in g.gate_spans(c):
            ax.add_patch(Rectangle((c - 0.09, a + 0.05), 0.18, b - a + 0.9,
                                   facecolor=C_POLY, alpha=0.28, edgecolor="none", zorder=2))
    for s in range(ogd):
        for a, b in g.tcn_spans(s):
            ax.add_patch(Rectangle((s + 0.5 - 0.09, a + 0.1), 0.18, b - a + 0.8,
                                   facecolor=C_TCN, alpha=0.28, edgecolor="none", zorder=2))

    # active gates (VG) and TCN (VT): bold segment + via marker
    for c, tracks in g.vg_cols.items():
        for t in tracks:
            cell = ym.cell(t)
            a, b = span_containing(g.gate_spans(c), cell)
            ax.add_patch(Rectangle((c - 0.11, a + 0.05), 0.22, b - a + 0.9,
                                   facecolor=C_POLY, alpha=0.95, edgecolor="none", zorder=4))
            ax.scatter([c], [cell + 0.5], marker="s", s=70, color=net_color({t}, g),
                       edgecolor="k", linewidth=0.6, zorder=6)
    for s, tracks in g.vt_cols.items():
        for t in tracks:
            cell = ym.cell(t, "vt")
            a, b = span_containing(g.tcn_spans(s), cell)
            ax.add_patch(Rectangle((s + 0.5 - 0.11, a + 0.1), 0.22, b - a + 0.8,
                                   facecolor=C_TCN, alpha=0.95, edgecolor="none", zorder=4))
            ax.scatter([s + 0.5], [cell + 0.5], marker="D", s=60, color=net_color({t}, g),
                       edgecolor="k", linewidth=0.6, zorder=6)


# ---------------------------------------------------------------------------
# Family-specific path overlays
# ---------------------------------------------------------------------------
def _harrow(ax, x0: float, x1: float, y: float) -> None:
    ax.annotate("", (x1, y), (x0, y), zorder=7,
                arrowprops=dict(arrowstyle="<->", color=C_PATH, lw=1.6, shrinkA=0, shrinkB=0))


def _varrow(ax, x: float, y0: float, y1: float) -> None:
    ax.annotate("", (x, y1), (x, y0), zorder=7,
                arrowprops=dict(arrowstyle="<->", color=C_PATH, lw=1.6, shrinkA=0, shrinkB=0))


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
    ncell = g.n_cells
    ym = g.ymap
    assert ym is not None
    runs = _stacked_runs(g)
    idx = 0
    for run in runs:
        for c in run:
            idx += 1
            ax.text(c, ncell + 0.25, str(idx), ha="center", va="bottom", fontsize=7, color=C_PATH, zorder=8)
        if len(run) > 1:
            ax.plot([run[0] - 0.3, run[-1] + 0.3], [ncell + 0.9, ncell + 0.9], color=C_PATH, lw=1.2)
            ax.text((run[0] + run[-1]) / 2, ncell + 0.95, "stacked", ha="center", va="bottom",
                    fontsize=6, color=C_PATH)
    for s, tracks in g.vt_cols.items():
        col = net_color(tracks, g)
        if col != C_OTHER:
            ax.text(s + 0.5, ym.cell(min(tracks), "vt") + 1.05, "LO" if col == C_LO else "HI",
                    ha="center", va="bottom", fontsize=6, color=col, zorder=8)
    return (f"numbers = active VG gates (transistors in series) counted in the chain: {idx}; "
            f"grey diamonds = VT on M0 jumper tracks (not LO1/HI1)")


OVERLAYS = {"CTG": overlay_ctg, "GATEGATE": overlay_ete, "EPIEPI": overlay_ete,
            "TCNTCN": overlay_ete, "DIFFOGD": overlay_diffogd, "DIFFCHN": overlay_diffchn}


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def render_snapshot(row: dict[str, str], res: Result, out_path: Path, subtitle: str = "") -> None:
    g = geometry_from_row(row)
    ogd, ncell = g.ogd, g.n_cells
    fig, ax = plt.subplots(figsize=(max(9.0, 0.42 * ogd + 3.5), 7.2))
    draw_base(ax, row, g)
    caption = OVERLAYS[res.family](ax, g, res)

    ax.set_xlim(-0.8, ogd + 0.8)
    ax.set_ylim(-0.6, ncell + 1.9)
    ax.set_xticks(range(0, ogd + 1, 1 if ogd <= 30 else 2))
    ax.set_xlabel("poly column within Ogd unit (TCN slots at half positions; FTI = dark bars)")
    ax.set_yticks([k + 0.5 for k in range(ncell)])
    ax.set_yticklabels([f"diff trk {k}" for k in range(ncell)], fontsize=8)
    ax.tick_params(axis="x", labelsize=7)
    ax.grid(True, axis="x", alpha=0.12)
    ax.set_aspect("equal", adjustable="box")

    legend = [
        Patch(facecolor=C_NDIFF, label="N diff track"), Patch(facecolor=C_PDIFF, label="P diff track"),
        Patch(facecolor=C_FTI, label="FTI"), Patch(facecolor=C_POLY, alpha=0.9, label="poly gate (bold = active VG)"),
        Patch(facecolor=C_TCN, alpha=0.9, label="TCN (bold = has VT)"),
        Line2D([], [], marker="s", ls="", color=C_LO, markeredgecolor="k", label="VG on LO1 track"),
        Line2D([], [], marker="s", ls="", color=C_HI, markeredgecolor="k", label="VG on HI1 track"),
        Line2D([], [], marker="D", ls="", color=C_LO, markeredgecolor="k", label="VT on LO1 track"),
        Line2D([], [], marker="D", ls="", color=C_HI, markeredgecolor="k", label="VT on HI1 track"),
        Line2D([], [], marker="D", ls="", color=C_OTHER, markeredgecolor="k", label="via on other track"),
        Line2D([], [], color=C_PATH, lw=1.6, label="counted LKG path"),
    ]
    ax.legend(handles=legend, loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=6, fontsize=7, frameon=False)

    title = f"{row.get('StructureName', '')}   [{row.get('TestRow', '')}]"
    if subtitle:
        title += f"\n{subtitle}"
    sub = "\n".join(textwrap.wrap(res.formula, 120))
    ax.set_title(f"{title}\n{sub}\n{caption}", fontsize=8.5, loc="left")
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
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
