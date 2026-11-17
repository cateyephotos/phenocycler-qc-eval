"""The example image: where it lives, how to get it, and how to check it arrived whole.

The example is a 1 mm piece of a 32-channel tonsil scan, published as a release file (too big to keep in the
code repository). It is stored in a per-user folder, `example_dir()`, next to the saved settings. A copy placed
there by hand works exactly like a downloaded one, as long as its checksum matches.

Nothing here imports the image or analysis code, so the GUI can import it freely. The download itself always
runs off the Tk thread (in the worker process, or from the command line).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import urllib.request
from importlib import resources
from pathlib import Path
from typing import Callable

from .fsutil import replace_file
from .settings import config_path

EXAMPLE_FILE = "tonsil31_crop.ome.tif"
EXAMPLE_URL = ("https://github.com/cateyephotos/phenocycler-qc-eval/releases/download/"
               "example-data-v1/tonsil31_crop.ome.tif")
EXAMPLE_SHA256 = "d2bbf5898841947f5b101763567f7a0c8811a8665ef773416cae9f1e2feab97f"   # also in examples/example_data.json
EXAMPLE_SIZE_MB = 83               # about how big the download is; shown to the user before it starts
PANEL_FILE = "31MarkerpanelB.xpd"

# download progress callback: (bytes done, total bytes or None, in megabytes would be done by the caller)
ProgressFn = Callable[[int, "int | None"], None]
_CHUNK = 1 << 20


def example_dir() -> Path:
    """Where the example image is kept: <folder of the saved settings>/example."""
    return config_path().parent / "example"


def example_image_path() -> Path:
    return example_dir() / EXAMPLE_FILE


def example_panel() -> Path:
    """The panel file that goes with the example (package data)."""
    return Path(str(resources.files("phenocycler_qc_eval").joinpath("data", PANEL_FILE)))


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def manual_instructions() -> str:
    """What to do when the download is not possible: where to get the file and where to put it."""
    return (f"Download {EXAMPLE_FILE} from {EXAMPLE_URL} and place it in {example_dir()}, "
            f"then press Try the example again.")


def _open_source(source: str):
    """(stream, total bytes or None) for an http(s) link, a file:// link or a plain file path."""
    text = str(source)
    if text.lower().startswith(("http://", "https://")):
        resp = urllib.request.urlopen(text, timeout=30)          # noqa: S310 - the link is ours or the user's
        length = resp.headers.get("Content-Length")
        return resp, int(length) if length and length.isdigit() else None
    if text.lower().startswith("file:"):
        text = urllib.request.url2pathname(urllib.request.urlparse(text).path)
    path = Path(text)
    return open(path, "rb"), path.stat().st_size


def download(url: str, dest, sha256: str, progress: ProgressFn | None = None) -> Path:
    """Copy `url` (http(s) link, file:// link or local path) to `dest` and check its sha256.

    Written to a temporary file first and moved into place only when the checksum matches. On a mismatch the
    file is removed and ValueError is raised; any other problem (no connection, 404) raises OSError.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    done = 0
    try:
        part.unlink(missing_ok=True)         # a killed earlier download may have left its half file
    except OSError:
        pass
    try:
        stream, total = _open_source(url)
        with stream, open(part, "wb") as out:
            for block in iter(lambda: stream.read(_CHUNK), b""):
                out.write(block)
                digest.update(block)
                done += len(block)
                if progress is not None:
                    progress(done, total)
        if digest.hexdigest().lower() != str(sha256).lower():
            raise ValueError(f"sha256 does not match: expected {sha256}, got {digest.hexdigest()}. "
                             "The file was removed; try the download again.")
        replace_file(part, dest)             # retries a busy file (Windows virus scanner, indexer)
    except BaseException as exc:
        # A verified copy that could not be moved (PermissionError) stays as .part; anything else leaves nothing.
        keep_part = isinstance(exc, PermissionError) and part.exists() and digest.hexdigest().lower() == str(sha256).lower()
        for leftover in (part, dest) if isinstance(exc, ValueError) else (() if keep_part else (part,)):
            try:
                leftover.unlink()            # no half file, and (after a bad checksum) no wrong file, is left
            except OSError:
                pass
        raise
    return dest


def have_example() -> bool:
    """The example image is in example_dir() and its checksum is right."""
    path = example_image_path()
    try:
        return path.is_file() and sha256_file(path) == EXAMPLE_SHA256
    except OSError:
        return False


def download_example(progress: ProgressFn | None = None, source: str | None = None) -> tuple[Path, Path]:
    """Make sure the example image is in example_dir(); returns (image, panel file).

    The download is skipped when the file is already there with the right checksum. `source` (a link or a
    local file) replaces the release link; the environment variable PQE_EXAMPLE_SOURCE does the same, so the
    example can be used before the release is published. The checksum is always checked.
    """
    image = example_image_path()
    if not have_example():
        where = source or os.environ.get("PQE_EXAMPLE_SOURCE") or EXAMPLE_URL
        download(where, image, EXAMPLE_SHA256, progress)
    return image, example_panel()


def open_folder(folder: Path) -> None:
    """Show a folder in the system file browser (created first when missing)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(str(folder))                                        # noqa: S606
    else:
        import subprocess
        subprocess.Popen(["open" if shutil.which("open") else "xdg-open", str(folder)])
