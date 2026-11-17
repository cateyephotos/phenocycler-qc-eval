"""Load a run folder (current, partial or prototype) into one Results object.

Also home of safe_name() and load_preview(), so engine code and tests never
need tkinter.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .advice import next_run_text

CHANNELS_CSV = "stain_qc_channels.csv"
PAIRS_CSV = "stain_qc_crosstalk_pairs.csv"
EXCLUSIVE_CSV = "stain_qc_exclusive_pairs.csv"
FOLLOWUPS_JSON = "stain_qc_followups.json"
RUN_INFO_JSON = "run_info.json"
INCOMPLETE_TXT = "RUN_INCOMPLETE.txt"
REPORT_MD = "STAIN_QC_REPORT.md"

_TEXT_COLUMNS = ("call", "exposure", "next_run", "reason")
_DETECTION_COLUMNS = [
    "channel", "call", "exposure", "next_run", "reason",
    "signal_p99_5", "stain_index", "positive_fraction", "saturation_fraction",
]
_NUCLEAR_NAMES = {"dapi", "hoechst", "dna"}


class NotAResultsFolder(Exception):
    """The folder holds neither stain_qc_channels.csv nor run_info.json."""


NOT_JUDGED_MESSAGE = ("Crosstalk could not be judged for this panel: too few unrelated channel pairs (markers two or "
                      "more cycles apart in different fluors) to set a cut-off, so no pair is flagged or cleared. "
                      "Panels of one or two cycles, or a single-round image, cannot be judged.")


@dataclass
class Results:
    folder: Path
    detection: pd.DataFrame
    crosstalk: pd.DataFrame | None = None
    crosstalk_enabled: bool = False
    crosstalk_message: str = ""
    exclusive_pairs: pd.DataFrame | None = None
    followups: list[dict] = field(default_factory=list)
    figures: list[Path] = field(default_factory=list)
    settings: dict = field(default_factory=dict)
    status: str = "complete"  # complete | running | cancelled | error
    incomplete_note: str | None = None
    unmatched: list[str] = field(default_factory=list)
    saturation_checked: bool = True
    report: Path | None = None
    # Beyond the contract: tells "pending" from "off" (ruling R2).
    crosstalk_state: str = "off"  # on | off | pending
    # Run identity for the window header (from run_info.json when present).
    image_name: str = ""
    image_path: str = ""
    panel_path: str = ""            # the panel file the run used (run_info.json "panel"), "" when there was none
    run_date: str = ""
    intensity_range: float = 255.0  # the "of 255" in evidence phrases

    @property
    def crosstalk_not_judged(self) -> bool:
        """Crosstalk is off because the panel had too few control pairs to set a cut-off (not for lack of a panel)."""
        return self.crosstalk_state == "off" and self.crosstalk_message.startswith(NOT_JUDGED_MESSAGE[:40])


def safe_name(marker: str) -> str:
    """Marker name made safe for a file name: letters, digits, '-', '_'; others -> '_'."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(marker))


def norm_name(name: str) -> str:
    """Lowercase and keep only letters and digits: the one name-matching rule."""
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_csv(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        return pd.read_csv(path)
    except (OSError, ValueError):
        return None


def _detection_table(raw: pd.DataFrame | None, nuclear: str | None) -> pd.DataFrame:
    if raw is None:
        return pd.DataFrame(columns=_DETECTION_COLUMNS)
    det = raw.copy()
    if "channel" in det.columns:
        nuclear_norm = norm_name(nuclear) if nuclear else None
        drop = det["channel"].map(lambda c: norm_name(c) in _NUCLEAR_NAMES or norm_name(c) == nuclear_norm)
        det = det[~drop].reset_index(drop=True)
    for col in _DETECTION_COLUMNS:
        if col not in det.columns:
            det[col] = None if col in _TEXT_COLUMNS else np.nan
    det["exposure"] = det["exposure"].fillna("KEEP")
    det["reason"] = det["reason"].fillna("")
    missing = det["next_run"].isna()
    det.loc[missing, "next_run"] = [
        next_run_text(c, e) for c, e in zip(det.loc[missing, "call"], det.loc[missing, "exposure"])
    ]
    return det


def _status(folder: Path, info: dict | None) -> str:
    raw = (info or {}).get("status")
    if raw:
        raw = str(raw).lower()
        if raw == "failed":
            return "error"
        return raw if raw in ("complete", "running", "cancelled", "error") else "error"
    marker = folder / INCOMPLETE_TXT
    if marker.exists():
        try:
            text = marker.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            text = ""
        if "cancel" in text:
            return "cancelled"
        if "running" in text:
            return "running"
        return "error"
    return "complete"


_STAMP = re.compile(r"^(?P<stem>.+)_(?P<d>\d{8})-(?P<t>\d{6})$")


def _fmt_date(dt: datetime) -> str:
    return f"{dt.day} {dt:%b %Y %H:%M}"


def _run_identity(folder: Path, info: dict | None, channels_path: Path) -> tuple[str, str, str]:
    """(image name, image path, date) for the header: run_info first, else the folder name."""
    info = info or {}
    path = str(info.get("image_path") or "")
    stamp = _STAMP.match(folder.name)
    name = str(info.get("image_name") or (Path(path).name if path else "")
               or (stamp.group("stem") if stamp else folder.name))
    date = ""
    for key in ("started_at", "date"):
        try:
            date = _fmt_date(datetime.fromisoformat(str(info[key])))
            break
        except (KeyError, ValueError):
            continue
    if not date and stamp:
        try:
            date = _fmt_date(datetime.strptime(stamp.group("d") + stamp.group("t"), "%Y%m%d%H%M%S"))
        except ValueError:
            pass
    if not date:
        try:
            ref = channels_path if channels_path.exists() else folder
            date = "saved " + _fmt_date(datetime.fromtimestamp(ref.stat().st_mtime))
        except OSError:
            pass
    return name, path, date


def _panel_text(value) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in ("", "none", "null") else text


def load_results(folder: Path, ended: str | None = None) -> Results:
    """Load a run folder. ended ("cancelled" or "error") says the run is over although the folder may still
    say "running" (a hard stop or a dead worker): it is then shown as cancelled / failed, never as running."""
    folder = Path(folder)
    channels_path = folder / CHANNELS_CSV
    info_path = folder / RUN_INFO_JSON
    if not folder.is_dir() or not (channels_path.exists() or info_path.exists()):
        raise NotAResultsFolder(
            f"{folder} is not a results folder (no {CHANNELS_CSV} or {RUN_INFO_JSON})"
        )
    info = _read_json(info_path) if info_path.exists() else None
    info = info if isinstance(info, dict) else None

    status = _status(folder, info)
    if status == "running" and ended in ("cancelled", "error"):
        status = ended
    detection = _detection_table(_read_csv(channels_path), (info or {}).get("nuclear_channel"))
    crosstalk = _read_csv(folder / PAIRS_CSV)
    exclusive = _read_csv(folder / EXCLUSIVE_CSV)

    state = (info or {}).get("crosstalk_state")
    if state not in ("on", "off", "pending"):
        if (folder / PAIRS_CSV).exists():
            state = "on"
        else:
            state = "off" if status == "complete" else "pending"

    followups_raw = _read_json(folder / FOLLOWUPS_JSON)
    followups = [f for f in followups_raw if isinstance(f, dict)] if isinstance(followups_raw, list) else []

    figures = sorted(folder.glob("stain_qc_*.png")) + sorted(folder.glob("followup_*.png"))

    if status in ("cancelled", "error"):
        early, of = (info or {}).get("partial_windows"), (info or {}).get("early_of")
        if not channels_path.exists():
            usable = "no results were saved"
        elif early and of:
            cause = "the run was cancelled" if status == "cancelled" else "the run failed before finishing"
            usable = f"Antibody results are from only {early} of {of} windows; {cause}."
        else:
            usable = "antibody results are complete; crosstalk did not finish"
        incomplete = f"Run {'cancelled' if status == 'cancelled' else 'failed'}: {usable}"
    elif status == "running" and channels_path.exists():
        early, of = (info or {}).get("partial_windows"), (info or {}).get("early_of")
        if early and of:
            incomplete = f"Early antibody results from {early} of {of} windows. Reading the rest..."
        else:
            incomplete = "Antibody results ready. Checking crosstalk..."
    else:
        incomplete = None

    report = folder / REPORT_MD
    image_name, image_path, run_date = _run_identity(folder, info, channels_path)
    try:
        intensity_range = float((info or {}).get("intensity_range", 255.0))
    except (TypeError, ValueError):
        intensity_range = 255.0
    return Results(
        folder=folder,
        detection=detection,
        crosstalk=crosstalk,
        crosstalk_enabled=state == "on",
        crosstalk_message=str((info or {}).get("crosstalk_message") or ""),
        exclusive_pairs=exclusive,
        followups=followups,
        figures=figures,
        settings=dict((info or {}).get("settings") or {}),
        status=status,
        incomplete_note=incomplete,
        unmatched=[str(u) for u in ((info or {}).get("unmatched") or [])],
        saturation_checked=bool((info or {}).get("saturation_checked", True)),
        report=report if report.exists() else None,
        crosstalk_state=state,
        image_name=image_name,
        image_path=image_path,
        panel_path=_panel_text((info or {}).get("panel")),
        run_date=run_date,
        intensity_range=intensity_range if intensity_range > 0 else 255.0,
    )


def antibody_preview_path(r: Results, marker: str) -> Path | None:
    p = r.folder / "previews" / f"antibody_{safe_name(marker)}.npz"
    return p if p.exists() else None


def followup_preview_path(r: Results, source: str, target: str) -> Path | None:
    p = r.folder / "previews" / f"followup_{safe_name(source)}_to_{safe_name(target)}.npz"
    return p if p.exists() else None


def followup_figure_path(r: Results, source: str, target: str) -> Path | None:
    p = r.folder / f"followup_{safe_name(source)}_to_{safe_name(target)}.png"
    return p if p.exists() else None


def load_preview(path: Path) -> dict:
    """Read a preview .npz into a dict.

    Arrays stay numpy arrays; 0-d entries become Python scalars or str
    (marker, source_name, vmin, ...). 'numbers' stays a JSON string.
    """
    out: dict = {}
    with np.load(Path(path), allow_pickle=False) as z:
        for key in z.files:
            val = z[key]
            out[key] = val.item() if val.ndim == 0 else val
    return out
