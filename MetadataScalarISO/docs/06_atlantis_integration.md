# 6. Atlantis integration

The library comes from Atlantis, read **read-only** through the same Windows-authenticated WCF services the
Atlantis desktop client uses. The full technical reference (ports, bindings, every operation, gotchas,
security) is [ATLANTIS_ACCESS.md](../ATLANTIS_ACCESS.md); this page summarises how the tool uses it.

## 6.1 Components

| Component | Role |
|---|---|
| [atlantis_wcf_bridge.cs](../atlantis_wcf_bridge.cs) | C# console bridge compiled on first use with the .NET Framework `csc.exe` against the installed `Atlantis.Contracts.Definitions.dll`; prints JSON |
| [atlantis_wcf.py](../atlantis_wcf.py) | `AtlantisWcfClient`: compiles/caches the bridge in `%TEMP%\MetadataScalarISO\atlantis-wcf-<hash>`, runs operations, pivots parameters into buildsheet rows |
| [atlantis_wcf_probe.cs](../atlantis_wcf_probe.cs) | Discovery probe used while reverse-engineering the services |
| [atlantis_db.py](../atlantis_db.py) | Alternative Oracle path (`python-oracledb`, env `ATLANTIS_DSN/USER/PASSWORD`) that pivots `TESTSTRUCTUREPARAMETERS`; kept for environments with DB credentials |

## 6.2 Operations used by the GUI

| Bridge op | Client method | GUI use |
|---|---|---|
| `projects` | `list_projects()` | Project combo (defaults to `X81`) |
| `libraries` | `list_libraries(project)` | Library list |
| `rows` | `list_rows(project, library)` | Test rows with status, owner, last modified, templates used |
| `library-structures` | `list_library_structures(...)` | “Structures” tab: every structure of a library, searchable |
| `structures` | `list_structures(...)` | Structures of one test row |
| `parameters` | `fetch_structures(...)` | One buildsheet row per structure (column order from the CSV header, unknown parameters appended) |
| `templates` | `list_templates(...)` | Template definitions (name, terminals, parameter caption/type/default/help) — the source of the parameter meanings in [Template builders](05_template_builders.md) |
| `find-rows` | `find_rows(project, text)` | Project-wide test-row search (~10 s) behind the library search box |

Fetched rows get metadata columns (`AtlantisProject`, `AtlantisLibrary`, `AtlantisTemplates`,
`PadRowTemplate`, `AtlantisRowStatus`, `AtlantisRowOwner`, `AtlantisLastModified`) and open in a
`StructureDesigner(use_llm=False, source=(fields, rows))`. Results are cached per (project, library, row).
Calls run off the UI thread.

## 6.3 Search behaviour

* Typed text filters the row / structure lists as you type (case-insensitive, natural-language tokens rather
  than an exact string) and results are sorted alphabetically with the first match selected.
* Server-side regex is case-sensitive, so the bridge expands letters to `[dD]` classes; whitespace, `_` and `-`
  become `.*`.
* Catalog results span the whole project and are filtered client-side by library and exact row name.

## 6.4 How access was established

1. The first request was to replace the Excel/CSV buildsheet with an SQL query; `atlantis_db.py` (Oracle,
   credentials from environment variables) was written for that.
2. A SAP BusinessObjects (BI Launch Pad, port 8443) route was then tried; its interactive login did not work
   for scripted access.
3. The installed Atlantis ClickOnce client (“uses a separate Windows driver”) was inspected: it talks WCF
   `net.tcp` on ports 6000/6200/6300 with Windows transport security. The probe confirmed the contracts and
   the bridge was written against them; this became the production path.
4. No passwords or tokens are stored; the logged-in user’s Kerberos credentials are used.

## 6.5 Offline corpora

For regression tests without Atlantis, representative rows are cached (git-ignored):

| File | Content |
|---|---|
| `extracted/ply_rows.json` | MLR PLY skew rows (`x81zs_nm_ply03/04`) |
| `extracted/category_rows.json` | ~70 test rows: ROSPFT, FKTCN, contact KLV/CHN, BE ECD, BE chains, BE via KLV, with template definitions |
| `extracted/x81z_found_rows.json` | `find_rows` results per keyword |

[study/render_corpus.py](../study/render_corpus.py) renders a sample of every cached test row and lists any
structure without a preview.
