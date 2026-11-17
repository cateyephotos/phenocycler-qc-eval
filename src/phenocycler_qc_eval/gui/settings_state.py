"""Settings state in the main window: the status line, its tooltip, and opening the Settings panel."""

from __future__ import annotations

from ..settings import config_path, format_changes, load_settings
from .settings_dialog import LABELS, SettingsDialog


class SettingsMixin:
    """Part of App. `settings_status_var` reads "Settings: defaults" or "Settings: modified"."""

    settings_dialog: SettingsDialog | None = None
    screen_override: tuple[int, int] | None = None     # screenshots and tests pretend to have a smaller screen

    def refresh_settings_status(self) -> None:
        """Read the saved settings again and update the status line. Problems in the file go to Details (once)."""
        settings, warnings = load_settings()
        self._settings_changes = format_changes(settings, LABELS)
        self.settings_status_var.set("Settings: modified" if self._settings_changes else "Settings: defaults")
        for w in warnings:
            if w not in self._settings_warned:
                self._settings_warned.add(w)
                self.log_line(w)

    def settings_changes(self) -> list[str]:
        """What differs from the defaults, one line each, e.g. "detection.dim_fraction: 0.1 -> 0.2"."""
        return list(self._settings_changes)

    def settings_tooltip_text(self) -> str:
        return "\n".join(self._settings_changes)

    def on_settings(self) -> None:
        d = self.settings_dialog
        if d is not None and d.winfo_exists():
            d.deiconify()
            d.lift()
            d.focus_set()
            return
        settings, _warnings = load_settings()
        self.settings_dialog = SettingsDialog(
            self, settings, config_path(), run_active=self.handle is not None,
            on_saved=self.refresh_settings_status, on_close=self._settings_closed,
            screen_size=self.screen_override)

    def _settings_closed(self) -> None:
        self.settings_dialog = None

    def _tell_settings_dialog(self, busy: bool) -> None:
        d = self.settings_dialog
        if d is not None and d.winfo_exists():
            d.set_run_active(busy)
