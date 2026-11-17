"""Try the example: get the example image (in the worker process), fill in the inputs and start the run.

The download never happens on the Tk thread: the worker's `download_example` action does it and the normal
poll loop (run_controller.py) picks up its progress and its answer. When the image cannot be had (the release is
not published yet, or this computer is offline) the banner says where to get it by hand.
"""

from __future__ import annotations

from pathlib import Path

from .. import example


class ExampleMixin:
    """The Try the example button for App."""

    def try_example(self) -> None:
        if self.handle is not None:
            return
        handle = self.make_handle({"action": "download_example"})
        self._run_folder = None
        self._last_stage = None
        self._cancelling = False
        self._sticky_status = False
        self._cancel_thread = None
        self.progress.configure(value=0)
        self._error_seen = False
        self.elapsed_var.set("Elapsed 0:00")
        self.status_var.set(f"Downloading the example (about {example.EXAMPLE_SIZE_MB} MB)...")
        try:
            handle.start()
        except OSError as exc:
            self.status_var.set(f"The example could not be fetched: {exc}")
            return
        self.handle = handle
        self._downloading_example = True
        self.begin_wait("download")
        self.feed_wait("download", 0.0, f"Downloading the example (about {example.EXAMPLE_SIZE_MB} MB)...")
        self._busy(True)
        self._schedule_poll()

    def _example_ready(self, msg: dict) -> None:
        """The worker has the example image: fill in the inputs and start the run. The worker has already
        checked the file (checksum), so it is not looked up again here."""
        if self._cancelling:                       # Cancel was pressed just before the answer was read
            self._conclude("cancelled")
            return
        image, panel = Path(str(msg["image"])), Path(str(msg["panel"]))
        self._end_run("download")
        self.image_var.set(str(image))
        self.panel_var.set(str(panel))
        if not self._output_user_set:
            self._set_output(image.parent / "qc_results")
        self.log_line(f"Example image: {image}")
        self._begin_run(image, panel)

    def copy_example_link(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(example.EXAMPLE_URL)
        self.status_var.set("Link copied.")

    def open_example_folder(self) -> None:
        try:
            example.open_folder(example.example_dir())
        except OSError as exc:
            self.status_var.set(f"The folder could not be opened: {exc}")
