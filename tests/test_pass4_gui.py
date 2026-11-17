"""Review fix pass 4, the window: a click gets an answer at once, and the evidence arrives fast and always belongs to the
row that was clicked.

Pictures are made in a background thread in the real window; these tests turn that on (`background_pictures`) and step the
event loop. Every older test runs with the picture made on the calling thread (see conftest) so it can look straight away.
"""

from __future__ import annotations

import sys
import threading
import time

import pytest

from conftest import pump_until

pytestmark = pytest.mark.gui


# ---------------------------------------------------------------------------- helpers
def choose(tree, iid: str) -> float:
    """What a click on a table row does; returns the seconds the click's own event took."""
    t0 = time.monotonic()
    tree.selection_set(iid)
    tree.focus(iid)
    tree.event_generate("<<TreeviewSelect>>")
    return time.monotonic() - t0


def row_of(tree, first_value: str) -> str:
    return next(i for i in tree.get_children() if str(tree.item(i, "values")[0]) == first_value)


def slow_pictures(monkeypatch, seconds: float):
    """Make drawing a picture take at least `seconds` (the thread sleeps), so the loading state can be looked at."""
    from phenocycler_qc_eval.gui.scene import Scene

    real = Scene.render_png

    def slow(self, *a, **k):
        time.sleep(seconds)
        return real(self, *a, **k)

    monkeypatch.setattr(Scene, "render_png", slow)


def settled(ev) -> bool:
    return ev.is_settled() and not ev.is_loading()


@pytest.fixture
def window(background_pictures, app, run_with_previews):
    app.geometry("1280x820+40+40")
    app.load_past_run(run_with_previews)
    pump_until(app, lambda: settled(app.titration_tab.evidence), 60)
    return app


def other_rows(tab, n: int) -> list[str]:
    shown = tab.selected_channel()
    return [i for i in tab.tree.get_children() if str(tab.tree.item(i, "values")[0]) != shown][:n]


# ---------------------------------------------------------------------------- Z1 / F1 (a): the answer to a click
def test_a_click_on_a_row_answers_in_its_own_event_before_anything_is_drawn(window, monkeypatch):
    slow_pictures(monkeypatch, 0.6)                    # long enough to look at the loading state
    t = window.titration_tab
    ev = t.evidence
    old_title = ev.header_title()
    iid = other_rows(t, 1)[0]
    name = str(t.tree.item(iid, "values")[0])

    took = choose(t.tree, iid)

    # asserted before any update() beyond what the click itself did
    assert took < 0.25, f"the click's own event took {took:.3f} s"
    assert t.selected_channel() == name, "the row is selected"
    assert ev.is_loading()
    assert ev.header_title() == f"Loading evidence for {name}..."
    assert ev.header_subtitle() == "" and ev.facts() == {}, "the old row's words are gone"
    assert ev.drawn_owner() is None and ev.picture_size() is None, "the old row's tiles are gone"
    assert old_title not in ev.header_title()
    pump_until(window, lambda: settled(ev), 60)
    assert ev.header_title().startswith(f"{name}:") and ev.drawn_owner() == name


def test_the_loading_state_never_shows_one_rows_words_under_anothers_selection(window, monkeypatch):
    slow_pictures(monkeypatch, 0.3)
    t = window.titration_tab
    ev = t.evidence
    for iid in other_rows(t, 3):
        name = str(t.tree.item(iid, "values")[0])
        choose(t.tree, iid)
        end = time.monotonic() + 60
        while time.monotonic() < end and not settled(ev):
            window.update()
            title = ev.header_title()
            assert title.startswith((f"Loading evidence for {name}", f"{name}:")), f"{title!r} under {name}"
            assert ev.drawn_owner() in (None, name)
            time.sleep(0.01)
        assert ev.drawn_owner() == name


# ---------------------------------------------------------------------------- Z1 / F1 (b): fast, and always the right row
def test_two_quick_clicks_show_the_second_row_and_never_the_first(window, monkeypatch):
    slow_pictures(monkeypatch, 0.5)
    t = window.titration_tab
    ev = t.evidence
    a, b = other_rows(t, 2)
    name_a, name_b = (str(t.tree.item(i, "values")[0]) for i in (a, b))
    before = len(ev.shown_log())
    choose(t.tree, a)
    window.update()
    choose(t.tree, b)                                  # before A's picture is ready
    pump_until(window, lambda: settled(ev) and ev.owner() == name_b, 60)
    time.sleep(1.2)                                    # A's picture finishes meanwhile
    for _ in range(20):
        window.update()
        time.sleep(0.02)
    owners = [o for o, _k in ev.shown_log()[before:]]
    assert name_a not in owners, f"the first row's picture was put on screen: {owners}"
    assert owners and owners[-1] == name_b
    assert ev.header_title().startswith(f"{name_b}:") and t.selected_channel() == name_b


def test_a_past_run_without_pictures_made_ahead_still_shows_the_evidence(window):
    """Nothing was made ahead (the switch is off here): every click makes its own picture, with the loading state first."""
    t = window.titration_tab
    ev = t.evidence
    for iid in other_rows(t, 3):
        name = str(t.tree.item(iid, "values")[0])
        assert not ev.cache_has(t._row_plan(iid)[1])
        choose(t.tree, iid)
        assert ev.is_loading()
        pump_until(window, lambda: settled(ev), 60)
        assert ev.header_title().startswith(f"{name}:") and ev.drawn_owner() == name
        assert ev.picture_size() is not None


def test_a_row_seen_before_comes_back_in_the_same_event(window):
    t = window.titration_tab
    ev = t.evidence
    first = t.selected_channel()
    iid = other_rows(t, 1)[0]
    choose(t.tree, iid)
    pump_until(window, lambda: settled(ev), 60)
    back = row_of(t.tree, first)
    took = choose(t.tree, back)
    assert not ev.is_loading(), "a picture seen a moment ago must not need loading again"
    assert ev.drawn_owner() == first and ev.header_title().startswith(f"{first}:")
    assert took < 0.5, f"{took:.3f} s"


def test_the_other_rows_pictures_are_made_ahead_so_a_click_finds_its_picture_ready(background_pictures, app,
                                                                                  run_with_previews, monkeypatch):
    from phenocycler_qc_eval.gui.evidence import EvidenceCanvas

    monkeypatch.setattr(EvidenceCanvas, "PREFETCH", True)
    monkeypatch.setattr(EvidenceCanvas, "PREFETCH_WAIT_MS", 50)
    app.geometry("1280x820+40+40")
    app.load_past_run(run_with_previews)
    t = app.titration_tab
    ev = t.evidence
    pump_until(app, lambda: settled(ev), 60)
    rows = list(t.tree.get_children())
    nxt = rows[rows.index(t._shown) + 1]
    pump_until(app, lambda: ev.cache_has(t._row_plan(nxt)[1]), 120)       # made while nobody was looking
    took = choose(t.tree, nxt)
    assert not ev.is_loading(), "the picture was made ahead: it comes in the click's own event"
    assert ev.drawn_owner() == str(t.tree.item(nxt, "values")[0])
    assert took < 0.5, f"{took:.3f} s"


def test_the_picture_is_made_at_the_pixels_it_fills_so_it_is_sharp(window):
    ev = window.titration_tab.evidence
    w, h = ev.picture_size()
    assert abs(w - ev.pic.winfo_width()) <= 24, "one image pixel per screen pixel, never stretched"
    assert ev.pic.winfo_height() >= h - 6
    assert not ev.scrollable(), "and the page fits"


def test_at_150_percent_the_picture_is_made_for_that_scaling(background_pictures, monkeypatch, tmp_path, run_with_previews):
    from phenocycler_qc_eval.gui.app import App

    monkeypatch.setenv("PQE_SETTINGS_DIR", str(tmp_path / "_s"))
    app = App(simulate_scale=1.5)
    try:
        app.geometry("1280x820+40+40")
        app.update()
        app.load_past_run(run_with_previews)
        ev = app.titration_tab.evidence
        pump_until(app, lambda: settled(ev), 60)
        assert ev.figure.dpi == pytest.approx(150.0, rel=0.01)
        assert abs(ev.picture_size()[0] - ev.pic.winfo_width()) <= 24
    finally:
        app.destroy()


def test_pictures_are_made_off_the_tk_thread_and_never_with_pyplot(window, monkeypatch):
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    drawn_on = []
    real = FigureCanvasAgg.draw

    def spy(self):
        drawn_on.append(threading.get_ident())
        return real(self)

    monkeypatch.setattr(FigureCanvasAgg, "draw", spy)
    t = window.titration_tab
    iid = other_rows(t, 1)[0]
    choose(t.tree, iid)
    pump_until(window, lambda: settled(t.evidence), 60)
    assert drawn_on, "a picture was drawn"
    assert threading.get_ident() not in drawn_on, "the Tk thread drew a picture"
    assert "matplotlib.pyplot" not in sys.modules


def test_a_resize_keeps_the_picture_on_screen_and_then_makes_one_for_the_new_size(window):
    ev = window.titration_tab.evidence
    old = ev.picture_size()
    old_room = ev.room()
    window.geometry("1000x720+40+40")
    end_room = time.monotonic() + 5
    while time.monotonic() < end_room and ev.room() == old_room:
        window.update()
        time.sleep(0.05)
    if ev.room() == old_room:
        pytest.skip("this screen did not let the window change size")
    seen_blank = False
    end = time.monotonic() + 60
    while time.monotonic() < end:
        window.update()
        if not ev.has_picture():
            seen_blank = True
        if settled(ev) and ev.picture_size() != old:
            break
        time.sleep(0.02)
    assert not seen_blank, "the pane was blank during the resize"
    assert ev.picture_size() != old
    assert abs(ev.picture_size()[0] - ev.pic.winfo_width()) <= 24


def test_an_unreadable_preview_says_so_and_keeps_the_rows_words(window, run_with_previews):
    t = window.titration_tab
    iid = other_rows(t, 1)[0]
    name = str(t.tree.item(iid, "values")[0])
    from phenocycler_qc_eval.results import safe_name

    (run_with_previews / "previews" / f"antibody_{safe_name(name)}.npz").write_bytes(b"not a zip")
    window.load_past_run(run_with_previews)              # the file changed: a new picture is wanted
    pump_until(window, lambda: settled(t.evidence), 60)
    choose(t.tree, row_of(t.tree, name))
    pump_until(window, lambda: settled(t.evidence), 60)
    assert t.evidence.drawn_owner().startswith("message:")
    assert t.evidence.header_title().startswith(f"{name}:") and t.evidence.header_subtitle()


# ---------------------------------------------------------------------------- the Crosstalk tab and the overlap rows
def test_the_crosstalk_tab_and_its_pairs_answer_at_once_and_show_their_own_evidence(window, monkeypatch):
    slow_pictures(monkeypatch, 0.4)
    ct = window.crosstalk_tab
    ev = ct.evidence
    window.notebook.select(ct)
    pump_until(window, lambda: settled(ev), 60)
    iid = next(i for i in ct.tree.get_children() if tuple(ct.tree.item(i, "values")[:2]) == ("TOX", "CD163"))
    ct.tree.selection_clear()
    ct._shown = None
    took = choose(ct.tree, iid)
    assert took < 0.25
    assert ev.is_loading() and ev.header_title() == "Loading evidence for TOX -> CD163..."
    assert ev.header_subtitle() == "" and ev.drawn_owner() is None
    pump_until(window, lambda: settled(ev), 60)
    assert ev.drawn_owner() == "TOX->CD163" and ev.header_title() == "TOX -> CD163"
    assert "What the numbers say" in ev.header_subtitle()


def test_an_overlap_row_answers_at_once_and_shows_its_two_markers(window, monkeypatch):
    slow_pictures(monkeypatch, 0.4)
    ct = window.crosstalk_tab
    ev = ct.evidence
    window.notebook.select(ct)
    pump_until(window, lambda: settled(ev), 60)
    iid = next(i for i in ct.exclusive_tree.get_children()
               if not ct._ex_blank.get(str(i)) and ct.exclusive_tree.item(i, "values")[2] != "")
    a, b = (str(v) for v in ct.exclusive_tree.item(iid, "values")[:2])
    choose(ct.exclusive_tree, iid)
    assert ev.is_loading() and ev.header_title() == f"Loading evidence for {a} and {b}..."
    pump_until(window, lambda: settled(ev), 60)
    assert ev.drawn_owner() == f"{a}+{b}" and ev.header_title().startswith(f"{a} and {b}:")


def test_the_figures_tab_shows_a_figure_after_a_moment_and_says_so_meanwhile(window, monkeypatch):
    slow_pictures(monkeypatch, 0.3)
    ft = window.figures_tab
    window.notebook.select(ft)
    window.update()
    if not ft.tree.get_children():
        pytest.skip("the fixture run has no overview figures")
    iid = ft.tree.get_children()[0]
    choose(ft.tree, iid)
    assert ft.evidence.is_loading()
    pump_until(window, lambda: settled(ft.evidence), 60)
    assert ft.evidence.drawn_owner().startswith("image:")


# ---------------------------------------------------------------------------- the size of a picture is worked out beforehand
def test_the_height_of_the_words_is_known_before_they_are_put_up(window):
    """The picture is made for the room that is left under the words, so the words' height has to be right."""
    t = window.titration_tab
    ev = t.evidence
    width = ev.room()[0]
    for iid in list(t.tree.get_children())[:8]:
        choose(t.tree, iid)
        pump_until(window, lambda: settled(ev), 60)
        window.update_idletasks()
        predicted = ev.header_height(*t._row_plan(iid)[0], width=width)
        actual = sum(w.winfo_reqheight() for w in (ev._head_title, ev._head_sub) if w.winfo_manager())
        actual += (2 if ev._head_sub.winfo_manager() else 0)
        if ev._facts_frame.winfo_manager():
            actual += 6 + 2 + ev._facts_frame.winfo_reqheight()
        assert abs(predicted - actual) <= 3, f"{t.tree.item(iid, 'values')[0]}: predicted {predicted}, real {actual}"


# ---------------------------------------------------------------------------- item C: the numbers in the pane are the CSV's
def test_the_fine_detail_match_shown_for_each_pair_is_the_one_in_the_csv(app, run_with_previews):
    """Review note: "fine-detail match" read 0.48 for three pairs. It must be the CSV's own number for the pair selected
    (a value left over from the pair before, or a default, would show here)."""
    import pandas as pd

    csv = run_with_previews / "stain_qc_crosstalk_pairs.csv"
    df = pd.read_csv(csv)
    values = [0.481, 0.337, 0.512, 0.307, 0.455, 0.399, 0.302]
    for i, v in enumerate(values):
        df.loc[i, ["flag", "highpass_corr", "highpass_cut", "spill"]] = [True, v, 0.3, 0.9 - 0.05 * i]
        df.loc[i, "asymmetry"] = 0.1 + 0.01 * i
    df.to_csv(csv, index=False)
    app.load_past_run(run_with_previews)
    ct = app.crosstalk_tab
    app.notebook.select(ct)
    app.update()
    expected = pd.read_csv(csv)
    checked = 0
    for iid in ct.tree.get_children():
        source, target = (str(v) for v in ct.tree.item(iid, "values")[:2])
        choose(ct.tree, iid)
        app.update()
        row = expected[(expected["source"] == source) & (expected["target"] == target)].iloc[0]
        shown = ct.evidence.facts()["Fine-detail match"]
        want = f"{row['highpass_corr']:.2f} (cut-off {row['highpass_cut']:.2f}, {'over' if row['highpass_corr'] > row['highpass_cut'] else 'under'})"
        assert shown == want, f"{source} -> {target}: window {shown!r}, csv {want!r}"
        checked += 1
    assert checked >= 5


# ---------------------------------------------------------------------------- F3: a ratio just above 1 is not tinted like a flag
def test_an_overlap_ratio_just_above_one_is_tinted_paler_than_a_clear_one():
    from phenocycler_qc_eval.gui.tabs import overlap_tint

    full = overlap_tint(1.6)
    assert full == "#fdf1e3"                                   # the colour of a flagged pair
    near = overlap_tint(1.06)
    assert near != full and near > full                         # lighter (closer to white)
    assert overlap_tint(1.3) != near and overlap_tint(1.3) != full
    assert overlap_tint(None) != full                           # no ratio: no strong tint


# ---------------------------------------------------------------------------- F4 / Z6: one wording, two passes named
def test_the_scoring_step_names_its_two_passes_and_the_early_look_has_one_name():
    from phenocycler_qc_eval.gui.run_state import RunState

    s = RunState()
    s.start("run")
    s.update("early_scoring", 0.3, "Early look: antibody 3 of 31 (first 50 windows)")
    assert "(early look: antibody 3 of 31)" in " ".join(s.lines())
    s.update("detection", 0.5, "Scoring antibodies, first pass: antibody 10 of 31")
    now = next(x for x in s.lines() if x.startswith("\u25CF"))
    assert now == "\u25CF Scoring antibodies (first pass: antibody 10 of 31)"
    s.update("detection", 0.55, "Scoring antibodies, second pass: preview 10 of 31")
    now = next(x for x in s.lines() if x.startswith("\u25CF"))
    assert now == "\u25CF Scoring antibodies (second pass: preview 10 of 31)"
    assert s.count() == (10, 31)                                 # the word "first" never reads as a count


def test_the_engine_says_the_same_words_as_the_step_list(sampling_qptiff, tmp_path, monkeypatch):
    from test_pass2_engine import _run

    events = _run(sampling_qptiff, tmp_path, monkeypatch)
    said = [m for s, _f, m in events if s in ("early_scoring", "detection")]
    assert any(m.startswith("Early look: ") for m in said)
    assert any(m.startswith("Scoring antibodies, first pass: antibody") for m in said)
    assert any(m.startswith("Scoring antibodies, second pass: preview") for m in said)
    assert not any(m.startswith(("Scoring antibody ", "Preparing preview")) for m in said)


# ---------------------------------------------------------------------------- Y7 / Z7: the bar and the status line are shown once
def test_while_the_working_page_shows_the_bar_and_the_status_the_rail_does_not(app, fake_handle):
    app.handle = fake_handle
    app.begin_wait("run")
    app._busy(True)
    fake_handle.queue([{"type": "progress", "stage": "sampling", "fraction": 0.3, "message": "Reading window 21 of 61"}])
    app._poll()
    app.update()
    assert app.working_state_visible()
    assert not app.progress.winfo_ismapped() and not app.status_label.winfo_ismapped()
    assert app.working_progress.winfo_ismapped()                  # the page has them
    app._hide_working()
    app.update()
    assert app.progress.winfo_manager() == "grid" and app.status_label.winfo_manager() == "grid"
    fake_handle.running = False


def test_the_results_path_in_the_rail_is_short_enough_not_to_break_a_word(app, tmp_path):
    folder = tmp_path / "runs" / "p3_full" / "qc_results"
    app._output_user_set = True
    app._set_output(folder)
    text = app.saved_to_var.get()
    longest = max(len(w) for w in text.split())
    assert longest <= 18, text


# ---------------------------------------------------------------------------- Z3: the numbers never run past the pane
def test_the_facts_row_leaves_room_for_the_scroll_bar(app):
    ev = app.titration_tab.evidence
    facts = [("Both positive", "6.8%"), ("Chance alone would give", "6.5%"), ("Ratio", "1.06 (cut-off 1.0, over)")]
    small, big = ev._fonts()
    cells = [max(small.measure(n), big.measure(v)) + 22 for n, v in facts]
    for width in (420, 470, 520, 600, 800):
        per_row = ev.facts_columns(facts, width)
        widest = max(sum(cells[i:i + per_row]) for i in range(0, len(cells), per_row))
        assert per_row == 1 or widest <= width - 40, (width, per_row, widest)


# ---------------------------------------------------------------------------- Z2 / F2: a 1366 x 768 laptop at 125% and 150%
@pytest.mark.parametrize("scale", [1.25, 1.5])
def test_on_a_small_screen_the_numbers_and_tiles_show_without_scrolling(background_pictures, monkeypatch, tmp_path,
                                                                        run_with_previews, scale):
    from phenocycler_qc_eval.gui.app import App

    monkeypatch.setenv("PQE_SETTINGS_DIR", str(tmp_path / "_s"))
    app = App(simulate_scale=scale)
    try:
        app.geometry(f"1366x{int(690 * scale / 1.25) + 86}+0+0")
        app.update()
        app.load_past_run(run_with_previews)
        t, ct = app.titration_tab, app.crosstalk_tab
        pump_until(app, app.compact, 30)
        pump_until(app, lambda: settled(t.evidence), 90)
        assert app.compact()
        assert t.tree.xview() == (0.0, 1.0), "the table scrolls sideways"
        assert not t.page.scrollable(), "the page scrolls"
        assert not t.evidence.scrollable(), "the evidence pane scrolls: numbers or tiles are below the fold"
        app.notebook.select(ct)
        pump_until(app, lambda: settled(ct.evidence), 90)
        assert ct.tree.xview() == (0.0, 1.0) and ct.exclusive_tree.xview() == (0.0, 1.0)
        ev = ct.evidence
        viewport = ev.scroll._canvas.winfo_height()
        assert ev.pic.winfo_y() + ev.pic.winfo_height() <= viewport + 2, "the tiles are below the fold"
        assert ev._facts_frame.winfo_y() + ev._facts_frame.winfo_height() <= viewport, "the numbers are below the fold"
    finally:
        app.destroy()


# ---------------------------------------------------------------------------- F5: Settings with Advanced open keeps its Save row
def test_settings_with_advanced_open_never_grows_past_the_screen_and_keeps_save_in_view(app, tmp_path):
    from phenocycler_qc_eval.gui.settings_dialog import SettingsDialog
    from phenocycler_qc_eval.settings import default_settings

    for screen in ((1920, 1080), (1366, 768)):
        d = SettingsDialog(app, default_settings(), tmp_path / "s.json", screen_size=screen)
        try:
            d.toggle_advanced()
            for _ in range(5):
                d.update()
                time.sleep(0.05)
            assert d.winfo_height() <= 0.85 * screen[1] + 4, (screen, d.winfo_height())
            bottom = d.save_button.winfo_rooty() + d.save_button.winfo_height()
            assert bottom <= d.winfo_rooty() + d.winfo_height(), "the Save button is cut off"
            assert d.save_button.winfo_ismapped()
        finally:
            d.destroy()


def test_a_negative_one_way_excess_reads_none_not_a_bare_negative_number():
    import pandas as pd

    from phenocycler_qc_eval.advice import pair_numbers

    row = pd.Series({"highpass_corr": 0.4, "highpass_cut": 0.3, "asymmetry": -0.09, "asym_cut": 0.15})
    assert dict(pair_numbers(row))["One-way excess"] == "none (cut-off 0.15, under)"
    row["asymmetry"] = 0.02
    assert dict(pair_numbers(row))["One-way excess"].startswith("0.02 (cut-off 0.15")


# ---------------------------------------------------------------------------- the compact switch cannot feed itself
def test_resizing_back_and_forth_across_the_compact_threshold_switches_a_bounded_number_of_times(app, run_with_previews,
                                                                                                monkeypatch):
    from phenocycler_qc_eval.gui import results_pane, tabs

    calls = {"apply": 0, "set": 0, "configure_titration": 0}
    real_apply, real_set = results_pane.ResultsPaneMixin._apply_compact, tabs.TitrationTab.set_compact

    def counted_apply(self):
        calls["apply"] += 1
        return real_apply(self)

    def counted_set(self, on):
        calls["set"] += 1
        return real_set(self, on)

    monkeypatch.setattr(results_pane.ResultsPaneMixin, "_apply_compact", counted_apply)
    monkeypatch.setattr(tabs.TitrationTab, "set_compact", counted_set)
    app.load_past_run(run_with_previews)
    app.update()
    below = results_pane.ResultsPaneMixin.COMPACT_BELOW
    gap = results_pane.ResultsPaneMixin.COMPACT_GAP
    heights = [below - 20, below + 10, below - 15, below + gap + 30, below - 10, below + 5, below + gap + 50]   # logical px
    for h in heights * 2:
        app.geometry(f"1100x{int(h)}")
        end = time.monotonic() + 0.9
        while time.monotonic() < end:
            app.update()
            time.sleep(0.02)
    for _ in range(40):                                  # then it must go quiet: no handler keeps calling itself
        app.update()
        time.sleep(0.02)
    before = dict(calls)
    for _ in range(60):
        app.update()
        time.sleep(0.02)
    assert calls == before, "the switch is still running with nothing changing"
    assert calls["set"] <= 2 * len(heights) * 2 + 2, calls          # at most one switch per resize
    assert calls["set"] <= 12, calls                                # the gap keeps a size near the limit from flipping it


def test_the_evidence_pane_goes_quiet_at_125_percent_on_a_small_screen(background_pictures, monkeypatch, tmp_path,
                                                                       run_with_previews):
    """The cause of the pass 4 hang: a picture swapped in made the page need its scroll bar, the bar narrowed the pane, the
    narrower pane asked for another picture that made the bar go, and so on for ever (about one swap a second, so a
    waiting event loop never came back). Once the results have settled nothing may keep being swapped."""
    from phenocycler_qc_eval.gui.app import App
    from phenocycler_qc_eval.gui.evidence import EvidenceCanvas

    monkeypatch.setenv("PQE_SETTINGS_DIR", str(tmp_path / "_s"))
    swaps = []
    real = EvidenceCanvas._apply

    def counted(self, *a, **k):
        swaps.append(time.monotonic())
        return real(self, *a, **k)

    monkeypatch.setattr(EvidenceCanvas, "_apply", counted)
    app = App(simulate_scale=1.25)
    try:
        app.geometry("1366x776+0+0")
        app.update()
        app.load_past_run(run_with_previews)
        pump_until(app, app.compact, 30)
        pump_until(app, lambda: settled(app.titration_tab.evidence), 90)
        app.notebook.select(app.crosstalk_tab)
        pump_until(app, lambda: settled(app.crosstalk_tab.evidence), 90)
        end = time.monotonic() + 6
        while time.monotonic() < end:                    # let any layout settle
            app.update()
            time.sleep(0.02)
        mark = time.monotonic()
        end = mark + 8
        while time.monotonic() < end:
            app.update()
            time.sleep(0.02)
        later = [t for t in swaps if t >= mark]
        assert len(later) <= 1, f"{len(later)} pictures were swapped in during 8 quiet seconds"
    finally:
        app.destroy()
