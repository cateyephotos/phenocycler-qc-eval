"""One model of where a run is, read by every part of the window that talks about the wait.

The rail, the working page, the banner and the progress bars all take their words from one RunState, so they cannot
disagree. It is plain Python (no Tk), fed with the worker's progress messages.

Steps (a step never goes back): Finding tissue, Reading windows, Scoring antibodies, Checking crosstalk, and
Drawing figures and writing the report. The early look at the first windows is part of Reading windows: scoring them
happens while the rest are still to be read.
"""

from __future__ import annotations

import re
import time
from typing import Callable

STAGES = (
    ("tissue", "Finding tissue"),
    ("reading", "Reading windows"),
    ("scoring", "Scoring antibodies"),
    ("crosstalk", "Checking crosstalk"),
    ("report", "Drawing figures and writing the report"),
)
_STEP_OF = {"open": 0, "tissue": 0, "sampling": 1, "early_scoring": 1, "antibody_results_early": 1,
            "detection": 2, "antibody_results_ready": 2, "crosstalk": 3, "followups": 3,
            "figures": 4, "report": 4}
_COUNT = re.compile(r"(\d+)\s+of\s+(\d+)")
_UNIT = re.compile(r"([A-Za-z][A-Za-z-]*)\s+\d+\s+of\s+\d+")    # "window 21 of 61", "antibody 3 of 31", "figure 4 of 9"
_UNIT_OF_STEP = {1: "window", 2: "antibody", 3: "panel", 4: "figure"}     # when a message names no unit
FULL_SECTION_WINDOWS = 150          # a read of this many windows (up to 200) or more is a whole section, not a crop
MIN_STEP_SECONDS_FOR_LEFT = 10.0    # a counted step has run this long before it says how long is left
DONE, NOW, TODO = "✓", "●", "○"
MIN_WINDOWS_FOR_ESTIMATE = 5
MIN_SECONDS_FOR_ESTIMATE = 8.0


class RunState:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.reset()

    def reset(self) -> None:
        self.kind = "run"
        self.active = False
        self.finished = False
        self.outcome = ""
        self.stage_index = -1
        self.message = ""
        self.fraction = 0.0
        self.early_of: int | None = None            # the early look rests on this many windows, when it was shown
        self.stage = ""
        self.windows_total = 0                       # the most windows a "window n of m" message has named
        self._count: tuple[int, int] | None = None
        self._step_began: float | None = None        # when the current counted step first reported a count
        self._step_first = 0

    def start(self, kind: str = "run") -> None:
        self.reset()
        self.kind = kind
        self.active = True

    # ---- feeding it -------------------------------------------------------------
    def update(self, stage: str, fraction: float, message: str) -> None:
        self.fraction = float(fraction)
        self.message = str(message or "")
        found = _COUNT.search(self.message)
        before = self._count
        self._count = (int(found.group(1)), int(found.group(2))) if found else None
        if self.kind == "download":
            return
        self.stage = stage
        step = _STEP_OF.get(stage)
        if step is not None and step >= self.stage_index:
            if step != self.stage_index:
                self._step_began = None
            self.stage_index = step
        unit = _UNIT.search(self.message)
        if found and unit and unit.group(1).lower() == "window":
            self.windows_total = max(self.windows_total, int(found.group(2)))
        if self._count is not None and (self._step_began is None or before is None or self._count[1] != before[1]):
            self._step_began, self._step_first = self._clock(), self._count[0]
        if stage == "antibody_results_early" and self._count is not None:
            self.early_of = self._count[0]

    def finish(self, outcome: str = "done") -> None:
        self.active = False
        self.finished = True
        self.outcome = outcome
        if outcome == "done":
            self.stage_index = len(STAGES)

    # ---- reading it -------------------------------------------------------------------
    def count(self) -> tuple[int, int] | None:
        return self._count

    def indeterminate(self) -> bool:
        """True while the step has no count to show: the bar then animates instead of standing still."""
        return self.active and self._count is None

    def _early_scoring(self) -> bool:
        """The early look at the first windows: scoring them happens inside the reading step."""
        return self.stage_index == 1 and self.stage == "early_scoring"

    def unit(self) -> str:
        """What the current count counts ("window", "antibody", "preview", "panel", "figure")."""
        found = _UNIT.search(self.message)
        word = found.group(1).lower() if found else ""
        if word in ("of", "window", "antibody", "preview", "panel", "figure"):
            return word if word != "of" else _UNIT_OF_STEP.get(self.stage_index, "")
        return _UNIT_OF_STEP.get(self.stage_index, word)

    def _step_detail(self, i: int) -> str:
        """The count of the current step, always with what it counts: never a bare "28 of 31" next to a step whose
        other counts are of something else."""
        if i != self.stage_index or self._count is None:
            return ""
        n, m = self._count
        unit = self.unit()
        what = f"{unit} {n} of {m}" if unit else f"{n} of {m}"
        if self.stage_index == 2 and unit in ("antibody", "preview"):
            # The step counts antibodies twice (scored, then their previews made): say which pass this is.
            what = f"{'first' if unit == 'antibody' else 'second'} pass: {what}"
        if self._early_scoring():
            return f" (early look: {what})"
        return f" ({what})"

    def lines(self) -> list[str]:
        if self.kind == "download":
            return [f"{DONE if self.finished else NOW} " + (self.message or "Downloading the example")]
        out = []
        for i, (_key, label) in enumerate(STAGES):
            if self.finished and self.outcome == "done":
                mark = DONE
            elif self.stage_index < 0 or i > self.stage_index:
                mark = TODO
            elif i < self.stage_index:
                mark = DONE
            else:
                mark = NOW
            out.append(f"{mark} {label}{self._step_detail(i) if mark == NOW else ''}")
        return out

    def compact(self) -> str:
        """The whole list as one line, for a window too short to show five: "Step 2 of 5: Reading windows (21 of 61)"."""
        if self.kind == "download":
            return self.lines()[0]
        n = len(STAGES)
        if self.finished and self.outcome == "done":
            return f"All {n} steps done"
        i = max(self.stage_index, 0)
        return f"Step {min(i + 1, n)} of {n}: {STAGES[min(i, n - 1)][1]}{self._step_detail(i)}"

    def status(self) -> str:
        if self.finished:
            return {"done": "Finished.", "cancelled": "Stopped.", "error": "Stopped by an error."}.get(
                self.outcome, "Finished.")
        return self.message or "Starting the run..."

    def banner_phrase(self) -> str:
        """The current step as a clause that follows "Now:" in the banner."""
        if self.stage_index < 0:
            return "starting"
        if self.stage_index >= len(STAGES):
            return "finished"
        label = STAGES[self.stage_index][1]
        label = label[0].lower() + label[1:]
        if self._count is not None and self.stage_index == 1 and not self._early_scoring():
            return f"reading window {self._count[0]} of {self._count[1]}"
        if self._count is not None and self.stage_index in (1, 2, 3, 4):
            return f"{label}{self._step_detail(self.stage_index)}"
        return label

    def _left_seconds(self) -> float | None:
        """Seconds left in the step that is running, from the pace seen so far in that step; None when there is no honest
        estimate (no count, too early, or a step that never reports a count)."""
        if self._count is None or self._step_began is None or self.stage_index < 1 or self.stage_index >= len(STAGES):
            return None
        n, m = self._count
        done = n - self._step_first
        took = self._clock() - self._step_began
        if m <= n:
            return None
        if self.stage_index == 1:
            if self._early_scoring() or self.unit() != "window":
                return None
            if done < MIN_WINDOWS_FOR_ESTIMATE or took < MIN_SECONDS_FOR_ESTIMATE:
                return None
        elif done < 1 or took < MIN_STEP_SECONDS_FOR_LEFT:
            return None
        return (m - n) * took / done

    def eta_text(self) -> str:
        """For the working page: how long the running step has left, from its own pace. Nothing without a count."""
        left = self._left_seconds()
        if left is None:
            return ""
        if self.stage_index == 1:
            return f"Reading the windows should take {self._span(left)} more at this pace. Scoring and crosstalk take longer."
        return f"This step should take {self._span(left)} more at this pace."

    def left_short(self) -> str:
        """The same estimate as one short sentence, for the banner (which stays on screen after the working page goes)."""
        left = self._left_seconds()
        if left is None:
            return ""
        if left < 45:
            return "Under a minute left in this step."
        minutes = max(1, round(left / 60))
        return f"About {minutes} minute{'s' if minutes != 1 else ''} left in this step."

    @staticmethod
    def _span(seconds: float) -> str:
        if seconds < 45:
            return "under a minute"
        minutes = max(1, round(seconds / 60))
        return f"about {minutes} minute{'s' if minutes != 1 else ''}"

    def size_note(self) -> str:
        """How long the whole run takes, once the number of windows shows the image is a whole section."""
        if self.kind == "download" or self.windows_total < FULL_SECTION_WINDOWS:
            return ""
        return "This is a full section: expect about 10 to 15 minutes in all. Antibody results come first."
