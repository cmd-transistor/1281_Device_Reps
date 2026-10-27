# 9. GUI guide (`iso_ai_gui.py`)

```powershell
python iso_ai_gui.py
```

The window opens full screen with the **Atlantis lookup** (Windows credentials). It is a library viewer and
editor: it shows existing 81Z structures, recomputes scalars, previews layouts and lets you edit a working
copy. It never writes to Atlantis or to `ISO_Metadata_Input.csv`.

## 9.1 Finding structures

| Area | Use |
|---|---|
| **Atlantis lookup** → **Test rows** | Pick *Project / node* and library, search rows as you type, **Open selected rows** (several rows can be opened together) |
| **Atlantis lookup** → **Structures (whole library)** | Every structure of the library with row, pad and description; type to filter |
| **Structure viewer** list | Structures of the opened rows; natural-language search, column filters (*Clear column filters*, *Sort A → Z / Z → A*), **Hide search** to enlarge the layout |
| **Catalog** | Catalog slides that best match the selected structure (see [Catalogs](08_catalogs_and_verification.md)) |

## 9.2 Layout preview

| Control | Behaviour |
|---|---|
| **View** | Unit / Global / Global + routing; **units** boxes (N × M unit cells); **Coords:** unit, global (`g`), both |
| **Unit cell** / **Full structure** | Jump to 1×1 / the library array (Full structure turns Grid, M0 Y grid and Guide tracks off and restores them on Unit cell) |
| **Layers** | Toggles depend on the renderer (ISO, PFT, VIA_CHN, BE_ECD, VIA_KLV, ROSPFT) |
| **TSDR:** + **Region** | Show a TSDR / misc ID and shade its region |
| **Fit**, `+`/`−`, Ctrl+wheel, box zoom | Zoom; past 100 % the layout re-renders sharper (up to 4×) |
| **Copy PNG**, **Open PNG**, **Include legend** | Clipboard (PNG + DIB) / file, optionally with legend |
| **Open in KLayout** | GDS + `.lyp` of the current view (GDS source: Generator for CTG132, otherwise Preview drawing) |
| **Measure** (K), **Keep**, **Snap**, **Straight (H/V)**, **Measurements (n)**, **Erase all** | nm grid, numbered measurements, snapping to drawn edges/centres |
| **DR highlights**, **DR details** | Numbered DR findings on the Unit view; plain vs technical DR text |
| **Design rules** | Rule browser, **Review structure**, **Show details**, **Open outsheet** |
| Right-click on a shape | “Remove …” performs a real buildsheet edit (not just hiding) |

Rulers outside the plot show poly tracks (top), diffusion tracks (left) and M0 tracks / CRL (right).

## 9.3 Tabs

| Tab | Content |
|---|---|
| **Summary** | Family, type, fail modes, scalar, formula, connectivity/syntax/DR review (plain unless DR details), library baseline vs working copy |
| **Buildsheet** | Field / Library value / Working value table with filled vs empty separation, **Decoded layers** view (each pattern segment with X/Y specs and expanded positions), **Edit:** box with live hint, **Apply & recalculate**, **Clear field**, **Reset working copy**, **Copy buildsheet with header**, **Export working buildsheet...** |
| **Test row** | Collect structures (**+ Add to test row**, **Paste buildsheet** from TSV/CSV), edit entries, **Suggest BKM names** / **Suggest type comment**, **Copy test row**, **Export test row...** |
| **Selection details** | Library row metadata, buildsheet metadata, formula and reasoning |

## 9.4 Edit box language

The **Edit:** box accepts field assignments and higher-level requests, with a one-line hint that shows what
Enter will change:

| Example | Effect |
|---|---|
| `set PolyCdSkew = 0.043;0.011;0.012` | Assign a field (syntax checked first) |
| `append VcxPatternUnit += vt,3,14` | Add a segment |
| `buffer 3`, `buffer L2 R2 B1 T1` | Buffer sizes |
| `fti mode ml` / `fti mode sl` | FTI multi-line / single-line |
| `<layer> start N keep K skip S repeat …` | Generate repeated patterns |
| `remove fti at ply 4 diff 3` | Remove a shape (same as right-click) |
| TSDR requests (e.g. FTI/TCN/PLY/GLK CD or REG skews) | Parsed into skew fields with the implied TSDR IDs |

Invalid syntax blocks the edit; DR checks run on the edited row and report in plain language.

## 9.5 Performance notes

* Rendering runs off the UI thread; PNGs are cached per design/version/layers/view.
* Full structure of large arrays uses raster tiling (21×250 in ~9 s).
* Atlantis calls are cached per project/library/row.
