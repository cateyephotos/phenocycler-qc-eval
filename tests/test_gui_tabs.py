"""Result tabs, banner notes and the evidence canvas (one shared window per module)."""

from __future__ import annotations

import tkinter as tk

import pytest
from conftest import new_app

from phenocycler_qc_eval.gui.app import App
from phenocycler_qc_eval.results import load_preview, load_results, safe_name
from synth import PROTO, copy_only, write_followup_preview

pytestmark = pytest.mark.gui


@pytest.fixture(scope="module")
def shared(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("PQE_SETTINGS_DIR", str(tmp_path_factory.mktemp("settings")))
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
def app(shared):
    return shared


def _select(tree, app, row):
    tree.selection_set(row)
    app.update()


def _pair_row(tree, source, target):
    return next(i for i in tree.get_children() if tree.item(i)["values"][:2] == [source, target])


def test_titration_has_one_next_run_column(app):
    app.load_past_run(PROTO); app.update()
    cols = [app.titration_tab.tree.heading(c)["text"] for c in app.titration_tab.tree["columns"]]
    assert cols == ["Antibody", "Next run", "Evidence"]
    assert "Exposure" not in cols and "Concentration" not in cols


def test_rows_to_change_come_first(app):
    app.load_past_run(PROTO); app.update()
    t = app.titration_tab.tree
    nexts = [t.item(i)["values"][1] for i in t.get_children()]
    assert nexts[0].startswith(("Raise", "Lower"))
    rank = {"change": 0, "check": 1, "keep": 2}
    kinds = [
        "change" if n.startswith(("Raise", "Lower")) else "check" if n.startswith("Compare") else "keep"
        for n in nexts
    ]
    assert kinds == sorted(kinds, key=rank.get)
    assert len(nexts) == len(load_results(PROTO).detection)


def test_antibody_row_shows_preview(app, run_with_previews):
    app.load_past_run(run_with_previews); app.update()
    t = app.titration_tab
    row = t.tree.get_children()[0]
    _select(t.tree, app, row)
    name = str(t.tree.item(row)["values"][0])
    assert t.evidence.title().startswith(name + ":")
    assert t.evidence.tile_count() == 3 and len(t.evidence.figure.axes) == 2  # the three tiles as one picture, and the histogram


def test_prototype_run_without_previews_says_so(app):
    app.load_past_run(PROTO); app.update()
    t = app.titration_tab
    _select(t.tree, app, t.tree.get_children()[0])
    assert t.evidence.title() == "No preview images were saved for this run."


def test_crosstalk_row_falls_back_to_png(app):
    app.load_past_run(PROTO); app.update()
    t = app.crosstalk_tab
    _select(t.tree, app, _pair_row(t.tree, "TOX", "CD163"))
    assert "TOX" in t.evidence.title() and "CD163" in t.evidence.title()
    assert len(t.evidence.figure.axes) == 1  # the saved PNG


def test_crosstalk_row_without_any_followup_says_so(app):
    app.load_past_run(PROTO); app.update()
    t = app.crosstalk_tab
    _select(t.tree, app, _pair_row(t.tree, "CD8", "CD21"))
    assert t.evidence.title().startswith("No follow-up panel was saved")


def test_crosstalk_row_shows_followup_preview(app, run_with_previews):
    app.load_past_run(run_with_previews); app.update()
    t = app.crosstalk_tab
    _select(t.tree, app, _pair_row(t.tree, "TOX", "CD163"))
    assert t.evidence.title().startswith("TOX -> CD163: CD163 reads 11.6 where TOX is bright")
    assert t.evidence.header_title() == "TOX -> CD163"
    assert "CD163 reads 11.6 where TOX is bright and 0.4" in t.evidence.header_subtitle()
    assert t.evidence.header_subtitle().startswith("What to look for:")      # first, in normal text
    assert t.evidence.followup_rows() == [("TOX", "source"), ("CD163", "target"), ("CD68", "control"), ("DAPI", "nuclei")]
    assert len(t.evidence.figure.axes) == 1  # all four rows of tiles are one picture


def test_followup_without_control_has_three_rows(app, tmp_path):
    p = tmp_path / "f.npz"
    write_followup_preview(p, "A", "B", with_control=False)
    ev = app.crosstalk_tab.evidence
    ev.show_followup(load_preview(p)); app.update()
    assert [role for _name, role in ev.followup_rows()] == ["source", "target", "nuclei"]   # three rows, no control
    assert ev.followup_names() == ("A", "B", None)          # the reading sentence is built from these, above the tiles
    assert not any("leak" in t.get_text() for t in ev.figure.texts)


def test_exclusive_pairs_table_shown(app):
    app.load_past_run(PROTO); app.update()
    c = app.crosstalk_tab
    assert len(c.exclusive_tree.get_children()) == len(load_results(PROTO).exclusive_pairs)
    cols = [c.exclusive_tree.heading(i)["text"] for i in c.exclusive_tree["columns"]]
    assert cols == ["Marker A", "Marker B", "Result", "Ratio"]       # the overlap and the chance figure are in the pane
    cols = [c.tree.heading(i)["text"] for i in c.tree["columns"]]
    assert cols == ["Source", "Target", "Result", "Likely cause"]


def test_crosstalk_off_message(app, run_without_panel):
    app.load_past_run(run_without_panel); app.update()
    c = app.crosstalk_tab
    assert "no panel file" in c.message_text().lower()
    assert c.tree.get_children() == ()
    assert len(c.exclusive_tree.get_children()) > 0  # still shown when crosstalk is off


def test_banner_notes_for_cancelled_run(app, cancelled_run):
    app.load_past_run(cancelled_run); app.update()
    notes = app.banner_notes()
    assert any(n.startswith("Run cancelled:") for n in notes)
    assert sum(n.startswith("Run cancelled:") for n in notes) == 1  # not repeated


def test_figures_tab_lists_and_shows_figure(app):
    app.load_past_run(PROTO); app.update()
    f = app.figures_tab
    assert f.names() == ["stain_qc_detection.png"]          # the follow-up panels live on the Crosstalk tab
    assert "Marker detection: stained vs background" in f.titles()
    _select(f.tree, app, f.tree.get_children()[0])
    assert f.evidence.title() == "Marker detection: stained vs background"
    assert f.evidence.header_subtitle() == "stain_qc_detection.png"
    assert str(f.report_button.cget("state")) == "normal"


def test_evidence_canvas_clear_and_bad_image(app, tmp_path):
    ev = app.figures_tab.evidence
    ev.clear("Nothing here.")
    assert ev.title() == "Nothing here."
    bad = tmp_path / "bad.png"
    bad.write_text("not a png")
    ev.show_image(bad, "bad")
    assert "could not be opened" in ev.title()


def test_first_antibody_is_selected_on_load_with_its_numbers(app, run_with_previews):
    app.load_past_run(run_with_previews); app.update()
    t = app.titration_tab
    first = t.tree.get_children()[0]
    assert t.tree.selection() == (first,)
    facts = t.evidence.facts()
    assert list(facts) == ["Signal (brightest 0.5%)", "Stain index", "Stained pixels", "Saturated pixels"]
    assert t.evidence.header_title().startswith(str(t.tree.item(first)["values"][0]) + ": ")
    assert t.evidence.header_subtitle()  # the full evidence sentence, wrapped in the pane


def test_saturation_not_checked_is_shown_in_the_detail_pane(app, results_no_panel_float):
    app.load_past_run(results_no_panel_float.folder); app.update()
    t = app.titration_tab
    assert t.evidence.facts()["Saturated pixels"] == "not checked"
    evidence = [t.tree.item(i)["values"][2] for i in t.tree.get_children()]
    assert not any("saturated" in str(e) for e in evidence)  # no number shown for a skipped check


def test_saturation_value_is_shown_when_checked(app):
    app.load_past_run(PROTO); app.update()
    t = app.titration_tab
    hif1a = next(i for i in t.tree.get_children() if t.tree.item(i)["values"][0] == "HIF1A")
    _select(t.tree, app, hif1a)
    assert t.evidence.facts()["Saturated pixels"] == "3.78%"


def test_evidence_column_text_fits(app):
    app.load_past_run(PROTO); app.update()
    t = app.titration_tab
    assert all(len(str(t.tree.item(i)["values"][2])) <= 55 for i in t.tree.get_children())


def test_empty_figures_message(app, tmp_path):
    run = copy_only(PROTO, tmp_path, ["stain_qc_channels.csv"], folder_name="no_figs")
    app.load_past_run(run); app.update()
    f = app.figures_tab
    assert f.names() == []
    assert f.evidence.title() == "No figures were saved for this run."
    assert str(f.report_button.cget("state")) == "disabled"


def test_corrupt_antibody_preview_shows_message(app, run_with_previews):
    app.load_past_run(run_with_previews); app.update()
    marker = app.titration_tab.selected_channel()           # the row the window selects by itself
    (run_with_previews / "previews" / f"antibody_{safe_name(marker)}.npz").write_bytes(b"not a zip")
    app.load_past_run(run_with_previews); app.update()  # the same row is shown on load
    t = app.titration_tab
    assert t.evidence.title() == "That preview could not be read."
    assert t.evidence.header_title().startswith(marker)  # numbers still shown


def test_corrupt_followup_preview_shows_message(app, run_with_previews):
    (run_with_previews / "previews" / "followup_TOX_to_CD163.npz").write_bytes(b"PK\x03\x04junk")
    app.load_past_run(run_with_previews); app.update()
    t = app.crosstalk_tab
    _select(t.tree, app, _pair_row(t.tree, "TOX", "CD163"))
    assert t.evidence.title() == "That preview could not be read."


def test_open_report_failure_is_shown_in_the_window(app, monkeypatch):
    import os
    app.load_past_run(PROTO); app.update()
    f = app.figures_tab
    def boom(_path):
        raise OSError("no program is set to open this file")
    monkeypatch.setattr(os, "startfile", boom, raising=False)
    f.report_button.invoke(); app.update()
    assert f.open_message.cget("text").startswith("Could not open the report:")
    assert "no program" in f.open_message.cget("text")


def test_crosstalk_detail_has_cause_and_reason(app):
    app.load_past_run(PROTO); app.update()
    t = app.crosstalk_tab
    _select(t.tree, app, _pair_row(t.tree, "CD8", "CD21"))
    assert "Likely cause: carryover from the previous cycle." in t.evidence.header_subtitle()
    assert "one direction is much stronger" in t.evidence.header_subtitle()
    assert t.tree.item(_pair_row(t.tree, "CD8", "CD21"))["values"][3] == "Carryover"      # the pane has the whole phrase


def test_antibody_caption_shows_the_real_range_for_a_12_bit_preview(app, tmp_path):
    import shutil

    import numpy as np

    from synth import write_antibody_preview

    run = tmp_path / "run_12bit"
    shutil.copytree(PROTO, run)
    row = load_results(run).detection.iloc[0]
    path = run / "previews" / f"antibody_{safe_name(row['channel'])}.npz"
    write_antibody_preview(path, str(row["channel"]))
    with np.load(path) as z:
        data = {k: z[k] for k in z.files}
    data.update(display_min=0.0, display_max=1800.0)       # tiles are stored scaled to 0..255
    np.savez(path, **data)
    ev = app.titration_tab.evidence
    ev.show_antibody(load_preview(path), row, 4095.0)
    caption = ev.caption_text()
    assert "display range" in caption and "0-1800 of 4095" in caption and "0-255" not in caption
