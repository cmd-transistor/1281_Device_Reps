"""Windows-authenticated access to Atlantis through its installed WCF client contracts."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ScalarCalculation import DEFAULT_INPUT

BRIDGE_SOURCE = Path(__file__).with_name("atlantis_wcf_bridge.cs")


def _installed_contract() -> Path:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    candidates = list((local_app_data / "Apps" / "2.0").rglob("Atlantis.Contracts.Definitions.dll"))
    if not candidates:
        raise RuntimeError("Atlantis is not installed for this Windows user; install/launch the Atlantis desktop app first.")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _compiler() -> Path:
    windows = Path(os.environ.get("WINDIR", r"C:\Windows"))
    candidates = (
        windows / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe",
        windows / "Microsoft.NET" / "Framework" / "v4.0.30319" / "csc.exe",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("The .NET Framework compiler needed by the installed Atlantis client was not found.")


class AtlantisWcfClient:
    """Read Atlantis project, library, row, structure, and parameter data using Windows auth."""

    def __init__(self) -> None:
        contract = _installed_contract()
        self.assembly_dir = contract.parent
        source_stamp = max(BRIDGE_SOURCE.stat().st_mtime_ns, contract.stat().st_mtime_ns)
        suffix = hashlib.sha256(str(self.assembly_dir).encode("utf-8")).hexdigest()[:12]
        self.runtime_dir = Path(tempfile.gettempdir()) / "MetadataScalarISO" / f"atlantis-wcf-{suffix}"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.executable = self.runtime_dir / "AtlantisWcfBridge.exe"
        self._sync_assemblies()
        if not self.executable.is_file() or self.executable.stat().st_mtime_ns < source_stamp:
            self._compile()

    def _sync_assemblies(self) -> None:
        for assembly in self.assembly_dir.glob("Atlantis*.dll"):
            target = self.runtime_dir / assembly.name
            if not target.exists() or target.stat().st_size != assembly.stat().st_size \
                    or target.stat().st_mtime_ns < assembly.stat().st_mtime_ns:
                shutil.copy2(assembly, target)

    def _compile(self) -> None:
        windows = Path(os.environ.get("WINDIR", r"C:\Windows"))
        framework = windows / "Microsoft.NET" / "Framework64" / "v4.0.30319"
        if not framework.exists():
            framework = windows / "Microsoft.NET" / "Framework" / "v4.0.30319"
        compiler = _compiler()
        command = [
            str(compiler), "/nologo", f"/out:{self.executable}",
            f"/r:{framework / 'System.ServiceModel.dll'}",
            f"/r:{framework / 'System.Runtime.Serialization.dll'}",
            f"/r:{framework / 'System.Web.Extensions.dll'}",
            f"/r:{self.assembly_dir / 'Atlantis.Contracts.Definitions.dll'}",
            str(BRIDGE_SOURCE),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=60,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise RuntimeError("Could not build the Atlantis service bridge: " + (result.stderr or result.stdout).strip())
        self.executable.touch()

    def _call(self, operation: str, *arguments: str) -> Any:
        command = [str(self.executable), operation, *arguments]
        result = subprocess.run(command, capture_output=True, text=True, timeout=600,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            message = result.stderr.strip() or result.stdout.strip() or "Atlantis service call failed"
            raise RuntimeError(message)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Atlantis returned an invalid response.") from exc

    def list_projects(self) -> list[str]:
        return self._call("projects")

    def list_libraries(self, project: str) -> list[str]:
        return self._call("libraries", project)

    def list_rows(self, project: str, library: str) -> list[dict[str, Any]]:
        return self._call("rows", project, library)

    def list_library_structures(self, project: str, library: str) -> list[dict[str, Any]]:
        return self._call("library-structures", project, library)

    def list_structures(self, project: str, library: str, test_row: str) -> list[dict[str, Any]]:
        return self._call("structures", project, library, test_row)

    def list_templates(self, project: str, library: str, test_row: str) -> list[dict[str, Any]]:
        """Template definitions used by a test row (name, description, parameter captions and help text)."""
        return self._call("templates", project, library, test_row)

    def find_rows(self, project: str, fragment: str) -> list[dict[str, Any]]:
        """Test rows in every library of ``project`` whose name contains ``fragment`` (case-insensitive)."""
        return self._call("find-rows", project, fragment)

    def fetch_structures(self, project: str, library: str, test_row: str,
                         structure_names: list[str]) -> tuple[list[str], list[dict[str, str]]]:
        raw = self._call("parameters", project, library, test_row,
                         json.dumps(structure_names, ensure_ascii=True, separators=(",", ":")))
        fields = self._csv_fields()
        by_lower = {field.casefold(): field for field in fields}
        grouped: dict[str, dict[str, str]] = {}
        for item in raw:
            name = str(item.get("StructureName") or "")
            row = grouped.setdefault(name, {
                "TestRow": str(item.get("TestRowName") or test_row),
                "StructureName": name,
                "StructureComments": "",
            })
            parameter = str(item.get("ParameterName") or "").strip()
            if parameter:
                field = by_lower.get(parameter.casefold(), parameter)
                row[field] = "" if item.get("ParameterValue") is None else str(item["ParameterValue"])
                if field not in by_lower.values():
                    by_lower[field.casefold()] = field
                    fields.append(field)
        for field in ("TestRow", "StructureName", "StructureComments"):
            if field not in fields:
                fields.insert(0, field)
        return fields, [{field: row.get(field, "") for field in fields} for row in grouped.values()]

    @staticmethod
    def _csv_fields() -> list[str]:
        try:
            with open(DEFAULT_INPUT, newline="", encoding="utf-8-sig") as stream:
                return [field for field in next(csv.reader(stream), []) if field]
        except OSError:
            return []
