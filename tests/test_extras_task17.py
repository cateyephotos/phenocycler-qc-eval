"""Carried-over review items (Task 15 re-review N1-N5, Task 15 Minors 4, 5, 7, 8, 9)."""

from __future__ import annotations

import json
import threading
import time

import pytest
from conftest import FakeHandle


# ---------------------------------------------------------------------------- GUI items
@pytest.mark.gui
def test_poll_survives_a_handle_that_raises(app, fake_handle, tmp_path):
    """N1: one exception from is_running()/poll() must not leave Run disabled or the loop spinning."""
    image = tmp_path / "x.qptiff"
    image.write_bytes(b"")
    app.image_var.set(str(image))

    def boom():
        raise RuntimeError("pipe went away")

    fake_handle.is_running = boom
    app.handle = fake_handle
    app._busy(True)
    app._poll()
    assert app.handle is None and app._poll_job is None
    assert str(app.run_button["state"]) == "normal"
    assert "pipe went away" in app.status_var.get() and "pipe went away" in app.log.get("1.0", "end")


class _OrderHandle(FakeHandle):
    def __init__(self):
        super().__init__(cancel_mode="silent", cancel_gate=threading.Event())
        self.events: list[str] = []

    def cancel(self):
        self.events.append("cancel_start")
        self.cancel_gate.wait(10)
        self.running = False
        self.events.append("cancel_end")

    def close(self):
        self.events.append("close")


@pytest.mark.gui
def test_destroy_waits_for_a_live_cancel_before_closing(app):
    """N2: close() must not overlap a running cancel thread (bounded wait)."""
    h = _OrderHandle()
    app.handle = h
    app._busy(True)
    app.cancel_run()
    time.sleep(0.2)                                   # the cancel thread is now blocked in cancel()
    threading.Timer(0.3, h.cancel_gate.set).start()
    began = time.monotonic()
    app.destroy()
    assert h.events == ["cancel_start", "cancel_end", "close"]
    assert time.monotonic() - began < 5


@pytest.mark.gui
def test_destroy_is_bounded_when_cancel_hangs(app, monkeypatch):
    from phenocycler_qc_eval.gui import app as app_mod

    monkeypatch.setattr(app_mod, "CANCEL_JOIN_S", 0.3)
    h = _OrderHandle()
    app.handle = h
    app._busy(True)
    app.cancel_run()
    time.sleep(0.2)
    began = time.monotonic()
    app.destroy()
    assert time.monotonic() - began < 3 and h.events[-1] == "close"
    h.cancel_gate.set()


@pytest.mark.gui
def test_error_after_cancel_stays_cancelled_but_the_text_is_logged(app, fake_handle, partial_folder):
    """N3: the status line says cancelled; the real error text is kept in the Details log."""
    fake_handle.out_dir = partial_folder
    app._run_folder = partial_folder
    app.handle = fake_handle
    app._busy(True)
    app._cancelling = True
    fake_handle.queue([{"type": "error", "kind": "unexpected", "message": "ValueError: real failure"}])
    app._poll()
    assert app.status_var.get().startswith("Run cancelled")
    assert "ValueError: real failure" in app.log.get("1.0", "end")


@pytest.mark.gui
def test_cancel_thread_is_cleared_between_runs(app, fake_handle):
    """N4."""
    app.handle = fake_handle
    app._busy(True)
    app.cancel_run()
    app._join_background()
    app._poll()
    app._poll()
    assert app.handle is None and app._cancel_thread is None


@pytest.mark.gui
def test_reload_failure_text_stays_until_the_run_ends(app, fake_handle, partial_folder, monkeypatch):
    """N5: a progress line must not wipe the 'could not update' status."""
    app.handle = fake_handle
    app._run_folder = partial_folder
    fake_handle.out_dir = partial_folder

    def bad_reload(**kw):
        raise OSError("file is locked")

    monkeypatch.setattr(app, "_reload_run", bad_reload)
    fake_handle.queue([{"type": "partial", "out_dir": str(partial_folder), "early": False}])
    app._poll()
    assert "Could not update the results" in app.status_var.get()
    fake_handle.queue([{"type": "progress", "stage": "crosstalk", "fraction": 0.7, "message": "Checking crosstalk: fine detail, window 7 of 10"}])
    app._poll()
    assert "Could not update the results" in app.status_var.get()
    assert app.progress["value"] == 70                       # progress itself still moves


@pytest.mark.gui
def test_error_copy_drops_the_saved_results_sentence_when_nothing_was_saved(app):
    """Minor 7."""
    app._run_folder = None
    app.show_error("unexpected", "ValueError: boom")
    assert app.banner_action_text() == "Open Details for the full message."


@pytest.mark.gui
def test_error_copy_keeps_the_saved_results_sentence_when_results_exist(app, partial_folder):
    app._run_folder = partial_folder
    app.show_error("unexpected", "ValueError: boom")
    assert app.banner_action_text() == ("Open Details for the full message. "
                                        "Antibody results saved before the error are shown below.")


@pytest.mark.gui
def test_titration_selection_survives_a_reload(app, complete_folder):
    """Minor 8."""
    from phenocycler_qc_eval.results import load_results

    r = load_results(complete_folder)
    app.show_results(r)
    tree = app.titration_tab.tree
    pick = tree.get_children()[3]
    tree.selection_set(pick)
    name = tree.item(pick, "values")[0]
    app.show_results(r, keep_tab=True)               # a reload of the run on screen
    sel = tree.selection()
    assert sel and tree.item(sel[0], "values")[0] == name


@pytest.mark.gui
def test_template_status_does_not_overwrite_run_progress(app, fake_handle, tmp_path):
    """Minor 9: while a run is active the template reader writes to the Details log, not the status line."""
    app.handle = FakeHandle()
    app.status_var.set("Reading window 30 of 200")
    reader = FakeHandle()
    reader.queue([{"type": "error", "message": "the reader failed"}])
    app._template_handle = reader
    app._poll_template()
    assert app.status_var.get() == "Reading window 30 of 200"
    assert "the reader failed" in app.log.get("1.0", "end")


# ---------------------------------------------------------------------------- engine items
def test_replace_retries_a_busy_file(tmp_path, monkeypatch):
    """Minor 4: on Windows os.replace fails while the GUI has the file open; retry briefly."""
    import os

    from phenocycler_qc_eval import run as run_mod

    real = os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError("in use")
        return real(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    run_mod._write_json(tmp_path / "a.json", {"k": 1})
    assert json.loads((tmp_path / "a.json").read_text()) == {"k": 1} and calls["n"] == 3


def test_replace_gives_up_after_a_few_tries(tmp_path, monkeypatch):
    import os

    from phenocycler_qc_eval import run as run_mod

    def always(src, dst):
        raise PermissionError("in use")

    monkeypatch.setattr(os, "replace", always)
    with pytest.raises(PermissionError):
        run_mod._write_json(tmp_path / "a.json", {"k": 1})


def test_no_panel_run_says_crosstalk_is_off_from_the_start(tmp_path, synthetic_qptiff_in_tmp):
    """Minor 5."""
    from phenocycler_qc_eval.crosstalk import NO_LAYOUT_MESSAGE
    from phenocycler_qc_eval.run import RunRequest, run_qc
    from synth import small_settings

    seen = {}

    def on_started(folder):
        seen.update(json.loads((folder / "run_info.json").read_text()))

    run_qc(RunRequest(synthetic_qptiff_in_tmp, None, tmp_path / "out", small_settings(), on_started=on_started))
    assert seen["crosstalk_state"] == "off" and seen["crosstalk_message"] == NO_LAYOUT_MESSAGE
