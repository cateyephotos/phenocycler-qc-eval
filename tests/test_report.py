import re
from dataclasses import fields

import numpy as np
import pandas as pd
import pytest

from phenocycler_qc_eval.advice import next_run_text
from phenocycler_qc_eval.crosstalk import CrosstalkResult, exclusive_pairs_table
from phenocycler_qc_eval.report import write_report
from phenocycler_qc_eval.settings import default_settings

META = {"image": "tonsil.qptiff", "panel": "panel.xpd", "pixel_size_um": 0.497, "dtype": "uint8", "n_windows": 12,
        "window_px": 128, "version": "0.1.0", "started": "2026-11-12 10:15:30", "finished": "2026-11-12 10:25:41",
        "value_range": 255, "range_basis": "8-bit image: the top of the range is 255"}


@pytest.fixture
def det(leak_det):
    """The leak sample's table with one weak antibody (raise) and one single-weakness antibody (compare)."""
    d = leak_det.copy()
    weak = d["channel"] == "CD44"
    d.loc[weak, ["call", "exposure", "next_run"]] = ["RAISE", "LENGTHEN", next_run_text("RAISE", "LENGTHEN")]
    d.loc[weak, ["signal_p99_5", "signal_fraction", "positive_fraction", "stain_index"]] = [5.0, 0.02, 0.001, 1.5]
    check = d["channel"] == "CD21"
    d.loc[check, ["call", "exposure", "next_run"]] = ["CHECK", "KEEP", next_run_text("CHECK", "KEEP")]
    d.loc[check, "stain_index"] = 1.8
    return d


@pytest.fixture
def det_float(det):
    d = det.copy()
    d["saturation_fraction"] = np.nan
    return d


@pytest.fixture
def ex(leak_sample, leak_det):
    return exclusive_pairs_table(leak_sample, leak_det, [["CD4", "CD20"], ["CD8", "CD20"]])


@pytest.fixture
def meta():
    return dict(META)


def run(tmp_path, det, xt, ex, meta, followups=(), settings=None, warnings=()):
    return write_report(tmp_path, det, xt, ex, list(followups), settings or default_settings(), meta,
                        list(warnings)).read_text(encoding="utf-8")


def test_report_sections_in_order(tmp_path, det, xt_result, ex, meta):
    text = write_report(tmp_path, det, xt_result, ex, [], default_settings(), meta, []).read_text()
    assert [l for l in text.splitlines() if l.startswith("## ")] == [
        "## Next run", "## Crosstalk", "## Markers that should not overlap", "## Cycle-order suggestions",
        "## Markers not in the panel file", "## Settings used", "## How the calls are made", "## Warnings"]


def test_headline_is_the_gui_banner_sentence(tmp_path, det, xt_result, ex, meta):
    from pathlib import Path

    from phenocycler_qc_eval.advice import headline
    from phenocycler_qc_eval.results import Results

    lines = [l for l in write_report(tmp_path, det, xt_result, ex, [], default_settings(), meta, []).read_text()
             .splitlines() if l.strip()]
    antibodies = det[~det["channel"].isin(["DAPI"])]
    banner = headline(Results(folder=Path("."), detection=antibodies, crosstalk=xt_result.table,
                              crosstalk_state="on", status="complete", unmatched=list(xt_result.unmatched)))
    assert lines[0].startswith("# ")
    assert lines[1:3] == banner.text.splitlines()


def test_headline_counts_match_the_table(tmp_path, det, xt_result, ex, meta):
    line = run(tmp_path, det, xt_result, ex, meta).splitlines()[2]
    n_change = int(det["call"].isin(["RAISE", "LOWER"]).sum())
    assert line.startswith(f"Next run: {n_change} to change") and f"(of {len(det)} antibodies)" in line


def test_one_next_run_column(tmp_path, det, xt_result, ex, meta):
    text = write_report(tmp_path, det, xt_result, ex, [], default_settings(), meta, []).read_text()
    assert "| Antibody | Next run | Why |" in text and "Raise concentration (or lengthen exposure)" in text


def test_rows_to_change_come_first(tmp_path, det, xt_result, ex, meta):
    text = run(tmp_path, det, xt_result, ex, meta)
    table = text.split("## Next run")[1].split("## Crosstalk")[0]
    rows = [r for r in table.splitlines() if r.startswith("| ") and "---" not in r][1:]
    calls = [r.split("|")[2].strip() for r in rows]
    rank = [0 if c.startswith(("Raise", "Lower")) else 1 if c.startswith("Compare") else 2 for c in calls]
    assert rank == sorted(rank) and len(calls) == len(det)


def test_every_row_carries_its_evidence(tmp_path, det, xt_result, ex, meta):
    text = run(tmp_path, det, xt_result, ex, meta)
    row = next(l for l in text.splitlines() if l.startswith(f"| {det['channel'].iloc[0]} |"))
    assert len(row.strip().strip("|").split("|")) == 7 and "of 255" in row and "%" in row


def test_report_records_every_setting(tmp_path, det, xt_result, ex, meta):
    s = default_settings(); s.detection.dim_fraction = 0.123
    text = write_report(tmp_path, det, xt_result, ex, [], s, meta, []).read_text()
    assert "0.123" in text and "lab-defined, not published" in text


def test_every_settings_field_is_listed(tmp_path, det, xt_result, ex, meta):
    s = default_settings()
    text = run(tmp_path, det, xt_result, ex, meta, settings=s)
    section = text.split("## Settings used")[1].split("## How the calls")[0]
    for sec in fields(s):
        for f in fields(getattr(s, sec.name)):
            assert f"({f.name})" in section


def test_range_and_how_it_was_chosen_are_recorded(tmp_path, det, xt_result, ex, meta):
    text = run(tmp_path, det, xt_result, ex,
               dict(meta, value_range=4095, range_basis="12-bit data in a 16-bit file"))
    assert "Intensity range used: 0 to 4095 (12-bit data in a 16-bit file)" in text


def test_crosstalk_off_still_lists_exclusive_pairs(tmp_path, det, xt_off, ex, meta):
    text = write_report(tmp_path, det, xt_off, ex, [], default_settings(), meta, []).read_text()
    assert "Crosstalk is off" in text and ex.marker_a.iloc[0] in text.split("## Markers that should not overlap")[1]


def test_crosstalk_off_offers_the_layout_template(tmp_path, det, xt_off, ex, meta):
    text = run(tmp_path, det, xt_off, ex, meta)
    assert "marker,cycle,fluor" in text.split("## Crosstalk")[1].split("## Markers")[0]
    assert "--layout-template" in text
    assert "Crosstalk: not checked." in text.splitlines()[4]


def test_float_image_note(tmp_path, det_float, xt_result, ex, meta):
    assert "saturation was not checked" in write_report(tmp_path, det_float, xt_result, ex, [], default_settings(),
                                                        dict(meta, dtype="float32"), []).read_text()


def test_float_image_table_says_not_checked(tmp_path, det_float, xt_result, ex, meta):
    text = run(tmp_path, det_float, xt_result, ex, dict(meta, dtype="float32"))
    assert "Saturation not checked" in text and text.count("| not checked |") == len(det_float)


def test_integer_image_has_no_saturation_note(tmp_path, det, xt_result, ex, meta):
    low = run(tmp_path, det, xt_result, ex, meta).lower()
    assert "saturation was not checked" not in low and "saturation not checked" not in low


def test_no_numeric_targets(tmp_path, det, xt_result, ex, meta):
    text = write_report(tmp_path, det, xt_result, ex, [], default_settings(), meta, []).read_text()
    assert not re.search(r"\b1:\d+\b|\d+\s*ms\b", text.split("## Settings used")[0])


def test_flagged_pairs_get_cause_numbers_and_figure_link(tmp_path, det, xt_result, ex, meta):
    (tmp_path / "followup_CD20_to_CD31.png").write_bytes(b"x")
    section = run(tmp_path, det, xt_result, ex, meta).split("## Crosstalk")[1].split("## Markers")[0]
    assert "CD20 -> CD31" in section and "Same-cycle bleed-through" in section
    assert "[follow-up panel](followup_CD20_to_CD31.png)" in section
    assert "follow-up panel (followup_CD4_to_FOXP3.png, not saved)" in section   # no file, no dead link


def test_followup_numbers_are_quoted(tmp_path, det, xt_result, ex, meta):
    fu = {"source": "CD20", "target": "CD31", "control": "CD4", "target_where_source_bright": 11.6,
          "target_where_source_low": 0.4, "target_where_control_bright": 5.5, "figure": "followup_CD20_to_CD31.png"}
    text = run(tmp_path, det, xt_result, ex, meta, followups=[fu])
    assert "CD31 reads 11.6 where CD20 is bright and 0.4 where CD20 is low" in text
    assert "control marker CD4 is bright it reads 5.5" in text


def test_unmatched_markers_use_the_agreed_wording(tmp_path, det, xt_result, ex, meta):
    xt = CrosstalkResult(enabled=True, message="Crosstalk checked.", table=xt_result.table, spill=xt_result.spill,
                         highpass=xt_result.highpass, unmatched=["CD99"], suggestions=[])
    text = run(tmp_path, det, xt, ex, dict(meta, image_only=["CD3e"]))
    section = text.split("## Markers not in the panel file")[1].split("## Settings")[0]
    assert "In the panel file but not found in the image" in section and "CD99" in section
    assert "In the image but not in the panel file" in section and "CD3e" in section


def test_suggestions_and_warnings_are_printed(tmp_path, det, xt_result, ex, meta):
    text = run(tmp_path, det, xt_result, ex, meta, warnings=["Setting seed was not a whole number; used 0."])
    assert "- Setting seed was not a whole number; used 0." in text.split("## Warnings")[1]
    for s in xt_result.suggestions:
        assert s in text.split("## Cycle-order suggestions")[1]


def test_no_warnings_says_none(tmp_path, det, xt_result, ex, meta):
    assert run(tmp_path, det, xt_result, ex, meta).rstrip().endswith("None.")


def test_documented_choices_are_stated(tmp_path, det, xt_result, ex, meta):
    rules = run(tmp_path, det, xt_result, ex, meta).split("## How the calls are made")[1]
    assert "Near-blank channels are never flagged" in rules and "unrelated pairs that set the flag cut-off" in rules
    assert "brightest sampled pixel" in rules and "sampled tissue only" in rules


def test_nuclear_channel_is_not_a_row(tmp_path, det, xt_result, ex, meta):
    extra = pd.concat([det, det.iloc[[0]].assign(channel="DAPI")], ignore_index=True)
    table = run(tmp_path, extra, xt_result, ex, meta).split("## Next run")[1].split("## Crosstalk")[0]
    assert "| DAPI |" not in table


def test_empty_exclusive_pairs(tmp_path, det, xt_result, ex, meta):
    assert "No pairs could be checked" in run(tmp_path, det, xt_result, ex.iloc[0:0], meta)


def test_ascii_only_and_plain_words(tmp_path, det, xt_result, ex, meta):
    text = run(tmp_path, det, xt_result, ex, meta)
    text.encode("ascii")
    low = text.lower()
    assert "verdict" not in low and "grade" not in low and "carry-over" not in low


def test_whole_report_says_crosstalk_not_leak_or_bleed(tmp_path, det, xt_result, ex, meta):
    low = run(tmp_path, det, xt_result, ex, meta).lower()
    assert "leak" not in low
    assert "bleed" not in low.replace("same-cycle bleed-through", "")   # R22: allowed only as the likely cause


def test_report_wording_for_top_group_and_footnote(tmp_path, det, xt_result, ex, meta):
    text = run(tmp_path, det, xt_result, ex, meta)
    assert "top 1%" not in text and "at or above its 99th percentile" in text
    e = ex.copy()
    e.loc[e.index[0], "lift"] = np.inf
    assert not any(line.startswith("- =") for line in run(tmp_path, det, xt_result, e, meta).splitlines())


# ---- fix round 1 (SUPP-870)
def test_blank_spill_row_rule_is_stated_in_the_report(tmp_path, det, xt_result, ex, meta):
    text = run(tmp_path, det, xt_result, ex, meta)
    rules = text.split("## How the calls are made")[1]
    assert "sparse" in rules and "99th percentile is zero or no higher than its median" in rules
    assert "saturated over many pixels is not sparse" in rules
    assert "A sparse channel has no spill row" in rules.split("Choices that are easy to miss:")[1]


def test_control_marker_setting_reads_naturally(tmp_path, det, xt_result, ex, meta):
    s = default_settings()
    s.markers.control_markers = [["CD31", "CD68"]]
    text = run(tmp_path, det, xt_result, ex, meta, settings=s)
    assert "CD31 (control: CD68)" in text and "CD31 with CD68" not in text


def test_call_words_are_mapped_to_next_run_words(tmp_path, det, xt_result, ex, meta):
    rules = run(tmp_path, det, xt_result, ex, meta).split("## How the calls are made")[1]
    assert "Raise concentration" in rules.split("Crosstalk:")[0] and "Lower concentration" in rules
    assert "Otsu" in rules and "automatic cut between stained and background pixels" in rules
    assert "SD" in rules and "spread" in rules


def test_crosstalk_headers_use_spill_not_leak(tmp_path, det, xt_result, ex, meta):
    section = run(tmp_path, det, xt_result, ex, meta).split("## Crosstalk")[1].split("## Markers")[0]
    assert "Spill, source to target" in section and "Spill, target to source" in section
    assert "leak" not in section.lower()


def test_integer_image_with_unmeasured_saturation_is_not_called_float(tmp_path, det_float, xt_result, ex, meta):
    text = run(tmp_path, det_float, xt_result, ex, meta)       # meta dtype is uint8
    assert "decimal" not in text and "float" not in text.split("## Settings used")[0]
    assert "saturation could not be measured" in text


def test_float_image_still_says_decimal(tmp_path, det_float, xt_result, ex, meta):
    assert "decimal (float) values" in run(tmp_path, det_float, xt_result, ex, dict(meta, dtype="float32"))


def test_absent_target_wording_names_the_section(tmp_path, det, xt_result, ex, meta):
    rules = run(tmp_path, det, xt_result, ex, meta).split("## How the calls are made")[1]
    assert "absent from this tissue section" in rules


def test_empty_detection_table_without_channel_column_does_not_raise(tmp_path, xt_result, ex, meta):
    text = run(tmp_path, pd.DataFrame(), xt_result, ex, meta)
    assert "## Next run" in text


def test_lift_of_zero_or_inf_gets_a_footnote(tmp_path, det, xt_result, ex, meta):
    e = ex.copy()
    e.loc[e.index[0], "lift"] = np.inf
    section = run(tmp_path, det, xt_result, e, meta).split("## Markers that should not overlap")[1] \
        .split("## Cycle-order")[0]
    assert "not enough pixels to compare" in section
    full = ex.copy()
    full["lift"] = 0.5
    assert "not enough pixels to compare" not in run(tmp_path, det, xt_result, full, meta) \
        .split("## Markers that should not overlap")[1].split("## Cycle-order")[0]


def test_crosstalk_with_no_flags_and_followups_supplied(tmp_path, det, xt_result, ex, meta):
    clean = CrosstalkResult(enabled=True, message="Crosstalk checked.", table=xt_result.table.iloc[0:0],
                            unmatched=[])
    fu = {"source": "CD20", "target": "CD31", "control": "CD4", "target_where_source_bright": 11.6,
          "target_where_source_low": 0.4, "figure": "followup_CD20_to_CD31.png"}
    text = run(tmp_path, det, clean, ex, meta, followups=[fu])
    assert "No channel pairs were flagged." in text
    assert "CD31 reads 11.6" not in text


def test_plural_is_public():
    from phenocycler_qc_eval.advice import plural
    assert plural(1, "pair", "pairs") == "1 pair" and plural(2, "pair", "pairs") == "2 pairs"


def test_report_why_column_uses_the_run_settings():
    import pandas as pd
    from pathlib import Path

    from phenocycler_qc_eval import report
    from phenocycler_qc_eval.settings import DetectionSettings
    d = pd.DataFrame([{"channel": "X", "call": "RAISE", "exposure": "KEEP", "next_run": "Raise concentration",
                       "signal_p99_5": 20.0, "stain_index": 8.0, "positive_fraction": 0.2,
                       "saturation_fraction": 0.0}])
    meta = {"value_range": 255.0}
    default = " ".join(report._next_run_section(d, meta, Path("."), True))
    own = " ".join(report._next_run_section(d, meta, Path("."), True, DetectionSettings(dim_fraction=0.05)))
    assert "Dim:" in default and "Dim:" not in own
