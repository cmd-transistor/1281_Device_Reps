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
9. [AI Structure Designer (GUI + API)](#ai-structure-designer-gui--api)

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

## AI Structure Designer (GUI + API)

Use a text prompt to create an ISO structure, view its unit-cell layout and scalar, refine it with feedback, and export it as a buildsheet.

| File | Role |
| :--- | :--- |
| `iso_structure_ai.py` | Engine: interprets the prompt, retrieves a template, applies edits, validates the design, and learns from feedback |
| `iso_ai_gui.py` | Desktop GUI (Tkinter) |
| `iso_ai_api.py` | Local HTTP API |

### How it works
1. **Interpret**: A rule-based parser extracts family, cell height, MOS, VT flavor, type letter, array or unit-cell size, device width, design keywords such as `sdr`, `pxl`, `ftiml`, and `len10`, and keywords to avoid (for example, `not sdr`). Lines such as `PolyPitch=pp50` set buildsheet fields directly. Computed fields (`Scalar`, `Force`, `Test Type`, `ScalarFormula`) cannot be overridden.
2. **Retrieve**: The engine ranks every scorable structure in `ISO_Metadata_Input.csv` plus previously accepted designs against the spec. It uses IDF-weighted keyword matching and learned weights.
3. **Adapt**: VT flavor, device width, array size, and explicit field values are applied. The `StructureName` is updated. If the geometry changed, the name gets a unique `:AI` suffix.
4. **Validate**: `ScalarCalculation.compute` derives fail modes and the scalar. The engine fills the six-value `Test Type`/`Force`/`Scalar` columns and runs the structure-name checks.
5. **Learn**: **Accept** stores the design as a reusable reference and reinforces prompt-keyword-to-design-pattern weights. **Reject** penalises those weights and moves to the next-best template. **Revise** applies a change request, such as `make it HVT, array 10x60` or `try another template`. Learning is saved to `iso_ai_memory.json`, which is git-ignored.

Via chains are out of scope because the scalar calculator does not cover them. Track geometry depends on cell height, so the engine does not change cell height or MOS; it warns when no matching template exists.

### GUI
```bash
python iso_ai_gui.py
```
Enter a prompt, then press **Generate** (Ctrl+Enter). Double-click a buildsheet field to edit it. Use **Apply change**, **Accept (learn)**, or **Reject -> next option** to refine the design. **Export buildsheet...** writes the accepted designs and the current design to CSV. **Start API** serves the same engine on `127.0.0.1:8765`.

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

### Optional LLM interpretation
Set `ISO_AI_ENDPOINT` to an OpenAI-compatible `/chat/completions` URL, and set `ISO_AI_MODEL` and, if required, `ISO_AI_API_KEY`. The endpoint must use HTTPS, except for HTTP on localhost. The model only proposes spec fields. Each field is validated, explicit prompt values take priority, and the engine falls back to rule-based parsing if the model is unavailable.
