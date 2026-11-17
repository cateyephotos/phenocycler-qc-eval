"""Review fix pass 2: the worker keeps talking during every long step (no silent stretch of 40 seconds).

The 26 to 40 second stall at "Reading window 50 of 61" was the worker, not the window: it scored the first 50
windows inside the reading loop and said nothing until it was done. These tests pin that every long step reports
progress as it goes.
"""

from __future__ import annotations

import pytest
from synth import make_layout_csv, small_settings

from phenocycler_qc_eval import run as run_module
from phenocycler_qc_eval.run import EARLY_STAGE, PARTIAL_STAGE, RunRequest, run_qc

pytestmark = pytest.mark.slow


def _run(sampling_qptiff, tmp_path, monkeypatch, with_panel=True):
    events = []
    monkeypatch.setattr(run_module, "EARLY_WINDOWS", 5)
    panel = make_layout_csv(tmp_path / "layout.csv", [("CD31", 1, "ATTO550"), ("CD4", 1, "CY5")]) if with_panel else None
    run_qc(RunRequest(sampling_qptiff, panel, tmp_path / "out", small_settings(14, 4)),
           lambda s, f, m: events.append((s, f, m)))
    return events


def test_early_scoring_is_announced_before_the_early_results(sampling_qptiff, tmp_path, monkeypatch):
    events = _run(sampling_qptiff, tmp_path, monkeypatch)
    stages = [s for s, _f, _m in events]
    assert "early_scoring" in stages
    assert stages.index("early_scoring") < stages.index(EARLY_STAGE)
    early = [m for s, _f, m in events if s == "early_scoring"]
    assert early[0].startswith("Early look: scoring the first") and any("2 of 2" in m for m in early)


def test_final_scoring_reports_each_antibody(sampling_qptiff, tmp_path, monkeypatch):
    events = _run(sampling_qptiff, tmp_path, monkeypatch)
    scoring = [m for s, _f, m in events if s == "detection"]
    assert any("1 of 2" in m for m in scoring) and any("2 of 2" in m for m in scoring)
    assert [s for s, _f, _m in events].index("detection") < [s for s, _f, _m in events].index(PARTIAL_STAGE)


def test_crosstalk_reports_windows_and_figures_report_each_picture(sampling_qptiff, tmp_path, monkeypatch):
    events = _run(sampling_qptiff, tmp_path, monkeypatch)
    crosstalk = [m for s, _f, m in events if s == "crosstalk"]
    assert any("window" in m and " of " in m for m in crosstalk)
    figures = [m for s, _f, m in events if s == "figures"]
    assert any(" of " in m for m in figures) and len(figures) >= 2


def test_progress_fraction_never_goes_backwards(sampling_qptiff, tmp_path, monkeypatch):
    fractions = [f for _s, f, _m in _run(sampling_qptiff, tmp_path, monkeypatch)]
    assert fractions == sorted(fractions) and fractions[-1] == 1.0


def test_no_panel_run_still_reports_scoring_and_figures(sampling_qptiff, tmp_path, monkeypatch):
    events = _run(sampling_qptiff, tmp_path, monkeypatch, with_panel=False)
    assert any(s == "detection" and " of " in m for s, _f, m in events)
    assert any(s == "figures" and " of " in m for s, _f, m in events)
