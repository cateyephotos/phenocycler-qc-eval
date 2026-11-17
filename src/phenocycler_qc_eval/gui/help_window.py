"""A window that shows the guide to reading the results (docs/how_to_read_results.md, shipped with the package).

A small reader for the guide's own markdown: headings, bullets, bold, and tables shown as one wrapped line per row
(the first cell in bold, then the rest in words). Nothing is fetched; the file is read from the package. When this copy
has no guide the window says so.
"""

from __future__ import annotations

import re
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from .theme import BG, FONT, INK

GUIDE = Path(__file__).resolve().parents[1] / "data" / "how_to_read_results.md"
MISSING = ("The guide to reading the results is not installed with this copy of the tool. "
           "It is the file docs/how_to_read_results.md in the project folder.")
_BOLD = re.compile(r"\*\*(.+?)\*\*")


def guide_text() -> str:
    try:
        return GUIDE.read_text(encoding="utf-8")
    except OSError:
        return MISSING


class HelpWindow(tk.Toplevel):
    def __init__(self, master) -> None:
        super().__init__(master)
        self.title("How to read these results")
        self.configure(bg=BG)
        self.geometry("760x640+80+60")
        self.minsize(420, 320)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.text = tk.Text(self, wrap="word", font=(FONT, 11), bg="#ffffff", fg=INK, relief="flat", padx=20, pady=14,
                            borderwidth=0, highlightthickness=0, spacing3=4)
        bar = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=bar.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        bar.grid(row=0, column=1, sticky="ns")
        self.text.tag_configure("h1", font=(FONT, 17, "bold"), spacing1=6, spacing3=8)
        self.text.tag_configure("h2", font=(FONT, 13, "bold"), spacing1=14, spacing3=4)
        self.text.tag_configure("h3", font=(FONT, 11, "bold"), spacing1=8)
        self.text.tag_configure("bold", font=(FONT, 11, "bold"))
        self.text.tag_configure("bullet", lmargin1=16, lmargin2=30)
        self.text.tag_configure("trow", lmargin1=16, lmargin2=34, spacing1=3)
        self._render(guide_text())
        self.text.configure(state="disabled")
        buttons = ttk.Frame(self, padding=(12, 8))
        buttons.grid(row=1, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text="Close", command=self.destroy).pack()
        self.bind("<Escape>", lambda _e: self.destroy())

    def _render(self, source: str) -> None:
        """Hard-wrapped lines of one paragraph (or one bullet) are joined, so the text wraps to the window."""
        para: list[str] = []
        tag: str | None = None
        table: list[list[str]] = []

        def flush() -> None:
            nonlocal para, tag, table
            if para:
                self._line(" ".join(para), tag)
            para, tag = [], None
            if table:
                self._table(table)
            table = []

        for raw in source.splitlines():
            line = raw.rstrip()
            if not line.strip():
                flush()
            elif line.startswith("|"):
                if para:
                    flush()
                cells = [c.strip() for c in line.strip("|").split("|")]
                if not all(set(c) <= set("-: ") for c in cells):     # skip the ruler under a table heading
                    table.append([c.replace("**", "") for c in cells])
            elif line.startswith("#"):
                flush()
                level = len(line) - len(line.lstrip("#"))
                self._line(line[level:].strip(), f"h{min(level, 3)}")
            elif re.match(r"^\s*[-*] ", line):
                flush()
                para, tag = ["• " + re.sub(r"^\s*[-*] ", "", line)], "bullet"
            elif line.startswith("  ") and tag == "bullet":
                para.append(line.strip())
            else:
                if tag == "bullet" or table:
                    flush()
                para.append(line.strip())

        flush()

    def _table(self, rows: list[list[str]]) -> None:
        """One wrapped line per row. A two-column table (a key) reads "Label  explanation"; a table of pairs reads "A and
        B"; a wider one names each column: "Label: heading one: value; heading two: value"."""
        head, body = rows[0], rows[1:]
        for cells in body:
            first = cells[0]
            if len(cells) == 2 and head[0] == head[1]:
                self.text.insert("end", f"{cells[0]} and {cells[1]}" + chr(10), "trow")
                continue
            self.text.insert("end", first, ("trow", "bold"))
            if len(cells) == 2:
                self.text.insert("end", "  " + cells[1] + chr(10), "trow")
            else:
                rest = "; ".join(f"{h}: {c}" for h, c in zip(head[1:], cells[1:]))
                self.text.insert("end", ": " + rest + chr(10), "trow")

    def _line(self, text: str, tag: str | None) -> None:
        text = text.replace("`", "")
        pos = 0
        for m in _BOLD.finditer(text):
            self.text.insert("end", text[pos:m.start()], tag or ())
            self.text.insert("end", m.group(1), (tag, "bold") if tag else "bold")
            pos = m.end()
        self.text.insert("end", text[pos:] + "\n", tag or ())

    def text_content(self) -> str:
        """All the words in the window (for tests)."""
        return self.text.get("1.0", "end")
