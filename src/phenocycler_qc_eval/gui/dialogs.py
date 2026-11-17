"""Small modal dialogs. Today: the nuclear-channel picker."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

PROMPT = "This image has no channel named DAPI. Which channel is the nuclear stain?"
_NUCLEAR_WORDS = ("dapi", "hoechst", "nuc", "dna")


def likely_nuclear(channels: list[str]) -> int | None:
    """Index of the first channel whose name looks like a nuclear stain (DAPI, Hoechst, nuc..., DNA), else None."""
    for i, name in enumerate(channels):
        if any(word in str(name).lower() for word in _NUCLEAR_WORDS):
            return i
    return None


class NuclearChannelDialog(tk.Toplevel):
    """Pick the nuclear channel from a list. `result` is the chosen name, or None when cancelled."""

    def __init__(self, master, channels: list[str]) -> None:
        super().__init__(master)
        self.result: str | None = None
        self.channels = [str(c) for c in channels]
        self.title("Choose the nuclear channel")
        self.resizable(False, True)
        self.transient(master)
        body = ttk.Frame(self, padding=16)
        body.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        self._prompt = ttk.Label(body, text=PROMPT, wraplength=360, justify="left")
        self._prompt.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        self.channel_list = tk.Listbox(
            body, height=min(max(len(self.channels), 4), 12), width=34, exportselection=False,
            activestyle="none", selectmode="browse")
        for name in self.channels:
            self.channel_list.insert("end", name)
        bar = ttk.Scrollbar(body, orient="vertical", command=self.channel_list.yview)
        self.channel_list.configure(yscrollcommand=bar.set)
        self.channel_list.grid(row=1, column=0, sticky="nsew")
        bar.grid(row=1, column=1, sticky="ns")

        buttons = ttk.Frame(body)
        buttons.grid(row=2, column=0, columnspan=2, sticky="e", pady=(12, 0))
        self.use_button = ttk.Button(buttons, text="Use this channel", style="Primary.TButton",
                                     command=self._use, state="disabled")
        self.cancel_button = ttk.Button(buttons, text="Cancel", command=self._cancel)
        self.use_button.pack(side="left")
        self.cancel_button.pack(side="left", padx=(8, 0))

        self.channel_list.bind("<<ListboxSelect>>", self._on_select)
        self.channel_list.bind("<Double-Button-1>", lambda _e: self._use())
        self.use_button.bind("<Return>", lambda _e: self._use())      # Enter acts on the focused control only
        self.cancel_button.bind("<Return>", lambda _e: self._cancel())
        self.channel_list.bind("<Return>", lambda _e: self._use())
        self.bind("<Escape>", lambda _e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        guess = likely_nuclear(self.channels)
        if guess is not None:                      # the likely nuclear channel is already chosen; Enter accepts it
            self.channel_list.selection_set(guess)
            self.channel_list.see(guess)
            self.channel_list.activate(guess)
            self.use_button.configure(state="normal")
        self.channel_list.focus_set()

    def prompt_text(self) -> str:
        return str(self._prompt.cget("text"))

    def selected_channel(self) -> str | None:
        return self._selected()

    def _selected(self) -> str | None:
        sel = self.channel_list.curselection()
        return self.channels[sel[0]] if sel else None

    def _on_select(self, _event=None) -> None:
        self.use_button.configure(state="normal" if self._selected() else "disabled")

    def _use(self) -> None:
        choice = self._selected()
        if choice is None:
            return                       # Enter or a double-click with nothing chosen does nothing
        self.result = choice
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()


def ask_nuclear_channel(master, channels: list[str]) -> str | None:
    """Show the picker and wait for it. Returns the chosen channel name, or None when cancelled."""
    dlg = NuclearChannelDialog(master, channels)
    try:
        dlg.wait_visibility()             # a grab needs a visible window; without it the dialog would not be modal
        dlg.grab_set()
    except tk.TclError:
        pass                              # a grab can fail while the window is still being mapped
    master.wait_window(dlg)
    return dlg.result


class ConfirmDiscardDialog(tk.Toplevel):
    """"Discard changes?" with two buttons. `result` is True for Discard and False for Keep editing (the default,
    also what Escape and the window's close button mean)."""

    PROMPT = "Discard changes?"
    DETAIL = "You changed some settings and did not save them. Closing now loses those changes."

    def __init__(self, master) -> None:
        super().__init__(master)
        self.result = False
        self.title("Discard changes?")
        self.resizable(False, False)
        try:
            self.transient(master)
        except tk.TclError:
            pass
        body = ttk.Frame(self, padding=18)
        body.grid(row=0, column=0, sticky="nsew")
        self._prompt = ttk.Label(body, text=f"{self.PROMPT}\n{self.DETAIL}", wraplength=380, justify="left")
        self._prompt.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 14))
        buttons = ttk.Frame(body)
        buttons.grid(row=1, column=0, sticky="e")
        self.keep_button = ttk.Button(buttons, text="Keep editing", style="Primary.TButton", command=self._keep)
        self.discard_button = ttk.Button(buttons, text="Discard", command=self._discard)
        self.keep_button.pack(side="left")
        self.discard_button.pack(side="left", padx=(8, 0))
        self.keep_button.bind("<Return>", lambda _e: self._keep())
        self.discard_button.bind("<Return>", lambda _e: self._discard())
        self.bind("<Escape>", lambda _e: self._keep())
        self.protocol("WM_DELETE_WINDOW", self._keep)
        self.keep_button.focus_set()                   # Enter keeps the user's work

    def prompt_text(self) -> str:
        return str(self._prompt.cget("text"))

    def _keep(self) -> None:
        self.result = False
        self.destroy()

    def _discard(self) -> None:
        self.result = True
        self.destroy()


def ask_discard_changes(master) -> bool:
    """Ask and wait. True = Discard, False = Keep editing."""
    dlg = ConfirmDiscardDialog(master)
    try:
        dlg.wait_visibility()
        dlg.grab_set()
    except tk.TclError:
        pass
    master.wait_window(dlg)
    return dlg.result
