"""Review fix pass 2, the window: Run state, the wait, verdicts, compare rows, dim antibodies, help, small screens."""

from __future__ import annotations

import json
import shutil
import tkinter as tk

import pytest
from conftest import new_app

from phenocycler_qc_eval.gui.app import App
from phenocycler_qc_eval.results import load_results
from synth import PROTO

pytestmark = pytest.mark.gui


@pytest.fixture(scope="module")
def shared(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("PQE_SETTINGS_DIR", str(tmp_path_factory.mktemp("settings")))
    mp.setenv("PQE_CONFIG_DIR", str(tmp_path_factory.mktemp("config")))
    mp.setattr(App, "WIRED", {"run": True, "settings": True, "example": True})
    a = new_app()
    a.update()
    yield a
    try:
        a.destroy()
    except tk.TclError:
        pass
    mp.undo()


@pytest.fixture
def view(shared):
    """The shared window, with the prototype run on screen."""
    shared.handle = None
    shared._busy(False)
    shared.load_past_run(PROTO)
    shared.update()
    return shared


def _row_of(tree, first_value):
    return next(i for i in tree.get_children() if str(tree.item(i, "values")[0]) == first_value)


def _select(tree, app, iid):
    tree.selection_set(iid)
    tree.focus(iid)
    app.update()


def _state(widget) -> str:
    return str(widget["state"])


# ---------------------------------------------------------------------------- D8: Run is live whenever a run can start
def test_run_is_enabled_as_soon_as_an_image_is_chosen(app, tmp_path):
    assert _state(app.run_button) == "disabled" and "Choose an image" in app.run_hint_text()
    image = tmp_path / "slide.qptiff"
    image.write_bytes(b"x")
    app.set_image(image)
    app.update()
    assert _state(app.run_button) == "normal" and app.run_hint_text() == ""


def test_run_is_enabled_after_an_error(app, tmp_path):
    image = tmp_path / "slide.qptiff"
    image.write_bytes(b"x")
    app.set_image(image)
    app.show_error("reader", "tonsil.qptiff: the file ends before its last tile.")
    app.update()
    assert _state(app.run_button) == "normal"


def test_run_is_enabled_after_a_cancel(app, fake_handle, tmp_path):
    image = tmp_path / "slide.qptiff"
    image.write_bytes(b"x")
    app.set_image(image)
    app.handle = fake_handle
    app._busy(True)
    assert _state(app.run_button) == "disabled" and _state(app.cancel_button) == "normal"
    app.cancel_run()
    from conftest import pump_until
    pump_until(app, lambda: app.handle is None)
    assert _state(app.run_button) == "normal" and _state(app.cancel_button) == "disabled"


def _past_run(tmp_path, image_path, panel="", name="past"):
    run = tmp_path / name
    shutil.copytree(PROTO, run)
    (run / "run_info.json").write_text(json.dumps({
        "status": "complete", "crosstalk_state": "on", "image_name": "slide.qptiff", "image_path": image_path,
        "panel": panel, "started_at": "2026-11-13T09:00:00"}))
    return run


def test_run_is_enabled_for_a_loaded_run_whose_image_still_exists(app, tmp_path):
    image = tmp_path / "slide.qptiff"
    image.write_bytes(b"x")
    app.elapsed_var.set("Elapsed 0:47")
    app.load_past_run(_past_run(tmp_path, str(image)))
    app.update()
    assert _state(app.run_button) == "normal" and app.image_var.get() == str(image)
    assert app.elapsed_var.get() == ""                       # no stale time from another run


def test_loaded_run_fills_the_panel_box_and_hides_the_template_button(app, tmp_path):
    image = tmp_path / "slide.qptiff"
    image.write_bytes(b"x")
    panel = tmp_path / "panel.xpd"
    panel.write_text("x")
    app.load_past_run(_past_run(tmp_path, str(image), str(panel)))
    app.update()
    assert app.panel_var.get() == str(panel) and not app.template_button.winfo_ismapped()
    assert "Showing saved run" in app.status_var.get() and "Choose Run to make a new one" in app.status_var.get()


def test_run_stays_off_with_a_reason_when_a_loaded_run_names_no_image(app, tmp_path):
    app.load_past_run(_past_run(tmp_path, "", name="no_image"))
    app.update()
    assert _state(app.run_button) == "disabled" and "Choose an image" in app.run_hint_text()


def test_enabled_and_disabled_run_look_clearly_different(app):
    from tkinter import ttk

    st = ttk.Style(app)
    on, off = st.lookup("Primary.TButton", "background", ["!disabled"]), st.lookup("Primary.TButton", "background", ["disabled"])
    assert on != off
    assert st.lookup("Primary.TButton", "foreground", ["disabled"]).lower() != "#ffffff"   # not white-on-pale


# ---------------------------------------------------------------------------- D1 / X7: the wait, one state
def _progress(app, fake_handle, stage, fraction, message):
    fake_handle.queue([{"type": "progress", "stage": stage, "fraction": fraction, "message": message}])
    app.handle = fake_handle
    app._poll()
    app.update()


def test_stage_list_and_status_and_banner_come_from_one_state(app, fake_handle, partial_folder):
    app._run_folder = partial_folder
    _progress(app, fake_handle, "sampling", 0.3, "Reading window 21 of 61")
    assert any("Reading windows (window 21 of 61)" in line for line in app.stage_lines())
    assert app.stage_lines()[0].startswith("✓") and app.stage_lines()[1].startswith("●")
    assert "Reading windows" in app.working_stages_text()
    fake_handle.queue([{"type": "partial", "out_dir": str(partial_folder), "early": True}])
    _progress(app, fake_handle, "detection", 0.52, "Scoring antibody 10 of 31")
    banner = app.banner_text() + "\n".join(app.banner_notes())
    assert "scoring antibodies (first pass: antibody 10 of 31)" in banner.lower()
    assert "Reading window" not in banner                       # the banner follows the same step as the rail
    assert app.status_var.get() == "Scoring antibody 10 of 31"


def test_bar_animates_when_a_step_has_no_count_and_fills_when_it_has_one(app, fake_handle):
    _progress(app, fake_handle, "tissue", 0.02, "Finding tissue")
    assert str(app.progress["mode"]) == "indeterminate"
    _progress(app, fake_handle, "sampling", 0.3, "Reading window 21 of 61")
    assert str(app.progress["mode"]) == "determinate"
    _progress(app, fake_handle, "crosstalk", 0.7, "Checking crosstalk")
    assert str(app.progress["mode"]) == "indeterminate"


def test_the_estimate_for_reading_appears_only_from_an_observed_pace(app, fake_handle):
    clock = [100.0]
    app.run_state._clock = lambda: clock[0]
    _progress(app, fake_handle, "sampling", 0.1, "Reading window 1 of 61")
    assert "should take" not in app.working_detail_text()
    clock[0] += 30
    _progress(app, fake_handle, "sampling", 0.2, "Reading window 11 of 61")
    assert "Reading the windows should take" in app.working_detail_text()


def test_finished_run_changes_the_banner_the_title_and_rings_once(app, fake_handle, complete_folder, monkeypatch):
    rang = []
    monkeypatch.setattr(app, "bell", lambda *a, **k: rang.append(1))
    app.image_var.set(str(complete_folder))
    app.handle = fake_handle
    app._busy(True)
    fake_handle.queue([{"type": "done", "out_dir": str(complete_folder), "summary": {}}])
    app._poll()
    app.update()
    assert app.title().startswith("Done") and rang == [1]
    assert any(line.startswith("✓") for line in app.stage_lines()) and all(
        line.startswith("✓") for line in app.stage_lines())
    assert any("Finished" in n for n in app.banner_notes())
    app.event_generate("<FocusIn>")
    app.update()
    assert app.title() == "Phenocycler QC Eval"


def test_finished_path_is_shortened_in_the_middle_and_the_full_path_is_a_tip(app, fake_handle, complete_folder):
    app.handle = fake_handle
    app._busy(True)
    app._run_folder = complete_folder
    fake_handle.queue([{"type": "done", "out_dir": str(complete_folder), "summary": {}}])
    app._poll()
    app.update()
    assert app.status_var.get() == "Finished."                    # where the results are is said once, in the banner
    assert any(complete_folder.name in n for n in app.banner_notes())
    assert app.full_path_tip() == str(complete_folder)


def test_run_label_in_the_content_pane_becomes_run_once_an_image_is_chosen(app, tmp_path):
    assert app.empty_button.cget("text") == "Choose your image"
    image = tmp_path / "slide.qptiff"
    image.write_bytes(b"x")
    app.set_image(image)
    app.update()
    assert app.empty_button.cget("text") == "Run"
    assert "Try the example" in app.empty_example_button.cget("text") and "Try the example" not in app.empty_detail.cget("text")


def test_idle_window_hides_buttons_that_mean_nothing_yet(app, tmp_path):
    app.update()
    assert not app.clear_panel_button.winfo_ismapped() and not app.details_button.winfo_ismapped()
    app.panel_var.set(str(tmp_path / "p.xpd"))
    app.update()
    assert app.clear_panel_button.winfo_ismapped()


def test_working_state_has_no_duplicate_headline(app, fake_handle, tmp_path):
    folder = tmp_path / "just_started"
    folder.mkdir()
    (folder / "run_info.json").write_text(json.dumps({"status": "running", "crosstalk_state": "pending"}))
    fake_handle.queue([{"type": "started", "out_dir": str(folder)}])
    app.handle = fake_handle
    app._poll()
    app.update()
    assert not app.banner.winfo_ismapped() and app.working_state_visible()
    assert "No results yet" not in app.banner_text()


# ---------------------------------------------------------------------------- X3 / X4 / D16: help and taking the answer away
def test_header_has_the_buttons_that_take_the_answer_away(view):
    keys = set(view.header_buttons)
    assert {"report", "folder", "copy", "help"} <= keys
    assert view.header_buttons["help"].cget("text") == "How to read these results"
    assert all(_state(b) == "normal" for b in view.header_buttons.values())


def test_copy_table_puts_one_tab_separated_row_per_antibody_on_the_clipboard(view):
    view.copy_table()
    text = view.clipboard_get()
    lines = text.splitlines()
    assert lines[0].split("\t")[:3] == ["Antibody", "Next run", "Evidence"]
    assert len(lines) == 1 + len(view.results.detection)
    assert all(len(line.split("\t")) == 3 for line in lines)


def test_open_folder_and_report_go_to_the_system(view, monkeypatch):
    opened = []
    monkeypatch.setattr("os.startfile", lambda p: opened.append(str(p)), raising=False)
    view.open_results_folder()
    view.open_report()
    assert opened[0] == str(view.results.folder) and opened[1].endswith("STAIN_QC_REPORT.md")


def test_how_to_read_opens_the_guide_in_a_window(view):
    win = view.show_help()
    view.update()
    try:
        text = win.text_content()
        assert "Stain index" in text and "Next run" in text
        assert "separation" not in text.lower().replace("what separation", "")
    finally:
        win.destroy()


def test_packaged_guide_matches_the_one_in_docs():
    from pathlib import Path

    from phenocycler_qc_eval import __file__ as pkg

    repo = Path(pkg).resolve().parents[2]
    assert (repo / "docs" / "how_to_read_results.md").read_text(encoding="utf-8") == (
        Path(pkg).parent / "data" / "how_to_read_results.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------- D2 / X5 / D5 / D6: the Titration tab
def test_a_compare_row_says_what_to_compare_and_what_each_outcome_means(view):
    t = view.titration_tab
    _select(t.tree, view, _row_of(t.tree, "CD14"))              # a CHECK row; the settings pair CD14 with CD68
    text = t.evidence.header_subtitle()
    assert "CD14 should look like CD68" in text and "raise concentration" in text and "keep it" in text


def test_a_compare_row_without_a_control_marker_still_helps(view):
    t = view.titration_tab
    _select(t.tree, view, _row_of(t.tree, "FOXP3"))              # FOXP3 is paired with CD4 in the settings
    assert "FOXP3 should look like CD4" in t.evidence.header_subtitle()
    _select(t.tree, view, _row_of(t.tree, "PDL1"))               # no control marker for PDL1
    assert "expect PDL1" in t.evidence.header_subtitle() and "keep it" in t.evidence.header_subtitle()


def test_no_amount_appears_in_a_compare_row_detail(view):
    t = view.titration_tab
    _select(t.tree, view, _row_of(t.tree, "CD14"))
    guidance = t.evidence.header_subtitle().split("\n\n", 1)[1]            # the evidence above it quotes measurements
    words = guidance.replace("CD14", "").replace("CD68", "")
    assert not any(ch.isdigit() for ch in words)


def test_rows_to_change_are_ordered_by_how_far_past_the_cut_off_and_carry_a_word(view):
    t = view.titration_tab
    change = [i for i in t.tree.get_children() if str(t.tree.item(i, "values")[1]).startswith(("Raise", "Lower"))]
    words = [str(t.tree.item(i, "values")[2]).split(".")[0] for i in change]
    assert set(words) <= {"Strong", "Moderate", "Mild"}
    rank = {"Strong": 0, "Moderate": 1, "Mild": 2}
    assert [rank[w] for w in words] == sorted(rank[w] for w in words)


def test_first_selected_row_is_one_that_needs_a_change_and_is_not_near_blank(app):
    app.load_past_run(PROTO)                       # a fresh window: a reload of the same run would keep the old row
    app.update()
    first = app.titration_tab.selected_channel()
    row = app.results.detection[app.results.detection["channel"] == first].iloc[0]
    assert str(row["call"]).upper() in ("RAISE", "LOWER") and float(row["signal_p99_5"]) > 0.05 * 255


def test_the_key_under_the_table_does_not_define_separation(view):
    key = view.titration_tab.key_text()
    assert "Separation" not in key and "separation" not in key


def test_facts_use_the_one_term(view):
    t = view.titration_tab
    _select(t.tree, view, _row_of(t.tree, "CD14"))
    assert "Stain index" in t.evidence.facts() and not any("separation" in k.lower() for k in t.evidence.facts())


def test_a_near_blank_antibody_shows_little_or_no_signal_instead_of_a_speckle_map(view, run_with_previews):
    view.load_past_run(run_with_previews)
    view.update()
    t = view.titration_tab
    _select(t.tree, view, _row_of(t.tree, "CD31"))               # signal 2 of 255: near-blank
    assert t.evidence.blank_state() and t.evidence.tile_count() == 0
    said = " ".join(x.get_text() for ax in t.evidence.figure.axes for x in ax.texts).lower()
    assert "little or no signal" in said and t.evidence.caption_text() == ""      # once, in the picture
    for ax in t.evidence.figure.axes:                             # the legend names only lines that are drawn
        legend = ax.get_legend()
        drawn = [ln for ln in ax.lines if ax.get_xlim()[0] <= float(ln.get_xdata()[0]) <= ax.get_xlim()[1]]
        assert (len(legend.get_texts()) if legend else 0) <= len(drawn)


def test_a_dim_antibody_is_brightened_and_says_so(view, run_with_previews):
    view.load_past_run(run_with_previews)
    view.update()
    t = view.titration_tab
    _select(t.tree, view, _row_of(t.tree, "iNOS"))               # signal 21 of 255: dim (below 25.5), not near-blank
    assert not t.evidence.blank_state() and t.evidence.tile_count() == 3
    assert "brightened for display" in t.evidence.caption_text().lower()
    assert "true signal is low" in t.evidence.caption_text().lower()


# ---------------------------------------------------------------------------- D3 / D4 / X3 / X6: the Crosstalk tab
def test_pair_table_has_a_result_column_in_plain_words(view):
    ct = view.crosstalk_tab
    cols = [ct.tree.heading(c)["text"] for c in ct.tree["columns"]]
    assert cols == ["Source", "Target", "Result", "Likely cause"]
    assert all(v.startswith("Flagged") for _s, _t, v in ct.verdicts())


def test_selected_pair_shows_each_number_next_to_its_cut_off(view):
    ct = view.crosstalk_tab
    view.notebook.select(ct)
    _select(ct.tree, view, _row_of(ct.tree, "TOX"))
    facts = ct.evidence.facts()
    assert facts["Fine-detail match"] == "0.48 (cut-off 0.30, over)"
    assert any(k.startswith("One-way excess") for k in facts)


def test_what_to_look_for_is_at_the_top_of_the_evidence_in_normal_text(view):
    ct = view.crosstalk_tab
    _select(ct.tree, view, _row_of(ct.tree, "TOX"))
    sub = ct.evidence.header_subtitle()
    assert sub.index("What to look for:") < sub.index("If it is real") and "flagged" in sub.lower()


def test_a_caveat_line_is_always_visible_under_the_pair_table(view):
    ct = view.crosstalk_tab
    view.notebook.select(ct)
    view.update()
    assert "lab-defined" in ct.caveat_text() and "Not flagged does not mean clean" in ct.caveat_text()
    assert ct.caveat_label.winfo_ismapped()


def test_pairs_that_were_not_flagged_can_be_listed_with_their_verdict(view, tmp_path):
    run = tmp_path / "with_blank"
    shutil.copytree(PROTO, run)
    import pandas as pd

    pairs = pd.read_csv(run / "stain_qc_crosstalk_pairs.csv")
    pairs["source_near_blank"] = pairs["source"].isin(["CD31"])
    pairs["target_near_blank"] = False
    pairs.to_csv(run / "stain_qc_crosstalk_pairs.csv", index=False)
    view.load_past_run(run)
    view.update()
    ct = view.crosstalk_tab
    before = len(ct.tree.get_children())
    ct.set_show_unflagged(True)
    view.update()
    shown = {v for _s, _t, v in ct.verdicts()}
    assert len(ct.tree.get_children()) > before and "Not flagged" in shown
    assert "Not tested: near-blank channel" in shown
    ct.set_show_unflagged(False)


def test_a_run_with_no_flagged_pairs_says_it_does_not_prove_the_channels_clean(view, tmp_path):
    run = tmp_path / "no_flags"
    shutil.copytree(PROTO, run)
    import pandas as pd

    pairs = pd.read_csv(run / "stain_qc_crosstalk_pairs.csv")
    pairs["flag"] = False
    pairs.to_csv(run / "stain_qc_crosstalk_pairs.csv", index=False)
    view.load_past_run(run)
    view.update()
    assert "does not prove the channels are clean" in view.banner_text()
    assert "does not prove the channels are clean" in view.crosstalk_tab.message_text()


def test_overlap_table_has_a_result_column_and_a_sentence_when_a_row_is_selected(view):
    ct = view.crosstalk_tab
    cols = [ct.exclusive_tree.heading(c)["text"] for c in ct.exclusive_tree["columns"]]
    assert cols[2] == "Result"
    verdicts = ct.exclusive_verdicts()
    assert ("CD4", "CD20", "Overlap higher than expected") in verdicts and ("SMA", "CD20", "As expected") in verdicts
    _select(ct.exclusive_tree, view, _row_of(ct.exclusive_tree, "CD4"))        # CD4 / CD20, ratio 1.06: tested
    assert "CD4" in ct.evidence.header_title() and "chance alone would give" in ct.evidence.header_subtitle()
    assert ct.tree.selection() == ()                                           # only one table drives the pane


def test_selecting_an_overlap_row_shows_the_two_markers_tiles(view, run_with_previews):
    view.load_past_run(run_with_previews)
    view.update()
    ct = view.crosstalk_tab
    _select(ct.exclusive_tree, view, _row_of(ct.exclusive_tree, "CD4"))
    assert [name for name, _role in ct.evidence.pair_rows()] == ["CD4", "CD20"]


def test_selecting_a_pair_clears_the_overlap_selection(view):
    ct = view.crosstalk_tab
    _select(ct.exclusive_tree, view, ct.exclusive_tree.get_children()[0])
    _select(ct.tree, view, ct.tree.get_children()[0])
    assert ct.exclusive_tree.selection() == ()


def test_crosstalk_off_is_said_once_not_four_times(app, run_without_panel):
    app.image_var.set("x")
    app.load_past_run(run_without_panel)
    app.update()
    shown = "\n".join([app.banner_text(), *app.banner_notes(), app.crosstalk_tab.message_text(), app.status_var.get()])
    assert shown.lower().count("crosstalk is off") + shown.lower().count("crosstalk was not checked") \
        + shown.lower().count("crosstalk: not checked") <= 2


# ---------------------------------------------------------------------------- D12 / D10 / D21 / X12 / D17
def test_figures_tab_lists_only_the_overview_figures_and_opens_on_double_click(view, monkeypatch):
    ft = view.figures_tab
    assert not any(title.startswith("Follow-up panel") for title in ft.titles())
    opened = []
    monkeypatch.setattr(ft, "open_figure", lambda: opened.append(1))
    for widget in (ft.evidence.canvas.get_tk_widget(), ft.tree):
        assert widget.bind("<Double-Button-1>")                    # bound on the picture and on the list
    ft._double_clicked()                                            # Tk cannot synthesize a double click
    assert opened and "Double-click" in ft.hint_text()


def test_error_banner_is_visibly_an_error(app, tmp_path):
    image = tmp_path / "slide.qptiff"
    image.write_bytes(b"x")
    app.set_image(image)
    app.show_error("too_little_tissue", "found 3 tissue windows, need at least 20.", found=3, needed=20)
    app.update()
    assert app.banner_is_error()
    app.load_past_run(PROTO)
    assert not app.banner_is_error()


def test_reader_error_names_the_cause_and_every_accepted_type(app):
    app.show_error("reader", "tonsil.qptiff: the file ends before its last tile.")
    action = app.banner_action_text()
    assert "incomplete" in action.lower() and ".tif" in action and ".tiff" in action and ".qptiff" in action


def test_the_note_that_changes_how_much_to_trust_the_page_is_in_normal_text(view):
    assert str(view.banner_notes_label.cget("foreground")) in ("", "#1f2933")


def test_nuclear_channel_picker_preselects_a_nuclear_looking_channel(root):
    from phenocycler_qc_eval.gui.dialogs import NuclearChannelDialog

    dlg = NuclearChannelDialog(root, ["CD31", "Hoechst 33342", "CD4"])
    try:
        dlg.update()
        assert dlg.selected_channel() == "Hoechst 33342"
    finally:
        dlg.destroy()
    dlg = NuclearChannelDialog(root, ["CD31", "CD4"])
    try:
        dlg.update()
        assert dlg.selected_channel() is None and _state(dlg.use_button) == "disabled"
    finally:
        dlg.destroy()


def test_settings_tooltip_names_changes_in_words_not_keys(app):
    from phenocycler_qc_eval.settings import config_path, default_settings, save_settings

    s = default_settings()
    s.detection.dim_fraction = 0.15
    save_settings(s, config_path())
    app.refresh_settings_status()
    tip = app.settings_tooltip_text()
    assert "detection." not in tip and "crosstalk." not in tip and "0.1" in tip and "0.15" in tip


# ---------------------------------------------------------------------------- X1 / X2 / D14 / D19: small screens
@pytest.mark.parametrize("scale,size,screen", [(1.25, "1366x690+0+0", (1366, 768)), (1.5, "1900x1040+0+0", (1920, 1080)),
                                               (1.5, "1366x690+0+0", (1366, 768))])
def test_results_are_usable_without_dragging_the_sash(monkeypatch, tmp_path, run_with_previews, scale, size, screen):
    monkeypatch.setenv("PQE_SETTINGS_DIR", str(tmp_path / "s"))
    a = None
    for _ in range(3):                      # Tk start-up fails now and then on this workstation (a transient tk.tcl error)
        try:
            a = App(simulate_scale=scale)
            break
        except tk.TclError:
            import time
            time.sleep(1)
    if a is None:
        pytest.skip("no display for Tkinter")
    try:
        a.minsize(1, 1)
        a.geometry(size)
        a.screen_override = screen
        a.load_past_run(run_with_previews)
        a.update()
        for _ in range(3):
            a.update_idletasks()
            a.update()
        import time as _time
        end = _time.monotonic() + 2.0                      # a short window switches to its compact layout once its size is steady
        while _time.monotonic() < end:
            a.update()
            _time.sleep(0.02)
        t = a.titration_tab
        row_h = int(a.tk.call("ttk::style", "lookup", "Treeview", "-rowheight") or 24)
        rows_visible = t.tree.winfo_height() // max(row_h, 1)
        # Pass 4 (Z2): on a short window the table shows three rows and the evidence under it shows its numbers and tiles.
        assert rows_visible >= (3 if a.compact() else 5), (rows_visible, t.tree.winfo_height(), row_h, a.compact())
        ev = t.evidence
        assert ev.scrollable() or ev.fits(), "the evidence is either fully shown or can be scrolled"
        a.notebook.select(a.crosstalk_tab)
        ct = a.crosstalk_tab
        ct.tree.selection_set(_row_of(ct.tree, "TOX"))
        a.update()
        a.update()
        assert ct.evidence.scrollable() or ct.evidence.fits()
        names = ct.evidence.followup_rows()
        assert ct.evidence.min_row_px() >= 28 or not names, ct.evidence.min_row_px()   # no overprinted tile labels
    finally:
        try:
            a.destroy()
        except tk.TclError:
            pass


# ---------------------------------------------------------------------------- D18: each step is logged once
def test_the_log_says_each_step_once(app, fake_handle):
    app.handle = fake_handle
    msgs = [{"type": "progress", "stage": "sampling", "fraction": 0.2, "message": f"Reading window {i} of 61"}
            for i in range(1, 6)]
    msgs += [{"type": "progress", "stage": "crosstalk", "fraction": 0.7, "message": "Checking crosstalk"},
             {"type": "progress", "stage": "crosstalk", "fraction": 0.75,
              "message": "Checking crosstalk: fine detail, window 3 of 61"}]
    fake_handle.queue(msgs)
    app._poll()
    log = app.log.get("1.0", "end").splitlines()
    assert len([ln for ln in log if ln.startswith("Reading window")]) == 1
    assert len([ln for ln in log if "Checking crosstalk" in ln]) == 1


# ---------------------------------------------------------------------------- D13 / X15: one font size, whole rows
def test_entry_boxes_use_the_same_font_size_as_the_rest(app):
    from tkinter import font as tkfont

    assert tkfont.nametofont("TkTextFont", root=app).actual()["size"] == 10
    assert tkfont.nametofont("TkTextFont", root=app).actual()["family"] == "Segoe UI"


def test_marker_tables_in_settings_show_whole_rows(app):
    app.on_settings()
    d = app.settings_dialog
    d.update()
    d.update()
    try:
        for table in (d.control_table, d.exclusive_table):
            table.update_idletasks()
            row_h = table._rows[0].frame.winfo_reqheight()
            assert (int(table._canvas.cget("height")) - 2) % row_h == 0
    finally:
        d.cancel()


# ---------------------------------------------------------------------------- X7: a counted step that goes quiet still shows movement
def test_a_counted_step_that_goes_quiet_for_two_seconds_starts_the_moving_bar(app, fake_handle):
    import time

    _progress(app, fake_handle, "figures", 0.92, "Drawing figure 2 of 9")
    app._last_feed, app._stalled = time.monotonic(), False       # a slow machine may already have let a second pass
    app.apply_run_state()
    assert str(app.progress["mode"]) == "determinate"
    app._last_feed -= 3.0                           # nothing has been heard for three seconds
    app._stall_check()
    assert str(app.progress["mode"]) == "indeterminate" and str(app.working_progress["mode"]) == "indeterminate"
    _progress(app, fake_handle, "figures", 0.93, "Drawing figure 3 of 9")
    assert str(app.progress["mode"]) == "determinate"


# ---------------------------------------------------------------------------- the four numbers use one row when they fit
def test_the_four_numbers_share_one_row_when_the_pane_is_wide_and_two_by_two_when_it_is_narrow(view):
    t = view.titration_tab
    _select(t.tree, view, _row_of(t.tree, "CD14"))
    ev = t.evidence
    facts = list(ev.facts().items())
    assert ev.facts_columns(facts, 1100) == 4
    assert ev.facts_columns(facts, 340) in (1, 2)


def test_a_short_window_gets_one_step_line_so_run_and_cancel_stay_on_screen(monkeypatch, tmp_path, fake_handle):
    monkeypatch.setenv("PQE_SETTINGS_DIR", str(tmp_path / "s"))
    a = None
    for _ in range(3):
        try:
            a = App(simulate_scale=1.5)
            break
        except tk.TclError:
            import time
            time.sleep(1)
    if a is None:
        pytest.skip("no display for Tkinter")
    try:
        a.minsize(1, 1)
        a.geometry("1366x690+0+0")
        a.update()
        a.update()
        a.handle = fake_handle
        fake_handle.queue([{"type": "progress", "stage": "sampling", "fraction": 0.3, "message": "Reading window 21 of 61"}])
        a._poll()
        a.update()
        assert a.stage_var.get() == ""                             # the rail never repeats the step list
        assert len(a.stage_lines()) == 5                         # the model has all five (the working page lists them)
        for _ in range(6):
            a.update()
        bottom = a.runbar.winfo_rooty() + a.runbar.winfo_height()
        assert a.cancel_button.winfo_rooty() + a.cancel_button.winfo_height() <= a.winfo_rooty() + a.winfo_height(), \
            "Cancel is below the bottom of the window"
        assert a.run_button.winfo_rooty() >= a.winfo_rooty()
        assert bottom <= a.winfo_rooty() + a.winfo_height() + 2
    finally:
        a.handle = None
        try:
            a.destroy()
        except tk.TclError:
            pass
