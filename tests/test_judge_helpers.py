"""The judge's own guard rails (Task 18 review Minors 2, 3, 9): small, fast, no engine run.

A judge that can pass for the wrong reason is worse than none, so the checks it relies on are tested here
against stand-in runs that are built to fail them.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from synth import assert_call_moved, assert_leak_landed, pixels_digest


class FakeRun:
    """Just the parts of a JudgeRun the guard rails read."""

    def __init__(self, texture: float, signal: float, calls: dict[str, str]) -> None:
        self._row = pd.Series({"source": "A", "target": "B", "highpass_corr": texture, "flag": False})
        self.channels = pd.DataFrame({"signal_p99_5": [signal, signal]}, index=["B", "CD20"])
        self.calls = calls

    def pair(self, source: str, target: str):
        return self._row if (source, target) == ("A", "B") else None


def test_leak_landed_passes_when_texture_and_signal_rose():
    assert_leak_landed(FakeRun(0.13, 35.0, {}), FakeRun(0.30, 38.0, {}), ("A", "B"), margin=0.1)


def test_leak_landed_fails_when_the_leak_did_not_apply():
    with pytest.raises(AssertionError, match="texture"):
        assert_leak_landed(FakeRun(0.13, 35.0, {}), FakeRun(0.14, 35.0, {}), ("A", "B"), margin=0.1)


def test_leak_landed_fails_when_the_target_did_not_get_brighter():
    with pytest.raises(AssertionError, match="signal"):
        assert_leak_landed(FakeRun(0.13, 35.0, {}), FakeRun(0.30, 35.0, {}), ("A", "B"), margin=0.1)


def test_leak_landed_fails_for_a_pair_missing_from_the_table():
    with pytest.raises(AssertionError, match="not in the table"):
        assert_leak_landed(FakeRun(0.13, 35.0, {}), FakeRun(0.30, 38.0, {}), ("A", "Z"), margin=0.1)


def test_call_moved_passes_when_the_baseline_was_different():
    assert_call_moved(FakeRun(0, 0, {"CD20": "OK"}), FakeRun(0, 0, {"CD20": "RAISE"}), "CD20", ("RAISE", "CHECK"))


def test_call_moved_fails_when_the_baseline_already_had_that_call():
    """Otherwise the check would pass without the fault doing anything."""
    with pytest.raises(AssertionError, match="already"):
        assert_call_moved(FakeRun(0, 0, {"CD20": "RAISE"}), FakeRun(0, 0, {"CD20": "RAISE"}), "CD20",
                          ("RAISE", "CHECK"))


def test_call_moved_accepts_an_unchanged_call_only_when_the_injected_scale_shows_in_the_signal():
    base, faulty = FakeRun(0, 40.0, {"CD20": "CHECK"}), FakeRun(0, 5.0, {"CD20": "CHECK"})
    assert_call_moved(base, faulty, "CD20", ("RAISE", "CHECK"), scale=0.12)            # 40 -> 5 is about x0.12
    with pytest.raises(AssertionError, match="signal went"):
        assert_call_moved(base, FakeRun(0, 40.0, {"CD20": "CHECK"}), "CD20", ("RAISE", "CHECK"), scale=0.12)


def test_call_moved_fails_when_the_fault_was_not_caught():
    with pytest.raises(AssertionError, match="after"):
        assert_call_moved(FakeRun(0, 0, {"CD20": "OK"}), FakeRun(0, 0, {"CD20": "OK"}), "CD20", ("RAISE", "CHECK"))


def test_pixels_digest_changes_with_one_pixel_and_not_with_a_copy():
    px = np.arange(24, dtype=np.uint8).reshape(2, 3, 2, 2)
    smp = SimpleNamespace(pixels=px)
    same = SimpleNamespace(pixels=px.copy())
    other = px.copy()
    other[0, 0, 0, 0] += 1
    assert pixels_digest(smp) == pixels_digest(same)
    assert pixels_digest(smp) != pixels_digest(SimpleNamespace(pixels=other))
