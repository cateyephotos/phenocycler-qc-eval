"""Task 17 review round 1 (SUPP-870): Titration selection, download edges, crop script, honest README."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest
from conftest import FakeHandle

from phenocycler_qc_eval import example
from phenocycler_qc_eval.readers import open_image

REPO = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------- I2: Titration selection
def _select_row(app, index: int) -> str:
    tree = app.titration_tab.tree
    pick = tree.get_children()[index]
    tree.selection_set(pick)
    return str(tree.item(pick, "values")[0])


def _selected(app) -> str:
    tree = app.titration_tab.tree
    return str(tree.item(tree.selection()[0], "values")[0])


def _first(app) -> str:
    tree = app.titration_tab.tree
    return str(tree.item(tree.get_children()[0], "values")[0])


@pytest.mark.gui
def test_same_run_reloaded_from_partial_to_final_keeps_the_selected_antibody(app, tmp_path):
    """I2(a): the run folder fills in as the run goes; the row the user picked stays picked."""
    import shutil

    from phenocycler_qc_eval.results import load_results
    from synth import PROTO

    run = tmp_path / "going"
    run.mkdir()
    shutil.copy2(PROTO / "stain_qc_channels.csv", run / "stain_qc_channels.csv")
    (run / "RUN_INCOMPLETE.txt").write_text("Run in progress (running).")
    (run / "run_info.json").write_text(json.dumps({"status": "running", "crosstalk_state": "pending"}))
    app.show_results(load_results(run), only_titration=True)
    name = _select_row(app, 3)

    shutil.copytree(PROTO, run, dirs_exist_ok=True)           # the run finishes into the same folder
    (run / "RUN_INCOMPLETE.txt").unlink()
    (run / "run_info.json").write_text(json.dumps({"status": "complete", "crosstalk_state": "on"}))
    app.show_results(load_results(run))                      # no keep_tab: the folder alone decides
    assert _selected(app) == name


@pytest.mark.gui
def test_a_different_run_starts_on_the_first_row(app, complete_folder, partial_folder):
    """I2(b): never carry a selection from one run into another, whatever keep_tab says."""
    from phenocycler_qc_eval.results import load_results

    app.show_results(load_results(complete_folder))
    _select_row(app, 3)
    app.show_results(load_results(partial_folder), keep_tab=True)
    assert _selected(app) == _first(app)
    name = _select_row(app, 2)
    app.show_results(load_results(complete_folder), keep_tab=True)    # back to run A: that is a new load too
    assert _selected(app) == _first(app) and _first(app) != "" and name


@pytest.mark.gui
def test_show_error_does_not_carry_a_selection_over_from_another_run(app, complete_folder, partial_folder):
    """I2(b): an error on a different run's folder starts at the top; it does not force the old row."""
    from phenocycler_qc_eval.results import load_results

    app.show_results(load_results(complete_folder))
    _select_row(app, 3)
    app._run_folder = partial_folder
    app.show_error("unexpected", "ValueError: boom")
    assert _selected(app) == _first(app)


@pytest.mark.gui
def test_show_error_on_the_same_run_keeps_the_selection(app, partial_folder):
    from phenocycler_qc_eval.results import load_results

    app.show_results(load_results(partial_folder))
    name = _select_row(app, 2)
    app._run_folder = partial_folder
    app.show_error("unexpected", "ValueError: boom")
    assert _selected(app) == name


# ---------------------------------------------------------------------------- I2(c): pending with a layout
def test_a_run_with_a_panel_file_says_crosstalk_is_pending_at_the_start(tmp_path, synthetic_qptiff, xpd_file):
    from phenocycler_qc_eval.results import load_results
    from phenocycler_qc_eval.run import INCOMPLETE_MARKER, RunRequest, run_qc
    from synth import small_settings

    log = []

    def progress(stage, frac, msg):
        out = next((tmp_path / "out").iterdir())
        r = load_results(out)
        log.append((stage, r.status, r.crosstalk_state))
    out, _ = run_qc(RunRequest(synthetic_qptiff, xpd_file, tmp_path / "out", small_settings(6, 3)), progress)
    assert log[0] == ("open", "running", "pending")
    # The fixture panel has two cycles, so since X1 the finished run says "not judged" (state off), not "on".
    final = load_results(out)
    assert final.crosstalk_state == "off" and final.crosstalk_not_judged and not (out / INCOMPLETE_MARKER).exists()


# ---------------------------------------------------------------------------- Minor 3: cancel during the download
@pytest.mark.gui
def test_cancel_during_the_download_does_not_start_the_run(app, fake_handle_factory):
    fake_handle_factory.next_messages([{"type": "example", "image": "E:/x/tonsil31_crop.ome.tif",
                                        "panel": "E:/x/p.xpd"}])
    app.try_example()
    app._cancelling = True                                   # Cancel was pressed just before the answer was read
    app._poll()
    assert [s["action"] for s in fake_handle_factory.started] == ["download_example"]
    assert app.status_var.get() == "Download cancelled."
    assert app.handle is None and str(app.cancel_button["state"]) == "disabled"


@pytest.mark.gui
def test_try_example_starts_the_run_on_the_example_files(app, fake_handle_factory):
    fake_handle_factory.next_messages([{"type": "example", "image": "E:/x/tonsil31_crop.ome.tif",
                                        "panel": "E:/x/p.xpd"}])
    app.try_example()
    app._poll()
    req = fake_handle_factory.started[-1]
    assert req["action"] == "run"
    assert Path(req["image"]) == Path("E:/x/tonsil31_crop.ome.tif") and Path(req["panel"]) == Path("E:/x/p.xpd")


# ---------------------------------------------------------------------------- Minors 4, 5, 7: the download itself
def test_a_stale_part_file_is_removed_when_a_download_starts(tmp_path, local_http_file):
    dest = tmp_path / "x.ome.tif"
    part = tmp_path / "x.ome.tif.part"
    part.write_bytes(b"left behind by a killed download")
    seen = []
    example.download(local_http_file.url, dest, local_http_file.sha256, lambda done, total: seen.append(part.stat().st_size))
    assert dest.exists() and not part.exists()
    assert seen and seen[0] <= 1 << 20                        # the new file started empty, not on top of the old bytes


def test_download_retries_a_busy_destination(tmp_path, monkeypatch, local_http_file):
    """Windows holds the file for a moment (virus scanner, indexer): retry instead of throwing 83 MB away."""
    real = os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError("in use")
        return real(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    dest = example.download(local_http_file.url, tmp_path / "y.ome.tif", local_http_file.sha256)
    assert dest.exists() and calls["n"] == 3


def test_download_keeps_the_part_file_when_the_move_keeps_failing(tmp_path, monkeypatch, local_http_file):
    def always(src, dst):
        raise PermissionError("in use")

    monkeypatch.setattr(os, "replace", always)
    with pytest.raises(PermissionError):
        example.download(local_http_file.url, tmp_path / "z.ome.tif", local_http_file.sha256)
    assert (tmp_path / "z.ome.tif.part").exists()             # a verified copy: not thrown away for a busy file


def test_progress_keeps_moving_when_the_size_is_not_known():
    from phenocycler_qc_eval import worker

    sent = []

    def emit(**message):
        sent.append(message)

    class Quiet:
        pass

    def fake_download(progress, source=None):
        for done in range(1, 31):                             # 30 MB, no total
            progress(done * 1_000_000, None)
        return Path("a"), Path("b")

    import phenocycler_qc_eval.example as ex
    original = ex.download_example
    ex.download_example = fake_download
    try:
        worker._download_example({}, emit)
    finally:
        ex.download_example = original
    progress_messages = [m for m in sent if m["type"] == "progress"]
    assert len(progress_messages) >= 4                         # not one message and then silence
    assert progress_messages[-1]["message"].startswith("Downloading the example: ")


# ---------------------------------------------------------------------------- Minor 9: the crop script protects its source
def test_crop_script_refuses_to_write_over_its_source(tmp_path, synthetic_qptiff, capsys):
    mod = _load_script("make_example_crop")
    before = synthetic_qptiff.read_bytes()
    code = mod.main(["--qptiff", str(synthetic_qptiff), "--y0", "0", "--x0", "0", "--size-um", "100",
                     "--out", str(synthetic_qptiff)])
    assert code != 0 and synthetic_qptiff.read_bytes() == before
    assert "source" in capsys.readouterr().err.lower()


def test_crop_script_prints_ascii_only(tmp_path, synthetic_qptiff, capsys):
    mod = _load_script("make_example_crop")
    folder = tmp_path / "caf\u00e9"
    assert mod.main(["--qptiff", str(synthetic_qptiff), "--y0", "0", "--x0", "0", "--size-um", "100",
                     "--out", str(folder / "c.ome.tif")]) == 0
    text = capsys.readouterr().out
    assert text.isascii() and "sha256" in text


# ---------------------------------------------------------------------------- Minor 13: a picture of the crop
def test_preview_script_writes_a_small_rgb_picture(tmp_path):
    import imagecodecs

    from synth import make_synthetic_image

    image = make_synthetic_image(tmp_path / "p.ome.tif", kind="ome",
                                 channels=["DAPI", "CD20", "CD3e", "Pan-Cytokeratin"], size_px=512)
    mod = _load_script("make_example_preview")
    with open_image(image) as src:
        out = mod.make_preview(src, tmp_path / "preview.png", max_px=200)
    assert out.exists() and out.stat().st_size < 200_000
    pic = imagecodecs.png_decode(out.read_bytes())
    assert pic.ndim == 3 and pic.shape[2] == 3 and max(pic.shape[:2]) == 200 and int(pic.max()) > 0


# ---------------------------------------------------------------------------- I1: an honest README
def test_readme_does_not_present_a_vacuous_negative_control():
    text = (REPO / "examples" / "README.md").read_text(encoding="utf-8")
    meta = json.loads((REPO / "examples" / "example_data.json").read_text(encoding="utf-8"))
    for words in (text, meta["how_chosen"]):
        assert "does not flag CD8 to CD21" not in words and "CD8 -> CD21 not flagged" not in words
        assert "near-blank" in words and "CD21" in words
        assert "CD3e to CD38" in words or "CD3e -> CD38" in words      # a pair that was tested and came out clean
    assert "tonsil31_crop_preview.png" in text
    assert "same bytes every time" not in text and "same package versions" in text
    assert "will be published" in text
    assert "<image_stem>_<YYYYMMDD-HHMMSS>" in (REPO / "README.md").read_text(encoding="utf-8")
    assert "tifffile" in meta and "imagecodecs" in meta
    assert (REPO / "examples" / "tonsil31_crop_preview.png").stat().st_size < 200_000
