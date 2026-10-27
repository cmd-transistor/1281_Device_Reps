# 2. Scalar calculation

[ScalarCalculation.py](../ScalarCalculation.py) turns a buildsheet row into a fail-mode count and an E-test
scalar, following *x1281z – ISO E-test Scalar calculations suggestion.pptx*:

$$\text{Scalar} = \frac{1}{\text{FailModeCount}}, \qquad \text{FailModeCount} = \text{fail modes per unit cell} \times \text{ArraySizeOgd} \times \text{ArraySizePgd}$$

Each scored row gets three 6-element vectors aligned with the bias plan:
`Test Type = I2 ×6`, `Force = -0.65,0.65,-0.75,0.75,-1.1,1.1`, `Scalar = 1/FM ×6`, plus a plain-text
`ScalarFormula` column.

## 2.1 Pipeline

```mermaid
flowchart TD
    A[row] --> B[family_of(StructureName)]
    A --> C[geometry_from_row: Geometry]
    C --> C1[FTI cols/cells]
    C --> C2[VG cols, VT slots → M0 tracks]
    C --> C3[poly/TCN plug spans]
    C --> C4[LO/HI tracks, TrackMap, device cells]
    B --> D{family rule}
    C --> D
    D --> E[Result: fail_modes, formula, notes]
    E --> F[Output / Review / Variants / Validation CSVs]
    E --> G[snapshot overlay (fail paths)]
```

`Geometry` carries the unit size, array size, `n_cells = 2·UnitCellSizePgd`, FTI segments, VG columns and VT
slots with their M0 tracks, plug maps, LO/HI track sets, Miller terminals and the via-track→cell map
(`TrackMap`). The via-track → diffusion-cell mapping and the plug model were calibrated on the chassis GDS
bundled in `ISO_LLM.bat`.

## 2.2 Family rules

| Family | Direction | Rule (per unit cell) |
|---|---|---|
| **CTG** contact-to-gate | OGD | #active PLY × #diffusion tracks on which an active (VT) TCN flanks the gate. One count per gate per track (either side of the gate is one path). SDR type-A reference = 2 per gate column per Pgd unit (`CTG_SDR_PER_GATE_COL`); other variants are counted from their own geometry and flagged when they differ |
| **GATEGATE** gate-to-gate | PGD | Tip-to-tip facing LO1 and HI1 gate segments on adjacent tracks. OC1/OC2/OC3 rows whose vias were removed keep the SDR count from the declared LO1/HI1 tracks |
| **EPIEPI** epi-to-epi | PGD | Facing LO1/HI1 diffusion/epi segments across track boundaries |
| **TCNTCN** TCN-to-TCN | PGD | Facing LO1/HI1 TCN segments across track boundaries |
| **DIFFOGD** diffusion isolation | OGD | #active FTI × #device diffusion tracks cut. Unit-boundary FTI columns excluded |
| **DIFFCHN** diffusion chain | series | #active VG gates in series along the chain (× #chains for stacked arrays such as 1×60) |
| VIA_CHAIN (`VT_MV`, `VG_MV`, `VCR_MV`) | — | Out of scope; scalar left blank (401 rows) |

Two single-cell tip-to-tip segments count as adjacent when their via tracks are closer than
`ETE_ADJ_CELLS = 1.75` cells (guards integer rounding). Active LO1/HI1 M0 tracks must have an M0→M1 pin route
and a matching via track to count connected paths.

## 2.3 Outputs

| File | Content |
|---|---|
| `ISO_Metadata_Output.csv` | Input columns + `Scalar`, `Test Type`, `Force`, `ScalarFormula` |
| `ISO_Scalar_Review.csv` | Per-row family, type, counts, formula, notes |
| `ISO_Scalar_Variants.csv` | 897 distinct geometry variants with row counts and snapshot names |
| `ISO_StructureName_Validation.csv` | Name grammar, CH token vs `CellHeight`, duplicate-name consistency |
| `--pptx` / `--summary-pptx` | PowerPoint catalog via [scalar_catalog.py](../scalar_catalog.py) |

```powershell
python ScalarCalculation.py
python ScalarCalculation.py --pptx ISO_Scalar_Catalog.pptx --summary-pptx ISO_Scalar_Summary.pptx
python ScalarCalculation.py --snapshot CTG110-NZ1-H-A:SDR:OGD:REF:2TRK --keep-snapshots
```

## 2.4 Verified results (library run)

2,581 rows → 2,180 scored, 401 via-chain rows blank, 897 variants, 0 name/buildsheet findings
([study/run_summary.txt](../study/run_summary.txt)). Representative per-test-row results:

| Test row | Family | Type | Rows | Fail modes | Note |
|---|---|---|---|---|---|
| x81zs_ctg01 | CTG | A | 72 | 10800 | |
| x81zs_ctg01 | CTG | D | 6 | 21600 | geometric 72/unit vs SDR 36/unit flagged |
| x81zs_diffchn01 | DIFFCHN | D | 53 | 11/19/31/48/1140 | 60 chains in 1×60 array counted |
| x81zs_diffiso01_e | DIFFOGD | A | 98 | 8400 | 1 boundary FTI column excluded |
| x81zs_epiepi01 | EPIEPI | C | 16 | 11520 | |
| x81zs_gategate03 | GATEGATE | SDR_PGD_REF | 90 | 3600/10800 | no vias; SDR-equivalent from plug/gate columns |
| x81zs_tcntcn01 | TCNTCN | B | 15 | 5760/11520 | |

Cross-checks performed while building:

* **DIFFCHN**: the net trace (renderer) and the scalar agree on chain length for 372 of 384 rows; the 12
  `U-B-STK` rows have no LO TCN plug (`17,3`) and are flagged.
* **CTG165**: the 12 CH165 CTG rows with VT track 17 but LO1 track 15 have no connected path under the
  strict M0 rule; with the rail-skipping map (§1.3) the 12 `NZ7/PZ7-*-A` SDR rows recover 36/unit.
* Name-declared lengths (`LEN16`) vs counted stacked gates (32) are reported as notes, not overwritten.

## 2.5 Corrections made during development

| Issue reported | Root cause | Fix |
|---|---|---|
| “3 active gates and 3 TCN but 4 active TCN counted” | Active TCN was not identified from VT vias | Active TCN = TCN slots carrying a VT on an active track; CTG = active PLY × flanking active TCN per diffusion track |
| “Two leakage paths counted per gate” | Left and right of a gate were counted separately | Leakage on either side of a gate is one path |
| “Gate overlapping two tracks counted once” (expected 72, got 36) | A slide-4 SDR-reference override replaced the geometric count for CTG E/F | Override removed: every CTG variant counted from its own layout (A/B/C/H/I/L/G-2TRK 10800, D/E/F/G-4TRK 21600, K 5400) |
| “More than one structure type per family” | Types were taken only from name letters | Variants grouped by type **and** scalar-relevant geometry (897 variants) |
| Continuous FTI drawn across two separate FTIs | Renderer merged FTI segments | Separate, non-bridging rectangles per FTI segment; layout drawn 1:1 with the buildsheet |
| Scalar derivation not visible | — | `ScalarFormula` column, e.g. `OGD CTG: 18 active VG gates/unit … = 72 fail modes/unit; x 5x60 unit cells = 21600; Scalar = 1/21600` |
| Test vectors | — | Six aligned `I2` / bias / scalar entries for all 2,180 scored rows |

The GUI recomputes the scalar on every edit (**Apply & recalculate**) and shows the library baseline next to
the working copy.
