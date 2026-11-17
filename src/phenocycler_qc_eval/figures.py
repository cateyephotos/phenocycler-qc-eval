"""PNG figures for the Figures tab and the report.

Drawn with matplotlib's object API on the Agg canvas (Figure + FigureCanvasAgg). No pyplot, no
backend selection, so it is safe in the worker process. Figures are designed 9 inches wide so
that, shown about 900 px wide, the smallest text (8 pt) stays readable.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle

from .advice import next_run_text
from .crosstalk import CrosstalkResult
from .layout import PanelLayout, match_markers
from .previews import followup_stacks, followup_window_indices
from .results import load_preview, safe_name
from .settings import DetectionSettings
from .tissue import Sample

WIDTH_IN = 9.0
DPI = 150
FONT = 8

CALL_COLORS = {"OK": "#2f7d3a", "CHECK": "#d9822b", "RAISE": "#c0392b", "LOWER": "#1f77b4"}
_MUTED = "#52606d"
_PHYSICAL = ["same_cycle_spectral", "adjacent_cycle_carryover"]


def _save(fig: Figure, path: Path) -> Path:
    FigureCanvasAgg(fig)
    fig.savefig(path, dpi=DPI)
    return path


def _place_labels(ax, fig, xs, ys, names, fontsize=FONT) -> None:
    """Label each point, trying several offsets until one does not overlap an earlier label."""
    px_per_pt = fig.dpi / 72.0
    pts = ax.transData.transform(np.column_stack([xs, ys]))
    placed: list[tuple[float, float, float, float]] = []
    offsets = [(5, 4), (5, -12), (-5, 4), (-5, -12), (5, 15), (5, -23), (-5, 15), (-5, -23),
               (9, 26), (9, -34), (-9, 26), (-9, -34), (14, 38), (-14, 38), (14, -46), (-14, -46)]
    for i in np.argsort(pts[:, 1]):
        w = 0.68 * fontsize * px_per_pt * len(names[i]) + 3
        h = 1.45 * fontsize * px_per_pt
        chosen, box = offsets[0], None
        for dx, dy in offsets:
            x0 = pts[i, 0] + dx * px_per_pt - (w if dx < 0 else 0)
            y0 = pts[i, 1] + dy * px_per_pt
            cand = (x0, y0, x0 + w, y0 + h)
            if not any(cand[0] < b[2] and b[0] < cand[2] and cand[1] < b[3] and b[1] < cand[3] for b in placed):
                chosen, box = (dx, dy), cand
                break
        if box is None:
            x0 = pts[i, 0] + chosen[0] * px_per_pt
            y0 = pts[i, 1] + chosen[1] * px_per_pt
            box = (x0, y0, x0 + w, y0 + h)
        placed.append(box)
        far = abs(chosen[0]) > 5 or abs(chosen[1]) > 15       # moved away from its dot: draw a leader line
        ax.annotate(names[i], (xs[i], ys[i]), xytext=chosen, textcoords="offset points", fontsize=fontsize,
                    ha="right" if chosen[0] < 0 else "left", color="#1f2933",
                    arrowprops=dict(arrowstyle="-", color="#9aa5b1", lw=0.6, shrinkA=1, shrinkB=3) if far else None)


# ---------------------------------------------------------------------------- detection
def detection_figure(det: pd.DataFrame, settings: DetectionSettings | None = None) -> Figure:
    fig = Figure(figsize=(WIDTH_IN, 6.6), facecolor="white")
    ax = fig.add_axes([0.09, 0.11, 0.88, 0.76])
    xs = (det["signal_fraction"].astype(float) * 100).clip(lower=0.05).to_numpy()
    ys = det["stain_index"].astype(float).clip(lower=0.1).to_numpy()
    names = [str(c) for c in det["channel"]]
    calls = det["call"].to_numpy()
    exposures = det["exposure"].to_numpy() if "exposure" in det.columns else np.array(["KEEP"] * len(det))
    ok = np.isfinite(xs) & np.isfinite(ys)
    for call, color in CALL_COLORS.items():
        # One legend entry per distinct instruction, worded by advice.next_run_text so it cannot drift.
        for exposure in dict.fromkeys(exposures[ok & (calls == call)]):
            m = ok & (calls == call) & (exposures == exposure)
            ax.scatter(xs[m], ys[m], s=46, color=color, label=next_run_text(call, exposure), zorder=3,
                       edgecolors="white", linewidths=0.6)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlim(0.04, 140)
    ax.set_ylim(0.08, max(float(np.nanmax(ys[ok])) * 1.6, 12) if ok.any() else 12)
    if settings is not None:
        ax.axvline(settings.dim_fraction * 100, color="#9aa5b1", ls="--", lw=0.9, zorder=1)
        ax.axhline(settings.stain_index_low, color="#9aa5b1", ls="--", lw=0.9, zorder=1)
        ax.text(settings.dim_fraction * 100 * 0.95, 0.5, "dim to the left", fontsize=FONT - 1, color=_MUTED,
                ha="right", rotation=90, va="center")
        ax.text(0.045, settings.stain_index_low * 1.08, "low stain index below", fontsize=FONT - 1,
                color=_MUTED)
    _place_labels(ax, fig, xs[ok], ys[ok], [n for n, o in zip(names, ok) if o])
    ax.set_xlabel("How bright the stain gets (brightest 0.5% of tissue pixels, % of the image's range)",
                  fontsize=FONT + 1)
    ax.set_ylabel("How well stain stands apart from background (higher is better)", fontsize=FONT + 1)
    ax.tick_params(labelsize=FONT)
    ax.legend(fontsize=FONT, frameon=False, loc="lower left", title="Next run", title_fontsize=FONT)
    fig.text(0.09, 0.965, "Which antibodies need a different concentration?", fontsize=13, weight="bold",
             va="top")
    fig.text(0.09, 0.925, "Each dot is one antibody. Top right = bright and clean. Bottom left = dim or muddy.",
             fontsize=FONT + 1, color=_MUTED, va="top")
    return fig


# ---------------------------------------------------------------------------- intensity ridges
def ridges_figure(smp: Sample, det: pd.DataFrame, max_pixels: int = 400_000) -> Figure:
    """One filled curve per antibody: how its tissue pixel values are spread (log scale of counts)."""
    order = det.sort_values("signal_fraction", ascending=False, kind="stable")
    n = len(order)
    fig_h = 0.34 * n + 1.9
    fig = Figure(figsize=(WIDTH_IN, fig_h), facecolor="white")
    ax = fig.add_axes([0.16, 0.65 / fig_h, 0.8, (fig_h - 1.5) / fig_h])
    tissue = (smp.pixels != 0).any(axis=1)
    rng = float(smp.value_range)
    edges = np.linspace(0.0, 100.0, 81)
    for i, (_, row) in enumerate(order.iloc[::-1].iterrows()):
        c = smp.names.index(str(row["channel"]))
        vals = smp.pixels[:, c][tissue]
        if vals.dtype.kind == "f":
            vals = vals[np.isfinite(vals)]
        if vals.size > max_pixels:
            vals = vals[:: int(np.ceil(vals.size / max_pixels))]
        h, _ = np.histogram(vals.astype(np.float64) / rng * 100.0, bins=edges)
        h = np.log1p(h)
        h = h / (h.max() or 1.0) * 0.9
        ax.fill_between(edges[:-1], i, i + h, color=CALL_COLORS.get(str(row["call"]), "#7b8794"), alpha=0.75,
                        step="post", lw=0)
        ax.plot([0, 100], [i, i], color="#d9dee3", lw=0.5, zorder=0)
    ax.set_yticks(np.arange(n) + 0.3)
    ax.set_yticklabels(order["channel"].astype(str).iloc[::-1], fontsize=FONT)
    ax.set_xlim(0, 100)
    ax.set_ylim(-0.1, n + 0.1)
    ax.set_xlabel("Pixel brightness (% of the image's range). Curve height = how many pixels, log scale.",
                  fontsize=FONT + 1)
    ax.tick_params(labelsize=FONT, length=2)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.text(0.02, 1 - 0.1 / fig_h, "How bright is each antibody across the tissue?", fontsize=13,
             weight="bold", va="top")
    fig.text(0.02, 1 - 0.5 / fig_h, "Brightest antibodies first. Color is the next-run call "
             "(green keep, orange compare, red raise, blue lower).", fontsize=FONT + 1, color=_MUTED, va="top")
    return fig


# ---------------------------------------------------------------------------- crosstalk
def _cycle_info(names: list[str], layout: PanelLayout | None) -> dict[str, tuple[float, str] | None]:
    """image channel -> (cycle, fluor) from the panel file, or None when not in it."""
    if layout is None:
        return {n: None for n in names}
    matched, _ = match_markers(layout, list(names))
    lay = layout.table.set_index("marker")
    out: dict[str, tuple[float, str] | None] = {}
    for n in names:
        if n in matched and matched[n] in lay.index and pd.notna(lay.loc[matched[n], "cycle"]):
            out[n] = (float(lay.loc[matched[n], "cycle"]), str(lay.loc[matched[n], "fluor"]))
        else:
            out[n] = None
    return out


def crosstalk_figure(smp: Sample, xt: CrosstalkResult, layout: PanelLayout | None = None) -> Figure:
    spill = xt.spill
    antibodies = [n for n in spill.index if n != smp.nuclear_name]
    info = _cycle_info(antibodies, layout)
    # cycle order; markers missing from the panel file go last, in image order
    names = sorted(antibodies, key=lambda n: (info[n] is None, info[n] or (0.0, ""), antibodies.index(n)))
    labels = {n: (f"{n} (c{int(info[n][0])})" if info[n] else n) for n in names}
    n = len(names)
    asym = (spill - spill.T).loc[names, names].to_numpy(dtype=float)

    side = float(np.clip(0.2 * n + 2.4, 5.5, 8.4))   # heat map height; 0.2 in per row keeps labels apart
    strip_h = 3.2
    top_h = 1.1                                      # title block
    fig_h = top_h + side + 1.3 + strip_h
    fig = Figure(figsize=(WIDTH_IN, fig_h), facecolor="white")
    heat_w = 6.0
    ax = fig.add_axes([1.9 / WIDTH_IN, (strip_h + 0.4) / fig_h, heat_w / WIDTH_IN, side / fig_h])
    im = ax.imshow(asym, cmap="RdBu_r", vmin=-0.3, vmax=0.3, interpolation="nearest", aspect="auto")
    small = FONT - 1 if n > 32 else FONT
    ax.set_xticks(range(n))
    ax.set_xticklabels([labels[m] for m in names], rotation=90, fontsize=small)
    ax.set_yticks(range(n))
    ax.set_yticklabels([labels[m] for m in names], fontsize=small)
    ax.xaxis.tick_top()
    ax.tick_params(length=2)
    pos = {m: i for i, m in enumerate(names)}
    table = xt.table if xt.table is not None else pd.DataFrame()
    flagged = table[table["flag"].astype(bool)] if len(table) else table
    for _, r in flagged.iterrows():
        if r["source"] in pos and r["target"] in pos:
            ax.add_patch(Rectangle((pos[r["target"]] - 0.5, pos[r["source"]] - 0.5), 1, 1, fill=False,
                                   ec="black", lw=1.8))
    cax = fig.add_axes([(1.9 + heat_w + 0.15) / WIDTH_IN, (strip_h + 0.4) / fig_h, 0.16 / WIDTH_IN,
                        side / fig_h])
    cb = fig.colorbar(im, cax=cax)
    cb.set_label("Red: the row channel leaks into the column channel.\nBlue: the other way round.",
                 fontsize=FONT)
    cb.ax.tick_params(labelsize=FONT)
    fig.text(0.02, 1 - 0.1 / fig_h, "How much of each channel's bright signal also appears in another channel",
             fontsize=13, weight="bold", va="top")
    fig.text(0.02, 1 - 0.42 / fig_h,
             "Channels run in cycle order. Black boxes mark pairs flagged as possible crosstalk\n"
             "(row = suspected source, column = suspected target).",
             fontsize=FONT + 1, color=_MUTED, va="top")

    # strip panels: every pair that can physically interfere, with the cut lines
    phys = table[table["kind"].isin(_PHYSICAL)] if len(table) else table
    if len(phys):
        phys = phys[~(phys["source_near_blank"].astype(bool) | phys["target_near_blank"].astype(bool))]
    panels = [("asymmetry", "asym_cut", "One-way leak (row into column, minus the reverse)"),
              ("highpass_corr", "highpass_cut", "Fine detail copied (do cell outlines match?)")]
    rng = np.random.default_rng(0)
    for j, (col, cut_col, title) in enumerate(panels):
        sx = fig.add_axes([(0.7 + j * 4.3) / WIDTH_IN, 0.55 / fig_h, 3.6 / WIDTH_IN, (strip_h - 1.2) / fig_h])
        vals = phys[col].astype(float).to_numpy() if len(phys) else np.zeros(0)
        jitter = rng.random(len(vals))
        fl = phys["flag"].astype(bool).to_numpy() if len(phys) else np.zeros(0, dtype=bool)
        sx.scatter(vals[~fl], jitter[~fl], s=14, color="#9fb3c8", edgecolors="none", label="Not flagged")
        sx.scatter(vals[fl], jitter[fl], s=26, color="#c0392b", edgecolors="none", label="Flagged")
        cut = float(phys[cut_col].iloc[0]) if len(phys) else float("nan")
        if np.isfinite(cut):
            sx.axvline(cut, color="black", ls="--", lw=1)
            sx.text(cut, 1.03, " cut-off", fontsize=FONT - 1, va="bottom", ha="left",
                    transform=sx.get_xaxis_transform())
        sx.set_yticks([])
        sx.set_ylim(-0.05, 1.05)
        sx.tick_params(labelsize=FONT)
        sx.set_title(title, fontsize=FONT, loc="left", pad=14)
        for s in ("top", "right", "left"):
            sx.spines[s].set_visible(False)
        if j == 0:
            sx.legend(fontsize=FONT - 1, frameon=False, loc="lower left")
    fig.text(0.02, 0.01, "Only pairs that can physically interfere (same cycle, or the same color in the next "
             "cycle) are shown. A pair is flagged when it passes either cut-off.", fontsize=FONT - 1,
             color=_MUTED, va="bottom")
    return fig


# ---------------------------------------------------------------------------- follow-up panels
def followup_figure(smp: Sample, item: dict, k: int, idx=None) -> Figure:
    """idx: the window indices to show (the ones saved in the follow-up preview); chosen here when absent."""
    source, target = str(item["source"]), str(item["target"])
    control = item.get("control") or None
    if idx is None:
        idx = followup_window_indices(smp, source, k)
    idx = np.asarray(idx, dtype=int)
    rows = followup_stacks(smp, source, target, control, idx)
    n_rows, n_cols = len(rows), max(len(idx), 1)
    tile_in = (WIDTH_IN - 1.6) / n_cols
    grid_h = tile_in * n_rows + 0.2
    head, foot = 0.95, 2.9                      # inches above the tiles and below them (bar chart + note)
    fig_h = head + grid_h + foot
    fig = Figure(figsize=(WIDTH_IN, fig_h), facecolor="white")
    gs = fig.add_gridspec(n_rows, n_cols, left=1.5 / WIDTH_IN, right=0.99, top=1 - head / fig_h,
                          bottom=foot / fig_h, hspace=0.04, wspace=0.03)
    roles = {"source": "suspected source", "target": "suspected target", "control": "control",
             "dapi": "nuclei"}
    scaled = smp.pixels.dtype != np.uint8     # tiles of wider images are already scaled to 0..255
    for r, (role, name, tiles, (_, hi)) in enumerate(rows):
        for c in range(tiles.shape[0]):
            ax = fig.add_subplot(gs[r, c])
            ax.imshow(tiles[c], cmap="gray", vmin=0, vmax=255 if scaled else hi, interpolation="nearest")
            ax.set_xticks([])
            ax.set_yticks([])
            for s in ax.spines.values():
                s.set_visible(False)
            if c == 0:
                ax.set_ylabel(f"{name}\n{roles[role]}", fontsize=FONT, rotation=0, ha="right", va="center",
                              labelpad=6)
    fig.text(0.02, 1 - 0.1 / fig_h, f"Does {source} show through into {target}?", fontsize=13, weight="bold",
             va="top")
    fig.text(0.02, 1 - 0.42 / fig_h,
             f"The {len(idx)} regions where {source} is brightest. Each row has its own brightness scale.",
             fontsize=FONT + 1, color=_MUTED, va="top")
    bars = [(f"where {source} is bright", item.get("target_where_source_bright"), "#c0392b"),
            (f"where {source} is low", item.get("target_where_source_low"), "#9fb3c8")]
    if control:
        bars += [(f"where {control} is bright", item.get("target_where_control_bright"), "#d9822b"),
                 (f"where {control} is low", item.get("target_where_control_low"), "#cbd2d9")]
    bars = [b for b in bars if b[1] is not None and np.isfinite(float(b[1]))]
    bx = fig.add_axes([3.4 / WIDTH_IN, 0.7 / fig_h, 5.4 / WIDTH_IN, 1.7 / fig_h])
    if bars:
        y = np.arange(len(bars))[::-1]
        bx.barh(y, [float(b[1]) for b in bars], color=[b[2] for b in bars], height=0.6)
        bx.set_yticks(y)
        bx.set_yticklabels([b[0] for b in bars], fontsize=FONT)
        for yi, b in zip(y, bars):
            bx.text(float(b[1]), yi, f" {float(b[1]):.3g}", va="center", fontsize=FONT)
    else:
        bx.text(0.5, 0.5, "No numbers available", ha="center", va="center", transform=bx.transAxes, fontsize=FONT)
    bx.set_xlabel(f"Average {target} signal (pixel value)", fontsize=FONT)
    bx.tick_params(labelsize=FONT, length=2)
    for s in ("top", "right"):
        bx.spines[s].set_visible(False)
    reading = (f"If this is crosstalk, {target} is clearly brighter where {source} is bright"
               + (f" and not where {control} is bright." if control else "."))
    fig.text(0.02, 0.012, reading, fontsize=FONT, color=_MUTED, va="bottom")
    return fig


def _saved_followup_windows(out: Path, source: str, target: str):
    """The window indices stored in this pair's follow-up preview, or None when there is no readable preview."""
    path = out / "previews" / f"followup_{safe_name(source)}_to_{safe_name(target)}.npz"
    if not path.exists():
        return None
    try:
        return np.asarray(load_preview(path)["windows"], dtype=int)
    except (OSError, ValueError, KeyError):
        return None


def write_figures(smp: Sample, det: pd.DataFrame, xt: CrosstalkResult, out_dir: Path, followups=None,
                  k: int = 6, layout: PanelLayout | None = None,
                  detection_settings: DetectionSettings | None = None, progress=None) -> list[Path]:
    """Write the PNG figures into out_dir and return their paths. progress(done, total), when given, is told
    after each figure (it only reports).

    followups: the list built from crosstalk.followup_numbers (one per pair, R3); each gets
    followup_<source>_to_<target>.png, showing the windows saved in that pair's follow-up preview when one
    exists (so figure and preview never differ). layout (optional) orders the crosstalk heat map by cycle;
    detection_settings (optional) draws the cut-off lines on the detection figure.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    has_xt = xt is not None and xt.enabled and xt.spill is not None
    total = 2 + (1 if has_xt else 0) + len(followups or [])

    def told() -> None:
        if progress is not None:
            progress(len(paths), total)

    paths = [_save(detection_figure(det, detection_settings), out / "stain_qc_detection.png")]
    told()
    paths.append(_save(ridges_figure(smp, det), out / "stain_qc_intensity_ridges.png"))
    told()
    if has_xt:
        paths.append(_save(crosstalk_figure(smp, xt, layout), out / "stain_qc_crosstalk_asymmetry.png"))
        told()
    for item in followups or []:
        name = item.get("figure") or f"followup_{safe_name(item['source'])}_to_{safe_name(item['target'])}.png"
        idx = _saved_followup_windows(out, str(item['source']), str(item['target']))
        paths.append(_save(followup_figure(smp, item, k, idx), out / name))
        told()
    return paths
