"""The pane that shows the evidence behind a result: words above, a picture below.

The picture is made in a background thread (scene.py builds it, renderpool.py draws it with matplotlib's Agg renderer, no
pyplot, no Tk) and the window only swaps a finished picture in on the Tk thread. So a click on a row does two things:

1. at once, in the click's own event: the old words and picture are cleared and the pane says "Loading evidence for
   <name>..." (so the old row's evidence is never read as the new row's);
2. when the picture is ready (immediately when it was made ahead of time, about a second otherwise), the words and the
   picture appear together.

Pictures for the other rows are made ahead of time, in the background, at the size the pane has now and kept (as small
PNG files in memory), so most clicks find theirs ready. A picture is drawn at exactly the pixels it will occupy, at the
display scaling in use, so it is sharp; a resize keeps the old picture on screen until the new size is ready.

The whole pane scrolls (and only then) when the room is shorter than the picture needs.
"""

from __future__ import annotations

import copy
import gc
import math
import queue
import time
import tkinter as tk
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import font as tkfont
from tkinter import ttk
from typing import Callable

import numpy as np
import pandas as pd

from ..results import load_preview
from .renderpool import POOL, Job, Result, RenderPool
from .scene import (MIN_ANTIBODY_PX, MIN_BLANK_PX, MIN_IMAGE_PX, MIN_MESSAGE_PX, MIN_ROW_PX, UNREADABLE, Scene,
                    _MUTED)
from .scrollframe import ScrollFrame
from .theme import px, ui_factor, wrap_to

DEFAULT_SIZE = (640, 300)          # when a pane has never been laid out
WIDTH_SLACK_UP = 2                 # a picture may be this much wider than its pane...
WIDTH_SLACK_DOWN = 24              # ...or this much narrower (white to the right)
HEIGHT_SLACK_UP = 6                # and this much taller than the room (a few pixels of scrolling)...
PICTURE_MARGIN_PX = 26             # pictures are made this much shorter than their room, so a banner that gains a line (the finish
                                   # note) or a few pixels of layout still leaves every picture made ahead usable
HEIGHT_SLACK_DOWN = 60             # ...or this much shorter (white below)


@dataclass
class _Want:
    """What the pane was last asked to show."""
    token: int
    key: tuple
    owner: str
    loading: str
    make: Callable[[], Scene]
    header: Callable[[Scene], tuple] | tuple | None = None
    hint: tuple | None = None          # (title, subtitle, facts) the header will hold: sizes the picture beforehand
    kind: str = "picture"
    min_px: int = MIN_ANTIBODY_PX


@dataclass
class _Item:
    """A picture to make ahead of time."""
    key: tuple
    owner: str
    make: Callable[[], Scene]
    hint: tuple | None
    kind: str
    min_px: int


class _CanvasShim:
    """What the rest of the program (and a few tests) knew as the matplotlib canvas: the widget the picture is on, and a
    way to draw the picture now."""

    def __init__(self, owner: "EvidenceCanvas") -> None:
        self._owner = owner

    def get_tk_widget(self) -> tk.Canvas:
        return self._owner.pic

    def draw(self) -> None:
        self._owner._draw_current_now()

    def get_renderer(self):
        return self._owner._renderer()


class EvidenceCanvas(ttk.Frame):
    ASYNC = True                  # False: build and draw on the calling thread (the test suite's default)
    PREFETCH = True               # make the other rows' pictures ahead of time (async only)
    PREFETCH_WAIT_MS = 400        # the pane must keep its size this long before pictures are made for it
    RESIZE_WAIT_MS = 250
    CACHE_MAX = 260

    def __init__(self, master) -> None:
        super().__init__(master, style="Card.TFrame")
        self._title = ""
        self._facts: dict[str, str] = {}
        self._fact_items: list[tuple[str, str]] = []
        self._facts_per_row = 0
        self._fact_labels: list[tuple[ttk.Label, ttk.Label]] = []
        self._shown_parts: tuple[bool, bool, bool] = (True, False, False)
        self._font_scale: float | None = None
        self._font_pair = None
        self._header_memo: dict[tuple, int] = {}
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.scroll = ScrollFrame(self)
        self.scroll.grid(row=0, column=0, sticky="nsew")
        body = self.scroll.body
        self.header = ttk.Frame(body, style="Card.TFrame")
        self.header.pack(fill="x")
        self._head_title = ttk.Label(self.header, text="", style="CardTitle.TLabel",
                                     wraplength=600, justify="left")
        self._head_sub = ttk.Label(self.header, text="", style="Card.TLabel",
                                   wraplength=600, justify="left")
        self._facts_frame = ttk.Frame(self.header, style="Card.TFrame")
        self.header.bind("<Configure>", self._rewrap)
        # A slim row for a view option that belongs to the picture (empty, and not shown, unless a tab asks).
        self.toolbar = ttk.Frame(body, style="Card.TFrame")
        self._toolbar_pinned = False
        self._caption = ttk.Label(self.toolbar, text="", style="CardMuted.TLabel", justify="left", wraplength=500)
        self._caption.pack(side="right", padx=(12, 0))
        # The picture: one image on a plain canvas. Its height is asked for by the picture itself; it takes the rest.
        self.pic = tk.Canvas(body, highlightthickness=0, borderwidth=0, bg="white", height=px(self, MIN_MESSAGE_PX))
        self.pic.pack(fill="both", expand=True)
        # On a short window a tab can ask for its explanatory sentences under the picture, so the numbers and the tiles
        # come first and the words that explain them are what scrolls.
        self.text_below = False
        self._tail_sub = ttk.Label(body, text="", style="Card.TLabel", wraplength=600, justify="left")
        self.canvas = _CanvasShim(self)
        # Hidden labels that tell what height the words above a picture will have, before they are put on screen.
        self._m_title = ttk.Label(self, style="CardTitle.TLabel", justify="left")
        self._m_sub = ttk.Label(self, style="Card.TLabel", justify="left")
        self._m_small = ttk.Label(self, text="Xg", style="CardMuted.TLabel")
        self._m_big = ttk.Label(self, text="Xg", style="CardValue.TLabel")
        self._m_cap = ttk.Label(self, style="CardMuted.TLabel", justify="left")

        self._batch = 0
        self._hold = False                    # True while the page is built under a cover: no work until released
        self._token = 0
        self._want: _Want | None = None       # what the pane was last asked to show
        self._loading = False
        self._scene: Scene = Scene.message_scene("Select a row to see the evidence.")
        self._photo: tk.PhotoImage | None = None
        self._applied_key: tuple | None = None
        self._applied_size: tuple[int, int] | None = None
        self._applied_hint: tuple | None = None
        self._applied_kind = "picture"
        self._drawn_owner: str | None = None
        self._cache: "OrderedDict[tuple, list[tuple[int, int, bytes, Scene]]]" = OrderedDict()
        self._caption_for: dict[str, str] = {}          # the last caption of each kind: sizes the toolbar beforehand
        self._last_room: tuple[int, int] | None = None
        self._sink: "queue.SimpleQueue[Result]" = queue.SimpleQueue()
        self._poll_job: str | None = None
        self._resize_job: str | None = None
        self._prefetch_job: str | None = None
        self._prefetch_items: list[_Item] = []
        self._dead = False
        self._text_item: int | None = None
        self._resize_times: list[float] = []             # when the pane last followed a change of its room
        self._applied_log: list[tuple] = []             # every picture put on screen (a test checks none is stale)
        self.scroll._canvas.bind("<Configure>", self._on_room, add="+")
        self.pic.bind("<Configure>", self._place_message, add="+")
        self.pic.bind("<MouseWheel>", self.scroll._wheel, add="+")
        self.bind("<Map>", self._on_mapped, add="+")
        self.pic.bind("<Map>", self._on_mapped, add="+")
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.scroll.bind_wheel(self.header)
        self.clear("Select a row to see the evidence.")
        self._warm_up()

    # ---- the pieces a caller or a test can ask --------------------------------------------------------------------
    @property
    def figure(self):
        return self._scene.ensure_figure()

    @property
    def _margins(self):
        self._scene.ensure_figure()
        return self._scene.margins

    @property
    def _followup_rows(self):
        return self._scene.followup_rows

    @property
    def _pair_rows(self):
        return self._scene.pair_rows

    @property
    def _tile_count(self):
        return self._scene.tile_count

    @property
    def _draw_pending(self) -> bool:
        """A picture was asked for and is not on screen yet."""
        want = self._want
        waiting_picture = self._scene.message is None and self._applied_size is None     # words up, picture not drawn yet
        return bool(want is not None and (self._loading or self._applied_key != want.key or waiting_picture))

    def scrollable(self) -> bool:
        return self.scroll.scrollable()

    def fits(self) -> bool:
        return self.scroll.fits()

    def is_loading(self) -> bool:
        """True from the click until the picture and its words arrive."""
        return self._loading

    def has_picture(self) -> bool:
        """A picture (or a message) is on screen: the pane is never blank."""
        return bool(self.pic.find_all())

    def picture_size(self) -> tuple[int, int] | None:
        """Pixels of the picture on screen (None for a message or while loading)."""
        return self._applied_size

    def shown_log(self) -> list[tuple]:
        """(owner, key) of every picture swapped in so far, oldest first."""
        return list(self._applied_log)

    def min_row_px(self) -> float:
        """Height in pixels of one row of tiles as drawn now (the strip of an antibody counts as one row)."""
        scene = self._scene
        fig = scene.ensure_figure()
        rows = max(len(scene.followup_rows), len(scene.pair_rows), 1)
        axes = [a for a in fig.axes if a.images]
        if not axes:
            return 0.0
        scene.apply_margins()
        height = axes[0].get_position().height * fig.get_figheight() * fig.dpi
        return float(height / rows)

    def show_toolbar(self, shown: bool = True) -> None:
        """A tab that puts its own option in the toolbar keeps the toolbar on screen (or takes it away)."""
        self._toolbar_pinned = bool(shown)
        self._sync_toolbar()

    def set_caption(self, text: str) -> None:
        """One muted line about the picture (what the tiles show), beside the toolbar options and not inside
        the picture, where it would take height from the tiles."""
        if text != str(self._caption.cget("text")):
            self._caption.configure(text=text)
        self._sync_toolbar()

    def caption_text(self) -> str:
        return str(self._caption.cget("text"))

    def _sync_toolbar(self) -> None:
        if self._toolbar_pinned or self.caption_text():
            if not self.toolbar.winfo_manager():
                self.toolbar.pack(fill="x", before=self.pic)
        else:
            self.toolbar.pack_forget()

    # ---- header text ---------------------------------------------------------
    def _rewrap(self, event) -> None:
        wrap = max(200, event.width - 16)
        wrap_to(self._head_title, wrap)
        wrap_to(self._head_sub, wrap)
        wrap_to(self._tail_sub, wrap)
        wrap_to(self._caption, max(200, event.width - 330))
        self._place_facts(event.width)
        self.after_idle(self._refit_later)            # wrapping changed the header's height

    def _refit_later(self) -> None:
        try:
            self.scroll.refit()
        except tk.TclError:
            pass

    def set_header(self, title: str = "", subtitle: str = "", facts=()) -> None:
        """Title (bold), wrapped sentences, and small (label, value) facts. Only what changed is touched: a click swaps
        the words in a few milliseconds, where building the header again took a tenth of a second."""
        facts = [(str(n), str(v)) for n, v in facts]
        for label, text in ((self._head_title, title), (self._head_sub, subtitle)):
            if str(label.cget("text")) != text:
                label.configure(text=text)
        below = bool(self.text_below and subtitle)
        if str(self._tail_sub.cget("text")) != (subtitle if below else ""):
            self._tail_sub.configure(text=subtitle if below else "")
        if below != bool(self._tail_sub.winfo_manager()):
            if below:
                self._tail_sub.pack(fill="x", anchor="w", pady=(6, 4), after=self.pic)
            else:
                self._tail_sub.pack_forget()
        if below:
            subtitle = ""
            self._head_sub.configure(text="")
        want = (bool(title), bool(subtitle), bool(facts))
        if want != self._shown_parts:
            for w in (self._head_title, self._head_sub, self._facts_frame):
                if w.winfo_manager():
                    w.pack_forget()
            if title:
                self._head_title.pack(fill="x", anchor="w")
            if subtitle:
                self._head_sub.pack(fill="x", anchor="w", pady=(2, 0))
            if facts:
                self._facts_frame.pack(fill="x", anchor="w", pady=(6, 2))
            self._shown_parts = want
        self._facts = dict(facts)                   # what is on show (empty while a row is loading)
        if facts and facts != self._fact_items:
            self._fact_items = facts                # what the labels hold: kept while the facts are hidden
            self._build_facts()
        self.scroll.refit()

    def _build_facts(self) -> None:
        """One name label and one value label per fact, kept from one header to the next (making or destroying a label
        costs 10 to 30 ms here, so a click only changes their text and the unused ones are hidden)."""
        facts = self._fact_items
        pairs = self._fact_labels
        while len(pairs) < len(facts):
            pairs.append((ttk.Label(self._facts_frame, text="", style="CardMuted.TLabel"),
                          ttk.Label(self._facts_frame, text="", style="CardValue.TLabel")))
            self.scroll.bind_wheel(self._facts_frame)
            self._facts_per_row = 0                          # new labels: they need placing
        for n, (name_label, value_label) in enumerate(pairs):
            if n < len(facts):
                name, value = facts[n]
                if str(name_label.cget("text")) != name:
                    name_label.configure(text=name)
                if str(value_label.cget("text")) != value:
                    value_label.configure(text=value)
            elif name_label.winfo_manager():
                name_label.grid_remove()
                value_label.grid_remove()
        self._facts_per_row = 0                              # the count may have changed: place them again
        self._place_facts(self.header.winfo_width())

    def _place_facts(self, width: int) -> None:
        """Lay the numbers out side by side as far as the pane's width allows (else fewer per row, down to one), so a
        number never runs off the right edge. Redone when the pane's width changes."""
        facts = self._fact_items
        if not facts:
            return
        per_row = self.facts_columns(facts, width if width > 50 else 900)
        if per_row == self._facts_per_row:
            return
        self._facts_per_row = per_row
        for i, (name_label, value_label) in enumerate(self._fact_labels[:len(facts)]):
            row, col = 2 * (i // per_row), i % per_row
            name_label.grid(row=row, column=col, sticky="w", padx=(0, 22))
            value_label.grid(row=row + 1, column=col, sticky="w", padx=(0, 22))

    def _fonts(self) -> tuple[tkfont.Font, tkfont.Font]:
        scale = ui_factor(self)
        if self._font_scale != scale:
            self._font_pair = (tkfont.Font(root=self, font=("Segoe UI", 9)),
                               tkfont.Font(root=self, font=("Segoe UI", 11, "bold")))
            self._font_scale = scale
        return self._font_pair

    def facts_columns(self, facts, width_px: float) -> int:
        """How many numbers go side by side: all of them when they fit the pane's width, else fewer (two by two for four),
        so a number never runs off the right edge."""
        facts = list(facts)
        if not facts:
            return 1
        small, big = self._fonts()
        cells = [max(small.measure(n), big.measure(v)) + 22 for n, v in facts]
        for per_row in range(len(facts), 1, -1):
            widest = max(sum(cells[i:i + per_row]) for i in range(0, len(cells), per_row))
            if widest <= width_px - 40:
                return per_row
        return 1

    def tile_count(self) -> int:
        """Tiles on show under an antibody (0 for anything else)."""
        return int(self._scene.tile_count)

    def blank_state(self) -> bool:
        """True while an antibody with little or no signal is shown as a plain message instead of tiles."""
        return bool(self._scene.blank)

    def followup_rows(self) -> list[tuple[str, str]]:
        """(name, role) of each row of a follow-up on show, top to bottom."""
        return list(self._scene.followup_rows)

    def pair_rows(self) -> list[tuple[str, str]]:
        """(name, role) of each row of two markers' tiles on show (the overlap view), top to bottom."""
        return list(self._scene.pair_rows)

    def followup_names(self) -> tuple[str, str, str | None] | None:
        """(source, target, control) of the follow-up on show, else None."""
        return self._scene.followup_names

    def header_title(self) -> str:
        return str(self._head_title.cget("text"))

    def header_subtitle(self) -> str:
        return str(self._head_sub.cget("text"))

    def facts(self) -> dict[str, str]:
        return dict(self._facts)

    def title(self) -> str:
        return self._title

    # ---- how big a picture will be -------------------------------------------------------------------------------
    def _on_room(self, event) -> None:
        w, h = int(event.width), int(event.height)
        if w >= 50 and h >= 50:
            changed = self._last_room != (w, h)
            self._last_room = (w, h)
            if changed:
                self._room_changed()

    room_hint: Callable[[], tuple[int, int] | None] | None = None      # set by a tab: what its room will be once shown

    def room(self) -> tuple[int, int] | None:
        """(width, height) in pixels of the pane's view, or the last size it had; None if it was never laid out. A pane
        on a tab nobody has opened yet has no size to measure: its tab says what it will be (so the picture can be made
        for it before the tab is opened)."""
        if self.room_hint is not None and not self._viewable():
            try:
                guess = self.room_hint()
            except tk.TclError:
                guess = None
            if guess is not None:
                return guess
        c = self.scroll._canvas
        try:
            w, h = c.winfo_width(), c.winfo_height()
        except tk.TclError:
            return self._last_room
        if w >= 50 and h >= 50:
            self._last_room = (w, h)
            return (w, h)
        return self._last_room

    def header_height(self, title: str, subtitle: str, facts, width: int) -> int:
        """Pixels the words above a picture will take at this width (the same labels the header uses, measured)."""
        facts = [(str(n), str(v)) for n, v in (facts or ())]
        memo_key = (title, subtitle, tuple(facts), int(width), ui_factor(self), self.text_below)
        known = self._header_memo.get(memo_key)
        if known is not None:
            return known
        wrap = max(200, width - 16)
        h = 0
        if title:
            self._m_title.configure(text=title, wraplength=wrap)
            h += self._m_title.winfo_reqheight()
        if subtitle and not self.text_below:
            self._m_sub.configure(text=subtitle, wraplength=wrap)
            h += 2 + self._m_sub.winfo_reqheight()
        if facts:
            per_row = self.facts_columns(facts, width)
            rows = math.ceil(len(facts) / per_row)
            h += 6 + 2 + rows * (self._m_small.winfo_reqheight() + self._m_big.winfo_reqheight())
        if len(self._header_memo) > 400:
            self._header_memo.clear()
        self._header_memo[memo_key] = int(h)
        return int(h)

    def toolbar_height(self, caption: str, width: int, pinned: bool | None = None) -> int:
        """Pixels the row above the picture (its option and its caption) will take."""
        pinned = self._toolbar_pinned if pinned is None else pinned
        if not (pinned or caption):
            return 0
        h = 0
        for w in self.toolbar.winfo_children():
            if w is not self._caption:
                h = max(h, w.winfo_reqheight())
        if caption:
            self._m_cap.configure(text=caption, wraplength=max(200, width - 330))
            h = max(h, self._m_cap.winfo_reqheight())
        return int(h)

    def target_size(self, hint: tuple | None, kind: str, min_px: int) -> tuple[int, int] | None:
        """(width, height) a picture with these words above it will have: the pane's room less the words and the
        toolbar, never less than the picture's own minimum. None if the pane has no size yet."""
        room = self.room()
        if room is None:
            return None
        w, h = room
        head = self.header_height(*(hint or ("", "", ())), width=w)
        tool = self.toolbar_height(self._caption_for.get(kind, ""), w)
        return (int(w), int(max(px(self, min_px), h - head - tool - 2)))

    @staticmethod
    def acceptable(size: tuple[int, int] | None, target: tuple[int, int] | None) -> bool:
        """A picture of `size` can stand in a place that wants `target`: never clipped, never far off."""
        if size is None or target is None:
            return False
        dw, dh = target[0] - size[0], target[1] - size[1]
        return -WIDTH_SLACK_UP <= dw <= WIDTH_SLACK_DOWN and -HEIGHT_SLACK_UP <= dh <= HEIGHT_SLACK_DOWN

    # ---- asking for a picture ------------------------------------------------------------------------------------------
    def request(self, key: tuple, owner: str, loading: str, make: Callable[[], Scene], *,
                header: Callable[[Scene], tuple] | tuple | None = None, hint: tuple | None = None,
                kind: str = "picture", min_px: int = MIN_ANTIBODY_PX) -> None:
        """Show what `make()` builds, under the words `header` gives.

        At once (in this very event): a picture that was made ahead of time is swapped in with its words; otherwise
        the pane clears the old words and picture and says `loading`. The picture is then made in a background thread
        and swapped in, with its words, when it is ready. Asking for another picture meanwhile drops this one."""
        self._token += 1
        want = _Want(self._token, key, owner, loading, make, header, hint, kind, min_px)
        self._want = want
        target = self.target_size(hint, kind, min_px)
        hit = self._lookup(key, target)
        if hit is not None:
            self._apply(want, hit[3], hit[2], hit[0], hit[1])
            return
        if self.ASYNC:
            self._begin_loading(want, target)
        self._ensure_current()

    def clear(self, message: str, top: bool = False) -> None:
        """Say `message` in the picture's place: centered, or (top) just under the words above it. Nothing is drawn by
        matplotlib, so this is immediate."""
        self._token += 1
        scene = Scene.message_scene(message, top)
        want = _Want(self._token, ("message", message, bool(top)), scene.owner, "", lambda: scene, None, None,
                     "message", MIN_MESSAGE_PX)
        self._want = want
        self._apply(want, scene, None, 0, 0)

    def read_preview(self, path: Path) -> dict | None:
        """Load a preview .npz; on any failure show the message and return None."""
        try:
            return load_preview(path)
        except Exception:  # BadZipFile, OSError, ValueError, KeyError, EOFError ...
            self.clear(UNREADABLE)
            return None

    # The older one-call forms: build the picture here and now (used by tests and small callers).
    def _show_now(self, scene: Scene, hint: tuple | None = None) -> None:
        self._token += 1
        key = ("now", self._token)
        want = _Want(self._token, key, scene.owner, "", lambda: scene, None, hint, scene.kind, scene.min_px)
        self._want = want
        if scene.message is not None:
            self._apply(want, scene, None, 0, 0)
            return
        target = self.target_size(hint, scene.kind, scene.min_px) or DEFAULT_SIZE
        png, w, h = scene.render_png(target[0], max(target[1] - PICTURE_MARGIN_PX, 40), ui_factor(self))
        self._apply(want, scene, png, w, h)

    def show_image(self, png: Path, title: str, subtitle: str = "") -> None:
        self._show_now(Scene.image(png, title, subtitle), (title, subtitle, ()))

    def show_antibody(self, preview: dict, row: pd.Series, range_max: float | None = None,
                      full_scale: bool = False, level: str = "ok") -> None:
        self._show_now(Scene.antibody(preview, row, range_max, full_scale, level))

    def show_followup(self, preview: dict) -> None:
        scene = Scene.followup(preview)
        self._show_now(scene, (*scene.header, ()))

    def show_pair(self, first: dict, second: dict, names: tuple[str, str], blank: frozenset = frozenset()) -> None:
        scene = Scene.pair(first, second, names, frozenset(blank))
        self._show_now(scene, (*scene.header, ()))

    # ---- the clock of one request ---------------------------------------------------------------------------------
    def _begin_loading(self, want: _Want, target: tuple[int, int] | None) -> None:
        """The click's own event: clear the old picture and words, say what is coming. Nothing here is slow."""
        self._loading = True
        self._drawn_owner = None
        self._applied_key = None
        self._applied_size = None
        self._clear_picture()
        if target is not None:
            self.pic.configure(height=target[1])
        self.set_caption("")
        self.set_header(want.loading)
        self.scroll.refit()

    def _clear_picture(self) -> None:
        self.pic.delete("all")
        self._photo = None
        self._text_item = None

    def _ensure_current(self) -> None:
        """Make what is on screen the picture that was last asked for, at the size the pane has now."""
        want = self._want
        if want is None or self._hold or self._dead:
            return
        if not self.ASYNC and not self._viewable():
            # A hidden tab costs nothing: its words are put up now and its picture is drawn when its tab is shown.
            if self._applied_key != want.key:
                self._apply(want, self._wrap_make(want.make)(), None, 0, 0)
            return
        target = self.target_size(want.hint, want.kind, want.min_px)
        if self._applied_key == want.key and not self._loading and (
                self._applied_scene_is_message() or self.acceptable(self._applied_size, target)):
            return
        hit = self._lookup(want.key, target)
        if hit is not None:
            self._apply(want, hit[3], hit[2], hit[0], hit[1], picture_only=self._applied_key == want.key)
            return
        self._start(want, target)

    def _applied_scene_is_message(self) -> bool:
        return self._scene.message is not None

    def _viewable(self) -> bool:
        try:
            return bool(self.pic.winfo_viewable())
        except tk.TclError:
            return False

    def _start(self, want: _Want, target: tuple[int, int] | None, priority: int = 0) -> None:
        size = target or DEFAULT_SIZE
        scale = ui_factor(self)
        render = (size[0], max(size[1] - PICTURE_MARGIN_PX, 40), scale)
        key, token = want.key, want.token

        def stale() -> bool:
            w = self._want
            return w is None or w.key != key or self._dead

        job = Job(ident=(id(self), key, render), make=self._wrap_make(want.make), sink=self._sink, priority=priority,
                  stale=stale if priority == 0 else None, size=render,
                  extra={"key": key, "kind": want.kind, "maker": want.make})
        if self.ASYNC:
            POOL.submit(job)
            self._watch_sink()
        else:
            result = RenderPool._run(job)                  # the calling thread, as before
            self._sink.put(result)
            self._take_results()

    @staticmethod
    def _wrap_make(make: Callable[[], Scene]) -> Callable[[], Scene]:
        def made() -> Scene:
            try:
                return make()
            except Exception:  # noqa: BLE001 - a picture that cannot be made is a message, never a crash
                return Scene.message_scene(UNREADABLE)
        return made

    # ---- results come back on the Tk thread ---------------------------------------------------------------------
    def _watch_sink(self) -> None:
        if self._poll_job is None and not self._dead:
            try:
                self._poll_job = self.after(20, self._poll_sink)
            except tk.TclError:
                self._poll_job = None

    def _poll_sink(self) -> None:
        self._poll_job = None
        self._take_results()
        if not self._dead and (self._outstanding() or not self._sink.empty()):
            self._watch_sink()

    def _outstanding(self) -> bool:
        """Jobs of this pane are still queued or running."""
        return POOL.outstanding_for(self._sink)

    def _take_results(self) -> None:
        got = False
        while True:
            try:
                res = self._sink.get_nowait()
            except queue.Empty:
                if got and not self._outstanding():
                    gc.collect()                  # on this (the Tk) thread: the render thread never collects, see renderpool
                return
            got = True
            try:
                self._take(res)
            except tk.TclError:
                return

    def _take(self, res: Result) -> None:
        job = res.job
        key = job.extra.get("key")
        if key == ("warm",):
            return
        if res.error == "stale":
            return
        want = self._want
        if res.scene is None:                              # the thread failed outright: say so for the row asked for
            if want is not None and want.key == key:
                self._loading = False
                self._apply(want, Scene.message_scene(UNREADABLE), None, 0, 0)
            return
        scene = res.scene
        if res.png is not None:
            self._remember(key, job.extra.get("maker"), scene, res.png, res.width, res.height)
        if want is None or want.key != key:
            return
        target = self.target_size(want.hint, want.kind, want.min_px)
        if res.png is None:                                # a message scene: no picture to size
            self._apply(want, scene, None, 0, 0, picture_only=False)
        elif self.acceptable((res.width, res.height), target) or target is None:
            self._apply(want, scene, res.png, res.width, res.height, picture_only=self._applied_key == want.key)
        else:
            self._ensure_current()                         # the pane changed size meanwhile: make it again

    # ---- the cache -------------------------------------------------------------------------------------------------
    def _remember(self, key, maker, scene: Scene, png: bytes, w: int, h: int) -> None:
        kept = copy.copy(scene)                            # a cached scene keeps no figure; it can be made again
        kept.light(maker)
        entries = self._cache.setdefault(key, [])
        entries[:] = [e for e in entries if (e[0], e[1]) != (w, h)][-1:]     # one picture per size, at most two sizes
        entries.append((w, h, png, kept))
        self._cache.move_to_end(key)
        while len(self._cache) > self.CACHE_MAX:
            self._cache.popitem(last=False)

    def _lookup(self, key, target):
        for entry in reversed(self._cache.get(key, [])):
            if self.acceptable((entry[0], entry[1]), target):
                self._cache.move_to_end(key)
                return entry
        return None

    def cached_keys(self) -> list[tuple]:
        return list(self._cache.keys())

    def cache_has(self, key) -> bool:
        return key in self._cache

    # ---- putting a finished picture and its words on screen, together -------------------------------------------------
    def _apply(self, want: _Want, scene: Scene, png: bytes | None, w: int, h: int, picture_only: bool = False) -> None:
        self._loading = False
        self._scene = scene
        self._title = scene.title
        if not picture_only:
            words = want.header(scene) if callable(want.header) else want.header
            self.set_header(*(words if words is not None else (*scene.header, ())))
            self.set_caption(scene.caption)
        if want.kind != "message":
            self._caption_for[want.kind] = scene.caption
        self._clear_picture()
        if scene.message is None and png is None:          # the picture waits until its pane is on screen
            self.pic.configure(height=px(self, scene.min_px))
            self._applied_size = None
        elif scene.message is not None:
            text, top = scene.message
            self.pic.configure(height=px(self, MIN_MESSAGE_PX))
            self._text_item = self.pic.create_text(0, 0, text=text, fill=_MUTED, font=("Segoe UI", 11),
                                                   anchor="n" if top else "center", justify="center",
                                                   width=max(200, self.pic.winfo_width() - 20))
            self._place_message()
            self._applied_size = None
        else:
            self._photo = tk.PhotoImage(master=self.pic, data=png)
            self.pic.create_image(0, 0, anchor="nw", image=self._photo)
            self.pic.configure(height=h)
            self._applied_size = (w, h)
        self._applied_key = want.key
        self._applied_hint = want.hint
        self._applied_kind = want.kind
        self._drawn_owner = scene.owner
        self._applied_log.append((scene.owner, want.key))
        self.scroll.refit()

    def _place_message(self, _event=None) -> None:
        if self._text_item is None:
            return
        try:
            w, h = self.pic.winfo_width(), self.pic.winfo_height()
            top = bool(self._scene.message and self._scene.message[1])
            self.pic.coords(self._text_item, w / 2, (0.12 * h) if top else (h / 2))
            self.pic.itemconfigure(self._text_item, width=max(200, w - 20))
        except tk.TclError:
            pass

    # ---- state the page asks about ------------------------------------------------------------------------------
    @property
    def defer_draws(self) -> bool:
        """True while the page is built under a cover: no work is started until the results page lets go (the layout
        under the cover is still changing, and a picture made at a size it is about to change is a second wasted)."""
        return self._hold

    @defer_draws.setter
    def defer_draws(self, on: bool) -> None:
        was = self._hold
        self._hold = bool(on)
        if was and not on:
            self._ensure_current()
            self._schedule_prefetch()

    def draw_now(self) -> None:
        """Start whatever was waiting for the layout (the layout is calm)."""
        self._hold = False
        self._ensure_current()
        self._schedule_prefetch()

    def ensure_drawn(self) -> None:
        """The pane has just been selected: see that its picture is the right one at the size it has now."""
        self._ensure_current()
        self._schedule_prefetch()

    @contextmanager
    def batch(self):
        """Group a change of words and picture (kept for callers; a picture and its words always arrive together)."""
        self._batch += 1
        try:
            yield
        finally:
            self._batch -= 1

    def _on_mapped(self, _event=None) -> None:
        self._ensure_current()
        self._schedule_prefetch()

    def drawn_owner(self) -> str | None:
        """Whose picture is on screen: the marker (or pair, or message) of the last picture swapped in; None while the
        pane is loading."""
        return self._drawn_owner

    def owner(self) -> str | None:
        want = self._want
        return want.owner if want is not None else self._scene.owner

    def is_settled(self, tolerance_px: int = 14) -> bool:
        """True when the picture on screen is the one last asked for, at (nearly) the size its place has: nothing is left
        to make."""
        want = self._want
        if want is None or self._batch or self._loading or self._applied_key != want.key:
            return False
        if self._scene.message is not None:
            return True
        target = self.target_size(want.hint, want.kind, want.min_px)
        if target is None or self._applied_size is None:
            return target is None and self._applied_size is not None
        return self.acceptable(self._applied_size, target)

    # ---- the pane changes size ----------------------------------------------------------------------------------------
    def _room_changed(self) -> None:
        """The room changed: the picture on screen stays until one for the new size is ready (never a blank), and the
        pictures made ahead are made again at the new size once the size has stopped changing."""
        if self._resize_job is not None:
            try:
                self.after_cancel(self._resize_job)
            except tk.TclError:
                pass
        try:
            self._resize_job = self.after(self.RESIZE_WAIT_MS, self._resize_settled)
        except tk.TclError:
            self._resize_job = None

    def _resize_settled(self) -> None:
        self._resize_job = None
        if self._dead:
            return
        # A guard against the pane feeding itself: a picture swapped in can make the page need (or stop needing) its scroll
        # bar, which changes the pane's width by the bar's width, which asks for another picture... The slack above stops
        # that for a bar coming and going; if a layout still keeps changing, the pane stops following it for a few seconds.
        now = time.monotonic()
        self._resize_times = [t for t in self._resize_times if now - t < 5.0] + [now]
        if len(self._resize_times) > 6:
            return
        POOL.drop_queued(lambda j: j.sink is self._sink and j.priority > 0)
        self._ensure_current()
        self._schedule_prefetch()

    def _on_destroy(self, event) -> None:
        if event.widget is self:
            self._dead = True
            POOL.drop_queued(lambda j: j.sink is self._sink)

    # ---- making the other rows' pictures ahead of time ----------------------------------------------------------------
    def prefetch(self, items: list[_Item]) -> None:
        """Make these pictures in the background, in this order, at the size the pane has now (async mode only).
        Replaces the list given before; whatever was not started is forgotten."""
        self._prefetch_items = list(items)
        POOL.drop_queued(lambda j: j.sink is self._sink and j.priority > 0)
        self._schedule_prefetch()

    def _schedule_prefetch(self) -> None:
        if not (self.ASYNC and self.PREFETCH) or not self._prefetch_items or self._dead:
            return
        if self._prefetch_job is not None:
            try:
                self.after_cancel(self._prefetch_job)
            except tk.TclError:
                pass
        try:
            self._prefetch_job = self.after(self.PREFETCH_WAIT_MS, self._start_prefetch)
        except tk.TclError:
            self._prefetch_job = None

    def _start_prefetch(self) -> None:
        self._prefetch_job = None
        if self._dead or self._hold:
            return
        if self.room() is None:
            return                                          # not laid out yet: the layout's resize brings us back
        if self._loading or (self._want is not None and self._applied_key != self._want.key):
            self._schedule_prefetch()                       # the picture asked for goes first
            return
        scale = ui_factor(self)
        n = 0
        for i, item in enumerate(self._prefetch_items):
            target = self.target_size(item.hint, item.kind, item.min_px)
            if target is None or self._lookup(item.key, target) is not None:
                continue
            render = (target[0], max(target[1] - PICTURE_MARGIN_PX, 40), scale)
            job = Job(ident=(id(self), item.key, render), make=self._wrap_make(item.make), sink=self._sink,
                      priority=3 + min(i, 200), size=render,
                      extra={"key": item.key, "kind": item.kind, "maker": item.make})
            POOL.submit(job)
            n += 1
        if n:
            self._watch_sink()

    def prefetch_idle(self) -> bool:
        """True when nothing of this pane's is queued or being made."""
        return not self._outstanding() and self._sink.empty()

    def _warm_up(self) -> None:
        """Make matplotlib load and lay out its fonts now, in the background thread, so the first real picture does not
        pay for it. (Nothing to do when pictures are made on the calling thread.)"""
        if not self.ASYNC or getattr(EvidenceCanvas, "_warmed", False):
            return
        EvidenceCanvas._warmed = True

        def warm() -> Scene:
            s = Scene(owner="warm", kind="warm")
            s.figure.text(0.5, 0.5, "Pixel value 0123456789", fontsize=8)
            s.figure.text(0.2, 0.2, "Pixels (log scale)", fontsize=11)
            return s
        POOL.submit(Job(ident=("warm",), make=warm, sink=self._sink, priority=9, size=(300, 120, 1.0),
                        extra={"key": ("warm",)}))
        self._watch_sink()

    # ---- drawing for a test, a script or a size check -------------------------------------------------------------------
    def _draw_current_now(self) -> None:
        """Draw the picture on show at the size its widget has (on the calling thread)."""
        self._scene.ensure_figure()
        w, h = self.pic.winfo_width(), self.pic.winfo_height()
        self._scene.draw(max(w, 40), max(h, 40), ui_factor(self))

    def _renderer(self):
        scene = self._scene
        scene.ensure_figure()
        if scene.agg is None:
            self._draw_current_now()
        return scene.agg.get_renderer()

    def clipped_texts(self) -> list[str]:
        """Words in the picture that run past the picture's edge (empty when everything is whole)."""
        if self._scene.message is not None:
            return []                                   # a message is Tk text that wraps to the pane, not a drawn picture
        w, h = self.pic.winfo_width(), self.pic.winfo_height()
        size = self._applied_size or (max(w, 40), max(h, 40))
        try:
            return self._scene.clipped_texts(size[0], size[1], ui_factor(self))
        except Exception:  # noqa: BLE001
            return []
