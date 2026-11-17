import json

import numpy as np
import pandas as pd

from phenocycler_qc_eval.detection import detection_table
from phenocycler_qc_eval.previews import (
    preview_indices, safe_name, write_antibody_previews, write_followup_preview,
)
from phenocycler_qc_eval.results import load_preview
from phenocycler_qc_eval.settings import DetectionSettings
from phenocycler_qc_eval.tissue import Sample


def test_safe_name():
    assert safe_name("Collagen IV") == "Collagen_IV" and safe_name("CD45RO/RA") == "CD45RO_RA"
    assert safe_name("Pan-Cytokeratin") == "Pan-Cytokeratin"


def test_antibody_previews_small_and_complete(tmp_path, small_sample, small_det):
    paths = write_antibody_previews(small_sample, small_det, tmp_path, k=6, seed=0)
    p = load_preview(paths[0])
    assert len(paths) == len(small_det) and p["tiles"].shape[0] == 6 and p["tiles"].dtype == np.uint8
    assert max(p["tiles"].shape[1:]) <= 256 and p["hist_counts"].sum() > 0
    assert max(x.stat().st_size for x in paths) < 400_000        # 32 antibodies stay under ~12 MB
    assert paths[0].parent == tmp_path / "previews"


def test_antibody_preview_keys_and_limits(tmp_path, small_sample, small_det):
    paths = write_antibody_previews(small_sample, small_det, tmp_path, k=6, seed=0)
    row = small_det.iloc[0]
    p = load_preview(paths[0])
    assert p["marker"] == row.channel and p["vmin"] == 0.0 and np.isclose(p["vmax"], row.signal_p99_5)
    assert len(p["hist_counts"]) == 64 and len(p["hist_edges"]) == 65
    assert p["hist_edges"][0] == 0 and p["hist_edges"][-1] == small_sample.value_range
    assert np.isclose(p["otsu_threshold"], row.otsu_threshold)


def test_same_windows_for_every_antibody(tmp_path, small_sample, small_det):
    assert (preview_indices(200, 6, 0) == preview_indices(200, 6, 0)).all()
    assert len(set(preview_indices(200, 6, 0))) == 6 and len(preview_indices(4, 6, 0)) == 4
    paths = write_antibody_previews(small_sample, small_det, tmp_path, k=4, seed=3)
    first = load_preview(paths[0])["windows"]
    assert all((load_preview(p)["windows"] == first).all() for p in paths)


def _synthetic(dtype, scale, size=300, n=3):
    rng = np.random.default_rng(0)
    px = (rng.random((n, 2, size, size)) * scale).astype(dtype)
    px[:, 0] = 1                                   # nuclear channel everywhere
    origins = pd.DataFrame({"y0": range(n), "x0": 0, "size_px": size})
    smp = Sample(pixels=px, names=["DAPI", "A"], origins=origins, window_px=size, nuclear_name="DAPI",
                 value_range=float(scale), saturation_checkable=dtype != np.float32, range_basis="test")
    return smp, detection_table(smp, DetectionSettings())


def test_wide_image_tiles_are_uint8_with_stored_range(tmp_path):
    smp, det = _synthetic(np.uint16, 4095, size=200)
    p = load_preview(write_antibody_previews(smp, det, tmp_path, k=3, seed=0)[0])
    assert p["tiles"].dtype == np.uint8 and p["tiles"].max() == 255
    assert p["display_min"] == 0.0 and np.isclose(p["display_max"], det.signal_p99_5.iloc[0])
    assert p["hist_edges"][-1] == 4095


def test_float_image_and_large_windows_downsampled(tmp_path):
    smp, det = _synthetic(np.float32, 1.0, size=600)
    p = load_preview(write_antibody_previews(smp, det, tmp_path, k=3, seed=0)[0])
    assert p["tiles"].dtype == np.uint8 and max(p["tiles"].shape[1:]) <= 256


def test_followup_preview_has_four_panels(tmp_path, leak_sample):
    path = write_followup_preview(leak_sample, "CD20", "CD31", "CD68", {"asymmetry": 0.2}, tmp_path, 6)
    p = load_preview(path)
    assert {"source", "target", "control", "dapi", "numbers"} <= set(p)
    assert (p["source_name"], p["target_name"], p["control_name"]) == ("CD20", "CD31", "CD68")
    assert json.loads(p["numbers"]) == {"asymmetry": 0.2}
    assert p["source"].shape[0] == 6 and p["source"].dtype == np.uint8 and p["control"].shape == p["source"].shape
    assert path.name == "followup_CD20_to_CD31.npz"


def test_followup_control_not_in_image_is_empty(tmp_path, leak_sample):
    p = load_preview(write_followup_preview(leak_sample, "CD20", "CD31", "Collagen IV", {}, tmp_path, 6))
    assert p["control"].size == 0 and p["control_name"] == ""


def test_followup_without_control(tmp_path, leak_sample):
    p = load_preview(write_followup_preview(leak_sample, "CD20", "CD31", None, {}, tmp_path, 6))
    assert p["control"].size == 0 and p["control_name"] == ""


def test_followup_windows_are_where_source_is_brightest(tmp_path, small_sample):
    smp = Sample(pixels=small_sample.pixels.copy(), names=small_sample.names, origins=small_sample.origins,
                 window_px=small_sample.window_px, nuclear_name="DAPI", value_range=255.0,
                 saturation_checkable=True, range_basis="test")
    smp.pixels[2, smp.names.index("CD20")] = 250    # window 2 is far brighter than the rest
    p = load_preview(write_followup_preview(smp, "CD20", "CD31", None, {}, tmp_path, 3))
    assert p["windows"][0] == 2 and len(p["windows"]) == 3


def test_numbers_with_numpy_values_are_stored(tmp_path, leak_sample):
    numbers = {"x": np.float32(1.5), "y": float("nan")}
    p = load_preview(write_followup_preview(leak_sample, "CD20", "CD31", None, numbers, tmp_path, 6))
    assert json.loads(p["numbers"])["x"] == 1.5


def test_histogram_ignores_non_finite_like_detection(tmp_path):
    px = np.full((2, 2, 8, 8), 100.0, dtype=np.float32)
    px[:, 1, :2, :2] = np.nan
    px[:, 1, 2:4, :2] = np.inf
    smp = Sample(pixels=px, names=["DAPI", "A"], origins=pd.DataFrame({"y0": [0, 8], "x0": [0, 8], "size_px": [8, 8]}),
                 window_px=8, nuclear_name="DAPI", value_range=255.0, saturation_checkable=False, range_basis="t")
    det = detection_table(smp, DetectionSettings())
    p = load_preview(write_antibody_previews(smp, det, tmp_path, k=2, seed=0)[0])
    finite = int(np.isfinite(px[:, 1]).sum())
    assert int(p["hist_counts"].sum()) == finite
    assert p["hist_counts"][0] == 0      # non-finite values must not pile up in bin 0


def test_followup_names_a_marker_missing_from_the_image(tmp_path, leak_sample):
    import pytest
    with pytest.raises(ValueError, match="Nope"):
        write_followup_preview(leak_sample, "Nope", "CD31", None, {}, tmp_path, 6)
    with pytest.raises(ValueError, match="Missing"):
        write_followup_preview(leak_sample, "CD20", "Missing", None, {}, tmp_path, 6)
