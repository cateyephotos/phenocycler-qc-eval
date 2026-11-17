"""WorkerHandle: start the worker process, read its messages without ever blocking, cancel it.

Used by the GUI on the Tk thread. Reading happens on background threads that feed a queue; poll() only
drains that queue. Nothing here imports numpy, matplotlib or any image code.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from .run import INCOMPLETE_MARKER, RunRequest, update_run_info
from .settings import settings_to_dict


def worker_env() -> dict[str, str]:
    """The environment the worker runs in: ours, plus the Windows rules from the lab crash notes."""
    env = dict(os.environ)
    env.update({"MPLBACKEND": "Agg", "PYTHONIOENCODING": "ascii:backslashreplace", "PYTHONUNBUFFERED": "1"})
    return env


def run_request_dict(req: RunRequest) -> dict:
    """The request as plain JSON-able data (on_started stays behind; the worker sets its own)."""
    return {"action": "run", "image": str(req.image), "panel": str(req.panel) if req.panel else None,
            "output_root": str(req.output_root), "settings": settings_to_dict(req.settings),
            "nuclear_channel": req.nuclear_channel, "save_sample": bool(req.save_sample),
            "settings_warnings": list(req.settings_warnings)}


class WorkerHandle:
    def __init__(self, request: dict, python: str = sys.executable, module: str = "phenocycler_qc_eval.worker"):
        self.request = request
        self.python = python
        self.module = module
        self.out_dir: Path | None = None
        self._proc: subprocess.Popen | None = None
        self._queue: queue.Queue = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._request_file: Path | None = None
        self._started_at: float | None = None
        self._ended_at: float | None = None
        self._stderr_tail: deque = deque(maxlen=20)   # the last lines the worker wrote, for a death report
        self._final = False                           # a done / error / cancelled / channels message was read
        self._cancel_requested = False
        self._ending_by_cancel = False                # cancel() has taken the right to queue the ending
        self._readers_left = 0
        self._lock = threading.Lock()
        self._closed = False

    # ------------------------------------------------------------------ start
    def start(self) -> None:
        if self._closed:
            raise RuntimeError("this WorkerHandle was closed")
        if self._proc is not None:
            raise RuntimeError("this WorkerHandle was already started")
        fd, name = tempfile.mkstemp(prefix="pqe_request_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self.request, fh, ensure_ascii=True)
        self._request_file = Path(name)
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self._started_at = time.monotonic()
        self._proc = subprocess.Popen(
            [self.python, "-m", self.module, name], env=worker_env(), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
        self._readers_left = 2
        self._threads = [threading.Thread(target=self._read_stdout, daemon=True),
                         threading.Thread(target=self._read_stderr, daemon=True)]
        for t in self._threads:
            t.start()

    def _read_stdout(self) -> None:
        try:
            for raw in iter(self._proc.stdout.readline, b""):
                line = raw.decode("ascii", "replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    if not isinstance(msg, dict):
                        raise ValueError
                except ValueError:
                    msg = {"type": "log", "message": line}
                if msg.get("out_dir") and msg.get("type") in ("started", "partial", "done", "cancelled"):
                    self.out_dir = Path(msg["out_dir"])
                if msg.get("type") in ("done", "error", "cancelled", "channels"):
                    with self._lock:
                        if self._ending_by_cancel:    # cancel() already queued (or is about to queue) the ending
                            continue
                        self._final = True            # set before the put, so cancel() never queues a second ending
                self._queue.put(msg)
        except (OSError, ValueError):                 # the pipe was closed under us (close())
            pass
        finally:
            self._reader_done()

    def _read_stderr(self) -> None:
        try:
            for raw in iter(self._proc.stderr.readline, b""):
                line = raw.decode("ascii", "replace").rstrip()
                if line:
                    self._stderr_tail.append(line)
                    self._queue.put({"type": "log", "message": line})
        except (OSError, ValueError):
            pass
        finally:
            self._reader_done()

    def _reader_done(self) -> None:
        """The last reader to finish sees the process out: reports a silent death, then closes the pipes."""
        with self._lock:
            self._readers_left -= 1
            if self._readers_left > 0:
                return
        proc = self._proc
        try:
            proc.wait()
        except OSError:
            pass
        self._note_end()
        with self._lock:                                  # test and claim in one hold: a cancel cannot slip between
            report = not self._cancel_requested and not self._final
            if report:
                self._final = True
        if report:
            tail = "\n".join(self._stderr_tail).strip()
            self._queue.put({"type": "error", "kind": "unexpected",
                             "message": f"Worker stopped unexpectedly (exit code {proc.returncode})."
                                        + (f" {tail}" if tail else "")})
        self._close_pipes()

    def _claim_final(self, by_cancel: bool = False) -> bool:
        """Take the one right to queue the run's ending. Only one caller (a message the worker sent, the death
        report, or cancel) ever gets True, so a death at the instant of a cancel cannot queue two endings."""
        with self._lock:
            if self._final:
                return False
            self._final = True
            self._ending_by_cancel = by_cancel
            return True

    def _note_end(self) -> None:
        if self._ended_at is None:
            self._ended_at = time.monotonic()
        self._remove_request_file()

    def _close_pipes_bounded(self, wait_s: float = 1.0) -> None:
        """Close both pipes without ever hanging: closing a pipe that a reader is still blocked on (a grandchild
        keeps it open) can wait for that reader, so it runs on a daemon thread that is given wait_s seconds."""
        t = threading.Thread(target=self._close_pipes, daemon=True)
        t.start()
        t.join(wait_s)

    def _close_pipes(self) -> None:
        for pipe in (getattr(self._proc, "stdout", None), getattr(self._proc, "stderr", None)):
            try:
                if pipe is not None:
                    pipe.close()
            except OSError:
                pass

    # ------------------------------------------------------------------ state
    def poll(self) -> list[dict]:
        """Every message that arrived since the last call. Never blocks."""
        msgs = []
        while True:
            try:
                msgs.append(self._queue.get_nowait())
            except queue.Empty:
                return msgs

    def is_running(self) -> bool:
        """True until the process has ended AND every message it sent has been put on the queue."""
        if self._proc is None:
            return False
        return self._proc.poll() is None or any(t.is_alive() for t in self._threads)

    @property
    def returncode(self) -> int | None:
        return None if self._proc is None else self._proc.poll()

    def elapsed_s(self) -> float:
        """Seconds since start; stops counting when the process ends."""
        if self._started_at is None:
            return 0.0
        if self._ended_at is None and self._proc is not None and self._proc.poll() is not None:
            self._ended_at = time.monotonic()
        return (self._ended_at or time.monotonic()) - self._started_at

    def _remove_request_file(self) -> None:
        if self._request_file is not None:
            try:
                self._request_file.unlink()
            except OSError:
                pass
            self._request_file = None

    # ------------------------------------------------------------------ cancel and close
    def cancel(self) -> None:
        """Stop the worker. The run folder keeps what was written and is marked cancelled (and incomplete).

        Can block for a few seconds (up to 5 s for the process to stop, then up to 2 s for the reader threads), so
        call it from a button click or a window close, not from a timer. Does nothing once the worker has
        ended, and queues no "cancelled" when the worker had already reported how it finished."""
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        with self._lock:
            self._cancel_requested = True
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        deadline = time.monotonic() + 2.0             # one bound for all readers, shared with close()
        for t in self._threads:
            t.join(timeout=max(0.0, deadline - time.monotonic()))
        self._note_end()
        if not self._claim_final(by_cancel=True):                   # it finished (or said how it stopped) before we got to it
            return
        self._mark_cancelled()
        self._queue.put({"type": "cancelled", "out_dir": str(self.out_dir) if self.out_dir else None})

    def close(self) -> None:
        """Release everything: stop a running worker, join the readers, close both pipes. Safe to repeat."""
        if self._closed:
            return
        self._closed = True
        if self._proc is None:
            return
        self.cancel()
        deadline = time.monotonic() + 2.0             # one bound for all readers, not 2 s each
        for t in self._threads:
            t.join(timeout=max(0.0, deadline - time.monotonic()))
        self._close_pipes_bounded()
        self._note_end()

    def _mark_cancelled(self) -> None:
        folder = self.out_dir
        if folder is None or not (folder / INCOMPLETE_MARKER).exists():
            return                                    # no run folder yet, or the run had already finished
        try:
            info = json.loads((folder / "run_info.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info = None
        if isinstance(info, dict) and info.get("status") == "complete":
            return                                    # run.py wrote "complete": never relabel a finished run
        try:
            when = datetime.now()
            (folder / INCOMPLETE_MARKER).write_text(f"cancelled by user at {when:%Y-%m-%d %H:%M:%S}\n",
                                                    encoding="utf-8")
            update_run_info(folder, status="cancelled", finished_at=when.isoformat(timespec="seconds"))
        except OSError:
            pass
