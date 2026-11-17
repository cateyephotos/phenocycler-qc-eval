"""Settings panel: the detection rules, crosstalk cut-offs and marker lists, saved per user.

The values are the same file the `pqe` command reads, so a change made here applies to the next run from either
place. The panel never touches a run in progress. Entries are checked as they are typed; a wrong value shows
a plain message beside its field and cannot be saved.
"""

from __future__ import annotations

import copy
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from ..results import norm_name
from ..settings import (
    Settings, default_settings, field_error, format_changes, save_settings,
)
from .dialogs import ask_discard_changes
from .theme import BG, FONT, build_style

ERROR_COLOR = "#b42318"

# (settings key, label, one-line explanation). The explanations restate the detection rules in plain words.
DETECTION_FIELDS = [
    ("detection.dim_fraction", "Dim signal below",
     "An antibody is dim when its signal (the brightest 0.5% of pixels, the 99.5th percentile) is below this share of the image range."),
    ("detection.blank_fraction", "Near-blank signal below",
     "Signal below this share of the range means almost nothing was stained. Such channels are left out "
     "of crosstalk checks."),
    ("detection.stain_index_low", "Weak stain index below",
     "The stain index compares positive pixels with the background. Below this value the stain is weak."),
    ("detection.positive_fraction_low", "Too few positive pixels below",
     "Share of tissue pixels above the automatic cut. Below this share there are too few positive pixels."),
    ("detection.saturation_high", "Saturated pixels above",
     "Share of tissue pixels at the top of the range. Above this share the antibody is too strong."),
    ("detection.background_high_fraction", "High background above",
     "Background above this share of the range is a concentration problem, so exposure is not changed."),
]
CROSSTALK_FIELDS = [
    ("crosstalk.null_percentile", "Reference percentile",
     "The cut-off is set from channel pairs that cannot show crosstalk, at this percentile."),
    ("crosstalk.margin", "Margin",
     "Added to that cut-off, for both the one-way spill check and the texture check."),
]
ADVANCED_FIELDS = [
    ("advanced.n_windows", "Number of windows",
     "How many tissue windows are sampled from the image."),
    ("advanced.window_um", "Window size (micrometers)",
     "Side length of each sampled square. Smaller windows see finer detail; larger windows average more tissue."),
    ("advanced.seed", "Window draw number (seed)",
     "Fixes which windows are drawn, so a rerun gives the same numbers."),
    ("advanced.highpass_sigma", "Fine-detail blur width (pixels)",
     "Blur width used to separate fine detail from cell-scale signal in the crosstalk check."),
    ("advanced.min_windows", "Minimum windows",
     "A run stops if fewer tissue windows than this are found."),
    ("advanced.n_preview_windows", "Preview windows",
     "How many example windows are saved per antibody for the tiles."),
]
EXPLANATIONS = {key: text for key, _label, text in DETECTION_FIELDS + CROSSTALK_FIELDS + ADVANCED_FIELDS}
# Names used in the "changed from the defaults" list, so a change reads as the field the user sees.
LABELS = {key: label for key, label, _text in DETECTION_FIELDS + CROSSTALK_FIELDS + ADVANCED_FIELDS}
LABELS["markers.control_markers"] = "Control markers"
LABELS["markers.exclusive_pairs"] = "Markers that should not overlap"
ERROR_COLUMN_PX = 260                      # reserved for the error beside a field, so an error never widens the window

INTRO = ("These cut-offs are the detection rules this tool uses to make its calls. They are lab-defined, "
         "not published. The tool gives directions only (raise, lower, compare, keep), never an amount.")
NEXT_RUN_NOTE = "Changes apply to the next run, not to a run already in progress."
RUN_ACTIVE_NOTE = "A run is in progress. Your changes apply to the next run, not this one."
DEFAULTS_TEXT = "All values are the defaults."
NEEDS_FIXING_TEXT = "Some entries need fixing."


def wheel_scroll(canvas: tk.Canvas, event) -> None:
    canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")


class _Row:
    """One row of a PairTable: its two text variables, its two cells, and whether the user has worked in it."""

    def __init__(self, frame, va, vb, ea, eb):
        self.frame, self.va, self.vb, self.ea, self.eb = frame, va, vb, ea, eb
        self.dirty = False          # the user typed in it
        self.touched = False        # the user typed in it and left it (or Save looked at it)


class PairTable(ttk.Frame):
    """Editable two-column list of marker names, with add and remove."""

    def __init__(self, master, title: str, hint: str, heads: tuple[str, str], height: int, on_change=None,
                 unique_left: bool = False):
        super().__init__(master)
        self._on_change = on_change
        self._unique_left = unique_left
        self._rows: list[_Row] = []
        self._muted = False
        self.columnconfigure(0, weight=1)
        ttk.Label(self, text=title, style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(self, text=hint, style="Muted.TLabel", wraplength=380, justify="left").grid(
            row=1, column=0, sticky="w", pady=(0, 4))
        head = ttk.Frame(self)
        head.grid(row=2, column=0, sticky="ew")
        head.columnconfigure((0, 1), weight=1, uniform="pair")
        ttk.Label(head, text=heads[0], style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(head, text=heads[1], style="Muted.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(head, text="", width=11).grid(row=0, column=2)      # room for the Remove buttons and scrollbar
        box = ttk.Frame(self)
        box.grid(row=3, column=0, sticky="nsew")
        box.columnconfigure(0, weight=1)
        self._nominal_height = height
        self._canvas = tk.Canvas(box, height=height, highlightthickness=1, highlightbackground="#cbd2d9",
                                 bg="#ffffff", borderwidth=0)
        bar = ttk.Scrollbar(box, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=bar.set)
        self._canvas.grid(row=0, column=0, sticky="nsew")
        bar.grid(row=0, column=1, sticky="ns")
        self._inner = ttk.Frame(self._canvas)
        self._inner.columnconfigure((0, 1), weight=1, uniform="pair")
        self._window = self._canvas.create_window((0, 0), window=self._inner, anchor="nw")
        self._inner.bind("<Configure>", lambda _e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>", lambda e: self._canvas.itemconfigure(self._window, width=e.width))
        self._canvas.bind("<MouseWheel>", self._wheel)
        self._inner.bind("<MouseWheel>", self._wheel)
        buttons = ttk.Frame(self)
        buttons.grid(row=4, column=0, sticky="w", pady=(4, 0))
        self.add_button = ttk.Button(buttons, text="Add row", command=self.add_row)
        self.add_button.pack(side="left")
        self.error_label = ttk.Label(self, text="", foreground=ERROR_COLOR, wraplength=380, justify="left")
        self.error_label.grid(row=5, column=0, sticky="w", pady=(4, 0))
        self.error_label.grid_remove()
        self._error = ""

    # ---- rows ------------------------------------------------------------------
    def _wheel(self, event) -> None:
        wheel_scroll(self._canvas, event)

    def _changed(self, *_args) -> None:
        if self._muted:
            return
        self.validate()
        if self._on_change:
            self._on_change()

    def _append_row(self, a: str = "", b: str = "") -> _Row:
        va, vb = tk.StringVar(master=self, value=a), tk.StringVar(master=self, value=b)
        frame = ttk.Frame(self._inner)
        frame.columnconfigure((0, 1), weight=1, uniform="cell")
        ea = ttk.Entry(frame, textvariable=va, width=14)
        eb = ttk.Entry(frame, textvariable=vb, width=14)
        gone = ttk.Button(frame, text="Remove", style="Link.TButton", width=7)
        ea.grid(row=0, column=0, sticky="ew", padx=(0, 4), pady=1)
        eb.grid(row=0, column=1, sticky="ew", padx=(0, 4), pady=1)
        gone.grid(row=0, column=2, pady=1)
        row = _Row(frame, va, vb, ea, eb)
        gone.configure(command=lambda r=row: self.remove_row(self._rows.index(r)))
        frame.grid(row=len(self._rows), column=0, columnspan=2, sticky="ew")
        self._rows.append(row)
        if len(self._rows) == 1:
            self.after_idle(self._snap_height)
        va.trace_add("write", lambda *_a, r=row: self._typed(r))
        vb.trace_add("write", lambda *_a, r=row: self._typed(r))
        for w in (frame, ea, eb, gone):
            w.bind("<MouseWheel>", self._wheel)
        for cell in (ea, eb):
            cell.bind("<FocusOut>", lambda _e, r=row: self.after_idle(self._left, r), add="+")
        return row

    def _snap_height(self) -> None:
        """The table shows whole rows: its height is a whole number of rows (never a half-clipped one at the bottom)."""
        try:
            if not self._rows:
                return
            self.update_idletasks()
            row_h = self._rows[0].frame.winfo_reqheight()
            if row_h >= 8:
                self._canvas.configure(height=max(2, int(self._nominal_height // row_h)) * row_h + 2)
        except tk.TclError:
            pass

    def add_row(self, a: str = "", b: str = "") -> None:
        """Add a row, scroll it into view and put the cursor in its first cell. No error is shown for it yet."""
        row = self._append_row(a, b)
        self._changed()
        self.update_idletasks()
        self._canvas.yview_moveto(1.0)
        row.ea.focus_set()

    def _typed(self, row: _Row) -> None:
        if not self._muted:
            row.dirty = True
        self._changed()

    def _left(self, row: _Row) -> None:
        """The user left a cell. When the cursor is outside both cells of that row, the row counts as finished."""
        if row not in self._rows or not row.dirty or row.touched:
            return
        try:
            now = self.focus_get()
        except (KeyError, tk.TclError):
            now = None
        if now in (row.ea, row.eb):
            return
        row.touched = True
        self._changed()

    def first_entry(self, index: int) -> ttk.Entry:
        return self._rows[index].ea

    def second_entry(self, index: int) -> ttk.Entry:
        return self._rows[index].eb

    def remove_row(self, index: int) -> None:
        if not 0 <= index < len(self._rows):
            return
        self._rows.pop(index).frame.destroy()
        for i, r in enumerate(self._rows):
            r.frame.grid(row=i, column=0, columnspan=2, sticky="ew")
        self._changed()

    def set_rows(self, rows) -> None:
        self._muted = True
        try:
            for r in self._rows:
                r.frame.destroy()
            self._rows = []
            for a, b in rows:
                self._append_row(str(a), str(b))
        finally:
            self._muted = False
        self._changed()

    def rows(self) -> list[tuple[str, str]]:
        return [(r.va.get().strip(), r.vb.get().strip()) for r in self._rows]

    # ---- checking --------------------------------------------------------------
    def validate(self, strict: bool = False) -> list[list[str]] | None:
        """The pairs as [name, name] lists, or None when any row is wrong. The problem is shown under the table,
        except an empty name in a row the user has not worked in yet: that shows only when `strict` (Save)."""
        pairs = self.rows()
        shown = ""
        problem = False
        seen: set[str] = set()
        for i, ((a, b), row) in enumerate(zip(pairs, self._rows), start=1):
            if strict:
                row.touched = True
            if not a or not b:
                problem = True
                if not shown and row.touched:
                    shown = f"Row {i} has an empty marker name. Fill in both names or remove the row."
            elif norm_name(a) == norm_name(b):
                problem = True
                shown = shown or f"Row {i} lists {a} twice. Use two different markers or remove the row."
            elif self._unique_left and norm_name(a) in seen:
                problem = True
                shown = shown or f"{a} is listed twice as a marker. Keep one row for it."
            seen.add(norm_name(a))
        self._fail(shown)
        return None if problem else [[a, b] for a, b in pairs]

    def _fail(self, text: str) -> None:
        self._error = text
        self.error_label.configure(text=text)
        if text:
            self.error_label.grid()
        else:
            self.error_label.grid_remove()

    def error_text(self) -> str:
        return self._error


class SettingsDialog(tk.Toplevel):
    """Edit the settings. `result` is the saved Settings, or None when the panel was cancelled or not saved."""

    def __init__(self, master, settings: Settings, path: Path, *, run_active: bool = False,
                 on_saved=None, on_close=None, screen_size: tuple[int, int] | None = None) -> None:
        super().__init__(master)
        build_style(self)                      # the ttk styles this panel uses (same ones the main window sets)
        self.configure(bg=BG)
        self.title("Settings")
        if master.winfo_viewable():            # a transient of a hidden window would be hidden too
            self.transient(master)
        self.resizable(False, False)
        self.result: Settings | None = None
        self.path = Path(path)
        self._base = copy.deepcopy(settings)
        self._on_saved = on_saved
        self._on_close = on_close
        self._advanced = False
        self._header = DEFAULTS_TEXT
        self._closed = False
        # The screen the panel must fit on (a test can pretend to have a small one).
        self._screen = screen_size or (self.winfo_screenwidth(), self.winfo_screenheight())
        self._wrap = max(300, min(860, self._screen[0] - 140))
        self.fields: dict[str, tk.StringVar] = {}
        self._errors: dict[str, str] = {}
        self._error_labels: dict[str, ttk.Label] = {}
        self._entries: dict[str, ttk.Entry] = {}
        self.protocol("WM_DELETE_WINDOW", self.request_close)

        # The form scrolls when the screen is short; the footer message and buttons stay in view below it.
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self._canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, bg=BG)
        self._scroll = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._scroll.set)
        self._canvas.grid(row=0, column=0, sticky="nsew")
        body = ttk.Frame(self._canvas, padding=(16, 14, 16, 8))
        self._body = body
        self._body_window = self._canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", self._body_resized)
        bottom = ttk.Frame(self, padding=(16, 4, 16, 12))
        self._bottom = bottom
        bottom.grid(row=1, column=0, columnspan=2, sticky="ew")
        bottom.columnconfigure(0, weight=1)

        ttk.Label(body, text=INTRO, style="Muted.TLabel", wraplength=self._wrap, justify="left").grid(
            row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(body, text=NEXT_RUN_NOTE, style="Muted.TLabel", wraplength=self._wrap, justify="left").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.run_note_label = ttk.Label(body, text="", foreground=ERROR_COLOR, wraplength=self._wrap, justify="left",
                                        font=(FONT, 10, "bold"))
        self.run_note_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.run_note_label.grid_remove()
        ttk.Label(body, text="Changed from the defaults", style="Section.TLabel").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.header_label = ttk.Label(body, text=DEFAULTS_TEXT, wraplength=self._wrap, justify="left")
        self.header_label.grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 8))

        left = ttk.Frame(body)
        left.grid(row=5, column=0, sticky="nw", padx=(0, 24))
        right = ttk.Frame(body)
        right.grid(row=5, column=1, sticky="nw")
        self._left_column, self._right_column = left, right
        left.columnconfigure(1, weight=0)

        row = 0
        row = self._group(left, row, "Detection", DETECTION_FIELDS, settings)
        row = self._group(left, row, "Crosstalk", CROSSTALK_FIELDS, settings, pad=12)
        # Advanced sits under the marker lists (the shorter column), so opening it never grows the window.
        self.advanced_button = ttk.Button(right, text="Show advanced settings", style="Link.TButton",
                                          command=self.toggle_advanced)
        self.advanced_button.grid(row=3, column=0, sticky="w", pady=(12, 0))
        self.advanced_frame = ttk.Frame(right)
        self.advanced_frame.grid(row=4, column=0, sticky="w")
        self._group(self.advanced_frame, 0, "Advanced", ADVANCED_FIELDS, settings)
        self.advanced_frame.grid_remove()

        ttk.Label(right, text="Markers", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.control_table = PairTable(
            right, "Control markers",
            "Each marker is checked for crosstalk against its control marker.",
            ("Marker", "Control marker"), height=150, unique_left=True)
        self.control_table.grid(row=1, column=0, sticky="nw", pady=(4, 0))
        self.exclusive_table = PairTable(
            right, "Markers that should not overlap",
            "Pairs that should not be stained in the same cells. Overlap is flagged.",
            ("Marker", "Marker"), height=110)
        self.exclusive_table.grid(row=2, column=0, sticky="nw", pady=(14, 0))

        self.footer_label = ttk.Label(bottom, text="", foreground=ERROR_COLOR, wraplength=self._wrap, justify="left")
        self.footer_label.grid(row=0, column=0, sticky="w")
        self.footer_label.grid_remove()
        buttons = ttk.Frame(bottom)
        buttons.grid(row=1, column=0, sticky="e", pady=(6, 0))
        self.save_button = ttk.Button(buttons, text="Save", style="Primary.TButton", command=self.save)
        self.reset_button = ttk.Button(buttons, text="Reset to defaults", command=self.reset)
        self.cancel_button = ttk.Button(buttons, text="Cancel", command=self.cancel)
        self.save_button.pack(side="left")
        self.reset_button.pack(side="left", padx=(8, 0))
        self.cancel_button.pack(side="left", padx=(8, 0))

        self._load(settings)
        for key, var in self.fields.items():
            var.trace_add("write", lambda *_a, k=key: self._edited(k))
        for table in (self.control_table, self.exclusive_table):
            table._on_change = self._refresh_header          # wired last: the header needs every widget to exist
        self.set_run_active(run_active)
        self._refresh_header()
        self._bind_wheel(self._body)
        self._canvas.bind("<MouseWheel>", self._wheel)
        self._baseline = self._snapshot()          # what the form held when it opened (or was last saved)
        self.bind("<Escape>", lambda _e: self.request_close())
        self._arrange_columns()
        self._place_on_screen()

    # ---- scrolling body ------------------------------------------------------------
    def _arrange_columns(self) -> None:
        """Two columns when they fit the screen; otherwise the marker lists go under the cut-offs."""
        self.update_idletasks()
        need = self._left_column.winfo_reqwidth() + self._right_column.winfo_reqwidth() + 24 + 32
        if need > self._screen[0] - 90:
            self._left_column.grid_configure(padx=0)
            self._right_column.grid_configure(row=6, column=0, columnspan=2, padx=0, pady=(14, 0))

    def _place_on_screen(self) -> None:
        """Open centered on the real screen so the buttons are never below the bottom edge."""
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        self.geometry(f"+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - 20)}")

    def _body_resized(self, _event=None) -> None:
        """Size the canvas to the form, up to what fits on the screen; scroll for the rest."""
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        want_h = self._body.winfo_reqheight()
        # Leave room for the buttons, the title bar and the taskbar: the window is never more than 85% of the screen
        # tall, so the Save row stays on screen whatever is open (the form scrolls instead).
        room = max(240, int(0.85 * self._screen[1]) - 40 - self._bottom.winfo_reqheight())
        width = min(self._body.winfo_reqwidth(), self._screen[0] - 70)
        self._canvas.configure(width=width, height=min(want_h, room))
        if want_h > room:
            self._scroll.grid(row=0, column=1, sticky="ns")
        else:
            self._scroll.grid_remove()

    def _wheel(self, event) -> None:
        wheel_scroll(self._canvas, event)

    def _bind_wheel(self, widget) -> None:
        """Scroll the form with the wheel wherever the pointer is. The marker tables keep their own scrolling."""
        for child in widget.winfo_children():
            if not child.bind("<MouseWheel>"):
                child.bind("<MouseWheel>", self._wheel)
            self._bind_wheel(child)

    def _reveal_first_error(self) -> None:
        """Scroll the form so the first wrong entry is in view."""
        target = next((self._error_labels[k] for k in self.fields if self._errors[k]), None)
        if target is None:
            target = next((t.error_label for t in (self.control_table, self.exclusive_table) if t.error_text()), None)
        if target is None:
            return
        self.update_idletasks()
        y, w = 0, target
        while w is not self._body and w is not None:
            y += w.winfo_y()
            w = w.master
        total = max(1, self._body.winfo_height())
        self._canvas.yview_moveto(max(0.0, (y - 60) / total))

    # ---- building ----------------------------------------------------------------
    def _group(self, parent, row: int, title: str, items, settings: Settings, pad: int = 0) -> int:
        ttk.Label(parent, text=title, style="Section.TLabel").grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(pad, 4))
        row += 1
        parent.columnconfigure(2, minsize=ERROR_COLUMN_PX)
        for key, label, text in items:
            var = tk.StringVar(master=self)
            self.fields[key] = var
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10))
            entry = ttk.Entry(parent, textvariable=var, width=9)
            entry.grid(row=row, column=1, sticky="w")
            err = ttk.Label(parent, text="", foreground=ERROR_COLOR, wraplength=250, justify="left")
            err.grid(row=row, column=2, sticky="w", padx=(8, 0))
            err.grid_remove()
            note = ttk.Label(parent, text=text, style="Muted.TLabel", wraplength=470, justify="left")
            note.grid(row=row + 1, column=0, columnspan=3, sticky="w", pady=(0, 6))
            self._entries[key] = entry
            self._error_labels[key] = err
            self._errors[key] = ""
            row += 2
        return row

    def _load(self, settings: Settings) -> None:
        for key, var in self.fields.items():
            group, name = key.split(".")
            var.set(str(getattr(getattr(settings, group), name)))
        self.control_table.set_rows(settings.markers.control_markers)
        self.exclusive_table.set_rows(settings.markers.exclusive_pairs)

    # ---- queries (also used by tests) ------------------------------------------------------
    def error_text(self, key: str) -> str:
        return self._errors.get(key, "")

    def error_label(self, key: str) -> ttk.Label:
        return self._error_labels[key]

    def field_entry(self, key: str) -> ttk.Entry:
        return self._entries[key]

    def explanation(self, key: str) -> str:
        return EXPLANATIONS.get(key, "")

    def header_text(self) -> str:
        return self._header

    def footer_text(self) -> str:
        return self.footer_label.cget("text")

    def run_note_text(self) -> str:
        return self.run_note_label.cget("text")

    def advanced_visible(self) -> bool:
        return self._advanced

    def visible_text(self) -> str:
        """Every label and button text in the panel, for wording checks."""
        out: list[str] = []

        def walk(w) -> None:
            for child in w.winfo_children():
                try:
                    text = child.cget("text")
                except tk.TclError:
                    text = ""
                if text:
                    out.append(str(text))
                walk(child)
        walk(self)
        return "\n".join(out)

    # ---- behavior ------------------------------------------------------------------------------
    def set_run_active(self, active: bool) -> None:
        if active:
            self.run_note_label.configure(text=RUN_ACTIVE_NOTE)
            self.run_note_label.grid()
        else:
            self.run_note_label.configure(text="")
            self.run_note_label.grid_remove()

    def toggle_advanced(self) -> None:
        self._advanced = not self._advanced
        if self._advanced:
            self.advanced_frame.grid()
            self.advanced_button.configure(text="Hide advanced settings")
        else:
            self.advanced_frame.grid_remove()
            self.advanced_button.configure(text="Show advanced settings")
        self.update_idletasks()
        self._body_resized()                 # the window is sized for the form again at once, never left taller than the screen
        self._place_on_screen()

    def _show_error(self, key: str, text: str) -> None:
        self._errors[key] = text
        label = self._error_labels[key]
        label.configure(text=text)
        if text:
            label.grid()
        else:
            label.grid_remove()

    def _edited(self, key: str) -> None:
        group, name = key.split(".")
        _value, error = field_error(group, name, self.fields[key].get())
        self._show_error(key, error)
        self._refresh_header()

    def _collect(self, show: bool) -> Settings | None:
        """Settings built from the form, or None when any entry is wrong (errors are shown when `show`)."""
        new = copy.deepcopy(self._base)
        bad = False
        for key, var in self.fields.items():
            group, name = key.split(".")
            value, error = field_error(group, name, var.get())
            if show:
                self._show_error(key, error)
            if error:
                bad = True
                if show and group == "advanced" and not self._advanced:
                    self.toggle_advanced()
            else:
                setattr(getattr(new, group), name, value)
        controls = self.control_table.validate(strict=show)
        exclusive = self.exclusive_table.validate(strict=show)
        if controls is None or exclusive is None:
            bad = True
        else:
            new.markers.control_markers = controls
            new.markers.exclusive_pairs = exclusive
        return None if bad else new

    def _refresh_header(self) -> None:
        current = self._collect(show=False)
        if current is not None:
            lines = format_changes(current, LABELS)
            self._header = "\n".join(lines) if lines else DEFAULTS_TEXT
        elif self._shows_an_error():
            self._header = NEEDS_FIXING_TEXT
        else:
            return                                   # only a row the user has not filled in yet: leave it be
        self.header_label.configure(text=self._header)

    def _shows_an_error(self) -> bool:
        return any(self._errors.values()) or bool(self.control_table.error_text()
                                                  or self.exclusive_table.error_text())

    def _set_footer(self, text: str) -> None:
        self.footer_label.configure(text=text)
        if text:
            self.footer_label.grid()
        else:
            self.footer_label.grid_remove()

    def reset(self) -> None:
        """Put every value back to the tonsil-calibrated defaults. Nothing is saved until Save."""
        self._load(default_settings())
        self._set_footer("")

    def save(self) -> None:
        new = self._collect(show=True)
        if new is None:
            self._set_footer("Some entries need fixing. They are marked in red. Nothing was saved.")
            self._reveal_first_error()
            return
        try:
            save_settings(new, self.path)
        except OSError as exc:
            self._set_footer("The settings could not be saved. Check that this folder can be written: "
                             f"{self.path.parent}\n{exc}")
            return
        self.result = new
        if self._on_saved:
            self._on_saved()
        self._close()

    def cancel(self) -> None:
        """The Cancel button: close and drop the edits (the button's own label is the question)."""
        self._close()

    # ---- unsaved edits ---------------------------------------------------------------------
    def _snapshot(self) -> tuple:
        return (tuple(var.get() for var in self.fields.values()),
                tuple(self.control_table.rows()), tuple(self.exclusive_table.rows()))

    def has_unsaved_changes(self) -> bool:
        return self._snapshot() != self._baseline

    def _confirm_discard(self) -> bool:
        """Ask "Discard changes?"; True means close without saving. Replaced by tests."""
        return ask_discard_changes(self)

    def request_close(self) -> None:
        """Escape or the window's close button: close at once when nothing changed, otherwise ask first."""
        if self._closed:
            return
        if self.has_unsaved_changes() and not self._confirm_discard():
            return                                   # Keep editing: the panel stays as it is
        self._close()

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        callback = self._on_close
        try:
            self.destroy()
        finally:
            if callback:
                callback()

