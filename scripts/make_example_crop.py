"""Cut a small example image out of a big PhenoCycler QPTIFF.

    python scripts/make_example_crop.py --qptiff SCAN.qptiff --y0 PX --x0 PX --size-um 1000 \
        --out examples/tonsil31_crop.ome.tif

The result is a two-level pyramidal OME-TIFF with every channel, the image's own pixel type (uint8 for the
tonsil scan), zlib compression, all channel names and the pixel size. The source file is only read.
The output is deterministic for one set of package versions (tifffile and imagecodecs, recorded in
examples/example_data.json): running this again on the same source gives the same bytes, so the checksum
printed at the end can be checked. A different tifffile or zlib build can write different bytes for the same
pixels; the checksum of the published file is the one that counts.

The crop itself is large (tens of MB) and is NOT committed; it is published as a release file. This script and
`score_example_regions.py` (how the region was chosen) are what is committed.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import numpy as np
import tifffile

from phenocycler_qc_eval.readers import ImageSource, open_image

TILE = 256
FILE_UUID = "urn:uuid:7f0d6c3e-5b1a-4c55-9a52-3d2f6f1e9a10"   # fixed, so the same crop gives the same bytes


def _tile(h: int, w: int) -> tuple[int, int]:
    """Tile shape: 256, or the image side rounded up to a multiple of 16 when it is smaller (TIFF needs 16)."""
    def side(n: int) -> int:
        return TILE if n >= TILE else max(16, -(-n // 16) * 16)
    return side(h), side(w)


def sha256_of(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _halve(data: np.ndarray) -> np.ndarray:
    """2 x 2 mean per channel (rounded), for the second pyramid level."""
    c, h, w = data.shape
    h2, w2 = h // 2 * 2, w // 2 * 2
    block = data[:, :h2, :w2].astype(np.uint16 if data.dtype == np.uint8 else np.float64)
    out = block.reshape(c, h2 // 2, 2, w2 // 2, 2).mean(axis=(2, 4))
    if np.issubdtype(data.dtype, np.integer):
        out = np.rint(out)
    return out.astype(data.dtype)


def crop_to_ome(src: ImageSource, y0: int, x0: int, size_px: int, out: Path) -> Path:
    """Write the square [y0, y0 + size_px) x [x0, x0 + size_px) of every channel to a 2-level OME-TIFF."""
    out = Path(out)
    height, width = src.shape_yx
    y1, x1 = min(int(y0) + int(size_px), height), min(int(x0) + int(size_px), width)
    if y0 < 0 or x0 < 0 or y1 <= y0 or x1 <= x0:
        raise ValueError(f"The region (y0={y0}, x0={x0}, size={size_px}) is outside the {height} x {width} image.")
    data = src.read(0, int(y0), y1, int(x0), x1, list(range(len(src.channels))))
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".part")
    mpp = float(src.pixel_size_um)
    tmp.unlink(missing_ok=True)
    metadata = {"axes": "CYX", "UUID": FILE_UUID, "Channel": {"Name": list(src.channels)},
                "PhysicalSizeX": mpp, "PhysicalSizeXUnit": "µm",
                "PhysicalSizeY": mpp, "PhysicalSizeYUnit": "µm"}
    tile = _tile(data.shape[1], data.shape[2])
    options = dict(tile=tile, compression="zlib", resolutionunit="CENTIMETER", photometric="minisblack")
    try:
        with tifffile.TiffWriter(tmp, ome=True) as tif:
            tif.write(data, subifds=1, resolution=(1e4 / mpp, 1e4 / mpp), metadata=metadata, **options)
            half = _halve(data)
            htile = _tile(half.shape[1], half.shape[2])
            tif.write(half, subfiletype=1, resolution=(1e4 / mpp / 2, 1e4 / mpp / 2),
                      **{**options, "tile": htile})
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)                  # a failed write leaves no half file
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--qptiff", required=True, help="the big source image (only read)")
    p.add_argument("--y0", type=int, required=True, help="top edge of the region, in full-resolution pixels")
    p.add_argument("--x0", type=int, required=True, help="left edge of the region, in full-resolution pixels")
    p.add_argument("--size-um", type=float, default=1000.0, help="side of the square region (default 1000)")
    p.add_argument("--out", required=True, help="where to write the OME-TIFF")
    args = p.parse_args(argv)
    source, target = Path(args.qptiff).resolve(), Path(args.out).resolve()
    if target == source or (source.is_dir() and source in target.parents):
        print("The output must not be the source image or inside it: the source is only read, never replaced. "
              "Choose another --out.", file=sys.stderr)
        return 2
    with open_image(args.qptiff) as src:
        size_px = int(round(args.size_um / src.pixel_size_um))
        out = crop_to_ome(src, args.y0, args.x0, size_px, Path(args.out))
    print(f"Wrote {ascii(str(out))}")
    print(f"size {out.stat().st_size / 1e6:.1f} MB ({size_px} x {size_px} pixels)")
    print(f"sha256 {sha256_of(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
