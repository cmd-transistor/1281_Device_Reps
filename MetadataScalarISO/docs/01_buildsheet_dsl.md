# 1. ISO buildsheet DSL

An ISO structure is fully described by one buildsheet row (Atlantis parameters or a row of
`ISO_Metadata_Input.csv`: 2,581 rows, 27 test rows, 92 columns — GATEGATE 654, CTG 461, VIA_CHAIN 401,
DIFFCHN 384, DIFFOGD 294, EPIEPI 249, TCNTCN 138). Every renderer, the scalar engine and the editor read the same row
through [ScalarCalculation.py](../ScalarCalculation.py) (`geometry_from_row`, `expand_axis`, `segments`,
`strip_shift`). This page is the reference for that grammar as it was reverse-engineered from the 2,581-row
library, the `claude_iso_generator.py` decoder DSL and the CTG132 chassis GDS.

## 1.1 Coordinate frames

| Axis | Unit | Index meaning |
|---|---|---|
| X (OGD, across poly) | poly pitch, 44 nm at `pp44` (skewable, see §1.7) | Poly column `c` at x = c; TCN slot `s` sits between poly `s` and `s+1` (x = s + 0.5); FTI columns are poly columns |
| Y (PGD, along poly) | diffusion track ("cell") = CellHeight / 2 nm (55 / 66 / 82.5 nm) | Cells `0 .. 2·UnitCellSizePgd − 1`; one cell height = 2 diffusion tracks |
| Y (M0) | M0 track | Equally spaced, track 0 on the unit boundary; 5 tracks per cell height for CH110/CH132 (22 / 26.4 nm pitch), 6 for CH165 (27.5 nm). Every multiple (0, 5, 10 … or 0, 6, 12 …) is a power rail (CRL) |
| Y (VCR) | NP boundary | VCR index k = boundary between cells 2k−1 and 2k (the CRL); numbered 0 … UnitCellSizePgd |

`UnitCellSizeOgd × UnitCellSizePgd` is the unit cell (poly tracks × cell heights); `ArraySizeOgd × ArraySizePgd`
is the number of unit cells; `BufferSizeL/R/B/T` is the buffer ring around the array (poly tracks left/right,
cell heights bottom/top).

VG/VT via tracks, V0 landings, `M0CutPatternUnit` Y and `LO1/HI1ActiveTracks` all use the **same M0 track
index**. This was confirmed on the CTG132 chassis GDS (V0 56/0, VG 32/0, M0 241/0 + 120/0, TCN 5/0) and is the
single most important convention: early versions used per-family Y scales and every misalignment bug
(“V0 not on the via track”, “LO/HI numbering does not match M0”) came from that.

## 1.2 Axis grammar (`expand_axis`)

| Form | Meaning | Example (extent 23) |
|---|---|---|
| `a` | single position | `5` → 5 |
| `a:b` | inclusive range | `2:5` → 2,3,4,5 |
| `a:b[N]` | range with step **N+1** | `0:23[3]` → 0,4,8,…,20 |
| `rK` | extent − K (counted from the end) | `r0` → 23, `r3:r0` → 20..23 |
| `x/y/z` | union of fragments | `1:3/5:7` → 1,2,3,5,6,7 |
| `seg1;seg2` | separate segments (each with its own X,Y,…) | — |

The extent is the axis length of the field’s frame: unit fields use `UnitCellSizeOgd` (X) and
`2·UnitCellSizePgd − 1` cells or the M0 track count (Y); `*Global` fields use the whole library frame
(array + buffers). On TCN-slot X axes (VT, `TcnPlugsUnit`, VCRNUB) `rK` counts from slot `ogd − 1`.

Segment suffixes:

* `|d1,d2,d3,d4` — ResizeShift / skew vector in µm: OGD CD, PGD CD, OGD REG, PGD REG (stripped by
  `strip_shift`; applied by the renderer).
* `?NET` — Miller terminal tag (`vg,1:3,3?GF`), see [Template builders — MLR](05_template_builders.md).
* `-bottom` / `-top` modifiers on via Y values (VG placement inside the M0 track pair).

## 1.3 Core unit-cell fields

| Field | Segment format | Notes |
|---|---|---|
| `DiffPatternUnit` | `X,Y,width_um,flavor` | e.g. `0:r0,2:5,0.046,nlvt`. Width sets diffusion band height; flavour gives N/P and VT (n/p + lvt/svt/hvt/ulvt/ulvtll/elvt) |
| `DiffPatternGlobal` | same, library frame | Used for the buffer; Miller rows only carry this (projected onto the unit cell) |
| `FTITracksUnit` | `X,Y` | FTI (fin trim / diffusion cut) columns × cells. Boundary FTI columns are excluded from DIFFOGD counts |
| `PolyPlugsUnit` | `X,V` | Plug model: poly is one segment per cell; plug value V closes the gap between cells V−1 and V (verified: plugs 3/4 → cells 2..4) |
| `TcnPlugsUnit` | `slot,V` | Same model for TCN slots |
| `TcnDepopUnit` | `slot,cells` | TCN removed |
| `VcxPatternUnit` | `vg,X,Y` / `vt,slot,Y` | Gate (VG) and TCN (VT) vias at M0 track Y; `|…` skew allowed |
| `VcrPatternUnit` | `slots,vcr` | VCR rail segments on the CRL |
| `VcrnubPatternUnit` | `slot,vcr,top\|bottom\|both` | VCRNUB stubs (1 poly pitch wide, 13.4 nm, measured on the PFT generator GDS) |
| `M0CutPatternUnit` | `X,Y` | X in **half poly pitches**, Y = M0 track (`rK` from the top track); verified on the CTG132 chassis |
| `DummyViaPattern` | `X,Y` | Dummy V0: X = global M1 track k (x = k − 0.5), Y = global M0 track (verified on the CTG132 chassis) |
| `TcnCutsUnit`, `TcnExtensionUnit`, `TCPSkewPatternUnit` | slot-axis patterns | TCN cuts, TCN extension to the CRL (VCRNUB), TCP (TCN plug between TCNs, parallel to poly) skews |
| `LO1ActiveTracks` / `HI1ActiveTracks` | `t1;t2` | Active M0 tracks of the LO/HI nets |
| `LO1Data` / `HI1Data` | `m0,m1,L,B,10,2/0,0;1,1` | from/to layer, castle side, pin edge, number of M1 jumpers, V0 every Nth jumper, per-unit-row jumper offset |
| `DrawMiscIDsUnit` / `DrawMiscIDsGlobal` | `layer,purpose,X,Y[,dL,dR,dB,dT]\|…` | Marker/TSDR ID boxes, see §1.6 |
| `CellHeight`, `PolyPitch`, `DesignContext` | `ch110/132/165`, `pp44`, `digital` | |

### CH165 signal-track numbering

CH165 N/P rows list `LO1/HI1ActiveTracks` in signal-track numbering (rails not counted). When the listed
tracks miss every via but the rail-skipping map `s + (s−1)//5` lands on them (e.g. 15/6 → M0 17/7), the mapped
tracks are used and a note is added. This recovered the 36/unit scalar of the 12 CTG165 `NZ7/PZ7-*-A` SDR rows.

## 1.4 Global / buffer fields

`*Global` versions of DiffPattern, FTITracks, PolyPlugs, TcnPlugs, TcnDepop, VcxPattern, VcrPattern,
VcrnubPattern, M0CutPattern and DrawMiscIDs are in **global** coordinates of the whole library frame:
X0 = BufferSizeL, Y0 = 2·BufferSizeB cells (M0: tracks-per-height·BufferSizeB). `rK` counts from the end of the
frame, so buffer patterns stay correct for any array size. Global shapes apply only outside unit cells
(unit inputs win where both exist). The GUI shows global coordinates with a `g` prefix.

## 1.5 Skew fields

| Field | Format | Meaning |
|---|---|---|
| `FTICdMisregSkew` | `ogdCD,pgdCD,ogdREG,pgdREG[,Y/N]` (µm) | FTI CD / misregistration; optional TSDRNoDepop flag |
| `TCNCdRegSkew` | same 4 slots | TCN CD / REG (drawn with TCG_mask) |
| `PolyCdSkew` | 4 slots **or** `PP;PolyCD;TCNCD` (µm) | Second form = poly-pitch skew (PLYCDPP rows, e.g. `0.043;0.011;0.012`); POR is 44/12/12 nm |
| `GLKSkew` | 4 slots | Poly-cut (GLK) skews |
| `PXLSkew` | `cd,reg[,shapes]` | PXL/TRM absolute bias (NOM 0.014 µm) and PGD registration |
| `IdLayerBasedSkew` | `poly:TSDRn12A` | ID-only poly CD skew: n/p sign, digits/10 nm (n12 = −1.2 nm). Drawn poly is unchanged; the ID biases it |
| `|d1..d4` on Vcx/Vcr/Vcrnub segments | µm | per-shape via / VCR resize-shift |

`scalar_snapshot.poly_pitch_skew`, `poly_skew_geometry`, `tcn_skew_geometry` and `_skew_geometry` are the
canonical parsers; `buildsheet_syntax_issues` rejects malformed values (including a bad `PP;CD;TCN` triple).

## 1.6 Misc / TSDR ID boxes (`DrawMiscIDs*`)

`layer,purpose,X,Y` draws an ID whose **X edges sit on poly/FTI centres** and whose Y covers diffusion
tracks `y0 .. max+1`. A `|` suffix is either

* **repetition** `|nx,ny,dx,dy` (integer counts + µm steps), or
* **edge moves** `|dL,dR,dB,dT` (µm, PFT style, e.g. `|-0.006,0.0215,…`).

`scalar_snapshot.misc_id_rects` implements both. Before this was implemented, TSDR tap IDs appeared as a
single tiny box offset by half a track.

## 1.7 Poly pitch

`x_pitch_nm(row)` returns 44 nm unless `PolyCdSkew` is in `PP;CD;TCN` form. All nm conversions (ruler,
measurement calibration, DR geometry) use it, so PLYCDPP rows measure correctly.

## 1.8 Decoder DSL (generator input)

The ISO generator (see [ISO generator](04_iso_generator_and_training.md)) uses a higher-level per-axis DSL
that expands into the buildsheet grammar:

| Decoder form | Buildsheet result |
|---|---|
| `All` | `0:r0` |
| `START:Skip:N:Repeat` | `START:<cell>[N]` (VcrnubPatternUnit: literal list `START/START+N+1/…`) |
| `START:END:Skip:GAP:Repeat` | repeated `a:b` segments filling the cell |
| `START:END:Skip:N` | `START:END[N]` |
| literal | passed through |

Decoder ↔ buildsheet pairs: FTIPattern→FTITracksUnit, PolyPlugs→PolyPlugsUnit, TCNPlugs→TcnPlugsUnit,
VcxPattern→VcxPatternUnit, M0CutPattern→M0CutPatternUnit, VcrPattern→VcrPatternUnit,
VcrNubPattern→VcrnubPatternUnit, plus `ResizeShift = "layer,d1,d2,d3,d4;…"` (vg/vt → VcxPatternUnit,
vcr → VcrPatternUnit, vcrnub → VcrnubPatternUnit).

## 1.9 Structure-name grammar

`<FAMILY><CH>-<MOS><Z/width>-<VT>-<TYPE>:<FEATURE>:<OGD|PGD>:<CD|REG|REF>:<N|P><nm>|NOM`, e.g.
`CTG132-NZ5-S-A:SDR:OGD:REF:2TRK`. Z codes: Z1 = CH110, Z5 = CH132, Z7 = CH165. The Test-row tab’s
**Suggest BKM names** rewrites only the tokens whose inputs changed. `ISO_StructureName_Validation.csv`
reports grammar and CH mismatches.
