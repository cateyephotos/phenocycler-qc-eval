"""Read multiplex images: Akoya QPTIFF, OME-TIFF, and Xenium morphology_focus.

Every format is opened through tifffile and read lazily as a zarr array, so only
the window you ask for is loaded. The reader reports facts about the file (names,
pixel size, dtype, pyramid levels); it does not decide intensity ranges.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import tifffile
import zarr

from .results import norm_name

# Channel names (after normalizing) that mean "nuclear stain", used to auto-detect the nuclear
# channel. results._NUCLEAR_NAMES is a different, smaller set on purpose: it only drops the
# nuclear row from loaded tables, where a looser match could hide a real antibody.
NUCLEAR_NAMES = frozenset({"dapi", "hoechst", "dna", "dna1", "dna2", "nuclear"})

_NUCLEAR_PREFIXES = ("dapi", "hoechst", "dna")

_UM_PER_UNIT = {"m": 1e6, "mm": 1e3, "cm": 1e4, "µm": 1.0, "um": 1.0, "nm": 1e-3}


class ReaderError(Exception):
    """The image could not be opened or understood."""


class NoNuclearChannelError(ReaderError):
    """No channel looks like a nuclear stain; the caller must choose one."""

    def __init__(self, channels: list[str]):
        self.channels = list(channels)
        super().__init__(
            "No nuclear channel (DAPI, Hoechst, DNA) found among: "
            + ", ".join(self.channels)
            + ". Name the nuclear channel with --nuclear-channel."
        )


class _Member:
    """One opened TIFF file (or QPTIFF series) holding one or more channels."""

    def __init__(self, tf: tifffile.TiffFile, series):
        self.tf = tf
        levels = list(series.levels)
        self.level_shapes = [_yx(lv) for lv in levels]
        self.dtype = np.dtype(series.dtype)
        self.single_channel_2d = series.axes == "YX"
        self.n_channels = 1 if self.single_channel_2d else int(series.shape[0])
        self.store = series.aszarr()
        zobj = zarr.open(self.store, mode="r")
        if hasattr(zobj, "shape"):  # single-level series come back as one array
            self.arrays = [zobj]
        else:
            self.arrays = [zobj[str(i)] for i in range(len(levels))]

    def close(self) -> None:
        try:
            self.store.close()
        finally:
            self.tf.close()


class ImageSource:
    """An opened image. Use as a context manager or call close().

    Usually one file holds every channel. A Xenium bundle can keep one channel per
    file; then `extra` carries the other files and channel i lives in file i.
    """

    def __init__(self, path: Path, kind: str, tf: tifffile.TiffFile, series,
                 channels: list[str], pixel_size_um: float, extra=()):
        self.path = Path(path)
        self.kind = kind
        self.channels = list(channels)
        self.pixel_size_um = float(pixel_size_um)
        self._members = [_Member(tf, series)]
        try:
            for tf2, series2 in extra:
                self._members.append(_Member(tf2, series2))
        except Exception:
            self.close()
            raise
        first = self._members[0]
        self.dtype = first.dtype
        self.n_levels = len(first.level_shapes)
        self._level_shapes = first.level_shapes
        self.shape_yx = first.level_shapes[0]
        for m in self._members[1:]:
            if m.level_shapes != first.level_shapes or m.dtype != first.dtype:
                self.close()
                raise ReaderError(f"{self.path.name}: the channel files differ in size or data type.")

    @property
    def level_shapes(self) -> list[tuple[int, int]]:
        """(height, width) of every pyramid level, finest first."""
        return [tuple(int(v) for v in shape) for shape in self._level_shapes]

    def level_downsample(self, level: int) -> float:
        """How many full-resolution pixels one pixel of this level covers (along x)."""
        return self.shape_yx[1] / self._level_shapes[level][1]

    def _locate(self, channel: int):
        """(member, index inside that member) for an image channel index."""
        if len(self._members) > 1:
            return self._members[channel], 0
        return self._members[0], channel

    def read(self, level: int, y0: int, y1: int, x0: int, x1: int, channels: list[int]) -> np.ndarray:
        """Pixels of the window, shape (C, h, w), in the given level's pixel grid.

        The window is clipped to the level's extent, so h and w can be smaller than asked.
        """
        planes = []
        for c in channels:
            member, local = self._locate(c)
            arr = member.arrays[level]
            if member.single_channel_2d:
                planes.append(np.asarray(arr[y0:y1, x0:x1]))
            else:
                planes.append(np.asarray(arr[local, y0:y1, x0:x1]))
        return np.stack(planes) if planes else np.zeros((0, 0, 0), dtype=self.dtype)

    def close(self) -> None:
        for m in self._members:
            try:
                m.close()
            except Exception:
                pass

    def __enter__(self) -> "ImageSource":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _yx(series) -> tuple[int, int]:
    shape = tuple(series.shape)
    return int(shape[-2]), int(shape[-1])


# ---------------------------------------------------------------------------- QPTIFF
def channel_name_from_description(description: str, fallback: str) -> str:
    """Channel name from one QPTIFF page description: <Biomarker>, else <Name>."""
    text = description.decode("utf-16", "ignore") if isinstance(description, bytes) else description
    text = text.replace("\x00", "")
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text)
    try:
        root = ET.fromstring(text)
        for tag in ("Biomarker", "Name"):
            value = (root.findtext(tag) or "").strip()
            if value:
                return value
    except ET.ParseError:
        for tag in ("Biomarker", "Name"):
            m = re.search(rf"<{tag}>([^<]*)</{tag}>", text)
            if m and m.group(1).strip():
                return m.group(1).strip()
    return fallback


def _qptiff_pixel_size(page) -> float:
    try:
        xres = page.tags["XResolution"].value
        unit = page.tags["ResolutionUnit"].value
        per_unit = xres[0] / xres[1]
        um_per_unit = {2: 25400.0, 3: 1e4}[int(unit)]
        return um_per_unit / per_unit
    except (KeyError, ZeroDivisionError, TypeError, ValueError):
        try:
            where = Path(page.parent.filehandle.name).name
        except Exception:
            where = "the image"
        raise ReaderError(f"No usable pixel size (XResolution/ResolutionUnit) in {where}")


def _open_qptiff(path: Path) -> ImageSource:
    tf = tifffile.TiffFile(path)
    try:
        if not tf.is_qpi:
            raise ReaderError(f"{path.name} is a TIFF but not an Akoya QPTIFF.")
        series = tf.series[0]
        names = [
            channel_name_from_description(p.description, f"Channel {i + 1}")
            for i, p in enumerate(series.pages)
        ]
        return ImageSource(path, "qptiff", tf, series, names, _qptiff_pixel_size(series.pages[0]))
    except Exception:
        tf.close()
        raise


# ---------------------------------------------------------------------------- OME-TIFF
def _open_ome(path: Path, kind: str, more: tuple = ()) -> ImageSource:
    """Open an OME-TIFF. `more` lists extra files of a one-channel-per-file Xenium bundle."""
    tf = tifffile.TiffFile(path)
    extra: list = []
    try:
        if not tf.is_ome or not tf.ome_metadata:
            raise ReaderError(f"{path.name} has no OME metadata.")
        series = tf.series[0]
        if series.axes not in ("CYX", "YX"):
            raise ReaderError(f"{path.name}: unsupported axes {series.axes!r}; need channels x Y x X.")
        pixels = _first_pixels(tf.ome_metadata)
        n_channels = int(series.shape[0]) if series.axes == "CYX" else 1
        names = [(c.get("Name") or "").strip() for c in _children(pixels, "Channel")]
        if len(names) != n_channels:
            names = names[:n_channels] + [""] * (n_channels - len(names))
        names = [n or f"Channel {i + 1}" for i, n in enumerate(names)]
        size = pixels.get("PhysicalSizeX")
        if size is None:
            raise ReaderError(f"{path.name}: no PhysicalSizeX in the OME metadata, so the pixel size is unknown.")
        unit = pixels.get("PhysicalSizeXUnit", "µm")
        if unit not in _UM_PER_UNIT:
            raise ReaderError(f"{path.name}: unknown pixel size unit {unit!r}.")
        if more and n_channels == 1:
            for k, other in enumerate(more, start=1):
                tf2 = tifffile.TiffFile(other)
                extra.append((tf2, None))
                if not tf2.is_ome or not tf2.ome_metadata:
                    raise ReaderError(f"{Path(other).name} has no OME metadata.")
                series2 = tf2.series[0]
                if series2.axes not in ("CYX", "YX") or (series2.axes == "CYX" and series2.shape[0] != 1):
                    raise ReaderError(f"{Path(other).name}: expected one channel per file.")
                extra[-1] = (tf2, series2)
                own = [(c.get("Name") or "").strip()
                       for c in _children(_first_pixels(tf2.ome_metadata), "Channel")]
                names.append(own[0] if own and own[0] else f"Channel {k + 1}")
        return ImageSource(path, kind, tf, series, names, float(size) * _UM_PER_UNIT[unit], extra)
    except Exception:
        tf.close()
        for tf2, _ in extra:
            tf2.close()
        raise


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(element, name: str):
    return [e for e in element if _strip_ns(e.tag) == name]


def _first_pixels(ome_xml: str):
    try:
        root = ET.fromstring(ome_xml)
    except ET.ParseError as exc:
        raise ReaderError(f"Unreadable OME metadata: {exc}")
    for image in _children(root, "Image"):
        for pixels in _children(image, "Pixels"):
            return pixels
    raise ReaderError("OME metadata has no image.")


# ---------------------------------------------------------------------------- Xenium
def _xenium_files(folder: Path) -> list[Path]:
    for base in (folder / "morphology_focus", folder):
        if base.is_dir():
            hits = sorted(base.glob("morphology_focus*.ome.tif*"))
            if hits:
                return hits
    return []


def open_image(path) -> ImageSource:
    """Open a QPTIFF, an OME-TIFF, or a Xenium output folder (morphology_focus)."""
    p = Path(path)
    if not p.exists():
        raise ReaderError(f"Image not found: {p}")
    if p.is_dir():
        found = _xenium_files(p)
        if not found:
            raise ReaderError(f"{p} is a folder without a morphology_focus image (not a Xenium bundle).")
        return _open_ome(found[0], "xenium", tuple(found[1:]))
    name = p.name.lower()
    if name.endswith((".qptiff", ".qptif")):
        return _open_qptiff(p)
    if name.endswith((".ome.tif", ".ome.tiff")):
        return _open_ome(p, "ome-tiff")
    raise ReaderError(f"Unsupported image type: {p.name}. Use .qptiff, .ome.tif, or a Xenium output folder.")


def nuclear_channel(src: ImageSource, override: str | None = None) -> int:
    """Index of the nuclear channel: the override if given, else the first DAPI/Hoechst/DNA-like name
    (an exact name beats a name that only starts with dapi / hoechst / dna)."""
    if override is not None:
        want = norm_name(override)
        for i, name in enumerate(src.channels):
            if norm_name(name) == want:
                return i
        raise ReaderError(
            f"Nuclear channel {override!r} is not in the image. Channels: {', '.join(src.channels)}."
        )
    for i, name in enumerate(src.channels):
        if norm_name(name) in NUCLEAR_NAMES:
            return i
    for i, name in enumerate(src.channels):  # e.g. "Hoechst33342"; exact matches win (above)
        if norm_name(name).startswith(_NUCLEAR_PREFIXES):
            return i
    raise NoNuclearChannelError(src.channels)
