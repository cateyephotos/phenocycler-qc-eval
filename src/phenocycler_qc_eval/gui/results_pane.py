"""Right side: banner, empty state, Titration / Crosstalk / Figures tabs, error banners, past runs."""

from __future__ import annotations

import os
import re
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from .. import example
from ..advice import headline
from ..results import NotAResultsFolder, Results, load_results
from .help_window import HelpWindow
from .tabs import CrosstalkTab, FiguresTab, TitrationTab
from .theme import ui_factor, ERROR_BAR, FONT, INK, px, wrap_to
from .tooltip import Tooltip


LAYOUT_CALM_LOOKS = 3        # equal sizes this many looks in a row: the layout under the cover has stopped moving
REVEAL_POLL_MS = 40          # how often the page built under the working page is looked at
REVEAL_GIVE_UP_S = 30.0      # a page that is still not whole after this long is shown anyway (a safety net, not a plan)


class ResultsPaneMixin:
    """The results side of App: what is on screen after a run or a loaded past run."""

    WARM_TABS = True        # lay every tab out at start, so the first results do not pay for it

    def _build_right(self) -> None:
        self._reveal_job: str | None = None
        self._reveal_since = 0.0
        self._reveal_calm = 0
        self._layout_calm = 0
        self._layout_seen: tuple = ()
        self.revealed_at = 0.0
        self.right = ttk.Frame(self.paned, style="Card.TFrame", padding=(22, 18, 22, 16))
        self.right.columnconfigure(0, weight=1)
        self.right.rowconfigure(2, weight=1)
        self.paned.add(self.left, weight=0)
        self.paned.add(self.right, weight=1)

        # Which run is on screen: image name, date, antibody count; and, on the same line, the buttons that take the
        # answer away (report, folder, table) and the help for reading it. Reachable from every tab.
        self.header_bar = ttk.Frame(self.right, style="Card.TFrame")
        self.header_bar.columnconfigure(0, weight=1)
        self.run_header_var = tk.StringVar(master=self, value="")
        self.run_header = ttk.Label(self.header_bar, textvariable=self.run_header_var, style="Header.TLabel")
        self.run_header.grid(row=0, column=0, sticky="w")
        buttons = ttk.Frame(self.header_bar, style="Card.TFrame")
        buttons.grid(row=0, column=1, sticky="e")
        self.header_buttons: dict[str, ttk.Button] = {}
        for key, label, command in (("report", "Open report", self.open_report),
                                    ("folder", "Open results folder", self.open_results_folder),
                                    ("copy", "Copy table", self.copy_table),
                                    ("help", "How to read these results", self.show_help)):
            b = ttk.Button(buttons, text=label, command=command)
            b.pack(side="left", padx=(6, 0))
            self.header_buttons[key] = b
        self.help_window: HelpWindow | None = None
        self.header_message_var = tk.StringVar(master=self, value="")
        self._header_buttons_frame = buttons
        self._header_stacked = False
        self.header_bar.bind("<Configure>", self._layout_header)
        self._compact = False
        self.right.bind("<Configure>", self._maybe_compact, add="+")

        # Banner: two lines (each jumps to its tab), notes, up to two action buttons.
        self.banner = ttk.Frame(self.right, style="Banner.TFrame", padding=(18, 12))
        self.banner.columnconfigure(0, weight=1)
        self._banner_line1 = tk.StringVar(master=self)
        self._banner_line2 = tk.StringVar(master=self)
        self._banner_notes = tk.StringVar(master=self)
        self._notes: list[str] = []
        self.banner_label = ttk.Label(
            self.banner, textvariable=self._banner_line1, style="Banner.TLabel",
            font=(FONT, 13, "bold"), wraplength=700, justify="left", cursor="hand2")
        self.banner_label.grid(row=0, column=0, sticky="w")
        self.banner_label2 = ttk.Label(
            self.banner, textvariable=self._banner_line2, style="Banner.TLabel",
            font=(FONT, 13, "bold"), wraplength=700, justify="left", cursor="hand2")
        self.banner_label2.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.banner_label.bind("<Button-1>", lambda _e: self._goto_tab(self.titration_tab))
        self.banner_label2.bind("<Button-1>", lambda _e: self._goto_tab(self.crosstalk_tab))
        # The notes change how far to trust the page, so they are in the normal text color and the body size.
        self.banner_notes_label = ttk.Label(
            self.banner, textvariable=self._banner_notes, style="Banner.TLabel",
            font=(FONT, 11), foreground=INK, wraplength=700, justify="left")
        self.banner_notes_label.grid(row=2, column=0, sticky="w", pady=(6, 0))
        Tooltip(self.banner_notes_label, self.full_path_tip)       # the whole results path, when it is shown shortened
        # One next action in words, then its button(s). Used by error banners.
        self._banner_action_text = tk.StringVar(master=self)
        self.banner_action_label = ttk.Label(
            self.banner, textvariable=self._banner_action_text, style="Banner.TLabel",
            font=(FONT, 11), wraplength=700, justify="left")
        self.banner_action_label.grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.banner_action_label.grid_remove()
        self.banner_actions = ttk.Frame(self.banner, style="Banner.TFrame")
        self.banner_actions.grid(row=4, column=0, sticky="w", pady=(8, 0))
        self.banner_action_buttons: list[ttk.Button] = []
        self.banner.bind("<Configure>", self._rewrap_banner)
        # A failure is red: a bar at the left edge, a tinted background, a red heading. Information stays blue.
        self.banner_bar = tk.Frame(self.banner, width=4, bg=ERROR_BAR)
        self._banner_error = False

        self.notebook = ttk.Notebook(self.right)

        # Empty state: a heading, what you get, what is accepted, one large button.
        self.empty_frame = ttk.Frame(self.right, style="Card.TFrame", padding=(4, 24, 4, 0))
        self.empty_frame.columnconfigure(0, weight=1)
        self._empty_lines = self._empty_instruction()
        self.empty_heading = ttk.Label(
            self.empty_frame, text=self._empty_lines[0], style="Card.TLabel",
            font=(FONT, 20, "bold"))
        self.empty_heading.grid(row=0, column=0, sticky="w")
        self.empty_steps = ttk.Label(
            self.empty_frame, text=self._empty_lines[1], style="Card.TLabel",
            font=(FONT, 14), justify="left")
        self.empty_steps.grid(row=1, column=0, sticky="w", pady=(14, 0))
        self.empty_button = ttk.Button(
            self.empty_frame, text="Choose your image", style="Primary.TButton",
            command=self.choose_image_file)
        self.empty_button.grid(row=2, column=0, sticky="w", pady=(18, 0))
        self.empty_detail = ttk.Label(
            self.empty_frame, text="\n\n".join(self._empty_lines[2:4]), style="CardMuted.TLabel",
            font=(FONT, 11), wraplength=620, justify="left")
        self.empty_detail.grid(row=3, column=0, sticky="w", pady=(18, 0))
        self.empty_example_button = ttk.Button(self.empty_frame, text="No image at hand? Try the example",
                                               style="CardLink.TButton", command=self._click_example)
        if self.WIRED.get("example"):
            self.empty_example_button.grid(row=4, column=0, sticky="w", pady=(4, 0))
        self.empty_frame.grid(row=0, column=0, rowspan=3, sticky="nsew")

        # Working state: shown from the moment a run (or the example download) starts until the first antibody
        # results arrive, so the pane never says "Choose your image" while a run is going.
        self.working_frame = ttk.Frame(self.right, style="Card.TFrame", padding=(4, 24, 4, 0))
        self.working_frame.columnconfigure(0, weight=1)
        self.working_heading = ttk.Label(
            self.working_frame, text="Checking your section", style="Card.TLabel", font=(FONT, 20, "bold"))
        self.working_heading.grid(row=0, column=0, sticky="w")
        # The steps, the current one marked (one RunState feeds this, the rail and the banner), then a bar that
        # moves or animates, the current line, and what to expect.
        self.working_stages = ttk.Label(
            self.working_frame, textvariable=self.working_stage_var, style="Card.TLabel", font=(FONT, 13),
            justify="left")
        self.working_stages.grid(row=1, column=0, sticky="w", pady=(14, 0))
        self.working_progress = ttk.Progressbar(self.working_frame, mode="determinate", maximum=100, length=420)
        self.working_progress.grid(row=2, column=0, sticky="w", pady=(12, 0))
        self.working_status = ttk.Label(
            self.working_frame, textvariable=self.status_var, style="Card.TLabel", font=(FONT, 12),
            wraplength=620, justify="left")
        self.working_status.grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.working_detail = ttk.Label(
            self.working_frame, textvariable=self.working_detail_var, style="CardMuted.TLabel", font=(FONT, 11),
            wraplength=620, justify="left")
        self.working_detail.grid(row=4, column=0, sticky="w", pady=(18, 0))
        self._working_visible = False

    def _prelayout_results(self) -> None:
        """Put the header, banner and tabs in place now, under the empty page that covers them, so the first layout of all
        those widgets (a second or more on a slow machine) is paid while the window starts, not when the first results
        arrive. They are never seen empty: the empty page, then the working page, lie on top until results are whole."""
        self.header_bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.banner.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        self.notebook.grid(row=2, column=0, sticky="nsew")
        self.empty_frame.lift()
        # Each tab is shown once, one at a time, as soon as the window is on screen (see `_warm_next`): a tab nobody has
        # opened costs seconds of first layout and first painting on a slow machine, and that must not be what a person
        # waits for when they click it. (A test that opens dozens of windows turns this off.)
        self._warm_queue = [self.crosstalk_tab, self.figures_tab] if self.WARM_TABS else []
        self._warm_job: str | None = None
        if self._warm_queue:
            self._warm_job = self.after(250, self._warm_next)

    def tabs_warm(self) -> bool:
        """True once every tab has been shown once (always True where warming is off)."""
        return not self._warm_queue

    def _warm_next(self) -> None:
        """Show the next tab that nobody has opened, under the empty page that covers the tabs, then go back to the
        first tab. One tab per event, so the window is never unresponsive for more than about a second at a time."""
        self._warm_job = None
        if not self._warm_queue:
            return
        try:
            if not self.winfo_ismapped():                       # not on screen yet: nothing to paint
                self._warm_job = self.after(200, self._warm_next)
                return
            if not self.empty_frame.winfo_manager() or self.handle is not None or self.results is not None:
                self._warm_queue = []                           # the window is in use: the first open pays after all
                return
            tab = self._warm_queue.pop(0)
            self.notebook.select(tab)
            self.update()                       # the events too: a tab's first paint is a map event, not an idle job
            self.notebook.select(self.titration_tab)
            self.update()
        except tk.TclError:
            self._warm_queue = []
            return
        if self._warm_queue:
            self._warm_job = self.after(40, self._warm_next)

    def _layout_header(self, event=None) -> None:
        """On a narrow pane (a small screen, or large display scaling) the buttons go on a line of their own under the
        image name instead of cutting the name off. Looked at again whenever the name's words change (they are set
        after the pane has its size, so the pane's own resize is not enough)."""
        width = event.width if event is not None else self.header_bar.winfo_width()
        need = self.run_header.winfo_reqwidth() + self._header_buttons_frame.winfo_reqwidth() + px(self, 24)
        stacked = width < need
        if stacked == self._header_stacked:
            return
        self._header_stacked = stacked
        if stacked:
            self._header_buttons_frame.grid_configure(row=1, column=0, sticky="w", pady=(6, 0))
        else:
            self._header_buttons_frame.grid_configure(row=0, column=1, sticky="e", pady=0)

    COMPACT_GAP = 40             # a compact window leaves compact mode only above COMPACT_BELOW + this
    COMPACT_BELOW = 640          # a window shorter than this many pixels at 100% (a 1366 x 768 laptop at 125%)

    def compact(self) -> bool:
        """True while the window is short: the header, the banner and the tables take less room, so the evidence under
        them (the numbers and the tiles) shows without scrolling."""
        return self._compact

    def _maybe_compact(self, event=None) -> None:
        """The window's size is looked at once it has stopped changing (a size seen for a moment while the window opens
        or is dragged must not flip the layout)."""
        job = getattr(self, "_compact_job", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass
        try:
            self._compact_job = self.after(300, self._apply_compact)
        except tk.TclError:
            self._compact_job = None

    def _apply_compact(self) -> None:
        self._compact_job = None
        height = self.winfo_height() / max(ui_factor(self), 1.0)
        # Two thresholds, 40 px apart: a window sitting near the limit never flips back and forth, and the layout change
        # itself (a few pixels of padding) can never carry the height across the gap.
        want = height < (self.COMPACT_BELOW + self.COMPACT_GAP if self._compact else self.COMPACT_BELOW)
        if want == self._compact or self.winfo_height() < 100:
            return
        self._compact = want
        self.right.configure(padding=(22, 8, 22, 6) if want else (22, 18, 22, 16))
        self.banner.configure(padding=(14, 6) if want else (18, 12))
        size = 11 if want else 13
        for lab in (self.banner_label, self.banner_label2):
            lab.configure(font=(FONT, size, "bold"))
        self.banner_label2.grid_configure(pady=(1, 0) if want else (4, 0))
        self.header_bar.grid_configure(pady=(0, 2) if want else (0, 6))
        self.banner.grid_configure(pady=(0, 6) if want else (0, 12))
        for b in self.header_buttons.values():
            b.configure(style="Compact.TButton" if want else "TButton")
        for tab in (self.titration_tab, self.crosstalk_tab):
            if tab is not None:
                tab.set_compact(want)

    def _rewrap_banner(self, event) -> None:
        wrap = max(300, event.width - 40)
        for lab in (self.banner_label, self.banner_label2, self.banner_notes_label, self.banner_action_label):
            wrap_to(lab, wrap)

    def _goto_tab(self, tab) -> None:
        if tab is not None:
            self.notebook.select(tab)

    # ---- empty state / banner --------------------------------------------
    def _empty_instruction(self) -> list[str]:
        lines = [
            "Check a stained section",
            "1. Choose your image.\n2. Press Run.",
            "You get one next-run direction for every antibody (raise, lower, compare with "
            "expected biology, or keep) and a list of channel pairs with crosstalk.",
            "Accepted images: .qptiff, .tif, .tiff, or a Xenium folder.",
        ]
        if self.WIRED.get("example"):
            lines.append("No image at hand? Press Try the example.")
        return lines

    def empty_state_text(self) -> str:
        return "\n".join(self._empty_lines)

    def empty_state_visible(self) -> bool:
        return self._empty_visible

    def banner_text(self) -> str:
        return f"{self._banner_line1.get()}\n{self._banner_line2.get()}".rstrip("\n")

    def banner_notes(self) -> list[str]:
        return list(self._notes)

    def banner_action_text(self) -> str:
        """The one next action, in words, shown under an error headline (empty otherwise)."""
        return self._banner_action_text.get()

    @property
    def banner_action_button(self):
        """The first action button under the banner, or None."""
        return self.banner_action_buttons[0] if self.banner_action_buttons else None

    def banner_is_error(self) -> bool:
        return self._banner_error

    def set_banner_actions(self, actions: list[tuple[str, object]]) -> None:
        """Up to three (label, command) buttons under the banner text."""
        for b in self.banner_action_buttons:
            b.destroy()
        self.banner_action_buttons = []
        for i, (label, command) in enumerate(actions[:3]):
            b = ttk.Button(self.banner_actions, text=label, command=command)
            b.pack(side="left", padx=(0 if i == 0 else 8, 0))
            self.banner_action_buttons.append(b)

    def add_banner_note(self, text: str) -> None:
        self._notes.append(text)
        self._banner_notes.set("\n".join(self._notes))
        self.banner_notes_label.grid()

    def _style_banner(self, error: bool) -> None:
        self._banner_error = bool(error)
        frame, label = ("BannerError.TFrame", "BannerError.TLabel") if error else ("Banner.TFrame", "Banner.TLabel")
        self.banner.configure(style=frame)
        self.banner_actions.configure(style=frame)
        for lab in (self.banner_label2, self.banner_notes_label, self.banner_action_label):
            lab.configure(style=label)
        self.banner_label.configure(style="BannerErrorHead.TLabel" if error else "Banner.TLabel")
        if error:
            self.banner_bar.place(x=0, y=0, relheight=1, width=4, bordermode="outside")
        else:
            self.banner_bar.place_forget()

    def _set_banner(self, line1: str, line2: str, notes: list[str], action_text: str = "",
                    actions: list[tuple[str, object]] | None = None, error: bool = False) -> None:
        self._style_banner(error)
        self._banner_line1.set(line1)
        self._banner_line2.set(line2)
        if line2:
            self.banner_label2.grid()
        else:
            self.banner_label2.grid_remove()
        self._notes = list(notes)
        self._banner_notes.set("\n".join(self._notes))
        if self._notes:
            self.banner_notes_label.grid()
        else:
            self.banner_notes_label.grid_remove()
        self._banner_action_text.set(action_text)
        if action_text:
            self.banner_action_label.grid()
        else:
            self.banner_action_label.grid_remove()
        self.set_banner_actions(actions or [])

    def working_state_visible(self) -> bool:
        return self._working_visible

    def _show_working(self, under_banner: bool) -> None:
        """Swap the empty state (or the tabs) for the working state. `under_banner` keeps the banner above it."""
        self.empty_frame.grid_remove()
        self.notebook.grid_remove()
        if under_banner:
            self.working_frame.grid(row=2, column=0, sticky="nsew")
        else:
            self.working_frame.grid(row=0, column=0, rowspan=3, sticky="nsew")
        self._working_visible = True
        self._sync_rail_progress()

    def _sync_rail_progress(self) -> None:
        """The working page has the bar and the status line, so the rail does not show them a second time; the rail keeps
        the elapsed time. They come back when the working page goes."""
        hidden = getattr(self, "_rail_hidden", None)
        if hidden is None:
            hidden = self._rail_hidden = []
        try:
            if self._working_visible:
                for w in (self.progress, self.status_label):
                    if w.winfo_manager() == "grid":
                        w.grid_remove()
                        hidden.append(w)
            else:
                while hidden:
                    hidden.pop().grid()
        except tk.TclError:
            pass

    def _hide_working(self) -> None:
        self._cancel_reveal()
        if self._working_visible:
            self.working_frame.grid_remove()
            self._working_visible = False
        self._sync_rail_progress()

    # ---- results appear once, whole -----------------------------------------------------------------------------
    def results_visible(self) -> bool:
        """True when a person can see the results: the tabs are on screen and nothing covers them. While a run is going
        the first results are built under the working page and this turns True only when they are complete."""
        try:
            return bool(self.notebook.winfo_ismapped() and not self.working_frame.winfo_ismapped()
                        and not self.empty_frame.winfo_ismapped())
        except tk.TclError:
            return False

    def reveal_state(self) -> str:
        """"building" while the first results are being drawn under the working page, else "shown" or "hidden"."""
        if self._reveal_job is not None:
            return "building"
        return "shown" if self.results_visible() else "hidden"

    def _defer_pictures(self, on: bool) -> None:
        """While the first results are built under the working page their pictures wait for the layout to settle (a draw
        made at a size the layout is about to change is a second wasted)."""
        for tab in (self.titration_tab, self.crosstalk_tab, self.figures_tab):
            if tab is not None:
                tab.evidence.defer_draws = on

    def _cancel_reveal(self) -> None:
        self._defer_pictures(False)
        if self._reveal_job is not None:
            try:
                self.after_cancel(self._reveal_job)
            except tk.TclError:
                pass
            self._reveal_job = None

    def _reveal_when_ready(self) -> None:
        """Leave the working page on screen until the results under it are whole, then take it away in one step."""
        if self._reveal_job is None:
            self._reveal_since = time.monotonic()
            self._reveal_calm = 0
            self._layout_calm = 0
            self._layout_seen = ()
            self._reveal_job = self.after(REVEAL_POLL_MS, self._reveal_check)

    def results_ready(self) -> bool:
        """The page under the working page is complete: the table is whole at the pane's full width, the words and the
        picture belong together, and nothing is left to draw."""
        try:
            tab = self.nametowidget(self.notebook.select())
        except (tk.TclError, KeyError):
            return False
        evidence = getattr(tab, "evidence", None)
        if evidence is None or not self.notebook.winfo_ismapped() or not tab.winfo_ismapped():
            return False
        if tab.winfo_width() < self.notebook.winfo_width() - 8:          # the tab has not taken its final width
            return False
        tree = getattr(tab, "tree", None)
        if tree is not None and not tree.get_children():
            return False
        return bool(evidence.is_settled())

    def _layout_signature(self) -> tuple:
        """The sizes of the notebook, the open tab and its picture: unchanged for a few looks in a row means the layout
        under the cover has stopped moving."""
        try:
            tab = self.nametowidget(self.notebook.select())
            canvas = tab.evidence.canvas.get_tk_widget()
            return (self.notebook.winfo_width(), self.notebook.winfo_height(), tab.winfo_width(), tab.winfo_height(),
                    canvas.winfo_width(), canvas.winfo_height())
        except (tk.TclError, KeyError, AttributeError):
            return ()

    def _reveal_check(self) -> None:
        self._reveal_job = None
        waited = time.monotonic() - self._reveal_since
        sig = self._layout_signature()
        self._layout_calm = self._layout_calm + 1 if sig and sig == self._layout_seen else 0
        self._layout_seen = sig
        if self._layout_calm >= LAYOUT_CALM_LOOKS and self.titration_tab.evidence.defer_draws:
            # The layout has stopped moving: draw the picture once, at its final size, then see that it holds.
            self._defer_pictures(False)
            for tab in (self.titration_tab,):
                tab.evidence.draw_now()
        if self.results_ready():
            self._reveal_calm += 1
        else:
            self._reveal_calm = 0
        if self._reveal_calm >= 2 or waited > REVEAL_GIVE_UP_S:       # two calm looks in a row, or never wait for ever
            self._reveal()
            return
        self._reveal_job = self.after(REVEAL_POLL_MS, self._reveal_check)

    def _reveal(self) -> None:
        self._cancel_reveal()
        if self._working_visible:
            self.working_frame.grid_remove()
            self._working_visible = False
        self._sync_rail_progress()
        self.revealed_at = time.monotonic()

    def _show_panel(self, with_tabs: bool, keep_working: bool = False) -> None:
        """Replace the empty state with the banner (and, when there is something to show, the tabs). With `keep_working`
        the working page stays on top of them: they are built underneath it and shown once they are whole."""
        if not keep_working:
            self._hide_working()
        if self._empty_visible:
            self.empty_frame.grid_remove()
            self._empty_visible = False
        self.banner.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        if with_tabs:
            self.header_bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
            self.notebook.grid(row=2, column=0, sticky="nsew")
        else:
            self.header_bar.grid_remove()
            self.notebook.grid_remove()

    def show_results(self, r: Results, *, only_titration: bool = False, keep_tab: bool = False) -> None:
        """Show a loaded run. During a run, only_titration refreshes just that tab and keep_tab leaves the
        user on the tab they are looking at."""
        self.results = r
        n = len(r.detection)
        if n == 0 and r.status == "running" and self.handle is not None:
            # Nothing to tabulate yet: the working page says what is going on, and no banner repeats "no results yet".
            self._hide_working()
            if self._empty_visible:
                self.empty_frame.grid_remove()
                self._empty_visible = False
            self.banner.grid_remove()
            self.header_bar.grid_remove()
            self._running_banner = False
            self._show_working(under_banner=False)
            return
        h = headline(r)
        notes, line2 = list(h.notes), h.crosstalk
        live = self.running_note(r)                       # from the run state, so it agrees with the rail
        if live:
            notes = [x for x in notes if x != r.incomplete_note]
            line2 = live
        self._running_banner = bool(live)
        # While a run is going and the results are not on screen yet, they are built under the working page and shown
        # once, whole. Once they are on screen a reload swaps the words and the pictures in place.
        covered = self.handle is not None and (self._reveal_job is not None or not self.results_visible())
        if covered:
            self._defer_pictures(True)
        self._set_banner(h.titration, line2, notes)
        count = f"{n} antibod{'y' if n == 1 else 'ies'}" if (n or r.status == "complete") else ""
        self.run_header_var.set("   |   ".join(x for x in (r.image_name, r.run_date, count) if x))
        self.after_idle(self._layout_header)
        self._show_panel(True, keep_working=covered)
        self._update_header_buttons(r)
        if only_titration and self.titration_tab is not None:
            self.titration_tab.load(r)
            self._tab_counts(r, titration_only=True)
            if not keep_tab:
                self.notebook.select(self.titration_tab)
        else:
            self.fill_tabs(r, keep_tab=keep_tab)
        if covered:
            self.working_frame.lift()
            self._reveal_when_ready()

    def build_tabs(self) -> None:
        """Make the three tabs (once). Done while the window starts, so the first results do not wait for it."""
        if self.titration_tab is not None:
            return
        self.titration_tab = TitrationTab(self.notebook)
        self.crosstalk_tab = CrosstalkTab(self.notebook, on_template=lambda: self.save_layout_template())   # looked up when pressed
        self.figures_tab = FiguresTab(self.notebook)
        self.notebook.add(self.titration_tab, text="Titration")
        self.notebook.add(self.crosstalk_tab, text="Crosstalk")
        self.notebook.add(self.figures_tab, text="Figures")
        self.notebook.bind("<<NotebookTabChanged>>", self._tab_changed, add="+")

    def _tab_changed(self, _event=None) -> None:
        """A tab was just opened: its picture is drawn now, with its words, before anything is painted (a tab that waited
        hidden would otherwise show an empty pane until its picture was drawn)."""
        try:
            tab = self.nametowidget(self.notebook.select())
        except (tk.TclError, KeyError):
            return
        evidence = getattr(tab, "evidence", None)
        if evidence is not None:
            evidence.ensure_drawn()

    def fill_tabs(self, r: Results, keep_tab: bool = False) -> None:
        current = None
        self.build_tabs()
        if keep_tab:
            current = self.notebook.select()
        self.titration_tab.load(r)
        self.crosstalk_tab.load(r)
        self.figures_tab.load(r)
        self._tab_counts(r)
        self.notebook.select(current or self.titration_tab)

    def _tab_counts(self, r: Results, titration_only: bool = False) -> None:
        self.notebook.tab(self.titration_tab, text=f"Titration ({len(r.detection)})")
        if titration_only:
            return
        if r.crosstalk_state == "on":
            self.notebook.tab(self.crosstalk_tab, text=f"Crosstalk ({self.crosstalk_tab.pair_count()})")
        else:
            self.notebook.tab(self.crosstalk_tab, text="Crosstalk")
        n_figures = self.figures_tab.count()
        self.notebook.tab(self.figures_tab, text=f"Figures ({n_figures})" if n_figures else "Figures")

    # ---- error banners ---------------------------------------------------------
    def show_error(self, kind: str, message: str, found=None, needed=None) -> None:
        """A run problem goes in the banner: a headline, one next action and (at most) one button.

        The run's folder, when there is one, is loaded first so antibody results saved before the problem
        stay visible; with nothing saved the tables are hidden."""
        r = None
        if self._run_folder is not None:
            try:
                r = load_results(self._run_folder, ended="error")   # the run is over: never "running"
            except NotAResultsFolder:
                r = None
        have_results = r is not None and len(r.detection) > 0
        if have_results:
            self.show_results(r, keep_tab=True)
            notes = list(self._notes)          # what is usable, from the loader (e.g. "Run failed: ...")
        else:
            self.results = r
            self._show_panel(False)
            notes = []

        status = "Run stopped."
        if kind == "too_little_tissue":
            if found is None or needed is None:
                m = re.search(r"found (\d+) tissue windows, need at least (\d+)", message or "")
                found, needed = (int(m.group(1)), int(m.group(2))) if m else (None, None)
            head = ("Not enough tissue found" + (f" ({found} windows, need {needed})." if found is not None
                                                 else "."))
            action = ("Check that the right image was chosen, or lower Minimum windows in Settings, Advanced.")
            button = ("Open Settings", self._click_settings)
        elif kind == "example_unavailable":
            head = "The example image is not on this computer yet."
            action = example.manual_instructions()
            button = [("Copy link", self.copy_example_link), ("Open that folder", self.open_example_folder)]
            status = "The example image could not be downloaded."
        elif kind == "reader":
            head = "This image could not be read."
            action = (f"{message} The file may be incomplete or damaged: copy it again from the scanner, then choose "
                      "it again. Accepted: .qptiff, .tif, .tiff, or a Xenium folder.").strip()
            button = ("Choose another image", self.choose_image_file)
        else:
            head = "The run stopped because of an unexpected error."
            action = "Open Details for the full message."
            if have_results:
                action += " Antibody results saved before the error are shown below."
            button = ("Open Details", self.open_details)
        if message:
            self.log_line(message)
        self._error_seen = True
        self._update_details_button()
        self._set_banner(head, "", notes, action, button if isinstance(button, list) else [button], error=True)
        self.status_var.set(status)

    def load_past_run(self, folder: Path) -> None:
        try:
            r = load_results(Path(folder))
        except NotAResultsFolder:
            self.status_var.set("That folder is not a past run.")
            return
        self._remember("past_run_dir", Path(folder).parent)
        self._run_folder = None
        self.show_results(r)
        # The boxes name what the loaded results are about: the image and the panel file the run used, so the
        # rail never contradicts the header. Run is on only if that image is still here to run again.
        image_here = bool(r.image_path) and Path(r.image_path).exists()
        panel_here = bool(r.panel_path) and Path(r.panel_path).exists()
        self.image_var.set(r.image_path if image_here else "")
        self.panel_var.set(r.panel_path if panel_here else "")
        self.elapsed_var.set("")                    # no stale time from another run
        if not self.run_state.active:
            self.run_state.reset()                  # a finished run's step list does not belong to this one
        self.apply_run_state()
        what = f"Showing saved run: {r.image_name}" + (f", {r.run_date}." if r.run_date else ".")
        self.status_var.set(what + (" Choose Run to make a new one." if image_here
                                    else " Its image is not available here: choose an image to run again."))
        self._refresh_run_button()

    # ---- the header buttons: take the answer away, and the help --------------------------------------------------
    def _update_header_buttons(self, r: Results) -> None:
        self.header_buttons["report"].configure(state="normal" if r.report else "disabled")
        self.header_buttons["folder"].configure(state="normal" if r.folder else "disabled")
        self.header_buttons["copy"].configure(state="normal" if len(r.detection) else "disabled")

    def copy_table(self) -> None:
        """The Titration table, tab-separated, on the clipboard (pastes into a spreadsheet or a notebook)."""
        if self.titration_tab is None:
            return
        text = self.titration_tab.copy_table_text()
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()                 # the clipboard is handed to the system now, not on the next idle
        self.header_message_var.set("Table copied.")

    def _open_path(self, path, what: str) -> None:
        if path is None:
            self.status_var.set(f"There is no {what} to open.")
            return
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except (OSError, AttributeError) as exc:
            self.status_var.set(f"Could not open the {what}: {exc}")

    def open_results_folder(self) -> None:
        self._open_path(self.results.folder if self.results else None, "results folder")

    def open_report(self) -> None:
        self._open_path(self.results.report if self.results else None, "report")

    def show_help(self) -> HelpWindow:
        """The guide to reading the results, in a window of its own (one at a time)."""
        win = self.help_window
        if win is not None and win.winfo_exists():
            win.deiconify()
            win.lift()
            return win
        self.help_window = HelpWindow(self)
        return self.help_window
