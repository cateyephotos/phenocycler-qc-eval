"""Small per-antibody and follow-up preview files shown when a row is clicked.

Each preview is one compressed .npz. Tile stacks are always uint8 (the GUI's display limits assume it).
A uint8 image keeps its own pixel values. A wider or float image is scaled to uint8 with ONE fixed display
range per channel, and that range is stored in the file (`display_min` / `display_max`, or `display_ranges`
for follow-ups), so a viewer can undo the scaling.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .fsutil import replace_file
from .results import load_preview, safe_name  # noqa: F401  (re-exported: safe_name lives in results.py, R5)
from .tissue import Sample

MAX_TILE_PX = 256
HIST_BINS = 64


def preview_indices(n_windows: int, k: int, seed: int) -> np.ndarray:
    """The same k windows (sorted) for every antibody: a seeded choice, no replacement."""
    n_windows, k = int(n_windows), int(k)
    if n_windows <= 0 or k <= 0:
        return np.zeros(0, dtype=int)
    rng = np.random.default_rng(int(seed))
    return np.sort(rng.choice(n_windows, size=min(k, n_windows), replace=False))


def _finite(x: np.ndarray) -> np.ndarray:
    return np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0) if x.dtype.kind == "f" else x


def _downsample(stack: np.ndarray) -> np.ndarray:
    """(k, h, w) -> block-averaged so neither side exceeds MAX_TILE_PX."""
    f = math.ceil(max(stack.shape[1:]) / MAX_TILE_PX)
    if f <= 1:
        return stack
    k, h, w = stack.shape
    h2, w2 = h // f * f, w // f * f
    blocks = stack[:, :h2, :w2].astype(np.float32).reshape(k, h2 // f, f, w2 // f, f).mean(axis=(2, 4))
    if stack.dtype.kind in "iu":
        blocks = np.rint(blocks)
    return blocks.astype(stack.dtype)


def _to_uint8(stack: np.ndarray, display_max: float) -> np.ndarray:
    """uint8 images are kept as they are; others are scaled so 0 -> 0 and display_max -> 255."""
    if stack.dtype == np.uint8:
        return np.ascontiguousarray(stack)
    scaled = np.clip(_finite(stack).astype(np.float32) / max(float(display_max), 1e-12), 0.0, 1.0)
    return np.rint(scaled * 255.0).astype(np.uint8)


def _display_max(values: np.ndarray, fallback: float) -> float:
    values = values[np.isfinite(values)] if values.dtype.kind == "f" else values
    if values.size == 0:
        return float(fallback)
    hi = float(np.percentile(values, 99.5))
    return hi if hi > 0 else float(fallback)


def window_tiles(smp: Sample, channel: str, idx: np.ndarray, display_max: float) -> np.ndarray:
    """uint8 tiles (len(idx), h, w) of one channel, downsampled to at most MAX_TILE_PX."""
    c = smp.names.index(channel)
    return _to_uint8(_downsample(smp.pixels[idx, c]), display_max)


def _tissue_mask(smp: Sample) -> np.ndarray:
    return (smp.pixels != 0).any(axis=1)


def write_antibody_previews(smp: Sample, det: pd.DataFrame, out_dir: Path, k: int, seed: int,
                            progress=None) -> list[Path]:
    """previews/antibody_<marker>.npz for each antibody in `det`. Display limits are 0 .. signal_p99_5.
    progress(done, total), when given, is told after each file (it only reports)."""
    out = Path(out_dir) / "previews"
    out.mkdir(parents=True, exist_ok=True)
    idx = preview_indices(len(smp.pixels), k, seed)
    tissue = _tissue_mask(smp)
    # Share of each preview window that is tissue, so the window can say why a tile looks empty.
    tissue_fraction = (tissue[idx].reshape(len(idx), -1).mean(axis=1) if len(idx) else np.zeros(0)).astype(np.float32)
    edges = np.linspace(0.0, float(smp.value_range), HIST_BINS + 1)
    paths: list[Path] = []
    for _, row in det.iterrows():
        name = str(row["channel"])
        c = smp.names.index(name)
        vals = smp.pixels[:, c][tissue]
        if vals.dtype.kind == "f":
            vals = vals[np.isfinite(vals)]        # the same filter detection.py uses
        counts, _ = np.histogram(vals, bins=edges)
        signal = float(row["signal_p99_5"]) if pd.notna(row["signal_p99_5"]) else 0.0
        signal = signal if signal > 0 else _display_max(vals, 1.0)
        tiles = window_tiles(smp, name, idx, signal)
        if smp.pixels.dtype == np.uint8:
            vmin, vmax = 0.0, max(signal, 1.0)
        else:  # already scaled: 0..signal -> 0..255
            vmin, vmax = 0.0, 255.0
        otsu = float(row["otsu_threshold"]) if pd.notna(row.get("otsu_threshold", np.nan)) else float("nan")
        path = out / f"antibody_{safe_name(name)}.npz"
        tmp = path.with_name(".tmp_" + path.name)       # swapped in whole, so a reader never sees half a file
        np.savez_compressed(tmp, tiles=tiles, vmin=vmin, vmax=vmax, display_min=0.0, display_max=signal,
                            hist_counts=counts.astype(np.int64), hist_edges=edges, otsu_threshold=otsu,
                            marker=name, windows=idx, tissue_fraction=tissue_fraction)
        replace_file(tmp, path)
        paths.append(path)
        if progress is not None:
            progress(len(paths), len(det))
    return paths


def _require_channel(smp: Sample, name: str, role: str) -> int:
    if name not in smp.names:
        raise ValueError(f"The {role} marker {name!r} is not a channel in this image.")
    return smp.names.index(name)


def followup_window_indices(smp: Sample, source: str, k: int) -> np.ndarray:
    """The k windows where `source` is brightest (mean over tissue pixels), brightest first."""
    c = _require_channel(smp, source, "source")
    tissue = _tissue_mask(smp)
    plane = _finite(smp.pixels[:, c]).astype(np.float32)
    means = np.array([plane[i][tissue[i]].mean() if tissue[i].any() else 0.0 for i in range(len(plane))])
    return np.argsort(-means, kind="stable")[: max(int(k), 0)]


def followup_stacks(smp: Sample, source: str, target: str, control: str | None, idx: np.ndarray):
    """[(role, name, uint8 tiles, (display_min, display_max))] for source, target, control (if any), DAPI."""
    rows = []
    for role, name in (("source", source), ("target", target), ("control", control), ("dapi", smp.nuclear_name)):
        if name is None or name not in smp.names:
            continue
        c = smp.names.index(name)
        raw = smp.pixels[idx, c]
        hi = _display_max(raw[raw != 0] if raw.size else raw, float(smp.value_range))
        rows.append((role, name, window_tiles(smp, name, idx, hi), (0.0, hi)))
    return rows


def write_followup_preview(smp: Sample, source: str, target: str, control: str | None, numbers: dict,
                           out_dir: Path, k: int) -> Path:
    """previews/followup_<source>_to_<target>.npz: the k windows where `source` is brightest.

    The choice of windows is deterministic. Raises ValueError when source or target is not in the image.
    """
    _require_channel(smp, source, "source")
    _require_channel(smp, target, "target")
    out = Path(out_dir) / "previews"
    out.mkdir(parents=True, exist_ok=True)
    idx = followup_window_indices(smp, source, k)
    stacks = {role: (name, tiles, rng) for role, name, tiles, rng in
              followup_stacks(smp, source, target, control, idx)}
    h, w = stacks["source"][1].shape[1:]
    empty = np.zeros((0, h, w), dtype=np.uint8)
    ranges = {role: [float(v[2][0]), float(v[2][1])] for role, v in stacks.items()}
    path = out / f"followup_{safe_name(source)}_to_{safe_name(target)}.npz"
    np.savez_compressed(
        path, source=stacks["source"][1], target=stacks["target"][1],
        control=stacks["control"][1] if "control" in stacks else empty,
        dapi=stacks["dapi"][1] if "dapi" in stacks else empty,
        numbers=json.dumps(numbers, ensure_ascii=True, default=float),
        display_ranges=json.dumps(ranges), windows=idx,
        source_name=source, target_name=target, control_name=stacks["control"][0] if "control" in stacks else "")
    return path
