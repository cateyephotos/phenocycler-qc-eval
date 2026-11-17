"""Panel layout: which antibody ran in which cycle and fluor.

Read from a PhenoCycler panel file (.xpd) or from a CSV with columns
marker, cycle, fluor. Crosstalk checks need this; without it they are off.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .readers import NUCLEAR_NAMES
from .results import norm_name

COLUMNS = ["marker", "cycle", "fluor", "exposure_ms"]

# One name-matching rule for the whole package (defined in results.py).
normalize_name = norm_name


@dataclass
class PanelLayout:
    table: pd.DataFrame  # columns: marker, cycle, fluor, exposure_ms
    source: str


def read_xpd(path) -> PanelLayout:
    """Layout from a .xpd file: cycle is the 1-based well order (blank wells keep their place)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except ValueError as exc:  # JSONDecodeError and UnicodeDecodeError
        raise ValueError(f"Panel file {Path(path).name} could not be read as a .xpd file ({exc}).") from None
    rows = []
    try:
        for cycle, well in enumerate(data.get("wells", []), start=1):
            for item in well.get("items", []):
                marker = str(item.get("markerName", "")).strip()
                if not marker or marker == "--" or marker.upper() == "DAPI":
                    continue
                rows.append({
                    "marker": marker,
                    "cycle": cycle,
                    "fluor": item.get("channel"),
                    "exposure_ms": item.get("exposure"),
                })
    except (AttributeError, TypeError):  # valid JSON, but not the shape of a panel file
        raise ValueError(f"Panel file {Path(path).name} could not be read as a .xpd file "
                         "(it does not have the expected wells and items).") from None
    return PanelLayout(pd.DataFrame(rows, columns=COLUMNS), str(path))


def read_layout_csv(path) -> PanelLayout:
    """Layout from a CSV with columns marker, cycle, fluor (exposure_ms optional)."""
    raw = pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    raw.columns = [str(c).strip() for c in raw.columns]
    missing = [c for c in ("marker", "cycle", "fluor") if c not in raw.columns]
    if missing:
        raise ValueError(f"Layout file {Path(path).name} is missing column(s): {', '.join(missing)}. "
                         "Needed: marker, cycle, fluor.")
    for col in ("marker", "cycle", "fluor"):
        raw[col] = raw[col].str.strip()
    raw = raw[(raw["cycle"] != "") & (raw["fluor"] != "") & (raw["marker"] != "")].copy()
    cycles = []
    # original file row: the header is row 1, so the first data row is row 2 (rows dropped above keep their place)
    for row, marker, text in zip(raw.index + 2, raw["marker"], raw["cycle"]):
        try:
            number = float(text)
        except ValueError:
            number = float("nan")
        if not number.is_integer():
            raise ValueError(f"Layout file {Path(path).name}, row {row} ({marker}): "
                             f"cycle {text!r} is not a whole number.")
        cycles.append(int(number))
    raw["cycle"] = cycles
    if "exposure_ms" in raw.columns:
        raw["exposure_ms"] = pd.to_numeric(raw["exposure_ms"], errors="coerce")
    else:
        raw["exposure_ms"] = float("nan")
    return PanelLayout(raw[COLUMNS].reset_index(drop=True), str(path))


def load_layout(path) -> PanelLayout | None:
    """Layout from a .xpd or .csv file; None when no file is given."""
    if path is None:
        return None
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".xpd":
        return read_xpd(p)
    if suffix == ".csv":
        return read_layout_csv(p)
    raise ValueError(f"Unsupported layout file {p.name}. Use a .xpd panel file or a CSV (marker, cycle, fluor).")


def write_layout_template(channel_names: list[str], path) -> Path:
    """A CSV to fill in: one row per non-nuclear channel, cycle and fluor empty."""
    path = Path(path)
    lines = ["marker,cycle,fluor"]
    for name in channel_names:
        if norm_name(name) in NUCLEAR_NAMES:
            continue
        text = str(name)
        if any(ch in text for ch in ',"\n'):
            text = '"' + text.replace('"', '""') + '"'
        lines.append(f"{text},,")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def match_markers(layout: PanelLayout, channel_names: list[str]) -> tuple[dict[str, str], list[str]]:
    """Match image channel names to layout markers after normalizing both.

    Returns (image name -> layout marker, layout markers not found in the image).
    """
    by_norm: dict[str, str] = {}
    for marker in layout.table["marker"]:
        by_norm.setdefault(normalize_name(marker), marker)
    matched: dict[str, str] = {}
    for name in channel_names:
        marker = by_norm.get(normalize_name(name))
        if marker is not None:
            matched[name] = marker
    found = set(matched.values())
    unmatched: list[str] = []
    for marker in layout.table["marker"]:
        if marker not in found and marker not in unmatched:
            unmatched.append(marker)
    return matched, unmatched
