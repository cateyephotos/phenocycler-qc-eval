"""GUI run wiring: Run, Cancel, progress, live results, error banners, layout template."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from conftest import pump_until
from synth import PROTO, make_synthetic_image

pytestmark = pytest.mark.gui


# ---------------------------------------------------------------------------- polling a handle
def test_poll_applies_progress_and_elapsed(app, fake_handle):
    fake_handle.queue([{"type": "progress", "stage": "sampling", "fraction": 0.3, "message": "window 60 of 200"}])
    fake_handle.elapsed = 75.0
    app.handle = fake_handle
    app._poll()
    assert app.progress["value"] == 30 and "window 60 of 200" in app.status_var.get()
    assert app.elapsed_var.get() == "Elapsed 1:15"


def test_started_shows_the_running_state(app, fake_handle, tmp_path):
    folder = tmp_path / "just_started"
    folder.mkdir()
    (folder / "run_info.json").write_text(json.dumps({"status": "running", "crosstalk_state": "pending"}))
    fake_handle.queue([{"type": "started", "out_dir": str(folder)}])
    app.handle = fake_handle
    app._poll()
    app.update()
    assert app.working_state_visible() and not app.banner.winfo_ismapped()      # the working page says it; no banner
    assert "0 to change" not in app.banner_text()


def test_partial_fills_titration_mid_run(app, fake_handle, partial_folder):
    fake_handle.queue([{"type": "partial", "out_dir": str(partial_folder), "early": False}])
    app.handle = fake_handle
    app._poll()
    app.update()
    assert len(app.titration_tab.tree.get_children()) > 0 and "Checking crosstalk" in app.banner_text()


def test_partial_keeps_the_tab_the_user_is_on(app, fake_handle, partial_folder):
    fake_handle.queue([{"type": "partial", "out_dir": str(partial_folder), "early": True}])
    app.handle = fake_handle
    app._poll()
    app.notebook.select(app.crosstalk_tab)                  # the user looks at the Crosstalk tab meanwhile
    app.update()
    fake_handle.queue([{"type": "partial", "out_dir": str(partial_folder), "early": False}])
    app._poll()
    app.update()
    assert app.notebook.select() == str(app.crosstalk_tab)


def test_done_reloads_everything_and_frees_the_run_button(app, fake_handle, complete_folder):
    app.image_var.set(str(complete_folder))
    app.handle = fake_handle
    app._busy(True)
    fake_handle.queue([{"type": "done", "out_dir": str(complete_folder), "summary": {}}])
    app._poll()
    app.update()
    assert "Crosstalk: 5 channel pairs flagged" in app.banner_text()
    assert str(app.cancel_button["state"]) == "disabled" and app.handle is None
    assert app.status_var.get().startswith("Finished")


def test_log_messages_go_to_the_run_log(app, fake_handle):
    fake_handle.queue([{"type": "log", "message": "a line from the worker"}])
    app.handle = fake_handle
    app._poll()
    assert "a line from the worker" in app.log.get("1.0", "end")


def test_worker_that_ends_without_a_final_message_is_an_error(app, fake_handle):
    fake_handle.running = False
    fake_handle.returncode = 3
    app.handle = fake_handle
    app._poll()
    app.update()
    assert "unexpected error" in app.banner_text() and app.handle is None


# ---------------------------------------------------------------------------- errors
def test_no_dapi_opens_picker_and_reruns(app, fake_handle, monkeypatch):
    started = []
    monkeypatch.setattr(app, "start_run", lambda nuclear_channel=None: started.append(nuclear_channel))
    asked = []
    monkeypatch.setattr("phenocycler_qc_eval.gui.run_controller.ask_nuclear_channel",
                        lambda master, ch: asked.append(list(ch)) or "Hoechst")
    fake_handle.queue([{"type": "error", "kind": "no_nuclear_channel", "message": "x",
                        "channels": ["Hoechst", "CD3e"]}])
    app.handle = fake_handle
    app._poll()
    assert started == ["Hoechst"] and asked == [["Hoechst", "CD3e"]]
    assert app.handle is None                                # free again before the rerun starts


def test_no_dapi_picker_cancelled_stops_with_a_plain_status(app, fake_handle, monkeypatch):
    started = []
    monkeypatch.setattr(app, "start_run", lambda nuclear_channel=None: started.append(nuclear_channel))
    monkeypatch.setattr("phenocycler_qc_eval.gui.run_controller.ask_nuclear_channel", lambda master, ch: None)
    fake_handle.queue([{"type": "error", "kind": "no_nuclear_channel", "message": "x", "channels": ["A"]}])
    app.handle = fake_handle
    app._poll()
    assert started == [] and app.status_var.get() == "Run stopped: no nuclear channel chosen."


@pytest.mark.parametrize("kind,headline,button", [
    ("too_little_tissue", "Not enough tissue found", "Open Settings"),
    ("reader", "This image could not be read.", "Choose another image"),
    ("unexpected", "The run stopped because of an unexpected error.", "Open Details"),
])
def test_errors_go_in_banner_with_next_action(app, fake_handle, kind, headline, button):
    fake_handle.queue([{"type": "error", "kind": kind, "message": "found 3 tissue windows, need at least 20"}])
    app.handle = fake_handle
    app._poll()
    app.update()
    assert headline in app.banner_text() and app.banner_action_button["text"] == button
    assert app.banner_action_text()
    if kind == "unexpected":                                  # the worker's own message is the status
        assert app.status_var.get() == "found 3 tissue windows, need at least 20"
    else:
        assert app.status_var.get() == "Run stopped."         # the detail lives in the banner, not the status


def test_error_copy_is_exact(app):
    app.show_error("too_little_tissue", "found 3 tissue windows, need at least 20")
    assert app.banner_text().startswith("Not enough tissue found (3 windows, need 20).")
    assert app.banner_action_text() == ("Check that the right image was chosen, or lower Minimum windows in "
                                        "Settings, Advanced.")
    app.show_error("reader", "Could not open the file.")
    assert app.banner_action_text() == ("Could not open the file. The file may be incomplete or damaged: copy it "
                                        "again from the scanner, then choose it again. Accepted: .qptiff, .tif, "
                                        ".tiff, or a Xenium folder.")
    app.show_error("unexpected", "ValueError: boom")
    assert app.banner_action_text() == "Open Details for the full message."     # nothing saved: no second sentence
    assert "ValueError: boom" in app.log.get("1.0", "end")


def test_error_buttons_do_what_they_say(app, monkeypatch):
    chosen, opened = [], []
    monkeypatch.setattr(app, "choose_image_file", lambda: chosen.append(1))
    monkeypatch.setattr(app, "on_settings", lambda: opened.append(1))
    app.show_error("reader", "x")
    app.banner_action_button.invoke()
    app.show_error("too_little_tissue", "x")
    app.banner_action_button.invoke()
    assert chosen == [1] and opened == [1]
    app.show_error("unexpected", "x")
    assert not app._details_open
    app.banner_action_button.invoke()
    assert app._details_open


def test_error_keeps_antibody_results_visible(app, fake_handle, partial_folder):
    fake_handle.out_dir = partial_folder
    fake_handle.queue([{"type": "error", "kind": "unexpected", "message": "boom"}])
    app.handle = fake_handle
    app._poll()
    app.update()
    assert len(app.titration_tab.tree.get_children()) > 0 and app.notebook.winfo_ismapped()
    assert "unexpected error" in app.banner_text()


def test_error_with_no_results_hides_the_empty_tables(app, fake_handle, tmp_path):
    folder = tmp_path / "no_results"
    folder.mkdir()
    (folder / "run_info.json").write_text(json.dumps({"status": "error"}))
    fake_handle.out_dir = folder
    fake_handle.queue([{"type": "error", "kind": "too_little_tissue", "message": "x", "found": 3, "needed": 20}])
    app.handle = fake_handle
    app._poll()
    app.update()
    assert not app.notebook.winfo_ismapped() and "3 windows, need 20" in app.banner_text()


# ---------------------------------------------------------------------------- start and cancel
def test_start_run_builds_the_request_and_locks_run(app, tmp_path, monkeypatch, fake_handle):
    img = tmp_path / "a.qptiff"
    img.touch()
    app.set_image(img)
    seen = []
    monkeypatch.setattr(app, "make_handle", lambda request: seen.append(request) or fake_handle)
    app.start_run(nuclear_channel="Nuc")
    assert fake_handle.started and app.handle is fake_handle
    assert seen[0]["image"] == str(img) and seen[0]["nuclear_channel"] == "Nuc" and seen[0]["panel"] is None
    assert Path(seen[0]["output_root"]) == tmp_path / "qc_results"
    assert str(app.run_button["state"]) == "disabled" and str(app.cancel_button["state"]) == "normal"
    app.start_run()                                                    # a second press does nothing
    assert len(seen) == 1


def test_image_is_checked_again_right_before_the_run(app, tmp_path, monkeypatch, fake_handle):
    img = tmp_path / "gone.qptiff"
    img.touch()
    app.set_image(img)
    img.unlink()
    monkeypatch.setattr(app, "make_handle", lambda request: fake_handle)
    app.start_run()
    assert app.handle is None and not fake_handle.started
    assert "could not be found" in app.status_var.get()


def test_cancel_run_reloads_the_partial_folder_and_says_what_is_usable(app, fake_handle, cancelled_run):
    app.handle = fake_handle
    fake_handle.out_dir = cancelled_run
    app._busy(True)
    app.cancel_run()
    assert str(app.cancel_button["state"]) == "disabled"
    pump_until(app, lambda: app.handle is None, 10)
    assert fake_handle.cancel_calls == 1
    assert "cancelled" in " ".join(app.banner_notes()).lower() and "cancelled" in app.status_var.get().lower()


def test_cancel_with_nothing_running_does_nothing(app):
    app.status_var.set("Ready to run.")
    app.cancel_run()
    assert str(app.cancel_button["state"]) == "disabled" and app.status_var.get() == "Ready to run."
    assert app.handle is None and not app._cancelling


def test_a_finished_run_is_not_shown_as_cancelled(app, fake_handle, complete_folder):
    """The cancel message can arrive just as the run completes: the folder's status decides."""
    app.handle = fake_handle
    fake_handle.out_dir = complete_folder
    app._busy(True)
    fake_handle.queue([{"type": "cancelled", "out_dir": str(complete_folder)}])
    app._poll()
    app.update()
    assert "cancelled" not in app.status_var.get().lower() and app.status_var.get().startswith("Finished")
    assert not any("cancelled" in n.lower() for n in app.banner_notes())


def test_cancelled_message_with_no_folder_yet(app, fake_handle):
    app.handle = fake_handle
    app._busy(True)
    fake_handle.queue([{"type": "cancelled", "out_dir": None}])
    app._poll()
    assert "cancelled" in app.status_var.get().lower() and app.handle is None


def test_closing_the_window_cancels_a_running_run(app, fake_handle):
    app.handle = fake_handle
    app._busy(True)
    app.destroy()
    assert fake_handle.cancel_calls == 1 and fake_handle.close_calls == 1


def test_closing_the_window_with_no_run_works(app):
    app.destroy()                                      # nothing running: nothing to stop, no error
    assert app.handle is None


def test_closing_the_window_after_a_finished_run_does_not_touch_the_old_handle(app, fake_handle, complete_folder):
    app.handle = fake_handle
    fake_handle.out_dir = complete_folder
    fake_handle.running = False
    app._busy(True)
    fake_handle.queue([{"type": "done", "out_dir": str(complete_folder)}])
    app._poll()
    assert app.handle is None
    app._join_background()
    assert fake_handle.close_calls == 1                 # released when the run ended (threads and pipes)
    app.destroy()
    assert fake_handle.close_calls == 1 and fake_handle.cancel_calls == 0


def test_closing_the_window_closes_the_template_reader_too(app, fake_handle):
    app._template_handle = fake_handle
    app.destroy()
    assert fake_handle.close_calls == 1


def test_worker_death_message_shows_in_the_status_line_and_frees_run(app, fake_handle, partial_folder, tmp_path):
    image = tmp_path / "x.qptiff"
    image.write_bytes(b"")
    app.image_var.set(str(image))
    app.handle = fake_handle
    fake_handle.out_dir = partial_folder
    app._run_folder = partial_folder
    app._busy(True)
    text = "Worker stopped unexpectedly (exit code 3). native crash detail"
    fake_handle.queue([{"type": "error", "kind": "unexpected", "message": text}])
    app._poll()
    app.update()
    assert app.handle is None and app.status_var.get() == text
    assert str(app.run_button["state"]) == "normal" and str(app.cancel_button["state"]) == "disabled"
    assert "unexpected error" in app.banner_text()
    assert len(app.results.detection) > 0              # the folder was reloaded: antibody results stay visible


def test_past_run_cannot_be_loaded_over_a_running_run(app, fake_handle):
    app.handle = fake_handle
    app._busy(True)
    assert str(app.load_button["state"]) == "disabled"
    fake_handle.queue([{"type": "done", "out_dir": None, "summary": {}}])
    app._poll()
    assert str(app.load_button["state"]) == "normal"


# ---------------------------------------------------------------------------- layout template
def test_template_button_only_without_panel(app, tmp_path):
    (tmp_path / "a.qptiff").touch()
    app.set_image(tmp_path / "a.qptiff")
    app.update()
    assert app.template_button.winfo_ismapped() and app.template_button["text"] == "Save a layout CSV template"
    app.panel_var.set(str(tmp_path / "p.xpd"))
    app.update()
    assert not app.template_button.winfo_ismapped()
    app.panel_var.set("")
    app.update()
    assert app.template_button.winfo_ismapped()


def test_template_flow_saves_a_csv_and_says_what_to_do(app, tmp_path, monkeypatch, fake_handle):
    img = tmp_path / "a.qptiff"
    img.touch()
    app.set_image(img)
    target = tmp_path / "mine.csv"
    monkeypatch.setattr("phenocycler_qc_eval.gui.run_controller.filedialog.asksaveasfilename", lambda **kw: str(target))
    requests = []
    monkeypatch.setattr(app, "make_handle", lambda request: requests.append(request) or fake_handle)
    fake_handle.queue([{"type": "channels", "names": ["DAPI", "CD3e", "CD20"]}])
    app.save_layout_template()
    assert requests[0]["action"] == "channels" and requests[0]["image"] == str(img)
    pump_until(app, lambda: target.exists(), 5)
    assert target.read_text().splitlines() == ["marker,cycle,fluor", "CD3e,,", "CD20,,"]
    assert app.status_var.get() == "Template saved. Fill in cycle and fluor, then choose it as the panel file."


def test_template_cancelled_dialog_and_read_error(app, tmp_path, monkeypatch, fake_handle):
    img = tmp_path / "a.qptiff"
    img.touch()
    app.set_image(img)
    monkeypatch.setattr(app, "make_handle", lambda request: fake_handle)
    monkeypatch.setattr("phenocycler_qc_eval.gui.run_controller.filedialog.asksaveasfilename", lambda **kw: "")
    fake_handle.queue([{"type": "channels", "names": ["CD3e"]}])
    app.save_layout_template()
    pump_until(app, lambda: app.status_var.get() == "Template not saved.", 5)
    fake_handle.running = True
    fake_handle.queue([{"type": "error", "kind": "reader", "message": "bad file"}])
    app.save_layout_template()
    pump_until(app, lambda: "bad file" in app.status_var.get(), 5)
    assert app.status_var.get().startswith("Could not read the channel names")


def test_template_needs_an_image_that_exists(app, monkeypatch, fake_handle):
    monkeypatch.setattr(app, "make_handle", lambda request: fake_handle)
    app.save_layout_template()
    assert not fake_handle.started and "image" in app.status_var.get().lower()


def test_crosstalk_tab_offers_the_template_when_crosstalk_is_off(app, run_without_panel, complete_folder):
    app.load_past_run(run_without_panel)
    app.notebook.select(app.crosstalk_tab)             # a tab's widgets are mapped only while it is showing
    app.update()
    assert app.crosstalk_tab.template_button.winfo_ismapped()
    assert app.crosstalk_tab.template_button["text"] == "Save a layout CSV template"
    app.load_past_run(complete_folder)
    app.notebook.select(app.crosstalk_tab)
    app.update()
    assert not app.crosstalk_tab.template_button.winfo_ismapped()


def test_crosstalk_tab_template_button_runs_the_same_flow(app, run_without_panel, monkeypatch):
    calls = []
    monkeypatch.setattr(app, "save_layout_template", lambda: calls.append(1))
    app.load_past_run(run_without_panel)
    app.crosstalk_tab.template_button.invoke()
    assert calls == [1]


# ---------------------------------------------------------------------------- run header and settings in advice
def test_run_that_has_no_results_yet_has_no_zero_count_header(app, tmp_path):
    run = tmp_path / "empty_run"
    run.mkdir()
    (run / "run_info.json").write_text(json.dumps({"status": "running", "image_name": "a.qptiff"}))
    app.load_past_run(run)
    assert "0 antibod" not in app.run_header_var.get() and app.banner_text().startswith("No results yet.")


def test_table_evidence_uses_the_cutoffs_the_run_used(app, tmp_path):
    run = tmp_path / "own_settings"
    shutil.copytree(PROTO, run)
    app.load_past_run(run)
    default_texts = [app.titration_tab.tree.item(i, "values")[2] for i in app.titration_tab.tree.get_children()]
    assert any("Dim:" in t for t in default_texts)
    (run / "run_info.json").write_text(json.dumps(
        {"status": "complete", "crosstalk_state": "on", "settings": {"detection": {"dim_fraction": 0.001}}}))
    app.load_past_run(run)
    own_texts = [app.titration_tab.tree.item(i, "values")[2] for i in app.titration_tab.tree.get_children()]
    assert not any("Dim:" in t for t in own_texts)


# ---------------------------------------------------------------------------- the nuclear channel picker
def test_nuclear_channel_dialog(app):
    from phenocycler_qc_eval.gui.dialogs import NuclearChannelDialog

    dlg = NuclearChannelDialog(app, ["Stain 1", "CD3e", "CD20"])
    app.update()
    assert "This image has no channel named DAPI. Which channel is the nuclear stain?" in dlg.prompt_text()
    assert dlg.channel_list.size() == 3 and dlg.result is None
    assert str(dlg.use_button["state"]) == "disabled"          # nothing is chosen until the user picks one
    dlg.channel_list.selection_set(0)
    dlg.channel_list.event_generate("<<ListboxSelect>>")
    app.update()
    assert dlg.use_button["text"] == "Use this channel" and str(dlg.use_button["state"]) == "normal"
    dlg.use_button.invoke()
    assert dlg.result == "Stain 1"


def test_nuclear_channel_dialog_cancel(app):
    from phenocycler_qc_eval.gui.dialogs import NuclearChannelDialog

    dlg = NuclearChannelDialog(app, ["A", "B"])
    app.update()
    assert dlg.cancel_button["text"] == "Cancel"
    dlg.cancel_button.invoke()
    assert dlg.result is None


# ---------------------------------------------------------------------------- real runs (child process)
@pytest.mark.slow
def test_xenium_run_shows_crosstalk_off_with_template_button(app, tmp_path):
    bundle = make_synthetic_image(tmp_path / "xen", kind="xenium", channels=["DAPI", "CD3e", "CD20", "CD68"])
    app.set_image(bundle)
    app.update()
    assert app.template_button.winfo_ismapped()
    app.start_run()
    pump_until(app, lambda: app.results is not None and app.results.status == "complete", timeout=180)
    assert "no panel file" in app.crosstalk_tab.message_text().lower()
    app.notebook.select(app.crosstalk_tab)
    app.update()
    assert app.crosstalk_tab.template_button.winfo_ismapped()
    assert len(app.titration_tab.tree.get_children()) == 3          # detection calls still run


@pytest.mark.slow
def test_real_run_end_to_end(app, synthetic_qptiff_in_tmp):
    app.set_image(synthetic_qptiff_in_tmp)
    app.start_run()
    pump_until(app, lambda: app.results is not None and app.results.status == "complete", timeout=180)
    assert "to change" in app.banner_text()
    pump_until(app, lambda: app.handle is None, 20)
    assert str(app.run_button["state"]) == "normal" and app.status_var.get().startswith("Finished")


@pytest.mark.slow
def test_cancel_real_run(app, slow_image):
    app.set_image(slow_image)
    app.start_run()
    pump_until(app, lambda: app.results is not None and len(app.results.detection) > 0, 180)   # past the partial
    app.cancel_run()
    pump_until(app, lambda: app.handle is None, 30)
    assert str(app.cancel_button["state"]) == "disabled" and "cancelled" in " ".join(app.banner_notes()).lower()
    assert app.status_var.get().startswith("Run cancelled") and not app.banner_text().startswith("The run stopped")
    assert "Checking crosstalk" not in app.banner_text()
    assert len(app.titration_tab.tree.get_children()) > 0


# ---------------------------------------------------------------------------- the run feedback stays on screen
@pytest.mark.parametrize("scale", [1.0, 1.25])
def test_run_controls_and_feedback_fit_in_the_default_window(app, tmp_path, scale):
    """The output folder, Run, Cancel, progress, status and elapsed time stay inside the left rail."""
    if scale != 1.0:
        app.tk.call("tk", "scaling", float(app.tk.call("tk", "scaling")) * scale)
    (tmp_path / "a.qptiff").touch()
    app.set_image(tmp_path / "a.qptiff")
    app.status_var.set("Reading window 120 of 200 and then some more words to fill two lines of status text")
    app.elapsed_var.set("Elapsed 12:34")
    app.update()
    rail_bottom = app.left.winfo_rooty() + app.left.winfo_height()
    for w in (app.saved_frame, app.run_button, app.cancel_button, app.progress, app.status_label, app.elapsed_label):
        assert w.winfo_rooty() + w.winfo_height() <= rail_bottom, f"{w} ends below the rail ({rail_bottom})"
    assert app.saved_frame.winfo_rooty() < app.run_button.winfo_rooty()     # where results go is chosen before Run


# ---------------------------------------------------------------------------- cancel state (I1, I2, I4, I5)
def _start_cancel(app, handle, folder=None):
    app.handle = handle
    handle.out_dir = folder
    app._run_folder = folder
    app._busy(True)
    app.cancel_run()


def test_cancel_that_queues_nothing_still_ends_as_cancelled(app, cancelled_run):
    from conftest import FakeHandle

    h = FakeHandle(cancel_mode="silent")
    _start_cancel(app, h, cancelled_run)
    pump_until(app, lambda: app.handle is None, 10)
    assert app.status_var.get().startswith("Run cancelled")
    assert "unexpected error" not in app.banner_text() and not app.banner_text().startswith("The run stopped")


def test_error_after_cancel_is_shown_as_cancelled(app, partial_folder):
    from conftest import FakeHandle

    h = FakeHandle(cancel_mode="unexpected")
    _start_cancel(app, h, partial_folder)
    pump_until(app, lambda: app.handle is None, 10)
    assert app.status_var.get().startswith("Run cancelled")
    assert "unexpected error" not in app.banner_text() and app.banner_action_button is None


def test_cancel_does_not_block_and_a_second_click_does_nothing(app, cancelled_run):
    import threading
    import time

    from conftest import FakeHandle

    gate = threading.Event()
    h = FakeHandle(cancel_gate=gate)
    t0 = time.monotonic()
    _start_cancel(app, h, cancelled_run)
    assert time.monotonic() - t0 < 1.0                     # returns at once although cancel() waits for the gate
    assert str(app.cancel_button["state"]) == "disabled" and app.status_var.get() == "Stopping the run..."
    app.cancel_run()
    app.update()
    assert app.handle is h                                  # still stopping; the poll has not ended the run
    gate.set()
    pump_until(app, lambda: app.handle is None, 10)
    assert h.cancel_calls == 1 and app.status_var.get().startswith("Run cancelled")


def test_cancelling_flag_is_cleared_for_the_next_run(app, tmp_path, monkeypatch, cancelled_run):
    from conftest import FakeHandle

    _start_cancel(app, FakeHandle(), cancelled_run)
    pump_until(app, lambda: app.handle is None, 10)
    assert not app._cancelling
    img = tmp_path / "a.qptiff"
    img.touch()
    app.set_image(img)
    nxt = FakeHandle()
    monkeypatch.setattr(app, "make_handle", lambda request: nxt)
    app.start_run()
    nxt.running = False
    app._poll()                                              # a silent death now is an error again, not a cancel
    app.update()
    assert "unexpected error" in app.banner_text()


def test_stopped_run_never_says_checking_crosstalk(app, fake_handle, partial_folder):
    app.handle = fake_handle
    fake_handle.out_dir = partial_folder
    app._busy(True)
    fake_handle.queue([{"type": "cancelled", "out_dir": str(partial_folder)}])
    app._poll()                                              # the folder still says "running" (a hard stop)
    app.update()
    assert "Checking crosstalk" not in app.banner_text() and "still being checked" not in app.banner_text()
    assert any(n.lower().startswith("run cancelled") for n in app.banner_notes())
    assert "Crosstalk is still being checked" not in app.crosstalk_tab.message_text()
    assert app.status_var.get().startswith("Run cancelled") and len(app.titration_tab.tree.get_children()) > 0


def test_dead_worker_never_says_checking_crosstalk(app, fake_handle, partial_folder):
    app.handle = fake_handle
    fake_handle.out_dir = partial_folder
    app._run_folder = partial_folder
    app._busy(True)
    fake_handle.queue([{"type": "error", "kind": "unexpected", "message": "Worker stopped unexpectedly (exit code 3)."}])
    app._poll()
    app.update()
    assert "Checking crosstalk" not in app.banner_text()
    assert any(n.lower().startswith("run failed") for n in app.banner_notes())
    assert "Crosstalk is still being checked" not in app.crosstalk_tab.message_text()


def test_last_message_that_lags_the_exit_is_not_an_error(app, complete_folder):
    from conftest import FakeHandle

    class Lagging(FakeHandle):
        """is_running() is already False but the final message is only readable on the next poll()."""
        def __init__(self):
            super().__init__()
            self.calls = 0

        def poll(self):
            self.calls += 1
            if self.calls == 2:
                self._pending.append({"type": "done", "out_dir": str(complete_folder), "summary": {}})
            return super().poll()

    h = Lagging()
    h.running = False
    app.handle = h
    app._busy(True)
    app._poll()
    app.update()
    assert app.status_var.get().startswith("Finished") and "unexpected error" not in app.banner_text()


def test_closing_the_window_hides_it_before_the_handle_is_closed(app, fake_handle):
    order = []
    app.handle = fake_handle
    app._busy(True)
    app.withdraw = lambda: order.append("withdraw")
    fake_handle.close = lambda: order.append("close")
    app.destroy()
    assert order == ["withdraw", "close"]


# ---------------------------------------------------------------------------- the poll never dies (I3)
def test_a_failing_reload_is_reported_and_polling_continues(app, fake_handle, partial_folder, complete_folder, monkeypatch):
    app.image_var.set(str(complete_folder))
    app.handle = fake_handle
    app._busy(True)
    real = app.show_results

    def broken(*a, **kw):
        raise ValueError("half-written file")

    monkeypatch.setattr(app, "show_results", broken)
    fake_handle.queue([{"type": "partial", "out_dir": str(partial_folder), "early": True}])
    app._poll()
    assert app.handle is fake_handle and app._poll_job is not None            # still polling
    assert "half-written file" in app.status_var.get() and "half-written file" in app.log.get("1.0", "end")
    monkeypatch.setattr(app, "show_results", real)
    fake_handle.queue([{"type": "done", "out_dir": str(complete_folder), "summary": {}}])
    pump_until(app, lambda: app.handle is None, 10)
    assert app.status_var.get().startswith("Finished")


def test_a_failing_final_reload_still_frees_run(app, fake_handle, complete_folder, monkeypatch):
    app.image_var.set(str(complete_folder))
    app.handle = fake_handle
    app._busy(True)
    monkeypatch.setattr(app, "show_results", lambda *a, **kw: (_ for _ in ()).throw(ValueError("bad csv")))
    fake_handle.queue([{"type": "done", "out_dir": str(complete_folder), "summary": {}}])
    app._poll()
    assert app.handle is None and str(app.run_button["state"]) == "normal"
    assert str(app.cancel_button["state"]) == "disabled" and "bad csv" in app.status_var.get()


def test_banner_shows_at_most_three_actions(app):
    app.set_banner_actions([("One", lambda: None), ("Two", lambda: None), ("Three", lambda: None),
                            ("Four", lambda: None)])
    assert len(app.banner_action_buttons) == 3


def test_details_toggle_is_called_details(app):
    assert app.details_button["text"] == "Details"
    app.toggle_details()
    assert app.details_button["text"] == "Hide details"
