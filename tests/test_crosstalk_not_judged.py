"""X1: a panel with too few unrelated channel pairs gets "not judged", never "0 flagged"."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from synth import judge_run
from test_crosstalk import LAYOUT, build, det_of, xt

from phenocycler_qc_eval.advice import NOT_TESTED_FEW_CONTROLS, headline, pair_verdict
from phenocycler_qc_eval.crosstalk import MIN_CONTROL_PAIRS, NOT_JUDGED_MESSAGE, run_crosstalk
from phenocycler_qc_eval.layout import PanelLayout
from phenocycler_qc_eval.results import load_results
from phenocycler_qc_eval.settings import CrosstalkSettings

# CD4 (cycle 1, CY5) leaks strongly into CD31 (cycle 1, ATTO550): an obvious same-cycle leak.
LEAK = [("CD4", "CD31", 0.9)]


def _layout(cycle_of):
    rows = [{"marker": m, "cycle": cycle_of(i, c), "fluor": f, "exposure_ms": np.nan}
            for i, (m, c, f) in enumerate(LAYOUT)]
    return PanelLayout(pd.DataFrame(rows), "test")


def two_cycles():
    return _layout(lambda i, c: 1 if i < 6 else 2)


def one_cycle():
    return _layout(lambda i, c: 1)


@pytest.fixture(scope="module")
def smp_big_leak():
    return build(leaks=LEAK)


def _run(smp, lay):
    return run_crosstalk(smp, det_of(smp), lay, CrosstalkSettings(), 2.0)


@pytest.mark.parametrize("make", [two_cycles, one_cycle], ids=["two_cycles", "all_in_cycle_1"])
def test_too_few_control_pairs_is_not_judged(smp_big_leak, make):
    res = _run(smp_big_leak, make())
    assert res.enabled is False
    assert res.message == NOT_JUDGED_MESSAGE
    assert "0 pair" not in res.message and "flagged" not in res.message.replace("no pair is flagged", "")
    t = res.table
    assert t is not None and not t["flag"].astype(bool).any()
    assert not np.isinf(t["asym_cut"].astype(float)).any()
    physical = t[t["kind"].isin(["same_cycle_spectral", "adjacent_cycle_carryover"])]
    assert len(physical) and (physical["flag_reason"] == "not_judged").all()
    for _, row in physical.iterrows():
        assert pair_verdict(row) == (NOT_TESTED_FEW_CONTROLS, "untested")
        assert pair_verdict(row, short=True)[1] == "untested"


@pytest.mark.parametrize("make", [two_cycles, one_cycle], ids=["two_cycles", "all_in_cycle_1"])
def test_report_csv_and_banner_say_not_judged(smp_big_leak, tmp_path, make):
    out = tmp_path / "run"
    judge_run(smp_big_leak, make(), out)
    report = (out / "STAIN_QC_REPORT.md").read_text(encoding="utf-8")
    assert "could not be judged" in report
    assert NOT_JUDGED_MESSAGE in report
    for bad in ("No channel pairs were flagged", "0 pair(s) flagged", "no pairs flagged"):
        assert bad not in report
    csv = pd.read_csv(out / "stain_qc_crosstalk_pairs.csv")
    assert not csv["flag"].astype(bool).any()
    phys = csv[csv["kind"].isin(["same_cycle_spectral", "adjacent_cycle_carryover"])]
    assert len(phys) and (phys["flag_reason"] == "not_judged").all()
    assert all(pair_verdict(r)[0] == NOT_TESTED_FEW_CONTROLS for _, r in phys.iterrows())
    r = load_results(out)
    assert r.crosstalk_state == "off" and r.crosstalk_not_judged
    text = headline(r).text
    assert "could not be judged" in text
    assert "no pairs flagged" not in text and "0 flagged" not in text


def test_normal_layout_gives_the_same_flags_as_before():
    smp = build(leaks=[("CD20", "CD31", 0.10), ("CD4", "FOXP3", 0.10)])
    res = xt(smp)
    assert res.enabled is True
    t = res.table
    flagged = sorted(map(tuple, t[t["flag"]][["source", "target", "flag_reason"]].values.tolist()))
    # Recorded from the code before X1 on this synthetic panel (30 distinct control pairs).
    assert flagged == [("CD20", "CD31", "asymmetry+texture"), ("CD31", "CD20", "texture"),
                       ("CD4", "FOXP3", "asymmetry+texture")]
    assert t["asym_cut"].iloc[0] == pytest.approx(0.06771104527281413, rel=1e-9)
    assert t["highpass_cut"].iloc[0] == pytest.approx(0.06280925712654231, rel=1e-9)
    assert "3 pair(s) flagged" not in res.message or res.message.startswith("Crosstalk checked")
    assert MIN_CONTROL_PAIRS == 6
