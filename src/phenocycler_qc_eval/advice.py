"""The words people read: next-run wording and the one-line headline."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .settings import DetectionSettings, default_settings, settings_from_dict

_NEXT_RUN = {
    ("RAISE", "LENGTHEN"): "Raise concentration (or lengthen exposure)",
    ("RAISE", "KEEP"): "Raise concentration",
    ("LOWER", "SHORTEN"): "Lower concentration (or shorten exposure)",
    ("LOWER", "KEEP"): "Lower concentration",
}
_MAX_PAIRS_LISTED = 3


def next_run_text(call: str, exposure: str = "KEEP") -> str:
    """One plain instruction for the next run. Directions only, never a number."""
    call = str(call).upper()
    exposure = str(exposure).upper()
    if call in ("RAISE", "LOWER"):
        # A mismatched exposure (e.g. RAISE + SHORTEN) gets the concentration advice alone.
        return _NEXT_RUN.get((call, exposure), _NEXT_RUN[(call, "KEEP")])
    if call == "CHECK":
        return "Compare with expected biology"
    return "Keep"


@dataclass
class Headline:
    """Two short lines (titration, crosstalk) plus notes. `text` joins the lines."""

    titration: str
    crosstalk: str
    notes: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return self.titration + "\n" + self.crosstalk


def plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _flag_mask(crosstalk):
    flag = crosstalk["flag"]
    return flag if flag.dtype == bool else flag.astype(str).str.lower().eq("true")


def _flagged_pairs(crosstalk) -> list[tuple[str, str]]:
    """Flagged (source, target) pairs, strongest spill first."""
    if crosstalk is None or len(crosstalk) == 0 or "flag" not in crosstalk.columns:
        return []
    rows = crosstalk[_flag_mask(crosstalk)]
    if "spill" in rows.columns:
        rows = rows.sort_values("spill", ascending=False, kind="stable")
    kinds = rows["kind"].astype(str) if "kind" in rows.columns else [""] * len(rows)
    out, seen = [], set()
    for s, t, kind in zip(rows["source"], rows["target"], kinds):
        s, t = str(s), str(t)
        key = frozenset((s, t)) if kind == "same_cycle_spectral" else (s, t)   # same-cycle rows run both ways
        if key not in seen:
            seen.add(key)
            out.append((s, t))
    return out


def headline(r) -> Headline:
    """Two lines and notes for a loaded Results object."""
    calls = r.detection["call"].astype(str).str.upper()
    n_change = int(calls.isin(["RAISE", "LOWER"]).sum())
    n_check = int((calls == "CHECK").sum())
    n_all = len(calls)
    n_keep = n_all - n_change - n_check

    no_results_yet = n_all == 0 and r.status != "complete"
    if no_results_yet:
        first = "No results yet."
    elif n_all == 0:
        first = "Next run: no antibody results."
    else:
        first = (f"Next run: {n_change} to change, {n_check} to compare with expected biology, "
                 f"{n_keep} to keep (of {plural(n_all, 'antibody', 'antibodies')}).")

    state = r.crosstalk_state
    note_in_line = False
    if no_results_yet:
        second = ""
    elif state == "off" and r.crosstalk_not_judged:
        second = CROSSTALK_NOT_JUDGED_LINE
    elif state == "off":
        second = "Crosstalk: not checked."
    elif r.crosstalk is None and r.status == "running" and r.incomplete_note:
        second = r.incomplete_note              # "Antibody results ready. Checking crosstalk..." and its early form
        note_in_line = True
    elif r.crosstalk is None:
        second = "Crosstalk: still being checked." if r.status == "running" else "Crosstalk: not finished."
    else:
        flagged = _flagged_pairs(r.crosstalk)
        if not flagged:
            second = "Crosstalk: no pairs flagged. This does not prove the channels are clean."
        else:
            s, t = flagged[0]
            second = (f"Crosstalk: {plural(len(flagged), 'channel pair', 'channel pairs')} flagged, "
                      f"strongest {s} -> {t}.")

    notes: list[str] = []
    if r.unmatched:
        notes.append("In the panel file but not found in the image: " + ", ".join(r.unmatched))
    if not r.saturation_checked:
        notes.append("Saturation not checked (float image)")
    # Crosstalk being off is said once in the headline and once on the Crosstalk tab (with the template button): no
    # banner note, which would be a third and fourth telling.
    if r.incomplete_note and not note_in_line:
        notes.append(r.incomplete_note)
    return Headline(titration=first, crosstalk=second, notes=notes)


# ---- plain wording for the result tabs ---------------------------------------
_KINDS = {
    "adjacent_cycle_carryover": "Carryover from the previous cycle",
    "same_cycle_spectral": "Same-cycle bleed-through",
}
_FLAG_REASONS = {
    "asymmetry": "one direction is much stronger than the other",
    "texture": "fine detail in the target copies the source",
    "asymmetry+texture": "one direction is much stronger and fine detail in the target copies the source",
}


def kind_text(kind) -> str:
    """Likely cause of a flagged pair, in words (never the raw id)."""
    return _KINDS.get(str(kind), "Cause unclear")


_KINDS_SHORT = {"adjacent_cycle_carryover": "Carryover", "same_cycle_spectral": "Same cycle"}


def kind_short(kind) -> str:
    """The likely cause as one or two words, for a table cell (the detail pane has the whole phrase)."""
    return _KINDS_SHORT.get(str(kind), "Unclear")


def flag_reason_text(reason) -> str:
    return _FLAG_REASONS.get(str(reason), "")


_D = default_settings().detection  # default cut-offs live in settings.py


def detection_settings_of(run_settings) -> DetectionSettings:
    """The detection cut-offs a run used (from run_info's settings dict); defaults for anything missing or bad."""
    data = run_settings.get("detection") if isinstance(run_settings, dict) else None
    if not isinstance(data, dict):
        return default_settings().detection
    settings, _warnings = settings_from_dict({"detection": data})   # bad values fall back one by one
    return settings.detection


def _f(value, default=float("nan")) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def fmt_value(value) -> str:
    """Shared number format: 3 significant digits below 1000, whole numbers from 1000 up."""
    v = _f(value)
    if v != v:
        return "-"
    return f"{v:.0f}" if abs(v) >= 1000 else format(v, ".3g")


def _findings(row, saturation_checked: bool, intensity_range: float,
              detection: DetectionSettings | None = None) -> dict[str, bool]:
    """Which problems the numbers show, in priority order. Reads no free text."""
    signal, rng = _f(row.get("signal_p99_5")), _f(intensity_range, 255.0)
    sat, pos, si = _f(row.get("saturation_fraction")), _f(row.get("positive_fraction")), _f(row.get("stain_index"))
    d = detection or _D
    return {
        "sat": saturation_checked and sat > d.saturation_high,
        "dim": rng > 0 and signal < d.dim_fraction * rng,
        "few": pos < d.positive_fraction_low,
        "weak": si < d.stain_index_low,
    }


def _neutral(row, intensity_range: float) -> str:
    return (f"Signal {fmt_value(row.get('signal_p99_5'))} of {intensity_range:g}, "
            f"stain index {_f(row.get('stain_index')):.1f}")


def _is_ok(row) -> bool:
    return str(row.get("call", "")).upper() not in ("RAISE", "LOWER", "CHECK")


def short_evidence(row, saturation_checked: bool = True, intensity_range: float = 255.0,
                   detection: DetectionSettings | None = None) -> str:
    """One short phrase (45 characters or fewer) for the table's Evidence column."""
    c = _findings(row, saturation_checked, intensity_range, detection)
    if c["sat"]:
        return f"{100 * _f(row.get('saturation_fraction'), 0):.1f}% of tissue pixels saturated"
    if c["dim"]:
        return f"Dim: brightest 0.5% reach {fmt_value(row.get('signal_p99_5'))} of {intensity_range:g}"
    if c["few"]:
        return f"Few stained pixels ({100 * _f(row.get('positive_fraction'), 0):.2f}%)"
    if c["weak"]:
        return f"Low stain index ({_f(row.get('stain_index')):.1f})"
    if _is_ok(row):
        return f"Signal {fmt_value(row.get('signal_p99_5'))} of {intensity_range:g}, no problem found"
    return _neutral(row, intensity_range)


def evidence_detail(row, saturation_checked: bool = True, intensity_range: float = 255.0,
                    detection: DetectionSettings | None = None, compare_hint: bool = True) -> str:
    """The full evidence sentence(s) for the detail pane (wrapped, never cut). The window passes
    compare_hint=False and adds compare_guidance() instead; the report keeps the one-line hint."""
    c = _findings(row, saturation_checked, intensity_range, detection)
    parts: list[str] = []
    if c["sat"]:
        parts.append(f"{100 * _f(row.get('saturation_fraction'), 0):.1f}% of tissue pixels are "
                     "saturated (at the top of the range), so bright detail is lost.")
    if c["dim"]:
        parts.append(f"Dim: the brightest 0.5% of pixels reach only {fmt_value(row.get('signal_p99_5'))} "
                     f"of {intensity_range:g}.")
    if c["few"]:
        parts.append(f"Few stained pixels: only {100 * _f(row.get('positive_fraction'), 0):.2f}% of "
                     "tissue is above the cut between stained and background.")
    if c["weak"]:
        d = detection or _D
        parts.append(f"Low stain index ({_f(row.get('stain_index')):.1f}; weak below {d.stain_index_low:g}): "
                     "stained pixels do not stand far enough above the background.")
    if not parts:
        tail = "no problem found." if _is_ok(row) else "no single measure is past its cut-off."
        parts.append(f"Signal {fmt_value(row.get('signal_p99_5'))} of {intensity_range:g}; "
                     f"stain index {_f(row.get('stain_index')):.1f}; {tail}")
    if compare_hint and str(row.get("call", "")).upper() == "CHECK":
        parts.append("This could be a rare target; compare with expected biology.")
    return " ".join(parts)


_FIGURE_TITLES = {
    "stain_qc_crosstalk_asymmetry.png": "Crosstalk heatmap: all channel pairs",
    "stain_qc_detection.png": "Marker detection: stained vs background",
    "stain_qc_intensity_ridges.png": "Intensity distributions",
}


def figure_title(name: str) -> str:
    """A readable title for a saved figure file."""
    if name in _FIGURE_TITLES:
        return _FIGURE_TITLES[name]
    m = re.fullmatch(r"followup_(.+)_to_(.+)\.png", name)
    if m:
        source, target = (g.replace("_", " ") for g in m.groups())      # file names carry "Collagen_IV"
        return f"Follow-up panel: {source} into {target}"
    return name


# ---- severity, compare rows and the dim state --------------------------------
def severity(row, saturation_checked: bool = True, intensity_range: float = 255.0,
             detection: DetectionSettings | None = None) -> tuple[float, str]:
    """How far past its cut-off the worst finding is (a multiple of the cut-off) and a word for it.

    Used only to order rows inside a group and to put a word in front of the evidence. It reads the cut-offs the run
    used and changes no call."""
    d = detection or _D
    rng = _f(intensity_range, 255.0)
    c = _findings(row, saturation_checked, rng, detection)
    ratios = []
    if c["sat"]:
        ratios.append(_f(row.get("saturation_fraction"), 0.0) / max(d.saturation_high, 1e-12))
    if c["dim"]:
        ratios.append(d.dim_fraction * rng / max(_f(row.get("signal_p99_5"), 0.0), 1e-9))
    if c["few"]:
        ratios.append(d.positive_fraction_low / max(_f(row.get("positive_fraction"), 0.0), 1e-9))
    if c["weak"]:
        ratios.append(d.stain_index_low / max(_f(row.get("stain_index"), 0.0), 0.05))
    score = min(max(ratios), 99.0) if ratios else 0.0
    return score, ("Strong" if score >= 4 else "Moderate" if score >= 2 else "Mild")


def signal_level(row, intensity_range: float = 255.0, detection: DetectionSettings | None = None) -> str:
    """"blank" (near-blank: almost nothing to see), "dim" or "ok", by the run's own cut-offs."""
    d = detection or _D
    rng, signal = _f(intensity_range, 255.0), _f(row.get("signal_p99_5"))
    if signal != signal or rng <= 0:
        return "ok"
    if signal < d.blank_fraction * rng:
        return "blank"
    return "dim" if signal < d.dim_fraction * rng else "ok"


def control_marker_for(marker, controls, names=None) -> str | None:
    """The control marker the settings pair with `marker` ("CD163 should look like CD68"), named as the image
    names it. None when there is none, or when it is not a channel of this image (names given)."""
    from .results import norm_name

    by_norm = {norm_name(n): str(n) for n in names} if names is not None else None
    wanted = norm_name(marker)
    for pair in controls or []:
        if len(pair) == 2 and norm_name(pair[0]) == wanted and norm_name(pair[1]) != wanted:
            if by_norm is None:
                return str(pair[1])
            if norm_name(pair[1]) in by_norm:
                return by_norm[norm_name(pair[1])]
    return None


def compare_guidance(row, saturation_checked: bool = True, intensity_range: float = 255.0,
                     detection: DetectionSettings | None = None, control: str | None = None) -> str:
    """Two or three short sentences for a "Compare with expected biology" row: which measure is weak, what to
    compare, and what each outcome means for the next run. Directions only, never an amount."""
    marker = str(row.get("channel", "This antibody"))
    c = _findings(row, saturation_checked, intensity_range, detection)
    weak = ("the signal is dim" if c["dim"] else "few pixels are stained" if c["few"]
            else "the stain index is low" if c["weak"] else "")
    lead = f"Only one measure is weak: {weak}. " if weak else ""
    if control:
        look = f"Compare the tiles: {marker} should look like {control}, bright in much the same places."
    else:
        look = (f"Compare the tiles with where you expect {marker} in this tissue: its bright cells should sit "
                "in the right structures.")
    outcome = (f"If {marker} is clearly weaker than expected, raise concentration at the next run. "
               "If the pattern is right and the marker is simply rare here, keep it.")
    return f"{lead}{look} {outcome}"


# ---- crosstalk and overlap verdicts, in plain words --------------------------------
NO_PAIRS_FLAGGED = "No pairs flagged. This does not prove the channels are clean."
CROSSTALK_CAVEAT = ("Cut-offs are lab-defined and were tuned on tonsil. Not flagged does not mean clean: "
                    "a weak leak can be missed.")
FOLLOWUP_ACTION = ("If it is real, change the antibody or the cycle order. If it is unclear, look at the raw "
                   "channels.")
EXCLUSIVE_CHECK_RATIO = 1.0          # overlap at or above what chance gives is higher than expected
_VERDICT_REASON = {"asymmetry": "one direction stronger", "texture": "fine detail copied",
                   "asymmetry+texture": "one direction stronger, fine detail copied"}
_VERDICT_REASON_SHORT = {"asymmetry": "one-way", "texture": "fine detail", "asymmetry+texture": "one-way, fine detail"}
NOT_TESTED_BLANK = "Not tested: near-blank channel"
NOT_TESTED_FEW_CONTROLS = "Not tested: too few unrelated channel pairs to set a cut-off"
CROSSTALK_NOT_JUDGED_LINE = ("Crosstalk: could not be judged for this panel (too few unrelated channel pairs to set a "
                             "cut-off), so no pair was flagged or cleared.")


def _truthy(v) -> bool:
    return v if isinstance(v, bool) else str(v).strip().lower() in ("true", "1")


def pair_verdict(row, short: bool = False) -> tuple[str, str]:
    """(words, tag) for one crosstalk-table row. tag is "flagged", "clear" or "untested". `short` gives the words a
    table cell needs (the pane beside the table has the whole reason)."""
    if _truthy(row.get("source_near_blank")) or _truthy(row.get("target_near_blank")):
        return NOT_TESTED_BLANK, "untested"
    if str(row.get("flag_reason", "")) == "not_judged":
        return NOT_TESTED_FEW_CONTROLS, "untested"
    if _truthy(row.get("flag")):
        why = (_VERDICT_REASON_SHORT if short else _VERDICT_REASON).get(str(row.get("flag_reason", "")), "")
        return "Flagged" + (f": {why}" if why else ""), "flagged"
    return "Not flagged", "clear"


def _over(value, cut) -> str:
    v, c = _f(value), _f(cut)
    if v != v or c != c:
        return "-"
    return f"{v:.2f} (cut-off {c:.2f}, {'over' if v > c else 'under'})"


def pair_numbers(row) -> list[tuple[str, str]]:
    """The two numbers that decide a flag, each next to its cut-off (the run's own, from the table)."""
    asym_cut = row.get("asym_cut")
    if _f(asym_cut) != _f(asym_cut):                  # an older table names this cut-off differently
        asym_cut = row.get("null_95pct_plus_margin")
    excess = _over(row.get("asymmetry"), asym_cut)
    if _f(row.get("asymmetry")) < 0:
        # Below zero means the leak runs the other way: say "none" here, not a negative number with no meaning.
        excess = "none" + excess[excess.index(" ("):] if " (" in excess else "none"
    return [
        ("Fine-detail match", _over(row.get("highpass_corr"), row.get("highpass_cut"))),
        ("One-way excess", excess),
    ]


def spill_sentence(row) -> str:
    """Spill in both directions in one sentence, each number named for the direction it describes."""
    def plain(v):
        v = _f(v)
        return "-" if v != v else f"{v:.2f}"

    s, t = row.get("source"), row.get("target")
    return (f"Spill (a share of the source's range) from {s} into {t} is {plain(row.get('spill'))}; "
            f"from {t} into {s} it is {plain(row.get('reverse_spill'))}.")


def untested_channels(crosstalk) -> list[str]:
    """Channels left out of the crosstalk check because they are nearly blank, each once, in table order."""
    if crosstalk is None or len(crosstalk) == 0:
        return []
    seen: list[str] = []
    for _, r in crosstalk.iterrows():
        for side in ("source", "target"):
            if _truthy(r.get(f"{side}_near_blank")) and str(r[side]) not in seen:
                seen.append(str(r[side]))
    return seen


def followup_reading(source: str, target: str, control: str | None) -> str:
    """What to look for in the tiles, in normal words, put above them."""
    text = f"What to look for: if this is crosstalk, {target} is brighter where {source} is bright"
    return text + (f", and {control} is not." if control else ".")


def exclusive_verdict(lift, blank: bool = False) -> tuple[str, str]:
    """(words, tag) for one markers-that-should-not-overlap row. A pair with a near-blank channel is not tested: a ratio
    made of noise says nothing (the same rule the pair table uses)."""
    if blank:
        return NOT_TESTED_BLANK, "untested"
    v = _f(lift)
    if v != v:
        return "Not tested", "untested"
    if v >= EXCLUSIVE_CHECK_RATIO:
        return "Overlap higher than expected", "flagged"
    return "As expected", "clear"


def exclusive_numbers(row, blank: bool = False) -> list[tuple[str, str]]:
    if blank:
        return []
    def pct(v):
        v = _f(v)
        return "-" if v != v else f"{100 * v:.1f}%"

    lift = _f(row.get("lift"))
    return [("Both positive", pct(row.get("co_positive"))),
            ("Chance alone would give", pct(row.get("expected_if_independent"))),
            ("Ratio", "-" if lift != lift else
             f"{lift:.2f} (cut-off {EXCLUSIVE_CHECK_RATIO:.1f}, {'over' if lift >= EXCLUSIVE_CHECK_RATIO else 'under'})")]


def exclusive_blank_sentence(a, b, blank_names) -> str:
    """The plain sentence for a pair left out because a channel is almost blank (no ratio, no map)."""
    names = [str(n) for n in (a, b) if str(n) in set(map(str, blank_names))] or [str(a)]
    who = " and ".join(names)
    return (f"Not tested: {who} {'has' if len(names) == 1 else 'have'} little or no signal, so any overlap would only "
            "measure noise. Check that the antibody was added and the channel was imaged.")


def exclusive_sentence(row, blank_names=()) -> str:
    a, b = row.get("marker_a"), row.get("marker_b")
    if blank_names:
        return exclusive_blank_sentence(a, b, blank_names)
    _words, tag = exclusive_verdict(row.get("lift"))
    co, exp, lift = _f(row.get("co_positive")), _f(row.get("expected_if_independent")), _f(row.get("lift"))
    if lift != lift:
        return f"{a} and {b} could not be compared in this run."
    head = (f"{100 * co:.1f}% of tissue is positive for both {a} and {b}; chance alone would give "
            f"{100 * exp:.1f}% (ratio {lift:.2f}). ")
    if tag == "flagged":
        return head + (f"These markers should not be stained in the same cells, so the tiles below are worth a look: "
                       f"do {a} and {b} light up in the same places?")
    return head + f"That is the expected direction: {a} and {b} are not found together more than chance."


def blank_markers(detection, intensity_range: float = 255.0, settings: DetectionSettings | None = None) -> set[str]:
    """Channels with little or no signal, by the run's own near-blank cut-off (the set the pair table leaves out)."""
    out: set[str] = set()
    if detection is None or len(detection) == 0:
        return out
    for _, r in detection.iterrows():
        if signal_level(r, intensity_range, settings) == "blank":
            out.add(str(r["channel"]))
    return out


def elide_middle(text: str, max_chars: int) -> str:
    """Shorten a long path by cutting out its middle, keeping both ends (the end holds the run's name)."""
    text = str(text)
    if len(text) <= max_chars or max_chars < 8:
        return text
    keep = max_chars - 3
    tail = max(keep // 2, 1)
    head = keep - tail
    return text[:head] + "..." + text[-tail:]


def short_path(path, max_chars: int = 30) -> str:
    """A folder path short enough to sit on one line in the narrow rail without breaking a word: the whole path when
    it fits, else the folder's own name after an ellipsis (the name is the part that identifies the run)."""
    import os
    from pathlib import Path

    text = str(path)
    if len(text) <= max_chars:
        return text
    name = Path(text).name or text
    return "..." + os.sep + elide_middle(name, max(max_chars - 4, 8))
