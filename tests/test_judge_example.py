"""The judge on the tonsil example crop: faults written into its real pixels must be caught, with no new flags.

Needs examples/tonsil31_crop.ome.tif (about 83 MB, not in git) and takes about 15 minutes: the crop is sampled
once, then analyze_sample runs on the unmodified sample and on two modified copies. The copies are kept in
memory. For this 8-bit image that is equal by construction to injecting into a copy of the file and sampling it:
windows are cut at the image's own resolution, the tissue mask comes from the untouched DAPI channel, and the
intensity range is the dtype's maximum, so nothing after sampling can tell the difference. (It would not hold for a
float image whose range comes from a whole-image percentile; no test compares the two routes.) What the in-memory
route skips is the reader and tiling path on modified pixels and run_qc's own steps; those are exercised on the
unmodified crop by the Task 17 test and by the synthetic run_qc tests. The crop file is never written to, its
checksum is checked before the first run (a locally remade crop can differ in bytes) and again at the end, and the
shared sample is fingerprinted before and after to show that no run changed it.

    pytest -m example tests/test_judge_example.py -v

Why these pairs. Rule, written down before any injection run, from the Task 17 baseline CSVs: take the same-cycle
pair with the largest source-to-target brightness ratio among the pairs that are clean in the unmodified crop, whose
target is not saturated and not near-blank (a rule that favors detection, not a hard pair). Near-blank channels are out of the crosstalk check as source and as target, so the leaks go
into channels that are not near-blank in this section (CD21 is, so it is not used):
  same cycle, ICOS -> MPO: cycle 12, CY5 -> ATTO550. ICOS is bright (99.5th percentile 112 of 255), MPO is dim
      but not near-blank (35), and the pair is clean in the unmodified crop (texture match 0.13 against a cut
      of 0.31, asymmetry -0.01 against 0.15). The brief's CD20 -> CD31 is not possible here: CD31 is near-blank
      (1 of 255).
  carryover, CD4 -> FOXP3: CY5, cycle 2 -> 3. CD4 is bright (124), FOXP3 is not near-blank (45), and the pair is
      clean in the unmodified crop (texture 0.17, asymmetry -0.08).
test_leak_pairs_are_testable_in_the_baseline checks all of that again on every run, so the judge cannot go
vacuous if the crop or the engine's near-blank rule changes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from synth import assert_call_moved, assert_leak_landed, inject, judge_run, pixels_digest

from phenocycler_qc_eval.example import EXAMPLE_SHA256, example_panel, sha256_file
from phenocycler_qc_eval.layout import load_layout
from phenocycler_qc_eval.readers import open_image
from phenocycler_qc_eval.settings import default_settings
from phenocycler_qc_eval.tissue import sample_windows

pytestmark = [pytest.mark.example, pytest.mark.slow]

CROP = Path(__file__).resolve().parents[1] / "examples" / "tonsil31_crop.ome.tif"
DIM = ["CD20", "CD3e", "Pan-Cytokeratin"]       # each scaled x0.12
BRIGHT = "HLA-DR"                               # x8, clipped at 255
SAME_CYCLE = ("ICOS", "MPO")                    # 10%, CY5 -> ATTO550, cycle 12
CARRYOVER = ("CD4", "FOXP3")                    # 10%, CY5, cycle 2 -> 3
TOUCHED = set(DIM) | {BRIGHT}
ADVICE_WORDS = {"RAISE", "LOWER", "CHECK", "OK"}
EXPOSURE_WORDS = {"SHORTEN", "KEEP", "LENGTHEN"}


@pytest.fixture(scope="module")
def crop():
    if not CROP.exists():
        pytest.skip(f"example image not found at {CROP}; run scripts/make_example_crop.py or "
                    "pqe --download-example --from <file>")
    return CROP


@pytest.fixture(scope="module")
def checksum_before(crop):
    digest = sha256_file(crop)
    assert digest == EXAMPLE_SHA256, (
        "the example file is not the pinned example; the pairs and numbers in this judge were chosen on it "
        "(a crop made locally can have other bytes for the same pixels: use the published file)")
    return digest


@pytest.fixture(scope="module")
def sample(crop, checksum_before):
    with open_image(crop) as src:
        return sample_windows(src, default_settings().advanced)          # every window, up to 200


@pytest.fixture(scope="module")
def sample_digest(sample):
    return pixels_digest(sample)                                          # taken before the first engine run


@pytest.fixture(scope="module")
def layout():
    return load_layout(example_panel())


@pytest.fixture(scope="module")
def baseline(sample, sample_digest, layout, tmp_path_factory):
    return judge_run(sample, layout, tmp_path_factory.mktemp("judge_baseline"))


@pytest.fixture(scope="module")
def detection_run(sample, layout, baseline, tmp_path_factory):
    faulty = inject(sample, scale={**{m: 0.12 for m in DIM}, BRIGHT: 8.0})
    return judge_run(faulty, layout, tmp_path_factory.mktemp("judge_detection"))


@pytest.fixture(scope="module")
def crosstalk_run(sample, layout, baseline, tmp_path_factory):
    faulty = inject(sample, leak={SAME_CYCLE: 0.10, CARRYOVER: 0.10})
    return judge_run(faulty, layout, tmp_path_factory.mktemp("judge_crosstalk"))


# ---------------------------------------------------------------------------- the unmodified crop
def test_baseline_uses_at_least_60_tissue_windows(baseline):
    assert baseline.n_windows >= 60


def test_baseline_flags_tox_to_cd163(baseline):
    assert ("TOX", "CD163") in baseline.flags


def test_baseline_clean_pair_was_tested_and_came_out_clean(baseline):
    """CD3e and CD38 share a cycle and are both bright: tested in both directions, flagged in neither."""
    for pair in (("CD3e", "CD38"), ("CD38", "CD3e")):
        row = baseline.pair(*pair)
        assert row is not None and row["kind"] == "same_cycle_spectral"
        assert not row["source_near_blank"] and not row["target_near_blank"] and not row["flag"], pair


def test_baseline_cd21_pairs_are_excluded_by_rule_not_tested(baseline):
    """CD21 is near-blank in this section, so "CD8 to CD21 is not flagged" says nothing: the pair is not tested."""
    assert bool(baseline.channels.loc["CD21", "near_blank"])
    rows = baseline.table[(baseline.table["source"] == "CD21") | (baseline.table["target"] == "CD21")]
    physical = rows[rows["kind"].isin(["same_cycle_spectral", "adjacent_cycle_carryover"])]
    assert len(physical) > 0
    assert (physical["source_near_blank"].astype(bool) | physical["target_near_blank"].astype(bool)).all()
    assert not physical["flag"].astype(bool).any()


def test_baseline_same_cycle_texture_flag_has_both_orientations(baseline):
    """Collagen IV and TOX share a cycle; their match is texture, which has no direction: both rows are flagged."""
    assert {("TOX", "Collagen IV"), ("Collagen IV", "TOX")} <= baseline.flags


def test_leak_pairs_are_testable_in_the_baseline(baseline):
    """R10: a leak into a near-blank channel would be excluded by rule and prove nothing."""
    for source, target in (SAME_CYCLE, CARRYOVER):
        row = baseline.pair(source, target)
        assert row is not None and not row["source_near_blank"] and not row["target_near_blank"]
        assert not row["flag"], (source, target)
        assert not baseline.channels.loc[source, "near_blank"] and not baseline.channels.loc[target, "near_blank"]
    assert baseline.pair(*SAME_CYCLE)["kind"] == "same_cycle_spectral"
    assert baseline.pair(*CARRYOVER)["kind"] == "adjacent_cycle_carryover"
    assert "CD21" not in set(DIM) | {BRIGHT} | set(SAME_CYCLE) | set(CARRYOVER)


# ---------------------------------------------------------------------------- detection faults
def test_dimmed_channels_flagged(detection_run, baseline):
    for m in DIM:                                                          # each scaled x0.12
        assert_call_moved(baseline, detection_run, m, ("RAISE", "CHECK"), scale=0.12)


def test_over_bright_hla_dr_lower(detection_run, baseline):
    assert_call_moved(baseline, detection_run, BRIGHT, ("LOWER",))         # x8, clipped at 255


def test_untouched_channels_keep_call(detection_run, baseline):
    assert {m: c for m, c in detection_run.calls.items() if m not in TOUCHED and c != baseline.calls[m]} == {}


def test_detection_injection_adds_no_crosstalk_flags(detection_run, baseline):
    assert detection_run.flags - baseline.flags == set()


# ---------------------------------------------------------------------------- crosstalk faults
def test_injected_leaks_landed_in_the_numbers(crosstalk_run, baseline):
    """The leaks are visible in the texture match and in the target's signal, so a miss below is the engine's
    sensitivity and not a leak that never applied (the baseline 0.134 -> 0.298 is now a test, not a string)."""
    for pair in (SAME_CYCLE, CARRYOVER):
        assert_leak_landed(baseline, crosstalk_run, pair, margin=0.1)


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "REAL MISS, kept visible on purpose (SUPP-870 Task 18): 10% of ICOS written into MPO raises their texture "
    "match from 0.134 to 0.298, just under the 0.332 cut-off, and their spill asymmetry from -0.005 to 0.055 "
    "(cut-off 0.147), so the same-cycle leak is not flagged. The cut-offs come from the null pairs and were not "
    "changed to make this pass; strict=True turns this into a failure the day the engine catches it, so the "
    "marker must then be removed."))
def test_same_cycle_leak_flagged(crosstalk_run):
    assert SAME_CYCLE in crosstalk_run.flags                               # 10%, CY5 -> ATTO550, cycle 12


def test_same_cycle_reverse_orientation_follows_the_texture_rule(crosstalk_run):
    """Same-cycle pairs are checked both ways. A texture match has no direction, so it flags both rows; a
    spill asymmetry points one way, so it flags only the injected direction.

    A consistency check only: it derives the expected reverse flag from the engine's own flag_reason, so it holds
    whichever way the engine answers, and on this crop it is trivially true because the same-cycle leak is missed
    (no forward flag, no reverse flag, no follow-up panel). The real both-orientations evidence is the baseline
    Collagen IV <-> TOX test above and the synthetic judge, where this pair is flagged."""
    forward, reverse = crosstalk_run.pair(*SAME_CYCLE), crosstalk_run.pair(*SAME_CYCLE[::-1])
    assert forward is not None and reverse is not None
    assert bool(reverse["flag"]) == ("texture" in str(forward["flag_reason"]))
    panels = [f for f in crosstalk_run.followups if {f["source"], f["target"]} == set(SAME_CYCLE)]
    assert len(panels) == (1 if forward["flag"] or reverse["flag"] else 0)   # one follow-up per flagged pair


def test_carryover_flagged(crosstalk_run):
    assert CARRYOVER in crosstalk_run.flags                                # 10%, CY5, cycle 2 -> 3
    assert CARRYOVER[::-1] not in crosstalk_run.flags                      # a carryover keeps its direction


def test_no_new_false_flags(crosstalk_run, baseline):
    allowed = {SAME_CYCLE, SAME_CYCLE[::-1], CARRYOVER}
    assert crosstalk_run.flags - baseline.flags - allowed == set()


def test_no_titration_call_changes_for_channels_not_modified_by_the_leaks(crosstalk_run, baseline):
    modified = {SAME_CYCLE[1], CARRYOVER[1]}
    assert {m: c for m, c in crosstalk_run.calls.items() if m not in modified and c != baseline.calls[m]} == {}


def test_advice_is_directions_only(baseline, detection_run, crosstalk_run):
    for run in (baseline, detection_run, crosstalk_run):
        assert set(run.channels["call"]) <= ADVICE_WORDS and set(run.channels["exposure"]) <= EXPOSURE_WORDS
        assert not any(ch.isdigit() for text in run.channels["next_run"] for ch in str(text))


# ---------------------------------------------------------------------------- last: the example file is untouched
def test_zz_example_file_is_unchanged(crop, checksum_before, crosstalk_run, detection_run):
    assert sha256_file(crop) == checksum_before == EXAMPLE_SHA256


def test_zz_shared_sample_was_not_changed_by_any_run(sample, sample_digest, crosstalk_run, detection_run):
    assert pixels_digest(sample) == sample_digest
