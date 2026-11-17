"""Per-antibody detection metrics, the call (RAISE / LOWER / CHECK / OK) and the exposure direction.

The rules are lab-defined, not published. Every threshold is a fraction of the image's range
(Sample.value_range) and comes from DetectionSettings.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from skimage.filters import threshold_otsu

from .advice import next_run_text
from .settings import DetectionSettings
from .tissue import Sample

RULE_TEXT: dict[str, str] = {
    "tissue pixels": "Pixels where any channel is above zero, taken from the sampled windows.",
    "otsu_threshold": "The automatic Otsu cut on raw intensity, per antibody. Above it counts as positive, "
                      "at or below as background.",
    "positive_fraction": "Share of tissue pixels above the Otsu cut.",
    "background": "Mean and standard deviation of tissue pixels at or below the Otsu cut.",
    "signal": "The 99.5th percentile of tissue pixels. signal_fraction is signal divided by the image range.",
    "stain_index": "(mean of positive pixels - mean of background) / (2 x background standard deviation). "
                   "The background SD is never taken below 0.2% of the range, so a flat background cannot "
                   "inflate the index.",
    "saturation_fraction": "Share of tissue pixels sitting at the top of the range. Not checked for float images.",
    "near_blank": "Signal below the near-blank fraction of the range: the antibody left almost nothing to see.",
    "LOWER": "Saturation is checkable and the saturated share is above the saturation limit.",
    "RAISE": "Two or more weaknesses: dim signal, weak stain index, too few positive pixels.",
    "CHECK": "Exactly one weakness. It may be real biology (for example a rare target), so compare with "
             "what you expect.",
    "OK": "No weakness and no saturation.",
    "exposure": "LOWER: SHORTEN if the background is low, otherwise KEEP (a high background is a concentration "
                "problem). RAISE: LENGTHEN if the background is low, otherwise KEEP. CHECK and OK: KEEP.",
}

_MIN_SD_FRACTION = 0.002
COLUMNS = [
    "channel", "next_run", "reason", "call", "exposure", "signal_p99_5", "signal_fraction",
    "background_mean", "background_fraction", "background_sd", "positive_mean", "positive_fraction",
    "stain_index", "saturation_fraction", "dynamic_range_used", "otsu_threshold", "near_blank",
]


def _otsu(values: np.ndarray) -> float:
    values = values[np.isfinite(values)]  # NaN / inf never enter the threshold
    if values.size == 0 or values.min() == values.max():
        return float(values.max()) if values.size else 0.0
    return float(threshold_otsu(values))


def _channel_metrics(vals: np.ndarray, rng: float, saturation_checkable: bool) -> dict:
    if vals.size == 0:
        nan = float("nan")
        return dict(signal_p99_5=nan, signal_fraction=nan, background_mean=nan, background_fraction=nan,
                    background_sd=nan, positive_mean=nan, positive_fraction=nan, stain_index=nan,
                    saturation_fraction=nan, dynamic_range_used=nan, otsu_threshold=nan, near_blank=False)
    thr = _otsu(vals)
    pos = vals > thr
    bg = vals[~pos]
    bg_mean = float(bg.mean(dtype=np.float64)) if bg.size else 0.0
    bg_sd = float(bg.std(dtype=np.float64)) if bg.size else 0.0
    n_pos = int(pos.sum())
    pos_mean = float(vals[pos].mean(dtype=np.float64)) if n_pos else bg_mean
    signal = float(np.percentile(vals, 99.5))
    sd_floor = max(bg_sd, _MIN_SD_FRACTION * rng)
    return dict(
        signal_p99_5=signal, signal_fraction=signal / rng,
        background_mean=bg_mean, background_fraction=bg_mean / rng, background_sd=bg_sd,
        positive_mean=pos_mean, positive_fraction=n_pos / vals.size,
        stain_index=(pos_mean - bg_mean) / (2.0 * sd_floor),
        saturation_fraction=float((vals >= rng).mean()) if saturation_checkable else float("nan"),
        # spread of tissue values (max - min) as a share of the range
        dynamic_range_used=float((float(vals.max()) - float(vals.min())) / rng),
        otsu_threshold=thr, near_blank=False,  # near_blank is set by the caller (needs the setting)
    )


def concentration_call(row, s: DetectionSettings, saturation_checkable: bool) -> tuple[str, str]:
    """(call, reason) from one row of metrics. `row` is a mapping or a pandas Series."""
    if any(pd.isna(row[k]) for k in ("signal_fraction", "stain_index", "positive_fraction")):
        return "CHECK", "not enough tissue pixels to judge"
    sat = row["saturation_fraction"]
    if saturation_checkable and not pd.isna(sat) and sat > s.saturation_high:
        return "LOWER", f"saturated ({sat * 100:.2f}% of tissue pixels at the top of the range)"
    weak = []
    if row["signal_fraction"] < s.dim_fraction:
        weak.append(f"dim (signal {row['signal_fraction'] * 100:.1f}% of range)")
    if row["stain_index"] < s.stain_index_low:
        weak.append(f"low stain index ({row['stain_index']:.1f})")
    if row["positive_fraction"] < s.positive_fraction_low:
        weak.append(f"few positive pixels ({row['positive_fraction'] * 100:.2f}%)")
    if len(weak) >= 2:
        return "RAISE", "; ".join(weak)
    if len(weak) == 1:
        return "CHECK", weak[0] + " (could be a rare target; compare with expected biology)"
    return "OK", "strong signal, high stain index, not saturated"


def exposure_direction(call: str, background_fraction: float, s: DetectionSettings) -> str:
    if call in ("LOWER", "RAISE"):
        if bool(background_fraction < s.background_high_fraction):  # NaN compares False -> KEEP
            return "SHORTEN" if call == "LOWER" else "LENGTHEN"
    return "KEEP"


def detection_table(smp: Sample, s: DetectionSettings, progress=None) -> pd.DataFrame:
    """One row per non-nuclear channel. progress(done, total), when given, is told after each antibody (it
    only reports; the table is the same with or without it)."""
    px = smp.pixels
    tissue = (px != 0).any(axis=1)  # (N, h, w)
    rng = float(smp.value_range)
    rows = []
    total = sum(1 for n in smp.names if n != smp.nuclear_name)
    for c, name in enumerate(smp.names):
        if name == smp.nuclear_name:
            continue
        vals = px[:, c][tissue]
        if vals.dtype.kind == "f":
            vals = vals[np.isfinite(vals)]
        m = _channel_metrics(vals, rng, smp.saturation_checkable)
        m["near_blank"] = bool(m["signal_fraction"] < s.blank_fraction) if vals.size else False
        call, reason = concentration_call(m, s, smp.saturation_checkable)
        exposure = exposure_direction(call, m["background_fraction"], s)
        rows.append({"channel": name, "next_run": next_run_text(call, exposure), "reason": reason,
                     "call": call, "exposure": exposure, **m})
        if progress is not None:
            progress(len(rows), total)
    return pd.DataFrame(rows, columns=COLUMNS)
