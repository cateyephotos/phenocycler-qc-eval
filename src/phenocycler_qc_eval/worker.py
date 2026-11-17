"""Child-process entry point: `python -m phenocycler_qc_eval.worker REQUEST_JSON`.

The GUI never reads images or runs the analysis itself; it starts this process and reads its stdout.
Stdout carries one ASCII JSON object per line and nothing else (anything a library prints goes to stderr).
Exit codes: 0 done, 2 a problem the user can fix, 1 unexpected, 130 cancelled.

Messages (the "type" field):
  run:      started {out_dir} | progress {stage, fraction, message} | partial {out_dir, early} | done {out_dir, summary}
  channels: channels {names}
  download_example: progress {stage: "download", fraction, message} ... then example {image, panel}
  any:      log {message} | error {kind, message, channels?, found?, needed?} | cancelled {out_dir?}
  error kinds: no_nuclear_channel | too_little_tissue | reader | example_unavailable | unexpected

Windows rule: the environment variable below is set before anything that could import matplotlib.
This module never calls matplotlib.use() and never imports the pyplot interface.
"""

from __future__ import annotations

import os

os.environ["MPLBACKEND"] = "Agg"

import json
import sys
import time
import traceback
from pathlib import Path


class _Emitter:
    """Writes JSON lines to the real stdout, whatever sys.stdout is pointed at meanwhile."""

    def __init__(self, stream):
        self.stream = stream

    def __call__(self, **message) -> None:
        self.stream.write(json.dumps(message, ensure_ascii=True, default=_plain) + "\n")
        self.stream.flush()


def _plain(value):
    """JSON fallback for numpy numbers, paths and anything else a summary might hold."""
    for cast in (int, float):
        try:
            if cast(value) == value:
                return cast(value)
        except (TypeError, ValueError, OverflowError):
            pass
    return str(value)


def _sleep_for_tests() -> None:
    """Test hook: PQE_TEST_SLEEP_AFTER_PARTIAL=<seconds> holds the run right after the final antibody results."""
    try:
        seconds = float(os.environ.get("PQE_TEST_SLEEP_AFTER_PARTIAL") or 0)
    except ValueError:
        return
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        time.sleep(0.1)


def _run(request: dict, emit: _Emitter, state: dict) -> int:
    from .run import EARLY_STAGE, PARTIAL_STAGE, RunRequest, run_qc
    from .settings import settings_from_dict

    settings, warnings = settings_from_dict(request.get("settings"))
    panel = request.get("panel")

    def on_started(folder) -> None:
        state["out_dir"] = str(folder)
        emit(type="started", out_dir=str(folder))

    def progress(stage: str, fraction: float, message: str) -> None:
        emit(type="progress", stage=stage, fraction=float(fraction), message=str(message))
        if stage in (EARLY_STAGE, PARTIAL_STAGE):
            emit(type="partial", out_dir=state.get("out_dir"), early=stage == EARLY_STAGE)
        if stage == PARTIAL_STAGE:
            _sleep_for_tests()

    req = RunRequest(Path(request["image"]), Path(panel) if panel else None, Path(request["output_root"]),
                     settings, nuclear_channel=request.get("nuclear_channel"),
                     save_sample=bool(request.get("save_sample")),
                     settings_warnings=list(request.get("settings_warnings") or []) + warnings,
                     on_started=on_started)
    out, summary = run_qc(req, progress)
    emit(type="done", out_dir=str(out), summary=summary)
    return 0


def _channels(request: dict, emit: _Emitter) -> int:
    from .readers import open_image

    with open_image(Path(request["image"])) as src:
        emit(type="channels", names=list(src.channels))
    return 0


def _download_example(request: dict, emit: _Emitter) -> int:
    from . import example

    shown = [-1.0, -1]                        # last fraction sent, last byte count sent

    def progress(done: int, total) -> None:
        fraction = min(1.0, done / total) if total else 0.0
        if total:
            if fraction - shown[0] < 0.01 and done < total:
                return
        elif done - shown[1] < 5_000_000 and shown[1] >= 0:   # size unknown: speak every 5 MB instead
            return
        shown[0], shown[1] = fraction, done
        size = f" of {total / 1e6:.0f} MB" if total else ""
        emit(type="progress", stage="download", fraction=fraction,
             message=f"Downloading the example: {done / 1e6:.0f}{size}" + ("" if total else " MB"))

    try:
        image, panel = example.download_example(progress, source=request.get("source"))
    except Exception as exc:                  # noqa: BLE001 - offline, not published yet, or a damaged file
        code = getattr(exc, "code", None)
        emit(type="error", kind="example_unavailable",
             message=f"HTTP {code}" if code else f"{type(exc).__name__}: {exc}")
        return 2
    emit(type="example", image=str(image), panel=str(panel))
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    emit = _Emitter(sys.stdout)
    real_stdout, sys.stdout = sys.stdout, sys.stderr      # stray prints must not corrupt the JSON stream
    state: dict = {}
    try:
        request = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
        action = request.get("action")
        if action == "run":
            return _run(request, emit, state)
        if action == "channels":
            return _channels(request, emit)
        if action == "download_example":
            return _download_example(request, emit)
        raise ValueError(f"unknown worker action {action!r}")
    except BaseException as exc:                          # noqa: BLE001 - every exit becomes one message
        return _report(exc, emit, state)
    finally:
        sys.stdout = real_stdout


def _report(exc: BaseException, emit: _Emitter, state: dict) -> int:
    from .readers import NoNuclearChannelError, ReaderError
    from .run import RunCancelled
    from .tissue import TooLittleTissueError

    def send(**message) -> None:
        try:
            emit(**message)
        except (OSError, ValueError):                     # the parent has gone: nobody is listening any more
            pass

    if isinstance(exc, (RunCancelled, KeyboardInterrupt)):
        send(type="cancelled", out_dir=state.get("out_dir"))
        return 130
    if isinstance(exc, NoNuclearChannelError):
        send(type="error", kind="no_nuclear_channel", message=str(exc), channels=list(exc.channels))
        return 2
    if isinstance(exc, TooLittleTissueError):
        send(type="error", kind="too_little_tissue", message=str(exc), found=exc.found, needed=exc.needed)
        return 2
    if isinstance(exc, ReaderError):
        send(type="error", kind="reader", message=str(exc))
        return 2
    try:
        traceback.print_exception(exc, file=sys.stderr)
    except OSError:
        pass
    send(type="error", kind="unexpected", message=f"{type(exc).__name__}: {exc}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
