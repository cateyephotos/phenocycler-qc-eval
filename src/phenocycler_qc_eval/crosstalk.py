"""Crosstalk between channels, checked only where a physical route exists.

Two routes are physically possible: two channels imaged in the same cycle (spectral
bleed-through, either direction), and the same fluor in the next cycle (carryover of
the earlier antibody's signal). Every other pair is either a control (the "null" pairs,
two or more cycles apart in different fluors) or not checked.

Two numbers are compared with the null: how much more of B appears where A is bright than
the reverse (spill asymmetry), and how well the fine texture of B copies the fine texture
of A (high-pass correlation). Channels that are almost blank are never a source or a target:
their brightest pixels are noise. The rules are lab-defined, not published.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.ndimage import gaussian_filter

from .layout import PanelLayout, match_markers
from .results import NOT_JUDGED_MESSAGE, norm_name, safe_name
from .settings import CrosstalkSettings
from .tissue import Sample

NO_LAYOUT_MESSAGE = "Crosstalk is off: no panel file. Supply the .xpd, or a CSV with columns marker,cycle,fluor."

# Fewest distinct control pairs (two or more cycles apart, different fluors; A-B and B-A count once) needed to set
# the cut-offs. The cut-off is the 95th percentile of the control values: with fewer than 6 control pairs that
# percentile sits within a fifth of the gap below the single largest control value, so one pair would decide where
# the line falls and there is no spread to speak of. Below this the check says "not judged" and flags nothing. A
# 3-cycle panel with 3 fluors has exactly 6 such pairs; the validated 12-marker example has 30.
MIN_CONTROL_PAIRS = 6
NOT_JUDGED_REASON = "not_judged"   # flag_reason on every checkable pair when the cut-off could not be set

TABLE_COLUMNS = [
    "source", "target", "kind", "spill", "reverse_spill", "asymmetry", "highpass_corr", "asym_cut",
    "highpass_cut", "source_near_blank", "target_near_blank", "flag", "flag_reason",
]
EXCLUSIVE_COLUMNS = ["marker_a", "marker_b", "pos_a", "pos_b", "co_positive", "expected_if_independent", "lift"]

_MIN_GROUP_PIXELS = 50
_SPILL_FLOOR_FRACTION = 0.004   # the divisor (99.5th percentile of B) never goes below this share of the range
_PHYSICAL_KINDS = ("same_cycle_spectral", "adjacent_cycle_carryover")

RULE_TEXT: dict[str, str] = {
    "spill": "spill[A, B] = (mean of B where A is at or above its 99th percentile - mean of B where A is below its median), "
             "divided by B's 99.5th percentile. Blank when either group has fewer than 50 pixels, or when A is "
             "sparse: its 99th percentile is zero or no higher than its median, so there is no real bright group. "
             "A channel that is saturated over many pixels is not sparse; its saturated pixels are the bright group.",
    "asymmetry": "spill[A, B] - spill[B, A]. Real crosstalk goes one way.",
    "highpass_corr": "Correlation of the fine texture (pixel minus a Gaussian blur) of A and B over tissue pixels. "
                     "Crosstalk copies cell-scale texture; co-expression does not.",
    "kinds": "same_cycle_spectral: same cycle, checked both ways. adjacent_cycle_carryover: same fluor, target "
             "imaged in the cycle after the source. null: two or more cycles apart in different fluors (sets the "
             "cut-offs). other: not checked.",
    "cut": "Cut-off = the chosen percentile of the null pairs + a margin, for asymmetry and for texture.",
    "near_blank": "A near-blank channel is never a source or a target.",
}


@dataclass
class CrosstalkResult:
    enabled: bool
    message: str = ""
    table: pd.DataFrame | None = None
    spill: pd.DataFrame | None = None
    highpass: pd.DataFrame | None = None
    unmatched: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------- pixels
def _tissue_flat(smp: Sample) -> np.ndarray:
    """(channels, tissue pixels) in the image's own dtype. Tissue = any channel above zero."""
    px = smp.pixels
    tissue = (px != 0).any(axis=1)
    flat = np.stack([px[:, c][tissue] for c in range(px.shape[1])]) if px.shape[1] else np.zeros((0, 0))
    if np.issubdtype(flat.dtype, np.floating):
        flat = np.nan_to_num(flat, nan=0.0, posinf=0.0, neginf=0.0)
    return flat


def spill_matrix(smp: Sample) -> pd.DataFrame:
    """spill[A, B]: how much more of B there is where A is brightest than where A is low, in units of B's range.

    A row is NaN when A is sparse: its 99th percentile is 0 or no higher than its median, so the "bright"
    group (values at or above that percentile) would be most of the tissue and the row would mean nothing.
    A saturated channel (the 99th percentile equals the maximum on many pixels) keeps its row: all pixels
    at or above that percentile are its bright group.
    """
    flat = _tissue_flat(smp)
    n_ch = flat.shape[0]
    floor = _SPILL_FLOOR_FRACTION * float(smp.value_range)
    p995 = np.array([max(float(np.percentile(flat[c], 99.5)), floor) if flat.shape[1] else floor
                     for c in range(n_ch)])
    out = np.full((n_ch, n_ch), np.nan)
    for a in range(n_ch):
        va = flat[a]
        if va.size == 0:
            continue
        p99 = float(np.percentile(va, 99))
        med = float(np.median(va))
        if p99 <= 0 or p99 <= med:
            continue
        hi = va >= p99
        lo = va < med if med > 0 else va == 0
        if hi.sum() < _MIN_GROUP_PIXELS or lo.sum() < _MIN_GROUP_PIXELS:
            continue
        out[a] = (flat[:, hi].mean(axis=1, dtype=np.float64)
                  - flat[:, lo].mean(axis=1, dtype=np.float64)) / p995
    np.fill_diagonal(out, np.nan)
    return pd.DataFrame(out, index=list(smp.names), columns=list(smp.names))


def highpass_corr(smp: Sample, sigma: float, progress=None) -> pd.DataFrame:
    """Pearson correlation of the fine texture of every channel pair, pooled over tissue pixels.

    Texture = pixel minus Gaussian blur, computed window by window so memory stays small.
    progress(done, total), when given, is told after each window (it only reports).
    """
    px = smp.pixels
    n_win, n_ch = px.shape[:2]
    cross = np.zeros((n_ch, n_ch))
    total = np.zeros(n_ch)
    count = 0
    for k in range(n_win):
        if progress is not None:
            progress(k, n_win)
        win = px[k].astype(np.float32)
        if np.issubdtype(px.dtype, np.floating):
            win = np.nan_to_num(win, nan=0.0, posinf=0.0, neginf=0.0)
        tissue = (win != 0).any(axis=0)
        if not tissue.any():
            continue
        hp = np.stack([win[c] - gaussian_filter(win[c], sigma) for c in range(n_ch)])[:, tissue]
        hp = hp.astype(np.float64)
        cross += hp @ hp.T
        total += hp.sum(axis=1)
        count += hp.shape[1]
    mean = total / max(count, 1)
    cov = cross / max(count, 1) - np.outer(mean, mean)
    sd = np.sqrt(np.clip(np.diag(cov), 1e-12, None))
    corr = cov / np.outer(sd, sd)
    np.fill_diagonal(corr, np.nan)
    return pd.DataFrame(corr, index=list(smp.names), columns=list(smp.names))


# ---------------------------------------------------------------------------- pairs
def _is_missing(v) -> bool:
    return v is None or (isinstance(v, float) and v != v)


def _layout_lookup(lay: pd.DataFrame) -> pd.DataFrame:
    return lay.drop_duplicates("marker").set_index("marker")


def pair_kind(a: str, b: str, lay: pd.DataFrame, table: pd.DataFrame | None = None) -> str:
    """Route between source a and target b (layout marker names): see the module docstring.

    `table` is the marker-indexed lookup from _layout_lookup(lay); pass it when calling in a loop.
    """
    if table is None:
        table = _layout_lookup(lay)
    if a not in table.index or b not in table.index:
        return "other"
    ra, rb = table.loc[a], table.loc[b]
    if _is_missing(ra["cycle"]) or _is_missing(rb["cycle"]):
        return "other"
    ca, cb = int(ra["cycle"]), int(rb["cycle"])
    fa, fb = ra["fluor"], rb["fluor"]
    known = not (_is_missing(fa) or _is_missing(fb))
    same_fluor = known and str(fa).strip().lower() == str(fb).strip().lower()
    if ca == cb:
        return "same_cycle_spectral"
    if same_fluor and cb == ca + 1:
        return "adjacent_cycle_carryover"
    if abs(ca - cb) >= 2 and known and not same_fluor:
        return "null"
    return "other"


def _null_cut(values: pd.Series, s: CrosstalkSettings) -> float:
    null = values.dropna()
    if null.empty:
        return float("inf")  # nothing to compare with: flag nothing
    return float(np.percentile(null, s.null_percentile)) + float(s.margin)


def n_control_pairs(df: pd.DataFrame, column: str = "asymmetry") -> int:
    """Distinct control pairs (A-B and B-A once) with a finite value in `column`."""
    null = df[df["kind"] == "null"]
    null = null[np.isfinite(null[column].astype(float))]
    return len({frozenset((a, b)) for a, b in zip(null["source"], null["target"])})


def has_enough_controls(df: pd.DataFrame, texture: bool = True) -> bool:
    """True when the control pairs can set a cut-off (see MIN_CONTROL_PAIRS)."""
    if n_control_pairs(df) < MIN_CONTROL_PAIRS:
        return False
    return not texture or n_control_pairs(df, "highpass_corr") >= MIN_CONTROL_PAIRS


def crosstalk_table(spill: pd.DataFrame, hp: pd.DataFrame | None, lay: pd.DataFrame, near_blank: set[str],
                    s: CrosstalkSettings, exclude=()) -> pd.DataFrame:
    """Every ordered channel pair with its route, numbers, cut-offs and flag.

    spill and hp are indexed by image channel names; lay is the layout table. Image names are
    matched to layout markers by normalized name; unmatched names get kind "other". `exclude`
    lists channels left out entirely (the nuclear channel).

    Near-blank channels stay in the null set that sets the cut-offs (as in the validated prototype);
    they are excluded only as the source or target of a flag.
    """
    names = [n for n in spill.index if n not in set(exclude)]
    matched, _ = match_markers(PanelLayout(lay, ""), list(names))
    sp = spill.reindex(index=names, columns=names).to_numpy(dtype=float)
    hpv = hp.reindex(index=names, columns=names).to_numpy(dtype=float) if hp is not None else None
    lookup = _layout_lookup(lay)
    rows = []
    for i, a in enumerate(names):
        for j, b in enumerate(names):
            if i == j:
                continue
            kind = pair_kind(matched[a], matched[b], lay, lookup) if a in matched and b in matched else "other"
            rows.append({
                "source": a, "target": b, "kind": kind, "spill": sp[i, j], "reverse_spill": sp[j, i],
                "asymmetry": sp[i, j] - sp[j, i],
                "highpass_corr": hpv[i, j] if hpv is not None else float("nan"),
                "source_near_blank": a in near_blank, "target_near_blank": b in near_blank,
            })
    df = pd.DataFrame(rows, columns=[c for c in TABLE_COLUMNS if c not in
                                     ("asym_cut", "highpass_cut", "flag", "flag_reason")])
    if not has_enough_controls(df, texture=hp is not None):
        return _not_judged_table(df)
    null = df["kind"] == "null"
    asym_cut = _null_cut(df.loc[null, "asymmetry"], s)
    hp_cut = _null_cut(df.loc[null, "highpass_corr"], s)
    df["asym_cut"], df["highpass_cut"] = asym_cut, hp_cut
    ok = df["kind"].isin(_PHYSICAL_KINDS) & ~df["source_near_blank"] & ~df["target_near_blank"]
    by_asym = ok & (df["asymmetry"] > asym_cut)
    by_texture = ok & (df["highpass_corr"] > hp_cut)
    df["flag"] = by_asym | by_texture
    df["flag_reason"] = np.where(by_asym & by_texture, "asymmetry+texture",
                                 np.where(by_asym, "asymmetry", np.where(by_texture, "texture", "")))
    df = df.sort_values("asymmetry", ascending=False, kind="stable", na_position="last")
    return df[TABLE_COLUMNS].reset_index(drop=True)


def _not_judged_table(df: pd.DataFrame) -> pd.DataFrame:
    """The table when there are too few control pairs: no cut-offs, no flags, and every checkable pair says why."""
    df["asym_cut"], df["highpass_cut"] = float("nan"), float("nan")
    df["flag"] = False
    df["flag_reason"] = np.where(df["kind"].isin(_PHYSICAL_KINDS), NOT_JUDGED_REASON, "")
    df = df.sort_values("asymmetry", ascending=False, kind="stable", na_position="last")
    return df[TABLE_COLUMNS].reset_index(drop=True)


# ---------------------------------------------------------------------------- suggestions
def cycle_order_suggestions(table: pd.DataFrame, lay: pd.DataFrame) -> list[str]:
    """One fixed rule: a target that shows carryover is moved to another fluor or a later cycle."""
    if table is None or table.empty:
        return []
    flagged = table[table["flag"].astype(bool) & (table["kind"] == "adjacent_cycle_carryover")]
    if flagged.empty:
        return []
    matched, _ = match_markers(PanelLayout(lay, ""), list(set(flagged["source"]) | set(flagged["target"])))
    info = lay.drop_duplicates("marker").set_index("marker")
    out: list[str] = []
    for _, row in flagged.iterrows():
        marker = matched.get(row["target"])
        if marker is None:
            continue
        text = (f"{row['target']} (cycle {int(info.loc[marker, 'cycle'])}, {info.loc[marker, 'fluor']}) "
                f"follows {row['source']} in the same fluor and shows carryover; consider moving it to a "
                "different fluor or a later cycle.")
        if text not in out:
            out.append(text)
    return out


# ---------------------------------------------------------------------------- follow-up
def followup_pairs(table: pd.DataFrame) -> list[tuple[str, str]]:
    """The (source, target) pairs that get a follow-up panel, strongest spill first.

    A same-cycle pair gets one follow-up, named by the orientation with the larger spill, even when
    only one orientation was flagged. A carryover pair keeps its forward orientation.
    """
    if table is None or table.empty:
        return []
    flagged = table[table["flag"].astype(bool)]
    chosen: dict[object, tuple[float, str, str]] = {}
    for _, r in flagged.iterrows():
        spill = float(r["spill"]) if not pd.isna(r["spill"]) else float("-inf")
        s, t = str(r["source"]), str(r["target"])
        if r["kind"] == "same_cycle_spectral":
            key = ("same", frozenset((s, t)))
            reverse = table[(table["source"] == t) & (table["target"] == s)]
            rev = float(reverse["spill"].iloc[0]) if len(reverse) and not pd.isna(reverse["spill"].iloc[0]) \
                else float("-inf")
            if rev > spill:
                s, t, spill = t, s, rev
        else:
            key = ("fwd", s, t)
        if key not in chosen or spill > chosen[key][0]:
            chosen[key] = (spill, s, t)
    return [(s, t) for _, s, t in sorted(chosen.values(), key=lambda v: -v[0])]


def control_for(target: str, controls, names: list[str], avoid=()) -> str | None:
    """Image channel to use as the control for `target`, from [marker, control] pairs, by normalized name."""
    by_norm = {norm_name(n): n for n in names}
    wanted = norm_name(target)
    skip = {norm_name(a) for a in avoid}
    for pair in controls or []:
        if len(pair) == 2 and norm_name(pair[0]) == wanted:
            found = by_norm.get(norm_name(pair[1]))
            if found is not None and norm_name(found) not in skip and norm_name(found) != wanted:
                return found
    return None


def followup_numbers(smp: Sample, source: str, target: str, control: str | None) -> dict:
    """The numbers behind one follow-up panel (keys follow stain_qc_followups.json).

    How much the target rises where the source is brightest versus where the control marker is
    brightest; how often the target's brightest pixels sit under the source's (or control's)
    bright pixels; and how nuclear the target's bright pixels are. chance_share is what a
    marker unrelated to the target would give.
    """
    idx = {name: i for i, name in enumerate(smp.names)}
    tissue = (smp.pixels != 0).any(axis=1)

    def plane(name):
        x = smp.pixels[:, idx[name]].astype(np.float32)
        return np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0) if np.issubdtype(x.dtype, np.floating) else x

    def top(x, q):
        return tissue & (x >= np.percentile(x[tissue], q)) if tissue.any() else tissue & False

    def low(x):
        return tissue & (x <= np.median(x[tissue])) if tissue.any() else tissue & False

    def mean_where(x, m):
        return float(x[m].mean()) if m.any() else float("nan")

    src, tgt = plane(source), plane(target)
    bright_target = top(tgt, 99)
    res: dict = {"source": source, "target": target,
                 "control": control if control in idx else None,
                 "target_where_source_bright": mean_where(tgt, top(src, 99)),
                 "target_where_source_low": mean_where(tgt, low(src)),
                 "share_target_bright_on_source_bright": mean_where(top(src, 95).astype(np.float32), bright_target)}
    if res["control"] is not None:
        ctl = plane(control)
        res["target_where_control_bright"] = mean_where(tgt, top(ctl, 99))
        res["target_where_control_low"] = mean_where(tgt, low(ctl))
        res["share_target_bright_on_control_bright"] = mean_where(top(ctl, 95).astype(np.float32), bright_target)
    if smp.nuclear_name in idx:
        res["share_target_bright_nuclear"] = mean_where(top(plane(smp.nuclear_name), 90).astype(np.float32),
                                                        bright_target)
    res["chance_share"] = 0.05
    res["figure"] = f"followup_{safe_name(source)}_to_{safe_name(target)}.png"
    return res


# ---------------------------------------------------------------------------- exclusive pairs
def exclusive_pairs_table(smp: Sample, det: pd.DataFrame, pairs) -> pd.DataFrame:
    """For markers that should not share cells: how often both are positive, against chance.

    Positive = above the channel's Otsu cut from the detection table. lift = both / (a x b);
    below 1 is the expected direction. Names are matched after normalizing; pairs with a
    missing marker are skipped. Works with or without a panel layout.
    """
    by_norm = {norm_name(n): n for n in smp.names}
    cut = dict(zip(det["channel"], det["otsu_threshold"]))
    flat = _tissue_flat(smp)
    idx = {n: i for i, n in enumerate(smp.names)}
    rows = []
    for pair in pairs or []:
        if len(pair) != 2:
            continue
        a, b = by_norm.get(norm_name(pair[0])), by_norm.get(norm_name(pair[1]))
        if a is None or b is None or a == b:
            continue
        if not (np.isfinite(cut.get(a, np.nan)) and np.isfinite(cut.get(b, np.nan))):
            continue
        pa, pb = flat[idx[a]] > cut[a], flat[idx[b]] > cut[b]
        if pa.size == 0:
            continue
        both, expected = float((pa & pb).mean()), float(pa.mean() * pb.mean())
        rows.append({"marker_a": a, "marker_b": b, "pos_a": float(pa.mean()), "pos_b": float(pb.mean()),
                     "co_positive": both, "expected_if_independent": expected,
                     "lift": both / expected if expected > 0 else float("nan")})
    return pd.DataFrame(rows, columns=EXCLUSIVE_COLUMNS)


# ---------------------------------------------------------------------------- run
def run_crosstalk(smp: Sample, det: pd.DataFrame, layout: PanelLayout | None, s: CrosstalkSettings,
                  sigma: float, progress=None) -> CrosstalkResult:
    """Spill, texture correlation, flagged pairs and the cycle-order suggestions. Off without a layout."""
    if layout is None:
        return CrosstalkResult(enabled=False, message=NO_LAYOUT_MESSAGE)
    near_blank = set(det.loc[det["near_blank"].astype(bool), "channel"]) if len(det) else set()
    spill = spill_matrix(smp)
    hp = highpass_corr(smp, sigma, progress)
    table = crosstalk_table(spill, hp, layout.table, near_blank, s, exclude=[smp.nuclear_name])
    antibodies = [n for n in smp.names if n != smp.nuclear_name]
    _, unmatched = match_markers(layout, antibodies)
    if not has_enough_controls(table, texture=hp is not None):
        # Same shape as the no-panel case (state "off", message says why); the table is kept for the CSV only.
        return CrosstalkResult(enabled=False, message=NOT_JUDGED_MESSAGE, table=table, spill=spill, highpass=hp,
                               unmatched=unmatched)
    message = f"Crosstalk checked: {len(followup_pairs(table))} pair(s) flagged."
    if unmatched:
        message += " In the panel file but not found in the image: " + ", ".join(unmatched) + "."
    return CrosstalkResult(enabled=True, message=message, table=table, spill=spill, highpass=hp,
                           unmatched=unmatched, suggestions=cycle_order_suggestions(table, layout.table))
