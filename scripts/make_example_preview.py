"""Write a small colour picture of the example image, as evidence of what it holds.

    python scripts/make_example_preview.py --image examples/tonsil31_crop.ome.tif --out examples/tonsil31_crop_preview.png

DAPI is blue, CD20 (B cells, follicles) green, CD3e (T cells) red and Pan-Cytokeratin (epithelium) orange, added
together on a black background. The picture is downsampled by block averaging, so it is small (well under
200 KB) and can be committed; the image itself cannot. The image is only read.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from phenocycler_qc_eval.readers import ImageSource, open_image

# channel name -> (red, green, blue) weights of its colour
COLORS = {"DAPI": (0.0, 0.2, 1.0), "CD20": (0.0, 1.0, 0.0), "CD3e": (1.0, 0.0, 0.0),
          "Pan-Cytokeratin": (1.0, 0.55, 0.0)}


def _block_mean(plane: np.ndarray, out_px: int) -> np.ndarray:
    """Mean of equal blocks so that the longer side becomes about out_px pixels (exactly out_px at most)."""
    h, w = plane.shape
    factor = max(1, int(np.ceil(max(h, w) / out_px)))
    h2, w2 = -(-h // factor) * factor, -(-w // factor) * factor
    padded = np.pad(plane.astype(np.float64), ((0, h2 - h), (0, w2 - w)), mode="edge")
    return padded.reshape(h2 // factor, factor, w2 // factor, factor).mean(axis=(1, 3))


def make_preview(src: ImageSource, out, max_px: int = 320) -> Path:
    """Write the picture to `out` (PNG) and return its path. The longer side is max_px pixels."""
    import imagecodecs
    import skimage.transform

    missing = [name for name in COLORS if name not in src.channels]
    if missing:
        raise ValueError(f"The image has no channel called {', '.join(missing)}.")
    height, width = src.shape_yx
    top = float(np.iinfo(src.dtype).max) if np.issubdtype(src.dtype, np.integer) else 1.0
    picture = None
    for name, color in COLORS.items():
        plane = src.read(0, 0, height, 0, width, [src.channels.index(name)])[0]
        small = _block_mean(plane, max_px) / top
        low, high = np.percentile(small, 1), np.percentile(small, 99.5)
        small = np.clip((small - low) / max(high - low, 1e-9), 0.0, 1.0)      # stretch each channel to its own range
        if picture is None:
            picture = np.zeros(small.shape + (3,))
        for k in range(3):
            picture[..., k] += small * color[k]
    rgb = np.rint(np.clip(picture, 0.0, 1.0) * 255).astype(np.uint8)
    longest = max(rgb.shape[:2])
    if longest != max_px:                              # the block size is a whole number: resize the last bit
        scale = max_px / longest
        rgb = np.rint(skimage.transform.resize(rgb, (max(1, round(rgb.shape[0] * scale)),
                                                     max(1, round(rgb.shape[1] * scale)), 3),
                                               order=1, anti_aliasing=True, preserve_range=True)).astype(np.uint8)
    rgb = (rgb // 16) * 17                              # 16 levels per colour: a picture, not data, and it shrinks the file
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(imagecodecs.png_encode(rgb, level=9))
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--image", required=True, help="the example image (only read)")
    p.add_argument("--out", required=True, help="where to write the PNG")
    p.add_argument("--max-px", type=int, default=320, help="longer side of the picture (default 320)")
    args = p.parse_args(argv)
    with open_image(args.image) as src:
        out = make_preview(src, args.out, args.max_px)
    print(f"Wrote {ascii(str(out))} ({out.stat().st_size / 1e3:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
