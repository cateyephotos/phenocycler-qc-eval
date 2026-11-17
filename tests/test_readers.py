import numpy as np
import pytest

from phenocycler_qc_eval.readers import (
    NoNuclearChannelError, ReaderError, channel_name_from_description, nuclear_channel, open_image,
)
from synth import make_synthetic_image


def test_qptiff_names_pixel_size_dtype(synthetic_qptiff):
    with open_image(synthetic_qptiff) as src:
        assert src.kind == "qptiff"
        assert len(src.channels) == 3 and src.channels == ["DAPI", "CD31", "CD4"]
        assert abs(src.pixel_size_um - 0.5) < 1e-6 and src.dtype == np.uint8 and src.n_levels == 2
        assert src.shape_yx == (1024, 1024)
        assert src.level_downsample(0) == 1.0 and src.level_downsample(1) == 2.0


def test_read_window_shape(synthetic_qptiff):
    with open_image(synthetic_qptiff) as src:
        assert src.read(0, 100, 300, 50, 250, [0, 2]).shape == (2, 200, 200)
        assert src.read(1, 0, 100, 0, 80, [1]).shape == (1, 100, 80)


def test_read_window_content_matches_channel(synthetic_qptiff):
    with open_image(synthetic_qptiff) as src:
        full = src.read(0, 0, 1024, 0, 1024, [0, 1])
        assert full[0].max() > 100 and full.dtype == np.uint8
        assert not np.array_equal(full[0], full[1])


def test_ome_tiff_round_trip(tmp_path):
    p = make_synthetic_image(tmp_path / "x.ome.tif", kind="ome", channels=["DAPI", "CD3e"], size_px=256)
    with open_image(p) as src:
        assert src.kind == "ome-tiff" and src.channels == ["DAPI", "CD3e"]
        assert abs(src.pixel_size_um - 0.5) < 1e-6 and src.n_levels == 2
        assert src.read(0, 0, 64, 0, 64, [1]).shape == (1, 64, 64)


def test_uint16_dtype_reported(tmp_path):
    p = make_synthetic_image(tmp_path / "w.ome.tif", kind="ome", channels=["DAPI", "CD3e"],
                             size_px=128, dtype="uint16")
    with open_image(p) as src:
        assert src.dtype == np.uint16


def test_no_nuclear_channel_message(tmp_path):
    p = make_synthetic_image(tmp_path / "x.qptiff", channels=["CD3e", "CD20"], size_px=256)
    with open_image(p) as src, pytest.raises(NoNuclearChannelError, match="--nuclear-channel") as e:
        nuclear_channel(src)
    assert e.value.channels == ["CD3e", "CD20"]


def test_nuclear_default_and_override(tmp_path):
    p = make_synthetic_image(tmp_path / "x.qptiff", channels=["Hoechst33342", "CD3e"], size_px=256)
    with open_image(p) as src:
        assert nuclear_channel(src, override="Hoechst33342") == 0
        assert nuclear_channel(src, override="cd3e") == 1
        with pytest.raises(ReaderError, match="Hoechst33342"):
            nuclear_channel(src, override="nope")
    q = make_synthetic_image(tmp_path / "y.qptiff", channels=["CD3e", "DAPI"], size_px=256)
    with open_image(q) as src:
        assert nuclear_channel(src) == 1


def test_xenium_bundle_reads(tmp_path):
    b = make_synthetic_image(tmp_path / "xen", kind="xenium",
                             channels=["DAPI", "ATP1A1/CD45/E-Cadherin", "18S"], size_px=256)
    with open_image(b) as src:
        assert src.kind == "xenium" and src.channels[0] == "DAPI" and abs(src.pixel_size_um - 0.5) < 1e-6
        assert src.read(0, 0, 64, 0, 64, [0, 1]).shape == (2, 64, 64)


def test_unknown_suffix_raises(tmp_path):
    (tmp_path / "a.png").write_bytes(b"x")
    with pytest.raises(ReaderError):
        open_image(tmp_path / "a.png")


def test_missing_path_and_plain_folder_raise(tmp_path):
    with pytest.raises(ReaderError):
        open_image(tmp_path / "nope.qptiff")
    with pytest.raises(ReaderError, match="morphology_focus"):
        open_image(tmp_path)


def test_description_name_rules():
    xml = '<?xml version="1.0" encoding="utf-16"?><R><Name>Opal 520</Name><Biomarker>CD8</Biomarker></R>'
    assert channel_name_from_description(xml, "x") == "CD8"
    assert channel_name_from_description("<R><Name>DAPI</Name><Biomarker/></R>", "x") == "DAPI"
    utf16 = "<R><Biomarker>PD-1</Biomarker></R>".encode("utf-16")
    assert channel_name_from_description(utf16, "x") == "PD-1"
    assert channel_name_from_description("not xml", "Channel 3") == "Channel 3"


def test_xenium_one_file_per_channel(tmp_path):
    names = ["DAPI", "ATP1A1/CD45/E-Cadherin", "18S"]
    b = make_synthetic_image(tmp_path / "xen3", kind="xenium", channels=names, size_px=256,
                             one_file_per_channel=True)
    assert len(list((b / "morphology_focus").glob("*.ome.tif"))) == 3
    with open_image(b) as src:
        assert src.channels == names and src.n_levels == 2
        whole = make_synthetic_image(tmp_path / "xen1", kind="xenium", channels=names, size_px=256)
        with open_image(whole) as ref:
            for c in range(3):
                assert np.array_equal(src.read(0, 0, 64, 0, 64, [c]), ref.read(0, 0, 64, 0, 64, [c]))
        assert src.read(1, 0, 32, 0, 32, [0, 1, 2]).shape == (3, 32, 32)


def test_nuclear_prefix_match_and_exact_wins(tmp_path):
    p = make_synthetic_image(tmp_path / "h.qptiff", channels=["CD3e", "Hoechst33342"], size_px=256)
    with open_image(p) as src:
        assert nuclear_channel(src) == 1
    q = make_synthetic_image(tmp_path / "d.qptiff", channels=["DAPI_old", "CD3e", "DAPI"], size_px=256)
    with open_image(q) as src:
        assert nuclear_channel(src) == 2  # exact name beats an earlier prefix match
