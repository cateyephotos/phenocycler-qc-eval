"""Score candidate 1 x 1 mm regions of a tonsil scan, to choose the example crop with evidence.

    python scripts/score_example_regions.py --qptiff SCAN.qptiff --out examples/example_region_scores.json

Everything is read from a coarse pyramid level (about 2 um per pixel), so it takes about a minute. A region
earns a score for each thing the example should show:

    tissue       fraction of the region that is tissue (DAPI mask), needed >= 0.9
    follicle     B cell area that also has follicular dendritic network (CD20 high, CD21 high)
    t_zone       T cell area outside the follicles (CD3e high, CD20 low)
    epithelium   Pan-Cytokeratin high area
    tox, cd163   area where TOX / CD163 is high
    carryover    how closely the fine-scale pattern of CD163 follows TOX (the TOX -> CD163 carryover problem
                 looks like TOX's nuclear dots appearing in the CD163 channel)

"High" means above the 85th percentile of that channel over tissue. Each area is scaled against a target
(a generous amount of that feature, about twice what 'above the 85th percentile' gives on average) and capped at 1; the region score is the mean of the
six scaled values plus the smallest of them, so a region with every feature beats one with a few very strong
ones. The list of all regions with their numbers goes to the JSON, so the choice can be checked later.
This script does not decide anything the engine decides: the final check is a real `pqe` run on the crop.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage as ndi
from skimage.filters import threshold_otsu

from phenocycler_qc_eval.readers import open_image

CHANNELS = ["DAPI", "CD20", "CD21", "CD3e", "Pan-Cytokeratin", "TOX", "CD163"]
TARGETS = {"follicle": 0.15, "t_zone": 0.20, "epithelium": 0.10, "tox": 0.25, "cd163": 0.20, "carryover": 0.65}
HIGH_PERCENTILE = 85.0
MIN_TISSUE = 0.90


def _level_for(src, target_um: float = 2.2) -> int:
    best = 0
    for level in range(src.n_levels):
        if src.pixel_size_um * src.level_downsample(level) <= target_um:
            best = level
    return best


def load_coarse(src) -> tuple[dict[str, np.ndarray], float, int]:
    """The scored channels at a coarse level: (name -> float32 plane, micrometers per pixel, level)."""
    level = _level_for(src)
    um = src.pixel_size_um * src.level_downsample(level)
    index = {name: src.channels.index(name) for name in CHANNELS}
    h, w = src.level_shapes[level]
    planes = src.read(level, 0, h, 0, w, [index[n] for n in CHANNELS]).astype(np.float32)
    return dict(zip(CHANNELS, planes)), um, level


def feature_maps(planes: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Boolean maps (tissue and the high areas) plus the fine-scale TOX and CD163 patterns."""
    smooth = ndi.gaussian_filter(planes["DAPI"], 4)
    tissue = smooth > threshold_otsu(smooth)
    tissue = ndi.binary_opening(tissue, iterations=2)
    maps = {"tissue": tissue}
    high = {}
    for name in CHANNELS[1:]:
        blurred = ndi.gaussian_filter(planes[name], 1.0)
        cut = np.percentile(blurred[tissue], HIGH_PERCENTILE) if tissue.any() else np.inf
        high[name] = (blurred > cut) & tissue
    maps["follicle"] = high["CD20"] & high["CD21"]
    maps["t_zone"] = high["CD3e"] & ~high["CD20"]
    maps["epithelium"] = high["Pan-Cytokeratin"]
    maps["tox"] = high["TOX"]
    maps["cd163"] = high["CD163"]
    fine = {n: ndi.gaussian_filter(planes[n], 1.0) - ndi.gaussian_filter(planes[n], 6.0) for n in ("TOX", "CD163")}
    return {"maps": maps, "fine": fine}


def score_region(maps: dict, fine: dict, y0: int, x0: int, size: int) -> dict:
    ys, xs = slice(y0, y0 + size), slice(x0, x0 + size)
    tissue = maps["tissue"][ys, xs]
    t_frac = float(tissue.mean())
    out = {"tissue": round(t_frac, 3)}
    denominator = max(int(tissue.sum()), 1)
    for key in ("follicle", "t_zone", "epithelium", "tox", "cd163"):
        out[key] = round(float(maps[key][ys, xs].sum()) / denominator, 4)
    a, b = fine["TOX"][ys, xs][tissue], fine["CD163"][ys, xs][tissue]
    corr = float(np.corrcoef(a, b)[0, 1]) if a.size > 10 and a.std() > 0 and b.std() > 0 else 0.0
    out["carryover"] = round(max(corr, 0.0), 4)
    scaled = [min(out[k] / TARGETS[k], 1.0) for k in TARGETS]
    out["score"] = round(float(np.mean(scaled) + min(scaled)), 4) if t_frac >= MIN_TISSUE else 0.0
    return out


def score_grid(planes: dict[str, np.ndarray], um_per_px: float, level_scale: float, size_um: float, step_um: float):
    built = feature_maps(planes)
    size = int(round(size_um / um_per_px))
    step = max(1, int(round(step_um / um_per_px)))
    h, w = planes["DAPI"].shape
    rows = []
    for y0 in range(0, h - size + 1, step):
        for x0 in range(0, w - size + 1, step):
            row = score_region(built["maps"], built["fine"], y0, x0, size)
            row["y0"] = int(round(y0 * level_scale))     # full-resolution pixels
            row["x0"] = int(round(x0 * level_scale))
            rows.append(row)
    return rows


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--qptiff", required=True)
    p.add_argument("--out", required=True, help="JSON file for all candidate scores")
    p.add_argument("--size-um", type=float, default=1000.0)
    p.add_argument("--step-um", type=float, default=250.0)
    p.add_argument("--top", type=int, default=10, help="how many best regions to keep in the JSON (default 10)")
    args = p.parse_args(argv)
    with open_image(args.qptiff) as src:
        planes, um, level = load_coarse(src)
        scale = src.level_downsample(level)
        pixel_um = src.pixel_size_um
        rows = score_grid(planes, um, scale, args.size_um, args.step_um)
    rows.sort(key=lambda r: (-r["score"], r["y0"], r["x0"]))
    top = rows[: args.top]
    result = {"size_um": args.size_um, "step_um": args.step_um, "coarse_level": level, "coarse_um_per_pixel": round(um, 3),
              "pixel_size_um": round(pixel_um, 4), "high_percentile": HIGH_PERCENTILE, "targets": TARGETS,
              "min_tissue": MIN_TISSUE, "candidates_scored": len(rows), "top": top}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Scored {len(rows)} regions; best {len(top)} written to {ascii(str(args.out))}")
    for r in top[:5]:
        print("y0 {y0} x0 {x0} score {score} tissue {tissue} follicle {follicle} t_zone {t_zone} "
              "epithelium {epithelium} tox {tox} cd163 {cd163} carryover {carryover}".format(**r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
