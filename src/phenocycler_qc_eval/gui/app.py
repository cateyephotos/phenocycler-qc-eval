"""Main window: pick an image, run the check, watch results arrive, load past runs.

The window never reads images or runs the analysis: a child process (runner.WorkerHandle) does, and the
run controller only polls its message queue with after(). The window is assembled from focused modules:
rail.py (left side), results_pane.py (right side), run_controller.py (run / poll / cancel), theme.py.
"""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from ..results import Results
from .example_flow import ExampleMixin
from .rail import RailMixin, _load_gui_state
from .results_pane import ResultsPaneMixin
from .run_controller import RunControllerMixin
from .settings_state import SettingsMixin
from .tabs import CrosstalkTab, FiguresTab, TitrationTab
from .theme import BG, LEFT_WIDTH, build_style, fit_window, px, ui_factor
from .tooltip import Tooltip
from .wait_view import APP_TITLE, WaitMixin


CANCEL_JOIN_S = 3.0      # how long closing the window waits for a Cancel that is still stopping the worker


class App(RailMixin, ResultsPaneMixin, RunControllerMixin, WaitMixin, ExampleMixin, SettingsMixin, tk.Tk):
    # Controls whose behavior is added by later tasks. Off = not shown.
    WIRED = {"run": True, "settings": True, "example": True}

    _real_scaling: float | None = None     # the display's own `tk scaling`, read before any window simulates another

    def __init__(self, simulate_scale: float | None = None) -> None:
        super().__init__()
        # Tk keeps `tk scaling` per display, not per window: a window that simulated 150% would leave every window made
        # after it in the same process at 150%. Each window starts from the display's own value.
        if App._real_scaling is None:
            App._real_scaling = float(self.tk.call("tk", "scaling"))
        elif simulate_scale is None:
            self.tk.call("tk", "scaling", App._real_scaling)
        if simulate_scale is not None:
            # A test and screenshot hook: 1.25 behaves like Windows display scaling of 125% (fonts and the
            # pixel arithmetic that follows them). It is not a real DPI change.
            self.tk.call("tk", "scaling", 96 / 72 * float(simulate_scale))
        self.title(APP_TITLE)
        width, height, min_w, min_h = fit_window(self.winfo_screenwidth(), self.winfo_screenheight(), ui_factor(self))
        self.geometry(f"{width}x{height}+20+20")
        self.minsize(min_w, min_h)
        self.configure(bg=BG)

        self.results: Results | None = None
        self.titration_tab: TitrationTab | None = None
        self.crosstalk_tab: CrosstalkTab | None = None
        self.figures_tab: FiguresTab | None = None
        self._state = _load_gui_state()
        self._output_user_set = False
        self._empty_visible = True
        self._details_open = False
        self.handle = None                    # the running WorkerHandle (or a stand-in), else None
        self._poll_job: str | None = None
        self._run_folder: Path | None = None  # the newest run's folder, once the worker has reported it
        self._last_stage: str | None = None
        self._template_handle = None
        self._template_job: str | None = None
        self._template_image: Path | None = None
        self._cancelling = False              # the user pressed Cancel and the worker has not finished stopping
        self._cancel_thread: threading.Thread | None = None
        self._downloading_example = False     # the running worker is fetching the example image, not a run
        self._sticky_status = False           # a "could not update" note that progress lines must not replace
        self.settings_dialog = None
        self._settings_changes: list[str] = []
        self._settings_warned: set[str] = set()
        self._bg: list[threading.Thread] = []  # short-lived helpers that stop or release a worker handle

        self.image_var = tk.StringVar(master=self)
        self.panel_var = tk.StringVar(master=self)
        self.output_var = tk.StringVar(master=self)
        self.saved_to_var = tk.StringVar(master=self, value="Results are saved next to your image.")
        self.settings_status_var = tk.StringVar(master=self, value="Settings: defaults")
        self.status_var = tk.StringVar(master=self, value="")
        self.elapsed_var = tk.StringVar(master=self, value="")

        self._log_row = 0
        self._error_seen = False              # a problem has been shown: the Details button is then offered
        self.init_wait()
        build_style(self)
        self._build_panes()
        self._build_left()
        self._build_right()
        self.build_tabs()
        self._prelayout_results()
        self._layout_left()
        self._update_run_state()
        self._set_sash()
        # Run and the panel buttons follow the boxes whatever changes them (a picker, the example, a loaded run).
        self.image_var.trace_add("write", lambda *_: (self._update_template_button(), self._refresh_run_button()))
        self.panel_var.trace_add("write", lambda *_: (self._update_template_button(), self._update_panel_widgets()))
        self._update_template_button()
        self._update_panel_widgets()
        self._refresh_run_button()
        self._update_details_button()
        self.refresh_settings_status()        # "Settings: defaults" or "modified"; file problems go to Details
        for w in (self.settings_label, self.settings_button):
            Tooltip(w, self.settings_tooltip_text)
        self.protocol("WM_DELETE_WINDOW", self.destroy)   # destroy() stops a running worker first

    def _build_panes(self) -> None:
        """Left rail and results side by side with a draggable sash (rail about 260 px)."""
        self.paned = ttk.PanedWindow(self, orient="horizontal")
        self.paned.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

    def _set_sash(self) -> None:
        """Put the sash at the rail width once the window has a real size (once only)."""
        self._sash_set = False
        self.paned.bind("<Configure>", self._place_sash)

    def _place_sash(self, event=None) -> None:
        if self._sash_set or self.paned.winfo_width() < 2:
            return
        try:
            self.paned.sashpos(0, min(px(self, LEFT_WIDTH), max(1, self.paned.winfo_width() - 200)))
            self._sash_set = True
        except tk.TclError:
            pass

    def destroy(self) -> None:
        """Closing the window stops a running worker cleanly first. The window is hidden before the (possibly
        slow) stop, so it never looks frozen."""
        busy = getattr(self, "handle", None) is not None or getattr(self, "_template_handle", None) is not None
        if busy:
            try:
                self.withdraw()
            except tk.TclError:
                pass
        try:
            self.shutdown_wait()
        except (AttributeError, tk.TclError):
            pass
        t = getattr(self, "_cancel_thread", None)
        if t is not None and t.is_alive():
            t.join(CANCEL_JOIN_S)             # close() must not overlap a cancel that is still running
        for attr in ("handle", "_template_handle"):
            h = getattr(self, attr, None)
            setattr(self, attr, None)
            if h is not None:
                try:
                    h.close()            # stops a running worker (cancel), joins readers, closes the pipes
                except Exception:        # noqa: BLE001 - closing must always go through
                    pass
        super().destroy()

    # ---- hooks for later tasks ---------------------------------------------
    def _click_run(self) -> None:
        self.on_run()

    def _click_cancel(self) -> None:
        self.on_cancel()

    def _click_settings(self) -> None:
        self.on_settings()

    def _click_example(self) -> None:
        self.on_example()

    def on_run(self) -> None:
        self.start_run()

    def on_cancel(self) -> None:
        self.cancel_run()

    def on_example(self) -> None:
        self.try_example()


def main() -> int:
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # sharp text on high-DPI screens
    except Exception:
        pass
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
