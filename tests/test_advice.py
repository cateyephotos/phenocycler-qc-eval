import json

import pytest

from phenocycler_qc_eval.advice import (
    evidence_detail, figure_title, headline, kind_text, next_run_text, short_evidence,
)
from phenocycler_qc_eval.results import load_results
from synth import PROTO, copy_only


@pytest.mark.parametrize(
    "call,exposure,text",
    [
        ("RAISE", "LENGTHEN", "Raise concentration (or lengthen exposure)"),
        ("RAISE", "KEEP", "Raise concentration"),
        ("LOWER", "SHORTEN", "Lower concentration (or shorten exposure)"),
        ("LOWER", "KEEP", "Lower concentration"),
        ("CHECK", "KEEP", "Compare with expected biology"),
        ("OK", "KEEP", "Keep"),
    ],
)
def test_next_run_text(call, exposure, text):
    assert next_run_text(call, exposure) == text


def test_next_run_text_default_exposure():
    assert next_run_text("RAISE") == "Raise concentration"


def test_headline_on_prototype_run():
    h = headline(load_results(PROTO))
    assert h.titration == ("Next run: 7 to change, 15 to compare with expected biology, "
                           "9 to keep (of 31 antibodies).")
    assert h.crosstalk == "Crosstalk: 5 channel pairs flagged, strongest CD8 -> CD21."
    assert h.text == h.titration + "\n" + h.crosstalk


def test_headline_crosstalk_off_and_float(results_no_panel_float):
    h = headline(results_no_panel_float)
    assert h.crosstalk == "Crosstalk: not checked."
    assert "Saturation not checked (float image)" in h.notes
    assert not any(n.startswith("Crosstalk is off") for n in h.notes)       # said once in the headline, once on the tab


def test_headline_cancelled_says_what_is_usable(tmp_path):
    part = copy_only(PROTO, tmp_path, ["stain_qc_channels.csv"])
    (part / "RUN_INCOMPLETE.txt").write_text("cancelled by user")
    (part / "run_info.json").write_text('{"status": "cancelled"}')
    h = headline(load_results(part))
    assert any(
        n == "Run cancelled: antibody results are complete; crosstalk did not finish" for n in h.notes
    )
    assert h.crosstalk == "Crosstalk: not finished."
    assert not any(n.startswith("Crosstalk is off") for n in h.notes)


def test_headline_failed_with_nothing_saved(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "run_info.json").write_text('{"status": "failed"}')
    h = headline(load_results(run))
    assert "Run failed: no results were saved" in h.notes
    assert h.titration == "No results yet." and h.crosstalk == ""


def test_headline_zero_changes_and_zero_flags(tmp_path):
    run = copy_only(PROTO, tmp_path, ["stain_qc_channels.csv", "stain_qc_crosstalk_pairs.csv"])
    r = load_results(run)
    r.detection["call"] = "OK"
    r.crosstalk["flag"] = False
    h = headline(r)
    assert h.titration.startswith("Next run: 0 to change, 0 to compare")
    assert h.crosstalk == "Crosstalk: no pairs flagged. This does not prove the channels are clean."


def test_headline_lists_unmatched_panel_markers(tmp_path):
    run = copy_only(PROTO, tmp_path, ["stain_qc_channels.csv"])
    (run / "run_info.json").write_text(
        json.dumps({"status": "complete", "crosstalk_state": "on", "unmatched": ["A", "B"]})
    )
    h = headline(load_results(run))
    assert "In the panel file but not found in the image: A, B" in h.notes


def test_kind_text_never_shows_the_raw_id():
    assert kind_text("adjacent_cycle_carryover") == "Carryover from the previous cycle"
    assert kind_text("same_cycle_spectral") == "Same-cycle bleed-through"
    assert kind_text("something_new") == "Cause unclear"


def test_short_evidence_fits_and_differs_between_rows():
    det = load_results(PROTO).detection
    texts = [short_evidence(row) for _, row in det.iterrows()]
    assert all(len(t) <= 45 for t in texts), [t for t in texts if len(t) > 45]
    cd163 = det[det["channel"] == "CD163"].iloc[0]
    assert short_evidence(cd163) == "Dim: brightest 0.5% reach 15 of 255"
    hif1a = det[det["channel"] == "HIF1A"].iloc[0]
    assert short_evidence(hif1a) == "3.8% of tissue pixels saturated"
    assert short_evidence(hif1a, saturation_checked=False) != short_evidence(hif1a)


def test_evidence_detail_keeps_the_hedge_for_compare_rows():
    det = load_results(PROTO).detection
    cd20 = det[det["channel"] == "CD20"].iloc[0]
    text = evidence_detail(cd20)
    assert "Low stain index" in text and text.endswith("compare with expected biology.")


def test_figure_titles_are_plain():
    assert figure_title("stain_qc_crosstalk_asymmetry.png") == "Crosstalk heatmap: all channel pairs"
    assert figure_title("followup_CD3e_to_ICOS.png") == "Follow-up panel: CD3e into ICOS"
    assert figure_title("odd.png") == "odd.png"


def test_evidence_comes_from_numbers_not_reason_text():
    import pandas as pd
    base = {"channel": "X", "signal_p99_5": 200.0, "stain_index": 8.0, "positive_fraction": 0.2,
            "saturation_fraction": 0.0, "reason": "something the engine says differently now"}
    dim = pd.Series({**base, "call": "RAISE", "signal_p99_5": 12.0})
    assert short_evidence(dim) == "Dim: brightest 0.5% reach 12 of 255"
    sat = pd.Series({**base, "call": "LOWER", "saturation_fraction": 0.03})
    assert short_evidence(sat) == "3.0% of tissue pixels saturated"
    # RAISE row where no cut-off is crossed: never "no problem found".
    odd = pd.Series({**base, "call": "RAISE"})
    assert "no problem found" not in short_evidence(odd)
    assert "no problem found" not in evidence_detail(odd)
    assert len(short_evidence(odd)) <= 45
    ok = pd.Series({**base, "call": "KEEP"})
    assert "no problem found" in short_evidence(ok)


def test_wide_range_values_are_not_scientific_notation():
    import pandas as pd
    from phenocycler_qc_eval.advice import fmt_value
    assert fmt_value(12345.6) == "12346" and fmt_value(15.04) == "15" and fmt_value(float("nan")) == "-"
    row = pd.Series({"call": "RAISE", "signal_p99_5": 5000.0, "stain_index": 8.0,
                     "positive_fraction": 0.2, "saturation_fraction": 0.0, "reason": "x"})
    text = short_evidence(row, intensity_range=65535.0)
    assert text == "Dim: brightest 0.5% reach 5000 of 65535" and "e+" not in text
    assert len(text) <= 45


def test_same_cycle_pair_flagged_both_ways_counts_once():
    import pandas as pd
    from types import SimpleNamespace
    xt = pd.DataFrame({"source": ["A", "B", "C"], "target": ["B", "A", "D"],
                       "kind": ["same_cycle_spectral", "same_cycle_spectral", "adjacent_cycle_carryover"],
                       "flag": [True, True, False], "spill": [0.3, 0.2, 0.1]})
    r = SimpleNamespace(detection=pd.DataFrame({"call": ["OK"]}), crosstalk=xt, crosstalk_state="on",
                        status="complete", unmatched=[], saturation_checked=True, incomplete_note=None,
                        crosstalk_message="")
    assert "1 channel pair flagged" in headline(r).crosstalk
    xt.loc[:, "kind"] = "adjacent_cycle_carryover"       # next-cycle rows stay directional
    assert "2 channel pairs flagged" in headline(r).crosstalk


# ---- Task 15: running and failed runs with nothing yet, and the run's own settings ---------------
@pytest.mark.parametrize("status", ["running", "error", "cancelled"])
def test_empty_unfinished_run_says_no_results_yet(tmp_path, status):
    run = tmp_path / "r"
    run.mkdir()
    (run / "run_info.json").write_text(json.dumps({"status": status}))
    h = headline(load_results(run))
    assert h.titration == "No results yet." and "0 to change" not in h.text


def test_running_run_with_antibody_results_says_checking_crosstalk(tmp_path):
    run = copy_only(PROTO, tmp_path, ["stain_qc_channels.csv"])
    (run / "run_info.json").write_text(json.dumps({"status": "running", "crosstalk_state": "pending"}))
    h = headline(load_results(run))
    assert h.crosstalk == "Antibody results ready. Checking crosstalk..."
    assert "Antibody results ready. Checking crosstalk..." not in h.notes


def test_evidence_uses_the_settings_the_run_used():
    import pandas as pd
    from phenocycler_qc_eval.advice import detection_settings_of
    row = pd.Series({"channel": "X", "call": "RAISE", "signal_p99_5": 20.0, "stain_index": 8.0,
                     "positive_fraction": 0.2, "saturation_fraction": 0.0})
    assert short_evidence(row).startswith("Dim")                       # 20 of 255 is under the default 10%
    own = detection_settings_of({"detection": {"dim_fraction": 0.05}})
    assert not short_evidence(row, detection=own).startswith("Dim")
    assert "Dim" not in evidence_detail(row, detection=own)
    assert detection_settings_of({}).dim_fraction == 0.10              # no recorded settings: defaults
    assert detection_settings_of({"detection": {"dim_fraction": "junk"}}).dim_fraction == 0.10
