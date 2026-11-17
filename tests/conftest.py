"""Shared fixtures. Each task adds the fixtures its own tests need."""

from __future__ import annotations

import json

import pytest

from phenocycler_qc_eval.results import load_results, safe_name
from synth import PROTO, copy_only


@pytest.fixture
def results_no_panel_float(tmp_path):
    """A finished run with no panel file (crosstalk off) on a float image."""
    run = copy_only(PROTO, tmp_path, ["stain_qc_channels.csv"])
    (run / "run_info.json").write_text(
        json.dumps(
            {
                "status": "complete",
                "crosstalk_state": "off",
                "crosstalk_message": "no panel file",
                "saturation_checked": False,
                "unmatched": [],
                "settings": {},
            }
        )
    )
    return load_results(run)


def new_app(attempts: int = 3):
    """Create a main window. Tk start-up fails sporadically on this workstation
    (a transient tk.tcl error), so retry before concluding there is no display."""
    import tkinter as tk
    import time

    from phenocycler_qc_eval.gui.app import App

    last = None
    for _ in range(attempts):
        try:
            return App()
        except tk.TclError as exc:
            last = exc
            time.sleep(1)
    pytest.skip(f"no display for Tkinter: {last}")


@pytest.fixture(scope="session", autouse=True)
def _no_tab_warming():
    """Laying every tab out at start (what the real window does so the first results arrive quickly) costs about three
    seconds a window on a slow machine, and the tests open hundreds of windows."""
    try:
        from phenocycler_qc_eval.gui.results_pane import ResultsPaneMixin
    except ImportError:          # no GUI toolkit here: nothing to switch off
        yield
        return
    ResultsPaneMixin.WARM_TABS = False
    yield
    ResultsPaneMixin.WARM_TABS = True


@pytest.fixture(autouse=True)
def _pictures_on_the_calling_thread(monkeypatch):
    """The older tests ask for a picture and look at it straight away, so by default a picture is built and drawn on the
    calling thread and nothing is made ahead of time. The pass 4 tests turn the background thread on (`background_pictures`)."""
    try:
        from phenocycler_qc_eval.gui.evidence import EvidenceCanvas
    except ImportError:          # no GUI toolkit here: nothing to switch
        yield
        return
    monkeypatch.setattr(EvidenceCanvas, "ASYNC", False, raising=False)
    monkeypatch.setattr(EvidenceCanvas, "PREFETCH", False, raising=False)
    yield


@pytest.fixture
def background_pictures(monkeypatch):
    """Pictures are made in the background thread, as in the real window (what a click sees: a loading state, then the
    picture and its words together)."""
    from phenocycler_qc_eval.gui.evidence import EvidenceCanvas

    monkeypatch.setattr(EvidenceCanvas, "ASYNC", True, raising=False)
    monkeypatch.setattr(EvidenceCanvas, "PREFETCH", False, raising=False)


@pytest.fixture(autouse=True)
def _own_config_folder(monkeypatch, tmp_path_factory):
    """Never read or write the real per-user settings file: every test gets its own empty config folder."""
    monkeypatch.setenv("PQE_CONFIG_DIR", str(tmp_path_factory.mktemp("config")))


@pytest.fixture
def root():
    """A hidden Tk root for dialogs that need a parent but not the main window."""
    import tkinter as tk
    import time

    last = None
    r = None
    for _ in range(3):
        try:
            r = tk.Tk()
            break
        except tk.TclError as exc:
            last = exc
            time.sleep(1)
    if r is None:
        pytest.skip(f"no display for Tkinter: {last}")
    r.withdraw()
    yield r
    try:
        r.destroy()
    except tk.TclError:
        pass


@pytest.fixture
def app(monkeypatch, tmp_path):
    """A main window with every control wired on, remembered folders in tmp_path."""
    import tkinter as tk

    from phenocycler_qc_eval.gui.app import App

    monkeypatch.setenv("PQE_SETTINGS_DIR", str(tmp_path / "_settings"))
    monkeypatch.setattr(App, "WIRED", {"run": True, "settings": True, "example": True})
    a = new_app()
    a.update()
    yield a
    try:
        a.destroy()
    except tk.TclError:
        pass


@pytest.fixture
def run_with_previews(tmp_path):
    """The prototype run plus synthetic previews for every antibody and TOX -> CD163."""
    import shutil

    from synth import write_antibody_preview, write_followup_preview

    run = tmp_path / "run_previews"
    shutil.copytree(PROTO, run)
    for marker in load_results(run).detection["channel"]:
        write_antibody_preview(run / "previews" / f"antibody_{safe_name(marker)}.npz", marker)
    write_followup_preview(run / "previews" / "followup_TOX_to_CD163.npz", "TOX", "CD163")
    return run


@pytest.fixture
def run_without_panel(tmp_path):
    """A finished run with no panel file: no crosstalk pairs CSV, crosstalk off."""
    import shutil

    run = tmp_path / "run_no_panel"
    shutil.copytree(PROTO, run)
    (run / "stain_qc_crosstalk_pairs.csv").unlink()
    (run / "run_info.json").write_text(
        json.dumps({"status": "complete", "crosstalk_state": "off",
                    "crosstalk_message": "no panel file", "settings": {}}))
    return run


@pytest.fixture
def cancelled_run(tmp_path):
    """Only the channels CSV and a cancelled marker."""
    run = copy_only(PROTO, tmp_path, ["stain_qc_channels.csv"], folder_name="run_cancelled")
    (run / "RUN_INCOMPLETE.txt").write_text("Run cancelled by the user.")
    return run


@pytest.fixture
def synthetic_qptiff(tmp_path):
    """A small 3-channel QPTIFF (DAPI, CD31, CD4), 1024 px, 0.5 um, uint8, two pyramid levels."""
    from synth import make_synthetic_image

    return make_synthetic_image(tmp_path / "s.qptiff", channels=["DAPI", "CD31", "CD4"], size_px=1024)


@pytest.fixture
def xpd_file(tmp_path):
    """Panel file: blank well H3, then C1 and C2 with three fluors each."""
    from synth import write_xpd

    blank = [("--", "ATTO550"), ("--", "CY5"), ("--", "AF750")]
    c1 = [("CD31", "ATTO550"), ("CD4", "CY5"), ("CD20", "AF750")]
    c2 = [("CD44", "ATTO550"), ("FOXP3", "CY5"), ("SMA", "AF750")]
    return write_xpd(tmp_path / "panel.xpd", [("H3", blank), ("C1", c1), ("C2", c2)])


@pytest.fixture(scope="session")
def sampling_qptiff(tmp_path_factory):
    """A 3-channel QPTIFF big enough for 30 non-overlapping 224 px windows (2048 px, 0.5 um, uint8).

    Built once per test run; tests only read it.
    """
    from synth import make_synthetic_image

    return make_synthetic_image(tmp_path_factory.mktemp("sampling") / "big.qptiff",
                                channels=["DAPI", "CD31", "CD4"], size_px=2048)


# ---------------------------------------------------------------------------- previews and figures
@pytest.fixture(scope="session")
def small_sample():
    from test_crosstalk import build
    return build()


@pytest.fixture(scope="session")
def small_det(small_sample):
    from phenocycler_qc_eval.detection import detection_table
    from phenocycler_qc_eval.settings import DetectionSettings
    return detection_table(small_sample, DetectionSettings())


@pytest.fixture(scope="session")
def leak_sample():
    from test_crosstalk import build
    return build(leaks=[("CD20", "CD31", 0.10), ("CD4", "FOXP3", 0.10)])


@pytest.fixture(scope="session")
def leak_det(leak_sample):
    from phenocycler_qc_eval.detection import detection_table
    from phenocycler_qc_eval.settings import DetectionSettings
    return detection_table(leak_sample, DetectionSettings())


@pytest.fixture(scope="session")
def xt_result(leak_sample, leak_det):
    from test_crosstalk import layout
    from phenocycler_qc_eval.crosstalk import run_crosstalk
    from phenocycler_qc_eval.settings import CrosstalkSettings
    return run_crosstalk(leak_sample, leak_det, layout(), CrosstalkSettings(), 2.0)


@pytest.fixture(scope="session")
def xt_off():
    from phenocycler_qc_eval.crosstalk import NO_LAYOUT_MESSAGE, CrosstalkResult
    return CrosstalkResult(enabled=False, message=NO_LAYOUT_MESSAGE)


# ---------------------------------------------------------------------------- whole runs (Task 13)
_RUN_LAYOUT = [  # marker, cycle, fluor
    ("CD31", 1, "ATTO550"), ("CD4", 1, "CY5"), ("CD20", 1, "AF750"),
    ("CD44", 2, "ATTO550"), ("FOXP3", 2, "CY5"), ("SMA", 2, "AF750"),
    ("KI67", 4, "ATTO550"), ("CD8", 4, "CY5"), ("CD21", 4, "AF750"),
]


@pytest.fixture(scope="session")
def finished_run(tmp_path_factory):
    """run_qc on a 10-channel synthetic QPTIFF with a layout CSV and CD4 carrying over into FOXP3."""
    from synth import make_layout_csv, make_synthetic_image, small_settings

    from phenocycler_qc_eval.run import RunRequest, run_qc

    root = tmp_path_factory.mktemp("finished")
    channels = ["DAPI"] + [m for m, _, _ in _RUN_LAYOUT]
    image = make_synthetic_image(root / "full.qptiff", channels=channels, size_px=2048,
                                 leaks={("CD4", "FOXP3"): 0.8})
    layout = make_layout_csv(root / "layout.csv", _RUN_LAYOUT)
    return run_qc(RunRequest(image, layout, root / "out", small_settings(n_windows=24)))


@pytest.fixture
def synthetic_qptiff_in_tmp(tmp_path):
    """A 2048 px, 3-channel QPTIFF alone in its own folder (enough tissue windows for the defaults)."""
    from synth import make_synthetic_image

    folder = tmp_path / "scan_folder"
    folder.mkdir()
    return make_synthetic_image(folder / "scan.qptiff", channels=["DAPI", "CD31", "CD4"], size_px=2048)


# ---------------------------------------------------------------------------- GUI run wiring (Task 15)
def pump_until(app, condition, timeout: float = 60.0) -> None:
    """Run the Tk event loop (so the app's own polling fires) until condition() is true."""
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.update()
        if condition():
            return
        time.sleep(0.05)
    raise AssertionError(f"condition not met within {timeout:g} s")


class FakeHandle:
    """Stands in for WorkerHandle: you feed it messages; nothing runs."""

    def __init__(self, cancel_mode: str = "message", cancel_gate=None) -> None:
        # cancel_mode: "message" queues `cancelled`; "silent" queues nothing; "unexpected" queues error/unexpected
        # (a worker that died while being stopped). cancel_gate (a threading.Event) makes cancel() block until set.
        self.cancel_mode = cancel_mode
        self.cancel_gate = cancel_gate
        self._pending: list[dict] = []
        self.elapsed = 0.0
        self.out_dir = None
        self.running = True
        self.cancel_calls = 0
        self.close_calls = 0
        self.started = False
        self.returncode = None

    def queue(self, messages) -> None:
        self._pending.extend(messages)

    def start(self) -> None:
        self.started = True

    def poll(self) -> list[dict]:
        out, self._pending = self._pending, []
        return out

    def is_running(self) -> bool:
        return self.running

    def elapsed_s(self) -> float:
        return self.elapsed

    def close(self) -> None:
        """Like WorkerHandle.close(): stops a running worker, harmless otherwise."""
        self.close_calls += 1
        if self.running:
            self.cancel()

    def cancel(self) -> None:
        self.cancel_calls += 1
        if self.cancel_gate is not None:
            self.cancel_gate.wait(10)
        self.running = False
        if self.cancel_mode == "message":
            self._pending.append({"type": "cancelled", "out_dir": str(self.out_dir) if self.out_dir else None})
        elif self.cancel_mode == "unexpected":
            self._pending.append({"type": "error", "kind": "unexpected",
                                  "message": "Worker stopped unexpectedly (exit code 1)."})


@pytest.fixture
def fake_handle():
    return FakeHandle()


@pytest.fixture
def partial_folder(tmp_path):
    """A run still going: antibody results written, crosstalk not yet."""
    from synth import PROTO as proto, copy_only as copy

    run = copy(proto, tmp_path, ["stain_qc_channels.csv"], folder_name="run_partial")
    (run / "RUN_INCOMPLETE.txt").write_text("Run in progress (running).")
    (run / "run_info.json").write_text(json.dumps({"status": "running", "crosstalk_state": "pending"}))
    return run


@pytest.fixture
def complete_folder(tmp_path):
    """A finished run (prototype copy marked complete)."""
    import shutil

    run = tmp_path / "run_complete"
    shutil.copytree(PROTO, run)
    (run / "run_info.json").write_text(json.dumps({"status": "complete", "crosstalk_state": "on"}))
    return run


@pytest.fixture
def slow_image(synthetic_qptiff_in_tmp, monkeypatch):
    """An image whose worker holds still after the antibody results, so a test can cancel mid-run."""
    monkeypatch.setenv("PQE_TEST_SLEEP_AFTER_PARTIAL", "300")
    return synthetic_qptiff_in_tmp


# ---------------------------------------------------------------------------- example image (Task 17)
class _LocalFile:
    """A small file served over HTTP from 127.0.0.1 (no outside network)."""

    def __init__(self, folder, name: str = "tonsil31_crop.ome.tif", size: int = 3_000_000) -> None:
        import hashlib
        import http.server
        import threading
        from functools import partial

        import numpy as np

        self.path = folder / name
        self.path.write_bytes(np.random.default_rng(7).integers(0, 256, size, dtype=np.uint8).tobytes())
        self.sha256 = hashlib.sha256(self.path.read_bytes()).hexdigest()

        class Quiet(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(folder)))
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/{name}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self._stopped = False

    def stop(self) -> None:
        if not self._stopped:
            self._stopped = True
            self._server.shutdown()
            self._server.server_close()


@pytest.fixture
def local_http_file(tmp_path):
    served = tmp_path / "served"
    served.mkdir()
    f = _LocalFile(served)
    yield f
    f.stop()


class FakeHandleFactory:
    """Replaces App.make_handle: hands out FakeHandles, each primed with the next queued messages."""

    def __init__(self) -> None:
        self.started: list[dict] = []     # the request of every handle that was started
        self.handles: list[FakeHandle] = []
        self._messages: list[list[dict]] = []

    def next_messages(self, messages) -> None:
        self._messages.append(list(messages))

    def __call__(self, request: dict) -> FakeHandle:
        factory = self

        class _Handle(FakeHandle):
            def start(self) -> None:
                super().start()
                factory.started.append(request)

        h = _Handle()
        if self._messages:
            h.queue(self._messages.pop(0))
        self.handles.append(h)
        return h


@pytest.fixture
def fake_handle_factory(app, monkeypatch):
    factory = FakeHandleFactory()
    monkeypatch.setattr(app, "make_handle", factory)
    return factory
