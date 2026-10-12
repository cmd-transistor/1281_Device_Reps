"""Desktop GUI for prompt-driven ISO structure creation.

``python iso_ai_gui.py`` -- type a prompt, generate, inspect the unit-cell layout,
scalar and buildsheet, then revise / accept / reject and export the buildsheet.
The same engine can be served to other tools with the "Start API" button
(see :mod:`iso_ai_api`).
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from iso_structure_ai import Design, StructureDesigner

EXAMPLES = (
    "CTG 132 NP HVT type A SDR reference",
    "gate to gate 165 P ULVT 1trk",
    "diffusion OGD 110 N FTISL CD",
    "diffchn 132 N len10 den",
    "epi 165 NP pxl array 4x80",
)
MAX_PREVIEW = (980, 640)


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("ISO Structure AI Designer")
        self.geometry("1500x940")
        self.designer: StructureDesigner | None = None
        self.design: Design | None = None
        self.accepted: dict[str, dict[str, str]] = {}
        self.server = None
        self.photo: tk.PhotoImage | None = None
        self.results: "queue.Queue[tuple]" = queue.Queue()
        self.busy = False
        self._build()
        self._run(StructureDesigner, self._loaded, "Loading reference buildsheet...")
        self.after(100, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._close)

    # -- layout --------------------------------------------------------------
    def _build(self) -> None:
        top = ttk.Frame(self, padding=6)
        top.pack(fill="x")
        ttk.Label(top, text="Prompt (free text; optional Field=value lines):").pack(anchor="w")
        row = ttk.Frame(top)
        row.pack(fill="x")
        self.prompt = tk.Text(row, height=3, wrap="word", font=("Segoe UI", 10))
        self.prompt.pack(side="left", fill="x", expand=True)
        self.prompt.bind("<Control-Return>", lambda _e: (self._generate(), "break")[1])
        side = ttk.Frame(row)
        side.pack(side="left", padx=6)
        self.generate_btn = ttk.Button(side, text="Generate (Ctrl+Enter)", command=self._generate)
        self.generate_btn.pack(fill="x")
        self.example = ttk.Combobox(side, values=EXAMPLES, state="readonly", width=34)
        self.example.set("Examples...")
        self.example.bind("<<ComboboxSelected>>", self._use_example)
        self.example.pack(fill="x", pady=(4, 0))

        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=6)
        left = ttk.Frame(body)
        body.add(left, weight=3)
        self.title_var = tk.StringVar(value="No design yet")
        ttk.Label(left, textvariable=self.title_var, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.canvas = tk.Canvas(left, background="white", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        right = ttk.Notebook(body)
        body.add(right, weight=2)
        self.summary = tk.Text(right, wrap="word", font=("Consolas", 9))
        right.add(self.summary, text="Summary")
        sheet = ttk.Frame(right)
        right.add(sheet, text="Buildsheet")
        self.table = ttk.Treeview(sheet, columns=("field", "value"), show="headings")
        self.table.heading("field", text="Field")
        self.table.heading("value", text="Value")
        self.table.column("field", width=170, stretch=False)
        self.table.column("value", width=520)
        scroll = ttk.Scrollbar(sheet, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.table.bind("<Double-1>", self._edit_field)

        bottom = ttk.Frame(self, padding=6)
        bottom.pack(fill="x")
        ttk.Label(bottom, text="Feedback / change request:").pack(anchor="w")
        self.feedback = ttk.Entry(bottom, font=("Segoe UI", 10))
        self.feedback.pack(fill="x")
        self.feedback.bind("<Return>", lambda _e: self._feedback("revise"))
        buttons = ttk.Frame(bottom)
        buttons.pack(fill="x", pady=(4, 0))
        self.action_btns = [
            ttk.Button(buttons, text="Apply change", command=lambda: self._feedback("revise")),
            ttk.Button(buttons, text="Accept (learn)", command=lambda: self._feedback("accept")),
            ttk.Button(buttons, text="Reject -> next option", command=lambda: self._feedback("reject")),
            ttk.Button(buttons, text="Export buildsheet...", command=self._export),
        ]
        for b in self.action_btns:
            b.pack(side="left", padx=(0, 6))
        self.api_btn = ttk.Button(buttons, text="Start API", command=self._toggle_api)
        self.api_btn.pack(side="right")
        self.status = tk.StringVar()
        ttk.Label(self, textvariable=self.status, relief="sunken", anchor="w", padding=3).pack(fill="x")
        self._set_enabled()

    def _set_enabled(self) -> None:
        ready = self.designer is not None and not self.busy
        self.generate_btn.state(["!disabled"] if ready else ["disabled"])
        for b in self.action_btns:
            b.state(["!disabled"] if ready and self.design else ["disabled"])
        self.api_btn.state(["!disabled"] if self.designer else ["disabled"])

    # -- background work -----------------------------------------------------
    def _run(self, func, done, message: str, *args) -> None:
        self.busy = True
        self.status.set(message)
        self._set_enabled()

        def worker() -> None:
            try:
                self.results.put((done, func(*args), None))
            except Exception as exc:  # surfaced in the UI thread
                self.results.put((done, None, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                done, value, error = self.results.get_nowait()
                self.busy = False
                if error:
                    self.status.set(f"Error: {error}")
                    messagebox.showerror("ISO Structure AI", str(error), parent=self)
                else:
                    done(value)
                self._set_enabled()
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _loaded(self, designer: StructureDesigner) -> None:
        self.designer = designer
        s = designer.stats()
        self.status.set(f"Ready: {s['references']} reference structures, {s['approved']} learned designs, "
                        f"{s['learned_keywords']} learned keywords, LLM {'on' if s['llm'] else 'off (rule-based)'}")

    # -- actions -------------------------------------------------------------
    def _use_example(self, _event) -> None:
        self.prompt.delete("1.0", "end")
        self.prompt.insert("1.0", self.example.get())

    def _generate(self) -> None:
        text = self.prompt.get("1.0", "end").strip()
        if self.designer and not self.busy and text:
            self._run(self._create_and_render, self._show, "Generating structure...", text)

    def _create_and_render(self, text: str) -> tuple[Design, Path | None]:
        assert self.designer is not None
        design = self.designer.create(text)
        return design, self.designer.layout_png(design.id)

    def _feedback(self, action: str) -> None:
        if not (self.designer and self.design) or self.busy:
            return
        text = self.feedback.get().strip()
        if action == "revise" and not text:
            messagebox.showinfo("ISO Structure AI", "Type a change request first.", parent=self)
            return
        if action == "accept":
            self._run(self.designer.accept, self._accepted, "Saving approved design...", self.design.id, text)
            return
        func = self.designer.revise if action == "revise" else self.designer.reject

        def work() -> tuple[Design, Path | None]:
            design = func(self.design.id, text)
            return design, self.designer.layout_png(design.id)

        self._run(work, self._show, "Applying feedback...")

    def _accepted(self, design: Design) -> None:
        self.accepted[design.row["StructureName"]] = dict(design.row)
        self.feedback.delete(0, "end")
        self._show((design, None), keep_image=True)
        self.status.set(f"Accepted {design.row['StructureName']} -- learned for future prompts "
                        f"({len(self.accepted)} accepted this session)")

    def _edit_field(self, _event) -> None:
        if not self.design or self.busy:
            return
        item = self.table.focus()
        if not item:
            return
        column, value = self.table.item(item, "values")
        new = _ask_value(self, column, value)
        if new is not None and new != value:
            self.feedback.delete(0, "end")
            self.feedback.insert(0, f"{column}={new}")
            self._feedback("revise")

    def _export(self) -> None:
        if not (self.designer and self.design):
            return
        rows = list(self.accepted.values())
        if self.design.row["StructureName"] not in self.accepted:
            rows.append(dict(self.design.row))
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".csv", filetypes=[("CSV", "*.csv")],
                                            initialfile="ISO_AI_Buildsheet.csv",
                                            title=f"Export {len(rows)} structure(s) as buildsheet")
        if path:
            self.designer.export(rows, Path(path))
            self.status.set(f"Exported {len(rows)} structure(s) to {path}")

    def _toggle_api(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
            self.api_btn.configure(text="Start API")
            self.status.set("API stopped")
            return
        from iso_ai_api import create_server
        try:
            self.server = create_server(self.designer)
        except (OSError, ValueError) as exc:
            messagebox.showerror("ISO Structure AI", f"Could not start API: {exc}", parent=self)
            return
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        host, port = self.server.server_address[:2]
        self.api_btn.configure(text="Stop API")
        self.status.set(f"API listening on http://{host}:{port}  (POST /structures with {{\"prompt\": ...}})")

    # -- display -------------------------------------------------------------
    def _show(self, value: tuple[Design, Path | None], keep_image: bool = False) -> None:
        design, png = value
        self.design = design
        name = design.row.get("StructureName", "")
        self.title_var.set(f"{name}   [v{design.version}, {design.status}]")
        if not keep_image:
            self._show_image(png)

        lines = [f"Structure : {name}", f"Template  : {design.template} ({design.source})",
                 f"Fail modes: {design.fail_modes or '-'}", f"Scalar    : {design.scalar or '-'}", "",
                 "Formula:", f"  {design.formula or '-'}", ""]
        if design.warnings:
            lines += ["Warnings:"] + [f"  ! {w}" for w in design.warnings] + [""]
        lines += ["Reasoning:"] + [f"  {r}" for r in design.reasoning]
        if design.alternatives:
            lines += ["", "Next options (Reject to try):"] + [f"  {a}" for a in design.alternatives]
        if design.history:
            lines += ["", "History:"] + [f"  {h['action']}: {h['text']}" for h in design.history]
        self.summary.configure(state="normal")
        self.summary.delete("1.0", "end")
        self.summary.insert("1.0", "\n".join(lines))
        self.summary.configure(state="disabled")

        self.table.delete(*self.table.get_children())
        assert self.designer is not None
        for column in self.designer.output_fields:
            value = design.row.get(column, "")
            if value:
                self.table.insert("", "end", values=(column, value))
        self.status.set(f"{name}: fail modes {design.fail_modes or '-'}, scalar {design.scalar or '-'}"
                        f"{'  (' + str(len(design.warnings)) + ' warning(s))' if design.warnings else ''}")

    def _show_image(self, png: Path | None) -> None:
        self.canvas.delete("all")
        if not png:
            self.photo = None
            self.canvas.create_text(20, 20, anchor="nw", text="No layout preview for this structure")
            return
        image = tk.PhotoImage(file=str(png))
        width = max(self.canvas.winfo_width(), 300)
        height = max(self.canvas.winfo_height(), 300)
        factor = max(1, -(-image.width() // min(width, MAX_PREVIEW[0])),
                     -(-image.height() // min(height, MAX_PREVIEW[1])))
        self.photo = image.subsample(factor) if factor > 1 else image
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)

    def _close(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        self.destroy()


def _ask_value(parent: tk.Tk, column: str, value: str) -> str | None:
    dialog = tk.Toplevel(parent)
    dialog.title(f"Edit {column}")
    dialog.transient(parent)
    dialog.grab_set()
    entry = ttk.Entry(dialog, width=100)
    entry.insert(0, value)
    entry.pack(padx=8, pady=8, fill="x")
    entry.focus_set()
    result: list[str] = []

    def ok(_event=None) -> None:
        result.append(entry.get())
        dialog.destroy()

    entry.bind("<Return>", ok)
    ttk.Button(dialog, text="Apply", command=ok).pack(pady=(0, 8))
    parent.wait_window(dialog)
    return result[0] if result else None


if __name__ == "__main__":
    App().mainloop()
