"""Example image: the crop script, the download, the CLI flag, the worker action, Try the example."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from phenocycler_qc_eval import example
from phenocycler_qc_eval.example import (
    EXAMPLE_URL, download, download_example, example_dir, example_panel, manual_instructions,
)
from phenocycler_qc_eval.readers import open_image

REPO = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------- the crop script
def test_crop_round_trip(tmp_path, synthetic_qptiff):
    crop_to_ome = _load_script("make_example_crop").crop_to_ome
    with open_image(synthetic_qptiff) as src:
        out = crop_to_ome(src, 0, 0, 512, tmp_path / "c.ome.tif")
        names = src.channels
    with open_image(out) as c:
        assert c.channels == names and c.shape_yx == (512, 512) and abs(c.pixel_size_um - 0.5) < 1e-6
        assert c.dtype.name == "uint8" and c.n_levels == 2


def test_crop_keeps_the_pixels(tmp_path, synthetic_qptiff):
    import numpy as np

    crop_to_ome = _load_script("make_example_crop").crop_to_ome
    with open_image(synthetic_qptiff) as src:
        out = crop_to_ome(src, 100, 200, 300, tmp_path / "c.ome.tif")
        want = src.read(0, 100, 400, 200, 500, list(range(len(src.channels))))
    with open_image(out) as c:
        assert np.array_equal(c.read(0, 0, 300, 0, 300, list(range(len(c.channels)))), want)


def test_crop_is_deterministic(tmp_path, synthetic_qptiff):
    mod = _load_script("make_example_crop")
    with open_image(synthetic_qptiff) as src:
        a = mod.crop_to_ome(src, 0, 0, 256, tmp_path / "a.ome.tif")
        b = mod.crop_to_ome(src, 0, 0, 256, tmp_path / "b.ome.tif")
    assert mod.sha256_of(a) == mod.sha256_of(b)


def test_crop_outside_the_image_is_refused(tmp_path, synthetic_qptiff):
    crop_to_ome = _load_script("make_example_crop").crop_to_ome
    with open_image(synthetic_qptiff) as src:
        with pytest.raises(ValueError, match="outside"):
            crop_to_ome(src, 5000, 0, 100, tmp_path / "c.ome.tif")


# ---------------------------------------------------------------------------- download
def test_download_rejects_bad_hash(tmp_path, local_http_file):
    with pytest.raises(ValueError, match="sha256"):
        download(local_http_file.url, tmp_path / "x", "0" * 64)
    assert not (tmp_path / "x").exists() and not (tmp_path / "x.part").exists()


def test_download_reports_progress_and_checks_the_hash(tmp_path, local_http_file):
    seen = []
    out = download(local_http_file.url, tmp_path / "x", local_http_file.sha256,
                   progress=lambda d, t: seen.append((d, t)))
    assert out.read_bytes() == local_http_file.path.read_bytes()
    assert seen and seen[-1][0] == local_http_file.path.stat().st_size == seen[-1][1]


def test_download_accepts_a_local_file(tmp_path, local_http_file):
    out = download(str(local_http_file.path), tmp_path / "x", local_http_file.sha256)
    assert out.exists()
    out2 = download(local_http_file.path.as_uri(), tmp_path / "y", local_http_file.sha256)
    assert out2.exists()


def test_download_failure_leaves_nothing_behind(tmp_path, local_http_file):
    url = local_http_file.url
    local_http_file.stop()
    with pytest.raises(OSError):
        download(url, tmp_path / "x", local_http_file.sha256)
    assert list(tmp_path.glob("x*")) == []


def test_download_example_skips_when_present(tmp_path, monkeypatch, local_http_file):
    monkeypatch.setenv("PQE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(example, "EXAMPLE_URL", local_http_file.url)
    monkeypatch.setattr(example, "EXAMPLE_SHA256", local_http_file.sha256)
    a, panel = download_example()
    local_http_file.stop()
    b, _ = download_example()
    assert a == b and panel.suffix == ".xpd" and panel.exists()


def test_download_example_replaces_a_damaged_copy(tmp_path, monkeypatch, local_http_file):
    monkeypatch.setenv("PQE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(example, "EXAMPLE_URL", local_http_file.url)
    monkeypatch.setattr(example, "EXAMPLE_SHA256", local_http_file.sha256)
    bad = example.example_image_path()
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"half a file")
    image, _ = download_example()
    assert image.read_bytes() == local_http_file.path.read_bytes()


def test_download_example_from_a_local_file(tmp_path, monkeypatch, local_http_file):
    monkeypatch.setenv("PQE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(example, "EXAMPLE_URL", "http://127.0.0.1:9/never")
    monkeypatch.setattr(example, "EXAMPLE_SHA256", local_http_file.sha256)
    image, _ = download_example(source=str(local_http_file.path))
    assert image.parent == example_dir() and image.exists()
    monkeypatch.setenv("PQE_EXAMPLE_SOURCE", str(local_http_file.path))
    image.unlink()
    assert download_example()[0].exists()


def test_manual_instructions_name_url_and_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("PQE_CONFIG_DIR", str(tmp_path))
    text = manual_instructions()
    assert EXAMPLE_URL in text and str(example_dir()) in text and "network" not in text.lower()
    assert text == (f"Download tonsil31_crop.ome.tif from {EXAMPLE_URL} and place it in {example_dir()}, "
                    "then press Try the example again.")


def test_example_url_names_the_release_asset():
    assert EXAMPLE_URL == ("https://github.com/cateyephotos/phenocycler-qc-eval/releases/download/"
                           "example-data-v1/tonsil31_crop.ome.tif")
    assert len(example.EXAMPLE_SHA256) == 64 and example.EXAMPLE_SHA256 != "0" * 64
    assert example.EXAMPLE_SIZE_MB > 0


def test_example_panel_is_package_data():
    from phenocycler_qc_eval.layout import read_xpd

    panel = example_panel()
    assert panel.name == "31MarkerpanelB.xpd" and panel.exists()
    assert {"TOX", "CD163", "CD3e"} <= set(read_xpd(panel).table["marker"])


def test_open_folder_creates_it_first(tmp_path, monkeypatch):
    import os

    opened = []
    monkeypatch.setattr(os, "startfile", lambda p: opened.append(p), raising=False)
    monkeypatch.setattr("subprocess.Popen", lambda cmd, *a, **k: opened.append(cmd[-1]))
    target = tmp_path / "new" / "example"
    example.open_folder(target)
    assert target.is_dir() and opened == [str(target)]


# ---------------------------------------------------------------------------- worker action
def _run_worker(request: dict, tmp_path, capsys) -> tuple[int, list[dict]]:
    from phenocycler_qc_eval import worker

    req = tmp_path / "req.json"
    req.write_text(json.dumps(request), encoding="utf-8")
    code = worker.main([str(req)])
    return code, [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]


def test_worker_download_example_sends_progress_then_the_paths(tmp_path, monkeypatch, local_http_file, capsys):
    monkeypatch.setenv("PQE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(example, "EXAMPLE_URL", local_http_file.url)
    monkeypatch.setattr(example, "EXAMPLE_SHA256", local_http_file.sha256)
    code, msgs = _run_worker({"action": "download_example"}, tmp_path, capsys)
    assert code == 0
    assert [m["type"] for m in msgs][-1] == "example" and any(m["type"] == "progress" for m in msgs)
    assert msgs[-1]["image"].endswith("tonsil31_crop.ome.tif") and msgs[-1]["panel"].endswith(".xpd")
    assert all(0.0 <= m["fraction"] <= 1.0 for m in msgs if m["type"] == "progress")


def test_worker_download_example_unavailable_is_one_error_message(tmp_path, monkeypatch, local_http_file, capsys):
    monkeypatch.setenv("PQE_CONFIG_DIR", str(tmp_path / "cfg"))
    url = local_http_file.url
    local_http_file.stop()
    monkeypatch.setattr(example, "EXAMPLE_URL", url)
    monkeypatch.setattr(example, "EXAMPLE_SHA256", local_http_file.sha256)
    code, msgs = _run_worker({"action": "download_example"}, tmp_path, capsys)
    assert code == 2 and msgs[-1]["type"] == "error" and msgs[-1]["kind"] == "example_unavailable"
    assert msgs[-1]["message"]


# ---------------------------------------------------------------------------- command line
def test_cli_download_example_from_a_local_file(tmp_path, monkeypatch, local_http_file, capsys):
    from phenocycler_qc_eval.cli import main

    monkeypatch.setenv("PQE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(example, "EXAMPLE_SHA256", local_http_file.sha256)
    assert main(["--download-example", "--from", str(local_http_file.path)]) == 0
    out = capsys.readouterr().out
    assert "tonsil31_crop.ome.tif" in out and ".xpd" in out and "pqe " in out
    out.encode("ascii")


def test_cli_download_example_unavailable_says_where_to_get_it(tmp_path, monkeypatch, local_http_file, capsys):
    from phenocycler_qc_eval.cli import main

    monkeypatch.setenv("PQE_CONFIG_DIR", str(tmp_path / "cfg"))
    url = local_http_file.url
    local_http_file.stop()
    monkeypatch.setattr(example, "EXAMPLE_URL", url)
    assert main(["--download-example"]) == 2
    err = capsys.readouterr().err
    assert "Download tonsil31_crop.ome.tif from" in err and "Traceback" not in err


# ---------------------------------------------------------------------------- Try the example (GUI)
@pytest.mark.gui
def test_try_example_failure_says_where_to_get_it(app, fake_handle_factory):
    fake_handle_factory.next_messages([{"type": "error", "kind": "example_unavailable", "message": "HTTP 404"}])
    app.try_example(); app._poll(); app.update()
    assert app.banner_text() == "The example image is not on this computer yet." and EXAMPLE_URL in app.banner_action_text()
    assert app.banner_action_text() == manual_instructions()
    assert [b["text"] for b in app.banner_action_buttons] == ["Copy link", "Open that folder"]
    assert "HTTP 404" in app.log.get("1.0", "end") and app.handle is None


@pytest.mark.gui
def test_try_example_fills_inputs_and_runs(app, fake_handle_factory):
    fake_handle_factory.next_messages([{"type": "example", "image": "E:/x/tonsil31_crop.ome.tif", "panel": "E:/x/p.xpd"}])
    app.try_example(); app._poll()
    assert app.image_var.get().endswith("tonsil31_crop.ome.tif") and app.panel_var.get().endswith("p.xpd")
    assert fake_handle_factory.started[-1]["action"] == "run"


@pytest.mark.gui
def test_try_example_asks_the_worker_and_says_how_big(app, fake_handle_factory, monkeypatch):
    monkeypatch.setattr(example, "EXAMPLE_SIZE_MB", 62)
    app.try_example()
    assert fake_handle_factory.started[-1] == {"action": "download_example"}
    assert app.status_var.get() == "Downloading the example (about 62 MB)..."
    assert str(app.run_button["state"]) == "disabled" and str(app.cancel_button["state"]) == "normal"
    app.try_example()                                         # a second press does nothing
    assert len(fake_handle_factory.started) == 1


@pytest.mark.gui
def test_try_example_shows_download_progress(app, fake_handle_factory):
    fake_handle_factory.next_messages([{"type": "progress", "stage": "download", "fraction": 0.4,
                                        "message": "Downloading the example: 25 of 62 MB"}])
    app.try_example(); app._poll()
    assert app.progress["value"] == 40 and "25 of 62 MB" in app.status_var.get()


@pytest.mark.gui
def test_example_banner_buttons_do_what_they_say(app, fake_handle_factory, monkeypatch):
    opened = []
    monkeypatch.setattr(example, "open_folder", lambda folder: opened.append(Path(folder)))
    fake_handle_factory.next_messages([{"type": "error", "kind": "example_unavailable", "message": "x"}])
    app.try_example(); app._poll(); app.update()
    copy, folder = app.banner_action_buttons
    copy.invoke()
    assert app.clipboard_get() == EXAMPLE_URL
    folder.invoke()
    assert opened == [example_dir()]


@pytest.mark.gui
def test_example_button_is_wired_and_starts_the_download(app, fake_handle_factory):
    assert app.example_button.winfo_ismapped()
    app.example_button.invoke()
    assert fake_handle_factory.started[-1]["action"] == "download_example"


# ---------------------------------------------------------------------------- the real crop (needs the file)
EXAMPLE_CROP = REPO / "examples" / "tonsil31_crop.ome.tif"
CHANNELS_32 = ["DAPI", "CD31", "CD20", "CD4", "CD44", "SMA", "FOXP3", "HLA-DR", "CD66", "CD8", "Pan-Cytokeratin",
               "PDL1", "CD21", "CD45", "CD14", "IDO1", "Podoplanin", "PD-1", "TOX", "Collagen IV", "CD163", "CD38",
               "CD3e", "MPO", "ICOS", "Ki67", "LAG3", "iNOS", "TCF-1", "HIF1A", "Bcl-2", "CD68"]


@pytest.fixture
def crop():
    if not EXAMPLE_CROP.exists():
        pytest.skip(f"example image not found at {EXAMPLE_CROP}; run scripts/make_example_crop.py or "
                    "pqe --download-example --from <file>")
    return EXAMPLE_CROP


@pytest.mark.example
def test_crop_has_the_32_channels_pixel_size_and_type(crop):
    with open_image(crop) as c:
        assert c.channels == CHANNELS_32
        assert abs(c.pixel_size_um - 0.5074) < 0.001
        assert c.dtype.name == "uint8" and c.n_levels == 2
        assert c.shape_yx[0] >= 1900 and c.shape_yx[1] >= 1900


@pytest.mark.example
def test_crop_panel_layout_resolves_cycles_and_fluors(crop):
    from phenocycler_qc_eval.layout import match_markers, read_xpd

    layout = read_xpd(example_panel())
    with open_image(crop) as c:
        matched, unmatched = match_markers(layout, [n for n in c.channels if n != "DAPI"])
    assert unmatched == [] and len(matched) == 31
    row = layout.table.set_index("marker")
    assert int(row.loc["TOX", "cycle"]) > 0 and row.loc["TOX", "fluor"] and row.loc["CD163", "fluor"]


@pytest.mark.example
def test_crop_checksum_matches_the_recorded_one(crop):
    assert example.sha256_file(crop) == example.EXAMPLE_SHA256


# ---------------------------------------------------------------------------- fewer windows than asked for
def test_fewer_windows_than_requested_is_explained_in_the_report(tmp_path, synthetic_qptiff_in_tmp):
    from phenocycler_qc_eval.run import RunRequest, run_qc
    from synth import small_settings

    settings = small_settings(n_windows=400, min_windows=4)
    messages = []
    out, _ = run_qc(RunRequest(synthetic_qptiff_in_tmp, None, tmp_path / "out", settings),
                    lambda stage, frac, msg: messages.append(msg))
    report = (out / "STAIN_QC_REPORT.md").read_text(encoding="utf-8")
    info = json.loads((out / "run_info.json").read_text())
    n = info["n_windows"]
    assert n < 400 and info["windows_requested"] == 400
    assert f"Only {n} of the 400 requested windows" in report and "every window" in report
    assert any(f"Used all {n} tissue windows in this image" in m and "up to 400" in m for m in messages)
    assert not any("were requested" in m for m in messages)       # a shortfall reading, which this is not


def test_no_note_when_all_requested_windows_were_used(finished_run):
    out, _ = finished_run
    report = (out / "STAIN_QC_REPORT.md").read_text(encoding="utf-8")
    assert "requested windows" not in report
