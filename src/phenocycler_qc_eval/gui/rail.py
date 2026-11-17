"""Left rail: image, panel file, output folder, Run / Cancel buttons, status, Details log and the pickers."""

from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from ..advice import short_path
from ..run import find_lone_xpd
from ..settings import settings_dir
from .theme import BG, GUI_STATE_FILE, IMAGE_TYPES, INK, LEFT_WIDTH, px, wrap_to
from .tooltip import Tooltip


def _load_gui_state() -> dict:
    try:
        data = json.loads((settings_dir() / GUI_STATE_FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_gui_state(state: dict) -> None:
    try:
        folder = settings_dir()
        folder.mkdir(parents=True, exist_ok=True)
        (folder / GUI_STATE_FILE).write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass  # remembering folders is a convenience, never a reason to fail


class RailMixin:
    """The left rail of App. Relies on App for the variables and the run / settings hooks."""

    def _path_entry(self, parent, var) -> ttk.Entry:
        entry = ttk.Entry(parent, textvariable=var, state="readonly")
        # A long path shows its end (the file name is the part that matters), not its start.
        var.trace_add("write", lambda *_a: entry.after_idle(self._show_path_end, entry))
        return entry

    @staticmethod
    def _show_path_end(entry) -> None:
        try:
            entry.xview_moveto(1.0)
        except tk.TclError:
            pass

    def _build_left(self) -> None:
        """The rail has two parts: the inputs, which scroll when the window is short, and a bar pinned under
        them (settings, output folder, Run / Cancel, progress, status, elapsed time) that never scrolls away."""
        self.left = ttk.Frame(self.paned)
        self.left.columnconfigure(0, weight=1)
        self.left.rowconfigure(0, weight=1)
        self.rail_canvas = tk.Canvas(self.left, highlightthickness=0, borderwidth=0, bg=BG,
                                     width=px(self, LEFT_WIDTH) - 6, height=1)
        self.rail_scroll = ttk.Scrollbar(self.left, orient="vertical", command=self.rail_canvas.yview)
        self.rail_canvas.configure(yscrollcommand=self.rail_scroll.set)
        self.rail_canvas.grid(row=0, column=0, sticky="nsew")
        self.inputs = ttk.Frame(self.rail_canvas, padding=(16, 16, 12, 8))
        self.inputs.columnconfigure(0, weight=1)
        self._inputs_window = self.rail_canvas.create_window((0, 0), window=self.inputs, anchor="nw")
        self.inputs.bind("<Configure>", self._rail_resized)
        self.rail_canvas.bind("<Configure>", self._rail_canvas_resized)
        self.runbar = ttk.Frame(self.left, padding=(16, 8, 12, 12))
        self.runbar.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.runbar.columnconfigure(0, weight=1)
        left, bar = self.inputs, self.runbar

        self.image_label = ttk.Label(left, text="Image", style="Section.TLabel")
        self.image_entry = self._path_entry(left, self.image_var)
        self.image_buttons = ttk.Frame(left)
        self.choose_file_button = ttk.Button(
            self.image_buttons, text="Choose file", command=self.choose_image_file)
        self.choose_folder_button = ttk.Button(
            self.image_buttons, text="Choose folder", command=self.choose_image_folder)
        self.choose_file_button.pack(side="left")
        self.choose_folder_button.pack(side="left", padx=(8, 0))
        self.image_hint = ttk.Label(
            left, text="A .qptiff slide file (.tif and .tiff also work), or a Xenium folder.",
            style="Muted.TLabel", wraplength=225, justify="left")

        self.panel_label = ttk.Label(left, text="Panel file (optional)", style="Section.TLabel")
        self.panel_entry = self._path_entry(left, self.panel_var)
        self.panel_buttons = ttk.Frame(left)
        self.choose_panel_button = ttk.Button(
            self.panel_buttons, text="Choose panel file", command=self.choose_panel_file)
        self.clear_panel_button = ttk.Button(
            self.panel_buttons, text="Remove panel file", style="Link.TButton", command=self.clear_panel)
        self.choose_panel_button.pack(side="top", anchor="w")
        # Shown only while there is an image and no panel file: saves a marker,cycle,fluor CSV to fill in.
        self.template_button = ttk.Button(
            left, text="Save a layout CSV template", style="Link.TButton", command=self.save_layout_template)
        self.panel_hint = ttk.Label(
            left, text="Optional. Lists antibody, cycle and dye for each channel. Needed for the crosstalk check.",
            style="Muted.TLabel", wraplength=225,
            justify="left")

        self.saved_frame = ttk.Frame(bar)
        self.saved_label = ttk.Label(
            self.saved_frame, textvariable=self.saved_to_var, style="Muted.TLabel",
            wraplength=160, justify="left")
        self.change_output_button = ttk.Button(
            self.saved_frame, text="Change", style="Link.TButton", command=self.choose_output_folder)
        self.saved_label.pack(side="left", fill="x", expand=True)
        self.change_output_button.pack(side="right", anchor="n")

        self.run_frame = ttk.Frame(bar)
        self.run_button = ttk.Button(
            self.run_frame, text="Run", style="Primary.TButton", command=self._click_run)
        self.cancel_button = ttk.Button(
            self.run_frame, text="Cancel", command=self._click_cancel, state="disabled")
        self.run_button.pack(side="left")
        self.cancel_button.pack(side="left", padx=(10, 0))

        self.settings_frame = ttk.Frame(bar)
        self.settings_label = ttk.Label(
            self.settings_frame, textvariable=self.settings_status_var, style="Muted.TLabel")
        self.settings_button = ttk.Button(
            self.settings_frame, text="Settings", command=self._click_settings)
        self.settings_label.pack(side="left")
        self.settings_button.pack(side="right")

        self.run_hint_var = tk.StringVar(master=self, value="")
        self.run_hint_label = ttk.Label(bar, textvariable=self.run_hint_var, style="Muted.TLabel",
                                        wraplength=225, justify="left")
        self.progress = ttk.Progressbar(bar, mode="determinate", maximum=100)
        self.status_label = ttk.Label(
            bar, textvariable=self.status_var, style="Status.TLabel",
            wraplength=225, justify="left")
        self.elapsed_label = ttk.Label(bar, textvariable=self.elapsed_var, style="Muted.TLabel")

        self.details_button = ttk.Button(
            left, text="Details", style="Link.TButton", command=self.toggle_details)
        self.log_frame = ttk.Frame(left)
        self.log = tk.Text(
            self.log_frame, height=8, width=28, wrap="word", state="disabled",
            font=("Consolas", 9), bg="#ffffff", fg=INK, relief="solid", borderwidth=1,
            highlightthickness=0)
        log_scroll = ttk.Scrollbar(self.log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        self.load_button = ttk.Button(
            left, text="Load a past run", style="Link.TButton", command=self.choose_past_run)
        self.example_button = ttk.Button(left, text="Try the example", style="Link.TButton",
                                         command=self._click_example)
        Tooltip(self.saved_label, lambda: self.output_var.get())
        Tooltip(self.status_label, self.full_path_tip)

    # ---- the scrolling inputs ----------------------------------------------
    def _rail_canvas_resized(self, event) -> None:
        self.rail_canvas.itemconfigure(self._inputs_window, width=event.width)
        wrap = max(120, event.width - 36)
        for lab in (self.image_hint, self.panel_hint):
            wrap_to(lab, wrap)
        self._rewrap_bar()
        self._rail_resized()

    def _rewrap_bar(self) -> None:
        wrap = max(120, self.runbar.winfo_width() - 28)
        wrap_to(self.status_label, wrap)
        wrap_to(self.run_hint_label, wrap)
        room = self.change_output_button.winfo_reqwidth() + 14     # the button keeps the width it needs
        wrap_to(self.saved_label, max(80, wrap - room))

    def _rail_resized(self, _event=None) -> None:
        """Scroll range follows the inputs; the scrollbar appears only when they do not fit."""
        self.rail_canvas.configure(scrollregion=self.rail_canvas.bbox("all"))
        if self.inputs.winfo_reqheight() > self.rail_canvas.winfo_height():
            self.rail_scroll.grid(row=0, column=1, sticky="ns")
        else:
            self.rail_scroll.grid_remove()
            self.rail_canvas.yview_moveto(0)

    def _rail_wheel(self, event) -> None:
        if self.rail_scroll.winfo_ismapped():
            self.rail_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _bind_rail_wheel(self, widget=None) -> None:
        widget = widget or self.inputs
        for child in widget.winfo_children():
            if not isinstance(child, tk.Text):                 # the log keeps its own wheel scrolling
                child.bind("<MouseWheel>", self._rail_wheel, add="+")
                self._bind_rail_wheel(child)

    # ---- left column layout ----------------------------------------------
    def unwired_widgets(self) -> list[tk.Widget]:
        out: list[tk.Widget] = []
        if not self.WIRED.get("run"):
            out += [self.run_frame, self.run_button, self.cancel_button, self.progress,
                    self.elapsed_label, self.details_button]
        if not self.WIRED.get("settings"):
            out += [self.settings_frame, self.settings_label, self.settings_button]
        if not self.WIRED.get("example"):
            out += [self.example_button]
        return out

    def _layout_left(self) -> None:
        w = self.WIRED
        west = (self.image_label, self.panel_label, self.image_hint, self.panel_hint,
                self.status_label, self.elapsed_label, self.details_button, self.load_button,
                self.image_buttons, self.panel_buttons, self.template_button, self.example_button,
                self.run_hint_label)
        # Inputs scroll. The big blue button in the results pane is the one primary action; the example is a link.
        rows: list[tuple[tk.Widget, dict]] = []
        rows += [
            (self.image_label, {"pady": (0, 4)}),
            (self.image_entry, {}),
            (self.image_buttons, {"pady": (6, 0)}),
            (self.image_hint, {"pady": (4, 10)}),
            (self.panel_label, {"pady": (0, 4)}),
            (self.panel_entry, {}),
            (self.panel_buttons, {"pady": (6, 0)}),
            (self.template_button, {"pady": (2, 0)}),
            (self.panel_hint, {"pady": (2, 10)}),
            (self.load_button, {"pady": (0, 0)}),
        ]
        if w.get("example"):
            rows.append((self.example_button, {"pady": (0, 4)}))
        if w.get("run"):
            rows.append((self.details_button, {"pady": (2, 4)}))
        row = 0
        for widget, opts in rows:
            widget.grid(row=row, column=0, sticky="w" if widget in west else "ew", **opts)
            row += 1
        self._log_row = row
        self.log_frame.grid(row=row, column=0, sticky="nsew")
        if not self._details_open:
            self.log_frame.grid_remove()
        self._set_log_weight()

        # Pinned bar: settings state, where results go, Run, progress, status and elapsed time.
        bar_rows: list[tuple[tk.Widget, dict]] = []
        if w.get("settings"):
            bar_rows.append((self.settings_frame, {"pady": (0, 8)}))
        bar_rows.append((self.saved_frame, {"pady": (0, 8)}))          # where results go is chosen before Run
        if w.get("run"):
            bar_rows.append((self.run_frame, {"pady": (0, 2)}))
            bar_rows.append((self.run_hint_label, {"pady": (0, 6)}))
            bar_rows.append((self.progress, {"pady": (0, 6)}))
        bar_rows.append((self.status_label, {"pady": (0, 2)}))
        if w.get("run"):
            bar_rows.append((self.elapsed_label, {"pady": (0, 0)}))
        for i, (widget, opts) in enumerate(bar_rows):
            widget.grid(row=i, column=0, sticky="w" if widget in west else "ew", **opts)
        self.runbar.bind("<Configure>", lambda _e: self._rewrap_bar())
        self._bind_rail_wheel()
        self.rail_canvas.bind("<MouseWheel>", self._rail_wheel)

    def _set_log_weight(self) -> None:
        """The log row takes spare height only while it is open (no blank gap when collapsed)."""
        self.inputs.rowconfigure(self._log_row, weight=1 if self._details_open else 0)

    # ---- pickers -----------------------------------------------------------
    def _remember(self, key: str, path: Path) -> None:
        self._state[key] = str(path)
        _save_gui_state(self._state)

    def _initial(self, key: str) -> str | None:
        p = self._state.get(key)
        return p if p and Path(p).is_dir() else None

    def choose_image_file(self) -> None:
        name = filedialog.askopenfilename(
            title="Choose your image", filetypes=IMAGE_TYPES,
            initialdir=self._initial("image_dir"))
        if name:
            self.set_image(Path(name))

    def choose_image_folder(self) -> None:
        name = filedialog.askdirectory(
            title="Choose your Xenium folder", initialdir=self._initial("image_dir"))
        if name:
            self.set_image(Path(name))

    def choose_panel_file(self) -> None:
        name = filedialog.askopenfilename(
            title="Choose your panel file",
            filetypes=[("Panel files", "*.xpd"), ("All files", "*.*")],
            initialdir=self._initial("panel_dir") or self._initial("image_dir"))
        if name:
            self.panel_var.set(name)
            self._remember("panel_dir", Path(name).parent)
            self._announce_panel()

    def clear_panel(self) -> None:
        self.panel_var.set("")
        self._announce_panel()

    def choose_output_folder(self) -> None:
        name = filedialog.askdirectory(
            title="Save results in",
            initialdir=self._initial("output_dir") or self._initial("image_dir"))
        if name:
            self._output_user_set = True
            self._set_output(Path(name))
            self._remember("output_dir", Path(name))

    def choose_past_run(self) -> None:
        name = filedialog.askdirectory(
            title="Choose a results folder", initialdir=self._initial("past_run_dir"))
        if name:
            self.load_past_run(Path(name))

    # ---- state ---------------------------------------------------------------
    def _set_output(self, folder: Path) -> None:
        """Remember where results go. The rail says it in one short line; the whole path is the tooltip."""
        self.output_var.set(str(folder))
        if self._output_user_set:
            self.saved_to_var.set(f"Results go to {short_path(folder, 18)}")
        else:
            self.saved_to_var.set("Results are saved next to your image.")

    def _announce_panel(self) -> None:
        if not self.panel_var.get():
            self.status_var.set("No panel file: crosstalk will be off.")
        else:
            self.status_var.set(f"Panel file: {Path(self.panel_var.get()).name}.")

    def _update_run_state(self) -> None:
        self._busy(self.handle is not None)

    def run_hint_text(self) -> str:
        """Why Run is off, in one line (empty while it is on, or while a run is going)."""
        return self.run_hint_var.get()

    def _image_ready(self) -> bool:
        text = self.image_var.get().strip()
        try:
            return bool(text) and Path(text).exists()
        except OSError:
            return False

    def _refresh_run_button(self) -> None:
        """Run is on whenever a run can start: an image that exists is chosen and no run is going. When it is off,
        a line under it says why. Called whenever the image box changes, so it can never be stale."""
        busy = self.handle is not None
        ready = self._image_ready()
        self.run_button.configure(state="disabled" if busy or not ready else "normal")
        if busy or ready:
            self.run_hint_var.set("")
        elif not self.image_var.get().strip():
            self.run_hint_var.set("Choose an image to enable Run.")
        else:
            self.run_hint_var.set("That image was not found. Choose it again to enable Run.")
        # The big button in the results pane is the one primary action: Run once an image is ready.
        if ready and not busy:
            self.empty_button.configure(text="Run", command=self._click_run)
            name = Path(self.image_var.get().strip()).name or self.image_var.get().strip()
            self.empty_steps.configure(text=f"Ready: {name}" + chr(10) + "Press Run to check it.")
        else:
            self.empty_button.configure(text="Choose your image", command=self.choose_image_file)
            self.empty_steps.configure(text=self._empty_lines[1])

    def _update_panel_widgets(self) -> None:
        """Remove panel file is shown only while there is a panel file to remove."""
        if self.panel_var.get().strip():
            if not self.clear_panel_button.winfo_manager():
                self.clear_panel_button.pack(side="top", anchor="w", pady=(2, 0))
        else:
            self.clear_panel_button.pack_forget()

    def _update_details_button(self) -> None:
        """Details is offered while a run is going, after a problem, and while the log is open: not on an idle window."""
        if self.handle is not None or self._details_open or self._error_seen:
            self.details_button.grid()
        else:
            self.details_button.grid_remove()

    def set_image(self, path: Path) -> None:
        path = Path(path)
        if not path.exists():
            self.status_var.set("That image was not found.")
            return
        self.image_var.set(str(path))
        self._remember("image_dir", path.parent)
        filled: list[str] = []
        if not self._output_user_set:
            self._set_output(path.parent / "qc_results")
        if not self.panel_var.get():
            lone = find_lone_xpd(path)           # the one lookup the CLI uses too (R16)
            if lone is not None:
                self.panel_var.set(str(lone))
                filled.append(f"panel file {lone.name}")
        if filled:
            self.status_var.set(f"Filled in the {' and '.join(filled)}. Ready to run.")
        elif not self.panel_var.get():
            self.status_var.set("No panel file: crosstalk will be off.")
        else:
            self.status_var.set("Ready to run.")
        self._update_run_state()

    # ---- details log ---------------------------------------------------------
    def toggle_details(self) -> None:
        self._details_open = not self._details_open
        self._update_details_button()
        if self._details_open:
            self.log_frame.grid()
            self.details_button.configure(text="Hide details")
            self.after_idle(self._show_log)
        else:
            self.log_frame.grid_remove()
            self.details_button.configure(text="Details")
        self._set_log_weight()

    def _show_log(self) -> None:
        """Scroll the rail down so the log that just opened is on screen."""
        try:
            self.update_idletasks()
            self.rail_canvas.yview_moveto(1.0)
        except tk.TclError:
            pass

    def open_details(self) -> None:
        if not self._details_open:
            self.toggle_details()

    def log_line(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
