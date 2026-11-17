"""STAIN_QC_REPORT.md: the document a lab member reads and forwards.

Plain wording, one "Next run" instruction per antibody with its evidence beside it, and a full record
of the settings and rules behind the calls. Nothing parses this file; the loader only opens it.
"""

from __future__ import annotations

import math
from dataclasses import fields, is_dataclass
from pathlib import Path

import pandas as pd

from . import crosstalk as _xt
from . import detection as _det
from .advice import (
    blank_markers, evidence_detail, exclusive_verdict, flag_reason_text, fmt_value, headline, kind_text, next_run_text,
)
from .crosstalk import followup_pairs
from .layout import NUCLEAR_NAMES
from .results import NOT_JUDGED_MESSAGE, Results, norm_name, safe_name
from .settings import DetectionSettings, Settings

REPORT_NAME = "STAIN_QC_REPORT.md"
TEMPLATE_HELP = (
    "To turn crosstalk on, give the tool the panel file (.xpd) or a layout CSV with the columns "
    "`marker,cycle,fluor` (one row per antibody, for example `CD3e,1,ATTO550`). To get a CSV to fill in, "
    "run `pqe --layout-template <image>`, or use the \"Save a layout CSV template\" button in the app."
)
_ORDER = {"RAISE": 0, "LOWER": 0, "CHECK": 1}   # rows to change first, then rows to compare, then the rest

_SETTING_WORDS = {
    "dim_fraction": "Dim: signal below this share of the intensity range",
    "blank_fraction": "Near blank: signal below this share of the intensity range",
    "stain_index_low": "Low stain index: below",
    "positive_fraction_low": "Few stained pixels: positive fraction below",
    "saturation_high": "Too saturated: share of tissue pixels at the top of the range above",
    "background_high_fraction": "High background: background above this share of the range",
    "null_percentile": "Crosstalk cut-off: percentile of the unrelated pairs",
    "margin": "Crosstalk cut-off: margin added to that percentile",
    "control_markers": "Control marker for each target (used in follow-up panels)",
    "exclusive_pairs": "Markers that should not overlap",
    "n_windows": "Number of windows sampled",
    "window_um": "Window size (micrometers)",
    "min_windows": "Fewest tissue windows needed",
    "seed": "Random seed for choosing windows",
    "n_preview_windows": "Windows shown in previews",
    "highpass_sigma": "Fine-detail filter width (pixels)",
}
_SECTION_WORDS = {"detection": "Detection", "crosstalk": "Crosstalk", "markers": "Marker lists",
                  "advanced": "Sampling and filters"}


# ---------------------------------------------------------------------------- helpers
def _cell(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(header: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    out += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
    return out


def _num(v, nd: int = 2) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "-"
    return "-" if not math.isfinite(v) else f"{v:.{nd}f}"


def _pct(v, nd: int = 1) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "-"
    return "-" if not math.isfinite(v) else f"{100 * v:.{nd}f}%"


def _intensity_range(det: pd.DataFrame, meta: dict) -> float:
    """The range the numbers are fractions of: from meta, else recovered from the detection table."""
    try:
        r = float(meta.get("value_range"))
        if math.isfinite(r) and r > 0:
            return r
    except (TypeError, ValueError):
        pass
    if len(det) and {"signal_p99_5", "signal_fraction"} <= set(det.columns):
        ok = det[(det["signal_fraction"] > 0) & det["signal_p99_5"].notna()]
        if len(ok):
            return float((ok["signal_p99_5"] / ok["signal_fraction"]).median())
    return 255.0


def _is_float_image(meta: dict) -> bool:
    return "float" in str(meta.get("dtype", "")).lower()


def _saturation_checked(det: pd.DataFrame, meta: dict) -> bool:
    if _is_float_image(meta):
        return False
    if "saturation_fraction" in det.columns and len(det) and det["saturation_fraction"].isna().all():
        return False
    return True


def _antibody_rows(det: pd.DataFrame, meta: dict) -> pd.DataFrame:
    """Detection rows without the nuclear channel: rows to change first, then rows to compare, then the rest."""
    if "channel" not in det.columns:
        return det.reindex(columns=list(det.columns) or ["channel", "call"]).iloc[0:0]
    nuclear = {norm_name(n) for n in NUCLEAR_NAMES}
    if meta.get("nuclear_channel"):
        nuclear.add(norm_name(meta["nuclear_channel"]))
    d = det[~det["channel"].map(norm_name).isin(nuclear)].copy()
    d["_order"] = d["call"].astype(str).str.upper().map(_ORDER).fillna(2)
    return d.sort_values("_order", kind="stable").drop(columns="_order").reset_index(drop=True)


def _headline(d: pd.DataFrame, xt, meta: dict) -> list[str]:
    """The headline lines: exactly what the GUI banner shows (advice.headline), so the two cannot differ."""
    state = "on" if xt.enabled else "off"
    r = Results(folder=Path("."), detection=d, crosstalk=xt.table if xt.enabled else None,
                crosstalk_state=state, crosstalk_message=xt.message, status="complete",
                unmatched=list(xt.unmatched), saturation_checked=_saturation_checked(d, meta))
    return [line for line in headline(r).text.splitlines() if line]


def _followup_for(followups: list[dict], source: str, target: str) -> dict | None:
    for f in followups or []:
        if f.get("source") == source and f.get("target") == target:
            return f
    return None


def _figure_link(out_dir: Path, name: str, label: str) -> str:
    return f"[{label}]({name})" if (Path(out_dir) / name).exists() else f"{label} ({name}, not saved)"


# ---------------------------------------------------------------------------- sections
def _no_saturation_reason(meta: dict) -> str:
    if _is_float_image(meta):
        return ("this image holds decimal (float) values, so there is no fixed top of the range to test against.")
    return "saturation could not be measured for this image (the table has no saturation values)."


def _run_details(meta: dict, d: pd.DataFrame, saturation_checked: bool) -> list[str]:
    rng = _intensity_range(d, meta)
    lines = []
    for label, key in (("Image", "image"), ("Panel file", "panel"), ("Pixel size (micrometers)", "pixel_size_um"),
                       ("Pixel type", "dtype")):
        if meta.get(key) not in (None, ""):
            lines.append(f"- {label}: {meta[key]}")
    if meta.get("n_windows") is not None:
        lines.append(f"- Windows sampled: {meta['n_windows']}"
                     + (f", each {meta['window_px']} pixels wide" if meta.get("window_px") else ""))
    asked = meta.get("n_windows_requested")
    if meta.get("n_windows") is not None and asked is not None and int(meta["n_windows"]) < int(asked):
        lines.append(f"- Only {meta['n_windows']} of the {asked} requested windows could be used: that is every window "
                     "in this image that is mostly tissue. The results rest on fewer windows than usual, so a call "
                     "close to a cut-off is less certain.")
    basis = str(meta.get("range_basis") or "").strip()
    lines.append(f"- Intensity range used: 0 to {rng:g}" + (f" ({basis})" if basis else "")
                 + ". Every cut-off is a share of this range.")
    if not saturation_checked:
        lines.append("- Saturation not checked: " + _no_saturation_reason(meta))
    for label, key in (("Tool version", "version"), ("Started", "started"), ("Finished", "finished")):
        if meta.get(key) not in (None, ""):
            lines.append(f"- {label}: {meta[key]}")
    return lines


def _next_run_section(d: pd.DataFrame, meta: dict, out_dir: Path, saturation_checked: bool,
                      detection=None) -> list[str]:
    rng = _intensity_range(d, meta)
    out = ["## Next run", "",
           "One instruction per antibody. Rows to change come first. These are directions only: the report never "
           "gives a dilution or an exposure time. The numbers beside each instruction are the evidence:", "",
           "- **Signal**: the brightness reached by the brightest 0.5% of tissue pixels, out of the intensity range.",
           "- **Stain index**: how far stained pixels stand above the background, measured in units of how much the "
           "background itself varies. Higher is cleaner.",
           "- **Positive**: the share of tissue pixels that are stained.",
           "- **Saturated**: the share of tissue pixels at the very top of the range, where bright detail is lost.", ""]
    rows = []
    for _, r in d.iterrows():
        sat = _pct(r.get("saturation_fraction")) if saturation_checked else "not checked"
        rows.append([r["channel"], r["next_run"], evidence_detail(r, saturation_checked, rng, detection),
                     f"{fmt_value(r.get('signal_p99_5'))} of {rng:g}", _num(r.get("stain_index"), 1),
                     _pct(r.get("positive_fraction"), 2), sat])
    out += _table(["Antibody", "Next run", "Why", "Signal", "Stain index", "Positive", "Saturated"], rows)
    out += ["", "Figures: " + _figure_link(out_dir, "stain_qc_detection.png", "detection") + "; "
            + _figure_link(out_dir, "stain_qc_intensity_ridges.png", "intensity distributions") + "."]
    return out


def _not_judged(xt) -> bool:
    return (not xt.enabled) and (xt.message or "").startswith(NOT_JUDGED_MESSAGE[:40])


def _crosstalk_section(xt, followups: list[dict], out_dir: Path) -> list[str]:
    out = ["## Crosstalk", ""]
    if _not_judged(xt):
        return out + [xt.message, "",
                      f"The cut-offs come from at least {_xt.MIN_CONTROL_PAIRS} control pairs (two markers two or more "
                      "cycles apart in different fluors, which cannot leak into each other). With fewer, the 95th "
                      "percentile that sets the cut-off is decided by one pair. The pair table "
                      "(stain_qc_crosstalk_pairs.csv) lists every pair as \"not_judged\" and its spill and texture "
                      "numbers are for reference only. This is not a clean result."]
    if not xt.enabled:
        return out + [xt.message or _xt.NO_LAYOUT_MESSAGE, "", TEMPLATE_HELP]
    pairs = followup_pairs(xt.table)
    out.append(xt.message or "Crosstalk checked.")
    if not pairs:
        out += ["", "No channel pairs were flagged."]
    else:
        out += ["", "Each pair is a candidate: the numbers say the target may be carrying some of the source's "
                "signal. The follow-up figure shows whether it does. Two markers that really are on the "
                "same cells can also be flagged, so compare with what you expect.", ""]
        t = xt.table
        rows, notes = [], []
        for s, tg in pairs:
            row = t[(t["source"] == s) & (t["target"] == tg)].iloc[0]
            flagged = t[t["source"].isin([s, tg]) & t["target"].isin([s, tg]) & t["flag"].astype(bool)]
            reason = flag_reason_text(row["flag_reason"]) or (
                flag_reason_text(flagged["flag_reason"].iloc[0]) if len(flagged) else "")
            fu = _followup_for(followups, s, tg)
            name = (fu or {}).get("figure") or f"followup_{safe_name(s)}_to_{safe_name(tg)}.png"
            rows.append([f"{s} -> {tg}", kind_text(row["kind"]), reason, _num(row["spill"]),
                         _num(row["reverse_spill"]), _num(row["highpass_corr"]),
                         _figure_link(out_dir, name, "follow-up panel")])
            if fu and fu.get("target_where_source_bright") is not None \
                    and fu.get("target_where_source_low") is not None:
                note = (f"- {s} -> {tg}: {tg} reads {fmt_value(fu['target_where_source_bright'])} where {s} is "
                        f"bright and {fmt_value(fu['target_where_source_low'])} where {s} is low")
                if fu.get("target_where_control_bright") is not None and fu.get("control"):
                    note += (f"; where the control marker {fu['control']} is bright it reads "
                             f"{fmt_value(fu['target_where_control_bright'])}")
                notes.append(note + ".")
        out += _table(["Pair (source -> target)", "Likely cause", "Why flagged", "Spill, source to target",
                       "Spill, target to source", "Fine-detail match", "Evidence"], rows)
        out += ["", "- **Spill, source to target**: how much more of the target shows up where the source is brightest than "
                "where the source is dim, as a share of the target's own range. Real crosstalk goes mostly one "
                "way, so compare with **Spill, target to source**.",
                "- **Fine-detail match**: how closely the small-scale texture of the two channels agrees "
                "(1 means identical, 0 means unrelated). Crosstalk copies the source's cell outlines."]
        if notes:
            out += ["", "What the follow-up panels show:", ""] + notes
    out += ["", _figure_link(out_dir, "stain_qc_crosstalk_asymmetry.png", "Crosstalk figure for all pairs") + "."]
    return out


def _exclusive_section(ex: pd.DataFrame, det: pd.DataFrame | None = None, intensity_range: float = 255.0,
                       settings: DetectionSettings | None = None) -> list[str]:
    out = ["## Markers that should not overlap", ""]
    if ex is None or len(ex) == 0:
        return out + ["No pairs could be checked (a marker in the list is missing from the image)."]
    out += ["For each pair, the share of tissue pixels positive for both markers is compared with what chance "
            "would give if the markers were unrelated. The ratio is observed divided by chance: below 1 means "
            "the markers sit together less than chance, which is what you expect for markers that should not "
            "overlap; near or above 1 deserves a look at the image. A pair with a channel that is almost blank is "
            "not tested: a ratio made of noise says nothing.", ""]
    near_blank = blank_markers(det, intensity_range, settings)
    rows, left_out = [], False
    for _, r in ex.iterrows():
        blank = str(r["marker_a"]) in near_blank or str(r["marker_b"]) in near_blank
        words, _tag = exclusive_verdict(r["lift"], blank)
        left_out = left_out or blank
        rows.append([r["marker_a"], r["marker_b"], words, _pct(r["pos_a"]), _pct(r["pos_b"]),
                     "-" if blank else _pct(r["co_positive"], 2), "-" if blank else _pct(r["expected_if_independent"], 2),
                     "-" if blank else _num(r["lift"])])
    out += _table(["Marker A", "Marker B", "Result", "A positive", "B positive", "Both positive", "Expected by chance",
                   "Ratio"], rows)
    if any(row[-1] == "-" for row in rows):
        out += ["", "A dash (-) means the pair was not tested (a channel is almost blank) or there were not enough "
                    "pixels to compare."]
    return out


def _suggestions_section(xt) -> list[str]:
    out = ["## Cycle-order suggestions", ""]
    if _not_judged(xt):
        return out + ["Not available: crosstalk could not be judged for this panel, so cycle order was not examined."]
    if not xt.enabled:
        return out + ["Not available: crosstalk is off, so cycle order was not examined."]
    if not xt.suggestions:
        return out + ["None: no flagged pair looks like carryover from the previous cycle."]
    return out + [f"- {s}" for s in xt.suggestions]


def _unmatched_section(xt, meta: dict) -> list[str]:
    out = ["## Markers not in the panel file", ""]
    if not xt.enabled and not _not_judged(xt):
        return out + ["Not checked: there is no panel file to compare with."]
    unmatched, image_only = list(xt.unmatched or []), list(meta.get("image_only") or [])
    if not unmatched and not image_only:
        return out + ["None: every marker in the panel file was found in the image, and the reverse."]
    if unmatched:
        out.append("In the panel file but not found in the image (these lose crosstalk checks): "
                   + ", ".join(unmatched))
    if image_only:
        out.append("In the image but not in the panel file (not checked for crosstalk): " + ", ".join(image_only))
    return out


def _setting_value(v, name: str = "") -> str:
    if name == "control_markers" and isinstance(v, list) and v and all(isinstance(p, (list, tuple)) and len(p) == 2
                                                                      for p in v):
        return "; ".join(f"{p[0]} (control: {p[1]})" for p in v)
    if isinstance(v, list) and v and all(isinstance(p, (list, tuple)) for p in v):
        return "; ".join(" with ".join(str(x) for x in p) for p in v)
    return str(v)


def _settings_section(settings: Settings) -> list[str]:
    out = ["## Settings used", "", "Every value in force for this run. Cut-offs are shares of the intensity range "
           "unless noted.", ""]
    for sec in fields(settings):
        obj = getattr(settings, sec.name)
        if not is_dataclass(obj):
            continue
        out += [f"### {_SECTION_WORDS.get(sec.name, sec.name)}", ""]
        out += [f"- {_SETTING_WORDS.get(f.name, f.name)} ({f.name}): {_setting_value(getattr(obj, f.name), f.name)}"
                for f in fields(obj)]
        out.append("")
    return out[:-1]


def _rules_section(saturation_checked: bool, meta: dict) -> list[str]:
    out = ["## How the calls are made", "",
           "These rules are lab-defined, not published. They were calibrated on one tonsil section and are a "
           "screening aid, not a pass/fail test: the call says where to look, and you decide.", "",
           "Words below, in the words of the Next run table: RAISE = " + next_run_text("RAISE", "KEEP") + " (with "
           "LENGTHEN: " + next_run_text("RAISE", "LENGTHEN") + "); LOWER = " + next_run_text("LOWER", "KEEP")
           + " (with SHORTEN: " + next_run_text("LOWER", "SHORTEN") + "); CHECK = " + next_run_text("CHECK") + "; "
           "OK = " + next_run_text("OK") + ". signal_fraction is the signal as a share of the intensity range. "
           "Otsu is an automatic cut between stained and background pixels. SD is the spread of the background "
           "(standard deviation).", ""]
    out += [f"- **{k.replace('_', ' ')}**: {v}" for k, v in _det.RULE_TEXT.items()]
    out += ["", "Crosstalk:", ""]
    out += [f"- **{k.replace('_', ' ')}**: {v}" for k, v in _xt.RULE_TEXT.items()]
    out += ["", "Choices that are easy to miss:", "",
            "- Near-blank channels are never flagged as a crosstalk source or target, but they still count among "
            "the unrelated pairs that set the flag cut-off.",
            "- For images deeper than 8-bit, the intensity range is inferred from the brightest sampled pixel "
            "(rounded up to 10, 12, 14 or 16 bits), because a 12-bit image stored in a 16-bit file would otherwise "
            "look dim everywhere. The range used is stated at the top of this report.",
            "- A sparse channel has no spill row. If a channel's 99th percentile is zero or no higher than its "
            "median, it has no bright group to compare, so it is never a spill source and its pairs are judged "
            "on fine-detail match alone. A channel that is saturated over many pixels is not sparse and keeps "
            "its row.",
            "- Calls describe the sampled tissue only. A target that is absent from this tissue section reads as "
            "\"raise\" or \"compare\"; that is a statement about the section, not about the antibody."]
    if not saturation_checked:
        out.append("- For this image saturation was not checked (" + _no_saturation_reason(meta).rstrip(".") + "), so no antibody "
                   "is called \"lower\" for saturation.")
    return out


def _warnings_section(warnings: list[str]) -> list[str]:
    return ["## Warnings", ""] + ([f"- {w}" for w in warnings] if warnings else ["None."])


# ---------------------------------------------------------------------------- entry point
def write_report(out_dir, det: pd.DataFrame, xt, exclusive: pd.DataFrame, followups: list[dict],
                 settings: Settings, meta: dict, warnings: list[str]) -> Path:
    """Write STAIN_QC_REPORT.md into out_dir and return its path.

    meta keys: image, panel, pixel_size_um, dtype, n_windows, window_px, version, started, finished.
    Optional keys: value_range and range_basis (the intensity range used and how it was chosen: from
    Sample.value_range and Sample.range_basis), nuclear_channel, and image_only (image channels with no
    match in the panel file).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = dict(meta or {})
    saturation_checked = _saturation_checked(det, meta)
    d = _antibody_rows(det, meta)
    lines = ["# Phenocycler QC Eval: staining report", ""]
    for line in _headline(d, xt, meta):
        lines += [line, ""]
    lines += _run_details(meta, d, saturation_checked) + [""]
    for block in (_next_run_section(d, meta, out_dir, saturation_checked, settings.detection), _crosstalk_section(xt, followups, out_dir),
                  _exclusive_section(exclusive, det, float(meta.get("value_range") or 255.0), settings.detection),
                  _suggestions_section(xt), _unmatched_section(xt, meta),
                  _settings_section(settings), _rules_section(saturation_checked, meta),
                  _warnings_section(list(warnings or []))):
        lines += block + [""]
    path = out_dir / REPORT_NAME
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path
