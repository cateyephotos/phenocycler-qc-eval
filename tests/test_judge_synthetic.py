"""The judge on a synthetic image: known faults are written into real pixels and the engine must catch them.

The same checks run on the tonsil example crop in test_judge_example.py. Nothing here needs a marker: the
image is small and the three engine runs are shared by every test in this file. It is not instant: the module
takes a few minutes (143 s on the lab workstation); shrink n_windows (16) before thinking of marking it slow.
"""

from __future__ import annotations

import pytest
from synth import (
    assert_call_moved, assert_leak_landed, inject, judge_run, make_layout_csv, make_synthetic_image,
    pixels_digest, small_settings,
)

from phenocycler_qc_eval.layout import load_layout
from phenocycler_qc_eval.readers import open_image
from phenocycler_qc_eval.tissue import sample_windows

PANEL = [  # marker, cycle, fluor
    ("CD31", 1, "ATTO550"), ("CD4", 1, "CY5"), ("CD20", 1, "AF750"),
    ("CD44", 2, "ATTO550"), ("FOXP3", 2, "CY5"), ("SMA", 2, "AF750"),
    ("KI67", 4, "ATTO550"), ("CD8", 4, "CY5"), ("CD21", 4, "AF750"),
]
DIM = ["CD20", "CD44", "SMA"]          # each scaled x0.12
BRIGHT = "CD8"                         # x8, clipped at 255
SAME_CYCLE = ("CD20", "CD31")          # 10%, AF750 -> ATTO550, cycle 1
CARRYOVER = ("CD4", "FOXP3")           # 10%, CY5, cycle 1 -> cycle 2
TOUCHED = set(DIM) | {BRIGHT}
ADVICE_WORDS = {"RAISE", "LOWER", "CHECK", "OK"}
EXPOSURE_WORDS = {"SHORTEN", "KEEP", "LENGTHEN"}


@pytest.fixture(scope="module")
def judge_layout(tmp_path_factory):
    return load_layout(make_layout_csv(tmp_path_factory.mktemp("judge_layout") / "layout.csv", PANEL))


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    image = make_synthetic_image(tmp_path_factory.mktemp("judge_image") / "judge.qptiff",
                                 channels=["DAPI"] + [m for m, _, _ in PANEL], size_px=2048)
    with open_image(image) as src:
        return sample_windows(src, small_settings(n_windows=16, min_windows=4).advanced)


@pytest.fixture(scope="module")
def sample_digest(sample):
    return pixels_digest(sample)                                          # taken before the first engine run


@pytest.fixture(scope="module")
def baseline(sample, sample_digest, judge_layout, tmp_path_factory):
    return judge_run(sample, judge_layout, tmp_path_factory.mktemp("judge_baseline"))


@pytest.fixture(scope="module")
def detection_run(sample, judge_layout, tmp_path_factory):
    faulty = inject(sample, scale={**{m: 0.12 for m in DIM}, BRIGHT: 8.0})
    return judge_run(faulty, judge_layout, tmp_path_factory.mktemp("judge_detection"))


@pytest.fixture(scope="module")
def crosstalk_run(sample, judge_layout, tmp_path_factory):
    faulty = inject(sample, leak={SAME_CYCLE: 0.10, CARRYOVER: 0.10})
    return judge_run(faulty, judge_layout, tmp_path_factory.mktemp("judge_crosstalk"))


# ---------------------------------------------------------------------------- the injector itself
def test_inject_scales_leaks_rounds_and_clips_without_touching_the_original(sample):
    import numpy as np

    names = sample.names
    before = sample.pixels.copy()
    out = inject(sample, scale={"CD8": 8.0, "CD20": 0.12}, leak={("CD20", "CD31"): 0.10})
    assert np.array_equal(sample.pixels, before)                         # the input is not modified
    assert out.pixels.dtype == sample.pixels.dtype and out.names == names
    i8, i20, i31, i4 = (names.index(n) for n in ("CD8", "CD20", "CD31", "CD4"))
    assert out.pixels[:, i8].max() == 255 and (out.pixels[:, i8] >= sample.pixels[:, i8]).all()
    want20 = np.clip(np.rint(sample.pixels[:, i20].astype(float) * 0.12), 0, 255)
    assert np.array_equal(out.pixels[:, i20], want20.astype(np.uint8))
    want31 = np.clip(np.rint(sample.pixels[:, i31].astype(float) + 0.10 * sample.pixels[:, i20].astype(float)), 0, 255)
    assert np.array_equal(out.pixels[:, i31], want31.astype(np.uint8))   # the leak uses the ORIGINAL source
    assert np.array_equal(out.pixels[:, i4], sample.pixels[:, i4])       # untouched channels are identical


def test_inject_rejects_a_channel_that_is_not_in_the_sample(sample):
    with pytest.raises(KeyError, match="NOPE"):
        inject(sample, scale={"NOPE": 2.0})
    with pytest.raises(KeyError, match="NOPE"):
        inject(sample, leak={("CD4", "NOPE"): 0.1})


# ---------------------------------------------------------------------------- the judge
def test_baseline_has_no_flags_in_independent_channels(baseline):
    assert baseline.flags == set()


def test_dimmed_channels_flagged(detection_run, baseline):
    for m in DIM:
        assert_call_moved(baseline, detection_run, m, ("RAISE", "CHECK"), scale=0.12)


def test_over_bright_channel_says_lower(detection_run, baseline):
    assert_call_moved(baseline, detection_run, BRIGHT, ("LOWER",))


def test_untouched_channels_keep_call(detection_run, baseline):
    assert {m: c for m, c in detection_run.calls.items() if m not in TOUCHED and c != baseline.calls[m]} == {}


def test_detection_injection_adds_no_crosstalk_flags(detection_run, baseline):
    assert detection_run.flags - baseline.flags == set()


def test_injected_leaks_landed_in_the_numbers(crosstalk_run, baseline):
    """A 10% leak raises the texture match by about 0.09 on this small image (0.16 on the example crop)."""
    for pair in (SAME_CYCLE, CARRYOVER):
        assert_leak_landed(baseline, crosstalk_run, pair, margin=0.05)


def test_same_cycle_leak_flagged(crosstalk_run):
    assert SAME_CYCLE in crosstalk_run.flags


def test_same_cycle_reverse_orientation_follows_the_texture_rule(crosstalk_run):
    """Same-cycle pairs are checked both ways. A texture match has no direction, so it flags both rows; a
    spill asymmetry points one way, so it flags only the injected direction.

    Here the pair really is flagged (asserted first), so the comparison with the engine's own flag_reason is not
    vacuous; on the example crop, where the same leak is missed, it is trivially true."""
    forward, reverse = crosstalk_run.pair(*SAME_CYCLE), crosstalk_run.pair(*SAME_CYCLE[::-1])
    assert forward is not None and reverse is not None and forward["kind"] == "same_cycle_spectral"
    assert bool(forward["flag"]) or bool(reverse["flag"]), "the same-cycle pair must be flagged for this check"
    assert bool(reverse["flag"]) == ("texture" in str(forward["flag_reason"]))
    panels = [f for f in crosstalk_run.followups if {f["source"], f["target"]} == set(SAME_CYCLE)]
    assert len(panels) == (1 if forward["flag"] or reverse["flag"] else 0)   # one follow-up for the pair


def test_carryover_flagged(crosstalk_run):
    assert CARRYOVER in crosstalk_run.flags
    assert crosstalk_run.pair(*CARRYOVER)["kind"] == "adjacent_cycle_carryover"
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


def test_zz_shared_sample_was_not_changed_by_any_run(sample, sample_digest, crosstalk_run, detection_run):
    assert pixels_digest(sample) == sample_digest
