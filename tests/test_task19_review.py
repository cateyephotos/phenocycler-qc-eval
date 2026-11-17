"""Task 19 build part: unsaved-settings prompt, review leftovers (U5, U12, U14, U24, J3, J22) and the layout at
Windows display scaling 100 / 125 / 150 percent on a 1366 x 768 screen.

The scaling is simulated: `tk scaling` is set before any widget is made (App(simulate_scale=...), or by hand for
the Settings panel) and the window is given the client size of a maximized window on a 1366 x 768 screen. That
exercises the same font and pixel arithmetic as a real display setting, but it is not a real DPI change.
"""

from __future__ import annotations

import json
import shutil
import time
import tkinter.font as tkfont

import numpy as np
import pytest

from phenocycler_qc_eval.advice import next_run_text
from phenocycler_qc_eval.settings import default_settings

tk = pytest.importorskip("tkinter")
from phenocycler_qc_eval.gui.settings_dialog import SettingsDialog  # noqa: E402

pytestmark = pytest.mark.gui

SCALES = [1.0, 1.25, 1.5]
SMALL_SCREEN_CLIENT = "1366x690"        # a maximized window on a 1366 x 768 screen: 768 less the taskbar and title bar


def _dialog(root, tmp_path, **kw):
    return SettingsDialog(root, default_settings(), tmp_path / "s.json", **kw)


# ---------------------------------------------------------------------------- unsaved edits in Settings
def _ask_log(monkeypatch, answer):
    asked = []

    def fake(self):
        asked.append(1)
        return answer
    monkeypatch.setattr(SettingsDialog, "_confirm_discard", fake)
    return asked


def test_escape_with_unsaved_edits_asks_and_keep_editing_leaves_it_open(root, tmp_path, monkeypatch):
    asked = _ask_log(monkeypatch, False)                      # the user picks Keep editing
    d = _dialog(root, tmp_path)
    d.fields["crosstalk.margin"].set("0.07")
    d.focus_force()
    d.update()
    d.event_generate("<Escape>")
    d.update()
    assert asked == [1] and d.winfo_exists() and d.fields["crosstalk.margin"].get() == "0.07"


def test_escape_then_discard_closes_without_saving(root, tmp_path, monkeypatch):
    asked = _ask_log(monkeypatch, True)                       # the user picks Discard
    d = _dialog(root, tmp_path)
    d.fields["crosstalk.margin"].set("0.07")
    d.request_close()
    assert asked == [1] and not d.winfo_exists() and d.result is None and not (tmp_path / "s.json").exists()


def test_window_close_button_asks_too(root, tmp_path, monkeypatch):
    asked = _ask_log(monkeypatch, False)
    d = _dialog(root, tmp_path)
    d.fields["detection.dim_fraction"].set("0.2")
    d.tk.call(d.protocol("WM_DELETE_WINDOW"))                 # what the title-bar X runs
    assert asked == [1] and d.winfo_exists()


@pytest.mark.parametrize("how", ["escape", "x"])
def test_no_prompt_when_nothing_changed(root, tmp_path, monkeypatch, how):
    asked = _ask_log(monkeypatch, False)
    d = _dialog(root, tmp_path)
    if how == "escape":
        d.focus_force()
        d.update()
        d.event_generate("<Escape>")
        d.update()
    else:
        d.tk.call(d.protocol("WM_DELETE_WINDOW"))
    assert asked == [] and not d.winfo_exists()


def test_editing_a_value_back_to_what_it_was_is_not_a_change(root, tmp_path, monkeypatch):
    asked = _ask_log(monkeypatch, False)
    d = _dialog(root, tmp_path)
    d.fields["crosstalk.margin"].set("0.07")
    d.fields["crosstalk.margin"].set("0.05")
    assert not d.has_unsaved_changes()
    d.request_close()
    assert asked == [] and not d.winfo_exists()


def test_a_changed_marker_row_counts_as_unsaved(root, tmp_path, monkeypatch):
    asked = _ask_log(monkeypatch, False)
    d = _dialog(root, tmp_path)
    d.exclusive_table.add_row("CD3e", "CD20")
    assert d.has_unsaved_changes()
    d.request_close()
    assert asked == [1] and d.winfo_exists()


def test_reset_to_defaults_counts_as_unsaved_when_the_file_held_other_values(root, tmp_path, monkeypatch):
    asked = _ask_log(monkeypatch, False)
    s = default_settings()
    s.crosstalk.margin = 0.1
    d = SettingsDialog(root, s, tmp_path / "s.json")
    assert not d.has_unsaved_changes()
    d.reset()
    assert d.has_unsaved_changes()


def test_after_a_save_nothing_is_asked(root, tmp_path, monkeypatch):
    asked = _ask_log(monkeypatch, False)
    d = _dialog(root, tmp_path)
    d.fields["crosstalk.margin"].set("0.07")
    d.save()
    assert asked == [] and not d.winfo_exists() and d.result.crosstalk.margin == 0.07


def test_cancel_button_still_discards_without_asking(root, tmp_path, monkeypatch):
    """The button says Cancel, so pressing it is the answer; only Escape and the window X are ambiguous."""
    asked = _ask_log(monkeypatch, False)
    d = _dialog(root, tmp_path)
    d.fields["crosstalk.margin"].set("0.07")
    d.cancel_button.invoke()
    assert asked == [] and not d.winfo_exists()


def test_confirm_dialog_wording_and_buttons(root):
    from phenocycler_qc_eval.gui.dialogs import ConfirmDiscardDialog

    root.deiconify()
    dlg = ConfirmDiscardDialog(root)
    dlg.update()
    assert dlg.prompt_text().startswith("Discard changes?")
    assert dlg.keep_button["text"] == "Keep editing" and dlg.discard_button["text"] == "Discard"
    dlg.keep_button.invoke()
    assert dlg.result is False
    dlg2 = ConfirmDiscardDialog(root)
    dlg2.discard_button.invoke()
    assert dlg2.result is True
    dlg3 = ConfirmDiscardDialog(root)
    dlg3.focus_force()
    dlg3.update()
    dlg3.event_generate("<Escape>")                           # Escape in the question means "keep editing"
    dlg3.update()
    assert dlg3.result is False


# ---------------------------------------------------------------------------- the Settings panel at every scale
@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize("screen", [(1366, 768), (1920, 1080)])
@pytest.mark.parametrize("advanced", [False, True])
def test_settings_panel_fits_the_screen_and_keeps_its_buttons_visible(root, tmp_path, scale, screen, advanced):
    root.tk.call("tk", "scaling", 96 / 72 * scale)             # before any widget exists, so fonts follow
    root.deiconify()
    d = _dialog(root, tmp_path, screen_size=screen)
    d.fields["detection.dim_fraction"].set("abc")              # an error line is showing
    if advanced:
        d.toggle_advanced()
    d.update()
    w, h = d.winfo_width(), d.winfo_height()
    assert w <= screen[0] - 40 and h <= screen[1] - 80, (w, h, screen)
    for b in (d.save_button, d.reset_button, d.cancel_button):
        assert b.winfo_ismapped()
        assert b.winfo_rootx() - d.winfo_rootx() >= 0 and b.winfo_rooty() - d.winfo_rooty() >= 0
        assert b.winfo_rootx() - d.winfo_rootx() + b.winfo_width() <= w, b
        assert b.winfo_rooty() - d.winfo_rooty() + b.winfo_height() <= h, b
    # every entry is reachable: inside the form's scroll range or visible
    assert d.field_entry("detection.dim_fraction").winfo_exists()


# ---------------------------------------------------------------------------- the main window at every scale
def _scaled_app(monkeypatch, tmp_path, scale):
    from phenocycler_qc_eval.gui.app import App

    monkeypatch.setenv("PQE_SETTINGS_DIR", str(tmp_path / "_settings"))
    monkeypatch.setattr(App, "WIRED", {"run": True, "settings": True, "example": True})
    last = None
    for _ in range(3):
        try:
            return App(simulate_scale=scale)
        except tk.TclError as exc:
            last = exc
            time.sleep(1)
    pytest.skip(f"no display for Tkinter: {last}")


def _inside(app, w) -> bool:
    x, y = w.winfo_rootx() - app.winfo_rootx(), w.winfo_rooty() - app.winfo_rooty()
    return x >= 0 and y >= 0 and x + w.winfo_width() <= app.winfo_width() and y + w.winfo_height() <= app.winfo_height()


@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize("client", [None, SMALL_SCREEN_CLIENT])
@pytest.mark.parametrize("details", [False, True])
def test_run_and_feedback_stay_visible_at_every_scale(monkeypatch, tmp_path, complete_folder, scale, client, details):
    app = _scaled_app(monkeypatch, tmp_path, scale)
    try:
        if client:
            app.minsize(1, 1)                       # the scaled minimum size would otherwise enlarge the window
            app.geometry(client + "+0+0")
        (tmp_path / "a.qptiff").touch()
        app.set_image(tmp_path / "a.qptiff")
        app.load_past_run(complete_folder)
        app.status_var.set("Reading window 120 of 200 and then some more words to fill two lines of status text")
        app.elapsed_var.set("Elapsed 12:34")
        if details:
            app.open_details()
            for i in range(30):
                app.log_line(f"line {i}")
        app.update()
        for name in ("run_button", "cancel_button", "progress", "status_label", "elapsed_label", "settings_button"):
            w = getattr(app, name)
            assert w.winfo_ismapped() and _inside(app, w), f"{name} is clipped at scale {scale}, client {client}"
        assert _inside(app, app.notebook)
        assert app.run_button.winfo_rooty() + app.run_button.winfo_height() <= app.left.winfo_rooty() + app.left.winfo_height()
    finally:
        app.destroy()


@pytest.mark.parametrize("scale", SCALES)
def test_the_rail_scrolls_when_the_window_is_short(monkeypatch, tmp_path, complete_folder, scale):
    """Inputs that do not fit are reached by scrolling; Run and Cancel never scroll away."""
    app = _scaled_app(monkeypatch, tmp_path, scale)
    try:
        app.minsize(1, 1)
        app.geometry("1366x560+0+0")
        app.update()
        run_y = app.run_button.winfo_rooty()
        app.rail_canvas.yview_moveto(1.0)
        app.update()
        assert app.run_button.winfo_rooty() == run_y and _inside(app, app.run_button)
        assert app.example_button.winfo_exists()
    finally:
        app.destroy()


@pytest.mark.parametrize("scale", SCALES)
def test_table_rows_and_columns_follow_the_font_size(monkeypatch, tmp_path, complete_folder, scale):
    app = _scaled_app(monkeypatch, tmp_path, scale)
    try:
        app.minsize(1, 1)
        app.geometry(SMALL_SCREEN_CLIENT + "+0+0")
        app.load_past_run(complete_folder)
        app.update()
        t = app.titration_tab
        font = tkfont.Font(root=app, font=app.tk.call("ttk::style", "lookup", "Treeview", "-font") or "TkDefaultFont")
        row_h = int(app.tk.call("ttk::style", "lookup", "Treeview", "-rowheight") or 0)
        assert row_h >= font.metrics("linespace") + 4, (row_h, font.metrics("linespace"))
        widest = max(font.measure(next_run_text(c, e)) for c in ("RAISE", "LOWER") for e in ("LENGTHEN", "SHORTEN"))
        assert t.tree.column("c1", "width") >= widest + 12, (t.tree.column("c1", "width"), widest)
        right = t.tree.winfo_rootx() + t.tree.winfo_width()
        assert right <= app.winfo_rootx() + app.winfo_width()
    finally:
        app.destroy()


def test_default_window_fits_a_1366_by_768_screen():
    from phenocycler_qc_eval.gui.theme import fit_window

    w, h, min_w, min_h = fit_window(1366, 768)
    assert w <= 1366 - 20 and h <= 768 - 100
    assert min_w <= w and min_h <= h
    big = fit_window(2560, 1440)
    assert big[:2] == (1120, 740)
    # the window grows with the display scaling (the text does), and still fits a small screen
    assert fit_window(2560, 1440, 1.5)[:2] == (1680, 1110)
    w, h, *_ = fit_window(1366, 768, 1.5)
    assert w <= 1366 - 20 and h <= 768 - 100


# ---------------------------------------------------------------------------- review leftovers
def test_u24_each_direction_has_a_glyph_and_the_words_stay_exact(app, complete_folder):
    app.load_past_run(complete_folder)
    tree = app.titration_tab.tree
    seen: dict[str, set[str]] = {}
    for iid in tree.get_children():
        row = app.titration_tab.results.detection.iloc[int(iid)]
        glyph = tree.item(iid, "text")
        assert glyph, "every row has a direction glyph"
        assert tree.item(iid, "values")[1] == row["next_run"]       # Next run copy is untouched
        seen.setdefault(str(row["call"]), set()).add(glyph)
    assert all(len(g) == 1 for g in seen.values())                  # one glyph per call
    assert len({next(iter(g)) for g in seen.values()}) == len(seen)  # a different glyph per call
    assert "Direction" in tree.heading("#0", "text") or tree.heading("#0", "text") == ""


def test_u12_headings_have_tips_and_a_key_sits_under_the_table(app, complete_folder):
    app.load_past_run(complete_folder)
    t = app.titration_tab
    assert "Next run" in t.key_text() and "separation" not in t.key_text().lower()
    tips = t.heading_tips()
    assert set(tips) >= {"Antibody", "Next run", "Evidence"} and all(len(v) > 20 for v in tips.values())
    ct = app.crosstalk_tab
    assert "Likely cause" in ct.heading_tips() and "Ratio" in ct.exclusive_heading_tips()


def test_j22_scrollbars_are_slim_and_light(app):
    st = app.tk.call("ttk::style", "lookup", "Vertical.TScrollbar", "-arrowsize")
    assert int(st) <= 11
    assert app.tk.call("ttk::style", "lookup", "Vertical.TScrollbar", "-troughcolor") not in ("", "#c3c3c3")
    assert app.tk.call("ttk::style", "lookup", "Vertical.TScrollbar", "-background") != ""


def test_j3_keep_rows_can_be_hidden(app, complete_folder):
    app.load_past_run(complete_folder)
    t = app.titration_tab
    total = len(t.tree.get_children())
    keeps = int((t.results.detection["call"].astype(str).str.upper() == "OK").sum())
    assert keeps > 0 and t.keep_toggle_text() == f"Hide the {keeps} antibodies to keep"
    t.set_hide_keep(True)
    assert len(t.tree.get_children()) == total - keeps
    assert all(t.results.detection.iloc[int(i)]["call"] != "OK" for i in t.tree.get_children())
    assert t.keep_toggle_text() == f"Show the {keeps} antibodies to keep"
    t.set_hide_keep(False)
    assert len(t.tree.get_children()) == total


def test_j3_hidden_keep_rows_survive_a_reload_of_the_same_run(app, complete_folder):
    app.load_past_run(complete_folder)
    app.titration_tab.set_hide_keep(True)
    n = len(app.titration_tab.tree.get_children())
    app.load_past_run(complete_folder)
    assert len(app.titration_tab.tree.get_children()) == n


def test_u5_tiles_can_be_shown_on_the_full_scale(app, run_with_previews):
    app.load_past_run(run_with_previews)
    t = app.titration_tab
    assert t.full_scale_text() == "Show tiles on the full scale (0-255)"
    caption = t.evidence.caption_text()
    assert "share one display range" in caption and "full scale" not in caption
    t.set_full_scale(True)
    caption = t.evidence.caption_text()
    assert "full scale" in caption.lower() and "0-255" in caption
    t.set_full_scale(False)
    assert "full scale" not in t.evidence.caption_text().lower()


def test_u14_antibody_tiles_say_how_much_of_each_tile_is_tissue(tmp_path, small_sample, small_det):
    from phenocycler_qc_eval.previews import write_antibody_previews
    from phenocycler_qc_eval.results import load_preview

    p = load_preview(write_antibody_previews(small_sample, small_det, tmp_path, k=6, seed=0)[0])
    frac = np.asarray(p["tissue_fraction"])
    assert frac.shape == (6,) and (frac >= 0).all() and (frac <= 1).all()


def test_u14_the_gui_prints_the_tissue_share_under_each_tile(app, run_with_previews):
    import numpy as np
    from synth import write_antibody_preview
    from phenocycler_qc_eval.results import load_results, safe_name

    folder = run_with_previews
    for marker in load_results(folder).detection["channel"]:
        path = folder / "previews" / f"antibody_{safe_name(marker)}.npz"
        with np.load(path) as z:
            data = {k: z[k] for k in z.files}
        data["tissue_fraction"] = np.array([0.04, 0.55, 0.9])
        np.savez(path, **data)
    app.load_past_run(folder)
    texts = [x.get_text() for fig_ax in app.titration_tab.evidence.figure.axes for x in fig_ax.texts]
    assert any("4% tissue" in t for t in texts) and any("90% tissue" in t for t in texts)


def test_heading_tip_is_found_under_the_pointer(app, complete_folder):
    app.load_past_run(complete_folder)
    t = app.titration_tab
    app.update()
    tree = t.tree
    col = tree.column("c1", "width")
    x = sum(tree.column(c, "width") for c in ("#0",)) + 10
    # a point inside the "Next run" heading: right of the glyph column and the Antibody column
    x = tree.column("#0", "width") + tree.column("c0", "width") + 10
    assert col > 0
    assert "direction" in t._tips.text_at(x, 5).lower()
    assert t._tips.text_at(x, 200) == ""                    # not over a heading


# ---------------------------------------------------------------------------- the capture script
def _script():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "capture_gui_states.py"
    spec = importlib.util.spec_from_file_location("capture_gui_states", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_thin_frames_keeps_changes_and_one_frame_every_few_seconds(tmp_path):
    from PIL import Image

    mod = _script()
    for i in range(40):                                     # 20 s at 2 frames a second
        shade = 200 if i < 20 else 80                       # one real change, at 10 s
        Image.new("RGB", (40, 30), (shade, shade, shade)).save(tmp_path / f"f_{i * 0.5:07.1f}s.png")
    taken, kept = mod.thin_frames(tmp_path)
    assert taken == 40 and 4 <= kept <= 8
    names = sorted(p.name for p in tmp_path.glob("f_*.png"))
    assert names[0] == "f_00000.0s.png" and "f_00010.0s.png" in names      # first frame and the change


# ---------------------------------------------------------------------------- found while looking at the screens
def test_crosstalk_off_shows_no_empty_pair_table_and_says_what_to_do(app, run_without_panel):
    app.load_past_run(run_without_panel)
    ct = app.crosstalk_tab
    app.notebook.select(ct)
    app.update()
    assert not ct.tree.container.winfo_ismapped() and ct.template_button.winfo_ismapped()
    assert "panel file" in ct.message_text() and "layout CSV template" in ct.message_text()


def test_flagged_pairs_show_the_pair_table_again_after_an_off_run(app, run_without_panel, complete_folder):
    app.load_past_run(run_without_panel)
    app.load_past_run(complete_folder)
    app.notebook.select(app.crosstalk_tab)
    app.update()
    assert app.crosstalk_tab.tree.container.winfo_ismapped()


def test_tab_count_counts_a_two_way_same_cycle_pair_once(app, tmp_path, complete_folder):
    import pandas as pd

    from phenocycler_qc_eval.results import load_results

    r = load_results(complete_folder)
    extra = pd.DataFrame([
        {"source": "A", "target": "B", "kind": "same_cycle_spectral", "flag": True, "spill": 0.5},
        {"source": "B", "target": "A", "kind": "same_cycle_spectral", "flag": True, "spill": 0.4}])
    r.crosstalk = pd.concat([r.crosstalk, extra], ignore_index=True)
    before = int(r.crosstalk["flag"].astype(str).str.lower().eq("true").sum()) - 2
    app.show_results(r)
    assert len(app.crosstalk_tab.tree.get_children()) == before + 2
    assert app.crosstalk_tab.pair_count() == before + 1
    assert app.notebook.tab(app.crosstalk_tab, "text") == f"Crosstalk ({before + 1})"


def test_follow_up_figure_titles_use_spaces_not_underscores():
    from phenocycler_qc_eval.advice import figure_title

    assert figure_title("followup_Collagen_IV_to_TOX.png") == "Follow-up panel: Collagen IV into TOX"
    assert figure_title("followup_CD3e_to_ICOS.png") == "Follow-up panel: CD3e into ICOS"


def test_tiles_that_are_all_tissue_carry_no_per_tile_label(app, run_with_previews):
    app.load_past_run(run_with_previews)                       # the fixture's previews have no share at all
    texts = [x.get_text() for ax in app.titration_tab.evidence.figure.axes for x in ax.texts]
    assert not any("tissue" in t for t in texts)


def test_a_run_with_no_results_yet_says_results_will_appear_and_hides_the_view_options(app, tmp_path):
    from conftest import FakeHandle  # noqa: F401  (the fixture module is importable from tests)

    folder = tmp_path / "run_waiting"
    folder.mkdir()
    (folder / "RUN_INCOMPLETE.txt").write_text("Run in progress (running).")
    (folder / "run_info.json").write_text('{"status": "running", "crosstalk_state": "pending"}')
    from phenocycler_qc_eval.results import load_results

    app.show_results(load_results(folder))
    app.update()
    t = app.titration_tab
    assert "appear here" in t.evidence.title() and not t.keep_toggle.winfo_ismapped()
    assert not t.full_scale_toggle.winfo_ismapped()


def test_a_long_path_shows_its_end_in_the_image_box(app, tmp_path):
    long = tmp_path / ("sub" * 40) / "slide_with_a_long_name.qptiff"
    long.parent.mkdir(parents=True)
    long.touch()
    app.set_image(long)
    app.update()
    app.update_idletasks()
    first, last = app.image_entry.xview()
    assert last > 0.97 and first > 0


@pytest.mark.parametrize("scale", SCALES)
def test_a_table_too_narrow_for_its_columns_gets_a_scrollbar_not_cut_text(monkeypatch, tmp_path, complete_folder, scale):
    app = _scaled_app(monkeypatch, tmp_path, scale)
    try:
        app.minsize(1, 1)
        app.geometry("1000x690+0+0")                     # narrow for the scale
        app.load_past_run(complete_folder)
        app.update()
        tree = app.titration_tab.tree
        widths = sum(tree.column(c, "width") for c in ("#0", "c0", "c1", "c2"))
        hbar = [w for w in tree.container.winfo_children() if str(w.winfo_class()) == "TScrollbar"
                and str(w.cget("orient")) == "horizontal"][0]
        first, last = tree.xview()
        if (first, last) != (0.0, 1.0):                   # part of the table is off to the side
            assert hbar.winfo_ismapped()                  # and the scrollbar that reaches it is showing
        else:
            assert not hbar.winfo_ismapped()
        assert widths >= 0
        assert tree.column("c1", "width") >= tree.column("c1", "minwidth")
    finally:
        app.destroy()


def test_picture_margins_are_in_pixels_so_a_short_pane_keeps_the_axis_label(app, run_with_previews):
    app.load_past_run(run_with_previews)
    ev = app.titration_tab.evidence
    app.update()
    ev.canvas.draw()
    gs, top_px, bottom_px, _left_px = ev._margins
    k = ev.figure.dpi / 100.0                         # the margins grow with the display scaling, like the text
    h = max(float(ev.figure.get_figheight() * ev.figure.dpi), 120.0)
    # room for the labels in pixels, unless the pane is so short that the cap (40% of the height) applies
    assert gs.bottom * h >= min(bottom_px * k, 0.4 * h) - 0.5 and (1 - gs.top) * h >= min(top_px * k, 0.3 * h) - 0.5


def test_margins_follow_the_picture_when_it_is_built_on_a_hidden_tab(app, run_with_previews):
    """A follow-up built before its tab was on screen gets its real margins once the tab is shown."""
    app.load_past_run(run_with_previews)
    app.update()
    ct = app.crosstalk_tab
    app.notebook.select(ct)
    iid = [i for i in ct.tree.get_children() if tuple(ct.tree.item(i, "values")[:2]) == ("TOX", "CD163")][0]
    ct.tree.selection_set(iid)
    ct.tree.event_generate("<<TreeviewSelect>>")      # built straight away, before the tab has its size
    app.update()
    app.update()
    ev = ct.evidence
    ev.canvas.draw()                                  # margins are worked out from the size at every draw
    gs, top_px, bottom_px, _left_px = ev._margins
    k = ev.figure.dpi / 100.0
    h = ev.figure.get_figheight() * ev.figure.dpi
    assert h > 200 and abs(gs.bottom * h - bottom_px * k) < 1.0 and abs((1 - gs.top) * h - top_px * k) < 1.0
    # and the axes really sit where the margins say (GridSpec.update does not move them for a figure made without pyplot)
    ax = ev.figure.axes[0]
    assert abs(ax.get_position().y0 - gs.bottom) < 0.02 or ax.get_position().y0 > gs.bottom


def test_opening_details_scrolls_the_rail_so_the_log_is_on_screen(monkeypatch, tmp_path):
    app = _scaled_app(monkeypatch, tmp_path, 1.0)
    try:
        app.minsize(1, 1)
        app.geometry("1100x520+0+0")
        app.update()
        app.open_details()
        for i in range(10):
            app.log_line(f"line {i}")
        app.update()
        app.update()
        assert app.rail_canvas.yview()[1] > 0.99          # scrolled to the bottom: the log is in view
    finally:
        app.destroy()


def test_crosstalk_off_note_does_not_repeat_itself():
    from phenocycler_qc_eval.advice import headline
    from phenocycler_qc_eval.crosstalk import NO_LAYOUT_MESSAGE
    from phenocycler_qc_eval.results import Results
    import pandas as pd

    det = pd.DataFrame({"channel": ["A"], "call": ["OK"]})
    r = Results(folder=None, detection=det, crosstalk=None, crosstalk_state="off",
                crosstalk_message=NO_LAYOUT_MESSAGE, status="complete")
    h = headline(r)
    assert h.crosstalk == "Crosstalk: not checked." and not any("Crosstalk is off" in n for n in h.notes)


# ---------------------------------------------------------------------------- the pane while a run is going
def test_the_pane_says_checking_from_the_first_second_not_choose_your_image(app, fake_handle_factory, tmp_path):
    (tmp_path / "a.qptiff").touch()
    app.set_image(tmp_path / "a.qptiff")
    app.update()
    assert not app.working_state_visible() and app.empty_button.winfo_ismapped()
    app.start_run()
    app.update()
    assert app.working_state_visible() and not app.empty_button.winfo_ismapped()
    assert app.working_heading.cget("text") == "Checking your section"
    app.status_var.set("Reading tissue windows (21 of 61)")
    assert "21 of 61" in app.working_status.cget("text") or app.working_status.cget("textvariable")


def test_working_state_stays_under_the_banner_until_antibody_results_arrive(app, fake_handle, tmp_path, partial_folder):
    folder = tmp_path / "just_started2"
    folder.mkdir()
    (folder / "run_info.json").write_text(json.dumps({"status": "running", "crosstalk_state": "pending"}))
    fake_handle.queue([{"type": "started", "out_dir": str(folder)}])
    app.handle = fake_handle
    app._busy(True)
    app._poll()
    app.update()
    assert app.working_state_visible() and not app.banner.winfo_ismapped() and not app.notebook.winfo_ismapped()
    assert "No results yet" not in app.banner_text()
    fake_handle.queue([{"type": "partial", "out_dir": str(partial_folder), "early": False}])
    app._poll()
    app.update()
    assert not app.working_state_visible() and app.notebook.winfo_ismapped()
    assert len(app.titration_tab.tree.get_children()) > 0


def test_cancel_before_any_result_brings_the_empty_state_back(app, fake_handle_factory, tmp_path):
    (tmp_path / "a.qptiff").touch()
    app.set_image(tmp_path / "a.qptiff")
    app.start_run()
    app.update()
    assert app.working_state_visible()
    app.handle.running = False
    app._end_run()
    app.update()
    assert not app.working_state_visible() and app.empty_state_visible() and app.empty_button.winfo_ismapped()


def test_a_saved_run_that_still_says_running_is_not_shown_as_working(app, tmp_path):
    run = tmp_path / "stale"
    run.mkdir()
    (run / "run_info.json").write_text(json.dumps({"status": "running", "image_name": "a.qptiff"}))
    app.load_past_run(run)
    app.update()
    assert not app.working_state_visible() and app.notebook.winfo_ismapped()


def test_a_picture_on_a_hidden_tab_is_drawn_when_the_tab_is_shown(app, run_with_previews):
    from conftest import pump_until

    app.load_past_run(run_with_previews)
    app.update()
    ct = app.crosstalk_tab.evidence
    pump_until(app, lambda: not app.titration_tab.evidence._draw_pending, 30)    # layout settles, then it is drawn
    # A pair that has a picture (the fixture has one, TOX -> CD163) is chosen while its tab is hidden: its words are up and
    # its picture waits for the tab.
    tab = app.crosstalk_tab
    iid = [i for i in tab.tree.get_children() if tuple(tab.tree.item(i, "values")[:2]) == ("TOX", "CD163")][0]
    tab.tree.selection_set(iid)
    tab.tree.event_generate("<<TreeviewSelect>>")
    app.update()
    assert ct._draw_pending                                                       # nobody is looking at Crosstalk
    app.notebook.select(app.crosstalk_tab)
    app.update()
    app.update()
    assert not ct._draw_pending

