"""A small hover tooltip whose text is asked for each time it is about to show."""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import Callable

from .theme import FONT, INK


class Tooltip:
    def __init__(self, widget: tk.Misc, text_fn: Callable[[], str], delay_ms: int = 450) -> None:
        self.widget = widget
        self.text_fn = text_fn
        self.delay_ms = delay_ms
        self._job: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._enter, add="+")
        widget.bind("<Leave>", self._leave, add="+")
        widget.bind("<ButtonPress>", self._leave, add="+")
        widget.bind("<Destroy>", self._leave, add="+")

    def _enter(self, _event=None) -> None:
        self._leave()
        self._job = self.widget.after(self.delay_ms, self.show)

    def _leave(self, _event=None) -> None:
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None

    def show(self) -> None:
        self._job = None
        try:
            if not self.widget.winfo_exists():
                return
        except tk.TclError:
            return
        text = self.text_fn()
        if not text or self._tip is not None:
            return
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        tip.wm_geometry(f"+{x}+{y}")
        tk.Label(tip, text=text, justify="left", bg="#fffbe6", fg=INK, relief="solid", borderwidth=1,
                 font=(FONT, 9), padx=8, pady=5).pack()
        self._tip = tip


class HeadingTips:
    """Hover tips for the column headings of a Treeview: tips = {heading text: sentence}.

    A Treeview has no per-heading tooltip of its own, so this watches the pointer, works out which heading it
    is over, and shows the sentence after a short delay."""

    def __init__(self, tree, tips: dict[str, str], delay_ms: int = 450) -> None:
        self.tree = tree
        self.tips = dict(tips)
        self.delay_ms = delay_ms
        self._job: str | None = None
        self._tip: tk.Toplevel | None = None
        self._col: str | None = None
        tree.bind("<Motion>", self._move, add="+")
        tree.bind("<Leave>", self._hide, add="+")
        tree.bind("<ButtonPress>", self._hide, add="+")
        tree.bind("<Destroy>", self._hide, add="+")

    def text_at(self, x: int, y: int) -> str:
        """The tip for the heading under (x, y) in tree coordinates, or ''."""
        try:
            region = self.tree.identify_region(x, y)
            col = self.tree.identify_column(x)
            if region == "heading":
                return self.tips.get(str(self.tree.heading(col, "text")), "")
            if region == "cell":
                return self.clipped_cell_text(self.tree.identify_row(y), col)
        except tk.TclError:
            pass
        return ""

    def clipped_cell_text(self, iid: str, col: str) -> str:
        """The whole text of a cell too wide for its column, so a long cell can still be read in full (else '')."""
        if not iid or col in ("", "#0"):
            return ""
        try:
            text = str(self.tree.item(iid, "values")[int(col[1:]) - 1])
            name = str(ttk.Style(self.tree).lookup("Treeview", "font") or "TkDefaultFont")
            font = tkfont.Font(root=self.tree, font=name)          # a named font or a description like "{Segoe UI} 10"
            return text if font.measure(text) + 12 > int(self.tree.column(col, "width")) else ""
        except (tk.TclError, ValueError, IndexError):
            return ""

    def _move(self, event) -> None:
        text = self.text_at(event.x, event.y)
        col = (self.tree.identify_row(event.y) + self.tree.identify_column(event.x)
               + self.tree.identify_region(event.x, event.y)) if text else None
        if col == self._col:
            return
        self._hide()
        self._col = col
        if text:
            self._job = self.tree.after(self.delay_ms, lambda: self._show(text, event.x_root, event.y_root))

    def _hide(self, _event=None) -> None:
        self._col = None
        if self._job is not None:
            try:
                self.tree.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None

    def _show(self, text: str, x_root: int, y_root: int) -> None:
        self._job = None
        try:
            if not self.tree.winfo_exists() or self._tip is not None:
                return
            tip = tk.Toplevel(self.tree)
        except tk.TclError:
            return
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x_root + 10}+{y_root + 18}")
        tk.Label(tip, text=text, justify="left", bg="#fffbe6", fg=INK, relief="solid", borderwidth=1,
                 font=(FONT, 9), padx=8, pady=5, wraplength=320).pack()
        self._tip = tip
