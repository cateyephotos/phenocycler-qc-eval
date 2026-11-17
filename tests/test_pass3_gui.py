"""Review fix pass 3, the window: results that arrive whole, no cut-off text, one name for each thing, the swap at finish.

The Y1 tests step Tk's event loop one event at a time and look at the window at every quiet point (nothing queued: the
moment a real window paints). At those points a person must never see an empty pane, a half-built page, a table narrower
than its pane, or tiles that belong to a different row than the one selected.
"""

from __future__ import annotations

import json
import shutil
import time
import tkinter as tk

import _tkinter
import pytest

from phenocycler_qc_eval.gui.app import App
from phenocycler_qc_eval.results import load_results
from synth import PROTO

pytestmark = pytest.mark.gui

CROSSTALK_FILES = ("stain_qc_crosstalk_pairs.csv", "stain_qc_spill_matrix.csv", "stain_qc_highpass_corr.csv",
                   "stain_qc_followups.json")


# ---------------------------------------------------------------------------- helpers
def watch(app, until, check, timeout: float = 40.0, gap: float = 0.004) -> bool:
    """Run the event loop one event at a time. At every quiet point (a real window paints now) call check(). Stops when
    until() is true; False when it never was."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not app.tk.dooneevent(_tkinter.DONT_WAIT):
            check()
            if until():
                return True
            time.sleep(gap)
    return False


def _row_of(tree, first_value):
    return next(i for i in tree.get_children() if str(tree.item(i, "values")[0]) == first_value)


def _pair_row(tree, source, target):
    return next(i for i in tree.get_children() if tuple(tree.item(i, "values")[:2]) == (source, target))


def _live_run(tmp_path, source) -> "tuple":
    """A run folder that is still going: antibody results and previews, no crosstalk yet. Returns (folder, files to add
    at the finish)."""
    live = tmp_path / "live_run"
    shutil.copytree(source, live)
    later = tmp_path / "later"
    later.mkdir()
    for name in CROSSTALK_FILES:
        if (live / name).exists():
            shutil.move(str(live / name), str(later / name))
    for p in list(live.glob("followup_*.png")) + list((live / "previews").glob("followup_*.npz")):
        shutil.move(str(p), str(later / p.name))
    info = json.loads((live / "run_info.json").read_text(encoding="utf-8")) if (live / "run_info.json").exists() else {}
    info.update({"status": "running", "crosstalk_state": "pending"})
    (live / "run_info.json").write_text(json.dumps(info), encoding="utf-8")
    (live / "RUN_INCOMPLETE.txt").write_text("Run in progress (running).", encoding="utf-8")
    return live, later


def _finish_live_run(live, later, source) -> None:
    for p in later.iterdir():
        dest = live / ("previews" if p.suffix == ".npz" else "") / p.name
        shutil.move(str(p), str(dest))
    info = json.loads((live / "run_info.json").read_text(encoding="utf-8"))
    src_info = json.loads((source / "run_info.json").read_text(encoding="utf-8")) if (source / "run_info.json").exists() else {}
    info.update({"status": "complete", "crosstalk_state": src_info.get("crosstalk_state", "on")})
    (live / "run_info.json").write_text(json.dumps(info), encoding="utf-8")
    (live / "RUN_INCOMPLETE.txt").unlink(missing_ok=True)


@pytest.fixture
def window(app, tmp_path):
    """The app at the size the review pictures use, with an image chosen so Run exists."""
    app.geometry("1280x820+40+40")
    image = tmp_path / "slide.qptiff"
    image.write_bytes(b"x")
    app.image_var.set(str(image))
    app.update()
    return app


# ---------------------------------------------------------------------------- Y1a: results arrive whole
def test_the_first_results_appear_once_whole_never_blank_or_half_built(window, fake_handle, run_with_previews, tmp_path):
    app = window
    live, _later = _live_run(tmp_path, run_with_previews)
    app.handle = fake_handle
    app.begin_wait("run")
    app._busy(True)
    fake_handle.queue([{"type": "started", "out_dir": str(live)},
                       {"type": "progress", "stage": "sampling", "fraction": 0.3, "message": "Reading window 40 of 61"},
                       {"type": "partial", "out_dir": str(live), "early": True}])
    app._poll()
    seen: list[str] = []

    def check() -> None:
        shown = app.results_visible()
        # Always something whole on screen: the working page, or the results. Never an empty pane.
        assert shown or app.working_frame.winfo_ismapped() or app.empty_frame.winfo_ismapped(), "an empty pane"
        if not shown:
            return
        t = app.titration_tab
        ev = t.evidence
        assert len(t.tree.get_children()) == len(load_results(live).detection), "a table with rows missing"
        assert t.tree.winfo_width() >= app.notebook.winfo_width() - 80, "a table narrower than its pane"
        assert ev.is_settled(), "a picture not drawn yet"
        assert ev.drawn_owner() == t.selected_channel(), "tiles of another row"
        assert ev.header_title() and app.banner_text().strip(), "an empty banner or detail pane"
        assert not t.clipped_cells() if hasattr(t, "clipped_cells") else True
        seen.append("shown")

    assert watch(app, lambda: bool(seen), check, timeout=60), "the results never appeared"
    assert app.reveal_state() == "shown"
    fake_handle.running = False


def test_while_the_results_are_built_the_working_page_stays_up(window, fake_handle, run_with_previews, tmp_path):
    app = window
    live, _later = _live_run(tmp_path, run_with_previews)
    app.handle = fake_handle
    app.begin_wait("run")
    app._busy(True)
    fake_handle.queue([{"type": "started", "out_dir": str(live)}, {"type": "partial", "out_dir": str(live)}])
    app._poll()
    assert app.reveal_state() == "building" and app.working_state_visible() and not app.results_visible()
    assert watch(app, app.results_visible, lambda: None, timeout=60)
    assert not app.working_state_visible()
    fake_handle.running = False


# ---------------------------------------------------------------------------- Y1b: the finish swaps in place
def test_at_the_finish_nothing_goes_blank_and_the_tiles_match_the_selected_row(window, fake_handle, run_with_previews,
                                                                                tmp_path):
    app = window
    live, later = _live_run(tmp_path, run_with_previews)
    app.handle = fake_handle
    app.begin_wait("run")
    app._busy(True)
    app._run_folder = live
    fake_handle.queue([{"type": "started", "out_dir": str(live)}, {"type": "partial", "out_dir": str(live)}])
    app._poll()
    assert watch(app, app.results_visible, lambda: None, timeout=60)
    t = app.titration_tab
    t.tree.selection_set(_row_of(t.tree, "CD14"))             # a Compare row the person is reading
    assert watch(app, lambda: t.evidence.drawn_owner() == "CD14", lambda: None, timeout=30)
    _finish_live_run(live, later, run_with_previews)
    fake_handle.queue([{"type": "done", "out_dir": str(live), "summary": {}}])
    app.bell = lambda *a, **k: None
    problems: list[str] = []
    checks = [0]

    def check() -> None:
        checks[0] += 1
        ev = t.evidence
        if not app.results_visible():
            problems.append("results not visible")
        if not ev.header_title() or not ev.header_subtitle():
            problems.append("the detail pane is blank")
        if ev.drawn_owner() != t.selected_channel():
            problems.append(f"tiles of {ev.drawn_owner()} under {t.selected_channel()}")
        if not app.banner_text().strip():
            problems.append("the banner is blank")

    app._poll()
    watch(app, lambda: app.handle is None and checks[0] > 25, check, timeout=30)
    assert not problems, sorted(set(problems))
    assert t.selected_channel() == "CD14"                      # the row the person was reading is still the one shown
    assert any("Finished" in n for n in app.banner_notes())


def test_opening_crosstalk_after_the_finish_never_shows_an_empty_pane(window, fake_handle, run_with_previews, tmp_path):
    app = window
    live, later = _live_run(tmp_path, run_with_previews)
    app.handle = fake_handle
    app.begin_wait("run")
    app._busy(True)
    app._run_folder = live
    fake_handle.queue([{"type": "started", "out_dir": str(live)}, {"type": "partial", "out_dir": str(live)}])
    app._poll()
    assert watch(app, app.results_visible, lambda: None, timeout=60)
    _finish_live_run(live, later, run_with_previews)
    fake_handle.queue([{"type": "done", "out_dir": str(live), "summary": {}}])
    app.bell = lambda *a, **k: None
    app._poll()
    assert watch(app, lambda: app.handle is None, lambda: None, timeout=30)
    ct = app.crosstalk_tab
    app.notebook.select(ct)
    problems: list[str] = []
    done = [0]

    def check() -> None:
        done[0] += 1
        ev = ct.evidence
        if not (ev.header_title() and ev.header_subtitle()):
            problems.append("empty words")
        if ev.drawn_owner() is None:
            problems.append("nothing drawn")
        if ct.clipped_cells():
            problems.append("cut-off cell")

    watch(app, lambda: done[0] > 12, check, timeout=30)
    assert not problems, sorted(set(problems))
    ct.tree.selection_set(_pair_row(ct.tree, "TOX", "CD163"))               # the one pair that has tiles in this fixture
    done[0] = 0
    watch(app, lambda: done[0] > 6 and ct.evidence.drawn_owner() == "TOX->CD163", check, timeout=30)
    assert not problems, sorted(set(problems))
    assert ct.evidence.drawn_owner() == "TOX->CD163" and ct.evidence.followup_rows()


def test_the_tiles_on_screen_always_belong_to_the_row_selected(window, run_with_previews):
    app = window
    app.load_past_run(run_with_previews)
    t = app.titration_tab
    wrong: list[str] = []

    def check() -> None:
        if t.evidence.drawn_owner() != t.selected_channel():
            wrong.append(f"{t.evidence.drawn_owner()} under {t.selected_channel()}")

    watch(app, lambda: t.evidence.is_settled(), check, timeout=20)
    for name in ("CD14", "iNOS", "CD31", "CD45", "CD20"):                 # quick clicks, one after the other
        t.tree.selection_set(_row_of(t.tree, name))
        watch(app, lambda: t.evidence.is_settled() and t.evidence.drawn_owner() == name, check, timeout=20)
        assert t.evidence.drawn_owner() == name
    assert not wrong, wrong[:3]


def test_a_reload_with_nothing_new_leaves_the_table_and_the_picture_alone(window, run_with_previews):
    app = window
    app.load_past_run(run_with_previews)
    t = app.titration_tab
    watch(app, t.evidence.is_settled, lambda: None, timeout=20)
    t.tree.selection_set(_row_of(t.tree, "CD14"))
    watch(app, lambda: t.evidence.drawn_owner() == "CD14" and t.evidence.is_settled(), lambda: None, timeout=20)
    before = (t.tree.get_children(), t.evidence.header_subtitle(), id(t.evidence.figure.axes[0]))
    t.load(load_results(run_with_previews))
    assert (t.tree.get_children(), t.evidence.header_subtitle(), id(t.evidence.figure.axes[0])) == before
    assert t.selected_channel() == "CD14"


# ---------------------------------------------------------------------------- E1 / Y3 / Y8: nothing cut off
SCREENS = [("100% 1280x820", 1.0, "1280x820+40+40", None), ("100% 1120x740", 1.0, "1120x740+20+20", None),
           ("100% 1366x768", 1.0, "1366x690+0+0", (1366, 768)), ("125%", 1.25, "1600x1025+20+20", None),
           ("125% 1366x768", 1.25, "1366x690+0+0", (1366, 768)), ("150%", 1.5, "1900x1140+0+0", None),
           ("150% 1920x1080", 1.5, "1900x1040+0+0", (1920, 1080)), ("150% 1366x768", 1.5, "1366x690+0+0", (1366, 768))]


# 150% display scaling on a 1366 x 768 screen leaves about 900 logical pixels: too narrow for the tables and a useful
# evidence pane side by side. The tables there scroll sideways (no cell is cut; a long cell shows its whole text as a tip).
SCROLL_ALLOWED = {"150% 1366x768"}


def _scaled_app(monkeypatch, tmp_path, scale, size, screen):
    monkeypatch.setenv("PQE_SETTINGS_DIR", str(tmp_path / "s"))
    for _ in range(3):
        try:
            a = App(simulate_scale=scale)
            break
        except tk.TclError:
            time.sleep(1)
    else:
        pytest.skip("no display for Tkinter")
    a.minsize(1, 1)
    a.geometry(size)
    a.screen_override = screen
    return a


@pytest.mark.parametrize("name,scale,size,screen", SCREENS, ids=[s[0] for s in SCREENS])
def test_the_crosstalk_tab_shows_every_cell_and_heading_whole(monkeypatch, tmp_path, run_with_previews, name, scale, size,
                                                              screen):
    a = _scaled_app(monkeypatch, tmp_path, scale, size, screen)
    try:
        a.load_past_run(run_with_previews)
        a.notebook.select(a.crosstalk_tab)
        ct = a.crosstalk_tab
        ct.tree.selection_set(_pair_row(ct.tree, "TOX", "CD163"))
        watch(a, lambda: ct.evidence.is_settled(), lambda: None, timeout=20)
        watch(a, lambda: False, lambda: None, timeout=2.5)
        assert ct.clipped_cells() == [], ct.clipped_cells()
        for tree in (ct.tree, ct.exclusive_tree):
            if name not in SCROLL_ALLOWED:
                assert tree.xview() == (0.0, 1.0), f"{name}: a table needs a horizontal scroll bar"
        assert ct.evidence.clipped_texts() == []
        header = ct.evidence.header
        for label in ct.evidence._facts_frame.winfo_children():
            assert label.winfo_x() + label.winfo_width() <= header.winfo_width() + 2, f"{name}: a number runs off the edge"
        # the tile labels: the longest label ("Pan-Cytokeratin", "no signal") fits inside the picture at left
        ct.exclusive_tree.selection_set(_row_of(ct.exclusive_tree, "Pan-Cytokeratin"))
        watch(a, lambda: ct.evidence.is_settled(), lambda: None, timeout=20)
        assert ct.evidence.pair_rows() and ct.evidence.clipped_texts() == []
    finally:
        a.destroy()


def test_a_long_cell_has_a_tip_with_its_whole_text(window, run_with_previews):
    app = window
    app.load_past_run(run_with_previews)
    t = app.titration_tab
    tips = t._tips
    tree = t.tree
    iid = tree.get_children()[0]
    narrow, floor = int(tree.column("c1", "width")), int(tree.column("c1", "minwidth"))
    tree.column("c1", minwidth=10, width=40)
    try:
        assert tips.clipped_cell_text(iid, "#2") == tree.item(iid, "values")[1]    # c1 is the second column: "#2"
    finally:
        tree.column("c1", width=narrow, minwidth=floor)
    assert tips.clipped_cell_text(iid, "#1") == ""                                  # a cell that fits has no tip


# ---------------------------------------------------------------------------- E2 in the window
def test_an_overlap_pair_with_a_near_blank_channel_says_so_and_shows_no_tiles(window, run_with_previews):
    app = window
    app.load_past_run(run_with_previews)
    app.notebook.select(app.crosstalk_tab)
    ct = app.crosstalk_tab
    verdicts = {(a, b): words for a, b, words in ct.exclusive_verdicts()}
    assert verdicts[("CD31", "CD20")] == "Not tested: near-blank channel"
    assert verdicts[("CD4", "CD20")] == "Overlap higher than expected" and verdicts[("SMA", "CD20")] == "As expected"
    row = _row_of(ct.exclusive_tree, "CD31")
    assert ct.exclusive_tree.item(row, "values")[3] == "-"                       # no ratio
    ct.exclusive_tree.selection_set(row)
    watch(app, ct.evidence.is_settled, lambda: None, timeout=20)
    assert ct.evidence.header_subtitle().startswith("Not tested: CD31 has little or no signal")
    assert ct.evidence.pair_rows() == [] and ct.evidence.tile_count() == 0 and ct.evidence.facts() == {}
    assert "ratio" not in ct.evidence.header_subtitle().lower()
    tested = _row_of(ct.exclusive_tree, "CD4")
    ct.exclusive_tree.selection_set(tested)
    watch(app, lambda: ct.evidence.pair_rows() != [], lambda: None, timeout=20)
    assert [n for n, _r in ct.evidence.pair_rows()] == ["CD4", "CD20"]


# ---------------------------------------------------------------------------- E3 / E4 / E11 / E14
def test_the_figures_tab_count_is_the_number_of_figures_listed(window, run_with_previews):
    for name in ("CD3e_to_ICOS", "TOX_to_CD163", "CD14_to_Podoplanin", "LAG3_to_TCF-1", "HLA-DR_to_CD66"):
        shutil.copy2(next(run_with_previews.glob("stain_qc_*.png")), run_with_previews / f"followup_{name}.png")
    app = window
    app.load_past_run(run_with_previews)
    assert len(app.results.figures) > len(app.figures_tab.titles())         # the follow-ups are on the Crosstalk tab
    text = app.notebook.tab(app.figures_tab, "text")
    assert text == f"Figures ({len(app.figures_tab.titles())})"


def test_the_pair_detail_says_each_thing_once_in_three_labeled_lines(window, run_with_previews):
    app = window
    app.load_past_run(run_with_previews)
    app.notebook.select(app.crosstalk_tab)
    ct = app.crosstalk_tab
    ct.tree.selection_set(_pair_row(ct.tree, "TOX", "CD163"))
    watch(app, ct.evidence.is_settled, lambda: None, timeout=20)
    lines = ct.evidence.header_subtitle().split("\n")
    assert [ln.split(":")[0] for ln in lines] == ["What to look for", "What the numbers say", "What to do"]
    assert ct.evidence.header_subtitle().lower().count("flagged") == 1
    assert list(ct.evidence.facts()) == ["Fine-detail match", "One-way excess"]
    assert "from TOX into CD163 is" in lines[1] and "from CD163 into TOX it is" in lines[1]


def test_a_compare_row_names_what_to_compare_in_the_table_itself(window, run_with_previews):
    app = window
    app.load_past_run(run_with_previews)
    t = app.titration_tab
    assert str(t.tree.item(_row_of(t.tree, "CD14"), "values")[2]).endswith("; compare with CD68")
    assert "compare with" not in str(t.tree.item(_row_of(t.tree, "CD45"), "values")[2])


def test_severity_and_evidence_are_not_two_labels_with_colons(window, run_with_previews):
    app = window
    app.load_past_run(run_with_previews)
    t = app.titration_tab
    shown = str(t.tree.item(_row_of(t.tree, "CD31"), "values")[2])
    assert shown.startswith("Strong. Dim: brightest") and shown.count(":") == 1


# ---------------------------------------------------------------------------- Y4 / Y6
def test_a_near_blank_antibody_asks_for_a_first_check_and_says_it_once(window, run_with_previews):
    app = window
    app.load_past_run(run_with_previews)
    t = app.titration_tab
    t.tree.selection_set(_row_of(t.tree, "CD31"))
    watch(app, t.evidence.is_settled, lambda: None, timeout=20)
    assert "Before raising, check that this antibody was added and this channel was imaged." in t.evidence.header_subtitle()
    said = " ".join(x.get_text() for x in t.evidence.figure.texts + [x for ax in t.evidence.figure.axes for x in ax.texts])
    assert "Little or no signal" in said and t.evidence.caption_text() == ""      # in the picture, not also in a caption


def test_the_default_window_shows_the_histogram_axis_label_whole_without_scrolling(window, run_with_previews):
    app = window
    app.load_past_run(run_with_previews)
    t = app.titration_tab
    watch(app, t.evidence.is_settled, lambda: None, timeout=20)
    watch(app, lambda: False, lambda: None, timeout=3.0)
    assert t.page.fits() and t.evidence.fits(), (
        t.page.fits(), t.evidence.fits(), t.page.content_height(), t.winfo_height(), t.evidence.scroll.content_height(),
        t.evidence.winfo_height(), app.winfo_height(), app.banner.winfo_height(), app.banner_notes())
    assert t.evidence.clipped_texts() == [] and "Pixel value" in [x.get_text() for ax in t.evidence.figure.axes
                                                                   for x in [ax.xaxis.label]]


# ---------------------------------------------------------------------------- Y5
def test_the_help_window_shows_a_table_as_one_wrapped_line_per_row_not_monospace_columns(window):
    win = window.show_help()
    window.update()
    try:
        text = win.text
        assert "table" not in text.tag_names()
        start = text.search("Raise concentration (or lengthen exposure)", "1.0")
        assert start, "the key row is in the window"
        assert "bold" in text.tag_names(start) and "trow" in text.tag_names(start)
        line = text.get(start, f"{start} lineend")
        assert line.startswith("Raise concentration (or lengthen exposure)  The stain is too weak")
        assert "Consolas" not in str(text.tag_cget("trow", "font"))
        assert "  |  " not in text.get("1.0", "end") and "|---" not in text.get("1.0", "end")
    finally:
        win.destroy()


# ---------------------------------------------------------------------------- E5 / Y7 / Y9 / C2 / E7
def test_the_rail_keeps_bar_status_and_time_while_the_step_list_is_on_the_working_page(window, fake_handle):
    app = window
    app.handle = fake_handle
    app.begin_wait("run")
    app._busy(True)
    fake_handle.queue([{"type": "progress", "stage": "sampling", "fraction": 0.3, "message": "Reading window 21 of 61"}])
    app._poll()
    app.update()
    assert app.working_state_visible() and len(app.working_stages_text().splitlines()) == 5
    assert "Reading windows (window 21 of 61)" in app.working_stages_text()
    assert app.stage_var.get() == "" and not (hasattr(app, "stage_label") and app.stage_label.winfo_ismapped())
    # Pass 4 (Y7): the bar and the status line are on the working page, so the rail does not show them a second time.
    assert app.run_button.winfo_ismapped() and app.cancel_button.winfo_ismapped()
    assert app.working_progress.winfo_ismapped() and not app.progress.winfo_ismapped()
    assert not app.status_label.winfo_ismapped()
    bottom = app.runbar.winfo_rooty() + app.runbar.winfo_height()
    assert bottom <= app.winfo_rooty() + app.winfo_height() + 1                       # nothing below the window's edge


def test_the_finish_says_where_the_results_are_once(window, fake_handle, complete_folder):
    app = window
    app.handle = fake_handle
    app._busy(True)
    app._run_folder = complete_folder
    app.bell = lambda *a, **k: None
    fake_handle.queue([{"type": "done", "out_dir": str(complete_folder), "summary": {}}])
    app._poll()
    app.update()
    assert app.status_var.get() == "Finished."
    assert any(complete_folder.name in n for n in app.banner_notes())
    assert app.full_path_tip() == str(complete_folder)


def test_a_whole_section_run_says_how_long_it_takes(window, fake_handle):
    app = window
    app.handle = fake_handle
    app.begin_wait("run")
    app._busy(True)
    fake_handle.queue([{"type": "progress", "stage": "sampling", "fraction": 0.1, "message": "Reading window 3 of 200"}])
    app._poll()
    assert "full section" in app.working_detail_text() and "10 to 15 minutes" in app.working_detail_text()


def test_no_panel_says_crosstalk_was_not_checked_once_and_the_tab_says_what_to_do(window, run_without_panel):
    app = window
    app.image_var.set("x")
    app.load_past_run(run_without_panel)
    app.notebook.select(app.crosstalk_tab)
    app.update()
    ct = app.crosstalk_tab
    shown = " ".join([app.banner_text(), ct.message_text(), app.panel_hint.cget("text"), ct.evidence.header_subtitle(),
                      ct.evidence.title()]).lower()
    assert shown.count("not checked") + shown.count("was not checked") + shown.count("crosstalk is off") == 1, shown
    assert "choose" in ct.message_text().lower() and ct.template_button.winfo_ismapped()
    assert app.panel_hint.cget("text").startswith("Optional.")


def test_the_example_download_shows_its_page_at_once_with_the_status_inside_the_window(window, monkeypatch):
    from conftest import FakeHandle

    monkeypatch.setattr(App, "make_handle", lambda self, request: FakeHandle())
    app = window
    app.image_var.set("")
    app.try_example()
    seen = []

    def check() -> None:
        assert app.working_frame.winfo_ismapped() and app.working_frame.winfo_width() > 300, "an empty pane"
        assert app.working_stages_text().startswith("● Downloading the example")
        seen.append(1)
        bottom = app.working_status.winfo_rooty() + app.working_status.winfo_height()
        assert bottom <= app.winfo_rooty() + app.winfo_height() + 1, "the status runs below the window"

    watch(app, lambda: len(seen) > 8, check, timeout=15)
    assert len(seen) > 8
    app.handle.running = False
    app._end_run("download")


# ---------------------------------------------------------------------------- E12 / E15 / Y8
def test_a_narrow_pane_puts_the_buttons_under_the_image_name_instead_of_cutting_it(monkeypatch, tmp_path,
                                                                                    run_with_previews):
    a = _scaled_app(monkeypatch, tmp_path, 1.5, "1366x690+0+0", (1366, 768))
    try:
        a.load_past_run(run_with_previews)
        a.update()
        for _ in range(3):
            a.update()
        assert a.run_header.winfo_width() >= a.run_header.winfo_reqwidth()
        assert a.header_buttons["help"].winfo_rooty() > a.run_header.winfo_rooty()
        assert all(b.winfo_rootx() + b.winfo_width() <= a.winfo_rootx() + a.winfo_width() for b in a.header_buttons.values())
    finally:
        a.destroy()


@pytest.mark.parametrize("scale,size,screen", [(1.5, "1900x1040+0+0", (1920, 1080)), (1.25, "1366x690+0+0", (1366, 768))])
def test_the_key_under_the_table_wraps_before_the_checkbox(monkeypatch, tmp_path, run_with_previews, scale, size, screen):
    a = _scaled_app(monkeypatch, tmp_path, scale, size, screen)
    try:
        a.load_past_run(run_with_previews)
        watch(a, lambda: False, lambda: None, timeout=3.0)
        t = a.titration_tab
        assert t._key.winfo_reqwidth() <= t._key.winfo_width() + 1, "the key is wider than the room it has"
        assert t._key.winfo_x() + t._key.winfo_width() <= t.keep_toggle.winfo_x() + 2
    finally:
        a.destroy()


def test_an_error_banner_keeps_its_text_clear_of_the_red_bar(window):
    window.show_error("reader", "tonsil.qptiff: the file ends before its last tile.")
    window.update()
    for _ in range(3):
        window.update()
    bar_right = window.banner_bar.winfo_x() + window.banner_bar.winfo_width()
    assert window.banner_label.winfo_x() - bar_right >= 10
    assert window.banner_action_label.winfo_x() - bar_right >= 10


# ---------------------------------------------------------------------------- the page after an image is chosen
def test_after_an_image_is_chosen_the_page_points_at_run(window, tmp_path):
    app = window
    app.image_var.set("")
    app.update()
    assert app.empty_steps.cget("text").startswith("1. Choose your image.")
    image = tmp_path / "tonsil.qptiff"
    image.write_bytes(b"x")
    app.set_image(image)
    app.update()
    assert app.empty_button.cget("text") == "Run"
    assert app.empty_steps.cget("text") == "Ready: tonsil.qptiff\nPress Run to check it."
    app.image_var.set("")
    app.update()
    assert app.empty_steps.cget("text").startswith("1. Choose your image.")


def test_the_figure_picture_shows_a_hand_because_it_opens_full_size_on_a_double_click(window, run_with_previews):
    app = window
    app.load_past_run(run_with_previews)
    assert str(app.figures_tab.evidence.canvas.get_tk_widget().cget("cursor")) == "hand2"
    assert "Double-click" in app.figures_tab.hint_text()


# ---------------------------------------------------------------------------- the sash, the figure count, big counts
def test_the_table_and_the_evidence_keep_their_share_when_the_banner_grows_a_line(window, run_with_previews):
    """The finish adds a note to the banner: the page is a line shorter, and the picture must still fit under the table."""
    app = window
    app.load_past_run(run_with_previews)
    t = app.titration_tab
    t.tree.selection_set(_row_of(t.tree, "CD20"))                          # a Compare row: the longest text above the picture
    watch(app, lambda: False, lambda: None, timeout=3.0)
    app.add_banner_note("Finished in 5:01. Results are saved in ...\tonsil31_crop_20261114-070253.")
    watch(app, lambda: False, lambda: None, timeout=4.0)
    assert t.page.fits() and t.evidence.fits(), (t.page.fits(), t.evidence.fits(), t.evidence.scroll.content_height(),
                                               t.evidence.winfo_height())
    assert t.evidence.clipped_texts() == []
    t._sash_dragged = True                                                  # a dragged sash stays where it is
    before = t.split.sashpos(0)
    t._place_split()
    assert t.split.sashpos(0) == before


def test_the_figures_tab_has_no_count_while_there_are_no_figures(window, partial_folder):
    app = window
    app.load_past_run(partial_folder)
    assert app.notebook.tab(app.figures_tab, "text") == "Figures"


def test_a_histogram_with_counts_in_the_millions_keeps_its_axis_label(window, run_with_previews):
    import numpy as np

    path = run_with_previews / "previews" / "antibody_CD31.npz"
    data = dict(np.load(path, allow_pickle=True))
    counts = np.asarray(data["hist_counts"]).copy()
    counts[0] = 2_500_000
    data["hist_counts"] = counts
    np.savez(path, **data)
    app = window
    app.load_past_run(run_with_previews)
    t = app.titration_tab
    t.tree.selection_set(_row_of(t.tree, "CD31"))
    watch(app, lambda: t.evidence.drawn_owner() == "CD31" and t.evidence.is_settled(), lambda: None, timeout=20)
    assert t.evidence.clipped_texts() == [], t.evidence.clipped_texts()
    assert "Pixels (log scale)" in [ax.yaxis.label.get_text() for ax in t.evidence.figure.axes]


def test_the_rows_hang_from_the_top_of_the_picture_and_a_not_tested_message_sits_under_the_words(window, run_with_previews):
    app = window
    app.load_past_run(run_with_previews)
    app.notebook.select(app.crosstalk_tab)
    ct = app.crosstalk_tab
    ct.exclusive_tree.selection_set(_row_of(ct.exclusive_tree, "CD4"))
    watch(app, lambda: ct.evidence.drawn_owner() == "CD4+CD20" and ct.evidence.is_settled(), lambda: None, timeout=20)
    assert ct.evidence.figure.axes[0].get_anchor() == "N"
    ct.exclusive_tree.selection_set(_row_of(ct.exclusive_tree, "CD31"))
    watch(app, lambda: str(ct.evidence.drawn_owner()).startswith("message") and ct.evidence.is_settled(), lambda: None,
          timeout=20)
    assert any(t.get_position()[1] > 0.7 for t in ct.evidence.figure.texts)
