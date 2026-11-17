"""Small file helpers with no heavy imports (run.py must stay light for the GUI)."""

from __future__ import annotations

import os
import time


def replace_file(src, dst, tries: int = 5, wait_s: float = 0.05) -> None:
    """os.replace with a short retry: on Windows the swap fails while a reader (the GUI) has the file open."""
    for attempt in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(wait_s)
