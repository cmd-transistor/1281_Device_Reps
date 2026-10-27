# Atlantis access (read-only, Windows-authenticated)

Atlantis is read through the same Windows-authenticated **WCF services** that the Atlantis desktop client uses — not through a direct database connection. A small C# bridge calls those services and prints JSON; Python wraps it. Nothing is written back to Atlantis.

## Prerequisites (on the user's Windows machine)

1. **Atlantis desktop app installed and launched once** for that Windows user. The ClickOnce install provides the client contract DLLs under
   `%LOCALAPPDATA%\Apps\2.0\...\Atlantis.Contracts.Definitions.dll` (plus the other `Atlantis*.dll`). The code searches there recursively and uses the newest copy.
2. **.NET Framework 4.x compiler** (ships with Windows): `C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe`. No SDK or Visual Studio needed.
3. **Domain Windows login with Atlantis access.** Authentication is the current user's Windows/Kerberos credentials — no passwords, tokens or stored secrets.
4. **Network reach** to `rf3pvwb460.rf3prod.mfg.intel.com` on TCP **6000, 6200, 6300**.

## Architecture

```
Python (atlantis_wcf.py)  --subprocess-->  AtlantisWcfBridge.exe  --net.tcp WCF (Windows auth)-->  Atlantis services
            ^                                     | JSON on stdout
            +-------------------------------------+
```

- `atlantis_wcf_bridge.cs` is a single C# file compiled on first use against the installed contracts DLL:
  ```
  csc /nologo /out:AtlantisWcfBridge.exe ^
      /r:System.ServiceModel.dll /r:System.Runtime.Serialization.dll /r:System.Web.Extensions.dll ^
      /r:<atlantis install>\Atlantis.Contracts.Definitions.dll atlantis_wcf_bridge.cs
  ```
- The exe and copies of the `Atlantis*.dll` live in `%TEMP%\MetadataScalarISO\atlantis-wcf-<hash>\`; the bridge is rebuilt automatically when the `.cs` file or the installed DLLs are newer.
- Each Python call runs `AtlantisWcfBridge.exe <operation> <args...>` and parses stdout as JSON. A non-zero exit code means an error; the message is on stderr.

## WCF connection details

- **Binding:** `NetTcpBinding(SecurityMode.Transport)`, `ClientCredentialType = Windows`, `TransferMode = StreamedResponse`, message size / reader quotas = `Int32.MaxValue`, open/close timeouts 2 min, send/receive 5 min.
- **Endpoint:** `net.tcp://rf3pvwb460.rf3prod.mfg.intel.com:<port>/Atlantis.<Service>`
- **Identity:** SPN `net.tcp/rf3pvwb460.rf3prod.mfg.intel.com`
- **Credentials:** `CredentialCache.DefaultNetworkCredentials` (the logged-in user).

| Port | Service | Contract | Used for |
|---|---|---|---|
| 6200 | `Atlantis.ConfigurationService` | `IConfigurationService` | projects, libraries, test-row name prefixes |
| 6300 | `Atlantis.CatalogService` | `ICatalogService` | test rows, structures, parameter values (catalog search) |
| 6000 | `Atlantis.StreamingMessageDataService` | `IStreamingMessageDataService` | template definitions (ReadOnly mode) |

## Operations

| Bridge op | Python method | Underlying calls |
|---|---|---|
| `projects` | `list_projects()` | `IConfigurationService.GetValidProjects()` |
| `libraries <project>` | `list_libraries(project)` | `GetLibraryNames(project, true)` |
| `rows <project> <library>` | `list_rows(...)` | `GetTestRowNamePrefix(project, library, false/true)`, then `ICatalogService.GetTestRowInfo(query)` with row regex `<prefix>.*`, filtered to the library. Returns status, owner, last modified, lock state, templates used, pad-row template, etc. |
| `library-structures <project> <library>` | `list_library_structures(...)` | same prefixes, then `GetTestRowStructInfo(query)` |
| `structures <project> <library> <row>` | `list_structures(...)` | `GetTestRowStructInfo` with row regex `^<row>$` |
| `parameters <project> <library> <row> <json-list>` | `fetch_structures(...)` | `GetTestRowParamInfo` with row regex `^<row>$`; one record per (StructureName, ParameterName, ParameterValue). Python pivots these into one buildsheet row per structure. Empty JSON list = all structures. |
| `templates <project> <library> <row>` | `list_templates(...)` | `GetTestRowSummary` (`TestRowEditMode.ReadOnly`), then `GetOrderedTemplates(trID)`: template name, description, terminals and each parameter's caption / type / default / help text |
| `find-rows <project> <text>` | `find_rows(...)` | `GetTestRowInfo` with a case-insensitive regex across all libraries of the project |

Catalog query object:

```csharp
new TestRowCatalogFindQuerykey {
    TestRowName   = "<regex>",
    StructureName = "<regex>",
    DBNames       = new List<string> { project }
}
```

## Gotchas

- **Regex is evaluated server-side.** Don't rely on inline options like `(?i)`. For case-insensitive search the bridge expands letters to character classes (`d` -> `[dD]`); whitespace, `_` and `-` become `.*`.
- **Catalog results span the whole project.** Always filter client-side by `Library` (and exact `TestRowName`).
- **Rows of a library** are found via that library's test-row name prefixes (released and scratch) from ConfigurationService, then a catalog search on `<prefix>.*`.
- **Parameter values are strings.** For a full buildsheet, column order was taken from an existing CSV header; parameters not in that header are appended.
- **Latency:** project-wide searches take ~10 s; single-row lookups a few seconds. Run calls off the UI thread and cache results.
- **Always close** each channel and factory (abort on failure) to avoid exhausting server connections.

## Security

- No secrets. Everything runs as the logged-in user; Atlantis permissions apply as usual.
- Keep it read-only: the bridge only uses Get/Find calls, and template access opens the row in `ReadOnly` mode (no lock taken, no edits).
- Do not hard-code another user's credentials or attempt impersonation; each user runs the tool under their own login.

## Files

- `atlantis_wcf_bridge.cs` — the bridge (self-contained).
- `atlantis_wcf.py` — Python wrapper (finds the contracts DLL, compiles, invokes, parses JSON). It imports `DEFAULT_INPUT` from `ScalarCalculation` only to read a CSV header for column order; replace or remove that if sharing standalone.

## Smoke test

```python
from atlantis_wcf import AtlantisWcfClient

c = AtlantisWcfClient()                      # compiles the bridge on first run
print(c.list_projects())
print(c.list_libraries("X81")[:5])
print(c.list_rows("X81", "isox81z_x81_lay")[:2])
print(c.find_rows("X81", "diffchn 04")[:3])
```
