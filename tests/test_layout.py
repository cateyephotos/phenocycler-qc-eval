from pathlib import Path

import pytest

from phenocycler_qc_eval.layout import (
    load_layout, match_markers, normalize_name, read_layout_csv, read_xpd, write_layout_template,
)
from phenocycler_qc_eval.results import norm_name
from synth import make_layout_csv


def test_read_xpd_cycles_and_fluors(xpd_file):
    t = read_xpd(xpd_file).table.set_index("marker")
    assert t.loc["CD4", "cycle"] == 2 and t.loc["FOXP3", "cycle"] == 3
    assert t.loc["CD20", "fluor"] == "AF750" and "DAPI" not in t.index and "--" not in t.index
    assert list(read_xpd(xpd_file).table.columns) == ["marker", "cycle", "fluor", "exposure_ms"]


def test_csv_missing_column(tmp_path):
    p = tmp_path / "l.csv"
    p.write_text("marker,cycle\nCD4,1\n")
    with pytest.raises(ValueError, match="fluor"):
        read_layout_csv(p)


def test_csv_ignores_incomplete_rows(tmp_path):
    p = make_layout_csv(tmp_path / "l.csv", [("CD4", 2, "CY5"), ("CD20", "", ""), ("CD8", 3, "")])
    t = read_layout_csv(p).table
    assert list(t["marker"]) == ["CD4"] and int(t["cycle"].iloc[0]) == 2


def test_load_layout(tmp_path, xpd_file):
    assert load_layout(None) is None
    assert len(load_layout(xpd_file).table) == 6
    csv = make_layout_csv(tmp_path / "l.csv", [("CD4", 1, "CY5")])
    assert load_layout(csv).source == str(csv)
    with pytest.raises(ValueError):
        load_layout(tmp_path / "l.txt")


def test_match_markers_normalizes(xpd_file):
    m, unmatched = match_markers(read_xpd(xpd_file), ["DAPI", "cd31", "CD 4", "Cd20", "Foxp3", "SMA"])
    assert m["cd31"] == "CD31" and m["CD 4"] == "CD4" and m["Foxp3"] == "FOXP3" and unmatched == ["CD44"]
    assert "DAPI" not in m


def test_one_normalization_rule():
    assert normalize_name is norm_name
    assert normalize_name("Pan-Cytokeratin") == normalize_name("PanCytokeratin")
    assert normalize_name("CD3e") == normalize_name("CD3E")


def test_template_round_trip(tmp_path):
    p = write_layout_template(["DAPI", "CD4", "CD20"], tmp_path / "layout.csv")
    assert p.read_text().splitlines() == ["marker,cycle,fluor", "CD4,,", "CD20,,"]
    assert read_layout_csv(p).table.empty


def test_example_xpd_parses():
    p = Path(r"E:\Tonsil31plex\31MarkerpanelB.xpd")
    if not p.exists():
        pytest.skip("lab file not present")
    assert {"CD31", "CD4", "CD20", "TOX", "CD163"} <= set(read_xpd(p).table.marker)


def test_bad_xpd_names_the_file(tmp_path):
    p = tmp_path / "broken.xpd"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="broken.xpd"):
        read_xpd(p)


def test_cycle_must_be_whole(tmp_path):
    ok = make_layout_csv(tmp_path / "ok.csv", [("CD4", "2.0", "CY5"), ("CD8", "3 ", "CY5")])
    assert list(read_layout_csv(ok).table["cycle"]) == [2, 3]
    bad = make_layout_csv(tmp_path / "bad.csv", [("CD4", "2", "CY5"), ("CD8", "2.5", "CY5")])
    with pytest.raises(ValueError, match="row 3 .CD8"):
        read_layout_csv(bad)


def test_cycle_error_names_the_original_file_row(tmp_path):
    p = tmp_path / "gap.csv"
    # row 2 is blank-ish (no cycle) and gets dropped; CD8 is on file row 4
    p.write_text("marker,cycle,fluor" + chr(10) + "CD4,,CY5" + chr(10) + "CD3,1,CY5" + chr(10) + "CD8,x,CY5" + chr(10), encoding="utf-8")
    with pytest.raises(ValueError, match="row 4 .CD8"):
        read_layout_csv(p)


def test_xpd_with_the_wrong_shape_names_the_file(tmp_path):
    for name, text in (("list.xpd", "[1, 2]"), ("wells.xpd", '{"wells": [1]}'),
                       ("items.xpd", '{"wells": [{"items": [3]}]}')):
        p = tmp_path / name
        p.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError, match=name):
            read_xpd(p)
