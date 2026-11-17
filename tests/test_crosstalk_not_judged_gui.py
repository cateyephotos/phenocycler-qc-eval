"""X1 in the window: banner and Crosstalk tab say crosstalk could not be judged (GUI)."""
from __future__ import annotations

import json
import tkinter as tk

import pytest
from conftest import new_app
from synth import PROTO, copy_only

from phenocycler_qc_eval.gui.app import App
from phenocycler_qc_eval.results import NOT_JUDGED_MESSAGE

pytestmark = pytest.mark.gui


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("PQE_SETTINGS_DIR", str(tmp_path_factory.mktemp("settings")))
    mp.setattr(App, "WIRED", {"run": True, "settings": True, "example": True})
    a = new_app()
    a.update()
    yield a
    try:
        a.destroy()
    except tk.TclError:
        pass
    mp.undo()


def test_banner_and_tab_say_crosstalk_not_judged(app, tmp_path):
    run = copy_only(PROTO, tmp_path, ["stain_qc_channels.csv"])
    (run / "run_info.json").write_text(json.dumps({
        "status": "complete", "crosstalk_state": "off", "crosstalk_message": NOT_JUDGED_MESSAGE,
        "saturation_checked": True, "unmatched": [], "settings": {}}))
    app.load_past_run(run)
    app.update()
    banner = app.banner_text()
    assert "could not be judged" in banner
    assert "no pairs flagged" not in banner and "0 flagged" not in banner
    tab = app.crosstalk_tab
    assert tab._message.cget("text") == NOT_JUDGED_MESSAGE
    assert not tab.template_button.winfo_ismapped()
