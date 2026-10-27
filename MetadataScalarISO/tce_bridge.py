"""Loads the TCE template service (tce-mcp-server) for the structure generator GUI and HTTP API.

The `tce_mcp` package is imported from the sibling ``tce-mcp-server/src`` checkout unless it is
already installed. Override with ``TCE_MCP_SRC``; the data bundle comes from ``TCE_DATA_DIR`` and
written buildsheets go to ``TCE_OUTPUT_DIR`` (same variables as the MCP server).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_SRC = HERE.parent / "tce-mcp-server" / "src"
DEFAULT_DATA_DIR = "//samba.pdx.intel.com/nfs/site/disks/tce_tools/tcc/tce.1281/data/x81a"


def _ensure_importable() -> None:
    try:
        import tce_mcp  # noqa: F401
        return
    except ImportError:
        pass
    src = Path(os.environ.get("TCE_MCP_SRC", DEFAULT_SRC))
    if not (src / "tce_mcp").is_dir():
        raise ImportError(f"tce_mcp package not found; set TCE_MCP_SRC to the tce-mcp-server/src folder (tried {src})")
    sys.path.insert(0, str(src))


_ensure_importable()

from tce_mcp import buildsheet  # noqa: E402
from tce_mcp.config import Config  # noqa: E402
from tce_mcp.service import TceService, issues_to_dicts  # noqa: E402
from tce_mcp.templates import Param, Template  # noqa: E402

__all__ = ["Config", "Param", "Template", "TceService", "buildsheet", "default_config", "issues_to_dicts",
           "make_service"]


def default_config() -> Config:
    data = os.environ.get("TCE_DATA_DIR") or DEFAULT_DATA_DIR
    out = os.environ.get("TCE_OUTPUT_DIR") or str(Path.home() / "tce-buildsheets")
    return Config(data_dir=Path(data).resolve(), output_dir=Path(out).resolve())


def make_service(data_dir: str | Path | None = None, output_dir: str | Path | None = None) -> TceService:
    cfg = default_config()
    return TceService(Config(
        data_dir=Path(data_dir).resolve() if data_dir else cfg.data_dir,
        output_dir=Path(output_dir).resolve() if output_dir else cfg.output_dir,
    ))
