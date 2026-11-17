import numpy as np
import pandas as pd
import pytest

from phenocycler_qc_eval.detection import RULE_TEXT, concentration_call, detection_table, exposure_direction
from phenocycler_qc_eval.settings import DetectionSettings
from phenocycler_qc_eval.tissue import Sample

N, H = 4, 64


def chan(rng, bg_mean, bg_sd, pos_frac=0.0, pos_level=0.0, pos_sd=0.0, sat_frac=0.0, top=255):
    shape = (N, H, H)
    a = np.clip(rng.normal(bg_mean, bg_sd, shape), 0, top)
    pos = rng.random(shape) < pos_frac
    a = np.where(pos, np.clip(rng.normal(pos_level, max(pos_sd, 1e-9), shape), 0, top), a)
    a[rng.random(shape) < sat_frac] = top
    return a


def make_sample(arrays, dtype=np.uint8, value_range=255.0, checkable=True):
    rng = np.random.default_rng(1)
    names = ["DAPI"] + list(arrays)
    dapi = np.clip(rng.normal(100, 10, (N, H, H)), 1, None)
    stack = np.stack([dapi] + [arrays[k] for k in arrays], axis=1)
    if np.issubdtype(np.dtype(dtype), np.integer):
        stack = np.rint(stack)
    return Sample(pixels=stack.astype(dtype), names=names,
                  origins=pd.DataFrame({"y0": range(N), "x0": range(N), "size_px": [H] * N}),
                  window_px=H, nuclear_name="DAPI", value_range=value_range, saturation_checkable=checkable,
                  range_basis="test")


def table(smp, s=None):
    return detection_table(smp, s or DetectionSettings()).set_index("channel", drop=False)


@pytest.fixture(scope="module")
def sat_sample():
    r = np.random.default_rng(0)
    return make_sample({
        "HLA-DR": chan(r, 5, 2, 0.10, 150, 20, sat_frac=0.02),
        "CD3e": chan(r, 5, 2, 0.10, 120, 20),
    })


@pytest.fixture(scope="module")
def sat_bg_sample():
    r = np.random.default_rng(0)
    return make_sample({"X": chan(r, 51, 5, 0.10, 200, 20, sat_frac=0.02)})


@pytest.fixture(scope="module")
def dim_sample():
    r = np.random.default_rng(0)
    return make_sample({"CD20dim": chan(r, 2, 2, 0.08, 8, 1)})


@pytest.fixture(scope="module")
def rare_sample():
    r = np.random.default_rng(0)
    return make_sample({"rare": chan(r, 30, 3, 0.004, 150, 10)})


@pytest.fixture(scope="module")
def blank_sample():
    r = np.random.default_rng(0)
    return make_sample({"blank": chan(r, 0.3, 0.5), "CD3e": chan(r, 5, 2, 0.10, 120, 20)})


def test_saturated_low_background(sat_sample):
    r = table(sat_sample).loc["HLA-DR"]
    assert (r.call, r.exposure) == ("LOWER", "SHORTEN")
    assert r.next_run == "Lower concentration (or shorten exposure)" and "saturated" in r.reason


def test_saturated_high_background(sat_bg_sample):
    r = table(sat_bg_sample).loc["X"]
    assert (r.call, r.exposure) == ("LOWER", "KEEP") and r.next_run == "Lower concentration"


def test_dim_and_weak(dim_sample):
    r = table(dim_sample).loc["CD20dim"]
    assert r.signal_fraction < 0.10 and r.stain_index < 3
    assert r.next_run == "Raise concentration (or lengthen exposure)"


def test_single_weakness(rare_sample):
    r = table(rare_sample).loc["rare"]
    assert r.call == "CHECK" and r.next_run == "Compare with expected biology"


def test_good_channel(sat_sample):
    r = table(sat_sample).loc["CD3e"]
    assert r.call == "OK" and r.next_run == "Keep"
    assert r.saturation_fraction == 0.0 and r.stain_index > 3


def test_float_image_never_lower_from_saturation():
    r = np.random.default_rng(0)
    smp = make_sample({"A": chan(r, 0.02, 0.01, 0.1, 0.6, 0.1, sat_frac=0.05, top=1.0)},
                      dtype=np.float32, value_range=1.0, checkable=False)
    df = detection_table(smp, DetectionSettings())
    assert df.saturation_fraction.isna().all() and "LOWER" not in set(df.call)


def test_near_blank_flag(blank_sample):
    t = table(blank_sample)
    assert t.loc["blank"].near_blank and not t.loc["CD3e"].near_blank


def test_thresholds_follow_settings(dim_sample):
    s = DetectionSettings(dim_fraction=0.01, stain_index_low=0.5)
    assert table(dim_sample, s).loc["CD20dim"].call == "OK"


def test_nuclear_channel_not_in_table(sat_sample):
    assert "DAPI" not in set(detection_table(sat_sample, DetectionSettings()).channel)


def test_columns_match_brief(sat_sample):
    df = detection_table(sat_sample, DetectionSettings())
    assert list(df.columns)[:5] == ["channel", "next_run", "reason", "call", "exposure"]
    assert {"signal_p99_5", "signal_fraction", "stain_index", "positive_fraction", "saturation_fraction",
            "dynamic_range_used", "otsu_threshold", "near_blank", "background_sd"} <= set(df.columns)


def test_exposure_rules():
    s = DetectionSettings()
    assert exposure_direction("RAISE", 0.01, s) == "LENGTHEN"
    assert exposure_direction("RAISE", 0.2, s) == "KEEP"
    assert exposure_direction("CHECK", 0.0, s) == "KEEP" and exposure_direction("OK", 0.0, s) == "KEEP"


def test_concentration_call_unchecked_saturation_ignored():
    row = dict(saturation_fraction=0.5, signal_fraction=0.5, stain_index=10, positive_fraction=0.1)
    assert concentration_call(row, DetectionSettings(), False)[0] == "OK"
    assert concentration_call(row, DetectionSettings(), True)[0] == "LOWER"


def test_flat_channel_does_not_crash():
    smp = make_sample({"flat": np.full((N, H, H), 7.0)})
    r = table(smp).loc["flat"]
    assert r.positive_fraction == 0 and np.isfinite(r.stain_index)


def test_rule_text_covers_calls():
    assert {"LOWER", "RAISE", "CHECK", "OK", "stain_index", "exposure"} <= set(RULE_TEXT)


def _row(**kw):
    base = dict(saturation_fraction=0.0, signal_fraction=0.5, stain_index=10.0, positive_fraction=0.1)
    base.update(kw)
    return base


def test_missing_metrics_never_read_keep():
    s = DetectionSettings()
    for key in ("signal_fraction", "stain_index", "positive_fraction"):
        call, reason = concentration_call(_row(**{key: float("nan")}), s, True)
        assert (call, reason) == ("CHECK", "not enough tissue pixels to judge")


def test_otsu_ignores_non_finite_values():
    r = np.random.default_rng(0)
    a = chan(r, 5, 2, 0.10, 120, 20)
    a[0, 0, :5] = np.nan
    a[1, 0, :5] = np.inf
    smp = make_sample({"A": a}, dtype=np.float32, value_range=255.0, checkable=False)
    row = detection_table(smp, DetectionSettings()).iloc[0]
    assert np.isfinite(row.otsu_threshold) and np.isfinite(row.signal_p99_5) and 0 < row.positive_fraction < 1


def test_values_at_each_threshold_are_not_weak():
    s = DetectionSettings()
    at = _row(signal_fraction=s.dim_fraction, stain_index=s.stain_index_low,
              positive_fraction=s.positive_fraction_low, saturation_fraction=s.saturation_high)
    assert concentration_call(at, s, True) == ("OK", "strong signal, high stain index, not saturated")
    over = _row(saturation_fraction=s.saturation_high * 1.01)
    assert concentration_call(over, s, True)[0] == "LOWER"
    assert concentration_call(_row(signal_fraction=s.dim_fraction * 0.99), s, True)[0] == "CHECK"


def test_single_weakness_paths_are_check():
    s = DetectionSettings()
    dim_only = concentration_call(_row(signal_fraction=0.05), s, True)
    weak_only = concentration_call(_row(stain_index=1.0), s, True)
    assert dim_only[0] == "CHECK" and "dim" in dim_only[1]
    assert weak_only[0] == "CHECK" and "low stain index" in weak_only[1]
    assert concentration_call(_row(signal_fraction=0.05, stain_index=1.0), s, True)[0] == "RAISE"


def test_twelve_bit_image_uses_its_own_range():
    r = np.random.default_rng(0)
    # a 12-bit file: values up to 4095, healthy signal at 3000 (73% of 4095, but only 4.6% of 65535)
    smp = make_sample({"A": chan(r, 100, 30, 0.10, 3000, 200, top=4095)}, dtype=np.uint16, value_range=4095.0)
    row = detection_table(smp, DetectionSettings()).iloc[0]
    assert row.call == "OK" and row.signal_fraction > 0.5 and row.saturation_fraction == 0.0
