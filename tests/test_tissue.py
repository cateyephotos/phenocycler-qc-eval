import numpy as np
import pandas as pd
import pytest

from phenocycler_qc_eval.readers import NoNuclearChannelError, open_image
from phenocycler_qc_eval.settings import AdvancedSettings
from phenocycler_qc_eval.tissue import Sample, TooLittleTissueError, sample_windows, tissue_mask
from synth import make_synthetic_image


def test_windows_are_mostly_tissue(sampling_qptiff):
    with open_image(sampling_qptiff) as src:
        smp = sample_windows(src, AdvancedSettings(n_windows=30))
    assert smp.pixels.shape[:2] == (30, len(smp.names)) and smp.window_px == round(111.328 / 0.5)
    dapi = smp.pixels[:, smp.names.index("DAPI")]
    assert ((dapi > 0).mean(axis=(1, 2)) > 0.3).all()
    assert list(smp.origins.columns) == ["y0", "x0", "size_px"]
    assert (smp.origins["y0"] % smp.window_px == 0).all()


def test_seed_is_reproducible(sampling_qptiff):
    with open_image(sampling_qptiff) as src:
        a = sample_windows(src, AdvancedSettings(n_windows=10)).origins
        b = sample_windows(src, AdvancedSettings(n_windows=10)).origins
        c = sample_windows(src, AdvancedSettings(n_windows=10, seed=1)).origins
    pd.testing.assert_frame_equal(a, b)
    assert not a.equals(c)


def test_too_little_tissue(tmp_path):
    p = make_synthetic_image(tmp_path / "t.qptiff", channels=["DAPI", "CD3e"], tissue_fraction=0.01)
    with open_image(p) as src, pytest.raises(TooLittleTissueError, match="need at least 20") as e:
        sample_windows(src, AdvancedSettings())
    assert e.value.needed == 20 and e.value.found < 20


def test_blank_image_has_no_tissue(tmp_path):
    p = make_synthetic_image(tmp_path / "b.qptiff", channels=["DAPI", "CD3e"], size_px=2048,
                             tissue_fraction=0.0001)
    with open_image(p) as src, pytest.raises(TooLittleTissueError) as e:
        sample_windows(src, AdvancedSettings())
    assert e.value.found < 20


def test_float_image_range_and_no_saturation(tmp_path):
    p = make_synthetic_image(tmp_path / "f.ome.tif", kind="ome", channels=["DAPI", "CD3e"],
                             dtype="float32", size_px=2048)
    with open_image(p) as src:
        smp = sample_windows(src, AdvancedSettings(n_windows=10))
    assert smp.saturation_checkable is False and smp.value_range > 0
    assert smp.pixels.dtype == np.float32 and "99.99th" in smp.range_basis


def test_uint8_range(sampling_qptiff):
    with open_image(sampling_qptiff) as src:
        smp = sample_windows(src, AdvancedSettings(n_windows=5))
    assert smp.value_range == 255 and smp.saturation_checkable and smp.pixels.dtype == np.uint8


def test_uint16_range_follows_the_data(tmp_path):
    # the synthetic uint16 image reaches 65535, so the range is 16-bit; smaller maxima are checked below
    p = make_synthetic_image(tmp_path / "w.ome.tif", kind="ome", channels=["DAPI", "CD3e"],
                             dtype="uint16", size_px=2048)
    with open_image(p) as src:
        smp = sample_windows(src, AdvancedSettings(n_windows=5))
        assert smp.value_range == 65535 and "16-bit" in smp.range_basis
        from phenocycler_qc_eval import tissue
        v, basis = tissue._integer_range(3000, np.dtype("uint16"))
        assert v == 4095 and "12-bit" in basis
        assert tissue._integer_range(900, np.dtype("uint16"))[0] == 1023
        assert tissue._integer_range(9000, np.dtype("uint16"))[0] == 16383


def test_mask_covers_the_tissue_block(sampling_qptiff):
    with open_image(sampling_qptiff) as src:
        mask, scale = tissue_mask(src, 0)
    assert mask.dtype == bool and abs(scale - 4.97 / 0.5) < 1e-6
    h, w = mask.shape
    assert mask[h // 2, w // 2]                   # the centre is tissue
    assert 0.3 < mask.mean() < 0.9                # the 60% tissue block, grown a little by the dilation


def test_no_nuclear_channel_is_reported(tmp_path):
    p = make_synthetic_image(tmp_path / "n.qptiff", channels=["CD3e", "CD20"], size_px=2048)
    with open_image(p) as src, pytest.raises(NoNuclearChannelError):
        sample_windows(src, AdvancedSettings())
    with open_image(p) as src:
        smp = sample_windows(src, AdvancedSettings(n_windows=5), nuclear="CD3e")
    assert smp.nuclear_name == "CD3e"


def test_progress_is_reported(sampling_qptiff):
    seen = []
    with open_image(sampling_qptiff) as src:
        sample_windows(src, AdvancedSettings(n_windows=4), progress=lambda f, t: seen.append((f, t)))
    assert seen[0][0] == 0.0 and seen[-1][0] == pytest.approx(1.0)
    assert all(0.0 <= f <= 1.0 for f, _ in seen)


def test_sample_round_trips_through_npz(sampling_qptiff, tmp_path):
    with open_image(sampling_qptiff) as src:
        smp = sample_windows(src, AdvancedSettings(n_windows=4))
    out = smp.save(tmp_path / "sample.npz")
    back = Sample.load(out)
    assert np.array_equal(back.pixels, smp.pixels) and back.names == smp.names
    pd.testing.assert_frame_equal(back.origins, smp.origins)
    assert (back.window_px, back.nuclear_name, back.value_range, back.saturation_checkable,
            back.range_basis) == (smp.window_px, smp.nuclear_name, smp.value_range,
                                  smp.saturation_checkable, smp.range_basis)


def test_nan_in_the_nuclear_plane_does_not_blank_the_mask(tmp_path):
    p = make_synthetic_image(tmp_path / "n.ome.tif", kind="ome", channels=["DAPI", "CD3e"],
                             dtype="float32", size_px=2048)
    with open_image(p) as src:
        clean, _ = tissue_mask(src, 0)
        real_read = src.read

        def read_with_nan(level, y0, y1, x0, x1, channels):
            out = real_read(level, y0, y1, x0, x1, channels).copy()
            if 0 in channels:
                out[0, 3, 3] = np.nan
            return out

        src.read = read_with_nan
        mask, _ = tissue_mask(src, 0)
        smp = sample_windows(src, AdvancedSettings(n_windows=5))
    assert mask.sum() > 0 and abs(mask.mean() - clean.mean()) < 0.05
    assert smp.pixels.shape[0] == 5


def test_mask_is_the_same_when_read_in_several_strips(sampling_qptiff, monkeypatch):
    from phenocycler_qc_eval import tissue
    with open_image(sampling_qptiff) as src:
        whole, _ = tissue_mask(src, 0)
        monkeypatch.setattr(tissue, "_STRIP_TARGET_PIXELS", 200_000)  # a few rows per strip
        strips, _ = tissue_mask(src, 0)
    assert np.array_equal(whole, strips)


def test_window_smaller_than_a_pixel_is_an_error(sampling_qptiff):
    with open_image(sampling_qptiff) as src, pytest.raises(ValueError, match="smaller than one pixel"):
        sample_windows(src, AdvancedSettings(window_um=0.1))


def test_save_gives_an_npz_name_and_returns_it(sampling_qptiff, tmp_path):
    with open_image(sampling_qptiff) as src:
        smp = sample_windows(src, AdvancedSettings(n_windows=3))
    out = smp.save(tmp_path / "sample.bak")
    assert out.name == "sample.bak.npz" and out.exists()
    assert Sample.load(out).names == smp.names
    assert smp.save(tmp_path / "again.npz").name == "again.npz"


def test_image_source_exposes_level_shapes(sampling_qptiff):
    with open_image(sampling_qptiff) as src:
        shapes = src.level_shapes
    assert shapes[0] == (2048, 2048) and len(shapes) == 2


def test_early_subset_is_handed_over_and_final_sample_is_unchanged(sampling_qptiff):
    seen = []
    with open_image(sampling_qptiff) as src:
        plain = sample_windows(src, AdvancedSettings(n_windows=12))
        early = sample_windows(src, AdvancedSettings(n_windows=12), early_windows=4,
                               on_early=lambda part, total: seen.append((part, total)))
    assert len(seen) == 1 and seen[0][1] == 12 and len(seen[0][0].pixels) == 4
    np.testing.assert_array_equal(plain.pixels, early.pixels)
    pd.testing.assert_frame_equal(plain.origins, early.origins)
    part = seen[0][0]
    for k in range(4):                       # every early window is one of the final windows, pixel for pixel
        assert any(np.array_equal(part.pixels[k], p) for p in early.pixels)
    assert part.value_range == early.value_range


def test_no_early_callback_when_the_run_is_small(sampling_qptiff):
    seen = []
    with open_image(sampling_qptiff) as src:
        sample_windows(src, AdvancedSettings(n_windows=6), early_windows=6, on_early=lambda p, t: seen.append(t))
    assert seen == []
