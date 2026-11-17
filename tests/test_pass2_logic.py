"""Review fix pass 2, the parts that need no window: wording, verdicts, severity, the run-state model."""

from __future__ import annotations

import pandas as pd
import pytest

from phenocycler_qc_eval import advice
from phenocycler_qc_eval.settings import DetectionSettings


def row(**kw) -> pd.Series:
    base = dict(channel="CD20", call="CHECK", exposure="KEEP", next_run="Compare with expected biology",
                signal_p99_5=120.0, stain_index=1.9, positive_fraction=0.2, saturation_fraction=0.0)
    base.update(kw)
    return pd.Series(base)


# ---------------------------------------------------------------------------- one term: stain index
def test_weak_stain_index_uses_the_one_term_everywhere():
    r = row()
    assert advice.short_evidence(r) == "Low stain index (1.9)"
    detail = advice.evidence_detail(r, True, 255.0, DetectionSettings(), compare_hint=False)
    assert "stain index" in detail.lower() and "separation" not in detail.lower()


def test_compare_hint_can_be_left_out_of_the_detail_sentence():
    r = row()
    assert "rare target" in advice.evidence_detail(r)
    assert "rare target" not in advice.evidence_detail(r, compare_hint=False)


# ---------------------------------------------------------------------------- compare rows
def test_compare_guidance_names_the_control_marker_and_both_outcomes():
    text = advice.compare_guidance(row(channel="CD163", stain_index=1.9), True, 255.0, DetectionSettings(), "CD68")
    assert "CD163 should look like CD68" in text
    assert "raise" in text.lower() and "keep" in text.lower()
    assert text.count(".") <= 4                           # two or three short sentences
    assert not any(ch.isdigit() for ch in text.replace("CD163", "").replace("CD68", ""))   # no amounts


def test_compare_guidance_without_a_control_marker_still_says_what_to_compare():
    text = advice.compare_guidance(row(channel="KI67"), True, 255.0, DetectionSettings(), None)
    assert "KI67" in text and "expect" in text.lower() and "raise" in text.lower() and "keep" in text.lower()


def test_compare_guidance_leads_with_the_one_weak_measure():
    few = advice.compare_guidance(row(positive_fraction=0.001, stain_index=9.0), True, 255.0, DetectionSettings(), None)
    assert "few" in few.lower()
    dim = advice.compare_guidance(row(signal_p99_5=10.0, stain_index=9.0), True, 255.0, DetectionSettings(), None)
    assert "dim" in dim.lower()


def test_control_marker_for_matches_names_after_normalizing():
    controls = [["CD163", "CD68"], ["CD4", "CD3e"]]
    assert advice.control_marker_for("CD163", controls, ["CD68", "CD163"]) == "CD68"
    assert advice.control_marker_for("cd-4", controls, ["CD3E", "CD4"]) == "CD3E"
    assert advice.control_marker_for("CD163", controls, ["CD163"]) is None            # control not in the image
    assert advice.control_marker_for("KI67", controls, ["KI67", "CD68"]) is None


# ---------------------------------------------------------------------------- severity and the dim state
def test_severity_orders_by_distance_past_the_cut_off_and_has_a_word():
    d = DetectionSettings()
    mild = row(call="LOWER", saturation_fraction=0.006)            # 1.2 x the 0.005 cut-off
    strong = row(call="LOWER", saturation_fraction=0.145)          # 29 x
    s_mild, w_mild = advice.severity(mild, True, 255.0, d)
    s_strong, w_strong = advice.severity(strong, True, 255.0, d)
    assert s_strong > s_mild and (w_mild, w_strong) == ("Mild", "Strong")


def test_signal_level_separates_near_blank_from_dim_using_the_run_settings():
    d = DetectionSettings()
    assert advice.signal_level(row(signal_p99_5=1.0), 255.0, d) == "blank"       # below 0.05 of 255
    assert advice.signal_level(row(signal_p99_5=20.0), 255.0, d) == "dim"        # below 0.10 of 255
    assert advice.signal_level(row(signal_p99_5=80.0), 255.0, d) == "ok"
    looser = DetectionSettings(dim_fraction=0.5, blank_fraction=0.2)
    assert advice.signal_level(row(signal_p99_5=80.0), 255.0, looser) == "dim"


# ---------------------------------------------------------------------------- crosstalk verdicts
def pair(**kw) -> pd.Series:
    base = dict(source="TOX", target="CD163", kind="adjacent_cycle_carryover", spill=0.27, reverse_spill=0.25,
                asymmetry=0.02, highpass_corr=0.48, asym_cut=0.15, highpass_cut=0.31, source_near_blank=False,
                target_near_blank=False, flag=True, flag_reason="texture")
    base.update(kw)
    return pd.Series(base)


def test_pair_verdict_words():
    assert advice.pair_verdict(pair())[0].startswith("Flagged")
    assert advice.pair_verdict(pair(flag=False, flag_reason=""))[0] == "Not flagged"
    assert advice.pair_verdict(pair(flag=False, flag_reason="", target_near_blank=True))[0] == \
        "Not tested: near-blank channel"
    assert advice.pair_verdict(pair(flag="True"))[0].startswith("Flagged")            # as read back from a CSV


def test_pair_numbers_sit_next_to_their_cut_offs_from_the_run():
    nums = dict(advice.pair_numbers(pair()))
    assert nums["Fine-detail match"] == "0.48 (cut-off 0.31, over)"
    assert nums["One-way excess"].startswith("0.02 (cut-off 0.15") and nums["One-way excess"].endswith("under)")
    assert list(nums) == ["Fine-detail match", "One-way excess"]                   # spill is one named sentence now


def test_exclusive_verdict_uses_the_ratio_rule():
    assert advice.exclusive_verdict(2.45)[0] == "Overlap higher than expected"
    assert advice.exclusive_verdict(1.0)[0] == "Overlap higher than expected"
    assert advice.exclusive_verdict(0.82)[0] == "As expected"
    assert advice.exclusive_verdict(float("nan"))[0] == "Not tested"


def test_exclusive_sentence_gives_the_numbers_and_the_cut_off():
    r = pd.Series(dict(marker_a="CD31", marker_b="CD20", co_positive=0.068, expected_if_independent=0.028, lift=2.45))
    text = advice.exclusive_sentence(r)
    assert "CD31" in text and "CD20" in text and "6.8%" in text and "2.8%" in text and "2.45" in text
    assert advice.exclusive_numbers(r)[-1][1] == "2.45 (cut-off 1.0, over)"


def test_followup_reading_goes_above_the_tiles_in_plain_words():
    text = advice.followup_reading("TOX", "CD163", "CD68")
    assert text.startswith("What to look for:") and "CD163 is brighter where TOX is bright" in text and "CD68" in text
    assert "cycle order" in advice.FOLLOWUP_ACTION


def test_untested_channels_lists_near_blank_names_once():
    t = pd.DataFrame([pair(source="CD21", source_near_blank=True, flag=False), pair(target="CD21", target_near_blank=True, flag=False),
                      pair()])
    assert advice.untested_channels(t) == ["CD21"]


def test_zero_flagged_pairs_never_reads_as_clean():
    from phenocycler_qc_eval.results import Results
    det = pd.DataFrame([row()])
    r = Results(folder=None, detection=det, crosstalk=pd.DataFrame([pair(flag=False, flag_reason="")]),
                crosstalk_state="on", status="complete")
    assert advice.headline(r).crosstalk == "Crosstalk: no pairs flagged. This does not prove the channels are clean."
    assert "does not prove" in advice.NO_PAIRS_FLAGGED and "Cut-offs are lab-defined" in advice.CROSSTALK_CAVEAT
    assert "tonsil" in advice.CROSSTALK_CAVEAT and "Not flagged does not mean clean" in advice.CROSSTALK_CAVEAT


# ---------------------------------------------------------------------------- path text
def test_elide_middle_keeps_both_ends_and_never_breaks_a_word_in_two_lines():
    path = r"E:\Downloads\very\long\folder\name\qc_results\tonsil31_crop_20261113-080137"
    short = advice.elide_middle(path, 40)
    assert len(short) <= 40 and short.startswith("E:\\") and short.endswith("20261113-080137") and "..." in short
    assert advice.elide_middle("short.txt", 40) == "short.txt"


# ---------------------------------------------------------------------------- the run-state model
from phenocycler_qc_eval.gui.run_state import STAGES, RunState  # noqa: E402


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def test_stage_list_has_the_five_stages_in_order():
    assert [label for _key, label in STAGES][:4] == [
        "Finding tissue", "Reading windows", "Scoring antibodies", "Checking crosstalk"]
    assert "report" in STAGES[-1][1].lower()


def test_one_state_gives_the_list_the_status_and_the_banner_phrase():
    clock = Clock()
    s = RunState(clock)
    s.start()
    s.update("open", 0.02, "Opening image")
    s.update("tissue", 0.02, "Finding tissue")
    assert s.stage_index == 0 and s.indeterminate()
    s.update("sampling", 0.3, "Reading window 21 of 61")
    assert s.stage_index == 1 and s.count() == (21, 61) and not s.indeterminate()
    lines = s.lines()
    assert lines[0].startswith("\u2713") and lines[1].startswith("\u25CF") and "21 of 61" in lines[1]
    assert lines[2].startswith("\u25CB") and len(lines) == 5
    assert s.banner_phrase() == "reading window 21 of 61"
    s.update("early_scoring", 0.3, "Scoring antibody 4 of 31 (first 50 windows)")
    assert s.stage_index == 1 and s.count() == (4, 31)                    # stays in the reading step, never goes back
    s.update("detection", 0.52, "Scoring antibody 10 of 31")
    assert s.stage_index == 2 and s.banner_phrase().startswith("scoring antibodies")
    s.update("crosstalk", 0.7, "Checking crosstalk")
    assert s.stage_index == 3 and s.indeterminate()
    s.update("figures", 0.9, "Drawing figure 2 of 9")
    assert s.stage_index == 4
    s.update("sampling", 0.5, "late straggler")                           # a stage never goes backwards
    assert s.stage_index == 4


def test_finished_state_marks_everything_done():
    s = RunState(Clock())
    s.start()
    s.update("figures", 0.9, "Drawing figures")
    s.finish("done")
    assert s.finished and all(line.startswith("\u2713") for line in s.lines())
    assert s.status().startswith("Finished")


def test_reading_estimate_only_from_the_observed_rate_and_only_when_it_is_honest():
    clock = Clock()
    s = RunState(clock)
    s.start()
    s.update("sampling", 0.1, "Reading window 1 of 61")
    assert s.eta_text() == ""                                              # too early to say
    clock.t += 30.0
    s.update("sampling", 0.2, "Reading window 11 of 61")
    eta = s.eta_text()
    assert eta and "reading" in eta.lower() and "minute" in eta.lower()    # 10 windows in 30 s: 50 left = 2.5 min
    clock.t += 1.0
    s.update("detection", 0.52, "Scoring antibody 3 of 31")
    assert s.eta_text() == ""                                              # no estimate for steps with no known rate


def test_download_is_its_own_kind_of_wait():
    s = RunState(Clock())
    s.start("download")
    s.update("download", 0.4, "Downloading the example: 30 of 83 MB")
    assert s.lines() == ["\u25CF Downloading the example: 30 of 83 MB"]
    assert s.count() == (30, 83)


def test_no_step_text_is_missing_before_the_first_message():
    s = RunState(Clock())
    s.start()
    assert s.status() == "Starting the run..." and s.lines()[0].startswith("\u25CB")


# ---------------------------------------------------------------------------- one line for a short window
def test_compact_step_line_for_a_short_rail():
    s = RunState(Clock())
    s.start()
    s.update("sampling", 0.3, "Reading window 21 of 61")
    assert s.compact() == "Step 2 of 5: Reading windows (window 21 of 61)"
    s.update("detection", 0.52, "Scoring antibody 10 of 31")
    assert s.compact() == "Step 3 of 5: Scoring antibodies (first pass: antibody 10 of 31)"
    s.finish("done")
    assert s.compact() == "All 5 steps done"
    d = RunState(Clock())
    d.start("download")
    d.update("download", 0.4, "Downloading the example: 30 of 83 MB")
    assert d.compact() == "\u25CF Downloading the example: 30 of 83 MB"
