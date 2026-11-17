import numpy as np
import pandas as pd
import pytest
from scipy.ndimage import gaussian_filter

from phenocycler_qc_eval.crosstalk import (
    CrosstalkResult, control_for, crosstalk_table, cycle_order_suggestions, exclusive_pairs_table,
    followup_numbers, followup_pairs, highpass_corr, pair_kind, run_crosstalk, spill_matrix,
)
from phenocycler_qc_eval.detection import detection_table
from phenocycler_qc_eval.layout import PanelLayout
from phenocycler_qc_eval.settings import CrosstalkSettings, DetectionSettings
from phenocycler_qc_eval.tissue import Sample

N, H = 6, 128
BRIGHT = {"CD20", "CD4", "CD8"}          # strong sources; the others are dimmer targets
LAYOUT = [  # marker, cycle, fluor
    ("CD31", 1, "ATTO550"), ("CD4", 1, "CY5"), ("CD20", 1, "AF750"),
    ("CD44", 2, "ATTO550"), ("FOXP3", 2, "CY5"), ("SMA", 2, "AF750"),
    ("KI67", 4, "ATTO550"), ("CD8", 4, "CY5"), ("CD21", 4, "AF750"),
    ("CD68", 6, "ATTO550"), ("CD163", 6, "CY5"), ("TOX", 6, "AF750"),
]
NAMES = ["DAPI"] + [m for m, _, _ in LAYOUT] + ["CD3e"]   # CD3e is in the image but not in the panel file


def lay_table(fluors=None):
    fluors = fluors or {}
    return pd.DataFrame([{"marker": m, "cycle": c, "fluor": fluors.get(f, f), "exposure_ms": np.nan}
                         for m, c, f in LAYOUT])


def layout(fluors=None):
    return PanelLayout(lay_table(fluors), "test")


def cells(rng):
    """Cell-like blobs with fine texture, scaled to 0..1."""
    pts = (rng.random((N, H, H)) < 0.002).astype(np.float32)
    blobs = np.stack([gaussian_filter(p, 3) for p in pts]) / 0.0177
    return np.clip(blobs, 0, 1) * (0.6 + 0.8 * rng.random((N, H, H)))


def build(leaks=(), blank=(), copy_into_blank=None, seed=0):
    """leaks: (source, target, fraction). Returns a uint8 Sample of independent channels plus leaks."""
    rng = np.random.default_rng(seed)
    raw = {}
    for name in NAMES[1:]:
        amp = 230.0 if name in BRIGHT else 60.0
        raw[name] = amp * cells(rng) + rng.normal(2, 1, (N, H, H))
    for src, tgt, frac in leaks:
        raw[tgt] = raw[tgt] + frac * raw[src]
    for name in blank:
        raw[name] = rng.normal(1, 0.5, (N, H, H))
    if copy_into_blank:
        src, tgt, frac = copy_into_blank
        raw[tgt] = raw[tgt] + frac * raw[src]
    stack = np.stack([np.full((N, H, H), 100.0)] + [raw[n] for n in NAMES[1:]], axis=1)
    px = np.clip(np.rint(stack), 0, 255).astype(np.uint8)
    return Sample(pixels=px, names=list(NAMES),
                  origins=pd.DataFrame({"y0": range(N), "x0": range(N), "size_px": [H] * N}),
                  window_px=H, nuclear_name="DAPI", value_range=255.0, saturation_checkable=True,
                  range_basis="test")


@pytest.fixture(scope="module")
def smp_clean():
    return build()


@pytest.fixture(scope="module")
def smp_leak():
    # 10% CD20 (AF750) into CD31 (ATTO550), same cycle; 10% CD4 (c1, CY5) into FOXP3 (c2, CY5)
    return build(leaks=[("CD20", "CD31", 0.10), ("CD4", "FOXP3", 0.10)])


@pytest.fixture(scope="module")
def smp_blank_target():
    return build(blank=["CD21"], copy_into_blank=("CD8", "CD21", 0.01))


def det_of(smp):
    return detection_table(smp, DetectionSettings())


def xt(smp, fluors=None):
    return run_crosstalk(smp, det_of(smp), layout(fluors), CrosstalkSettings(), 2.0)


def test_same_cycle_leak_flagged_against_wavelength_order(smp_leak):
    t = xt(smp_leak).table
    assert t[(t.source == "CD20") & (t.target == "CD31")].flag.item()


def test_carryover_flagged_forward_only(smp_leak):
    t = xt(smp_leak).table
    fwd = t[(t.source == "CD4") & (t.target == "FOXP3")].iloc[0]
    assert fwd.kind == "adjacent_cycle_carryover" and fwd.flag
    back = t[(t.source == "FOXP3") & (t.target == "CD4")].iloc[0]
    assert back.kind == "other" and not back.flag


def test_near_blank_target_never_flagged(smp_blank_target):
    det = det_of(smp_blank_target).set_index("channel")
    assert bool(det.loc["CD21", "near_blank"])
    t = xt(smp_blank_target).table
    rows = t[(t.target == "CD21") | (t.source == "CD21")]
    assert len(rows) > 0 and not rows.flag.any()


def test_near_blank_would_flag_without_the_exclusion(smp_blank_target):
    """The copied texture is real, so only the exclusion keeps CD21 out of the flags."""
    sp, hp = spill_matrix(smp_blank_target), highpass_corr(smp_blank_target, 2.0)
    t = crosstalk_table(sp, hp, lay_table(), set(), CrosstalkSettings(), exclude=["DAPI"])
    assert t[(t.source == "CD8") & (t.target == "CD21")].flag.item()


def test_no_layout_turns_crosstalk_off(smp_clean):
    r = run_crosstalk(smp_clean, det_of(smp_clean), None, CrosstalkSettings(), 2.0)
    assert r.enabled is False and "no panel file" in r.message and r.table is None
    assert isinstance(r, CrosstalkResult) and r.unmatched == [] and r.suggestions == []


def test_clean_sample_has_no_flags(smp_clean):
    assert not xt(smp_clean).table.flag.any()


def test_fluor_names_come_from_layout(smp_leak):
    opal = {"CY5": "Opal690", "ATTO550": "Opal520", "AF750": "Opal780"}
    t = run_crosstalk(smp_leak, det_of(smp_leak), layout(opal), CrosstalkSettings(), 2.0).table
    assert t[(t.source == "CD4") & (t.target == "FOXP3")].flag.item()
    assert t[(t.source == "CD20") & (t.target == "CD31")].flag.item()


def test_cycle_order_suggestion_text(smp_leak):
    res = xt(smp_leak)
    assert any("FOXP3" in s and "consider moving" in s for s in res.suggestions)
    assert any("cycle 2, CY5" in s and "follows CD4" in s for s in res.suggestions)


def test_exclusive_pairs_lift(smp_clean):
    ex = exclusive_pairs_table(smp_clean, det_of(smp_clean), [("CD3e", "CD20"), ("CD3e", "MISSING")])
    assert list(ex.marker_b) == ["CD20"] and ex.lift.notna().all()
    assert list(ex.columns) == ["marker_a", "marker_b", "pos_a", "pos_b", "co_positive",
                                "expected_if_independent", "lift"]


def test_exclusive_pairs_match_names_after_normalizing(smp_clean):
    ex = exclusive_pairs_table(smp_clean, det_of(smp_clean), [["CD3E", "cd 20"]])
    assert list(ex.marker_a) == ["CD3e"] and list(ex.marker_b) == ["CD20"]


def test_exclusive_pairs_need_no_layout(smp_clean):
    r = run_crosstalk(smp_clean, det_of(smp_clean), None, CrosstalkSettings(), 2.0)
    assert r.enabled is False
    assert len(exclusive_pairs_table(smp_clean, det_of(smp_clean), [("CD4", "CD20")])) == 1


# ---- helpers
def test_pair_kind_rules():
    lay = lay_table()
    assert pair_kind("CD20", "CD31", lay) == "same_cycle_spectral"
    assert pair_kind("CD31", "CD20", lay) == "same_cycle_spectral"
    assert pair_kind("CD4", "FOXP3", lay) == "adjacent_cycle_carryover"
    assert pair_kind("FOXP3", "CD4", lay) == "other"                  # backward
    assert pair_kind("CD31", "CD44", lay) == "adjacent_cycle_carryover"   # ATTO550 in cycle 1 then 2
    assert pair_kind("CD31", "SMA", lay) == "other"                   # next cycle, different fluor
    assert pair_kind("CD31", "KI67", lay) == "other"                  # 3 cycles apart, same fluor
    assert pair_kind("CD31", "CD8", lay) == "null"                    # 3 cycles apart, different fluor
    assert pair_kind("CD31", "NOT_IN_LAYOUT", lay) == "other"


def test_unmatched_image_channel_gets_kind_other(smp_clean):
    t = xt(smp_clean).table
    assert (t[(t.source == "CD3e") | (t.target == "CD3e")].kind == "other").all()
    assert not t.source.eq("DAPI").any() and not t.target.eq("DAPI").any()


def test_unmatched_markers_are_reported(smp_clean):
    ghost = pd.DataFrame([{"marker": "GHOST", "cycle": 3, "fluor": "CY5", "exposure_ms": np.nan}])
    lay = pd.concat([lay_table(), ghost], ignore_index=True)
    r = run_crosstalk(smp_clean, det_of(smp_clean), PanelLayout(lay, "t"), CrosstalkSettings(), 2.0)
    assert r.unmatched == ["GHOST"] and "GHOST" in r.message


def test_table_columns_and_cuts(smp_leak):
    t = xt(smp_leak).table
    assert list(t.columns) == ["source", "target", "kind", "spill", "reverse_spill", "asymmetry",
                               "highpass_corr", "asym_cut", "highpass_cut", "source_near_blank",
                               "target_near_blank", "flag", "flag_reason"]
    assert t.asym_cut.nunique() == 1 and np.isfinite(t.asym_cut.iloc[0])
    row = t[(t.source == "CD20") & (t.target == "CD31")].iloc[0]
    assert row.flag_reason in ("asymmetry", "texture", "asymmetry+texture")
    assert row.asymmetry == pytest.approx(row.spill - row.reverse_spill)


def test_spill_matrix_shape_and_small_groups(smp_clean):
    sp = spill_matrix(smp_clean)
    assert list(sp.index) == NAMES and np.isnan(np.diag(sp.to_numpy())).all()
    tiny = Sample(pixels=smp_clean.pixels[:1, :, :6, :6], names=NAMES, origins=smp_clean.origins.iloc[:1],
                  window_px=6, nuclear_name="DAPI", value_range=255.0, saturation_checkable=True)
    assert spill_matrix(tiny).isna().all().all()          # under 50 pixels in a group: blank rows


def test_highpass_is_symmetric_and_picks_up_a_copy(smp_leak):
    hp = highpass_corr(smp_leak, 2.0)
    assert hp.loc["CD20", "CD31"] == pytest.approx(hp.loc["CD31", "CD20"])
    assert hp.loc["CD20", "CD31"] > 0.2 > abs(hp.loc["CD20", "CD44"])


def test_followup_pairs_one_per_same_cycle_pair(smp_leak):
    res = xt(smp_leak)
    pairs = followup_pairs(res.table)
    assert ("CD4", "FOXP3") in pairs
    same = [p for p in pairs if set(p) == {"CD20", "CD31"}]
    assert same == [("CD20", "CD31")]                      # the larger-spill orientation, once
    assert followup_pairs(res.table.iloc[0:0]) == []


def test_followup_numbers_keys_and_leak(smp_leak):
    f = followup_numbers(smp_leak, "CD4", "FOXP3", "CD44")
    assert {"source", "target", "control", "target_where_source_bright", "target_where_source_low",
            "share_target_bright_on_source_bright", "target_where_control_bright",
            "target_where_control_low", "share_target_bright_on_control_bright",
            "share_target_bright_nuclear", "chance_share", "figure"} <= set(f)
    assert f["target_where_source_bright"] > 2 * f["target_where_source_low"]
    assert f["chance_share"] == 0.05 and f["figure"] == "followup_CD4_to_FOXP3.png"
    none = followup_numbers(smp_leak, "CD4", "FOXP3", None)
    assert none["control"] is None and "target_where_control_bright" not in none


def test_control_for_normalizes_names():
    controls = [["cd163", "CD 68"], ["FOXP3", "CD4"]]
    assert control_for("CD163", controls, ["CD68", "CD163"]) == "CD68"
    assert control_for("TOX", controls, ["TOX"]) is None
    assert control_for("FOXP3", controls, ["CD4", "FOXP3"], avoid=["CD4"]) is None


def test_suggestions_empty_without_carryover(smp_clean):
    assert xt(smp_clean).suggestions == []
    assert cycle_order_suggestions(None, lay_table()) == []


def test_every_flag_reason_has_plain_words(smp_leak):
    from phenocycler_qc_eval.advice import flag_reason_text
    reasons = set(xt(smp_leak).table.query("flag").flag_reason)
    assert reasons and all(flag_reason_text(r) for r in reasons)


def test_message_counts_distinct_pairs_not_table_rows(smp_leak):
    res = xt(smp_leak)
    rows = int(res.table["flag"].sum())
    pairs = len(followup_pairs(res.table))
    assert rows > pairs                                    # a same-cycle texture flag fills both directions
    assert f"{pairs} pair(s) flagged" in res.message


def test_sparse_channel_has_blank_spill_row(smp_clean):
    px = smp_clean.pixels.copy()
    i = NAMES.index("CD44")
    px[:, i] = 0
    px[:, i, :8, :8] = 200                                 # bright on well under 2% of tissue, zero elsewhere
    px[:, i, 20:40, 20:40] = 0
    sparse = Sample(pixels=px, names=list(NAMES), origins=smp_clean.origins, window_px=smp_clean.window_px,
                    nuclear_name="DAPI", value_range=255.0, saturation_checkable=True)
    sp = spill_matrix(sparse)
    assert sp.loc["CD44"].isna().all()                     # 99th percentile is 0: "bright" would be everything
    assert sp.loc["CD20"].drop("CD20").notna().all()       # other rows are untouched


def test_cut_follows_null_percentile_and_margin(smp_leak):
    det = det_of(smp_leak)
    sp, hp = spill_matrix(smp_leak), highpass_corr(smp_leak, 2.0)
    blank = set(det.loc[det["near_blank"].astype(bool), "channel"])

    def cuts(**kw):
        t = crosstalk_table(sp, hp, lay_table(), blank, CrosstalkSettings(**kw), exclude=["DAPI"])
        null = t[t.kind == "null"]
        return t.asym_cut.iloc[0], t.highpass_cut.iloc[0], null

    a95, h95, null = cuts(null_percentile=95.0, margin=0.05)
    assert a95 == pytest.approx(np.percentile(null.asymmetry.dropna(), 95) + 0.05)
    assert h95 == pytest.approx(np.percentile(null.highpass_corr.dropna(), 95) + 0.05)
    a_m, h_m, _ = cuts(null_percentile=95.0, margin=0.15)
    assert a_m - a95 == pytest.approx(0.10) and h_m - h95 == pytest.approx(0.10)
    a50, h50, _ = cuts(null_percentile=50.0, margin=0.05)
    assert a50 == pytest.approx(np.percentile(null.asymmetry.dropna(), 50) + 0.05)
    assert a50 < a95 and h50 < h95


def test_pair_kind_accepts_a_prebuilt_lookup():
    lay = lay_table()
    lookup = lay.drop_duplicates("marker").set_index("marker")
    assert pair_kind("CD20", "CD31", lay, lookup) == pair_kind("CD20", "CD31", lay) == "same_cycle_spectral"


def _saturated_leak_sample():
    base = build(leaks=[("CD20", "CD31", 0.10)])
    px = base.pixels.copy()
    i = NAMES.index("CD20")
    ch = px[:, i].astype(np.float32)
    ch[ch > 120] = 255                                     # saturate the cells after the leak was copied
    px[:, i] = ch.astype(np.uint8)
    return Sample(pixels=px, names=list(NAMES), origins=base.origins, window_px=base.window_px,
                  nuclear_name="DAPI", value_range=255.0, saturation_checkable=True)


def test_saturated_channel_keeps_its_spill_row_and_is_still_a_source():
    smp = _saturated_leak_sample()
    i = NAMES.index("CD20")
    share = (smp.pixels[:, i] == 255).mean()
    assert share > 0.02                                    # fixture check: more than the old 2% limit is saturated
    sp = spill_matrix(smp)
    assert sp.loc["CD20"].drop("CD20").notna().all()       # saturated is not blank
    t = xt(smp).table
    row = t[(t.source == "CD20") & (t.target == "CD31")].iloc[0]
    assert bool(row["flag"])


def test_channel_whose_median_equals_its_99th_percentile_is_blank(smp_clean):
    px = smp_clean.pixels.copy()
    i = NAMES.index("CD44")
    px[:, i] = 30                                          # flat: p99 == median, no bright group exists
    px[:, i, :2, :2] = 31
    flat = Sample(pixels=px, names=list(NAMES), origins=smp_clean.origins, window_px=smp_clean.window_px,
                  nuclear_name="DAPI", value_range=255.0, saturation_checkable=True)
    assert spill_matrix(flat).loc["CD44"].isna().all()


def _with_noise_channel(base):
    """Append an 8-bit noise channel: median 0, 99th percentile about 2."""
    rng = np.random.default_rng(7)
    noise = np.where(rng.random(base.pixels[:, :1].shape) < 0.03, 2, 0).astype(np.uint8)
    px = np.concatenate([base.pixels, noise], axis=1)
    smp = Sample(pixels=px, names=list(base.names) + ["NOISE"], origins=base.origins, window_px=base.window_px,
                 nuclear_name="DAPI", value_range=255.0, saturation_checkable=True)
    table = pd.concat([lay_table(), pd.DataFrame([{"marker": "NOISE", "cycle": 5, "fluor": "AF750",
                                                   "exposure_ms": np.nan}])], ignore_index=True)
    return smp, PanelLayout(table, "test")


def test_noise_channel_is_never_flagged_and_does_not_move_the_cut(smp_leak):
    smp, lay = _with_noise_channel(smp_leak)
    px = smp.pixels[:, -1]
    assert np.median(px) == 0 and np.percentile(px, 99) == 2          # fixture check
    res = run_crosstalk(smp, det_of(smp), lay, CrosstalkSettings(), 2.0)
    t = res.table
    rows = t[(t.source == "NOISE") | (t.target == "NOISE")]
    assert len(rows) > 0 and not rows.flag.any()
    base = xt(smp_leak).table
    assert abs(t.asym_cut.iloc[0] - base.asym_cut.iloc[0]) <= 0.01
    assert abs(t.highpass_cut.iloc[0] - base.highpass_cut.iloc[0]) <= 0.01
