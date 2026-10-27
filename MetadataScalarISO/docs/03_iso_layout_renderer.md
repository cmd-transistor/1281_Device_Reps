# 3. ISO layout renderer (`scalar_snapshot.py`)

[scalar_snapshot.py](../scalar_snapshot.py) draws ISO (and Miller/GNAC/FE_ECD-mapped) structures **directly
from the buildsheet** with matplotlib. No layer except PXL/TRM comes from GDS; the drawing is a calibrated
preview, not signoff geometry. All coordinates are in preview units (x = poly track, y = diffusion track) and
each PNG gets a `.json` calibration sidecar so the GUI can measure in nm.

## 3.1 Entry points

| Function | Used for |
|---|---|
| `render_snapshot(row, result, out, …, preview=True, visible_layers, coords, scale, panel)` | Unit view (one unit cell) |
| `render_global_snapshot(row, out, units_ogd, units_pgd, coords, routing, buffer, scale, panel)` | Global view, tiled units, Full structure |
| `draw_base(ax, row, g, visible)` | Shared unit-cell drawing used by both, DR geometry extraction and raster tiling |
| `trace_nets(row, g)` | Current-flow trace (LO / HI / bridge / short nets) |
| `layout_layers(row)` / `tsdr_draw_layers(row)` | Layer toggles available for a row / TSDR IDs |
| `elements_at(row, x, y, visible)` | Right-click “Remove …” edits |
| `buildsheet_syntax_issues`, `buildsheet_review_notes`, `design_rule_issues`, `vcrnub_rule_issues`, `flavor_alignment` | Editor validation |
| `write_gds(ax, path, row, cell)` | KLayout export of exactly what is drawn |
| `render_snapshots(...)` | Batch snapshots for the PowerPoint catalog |

[iso_structure_ai.py](../iso_structure_ai.py) `StructureDesigner.layout_png` chooses the renderer
([dispatch](05_template_builders.md#dispatch)) and caches PNGs in `ai_layouts/` under a key containing the
design id, version, dpi, a render-style token (`_r7_` for ISO, `_w4_` for PFT), the visible-layer bit mask,
view/units/coords, scale and panel flag. **Bump the token whenever drawing changes**, otherwise stale PNGs are
reused.

## 3.2 Layers

`NDIFF, PDIFF, VT flavor, FTI, PLY, TCN, TCP, VG, VT, VCR, PXL / TRM, M0 lines, M0 LO1, M0 HI1, M0 cuts,
V0 landings, V0 dummy, Current flow, Fail paths, Grid, M0 Y grid, Guide tracks` plus one toggle per
`DrawMiscIDs` / TSDR ID present in the row.

Conventions that were each the result of a user-reported issue:

* PLY is drawn under FTI; FTI cuts only the active gate segment. A gate cell is cut only when the (skewed)
  FTI covers most of it, and the active gate fill is clipped at the FTI edge.
* Active PLY/TCN fills follow the traced nets and stay highlighted (darker) even if the plain layer is off.
* M0 is drawn as data-unit rectangles (`_m0_height`) so it scales with zoom; colours follow Cadence:
  CH110 single red; CH132/165 odd tracks CLR1 red (GDS 120/0), even CLR2 green (241/0); rails (CRL) brown,
  27 nm wide on CH132.
* VCR rails sit on NP boundaries (CRL), numbered on every boundary 0 … UnitCellSizePgd.
* VCRNUB is 1 poly pitch × 13.4 nm from the VCR centre (measured on the PFT generator GDS).
* TCP is drawn between TCNs, parallel to poly.
* `PXLSkew` CD is the absolute bias (NOM 0.014), so N5/P5 render 9/19 nm; PXL/TRM turns on for PXL-skew rows.
* VT flavour IDs align with diffusion device-flavour IDs (DevFlav).
* M0 lines are off by default; LO/HI shown by default.

## 3.3 Current-flow trace

`trace_nets` follows TCN → VT → M0 → V0, PLY → VG → M0 → V0 and TCN → VCRNUB → VCR → M0 rail.
M0 tracks are split by `M0CutPatternUnit`. Nets reaching the LO1 V0 are blue, HI1 magenta, floating M0 jumper
nets that carry current between LO and HI through gates driven by LO/HI (DIFFCHN serpentines) purple with the
conducting channels marked, and LO+HI together are a SHORT. Rows whose LO1/HI1 tracks carry no via show a red
buildsheet check. `LO1Data`/`HI1Data` decode the castle (M1 jumpers, V0 every Nth jumper, alternating row
offset — checked on the CTG132 chassis: 5 V0 per track, alternate rows shifted by one jumper).

## 3.4 Views

| View | What is drawn |
|---|---|
| **Unit** | One unit cell from the `*Unit` fields |
| **Global** | Buffer ring (BufferSizeL/R/B/T) around N×M unit cells; `*Global` fields outside the units; default buffer = per-cell PLY and TCN in every slot unless global plugs/depop say otherwise |
| **Global + routing** | Adds the LO/HI castle: M1 jumpers, V0 landings, pins |
| **Full structure** | Global view with `ArraySizeOgd × ArraySizePgd` units (e.g. 21×250). Turns Grid / M0 Y grid / Guide tracks off and restores them on **Unit cell** |

**Coords** switches axis labels between unit, global (`g` prefix) and both.

### Performance (Full structure)

The first implementation drew every unit cell as matplotlib patches: a 21×250 PLY row took **281 s**.

1. `_replicate_tile` copies the unit cell’s artists as `PolyCollection`s (gid `TILES`) → still slow.
2. `_RectBatch` monkey-patches `ax.add_patch` during buffer drawing and flushes rectangles into collections
   → **40.7 s**.
3. For ≥ 64 tiles and PNG output, `_unit_tile_raster` renders one unit cell to an RGBA image and
   `np.tile` + `imshow` places the array; per-unit outlines are replaced by one array outline → **8.9 s**.
   GDS export keeps the vector path.

`_fit_image` caps the displayed image (≤ 8192 px, ≤ 16 MP) and sharp re-renders use `wanted = 1` above
16 units.

## 3.5 Skews and TSDR IDs

* FTI / TCN / PLY / GLK / PXL skews from §1.5 are applied to the drawn shapes; the nominal shapes are drawn
  as dashed outlines and the legend lists the applied skews (including “poly pitch X nm” and ID skews such as
  `poly:TSDRn12A`).
* `PolyCdSkew = PP;CD;TCN` changes the x-pitch (`x_pitch_nm`) used by the ruler, measurement and DR geometry,
  and poly/TCN widths (`poly_skew_geometry`, `tcn_skew_geometry`).
* TSDR dropdown: implied IDs (e.g. `poly:TSDRActiveCDID` from a poly skew, `TCP_mask:TSDRActiveCDID` from a TCN
  skew) and explicit `DrawMiscIDs*` IDs; global IDs are listed when the layer/purpose contains `TSDR`.
  “TSDR region” shades the ID’s area. `misc_id_rects` handles repetition and edge moves; X edges sit on
  poly/FTI centres (fixed after “TSDR tap ID not aligned on PLY/FTI”).

## 3.6 Measurement calibration

Each PNG writes `<png>.json`: origin, pixels per track, nm per unit (`x_pitch_nm`, CH/2), axes box and every
drawn edge/line/centre for **Snap**. Sharp zoom re-renders up to 4× figure size (lines/text keep their point
size). Rulers (top: poly tracks, left: diffusion tracks, right: M0 tracks/CRL) are drawn outside the plot with
strides for large arrays.

## 3.7 KLayout / GDS export

`write_gds` writes `ai_layouts/*.gds` (database unit 0.1 nm) and a `.lyp` with the chassis layer numbers:
NDIFF 1/0, POLY 2/0, TCN 5/0, PDIFF 8/0, VT 31/0, VG 32/0, V0 56/0, M0 241/0 and 120/0, TRM 509/0. FTI, TCP,
VCR, VCRNUB, rails, cuts, nets, TSDR regions, IDs and nominal outlines go to 1000+ layers. **GDS source**
“Generator” uses the CTG132 chassis via `claude_iso_gds_generator.parse_chassis`; other cell heights fall back
to “Preview drawing”. The generator can show streaks because it is a chassis+delta model, not an
authoritative layout (see [ISO generator §4.3](04_iso_generator_and_training.md)).

## 3.8 DR overlay

With **DR highlights** on (Unit view), `_draw_dr_overlay` draws numbered boxes for
`dr_geometry.review_geometry` findings: red = outsheet rule, amber = geometry advisory; hovering shows the
plain-language finding. See [Design rules](07_design_rules.md).
