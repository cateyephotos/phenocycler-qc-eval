"""Settings panel and settings state (Task 16)."""

from __future__ import annotations

import json

import pytest

from phenocycler_qc_eval.settings import (
    changed_values, config_path, default_settings, load_settings, save_settings,
)

tk = pytest.importorskip("tkinter")
from phenocycler_qc_eval.gui.settings_dialog import SettingsDialog  # noqa: E402

pytestmark = pytest.mark.gui


def _modified(**kw):
    s = default_settings()
    for k, v in kw.items():
        group, name = k.split("__")
        setattr(getattr(s, group), name, v)
    return s


# ---- the brief's tests ----------------------------------------------------------------
def test_save_writes_config_and_status(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PQE_CONFIG_DIR", str(tmp_path))
    assert app.settings_status_var.get() == "Settings: defaults"
    d = SettingsDialog(app, default_settings(), tmp_path / "settings.json")
    d.fields["detection.dim_fraction"].set("0.2")
    d.save()
    app.refresh_settings_status()
    assert load_settings(tmp_path / "settings.json")[0].detection.dim_fraction == 0.2
    assert app.settings_status_var.get() == "Settings: modified"
    assert app.settings_changes() == ["Dim signal below: 0.1 to 0.2"]


def test_changed_values():
    s = default_settings()
    s.crosstalk.margin = 0.1
    assert changed_values(s) == [("crosstalk.margin", 0.05, 0.1)] and changed_values(default_settings()) == []


def test_dialog_header_lists_changes(root, tmp_path):
    s = _modified(crosstalk__margin=0.1)
    assert "Margin: 0.05 to 0.1" in SettingsDialog(root, s, tmp_path / "s.json").header_text()
    assert SettingsDialog(root, default_settings(), tmp_path / "s.json").header_text() == "All values are the defaults."


def test_invalid_value_blocks_save(root, tmp_path):
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    d.fields["detection.dim_fraction"].set("abc")
    d.save()
    assert not (tmp_path / "s.json").exists() and d.error_text("detection.dim_fraction")
    assert d.result is None


def test_reset_to_defaults(root, tmp_path):
    d = SettingsDialog(root, _modified(crosstalk__margin=0.2), tmp_path / "s.json")
    d.reset()
    d.save()
    assert load_settings(tmp_path / "s.json")[0] == default_settings()


def test_advanced_collapsed_by_default(root, tmp_path):
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    assert not d.advanced_visible() and "advanced.n_windows" in d.fields and "advanced.highpass_sigma" in d.fields


def test_edit_control_marker_map(root, tmp_path):
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    d.control_table.set_rows([("CD163", "CD206")])
    d.save()
    # Stored as a list of [marker, control] pairs (see settings.MarkerSettings), not a dict.
    assert load_settings(tmp_path / "s.json")[0].markers.control_markers == [["CD163", "CD206"]]


# ---- validation in plain words -------------------------------------------------------------
@pytest.mark.parametrize("key,text,needle", [
    ("detection.dim_fraction", "abc", "number"),
    ("detection.dim_fraction", "1.5", "at most 1"),
    ("detection.dim_fraction", "0", "above 0"),
    ("detection.stain_index_low", "-2", "positive"),
    ("crosstalk.null_percentile", "101", "at most 100"),
    ("advanced.n_windows", "2.5", "whole number"),
    ("advanced.n_windows", "0", "at least 1"),
    ("advanced.seed", "-1", "at least 0"),
    ("advanced.highpass_sigma", "nan", "number"),
    ("advanced.min_windows", "", "number"),
])
def test_each_bad_entry_names_the_problem_and_the_allowed_range(root, tmp_path, key, text, needle):
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    d.fields[key].set(text)
    d.save()
    assert needle in d.error_text(key) and not (tmp_path / "s.json").exists()
    d.update()
    assert d.error_label(key).winfo_ismapped()          # shown beside the field


def test_fixing_the_entry_clears_the_error_and_saves(root, tmp_path):
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    d.fields["detection.dim_fraction"].set("abc")
    assert d.error_text("detection.dim_fraction")
    d.fields["detection.dim_fraction"].set("0.15")
    assert d.error_text("detection.dim_fraction") == ""
    d.save()
    assert load_settings(tmp_path / "s.json")[0].detection.dim_fraction == 0.15


def test_whole_numbers_are_accepted_for_counts_and_floats_for_thresholds(root, tmp_path):
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    d.fields["advanced.n_windows"].set("300")
    d.fields["detection.stain_index_low"].set("4")
    d.save()
    s = load_settings(tmp_path / "s.json")[0]
    assert s.advanced.n_windows == 300 and isinstance(s.advanced.n_windows, int)
    assert s.detection.stain_index_low == 4.0 and isinstance(s.detection.stain_index_low, float)


@pytest.mark.parametrize("rows", [[("", "CD68")], [("CD163", "   ")], [("  ", "  ")], [("CD163", "CD163")]])
def test_marker_names_that_are_empty_or_blank_are_rejected(root, tmp_path, rows):
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    d.control_table.set_rows(rows)
    d.save()
    assert d.control_table.error_text() and not (tmp_path / "s.json").exists()
    d.update()
    assert d.control_table.error_label.winfo_ismapped()


def test_exclusive_table_rejects_blank_names_too(root, tmp_path):
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    d.exclusive_table.set_rows([("CD3e", "")])
    d.save()
    assert d.exclusive_table.error_text() and not (tmp_path / "s.json").exists()


def test_marker_rows_can_be_added_and_removed(root, tmp_path):
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    n = len(d.control_table.rows())
    assert n == 18
    d.control_table.add_row()
    assert len(d.control_table.rows()) == n + 1
    d.control_table.remove_row(0)
    assert len(d.control_table.rows()) == n
    d.control_table.set_rows([("CD163", "CD206"), ("", "")])       # an empty trailing row is rejected, not dropped
    d.save()
    assert d.control_table.error_text()


def test_names_are_trimmed_when_saved(root, tmp_path):
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    d.exclusive_table.set_rows([("  CD3e ", "CD20  ")])
    d.save()
    assert load_settings(tmp_path / "s.json")[0].markers.exclusive_pairs == [["CD3e", "CD20"]]


# ---- what Save keeps, what Cancel does -------------------------------------------------------
def test_window_size_is_a_visible_advanced_field_with_an_explanation(root, tmp_path):
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    assert "advanced.window_um" in d.fields and d.explanation("advanced.window_um")
    assert "Window size" in d.visible_text()
    d.toggle_advanced()
    d.update()
    assert d.field_entry("advanced.window_um").winfo_ismapped()


@pytest.mark.parametrize("text,needle", [("abc", "not a number"), ("0", "1"), ("-5", "1"), ("99999", "2000")])
def test_window_size_rejects_bad_values_and_states_the_allowed_range(root, tmp_path, text, needle):
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    d.fields["advanced.window_um"].set(text)
    err = d.error_text("advanced.window_um")
    assert needle in err and "Allowed" in err and "1" in err and "2000" in err
    d.save()
    assert not (tmp_path / "s.json").exists() and d.advanced_visible()


def test_window_size_can_be_edited_saved_and_counts_as_modified(root, tmp_path):
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    d.fields["advanced.window_um"].set("80")
    assert "Window size (micrometers)" in d.header_text() and "111.328 to 80.0" in d.header_text()
    d.save()
    saved = load_settings(tmp_path / "s.json")[0]
    assert saved.advanced.window_um == 80.0 and d.result.advanced.window_um == 80.0
    assert ("advanced.window_um", 111.328, 80.0) in changed_values(saved)


def test_reset_restores_the_window_size_from_a_modified_file(root, tmp_path):
    s = default_settings()
    s.advanced.window_um = 80.0                                     # e.g. hand-edited in the JSON file
    d = SettingsDialog(root, s, tmp_path / "s.json")
    assert d.fields["advanced.window_um"].get() == "80.0"
    d.reset()
    assert d.fields["advanced.window_um"].get() == "111.328"
    assert d.header_text() == "All values are the defaults."
    d.save()
    assert load_settings(tmp_path / "s.json")[0].advanced.window_um == 111.328


def test_window_size_is_in_the_settings_record_the_report_uses():
    from phenocycler_qc_eval.report import _SETTING_WORDS as SETTING_LABELS
    from phenocycler_qc_eval.settings import settings_to_dict
    s = default_settings()
    s.advanced.window_um = 80.0
    assert settings_to_dict(s)["advanced"]["window_um"] == 80.0
    assert "window_um" in SETTING_LABELS


def test_reset_alone_writes_nothing(root, tmp_path):
    d = SettingsDialog(root, _modified(crosstalk__margin=0.2), tmp_path / "s.json")
    d.reset()
    assert not (tmp_path / "s.json").exists() and d.result is None


def test_save_sets_result_and_closes(root, tmp_path):
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    d.fields["crosstalk.margin"].set("0.07")
    d.save()
    assert d.result.crosstalk.margin == 0.07
    assert not d.winfo_exists()


def test_cancel_writes_nothing(root, tmp_path):
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    d.fields["crosstalk.margin"].set("0.07")
    d.cancel()
    assert d.result is None and not (tmp_path / "s.json").exists() and not d.winfo_exists()


def test_unwritable_location_is_reported_in_the_dialog_not_raised(root, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    d = SettingsDialog(root, default_settings(), blocker / "s.json")   # parent is a file: cannot be created
    d.save()
    assert d.result is None and "could not" in d.footer_text().lower() and d.winfo_exists()


def test_on_saved_callback_runs_after_a_save(root, tmp_path):
    seen = []
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json", on_saved=lambda: seen.append(1))
    d.save()
    assert seen == [1]


def test_header_follows_edits_and_reset(root, tmp_path):
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    assert d.header_text() == "All values are the defaults."
    d.fields["detection.dim_fraction"].set("0.2")
    assert d.header_text() == "Dim signal below: 0.1 to 0.2"
    d.reset()
    assert d.header_text() == "All values are the defaults."


# ---- wording rules --------------------------------------------------------------------------
def test_panel_says_rules_are_lab_defined_and_apply_next_run(root, tmp_path):
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    text = d.visible_text().lower()
    assert "lab-defined" in text and "not published" in text
    assert "next run" in text
    for banned in ("verdict", "grade", "drilldown", "drill-down"):
        assert banned not in text
    assert "dilution" not in text and "millisecond" not in text


def test_every_threshold_has_a_one_line_explanation(root, tmp_path):
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    for key in d.fields:
        assert d.explanation(key), key
    assert len([k for k in d.fields if k.startswith("detection.")]) == 6


def test_active_run_note_is_shown_only_while_a_run_is_active(root, tmp_path):
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json", run_active=True)
    assert "run is in progress" in d.run_note_text().lower() and "not this one" in d.run_note_text().lower()
    d.set_run_active(False)
    assert d.run_note_text() == ""


def test_advanced_group_expands_and_collapses(root, tmp_path):
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    d.toggle_advanced()
    d.update()
    assert d.advanced_visible() and d.field_entry("advanced.seed").winfo_ismapped()
    d.toggle_advanced()
    assert not d.advanced_visible()


def test_an_error_inside_advanced_opens_the_group(root, tmp_path):
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    d.fields["advanced.seed"].set("x")
    d.save()
    assert d.advanced_visible() and d.error_text("advanced.seed")


# ---- layout: nothing clipped at the default size ---------------------------------------------
def test_buttons_and_errors_fit_inside_the_dialog(root, tmp_path):
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    d.fields["detection.dim_fraction"].set("abc")
    d.toggle_advanced()
    d.update()
    bottom = d.winfo_rooty() + d.winfo_height()
    right = d.winfo_rootx() + d.winfo_width()
    for w in (d.save_button, d.reset_button, d.cancel_button, d.error_label("detection.dim_fraction")):
        assert w.winfo_rooty() + w.winfo_height() <= bottom and w.winfo_rootx() + w.winfo_width() <= right


# ---- the main window ---------------------------------------------------------------------------
def test_status_line_starts_from_the_saved_settings(monkeypatch, tmp_path):
    from conftest import new_app

    monkeypatch.setenv("PQE_SETTINGS_DIR", str(tmp_path / "_s"))
    monkeypatch.setenv("PQE_CONFIG_DIR", str(tmp_path / "cfg"))
    save_settings(_modified(detection__dim_fraction=0.25), config_path())
    a = new_app()
    try:
        assert a.settings_status_var.get() == "Settings: modified"
        assert a.settings_changes() == ["Dim signal below: 0.1 to 0.25"]
        assert a.settings_tooltip_text() == "Dim signal below: 0.1 to 0.25"
    finally:
        a.destroy()


def test_corrupt_settings_file_warns_in_details_and_does_not_crash(monkeypatch, tmp_path):
    from conftest import new_app

    monkeypatch.setenv("PQE_SETTINGS_DIR", str(tmp_path / "_s"))
    monkeypatch.setenv("PQE_CONFIG_DIR", str(tmp_path / "cfg"))
    config_path().parent.mkdir(parents=True)
    config_path().write_text("{ not json", encoding="utf-8")
    a = new_app()
    try:
        assert a.settings_status_var.get() == "Settings: defaults"
        assert "not valid" in a.log.get("1.0", "end")
    finally:
        a.destroy()


def test_settings_button_opens_one_panel_and_shows_it_again(app):
    app.settings_button.invoke()
    d = app.settings_dialog
    assert d is not None and d.winfo_exists()
    app.settings_button.invoke()
    assert app.settings_dialog is d
    d.cancel()
    app.update()
    assert app.settings_dialog is None


def test_save_in_the_panel_refreshes_the_status_line(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PQE_CONFIG_DIR", str(tmp_path))
    app.on_settings()
    app.settings_dialog.fields["crosstalk.margin"].set("0.1")
    app.settings_dialog.save()
    assert app.settings_status_var.get() == "Settings: modified"
    assert app.settings_changes() == ["Margin: 0.05 to 0.1"]
    app.on_settings()
    app.settings_dialog.reset()
    app.settings_dialog.save()
    assert app.settings_status_var.get() == "Settings: defaults" and app.settings_changes() == []


def test_panel_opens_with_the_note_when_a_run_is_active(app, fake_handle):
    app.handle = fake_handle
    app.on_settings()
    assert "run is in progress" in app.settings_dialog.run_note_text().lower()
    app.handle = None                                              # (the poll loop is not running in this test)
    app.settings_dialog.cancel()


def test_next_run_uses_the_saved_settings(app, tmp_path, monkeypatch, fake_handle):
    monkeypatch.setenv("PQE_CONFIG_DIR", str(tmp_path / "cfg"))
    save_settings(_modified(detection__dim_fraction=0.2), config_path())
    img = tmp_path / "x.qptiff"
    img.write_bytes(b"x")
    app.image_var.set(str(img))
    seen = []
    monkeypatch.setattr(app, "make_handle", lambda request: seen.append(request) or fake_handle)
    app.start_run()
    assert seen[0]["settings"]["detection"]["dim_fraction"] == 0.2
    app.cancel_run()


def test_settings_wired_and_shown_by_default():
    from phenocycler_qc_eval.gui.app import App
    assert App.WIRED["settings"] is True


def test_settings_row_is_visible_in_the_rail(app):
    app.update()
    assert app.settings_button.winfo_ismapped() and app.settings_label.winfo_ismapped()
    rail_bottom = app.left.winfo_rooty() + app.left.winfo_height()
    assert app.settings_button.winfo_rooty() + app.settings_button.winfo_height() <= rail_bottom


def test_json_on_disk_is_what_the_cli_reads(root, tmp_path):
    from phenocycler_qc_eval.settings import settings_from_dict
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    d.fields["detection.dim_fraction"].set("0.2")
    d.save()
    s, warnings = settings_from_dict(json.loads((tmp_path / "s.json").read_text(encoding="utf-8")))
    assert warnings == [] and s.detection.dim_fraction == 0.2


# ---- review round 1 ---------------------------------------------------------------------------
def _in_view(table, widget) -> bool:
    top = table._canvas.winfo_rooty()
    bottom = top + table._canvas.winfo_height()
    return top <= widget.winfo_rooty() and widget.winfo_rooty() + widget.winfo_height() <= bottom


def _all_widgets(w):
    for c in w.winfo_children():
        yield c
        yield from _all_widgets(c)


def test_add_row_scrolls_the_new_row_into_view_and_focuses_it(root, tmp_path):
    root.deiconify()
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    d.update()
    d.focus_force()
    d.update()
    t = d.control_table
    t.add_row()
    d.update()
    first_cell = t.first_entry(len(t.rows()) - 1)
    assert _in_view(t, first_cell)
    assert d.focus_get() is first_cell


def test_add_row_does_not_show_the_empty_name_error_until_save(root, tmp_path):
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    t = d.control_table
    t.add_row()
    d.update()
    assert t.error_text() == "" and not t.error_label.winfo_ismapped()
    d.save()                                                       # Save still refuses an empty name
    assert "empty marker name" in t.error_text() and not (tmp_path / "s.json").exists()


def test_a_half_filled_row_is_flagged_once_the_user_leaves_it(root, tmp_path):
    root.deiconify()
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    d.update()
    d.focus_force()
    d.update()
    t = d.control_table
    t.add_row()
    d.update()
    row = len(t.rows()) - 1
    t.first_entry(row).insert(0, "CD163")
    assert t.error_text() == ""                                    # still typing in the row
    d.save_button.focus_force()                                    # leaves the row
    d.update()
    assert "empty marker name" in t.error_text()


def test_the_row_keeps_no_error_when_the_user_only_moves_to_its_second_cell(root, tmp_path):
    root.deiconify()
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    d.update()
    d.focus_force()
    d.update()
    t = d.control_table
    t.add_row()
    d.update()
    row = len(t.rows()) - 1
    t.first_entry(row).insert(0, "CD163")
    t.second_entry(row).focus_force()
    d.update()
    assert t.error_text() == ""


def test_changes_list_says_what_differs_when_the_count_is_the_same():
    from phenocycler_qc_eval.settings import format_changes
    s = default_settings()
    s.markers.control_markers = [list(p) for p in s.markers.control_markers]
    s.markers.control_markers[0] = ["CD163", "CD206"]
    assert format_changes(s) == ["markers.control_markers: 18 pairs, 1 changed"]


def test_header_names_the_field_and_never_its_internal_key(root, tmp_path):
    d = SettingsDialog(root, _modified(detection__dim_fraction=0.2), tmp_path / "s.json")
    assert d.header_text() == "Dim signal below: 0.1 to 0.2"
    d2 = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    d2.control_table.set_rows([["CD163", "CD206"]])
    assert "Control markers: 18 pairs -> 1 pair" in d2.header_text()


def test_duplicate_control_marker_rows_are_rejected(root, tmp_path):
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    d.control_table.set_rows([("CD163", "CD206"), ("cd-163", "CD68")])
    d.save()
    assert "cd-163" in d.control_table.error_text() and "twice" in d.control_table.error_text()
    assert not (tmp_path / "s.json").exists()


def test_self_pair_uses_the_same_name_matching_as_the_analysis(root, tmp_path):
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    d.control_table.set_rows([("CD3e", "CD-3e")])
    d.save()
    assert "twice" in d.control_table.error_text()


def test_header_says_entries_need_fixing_while_one_is_invalid(root, tmp_path):
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    d.fields["detection.dim_fraction"].set("1.5")
    assert d.header_text() == "Some entries need fixing."
    d.fields["detection.dim_fraction"].set("0.1")
    assert d.header_text() == "All values are the defaults."


def test_starting_a_run_refreshes_the_status_line_from_the_file(app, tmp_path, monkeypatch, fake_handle):
    monkeypatch.setenv("PQE_CONFIG_DIR", str(tmp_path / "cfg"))
    assert app.settings_status_var.get() == "Settings: defaults"
    save_settings(_modified(detection__dim_fraction=0.2), config_path())    # changed behind the window's back
    img = tmp_path / "x.qptiff"
    img.write_bytes(b"x")
    app.image_var.set(str(img))
    monkeypatch.setattr(app, "make_handle", lambda request: fake_handle)
    app.start_run()
    assert app.settings_status_var.get() == "Settings: modified"
    app.cancel_run()


def test_explanations_share_their_key_words_with_the_rule_text():
    from phenocycler_qc_eval import crosstalk, detection
    from phenocycler_qc_eval.gui.settings_dialog import EXPLANATIONS
    checks = [("detection.dim_fraction", "99.5th percentile", detection.RULE_TEXT["signal"]),
              ("detection.blank_fraction", "almost nothing", detection.RULE_TEXT["near_blank"]),
              ("detection.stain_index_low", "background", detection.RULE_TEXT["stain_index"]),
              ("detection.positive_fraction_low", "tissue pixels", detection.RULE_TEXT["positive_fraction"]),
              ("detection.saturation_high", "top of the range", detection.RULE_TEXT["saturation_fraction"]),
              ("crosstalk.null_percentile", "percentile", crosstalk.RULE_TEXT["cut"]),
              ("crosstalk.margin", "cut-off", crosstalk.RULE_TEXT["cut"])]
    for key, word, rule in checks:
        assert word in EXPLANATIONS[key].lower() and word in rule.lower(), key


def test_awkward_sentences_are_reworded():
    from phenocycler_qc_eval.gui.settings_dialog import EXPLANATIONS
    assert EXPLANATIONS["crosstalk.null_percentile"] == \
        "The cut-off is set from channel pairs that cannot show crosstalk, at this percentile."
    assert EXPLANATIONS["crosstalk.margin"] == \
        "Added to that cut-off, for both the one-way spill check and the texture check."


def test_there_is_a_markers_heading_above_the_tables(root, tmp_path):
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    assert "Markers" in [w.cget("text") for w in _all_widgets(d) if str(w.winfo_class()) == "TLabel"]


def test_window_width_does_not_change_when_an_error_appears(root, tmp_path):
    root.deiconify()
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    d.toggle_advanced()
    d.update()
    before = d.winfo_width()
    d.fields["detection.dim_fraction"].set("1.5")
    d.fields["advanced.seed"].set("abc")
    d.fields["advanced.window_um"].set("0")
    d.update()
    assert d.winfo_width() == before


def test_the_form_scrolls_with_the_wheel_over_any_field(root, tmp_path):
    d = SettingsDialog(root, default_settings(), tmp_path / "s.json")
    assert "<MouseWheel>" in d.field_entry("detection.dim_fraction").bind()


def test_save_failure_message_leads_with_plain_words(root, tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("x")
    d = SettingsDialog(root, default_settings(), blocked / "s.json")
    d.save()
    assert d.footer_text().startswith("The settings could not be saved.")


def test_tooltip_survives_its_widget_being_destroyed(root):
    import tkinter as tk

    from phenocycler_qc_eval.gui.tooltip import Tooltip
    lab = tk.Label(root, text="x")
    tip = Tooltip(lab, lambda: "hello", delay_ms=10)
    tip._enter()
    lab.destroy()
    root.update()
    tip.show()                                                     # must not raise
