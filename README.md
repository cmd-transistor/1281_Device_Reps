# 1281 Device Test & E-Test Automation Repositories

This workspace contains automated toolchains, data extractors, and geometric scalar calculation suites for 1281 semiconductor parametric E-Test structures.

---

## Workspace Modules

### 1. [MetadataScalarISO](MetadataScalarISO/README.md)
**ISO E-Test Scalar Calculator, Geometry Parser & PowerPoint Catalog Generator**
- **Purpose**: Parses dense layout and netlist buildsheets (`ISO_Metadata_Input.csv`) for Isolation (ISO) E-Test structures, computes exact physical fail-mode counts and normalization scalars ($1/\text{FailModes}$), populates 6-bias electrical test vectors, performs structure-name anomaly validation, and generates visual PowerPoint catalogs.
- **Key Files**:
  - [ScalarCalculation.py](MetadataScalarISO/ScalarCalculation.py) — Core geometric parser, rule calculator, test-vector builder, and CSV writer.
  - [scalar_snapshot.py](MetadataScalarISO/scalar_snapshot.py) — 2D layout snapshot renderer with accurate layer, cut, and fail-site drawing.
  - [scalar_catalog.py](MetadataScalarISO/scalar_catalog.py) — Automated PowerPoint deck generator (`python-pptx`).
  - [ISO_Metadata_Output.csv](MetadataScalarISO/ISO_Metadata_Output.csv) — Primary scored dataset with aligned 6-element vectors for `Test Type`, `Force`, and `Scalar`.
  - [ISO_StructureName_Validation.csv](MetadataScalarISO/ISO_StructureName_Validation.csv) — Automated anomaly audit for buildsheet and name consistency.
  - [ISO_Scalar_Catalog.pptx](MetadataScalarISO/ISO_Scalar_Catalog.pptx) — Comprehensive 500+ slide catalog with layout snapshots and derivations.
  - [ISO_Scalar_Summary.pptx](MetadataScalarISO/ISO_Scalar_Summary.pptx) — 5-slide executive presentation.
- **Details**: See [MetadataScalarISO/README.md](MetadataScalarISO/README.md).

---

### 2. [SuperTOPAX](SuperTOPAX/README.md)
**Unified Topax/TPX E-Test Extraction & Plotting Suite**
- **Purpose**: Automates the extraction of Topax/TPX `.tgz` E-Test data from Intel servers and provides GUI/CLI visualization tools for per-die $I_\text{D}$-$V_\text{G}$ curves and wafer-level sparklines.
- **Key Files**:
  - `Topax_Extractor_GUI.py` — Main graphical interface for extracting data and triggering plotting tools.
  - `topax_idvg_plotter.py` — Renders per-die transfer characteristics ($I_\text{D}, I_\text{S}, I_\text{G}, I_\text{B}$ vs $V_\text{G}$) into 4-up PowerPoint decks.
  - `topax_wafer_sparkline.py` — Assembles per-wafer tile grids of Id/Is/Ig sparklines across dies.
- **Details**: See [SuperTOPAX/README.md](SuperTOPAX/README.md).

---

## Environment & Requirements

- Python 3.10+
- `matplotlib`, `python-pptx`, `pandas`
- `tkinter` (bundled with standard Python on Windows)
- `PyUber` (Intel internal, used in SuperTOPAX extraction)
