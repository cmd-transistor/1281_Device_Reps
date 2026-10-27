"""TCE templates workbench: builds buildsheets for every TCE pcell template inside the structure generator GUI.

Exposes every tce-mcp-server feature (template catalog, column editor with defaults/legal values/help,
finalized example structures as a base, sweeps, documentation search, parameter search, buildsheet
reading/validation and .xlsx/.csv export) through `tce_bridge.TceService`.
"""

from __future__ import annotations

import json
import queue
import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from tce_bridge import TceService, Template, buildsheet, default_config, make_service

LEADING = buildsheet.LEADING_COLUMNS
PLACEHOLDER = re.compile(r"\{(\w+)\}")
BLANK_TOKEN = "<blank>"
ORIGIN_COLORS = {"row": "#0b4f9c", "example": "#2e7d32", "default": "#6b7280"}


def _split_list(text: str) -> list[str]:
    return [t for t in re.split(r"[,;\s]+", text.strip()) if t]


def _tree(parent, columns: list[tuple[str, str, int, bool]], selectmode: str = "browse", height: int = 10):
    """Treeview with scrollbars in a frame; columns are (key, heading, width, stretch)."""
    frame = ttk.Frame(parent)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(0, weight=1)
    tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", selectmode=selectmode,
                        height=height)
    for key, label, width, stretch in columns:
        tree.heading(key, text=label)
        tree.column(key, width=width, minwidth=40, stretch=stretch)
    ys = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    xs = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
    tree.grid(row=0, column=0, sticky="nsew")
    ys.grid(row=0, column=1, sticky="ns")
    xs.grid(row=1, column=0, sticky="ew")
    return frame, tree


def _clear(tree: ttk.Treeview) -> None:
    tree.delete(*tree.get_children())


class TceWorkbench(ttk.Frame):
    def __init__(self, master) -> None:
        super().__init__(master)
        cfg = default_config()
        self.svc: TceService | None = None
        self.templates: list[Template] = []
        self.tpl: Template | None = None
        self.example: dict | None = None
        self.rows: list[dict] = []
        self.sweeps: dict[str, list[str]] = {}
        self.columns: list[str] = []
        self.params: dict = {}
        self.base: dict = {}
        self.extras_eff: list[str] = []
        self.pins_eff: list[str] | None = None
        self.example_hits: list[dict] = []
        self.doc_hits: dict[str, tuple[str, int]] = {}
        self.import_path: Path | None = None
        self._results: "queue.Queue[tuple]" = queue.Queue()

        self.data_var = tk.StringVar(value=cfg.data_dir.as_posix())
        self.out_var = tk.StringVar(value=cfg.output_dir.as_posix())
        self.status = tk.StringVar(value="Press Load to read the TCE template bundle")
        self.pins_var = tk.StringVar()
        self.extras_var = tk.StringVar()
        self.format_var = tk.StringVar(value="xlsx")
        self.base_label = tk.StringVar(value="Base: pcell defaults")
        self.template_label = tk.StringVar(value="No template selected")
        self.edit_column = tk.StringVar()
        self.edit_value = tk.StringVar()
        self.column_filter = tk.StringVar()
        self.only_set = tk.BooleanVar(value=False)

        self._build()
        self._poll_id = self.after(100, self._poll)

    # -- background work -------------------------------------------------------
    def _task(self, fn, done, message: str) -> None:
        self.status.set(message)

        def run() -> None:
            try:
                self._results.put((done, fn(), None))
            except Exception as exc:  # reported in the UI
                self._results.put((done, None, exc))

        threading.Thread(target=run, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                done, value, error = self._results.get_nowait()
                if error is not None:
                    self.status.set(f"Error: {error}")
                    messagebox.showerror("TCE templates", str(error), parent=self)
                else:
                    done(value)
        except queue.Empty:
            pass
        try:
            self._poll_id = self.after(100, self._poll)
        except tk.TclError:
            pass

    def _guard(self, fn, *args):
        try:
            return fn(*args)
        except (ValueError, FileNotFoundError, KeyError) as exc:
            messagebox.showerror("TCE templates", str(exc), parent=self)
            return None

    # -- layout ----------------------------------------------------------------
    def _build(self) -> None:
        top = ttk.Frame(self, padding=(8, 6))
        top.pack(fill="x")
        ttk.Label(top, text="Data dir").pack(side="left")
        ttk.Entry(top, textvariable=self.data_var, width=60).pack(side="left", padx=4)
        ttk.Button(top, text="...", width=3, command=lambda: self._browse_dir(self.data_var)).pack(side="left")
        ttk.Label(top, text="Output dir").pack(side="left", padx=(12, 0))
        ttk.Entry(top, textvariable=self.out_var, width=40).pack(side="left", padx=4)
        ttk.Button(top, text="...", width=3, command=lambda: self._browse_dir(self.out_var)).pack(side="left")
        ttk.Button(top, text="Load", command=self._load).pack(side="left", padx=(8, 0))
        ttk.Label(top, textvariable=self.status, foreground="#4a5560").pack(side="right")

        split = ttk.PanedWindow(self, orient="horizontal")
        split.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        split.add(self._build_catalog(split), weight=0)
        self.tabs = ttk.Notebook(split)
        split.add(self.tabs, weight=1)
        self.build_tab = self._build_build_tab(self.tabs)
        self.tabs.add(self.build_tab, text="Build")
        self.tabs.add(self._build_sweep_tab(self.tabs), text="Sweep")
        self.examples_tab = self._build_examples_tab(self.tabs)
        self.tabs.add(self.examples_tab, text="Finalized examples")
        self.docs_tab = self._build_docs_tab(self.tabs)
        self.tabs.add(self.docs_tab, text="Docs")
        self.tabs.add(self._build_params_tab(self.tabs), text="Parameter search")
        self.tabs.add(self._build_import_tab(self.tabs), text="Validate / import")

    def _build_catalog(self, parent) -> ttk.Frame:
        frame = ttk.Frame(parent, width=380)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        bar = ttk.Frame(frame)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(bar, text="Templates").pack(side="left")
        self.category_var = tk.StringVar(value="All")
        self.category_combo = ttk.Combobox(bar, textvariable=self.category_var, state="readonly", width=12,
                                           values=("All",))
        self.category_combo.pack(side="right")
        self.category_combo.bind("<<ComboboxSelected>>", lambda _e: self._fill_catalog())
        self.catalog_filter = tk.StringVar()
        entry = ttk.Entry(frame, textvariable=self.catalog_filter)
        entry.grid(row=1, column=0, sticky="ew", pady=(0, 4))
        self.catalog_filter.trace_add("write", lambda *_a: self._fill_catalog())
        box, self.catalog = _tree(frame, [("id", "Template", 170, True), ("category", "Category", 70, False),
                                          ("cell", "Cell", 110, False), ("n", "Params", 55, False)])
        box.grid(row=2, column=0, sticky="nsew")
        self.catalog.bind("<<TreeviewSelect>>", lambda _e: self._catalog_selected())
        self.catalog_info = tk.Text(frame, height=6, wrap="word", font=("Segoe UI", 9), state="disabled",
                                    background="#f6f7f9", relief="flat")
        self.catalog_info.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        return frame

    def _build_build_tab(self, parent) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=6)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(2, weight=1)
        head = ttk.Frame(tab)
        head.grid(row=0, column=0, sticky="ew")
        ttk.Label(head, textvariable=self.template_label, font=("Segoe UI", 11, "bold")).pack(side="left")
        ttk.Button(head, text="Clear base", command=self._clear_example).pack(side="right")
        ttk.Label(head, textvariable=self.base_label, foreground=ORIGIN_COLORS["example"]).pack(side="right", padx=8)

        opts = ttk.Frame(tab)
        opts.grid(row=1, column=0, sticky="ew", pady=4)
        ttk.Label(opts, text="Pins (blank = template/example pins)").pack(side="left")
        pins = ttk.Entry(opts, textvariable=self.pins_var, width=40)
        pins.pack(side="left", padx=4)
        ttk.Label(opts, text="Extra tool columns").pack(side="left", padx=(10, 0))
        extras = ttk.Entry(opts, textvariable=self.extras_var, width=40)
        extras.pack(side="left", padx=4)
        for widget in (pins, extras):
            widget.bind("<Return>", lambda _e: self._relayout())
            widget.bind("<FocusOut>", lambda _e: self._relayout())

        body = ttk.PanedWindow(tab, orient="horizontal")
        body.grid(row=2, column=0, sticky="nsew")

        rows_panel = ttk.Frame(body)
        rows_panel.columnconfigure(0, weight=1)
        rows_panel.rowconfigure(0, weight=1)
        box, self.row_tree = _tree(rows_panel, [("n", "#", 40, False), ("name", "StructureName", 260, True),
                                                ("set", "Set", 45, False)], selectmode="extended")
        box.grid(row=0, column=0, sticky="nsew")
        self.row_tree.bind("<<TreeviewSelect>>", lambda _e: self._fill_columns())
        btns = ttk.Frame(rows_panel)
        btns.grid(row=1, column=0, sticky="ew", pady=4)
        for text, cmd in (("Add row", self._add_row), ("Duplicate", self._dup_rows),
                          ("Delete", self._delete_rows), ("Clear all", self._clear_rows)):
            ttk.Button(btns, text=text, command=cmd).pack(side="left", padx=(0, 4))
        body.add(rows_panel, weight=1)

        cols_panel = ttk.Frame(body, padding=(8, 0, 0, 0))
        cols_panel.columnconfigure(0, weight=1)
        cols_panel.rowconfigure(1, weight=1)
        bar = ttk.Frame(cols_panel)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(bar, text="Filter columns").pack(side="left")
        ttk.Entry(bar, textvariable=self.column_filter, width=24).pack(side="left", padx=4)
        ttk.Checkbutton(bar, text="Only non-default", variable=self.only_set,
                        command=self._fill_columns).pack(side="left", padx=6)
        legend = ttk.Frame(bar)
        legend.pack(side="right")
        for origin, label in (("row", "set on row"), ("example", "from example"), ("default", "pcell default")):
            ttk.Label(legend, text=label, foreground=ORIGIN_COLORS[origin]).pack(side="left", padx=4)
        self.column_filter.trace_add("write", lambda *_a: self._fill_columns())
        box, self.col_tree = _tree(cols_panel, [("column", "Column", 190, False), ("value", "Value", 330, True),
                                                ("origin", "From", 70, False), ("default", "Default", 200, True),
                                                ("type", "Type", 55, False)])
        box.grid(row=1, column=0, sticky="nsew")
        for origin, color in ORIGIN_COLORS.items():
            self.col_tree.tag_configure(origin, foreground=color)
        self.col_tree.bind("<<TreeviewSelect>>", lambda _e: self._column_selected())
        self.col_tree.bind("<Double-1>", lambda _e: self.value_combo.focus_set())

        edit = ttk.Frame(cols_panel)
        edit.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        edit.columnconfigure(1, weight=1)
        ttk.Label(edit, textvariable=self.edit_column, width=24, font=("Segoe UI", 10, "bold")).grid(row=0, column=0)
        self.value_combo = ttk.Combobox(edit, textvariable=self.edit_value)
        self.value_combo.grid(row=0, column=1, sticky="ew", padx=4)
        self.value_combo.bind("<Return>", lambda _e: self._set_value())
        for col, (text, cmd) in enumerate((("Set", self._set_value), ("Use default", self._unset_value),
                                           ("Blank", self._blank_value), ("Docs", self._docs_for_column)), start=2):
            ttk.Button(edit, text=text, command=cmd).grid(row=0, column=col, padx=(0, 4))
        self.help_text = tk.Text(cols_panel, height=4, wrap="word", font=("Segoe UI", 9), state="disabled",
                                 background="#f6f7f9", relief="flat")
        self.help_text.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        body.add(cols_panel, weight=3)

        foot = ttk.Frame(tab)
        foot.grid(row=3, column=0, sticky="ew", pady=(6, 2))
        ttk.Button(foot, text="Check", command=self._check).pack(side="left")
        ttk.Label(foot, text="Format").pack(side="left", padx=(12, 2))
        ttk.Combobox(foot, textvariable=self.format_var, values=("xlsx", "csv"), state="readonly",
                     width=6).pack(side="left")
        ttk.Button(foot, text="Save to output dir", command=lambda: self._save(False)).pack(side="left", padx=(8, 0))
        ttk.Button(foot, text="Save as...", command=lambda: self._save(True)).pack(side="left", padx=(4, 0))
        box, self.issue_tree = _tree(tab, [("level", "Level", 70, False), ("row", "Row", 50, False),
                                           ("column", "Column", 170, False), ("message", "Message", 700, True)],
                                     height=5)
        box.grid(row=4, column=0, sticky="ew")
        self.issue_tree.tag_configure("error", foreground="#b00020")
        self.issue_tree.tag_configure("warning", foreground="#a15c00")
        self.issue_tree.bind("<Double-1>", lambda _e: self._goto_issue())
        return tab

    def _build_sweep_tab(self, parent) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=6)
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(1, weight=1)
        ttk.Label(tab, text="Sweep columns over the selected Build row (or an empty row). Generated rows are added "
                            "to the Build tab for review before saving.", foreground="#4a5560").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        box, self.sweep_tree = _tree(tab, [("column", "Column", 180, False), ("count", "Values", 60, False),
                                           ("values", "Values (one row each)", 420, True)])
        box.grid(row=1, column=0, sticky="nsew")
        self.sweep_tree.bind("<<TreeviewSelect>>", lambda _e: self._sweep_selected())

        editor = ttk.Frame(tab, padding=(8, 0, 0, 0))
        editor.grid(row=1, column=1, sticky="nsew")
        editor.columnconfigure(0, weight=1)
        editor.rowconfigure(3, weight=1)
        ttk.Label(editor, text="Column").grid(row=0, column=0, sticky="w")
        self.sweep_column = tk.StringVar()
        self.sweep_column_combo = ttk.Combobox(editor, textvariable=self.sweep_column)
        self.sweep_column_combo.grid(row=1, column=0, sticky="ew")
        ttk.Label(editor, text=f"Values, one per line ({BLANK_TOKEN} for an empty value)").grid(
            row=2, column=0, sticky="w", pady=(6, 0))
        self.sweep_values = tk.Text(editor, height=10, wrap="none", font=("Consolas", 10))
        self.sweep_values.grid(row=3, column=0, sticky="nsew")
        btns = ttk.Frame(editor)
        btns.grid(row=4, column=0, sticky="ew", pady=4)
        ttk.Button(btns, text="Add / update", command=self._sweep_put).pack(side="left")
        ttk.Button(btns, text="Remove", command=self._sweep_remove).pack(side="left", padx=4)
        ttk.Button(btns, text="Clear", command=self._sweep_clear).pack(side="left")

        opts = ttk.Frame(tab)
        opts.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.sweep_mode = tk.StringVar(value="product")
        ttk.Radiobutton(opts, text="Cartesian product", value="product", variable=self.sweep_mode).pack(side="left")
        ttk.Radiobutton(opts, text="Zip (paired lists)", value="zip", variable=self.sweep_mode).pack(side="left",
                                                                                                   padx=6)
        ttk.Label(opts, text="Name template").pack(side="left", padx=(12, 2))
        self.sweep_name = tk.StringVar(value="{StructureName}_{i}")
        ttk.Entry(opts, textvariable=self.sweep_name, width=40).pack(side="left")
        self.sweep_replace = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Replace the base row", variable=self.sweep_replace).pack(side="left", padx=8)
        ttk.Button(opts, text="Generate rows", command=self._sweep_generate).pack(side="left")
        return tab

    def _build_examples_tab(self, parent) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=6)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        self.example_tabs = ttk.Notebook(tab)
        self.example_tabs.grid(row=0, column=0, sticky="nsew")

        structs = ttk.Frame(self.example_tabs, padding=6)
        structs.columnconfigure(0, weight=1)
        structs.rowconfigure(1, weight=1)
        bar = ttk.Frame(structs)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.example_query = tk.StringVar()
        self.example_row = tk.StringVar()
        self.example_current_only = tk.BooleanVar(value=True)
        ttk.Label(bar, text="Search (e.g. ch132 lvt z5)").pack(side="left")
        q = ttk.Entry(bar, textvariable=self.example_query, width=34)
        q.pack(side="left", padx=4)
        q.bind("<Return>", lambda _e: self._example_search())
        ttk.Label(bar, text="Test row").pack(side="left", padx=(8, 2))
        r = ttk.Entry(bar, textvariable=self.example_row, width=20)
        r.pack(side="left")
        r.bind("<Return>", lambda _e: self._example_search())
        ttk.Checkbutton(bar, text="Current template only", variable=self.example_current_only).pack(side="left",
                                                                                                    padx=8)
        ttk.Button(bar, text="Search", command=self._example_search).pack(side="left")
        ttk.Button(bar, text="Use as build base", command=self._example_use).pack(side="right")
        split = ttk.PanedWindow(structs, orient="vertical")
        split.grid(row=1, column=0, sticky="nsew")
        box, self.example_tree = _tree(split, [("name", "Test Struct Name", 360, True),
                                               ("template", "Template", 110, False), ("row", "Test row", 130, False),
                                               ("sheet", "Sheet", 120, False), ("status", "Status", 140, False),
                                               ("score", "Score", 50, False)])
        split.add(box, weight=2)
        self.example_tree.bind("<<TreeviewSelect>>", lambda _e: self._example_selected())
        self.example_tree.bind("<Double-1>", lambda _e: self._example_use())
        self.example_detail = tk.Text(split, height=12, wrap="word", font=("Consolas", 9), state="disabled")
        split.add(self.example_detail, weight=1)
        self.example_tabs.add(structs, text="Structures")

        rows = ttk.Frame(self.example_tabs, padding=6)
        rows.columnconfigure(0, weight=1)
        rows.rowconfigure(1, weight=1)
        bar = ttk.Frame(rows)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.example_rows_query = tk.StringVar()
        ttk.Label(bar, text="Filter").pack(side="left")
        e = ttk.Entry(bar, textvariable=self.example_rows_query, width=34)
        e.pack(side="left", padx=4)
        e.bind("<Return>", lambda _e: self._example_rows())
        ttk.Button(bar, text="List test rows", command=self._example_rows).pack(side="left")
        ttk.Label(bar, text="Double-click a row to list its structures", foreground="#4a5560").pack(side="right")
        box, self.example_rows_tree = _tree(rows, [("workbook", "Workbook", 260, True), ("row", "Row", 140, False),
                                                   ("status", "Status", 140, False), ("pad", "Pad-row template", 140,
                                                                                        False),
                                                   ("count", "Structures", 70, False),
                                                   ("sheets", "Template sheets", 260, True)])
        box.grid(row=1, column=0, sticky="nsew")
        self.example_rows_tree.bind("<Double-1>", lambda _e: self._example_row_open())
        self.example_tabs.add(rows, text="Test rows")
        return tab

    def _build_docs_tab(self, parent) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=6)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        bar = ttk.Frame(tab)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.doc_query = tk.StringVar()
        ttk.Label(bar, text="Search user guides / specs / DRC tables").pack(side="left")
        e = ttk.Entry(bar, textvariable=self.doc_query, width=50)
        e.pack(side="left", padx=4)
        e.bind("<Return>", lambda _e: self._doc_search())
        ttk.Button(bar, text="Search", command=self._doc_search).pack(side="left")
        split = ttk.PanedWindow(tab, orient="vertical")
        split.grid(row=1, column=0, sticky="nsew")
        box, self.doc_tree = _tree(split, [("file", "File", 300, False), ("line", "Line", 55, False),
                                           ("text", "Snippet", 600, True)])
        split.add(box, weight=1)
        self.doc_tree.bind("<<TreeviewSelect>>", lambda _e: self._doc_open())
        self.doc_text = tk.Text(split, wrap="word", font=("Consolas", 10), state="disabled")
        self.doc_text.tag_configure("hit", background="#fff2a8")
        split.add(self.doc_text, weight=2)
        return tab

    def _build_params_tab(self, parent) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=6)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        bar = ttk.Frame(tab)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.param_query = tk.StringVar()
        self.param_current_only = tk.BooleanVar(value=False)
        ttk.Label(bar, text="Words in parameter name or help").pack(side="left")
        e = ttk.Entry(bar, textvariable=self.param_query, width=40)
        e.pack(side="left", padx=4)
        e.bind("<Return>", lambda _e: self._param_search())
        ttk.Checkbutton(bar, text="Current template only", variable=self.param_current_only).pack(side="left",
                                                                                                  padx=8)
        ttk.Button(bar, text="Search", command=self._param_search).pack(side="left")
        ttk.Label(bar, text="Double-click to edit it in Build", foreground="#4a5560").pack(side="right")
        box, self.param_tree = _tree(tab, [("template", "Template", 150, False), ("name", "Parameter", 180, False),
                                           ("type", "Type", 55, False), ("default", "Default", 200, False),
                                           ("help", "Help", 600, True)])
        box.grid(row=1, column=0, sticky="nsew")
        self.param_tree.bind("<Double-1>", lambda _e: self._param_open())
        return tab

    def _build_import_tab(self, parent) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=6)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(2, weight=1)
        bar = ttk.Frame(tab)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.import_var = tk.StringVar()
        ttk.Label(bar, text="Buildsheet (.xlsx / tab-delimited .csv)").pack(side="left")
        ttk.Entry(bar, textvariable=self.import_var, width=60).pack(side="left", padx=4)
        ttk.Button(bar, text="Browse...", command=self._import_browse).pack(side="left")
        bar2 = ttk.Frame(tab)
        bar2.grid(row=1, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(bar2, text="Template").pack(side="left")
        self.import_template = tk.StringVar()
        self.import_template_combo = ttk.Combobox(bar2, textvariable=self.import_template, state="readonly",
                                                  width=28)
        self.import_template_combo.pack(side="left", padx=4)
        ttk.Button(bar2, text="Validate", command=self._import_validate).pack(side="left", padx=(8, 0))
        ttk.Button(bar2, text="Load rows into Build", command=self._import_load).pack(side="left", padx=4)
        self.import_summary = tk.StringVar()
        ttk.Label(bar2, textvariable=self.import_summary, foreground="#4a5560").pack(side="right")
        split = ttk.PanedWindow(tab, orient="vertical")
        split.grid(row=2, column=0, sticky="nsew")
        box, self.import_issues = _tree(split, [("level", "Level", 70, False), ("row", "Row", 50, False),
                                                ("column", "Column", 170, False), ("message", "Message", 700, True)],
                                        height=6)
        self.import_issues.tag_configure("error", foreground="#b00020")
        self.import_issues.tag_configure("warning", foreground="#a15c00")
        split.add(box, weight=1)
        self.import_preview_box = ttk.Frame(split)
        self.import_preview_box.columnconfigure(0, weight=1)
        self.import_preview_box.rowconfigure(0, weight=1)
        split.add(self.import_preview_box, weight=2)
        return tab

    # -- loading / catalog -----------------------------------------------------
    def _browse_dir(self, var: tk.StringVar) -> None:
        path = filedialog.askdirectory(parent=self, initialdir=var.get() or None)
        if path:
            var.set(path)

    def _load(self) -> None:
        svc = make_service(self.data_var.get().strip() or None, self.out_var.get().strip() or None)

        def work():
            templates = svc.templates()
            svc.list_example_rows(max_results=1)
            return svc, templates

        self._task(work, self._loaded, f"Loading templates from {svc.cfg.data_dir} ...")

    def _loaded(self, value) -> None:
        self.svc, self.templates = value
        cats = sorted({t.category for t in self.templates})
        self.category_combo.configure(values=("All", *cats))
        self.import_template_combo.configure(values=[t.id for t in self.templates])
        self._fill_catalog()
        self.status.set(f"{len(self.templates)} templates loaded; output -> {self.svc.cfg.output_dir}")

    def _fill_catalog(self) -> None:
        _clear(self.catalog)
        words = self.catalog_filter.get().lower().split()
        cat = self.category_var.get()
        for t in self.templates:
            hay = f"{t.id} {t.cell_name} {t.template_name} {t.library} {t.description} {t.owner}".lower()
            if (cat != "All" and t.category != cat) or not all(w in hay for w in words):
                continue
            self.catalog.insert("", "end", iid=t.id, values=(t.id, t.category, t.cell_name, len(t.params)))
        if self.tpl and self.catalog.exists(self.tpl.id):
            self.catalog.selection_set(self.tpl.id)
            self.catalog.see(self.tpl.id)

    def _catalog_selected(self) -> None:
        sel = self.catalog.selection()
        if sel and (self.tpl is None or sel[0] != self.tpl.id):
            self._set_template(sel[0])

    def _set_template(self, key: str, keep_example: bool = False) -> bool:
        if self.svc is None:
            return False
        tpl = self._guard(self.svc.template, key)
        if tpl is None:
            return False
        if self.tpl is not None and tpl.id == self.tpl.id:
            return True
        if self.rows and not messagebox.askyesno(
                "TCE templates", f"Switch to {tpl.id}? The {len(self.rows)} row(s) in Build are cleared.", parent=self):
            if self.tpl and self.catalog.exists(self.tpl.id):
                self.catalog.selection_set(self.tpl.id)
            return False
        self.tpl = tpl
        self.rows = []
        self.sweeps = {}
        if not keep_example:
            self.example = None
        self.pins_var.set("")
        self.extras_var.set("")
        self.import_template.set(tpl.id)
        if self.catalog.exists(tpl.id):
            self.catalog.selection_set(tpl.id)
            self.catalog.see(tpl.id)
        info = self.svc.describe_template(tpl.id)
        text = (f"{tpl.template_name}  ({tpl.library}/{tpl.cell_name})\nOwner: {tpl.owner}\n{tpl.description}\n"
                f"Pins: {', '.join(tpl.pins) or '-'}\n{info.get('pins_note', '')}")
        self._set_text(self.catalog_info, text)
        self._relayout()
        self._fill_sweeps()
        return True

    def _relayout(self) -> None:
        if self.svc is None or self.tpl is None:
            return
        prepared = self._guard(self.svc.prepare, self.tpl.id, None, self._pins(), self._extras(), self.example)
        if prepared is None:
            return
        tpl, pins, extras, base = prepared
        self.pins_eff, self.extras_eff, self.base = pins, extras, base
        cols = tpl.columns(pins)
        self.params = {p.name: p for p in cols}
        self.columns = list(dict.fromkeys([*LEADING, *(p.name for p in cols), *extras]))
        self.sweep_column_combo.configure(values=self.columns)
        self.template_label.set(f"{tpl.id}  -  {len(self.columns)} columns")
        if self.example:
            self.base_label.set(f"Base: {self.example.get('test_struct_name')} ({self.example.get('workbook')})")
        else:
            self.base_label.set("Base: pcell defaults")
        self._fill_rows()

    def _pins(self) -> list[str] | None:
        return _split_list(self.pins_var.get()) or None

    def _extras(self) -> list[str] | None:
        # Tool column names contain spaces ("Test Type"), so only commas separate them.
        return [e.strip() for e in self.extras_var.get().split(",") if e.strip()] or None

    # -- rows ------------------------------------------------------------------
    def _value(self, row: dict, column: str) -> tuple[str, str]:
        if column in row:
            return str(row[column]), "row"
        if column in self.base:
            return str(self.base[column]), "example"
        p = self.params.get(column)
        return (p.default if p else ""), "default"

    def _fill_rows(self, select: list[int] | None = None) -> None:
        _clear(self.row_tree)
        for i, row in enumerate(self.rows):
            self.row_tree.insert("", "end", iid=str(i), values=(i + 1, self._value(row, "StructureName")[0], len(row)))
        if select is None:
            select = [0] if self.rows else []
        sel = [str(i) for i in select if 0 <= i < len(self.rows)]
        if sel:
            self.row_tree.selection_set(sel)
            self.row_tree.see(sel[0])
        self._fill_columns()

    def _selected_rows(self) -> list[int]:
        return sorted(int(i) for i in self.row_tree.selection())

    def _require_template(self) -> bool:
        if self.tpl is None:
            messagebox.showinfo("TCE templates", "Load the bundle and pick a template first.", parent=self)
            return False
        return True

    def _add_row(self) -> None:
        if not self._require_template():
            return
        self.rows.append({"StructureName": f"{self.tpl.id.upper()}_{len(self.rows) + 1}"})
        self._fill_rows([len(self.rows) - 1])

    def _dup_rows(self) -> None:
        picked = self._selected_rows()
        start = len(self.rows)
        self.rows += [dict(self.rows[i]) for i in picked]
        self._fill_rows(list(range(start, len(self.rows))))

    def _delete_rows(self) -> None:
        picked = set(self._selected_rows())
        self.rows = [r for i, r in enumerate(self.rows) if i not in picked]
        self._fill_rows()

    def _clear_rows(self) -> None:
        if self.rows and messagebox.askyesno("TCE templates", "Remove all rows?", parent=self):
            self.rows = []
            self._fill_rows()

    def _fill_columns(self) -> None:
        _clear(self.col_tree)
        picked = self._selected_rows()
        row = self.rows[picked[0]] if picked else {}
        words = self.column_filter.get().lower().split()
        for col in self.columns:
            value, origin = self._value(row, col)
            p = self.params.get(col)
            default = p.default if p else ""
            if words and not all(w in col.lower() for w in words):
                continue
            if self.only_set.get() and origin == "default":
                continue
            if len(picked) > 1 and len({self._value(self.rows[i], col)[0] for i in picked}) > 1:
                value = "<varies>"
            self.col_tree.insert("", "end", iid=col, values=(col, value, origin, default, p.type if p else "tool"),
                                 tags=(origin,))
        if self.edit_column.get() and self.col_tree.exists(self.edit_column.get()):
            self.col_tree.selection_set(self.edit_column.get())
            self.col_tree.see(self.edit_column.get())

    def _column_selected(self) -> None:
        sel = self.col_tree.selection()
        if not sel:
            return
        col = sel[0]
        self.edit_column.set(col)
        self.edit_value.set(self.col_tree.set(col, "value"))
        p = self.params.get(col)
        self.value_combo.configure(values=p.legal_values if p and p.legal_values else ())
        if p:
            legal = f"\nLegal values: {', '.join(p.legal_values)}" if p.legal_values else ""
            flags = "" if p.editable else "  [not editable in CDF]"
            text = f"{col} ({p.type}, {p.source}){flags}  default: {p.default!r}\n{p.help}{legal}"
        else:
            text = f"{col}: tool column (not a template parameter)."
        self._set_text(self.help_text, text)

    def _apply(self, fn) -> None:
        col = self.edit_column.get()
        picked = self._selected_rows()
        if not col or not picked:
            messagebox.showinfo("TCE templates", "Select row(s) and a column first.", parent=self)
            return
        for i in picked:
            fn(self.rows[i], col)
        self._fill_rows(picked)

    def _set_value(self) -> None:
        value = self.edit_value.get()
        if value == "<varies>":
            return
        self._apply(lambda row, col: row.__setitem__(col, value))

    def _unset_value(self) -> None:
        self._apply(lambda row, col: row.pop(col, None))

    def _blank_value(self) -> None:
        self._apply(lambda row, col: row.__setitem__(col, ""))

    # -- build / save ----------------------------------------------------------
    def _build_sheet(self):
        if not self._require_template() or self.svc is None:
            return None
        if not self.rows:
            messagebox.showinfo("TCE templates", "Add at least one row.", parent=self)
            return None
        return self._guard(self.svc.build_rows, self.tpl.id, self.rows, None, self._pins(), self._extras(), None,
                           self.example)

    def _show_issues(self, tree: ttk.Treeview, issues: list[dict]) -> None:
        _clear(tree)
        for n, issue in enumerate(issues):
            tree.insert("", "end", iid=str(n), tags=(issue.get("level", ""),),
                        values=(issue.get("level", ""), issue.get("row", ""), issue.get("column", ""),
                                issue.get("message", "")))

    def _check(self) -> None:
        built = self._build_sheet()
        if built is None:
            return
        _tpl, sheet = built
        self._show_issues(self.issue_tree, [i.to_dict() for i in sheet.issues])
        errors = sum(i.level == "error" for i in sheet.issues)
        self.status.set(f"{len(sheet.rows)} rows x {len(sheet.columns)} columns; {errors} error(s), "
                        f"{len(sheet.issues) - errors} warning(s)")

    def _save(self, ask: bool) -> None:
        built = self._build_sheet()
        if built is None:
            return
        tpl, sheet = built
        self._show_issues(self.issue_tree, [i.to_dict() for i in sheet.issues])
        if not sheet.ok:
            messagebox.showerror("TCE templates", "Fix the errors listed below before saving.", parent=self)
            return
        fmt = self.format_var.get()
        if ask:
            path = filedialog.asksaveasfilename(
                parent=self, defaultextension=f".{fmt}", initialdir=str(self.svc.cfg.output_dir),
                initialfile=f"{tpl.id}.{fmt}", filetypes=[("Excel", "*.xlsx"), ("Tab-delimited CSV", "*.csv")])
            if not path:
                return
            if Path(path).suffix.lower() not in (".xlsx", ".csv"):
                messagebox.showerror("TCE templates", "Save as .xlsx or .csv", parent=self)
                return
            sheet.write(Path(path))
        else:
            path = self._guard(self.svc.write, sheet, tpl, None, fmt)
            if path is None:
                return
            path = path["path"]
        self.status.set(f"Wrote {len(sheet.rows)} row(s) to {path}")

    def _goto_issue(self) -> None:
        sel = self.issue_tree.selection()
        if not sel:
            return
        row, col = self.issue_tree.set(sel[0], "row"), self.issue_tree.set(sel[0], "column")
        if row:
            self._fill_rows([int(row) - 1])
        if col and self.col_tree.exists(col):
            self.col_tree.selection_set(col)
            self.col_tree.see(col)

    # -- sweeps ----------------------------------------------------------------
    def _fill_sweeps(self) -> None:
        _clear(self.sweep_tree)
        for col, values in self.sweeps.items():
            self.sweep_tree.insert("", "end", iid=col, values=(col, len(values),
                                                               " | ".join(v or BLANK_TOKEN for v in values)))

    def _sweep_selected(self) -> None:
        sel = self.sweep_tree.selection()
        if sel:
            self.sweep_column.set(sel[0])
            self.sweep_values.delete("1.0", "end")
            self.sweep_values.insert("1.0", "\n".join(v or BLANK_TOKEN for v in self.sweeps[sel[0]]))

    def _sweep_put(self) -> None:
        col = self.sweep_column.get().strip()
        values = [("" if v.strip() == BLANK_TOKEN else v.strip())
                  for v in self.sweep_values.get("1.0", "end").splitlines() if v.strip()]
        if not col or not values:
            messagebox.showinfo("TCE templates", "Pick a column and enter at least one value.", parent=self)
            return
        if self.columns and col not in self.columns:
            messagebox.showerror("TCE templates", f"{col!r} is not a column of {self.tpl.id}.", parent=self)
            return
        self.sweeps[col] = values
        self._fill_sweeps()

    def _sweep_remove(self) -> None:
        for col in self.sweep_tree.selection():
            self.sweeps.pop(col, None)
        self._fill_sweeps()

    def _sweep_clear(self) -> None:
        self.sweeps = {}
        self._fill_sweeps()

    def _sweep_generate(self) -> None:
        if not self._require_template():
            return
        picked = self._selected_rows()
        index = picked[0] if picked else None
        base = dict(self.rows[index]) if index is not None else {}
        rows = self._guard(buildsheet.expand_sweep, base, self.sweeps, self.sweep_mode.get())
        if rows is None:
            return
        name_template = self.sweep_name.get().strip()
        if name_template:
            try:
                for i, row in enumerate(rows, start=1):
                    ctx = {c: self._value(row, c)[0] for c in self.columns}
                    ctx["i"] = i

                    def sub(m: re.Match) -> str:
                        if m.group(1) not in ctx:
                            raise ValueError(f"name template references unknown column {m.group(1)!r}")
                        return str(ctx[m.group(1)])

                    row["StructureName"] = PLACEHOLDER.sub(sub, name_template)
            except ValueError as exc:
                messagebox.showerror("TCE templates", str(exc), parent=self)
                return
        if index is not None and self.sweep_replace.get():
            self.rows[index:index + 1] = rows
            start = index
        else:
            start = len(self.rows)
            self.rows += rows
        self._fill_rows(list(range(start, start + len(rows))))
        self.tabs.select(self.build_tab)
        self.status.set(f"Generated {len(rows)} row(s)")

    # -- examples --------------------------------------------------------------
    def _example_search(self) -> None:
        if self.svc is None:
            return
        svc = self.svc
        template = self.tpl.id if self.example_current_only.get() and self.tpl else None
        query, row = self.example_query.get(), self.example_row.get().strip() or None
        self._task(lambda: svc.search_example_structures(query, template, row, 500), self._example_results,
                   "Searching finalized examples ...")

    def _example_results(self, value: dict) -> None:
        self.example_hits = value["results"]
        _clear(self.example_tree)
        for n, h in enumerate(self.example_hits):
            self.example_tree.insert("", "end", iid=str(n), values=(
                h["test_struct_name"], h.get("template") or "", h["row_name"], h["sheet"], h["status"], h["score"]))
        self.status.set(f"{len(self.example_hits)} example structure(s)")

    def _example_key(self) -> dict | None:
        sel = self.example_tree.selection()
        if not sel:
            return None
        h = self.example_hits[int(sel[0])]
        return {"workbook": h["workbook"], "test_struct_name": h["test_struct_name"], "struct_id": h["struct_id"],
                "sheet": h["sheet"], "template": h.get("template")}

    def _example_selected(self) -> None:
        key = self._example_key()
        if key is None or self.svc is None:
            return
        svc = self.svc
        self._task(lambda: svc.get_example_structure(key["workbook"], key["test_struct_name"], key["struct_id"],
                                                     key["sheet"]),
                   lambda v: self._set_text(self.example_detail, json.dumps(v, indent=1)),
                   "Reading example ...")

    def _example_use(self) -> None:
        key = self._example_key()
        if key is None:
            return
        if not key["template"]:
            messagebox.showerror("TCE templates", "This example's template is not in the bundle.", parent=self)
            return
        example = {k: key[k] for k in ("workbook", "test_struct_name", "struct_id", "sheet")}
        if self.tpl is None or self.tpl.id != key["template"]:
            previous, self.example = self.example, example
            if not self._set_template(key["template"], keep_example=True):
                self.example = previous
                return
        else:
            self.example = example
            self.pins_var.set("")
            self._relayout()
        if not self.rows:
            self.rows.append({"StructureName": key["test_struct_name"]})
            self._fill_rows([0])
        self.tabs.select(self.build_tab)

    def _clear_example(self) -> None:
        self.example = None
        self._relayout()

    def _example_rows(self) -> None:
        if self.svc is None:
            return
        svc = self.svc
        query = self.example_rows_query.get()
        self._task(lambda: svc.list_example_rows(query, 1000), self._example_rows_results, "Listing test rows ...")

    def _example_rows_results(self, value: dict) -> None:
        _clear(self.example_rows_tree)
        for n, r in enumerate(value["rows"]):
            self.example_rows_tree.insert("", "end", iid=str(n), values=(
                r.get("workbook", ""), r.get("row_name", ""), r.get("status", ""), r.get("padrow_template", ""),
                r.get("total_structures", ""), r.get("template_sheets", "")))
        self.status.set(f"{value['total']} test row(s); {len(value.get('unreadable', []))} unreadable")

    def _example_row_open(self) -> None:
        sel = self.example_rows_tree.selection()
        if not sel:
            return
        self.example_row.set(self.example_rows_tree.set(sel[0], "row") or self.example_rows_tree.set(sel[0],
                                                                                                    "workbook"))
        self.example_query.set("")
        self.example_current_only.set(False)
        self.example_tabs.select(0)
        self._example_search()

    # -- docs ------------------------------------------------------------------
    def _doc_search(self) -> None:
        if self.svc is None or not self.doc_query.get().strip():
            return
        svc, query = self.svc, self.doc_query.get()
        self._task(lambda: svc.search_docs(query, 30), self._doc_results, "Searching documentation ...")

    def _doc_results(self, value: dict) -> None:
        _clear(self.doc_tree)
        self.doc_hits = {}
        if value.get("error"):
            self.status.set(value["error"])
            return
        for f in value["results"]:
            for s in f["snippets"]:
                iid = str(len(self.doc_hits))
                self.doc_hits[iid] = (f["file"], s["line"])
                self.doc_tree.insert("", "end", iid=iid, values=(f["file"], s["line"], s["text"]))
        self.status.set(f"{len(value['results'])} document(s) matched")

    def _doc_open(self) -> None:
        sel = self.doc_tree.selection()
        if not sel or self.svc is None:
            return
        file, line = self.doc_hits[sel[0]]
        start = max(1, line - 40)
        doc = self._guard(self.svc.read_doc, file, start, line + 160)
        if doc is None:
            return
        self._set_text(self.doc_text, doc["text"])
        offset = line - doc["start_line"] + 1
        self.doc_text.tag_add("hit", f"{offset}.0", f"{offset}.end")
        self.doc_text.see(f"{offset}.0")

    def _docs_for_column(self) -> None:
        col = self.edit_column.get()
        if col:
            self.doc_query.set(col)
            self.tabs.select(self.docs_tab)
            self._doc_search()

    # -- parameter search ------------------------------------------------------
    def _param_search(self) -> None:
        if self.svc is None or not self.param_query.get().strip():
            return
        template = self.tpl.id if self.param_current_only.get() and self.tpl else None
        hits = self._guard(self.svc.search_parameters, self.param_query.get(), template, 1000)
        if hits is None:
            return
        _clear(self.param_tree)
        for n, h in enumerate(hits):
            self.param_tree.insert("", "end", iid=str(n), values=(h["template"], h["name"], h["type"], h["default"],
                                                                  h["help"]))
        self.status.set(f"{len(hits)} parameter(s)")

    def _param_open(self) -> None:
        sel = self.param_tree.selection()
        if not sel:
            return
        template, name = self.param_tree.set(sel[0], "template"), self.param_tree.set(sel[0], "name")
        if not self._set_template(template):
            return
        if not self.rows:
            self._add_row()
        self.column_filter.set("")
        self.only_set.set(False)
        self.edit_column.set(name)
        self._fill_columns()
        self.tabs.select(self.build_tab)

    # -- validate / import -----------------------------------------------------
    def _import_browse(self) -> None:
        initial = str(self.svc.cfg.output_dir) if self.svc else None
        path = filedialog.askopenfilename(parent=self, initialdir=initial,
                                          filetypes=[("Buildsheets", "*.xlsx *.csv"), ("All files", "*.*")])
        if path:
            self.import_var.set(path)

    def _import_validate(self) -> None:
        if self.svc is None or not self.import_var.get().strip() or not self.import_template.get():
            messagebox.showinfo("TCE templates", "Load the bundle, choose a file and a template.", parent=self)
            return
        svc, path, template = self.svc, self.import_var.get().strip(), self.import_template.get()

        def work():
            p = svc.sheet_path(path)
            return p, svc.validate_buildsheet(str(p), template), buildsheet.read_csv_file(p)

        self._task(work, self._import_validated, "Validating buildsheet ...")

    def _import_validated(self, value) -> None:
        self.import_path, result, (header, body, _issues) = value
        self._show_issues(self.import_issues, result["issues"])
        state = "OK" if result["ok"] else "has errors"
        self.import_summary.set(f"{result['row_count']} row(s), {len(header)} column(s): {state}")
        for child in self.import_preview_box.winfo_children():
            child.destroy()
        shown = header[:40]
        box, tree = _tree(self.import_preview_box, [(f"c{i}", h, 120, False) for i, h in enumerate(shown)])
        box.grid(row=0, column=0, sticky="nsew")
        for n, row in enumerate(body[:500]):
            tree.insert("", "end", iid=str(n), values=row[:len(shown)])

    def _import_load(self) -> None:
        if self.svc is None or not self.import_var.get().strip() or not self.import_template.get():
            return
        path = self._guard(self.svc.sheet_path, self.import_var.get().strip())
        tpl = self._guard(self.svc.template, self.import_template.get())
        if path is None or tpl is None:
            return
        header, body, issues = buildsheet.read_csv_file(path)
        if any(i.level == "error" for i in issues):
            messagebox.showerror("TCE templates", "\n".join(i.message for i in issues[:10]), parent=self)
            return
        if not self._set_template(tpl.id):
            return
        pins, extras = buildsheet.infer_from_reference(tpl, header)
        self.pins_var.set(", ".join(pins))
        self.extras_var.set(", ".join(extras))
        self.example = None
        self._relayout()
        rows = []
        for values in body:
            row = {}
            for col, text in zip(header, values):
                p = self.params.get(col)
                if col in LEADING or (p is None and text) or (p is not None and text != p.default):
                    row[col] = text
            rows.append(row)
        self.rows = rows
        self._fill_rows()
        self.tabs.select(self.build_tab)
        self.status.set(f"Loaded {len(rows)} row(s) from {path.name}")

    # -- helpers ---------------------------------------------------------------
    @staticmethod
    def _set_text(widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")
