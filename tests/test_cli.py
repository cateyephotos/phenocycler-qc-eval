import pytest

from phenocycler_qc_eval.cli import main
from synth import make_synthetic_image, write_xpd


def test_cli_no_dapi_exit_2(tmp_path, capsys):
    p = make_synthetic_image(tmp_path / "x.qptiff", channels=["CD3e", "CD20"])
    assert main([str(p)]) == 2
    err = capsys.readouterr().err
    assert "--nuclear-channel" in err and "CD3e" in err and "CD20" in err
    assert len(err.strip().splitlines()) == 1 and "Traceback" not in err
    assert not (tmp_path / "qc_results").exists()            # nothing left behind


def test_cli_nuclear_channel_override_runs(tmp_path, capsys):
    p = make_synthetic_image(tmp_path / "x.qptiff", channels=["Nuc", "CD3e", "CD20"])
    assert main([str(p), "--nuclear-channel", "Nuc", "--n-windows", "20"]) == 0


def test_cli_too_little_tissue_exit_2(tmp_path, capsys):
    p = make_synthetic_image(tmp_path / "t.qptiff", channels=["DAPI", "CD3e"], tissue_fraction=0.01)
    assert main([str(p)]) == 2
    assert "need at least 20" in capsys.readouterr().err


@pytest.mark.slow
def test_cli_defaults_output_beside_image(synthetic_qptiff_in_tmp, capsys):
    assert main([str(synthetic_qptiff_in_tmp), "--n-windows", "20"]) == 0
    out = capsys.readouterr().out
    assert (synthetic_qptiff_in_tmp.parent / "qc_results").is_dir()
    assert "to change" in out and "Crosstalk: not checked." in out and "STAIN_QC_REPORT.md" in out
    assert "No panel file: crosstalk will be off" in out
    out.encode("ascii")                                      # plain ASCII all the way


@pytest.mark.slow
def test_cli_uses_a_lone_xpd_and_says_so(synthetic_qptiff_in_tmp, capsys):
    folder = synthetic_qptiff_in_tmp.parent
    write_xpd(folder / "panel.xpd", [("A1", [("CD31", "ATTO550"), ("CD4", "CY5")])])
    assert main([str(synthetic_qptiff_in_tmp), "--n-windows", "20", "--out", str(folder / "res")]) == 0
    out = capsys.readouterr().out
    assert "Using panel file panel.xpd" in out and "Crosstalk: not checked." not in out
    assert (folder / "res").is_dir() and not (folder / "qc_results").exists()


@pytest.mark.slow
def test_cli_each_run_gets_its_own_folder(synthetic_qptiff_in_tmp):
    for _ in range(2):
        assert main([str(synthetic_qptiff_in_tmp), "--n-windows", "20"]) == 0
    assert len(list((synthetic_qptiff_in_tmp.parent / "qc_results").iterdir())) == 2


@pytest.mark.slow
def test_cli_seed_and_save_sample(synthetic_qptiff_in_tmp):
    assert main([str(synthetic_qptiff_in_tmp), "--n-windows", "20", "--seed", "3", "--save-sample"]) == 0
    run = next((synthetic_qptiff_in_tmp.parent / "qc_results").iterdir())
    assert (run / "sample.npz").exists()
    import json
    assert json.loads((run / "run_info.json").read_text())["settings"]["advanced"]["seed"] == 3


@pytest.mark.slow
def test_cli_layout_template(synthetic_qptiff_in_tmp):
    assert main(["--layout-template", str(synthetic_qptiff_in_tmp)]) == 0
    path = synthetic_qptiff_in_tmp.parent / (synthetic_qptiff_in_tmp.stem + "_layout.csv")
    assert path.exists()
    assert path.read_text().splitlines() == ["marker,cycle,fluor", "CD31,,", "CD4,,"]


def test_cli_missing_image_exit_2(tmp_path, capsys):
    assert main([str(tmp_path / "nope.qptiff")]) == 2
    assert "not found" in capsys.readouterr().err
    assert not (tmp_path / "qc_results").exists()


def test_cli_without_image_exit_2(capsys):
    assert main([]) == 2
    assert "give the image" in capsys.readouterr().err


def test_cli_rejects_a_bad_window_count(synthetic_qptiff_in_tmp):
    with pytest.raises(SystemExit) as e:
        main([str(synthetic_qptiff_in_tmp), "--n-windows", "0"])
    assert e.value.code == 2


@pytest.mark.slow
def test_cli_bad_settings_file_warns_and_runs(synthetic_qptiff_in_tmp, capsys):
    bad = synthetic_qptiff_in_tmp.parent / "settings.json"
    bad.write_text("{ not json")
    assert main([str(synthetic_qptiff_in_tmp), "--n-windows", "20", "--settings", str(bad)]) == 0
    assert "Warning:" in capsys.readouterr().out


@pytest.mark.slow
def test_cli_cancel_returns_130(synthetic_qptiff_in_tmp, capsys, monkeypatch):
    from phenocycler_qc_eval import run as run_module
    def boom(req, progress=None):
        raise KeyboardInterrupt
    monkeypatch.setattr(run_module, "run_qc", boom)
    assert main([str(synthetic_qptiff_in_tmp)]) == 130
    assert "cancel" in capsys.readouterr().err.lower()


def test_cli_too_little_tissue_names_the_run_folder(tmp_path, capsys):
    p = make_synthetic_image(tmp_path / "t.qptiff", channels=["DAPI", "CD3e"], tissue_fraction=0.01)
    assert main([str(p)]) == 2
    err = capsys.readouterr().err
    folder = next((tmp_path / "qc_results").iterdir())
    assert str(folder) in err


def test_cli_documents_that_it_uses_the_agg_canvas_only():
    from phenocycler_qc_eval import cli
    assert "FigureCanvasAgg" in cli.__doc__ and "MPLBACKEND" in cli.__doc__


@pytest.mark.slow
def test_cli_error_text_has_no_double_period(synthetic_qptiff_in_tmp, capsys, monkeypatch):
    from phenocycler_qc_eval import run as run_module
    from phenocycler_qc_eval.readers import ReaderError

    def fail_after_start(req, progress=None):
        req.on_started(run_module.make_run_dir(req.output_root, req.image))
        raise ReaderError("The file ended early.")
    monkeypatch.setattr(run_module, "run_qc", fail_after_start)
    assert main([str(synthetic_qptiff_in_tmp)]) == 2
    err = capsys.readouterr().err
    assert ".." not in err and "ended early. The run folder is" in err


@pytest.mark.slow
def test_cli_cancel_after_start_names_the_partial_folder(synthetic_qptiff_in_tmp, capsys, monkeypatch):
    from phenocycler_qc_eval import run as run_module

    def stop_after_start(req, progress=None):
        folder = run_module.make_run_dir(req.output_root, req.image)
        req.on_started(folder)
        raise KeyboardInterrupt
    monkeypatch.setattr(run_module, "run_qc", stop_after_start)
    assert main([str(synthetic_qptiff_in_tmp)]) == 130
    err = capsys.readouterr().err
    folder = next((synthetic_qptiff_in_tmp.parent / "qc_results").iterdir())
    assert "Partial results are in" in err and str(folder) in err
