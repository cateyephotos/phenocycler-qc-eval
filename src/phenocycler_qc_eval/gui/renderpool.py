"""One background thread that builds and draws evidence pictures, so the Tk thread never does.

The thread touches no Tk object: a job is a plain function that returns a `Scene` (matplotlib Figure + Agg, never
pyplot), and the finished picture goes back through a queue the window looks at on its own timer. Jobs are taken in
priority order: the picture a person just asked for first, then the pictures they are likely to ask for next.
"""

from __future__ import annotations

import gc
import heapq
import itertools
import queue
import sys
import threading
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Job:
    ident: tuple                       # (key, width, height, scale): one picture at one size
    make: Callable                     # () -> Scene (may raise)
    sink: "queue.SimpleQueue"          # where the Result goes (the window that asked polls it)
    priority: int = 5                  # lower runs first
    stale: Callable[[], bool] | None = None      # asked between the steps: True drops the job
    size: tuple = (0, 0, 1.0)
    state: str = "queued"              # queued, running, done, dropped
    seq: int = 0
    extra: dict = field(default_factory=dict)


@dataclass
class Result:
    job: Job
    scene: object | None
    png: bytes | None
    width: int
    height: int
    error: str | None = None


def _lower_thread_priority() -> None:
    """Prefetch work must never slow the analysis that is running beside the window: ask Windows for a lower priority."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel = ctypes.windll.kernel32
        kernel.SetThreadPriority(kernel.GetCurrentThread(), -1)      # THREAD_PRIORITY_BELOW_NORMAL
    except Exception:  # noqa: BLE001 - a nicety, never a reason to fail
        pass


class RenderPool:
    """The shared worker. Started on first use; a daemon, so it never keeps the program alive."""

    def __init__(self) -> None:
        self._cv = threading.Condition()
        self._heap: list[tuple[int, int, Job]] = []
        self._jobs: dict[tuple, Job] = {}        # queued or running, by ident
        self._counter = itertools.count()
        self._thread: threading.Thread | None = None
        self.running: Job | None = None
        self.threads_seen: set[int] = set()      # thread ids that drew a picture (a test asserts it is never the Tk thread)

    # ---- the Tk side --------------------------------------------------------------------------------------------
    def submit(self, job: Job) -> Job:
        """Queue a job, or return the one already queued (or running) for the same picture, moved up if this one is more
        urgent."""
        with self._cv:
            have = self._jobs.get(job.ident)
            if have is not None and have.state in ("queued", "running"):
                if job.priority < have.priority and have.state == "queued":
                    have.priority = job.priority
                    have.seq = next(self._counter)
                    heapq.heappush(self._heap, (have.priority, have.seq, have))
                return have
            job.seq = next(self._counter)
            self._jobs[job.ident] = job
            heapq.heappush(self._heap, (job.priority, job.seq, job))
            self._ensure_thread()
            self._cv.notify()
            return job

    def drop_queued(self, predicate) -> int:
        """Forget the jobs not started yet that `predicate(job)` picks. A running job finishes."""
        n = 0
        with self._cv:
            for job in list(self._jobs.values()):
                if job.state == "queued" and predicate(job):
                    job.state = "dropped"
                    self._jobs.pop(job.ident, None)
                    n += 1
        return n

    def outstanding_for(self, sink) -> bool:
        """Jobs for this sink are queued or running."""
        with self._cv:
            return any(j.sink is sink and j.state in ("queued", "running") for j in self._jobs.values())

    def queued(self) -> int:
        with self._cv:
            return sum(1 for j in self._jobs.values() if j.state in ("queued", "running"))

    def idle(self) -> bool:
        return self.queued() == 0

    # ---- the thread ----------------------------------------------------------------------------------------------
    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name="pqe-render", daemon=True)
            self._thread.start()

    def _next(self) -> Job:
        with self._cv:
            while True:
                while self._heap:
                    _p, _s, job = heapq.heappop(self._heap)
                    if job.state == "queued":
                        job.state = "running"
                        self.running = job
                        return job
                self._cv.wait()

    def _loop(self) -> None:
        _lower_thread_priority()
        # Python's cycle collector runs in whichever thread allocates when it fires, and an object that holds a Tk handle
        # (a PhotoImage or a variable of a window that was closed) finalized here would call Tcl from this thread (lab
        # crash compendium TK-002). Collection is switched off while this thread exists; the window's own thread runs it
        # (EvidenceCanvas._take_results), so nothing is leaked and Tcl is only ever called from the Tk thread.
        gc.disable()
        while True:
            job = self._next()
            self.threads_seen.add(threading.get_ident())
            result = self._run(job)
            job.sink.put(result)                 # before the job is forgotten: a window that looks sees one or the other
            with self._cv:
                job.state = "done"
                self._jobs.pop(job.ident, None)
                self.running = None

    @staticmethod
    def _run(job: Job) -> Result:
        try:
            if job.stale is not None and job.stale():
                return Result(job, None, None, 0, 0, error="stale")
            scene = job.make()
            if job.stale is not None and job.stale():
                return Result(job, scene, None, 0, 0, error="stale")
            width, height, scale = job.size
            if getattr(scene, "message", None) is not None:
                return Result(job, scene, None, 0, 0)
            png, w, h = scene.render_png(width, height, scale)
            return Result(job, scene, png, w, h)
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the thread
            return Result(job, None, None, 0, 0, error=f"{type(exc).__name__}: {exc}")


POOL = RenderPool()
