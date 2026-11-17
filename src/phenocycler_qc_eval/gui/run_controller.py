"""Starting, polling, cancelling and concluding a run, and reading channel names for the layout template.

Everything slow happens in a child process (runner.WorkerHandle); this module only polls its message queue
with after(), and keeps the (slow) handle calls cancel() and close() on helper threads. No Tk call is made
off the Tk thread.
"""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

from ..advice import short_path
from ..layout import write_layout_template
from ..results import NotAResultsFolder, Results, load_results
from ..run import RunRequest, image_stem
from ..runner import WorkerHandle, run_request_dict
from ..settings import load_settings
from .dialogs import ask_nuclear_channel

POLL_MS = 100
TEMPLATE_DONE = "Template saved. Fill in cycle and fluor, then choose it as the panel file."


class RunControllerMixin:
    """Run state for App: start, poll, cancel, conclude; the layout-template reader."""

    # ---- starting, polling and stopping a run ------------------------------------
    def make_handle(self, request: dict):
        """The worker handle for a request (replaced by a stand-in in tests)."""
        return WorkerHandle(request)

    def _busy(self, busy: bool) -> None:
        """Run is locked while a run is active; Cancel exists only then; a past run cannot replace it."""
        self._refresh_run_button()
        self.cancel_button.configure(state="normal" if busy else "disabled")
        self.load_button.configure(state="disabled" if busy else "normal")
        self._update_details_button()
        self._tell_settings_dialog(busy)
        if busy and self._empty_visible:
            self._show_working(under_banner=False)         # from the first second, not from the first result
        elif not busy:
            if self._reveal_job is None:           # results still being built under it: it leaves when they are whole
                self._hide_working()
            if self._empty_visible:
                self.empty_frame.grid()

    def start_run(self, nuclear_channel: str | None = None) -> None:
        if self.handle is not None:
            return
        text = self.image_var.get().strip()
        image = Path(text) if text else None
        if image is None or not image.exists():            # the file may have moved since it was chosen
            self.status_var.set("The image could not be found. Choose it again.")
            return
        panel_text = self.panel_var.get().strip()
        panel = Path(panel_text) if panel_text else None
        if panel is not None and not panel.exists():
            self.status_var.set("The panel file could not be found. Choose it again or remove it.")
            return
        self._begin_run(image, panel, nuclear_channel)

    def _begin_run(self, image: Path, panel: Path | None, nuclear_channel: str | None = None) -> None:
        """Start the worker for an image that has been checked (by start_run, or by the example download)."""
        settings, warnings = load_settings()
        self.refresh_settings_status()                     # the status line must match the file this run reads
        out_root = Path(self.output_var.get()) if self.output_var.get() else image.parent / "qc_results"
        req = RunRequest(image, panel, out_root, settings, nuclear_channel=nuclear_channel,
                         settings_warnings=list(warnings))
        handle = self.make_handle(run_request_dict(req))
        self._run_folder = None
        self._last_stage = None
        self._cancelling = False
        self._sticky_status = False
        self._cancel_thread = None
        self._error_seen = False
        self.progress.configure(value=0)
        self.elapsed_var.set("Elapsed 0:00")
        self.status_var.set("Starting the run...")
        try:
            handle.start()
        except OSError as exc:
            self.status_var.set(f"The run could not start: {exc}")
            return
        for w in warnings:
            self.log_line(w)
        self.handle = handle
        self.begin_wait("run")
        self._busy(True)
        self._schedule_poll()

    # ---- helpers that keep slow handle calls off the Tk thread ----------------------
    def _background(self, fn) -> threading.Thread:
        """Run fn on a short-lived thread. fn must not touch Tk; its outcome is read through the poll loop."""
        def run() -> None:
            try:
                fn()
            except Exception:        # noqa: BLE001 - stopping or releasing a worker must never raise into Tk
                pass

        self._bg = [t for t in self._bg if t.is_alive()]
        t = threading.Thread(target=run, daemon=True)
        self._bg.append(t)
        t.start()
        return t

    def _join_background(self, timeout: float = 5.0) -> None:
        for t in list(self._bg):
            t.join(timeout)

    def _release(self, h) -> None:
        """Free a finished handle's threads and pipes (WorkerHandle.close(), which is safe to repeat)."""
        close = getattr(h, "close", None)
        if close is not None:
            self._background(close)

    def cancel_run(self) -> None:
        """Stop the run without blocking the window.

        Cancel is disabled and the status line says so at once; the handle's (slow) cancel runs on a helper
        thread and the normal poll loop picks up the outcome. Nothing is asked: what was saved stays in the
        folder, and the status line says so."""
        h = self.handle
        if h is None or self._cancelling:
            return
        self._cancelling = True
        self.cancel_button.configure(state="disabled")
        self.status_var.set("Stopping the run...")
        self._cancel_thread = self._background(h.cancel)
        if self._poll_job is None:
            self._schedule_poll()          # the poll loop picks up the outcome

    def _schedule_poll(self) -> None:
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except tk.TclError:
                pass
        self._poll_job = self.after(POLL_MS, self._poll)

    def _poll(self) -> None:
        """Take what the worker has said since the last call. Never blocks; no image work happens here.

        One failing reload never ends the polling: it is logged, shown in the status line, and the next
        message tries again."""
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except tk.TclError:
                pass
            self._poll_job = None
        h = self.handle
        if h is None:
            return
        try:
            running = h.is_running()    # asked BEFORE draining, so a stopped worker has no message left behind
            messages = h.poll()
            secs = int(h.elapsed_s())
        except Exception as exc:        # noqa: BLE001 - a broken handle must not freeze the window or lock Run
            self._report_failure(exc, "Lost contact with the check")
            self._end_run()
            return
        try:
            self.elapsed_var.set(f"Elapsed {secs // 60}:{secs % 60:02d}")
            self._handle_messages(h, messages)
            if self.handle is not h:
                return
            if not running:
                # The last message can be queued just after the process is seen gone: look once more.
                self._handle_messages(h, h.poll())
                if self.handle is not h:
                    return
                if self._cancel_thread is None or not self._cancel_thread.is_alive():
                    self._stopped_without_a_word(h)
                    return
        finally:
            if self.handle is h and self._poll_job is None:
                self._schedule_poll()

    def _handle_messages(self, h, messages: list[dict]) -> None:
        for msg in messages:
            if self.handle is not h:    # a final message already ended this run
                break
            try:
                self._on_message(h, msg)
            except Exception as exc:    # noqa: BLE001 - one bad reload must not freeze the window
                self._report_failure(exc)
                if msg.get("type") in ("done", "cancelled", "error") and self.handle is h:
                    self._end_run()

    def _report_failure(self, exc: Exception, what: str = "Could not update the results") -> None:
        """Say so in the status line and the Details log. The status text is kept (progress lines no longer
        replace it) until the run ends."""
        text = f"{what}: {type(exc).__name__}: {exc}"
        self.log_line(text)
        self.status_var.set(text)
        self._sticky_status = True

    def _stopped_without_a_word(self, h) -> None:
        """The worker is gone and never said how it ended. After a Cancel that is a cancel; otherwise an error."""
        try:
            if self._cancelling:
                self._conclude("cancelled")
                return
            self._end_run()
            code = h.returncode
            self.show_error("unexpected", "The check stopped without finishing"
                            + (f" (exit code {code})." if code is not None else "."))
        except Exception as exc:        # noqa: BLE001
            self._report_failure(exc)
            if self.handle is h:
                self._end_run()

    def _note_folder(self, h, msg: dict) -> None:
        folder = msg.get("out_dir") or (str(h.out_dir) if h.out_dir else None)
        if folder:
            self._run_folder = Path(folder)

    def _reload_run(self, *, only_titration: bool = False, ended: str | None = None) -> Results | None:
        """Load the run folder from disk and show it. Returns None when the folder is not readable yet."""
        folder = self._run_folder
        if folder is None:
            return None
        try:
            r = load_results(folder, ended=ended)
        except NotAResultsFolder:
            return None
        before = self.results
        had = before is not None and before.folder == r.folder and len(before.detection) > 0
        self.show_results(r, only_titration=only_titration, keep_tab=had)
        return r

    def _on_message(self, h, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "started":
            self._note_folder(h, msg)
            if self._run_folder is not None:
                self.log_line(f"Results folder: {self._run_folder}")
            self._reload_run()
        elif kind == "progress":
            try:
                fraction = float(msg.get("fraction", 0.0))
            except (TypeError, ValueError):
                fraction = 0.0
            text = str(msg.get("message") or "")
            self.feed_wait(str(msg.get("stage") or ""), fraction, text)   # the one state: bars, step list, banner
            if not self._cancelling and not self._sticky_status:   # these two notes stay until the run is over
                self.status_var.set(text)
            if text and msg.get("stage") != self._last_stage:
                self.log_line(text)                  # one log line per stage, not one per window
            self._last_stage = msg.get("stage")
        elif kind == "partial":
            self._note_folder(h, msg)
            self._reload_run(only_titration=True)    # the Titration tab fills while crosstalk runs
        elif kind == "log":
            self.log_line(str(msg.get("message", "")))
        elif kind in ("done", "cancelled"):
            self._note_folder(h, msg)
            self._conclude(kind)
        elif kind == "example":
            self._example_ready(msg)
        elif kind == "error":
            self._note_folder(h, msg)
            self._on_error(msg)

    def _end_run(self, outcome: str = "error") -> None:
        """The run is over. `outcome` ("done", "cancelled", "error" or "download") is what the step list shows."""
        h = self.handle
        self.handle = None
        self.end_wait(outcome)
        self._cancelling = False
        self._sticky_status = False
        self._cancel_thread = None
        self._downloading_example = False
        if h is not None:
            self._release(h)
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except tk.TclError:
                pass
            self._poll_job = None
        self._busy(False)

    def _conclude(self, kind: str) -> None:
        """The run is over. The folder's own status decides what is shown, not the message type: a cancel that
        lands just as a run finishes must not relabel a finished run."""
        cancelled_by_user = self._cancelling
        downloading = self._downloading_example
        r = self._reload_run(ended="cancelled" if kind == "cancelled" or cancelled_by_user else None)
        state = r.status if r is not None else None
        stopped = kind == "cancelled" or cancelled_by_user
        elapsed = self.elapsed_var.get().replace("Elapsed ", "")
        self._end_run("done" if state == "complete" else "cancelled" if stopped else "error")
        if state == "complete":
            self.progress.configure(value=100)
            self.working_progress.configure(value=100)
            self.status_var.set(self.finished_status(r.folder))
            self.announce_finished(r.folder)
            self.add_banner_note((f"Finished in {elapsed}. " if elapsed else "Finished. ")
                                 + f"Results are saved in {short_path(r.folder, 40)}.")
        elif (kind == "cancelled" or cancelled_by_user) and state in (None, "cancelled", "running", "error"):
            if downloading:
                self.status_var.set("Download cancelled.")
            elif r is None:
                self.status_var.set("Run cancelled before any results were saved.")
            elif len(r.detection) > 0:
                self.status_var.set(f"Run cancelled. The antibody results saved so far stay in {r.folder}.")
            else:
                self.status_var.set(f"Run cancelled. No results were saved yet; the folder stays at {r.folder}.")
        else:
            self.status_var.set("The run did not finish. See the note above the results.")

    def _on_error(self, msg: dict) -> None:
        kind = str(msg.get("kind") or "unexpected")
        text = str(msg.get("message") or "")
        if self._cancelling:                         # the user asked to stop: whatever the worker last said, it is a cancel
            if text:
                self.log_line(text)                  # the status line says cancelled; the real text stays readable
            self._conclude("cancelled")
            return
        self._end_run()
        if kind == "no_nuclear_channel":
            self.status_var.set("Waiting for you to choose the nuclear channel.")
            choice = ask_nuclear_channel(self, list(msg.get("channels") or []))
            if choice:
                self.start_run(nuclear_channel=choice)
            else:
                self.status_var.set("Run stopped: no nuclear channel chosen.")
            return
        self.show_error(kind, text, found=msg.get("found"), needed=msg.get("needed"))
        if kind == "unexpected" and text:
            self.status_var.set(text.splitlines()[0])      # e.g. "Worker stopped unexpectedly (exit code 3)."

    # ---- layout CSV template -------------------------------------------------------
    def _template_say(self, text: str) -> None:
        """Template reader news goes to the status line only while no run is active (it would replace run
        progress); during a run it goes to the Details log."""
        if self.handle is not None:
            self.log_line(text)
        else:
            self.status_var.set(text)

    def _update_template_button(self) -> None:
        """The template is offered while there is an image and no panel file."""
        if self.image_var.get() and not self.panel_var.get().strip():
            self.template_button.grid()
        else:
            self.template_button.grid_remove()

    def save_layout_template(self) -> None:
        """Read the channel names (in the worker), ask where to save, write a marker,cycle,fluor CSV."""
        if self._template_handle is not None:
            return
        text = self.image_var.get().strip()
        image = Path(text) if text else None
        if image is None or not image.exists():
            self._template_say("Choose your image first, then save the template.")
            return
        handle = self.make_handle({"action": "channels", "image": str(image)})
        try:
            handle.start()
        except OSError as exc:
            self._template_say(f"Could not read the channel names: {exc}")
            return
        self._template_handle = handle
        self._template_image = image
        self._template_say("Reading the channel names...")
        self._template_job = self.after(POLL_MS, self._poll_template)

    def _poll_template(self) -> None:
        h = self._template_handle
        if h is None:
            return
        try:
            self._poll_template_once(h)
        except Exception as exc:        # noqa: BLE001 - never leave the reader stuck
            if self._template_handle is h:
                self._template_handle = None
                self._release(h)
            self._template_say(f"Could not read the channel names: {exc}")

    def _poll_template_once(self, h) -> None:
        running = h.is_running()
        outcome = None                    # (names or None, error text or None) once the worker has answered
        for msg in h.poll():
            kind = msg.get("type")
            if kind == "channels":
                outcome = (list(msg.get("names") or []), None)
                break
            if kind == "error":
                outcome = (None, str(msg.get("message") or "the image reader reported an error"))
                break
            if kind == "log":
                self.log_line(str(msg.get("message", "")))
        if outcome is None and not running:
            outcome = (None, "the image reader stopped")
        if outcome is None:
            self._template_job = self.after(POLL_MS, self._poll_template)
            return
        self._template_handle = None
        self._release(h)
        names, error = outcome
        if names is None:
            self._template_say(f"Could not read the channel names: {error}")
        else:
            self._finish_template(names)

    def _finish_template(self, names: list[str]) -> None:
        image = self._template_image
        path = filedialog.asksaveasfilename(
            title="Save the layout CSV template", defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")], initialdir=str(image.parent) if image else None,
            initialfile=f"{image_stem(image)}_layout.csv" if image else "layout.csv")
        if not path:
            self._template_say("Template not saved.")
            return
        try:
            write_layout_template(names, Path(path))
        except OSError as exc:
            self._template_say(f"The template could not be saved: {exc}")
            return
        self._template_say(TEMPLATE_DONE)
