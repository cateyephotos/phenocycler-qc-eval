"""What one evidence picture is made of, built and drawn without touching Tk.

A `Scene` holds a matplotlib Figure (OO API only: Figure + FigureCanvasAgg, never pyplot), the words that belong
beside it (caption, default header) and the facts the window and its tests ask about (tile count, row names, ...).
Building and drawing a scene take about a second on a slow workstation, so the window does both in a background
thread (see renderpool.py) and only swaps the finished picture in on the Tk thread.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.image import imread
from matplotlib.text import Text
from matplotlib.ticker import FixedLocator, FuncFormatter, NullFormatter

_FONT = 8
_MUTED = "#52606d"
UNREADABLE = "That preview could not be read."
MIN_ROW_PX = 44            # a tile row is never drawn shorter than this (at 100% scaling): its 3-line label fits
MIN_BLANK_PX = 140         # a near-blank antibody has no tiles: a message and the histogram
MIN_ANTIBODY_PX = 168      # tiles and histogram together; with the text above it the page fits the default window
ANTIBODY_LEFT_PX = 84       # the histogram's axis label and the widest count on its log axis ("1,000,000")
MIN_MESSAGE_PX = 120
MIN_IMAGE_PX = 260
_CHAR_PX = 7.2             # width of one character of a tile-row label (8 pt) at 100%, with a little slack
_GAP = 6                   # pixels of white between tiles in a montage


def _row_limits(stack: np.ndarray) -> tuple[float, float]:
    """Display range for one row of tiles: 0 (or the minimum) to the 99.5th percentile."""
    arr = np.asarray(stack, dtype=float)
    lo = float(np.nanmin(arr)) if arr.dtype.kind == "f" else 0.0
    hi = float(np.nanpercentile(arr, 99.5))
    return lo, (hi if hi > lo else lo + 1.0)


def _gray_cmap():
    return matplotlib.colormaps["gray"].with_extremes(bad="white")      # the gaps between tiles


def _strip(stack: np.ndarray, vmin: float, vmax: float) -> np.ndarray:
    """k tiles (k, h, w) side by side as one float picture scaled 0..1, with NaN gaps between them."""
    stack = np.asarray(stack, dtype=float)
    span = (vmax - vmin) if vmax > vmin else 1.0
    k, h, w = stack.shape
    cols = []
    for i in range(k):
        cols.append(np.clip((stack[i] - vmin) / span, 0.0, 1.0))
        if i < k - 1:
            cols.append(np.full((h, _GAP), np.nan))
    return np.hstack(cols)


def _fmt(v: float) -> str:
    return f"{v:.3g}" if abs(v) < 10 and v != int(v) else f"{v:.0f}"


def followup_sentence(source: str, target: str, numbers: dict) -> str:
    """The one sentence the follow-up panel opens with, from the run's own numbers."""
    hi, lo = numbers.get("target_where_source_bright"), numbers.get("target_where_source_low")
    if hi is not None and lo is not None:
        return f"{target} reads {hi:.3g} where {source} is bright and {lo:.3g} where {source} is low."
    return ""


class Scene:
    """One picture and what goes with it. Nothing here touches Tk, so it can be made and drawn in any thread."""

    def __init__(self, owner: str = "", title: str = "", kind: str = "picture") -> None:
        self.figure: Figure | None = Figure(figsize=(6.4, 2.9), dpi=100, facecolor="white")
        self.owner = owner
        self.title = title
        self.kind = kind
        self.min_px = MIN_MESSAGE_PX                # the picture is never asked to be shorter than this (at 100%)
        self.margins: tuple | None = None           # (grid spec, top px, bottom px, left px or None)
        self.caption = ""
        self.tile_count = 0
        self.blank = False
        self.followup_rows: list[tuple[str, str]] = []
        self.pair_rows: list[tuple[str, str]] = []
        self.followup_names: tuple[str, str, str | None] | None = None
        self.header: tuple[str, str] = ("", "")     # the words the builder itself puts above the picture
        self.message: tuple[str, bool] | None = None   # (text, top): a plain message, drawn by Tk, never rendered
        self.agg: FigureCanvasAgg | None = None
        self._maker = None                           # rebuilds this scene (a cached scene keeps no figure)

    # ---- constructors: one per kind of picture ---------------------------------------------------------------
    @classmethod
    def message_scene(cls, text: str, top: bool = False) -> "Scene":
        s = cls(owner=f"message: {text}", title=text, kind="message")
        s.message = (text, bool(top))
        s.figure.text(0.5, 0.88 if top else 0.5, text, ha="center", va="top" if top else "center", fontsize=11,
                      color=_MUTED)
        return s

    @classmethod
    def image(cls, png: Path, title: str, subtitle: str = "") -> "Scene":
        try:
            img = imread(str(png))
        except Exception:  # PIL raises SyntaxError, OSError, ValueError for bad files
            return cls.message_scene("That figure could not be opened.")
        s = cls(owner=f"image: {Path(png).name}", title=title, kind="image")
        s.min_px = MIN_IMAGE_PX
        s.header = (title, subtitle)
        ax = s.figure.add_axes([0.01, 0.01, 0.98, 0.98])
        ax.imshow(img)
        ax.axis("off")
        return s

    @classmethod
    def antibody(cls, preview: dict, row: pd.Series, range_max: float | None = None,
                 full_scale: bool = False, level: str = "ok") -> "Scene":
        """Tiles and histogram for one antibody. The text header is set by the caller.

        By default the tiles share one display range (0 to the antibody's own bright end), so a dim channel
        is readable. With `full_scale` they are drawn on the whole range of the image instead, which is how
        dim they really are next to the other channels. `level` is "blank" (little or no signal: a plain message
        takes the place of the tiles, which would show only noise), "dim" (the caption says the tiles are
        brightened) or "ok"."""
        marker = str(row.get("channel", preview.get("marker", "")))
        s = cls(owner=marker, title=f"{marker}: {row.get('next_run', '')}", kind="antibody")
        s.min_px = MIN_ANTIBODY_PX if level != "blank" else MIN_BLANK_PX
        tiles = np.asarray(preview["tiles"])
        k = max(int(tiles.shape[0]), 1)
        lo, hi = _row_limits(tiles)
        vmin = float(preview.get("vmin", lo))
        vmax = float(preview.get("vmax", hi))
        # Wider and float images are stored scaled to 0..255; the caption shows the real display range.
        shown_lo = float(preview.get("display_min", vmin))
        shown_hi = float(preview.get("display_max", vmax))
        full = float(range_max) if range_max else (255.0 if tiles.dtype == np.uint8 else None)
        of_full = f" of {full:g}" if full else ""
        if full_scale and full and level != "blank":
            # tile value t stands for t * display_max / vmax in image units, so the full range is at:
            vmax = full * vmax / shown_hi if shown_hi > 0 else vmax
            shown_lo, shown_hi = 0.0, full
        tissue_share = np.asarray(preview.get("tissue_fraction", np.zeros(0)), dtype=float).ravel()
        if tissue_share.size != tiles.shape[0]:
            tissue_share = np.zeros(0)         # an older preview file: no share to print
        # Printed only when it says something: a section that is all tissue needs no "100% tissue" under every tile.
        show_share = bool(tissue_share.size) and float(tissue_share.min()) < 0.95
        fig = s.figure
        gs = fig.add_gridspec(2, 1, height_ratios=[2.0, 1.0], hspace=0.5, left=0.07, right=0.98, top=0.95, bottom=0.17)
        s.set_margins(gs, top_px=8, bottom_px=50, left_px=ANTIBODY_LEFT_PX)
        ax = fig.add_subplot(gs[0, 0])
        h, w = tiles.shape[1:3]
        if level == "blank":
            ax.axis("off")
            s.blank = True
            ax.text(0.5, 0.5, f"Little or no signal.\nThe brightest 0.5% of pixels reach {_fmt(shown_hi)}{of_full}, "
                    "so tiles would show only noise.", ha="center", va="center", fontsize=_FONT + 2,
                    color=_MUTED, transform=ax.transAxes)
            # The message in the picture says it; a caption beside the toolbar would say it a second time.
        else:
            # All tiles are one picture (one set of axes), which costs a fraction of one axes per tile to draw.
            ax.imshow(_strip(tiles, vmin, vmax), cmap=_gray_cmap(), vmin=0.0, vmax=1.0, interpolation="nearest")
            ax.axis("off")
            s.tile_count = int(tiles.shape[0])
            if show_share:
                for i in range(tiles.shape[0]):
                    ax.text(i * (w + _GAP) + w / 2, h + 0.04 * h, f"{round(100 * float(tissue_share[i]))}% tissue",
                            ha="center", va="top", fontsize=_FONT - 1, color=_MUTED)
            if full_scale and full:
                caption = (f"{k} regions of the section, shown on the full scale: "
                           f"{_fmt(shown_lo)}-{_fmt(shown_hi)}{of_full}.")
            elif level == "dim":
                caption = (f"{k} regions of the section, brightened for display ({_fmt(shown_lo)}-{_fmt(shown_hi)}"
                           f"{of_full}); true signal is low.")
            else:
                caption = (f"{k} regions of the section. Tiles share one display range: "
                           f"{_fmt(shown_lo)}-{_fmt(shown_hi)}{of_full}.")
            s.caption = caption
        ax = fig.add_subplot(gs[1, 0])
        raw = np.asarray(preview["hist_counts"], dtype=float)
        edges = np.asarray(preview["hist_edges"], dtype=float)
        ax.stairs(np.clip(raw, 1, None), edges, fill=True, color="#9fb3c8")
        ax.set_yscale("log")
        ax.set_ylim(bottom=1)
        ymax = float(max(raw.max(), 10.0)) * 1.4
        ax.set_ylim(1, ymax)
        # Plain numbers on the log axis (mathtext is slow to lay out), at 1, 100, 10,000 ... so the scale is readable.
        ticks = [t for t in (1, 100, 10_000, 1_000_000, 100_000_000) if t <= ymax]
        ax.yaxis.set_major_locator(FixedLocator(ticks))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _pos: f"{v:,.0f}"))
        ax.yaxis.set_minor_formatter(NullFormatter())
        xlabel = "Pixel value"
        filled = np.nonzero(raw > 0)[0]
        top = full if full else float(edges[-1])
        if filled.size:
            seen = float(edges[min(filled.max() + 1, len(edges) - 1)])
            if full and seen < 0.4 * full:
                top = min(full, 1.5 * seen)  # dim channel: zoom in so the shape is readable
                xlabel = f"Pixel value (axis zoomed to 0-{_fmt(top)}{of_full})"
        ax.set_xlim(0, top)
        otsu = float(preview.get("otsu_threshold", np.nan))
        if np.isfinite(otsu) and otsu <= top:                       # the legend names only a line that is drawn
            ax.axvline(otsu, color="#c0392b", linewidth=1.4, label="Cut between stained and background")
            ax.legend(fontsize=_FONT, frameon=False, loc="upper right")
        ax.set_xlabel(xlabel, fontsize=_FONT)
        ax.set_ylabel("Pixels (log scale)", fontsize=_FONT)
        ax.tick_params(labelsize=_FONT)
        return s

    @staticmethod
    def _row_label(name: str, role: str, vmin: float, vmax: float, blank: bool = False) -> str:
        # A near-blank marker would be a speckle map of noise: the label says so instead of printing its range.
        return f"{name}\n{role}\n" + ("little or no signal" if blank else f"{_fmt(vmin)}-{_fmt(vmax)}")

    def _draw_rows(self, rows: list[tuple[str, str, np.ndarray]], blank: frozenset = frozenset()) -> None:
        """Rows of tiles, one picture, each row labeled at left with its name, role and display range."""
        n = len(rows)
        self.min_px = MIN_ROW_PX * n + 52
        strips, labels = [], []
        for name, role, stack in rows:
            vmin, vmax = _row_limits(stack)                       # each row has its own display range
            strips.append(_strip(stack, vmin, vmax))
            labels.append(self._row_label(name, role, vmin, vmax, name in blank))
        # Room at left for the longest line of any label, in pixels: a long marker name is never cut off.
        label_px = _CHAR_PX * max(len(line) for label in labels for line in label.split(chr(10))) + 18
        gs = self.figure.add_gridspec(1, 1, left=0.15, right=0.98, top=0.92, bottom=0.11)
        self.set_margins(gs, top_px=8, bottom_px=30, left_px=label_px)
        ax = self.figure.add_subplot(gs[0, 0])
        width = max(a.shape[1] for a in strips)
        picture, centers, y = [], [], 0
        for strip in strips:
            if strip.shape[1] < width:
                strip = np.hstack([strip, np.full((strip.shape[0], width - strip.shape[1]), np.nan)])
            picture.append(strip)
            centers.append(y + strip.shape[0] / 2)
            y += strip.shape[0]
            picture.append(np.full((_GAP, width), np.nan))
            y += _GAP
        ax.imshow(np.vstack(picture[:-1]), cmap=_gray_cmap(), vmin=0.0, vmax=1.0, interpolation="nearest")
        ax.axis("off")
        ax.set_anchor("N")                    # the rows hang from the top, under the words, not centered in spare height
        for label, yc in zip(labels, centers):
            # A few points left of the picture's edge whatever the tiles' size (a gap in picture units would grow with it).
            ax.annotate(label, xy=(0, yc), xycoords=ax.get_yaxis_transform(), xytext=(-6, 0),
                        textcoords="offset points", ha="right", va="center", fontsize=_FONT, linespacing=1.15,
                        multialignment="right", annotation_clip=False)

    @classmethod
    def followup(cls, preview: dict) -> "Scene":
        source = str(preview.get("source_name", "source"))
        target = str(preview.get("target_name", "target"))
        control = str(preview.get("control_name", "") or "control")
        rows: list[tuple[str, str, np.ndarray]] = [
            (source, "source", np.asarray(preview["source"])),
            (target, "target", np.asarray(preview["target"])),
        ]
        ctrl = np.asarray(preview.get("control", np.zeros(0)))
        if ctrl.size:
            rows.append((control, "control", ctrl))
        rows.append(("DAPI", "nuclei", np.asarray(preview["dapi"])))
        try:
            numbers = json.loads(str(preview.get("numbers", "{}")))
        except ValueError:
            numbers = {}
        title = f"{source} -> {target}"
        sentence = followup_sentence(source, target, numbers)
        s = cls(owner=f"{source}->{target}", title=f"{title}: {sentence}" if sentence else title, kind="followup")
        s.followup_names = (source, target, control if ctrl.size else None)
        s.header = (title, sentence)
        k = max(int(rows[0][2].shape[0]), 1)
        s._draw_rows(rows)
        s.followup_rows = [(name, role) for name, role, _stack in rows]
        s.caption = f"{k} regions of the section. Each row has its own display range (shown at left)."
        return s

    @classmethod
    def pair(cls, first: dict, second: dict, names: tuple[str, str], blank: frozenset = frozenset()) -> "Scene":
        """The tiles of two markers on the same windows, one row each (the overlap view). The header is set by the
        caller."""
        rows = []
        for role, (name, prev) in zip(("marker A", "marker B"), zip(names, (first, second))):
            rows.append((name, role, np.asarray(prev["tiles"])))
        s = cls(owner=f"{names[0]}+{names[1]}", title=f"{names[0]} and {names[1]}", kind="pair")
        s.header = (f"{names[0]} and {names[1]}", "")
        s._draw_rows(rows, frozenset(blank))
        s.pair_rows = [(name, role) for name, role, _s in rows]
        k = max(int(rows[0][2].shape[0]), 1)
        s.caption = (f"The same {k} regions of the section for both markers. Each row has its own display "
                     "range (shown at left).")
        return s

    # ---- margins and drawing -------------------------------------------------------------------------------------
    def set_margins(self, gs, top_px: float, bottom_px: float, left_px: float | None = None) -> None:
        """Keep room for the caption above, the axis labels below and the row labels at left in pixels, so a short or
        narrow pane never cuts them."""
        self.margins = (gs, float(top_px), float(bottom_px), None if left_px is None else float(left_px))
        self.apply_margins()

    def apply_margins(self, height_px: float | None = None) -> None:
        if self.margins is None or self.figure is None:
            return
        gs, top_px, bottom_px, left_px = self.margins
        k = self.figure.dpi / 100.0                  # the text grows with the display scaling; so do its margins
        h = float(height_px) if height_px else float(self.figure.get_figheight() * self.figure.dpi)
        h = max(h, 120.0)
        edges = {"top": 1.0 - min(top_px * k / h, 0.3), "bottom": min(bottom_px * k / h, 0.4)}
        if left_px is not None:
            w = max(float(self.figure.get_figwidth() * self.figure.dpi), 120.0)
            edges["left"] = min(left_px * k / w, 0.55)
        gs.update(**edges)
        # GridSpec.update moves the axes only for pyplot figures; this one is not, so move them here.
        for ax in self.figure.axes:
            spec = ax.get_subplotspec()
            if spec is not None and spec.get_gridspec() is gs:
                ax._set_position(spec.get_position(self.figure))

    def size_figure(self, width_px: int, height_px: int, scale: float = 1.0) -> None:
        """Make the figure `width_px` by `height_px` real pixels (the text and the margins grow with `scale`)."""
        fig = self.figure
        dpi = 100.0 * max(float(scale), 0.5)
        fig.set_dpi(dpi)
        fig.set_size_inches((width_px + 0.01) / dpi, (height_px + 0.01) / dpi)
        self.apply_margins()

    def draw(self, width_px: int, height_px: int, scale: float = 1.0) -> np.ndarray:
        """Draw at exactly this many pixels; returns the picture as an (h, w, 4) uint8 array."""
        self.size_figure(width_px, height_px, scale)
        if self.agg is None or self.agg.figure is not self.figure:
            self.agg = FigureCanvasAgg(self.figure)
        self.agg.draw()
        return np.asarray(self.agg.buffer_rgba())

    def render_png(self, width_px: int, height_px: int, scale: float = 1.0) -> tuple[bytes, int, int]:
        """(PNG bytes, width, height) of the picture drawn at this size."""
        from PIL import Image

        rgba = self.draw(width_px, height_px, scale)
        buf = io.BytesIO()
        Image.fromarray(np.ascontiguousarray(rgba[..., :3])).save(buf, "PNG", compress_level=1)
        return buf.getvalue(), int(rgba.shape[1]), int(rgba.shape[0])

    # ---- a cached scene keeps no figure (it would hold every tile's pixels); it can be made again --------------------
    def light(self, maker=None) -> "Scene":
        """This scene without its figure: the words and facts stay, the figure is rebuilt by `maker` if it is asked for."""
        self._maker = maker if maker is not None else self._maker
        self.figure = None
        self.agg = None
        self.margins = None
        return self

    def ensure_figure(self) -> Figure:
        """The figure (rebuilt from its maker when this scene was cached without one)."""
        if self.figure is None:
            fresh = self._maker() if self._maker is not None else None
            if fresh is None or fresh.figure is None:
                raise RuntimeError("this picture cannot be made again")
            self.figure, self.margins, self.agg = fresh.figure, fresh.margins, None
        return self.figure

    def clipped_texts(self, width_px: int, height_px: int, scale: float = 1.0) -> list[str]:
        """Words in the picture that run past the picture's edge (empty when everything is whole)."""
        self.ensure_figure()
        if self.agg is None or self.agg.figure is not self.figure:
            self.draw(width_px, height_px, scale)
        renderer = self.agg.get_renderer()
        box = self.figure.bbox
        unseen: set[int] = set()                 # tick labels of ticks outside the axis range (never drawn)
        for ax in self.figure.axes:
            for axis, lim in ((ax.xaxis, ax.get_xlim()), (ax.yaxis, ax.get_ylim())):
                lo, hi = sorted(lim)
                for tick in axis.get_major_ticks():
                    if not lo <= tick.get_loc() <= hi:
                        unseen.update((id(tick.label1), id(tick.label2)))
        out = []
        for t in self.figure.findobj(Text):
            if id(t) in unseen or not t.get_visible() or not str(t.get_text()).strip():
                continue
            bb = t.get_window_extent(renderer)
            if bb.x0 < box.x0 - 3 or bb.x1 > box.x1 + 3 or bb.y0 < box.y0 - 3 or bb.y1 > box.y1 + 3:
                out.append(str(t.get_text()))
        return out
