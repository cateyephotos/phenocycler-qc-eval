"""Screenshots and a timed click-through of the main window, for the GUI review gates (Task 19, pass 2).

States, on a finished run of the example crop:

    python scripts/capture_gui_states.py --run <run folder> --out docs/screens/pass1
    python scripts/capture_gui_states.py --run <run folder> --out docs/screens/pass1/scaling --scaling

Click-through (a scripted first use: Try the example, wait, pick the first row to change, open Crosstalk, pick
TOX -> CD163), with a frame every 0.5 s and timings.json:

    python scripts/capture_gui_states.py --clickthrough --out docs/screens/pass1/clickthrough
    python scripts/capture_gui_states.py --clickthrough --image <image> --panel <panel> --out <folder>

Capture tries PIL.ImageGrab on the window's screen box first; if that fails (a locked desktop refuses screen
grabs) it falls back to a Win32 PrintWindow of the window. Tooltips are separate windows, so with PrintWindow
they are drawn onto the picture from their own PrintWindow.

Display scaling is SIMULATED (`tk scaling` set before any widget is made, and a constrained window size). It is
not a real DPI change.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path

import _tkinter

from PIL import Image, ImageGrab

REPO = Path(__file__).resolve().parents[1]
EXAMPLE_CROP = REPO / "examples" / "tonsil31_crop.ome.tif"
METHOD_USED: list[str] = []
_GRAB_WORKS = [True]          # flips to False after ImageGrab fails once, so frames do not retry it every time
STATE_SIZE = "1280x820+40+40"
TAIL_SECONDS = 5.5          # the click-through keeps recording this long after the last click
SMALL_SCREEN = "1366x690+0+0"  # a maximized window on a 1366 x 768 screen: 768 less the taskbar and the title bar


# ---------------------------------------------------------------------------- capture
def _burst(app, seconds: float) -> None:
    """Handle events one at a time for `seconds`. app.update() can livelock while a progress bar animates: each pass finds
    the next animation timer already due, so it never returns on a busy machine."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while time.monotonic() < end and app.tk.dooneevent(_tkinter.DONT_WAIT):
            pass
        try:
            app.update_idletasks()          # geometry and repaint jobs (they do not livelock; update() does)
        except tk.TclError:
            pass
        time.sleep(0.01)


def _settle(app, wait_ms: int = 600) -> None:
    """Let Tk and the matplotlib canvases finish drawing."""
    _burst(app, wait_ms / 1000.0 + 0.4)


def _window_box(win) -> tuple[int, int, int, int]:
    x, y = win.winfo_rootx(), win.winfo_rooty()
    return x, y, x + win.winfo_width(), y + win.winfo_height()


def _printwindow(win) -> Image.Image:
    from ctypes import wintypes

    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    H, P = wintypes.HANDLE, ctypes.c_void_p
    user32.GetParent.argtypes, user32.GetParent.restype = [wintypes.HWND], wintypes.HWND
    user32.GetClientRect.argtypes, user32.GetClientRect.restype = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL
    user32.GetDC.argtypes, user32.GetDC.restype = [wintypes.HWND], wintypes.HDC
    user32.ReleaseDC.argtypes, user32.ReleaseDC.restype = [wintypes.HWND, wintypes.HDC], ctypes.c_int
    user32.PrintWindow.argtypes, user32.PrintWindow.restype = [wintypes.HWND, wintypes.HDC, wintypes.UINT], wintypes.BOOL
    gdi32.CreateCompatibleDC.argtypes, gdi32.CreateCompatibleDC.restype = [wintypes.HDC], wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes, gdi32.CreateCompatibleBitmap.restype = [wintypes.HDC, ctypes.c_int, ctypes.c_int], H
    gdi32.SelectObject.argtypes, gdi32.SelectObject.restype = [wintypes.HDC, H], H
    gdi32.GetDIBits.argtypes = [wintypes.HDC, H, wintypes.UINT, wintypes.UINT, P, P, wintypes.UINT]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes, gdi32.DeleteObject.restype = [H], wintypes.BOOL
    gdi32.DeleteDC.argtypes, gdi32.DeleteDC.restype = [wintypes.HDC], wintypes.BOOL
    hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    hdc = user32.GetDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    ok = user32.PrintWindow(hwnd, mem, 3)  # PW_CLIENTONLY | PW_RENDERFULLCONTENT

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                    ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    bi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    if not ok:
        raise OSError("PrintWindow returned 0")
    return Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")


def is_blank(img: Image.Image) -> bool:
    import numpy as np

    return float(np.asarray(img.convert("L"), dtype=float).std()) < 3.0


_is_blank = is_blank


class CaptureError(RuntimeError):
    """A picture that cannot be trusted (a locked desktop, the wrong size, an empty window). Never saved."""


def desktop_locked() -> bool:
    """True when the input desktop is not the user's (the lock screen or a secure prompt is up). A screen grab then
    returns the lock screen, which is not blank, so it must be recognized here, not by looking at the pixels alone."""
    try:
        user32 = ctypes.windll.user32
        user32.OpenInputDesktop.restype = ctypes.c_void_p
        user32.OpenInputDesktop.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        handle = user32.OpenInputDesktop(0, False, 0x0100)          # DESKTOP_SWITCHDESKTOP
        if not handle:
            return True
        user32.CloseDesktop.argtypes = [ctypes.c_void_p]
        user32.CloseDesktop(handle)
        return False
    except Exception:  # noqa: BLE001 - not Windows: nothing to detect
        return False


def looks_like_lock_screen(img: Image.Image) -> bool:
    """The Windows lock screen is mostly one dark navy. A real window of this tool is mostly white and light gray."""
    import numpy as np

    small = np.asarray(img.convert("RGB").resize((64, 40)), dtype=float)
    navy = np.array([0.0, 32.0, 92.0])
    near = (np.abs(small - navy).sum(axis=2) < 70).mean()
    return bool(near > 0.5)


def check_picture(img: Image.Image, win, what: str = "") -> Image.Image:
    """The picture is of `win` (its size, not blank, not the lock screen) or this fails loudly."""
    expected = (win.winfo_width(), win.winfo_height())
    if looks_like_lock_screen(img):
        raise CaptureError(f"{what}: the picture is the lock screen, not the window. Unlock the desktop and run again.")
    if abs(img.width - expected[0]) > 3 or abs(img.height - expected[1]) > 3:
        raise CaptureError(f"{what}: the picture is {img.size} but the window is {expected}.")
    return img


def grab(win, overlays=()) -> Image.Image:
    """A picture of `win` (a Tk or Toplevel). `overlays` are separate windows (tooltips) that belong on top.

    A locked desktop never gets a screen grab (that would save the lock screen): the window is drawn with PrintWindow,
    and the picture is checked against the window's size. A picture that fails the check raises CaptureError."""
    img = None
    locked = desktop_locked()
    if locked and "locked desktop" not in METHOD_USED:
        METHOD_USED.append("locked desktop")
    if _GRAB_WORKS[0] and not locked:
        try:
            img = ImageGrab.grab(bbox=_window_box(win))
            if is_blank(img) or looks_like_lock_screen(img):
                img = None
            else:
                METHOD_USED.append("ImageGrab")
        except Exception:  # OSError "screen grab failed" and friends
            img = None
            _GRAB_WORKS[0] = False
    if img is None:
        img = _printwindow(win)
        METHOD_USED.append("PrintWindow")
        for over in overlays:           # PrintWindow of one window never contains another window
            try:
                piece = _printwindow(over)
                img.paste(piece, (over.winfo_rootx() - win.winfo_rootx(), over.winfo_rooty() - win.winfo_rooty()))
            except Exception:  # noqa: BLE001
                pass
    return check_picture(img, win, win.winfo_class())


def _right_side_blank(img: Image.Image, win) -> bool:
    """True when the main window's results side is an empty white page (not drawn yet); other windows are never "blank"."""
    import numpy as np

    if win.winfo_class() != "Tk" or img.width < 900:
        return False
    right = np.asarray(img.convert("L").crop((int(img.width * 0.35), 0, img.width, img.height)), dtype=float)
    if float(right.std()) >= 3.0:
        return False
    # An empty-state or working page has text; a fully white right side is only right when nothing is on screen at all.
    return getattr(win, "_empty_visible", False) is False and not getattr(win, "_working_visible", False)


def wait_for_pictures(win, timeout: float = 40.0) -> None:
    """Until the results (when there are any) are on screen and every picture in view is drawn at its size: two calm looks
    in a row. A picture taken earlier would show a half-built page, which is not what a person sees."""
    if win.winfo_class() != "Tk" or not hasattr(win, "reveal_state"):
        return
    end, calm = time.monotonic() + timeout, 0
    while time.monotonic() < end:
        _burst(win, 0.25)
        try:
            ok = win.reveal_state() != "building" and win.tabs_warm()
            if ok and win.results_visible():
                tab = win.nametowidget(win.notebook.select())
                ev = getattr(tab, "evidence", None)
                ok = ev is None or ev.is_settled()
        except tk.TclError:
            return
        calm = calm + 1 if ok else 0
        if calm >= 3:
            return


def capture(win, name: str, out: Path, overlays=(), wait: bool = True) -> Path:
    """Save a window-only screenshot of `win` as out/<name>.png and return the path. With `wait=False` the window is
    photographed as it is this moment (the loading state, which is gone after a second or two)."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    if not wait:
        win.update_idletasks()
        path = out / f"{name}.png"
        grab(win).save(path, optimize=True)
        return path
    wait_for_pictures(win)
    _settle(win)
    win.lift()
    _burst(win, 0.2)
    state = getattr(win, "run_state", None)
    if state is not None and not state.active:       # nothing animates: a full update repaints every widget
        try:
            win.update()
            if win.winfo_class() == "Tk":
                # PrintWindow can hand back pixels a canvas never repainted after a resize (a stale label in the rail):
                # hiding and showing the window invalidates every widget, so all of it is drawn afresh.
                win.withdraw()
                win.update()
                win.deiconify()
                win.update()
                _burst(win, 0.3)
        except tk.TclError:
            pass
    path = out / f"{name}.png"
    img = grab(win, overlays)
    for _ in range(8):                       # a slow machine can hand over a window whose right side is not drawn yet
        if not _right_side_blank(img, win):
            break
        _burst(win, 2.0)
        img = grab(win, overlays)
    img.save(path, optimize=True)
    return path


# ---------------------------------------------------------------------------- app helpers
DEFAULT_SCALE = [1.0]    # the look used when a screen does not ask for another one: 100% display scaling


def new_app(simulate_scale: float | None = None, attempts: int = 3):
    from phenocycler_qc_eval.gui.app import App

    scale = DEFAULT_SCALE[0] if simulate_scale is None else simulate_scale
    last = None
    for _ in range(attempts):
        try:
            return App(simulate_scale=scale)
        except tk.TclError as exc:
            last = exc
            time.sleep(1)
    raise SystemExit(f"no display for Tkinter: {last}")


def _new_app(attempts: int = 3):          # name kept from the early script
    return new_app(None, attempts)


def select(tree, iid: str) -> None:
    tree.selection_set(iid)
    tree.focus(iid)
    tree.see(iid)
    tree.event_generate("<<TreeviewSelect>>")


_select = select


def tree_row(tree, first_value: str, second_value: str | None = None) -> str:
    for iid in tree.get_children():
        values = tree.item(iid, "values")
        if values[0] == first_value and (second_value is None or values[1] == second_value):
            return iid
    raise LookupError(f"no row {first_value!r} {second_value or ''}".strip())


class FakeHandle:
    """Stands in for the worker handle so a mid-run screen can be drawn from saved results."""

    def __init__(self, out_dir: Path | None = None, elapsed: float = 0.0) -> None:
        self._pending: list[dict] = []
        self.out_dir, self.elapsed, self.running = out_dir, elapsed, True
        self.returncode = None

    def queue(self, messages) -> None:
        self._pending.extend(messages)

    def start(self) -> None:
        pass

    def poll(self) -> list[dict]:
        out, self._pending = self._pending, []
        return out

    def is_running(self) -> bool:
        return self.running

    def elapsed_s(self) -> float:
        return self.elapsed

    def close(self) -> None:
        if self.running:
            self.cancel()

    def cancel(self) -> None:
        self.running = False
        self._pending.append({"type": "cancelled", "out_dir": str(self.out_dir) if self.out_dir else None})


def _fresh(folder: Path) -> Path:
    """An empty folder, replacing whatever an earlier capture left there."""
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    return folder


def _copy_run(run: Path, dest: Path, keep) -> Path:
    """A copy of a finished run holding only the files `keep(relative path)` allows."""
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(run, dest, ignore=lambda d, names: [n for n in names if not keep((Path(d) / n).relative_to(run))
                                                         and (Path(d) / n).is_file()])
    return dest


def _partial_copy(run: Path, dest: Path, status: str, note: str, with_antibodies: bool = True,
                  early: tuple[int, int] | None = None) -> Path:
    """The folder a run leaves while it is still going (or after a cancel): antibody results and previews only.
    `early` = (windows used, windows in all) marks the results as the early look at the first windows."""
    _fresh(dest)
    info = json.loads((run / "run_info.json").read_text(encoding="utf-8")) if (run / "run_info.json").exists() else {}
    info.update({"status": status, "crosstalk_state": "pending"})
    info.pop("finished", None)
    if early:
        info.update({"partial_windows": early[0], "early_of": early[1]})
    (dest / "run_info.json").write_text(json.dumps(info), encoding="utf-8")
    (dest / "RUN_INCOMPLETE.txt").write_text(note, encoding="utf-8")
    if with_antibodies:
        shutil.copy2(run / "stain_qc_channels.csv", dest / "stain_qc_channels.csv")
        prev = dest / "previews"
        prev.mkdir()
        for f in (run / "previews").glob("antibody_*.npz"):
            shutil.copy2(f, prev / f.name)
    return dest


def _no_panel_copy(run: Path, dest: Path) -> Path:
    _copy_run(run, dest, lambda rel: not rel.name.startswith(("stain_qc_crosstalk", "stain_qc_exclusive", "stain_qc_spill",
                                                              "stain_qc_highpass", "stain_qc_followups", "followup_"))
              and "followup" not in rel.name)
    info = json.loads((dest / "run_info.json").read_text(encoding="utf-8"))
    info.update({"crosstalk_state": "off", "crosstalk_message": "No panel file, so crosstalk was not checked.",
                 "panel": ""})
    (dest / "run_info.json").write_text(json.dumps(info), encoding="utf-8")
    return dest


def _prepare_env(scratch: Path) -> None:
    os.environ["PQE_SETTINGS_DIR"] = str(scratch / "_settings")
    os.environ["PQE_CONFIG_DIR"] = str(scratch / "_config")
    os.environ.setdefault("PQE_EXAMPLE_SOURCE", str(EXAMPLE_CROP))
    sys.path[:0] = [str(REPO / "src")]


def _close(app) -> None:
    """Close a window that may still have a stand-in worker and a pending poll, without stray Tk errors."""
    try:
        if isinstance(getattr(app, "handle", None), FakeHandle):      # a real worker is stopped by destroy()
            job = getattr(app, "_poll_job", None)
            if job:
                app.after_cancel(job)
                app._poll_job = None
            app.handle = None
    except tk.TclError:
        pass
    try:
        app.destroy()
    except tk.TclError:
        pass


def _pump(app, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        _burst(app, 0.05)


# ---------------------------------------------------------------------------- the states
def tested_overlap_iid(ct, pick_max: bool = True) -> str:
    """The row of the overlap table with the strongest ratio among the pairs that were tested (no near-blank channel)."""
    rows = [i for i in ct.exclusive_tree.get_children() if not ct._ex_blank.get(i)]
    lifts = {i: float(ct.results.exclusive_pairs.iloc[int(i)]["lift"]) for i in rows}
    return max(rows, key=lambda i: lifts[i]) if pick_max else rows[0]


def not_tested_overlap_iid(ct) -> str | None:
    return next((i for i in ct.exclusive_tree.get_children() if ct._ex_blank.get(i)), None)


def _first_change_iid(tab) -> str:
    det = tab.results.detection
    for iid in tab.tree.get_children():
        if str(det.iloc[int(iid)]["call"]).upper() in ("RAISE", "LOWER"):
            return iid
    return tab.tree.get_children()[0]


def _example_image_note(app, run: Path) -> None:
    """Fill the Image and Panel boxes the way the example does, for screens that show an input."""
    from phenocycler_qc_eval import example

    app.image_var.set(str(EXAMPLE_CROP))                     # an image that exists, so Run is on (as in a real session)
    app.panel_var.set(str(example.example_panel()))
    app._set_output(example.example_dir() / "qc_results")


def _state_running(app, run: Path, scratch: Path, name: str):
    """Mid-run, no antibody results yet: progress and the elapsed time."""
    folder = _fresh(scratch / name)
    (folder / "RUN_INCOMPLETE.txt").write_text("Run in progress (running).\n")
    (folder / "run_info.json").write_text(json.dumps({
        "status": "running", "crosstalk_state": "pending", "image_name": "tonsil31_crop.ome.tif",
        "started_at": "2026-11-13T09:00:00"}))
    _example_image_note(app, run)
    h = FakeHandle(folder, elapsed=47.0)
    app.handle = h
    app.begin_wait("run")
    h.queue([{"type": "started", "out_dir": str(folder)},
             {"type": "progress", "stage": "tissue", "fraction": 0.02, "message": "Finding tissue"},
             {"type": "progress", "stage": "sampling", "fraction": 0.35, "message": "Reading window 21 of 61"}])
    app._busy(True)
    app._poll()
    return h


def capture_states(run: Path, out: Path, scratch: Path, nopanel_run: Path | None = None) -> list[Path]:
    from phenocycler_qc_eval.gui.dialogs import NuclearChannelDialog
    from phenocycler_qc_eval.gui.tooltip import Tooltip
    from phenocycler_qc_eval.results import load_results
    from phenocycler_qc_eval.settings import default_settings, config_path, save_settings

    paths: list[Path] = []
    out.mkdir(parents=True, exist_ok=True)

    def shot(app, name, overlays=()):
        p = capture(app, name, out, overlays)
        paths.append(p)
        print("saved", p.name, flush=True)

    def fresh(scale=None, size=STATE_SIZE):
        app = new_app(scale)
        app.geometry(size)
        app.update()
        return app

    # 01 empty ------------------------------------------------------------------------------------------
    app = fresh()
    shot(app, "01_empty")

    # 01b an image is chosen and nothing has run yet: the page points at Run ---------------------------------------
    from phenocycler_qc_eval import example as _example

    app.set_image(EXAMPLE_CROP)                              # the repo's copy of the example: it exists on this computer
    app.panel_var.set(str(_example.example_panel()))
    app._announce_panel()                                    # the status line says so, as choosing the file does
    shot(app, "01b_image_chosen")
    app.image_var.set("")
    app.panel_var.set("")

    # 02 running (progress, elapsed time) --------------------------------------------------------------------
    _state_running(app, run, scratch, "run_02")
    shot(app, "02_running")
    app.handle.running = False
    _close(app)

    # 03 running, antibody results already in (Titration filled while crosstalk runs) ------------------------------
    app = fresh()
    folder = _partial_copy(run, scratch / "run_03", "running", "Run in progress (running).\n", early=(50, 61))
    _example_image_note(app, run)
    h = FakeHandle(folder, elapsed=99.0)
    app.handle = h
    app.begin_wait("run")
    h.queue([{"type": "started", "out_dir": str(folder)},
             {"type": "progress", "stage": "tissue", "fraction": 0.02, "message": "Finding tissue"},
             {"type": "progress", "stage": "sampling", "fraction": 0.5, "message": "Reading window 57 of 61"},
             {"type": "partial", "out_dir": str(folder), "early": True}])
    app._busy(True)
    app._poll()
    shot(app, "03_running_partial")
    h.running = False
    _close(app)

    app = fresh()
    folder = _partial_copy(run, scratch / "run_03b", "running", "Run in progress (running).\n")
    _example_image_note(app, run)
    h = FakeHandle(folder, elapsed=172.0)
    app.handle = h
    app.begin_wait("run")
    h.queue([{"type": "started", "out_dir": str(folder)},
             {"type": "progress", "stage": "sampling", "fraction": 0.5, "message": "Read 61 windows"},
             {"type": "progress", "stage": "detection", "fraction": 0.58, "message": "Preparing preview 20 of 31"},
             {"type": "partial", "out_dir": str(folder), "early": False},
             {"type": "progress", "stage": "crosstalk", "fraction": 0.7,
              "message": "Checking crosstalk: fine detail, window 21 of 61"}])
    app._busy(True)
    app._poll()
    shot(app, "03b_running_crosstalk")
    h.running = False
    _close(app)

    # 04 - 07 on the finished run -----------------------------------------------------------------------------
    app = fresh()
    app.load_past_run(run)
    t = app.titration_tab
    shot(app, "04_titration_selected")                 # the row the window selected by itself
    # 04e the moment after a click: the old words and tiles are gone and the pane says what is coming. A picture made
    # ahead of time would replace this state at once, so the cache is emptied and drawing is slowed for this one click.
    from phenocycler_qc_eval.gui.scene import Scene
    real_render = Scene.render_png
    Scene.render_png = lambda self, *a, **k: (time.sleep(25.0), real_render(self, *a, **k))[1]
    t.evidence._cache.clear()
    other = next(i for i in t.tree.get_children() if i != t._shown)
    select(t.tree, other)
    _burst(app, 0.5)
    app.withdraw(); app.update(); app.deiconify(); app.update()        # a repaint of every widget, as for the other states
    _burst(app, 0.4)
    paths.append(capture(app, "04e_loading_state", out, wait=False))
    print("saved 04e_loading_state.png", flush=True)
    Scene.render_png = real_render
    wait_for_pictures(app)
    from phenocycler_qc_eval.advice import detection_settings_of, signal_level
    det = t.results.detection
    dset = detection_settings_of(t.results.settings)
    rng = t.results.intensity_range
    levels = {str(r["channel"]): signal_level(r, rng, dset) for _, r in det.iterrows()}
    check = next(i for i in t.tree.get_children() if str(det.iloc[int(i)]["call"]).upper() == "CHECK")
    select(t.tree, check)
    shot(app, "04b_compare_row")
    dim = next((i for i in t.tree.get_children() if levels[str(det.iloc[int(i)]["channel"])] == "dim"), None)
    if dim is not None:
        select(t.tree, dim)
        shot(app, "04c_dim_antibody")
    blank = next((i for i in t.tree.get_children() if levels[str(det.iloc[int(i)]["channel"])] == "blank"), None)
    if blank is not None:
        select(t.tree, blank)
        shot(app, "04d_near_blank_antibody")
    app.notebook.select(app.crosstalk_tab)
    ct = app.crosstalk_tab
    select(ct.tree, tree_row(ct.tree, "TOX", "CD163"))
    shot(app, "05_crosstalk_followup")
    ct.set_show_unflagged(True)
    app.update()
    shot(app, "05c_crosstalk_all_pairs")
    ct.set_show_unflagged(False)
    ex = ct.exclusive_tree
    if ex.get_children():
        select(ex, tested_overlap_iid(ct))                       # the strongest overlap among the pairs that were tested
        ex.focus_set()
    shot(app, "06_exclusive_pairs")
    skipped = not_tested_overlap_iid(ct)
    if skipped is not None:
        select(ex, skipped)                                      # a pair with a near-blank channel: a plain sentence
        shot(app, "06b_exclusive_not_tested")
    app.notebook.select(app.figures_tab)
    ft = app.figures_tab
    select(ft.tree, ft.tree.get_children()[0])
    shot(app, "07_figures")
    _close(app)

    # 05b a run with no flagged pairs: it must not read as clean ------------------------------------------------------
    import pandas as pd
    clean = _copy_run(run, scratch / "run_noflags", lambda rel: True)
    pairs = pd.read_csv(clean / "stain_qc_crosstalk_pairs.csv")
    pairs["flag"] = False
    pairs["flag_reason"] = ""
    pairs.to_csv(clean / "stain_qc_crosstalk_pairs.csv", index=False)
    app = fresh()
    app.load_past_run(clean)
    app.notebook.select(app.crosstalk_tab)
    shot(app, "05b_zero_flagged_pairs")
    _close(app)

    # 19 finished: a run that has just ended (stand-in worker, the saved run) --------------------------------------------
    app = fresh()
    _example_image_note(app, run)
    done = _copy_run(run, scratch / "run_done", lambda rel: True)
    h = FakeHandle(done, elapsed=318.0)
    app.handle = h
    app.begin_wait("run")
    app._busy(True)
    app._run_folder = done
    h.queue([{"type": "progress", "stage": "figures", "fraction": 0.95, "message": "Drawing figure 9 of 9"},
             {"type": "done", "out_dir": str(done), "summary": {}}])
    app._poll()
    app.bell = lambda *a, **k: None                    # no beep while capturing
    shot(app, "19_finished")
    (out / "19_finished_title.txt").write_text(app.title() + "\n", encoding="utf-8")
    _close(app)

    # 20 the help window ---------------------------------------------------------------------------------------------------
    app = fresh()
    app.load_past_run(run)
    win = app.show_help()
    win.geometry("760x640+60+40")
    win.update()
    paths.append(capture(win, "20_help_window", out))
    print("saved 20_help_window", flush=True)
    win.destroy()
    _close(app)

    # 08 settings: one form, four states of it ------------------------------------------------------------------
    app = fresh()
    app.load_past_run(run)
    app.notebook.select(app.titration_tab)
    app.on_settings()
    d = app.settings_dialog
    d.update()
    d.fields["detection.dim_fraction"].set("1.5")                       # a wrong entry shows its message
    shot_dialog = lambda name: paths.append(capture(d, name, out)) or print("saved", name, flush=True)  # noqa: E731
    shot_dialog("08a_settings_detection")
    d.fields["detection.dim_fraction"].set("0.1")
    d.fields["crosstalk.margin"].set("0.08")                            # a changed value lists itself at the top
    shot_dialog("08b_settings_crosstalk")
    d.fields["crosstalk.margin"].set("0.05")
    d.exclusive_table.add_row("CD68", "")                               # a half-filled marker row
    d.exclusive_table.add_row("CD3e", "CD4")
    d.exclusive_table.validate(strict=True)
    d.update()
    shot_dialog("08c_settings_markers")
    d.exclusive_table.remove_row(len(d.exclusive_table.rows()) - 1)
    d.exclusive_table.remove_row(len(d.exclusive_table.rows()) - 1)
    d.toggle_advanced()
    d.update()
    shot_dialog("08d_settings_advanced")
    d.cancel()
    app.update()

    # 09 no DAPI picker ---------------------------------------------------------------------------------------------
    channels = ["Hoechst 33342"] + list(load_results(run).detection["channel"])
    dlg = NuclearChannelDialog(app, channels)
    dlg.update()
    paths.append(capture(dlg, "09_no_dapi_picker", out))
    print("saved 09_no_dapi_picker", flush=True)
    dlg.destroy()
    _close(app)

    # 10 no panel ------------------------------------------------------------------------------------------------------
    app = fresh()
    nopanel = nopanel_run or _no_panel_copy(run, scratch / "run_nopanel")   # a real no-panel run when given
    from phenocycler_qc_eval import example
    app.image_var.set(str(EXAMPLE_CROP))
    app._update_template_button()
    app.show_results(load_results(nopanel))
    app.notebook.select(app.crosstalk_tab)
    shot(app, "10_no_panel")
    _close(app)

    # 17 a Xenium folder chosen, no panel file, before Run: nothing of another image on screen ---------------------------
    xen = scratch / "Xenium_output_folder"
    xen.mkdir(exist_ok=True)
    app = fresh()
    app.set_image(xen)
    shot(app, "17_xenium_no_panel")
    _close(app)

    # 11 cancelled ----------------------------------------------------------------------------------------------------------
    app = fresh()
    folder = _partial_copy(run, scratch / "run_11", "running", "Run in progress (running).\n")
    _example_image_note(app, run)
    h = FakeHandle(folder, elapsed=96.0)
    app.handle = h
    app.begin_wait("run")
    h.queue([{"type": "started", "out_dir": str(folder)},
             {"type": "progress", "stage": "crosstalk", "fraction": 0.4,
              "message": "Checking crosstalk: fine detail, window 12 of 61"},
             {"type": "partial", "out_dir": str(folder), "early": False}])
    app._busy(True)
    app._poll()
    app.cancel_run()
    end = time.monotonic() + 10
    while app.handle is not None and time.monotonic() < end:
        _burst(app, 0.1)
    shot(app, "11_cancelled")

    # 12 details open ----------------------------------------------------------------------------------------------------------
    _close(app)
    app = fresh()
    app.load_past_run(run)
    for line in ("Results folder: " + str(run), "Finding tissue", "Reading window 1 of 61",
                 "Scoring the antibodies on the first 50 windows", "Scoring antibodies", "Checking crosstalk",
                 "Drawing figures", "Writing report"):
        app.log_line(line)
    app.open_details()
    for _ in range(6):                     # the rail scrolls to the log; let the scrolled widgets repaint before the picture
        _burst(app, 0.4)
        app.rail_canvas.update_idletasks()
    shot(app, "12_details_open")
    _close(app)

    # 13 - 16 errors -------------------------------------------------------------------------------------------------------------
    for name, kind, message, kw in (
            ("13_error_too_little_tissue", "too_little_tissue", "found 3 tissue windows, need at least 20.",
             {"found": 3, "needed": 20}),
            ("14_error_reader", "reader", "tonsil.qptiff: the file ends before its last tile.", {}),
            ("15_error_unexpected", "unexpected", "Worker stopped unexpectedly (exit code 3).", {}),
            ("16_example_not_available", "example_unavailable", "", {})):
        app = fresh()
        (scratch / "slide.qptiff").write_bytes(b"x")          # an image that exists, so Run is on (the error is the point)
        app.image_var.set(str(scratch / "slide.qptiff"))
        app.show_error(kind, message, **kw)
        shot(app, name)
        _close(app)

    # 18 settings modified: the tooltip lists the changes ---------------------------------------------------------------------------
    modified = default_settings()
    modified.detection.dim_fraction = 0.15
    modified.crosstalk.margin = 0.08
    save_settings(modified, config_path())
    app = fresh()
    app.load_past_run(run)
    app.refresh_settings_status()
    tip = Tooltip(app.settings_label, app.settings_tooltip_text)
    app.update()
    tip.show()
    app.update()
    tip_window = tip._tip
    shot(app, "18_settings_modified", overlays=[tip_window] if tip_window is not None else [])
    tip._leave()
    _close(app)
    config_path().unlink(missing_ok=True)
    return paths


def capture_scaling(run: Path, out: Path, scratch: Path) -> list[Path]:
    """02, 04, 05, 06 and 08d at simulated 125% and 150% display scaling and on 1366 x 768 and 1920 x 1080 screens
    (including the two common real cases: 125% on 1366 x 768 and 150% on 1920 x 1080)."""
    from phenocycler_qc_eval.results import load_results  # noqa: F401

    paths: list[Path] = []
    out.mkdir(parents=True, exist_ok=True)
    # The same logical window as the 100% screens (1280 x 820), grown by the scaling and kept on a 1920 x 1200 screen.
    variants = [("125pct", 1.25, "1600x1025+20+20", None), ("150pct", 1.5, "1900x1140+0+0", None),
                ("1366x768", 1.0, SMALL_SCREEN, (1366, 768)), ("150pct_1366x768", 1.5, SMALL_SCREEN, (1366, 768)),
                ("125pct_1366x768", 1.25, SMALL_SCREEN, (1366, 768)),
                ("150pct_1920x1080", 1.5, "1900x1040+0+0", (1920, 1080))]
    for label, scale, size, screen in variants:
        app = new_app(scale)
        app.minsize(1, 1)                  # the scaled minimum size would otherwise enlarge the window
        app.geometry(size)
        app.screen_override = screen
        app.update()

        def shot(name, win=app, overlays=()):
            p = capture(win, f"{name}_{label}", out, overlays)
            paths.append(p)
            print("saved", p.name, flush=True)

        h = _state_running(app, run, scratch, f"run_scale_{label}")
        shot("02_running")
        h.running = False
        app._end_run()
        app.load_past_run(run)
        shot("04_titration_selected")                  # the row the window selected by itself
        app.notebook.select(app.crosstalk_tab)
        ct = app.crosstalk_tab
        select(ct.tree, tree_row(ct.tree, "TOX", "CD163"))
        shot("05_crosstalk_followup")
        ex = ct.exclusive_tree
        if ex.get_children():
            select(ex, tested_overlap_iid(ct))
        shot("06_exclusive_pairs")
        app.notebook.select(app.titration_tab)
        app.on_settings()
        d = app.settings_dialog
        d.toggle_advanced()
        d.update()
        shot("08d_settings_advanced", win=d)
        d.cancel()
        _close(app)
    return paths


# ---------------------------------------------------------------------------- the click-through
def _signature(app) -> tuple:
    """Everything a person can see change while a run goes on, except the elapsed clock (the clock proves the window is
    alive, not that the run is moving: counting it hid 26 to 40 second quiet stretches in pass 1)."""
    t = app.titration_tab
    rows = len(t.tree.get_children()) if t is not None else 0
    sel = t.tree.selection() if t is not None else ()
    return (app.status_var.get(), int(float(app.progress.cget("value"))), tuple(app.stage_lines()),
            app.banner_text(), rows, sel, app.notebook.select() if app.titration_tab is not None else "")


def _animating(app) -> bool:
    """The progress bar is showing its moving (indeterminate) animation."""
    return bool(app.run_state.active) and str(app.progress.cget("mode")) == "indeterminate"


def thin_frames(folder: Path, keep_every_s: float = 5.0, threshold: float = 0.35) -> tuple[int, int]:
    """Keep a frame when the picture changed (mean pixel difference above `threshold`) or `keep_every_s` passed.

    Returns (frames taken, frames kept). Frames are named f_<seconds>s.png."""
    import numpy as np

    frames = sorted(folder.glob("f_*.png"), key=lambda p: float(p.stem[2:-1]))
    kept, last_img, last_t = 0, None, -1e9
    for p in frames:
        t = float(p.stem[2:-1])
        img = np.asarray(Image.open(p).convert("L"), dtype=np.int16)
        change = 1e9 if last_img is None else float(np.abs(img - last_img).mean())
        if change > threshold or t - last_t >= keep_every_s:
            kept += 1
            last_img, last_t = img, t
        else:
            p.unlink()
    return len(frames), kept


def run_clickthrough(out: Path, scratch: Path, image: Path | None, panel: Path | None,
                     scale: float | None = None, max_seconds: float = 3600.0, frames_on: bool = True,
                     frame_interval: float = 0.5) -> dict:
    """Scripted first use. Returns the timings (also written to out/timings.json)."""
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("f_*.png"):
        old.unlink()
    app = new_app(scale)
    app.geometry(STATE_SIZE)
    app.update()
    frames: list[float] = []
    state = {"next_frame": 0.0}
    t0 = [time.monotonic()]
    changes: list[tuple[float, str]] = []              # (seconds since the click, what changed or "animating")
    text_changes: list[tuple[float, str]] = []         # the same, without the bar's animation
    stages_seen: list[tuple[float, str]] = []          # (seconds, the step that became current)
    rang: list[float] = []                             # when the finish beep was asked for
    missed: list[float] = []                           # frames the picture grab failed for
    last_stage = [""]
    captures: list[tuple[float, float]] = []           # the stretches the script itself spent taking pictures
    sig = [None]
    longest_update = [0.0, 0.0]                        # (seconds, at) the longest whole pass of the Tk loop
    longest_event = [0.0, 0.0]                         # (seconds, at) the longest single event
    names = ("status", "progress", "steps", "banner", "rows", "selection", "tab")

    quiet_now = [False]
    fast_frames = {"until": 0.0, "interval": 0.1}      # 0.1 s frames for 1.5 s after each click (the loading state shows)

    def readable_now() -> bool:
        """The antibody table is on screen, whole, at the pane's full width, with its picture drawn."""
        from phenocycler_qc_eval.gui.tabs import clipped_cells

        tab = app.titration_tab
        if tab is None or not app.results_visible() or not tab.tree.get_children():
            return False
        nb = app.notebook.winfo_width()
        return bool(tab.winfo_width() >= nb - 8 and tab.tree.winfo_width() >= nb - 80 and tab.evidence.is_settled()
                    and not clipped_cells(tab.tree))

    def tick(frame: bool = True) -> bool:
        """One pass of the event loop plus the bookkeeping: what changed when, the longest pass, a frame every
        `frame_interval` seconds. Returns True when it ended at a quiet point (nothing queued: a real window paints now);
        pictures are taken only then, so they show what a person would see and never a half-laid-out moment."""
        u0 = time.monotonic()
        quiet_now[0] = False
        # One event at a time, as a real mainloop does: the window takes input between two events, so the longest
        # single event is how long it can be unresponsive. The whole pass (what pass 1 reported) is kept as well.
        while True:
            e0 = time.monotonic()
            busy["since"] = e0
            did = app.tk.dooneevent(_tkinter.DONT_WAIT)
            busy["since"] = None
            one = time.monotonic() - e0
            if not did:
                quiet_now[0] = True
                break
            if one > longest_event[0]:
                longest_event[:] = [one, e0 - t0[0]]
            if time.monotonic() - u0 > 0.25:
                break                      # look at the window between bursts, as a person watching would
        took = time.monotonic() - u0
        if took > longest_update[0]:
            longest_update[:] = [took, u0 - t0[0]]
        s = _signature(app)
        el = time.monotonic() - t0[0]
        if app.titration_tab is not None and "results_loaded" not in marks and app.titration_tab.tree.get_children():
            marks["results_loaded"] = el                       # the rows exist (maybe still under the working page)
        if quiet_now[0] and "results_readable" not in marks and "results_loaded" in marks and readable_now():
            marks["results_readable"] = el                     # shown, whole, at its final width, picture drawn
        if s != sig[0]:
            what = ",".join(n for n, a, b in zip(names, s, sig[0] or s) if a != b) or "first"
            sig[0] = s
            changes.append((el, what))
            text_changes.append((el, what))
        elif _animating(app) and (not changes or el - changes[-1][0] >= 0.4):
            changes.append((el, "animating"))              # a moving bar is the window saying "still working"
        current = next((ln for ln in app.stage_lines() if ln.startswith("\u25CF")), "")
        if current and current != last_stage[0] and current.split("(")[0] != last_stage[0].split("(")[0]:
            stages_seen.append((round(el, 1), current))
        last_stage[0] = current or last_stage[0]
        interval = fast_frames["interval"] if time.monotonic() < fast_frames["until"] else frame_interval
        if frames_on and frame and quiet_now[0] and el >= state["next_frame"]:
            c0 = time.monotonic()
            try:
                grab(app).save(out / f"f_{el:07.1f}s.png")
                frames.append(el)
            except OSError:                    # PrintWindow can fail now and then (a locked desktop): skip this frame
                missed.append(el)
            captures.append((c0 - t0[0], time.monotonic() - t0[0]))
            state["next_frame"] = el + interval
        time.sleep(0.02)
        return quiet_now[0]

    def wait_until(cond, what: str, timeout: float = max_seconds):
        """Tick until cond() is true at a quiet point."""
        end = time.monotonic() + timeout
        while True:
            quiet = tick()
            if quiet and cond():
                return
            if time.monotonic() > end:
                raise TimeoutError(f"gave up waiting for {what}")

    def hold(seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            tick()

    marks: dict[str, float] = {}
    slow_stacks: list[dict] = []                      # what the main thread was doing while one event ran long
    busy = {"since": None}
    stop_sampler = threading.Event()
    main_id = threading.get_ident()

    def sampler() -> None:
        import traceback
        while not stop_sampler.wait(0.5):
            since = busy["since"]
            if since is not None and time.monotonic() - since > 1.5:
                frame = sys._current_frames().get(main_id)
                if frame is not None:
                    stack = traceback.extract_stack(frame)[-7:]
                    slow_stacks.append({"at_s": round(time.monotonic() - t0[0], 1),
                                        "event_running_s": round(time.monotonic() - since, 1),
                                        "stack": [f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in stack]})

    threading.Thread(target=sampler, daemon=True).start()
    original_bell = app.bell
    app.bell = lambda *a, **k: rang.append(time.monotonic() - t0[0])      # count the finish beep instead of playing it
    try:
        hold(1.0)                                       # the empty window, as a person first sees it
        warm_end = time.monotonic() + 30
        while not app.tabs_warm() and time.monotonic() < warm_end:
            tick(frame=False)                           # the tabs are shown once, under the empty page, right after start
        hold(0.5)
        t0[0] = time.monotonic()
        state["next_frame"] = 0.0
        sig[0] = _signature(app)
        clicks = 0
        if image is None:
            clicks += 1                                  # Try the example
            marks["click"] = time.monotonic() - t0[0]
            app.example_button.invoke()
        else:
            clicks += 1                                  # Choose file (the picker is replaced by set_image)
            app._output_user_set = True
            app._set_output(scratch / "qc_results")
            app.set_image(Path(image))
            if panel is not None and str(panel) != app.panel_var.get():
                clicks += 1                              # Choose panel file
                app.panel_var.set(str(panel))
            hold(0.5)
            clicks += 1                                  # Run
            marks["click"] = time.monotonic() - t0[0]
            app.run_button.invoke()
        marks["clicks_before_run"] = clicks

        def progress_started() -> bool:
            return float(app.progress.cget("value")) > 0 and app.handle is not None
        wait_until(lambda: progress_started() or app.handle is None, "the first progress")
        marks["first_progress"] = time.monotonic() - t0[0]
        wait_until(lambda: "results_readable" in marks or app.handle is None, "the antibody results")
        marks["antibody_results"] = marks.get("results_readable", time.monotonic() - t0[0])
        hold(1.0)
        tab = app.titration_tab                           # the person looks at the row the window selected, as it is
        marks["first_row_selected"] = time.monotonic() - t0[0]
        wait_until(lambda: app.handle is None, "the run to finish")
        marks["done"] = time.monotonic() - t0[0]
        finished_title = app.title()
        finished_banner = app.banner_text() + " | " + " | ".join(app.banner_notes())
        finished_steps = list(app.stage_lines())
        hold(1.0)
        det = tab.results.detection
        compare = next((i for i in tab.tree.get_children() if str(det.iloc[int(i)]["call"]).upper() == "CHECK"), None)
        clicks_settled: dict[str, float] = {}
        clicks_shown: dict[str, float] = {}
        clicks_feedback: dict[str, float] = {}       # ms from the click to the first quiet point after it (feedback painted)
        clicks_handler: dict[str, float] = {}        # ms the click's own event took
        click_marks: dict[str, tuple[float, float, float]] = {}      # label -> (click, shown, settled), seconds since t0

        def click_and_time(label: str, do, shown, settled) -> float:
            """Do the click; record the seconds until `shown()` (the right words and picture are on screen) and until
            `settled()` (the picture is also drawn at its final size and nothing is left to draw), at quiet points."""
            began = time.monotonic()
            fast_frames["until"] = began + 1.5
            state["next_frame"] = 0.0
            do()
            fed_back = time.monotonic()
            # Feedback is "on screen" at the first quiet point after the click: nothing queued, a real window paints now.
            while not tick(frame=True):
                pass
            clicks_feedback[label] = round((time.monotonic() - began) * 1000.0, 0)
            clicks_handler[label] = round((fed_back - began) * 1000.0, 0)
            wait_until(shown, label + " shown", timeout=120)
            shown_at = time.monotonic()
            clicks_shown[label] = round(shown_at - began, 2)
            wait_until(settled, label + " settled", timeout=120)
            clicks_settled[label] = round(time.monotonic() - began, 2)
            click_marks[label] = (began - t0[0], shown_at - t0[0], time.monotonic() - t0[0])
            return began

        def words_and_picture_shown(ev, owner) -> bool:
            return bool(ev.header_title() and ev.header_subtitle() and ev.drawn_owner() == owner)

        def words_and_picture(ev, owner) -> bool:
            return bool(words_and_picture_shown(ev, owner) and ev.is_settled())

        if compare is not None:                             # a Compare row: what to compare and what it means
            channel = str(tab.tree.item(compare, "values")[0])
            click_and_time("compare_row", lambda: select(tab.tree, compare),
                           lambda: words_and_picture_shown(tab.evidence, channel),
                           lambda: words_and_picture(tab.evidence, channel))
            hold(1.0)
        click_and_time("open_crosstalk_tab", lambda: app.notebook.select(app.crosstalk_tab),
                       lambda: bool(app.crosstalk_tab.evidence.header_title() and app.crosstalk_tab.evidence.drawn_owner()),
                       lambda: app.crosstalk_tab.evidence.is_settled() and not app.crosstalk_tab.clipped_cells())
        hold(1.0)
        ct = app.crosstalk_tab
        pair = tree_row(ct.tree, "TOX", "CD163")
        last_click = click_and_time("pair_TOX_CD163", lambda: select(ct.tree, pair),
                                    lambda: words_and_picture_shown(ct.evidence, "TOX->CD163"),
                                    lambda: words_and_picture(ct.evidence, "TOX->CD163") and not ct.clipped_cells()
                                    and not ct.evidence.clipped_texts())
        marks["crosstalk_opened"] = time.monotonic() - t0[0]
        marks["last_click_settled"] = time.monotonic() - last_click
        hold(max(0.0, TAIL_SECONDS - (time.monotonic() - last_click)))     # frames for at least 5 s after the last click
        status = app.status_var.get()
        banner = app.banner_text()
        run_folder = str(app._run_folder) if app._run_folder else None
    finally:
        try:
            app.bell = original_bell
            _close(app)
        except tk.TclError:
            pass
    stop_sampler.set()
    taken, kept = thin_frames(out) if frames_on else (0, 0)
    def own_time(a: float, b: float) -> float:
        """How much of (a, b) the script spent taking pictures (that is not time the window sat still)."""
        return sum(max(0.0, min(b, e) - max(a, st)) for st, e in captures)

    def gaps(series):
        """(length, start, what changed at the start) of every stretch between two changes inside the run."""
        return [(b - a, a, wa) for (a, wa), (b, _wb) in zip(series, series[1:])
                if a >= marks.get("click", 0) and b <= marks["done"] + 1.0]

    window = gaps(changes)
    raw = max(window, default=(0.0, 0.0, ""))
    adjusted = max(((length - own_time(start, start + length), start, what) for length, start, what in window),
                   default=(0.0, 0.0, ""))
    text_gap = max(gaps(text_changes), default=(0.0, 0.0, ""))
    quiet = sorted((g for g in gaps(text_changes) if g[0] > 2.0), reverse=True)[:8]
    longest_capture = max((e - st for st, e in captures), default=0.0)
    timings = {
        "clicks_before_run": marks["clicks_before_run"],
        "seconds_to_first_progress": round(marks["first_progress"] - marks["click"], 1),
        "seconds_to_antibody_results": round(marks["antibody_results"] - marks["click"], 1),
        "seconds_until_results_readable": (round(marks["results_readable"] - marks["results_loaded"], 1)
                                           if "results_readable" in marks and "results_loaded" in marks else None),
        "seconds_to_results_loaded": round(marks["results_loaded"] - marks["click"], 1) if "results_loaded" in marks else None,
        "seconds_after_last_click_until_stable": round(marks["last_click_settled"], 2),
        "ms_click_to_feedback": clicks_feedback.get("compare_row"),
        "ms_click_to_feedback_each_click": clicks_feedback,
        "ms_click_handler_each_click": clicks_handler,
        "seconds_click_to_evidence_row": clicks_shown.get("compare_row"),
        "seconds_click_to_evidence_pair": clicks_shown.get("pair_TOX_CD163"),
        "seconds_to_open_crosstalk_tab": clicks_shown.get("open_crosstalk_tab"),
        "seconds_from_each_click_until_shown": clicks_shown,
        "seconds_from_each_click_until_settled": clicks_settled,
        "seconds_from_each_click_until_shown_without_screenshot_time": {
            k: round(b - a - own_time(a, b), 2) for k, (a, b, _c) in click_marks.items()},
        "seconds_from_each_click_until_settled_without_screenshot_time": {
            k: round(c - a - own_time(a, c), 2) for k, (a, _b, c) in click_marks.items()},
        "seconds_until_results_readable_without_screenshot_time": (
            round(marks["results_readable"] - marks["results_loaded"] - own_time(marks["results_loaded"], marks["results_readable"]), 1)
            if "results_readable" in marks and "results_loaded" in marks else None),
        "seconds_after_last_click_until_stable_without_screenshot_time": (
            round(click_marks["pair_TOX_CD163"][2] - click_marks["pair_TOX_CD163"][0]
                  - own_time(click_marks["pair_TOX_CD163"][0], click_marks["pair_TOX_CD163"][2]), 2)
            if "pair_TOX_CD163" in click_marks else None),
        "seconds_recorded_after_last_click": round(time.monotonic() - last_click, 1),
        "seconds_to_done": round(marks["done"] - marks["click"], 1),
        "longest_gap_without_ui_update_s": round(adjusted[0], 2),
        "longest_gap_at_s": round(adjusted[1], 1),
        "longest_gap_last_change": adjusted[2],
        "longest_gap_including_screenshot_time_s": round(raw[0], 2),
        "longest_gap_in_words_and_numbers_s": round(text_gap[0], 2),     # ignores the bar's animation
        "longest_gap_in_words_and_numbers_at_s": round(text_gap[1], 1),
        "stretches_over_2s_without_new_words_or_numbers": [
            {"seconds": round(g, 1), "at_s": round(a, 1), "last_change": w} for g, a, w in quiet],
        "steps_seen": stages_seen,
        "finished_window_title": finished_title,
        "finished_banner": finished_banner,
        "finished_steps": finished_steps,
        "finish_beeps_asked_for": len(rang),
        "longest_single_screenshot_s": round(longest_capture, 2),
        "slow_event_samples": slow_stacks[:12],
        "longest_single_event_s": round(longest_event[0], 2),
        "longest_single_event_at_s": round(longest_event[1], 1),
        "longest_event_loop_pass_s": round(longest_update[0], 2),
        "longest_event_loop_pass_at_s": round(longest_update[1], 1),
        "source": "example" if image is None else str(image),
        "frames_taken": taken, "frames_kept": kept, "frames_missed": len(missed),
        "frame_interval_s": frame_interval if frames_on else None, "frames_only_at_quiet_points": True,
        "final_status": status, "final_banner": banner, "run_folder": run_folder,
        "capture_method": sorted(set(METHOD_USED)),
        "note": ("Display scaling is 100%. The example is copied from a local file, so the download step is a copy; "
                 "a real download takes longer." if image is None else "Full section, read-only source."),
    }
    (out / "timings.json").write_text(json.dumps(timings, indent=2), encoding="utf-8")
    return timings


# ---------------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--run", help="a finished run folder of the example (states and scaling modes)")
    ap.add_argument("--nopanel-run", help="a finished run of the same image made without a panel file (screens 10, 17)")
    ap.add_argument("--scaling", action="store_true", help="the scaling set: 04, 05, 08d and 02 at 125%%, 150%% and 1366x768")
    ap.add_argument("--clickthrough", action="store_true")
    ap.add_argument("--image", help="click-through on this image instead of the example")
    ap.add_argument("--panel", help="panel file for --image")
    ap.add_argument("--scratch", help="working folder (default: a new temporary one)")
    ap.add_argument("--printwindow", action="store_true",
                    help="never grab the screen: draw each window with PrintWindow (safe when other windows overlap)")
    ap.add_argument("--scale", type=float, help="simulated display scaling for the click-through (1.25, 1.5)")
    ap.add_argument("--frame-interval", type=float, default=0.5, help="seconds between click-through frames")
    ap.add_argument("--no-frames", action="store_true",
                    help="click-through without pictures (timings only; a picture is taken on the Tk thread)")
    a = ap.parse_args(argv)
    if a.printwindow:
        _GRAB_WORKS[0] = False
    try:
        # Like pqe-gui itself. Without it Windows scales the window as a picture and a window grab comes out
        # cropped; with it every size is in real pixels and the look is set by `tk scaling` alone.
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001 - not Windows, or already set
        pass
    scratch = Path(a.scratch) if a.scratch else Path(tempfile.mkdtemp(prefix="pqe_capture_"))
    scratch.mkdir(parents=True, exist_ok=True)
    _prepare_env(scratch)
    out = Path(a.out)

    if a.clickthrough:
        timings = run_clickthrough(out, scratch, Path(a.image) if a.image else None,
                                   Path(a.panel) if a.panel else None, a.scale, frames_on=not a.no_frames,
                                   frame_interval=a.frame_interval)
        print(json.dumps(timings, indent=2))
        return 0
    if not a.run:
        print("--run <finished run folder> is needed (make one with pqe or the click-through).")
        return 2
    run = Path(a.run)
    paths = capture_scaling(run, out, scratch) if a.scaling else capture_states(run, out, scratch,
                                                                      Path(a.nopanel_run) if a.nopanel_run else None)
    for p in paths:
        im = Image.open(p)
        print(p.name, im.size, "blank" if is_blank(im) else "ok")
    print("method:", sorted(set(METHOD_USED)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
