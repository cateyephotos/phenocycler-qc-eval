"""Shared test helpers (plain functions, importable from any test)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

PROTO = Path(__file__).parent / "fixtures" / "prototype_run"


def copy_only(src: Path, dest_parent: Path, names: list[str], folder_name: str = "run") -> Path:
    """Copy just the named files from a run folder into a fresh folder; return it."""
    out = Path(dest_parent) / folder_name
    out.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copy2(Path(src) / name, out / name)
    return out


def write_antibody_preview(path: Path, marker: str) -> None:
    """A small synthetic antibody preview .npz in the layout previews.py will write."""
    import numpy as np

    rng = np.random.default_rng(0)
    tiles = rng.integers(0, 255, size=(3, 24, 24), dtype=np.uint8)
    counts, edges = np.histogram(tiles, bins=32, range=(0, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, tiles=tiles, vmin=0.0, vmax=255.0, hist_counts=counts, hist_edges=edges,
             otsu_threshold=90.0, marker=marker)


def write_followup_preview(path: Path, source: str, target: str, control: str = "CD68",
                           with_control: bool = True) -> None:
    """A small synthetic follow-up preview .npz (source/target/control/dapi stacks)."""
    import json

    import numpy as np

    rng = np.random.default_rng(1)

    def stack():
        return rng.integers(0, 255, size=(3, 24, 24), dtype=np.uint8)

    numbers = {"target_where_source_bright": 11.6, "target_where_source_low": 0.4}
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, source=stack(), target=stack(),
             control=stack() if with_control else np.zeros((0, 24, 24), dtype=np.uint8),
             dapi=stack(), numbers=json.dumps(numbers),
             source_name=source, target_name=target, control_name=control)


# ---------------------------------------------------------------------------- synthetic images
_NUCLEAR = {"dapi", "hoechst", "dna"}
_DTYPE_SCALE = {"uint8": 1.0, "uint16": 257.0, "float32": 1.0 / 255.0}


def _is_nuclear(name: str) -> bool:
    return str(name).lower() in _NUCLEAR


def _qptiff_xml(name: str, index: int) -> str:
    biomarker = "" if name == "DAPI" else f"  <Biomarker>{name}</Biomarker>\n"
    plain = "DAPI" if name == "DAPI" else f"Fluor{index}"
    return (
        '<?xml version="1.0" encoding="utf-16"?>\n'
        "<PerkinElmer-QPI-ImageDescription>\n"
        "  <DescriptionVersion>5</DescriptionVersion>\n"
        "  <ImageType>FullResolution</ImageType>\n"
        f"  <Name>{plain}</Name>\n"
        f"{biomarker}"
        "</PerkinElmer-QPI-ImageDescription>"
    )


def _build_planes(channels, size_px, dtype, tissue_fraction, leaks, blank, seed):
    """(C, size, size) array of the requested dtype: nuclei in a central tissue block."""
    import numpy as np

    rng = np.random.default_rng(seed)
    side = int(round(size_px * float(tissue_fraction) ** 0.5))
    lo = (size_px - side) // 2
    n_cells = max(1, int(side * side / 14.0 ** 2))
    cy = rng.integers(lo, lo + side, n_cells)
    cx = rng.integers(lo, lo + side, n_cells)
    label = np.zeros((size_px, size_px), dtype=np.int32)  # radius-6 cell body
    nucleus = np.zeros((size_px, size_px), dtype=bool)  # radius-4 nucleus
    for k, (y, x) in enumerate(zip(cy, cx), start=1):
        y0, y1, x0, x1 = max(y - 6, 0), min(y + 7, size_px), max(x - 6, 0), min(x + 7, size_px)
        yy, xx = np.ogrid[y0:y1, x0:x1]
        d2 = (yy - y) ** 2 + (xx - x) ** 2
        label[y0:y1, x0:x1][d2 <= 36] = k
        nucleus[y0:y1, x0:x1][d2 <= 16] = True
    strength = np.concatenate([[0.0], rng.uniform(0.6, 1.0, n_cells)])
    in_tissue = np.zeros((size_px, size_px), dtype=bool)
    in_tissue[lo:lo + side, lo:lo + side] = True

    raw = {}
    for name in channels:
        if _is_nuclear(name):
            sig = np.where(nucleus, 180.0 * strength[label], 0.0)
            base = 5.0
        elif name in blank:
            sig = np.zeros((size_px, size_px))
            base = 1.0
        else:
            positive = np.concatenate([[False], rng.random(n_cells) < 0.3])
            sig = np.where(positive[label], 150.0 * strength[label], 0.0)
            base = 8.0
        noise_sd = 1.0 if name in blank else 3.0
        raw[name] = base * in_tissue + sig + rng.normal(0.0, noise_sd, (size_px, size_px))
    leaked = {k: v.copy() for k, v in raw.items()}
    for (source, target), fraction in (leaks or {}).items():
        leaked[target] += float(fraction) * raw[source]
    scale = _DTYPE_SCALE[dtype]
    out = np.empty((len(channels), size_px, size_px), dtype=dtype)
    for i, name in enumerate(channels):
        plane = np.clip(leaked[name], 0.0, 255.0) * scale
        out[i] = np.rint(plane) if dtype != "float32" else plane
    return out


def make_synthetic_image(path, kind="qptiff", channels=("DAPI", "CD31", "CD4"), size_px=2048,
                         pixel_um=0.5, dtype="uint8", tissue_fraction=0.6, leaks=None, blank=(),
                         seed=0, one_file_per_channel=False):
    """Write a small multiplex image and return its path (a folder for kind='xenium')."""
    import numpy as np
    import tifffile

    path = Path(path)
    channels = list(channels)
    data = _build_planes(channels, size_px, dtype, tissue_fraction, leaks, set(blank), seed)
    half = data[:, ::2, ::2]
    tile = (64, 64)
    if kind == "qptiff":
        res_cm = 1e4 / pixel_um
        common = dict(tile=tile, photometric="minisblack", software="PerkinElmer-QPI",
                      metadata=None, resolutionunit="CENTIMETER")
        with tifffile.TiffWriter(path) as tw:
            for i, name in enumerate(channels):
                tw.write(data[i], description=_qptiff_xml(name, i), resolution=(res_cm, res_cm), **common)
            thumb = np.ascontiguousarray(data[0][::8, ::8])
            tw.write(thumb, photometric="minisblack", software="PerkinElmer-QPI",
                     description=_qptiff_xml("Thumbnail", 0), metadata=None)
            for i, name in enumerate(channels):
                tw.write(np.ascontiguousarray(half[i]), description=_qptiff_xml(name, i),
                         resolution=(res_cm / 2, res_cm / 2), **common)
        return path
    if kind in ("ome", "xenium"):
        if kind == "xenium":
            folder = path / "morphology_focus"
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / "morphology_focus_0000.ome.tif"
            if one_file_per_channel:  # newer bundles: one single-channel file per channel
                for i, name in enumerate(channels):
                    meta = {"axes": "YX", "Channel": {"Name": [name]}, "PhysicalSizeX": pixel_um,
                            "PhysicalSizeY": pixel_um, "PhysicalSizeXUnit": "µm", "PhysicalSizeYUnit": "µm"}
                    with tifffile.TiffWriter(folder / f"morphology_focus_{i:04d}.ome.tif", ome=True) as tw:
                        tw.write(data[i], subifds=1, metadata=meta, photometric="minisblack", tile=tile)
                        tw.write(np.ascontiguousarray(half[i]), subfiletype=1, photometric="minisblack", tile=tile)
                return path
        else:
            target = path
        meta = {"axes": "CYX", "Channel": {"Name": channels}, "PhysicalSizeX": pixel_um,
                "PhysicalSizeY": pixel_um, "PhysicalSizeXUnit": "µm", "PhysicalSizeYUnit": "µm"}
        with tifffile.TiffWriter(target, ome=True) as tw:
            tw.write(data, subifds=1, metadata=meta, photometric="minisblack", tile=tile)
            tw.write(np.ascontiguousarray(half), subfiletype=1, photometric="minisblack", tile=tile)
        return path
    raise ValueError(f"unknown kind {kind!r}")


def make_layout_csv(path, rows):
    """Write a layout CSV from (marker, cycle, fluor) rows."""
    path = Path(path)
    lines = ["marker,cycle,fluor"] + [f"{m},{c},{f}" for m, c, f in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_xpd(path, wells):
    """Write a minimal .xpd. wells: list of (well_name, [(marker, fluor), ...]); DAPI is added to the first."""
    out_wells = []
    for k, (well, items) in enumerate(wells):
        rows = [("DAPI", "DAPI")] if k == 0 else []
        rows += list(items)
        out_wells.append({"wellName": well, "items": [
            {"inventoryItemType": "Marker", "markerName": m, "channel": f, "exposure": 150} for m, f in rows]})
    Path(path).write_text(json.dumps({"formatVersion": "2", "wells": out_wells}, indent=2), encoding="utf-8")
    return Path(path)


def small_settings(n_windows: int = 12, min_windows: int = 4, previews: int = 3):
    """Default settings with a small, fast sample (the real defaults need 20+ windows)."""
    from phenocycler_qc_eval.settings import default_settings

    s = default_settings()
    s.advanced.n_windows = n_windows
    s.advanced.min_windows = min_windows
    s.advanced.n_preview_windows = previews
    return s


# ---------------------------------------------------------------------------- fault injection (the judge)
def inject(smp, *, scale: dict[str, float] = {}, leak: dict[tuple[str, str], float] = {}):  # noqa: B006
    """A copy of a Sample with known faults written into its pixels (the original is not touched).

    scale: {channel: factor} multiplies that channel (0.12 dims it, 8 over-exposes it).
    leak:  {(source, target): fraction} adds fraction x the source channel's ORIGINAL pixels to the target.
    The arithmetic is done in float from the original pixels, then rounded and clipped to the dtype's range
    (floating-point images are only kept at or above zero). Channel names must be in `smp.names`.
    """
    import dataclasses

    import numpy as np

    index = {name: i for i, name in enumerate(smp.names)}
    for name in list(scale) + [n for pair in leak for n in pair]:
        if name not in index:
            raise KeyError(f"channel {name!r} is not in the sample: {smp.names}")
    pixels = smp.pixels.copy()
    changed = set(scale) | {target for _, target in leak}
    for name in changed:
        i = index[name]
        plane = smp.pixels[:, i].astype(np.float64) * float(scale.get(name, 1.0))
        for (source, target), fraction in leak.items():
            if target == name:
                plane += float(fraction) * smp.pixels[:, index[source]].astype(np.float64)
        if np.issubdtype(smp.pixels.dtype, np.integer):
            info = np.iinfo(smp.pixels.dtype)
            pixels[:, i] = np.clip(np.rint(plane), info.min, info.max).astype(smp.pixels.dtype)
        else:
            pixels[:, i] = np.clip(plane, 0.0, None).astype(smp.pixels.dtype)
    return dataclasses.replace(smp, pixels=pixels)


class JudgeRun:
    """What one analyze_sample run said, in the shapes the judge checks read."""

    def __init__(self, folder: Path, seconds: float, n_windows: int) -> None:
        import pandas as pd

        self.folder, self.seconds, self.n_windows = Path(folder), seconds, n_windows
        self.channels = pd.read_csv(self.folder / "stain_qc_channels.csv").set_index("channel", drop=False)
        pairs = self.folder / "stain_qc_crosstalk_pairs.csv"
        self.table = pd.read_csv(pairs) if pairs.exists() else pd.DataFrame(
            columns=["source", "target", "kind", "flag", "flag_reason", "source_near_blank", "target_near_blank"])
        followups = self.folder / "stain_qc_followups.json"
        self.followups = json.loads(followups.read_text(encoding="utf-8")) if followups.exists() else []
        self.calls: dict[str, str] = {str(k): str(v) for k, v in self.channels["call"].items()}
        flagged = self.table[self.table["flag"].astype(bool)]
        self.flags: set[tuple[str, str]] = set(zip(flagged["source"].astype(str), flagged["target"].astype(str)))

    def pair(self, source: str, target: str):
        """The crosstalk-table row for one orientation of a pair (None when the pair is not in the table)."""
        rows = self.table[(self.table["source"] == source) & (self.table["target"] == target)]
        return None if rows.empty else rows.iloc[0]


def judge_run(smp, layout, out_dir, retries: int = 3) -> JudgeRun:
    """analyze_sample on `smp` with default settings; a random SystemError is retried, a repeated one is real."""
    import time

    from phenocycler_qc_eval import __version__
    from phenocycler_qc_eval.run import analyze_sample
    from phenocycler_qc_eval.settings import default_settings

    settings = default_settings()
    meta = {"image": "judge", "panel": "judge layout" if layout is not None else "none", "pixel_size_um": 0.5,
            "dtype": str(smp.pixels.dtype), "n_windows": int(len(smp.pixels)), "n_windows_requested": 200,
            "window_px": int(smp.window_px), "version": __version__, "started": "judge", "warnings": []}
    last = None
    for attempt in range(retries):
        began = time.monotonic()
        try:
            analyze_sample(smp, layout, settings, out_dir, meta)
            return JudgeRun(Path(out_dir), time.monotonic() - began, int(len(smp.pixels)))
        except SystemError as exc:                                  # the workstation's sporadic native flake
            if last is not None and str(exc) == last:
                raise
            last = str(exc)
    raise RuntimeError(f"analyze_sample kept failing with SystemError: {last}")


# ---------------------------------------------------------------------------- guard rails for the judge checks
def pixels_digest(smp) -> str:
    """A fingerprint of a Sample's pixels, to show later that no run changed the shared sample."""
    import hashlib

    import numpy as np

    return hashlib.sha256(np.ascontiguousarray(smp.pixels).tobytes()).hexdigest()


def assert_leak_landed(baseline, faulty, pair: tuple[str, str], *, margin: float = 0.1) -> None:
    """The injected leak is in the numbers: the pair's texture match rose by more than `margin` and the target got
    brighter. Without this, "the engine did not flag it" could mean "the leak never applied"."""
    b, f = baseline.pair(*pair), faulty.pair(*pair)
    assert b is not None and f is not None, f"{pair} is not in the table"
    rise = float(f["highpass_corr"]) - float(b["highpass_corr"])
    assert rise > margin, f"{pair}: texture match rose by {rise:.3f}, not more than {margin}"
    target = pair[1]
    before, after = (float(r.channels.loc[target, "signal_p99_5"]) for r in (baseline, faulty))
    assert after > before, f"{target}: signal {after:g} after the leak, not above {before:g} before"


def assert_call_moved(baseline, faulty, channel: str, wanted: tuple[str, ...], *, scale: float | None = None) -> None:
    """The fault changed a call: the faulty run holds one of `wanted`, and the unmodified run did not.

    When the unmodified run already held one of `wanted` (a channel that was weak to begin with), the call cannot
    show the fault. Then `scale`, the factor that was injected, must show in the channel's signal instead (within
    a factor of two, because pixel values are whole numbers); without `scale` that case fails."""
    assert faulty.calls[channel] in wanted, f"{channel}: call {faulty.calls[channel]} after the fault, wanted {wanted}"
    if baseline.calls[channel] not in wanted:
        return
    assert scale is not None,         f"{channel} already had call {baseline.calls[channel]} before the fault, so the check proves nothing"
    before, after = (float(r.channels.loc[channel, "signal_p99_5"]) for r in (baseline, faulty))
    ratio = after / before if before else float("inf")
    assert 0.5 * scale <= ratio <= 1.6 * scale,         f"{channel}: signal went from {before:g} to {after:g} (x{ratio:.2f}), not about x{scale:g}"
