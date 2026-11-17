import json

import numpy as np
import pandas as pd
import pytest

from phenocycler_qc_eval.results import (
    NotAResultsFolder,
    antibody_preview_path,
    followup_figure_path,
    followup_preview_path,
    load_preview,
    load_results,
    safe_name,
)
from synth import PROTO, copy_only


def test_load_prototype_run():
    r = load_results(PROTO)
    assert r.status == "complete" and "DAPI" not in set(r.detection.channel)
    assert r.detection.set_index("channel").loc["CD163", "next_run"] == "Raise concentration"
    assert (r.detection.exposure == "KEEP").all() and r.saturation_checked
    assert r.crosstalk_enabled and len(r.followups) == 5 and r.exclusive_pairs is not None
    assert followup_figure_path(r, "TOX", "CD163").name == "followup_TOX_to_CD163.png"
    assert antibody_preview_path(r, "CD163") is None
    assert r.report is not None and r.incomplete_note is None and r.unmatched == []


def test_detection_has_required_columns():
    cols = set(load_results(PROTO).detection.columns)
    assert {"channel", "call", "exposure", "next_run", "reason", "signal_p99_5",
            "stain_index", "positive_fraction", "saturation_fraction"} <= cols


def test_not_a_results_folder(tmp_path):
    with pytest.raises(NotAResultsFolder):
        load_results(tmp_path)
    with pytest.raises(NotAResultsFolder):
        load_results(tmp_path / "missing")


def test_run_info_only_folder_loads_empty(tmp_path):
    (tmp_path / "run_info.json").write_text('{"status": "running", "crosstalk_state": "pending"}')
    r = load_results(tmp_path)
    assert r.status == "running" and len(r.detection) == 0 and r.crosstalk is None
    assert r.crosstalk_state == "pending" and not r.crosstalk_enabled


def test_corrupt_run_info_is_tolerated(tmp_path):
    run = copy_only(PROTO, tmp_path, ["stain_qc_channels.csv"])
    (run / "run_info.json").write_text("{not json")
    r = load_results(run)
    assert r.status == "complete" and len(r.detection) > 0


def test_nuclear_row_dropped_by_run_info_name(tmp_path):
    run = copy_only(PROTO, tmp_path, ["stain_qc_channels.csv"])
    (run / "run_info.json").write_text('{"status": "complete", "nuclear_channel": "cd31"}')
    assert "CD31" not in set(load_results(run).detection.channel)


def test_status_from_incomplete_marker_without_run_info(tmp_path):
    run = copy_only(PROTO, tmp_path, ["stain_qc_channels.csv"])
    (run / "RUN_INCOMPLETE.txt").write_text("cancelled by user")
    assert load_results(run).status == "cancelled"


def test_exposure_and_next_run_columns_are_kept_when_present(tmp_path):
    run = copy_only(PROTO, tmp_path, ["stain_qc_channels.csv"])
    import pandas as pd

    df = pd.read_csv(run / "stain_qc_channels.csv")
    df["exposure"] = "LENGTHEN"
    df["next_run"] = "custom"
    df.to_csv(run / "stain_qc_channels.csv", index=False)
    det = load_results(run).detection
    assert (det.exposure == "LENGTHEN").all() and (det.next_run == "custom").all()


def test_safe_name():
    assert safe_name("Collagen IV") == "Collagen_IV"
    assert safe_name("CD45RO/RA") == "CD45RO_RA"
    assert safe_name("TCF-1") == "TCF-1"


def test_preview_paths_and_load_preview(tmp_path):
    run = copy_only(PROTO, tmp_path, ["stain_qc_channels.csv"])
    prev = run / "previews"
    prev.mkdir()
    np.savez(
        prev / "antibody_Collagen_IV.npz",
        tiles=np.zeros((2, 4, 4), dtype=np.uint8),
        vmin=0.0, vmax=12.5, otsu_threshold=3.0, marker="Collagen IV",
    )
    np.savez(
        prev / "followup_TOX_to_CD163.npz",
        source=np.zeros((1, 2, 2), dtype=np.uint8), target=np.zeros((1, 2, 2), dtype=np.uint8),
        control=np.zeros((0, 2, 2), dtype=np.uint8), dapi=np.zeros((1, 2, 2), dtype=np.uint8),
        numbers=json.dumps({"asymmetry": 0.1}),
        source_name="TOX", target_name="CD163", control_name="CD68",
    )
    r = load_results(run)
    ab = antibody_preview_path(r, "Collagen IV")
    assert ab is not None and ab.name == "antibody_Collagen_IV.npz"
    assert antibody_preview_path(r, "CD3") is None
    fu = followup_preview_path(r, "TOX", "CD163")
    assert fu is not None and followup_preview_path(r, "CD163", "TOX") is None

    p = load_preview(ab)
    assert p["tiles"].shape == (2, 4, 4) and p["vmax"] == 12.5 and p["marker"] == "Collagen IV"
    q = load_preview(fu)
    assert q["control"].size == 0 and q["control_name"] == "CD68"
    assert json.loads(q["numbers"]) == {"asymmetry": 0.1}


def test_windows_used_column_is_optional(tmp_path):
    run = copy_only(PROTO, tmp_path, ["stain_qc_channels.csv"])
    df = pd.read_csv(run / "stain_qc_channels.csv")
    assert "windows_used" not in df.columns                      # old run folders still load
    base = load_results(run).detection
    df["windows_used"] = 12
    df.to_csv(run / "stain_qc_channels.csv", index=False)
    r = load_results(run)
    assert len(r.detection) == len(base) and list(r.detection["windows_used"].unique()) == [12]


def test_incomplete_note_names_the_cause(tmp_path):
    import json as _json
    for status, phrase, other in (("error", "the run failed before finishing", "cancelled"),
                                  ("cancelled", "the run was cancelled", "failed")):
        run = copy_only(PROTO, tmp_path, ["stain_qc_channels.csv"], folder_name=f"r_{status}")
        (run / "run_info.json").write_text(_json.dumps(
            {"status": status, "partial_windows": 5, "early_of": 14, "crosstalk_state": "pending"}))
        note = load_results(run).incomplete_note
        assert "only 5 of 14 windows" in note and phrase in note.lower() and other not in note.lower().split(";")[-1]


def test_truncated_run_info_is_tolerated(tmp_path):
    run = copy_only(PROTO, tmp_path, ["stain_qc_channels.csv"], folder_name="r_trunc")
    (run / "run_info.json").write_text('{"status": "running", "partial_win')
    r = load_results(run)
    assert len(r.detection) > 0


def test_a_run_that_ended_is_never_shown_as_running(partial_folder):
    """ended= says the run is over although the folder still says running (a hard stop or a dead worker)."""
    from phenocycler_qc_eval.results import load_results

    assert load_results(partial_folder).status == "running"
    r = load_results(partial_folder, ended="cancelled")
    assert r.status == "cancelled" and r.incomplete_note.startswith("Run cancelled")
    assert load_results(partial_folder, ended="error").incomplete_note.startswith("Run failed")


def test_ended_does_not_relabel_a_finished_run(complete_folder):
    from phenocycler_qc_eval.results import load_results

    assert load_results(complete_folder, ended="cancelled").status == "complete"
