# 8. Catalogs and verification

The 81Z catalog decks are the visual ground truth for layouts. They are exported to images + text and used
both inside the GUI (**Catalog** button) and while developing renderers.

## 8.1 Catalog export (`catalog_refs.py`)

* Decks live in `81Z Catalog/` (git-ignored; some are sensitivity-encrypted, so export uses **PowerPoint COM**,
  not python-pptx).
* `pending_decks()` finds decks not exported yet (or changed); `extract()` writes
  `extracted/catalog/<deck>/slide_NNN.png` and `index.json` (slide text).
* `find_slides(row)` ranks slides best-first: exact structure name, name without the skew value, test row,
  then the skew feature (e.g. `PXL:REG`, `VCRNUB:OGD:CD`) within the same structure family. Slides are
  exported at 1600×900 read-only.

## 8.2 Decks and what they were used for

| Deck | Used to verify / learn |
|---|---|
| `X81Z_TC_ROSPFT_catalog.pptx` | ROSPFT pin placement (DF/SF/DS/SS/GATE, N vs P mirroring, 1X vs 6X instances) |
| `P1281_X81Z_MOL_Contact_Catalogue.pptx` | FKTCN layout (slide 2), layout delta by cell height and poly/TCN cut removal (slide 3), TCN CD skew coverage (slide 4) |
| `1281-TC_X81Z_MOL KLV_VT_VG_26WW33.5_Catalog.pptx` | VG/VT KLV line-end enclosure, REG PGD, GLK overlap, dummy-via nesting |
| `1281-TC_X81Z_MOS_PFT_PW_Catalog_v1.pptx` | FTI CD/REG OGD/PGD skew rows and pin labels (slide 8: NS165 FTI-CD-OGD:N3 pins match; catalog labels S/D the other way round) |
| `X81Z PFT PW skew catalog - Han.pptx` | PFT process-window skews: PolyCD + PP, PolyCD & TCN CD, `Poly:TSDRn12A/p12A`, TCN CD only, PXL REG/CD (PGD) |
| `1281-TC_X81Z_PW MLR_26WW33.5_Catalog.pptx` | MLR PW skews: PLYCDPP (PP 42 nm; FTI CD follows poly CD), PolyCD, TCN CD, PolyCD inverse TCN CD |
| `1281-TC_X81Z_LLE MLR_…`, `P1281_X81Z_LLE_Catalogue_…` | Local-layout-effect MLR context |
| `1281-TC_X81Z_Golden-PFT_MAT_GNAC_…` | GNAC / MAT / golden PFT cell-height and LNR DOE context |
| `1281-TC_X81Z_ADT_Catalog_…`, `X81Z_Etest_Memory_Catalog_…` | ADT and memory structures (reference) |
| `X78A_Reference/*`, `X78B_Reference/*` | Prior-node references: KLV, VIA KLV, DVB, CMB, GCN shorts/KLV/CHN, FS/BS ECD and via-chain, isolation coverage, well leakage |

## 8.3 Regression corpus

`study/render_corpus.py` renders the first, middle and last structure of every cached Atlantis test row
(`extracted/category_rows.json`) through the GUI path (`open_library_row` + `layout_png(panel=True)`) and
prints `OK`/`NO` with the renderer kind. At the end of the last round **every sampled structure rendered**:

| Category | Renderer | Before the fixes |
|---|---|---|
| ROSPFT (`xtrmiscx81z` rows) | ROSPFT | no preview |
| TCN KLV (9 test rows) | ISO via FE_ECD mapping | no preview |
| VCR via chains (`VCR_VCHN…`) | VIA_CHN | no preview |
| FKTCN | PFT + cut merge | rendered, but Kelvin TCN cut and GATE3 label |
| Gate KLV, VG/VT KLV, BE ECD, other chains | ISO / VIA_KLV / BE_ECD / VIA_CHN | rendered |

```powershell
$env:PYTHONPATH = (Get-Location).Path
python study\render_corpus.py                 # all cached rows
python study\render_corpus.py fktcn rospft    # filter by test-row substring
```

## 8.4 Other study scripts

| Script | Purpose |
|---|---|
| `list_variants.py`, `variant_table.py`, `show_variants.py` | Enumerate structure variants per family (`variants_*.txt`) |
| `measure_unit_y.py`, `column_positions.py` | Measure unit-cell Y and column positions on chassis GDS |
| `render_crop.py`, `render_all.py` | Render crops / all variants for visual review |
| `extract_pptx.py`, `pptx_to_png.py` | Early catalog/summary deck extraction |
| `dump_generators.py` | Print docstrings and definitions of the generators embedded in `*_LLM.bat` |
| `run_summary.txt` | Batch scalar run summary per test row/family/type |

## 8.5 Automated tests

`python -m unittest test_tsdr_rules -v` — 23 tests covering rules, geometry checks, skew parsing, misc-ID
rectangles and plain-language output (see [Design rules](07_design_rules.md#76-tests)).

## 8.6 Visual verification policy

* Compare each new renderer against at least one catalog slide of the same structure family.
* Prefer numeric checks (track indices, widths, bridge counts) over image inspection; image review was used
  sparingly and only for final confirmation.
* When a convention cannot be confirmed from a catalog or GDS, say so in the preview footer/notes rather than
  guessing silently (e.g. ROSPFT standard-cell content, VIA_KLV `DrawDiff` placement).
