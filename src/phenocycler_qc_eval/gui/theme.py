"""Colors, fonts and ttk styles shared by the GUI modules."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# ---- look -----------------------------------------------------------------
BG = "#f3f4f6"        # left column
CARD = "#ffffff"      # right side
INK = "#1f2933"
MUTED = "#52606d"
ACCENT = "#1f5fbf"
ACCENT_DARK = "#174a96"
BANNER_BG = "#e8f0fc"
ERROR_BG = "#fdecea"      # an error banner: light red, with a dark red bar at its left edge
ERROR_INK = "#8a1c14"
ERROR_BAR = "#B3261E"
BORDER = "#cbd2d9"
FONT = "Segoe UI"

GUI_STATE_FILE = "gui_state.json"
LEFT_WIDTH = 260  # left rail width in pixels; the sash can be dragged
IMAGE_TYPES = [
    ("Whole-slide images", "*.qptiff *.tif *.tiff"),
    ("All files", "*.*"),
]


def ui_factor(root: tk.Misc) -> float:
    """Display scaling relative to 96 dpi (1.0 = 100%, 1.25 = 125%, 1.5 = 150%), read from `tk scaling`."""
    try:
        return float(root.tk.call("tk", "scaling")) / (96 / 72)
    except (tk.TclError, ValueError):
        return 1.0


def px(root: tk.Misc, n: float) -> int:
    """A pixel length designed at 100% display scaling, made to follow the actual scaling."""
    return int(round(n * max(ui_factor(root), 1.0)))


def fit_window(screen_w: int, screen_h: int, factor: float = 1.0) -> tuple[int, int, int, int]:
    """(width, height, min width, min height) of the main window for a screen. The usual size grows with the
    display scaling (the text does), and is cut down so it fits above the taskbar on a small screen."""
    f = max(float(factor), 1.0)
    w = min(round(1120 * f), max(640, screen_w - 40))
    h = min(round(740 * f), max(480, screen_h - 100))
    return w, h, min(round(940 * f), w), min(round(620 * f), h)


def build_style(root: tk.Misc) -> None:
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass
    # Entries, the log and lists use Tk's text font, which is smaller than the 10 pt used everywhere else.
    from tkinter import font as tkfont0
    for name in ("TkTextFont", "TkDefaultFont", "TkMenuFont", "TkFixedFont"):
        try:
            tkfont0.nametofont(name, root=root).configure(**({"family": FONT} if name != "TkFixedFont" else {}), size=10)
        except tk.TclError:
            pass
    st.configure(".", font=(FONT, 10), background=BG, foreground=INK)
    st.configure("TFrame", background=BG)
    st.configure("Card.TFrame", background=CARD)
    st.configure("TLabel", background=BG, foreground=INK)
    st.configure("Muted.TLabel", foreground=MUTED)
    st.configure("Section.TLabel", font=(FONT, 10, "bold"))
    st.configure("Status.TLabel", foreground=INK)
    st.configure(
        "TButton", padding=(10, 5), background="#e4e7eb", foreground=INK,
        bordercolor=BORDER, focusthickness=2, focuscolor=ACCENT,
    )
    st.map("TButton", background=[("active", "#d3d8de"), ("disabled", "#eceff2")],
           foreground=[("disabled", "#9aa5b1")])
    # The header buttons on a short window: the same look, less height.
    st.configure("Compact.TButton", padding=(8, 2), background="#e4e7eb", foreground=INK, bordercolor=BORDER)
    st.map("Compact.TButton", background=[("active", "#d3d8de"), ("disabled", "#eceff2")],
           foreground=[("disabled", "#9aa5b1")])
    st.configure(
        "Primary.TButton", font=(FONT, 11, "bold"), padding=(14, 8),
        background=ACCENT, foreground="#ffffff", bordercolor=ACCENT,
    )
    # Off looks off: flat grey with grey text, nothing like the blue it turns into.
    st.map("Primary.TButton",
           background=[("disabled", "#dfe3e8"), ("active", ACCENT_DARK)],
           foreground=[("disabled", "#8a95a3")],
           bordercolor=[("disabled", "#dfe3e8")])
    st.configure("Link.TButton", padding=(6, 3), background=BG, bordercolor=BG, foreground=ACCENT)
    st.map("Link.TButton", background=[("active", "#e4e7eb")])
    st.configure("CardLink.TButton", padding=(4, 3), background=CARD, bordercolor=CARD, foreground=ACCENT)
    st.map("CardLink.TButton", background=[("active", "#eef2f7")])
    st.map("Link.TButton", foreground=[("disabled", "#9aa5b1")])
    st.configure("TEntry", fieldbackground="#ffffff", bordercolor=BORDER, padding=4, font=(FONT, 10))
    st.configure("Horizontal.TProgressbar", troughcolor="#dde1e6", background=ACCENT,
                 bordercolor=BORDER)
    st.configure("TNotebook", background=CARD, bordercolor=BORDER)
    st.configure("TNotebook.Tab", font=(FONT, 10), padding=(14, 7), background="#e4e7eb")
    st.map("TNotebook.Tab", background=[("selected", CARD)])
    st.configure("Card.TLabel", background=CARD)
    st.configure("CardTitle.TLabel", background=CARD, font=(FONT, 12, "bold"))
    st.configure("CardMuted.TLabel", background=CARD, foreground=MUTED, font=(FONT, 9))
    st.configure("CardValue.TLabel", background=CARD, font=(FONT, 11, "bold"))
    st.configure("Header.TLabel", background=CARD, foreground=MUTED, font=(FONT, 10))
    from tkinter import font as tkfont
    line = tkfont.Font(root=root, font=(FONT, 10)).metrics("linespace")
    st.configure("Treeview", rowheight=max(24, line + 8), fieldbackground=CARD)
    st.configure("Treeview.Heading", font=(FONT, 9, "bold"), padding=(4, 3))
    st.map("Treeview", background=[("selected", "#cfe0fa")], foreground=[("selected", INK)])
    # Slim, light scrollbars (the native look is heavy next to this window's flat style).
    for orient in ("Vertical", "Horizontal"):
        name = f"{orient}.TScrollbar"
        st.configure(name, arrowsize=10, gripcount=0, background="#c5ccd3", troughcolor="#eef0f3",
                     bordercolor="#eef0f3", lightcolor="#c5ccd3", darkcolor="#c5ccd3", relief="flat")
        st.map(name, background=[("active", "#9aa5b1"), ("pressed", "#7b8794")])
    st.configure("TPanedwindow", background=BG)
    st.configure("Sash", sashthickness=6, background="#dde1e6")
    st.configure("Card.TCheckbutton", background=CARD, foreground=INK)
    st.map("Card.TCheckbutton", background=[("active", CARD)])
    st.configure("Banner.TFrame", background=BANNER_BG)
    st.configure("Banner.TLabel", background=BANNER_BG, foreground=INK)
    st.configure("BannerError.TFrame", background=ERROR_BG)
    st.configure("BannerError.TLabel", background=ERROR_BG, foreground=INK)
    st.configure("BannerErrorHead.TLabel", background=ERROR_BG, foreground=ERROR_INK)
    st.configure("Caveat.TLabel", background=CARD, foreground=INK, font=(FONT, 10))


def wrap_to(label, length: float) -> None:
    """Set a label's wrap width only when it changes. Setting the same width again still asks Tk for a new layout, and
    the resize handlers that re-wrap text then keep each other going: that cost seconds when results arrived."""
    length = int(length)
    try:
        current = int(float(str(label.cget("wraplength")) or 0))
    except (ValueError, tk.TclError):
        current = -1
    if current != length:
        label.configure(wraplength=length)
