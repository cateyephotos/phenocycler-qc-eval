"""Review fix pass 3, the parts that need no window: wording, the overlap rule, step counts, time left, the report."""

from __future__ import annotations

import re

import pandas as pd

from phenocycler_qc_eval import advice
from phenocycler_qc_eval.gui.run_state import RunState
from phenocycler_qc_eval.settings import DetectionSettings


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def pair(**kw) -> pd.Series:
    base = dict(source="TOX", target="CD163", kind="adjacent_cycle_carryover", spill=0.27, reverse_spill=0.25,
                asymmetry=0.02, highpass_corr=0.48, asym_cut=0.15, highpass_cut=0.31, source_near_blank=False,
                target_near_blank=False, flag=True, flag_reason="texture")
    base.update(kw)
    return pd.Series(base)


def overlap(**kw) -> pd.Series:
    base = dict(marker_a="CD31", marker_b="CD20", co_positive=0.009, expected_if_independent=0.004, lift=2.45)
    base.update(kw)
    return pd.Series(base)


# ---------------------------------------------------------------------------- E2: a near-blank channel is not tested
def test_an_overlap_pair_with_a_near_blank_channel_is_not_tested_and_has_no_ratio_verdict():
    assert advice.exclusive_verdict(2.45, blank=True) == ("Not tested: near-blank channel", "untested")
    assert advice.exclusive_verdict(2.45)[0] == "Overlap higher than expected"          # the rule itself is unchanged
    assert advice.exclusive_numbers(overlap(), blank=True) == []


def test_the_sentence_for_a_pair_left_out_is_plain_and_has_no_ratio():
    text = advice.exclusive_sentence(overlap(), blank_names=["CD31"])
    assert text.startswith("Not tested: CD31 has little or no signal")
    assert "2.45" not in text and "ratio" not in text.lower() and "%" not in text
    both = advice.exclusive_sentence(overlap(), blank_names=["CD31", "CD20"])
    assert "CD31 and CD20 have little or no signal" in both


def test_blank_markers_use_the_run_s_own_near_blank_cut_off():
    det = pd.DataFrame([dict(channel="CD31", signal_p99_5=2.0), dict(channel="CD20", signal_p99_5=165.0),
                        dict(channel="iNOS", signal_p99_5=21.0)])
    assert advice.blank_markers(det, 255.0, DetectionSettings()) == {"CD31"}                 # 0.05 of 255 = 12.75
    assert advice.blank_markers(det, 255.0, DetectionSettings(blank_fraction=0.2)) == {"CD31", "iNOS"}


# ---------------------------------------------------------------------------- E1 / E4: short cells, spill said once
def test_the_cause_has_a_short_form_for_a_table_cell():
    assert advice.kind_short("adjacent_cycle_carryover") == "Carryover"
    assert advice.kind_short("same_cycle_spectral") == "Same cycle"
    assert advice.kind_short("something_new") == "Unclear"
    assert advice.kind_text("same_cycle_spectral") == "Same-cycle bleed-through"             # the whole phrase is unchanged


def test_the_flag_reason_has_a_short_form_for_a_table_cell():
    for reason in ("asymmetry", "texture", "asymmetry+texture"):
        short = advice.pair_verdict(pair(flag_reason=reason), short=True)[0]
        whole = advice.pair_verdict(pair(flag_reason=reason))[0]
        assert short.startswith("Flagged") and len(short) < len(whole)
    assert advice.pair_verdict(pair(flag=False, source_near_blank=True), short=True)[0] == "Not tested: near-blank channel"


def test_only_the_two_numbers_that_decide_a_flag_are_facts_and_spill_is_one_named_sentence():
    nums = advice.pair_numbers(pair())
    assert [label for label, _ in nums] == ["Fine-detail match", "One-way excess"]
    assert all("cut-off" in value for _label, value in nums)
    sentence = advice.spill_sentence(pair())
    assert "from TOX into CD163 is 0.27" in sentence and "from CD163 into TOX it is 0.25" in sentence


# ---------------------------------------------------------------------------- Y2: each step counts its own unit
def _state(clock=None) -> RunState:
    s = RunState(clock or Clock())
    s.start()
    return s


def _now_line(s: RunState) -> str:
    return next(line for line in s.lines() if line.startswith("●"))


def test_every_count_in_the_step_list_says_what_it_counts():
    s = _state()
    cases = [("sampling", "Reading window 21 of 61", "Reading windows (window 21 of 61)"),
             ("early_scoring", "Scoring antibody 3 of 31 (first 50 windows)", "(early look: antibody 3 of 31)"),
             ("early_scoring", "Preparing preview 28 of 31 (first 50 windows)", "(early look: preview 28 of 31)"),
             ("sampling", "Reading window 52 of 61", "(window 52 of 61)"),
             ("detection", "Scoring antibody 10 of 31", "Scoring antibodies (first pass: antibody 10 of 31)"),
             ("detection", "Preparing preview 3 of 31", "(second pass: preview 3 of 31)"),
             ("crosstalk", "Checking crosstalk: fine detail, window 21 of 61", "Checking crosstalk (window 21 of 61)"),
             ("followups", "Follow-up panel 2 of 6", "(panel 2 of 6)"),
             ("figures", "Drawing figure 4 of 9", "(figure 4 of 9)")]
    for stage, message, expect in cases:
        s.update(stage, 0.5, message)
        assert expect in _now_line(s), (message, _now_line(s))
        assert not re.search(r"\((?:early look: )?\d+ of \d+\)", _now_line(s)), _now_line(s)   # never a bare count


def test_the_steps_do_not_go_back_when_the_early_look_counts_antibodies():
    s = _state()
    s.update("sampling", 0.2, "Reading window 5 of 61")
    s.update("early_scoring", 0.3, "Preparing preview 28 of 31 (first 50 windows)")
    s.update("sampling", 0.4, "Reading window 52 of 61")
    assert "Reading windows (window 52 of 61)" in _now_line(s)
    assert s.compact() == "Step 2 of 5: Reading windows (window 52 of 61)"


# ---------------------------------------------------------------------------- Y9 / E6: time
def test_a_whole_section_says_how_long_it_takes_a_crop_does_not():
    crop, full = _state(), _state()
    crop.update("sampling", 0.1, "Reading window 3 of 61")
    full.update("sampling", 0.1, "Reading window 3 of 200")
    assert crop.size_note() == ""
    assert "full section" in full.size_note() and "10 to 15 minutes" in full.size_note()
    assert "antibody results come first" in full.size_note().lower()


def test_a_counted_step_says_how_long_is_left_after_ten_seconds_from_its_own_pace():
    clock = Clock()
    s = _state(clock)
    s.update("crosstalk", 0.5, "Checking crosstalk: fine detail, window 1 of 61")
    clock.t += 5.0
    s.update("crosstalk", 0.5, "Checking crosstalk: fine detail, window 3 of 61")
    assert s.left_short() == ""                                                  # too early to say
    clock.t += 10.0
    s.update("crosstalk", 0.5, "Checking crosstalk: fine detail, window 7 of 61")   # 6 windows in 15 s: 54 left = 135 s
    assert s.left_short() == "About 2 minutes left in this step."
    assert "step should take about 2 minutes more" in s.eta_text()
    s.update("figures", 0.9, "Writing the report")                                # no count: no estimate
    assert s.left_short() == "" and s.eta_text() == ""


# ---------------------------------------------------------------------------- E2 in the report
def test_the_report_says_not_tested_for_an_overlap_pair_with_a_near_blank_channel(tmp_path):
    from synth import PROTO
    from phenocycler_qc_eval.report import _exclusive_section
    from phenocycler_qc_eval.results import load_results

    r = load_results(PROTO)
    text = "\n".join(_exclusive_section(r.exclusive_pairs, r.detection, r.intensity_range))
    assert "Result" in text
    cd31 = next(line for line in text.splitlines() if line.startswith("| CD31 "))
    assert "Not tested: near-blank channel" in cd31 and "2.45" not in cd31
    cd4 = next(line for line in text.splitlines() if line.startswith("| CD4 "))
    assert "Overlap higher than expected" in cd4
