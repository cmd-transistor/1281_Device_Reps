#!/usr/bin/env python3
r"""
ISO Generator GUI  --  1281 Node (Isolation) Buildsheet Translator
================================================================

Self-contained Tkinter front-end for `claude_iso_generator.py`.

Features
--------
* Batch CSV input only (Single-structure tab removed).
* Pre-generation VALIDATION of the input CSV. Enum columns MOS, CELLHEIGHT,
  MG, PFTTYPE, CELLTYPE are validated against DISTINCT values in the BUNDLED
  training decoder CSV; out-of-set values block generation.
* "Open" buttons for the input CSV and the (optional) PadConfig CSV.
* "Show sample input template" button opens a ready-made example decoder CSV.
* Single "Output file (full path)" field (folder + filename merged) with Save As.
  Default output folder = <User Desktop>\MLR_Template  (auto-created if missing).
* Training decoder / Training MLR are FIXED to the bundled defaults (no UI).
* PadConfig CSV is OPTIONAL and BLANK by default (bundled default used if blank).
* Every run is LOGGED locally (username + timestamp + input/output + status
  + rows_generated) to iso_generator_runlog.csv next to the app.
* The USERNAME + rows_generated of each run are also appended to a shared CSV:
      \\amr.corp.intel.com\ec\proj\tmg\LTD\device_infra\Trending\TestRow
  (The normal [log]/[user-log] lines are NOT shown in the GUI; only WARNINGs.)
* Engine fixed to **tuned**; generator bundled and fixed.

Frozen-exe self-dispatch retained (hidden "__generator__" sentinel).
Standard library only (tkinter ships with CPython on Windows/macOS).
"""
from __future__ import annotations

import csv
import getpass
import importlib.util
import os
import queue
import socket
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

APP_TITLE = "ISO Generator  \u2014  1281 Node (Isolation)"
GENERATOR_NAME = "claude_iso_generator.py"
ENGINE = "tuned"
GEN_SENTINEL = "__generator__"

DECODER_COLS = [
    "StructureName", "MOS", "MG", "CELLHEIGHT", "DEVWIDTH",
    "UnitCellSizeOgd", "UnitCellSizePgd", "ArraySizeOgd", "ArraySizePgd",
    "FTIPattern", "PolyPlugs", "TCNPlugs", "VcxPattern", "M0CutPattern",
    "LO1ActiveTracks", "HI1ActiveTracks",
    "VcrPattern", "VcrNubPattern",
]
# LO1ActiveTracks / HI1ActiveTracks are OPTIONAL — the generator falls back
# to VcxPattern-derived values or the training index when absent. They are
# listed in DECODER_COLS so the GUI-generated template includes them, but
# skipped by the "missing required column" check for backward compatibility
# with pre-existing decoder CSVs.
# VcrPattern / VcrNubPattern are OPTIONAL DSL columns; when blank the
# generator defers to the training index (falls back to empty for
# never-trained rows).
OPTIONAL_DECODER_COLS = {
    "LO1ActiveTracks", "HI1ActiveTracks",
    "VcrPattern", "VcrNubPattern",
}
ENUM_COLS = ["MOS", "CELLHEIGHT", "MG"]
DEFAULT_OUTPUT_NAME = "ISO_generated.csv"
OUTPUT_SUBFOLDER = "ISO_Template"
RUN_LOG_NAME = "iso_generator_runlog.csv"
SAMPLE_TEMPLATE_NAME = "ISO_input_template.csv"

# --- Optional GDS .txt generation (claude_iso_gds_generator.py) --------------
GDS_GENERATOR_NAME = "claude_iso_gds_generator.py"
GDS_TRAINING_DECODER = "ISO_Mini_GDS.csv"   # bundled training decoder
GDS_TRAINING_DIR = "ISO-GDSNamed"           # bundled chassis .txt templates
GDS_LAYER_MAP = "svrf_layer_mapping.csv"    # optional layer-name comments
GDS_OUTPUT_SUBFOLDER = "ISO_GDSGen"         # sibling of the buildsheet output

# Shared network location where each run's USERNAME + row count is logged.
NETWORK_USERLOG_DIR = r"\\amr.corp.intel.com\ec\proj\tmg\LTD\device_infra\Trending\TestRow"
NETWORK_USERLOG_NAME = "iso_user_log.csv"

# A few valid example rows (values chosen to match the training decoder set).
# LO1ActiveTracks / HI1ActiveTracks are optional; leaving them blank lets the
# generator derive them from the VcxPattern DSL or the training index.
# VcrPattern / VcrNubPattern are optional DSL columns; leaving them blank
# defers to the training index (empty output for never-trained combos).
SAMPLE_TEMPLATE_ROWS = [
    ["EXAMPLE_N110_A_1", "N", "HVT", "110", "27",
     "24", "4", "5", "10",
     "0:Skip:3:Repeat,All",
     "1:3:Skip:1:Repeat,3/4",
     "All,4/5",
     "vg,1:3:Skip:1:Repeat,6;  vt,0:r0:Skip:3,14;  vt,1:r0:Skip:3,14;  vt,2:r0:Skip:3,14",
     "2:r2:Skip:5,1:3:Skip:1/r3:r1:Skip:1",
     "14", "6",
     "", ""],
    ["EXAMPLE_NP132_B_2", "NP", "LVT", "132", "46",
     "24", "4", "5", "20",
     "0:Skip:3:Repeat,2:5",
     "",
     "All,3/5",
     "vg,1:3:Skip:1:Repeat,9/11;  vt,0:r0:Skip:3,6/14;  vt,1:r0:Skip:3,6/14;  vt,2:r0:Skip:3,6/14",
     "2:r2:Skip:5,1:3:Skip:1/r3:r1:Skip:1",
     "6;14", "9;11",
     "", ""],
    ["EXAMPLE_P165_D_3", "P", "LVT", "165", "62",
     "24", "4", "5", "30",
     "0:Skip:3:Repeat,2:5",
     "",
     "",
     "vg,1:3:Skip:1:Repeat,8-bottom/10/14-bottom/16;  vt,0:r0:Skip:3,7/11/13/17;  vt,1:r0:Skip:3,7/11/13/17;  vt,2:r0:Skip:3,7/11/13/17",
     "2:r2:Skip:5,2:4:Skip:1/r4:r2:Skip:1",
     "7;11;13;17", "8;10;14;16",
     "", ""],
]


def write_sample_template(path: Path) -> None:
    """Write a ready-to-edit sample decoder CSV (header + example rows)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(DECODER_COLS)
        w.writerows(SAMPLE_TEMPLATE_ROWS)


# ---------------------------------------------------------------- path helpers
def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        return Path(base) if base else Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def find_default(name: str) -> str:
    p = resource_dir() / name
    return str(p) if p.exists() else ""


def generator_path() -> Path:
    return resource_dir() / GENERATOR_NAME


def current_username() -> str:
    for key in ("USERNAME", "USER", "LOGNAME"):
        v = os.environ.get(key)
        if v:
            return v
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return "unknown"


def desktop_dir() -> Path:
    """Best-effort path to the current user's Desktop (Windows/macOS/Linux)."""
    for env in ("USERPROFILE", "HOME"):
        base = os.environ.get(env)
        if base:
            candidates = [Path(base) / "Desktop"]
            od = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer")
            if od:
                candidates.insert(0, Path(od) / "Desktop")
            for c in candidates:
                if c.exists():
                    return c
            return Path(base) / "Desktop"
    return Path.home() / "Desktop"


def default_output_dir() -> Path:
    """<Desktop>/MLR_Template  (folder is created on demand at run time)."""
    return desktop_dir() / OUTPUT_SUBFOLDER


def count_output_rows(output_path: str) -> int:
    """Return the number of DATA rows in a generated CSV (excludes header).

    Returns -1 if the file can't be read (so callers can log 'n/a').
    """
    try:
        with open(output_path, newline="", encoding="utf-8-sig") as fh:
            total = sum(1 for _ in csv.reader(fh))
        return max(0, total - 1)
    except Exception:  # noqa: BLE001
        return -1


# ---------------------------------------------------------- validation helpers
def load_allowed_values(training_decoder: str) -> dict[str, set[str]]:
    allowed: dict[str, set[str]] = {}
    try:
        with open(training_decoder, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            fields = reader.fieldnames or []
            present = [c for c in ENUM_COLS if c in fields]
            for c in present:
                allowed[c] = set()
            for row in reader:
                for c in present:
                    v = (row.get(c) or "").strip()
                    if v:
                        allowed[c].add(v)
    except Exception:
        return {}
    return allowed


def validate_input_csv(input_csv: str, allowed: dict[str, set[str]]) -> tuple[bool, list[str]]:
    msgs: list[str] = []
    try:
        with open(input_csv, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            fields = reader.fieldnames or []
            rows = list(reader)
    except Exception as exc:  # noqa: BLE001
        return False, [f"Could not read input CSV: {exc}"]

    missing = [
        c for c in DECODER_COLS
        if c not in fields and c not in OPTIONAL_DECODER_COLS
    ]
    if missing:
        msgs.append("Missing required column(s): " + ", ".join(missing))
    if not rows:
        msgs.append("Input CSV has no data rows.")

    checkable = [c for c in ENUM_COLS if c in fields and c in allowed and allowed[c]]
    for idx, row in enumerate(rows, start=1):
        for c in checkable:
            v = (row.get(c) or "").strip()
            if v == "":
                msgs.append(f"Row {idx}: '{c}' is empty (allowed: {_fmt(allowed[c])}).")
            elif v not in allowed[c]:
                msgs.append(
                    f"Row {idx}: '{c}' = '{v}' is not in the training set "
                    f"(allowed: {_fmt(allowed[c])})."
                )

    unconstrained = [c for c in ENUM_COLS if c in fields and (c not in allowed or not allowed[c])]
    if unconstrained:
        msgs.append(
            "Note: could not constrain " + ", ".join(unconstrained) +
            " (not present/populated in the training decoder CSV) \u2014 values not enum-checked."
        )

    hard_errors = [m for m in msgs if not m.startswith("Note:")]
    return (len(hard_errors) == 0), msgs


def _fmt(values: set[str]) -> str:
    vals = sorted(values, key=lambda s: (len(s), s))
    shown = ", ".join(vals[:12])
    return shown + (" \u2026" if len(vals) > 12 else "")


# ---------------------------------------------------------------- audit/logging
def log_username_network(status: str = "", rows_generated: int | None = None) -> str:
    r"""Append USERNAME (+ timestamp/host/status/rows) to a shared CSV on the
    network share. Best-effort; never raises.

        \\amr.corp.intel.com\ec\proj\tmg\LTD\device_infra\Trending\TestRow
    """
    user = current_username()
    host = socket.gethostname()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows_str = "" if rows_generated is None else (
        "n/a" if rows_generated < 0 else str(rows_generated))
    net = Path(NETWORK_USERLOG_DIR)
    header = ["timestamp", "username", "host", "status", "rows_generated"]
    row = [ts, user, host, status, rows_str]
    try:
        net.mkdir(parents=True, exist_ok=True)
        log = net / NETWORK_USERLOG_NAME
        new = not log.exists()
        with open(log, "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(header)
            w.writerow(row)
        return f"[user-log] Logged user '{user}' to: {log}"
    except Exception as exc:  # noqa: BLE001
        return (f"[user-log] WARNING: could not write username to network "
                f"({NETWORK_USERLOG_DIR}): {exc}")


def audit_run(input_csv: str, output_path: str, status: str,
              extra: str = "", rows_generated: int | None = None) -> list[str]:
    """Record a run to a LOCAL run-history CSV next to the app (best-effort)."""
    lines: list[str] = []
    user = current_username()
    host = socket.gethostname()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows_str = "" if rows_generated is None else (
        "n/a" if rows_generated < 0 else str(rows_generated))

    local_log = app_dir() / RUN_LOG_NAME
    header = ["timestamp", "user", "host", "input_file", "output_file",
              "status", "rows_generated", "notes"]
    row = [ts, user, host, input_csv, output_path, status, rows_str, extra]
    try:
        new = not local_log.exists()
        with open(local_log, "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(header)
            w.writerow(row)
        lines.append(f"[log] Recorded run locally: {local_log}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"[log] WARNING: could not write local run log: {exc}")

    lines.append(log_username_network(status, rows_generated))
    return lines


# ------------------------------------------------------- frozen generator mode
def _run_generator_inproc(argv: list[str]) -> int:
    genfile = generator_path()
    if not genfile.exists():
        sys.stderr.write(f"[ERROR] bundled {GENERATOR_NAME} not found at {genfile}\n")
        return 1
    spec = importlib.util.spec_from_file_location("claude_iso_generator", str(genfile))
    if spec is None or spec.loader is None:
        sys.stderr.write("[ERROR] could not load bundled generator module\n")
        return 1
    mod = importlib.util.module_from_spec(spec)
    sys.modules["claude_iso_generator"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    sys.argv = [GENERATOR_NAME, *argv]
    try:
        mod.main()  # type: ignore[attr-defined]
        return 0
    except SystemExit as exc:
        code = exc.code
        return int(code) if isinstance(code, int) else (0 if code is None else 1)


def _open_in_os(path: str) -> str | None:
    p = Path(path)
    if not path or not p.exists():
        return "File does not exist."
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(p))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])
        return None
    except Exception as exc:  # noqa: BLE001
        return str(exc)


# ============================================================ GUI (import lazy)
def _build_gui_class():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from tkinter.scrolledtext import ScrolledText

    class ISOGeneratorGUI(tk.Tk):
        def __init__(self) -> None:
            super().__init__()
            self.title(APP_TITLE)
            self.geometry("930x740")
            self.minsize(870, 680)
            self._proc: subprocess.Popen | None = None
            self._log_q: "queue.Queue[str]" = queue.Queue()
            self._run_cwd = str(resource_dir())
            self._allowed_cache: dict[str, set[str]] | None = None
            self._phase = "buildsheet"
            self._gds_output_dir = ""
            self._tk = tk
            self._ttk = ttk
            self._fd = filedialog
            self._mb = messagebox
            self._ScrolledText = ScrolledText
            self._build_styles()
            self._build_widgets()
            self._prefill_defaults()
            self.after(100, self._drain_log)

        def _build_styles(self) -> None:
            style = self._ttk.Style(self)
            try:
                style.theme_use("clam")
            except tk.TclError:
                pass
            style.configure("Header.TLabel", font=("Segoe UI", 15, "bold"))
            style.configure("Sub.TLabel", foreground="#555555")
            style.configure("Run.TButton", font=("Segoe UI", 10, "bold"))

        def _build_widgets(self) -> None:
            ttk = self._ttk
            head = ttk.Frame(self, padding=(14, 12, 14, 4))
            head.pack(fill="x")
            ttk.Label(head, text="ISO Generator", style="Header.TLabel").pack(anchor="w")
            ttk.Label(
                head,
                text="Batch CSV translator (1281 node / Isolation).  Engine: tuned.  "
                     "Inputs validated against training; runs are logged.",
                style="Sub.TLabel",
            ).pack(anchor="w")
            self._build_input_area()
            self._build_common_options()
            self._build_actions()
            self._build_log()

        def _open_row(self, parent, label, var, r, browse_title, hint=None):
            ttk = self._ttk
            ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", pady=4)
            ent = ttk.Entry(parent, textvariable=var)
            ent.grid(row=r, column=1, sticky="ew", padx=6, pady=4)
            ttk.Button(parent, text="Browse\u2026",
                       command=lambda: self._pick_open(var, browse_title)).grid(row=r, column=2, pady=4)
            ttk.Button(parent, text="Open",
                       command=lambda: self._open_csv(var)).grid(row=r, column=3, padx=(4, 0), pady=4)
            parent.columnconfigure(1, weight=1)
            if hint:
                ttk.Label(parent, text=hint, style="Sub.TLabel").grid(
                    row=r + 1, column=1, columnspan=3, sticky="w", padx=6)
            return ent

        def _build_input_area(self) -> None:
            tk, ttk = self._tk, self._ttk
            box = ttk.LabelFrame(self, text="Input", padding=10)
            box.pack(fill="x", padx=12, pady=(8, 6))
            self.var_input = tk.StringVar()
            self._open_row(box, "Input decoder CSV *", self.var_input, 0,
                           "Select decoder CSV",
                           hint="Required. Must contain: " + ", ".join(DECODER_COLS))
            btns = ttk.Frame(box)
            btns.grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))
            ttk.Button(btns, text="Show sample input template",
                       command=self._show_sample_template).pack(side="left")
            ttk.Button(btns, text="Validate input against training",
                       command=self._on_validate).pack(side="left", padx=6)
            ttk.Button(btns, text="Show allowed values",
                       command=self._show_allowed).pack(side="left")

        def _build_common_options(self) -> None:
            tk, ttk = self._tk, self._ttk
            box = ttk.LabelFrame(self, text="Output & options", padding=10)
            box.pack(fill="x", padx=12, pady=(0, 6))
            self.var_output = tk.StringVar()
            self.var_padconfig = tk.StringVar()

            # MERGED output path: single field + "Save As..." + Open folder
            ttk.Label(box, text="Output file (full path) *").grid(row=0, column=0, sticky="w", pady=4)
            ttk.Entry(box, textvariable=self.var_output).grid(row=0, column=1, sticky="ew", padx=6, pady=4)
            ttk.Button(box, text="Save As\u2026",
                       command=self._pick_output).grid(row=0, column=2, pady=4)
            ttk.Button(box, text="Open folder",
                       command=self._open_out_folder).grid(row=0, column=3, padx=(4, 0), pady=4)
            box.columnconfigure(1, weight=1)
            ttk.Label(box, style="Sub.TLabel",
                      text=f"Defaults to <Desktop>\\{OUTPUT_SUBFOLDER} (created automatically). "
                           "'.csv' is appended if missing.").grid(
                row=1, column=1, columnspan=3, sticky="w", padx=6)

            # PadConfig: optional, BLANK by default. Browse + Open.
            self._open_row(box, "PadConfig CSV (optional)", self.var_padconfig, 2,
                           "Select PadConfig CSV",
                           hint="Optional \u2014 leave blank to use the bundled default "
                                "(or tick 'Skip PadConfig').")

            opt = ttk.Frame(box)
            opt.grid(row=4, column=0, columnspan=4, sticky="w", pady=(8, 0))
            ttk.Label(opt, text="Engine: tuned (rule-based hybrid)",
                      style="Sub.TLabel").pack(side="left", padx=(0, 18))
            self.var_no_pad = tk.BooleanVar(value=False)
            self.var_no_val = tk.BooleanVar(value=False)
            ttk.Checkbutton(opt, text="Skip PadConfig", variable=self.var_no_pad).pack(side="left", padx=6)
            ttk.Checkbutton(opt, text="Skip engine validation", variable=self.var_no_val).pack(side="left", padx=6)

            self.var_gen_gds = tk.BooleanVar(value=False)
            ttk.Checkbutton(opt, text="Also generate GDS .txt files",
                            variable=self.var_gen_gds).pack(side="left", padx=6)
            ttk.Label(box, style="Sub.TLabel",
                      text=f"GDS .txt files are written to a '{GDS_OUTPUT_SUBFOLDER}' folder "
                           "beside the buildsheet output (after the buildsheet succeeds).").grid(
                row=5, column=1, columnspan=3, sticky="w", padx=6, pady=(4, 0))

        def _build_actions(self) -> None:
            ttk = self._ttk
            bar = ttk.Frame(self, padding=(12, 0))
            bar.pack(fill="x")
            self.btn_run = ttk.Button(bar, text="\u25B6  Generate", style="Run.TButton",
                                      command=self._on_run)
            self.btn_run.pack(side="left")
            self.btn_stop = ttk.Button(bar, text="Stop", command=self._on_stop, state="disabled")
            self.btn_stop.pack(side="left", padx=6)
            ttk.Button(bar, text="Open output folder", command=self._open_out_folder).pack(side="left", padx=6)
            ttk.Button(bar, text="Preview output", command=self._preview_output).pack(side="left", padx=6)
            ttk.Button(bar, text="Clear log", command=lambda: self.log.delete("1.0", "end")).pack(side="left", padx=6)
            self.progress = ttk.Progressbar(bar, mode="indeterminate", length=160)
            self.progress.pack(side="right")

        def _build_log(self) -> None:
            tk, ttk = self._tk, self._ttk
            frame = ttk.LabelFrame(self, text="Log", padding=6)
            frame.pack(fill="both", expand=True, padx=12, pady=(6, 10))
            self.log = self._ScrolledText(frame, height=12, wrap="word",
                                          font=("Consolas", 9), background="#101418",
                                          foreground="#d6e2ea", insertbackground="#d6e2ea")
            self.log.pack(fill="both", expand=True)
            self.log.tag_config("err", foreground="#ff8a80")
            self.log.tag_config("ok", foreground="#9ccc65")
            self.log.tag_config("warn", foreground="#ffd479")
            self.log.tag_config("audit", foreground="#82b1ff")
            self.status = tk.StringVar(value=f"Ready.  User: {current_username()}")
            ttk.Label(self, textvariable=self.status, style="Sub.TLabel",
                      anchor="w", padding=(14, 0, 14, 8)).pack(fill="x")

        # ------------------------------------------------------------ defaults
        def _prefill_defaults(self) -> None:
            self.var_output.set(str(default_output_dir() / DEFAULT_OUTPUT_NAME))
            self.var_padconfig.set("")

        # -------------------------------------------------------- allowed enums
        def _invalidate_allowed(self) -> None:
            self._allowed_cache = None

        def _get_allowed(self) -> dict[str, set[str]]:
            if self._allowed_cache is None:
                td = find_default("MLR_Decoder.csv")
                self._allowed_cache = load_allowed_values(td) if td else {}
            return self._allowed_cache

        def _show_sample_template(self) -> None:
            dest = default_output_dir() / SAMPLE_TEMPLATE_NAME
            try:
                write_sample_template(dest)
            except Exception as exc:  # noqa: BLE001
                self._mb.showerror(APP_TITLE, f"Could not write sample template:\n{exc}")
                return
            self._append_log("\n--- Sample input template ---\n", "audit")
            self._append_log(",".join(DECODER_COLS) + "\n")
            for r in SAMPLE_TEMPLATE_ROWS:
                self._append_log(",".join(r) + "\n")
            self._append_log(f"[i] Saved sample template to: {dest}\n", "audit")
            self.status.set(f"Sample template saved: {dest}")
            _open_in_os(str(dest))
            if self._mb.askyesno(
                APP_TITLE,
                "A sample input template was created and opened:\n\n"
                f"{dest}\n\nUse it as the current input decoder file now?",
            ):
                self.var_input.set(str(dest))

        def _show_allowed(self) -> None:
            allowed = self._get_allowed()
            if not allowed:
                self._mb.showwarning(APP_TITLE, "Could not read allowed values from the "
                                                "bundled training decoder CSV.")
                return
            lines = ["Allowed values (from bundled training decoder CSV):\n"]
            for c in ENUM_COLS:
                if c in allowed and allowed[c]:
                    lines.append(f"  {c}: {_fmt(allowed[c])}")
                else:
                    lines.append(f"  {c}: (not constrained \u2014 absent in training file)")
            self._append_log("\n".join(lines) + "\n")
            self.status.set("Listed allowed values in the log.")

        # ------------------------------------------------------------- pickers
        def _pick_open(self, var, title, types=None):
            types = types or [("CSV files", "*.csv"), ("All files", "*.*")]
            p = self._fd.askopenfilename(title=title, filetypes=types, initialdir=self._initdir(var))
            if p:
                var.set(p)

        def _pick_output(self):
            init = self.var_output.get().strip()
            initdir = str(Path(init).parent) if init else str(default_output_dir())
            initfile = Path(init).name if init else DEFAULT_OUTPUT_NAME
            try:
                Path(initdir).mkdir(parents=True, exist_ok=True)
            except Exception:  # noqa: BLE001
                pass
            p = self._fd.asksaveasfilename(
                title="Save generated MLR CSV as", defaultextension=".csv",
                filetypes=[("CSV files", "*.csv")], initialdir=initdir, initialfile=initfile)
            if p:
                self.var_output.set(p)

        def _open_csv(self, var) -> None:
            err = _open_in_os(var.get().strip())
            if err:
                self._mb.showwarning(APP_TITLE, f"Cannot open file:\n{err}")

        def _initdir(self, var):
            cur = var.get().strip()
            if cur:
                d = Path(cur).parent if Path(cur).suffix else Path(cur)
                if d.exists():
                    return str(d)
            return str(app_dir())

        # ------------------------------------------------------------ validate
        def _on_validate(self) -> bool:
            inp = self.var_input.get().strip()
            if not inp or not Path(inp).exists():
                self._mb.showerror(APP_TITLE, "Select a valid input decoder CSV first.")
                return False
            allowed = self._get_allowed()
            if not allowed:
                self._append_log("[!] Could not load allowed values from bundled training "
                                 "decoder; enum checks skipped.\n", "warn")
            ok, msgs = validate_input_csv(inp, allowed)
            self._append_log("\n--- Input validation ---\n")
            if not msgs:
                self._append_log("\u2714 All rows valid.\n", "ok")
            else:
                for m in msgs:
                    self._append_log(("  " + m + "\n"),
                                     "warn" if m.startswith("Note:") else "err")
            if ok:
                self._append_log("\u2714 Validation passed.\n", "ok")
                self.status.set("Validation passed.")
            else:
                self._append_log("\u2716 Validation failed \u2014 fix the issues above.\n", "err")
                self.status.set("Validation failed. See log.")
            return ok

        # --------------------------------------------------------------- run
        def _full_output_path(self) -> str:
            out = self.var_output.get().strip()
            if not out:
                return ""
            if not out.lower().endswith(".csv"):
                out += ".csv"
            return out

        def _validate_before_run(self) -> str | None:
            if not generator_path().exists():
                return (f"Bundled '{GENERATOR_NAME}' not found. If running as a script, "
                        f"keep it beside mlr_gui.py.")
            if not self._full_output_path():
                return "Please choose an output file (full path)."
            inp = self.var_input.get().strip()
            if not inp or not Path(inp).exists():
                return "Please select a valid input decoder CSV."
            return None

        def _build_cmd(self) -> list[str]:
            if getattr(sys, "frozen", False):
                base = [sys.executable, GEN_SENTINEL]
            else:
                base = [sys.executable, str(generator_path())]
            inp = self.var_input.get().strip()
            out = self._full_output_path()
            cmd = [*base, "--engine", ENGINE, "--input", inp, "--output", out]
            td = find_default("MLR_Decoder.csv")
            tm = find_default("MLR_Mini.csv")
            if td:
                cmd += ["--training-decoder", td]
            if tm:
                cmd += ["--training-mlr", tm]
            pc = self.var_padconfig.get().strip() or find_default("MLR28_PadConfig.csv")
            if self.var_no_pad.get():
                cmd.append("--no-padconfig")
            elif pc:
                cmd += ["--padconfig", pc]
            if self.var_no_val.get():
                cmd.append("--no-validate")
            return cmd

        def _on_run(self) -> None:
            if self._proc is not None:
                self._mb.showinfo(APP_TITLE, "A run is already in progress.")
                return
            err = self._validate_before_run()
            if err:
                self._mb.showerror(APP_TITLE, err)
                return

            if not self._on_validate():
                # Record blocked attempt (local + network). Show only WARNINGs.
                for ln in audit_run(self.var_input.get().strip(),
                                    self._full_output_path(),
                                    status="blocked-validation",
                                    rows_generated=0):
                    if "WARNING" in ln:
                        self._append_log(ln + "\n", "warn")
                self._mb.showerror(
                    APP_TITLE,
                    "Input validation failed. The buildsheet was NOT generated.\n\n"
                    "Fix the flagged column values (they must match the training "
                    "decoder CSV) and try again.")
                return

            out = self._full_output_path()
            try:
                Path(out).parent.mkdir(parents=True, exist_ok=True)
            except Exception as exc:  # noqa: BLE001
                self._mb.showerror(APP_TITLE, f"Could not create output folder:\n{exc}")
                return

            cmd = self._build_cmd()
            shown = cmd[:]
            if getattr(sys, "frozen", False):
                shown[0] = Path(sys.executable).name
            self._append_log(f"\n[user] {current_username()} @ {socket.gethostname()}\n", "audit")
            self._append_log("$ " + " ".join(_quote(c) for c in shown) + "\n")
            self.status.set("Running\u2026")
            self._phase = "buildsheet"
            self.btn_run.config(state="disabled")
            self.btn_stop.config(state="normal")
            self.progress.start(12)
            t = threading.Thread(target=self._worker, args=(cmd,), daemon=True)
            t.start()

        def _worker(self, cmd) -> None:
            try:
                self._proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, cwd=self._run_cwd,
                )
                assert self._proc.stdout is not None
                for line in self._proc.stdout:
                    self._log_q.put(line)
                self._proc.wait()
                self._log_q.put(f"__DONE__{self._proc.returncode}")
            except Exception as exc:  # noqa: BLE001
                self._log_q.put(f"[ERROR] {exc}\n")
                self._log_q.put("__DONE__1")
            finally:
                self._proc = None

        def _on_stop(self) -> None:
            if self._proc is not None:
                try:
                    self._proc.terminate()
                    self._append_log("[i] Stop requested.\n")
                except Exception:  # noqa: BLE001
                    pass

        def _drain_log(self) -> None:
            try:
                while True:
                    item = self._log_q.get_nowait()
                    if item.startswith("__DONE__"):
                        rc = int(item.replace("__DONE__", "") or "1")
                        if self._phase == "gds":
                            self._finish_gds(rc)
                        else:
                            self._finish(rc)
                    else:
                        tag = "err" if ("ERROR" in item or "WARNING" in item) else None
                        self._append_log(item, tag)
            except queue.Empty:
                pass
            self.after(100, self._drain_log)

        def _append_log(self, text, tag=None) -> None:
            self.log.insert("end", text, tag or ())
            self.log.see("end")

        def _finish(self, rc) -> None:
            self.progress.stop()
            status = "success" if rc == 0 else f"failed-exit-{rc}"
            rows = count_output_rows(self._full_output_path()) if rc == 0 else 0
            if rc == 0:
                self._append_log("\u2714 Done. Output written successfully.\n", "ok")
                self._append_log(f"[i] Rows generated: {rows}\n", "audit")
                self.status.set(f"Success \u2014 {rows} rows \u2014 {self._full_output_path()}")
            else:
                self._append_log(f"\u2716 Generator exited with code {rc}.\n", "err")
                self.status.set(f"Failed (exit {rc}). See log.")
            # Record run (local + network). Show only WARNINGs in the GUI.
            for ln in audit_run(self.var_input.get().strip(),
                                self._full_output_path(), status=status,
                                rows_generated=rows):
                if "WARNING" in ln:
                    self._append_log(ln + "\n", "warn")
            # Chain to GDS .txt generation if requested and buildsheet succeeded.
            if rc == 0 and self.var_gen_gds.get():
                self._start_gds()
                return
            self.btn_run.config(state="normal")
            self.btn_stop.config(state="disabled")

        # --------------------------------------------------------- GDS .txt gen
        def _gds_generator_path(self) -> Path:
            return resource_dir() / GDS_GENERATOR_NAME

        def _gds_out_folder(self) -> Path:
            out = self._full_output_path()
            parent = Path(out).parent if out else default_output_dir()
            return parent / GDS_OUTPUT_SUBFOLDER

        def _gds_preflight(self) -> str | None:
            if getattr(sys, "frozen", False):
                return "GDS generation is only available when running from source (.py)."
            if not self._gds_generator_path().exists():
                return f"bundled '{GDS_GENERATOR_NAME}' not found"
            if not find_default(GDS_TRAINING_DECODER):
                return f"training decoder '{GDS_TRAINING_DECODER}' not found"
            if not (resource_dir() / GDS_TRAINING_DIR).exists():
                return f"training GDS folder '{GDS_TRAINING_DIR}' not found"
            return None

        def _build_gds_cmd(self) -> list[str]:
            cmd = [
                sys.executable, str(self._gds_generator_path()),
                "--training-decoder", find_default(GDS_TRAINING_DECODER),
                "--decoder", self.var_input.get().strip(),
                "--buildsheet", self._full_output_path(),
                "--training-dir", str(resource_dir() / GDS_TRAINING_DIR),
                "--output-dir", str(self._gds_out_folder()),
            ]
            lm = find_default(GDS_LAYER_MAP)
            if lm:
                cmd += ["--layer-map", lm]
            return cmd

        def _start_gds(self) -> None:
            err = self._gds_preflight()
            if err:
                self._append_log(f"[GDS] SKIPPED: {err}\n", "warn")
                self.btn_run.config(state="normal")
                self.btn_stop.config(state="disabled")
                return
            self._gds_output_dir = str(self._gds_out_folder())
            try:
                Path(self._gds_output_dir).mkdir(parents=True, exist_ok=True)
            except Exception as exc:  # noqa: BLE001
                self._append_log(f"[GDS] SKIPPED: could not create output folder: {exc}\n", "warn")
                self.btn_run.config(state="normal")
                self.btn_stop.config(state="disabled")
                return
            cmd = self._build_gds_cmd()
            self._phase = "gds"
            self._append_log("\n[GDS] Generating GDS .txt files\u2026\n", "audit")
            self._append_log("$ " + " ".join(_quote(c) for c in cmd) + "\n")
            self.status.set("Generating GDS .txt\u2026")
            self.btn_run.config(state="disabled")
            self.btn_stop.config(state="normal")
            self.progress.start(12)
            t = threading.Thread(target=self._worker, args=(cmd,), daemon=True)
            t.start()

        def _finish_gds(self, rc) -> None:
            self.progress.stop()
            self.btn_run.config(state="normal")
            self.btn_stop.config(state="disabled")
            self._phase = "buildsheet"
            out_dir = self._gds_output_dir
            n_txt = 0
            if out_dir and Path(out_dir).exists():
                n_txt = sum(1 for _ in Path(out_dir).glob("*.txt"))
            status = "gds-success" if rc == 0 else f"gds-failed-exit-{rc}"
            if rc == 0:
                self._append_log(
                    f"\u2714 GDS generation done. {n_txt} .txt file(s) written to {out_dir}\n", "ok")
                self.status.set(f"GDS success \u2014 {n_txt} .txt files \u2014 {out_dir}")
            else:
                self._append_log(f"\u2716 GDS generator exited with code {rc}.\n", "err")
                self.status.set(f"GDS failed (exit {rc}). See log.")
            # Record GDS run (local + network). Show only WARNINGs in the GUI.
            for ln in audit_run(self.var_input.get().strip(), out_dir,
                                status=status, extra="gds_txt", rows_generated=n_txt):
                if "WARNING" in ln:
                    self._append_log(ln + "\n", "warn")

        # ----------------------------------------------------------- helpers
        def _open_out_folder(self) -> None:
            out = self._full_output_path()
            folder = Path(out).parent if out else default_output_dir()
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except Exception:  # noqa: BLE001
                pass
            if not folder.exists():
                self._mb.showwarning(APP_TITLE, "Output folder does not exist yet.")
                return
            err = _open_in_os(str(folder))
            if err:
                self._mb.showerror(APP_TITLE, f"Could not open folder:\n{err}")

        def _preview_output(self) -> None:
            out = self._full_output_path()
            if not out or not Path(out).exists():
                self._mb.showinfo(APP_TITLE, "No output file to preview yet. Run first.")
                return
            top = self._tk.Toplevel(self)
            top.title(f"Preview \u2014 {Path(out).name}")
            top.geometry("900x420")
            txt = self._ScrolledText(top, wrap="none", font=("Consolas", 9))
            txt.pack(fill="both", expand=True)
            try:
                with open(out, encoding="utf-8") as fh:
                    for i, line in enumerate(fh):
                        if i > 40:
                            txt.insert("end", "\u2026 (truncated; open the file for full content)\n")
                            break
                        txt.insert("end", line)
            except Exception as exc:  # noqa: BLE001
                txt.insert("end", f"Could not read file: {exc}")

    return ISOGeneratorGUI


def _quote(s: str) -> str:
    return f'"{s}"' if " " in s else s


def _run_gui() -> None:
    gui_cls = _build_gui_class()
    app = gui_cls()
    app.mainloop()


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == GEN_SENTINEL:
        sys.exit(_run_generator_inproc(argv[1:]))
    _run_gui()


if __name__ == "__main__":
    main()
