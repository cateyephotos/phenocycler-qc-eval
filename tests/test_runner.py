import ast
import json
import os
import sys
import time
from pathlib import Path

import pytest

from phenocycler_qc_eval import worker
from phenocycler_qc_eval.readers import NoNuclearChannelError, ReaderError
from phenocycler_qc_eval.results import load_results
from phenocycler_qc_eval.run import EARLY_STAGE, INCOMPLETE_MARKER, PARTIAL_STAGE, RunCancelled, RunRequest
from phenocycler_qc_eval.runner import WorkerHandle, run_request_dict, worker_env
from phenocycler_qc_eval.settings import default_settings, settings_to_dict
from phenocycler_qc_eval.tissue import TooLittleTissueError
from synth import make_synthetic_image, small_settings


def drain_until_exit(h, timeout):
    """Collect every message until the process has ended and the queue is empty."""
    msgs, end = [], time.monotonic() + timeout
    while time.monotonic() < end:
        msgs += h.poll()
        if not h.is_running():
            msgs += h.poll()
            return msgs
        time.sleep(0.05)
    h.cancel()
    raise AssertionError(f"worker still running after {timeout} s; got {[m['type'] for m in msgs]}")


def wait_for(fn, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if fn():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


# ---------------------------------------------------------------- in-process worker (fast)
def run_worker_main(tmp_path, capsys, request: dict, fake_run_qc=None, monkeypatch=None):
    if fake_run_qc is not None:
        monkeypatch.setattr("phenocycler_qc_eval.run.run_qc", fake_run_qc)
    req = tmp_path / "req.json"
    req.write_text(json.dumps(request), encoding="utf-8")
    code = worker.main([str(req)])
    cap = capsys.readouterr()
    return code, [json.loads(line) for line in cap.out.splitlines()], cap


def run_request(tmp_path, **kw):
    base = {"action": "run", "image": str(tmp_path / "x.qptiff"), "panel": None, "output_root": str(tmp_path / "o"),
            "settings": settings_to_dict(default_settings()), "nuclear_channel": None, "save_sample": False,
            "settings_warnings": []}
    base.update(kw)
    return base


def test_worker_env():
    e = worker_env()
    assert e["MPLBACKEND"] == "Agg" and e["PYTHONUNBUFFERED"] == "1"
    assert e["PYTHONIOENCODING"] == "ascii:backslashreplace"
    assert "PATH" in e or "Path" in e


def test_worker_sets_agg_before_anything_else_and_never_imports_pyplot():
    assert os.environ["MPLBACKEND"] == "Agg"
    tree = ast.parse(Path(worker.__file__).read_text(encoding="utf-8"))
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
              for a in n.names} | {getattr(n, "module", None) for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert "use" not in names and not any(x and "pyplot" in x for x in names)
    before = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and "MPLBACKEND" in ast.unparse(node):
            break
        before.append(node)
    else:
        raise AssertionError("MPLBACKEND is never set at module level")
    for node in before:                                    # nothing but the docstring, __future__ and os comes first
        ok = isinstance(node, ast.Expr) or (isinstance(node, ast.ImportFrom) and node.module == "__future__") \
            or (isinstance(node, ast.Import) and [a.name for a in node.names] == ["os"])
        assert ok, ast.unparse(node)


def test_run_request_dict_is_plain_json(tmp_path):
    req = RunRequest(tmp_path / "a.qptiff", None, tmp_path / "o", small_settings(), settings_warnings=["w"],
                     nuclear_channel="Hoechst")
    d = run_request_dict(req)
    assert json.loads(json.dumps(d)) == d
    assert d["action"] == "run" and d["nuclear_channel"] == "Hoechst" and d["settings_warnings"] == ["w"]
    assert d["panel"] is None and d["settings"] == settings_to_dict(small_settings())


def test_worker_started_comes_from_on_started_and_partials_say_early_or_not(tmp_path, capsys, monkeypatch):
    def fake(req, progress):
        out = tmp_path / "run1"
        out.mkdir()
        req.on_started(out)
        progress("open", 0.02, "Opening image")
        progress(EARLY_STAGE, 0.3, "early")
        progress(PARTIAL_STAGE, 0.6, "final")
        return out, {"n": 1}

    code, msgs, _ = run_worker_main(tmp_path, capsys, run_request(tmp_path), fake, monkeypatch)
    kinds = [m["type"] for m in msgs]
    assert code == 0 and kinds[0] == "started" and kinds[-1] == "done"
    assert [m["early"] for m in msgs if m["type"] == "partial"] == [True, False]
    assert msgs[0]["out_dir"] == str(tmp_path / "run1") and msgs[-1]["summary"] == {"n": 1}
    progress_msgs = [m for m in msgs if m["type"] == "progress"]
    assert progress_msgs[0] == {"type": "progress", "stage": "open", "fraction": 0.02, "message": "Opening image"}


def test_worker_done_is_only_sent_after_run_qc_returns(tmp_path, capsys, monkeypatch):
    def fake(req, progress):
        progress("done", 1.0, "Finished")          # run_qc's own last progress call
        return tmp_path, {}

    _, msgs, _ = run_worker_main(tmp_path, capsys, run_request(tmp_path), fake, monkeypatch)
    assert [m["type"] for m in msgs] == ["progress", "done"]


@pytest.mark.parametrize("exc, kind, code", [
    (NoNuclearChannelError(["CD3e", "CD20"]), "no_nuclear_channel", 2),
    (TooLittleTissueError(9, 20), "too_little_tissue", 2),
    (ReaderError("cannot read"), "reader", 2),
    (ValueError("boom"), "unexpected", 1),
])
def test_worker_error_kinds_and_exit_codes(tmp_path, capsys, monkeypatch, exc, kind, code):
    def fake(req, progress):
        raise exc

    got, msgs, cap = run_worker_main(tmp_path, capsys, run_request(tmp_path), fake, monkeypatch)
    last = msgs[-1]
    assert got == code and last["type"] == "error" and last["kind"] == kind
    assert ("channels" in last) == (kind == "no_nuclear_channel")
    if kind == "no_nuclear_channel":
        assert last["channels"] == ["CD3e", "CD20"]
    if kind == "too_little_tissue":
        assert "found 9 tissue windows, need at least 20" in last["message"]
    if kind == "unexpected":
        assert "Traceback" in cap.err


def test_worker_reports_cancel_as_cancelled_not_error(tmp_path, capsys, monkeypatch):
    def fake(req, progress):
        req.on_started(tmp_path / "r")
        raise RunCancelled()

    code, msgs, _ = run_worker_main(tmp_path, capsys, run_request(tmp_path), fake, monkeypatch)
    assert code == 130 and msgs[-1]["type"] == "cancelled" and msgs[-1]["out_dir"] == str(tmp_path / "r")
    assert not any(m["type"] == "error" for m in msgs)


def test_worker_stdout_is_json_lines_only_and_ascii(tmp_path, capsys, monkeypatch):
    def fake(req, progress):
        print("noise from a library")                    # must not reach the JSON stream
        progress("open", 0.1, "café α")
        raise ReaderError("naïve — bad file")

    code, msgs, cap = run_worker_main(tmp_path, capsys, run_request(tmp_path), fake, monkeypatch)
    cap.out.encode("ascii")
    assert "noise from a library" in cap.err and "noise" not in cap.out
    assert msgs[0]["message"] == "café α" and "\\u00e9" in cap.out
    assert code == 2


def test_worker_settings_warnings_reach_run_qc(tmp_path, capsys, monkeypatch):
    seen = {}

    def fake(req, progress):
        seen["w"], seen["panel"], seen["nuc"] = req.settings_warnings, req.panel, req.nuclear_channel
        return tmp_path, {}

    run_worker_main(tmp_path, capsys, run_request(tmp_path, settings_warnings=["bad value"], nuclear_channel="H"),
                    fake, monkeypatch)
    assert seen == {"w": ["bad value"], "panel": None, "nuc": "H"}


def test_worker_bad_settings_in_request_fall_back_with_a_warning(tmp_path, capsys, monkeypatch):
    seen = {}

    def fake(req, progress):
        seen["w"] = req.settings_warnings
        return tmp_path, {}

    bad = settings_to_dict(default_settings())
    bad["advanced"]["seed"] = -4
    run_worker_main(tmp_path, capsys, run_request(tmp_path, settings=bad), fake, monkeypatch)
    assert any("seed" in w for w in seen["w"])


# ---------------------------------------------------------------- child process (spawns python)
def test_channels_action(synthetic_qptiff):
    h = WorkerHandle({"action": "channels", "image": str(synthetic_qptiff)})
    h.start()
    msgs = drain_until_exit(h, timeout=60)
    assert msgs[-1]["names"][0] == "DAPI" and h.returncode == 0


def test_unknown_action_is_an_unexpected_error_and_stderr_becomes_log():
    h = WorkerHandle({"action": "bogus"})
    h.start()
    msgs = drain_until_exit(h, timeout=60)
    assert msgs[-1]["type"] == "error" and msgs[-1]["kind"] == "unexpected" and h.returncode == 1
    assert any(m["type"] == "log" for m in msgs)


def test_handle_before_start_is_idle_and_cancel_is_harmless():
    h = WorkerHandle({"action": "channels", "image": "x"})
    assert h.poll() == [] and not h.is_running() and h.returncode is None and h.elapsed_s() == 0.0
    h.cancel()                                          # cancelling something never started is harmless


@pytest.mark.slow
def test_worker_streams_started_partial_then_done(synthetic_qptiff, tmp_path):
    h = WorkerHandle(run_request_dict(RunRequest(synthetic_qptiff, None, tmp_path, small_settings())))
    h.start()
    kinds = [m["type"] for m in drain_until_exit(h, timeout=180)]
    assert kinds[0] == "started" and kinds[-1] == "done" and h.returncode == 0
    assert kinds.index("partial") < kinds.index("done")
    assert h.out_dir is not None and load_results(h.out_dir).status == "complete"


@pytest.mark.slow
def test_no_dapi_error_carries_channels(tmp_path):                    # Review Focus 2
    p = make_synthetic_image(tmp_path / "x.qptiff", channels=["CD3e", "CD20"])
    h = WorkerHandle(run_request_dict(RunRequest(p, None, tmp_path / "o", small_settings())))
    h.start()
    last = drain_until_exit(h, timeout=120)[-1]
    assert last["type"] == "error" and last["kind"] == "no_nuclear_channel" and last["channels"] == ["CD3e", "CD20"]
    assert h.returncode == 2


@pytest.fixture
def slow_request(synthetic_qptiff, tmp_path, monkeypatch):
    monkeypatch.setenv("PQE_TEST_SLEEP_AFTER_PARTIAL", "300")
    return run_request_dict(RunRequest(synthetic_qptiff, None, tmp_path / "o", small_settings()))


@pytest.mark.slow
def test_cancel_after_partial_keeps_antibody_results(slow_request):
    h = WorkerHandle(slow_request)
    h.start()
    seen = []
    wait_for(lambda: seen.extend(h.poll()) or any(m["type"] == "partial" and m["early"] is False for m in seen), 180)
    h.cancel()
    r = load_results(h.out_dir)
    assert not h.is_running() and r.status == "cancelled" and len(r.detection) > 0 and r.crosstalk is None
    assert (h.out_dir / INCOMPLETE_MARKER).read_text(encoding="utf-8").startswith("cancelled by user at ")
    assert any(m["type"] == "cancelled" for m in h.poll())


@pytest.mark.slow
def test_cancel_does_not_touch_a_finished_run(synthetic_qptiff, tmp_path):
    h = WorkerHandle(run_request_dict(RunRequest(synthetic_qptiff, None, tmp_path, small_settings())))
    h.start()
    drain_until_exit(h, timeout=180)
    h.cancel()                                          # already finished: nothing changes
    assert load_results(h.out_dir).status == "complete" and not (h.out_dir / INCOMPLETE_MARKER).exists()


# ---------------------------------------------------------------- review round 1 (worker death, races, cleanup)
DIE_SCRIPT = """
import os, sys
sys.stderr.write('native crash detail\\n'); sys.stderr.flush()
print('{"type": "progress", "stage": "open", "fraction": 0.1, "message": "x"}', flush=True)
os._exit(3)
"""
DONE_THEN_LINGER_SCRIPT = """
import json, time
print(json.dumps({'type': 'done', 'out_dir': None}), flush=True)
time.sleep(30)
"""
SLEEP_SCRIPT = "import time\ntime.sleep(60)\n"


def _script_handle(tmp_path, monkeypatch, body):
    """A handle whose child is a tiny script standing in for the worker module."""
    (tmp_path / "pqe_child.py").write_text(body, encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    return WorkerHandle({"action": "x"}, module="pqe_child")


def test_worker_that_dies_without_a_final_message_is_reported(tmp_path, monkeypatch):
    h = _script_handle(tmp_path, monkeypatch, DIE_SCRIPT)
    h.start()
    msgs = drain_until_exit(h, timeout=30)
    last = msgs[-1]
    assert last["type"] == "error" and last["kind"] == "unexpected"
    assert "Worker stopped unexpectedly (exit code 3)" in last["message"]
    assert "native crash detail" in last["message"]
    assert sum(m["type"] == "error" for m in msgs) == 1


def test_normal_error_exit_does_not_add_a_second_unexpected_error():
    h = WorkerHandle({"action": "bogus"})
    h.start()
    msgs = drain_until_exit(h, timeout=60)
    assert [m["type"] for m in msgs if m["type"] == "error"] == ["error"]
    assert "Worker stopped unexpectedly" not in msgs[-1]["message"]


def test_cancel_after_done_queues_no_cancelled(tmp_path, monkeypatch):
    h = _script_handle(tmp_path, monkeypatch, DONE_THEN_LINGER_SCRIPT)
    h.start()
    seen = []
    wait_for(lambda: seen.extend(h.poll()) or any(m["type"] == "done" for m in seen), 30)
    h.cancel()                                          # the process is still alive (teardown), done already sent
    seen += h.poll()
    assert [m["type"] for m in seen] == ["done"] and not h.is_running()


def test_mark_cancelled_leaves_a_finished_run_alone(tmp_path):
    folder = tmp_path / "run"
    folder.mkdir()
    (folder / INCOMPLETE_MARKER).write_text("still here\n", encoding="utf-8")
    (folder / "run_info.json").write_text(json.dumps({"status": "complete"}), encoding="utf-8")
    h = WorkerHandle({})
    h.out_dir = folder
    h._mark_cancelled()
    assert json.loads((folder / "run_info.json").read_text())["status"] == "complete"
    assert (folder / INCOMPLETE_MARKER).read_text() == "still here\n"


def test_mark_cancelled_marks_a_running_run(tmp_path):
    folder = tmp_path / "run"
    folder.mkdir()
    (folder / INCOMPLETE_MARKER).write_text("running\n", encoding="utf-8")
    (folder / "run_info.json").write_text(json.dumps({"status": "running"}), encoding="utf-8")
    h = WorkerHandle({})
    h.out_dir = folder
    h._mark_cancelled()
    assert json.loads((folder / "run_info.json").read_text())["status"] == "cancelled"


def test_close_is_idempotent_and_stops_a_running_worker(tmp_path, monkeypatch):
    h = _script_handle(tmp_path, monkeypatch, SLEEP_SCRIPT)
    h.start()
    h.close()
    h.close()
    assert not h.is_running() and h.returncode is not None
    assert h._proc.stdout.closed and h._proc.stderr.closed
    assert h._request_file is None or not h._request_file.exists()


def test_close_before_start_and_after_finish_is_harmless():
    WorkerHandle({"action": "bogus"}).close()
    h = WorkerHandle({"action": "bogus"})
    h.start()
    drain_until_exit(h, timeout=60)
    h.close()
    h.close()
    assert h._proc.stdout.closed


def test_pipes_are_closed_once_the_process_has_ended():
    h = WorkerHandle({"action": "bogus"})
    h.start()
    drain_until_exit(h, timeout=60)
    assert h._proc.stdout.closed and h._proc.stderr.closed


def test_second_start_raises(tmp_path, monkeypatch):
    h = _script_handle(tmp_path, monkeypatch, SLEEP_SCRIPT)
    h.start()
    try:
        with pytest.raises(RuntimeError):
            h.start()
    finally:
        h.close()


def test_elapsed_stops_when_the_process_ends_without_asking_is_running():
    h = WorkerHandle({"action": "bogus"})
    h.start()
    wait_for(lambda: h._proc.poll() is not None, 60)
    wait_for(lambda: not any(t.is_alive() for t in h._threads), 10)
    first = h.elapsed_s()
    time.sleep(0.3)
    assert h.elapsed_s() == first


def test_worker_report_survives_a_gone_parent():
    class Gone:
        def write(self, text):
            raise BrokenPipeError("parent closed the pipe")

        def flush(self):
            raise BrokenPipeError("parent closed the pipe")

    assert worker._report(ValueError("boom"), worker._Emitter(Gone()), {}) == 1
    assert worker._report(RunCancelled(), worker._Emitter(Gone()), {}) == 130


# ---------------------------------------------------------------- task 16 extras (N1, N2)
class _LaggingProc:
    """A process that has died but whose poll() has not noticed yet: the instant a cancel can collide with a death."""
    returncode = 7
    stdout = None
    stderr = None

    def __init__(self):
        self.terminated = False

    def poll(self):
        return 7 if self.terminated else None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 7


class _CancelWhenRead:
    """Stands in for the stderr tail: reading it runs a cancel(), which is how the collision is forced."""

    def __init__(self, handle):
        self.handle = handle
        self.fired = False

    def __iter__(self):
        if not self.fired:
            self.fired = True
            self.handle.cancel()
        return iter(())


def test_death_at_the_instant_of_cancel_queues_one_ending(tmp_path):
    h = WorkerHandle({})
    h._proc = _LaggingProc()
    h._readers_left = 1
    h._stderr_tail = _CancelWhenRead(h)
    h._reader_done()
    kinds = [m["type"] for m in h.poll()]
    assert len(kinds) == 1 and kinds[0] in ("error", "cancelled"), kinds


def test_start_after_close_raises():
    h = WorkerHandle({"action": "bogus"})
    h.close()
    with pytest.raises(RuntimeError):
        h.start()
    assert h._proc is None


GRANDCHILD_SCRIPT = """
import subprocess, sys
subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(40)'])
print('{"type": "log", "message": "parent leaving"}', flush=True)
"""


def test_close_does_not_hang_on_a_pipe_still_held_open(tmp_path, monkeypatch):
    """The child exits but a grandchild keeps the pipe open, so the readers stay blocked."""
    import threading

    h = _script_handle(tmp_path, monkeypatch, GRANDCHILD_SCRIPT)
    h.start()
    wait_for(lambda: h._proc.poll() is not None, 30)
    done = threading.Event()
    threading.Thread(target=lambda: (h.close(), done.set()), daemon=True).start()
    try:
        assert done.wait(15), "close() is stuck on a blocked reader"
    finally:
        import subprocess
        if sys.platform == "win32":                       # the grandchild would otherwise outlive the test
            subprocess.run(["taskkill", "/F", "/T", "/FI", f"PPID eq {h._proc.pid}"], capture_output=True)
