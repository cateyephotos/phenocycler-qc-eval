"""The wait: one RunState drives the stage list, the status line, the bars and the banner, and says when it is over.

Every message from the worker goes through RunState.update(), then apply_run_state() pushes the one state into
every place that talks about it, so the rail and the banner cannot disagree. A bar with no count to show animates
instead of standing still. A finished run changes the banner, the window title and rings the system bell once.
"""

from __future__ import annotations

import re
import time
import tkinter as tk

from ..advice import short_path
from .run_state import RunState

WORKING_TEXT = ("Antibody results appear here first, usually within a few minutes. Crosstalk results follow.\n\n"
                "You can cancel at any time. Whatever was saved stays in the results folder.")
STALL_S = 1.2          # a step that has said nothing for this long gets the moving bar (with the check below: under 1.6 s)
STALL_CHECK_MS = 400
APP_TITLE = "Phenocycler QC Eval"
DONE_TITLE = "Done - " + APP_TITLE
_EARLY = re.compile(r"Early antibody results from (\d+) of (\d+) windows")


class WaitMixin:
    """Part of App. Needs the rail's `progress` and `status_var` and the working page's widgets."""

    def init_wait(self) -> None:
        self.run_state = RunState()
        self.stage_var = tk.StringVar(master=self, value="")
        self.working_stage_var = tk.StringVar(master=self, value="")
        self.working_detail_var = tk.StringVar(master=self, value=WORKING_TEXT)
        self._running_banner = False            # the banner is showing the live "Now: ..." clause
        self._finished_folder: str | None = None
        self._last_feed = time.monotonic()
        self._stalled = False
        self._stall_job: str | None = None
        self.bind("<FocusIn>", self._reset_title, add="+")

    # ---- what a test or a caller can read -----------------------------------------------
    def stage_lines(self) -> list[str]:
        return self.run_state.lines()

    def working_stages_text(self) -> str:
        return self.working_stage_var.get()

    def working_detail_text(self) -> str:
        return self.working_detail_var.get()

    def full_path_tip(self) -> str:
        """The run folder's whole path, for the tooltip over a status line that shows it shortened."""
        return self._finished_folder or ""

    # ---- driving it ------------------------------------------------------------------------
    def begin_wait(self, kind: str = "run") -> None:
        self.run_state.start(kind)
        self._finished_folder = None
        self._running_banner = False
        self._last_feed, self._stalled = time.monotonic(), False
        self.title(APP_TITLE)
        self._schedule_stall()
        self.apply_run_state()

    def feed_wait(self, stage: str, fraction: float, message: str) -> None:
        if not self.run_state.active:               # a message means a run is going, however it was started
            self.run_state.start("download" if stage == "download" else "run")
        self.run_state.update(stage, fraction, message)
        self._last_feed, self._stalled = time.monotonic(), False
        self._schedule_stall()
        self.apply_run_state()

    # ---- a step that goes quiet still shows movement ---------------------------------------------------
    def _schedule_stall(self) -> None:
        if self._stall_job is None and self.run_state.active:
            self._stall_job = self.after(STALL_CHECK_MS, self._stall_check)

    def _stall_check(self) -> None:
        """Counted steps (window 12 of 61, figure 3 of 9) can take many seconds each. When nothing has been heard for
        STALL_S the bar animates, so the window never looks stuck; the next message puts the count back."""
        self._stall_job = None
        if not self.run_state.active:
            return
        stalled = (time.monotonic() - self._last_feed) > STALL_S
        if stalled != self._stalled:
            self._stalled = stalled
            self.apply_run_state()
        self._schedule_stall()

    def shutdown_wait(self) -> None:
        """The window is closing: no timer may fire into a destroyed window."""
        if self._stall_job is not None:
            try:
                self.after_cancel(self._stall_job)
            except tk.TclError:
                pass
            self._stall_job = None
        for bar in (self.progress, self.working_progress):
            try:
                bar.stop()
            except tk.TclError:
                pass

    def end_wait(self, outcome: str) -> None:
        if self._stall_job is not None:
            try:
                self.after_cancel(self._stall_job)
            except tk.TclError:
                pass
            self._stall_job = None
        self._stalled = False
        self.run_state.finish(outcome)
        self._running_banner = False
        self.apply_run_state()

    def apply_run_state(self) -> None:
        s = self.run_state
        lines = s.lines() if (s.active or s.finished) else []
        # The step list is on the working page only. The rail keeps the bar, one status line and the elapsed time (it
        # never repeats the list), and the banner names the current step once the results are on screen.
        self.stage_var.set("")
        self.working_stage_var.set("\n".join(lines))
        eta = s.eta_text() if s.active else ""
        size = s.size_note() if s.active else ""
        self.working_detail_var.set("\n\n".join(x for x in (eta, size, WORKING_TEXT) if x))
        self._set_bars()
        self._refresh_running_banner()

    def _set_bars(self) -> None:
        s = self.run_state
        animate = s.active and (s.indeterminate() or self._stalled)
        for bar in (self.progress, self.working_progress):
            mode = str(bar.cget("mode"))
            if animate:
                if mode != "indeterminate":
                    bar.configure(mode="indeterminate")
                    bar.start(40)
            else:
                if mode == "indeterminate":
                    bar.stop()
                    bar.configure(mode="determinate")
                if s.active:
                    bar.configure(value=max(0, min(100, round(s.fraction * 100))))

    # ---- the banner follows the same step ------------------------------------------------------
    def running_note(self, r) -> str | None:
        """The banner's clause about a run that is still going, from the run state (never from a stale folder)."""
        if self.handle is None or r.status != "running" or len(r.detection) == 0 or self.run_state.stage_index < 0:
            return None                    # no step reported yet: the folder's own note is the best there is
        phrase = self.run_state.banner_phrase()
        left = self.run_state.left_short()
        tail = (" " + left) if left else ""
        early = _EARLY.search(r.incomplete_note or "")
        if early:
            return (f"Early antibody results from {early.group(1)} of {early.group(2)} windows (they can still "
                    f"change). Now: {phrase}.{tail}")
        return f"Antibody results are final. Now: {phrase}.{tail}"

    def _refresh_running_banner(self) -> None:
        r = getattr(self, "results", None)
        if not self._running_banner or r is None:
            return
        note = self.running_note(r)
        if note:
            self._banner_line2.set(note)

    # ---- the end ----------------------------------------------------------------------------------
    def announce_finished(self, folder) -> None:
        """A finished run is told three ways, for someone who is not looking at the window: the banner, the title
        and one beep."""
        self._finished_folder = str(folder)
        self.title(DONE_TITLE)
        try:
            self.bell()
        except tk.TclError:
            pass

    def _reset_title(self, _event=None) -> None:
        if self.title() == DONE_TITLE:
            self.title(APP_TITLE)

    def finished_status(self, folder) -> str:
        """The rail says it is over; where the results are is said once, in the banner (its tip has the whole path)."""
        return "Finished."
