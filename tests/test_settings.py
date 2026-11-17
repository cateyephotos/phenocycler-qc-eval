import json
import os

import pytest

from phenocycler_qc_eval.settings import (
    config_path, default_settings, is_modified, load_settings, save_settings,
    settings_dir, settings_from_dict, settings_to_dict,
)


def test_defaults_match_tonsil_calibration():
    s = default_settings()
    assert s.detection.dim_fraction == 0.10 and s.detection.blank_fraction == 0.05
    assert s.crosstalk.null_percentile == 95.0 and s.crosstalk.margin == 0.05
    assert s.advanced.n_windows == 200 and s.advanced.highpass_sigma == 2.0
    assert dict(map(tuple, s.markers.control_markers))["CD163"] == "CD68" and not is_modified(s)
    assert len(s.markers.control_markers) == 18 and len(s.markers.exclusive_pairs) == 8


def test_round_trip_and_modified(tmp_path):
    s = default_settings(); s.detection.dim_fraction = 0.2
    save_settings(s, tmp_path / "s.json")
    s2, warn = load_settings(tmp_path / "s.json")
    assert s2.detection.dim_fraction == 0.2 and warn == [] and is_modified(s2)


def test_defaults_round_trip_equal_defaults(tmp_path):
    save_settings(default_settings(), tmp_path / "s.json")
    s, warn = load_settings(tmp_path / "s.json")
    assert s == default_settings() and warn == [] and not is_modified(s)
    assert settings_from_dict(json.loads(json.dumps(settings_to_dict(s))))[0] == s


def test_missing_file_gives_defaults(tmp_path):
    s, warn = load_settings(tmp_path / "none.json")
    assert s == default_settings() and warn == []


def test_corrupt_file_falls_back_with_warning(tmp_path):
    p = tmp_path / "s.json"; p.write_text("{not json")
    s, warn = load_settings(p)
    assert s == default_settings() and len(warn) == 1


def test_bad_value_only_resets_that_value(tmp_path):
    p = tmp_path / "s.json"
    p.write_text('{"detection": {"dim_fraction": "high", "stain_index_low": 4.0}}')
    s, warn = load_settings(p)
    assert s.detection.dim_fraction == 0.10 and s.detection.stain_index_low == 4.0
    assert any("dim_fraction" in w for w in warn)


def test_fraction_out_of_range_rejected():
    s, warn = settings_from_dict({"detection": {"dim_fraction": 1.5}})
    assert s.detection.dim_fraction == 0.10 and warn


def test_ranges_follow_ruling_r6():
    bad = {"advanced": {"seed": -1, "n_windows": 0, "min_windows": 2.5},
           "crosstalk": {"null_percentile": 0, "margin": -0.1},
           "detection": {"dim_fraction": 0, "blank_fraction": True}}
    s, warn = settings_from_dict(bad)
    assert s == default_settings() and len(warn) == 7
    ok, w = settings_from_dict({"advanced": {"seed": 0}, "crosstalk": {"null_percentile": 100},
                                "detection": {"dim_fraction": 1}})
    assert w == [] and ok.crosstalk.null_percentile == 100 and ok.detection.dim_fraction == 1


def test_unknown_keys_warned_and_ignored():
    s, warn = settings_from_dict({"detection": {"nope": 1}, "extra": {}, "markers": {"x": 1}})
    assert s == default_settings() and len(warn) == 3


def test_marker_maps_validated():
    s, warn = settings_from_dict({"markers": {"control_markers": [["A", "B"]],
                                              "exclusive_pairs": [["A"]]}})
    assert s.markers.control_markers == [["A", "B"]] and len(warn) == 1
    assert s.markers.exclusive_pairs == default_settings().markers.exclusive_pairs


def test_non_dict_input_gives_defaults():
    s, warn = settings_from_dict([1, 2])
    assert s == default_settings() and len(warn) == 1


def test_config_path_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("PQE_CONFIG_DIR", str(tmp_path))
    assert config_path() == tmp_path / "settings.json"


def test_settings_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("PQE_SETTINGS_DIR", str(tmp_path))
    assert settings_dir() == tmp_path


def test_non_utf8_file_falls_back_with_warning(tmp_path):
    p = tmp_path / "s.json"; p.write_bytes(b"\xff\xfe\x00{\x80garbage")
    s, warn = load_settings(p)
    assert s == default_settings() and len(warn) == 1


def test_utf8_bom_file_loads(tmp_path):
    p = tmp_path / "s.json"
    p.write_bytes(b"\xef\xbb\xbf" + b'{"detection": {"dim_fraction": 0.2}}')
    s, warn = load_settings(p)
    assert s.detection.dim_fraction == 0.2 and warn == []


def test_marker_names_are_stripped_on_load():
    s, warn = settings_from_dict({"markers": {"control_markers": [[" CD3e ", "CD4 "]]}})
    assert s.markers.control_markers == [["CD3e", "CD4"]] and warn == []


def test_save_leaves_only_the_settings_file(tmp_path):
    save_settings(default_settings(), tmp_path / "s.json")
    assert sorted(x.name for x in tmp_path.iterdir()) == ["s.json"]


def test_failed_save_keeps_previous_file(tmp_path, monkeypatch):
    p = tmp_path / "s.json"
    save_settings(default_settings(), p)
    before = p.read_bytes()
    s = default_settings(); s.detection.dim_fraction = 0.3

    def fail_replace(src, dst):
        raise OSError("simulated failure during replace")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError):
        save_settings(s, p)
    assert p.read_bytes() == before
    assert sorted(x.name for x in tmp_path.iterdir()) == ["s.json"]


# ---- Task 16: change list and field checking used by the Settings panel ----
def test_changed_values_lists_every_difference_with_defaults_first():
    from phenocycler_qc_eval.settings import changed_values
    s = default_settings()
    s.detection.dim_fraction = 0.2
    s.advanced.seed = 5
    assert changed_values(s) == [("detection.dim_fraction", 0.1, 0.2), ("advanced.seed", 0, 5)]
    assert changed_values(default_settings()) == []


def test_format_changes_summarizes_marker_lists():
    from phenocycler_qc_eval.settings import format_changes
    s = default_settings()
    s.markers.control_markers = [["CD163", "CD206"]]
    s.markers.exclusive_pairs = s.markers.exclusive_pairs[:2]
    assert format_changes(s) == ["markers.control_markers: 18 pairs -> 1 pair",
                                 "markers.exclusive_pairs: 8 pairs -> 2 pairs"]


@pytest.mark.parametrize("group,key,text,value", [
    ("detection", "dim_fraction", " 0.2 ", 0.2), ("detection", "stain_index_low", "4", 4.0),
    ("advanced", "n_windows", "300", 300), ("advanced", "seed", "0", 0),
])
def test_field_error_accepts_good_entries_with_the_right_type(group, key, text, value):
    from phenocycler_qc_eval.settings import field_error
    got, err = field_error(group, key, text)
    assert err == "" and got == value and type(got) is type(value)


@pytest.mark.parametrize("group,key,text,needle", [
    ("detection", "dim_fraction", "abc", "not a number"), ("detection", "dim_fraction", "", "not a number"),
    ("detection", "dim_fraction", "1.5", "at most 1"), ("advanced", "n_windows", "2.5", "not a whole number"),
    ("advanced", "seed", "-1", "at least 0"), ("detection", "stain_index_low", "nan", "positive"),
    ("detection", "stain_index_low", "inf", "positive"),
])
def test_field_error_says_what_is_wrong_and_what_is_allowed(group, key, text, needle):
    from phenocycler_qc_eval.settings import field_error
    got, err = field_error(group, key, text)
    assert got is None and needle in err and "Allowed:" in err


@pytest.mark.parametrize("text", ["0", "-1", "0.5", "2001", "nan"])
def test_window_size_entry_must_be_between_1_and_2000(text):
    from phenocycler_qc_eval.settings import field_error
    value, message = field_error("advanced", "window_um", text)
    assert value is None and "2000" in message


@pytest.mark.parametrize("text,value", [("1", 1.0), ("111.328", 111.328), ("2000", 2000.0)])
def test_window_size_entry_accepts_the_allowed_range(text, value):
    from phenocycler_qc_eval.settings import field_error
    assert field_error("advanced", "window_um", text) == (value, "")


def test_a_saved_file_with_an_out_of_range_window_size_warns_and_uses_the_default():
    from phenocycler_qc_eval.settings import settings_from_dict
    s, warnings = settings_from_dict({"advanced": {"window_um": 50000}})
    assert s.advanced.window_um == 111.328 and warnings and "window_um" in warnings[0]
