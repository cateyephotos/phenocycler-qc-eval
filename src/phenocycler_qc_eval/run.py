"""One function turns an image into a finished run folder: run_qc().

The folder can be opened by `load_results` at every stage. Order of work:
  1. open the image and cut up the tissue windows (an early set of windows is analyzed first),
  2. antibody results (detection table and previews) are written, then
  3. crosstalk, follow-up panels, figures and the report.
RUN_INCOMPLETE.txt sits in the folder until the very end, so a crashed or cancelled run is still readable.

Contract for callers (the GUI worker reads these):
  * RunRequest.on_started(out_dir) is called once, as soon as the run folder exists and is loadable
    (marker and run_info.json written), before any image work. Use it to learn the folder while the run is going.
  * progress(stage, fraction, message) gets stage names as they happen. Two of them mean "antibody results are
    on disk and load_results(out_dir) can read them":
      EARLY_STAGE   fires once, only on big images (more than EARLY_WINDOWS windows), after the early subset
                    of windows is scored. The CSV holds a windows_used column; run_info.json has
                    partial_windows / early_of. These results come from a few windows only.
      PARTIAL_STAGE fires once, after the final antibody results (all windows) are written. Crosstalk follows.
  * The fraction passed to progress never goes backwards.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    import pandas as pd

    from .crosstalk import CrosstalkResult
    from .layout import PanelLayout
    from .tissue import Sample

from . import __version__
from .fsutil import replace_file
from .settings import Settings, settings_to_dict

# The analysis modules (numpy, scipy, scikit-image, matplotlib) are imported inside the functions that use
# them, so the GUI can import find_lone_xpd / make_run_dir without loading the whole engine.

INCOMPLETE_MARKER = "RUN_INCOMPLETE.txt"
PARTIAL_STAGE = "antibody_results_ready"   # final antibody results (all windows) are on disk
EARLY_STAGE = "antibody_results_early"     # early antibody results (a subset of windows) are on disk
EARLY_WINDOWS = 50     # windows analyzed first so antibody results appear early on a big image

ProgressFn = Callable[[str, float, str], None]  # (stage, overall fraction 0..1, message)

# Overall progress marks (fractions of the whole run).
P_OPEN, P_MASK, P_SAMPLED, P_DETECTED, P_PARTIAL = 0.02, 0.10, 0.50, 0.55, 0.60
P_CROSSTALK, P_FOLLOWUPS, P_FIGURES, P_REPORT = 0.62, 0.90, 0.95, 0.98


class RunCancelled(Exception):
    """Raise this from a progress callback to stop the run; the folder is marked cancelled."""


@dataclass
class RunRequest:
    image: Path
    panel: Path | None
    output_root: Path
    settings: Settings
    nuclear_channel: str | None = None
    save_sample: bool = False
    settings_warnings: list[str] = field(default_factory=list)   # R7: from the settings load
    on_started: Callable[[Path], None] | None = None             # called with the run folder once it exists


# ---------------------------------------------------------------------------- shared lookups (R16)
def image_stem(image) -> str:
    """File name without its image extension ('a.ome.tif' -> 'a'; a bundle folder keeps its name)."""
    p = Path(image)
    stem = p.name if p.is_dir() else p.stem
    return stem[:-4] if stem.lower().endswith(".ome") else stem


def make_run_dir(output_root, image, now: datetime | None = None) -> Path:
    """Create <output_root>/<image_stem>_<YYYYMMDD-HHMMSS> (then _2, _3, ... on collision); never reuses a folder."""
    now = now or datetime.now()
    base = f"{image_stem(image)}_{now:%Y%m%d-%H%M%S}"
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        path = root / (base if n == 1 else f"{base}_{n}")
        try:
            path.mkdir()
            return path
        except FileExistsError:
            n += 1


def find_lone_xpd(image) -> Path | None:
    """The single .xpd panel file next to the image (or next to a bundle folder), else None."""
    folder = Path(image).parent
    try:
        found = sorted(p for p in folder.glob("*.xpd") if p.is_file())
    except OSError:
        return None
    return found[0] if len(found) == 1 else None


# ---------------------------------------------------------------------------- run folder bookkeeping
def _write_json(path: Path, obj) -> None:
    """Write via a temp file and swap it in, so a crash never leaves half a file behind."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=True, default=float), encoding="utf-8")
    replace_file(tmp, path)


REMOVE = object()   # pass as a value to update_run_info to delete that key


def update_run_info(out_dir, **changes) -> dict:
    """Merge `changes` into run_info.json (created when absent; REMOVE deletes a key) and return the result."""
    path = Path(out_dir) / "run_info.json"
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(info, dict):
            info = {}
    except (OSError, ValueError):
        info = {}
    for key, value in changes.items():
        if value is REMOVE:
            info.pop(key, None)
        else:
            info[key] = value
    _write_json(path, info)
    return info


def _write_marker(out_dir: Path, text: str) -> None:
    (Path(out_dir) / INCOMPLETE_MARKER).write_text(text + "\n", encoding="utf-8")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------- analysis
def _write_antibody_results(smp: Sample, settings: Settings, out: Path, step=None) -> pd.DataFrame:
    """Detection table and antibody previews, plus the run_info fields that depend on the sample.
    step(kind, done, total) reports each antibody scored ("score") and each preview written ("preview")."""
    from .detection import detection_table
    from .previews import write_antibody_previews

    det = detection_table(smp, settings.detection, (lambda i, n: step("score", i, n)) if step else None)
    det["windows_used"] = int(len(smp.pixels))     # the file itself says how many windows it rests on
    # Previews are swapped in first and the CSV last, each by temp file + replace, so a reader that finds the
    # CSV never meets a half-written file (and never sees a CSV newer than its previews).
    csv_path = out / "stain_qc_channels.csv"
    tmp = csv_path.with_name(csv_path.name + ".tmp")
    try:
        write_antibody_previews(smp, det, out, k=settings.advanced.n_preview_windows, seed=settings.advanced.seed,
                                progress=(lambda i, n: step("preview", i, n)) if step else None)
        det.to_csv(tmp, index=False)
        replace_file(tmp, csv_path)
    except BaseException:
        _remove_temp_files(out)      # a failed write leaves no half-written temp files behind
        raise
    update_run_info(out, intensity_range=float(smp.value_range), range_basis=smp.range_basis,
                    saturation_checked=bool(smp.saturation_checkable), nuclear_channel=smp.nuclear_name)
    return det


def _remove_temp_files(out: Path) -> None:
    """Delete the temp files an interrupted antibody write can leave (CSV and preview .npz)."""
    leftovers = [out / "stain_qc_channels.csv.tmp", *(out / "previews").glob(".tmp_antibody_*.npz")]
    for path in leftovers:
        try:
            path.unlink()
        except OSError:
            pass


def _flagged_summary(xt: CrosstalkResult) -> list[dict]:
    from .crosstalk import followup_pairs

    if not xt.enabled or xt.table is None:
        return []
    out = []
    for s, t in followup_pairs(xt.table):
        row = xt.table[(xt.table["source"] == s) & (xt.table["target"] == t)].iloc[0]
        out.append({"source": s, "target": t, "kind": str(row["kind"]), "asymmetry": round(float(row["asymmetry"]), 3)})
    return out


def analyze_sample(smp: Sample, layout: PanelLayout | None, settings: Settings, out_dir, meta: dict,
                   progress: ProgressFn | None = None) -> dict:
    """Run every analysis on one Sample and write the result files. Returns the summary dict.

    Antibody results are on disk (and `progress` told) before any crosstalk work starts.
    meta: image, panel, pixel_size_um, dtype, n_windows, window_px, version, started, and optionally
    warnings (list of text) and finished; value_range, range_basis, nuclear_channel and image_only are
    filled in from the sample when absent.
    """
    from .crosstalk import (
        control_for, exclusive_pairs_table, followup_numbers, followup_pairs, run_crosstalk,
    )
    from .figures import write_figures
    from .layout import match_markers
    from .previews import write_followup_preview
    from .report import write_report

    def tell(stage: str, fraction: float, message: str) -> None:
        if progress is not None:
            progress(stage, fraction, message)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    adv = settings.advanced
    meta = dict(meta)
    meta.setdefault("value_range", float(smp.value_range))
    meta.setdefault("range_basis", smp.range_basis)
    meta.setdefault("nuclear_channel", smp.nuclear_name)
    antibodies = [n for n in smp.names if n != smp.nuclear_name]
    if layout is not None and "image_only" not in meta:
        matched, _ = match_markers(layout, antibodies)
        meta["image_only"] = [n for n in antibodies if n not in matched]

    # (1) antibody results first
    tell("detection", P_DETECTED, "Scoring antibodies")

    def scoring_step(kind: str, done: int, total: int) -> None:
        # A count in every message: the window shows movement, so a long step never looks stuck.
        # The same words as the step list on the page: "Scoring antibodies (first pass: antibody 3 of 31)".
        words = "Scoring antibodies, first pass: antibody" if kind == "score" else "Scoring antibodies, second pass: preview"
        tell("detection", P_SAMPLED + (P_PARTIAL - P_SAMPLED) * ((0 if kind == "score" else total) + done) / (2 * total),
             f"{words} {done} of {total}")

    det = _write_antibody_results(smp, settings, out, scoring_step)
    update_run_info(out, partial_windows=REMOVE, early_of=REMOVE)
    tell(PARTIAL_STAGE, P_PARTIAL, "Antibody results are ready")

    # (2) crosstalk and the pairs that should not overlap
    tell("crosstalk", P_CROSSTALK, "Checking crosstalk" if layout is not None else "Crosstalk is off")
    def texture_step(done: int, total: int) -> None:
        tell("crosstalk", P_CROSSTALK + (P_FOLLOWUPS - 0.05 - P_CROSSTALK) * done / max(total, 1),
             f"Checking crosstalk: fine detail, window {done + 1} of {total}")

    xt = run_crosstalk(smp, det, layout, settings.crosstalk, adv.highpass_sigma,
                       texture_step if layout is not None else None)
    if xt.table is not None:        # also when crosstalk was "not judged": the CSV then says so on every pair
        xt.table.to_csv(out / "stain_qc_crosstalk_pairs.csv", index=False)
        xt.spill.to_csv(out / "stain_qc_spill_matrix.csv")
        xt.highpass.to_csv(out / "stain_qc_highpass_corr.csv")
    exclusive = exclusive_pairs_table(smp, det, settings.markers.exclusive_pairs)
    exclusive.to_csv(out / "stain_qc_exclusive_pairs.csv", index=False)
    update_run_info(out, crosstalk_state="on" if xt.enabled else "off", crosstalk_message=xt.message,
                    unmatched=list(xt.unmatched))
    tell("crosstalk", P_FOLLOWUPS - 0.05, "Crosstalk checked")

    # (3) one follow-up per flagged pair
    followups: list[dict] = []
    if xt.enabled:
        pairs = followup_pairs(xt.table)
        for n, (source, target) in enumerate(pairs, start=1):
            tell("followups", P_FOLLOWUPS - 0.05 + 0.05 * n / (len(pairs) + 1), f"Follow-up panel {n} of {len(pairs)}")
            control = control_for(target, settings.markers.control_markers, smp.names, avoid=(source,))
            numbers = followup_numbers(smp, source, target, control)
            write_followup_preview(smp, source, target, control, numbers, out, adv.n_preview_windows)
            followups.append(numbers)
    _write_json(out / "stain_qc_followups.json", followups)
    tell("followups", P_FOLLOWUPS, "Follow-up panels done")

    # (4) figures, before the report so the report's figure links resolve
    tell("figures", P_FIGURES, "Drawing figures")
    write_figures(smp, det, xt, out, followups=followups, k=adv.n_preview_windows, layout=layout,
                  detection_settings=settings.detection,
                  progress=lambda i, n: tell("figures", P_FIGURES - 0.05 + 0.05 * i / n, f"Drawing figure {i} of {n}"))

    # (5) summary and report
    calls = det["call"].astype(str)
    summary = {
        "calls": {c: int(n) for c, n in calls.value_counts().items()},
        "raise": list(det.loc[calls == "RAISE", "channel"]),
        "lower": list(det.loc[calls == "LOWER", "channel"]),
        "check": list(det.loc[calls == "CHECK", "channel"]),
        "flagged_crosstalk": _flagged_summary(xt),
        "crosstalk_enabled": bool(xt.enabled),
        "crosstalk_message": xt.message,
        "unmatched": list(xt.unmatched),
        "saturation_checked": bool(smp.saturation_checkable),
        "rules": settings_to_dict(settings)["detection"],
    }
    _write_json(out / "stain_qc_summary.json", summary)
    tell("report", P_REPORT, "Writing report")
    meta.setdefault("finished", _now().replace("T", " "))
    write_report(out, det, xt, exclusive, followups, settings, meta, list(meta.get("warnings") or []))
    return summary


# ---------------------------------------------------------------------------- the whole run
def run_qc(req: RunRequest, progress: ProgressFn | None = None) -> tuple[Path, dict]:
    """Image in, finished run folder out. Returns (run folder, summary).

    On an exception the marker and run_info.json record it (status "error", or "cancelled" for RunCancelled
    and KeyboardInterrupt) and the exception is re-raised. The folder stays readable.
    """
    from .crosstalk import NO_LAYOUT_MESSAGE
    from .layout import load_layout
    from .readers import ReaderError, nuclear_channel, open_image
    from .tissue import sample_windows

    top = [0.0]

    def tell(stage: str, fraction: float, message: str) -> None:
        top[0] = max(top[0], float(fraction))    # never go backwards, whatever the stage reports
        if progress is not None:
            progress(stage, top[0], message)

    settings, adv = req.settings, req.settings.advanced
    # Problems the user must fix first (missing image, no nuclear channel, unreadable panel file) stop the run
    # before a results folder exists, so they leave nothing behind.
    try:
        layout = load_layout(req.panel)
    except ValueError as exc:                     # unreadable panel file: say so in one line
        raise ReaderError(str(exc)) from None
    with open_image(req.image) as src:
        nuclear_channel(src, req.nuclear_channel)
    started = datetime.now()
    out = make_run_dir(req.output_root, req.image, started)
    _write_marker(out, f"Run in progress (running). Started {started:%Y-%m-%d %H:%M:%S} on {Path(req.image).name}.")
    update_run_info(out, image_name=Path(req.image).name, image_path=str(req.image),
                    panel=str(req.panel) if req.panel else None, started_at=started.isoformat(timespec="seconds"),
                    status="running", crosstalk_state="pending" if layout is not None else "off",
                    crosstalk_message="" if layout is not None else NO_LAYOUT_MESSAGE, saturation_checked=True,
                    unmatched=[], nuclear_channel=req.nuclear_channel, settings=settings_to_dict(settings),
                    settings_warnings=list(req.settings_warnings), version=__version__,
                    output_folder=str(out))
    try:
        if req.on_started is not None:
            req.on_started(out)
        tell("open", P_OPEN, "Opening image")
        def on_window(fraction: float, message: str) -> None:
            if fraction < 0.05:
                tell("tissue", P_OPEN, message)
            else:
                tell("sampling", P_MASK + (P_SAMPLED - P_MASK) * (fraction - 0.05) / 0.95, message)

        def on_early(partial: Sample, total: int) -> None:
            def early_step(kind: str, done: int, count: int) -> None:
                words = "antibody" if kind == "score" else "preview"
                tell("early_scoring", top[0], f"Early look: {words} {done} of {count} (first {len(partial.pixels)} windows)")

            # Said first: scoring the early windows takes a while, and reading stops meanwhile.
            tell("early_scoring", top[0], f"Early look: scoring the first {len(partial.pixels)} windows")
            _write_antibody_results(partial, settings, out, early_step)
            update_run_info(out, partial_windows=len(partial.pixels), early_of=int(total))
            tell(EARLY_STAGE, top[0], f"Early antibody results from {len(partial.pixels)} of {total} windows")

        with open_image(req.image) as src:
            meta = {"image": Path(req.image).name, "panel": str(req.panel) if req.panel else "none",
                    "pixel_size_um": src.pixel_size_um, "dtype": str(src.dtype),
                    "version": __version__, "started": started.strftime("%Y-%m-%d %H:%M:%S"),
                    "warnings": list(req.settings_warnings)}
            smp = sample_windows(src, adv, req.nuclear_channel, progress=on_window,
                                 early_windows=EARLY_WINDOWS, on_early=on_early)
        meta["n_windows"] = int(len(smp.pixels))
        meta["n_windows_requested"] = int(adv.n_windows)
        meta["window_px"] = int(smp.window_px)
        update_run_info(out, nuclear_channel=smp.nuclear_name, intensity_range=float(smp.value_range),
                        range_basis=smp.range_basis, saturation_checked=bool(smp.saturation_checkable),
                        n_windows=meta["n_windows"], windows_requested=meta["n_windows_requested"],
                        window_px=meta["window_px"])
        if req.save_sample:
            smp.save(out / "sample.npz")
        few = meta["n_windows"] < meta["n_windows_requested"]
        tell("sampling", P_SAMPLED, f"Used all {meta['n_windows']} tissue windows in this image. A full section "
             f"uses up to {meta['n_windows_requested']}." if few else f"Read {meta['n_windows']} windows")

        summary = analyze_sample(smp, layout, settings, out, meta, tell)
    except RunCancelled:
        _finish_with_problem(out, "cancelled", "Run cancelled by the user.")
        raise
    except KeyboardInterrupt:
        _finish_with_problem(out, "cancelled", "Run cancelled (interrupted).")
        raise
    except Exception as exc:
        _finish_with_problem(out, "error", f"Run failed: {exc}", error=str(exc))
        raise
    update_run_info(out, status="complete", finished_at=_now())
    try:
        (out / INCOMPLETE_MARKER).unlink()
    except FileNotFoundError:
        pass
    tell("done", 1.0, "Finished")
    return out, summary


def _finish_with_problem(out: Path, status: str, text: str, error: str | None = None) -> None:
    """Record why the run stopped; never let the bookkeeping hide the original exception."""
    try:
        _write_marker(out, text)
        changes = {"status": status, "finished_at": _now()}
        if error is not None:
            changes["error"] = error
        update_run_info(out, **changes)
    except OSError:
        pass
