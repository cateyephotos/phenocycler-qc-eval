"""Result tabs: Titration, Crosstalk, Figures."""

from __future__ import annotations

import functools
import os
from pathlib import Path
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

import pandas as pd

from ..advice import (
    CROSSTALK_CAVEAT, FOLLOWUP_ACTION, NO_PAIRS_FLAGGED, blank_markers, compare_guidance, control_marker_for,
    detection_settings_of, evidence_detail, exclusive_numbers, exclusive_sentence, exclusive_verdict, figure_title,
    flag_reason_text, followup_reading, fmt_value, kind_short, kind_text, pair_numbers, pair_verdict, severity,
    short_evidence, signal_level, spill_sentence, untested_channels,
)
from ..results import Results, antibody_preview_path, followup_figure_path, followup_preview_path, load_preview
from ..settings import default_settings
from .evidence import EvidenceCanvas, _Item
from .scene import (MIN_ANTIBODY_PX, MIN_BLANK_PX, MIN_IMAGE_PX, MIN_MESSAGE_PX, MIN_ROW_PX, UNREADABLE, Scene,
                    followup_sentence)
from .scrollframe import ScrollFrame
from .theme import FONT, px, wrap_to
from .tooltip import HeadingTips

NO_PREVIEW = "No preview images were saved for this run."
NO_FIGURES = "No figures were saved for this run."
_CHANGE_ORDER = {"RAISE": 0, "LOWER": 0, "CHECK": 1}  # everything else (Keep) sorts last
_ROW_TINT = {0: "#fdf1e3", 1: "#ffffff", 2: "#eef6ee"}  # change / compare / keep
# One glyph per call, so the direction shows without relying on the row color. The words in Next run are untouched.
_GLYPH = {"RAISE": "↑", "LOWER": "↓", "CHECK": "?", "OK": "✓"}
HEADING_TIPS = {
    "Antibody": "The antibody (marker) on that channel.",
    "Next run": "What to change at the next staining. A direction only, never an amount.",
    "Evidence": "The finding behind that direction, and how far past its cut-off it is (Strong, Moderate, Mild). "
                "Select a row to see the numbers and example tiles.",
}
PAIR_TIPS = {
    "Source": "The channel that seems to leak.",
    "Target": "The channel that picks the leak up.",
    "Likely cause": "Carryover: from the previous cycle. Same cycle: bleed-through from a channel imaged in the same cycle.",
    "Result": "Flagged: a number is past its cut-off. Not flagged: nothing is past a cut-off (a weak leak can still be "
              "missed). Not tested: a channel is almost blank, so the pair is left out.",
}
EXCLUSIVE_TIPS = {
    "Marker A": "First marker of a pair that should not be stained in the same cells.",
    "Marker B": "Second marker of that pair.",
    "Ratio": "Overlap divided by what chance gives. Below 1 is the expected direction. Select a row for the overlap "
             "and the chance figure.",
    "Result": "Overlap higher than expected: the ratio is 1 or more. As expected: it is below 1. Not tested: a "
              "channel is almost blank, so a ratio would only measure noise.",
}
# On a short window the overlap table says the same thing in fewer words (the pane beside it has the whole sentence).
COMPACT_WORDS = {"Overlap higher than expected": "Higher than expected", "Not tested: near-blank channel": "Not tested"}
def overlap_tint(lift) -> str:
    """The warning tint of an overlap row, paler the closer its ratio is to 1 (a full tint from 1.5 up)."""
    try:
        v = float(lift)
    except (TypeError, ValueError):
        v = 1.0
    w = 0.3 + 0.7 * min(1.0, max(0.0, (v - 1.0) / 0.5))
    base, tint = (255, 255, 255), (0xfd, 0xf1, 0xe3)
    r, g, b = (round(b0 + (t0 - b0) * w) for b0, t0 in zip(base, tint))
    return f"#{r:02x}{g:02x}{b:02x}"


_VERDICT_TINT = {"flagged": "#fdf1e3", "clear": "#ffffff", "untested": "#f1f3f5"}


# ---- what a background thread makes: a picture from a file (no Tk in here) --------------------------------------------
def _make_antibody(path, row, range_max, full_scale, level) -> Scene:
    try:
        return Scene.antibody(load_preview(path), row, range_max, full_scale=full_scale, level=level)
    except Exception:  # noqa: BLE001 - BadZipFile, OSError, ValueError, KeyError, EOFError ...
        return Scene.message_scene(UNREADABLE)


def _make_followup(path) -> Scene:
    try:
        return Scene.followup(load_preview(path))
    except Exception:  # noqa: BLE001
        return Scene.message_scene(UNREADABLE)


def _make_pair(path_a, path_b, a: str, b: str) -> Scene:
    try:
        return Scene.pair(load_preview(path_a), load_preview(path_b), (a, b), frozenset())
    except Exception:  # noqa: BLE001
        return Scene.message_scene(UNREADABLE)


def _make_image(path, title: str, subtitle: str) -> Scene:
    return Scene.image(Path(path), title, subtitle)


def _num(value, fmt: str) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "-"
    return "-" if v != v else format(v, fmt)


def _pct(value, digits: int) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "-"
    return "-" if v != v else f"{100 * v:.{digits}f}%"


CELL_PAD = 14       # pixels (at 100%) a cell needs around its text: the cell's own padding and a little slack
HEAD_PAD = 22       # the same for a heading (padding, and room for the sort arrow the style reserves)


def _tree_fonts(tree) -> tuple[tkfont.Font, tkfont.Font]:
    name = str(ttk.Style(tree).lookup("Treeview", "font") or "TkDefaultFont")
    try:
        cell = tkfont.Font(root=tree, font=name)           # a named font or a description like "{Segoe UI} 10"
    except tk.TclError:
        cell = tkfont.nametofont("TkDefaultFont", root=tree)
    return cell, tkfont.Font(root=tree, font=(FONT, 9, "bold"))


def column_needs(tree) -> dict[str, int]:
    """The width each column needs to show its heading and every cell whole, in pixels."""
    cell, head = _tree_fonts(tree)
    scale = max(px(tree, 100) / 100.0, 1.0)
    out: dict[str, int] = {}
    for n, cid in enumerate(tree["columns"]):
        need = head.measure(str(tree.heading(cid, "text"))) + HEAD_PAD * scale
        for iid in tree.get_children():
            need = max(need, cell.measure(str(tree.set(iid, cid))) + CELL_PAD * scale)
        out[cid] = int(need + 0.999)
    return out


def fit_columns(tree, floors: dict[str, int] | None = None) -> int:
    """Give every column the width its content needs (never less than its designed width), so no cell or heading is cut
    off. The one stretch column takes any spare width. Returns the width the whole table needs."""
    needs = column_needs(tree)
    shown = [c for c in tree["displaycolumns"]]
    shown = list(needs) if shown == ["#all"] else shown
    total = 0
    for cid, need in needs.items():
        width = max(need, int((floors or {}).get(cid, 0)))
        tree.column(cid, width=width, minwidth=width)
        if cid in shown:
            total += width
    glyph = int(tree.column("#0", "width")) if str(tree.cget("show")).find("tree") >= 0 else 0
    return total + glyph


def clipped_cells(tree) -> list[tuple[str, str, str]]:
    """(row, column heading, text) of every cell or heading whose text is wider than its column now (empty when the whole
    table is readable). A cell that is scrolled out of view horizontally still counts: it needs the width."""
    cell, head = _tree_fonts(tree)
    scale = max(px(tree, 100) / 100.0, 1.0)
    out = []
    for cid in tree["columns"]:
        width = int(tree.column(cid, "width"))
        heading = str(tree.heading(cid, "text"))
        if head.measure(heading) + 8 * scale > width:
            out.append(("", heading, heading))
        for iid in tree.get_children():
            text = str(tree.set(iid, cid))
            if cell.measure(text) + 8 * scale > width:
                out.append((iid, heading, text))
    return out


def _make_tree(parent, columns: list[tuple], height: int, glyph: bool = False) -> ttk.Treeview:
    """A Treeview with a vertical scrollbar.

    columns = (heading, width, anchor[, stretch]); one column absorbs spare width. Widths are given for 100%
    display scaling and follow the actual scaling; `fit_columns` raises a column to what its content needs. With
    `glyph` a narrow first column holds a direction symbol.
    """
    box = ttk.Frame(parent, style="Card.TFrame")
    box.columnconfigure(0, weight=1)
    box.rowconfigure(0, weight=1)
    ids = [f"c{i}" for i in range(len(columns))]
    tree = ttk.Treeview(box, columns=ids, show=("tree", "headings") if glyph else "headings", height=height,
                        selectmode="browse")
    if glyph:
        tree.heading("#0", text="", anchor="center")
        tree.column("#0", width=px(parent, 34), minwidth=px(parent, 30), anchor="center", stretch=False)
    for cid, col in zip(ids, columns):
        text, width, anchor = col[:3]
        stretch = bool(col[3]) if len(col) > 3 else False
        tree.heading(cid, text=text, anchor="w" if anchor == "w" else "e")
        # A column never shrinks below the width its text needs; a window that is too narrow gets a scrollbar.
        tree.column(cid, width=px(parent, width), minwidth=px(parent, width), anchor=anchor, stretch=stretch)
    tree.floors = {cid: px(parent, col[1]) for cid, col in zip(ids, columns)}      # type: ignore[attr-defined]
    bar = ttk.Scrollbar(box, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=bar.set)
    tree.grid(row=0, column=0, sticky="nsew")
    bar.grid(row=0, column=1, sticky="ns")
    hbar = ttk.Scrollbar(box, orient="horizontal", command=tree.xview)

    def _hset(first, last):
        hbar.set(first, last)
        if float(first) <= 0.0 and float(last) >= 1.0:
            hbar.grid_remove()
        else:
            hbar.grid(row=1, column=0, sticky="ew")
    tree.configure(xscrollcommand=_hset)
    tree.container = box  # type: ignore[attr-defined]
    return tree


def _same_folder(before: Results | None, after: Results) -> bool:
    """The two results come from the same run folder (compared as resolved paths)."""
    if before is None or before.folder is None or after.folder is None:
        return False
    try:
        return Path(before.folder).resolve() == Path(after.folder).resolve()
    except OSError:
        return False


def _control_map(r: Results) -> list:
    """The [marker, control marker] pairs the run used (the settings' map), else today's defaults."""
    markers = r.settings.get("markers") if isinstance(r.settings, dict) else None
    controls = markers.get("control_markers") if isinstance(markers, dict) else None
    return controls if isinstance(controls, list) else default_settings().markers.control_markers


class TitrationTab(ttk.Frame):
    COLUMNS = [("Antibody", 110, "w"), ("Next run", 285, "w"), ("Evidence", 300, "w", True)]
    MIN_TABLE_ROWS = 5
    PAGE_MIN_PX = 500           # the page is never shorter than this (at 100% scaling); a shorter window scrolls

    def __init__(self, master) -> None:
        super().__init__(master, style="Card.TFrame", padding=(0, 10, 0, 0))
        self.results: Results | None = None
        self._shown: str | None = None
        self._sig: tuple | None = None
        self._compact = False
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        # Table above, detail pane below, with a draggable sash between them.
        # On a short or zoomed window the whole page scrolls (table and evidence keep a usable size) instead of one of
        # them being squeezed to a few pixels.
        self.page = ScrollFrame(self)
        self.page.grid(row=0, column=0, sticky="nsew")
        self.split = ttk.PanedWindow(self.page.body, orient="vertical", height=px(self, self.PAGE_MIN_PX))
        self.split.pack(fill="both", expand=True)
        table_box = ttk.Frame(self.split, style="Card.TFrame")
        table_box.columnconfigure(0, weight=1)
        table_box.rowconfigure(0, weight=1)
        self.tree = _make_tree(table_box, self.COLUMNS, height=10, glyph=True)
        self.tree.container.grid(row=0, column=0, sticky="nsew")
        under = ttk.Frame(table_box, style="Card.TFrame")
        under.grid(row=1, column=0, sticky="ew", pady=(4, 2))
        under.columnconfigure(0, weight=1)
        self._under = under
        self._key = ttk.Label(
            under, style="CardMuted.TLabel", justify="left", wraplength=600,
            text=("Next run is what to change at the next staining. ↑ raise, ↓ lower, ? compare with "
                  "expected biology, ✓ keep. Strong, Moderate and Mild say how far past its cut-off a finding is."))
        self._key.grid(row=0, column=0, sticky="w")
        under.bind("<Configure>", self._rewrap_key)
        self._hide_keep_var = tk.BooleanVar(master=self, value=False)
        self.keep_toggle = ttk.Checkbutton(under, variable=self._hide_keep_var, command=self._hide_keep_clicked,
                                           style="Card.TCheckbutton")
        self.keep_toggle.grid(row=0, column=1, sticky="ne", padx=(12, 0))
        self.evidence = EvidenceCanvas(self.split)
        self._full_scale_var = tk.BooleanVar(master=self, value=False)
        self.full_scale_toggle = ttk.Checkbutton(self.evidence.toolbar, variable=self._full_scale_var,
                                                 command=self._full_scale_clicked, style="Card.TCheckbutton")
        self.full_scale_toggle.pack(side="left")
        self.split.add(table_box, weight=3)
        self.split.add(self.evidence, weight=2)
        self._sash_dragged = False            # the person moved the sash: it stays where they put it
        self.split.bind("<Configure>", self._place_split)
        self.split.bind("<ButtonRelease-1>", self._note_sash_drag, add="+")
        self._tips = HeadingTips(self.tree, HEADING_TIPS)
        for tag, group in (("change", 0), ("check", 1), ("keep", 2)):
            self.tree.tag_configure(tag, background=_ROW_TINT[group])
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

    def set_compact(self, on: bool) -> None:
        """A short window: three table rows (the table scrolls), no key line (the help window has it), a shorter page, so
        the evidence under the table shows its numbers and its tiles without scrolling. A table too wide for its pane
        drops its Evidence column (the pane repeats it)."""
        self._compact = bool(on)
        self.MIN_TABLE_ROWS = 3 if on else 5
        self.PAGE_MIN_PX = 330 if on else 500
        self.split.configure(height=px(self, self.PAGE_MIN_PX))
        if on:
            self._key.grid_remove()
        else:
            self._key.grid()
        self._fit_evidence_column()
        self._place_split()

    def _fit_evidence_column(self) -> None:
        cols = list(self.tree["columns"])
        width = self.tree.winfo_width()
        need = sum(int(self.tree.column(c, "width")) for c in cols) + int(self.tree.column("#0", "width"))
        hide = bool(getattr(self, "_compact", False)) and width > 100 and need > width - px(self, 20)
        want = cols[:-1] if hide else cols
        if list(self.tree["displaycolumns"]) != want and tuple(self.tree["displaycolumns"]) != ("#all",):
            self.tree["displaycolumns"] = want
        elif tuple(self.tree["displaycolumns"]) == ("#all",) and hide:
            self.tree["displaycolumns"] = want

    def _rewrap_key(self, event=None) -> None:
        """The key wraps before it meets the checkbox beside it, whatever the display scaling makes of the checkbox (and
        whatever its words are: they change with the number of antibodies to keep)."""
        width = event.width if event is not None else self._under.winfo_width()
        room = width - self.keep_toggle.winfo_reqwidth() - px(self, 28)
        wrap_to(self._key, max(px(self, 200), room))

    def _table_min_px(self) -> int:
        """Room the table needs to show its heading and MIN_TABLE_ROWS rows and the key line under it."""
        try:
            row_h = int(self.tk.call("ttk::style", "lookup", "Treeview", "-rowheight") or 24)
        except (tk.TclError, ValueError):
            row_h = 24
        return (self.MIN_TABLE_ROWS + 1) * row_h + self._under.winfo_reqheight() + 14

    def _note_sash_drag(self, event) -> None:
        try:
            if "sash" in str(self.split.identify(event.x, event.y)):
                self._sash_dragged = True
        except tk.TclError:
            pass

    def _place_split(self, _event=None) -> None:
        """Every time the tab's height changes (until the person drags the sash): the table gets room for at least five
        rows and the evidence the rest, so the picture and its axis labels fit whatever the banner above takes (it gains
        a line when a run finishes). A short pane scrolls inside instead of squeezing the table."""
        if self._sash_dragged or self.split.winfo_height() < 120:
            return
        height = self.split.winfo_height()
        share = 0.27 if self._compact else 0.385        # a short window: the table is small, the evidence needs the room
        want = max(int(height * share), min(self._table_min_px(), height - 40))
        try:
            if abs(int(self.split.sashpos(0)) - want) > 2:
                self.split.sashpos(0, want)
        except tk.TclError:
            pass

    # ---- the key, the heading tips and the two view options ---------------------------------
    def key_text(self) -> str:
        return str(self._key.cget("text"))

    def heading_tips(self) -> dict[str, str]:
        return dict(HEADING_TIPS)

    def _n_keep(self) -> int:
        if self.results is None:
            return 0
        calls = self.results.detection["call"].astype(str).str.upper()
        return int(sum(_CHANGE_ORDER.get(c, 2) == 2 for c in calls))

    def keep_toggle_text(self) -> str:
        n = self._n_keep()
        noun = "antibody" if n == 1 else "antibodies"
        return (f"Show the {n} {noun} to keep" if self._hide_keep_var.get()
                else f"Hide the {n} {noun} to keep")

    def full_scale_text(self) -> str:
        top = self.results.intensity_range if self.results is not None else 255
        return f"Show tiles on the full scale (0-{top:g})"

    def _refresh_options(self) -> None:
        nothing_yet = self.results is not None and len(self.results.detection) == 0
        if nothing_yet:                               # nothing to hide or rescale yet
            self.keep_toggle.grid_remove()
            self.evidence.show_toolbar(False)
        else:
            self.keep_toggle.grid()
            self.evidence.show_toolbar(True)
        self.keep_toggle.configure(text=self.keep_toggle_text())
        self.after_idle(self._rewrap_key)                 # its words changed width, so the key beside it re-wraps
        self.full_scale_toggle.configure(text=self.full_scale_text())
        if self._n_keep():
            self.keep_toggle.state(["!disabled"])
        else:
            self.keep_toggle.state(["disabled"])

    def set_hide_keep(self, hide: bool) -> None:
        """Hide (or show) the rows whose Next run is Keep; the others stay in the same order."""
        self._hide_keep_var.set(bool(hide))
        self._hide_keep_clicked()

    def _hide_keep_clicked(self) -> None:
        if self.results is not None:
            self.load(self.results)

    def set_full_scale(self, full: bool) -> None:
        self._full_scale_var.set(bool(full))
        self._full_scale_clicked()

    def _full_scale_clicked(self) -> None:
        if self._shown is not None:
            self._show(self._shown)
            self._prefetch_rows()

    # ---- the table -----------------------------------------------------------------------
    def _order(self, r: Results, detection) -> list[int]:
        """Rows to change first, the strongest finding first; then compare, then keep (CSV order inside those)."""
        det = r.detection
        score = {i: severity(det.iloc[i], r.saturation_checked, r.intensity_range, detection)[0] for i in range(len(det))}

        def key(i: int):
            group = _CHANGE_ORDER.get(str(det["call"].iloc[i]).upper(), 2)
            return (group, -score[i] if group == 0 else 0.0)
        return sorted(range(len(det)), key=key)       # sorted() is stable: CSV order breaks ties

    @staticmethod
    def _file_stamp(path) -> tuple:
        """A preview file's identity: its name, size and modification time (a rewritten file is a different picture)."""
        try:
            st = Path(path).stat() if path is not None else None
        except OSError:
            st = None
        return (str(path), None if st is None else (st.st_size, st.st_mtime_ns))

    def _signature(self, r: Results, detection) -> tuple:
        """Everything the table and its evidence depend on: a reload with the same signature changes nothing on screen."""
        det = r.detection
        cols = [c for c in ("channel", "call", "exposure", "next_run", "signal_p99_5", "stain_index",
                            "positive_fraction", "saturation_fraction") if c in det.columns]
        rows = tuple(tuple(str(v) for v in det[c]) for c in cols)
        previews = tuple(self._file_stamp(antibody_preview_path(r, str(c))) for c in det["channel"])
        return (str(r.folder), rows, previews, r.saturation_checked, r.intensity_range, repr(detection),
                bool(self._hide_keep_var.get()), repr(_control_map(r)))

    def load(self, r: Results) -> None:
        """Show the table. A reload of the run folder already on screen keeps the row the user was inspecting
        selected; a different run folder always starts on its first useful row. A reload that brings nothing new (the
        run finishing, with the same antibody results) leaves the table, the picture and the text exactly as they are."""
        detection = detection_settings_of(r.settings)     # the cut-offs this run used, not today's defaults
        sig = self._signature(r, detection)
        if sig == self._sig and self.tree.get_children() and _same_folder(self.results, r):
            self.results = r
            self._refresh_options()
            return
        keep = self._selected_channel() if _same_folder(self.results, r) else None
        self.results = r
        self._sig = sig
        self._shown = None
        self.tree.delete(*self.tree.get_children())
        det = r.detection
        order = self._order(r, detection)
        self._refresh_options()
        hide_keep = bool(self._hide_keep_var.get())
        first_useful: str | None = None
        names = list(det["channel"])
        controls = _control_map(r)
        for i in order:
            row = det.iloc[i]
            group = _CHANGE_ORDER.get(str(row["call"]).upper(), 2)
            if hide_keep and group == 2:
                continue
            evidence = short_evidence(row, r.saturation_checked, r.intensity_range, detection)
            if group == 0:
                evidence = f"{severity(row, r.saturation_checked, r.intensity_range, detection)[1]}. {evidence}"
                if first_useful is None and signal_level(row, r.intensity_range, detection) != "blank":
                    first_useful = str(i)
            elif group == 1:
                control = control_marker_for(row["channel"], controls, names)
                if control:
                    evidence += f"; compare with {control}"       # the row says what to compare, not only the pane
            self.tree.insert("", "end", iid=str(i), tags=(("change", "check", "keep")[group],),
                             text=_GLYPH.get(str(row["call"]).upper(), _GLYPH["OK"]),
                             values=(row["channel"], row["next_run"], evidence))
        fit_columns(self.tree, self.tree.floors)
        self._fit_evidence_column()
        children = self.tree.get_children()
        with self.evidence.batch():                  # one picture, drawn once
            if children:
                # The row the user was on, else the strongest row to change that has something to look at (a near-blank
                # channel teaches nothing as a first picture), else the first row.
                pick = next((c for c in children if keep is not None and str(self.tree.item(c, "values")[0]) == keep),
                            first_useful or children[0])
                self.tree.selection_set(pick)
                self.tree.focus(pick)
                self.tree.see(pick)
                self._show(pick)
            else:
                self.evidence.clear("Antibody results appear here as soon as they are ready."
                                    if r.status == "running" else "No antibody results to show.")
        self._prefetch_rows()

    def _selected_channel(self) -> str | None:
        sel = self.tree.selection()
        return str(self.tree.item(sel[0], "values")[0]) if sel else None

    def selected_channel(self) -> str | None:
        return self._selected_channel()

    def copy_table_text(self) -> str:
        """The table as tab-separated text, one row per antibody (Antibody, Next run, Evidence), as shown."""
        lines = ["\t".join(("Antibody", "Next run", "Evidence"))]
        for iid in self.tree.get_children():
            lines.append("\t".join(str(v) for v in self.tree.item(iid, "values")))
        return "\n".join(lines)

    def _facts(self, row: pd.Series, detection) -> list[tuple[str, str]]:
        r = self.results
        sat = "not checked" if r is not None and not r.saturation_checked \
            else _pct(row["saturation_fraction"], 2)
        rng = f" of {r.intensity_range:g}" if r is not None else ""
        return [
            ("Signal (brightest 0.5%)", f"{fmt_value(row['signal_p99_5'])}{rng}"),
            ("Stain index", _num(row["stain_index"], ".1f") + f" (weak below {detection.stain_index_low:g})"),
            ("Stained pixels", _pct(row["positive_fraction"], 1)),
            ("Saturated pixels", sat),
        ]

    def _row_plan(self, iid: str):
        """Everything one row's evidence needs, cheap to work out: its words, and what makes its picture."""
        r = self.results
        row = r.detection.iloc[int(iid)]
        detection = detection_settings_of(r.settings)
        text = evidence_detail(row, r.saturation_checked, r.intensity_range, detection, compare_hint=False)
        if str(row.get("call", "")).upper() == "CHECK":
            control = control_marker_for(row["channel"], _control_map(r), list(r.detection["channel"]))
            text += chr(10) * 2 + compare_guidance(row, r.saturation_checked, r.intensity_range, detection, control)
        level = signal_level(row, r.intensity_range, detection)
        if level == "blank":
            text += chr(10) * 2 + "Before raising, check that this antibody was added and this channel was imaged."
        header = (f"{row['channel']}: {row['next_run']}", text, self._facts(row, detection))
        path = antibody_preview_path(r, str(row["channel"]))
        owner = str(row["channel"])
        full = bool(self._full_scale_var.get())
        key = ("antibody", owner, full, level, self._file_stamp(path), float(r.intensity_range)) if path is not None else None
        make = functools.partial(_make_antibody, path, row, r.intensity_range, full, level)
        small = self._compact                       # a short window: the picture may be a little lower
        return header, key, make, owner, ((120 if small else MIN_BLANK_PX) if level == "blank"
                                          else (150 if small else MIN_ANTIBODY_PX))

    def _show(self, iid: str) -> None:
        """A row was chosen. In this very event the old words and picture are cleared and "Loading evidence for ..." is
        said (unless the picture was made ahead of time, which then appears at once); the picture is made in the
        background and appears with its words."""
        r = self.results
        if r is None:
            return
        self._shown = iid
        header, key, make, owner, min_px = self._row_plan(iid)
        if key is None:
            self.evidence.clear(NO_PREVIEW)
            self.evidence.set_header(*header)
            return
        self.evidence.request(key, owner, f"Loading evidence for {owner}...", make, header=header, hint=header,
                              kind="antibody", min_px=min_px)

    def _prefetch_rows(self) -> None:
        """Make the other rows' pictures ahead of time, the rows after the one on screen first."""
        ev = self.evidence
        if not (ev.ASYNC and ev.PREFETCH) or self.results is None:
            return
        kids = list(self.tree.get_children())
        if self._shown in kids:
            i = kids.index(self._shown)
            kids = kids[i + 1:] + kids[:i]
        items = []
        for iid in kids:
            header, key, make, owner, min_px = self._row_plan(iid)
            if key is not None:
                items.append(_Item(key, owner, make, header, "antibody", min_px))
        ev.prefetch(items)

    def _on_select(self, _event=None) -> None:
        sel = self.tree.selection()
        if sel and sel[0] != self._shown:
            self._show(sel[0])


class CrosstalkTab(ttk.Frame):
    # The verdict comes right after the names, so it is never the part that scrolls out of sight.
    PAIR_COLUMNS = [("Source", 60, "w"), ("Target", 60, "w"), ("Result", 150, "w"), ("Likely cause", 84, "w", True)]
    # The overlap and the chance figure are in the pane beside the table (Both positive, Chance alone would give).
    EXCLUSIVE_COLUMNS = [("Marker A", 66, "w"), ("Marker B", 66, "w"), ("Result", 150, "w"), ("Ratio", 48, "e", True)]
    LEFT_SHARE = 0.68        # the left column never takes more than this share of the tab (a narrower tab scrolls)
    _PHYSICAL = ("same_cycle_spectral", "adjacent_cycle_carryover")

    def pair_count(self) -> int:
        """Flagged pairs, counted as the headline counts them: a same-cycle pair listed in both directions is one."""
        seen = set()
        for iid, (s, t) in self._pairs.items():
            kind = str(self._rows[iid].get("kind", ""))
            seen.add(frozenset((s, t)) if kind == "same_cycle_spectral" else (s, t))
        return len(seen)

    def __init__(self, master, on_template=None) -> None:
        super().__init__(master, style="Card.TFrame", padding=(0, 10, 0, 0))
        self.results: Results | None = None
        self.on_template = on_template          # called by the template button (the app saves the CSV)
        self._pairs: dict[str, tuple[str, str]] = {}      # flagged pairs, by row id
        self._rows: dict[str, pd.Series] = {}             # every listed pair row, flagged or not
        self._shown: str | None = None
        self._syncing = False
        # The left column is as wide as its tables need (so no cell is cut off); the evidence takes the rest.
        self.columnconfigure(0, weight=0, minsize=px(self, 380))
        self.columnconfigure(1, weight=1, minsize=px(self, 120))     # never wider than what is left: no text runs off the edge
        self.rowconfigure(1, weight=1)
        self._left_need = px(self, 380)
        self._compact = False
        self.bind("<Configure>", self._fit_left_column, add="+")
        top = ttk.Frame(self, style="Card.TFrame")
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self._message = ttk.Label(top, text="", style="Caveat.TLabel", wraplength=700, justify="left")
        self._message.pack(side="left", anchor="w")
        # Offered when crosstalk is off for lack of a panel layout: saves a marker,cycle,fluor CSV to fill in.
        self.template_button = ttk.Button(top, text="Save a layout CSV template", command=self._template)

        self.left_scroll = ScrollFrame(self)
        self.left_scroll.grid(row=1, column=0, sticky="nsew", padx=(0, 12))
        left = self.left_scroll.body
        left.columnconfigure(0, weight=1)
        left.rowconfigure(6, weight=1)
        self.tree = _make_tree(left, self.PAIR_COLUMNS, height=5)
        self.tree.container.grid(row=0, column=0, sticky="ew")
        self._ex_blank: dict[str, list[str]] = {}          # near-blank markers of each overlap row, by row id
        self.caveat_label = ttk.Label(left, text=CROSSTALK_CAVEAT, style="Caveat.TLabel", wraplength=380,
                                      justify="left")
        self.caveat_label.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.untested_label = ttk.Label(left, text="", style="CardMuted.TLabel", wraplength=380, justify="left")
        self.untested_label.grid(row=2, column=0, sticky="ew", pady=(2, 0))
        self._show_all_var = tk.BooleanVar(master=self, value=False)
        self.show_all_toggle = ttk.Checkbutton(left, text="Also list pairs that were not flagged",
                                               variable=self._show_all_var, command=self._toggle_unflagged,
                                               style="Card.TCheckbutton")
        self.show_all_toggle.grid(row=3, column=0, sticky="w", pady=(4, 0))
        ttk.Label(left, text="Markers that should not overlap", style="CardTitle.TLabel").grid(
            row=4, column=0, sticky="w", pady=(14, 0))
        self._ratio_note = ttk.Label(
            left, text="Ratio is the overlap divided by what chance alone would give. "
            "Below 1 is the expected direction.", style="CardMuted.TLabel", wraplength=380,
            justify="left")
        self._ratio_note.grid(row=5, column=0, sticky="w", pady=(0, 4))
        left.bind("<Configure>", lambda e: [wrap_to(w, max(200, e.width - 10)) for w in
                                            (self._ratio_note, self.caveat_label, self.untested_label)])
        self.exclusive_tree = _make_tree(left, self.EXCLUSIVE_COLUMNS, height=8)
        self.exclusive_tree.container.grid(row=6, column=0, sticky="nsew")
        for tree in (self.tree, self.exclusive_tree):
            for tag, color in _VERDICT_TINT.items():
                tree.tag_configure(tag, background=color)
            tree.tag_configure("untested", foreground="#52606d")

        self.evidence = EvidenceCanvas(self)
        self.evidence.grid(row=1, column=1, sticky="nsew")
        self.evidence.room_hint = self._expected_room
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.exclusive_tree.bind("<<TreeviewSelect>>", self._on_exclusive_select)
        self._pair_tips = HeadingTips(self.tree, PAIR_TIPS)
        self._exclusive_tips = HeadingTips(self.exclusive_tree, EXCLUSIVE_TIPS)
        self.left_scroll.bind_wheel()

    def set_compact(self, on: bool) -> None:
        """A short window: the pair table drops its Likely cause column (the pane says it) so the evidence gets the room."""
        self._compact = bool(on)
        cols = list(self.tree["columns"])
        self.tree["displaycolumns"] = cols[:-1] if on else cols
        self.evidence.text_below = bool(on)
        if self.results is not None:
            self.load(self.results)               # the overlap table's words are shorter too
        else:
            self._fit_tables()

    def _expected_room(self) -> tuple[int, int] | None:
        """The room the evidence pane will have when this tab is opened, worked out from the tab that is open now (a tab
        nobody has opened has no size of its own to measure). Two pixels narrow on purpose: a picture a little narrower
        than its pane leaves white at the right; one a little wider would be cut off."""
        try:
            nb = self.master
            page = nb.nametowidget(nb.select())
            if page is self or not page.winfo_ismapped():
                return None
            width, height = page.winfo_width(), page.winfo_height()
            if width < 200 or height < 200:
                return None
            left = max(px(self, 380), min(self._left_need, int(width * self.LEFT_SHARE)))
            top = self._message.winfo_reqheight() + 10 + 6
            return (width - left - 2, height - top)
        except (tk.TclError, KeyError):
            return None

    def heading_tips(self) -> dict[str, str]:
        return dict(PAIR_TIPS)

    def exclusive_heading_tips(self) -> dict[str, str]:
        return dict(EXCLUSIVE_TIPS)

    def message_text(self) -> str:
        return str(self._message.cget("text"))

    def caveat_text(self) -> str:
        return str(self.caveat_label.cget("text"))

    def verdicts(self) -> list[tuple[str, str, str]]:
        """(source, target, result) of every pair row on show, top to bottom."""
        return [tuple(str(v) for v in (self.tree.item(i, "values")[0], self.tree.item(i, "values")[1],
                                       self.tree.item(i, "values")[2])) for i in self.tree.get_children()]

    def exclusive_verdicts(self) -> list[tuple[str, str, str]]:
        return [tuple(str(v) for v in (self.exclusive_tree.item(i, "values")[0], self.exclusive_tree.item(i, "values")[1],
                                       self.exclusive_tree.item(i, "values")[2]))
                for i in self.exclusive_tree.get_children()]

    def _template(self) -> None:
        if self.on_template is not None:
            self.on_template()

    # ---- loading -----------------------------------------------------------------------
    def set_show_unflagged(self, show: bool) -> None:
        self._show_all_var.set(bool(show))
        self._toggle_unflagged()

    def _toggle_unflagged(self) -> None:
        if self.results is not None:
            self.load(self.results)

    def load(self, r: Results) -> None:
        self.results = r
        self.tree.delete(*self.tree.get_children())
        self.exclusive_tree.delete(*self.exclusive_tree.get_children())
        self._pairs = {}
        self._rows = {}
        self._ex_blank = {}
        self._shown = None

        flagged = self._flagged_rows(r.crosstalk) if r.crosstalk_state == "on" else None
        if r.crosstalk_not_judged:
            msg = r.crosstalk_message          # the panel had too few control pairs; a template would not help
        elif r.crosstalk_state == "off":
            # Said once, in the banner ("Crosstalk: not checked."). Here: what to do about it, next to the button.
            msg = "No panel file was used. Choose one, or fill in a layout CSV template, and run again."
        elif r.crosstalk_state == "pending" or flagged is None:
            msg = ("Crosstalk is still being checked." if r.status == "running"
                   else "Crosstalk did not finish for this run.")
        elif flagged.empty:
            msg = NO_PAIRS_FLAGGED
        else:
            msg = "Flagged pairs, strongest first. Select one to see the evidence."
        self._message.configure(text=msg)
        off = r.crosstalk_state == "off"
        if off:
            if r.crosstalk_not_judged:
                self.template_button.pack_forget()
            else:
                self.template_button.pack(side="left", padx=(14, 0))
            for w in (self.tree.container, self.caveat_label, self.untested_label, self.show_all_toggle):
                w.grid_remove()          # no empty pair table when nothing was checked
        else:
            self.template_button.pack_forget()
            for w in (self.tree.container, self.caveat_label, self.show_all_toggle):
                w.grid()

        if flagged is not None:
            for n, (_, row) in enumerate(flagged.iterrows()):
                iid = str(n)
                self._pairs[iid] = (str(row["source"]), str(row["target"]))
                self._rows[iid] = row
                self._insert_pair(iid, row)
            if self._show_all_var.get():
                for n, row in enumerate(self._unflagged_rows(r.crosstalk)):
                    iid = f"u{n}"
                    self._rows[iid] = row
                    self._insert_pair(iid, row)
        blank = untested_channels(r.crosstalk) if not off else []
        self.untested_label.configure(
            text=("Not tested because the channel is almost blank: " + ", ".join(blank) + ".") if blank else "")
        if blank and not off:
            self.untested_label.grid()
        else:
            self.untested_label.grid_remove()
        self.tree.configure(height=max(2, min(len(self.tree.get_children()), 8)))  # no blank rows
        ex = r.exclusive_pairs
        if ex is not None:
            near_blank = blank_markers(r.detection, r.intensity_range, detection_settings_of(r.settings))
            for n, (_, row) in enumerate(ex.iterrows()):
                names = [m for m in (str(row["marker_a"]), str(row["marker_b"])) if m in near_blank]
                words, tag = exclusive_verdict(row.get("lift"), bool(names))
                self._ex_blank[str(n)] = names
                shown_words = COMPACT_WORDS.get(words, words) if self._compact else words
                row_tags = (tag,)
                if tag == "flagged":
                    # How far above chance it is decides how strong the tint is: a ratio of 1.06 must not look like a
                    # clear crosstalk flag. (Display only: the words and the ratio are what the run computed.)
                    row_tags = (f"overlap{n}",)
                    self.exclusive_tree.tag_configure(f"overlap{n}", background=overlap_tint(row.get("lift")))
                self.exclusive_tree.insert("", "end", iid=str(n), tags=row_tags, values=(
                    row["marker_a"], row["marker_b"], shown_words, "-" if names else _num(row.get("lift"), ".2f")))
            self.exclusive_tree.configure(height=max(2, min(len(ex), 10)))
        self._fit_tables()
        if off:
            empty = "No pairs to show."
        else:
            empty = "No flagged pairs to show."
        with self.evidence.batch():                   # one picture, drawn once
            self.evidence.clear("Select a pair to see the evidence." if self._pairs else empty)
            first = [i for i in self.tree.get_children() if i in self._pairs]
            if first:
                self._syncing = True
                self.tree.selection_set(first[0])
                self.tree.focus(first[0])
                self._syncing = False
                self._show(first[0])
        self._prefetch_pictures()

    # ---- widths: every cell and heading whole ----------------------------------------------------
    def _fit_tables(self) -> None:
        """Size every column to its content, then make the left column as wide as the wider table needs."""
        need = 0
        for tree in (self.tree, self.exclusive_tree):
            need = max(need, fit_columns(tree, tree.floors))
        bars = px(self, 44)                  # the table's own scroll bar, the left column's scroll bar, and their gaps
        self._left_need = need + bars + px(self, 12)
        self._fit_left_column()

    def _fit_left_column(self, _event=None) -> None:
        width = self.winfo_width()
        room = int(width * self.LEFT_SHARE) if width > 100 else self._left_need
        want = max(px(self, 380), min(self._left_need, room))
        if int(self.columnconfigure(0)["minsize"]) != want:
            self.columnconfigure(0, minsize=want)

    def clipped_cells(self) -> list[tuple[str, str, str, str]]:
        """Cells and headings of both tables that are wider than their column: (table, row, heading, text)."""
        out = []
        for name, tree in (("pairs", self.tree), ("overlap", self.exclusive_tree)):
            if name == "pairs" and not tree.container.winfo_manager():
                continue
            out += [(name, *c) for c in clipped_cells(tree)]
        return out

    def _insert_pair(self, iid: str, row: pd.Series) -> None:
        words, tag = pair_verdict(row, short=True)
        self.tree.insert("", "end", iid=iid, tags=(tag,), values=(
            row["source"], row["target"], words, kind_short(row.get("kind", ""))))

    @staticmethod
    def _flagged_rows(crosstalk: pd.DataFrame | None) -> pd.DataFrame | None:
        if crosstalk is None:
            return None
        if "flag" not in crosstalk.columns:
            return crosstalk.iloc[0:0]
        flag = crosstalk["flag"]
        mask = flag if flag.dtype == bool else flag.astype(str).str.lower().eq("true")
        rows = crosstalk[mask]
        if "spill" in rows.columns:
            rows = rows.sort_values("spill", ascending=False, kind="stable")
        return rows

    def _unflagged_rows(self, crosstalk: pd.DataFrame | None) -> list[pd.Series]:
        """Pairs the check looked at (or had to leave out) that were not flagged, tested ones first, strongest spill first."""
        if crosstalk is None or "flag" not in crosstalk.columns:
            return []
        flag = crosstalk["flag"]
        mask = flag if flag.dtype == bool else flag.astype(str).str.lower().eq("true")
        rows = crosstalk[~mask]
        if "kind" in rows.columns:
            rows = rows[rows["kind"].isin(self._PHYSICAL)]
        out = [row for _, row in rows.iterrows()]
        out.sort(key=lambda row: (pair_verdict(row)[1] == "untested", -float(row.get("spill", 0) or 0)))
        return out

    # ---- the evidence ------------------------------------------------------------------
    def _facts_and_text(self, iid: str, numbers_sentence: str, names) -> tuple[str, list[tuple[str, str]]]:
        """The three short lines at the top of the evidence pane: what to look for first, what the numbers say, what to
        do about it. The two numbers that decide a flag go in the row of figures; each fact is said once."""
        row = self._rows[iid]
        words, tag = pair_verdict(row)
        why = flag_reason_text(row.get("flag_reason", ""))
        parts = []
        if names is not None:
            parts.append(followup_reading(*names))
        if tag == "flagged":
            says = f"This pair is flagged because {why}." if why else "This pair is flagged."
            says += f" Likely cause: {kind_text(row.get('kind', '')).lower()}."
        elif tag == "untested":
            says = f"{words}."
        else:
            says = f"Not flagged. Kind of pair: {kind_text(row.get('kind', '')).lower()}."
        parts.append("What the numbers say: " + " ".join(x for x in (numbers_sentence, says, spill_sentence(row)) if x))
        if tag == "flagged":
            parts.append("What to do: " + FOLLOWUP_ACTION)
        return chr(10).join(parts), pair_numbers(row)

    def _followup_entry(self, source: str, target: str) -> dict | None:
        """The run's own numbers for a follow-up (from stain_qc_followups.json), in either direction."""
        for item in (self.results.followups if self.results else []):
            if {item.get("source"), item.get("target")} == {source, target}:
                return item
        return None

    def _names_from_run(self, iid: str):
        """(source, target, control) for the reading sentence when there is no preview: from the run's follow-up
        numbers, else just the two channels."""
        source, target = self._pairs[iid]
        item = self._followup_entry(source, target)
        if item is not None:
            control = item.get("control")
            return (item["source"], item["target"], str(control) if control else None)
        return (source, target, None)

    def _pair_header(self, iid: str, scene: Scene, mode: str, title: str = "") -> tuple:
        """The words above a flagged pair's picture: the three lines and the two numbers that decide the flag."""
        if mode == "npz":
            if scene.kind == "message":
                return ("", "", ())              # the preview could not be read: the message in the picture says so
            sentence, names, title = scene.header[1], scene.followup_names, scene.header[0]
        else:
            sentence, names = "", self._names_from_run(iid)
        text, facts = self._facts_and_text(iid, sentence, names)
        return (title, text, facts)

    def _pair_plan(self, iid: str):
        """(mode, key, make, owner, hint, min_px) for a flagged pair's picture, or None when no picture was saved.
        Cheap: looks for the files, reads none of them."""
        r = self.results
        source, target = self._pairs[iid]
        # A same-cycle pair has one follow-up, named by the larger-spill direction.
        for s, t in ((source, target), (target, source)):
            npz = followup_preview_path(r, s, t)
            if npz is not None:
                item = self._followup_entry(s, t)
                sentence = followup_sentence(s, t, item) if item else ""
                names = ((item.get("source", s), item.get("target", t), str(item["control"]) if item.get("control") else None)
                         if item else (s, t, None))
                text, facts = self._facts_and_text(iid, sentence, names)
                rows = 4 if names[2] else 3
                return ("npz", ("followup", s, t, TitrationTab._file_stamp(npz)), functools.partial(_make_followup, npz),
                        f"{s}->{t}", (f"{s} -> {t}", text, facts), MIN_ROW_PX * rows + 52)
        for s, t in ((source, target), (target, source)):
            png = followup_figure_path(r, s, t)
            if png is not None:
                title = f"{s} -> {t}"
                text, facts = self._facts_and_text(iid, "", self._names_from_run(iid))
                return ("png", ("followup-image", s, t, TitrationTab._file_stamp(png)),
                        functools.partial(_make_image, png, title, ""), f"image: {Path(png).name}",
                        (title, text, facts), MIN_IMAGE_PX)
        return None

    def _show(self, iid: str) -> None:
        self._shown = iid
        if iid not in self._pairs:                                   # a pair that was not flagged
            row = self._rows[iid]
            source, target = str(row["source"]), str(row["target"])
            self.evidence.clear("No follow-up panel is made for a pair that was not flagged.")
            text, facts = self._facts_and_text(iid, "", None)
            self.evidence.set_header(f"{source} -> {target}", text + " " + CROSSTALK_CAVEAT, facts)
            return
        plan = self._pair_plan(iid)
        if plan is None:
            source, target = self._pairs[iid]
            self.evidence.clear(f"No follow-up panel was saved for {source} -> {target}.")
            text, facts = self._facts_and_text(iid, "", self._names_from_run(iid))
            self.evidence.set_header(f"{source} -> {target}", text, facts)
            return
        mode, key, make, owner, hint, min_px = plan
        name = hint[0]
        self.evidence.request(key, owner, f"Loading evidence for {name}...", make,
                              header=lambda scene, i=iid, m=mode, n=name: self._pair_header(i, scene, m, n),
                              hint=hint, kind="followup" if mode == "npz" else "image", min_px=min_px)

    def _exclusive_plan(self, index: int):
        """(header, key, make, owner, min_px) of an overlap row; key None when it has no picture to make."""
        r = self.results
        row = r.exclusive_pairs.iloc[index]
        a, b = str(row["marker_a"]), str(row["marker_b"])
        names = self._ex_blank.get(str(index), [])
        words, _tag = exclusive_verdict(row.get("lift"), bool(names))
        header = (f"{a} and {b}: {words}", exclusive_sentence(row, names), exclusive_numbers(row, bool(names)))
        if names:
            return header, None, None, "", MIN_MESSAGE_PX
        pa, pb = antibody_preview_path(r, a), antibody_preview_path(r, b)
        if pa is None or pb is None:
            return header, None, None, "", MIN_MESSAGE_PX
        key = ("pair", a, b, TitrationTab._file_stamp(pa), TitrationTab._file_stamp(pb))
        return header, key, functools.partial(_make_pair, pa, pb, a, b), f"{a}+{b}", MIN_ROW_PX * 2 + 52

    def _prefetch_pictures(self) -> None:
        """Make the other pairs' pictures ahead of time (pairs first, then the overlap rows)."""
        ev = self.evidence
        if not (ev.ASYNC and ev.PREFETCH) or self.results is None:
            return
        items = []
        for iid in self.tree.get_children():
            if iid not in self._pairs:
                continue
            plan = self._pair_plan(iid)
            if plan is not None:
                mode, key, make, owner, hint, min_px = plan
                items.append(_Item(key, owner, make, hint, "followup" if mode == "npz" else "image", min_px))
        ex = self.results.exclusive_pairs
        for n in range(len(ex) if ex is not None else 0):
            header, key, make, owner, min_px = self._exclusive_plan(n)
            if key is not None:
                items.append(_Item(key, owner, make, header, "pair", min_px))
        ev.prefetch(items)

    def _on_select(self, _event=None) -> None:
        if self._syncing:
            return
        sel = self.tree.selection()
        if sel and self.results is not None and sel[0] != self._shown:
            self._syncing = True
            self.exclusive_tree.selection_remove(*self.exclusive_tree.selection())   # one table drives the pane
            self._syncing = False
            self._show(sel[0])

    @staticmethod
    def _is_blank(r: Results, marker: str, detection) -> bool:
        rows = r.detection[r.detection["channel"] == marker]
        return len(rows) > 0 and signal_level(rows.iloc[0], r.intensity_range, detection) == "blank"

    def _on_exclusive_select(self, _event=None) -> None:
        if self._syncing:
            return
        sel = self.exclusive_tree.selection()
        r = self.results
        if not sel or r is None or r.exclusive_pairs is None:
            return
        self._syncing = True
        self.tree.selection_remove(*self.tree.selection())
        self._syncing = False
        self._shown = None
        index = int(sel[0])
        row = r.exclusive_pairs.iloc[index]
        a, b = str(row["marker_a"]), str(row["marker_b"])
        header, key, make, owner, min_px = self._exclusive_plan(index)
        if key is None:
            # A near-blank channel (a plain sentence, no ratio, no tiles: they would show only noise), or nothing saved.
            if self._ex_blank.get(str(index)):
                self.evidence.clear("No tiles are drawn for a channel with little or no signal.", top=True)
            else:
                self.evidence.clear("No tiles were saved for these two markers.")
            self.evidence.set_header(*header)
            return
        self.evidence.request(key, owner, f"Loading evidence for {a} and {b}...", make, header=header, hint=header,
                              kind="pair", min_px=min_px)


class FiguresTab(ttk.Frame):
    HINT = "Double-click the picture to open it full size."

    def __init__(self, master) -> None:
        super().__init__(master, style="Card.TFrame", padding=(0, 10, 0, 0))
        self.results: Results | None = None
        self._files: list[Path] = []
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        side = ttk.Frame(self, style="Card.TFrame")
        side.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        side.rowconfigure(0, weight=1)
        self.tree = _make_tree(side, [("Figure", 260, "w", True)], height=12)
        self.tree.container.grid(row=0, column=0, sticky="nsew")
        self.evidence = EvidenceCanvas(self)
        self.evidence.grid(row=0, column=1, sticky="nsew")
        bar = ttk.Frame(self, style="Card.TFrame")
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self._hint = ttk.Label(bar, text=self.HINT, style="CardMuted.TLabel")
        self._hint.pack(side="left")
        self.open_message = ttk.Label(bar, text="", style="CardMuted.TLabel", foreground="#b42318")
        self.open_message.pack(side="left", padx=(12, 12))
        self.report_button = ttk.Button(bar, text="Open full report", command=self.open_report)
        self.report_button.pack(side="right")
        self.figure_button = ttk.Button(bar, text="Open this figure full size", command=self.open_figure)
        self.figure_button.pack(side="right", padx=(0, 8))
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        # The picture opens full size on a double-click: the heat map has 31 labels a side and is hard to read here.
        for widget in (self.evidence.canvas.get_tk_widget(), self.tree):
            widget.bind("<Double-Button-1>", self._double_clicked, add="+")
        self.evidence.canvas.get_tk_widget().configure(cursor="hand2")

    def _double_clicked(self, _event=None) -> None:
        self.open_figure()

    def hint_text(self) -> str:
        return str(self._hint.cget("text"))

    def names(self) -> list[str]:
        return [p.name for p in self._files]

    def count(self) -> int:
        """Figures listed (the number the tab's title shows)."""
        return len(self._files)

    def titles(self) -> list[str]:
        return [str(self.tree.item(i, "values")[0]) for i in self.tree.get_children()]

    def load(self, r: Results) -> None:
        self.results = r
        # The follow-up panels are on the Crosstalk tab; this list keeps the overview figures.
        self._files = [p for p in r.figures if not p.name.startswith("followup_")]
        self.tree.delete(*self.tree.get_children())
        self.open_message.configure(text="")
        for i, p in enumerate(self._files):
            self.tree.insert("", "end", iid=str(i), values=(figure_title(p.name),))
        self.report_button.configure(state="normal" if r.report else "disabled")
        self.figure_button.configure(state="disabled")
        self.evidence.clear("Select a figure." if self._files else NO_FIGURES)

    def _on_select(self, _event=None) -> None:
        sel = self.tree.selection()
        if sel:
            p = self._files[int(sel[0])]
            title = figure_title(p.name)
            self.evidence.request(("image", str(p), TitrationTab._file_stamp(p)), f"image: {p.name}",
                                  f"Loading {title}...", functools.partial(_make_image, p, title, p.name),
                                  hint=(title, p.name, ()), kind="image", min_px=MIN_IMAGE_PX)
            self.figure_button.configure(state="normal")
            self.open_message.configure(text="")

    def _open(self, path: Path | None, what: str) -> None:
        if path is None:
            self.open_message.configure(text=f"There is no {what} to open.")
            return
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
            self.open_message.configure(text="")
        except (OSError, AttributeError) as exc:
            self.open_message.configure(text=f"Could not open the {what}: {exc}")

    def open_report(self) -> None:
        self._open(self.results.report if self.results else None, "report")

    def open_figure(self) -> None:
        sel = self.tree.selection()
        self._open(self._files[int(sel[0])] if sel else None, "figure")
