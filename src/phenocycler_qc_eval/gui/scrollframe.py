"""A frame that scrolls only when its contents do not fit.

Used where squeezing would make a picture or a table unreadable on a small or zoomed screen: the evidence pane and
the left column of the Crosstalk tab. The body is as tall as it needs to be (never shorter than the room), and a
slim scrollbar appears beside it only when the room is short.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .theme import CARD


class ScrollFrame(ttk.Frame):
    def __init__(self, master, style: str = "Card.TFrame") -> None:
        super().__init__(master, style=style)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self._canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, bg=CARD)
        self._bar = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._bar.set)
        self._canvas.grid(row=0, column=0, sticky="nsew")
        self.body = ttk.Frame(self._canvas, style=style)
        self._window = self._canvas.create_window((0, 0), window=self.body, anchor="nw")
        self._scrollable = False
        self._need = 0
        self._refit_pending = False
        self._canvas.bind("<Configure>", self._fit)
        self.body.bind("<Configure>", self._fit)
        self._canvas.bind("<MouseWheel>", self._wheel)

    # ---- what a test or a caller can ask ---------------------------------------------
    def scrollable(self) -> bool:
        """True while the contents are taller than the room, so the scrollbar is showing."""
        return self._scrollable

    def fits(self) -> bool:
        return not self._scrollable

    def content_height(self) -> int:
        return int(self._need)

    # ---- layout -----------------------------------------------------------------------
    def _fit(self, _event=None) -> None:
        avail_h, avail_w = self._canvas.winfo_height(), self._canvas.winfo_width()
        if avail_h < 2 or avail_w < 2:
            return
        need = self.body.winfo_reqheight()
        self._need = need
        scroll = need > avail_h + 1
        if scroll != self._scrollable:
            self._scrollable = scroll
            if scroll:
                self._bar.grid(row=0, column=1, sticky="ns")
            else:
                self._bar.grid_remove()
                self._canvas.yview_moveto(0)
            avail_w = self._canvas.winfo_width()
        height = max(need, avail_h)
        self._canvas.itemconfigure(self._window, width=avail_w, height=height)
        self._canvas.configure(scrollregion=(0, 0, avail_w, height))

    def refit(self) -> None:
        """Work out again, once the pending layout work is done, whether the contents fit. The canvas does not tell
        us when the body's requested size changes (the body has a fixed size inside it). Several calls in a row
        make one pass, and none of them forces a layout on the spot (that cost seconds when results arrived)."""
        if self._refit_pending:
            return
        self._refit_pending = True
        self.after_idle(self._refit_now)

    def _refit_now(self) -> None:
        self._refit_pending = False
        try:
            self._fit()                  # layout jobs queued before this one have already run
        except tk.TclError:
            pass

    def _wheel(self, event) -> None:
        if self._scrollable:
            self._canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def bind_wheel(self, widget=None) -> None:
        """Let the mouse wheel scroll while the pointer is over `widget` and everything inside it."""
        widget = widget or self.body
        widget.bind("<MouseWheel>", self._wheel, add="+")
        for child in widget.winfo_children():
            self.bind_wheel(child)
