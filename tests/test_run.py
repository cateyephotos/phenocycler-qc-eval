import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from phenocycler_qc_eval import run as run_module
from phenocycler_qc_eval.readers import ReaderError
from phenocycler_qc_eval.results import load_results
from phenocycler_qc_eval.run import (
    EARLY_STAGE, INCOMPLETE_MARKER, PARTIAL_STAGE, RunCancelled, RunRequest, find_lone_xpd, make_run_dir, run_qc,
)
from phenocycler_qc_eval.settings import default_settings
from phenocycler_qc_eval.tissue import Sample, TooLittleTissueError
from synth import make_synthetic_image, small_settings, write_xpd


@pytest.mark.slow
def test_run_writes_full_output(finished_run):
    out, summary = finished_run
    for f in ["STAIN_QC_REPORT.md", "stain_qc_channels.csv", "stain_qc_crosstalk_pairs.csv",
              "stain_qc_exclusive_pairs.csv", "stain_qc_summary.json", "run_info.json", "stain_qc_detection.png",
              "stain_qc_spill_matrix.csv", "stain_qc_highpass_corr.csv", "stain_qc_followups.json",
              "stain_qc_intensity_ridges.png", "stain_qc_crosstalk_asymmetry.png"]:
        assert (out / f).exists(), f
    assert not (out / INCOMPLETE_MARKER).exists()
    assert len(list((out / "previews").glob("antibody_*.npz"))) == len(pd.read_csv(out / "stain_qc_channels.csv"))
    assert load_results(out).status == "complete"


@pytest.mark.slow
def test_injected_carryover_gets_followup(finished_run):
    out, summary = finished_run
    assert any(f["source"] == "CD4" and f["target"] == "FOXP3" for f in summary["flagged_crosstalk"])
    assert (out / "previews" / "followup_CD4_to_FOXP3.npz").exists()
    assert (out / "followup_CD4_to_FOXP3.png").exists()
    r = load_results(out)
    assert any(f["source"] == "CD4" and f["target"] == "FOXP3" for f in r.followups)


@pytest.mark.slow
def test_summary_keys_and_files_agree(finished_run):
    out, summary = finished_run
    on_disk = json.loads((out / "stain_qc_summary.json").read_text())
    assert on_disk["calls"] == summary["calls"] and summary["crosstalk_enabled"] is True
    for key in ("calls", "raise", "lower", "check", "flagged_crosstalk", "crosstalk_enabled",
                "crosstalk_message", "unmatched", "saturation_checked"):
        assert key in summary
    assert sum(summary["calls"].values()) == len(pd.read_csv(out / "stain_qc_channels.csv"))


@pytest.mark.slow
def test_report_headline_is_the_banner_sentence(finished_run):
    from phenocycler_qc_eval.advice import headline
    out, _ = finished_run
    r = load_results(out)
    lines = [l for l in (out / "STAIN_QC_REPORT.md").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert lines[1:3] == headline(r).text.splitlines()


@pytest.mark.slow
def test_run_info_records_the_run(finished_run):
    out, _ = finished_run
    info = json.loads((out / "run_info.json").read_text())
    assert info["status"] == "complete" and info["crosstalk_state"] == "on"
    assert info["image_name"] == "full.qptiff" and info["nuclear_channel"] == "DAPI"
    assert info["intensity_range"] == 255.0 and info["saturation_checked"] is True
    assert info["unmatched"] == [] and info["settings"]["advanced"]["n_windows"] == 24
    assert info["version"] and info["started_at"] and info["finished_at"]
    r = load_results(out)
    assert r.image_name == "full.qptiff" and r.intensity_range == 255.0 and r.crosstalk_enabled


@pytest.mark.slow
def test_report_has_range_and_real_figure_links(finished_run):
    out, _ = finished_run
    text = (out / "STAIN_QC_REPORT.md").read_text(encoding="utf-8")
    assert "0 to 255" in text
    assert "(followup_CD4_to_FOXP3.png)" in text      # a real link: the figure was written before the report


def test_antibody_results_exist_before_crosstalk(synthetic_qptiff, tmp_path):
    seen = {}

    def progress(stage, frac, msg):
        if stage == PARTIAL_STAGE:
            out = next((tmp_path / "out").iterdir())
            seen["channels"] = (out / "stain_qc_channels.csv").exists()
            seen["previews"] = any((out / "previews").glob("antibody_*.npz"))
            seen["crosstalk"] = (out / "stain_qc_crosstalk_pairs.csv").exists()
            seen["exclusive"] = (out / "stain_qc_exclusive_pairs.csv").exists()
            seen["loadable"] = load_results(out).status == "running"
    run_qc(RunRequest(synthetic_qptiff, None, (tmp_path / "out"), small_settings(6, 3)), progress)
    assert seen == {"channels": True, "previews": True, "crosstalk": False, "exclusive": False, "loadable": True}


def test_new_subfolder_each_run(tmp_path):
    t = datetime(2026, 11, 11, 9, 0, 0)
    a = make_run_dir(tmp_path, Path("x.qptiff"), t)
    b = make_run_dir(tmp_path, Path("x.qptiff"), t)
    c = make_run_dir(tmp_path, Path("x.qptiff"), t)
    assert (a.name, b.name, c.name) == ("x_20261111-090000", "x_20261111-090000_2", "x_20261111-090000_3")
    assert a.is_dir() and b.is_dir()


def test_run_name_strips_image_extensions(tmp_path):
    t = datetime(2026, 11, 11, 9, 0, 0)
    assert make_run_dir(tmp_path, Path("scan.ome.tif"), t).name == "scan_20261111-090000"
    assert make_run_dir(tmp_path, Path("bundle_folder"), t).name == "bundle_folder_20261111-090000"


@pytest.mark.slow
def test_second_run_never_overwrites_the_first(synthetic_qptiff, tmp_path):
    out1, _ = run_qc(RunRequest(synthetic_qptiff, None, tmp_path, small_settings(6, 3)))
    keep = out1 / "keep_me.txt"
    keep.write_text("first run")
    out2, _ = run_qc(RunRequest(synthetic_qptiff, None, tmp_path, small_settings(6, 3)))
    assert out1 != out2 and keep.read_text() == "first run"
    assert len([p for p in tmp_path.iterdir() if p.is_dir()]) == 2


@pytest.mark.slow
def test_failure_leaves_marker(tmp_path, monkeypatch, synthetic_qptiff):
    def boom(*a, **k):
        raise RuntimeError("disk exploded")

    monkeypatch.setattr("phenocycler_qc_eval.run.analyze_sample", boom)
    with pytest.raises(RuntimeError):
        run_qc(RunRequest(synthetic_qptiff, None, (tmp_path / "out"), small_settings(6, 3)))
    out = next((tmp_path / "out").iterdir())
    assert (out / INCOMPLETE_MARKER).exists() and load_results(out).status == "error"
    assert "disk exploded" in (out / INCOMPLETE_MARKER).read_text()
    assert json.loads((out / "run_info.json").read_text())["error"] == "disk exploded"


@pytest.mark.slow
def test_progress_is_monotonic(synthetic_qptiff, tmp_path):
    seen = []
    run_qc(RunRequest(synthetic_qptiff, None, tmp_path, small_settings(6, 3)), lambda s, f, m: seen.append(f))
    assert seen == sorted(seen) and seen[-1] == 1.0 and seen[0] <= 0.05


@pytest.mark.slow
def test_folder_loads_at_every_stage(synthetic_qptiff, tmp_path):
    """Running before and after the antibody results, then complete."""
    log = []

    def progress(stage, frac, msg):
        out = next((tmp_path / "out").iterdir())
        r = load_results(out)
        log.append((stage, r.status, len(r.detection), r.crosstalk_state, (out / INCOMPLETE_MARKER).exists()))
    out, _ = run_qc(RunRequest(synthetic_qptiff, None, (tmp_path / "out"), small_settings(6, 3)), progress)
    assert log[0][:4] == ("open", "running", 0, "off")                               # nothing analyzed yet; no panel, so no crosstalk to wait for
    after = [e for e in log if e[0] == "crosstalk"]
    assert after and all(e[1] == "running" and e[2] == 2 for e in after)             # CD31 and CD4
    assert all(e[4] for e in log[:-1])                                               # marker stays to the end
    final = load_results(out)
    assert final.status == "complete" and not (out / INCOMPLETE_MARKER).exists()
    assert final.crosstalk_state == "off" and "no panel file" in final.crosstalk_message


@pytest.mark.slow
def test_cancelled_run_is_still_readable(synthetic_qptiff, tmp_path):
    def progress(stage, frac, msg):
        if stage == "crosstalk":
            raise RunCancelled()
    with pytest.raises(RunCancelled):
        run_qc(RunRequest(synthetic_qptiff, None, (tmp_path / "out"), small_settings(6, 3)), progress)
    out = next((tmp_path / "out").iterdir())
    r = load_results(out)
    assert r.status == "cancelled" and len(r.detection) == 2           # antibody results survive
    assert (out / INCOMPLETE_MARKER).exists() and r.incomplete_note.startswith("Run cancelled")
    assert list((out / "previews").glob("antibody_*.npz"))


@pytest.mark.slow
def test_early_partial_from_first_windows(sampling_qptiff, tmp_path, monkeypatch):
    notes = []

    def progress(stage, frac, msg):
        if stage in (EARLY_STAGE, PARTIAL_STAGE):
            out = next(tmp_path.glob("early/*"))
            r = load_results(out)
            notes.append((msg, r.incomplete_note, len(r.detection)))
    settings = small_settings(14, 4)
    monkeypatch.setattr(run_module, "EARLY_WINDOWS", 5)
    out1, _ = run_qc(RunRequest(sampling_qptiff, None, tmp_path / "early", settings), progress)
    assert len(notes) == 2                                                    # early set, then all windows
    assert notes[0][0] == "Early antibody results from 5 of 14 windows"
    assert notes[0][1] == "Early antibody results from 5 of 14 windows. Reading the rest..."
    assert notes[0][2] == 2 and notes[1][0] == "Antibody results are ready"
    monkeypatch.setattr(run_module, "EARLY_WINDOWS", 0)
    out2, _ = run_qc(RunRequest(sampling_qptiff, None, tmp_path / "plain", settings))
    pd.testing.assert_frame_equal(pd.read_csv(out1 / "stain_qc_channels.csv"),
                                  pd.read_csv(out2 / "stain_qc_channels.csv"))
    info = json.loads((out1 / "run_info.json").read_text())
    assert "partial_windows" not in info and info["status"] == "complete"


def test_too_little_tissue_stops_before_analysis(tmp_path):
    img = make_synthetic_image(tmp_path / "t.qptiff", channels=["DAPI", "CD3e"], tissue_fraction=0.01)
    with pytest.raises(TooLittleTissueError, match=r"found \d+ tissue windows, need at least 20") as e:
        run_qc(RunRequest(img, None, tmp_path / "out", default_settings()))
    assert e.value.needed == 20 and e.value.found < 20
    out = next((tmp_path / "out").iterdir())
    assert not (out / "stain_qc_channels.csv").exists() and load_results(out).status == "error"


def test_unreadable_panel_file_is_a_reader_error(synthetic_qptiff, tmp_path):
    bad = tmp_path / "panel.xpd"
    bad.write_text("not json")
    with pytest.raises(ReaderError, match="panel.xpd"):
        run_qc(RunRequest(synthetic_qptiff, bad, tmp_path / "out", small_settings(6, 3)))


@pytest.mark.slow
def test_save_sample_writes_sample_npz(synthetic_qptiff, tmp_path):
    out, _ = run_qc(RunRequest(synthetic_qptiff, None, tmp_path, small_settings(6, 3), save_sample=True))
    smp = Sample.load(out / "sample.npz")
    assert smp.names == ["DAPI", "CD31", "CD4"] and len(smp.pixels) > 0
    plain, _ = run_qc(RunRequest(synthetic_qptiff, None, tmp_path, small_settings(6, 3)))
    assert not (plain / "sample.npz").exists()


@pytest.mark.slow
def test_settings_warnings_reach_the_report(synthetic_qptiff, tmp_path):
    out, _ = run_qc(RunRequest(synthetic_qptiff, None, tmp_path, small_settings(6, 3),
                               settings_warnings=["Setting 'seed' was bad; using 0."]))
    assert "Setting 'seed' was bad; using 0." in (out / "STAIN_QC_REPORT.md").read_text(encoding="utf-8")
    assert json.loads((out / "run_info.json").read_text())["settings_warnings"]


@pytest.mark.slow
def test_unmatched_panel_markers_are_recorded(synthetic_qptiff, tmp_path):
    panel = write_xpd(tmp_path / "p.xpd", [("A1", [("CD31", "ATTO550"), ("CD4", "CY5")]),
                                           ("A2", [("GhostMarker", "ATTO550")])])
    out, summary = run_qc(RunRequest(synthetic_qptiff, panel, tmp_path / "out", small_settings(6, 3)))
    assert summary["unmatched"] == ["GhostMarker"]
    assert load_results(out).unmatched == ["GhostMarker"]


# ---------------------------------------------------------------------------- the one lone-.xpd lookup (R16)
def test_find_lone_xpd(tmp_path):
    img = tmp_path / "a.qptiff"
    img.touch()
    assert find_lone_xpd(img) is None
    (tmp_path / "p.xpd").touch()
    assert find_lone_xpd(img) == tmp_path / "p.xpd"
    (tmp_path / "q.xpd").touch()
    assert find_lone_xpd(img) is None                      # two panel files: never guess


def test_find_lone_xpd_looks_beside_a_bundle_folder(tmp_path):
    bundle = tmp_path / "xenium_output"
    (bundle / "morphology_focus").mkdir(parents=True)
    (tmp_path / "panel.xpd").touch()
    assert find_lone_xpd(bundle) == tmp_path / "panel.xpd"


def test_importing_run_does_not_load_the_engine():
    code = ("import sys, phenocycler_qc_eval.run\n"
            "bad = [m for m in ('scipy', 'skimage', 'matplotlib', 'numpy') if m in sys.modules]\n"
            "assert not bad, bad\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ---- fix round 1 (review of Task 13) ----------------------------------------------------------
@pytest.mark.slow
def test_early_and_final_stages_are_separate_and_fire_once_each(sampling_qptiff, tmp_path, monkeypatch):
    assert EARLY_STAGE != PARTIAL_STAGE
    stages = []
    monkeypatch.setattr(run_module, "EARLY_WINDOWS", 5)
    run_qc(RunRequest(sampling_qptiff, None, tmp_path / "o", small_settings(14, 4)),
           lambda s, f, m: stages.append(s))
    assert stages.count(EARLY_STAGE) == 1 and stages.count(PARTIAL_STAGE) == 1
    assert stages.index(EARLY_STAGE) < stages.index(PARTIAL_STAGE)


@pytest.mark.slow
def test_windows_used_column_early_and_final(sampling_qptiff, tmp_path, monkeypatch):
    seen = []

    def progress(stage, frac, msg):
        if stage == EARLY_STAGE:
            seen.append(pd.read_csv(next(tmp_path.glob("o/*")) / "stain_qc_channels.csv")["windows_used"].unique())
    monkeypatch.setattr(run_module, "EARLY_WINDOWS", 5)
    out, _ = run_qc(RunRequest(sampling_qptiff, None, tmp_path / "o", small_settings(14, 4)), progress)
    assert list(seen[0]) == [5]
    assert list(pd.read_csv(out / "stain_qc_channels.csv")["windows_used"].unique()) == [14]


@pytest.mark.slow
def test_cancelled_after_early_says_results_are_partial(sampling_qptiff, tmp_path, monkeypatch):

    def progress(stage, frac, msg):
        if stage == EARLY_STAGE:
            raise RunCancelled()
    monkeypatch.setattr(run_module, "EARLY_WINDOWS", 5)
    with pytest.raises(RunCancelled):
        run_qc(RunRequest(sampling_qptiff, None, tmp_path / "o", small_settings(14, 4)), progress)
    r = load_results(next((tmp_path / "o").iterdir()))
    assert r.status == "cancelled"
    assert "only 5 of 14 windows" in r.incomplete_note and "the run was cancelled" in r.incomplete_note
    assert "complete" not in r.incomplete_note.split(":", 1)[1]


@pytest.mark.slow
def test_on_started_gets_the_run_folder_first(synthetic_qptiff, tmp_path):
    got, order = [], []
    req = RunRequest(synthetic_qptiff, None, tmp_path / "o", small_settings(6, 3),
                     on_started=lambda p: (got.append(p), order.append("started")))
    run_qc(req, lambda s, f, m: order.append(s))
    assert order[0] == "started" and got[0].is_dir() and got[0].parent == tmp_path / "o"


@pytest.mark.slow
def test_antibody_rewrite_is_atomic(synthetic_qptiff, tmp_path, monkeypatch):
    import os
    replaced = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (replaced.append(Path(b).name), real(a, b))[1])
    out, _ = run_qc(RunRequest(synthetic_qptiff, None, tmp_path / "o", small_settings(6, 3)))
    assert "stain_qc_channels.csv" in replaced
    first_csv = replaced.index("stain_qc_channels.csv")
    assert any(n.startswith("antibody_") for n in replaced[:first_csv])      # previews first, CSV last
    assert not list(out.rglob("*.tmp")) and not list(out.glob("previews/.tmp*"))


# ---- Task 15 extras (from the Task 13 re-review) -------------------------------------------------
@pytest.mark.parametrize("which", ["csv", "preview"])
def test_failed_antibody_write_leaves_no_temp_files(small_sample, tmp_path, monkeypatch, which):
    import os
    real = os.replace
    first = [n for n in small_sample.names if n != small_sample.nuclear_name][0]
    fail_on = "stain_qc_channels.csv" if which == "csv" else f"antibody_{first}.npz"

    def flaky(src, dst):
        if Path(dst).name == fail_on:
            raise OSError("disk full")
        return real(src, dst)
    monkeypatch.setattr(os, "replace", flaky)
    with pytest.raises(OSError):
        run_module._write_antibody_results(small_sample, small_settings(6, 3), tmp_path)
    assert not list(tmp_path.rglob("*.tmp")) and not list(tmp_path.rglob(".tmp_*"))


@pytest.mark.slow
def test_raising_on_started_marks_the_run_as_error(synthetic_qptiff, tmp_path):
    def boom(folder):
        raise RuntimeError("callback broke")
    with pytest.raises(RuntimeError, match="callback broke"):
        run_qc(RunRequest(synthetic_qptiff, None, tmp_path / "o", small_settings(6, 3), on_started=boom))
    out = next((tmp_path / "o").iterdir())
    assert load_results(out).status == "error"
    assert "callback broke" in (out / INCOMPLETE_MARKER).read_text()


@pytest.mark.slow
def test_early_stage_is_skipped_when_windows_fit_in_the_early_set(sampling_qptiff, tmp_path, monkeypatch):
    stages = []
    monkeypatch.setattr(run_module, "EARLY_WINDOWS", 14)         # exactly as many windows as the run uses
    run_qc(RunRequest(sampling_qptiff, None, tmp_path / "o", small_settings(14, 4)),
           lambda s, f, m: stages.append(s))
    assert EARLY_STAGE not in stages and stages.count(PARTIAL_STAGE) == 1


@pytest.mark.slow
def test_a_gui_saved_setting_reaches_the_report(tmp_path):
    """Settings saved the way the Settings panel saves them, loaded the way a run loads them, are in the report."""
    from phenocycler_qc_eval.settings import load_settings, save_settings
    s = small_settings()
    s.advanced.window_um = 90.0
    save_settings(s, tmp_path / "settings.json")
    loaded, warnings = load_settings(tmp_path / "settings.json")
    assert warnings == [] and loaded.advanced.window_um == 90.0
    image = make_synthetic_image(tmp_path / "x.qptiff", channels=["DAPI", "CD31", "CD4"])
    out, _summary = run_qc(RunRequest(image, None, tmp_path / "o", loaded))
    text = (out / "STAIN_QC_REPORT.md").read_text(encoding="utf-8")
    assert "Window size (micrometers) (window_um): 90.0" in text
