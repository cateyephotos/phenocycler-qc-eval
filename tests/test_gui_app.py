from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import new_app
from synth import PROTO

pytestmark = pytest.mark.gui

tk = pytest.importorskip("tkinter")
from phenocycler_qc_eval.gui import rail as rail_mod  # noqa: E402
from phenocycler_qc_eval.gui.app import App  # noqa: E402


def test_empty_state_shows_first_instruction(app):
    assert app.empty_state_visible() and "Choose your image" in app.empty_state_text()
    assert str(app.run_button["state"]) == "disabled"


def test_empty_state_drops_example_sentence_when_unwired(monkeypatch, tmp_path):
    monkeypatch.setenv("PQE_SETTINGS_DIR", str(tmp_path))
    monkeypatch.setattr(App, "WIRED", {"run": True, "settings": True, "example": False})
    a = new_app()
    try:
        assert "example" not in a.empty_state_text()
    finally:
        a.destroy()


def test_empty_state_mentions_example_when_wired(app):
    assert "Try the example" in app.empty_state_text()


def test_run_enabled_with_image_alone(app, tmp_path):
    img = tmp_path / "a.qptiff"; img.touch()
    app.set_image(img); app.update()
    assert str(app.run_button["state"]) == "normal"
    assert Path(app.output_var.get()) == tmp_path / "qc_results"
    assert app.saved_to_var.get() == "Results are saved next to your image."      # the whole path is the tooltip
    assert "crosstalk will be off" in app.status_var.get()


def test_lone_xpd_is_picked(app, tmp_path):
    (tmp_path / "a.qptiff").touch(); (tmp_path / "p.xpd").touch()
    app.set_image(tmp_path / "a.qptiff"); app.update()
    assert Path(app.panel_var.get()).name == "p.xpd"
    assert "p.xpd" in app.status_var.get()


def test_two_xpd_files_not_guessed(app, tmp_path):
    for n in ("a.qptiff", "p.xpd", "q.xpd"): (tmp_path / n).touch()
    app.set_image(tmp_path / "a.qptiff"); assert app.panel_var.get() == ""


def test_existing_panel_not_overwritten(app, tmp_path):
    (tmp_path / "a.qptiff").touch(); (tmp_path / "p.xpd").touch()
    app.panel_var.set("mine.xpd")
    app.set_image(tmp_path / "a.qptiff")
    assert app.panel_var.get() == "mine.xpd"


def test_changed_output_survives_new_image(app, tmp_path, monkeypatch):
    out = tmp_path / "elsewhere"; out.mkdir()
    monkeypatch.setattr(rail_mod.filedialog, "askdirectory", lambda **kw: str(out))
    app.change_output_button.invoke()
    (tmp_path / "a.qptiff").touch()
    app.set_image(tmp_path / "a.qptiff")
    assert Path(app.output_var.get()) == out


def test_missing_image_keeps_run_disabled(app, tmp_path):
    app.set_image(tmp_path / "nope.qptiff")
    assert str(app.run_button["state"]) == "disabled"
    assert "not found" in app.status_var.get()


def test_details_hidden_by_default(app):
    assert not app.log.winfo_ismapped()
    app.details_button.invoke(); app.update(); assert app.log.winfo_ismapped()
    app.details_button.invoke(); app.update(); assert not app.log.winfo_ismapped()


def test_load_past_run_fills_banner(app):
    app.load_past_run(PROTO); app.update()
    assert "Crosstalk: 5 channel pairs flagged" in app.banner_text() and not app.empty_state_visible()
    assert app.banner.winfo_ismapped() and app.notebook.winfo_ismapped()


def test_load_bad_folder_message(app, tmp_path):
    app.load_past_run(tmp_path); assert app.status_var.get() == "That folder is not a past run."
    assert app.empty_state_visible()


def test_banner_takes_three_action_buttons(app):
    app.set_banner_actions([("One", lambda: None), ("Two", lambda: None), ("Three", lambda: None),
                            ("Four", lambda: None)])
    assert [b["text"] for b in app.banner_action_buttons] == ["One", "Two", "Three"]


def test_button_labels(app):
    assert app.load_button["text"] == "Load a past run" and app.example_button["text"] == "Try the example"
    assert app.change_output_button["text"] == "Change"
    assert app.choose_file_button["text"] == "Choose file"
    assert app.choose_folder_button["text"] == "Choose folder"


def test_xenium_folder_is_accepted(app, tmp_path):
    bundle = tmp_path / "xen"; (bundle / "morphology_focus").mkdir(parents=True)
    app.set_image(bundle); app.update()
    assert str(app.run_button["state"]) == "normal" and Path(app.output_var.get()) == tmp_path / "qc_results"


def test_last_used_folders_are_remembered(app, tmp_path):
    (tmp_path / "a.qptiff").touch()
    app.set_image(tmp_path / "a.qptiff")
    saved = json.loads((tmp_path / "_settings" / "gui_state.json").read_text())
    assert saved["image_dir"] == str(tmp_path)


def test_unwired_controls_are_hidden(app, monkeypatch):
    monkeypatch.setattr(App, "WIRED", {"run": False, "settings": False, "example": False})
    a = new_app()
    try:
        a.update()
        assert a.unwired_widgets() and not any(w.winfo_ismapped() for w in a.unwired_widgets())
    finally:
        a.destroy()


def test_past_run_names_what_is_on_screen(app):
    app.load_past_run(PROTO); app.update()
    assert app.run_header_var.get().startswith("prototype_run   |   ")
    assert app.run_header_var.get().endswith("31 antibodies")
    assert app.image_var.get() == ""                 # no image path was recorded: nothing to run again, and Run says why
    assert app.status_var.get().startswith("Showing saved run: prototype_run")
    assert "Loaded demo" not in app.status_var.get()


def test_past_run_uses_recorded_image_path(app, tmp_path):
    import shutil
    run = tmp_path / "Tonsil_20261110-143200"
    shutil.copytree(PROTO, run)
    (run / "run_info.json").write_text(json.dumps(
        {"status": "complete", "crosstalk_state": "on", "image_path": str(tmp_path / "Tonsil.qptiff")}))
    (tmp_path / "Tonsil.qptiff").write_bytes(b"x")
    app.load_past_run(run); app.update()
    assert app.image_var.get() == str(tmp_path / "Tonsil.qptiff")
    assert app.run_header_var.get().startswith("Tonsil.qptiff   |   10 Nov 2026 14:32")


def test_banner_has_two_lines_and_lines_jump_to_tabs(app):
    app.load_past_run(PROTO); app.update()
    assert app.banner_text().count("\n") == 1
    app.banner_label2.event_generate("<Button-1>"); app.update()
    assert app.notebook.select() == str(app.crosstalk_tab)
    app.banner_label.event_generate("<Button-1>"); app.update()
    assert app.notebook.select() == str(app.titration_tab)


def test_tabs_carry_counts(app):
    app.load_past_run(PROTO); app.update()
    texts = [app.notebook.tab(t, "text") for t in app.notebook.tabs()]
    assert texts == ["Titration (31)", "Crosstalk (5)", "Figures (1)"]      # the count is the figures listed (E3)
    assert app.crosstalk_tab.pair_count() == 5


def test_sash_is_placed_even_when_the_window_opens_narrow(app):
    app.geometry("420x700"); app.update()
    assert app._sash_set


def test_empty_state_has_heading_types_and_button(app):
    text = app.empty_state_text()
    assert "Check a stained section" in text and ".qptiff" in text and "Xenium" in text
    assert app.empty_button["text"] == "Choose your image"


def test_left_rail_is_narrow(app):
    import time

    end = time.monotonic() + 10
    while time.monotonic() < end and not app._sash_set:          # the sash goes to the rail's width on the first layout
        app.update()
        time.sleep(0.02)
    app.update()
    assert app.left.winfo_width() <= 300, (
        f"rail {app.left.winfo_width()} px; sash set {app._sash_set}, sash at {app.paned.sashpos(0)}, "
        f"pane {app.paned.winfo_width()} px, window {app.winfo_width()} px, scaling {app.tk.call('tk', 'scaling')}, "
        f"rail asks for {app.left.winfo_reqwidth()} px")


def test_collapsed_log_holds_no_spare_height(app):
    app.update()
    assert int(app.inputs.grid_rowconfigure(app._log_row)["weight"]) == 0
    app.details_button.invoke(); app.update()
    assert int(app.inputs.grid_rowconfigure(app._log_row)["weight"]) == 1
    app.details_button.invoke(); app.update()
    assert int(app.inputs.grid_rowconfigure(app._log_row)["weight"]) == 0


def test_run_log_button_hidden_until_run_is_wired(monkeypatch, tmp_path):
    monkeypatch.setenv("PQE_SETTINGS_DIR", str(tmp_path))
    monkeypatch.setattr(App, "WIRED", {"run": False, "settings": False, "example": False})
    a = new_app()
    try:
        a.update()
        assert not a.details_button.winfo_ismapped()
    finally:
        a.destroy()


def test_lone_xpd_beside_a_bundle_folder_is_picked(app, tmp_path):
    bundle = tmp_path / "xenium_output"
    (bundle / "morphology_focus").mkdir(parents=True)
    (tmp_path / "panel.xpd").touch()
    app.set_image(bundle); app.update()
    assert Path(app.panel_var.get()) == tmp_path / "panel.xpd"
