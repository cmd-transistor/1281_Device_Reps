# MetadataScalarISO: ISO E-Test Scalar Calculator & Catalog Generator

Automated parser, geometric fail-mode scalar calculator, snapshot renderer, and PowerPoint catalog generator for 1281 technology Isolation (ISO) E-Test test structures.

---

## Table of Contents
1. [Overview & Purpose](#overview--purpose)
2. [Scalar Calculation Methodology](#scalar-calculation-methodology)
   - [Core Principle](#core-principle)
   - [Family Counting Rules](#family-counting-rules)
3. [Architecture & Workflow](#architecture--workflow)
   - [`ScalarCalculation.py`](#scalarcalculationpy)
   - [`scalar_snapshot.py`](#scalar_snapshotpy)
   - [`scalar_catalog.py`](#scalar_catalogpy)
4. [Input and Output Artifacts](#input-and-output-artifacts)
5. [Test-Vector Representation](#test-vector-representation)
6. [Structure-Name & Buildsheet Validation](#structure-name--buildsheet-validation)
7. [Dependencies & Requirements](#dependencies--requirements)
8. [CLI Usage Examples](#cli-usage-examples)
9. [81Z Structure Library](#81z-structure-library)

---

## Overview & Purpose

During semiconductor parametric E-testing, isolation test structures subject parallel device arrays (gates, diffusion, contacts, and epitaxial regions) to electrical stress to measure leakage and defects.

The measured leakage is proportional to the total number of parallel defect sites (fail modes) present in the structure:
$$\text{Scalar} = \frac{1}{\text{Fail Mode Count}}$$

Applying the scalar normalizes raw electrical leakage to a per-defect-mode basis. `MetadataScalarISO` parses dense buildsheet syntax (`DiffPatternUnit`, `VcxPatternUnit`, `FTITracksUnit`, `PolyPlugsUnit`, `TcnPlugsUnit`, etc.), reconstructs the 2D layout topology, calculates exact geometric fail mode counts, renders annotated visual snapshots, and compiles executive summaries and full slide catalogs.

---

## Scalar Calculation Methodology

### Core Principle
- **Array Tiling**: Total Fail Modes = $(\text{Fail Modes per Unit Cell}) \times \text{ArraySizeOgd} \times \text{ArraySizePgd}$.
- **SDR Standard**: Standard Design Rule reference structures establish the baseline unit-cell count.
- **Derived Scalar**: Output value is computed as $1 / \text{FailModeCount}$.

### Family Counting Rules

| Family | Classification | Unit-Cell Fail Mode Rule | Multiplier |
| :--- | :--- | :--- | :--- |
| **CTG** | Contact-to-Gate (OGD) | Counts active PLY gate columns overlapping active diffusion tracks that have a flanking active VT contact. *(1 count per gate per track)* | $\times \text{ArraySizeOgd} \times \text{ArraySizePgd}$ |
| **GATEGATE** | Gate-to-Gate (PGD) | Tip-to-tip facing active LO1 and HI1 gate segments on adjacent tracks. | $\times \text{ArraySizeOgd} \times \text{ArraySizePgd}$ |
| **EPIEPI** | Epi-to-Epi (PGD) | Facing active LO1 and HI1 diffusion/Epi segments across track boundaries. | $\times \text{ArraySizeOgd} \times \text{ArraySizePgd}$ |
| **TCNTCN** | TCN-to-TCN (PGD) | Facing active LO1 and HI1 TCN contact segments across track boundaries. | $\times \text{ArraySizeOgd} \times \text{ArraySizePgd}$ |
| **DIFFOGD** | Diffusion Isolation (OGD) | Interior active FTI (Fin/Diffusion isolation cut) segments passing through active device diffusion tracks. Boundary cuts excluded. | $\times \text{ArraySizeOgd} \times \text{ArraySizePgd}$ |
| **DIFFCHN** | Diffusion Chain / Gate Series | Active VG gates in series along the diffusion track. For multi-chain arrays (e.g., $1 \times 60$), multiplied by total chain instances. | $\times \text{ArrayCount}$ |
| **VIA_CHAIN** | Via Chains (`VT_MV`, `VG_MV`, `VCR_MV`) | Vertical via-chain structures. Out of scope for lateral ISO scalar calculation (left blank). | None |

---

## Architecture & Workflow

```
                   ISO_Metadata_Input.csv
                             │
                             ▼
                 ┌───────────────────────┐
                 │  ScalarCalculation.py │ ◄── Parses geometry, syntax, & array dimensions
                 └───────────┬───────────┘
                             │
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
ISO_Metadata_Output.csv   ISO_Scalar_Review.csv   ISO_Scalar_Variants.csv
 (6-element vectors)     (Formulas & detail)    (Geometry catalog index)
         │                                       │
         ▼                                       ▼
ISO_StructureName_Validation.csv         ┌───────────────────────┐
 (Anomaly detection report)              │  scalar_snapshot.py   │ ◄── Matplotlib visual CAD engine
                                         └───────────┬───────────┘
                                                     │ (Generates PNG snapshots)
                                                     ▼
                                         ┌───────────────────────┐
                                         │   scalar_catalog.py   │ ◄── python-pptx presentation builder
                                         └───────────┬───────────┘
                                                     │
                                 ┌───────────────────┴───────────────────┐
                                 ▼                                       ▼
                      ISO_Scalar_Summary.pptx                 ISO_Scalar_Catalog.pptx
                       (5-slide Executive Deck)                (500+ slide variant catalog)
```

### `ScalarCalculation.py`
- **Coordinate & Token Parser**: Parses unit-cell indices (`0:r0[3]`), repetition spans, FTI cut intervals, and plug arrays.
- **Track Map (`TrackMap`)**: Maps logical electrical tracks ($0 \dots N$) to vertical physical diffusion/gate cell coordinates across different cell heights (`ch110`, `ch132`, `ch165`).
- **Rule Evaluators**: Implements verified arithmetic and topological counting for all 6 active families.
- **Output Formatter**: Generates test vectors, review sheets, variant lists, and anomaly reports.

### `scalar_snapshot.py`
- **Visual CAD Rendering**: Draws high-resolution 2D unit-cell layout diagrams using `matplotlib`.
- **Accurate Layer Rendering**: Renders diffusion wells, poly gate columns, active TCN contacts, LO1/HI1 metal vias, and segmented FTI cuts (with separate non-bridging rectangles for discontinuous cut tracks).
- **Fail Site Annotations**: Highlights active leakage/fail sites with callout indicators.

### `scalar_catalog.py`
- **PowerPoint Automation**: Creates presentation decks via `python-pptx`.
- **Executive Summary Deck** (`ISO_Scalar_Summary.pptx`): 5-slide deck with workflow overview, test-vector format, methodology, and full family/test-row statistics.
- **Full Catalog Deck** (`ISO_Scalar_Catalog.pptx`): ~500 slide deck embedding high-resolution snapshots, geometric parameter tables, formula derivations, and design pattern classifications for every variant.

---

## Input and Output Artifacts

| File | Type | Description |
| :--- | :--- | :--- |
| `ISO_Metadata_Input.csv` | Input | Raw buildsheet containing 2,581 structure rows, coordinates, and layer patterns. |
| `ISO_Metadata_Output.csv` | Output | Primary output with populated `Test Type`, `Force`, and `Scalar` columns plus `ScalarFormula`. |
| `ISO_Scalar_Review.csv` | Output | Detailed evaluation table with `FailModes`, `Scalar`, `Formula`, `Notes`, and breakdown metrics. |
| `ISO_Scalar_Variants.csv` | Output | Aggregated catalog of 897 unique geometry variants, snapshot references, and row counts. |
| `ISO_StructureName_Validation.csv` | Output | Audit report verifying structure names against buildsheet parameters and geometry consistency. |
| `ISO_Scalar_Summary.pptx` | Output | 5-slide executive presentation explaining calculation methodology and coverage. |
| `ISO_Scalar_Catalog.pptx` | Output | 501-slide comprehensive visual catalog containing annotated layout snapshots for all variants. |

---

## Test-Vector Representation

For every scored structure (2,180 rows), the script populates three 6-element comma-separated vectors aligned with standard multi-bias parametric test routines:

- **`Test Type`**: `I2,I2,I2,I2,I2,I2`
- **`Force`**: `-0.65,0.65,-0.75,0.75,-1.1,1.1`
- **`Scalar`**: Six identical scalar entries repeating $\frac{1}{\text{FailModes}}$ (e.g. `9.25926e-05,9.25926e-05,9.25926e-05,9.25926e-05,9.25926e-05,9.25926e-05`)

Unsupported structures (401 via-chain rows) leave these columns blank.

---

## Structure-Name & Buildsheet Validation

`ScalarCalculation.py` includes a built-in anomaly detection engine outputting to `ISO_StructureName_Validation.csv`:
- **Name Grammar**: Validates naming structure: `<FAMILY><HEIGHT>-<MOS><FLAVOR>-<VARIANT>-...`
- **Cell Height Consistency**: Verifies that numeric height tokens in names (`110`, `132`, `165`) match the buildsheet `CellHeight` (`ch110`, `ch132`, `ch165`).
- **Duplicate Name Consistency**: Ensures that rows sharing identical `StructureName` values produce consistent scalar calculations.

---

## Dependencies & Requirements

- Python 3.10+
- `matplotlib` (for visual snapshot generation)
- `Pillow` (for smooth preview zoom and image clipboard encoding)
- `pywin32` (Windows desktop **Copy PNG** clipboard support)
- `python-pptx` (for PowerPoint catalog assembly)
- Python standard library (`csv`, `re`, `argparse`, `dataclasses`, `pathlib`, `collections`)

---

## CLI Usage Examples

Run complete calculation and generate all CSV outputs:
```bash
python ScalarCalculation.py
```

Rebuild CSV outputs and the 5-slide executive summary PowerPoint:
```bash
python ScalarCalculation.py --summary-pptx ISO_Scalar_Summary.pptx
```

Rebuild CSV outputs, re-render all layout snapshots, and build the 501-slide full catalog:
```bash
python ScalarCalculation.py --pptx ISO_Scalar_Catalog.pptx --summary-pptx ISO_Scalar_Summary.pptx
```

Render snapshot for a single specific structure:
```bash
python ScalarCalculation.py --snapshot CTG110-NZ1-H-A:SDR:OGD:REF:2TRK --keep-snapshots
```

---

## 81Z Structure Library

Browse existing 81Z ISO structures, inspect their unit-cell layouts and scalar calculations, and copy or export the selected buildsheet. The desktop GUI does not generate new structures.

| File | Role |
| :--- | :--- |
| `iso_ai_gui.py` | Desktop library browser (Tkinter) |
| `iso_structure_ai.py` | Loads library rows, computes scalar metadata, and renders the selected structure |
| `iso_ai_api.py` | Separate optional prompt-generation HTTP API; it is not used by the library GUI |

### GUI
```bash
python iso_ai_gui.py
```
The catalog loads existing rows from `ISO_Metadata_Input.csv`. Search by structure name, test-row identifier, or comments; filter by family or test row; select a row to see its annotated unit-cell layout, fail-mode count, scalar, and complete buildsheet. **Hide search** collapses the filters and results; **Show search** restores them. In the Buildsheet tab, select a field, change its value, and press Enter or **Apply & recalculate** to refresh the scalar and preview. The original and working values appear side by side; the Summary tab compares fail modes and scalar with the library baseline. Double-click a cell to copy it; Ctrl+C copies the selected working value, Ctrl+V pastes into the edit box, and right-click offers cell/field-and-value copy and paste. **Reset working copy** restores the selected row. **Copy buildsheet with header** copies the working row as tab-separated data, and **Export working buildsheet...** saves it as CSV. Edits never change `ISO_Metadata_Input.csv`. Invalid numeric dimensions and unparseable core geometry axes are rejected; via-chain rows remain outside lateral ISO scalar calculation (no scalar) but now render a layout preview from their buildsheet (DiffPatternGlobal is used when DiffPatternUnit is empty; VcxPatternUnit `|d1,d2,d3,d4` REG values offset the via markers).

The preview offers **Fit**, **Actual size**, `+`/`-` zoom (also Ctrl+mouse wheel), and **Copy PNG**, which copies the rendered image to the Windows clipboard as PNG and DIB. Layer toggles cover NDIFF, PDIFF, FTI, PLY, TCN, VG, VT, VCR/VCRNUB, PXL/TRM, numbered thin M0 lines/cuts, LO1/HI1, V0 landings, fail paths, **Grid**, and **Guide tracks**. The plot reserves a right-side legend and omits the long formula/title; the formula remains in Selection details. Grid is a light gray overlay, while Guide tracks shows a schematic poly/TCN/M0 reference (not actual tccDebug polygons). Actual `tccDebug`/`tccGuide` misc-ID shapes become separate layer toggles only when present in `DrawMiscIDsUnit`; none are in the current library or bundled layer map. VCR rails sit at NP boundaries; the VCR Y scale labels every boundary from 0 through `UnitCellSizePgd`, including boundaries without a rail. Edited `DiffPatternUnit` widths change diffusion band height relative to the cell-height grid. Experimental fields `PreviewPlyWidthNm`, `PreviewTcnWidthNm`, `PreviewVcrWidthNm`, and `PreviewM0WidthNm` survive working-copy CSV export; the M0 field changes the thin stroke rather than adding a fill. These fields are not process-qualified parameters. M0 tracks are equally spaced with track 0 on the unit boundary: 5 tracks per cell height for CH110/CH132 (22/26.4 nm) and 6 for CH165 (27.5 nm); every multiple is a power rail. VG/VT vias, V0 landings, and LO1/HI1 tracks all use this same M0 track index (CH132 checked against the bundled chassis GDS; the library scalars are unchanged by this model). Active LO1/HI1 M0 tracks require an M0-to-M1 pin route and a matching via track to count connected paths; OC reference scalars remain reference-only. The 12 CH165 CTG library rows with VT track 17 but LO1 M0 track 15 now have no connected CTG path under this rule. Use a height-specific layout for signoff.

**Current flow** (on by default) traces connectivity from the buildsheet: TCN -> VT -> M0 -> V0, PLY -> VG -> M0 -> V0 and TCN -> VCRNUB -> VCR -> M0 rail. M0 tracks are split by `M0CutPatternUnit` (X in half poly pitches, Y = M0 track, `rK` counted from the top track; checked on the CTG132 chassis GDS). Nets reaching the LO1 V0 are blue, HI1 red, and floating M0 jumper nets that carry current between LO1 and HI1 through gates driven by LO1/HI1 (for example DIFFCHN serpentines) purple, with the conducting gate channels marked. The traced DIFFCHN chain length matches the scalar for 372 of 384 DIFFCHN rows; the 12 `U-B-STK` rows have no LO TCN plug (`17,3`) and are flagged. Rows whose LO1/HI1 M0 tracks carry no via show a red buildsheet check above the layout and in Summary. CH165 N/P rows list LO1/HI1 in signal-track numbering (power rails not counted): when the listed tracks miss every via but the rail-skipping mapping (`s + (s-1)//5`, e.g. 15/6 -> M0 17/7) lands on them, the mapped tracks are used and noted; this gives the 12 CTG165 `NZ7/PZ7-*-A` SDR rows their 36/unit scalar (previously blank). `LO1Data`/`HI1Data` (`m0,m1,L,B,10,2/0,0;1,1`) are decoded as from/to layer, castle side, pin edge, number of M1 jumpers, V0 every Nth jumper, and per-unit-row jumper offset (row 0 -> 0, row 1 -> 1); Global view draws the jumpers and V0 landings in the castle accordingly (checked against the CTG132 chassis GDS: 5 V0 per track, alternating rows shifted by one jumper). Active PLY/TCN fills follow the traced nets. PLY is drawn under FTI; FTI cuts only the active (electrical) gate segment. On TCN slot axes (VT, TcnPlugsUnit, VCRNUB) `rK` counts from the last slot (ogd-1); library scalars are unchanged. `PXLSkew` CD is the absolute PXL bias (NOM 0.014), so N5/P5 render 9/19 nm; PXL/TRM turns on automatically for PXL-skew rows. The Buildsheet tab has a **Decoded layers** view that lists every pattern segment with its X/Y specs, expanded positions, parameters, and shift.

M0 power rails sit on every cell-height boundary (every 5 tracks for CH110/CH132, every 6 for CH165); they are drawn and numbered in orange as `CRLk`, the VCR sites. A gate cell is cut only when the (skewed) FTI covers most of it, and the active gate fill is clipped at the FTI edge, so small FTI misregistration no longer hides the active gate. **Fit** (or double-click on the layout) restores the full-fit view after zooming. **View** selects **Unit** (unit cell only), **Global** (buffer ring, no V0 routing) or **Global + routing** (adds the LO/HI castle: M1 jumpers, V0 landings, pins). Global draws the buffer ring (BufferSizeL/R/B/T) around one unit cell by default (the `units` boxes set 1-4 x 1-4 cells). Unit cells are rendered from the `*Unit` fields exactly as in the normal view; `*Global` fields (DiffPattern, FTITracks, PolyPlugs, TcnPlugs, TcnDepop, VcxPattern, VcrPattern, VcrnubPattern, M0CutPattern) apply only outside the unit cells and are ignored where unit inputs exist. Global coordinates are poly tracks / diffusion tracks / M0 tracks of the whole frame, and `rK` counts from the end of the shown frame, so buffer patterns stay correct for any array size; `DrawMiscIDsGlobal` boxes are drawn over the frame. In Global view, `*Global` shapes overwrite the unit cell where they land; the default buffer (per-cell PLY and TCN in every slot, unless `PolyPlugsGlobal`/`TcnPlugsGlobal`/`TcnDepopGlobal` say otherwise) fills only the area outside the unit cells. LO1/HI1ActiveTracks stay in unit M0 coordinates and are drawn on their global M0 tracks with V0 landings at the frame edge. **Coords** (enabled only in Global view; the normal view always uses unit coordinates) switches axis numbering for poly, diffusion and M0 tracks between unit coordinates, global coordinates (`g` prefix: unit + BufferSizeL / 2*BufferSizeB / tracks-per-height*BufferSizeB) and both; the Decoded layer table lists `*Global` fields in `g` coordinates of the full library structure. The TSDR dropdown lists every ID present (name, non-zero skew values, explicit and global misc IDs) with a count; hover for the list. The **Edit / TSDR** box also accepts structure edits: `buffer L2 R2 B1 T1`, `buffer 3`, `array 5x60`, `unit 24x4`, `cell height 165`, `lo 6;14`, `hi 9 11`, `global tcn 0:3,3:r3[1]`, `global vt 1:3,8`, `unit fti 3/6,2:3`, `set FIELD = value`, `append FIELD += segment` (separate requests with `; `). **Add TSDR** accepts plain-language skews (hover for syntax), e.g. `fti ogd cd -3nm`, `tcn pgd reg +2`, `pxl cd p5`, `vcr pgd cd -4`, `id tcn TSDRActiveL x 0:24 y 2:5`, `reset fti`; the request edits the working copy and recalculates. FTI/TCN/PolyCd/GLK skew vectors render every non-zero slot (OGD CD, PGD CD, OGD REG, PGD REG), so added skews show even when the name has no CD/REG tag. PXL/TRM uses `TRMDRAWN` geometry from the closest bundled chassis, cropped to the selected unit-cell view. TSDR targets without matching coordinates are shown as metadata only. Layer toggles change the preview only.

**Measure** overlays an nm grid on the preview. The grid spacing adapts to the zoom (0.1 nm to 2 µm steps, at least 45 screen pixels apart) and the lines are labelled. Each rendered PNG is saved with a `.json` calibration sidecar (origin, pixels per track, 44 nm per poly track, CH/2 nm per diffusion track). The pointer position is shown at 0.1 nm resolution, together with the current nm-per-screen-pixel value. Zoom in to pick finer points. Left-click two points to get dx, dy and d. Right-click or Esc clears the points. Double-click Fit is disabled while measuring.

**Pattern by repetition**: the Edit box understands keep/skip/repeat. Examples:
- `tcn plug start 4 keep 3 more skip 1 repeat` gives `4:7/9:12/14:17/19:22`.
- `poly plug start 2 keep 3 skip 1 repeat 4 times y: 2/4`.
- `vt start 1 keep 2 skip 2 repeat until 22 y: 14`.
- `fti start 3 keep 1 skip 2 repeat y: start 2 keep 2 skip 2 repeat`.

Indices are 0-based tracks. "keep N more" counts the start track as well. `repeat` without a count fills to the end of the unit, or of the frame when you say `global`. If Y is omitted, the layer's existing Y is reused. The result is appended unless you add `replace`/`only`/`instead`. Join several requests with `; `.

The TSDR layer dropdown offers only drawable IDs with row geometry: explicit `IdLayerBasedSkew` targets, coordinate-backed `DrawMiscIDsUnit` IDs, and supported name-derived overlays. FTI CD/REG selects `fti:TSDRActiveCDID`/`fti:TSDRActiveMisregID`; PXL and VCR/VCRNUB CD/REG select their matching mask IDs; TCP-related skew selects `TCP_mask` IDs. PXL variants can select `PXL_mask`, while coordinate-backed TCN, ESD-mask, and NES-mask IDs remain available as row-specific choices. Nominal variants without a changed ID default to Off. M0 Y grid is a separate optional amber overlay at the exact numbered M0 rail coordinates. It can be enabled independently of the light-gray diffusion Grid and the poly/TCN Guide tracks overlay.

The TSDR dropdown lists the applicable IDs (the debug-only `poly:TSDRn1` is skipped). **Region** hatches the shapes the selected ID applies to: its misc ID box, or the FTI, PLY, TCN, TCP, VCR, VCRNUB or PXL shapes it selects. Global IDs are shown as boxes in Global view. In Unit view, the units boxes tile N x M unit cells without a buffer. M0 lines start hidden; the LO1/HI1 tracks start visible. The physical geometry changes come from buildsheet skew values: FTI/TCN CD or REG vectors adjust the corresponding width or position on the named OGD/PGD axis, and `PXLSkew` adjusts TRMDRAWN geometry. VCR/VCRNUB `|d1,d2,d3,d4` suffixes use the row's OGD/PGD and CD/REG tags as OGD CD, PGD CD, OGD REG, and PGD REG, respectively; values adjust the matching dimension or position in nanometers. A VCRNUB CD adjustment that would reduce a dimension below zero is limited to the renderer's minimum schematic width. `PolyCdSkew` and `GLKSkew` also adjust PLY plug geometry when populated, but neither field has nonempty examples in the current CSV. TCP has its own layer toggle; its active overlay appears only over selected TCN plug spans when both TCP and TCN are visible. VG/VT toggles show via markers only; active conductor highlights remain subordinate to PLY/TCN toggles. TRM starts hidden.

### Optional generation API
The separate `iso_ai_api.py` command-line service retains the prompt-generation workflow. It is not started by or connected to `iso_ai_gui.py`.

### API
```bash
python iso_ai_api.py [--host 127.0.0.1] [--port 8765] [--no-llm]
```

| Method | Endpoint | Body / Result |
| :--- | :--- | :--- |
| GET | `/health` | Engine statistics |
| POST | `/structures` | `{"prompt": "..."}` -> design JSON (201) |
| GET | `/structures/<id>` | Current design |
| POST | `/structures/<id>/feedback` | `{"action": "revise"\|"accept"\|"reject", "text": "..."}` |
| GET | `/structures/<id>/buildsheet.csv` | One-row buildsheet |
| GET | `/structures/<id>/layout.png` | Unit-cell layout preview |

```python
import json, urllib.request
req = urllib.request.Request("http://127.0.0.1:8765/structures", method="POST",
    data=json.dumps({"prompt": "CTG 132 NP HVT type A SDR reference"}).encode(),
    headers={"Content-Type": "application/json"})
design = json.load(urllib.request.urlopen(req))
print(design["structure_name"], design["fail_modes"], design["scalar"])
```

The API binds to localhost by default. If `ISO_AI_API_TOKEN` is set, every request must include `Authorization: Bearer <token>`; binding to a non-loopback host requires this token. Request bodies are limited to 64 KB.

### LLM geometry generation
Set these environment variables before starting the optional generation API. Use a provider-approved endpoint and model name; do not put API keys in source files.

```powershell
$env:ISO_AI_ENDPOINT = "https://<provider>/v1/chat/completions"
$env:ISO_AI_MODEL = "<model-name>"
$env:ISO_AI_API_KEY = "<provider-api-key>"
python iso_ai_api.py
```

The endpoint must use HTTPS, except for HTTP on localhost. The library GUI does not require an endpoint or model.
