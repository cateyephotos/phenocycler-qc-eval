"""Find tissue from the nuclear channel and sample windows at native resolution.

The mask is built on a small, blurred copy of the nuclear channel (about 5 um per
pixel), so it never needs a full-resolution plane in memory. Windows are then cut
from the full-resolution image on a grid, keeping only those that are mostly tissue.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
from scipy import ndimage as ndi
from skimage.filters import threshold_otsu
from skimage.transform import resize

from .readers import ImageSource, nuclear_channel
from .settings import AdvancedSettings

MASK_UM_PER_PX = 4.97   # pixel size of the small tissue-mask image
DILATION_PX = 50        # grey dilation kernel, in mask pixels
MEDIAN_PX = 20          # median filter kernel, in mask pixels
MIN_TISSUE_FRACTION = 0.5
_STRIP_TARGET_PIXELS = 16_000_000  # rows are read in strips of about this many pixels
_POOL_TARGET = 20_000_000          # most pixels pooled for the float-image range
_INT_RANGE_BITS = (10, 12, 14, 16)


class TooLittleTissueError(Exception):
    """Fewer tissue windows than the minimum, so the analysis would not mean much."""

    def __init__(self, found: int, needed: int):
        self.found = int(found)
        self.needed = int(needed)
        super().__init__(f"found {self.found} tissue windows, need at least {self.needed}")


@dataclass
class Sample:
    """Windows cut from the image at native resolution, with everything detection needs."""

    pixels: np.ndarray        # (N, C, h, w), the image's own dtype
    names: list[str]          # channel names, in pixel order (includes the nuclear channel)
    origins: pd.DataFrame     # y0, x0, size_px of each window in full-resolution pixels
    window_px: int
    nuclear_name: str
    value_range: float        # the image's range; thresholds are fractions of this
    saturation_checkable: bool
    range_basis: str = ""     # how value_range was chosen, in words (recorded in run_info / report)

    def save(self, path) -> Path:
        """Write the sample to one .npz file (for --save-sample); returns the file actually written."""
        path = Path(path)
        if path.suffix != ".npz":
            path = path.with_name(path.name + ".npz")
        meta = {
            "names": list(self.names), "window_px": int(self.window_px),
            "nuclear_name": self.nuclear_name, "value_range": float(self.value_range),
            "saturation_checkable": bool(self.saturation_checkable), "range_basis": self.range_basis,
        }
        np.savez_compressed(path, pixels=self.pixels, origins=self.origins.to_numpy(dtype=np.int64),
                            origin_columns=np.array(list(self.origins.columns)),
                            meta=json.dumps(meta, ensure_ascii=True))
        return path

    @classmethod
    def load(cls, path) -> "Sample":
        with np.load(Path(path), allow_pickle=False) as z:
            meta = json.loads(str(z["meta"]))
            origins = pd.DataFrame(z["origins"], columns=[str(c) for c in z["origin_columns"]])
            return cls(pixels=z["pixels"], origins=origins, names=list(meta["names"]),
                       window_px=int(meta["window_px"]), nuclear_name=meta["nuclear_name"],
                       value_range=float(meta["value_range"]),
                       saturation_checkable=bool(meta["saturation_checkable"]),
                       range_basis=meta.get("range_basis", ""))


# ---------------------------------------------------------------------------- tissue mask
def _pick_level(src: ImageSource) -> int:
    """The coarsest pyramid level whose pixels are no bigger than MASK_UM_PER_PX.

    Reading it and shrinking to the target never has to invent detail. If even level 0
    is coarser than the target, level 0 is used.
    """
    best = 0
    for level in range(src.n_levels):
        if src.pixel_size_um * src.level_downsample(level) <= MASK_UM_PER_PX:
            best = level
    return best


def _read_small_nuclear(src: ImageSource, nuclear_idx: int, scale: float) -> np.ndarray:
    """The nuclear channel at about MASK_UM_PER_PX, read in row strips so memory stays small."""
    level = _pick_level(src)
    height, width = src.level_shapes[level]
    level_um = src.pixel_size_um * src.level_downsample(level)
    shrink = MASK_UM_PER_PX / level_um            # > 1 means the level is finer than the target
    k = max(1, int(shrink))                       # whole-pixel block average first
    rows = max(k, (_STRIP_TARGET_PIXELS // max(width, 1)) // k * k)
    strips = []
    for y0 in range(0, height, rows):
        block = src.read(level, y0, min(y0 + rows, height), 0, width, [nuclear_idx])[0].astype(np.float32)
        block = np.nan_to_num(block, nan=0.0, posinf=0.0, neginf=0.0)  # NaN would blank the whole mask
        if k > 1:
            h2, w2 = block.shape[0] // k * k, block.shape[1] // k * k
            if h2 == 0 or w2 == 0:
                continue
            block = block[:h2, :w2].reshape(h2 // k, k, w2 // k, k).mean(axis=(1, 3))
        strips.append(block)
    small = np.concatenate(strips, axis=0) if strips else np.zeros((1, 1), np.float32)
    full_h, full_w = src.shape_yx
    target = (max(1, round(full_h / scale)), max(1, round(full_w / scale)))
    if small.shape != target:
        small = resize(small, target, order=1, anti_aliasing=False, preserve_range=True).astype(np.float32)
    return small


def tissue_mask(src: ImageSource, nuclear_idx: int) -> tuple[np.ndarray, float]:
    """Boolean tissue mask on a small grid, and how many full-resolution pixels one mask pixel spans.

    Recipe: nuclear channel at about 4.97 um per pixel, grey dilation (50 px), median
    filter (20 px), Otsu threshold.
    """
    scale = MASK_UM_PER_PX / src.pixel_size_um
    small = _read_small_nuclear(src, nuclear_idx, scale)
    smooth = ndi.median_filter(ndi.grey_dilation(small, size=(DILATION_PX, DILATION_PX)),
                               size=(MEDIAN_PX, MEDIAN_PX))
    if not np.isfinite(smooth).all() or smooth.max() <= smooth.min():
        return np.zeros(smooth.shape, dtype=bool), scale
    return smooth > threshold_otsu(smooth), scale


# ---------------------------------------------------------------------------- windows
def _tissue_fractions(mask: np.ndarray, scale: float, y0s: np.ndarray, x0s: np.ndarray,
                      window_px: int) -> np.ndarray:
    """Fraction of each window (full-resolution coordinates) that falls on tissue."""
    integral = np.pad(mask.astype(np.int64).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    mh, mw = mask.shape

    def edge(v, limit):
        return np.clip(np.rint(np.asarray(v) / scale).astype(int), 0, limit)

    ya, yb = edge(y0s, mh), np.maximum(edge(y0s + window_px, mh), edge(y0s, mh) + 1)
    xa, xb = edge(x0s, mw), np.maximum(edge(x0s + window_px, mw), edge(x0s, mw) + 1)
    yb, xb = np.minimum(yb, mh), np.minimum(xb, mw)
    total = integral[yb, xb] - integral[ya, xb] - integral[yb, xa] + integral[ya, xa]
    area = np.maximum((yb - ya) * (xb - xa), 1)
    return total / area


def _integer_range(sample_max: int, dtype: np.dtype) -> tuple[float, str]:
    """Range for an integer image wider than 8 bits: the smallest of 10/12/14/16 bits holding the data."""
    for bits in _INT_RANGE_BITS:
        if sample_max <= 2 ** bits - 1:
            return float(2 ** bits - 1), (f"{bits}-bit: largest value in the sampled windows is "
                                          f"{sample_max} ({dtype} file)")
    top = float(np.iinfo(dtype).max)
    return top, f"{dtype} maximum ({int(top)}): sampled values exceed 16 bits"


def _float_range(pixels: np.ndarray, names: list[str], nuclear_name: str, mask: np.ndarray,
                 scale: float, origins: pd.DataFrame, window_px: int) -> tuple[float, str]:
    """One pooled 99.99th percentile over tissue pixels of all antibody channels."""
    antibody = [i for i, n in enumerate(names) if n != nuclear_name]
    n_win = pixels.shape[0]
    per_window = max(1, window_px * window_px * max(len(antibody), 1))
    stride = max(1, int(np.ceil(n_win * per_window / _POOL_TARGET)))
    pooled = []
    mh, mw = mask.shape
    for k in range(n_win):
        y0, x0 = int(origins["y0"].iloc[k]), int(origins["x0"].iloc[k])
        yy = np.clip(np.rint((y0 + np.arange(0, window_px, stride)) / scale).astype(int), 0, mh - 1)
        xx = np.clip(np.rint((x0 + np.arange(0, window_px, stride)) / scale).astype(int), 0, mw - 1)
        tissue = mask[np.ix_(yy, xx)]
        sub = pixels[k][antibody][:, ::stride, ::stride]
        vals = sub[:, tissue]
        pooled.append(vals.ravel().astype(np.float32))
    values = np.concatenate(pooled) if pooled else np.zeros(0, np.float32)
    values = values[np.isfinite(values)]
    basis = "float image: 99.99th percentile of tissue pixels pooled over all antibody channels"
    if values.size and float(values.max()) > 0:
        value = float(np.percentile(values, 99.99))
        if value > 0:
            return value, basis
        return float(values.max()), basis + " (was zero; used the maximum)"
    return 1.0, "float image: no positive tissue pixels in the sample; range set to 1"


def _range_for(pixels: np.ndarray, src: ImageSource, names: list[str], nuclear_name: str, mask: np.ndarray,
               scale: float, origins: pd.DataFrame, window_px: int) -> tuple[float, str, bool]:
    """(value_range, range_basis, saturation_checkable) for these windows (R9)."""
    if src.dtype == np.uint8:
        return 255.0, "uint8 file: range 255", True
    if np.issubdtype(src.dtype, np.integer):
        value_range, basis = _integer_range(int(pixels.max()) if pixels.size else 0, src.dtype)
        return value_range, basis, True
    value_range, basis = _float_range(pixels, names, nuclear_name, mask, scale, origins, window_px)
    return value_range, basis, False


def sample_windows(src: ImageSource, a: AdvancedSettings, nuclear: str | None = None,
                   progress: Callable[[float, str], None] | None = None,
                   early_windows: int = 0, on_early: Callable[["Sample", int], None] | None = None) -> Sample:
    """Cut up to a.n_windows tissue windows from the full-resolution image.

    Windows sit on a grid; those more than half tissue are candidates, and a seeded
    random pick chooses among them. Raises TooLittleTissueError below a.min_windows.

    early_windows / on_early: when both are given and the run takes more than early_windows windows, a
    seeded random subset of that size is read first and handed to on_early(sample, total_windows), so a caller can
    show early results while the rest is read. The returned Sample always holds all windows, in the same
    order and with the same pixels as a call without them.
    """
    def tell(fraction: float, text: str) -> None:
        if progress is not None:
            progress(fraction, text)

    nuclear_idx = nuclear_channel(src, nuclear)
    tell(0.0, "Finding tissue")
    mask, scale = tissue_mask(src, nuclear_idx)

    window_px = int(round(a.window_um / src.pixel_size_um))
    if window_px < 1:
        raise ValueError(f"The window size ({a.window_um} um) is smaller than one pixel "
                         f"({src.pixel_size_um} um per pixel).")
    height, width = src.shape_yx
    ys = np.arange(0, height - window_px + 1, window_px)
    xs = np.arange(0, width - window_px + 1, window_px)
    if ys.size == 0 or xs.size == 0:
        raise TooLittleTissueError(0, a.min_windows)
    gy, gx = np.meshgrid(ys, xs, indexing="ij")
    gy, gx = gy.ravel(), gx.ravel()
    fractions = _tissue_fractions(mask, scale, gy, gx, window_px)
    keep = np.flatnonzero(fractions > MIN_TISSUE_FRACTION)
    if keep.size < a.min_windows:
        raise TooLittleTissueError(keep.size, a.min_windows)

    rng = np.random.default_rng(a.seed)
    take = min(int(a.n_windows), keep.size)
    chosen = np.sort(rng.choice(keep, size=take, replace=False))  # sorted: reads move across the file in order
    origins = pd.DataFrame({"y0": gy[chosen], "x0": gx[chosen], "size_px": window_px}).astype(np.int64)

    channels = list(range(len(src.channels)))
    names = list(src.channels)
    nuclear_name = names[nuclear_idx]
    pixels = np.empty((take, len(channels), window_px, window_px), dtype=src.dtype)

    # Read order: the early subset first (sorted within itself), then everything else in file order.
    early_idx = np.zeros(0, dtype=int)
    if on_early is not None and 0 < early_windows < take:
        early_idx = np.sort(np.random.default_rng(a.seed + 1).choice(take, size=int(early_windows), replace=False))
    order = np.concatenate([early_idx, np.setdiff1d(np.arange(take), early_idx)]).astype(int)
    for done, k in enumerate(order, start=1):
        y0, x0 = int(origins["y0"].iloc[k]), int(origins["x0"].iloc[k])
        pixels[k] = src.read(0, y0, y0 + window_px, x0, x0 + window_px, channels)
        tell(0.05 + 0.95 * done / take, f"Reading window {done} of {take}")
        if early_idx.size and done == early_idx.size:
            sub_origins = origins.iloc[early_idx].reset_index(drop=True)
            sub_pixels = pixels[early_idx]
            v, b, checkable = _range_for(sub_pixels, src, names, nuclear_name, mask, scale, sub_origins, window_px)
            on_early(Sample(pixels=sub_pixels, names=names, origins=sub_origins, window_px=window_px,
                            nuclear_name=nuclear_name, value_range=v, saturation_checkable=checkable,
                            range_basis=b), take)

    value_range, basis, checkable = _range_for(pixels, src, names, nuclear_name, mask, scale, origins, window_px)
    return Sample(pixels=pixels, names=names, origins=origins, window_px=window_px,
                  nuclear_name=nuclear_name, value_range=value_range,
                  saturation_checkable=checkable, range_basis=basis)
