import subprocess
import sys
from pathlib import Path

import pytest
from matplotlib.image import imread
from matplotlib.patches import Rectangle

from phenocycler_qc_eval.crosstalk import control_for, followup_numbers, followup_pairs
from phenocycler_qc_eval.figures import crosstalk_figure, detection_figure, write_figures
from test_crosstalk import layout


def _followups(smp, xt):
    out = []
    for s, t in followup_pairs(xt.table):
        ctl = control_for(t, [[t, "CD68"]], smp.names, avoid=(s,))
        out.append(followup_numbers(smp, s, t, ctl))
    return out


@pytest.mark.slow
def test_figures_written(tmp_path, leak_sample, leak_det, xt_result):
    paths = write_figures(leak_sample, leak_det, xt_result, tmp_path, followups=_followups(leak_sample, xt_result))
    assert all(p.exists() and p.stat().st_size > 0 for p in paths)


@pytest.mark.slow
def test_figures_do_not_import_pyplot():
    code = (
        "import sys, pathlib, tempfile\n"
        "from test_crosstalk import build, layout\n"
        "from phenocycler_qc_eval.crosstalk import run_crosstalk\n"
        "from phenocycler_qc_eval.detection import detection_table\n"
        "from phenocycler_qc_eval.figures import write_figures\n"
        "from phenocycler_qc_eval.settings import CrosstalkSettings, DetectionSettings\n"
        "smp = build(leaks=[('CD20', 'CD31', 0.1)])\n"
        "det = detection_table(smp, DetectionSettings())\n"
        "xt = run_crosstalk(smp, det, layout(), CrosstalkSettings(), 2.0)\n"
        "write_figures(smp, det, xt, pathlib.Path(tempfile.mkdtemp()))\n"
        "assert 'matplotlib.pyplot' not in sys.modules\n"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=str(Path(__file__).parent), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.slow
def test_contract_file_names(tmp_path, leak_sample, leak_det, xt_result):
    fu = _followups(leak_sample, xt_result)
    assert fu, "the leak sample should flag at least one pair"
    names = {p.name for p in write_figures(leak_sample, leak_det, xt_result, tmp_path, followups=fu)}
    assert {"stain_qc_detection.png", "stain_qc_crosstalk_asymmetry.png", "stain_qc_intensity_ridges.png"} <= names
    assert {f["figure"] for f in fu} <= names        # exactly the name followup_numbers reports


def test_no_crosstalk_figure_when_off(tmp_path, small_sample, small_det, xt_off):
    names = [p.name for p in write_figures(small_sample, small_det, xt_off, tmp_path)]
    assert "stain_qc_crosstalk_asymmetry.png" not in names
    assert "stain_qc_detection.png" in names


def test_heat_map_rows_are_tall_enough_to_read(leak_sample, xt_result):
    fig = crosstalk_figure(leak_sample, xt_result, layout())
    ax = fig.axes[0]
    n = len(ax.get_xticklabels())
    assert n == 13 and ax.get_position().height * fig.get_figheight() / n >= 0.2   # inches per marker
    assert fig.get_figwidth() == 9.0


def test_heat_map_is_ordered_by_cycle_and_boxes_flagged(leak_sample, xt_result):
    fig = crosstalk_figure(leak_sample, xt_result, layout())
    labels = [t.get_text() for t in fig.axes[0].get_xticklabels()]
    cycles = [int(t.split("(c")[1].rstrip(")")) for t in labels if "(c" in t]
    assert cycles == sorted(cycles) and labels[-1] == "CD3e"       # CD3e is not in the panel file: last
    boxes = [p for p in fig.axes[0].patches if isinstance(p, Rectangle)]
    assert len(boxes) == int(xt_result.table["flag"].sum()) and len(boxes) >= 1


def test_plain_wording_on_crosstalk_figure(leak_sample, xt_result):
    fig = crosstalk_figure(leak_sample, xt_result, layout())
    text = " ".join(t.get_text() for t in fig.texts) + fig.axes[1].get_ylabel()
    assert "bright signal also appears in another channel" in text
    for jargon in ("asymmetry", "spill", "high-pass", "otsu"):
        assert jargon not in text.lower()


def test_detection_figure_labels_every_antibody(small_det):
    labels = {t.get_text() for t in detection_figure(small_det).axes[0].texts}
    assert set(small_det.channel) <= labels


@pytest.mark.slow
def test_figures_are_nine_inches_at_150_dpi(tmp_path, leak_sample, leak_det, xt_result):
    paths = write_figures(leak_sample, leak_det, xt_result, tmp_path, followups=_followups(leak_sample, xt_result))
    for p in paths:
        img = imread(str(p))
        assert img.shape[1] == 1350 and img.shape[0] > 300


@pytest.mark.slow
def test_followup_without_control_still_draws(tmp_path, leak_sample, small_det, xt_result):
    item = followup_numbers(leak_sample, "CD20", "CD31", None)
    names = [p.name for p in write_figures(leak_sample, small_det, xt_result, tmp_path, followups=[item])]
    assert "followup_CD20_to_CD31.png" in names


def test_detection_legend_uses_next_run_wording(small_det):
    from phenocycler_qc_eval.advice import next_run_text
    d = small_det.copy()
    d.loc[d.index[0], ["call", "exposure"]] = ["RAISE", "LENGTHEN"]
    d.loc[d.index[1], ["call", "exposure"]] = ["LOWER", "SHORTEN"]
    d.loc[d.index[2], ["call", "exposure"]] = ["RAISE", "KEEP"]
    legend = detection_figure(d).axes[0].get_legend()
    labels = {t.get_text() for t in legend.get_texts()}
    assert {next_run_text("RAISE", "LENGTHEN"), next_run_text("LOWER", "SHORTEN"),
            next_run_text("RAISE", "KEEP")} <= labels
    assert "Raise concentration (or lengthen exposure)" in labels


def test_followup_figure_shows_the_windows_saved_in_the_preview(tmp_path, leak_sample, monkeypatch):
    import numpy as np
    from phenocycler_qc_eval import figures
    from phenocycler_qc_eval.previews import write_followup_preview
    item = followup_numbers(leak_sample, "CD20", "CD31", None)
    write_followup_preview(leak_sample, "CD20", "CD31", None, item, tmp_path, 3)
    path = tmp_path / "previews" / "followup_CD20_to_CD31.npz"
    with np.load(path) as z:
        data = {k: z[k] for k in z.files}
    data["windows"] = np.array([5, 4, 3])                  # not what a fresh selection would give
    np.savez_compressed(path, **data)
    seen = []
    real = figures.followup_figure
    monkeypatch.setattr(figures, "followup_figure", lambda smp, it, k, idx=None: (seen.append(idx), real(smp, it, k, idx))[1])
    write_figures(leak_sample, leak_det_unused(leak_sample), None, tmp_path, followups=[item], k=3)
    assert list(seen[0]) == [5, 4, 3]


def leak_det_unused(smp):
    from phenocycler_qc_eval.detection import detection_table
    from phenocycler_qc_eval.settings import DetectionSettings
    return detection_table(smp, DetectionSettings())
