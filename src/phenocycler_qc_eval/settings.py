"""Settings: the thresholds and marker maps every stage reads, with per-user storage."""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass, field, fields
from pathlib import Path

_DEFAULT_CONTROLS = [
    ["CD163", "CD68"], ["CD68", "CD163"], ["CD4", "CD3e"], ["CD8", "CD3e"],
    ["CD3e", "CD4"], ["FOXP3", "CD4"], ["ICOS", "CD3e"], ["LAG3", "CD3e"],
    ["TCF-1", "CD3e"], ["TOX", "CD3e"], ["CD20", "HLA-DR"], ["CD21", "CD20"],
    ["CD31", "Collagen IV"], ["Collagen IV", "SMA"], ["SMA", "Collagen IV"],
    ["Pan-Cytokeratin", "CD66"], ["CD14", "CD68"], ["MPO", "CD66"],
]
_DEFAULT_EXCLUSIVE = [
    ["CD3e", "CD20"], ["Pan-Cytokeratin", "CD45"], ["CD31", "CD20"], ["CD8", "CD20"],
    ["CD4", "CD20"], ["Pan-Cytokeratin", "CD20"], ["CD68", "CD20"], ["SMA", "CD20"],
]


@dataclass
class DetectionSettings:
    dim_fraction: float = 0.10
    blank_fraction: float = 0.05
    stain_index_low: float = 3.0
    positive_fraction_low: float = 0.005
    saturation_high: float = 0.005
    background_high_fraction: float = 0.05


@dataclass
class CrosstalkSettings:
    null_percentile: float = 95.0
    margin: float = 0.05


@dataclass
class MarkerSettings:
    # Stored as lists of [name, name] so a JSON round trip equals the defaults (R6).
    # control_markers: [marker, control marker]; exclusive_pairs: [marker, marker].
    control_markers: list = field(default_factory=lambda: [list(p) for p in _DEFAULT_CONTROLS])
    exclusive_pairs: list = field(default_factory=lambda: [list(p) for p in _DEFAULT_EXCLUSIVE])


@dataclass
class AdvancedSettings:
    n_windows: int = 200
    window_um: float = 111.328
    min_windows: int = 20
    seed: int = 0
    n_preview_windows: int = 6
    highpass_sigma: float = 2.0


@dataclass
class Settings:
    detection: DetectionSettings = field(default_factory=DetectionSettings)
    crosstalk: CrosstalkSettings = field(default_factory=CrosstalkSettings)
    markers: MarkerSettings = field(default_factory=MarkerSettings)
    advanced: AdvancedSettings = field(default_factory=AdvancedSettings)


def default_settings() -> Settings:
    return Settings()


def is_modified(s: Settings) -> bool:
    return s != default_settings()


# ---- locations -------------------------------------------------------------
def settings_dir() -> Path:
    """Per-user folder for GUI state (PQE_SETTINGS_DIR overrides it, e.g. in tests)."""
    override = os.environ.get("PQE_SETTINGS_DIR")
    if override:
        return Path(override)
    return Path.home() / ".phenocycler_qc_eval"


def config_path() -> Path:
    override = os.environ.get("PQE_CONFIG_DIR")
    if override:
        return Path(override) / "settings.json"
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Roaming"
        return root / "phenocycler_qc_eval" / "settings.json"
    return Path.home() / ".config" / "phenocycler_qc_eval" / "settings.json"


# ---- validation (ruling R6) ------------------------------------------------
def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _fraction(v):      # (0, 1]
    return _is_number(v) and 0 < v <= 1


def _positive(v):
    return _is_number(v) and v > 0


def _percentile(v):    # (0, 100]
    return _is_number(v) and 0 < v <= 100


def _count(v):         # integer >= 1
    return _is_int(v) and v >= 1


def _seed(v):          # integer >= 0
    return _is_int(v) and v >= 0


def _window_um(v):     # 1 to 2000 micrometers: a window is a few cells to a few millimeters across
    return _is_number(v) and 1 <= v <= 2000


_FRAC = (_fraction, "a fraction above 0 and at most 1")
_POS = (_positive, "a positive number")
_RULES = {
    "detection": {
        "dim_fraction": _FRAC,
        "blank_fraction": _FRAC,
        "stain_index_low": _POS,
        "positive_fraction_low": _FRAC,
        "saturation_high": _FRAC,
        "background_high_fraction": _FRAC,
    },
    "crosstalk": {
        "null_percentile": (_percentile, "a number above 0 and at most 100"),
        "margin": _POS,
    },
    "advanced": {
        "n_windows": (_count, "a whole number of at least 1"),
        "window_um": (_window_um, "a number from 1 to 2000 micrometers"),
        "min_windows": (_count, "a whole number of at least 1"),
        "seed": (_seed, "a whole number of at least 0"),
        "n_preview_windows": (_count, "a whole number of at least 1"),
        "highpass_sigma": _POS,
    },
}


def _valid_name_pairs(value) -> bool:
    return (isinstance(value, (list, tuple))
            and all(isinstance(p, (list, tuple)) and len(p) == 2
                    and all(isinstance(x, str) and x.strip() for x in p) for p in value))


def _fill_group(obj, group: str, data, warnings: list[str]) -> None:
    rules = _RULES[group]
    if not isinstance(data, dict):
        warnings.append(f"Setting group '{group}' is not a table; using defaults.")
        return
    for key, value in data.items():
        if key not in rules:
            warnings.append(f"Unknown setting '{group}.{key}' ignored.")
            continue
        check, wording = rules[key]
        if check(value):
            setattr(obj, key, float(value) if isinstance(getattr(obj, key), float) else int(value))
        else:
            warnings.append(f"Setting '{group}.{key}' must be {wording} (got {value!r}); "
                            f"using {getattr(obj, key)}.")


def settings_to_dict(s: Settings) -> dict:
    out = {}
    for f in fields(Settings):
        group = getattr(s, f.name)
        out[f.name] = {}
        for g in fields(group):
            value = getattr(group, g.name)
            out[f.name][g.name] = [list(p) for p in value] if f.name == "markers" else value
    return out


def settings_from_dict(d) -> tuple[Settings, list[str]]:
    """Build Settings from a plain dict. Bad values reset one by one, with a warning each."""
    s = default_settings()
    warnings: list[str] = []
    if not isinstance(d, dict):
        return s, ["Settings file does not hold a table; using defaults."]
    for group, data in d.items():
        if group == "markers":
            if not isinstance(data, dict):
                warnings.append("Setting group 'markers' is not a table; using defaults.")
                continue
            for key, value in data.items():
                if key not in ("control_markers", "exclusive_pairs"):
                    warnings.append(f"Unknown setting 'markers.{key}' ignored.")
                elif _valid_name_pairs(value):
                    setattr(s.markers, key, [[x.strip() for x in p] for p in value])
                else:
                    warnings.append(f"Setting 'markers.{key}' must be a list of name pairs; "
                                    "using defaults.")
        elif group in _RULES:
            _fill_group(getattr(s, group), group, data, warnings)
        else:
            warnings.append(f"Unknown setting group '{group}' ignored.")
    return s, warnings


def load_settings(path=None) -> tuple[Settings, list[str]]:
    p = Path(path) if path is not None else config_path()
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        return default_settings(), []
    except OSError as exc:
        return default_settings(), [f"Could not read settings file {p} ({exc}); using defaults."]
    try:
        # utf-8-sig accepts a file saved with a byte order mark (e.g. by Notepad).
        # UnicodeDecodeError and JSON errors are both ValueError subclasses.
        data = json.loads(raw.decode("utf-8-sig"))
    except ValueError:
        return default_settings(), [f"Settings file {p} is not valid UTF-8 JSON; using defaults."]
    return settings_from_dict(data)


def save_settings(s: Settings, path=None) -> Path:
    """Write via a temp file in the same folder, then swap it in, so a failed write
    never leaves a half-written settings file behind."""
    p = Path(path) if path is not None else config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(settings_to_dict(s), indent=2)
    fd, tmp_name = tempfile.mkstemp(dir=p.parent, prefix=p.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, p)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return p


# ---- what differs from the defaults, and checking one typed value (used by the Settings panel) ----
def changed_values(s: Settings) -> list[tuple[str, object, object]]:
    """(key, default, current) for every value that differs from the defaults, e.g. ("crosstalk.margin", 0.05, 0.1)."""
    base = default_settings()
    out = []
    for sec in fields(Settings):
        mine, theirs = getattr(s, sec.name), getattr(base, sec.name)
        for f in fields(mine):
            a, b = getattr(theirs, f.name), getattr(mine, f.name)
            if a != b:
                out.append((f"{sec.name}.{f.name}", a, b))
    return out


def _plain(value) -> str:
    if isinstance(value, list):
        return f"{len(value)} pair" + ("" if len(value) == 1 else "s")
    return str(value)


def _list_change(a, b) -> str:
    """How a marker list differs from its default: counts when the length changed, else how many pairs differ."""
    if len(a) != len(b):
        return f"{_plain(a)} -> {_plain(b)}"
    base = {tuple(p) for p in a}
    changed = sum(1 for p in b if tuple(p) not in base)
    if changed == 0:
        return f"{_plain(b)}, order changed"
    return f"{_plain(b)}, {changed} changed"


def format_changes(s: Settings, labels: dict | None = None) -> list[str]:
    """One plain line per change, e.g. "detection.dim_fraction: 0.1 -> 0.2". With `labels` (key -> name) the line
    uses the name the window shows and never the internal key: "Dim signal below: 0.1 to 0.2". Marker lists show
    their length."""
    out = []
    for key, a, b in changed_values(s):
        named = bool(labels) and key in labels
        arrow = " to " if named else " -> "
        change = _list_change(a, b) if isinstance(a, list) else f"{_plain(a)}{arrow}{_plain(b)}"
        out.append(f"{labels[key] if named else key}: {change}")
    return out


def field_error(group: str, key: str, text: str):
    """Check one typed value for a numeric setting. Returns (value, "") or (None, a plain message that says
    what is wrong and what is allowed). Whole-number settings accept whole numbers only."""
    check, wording = _RULES[group][key]
    whole = isinstance(getattr(getattr(default_settings(), group), key), int)
    raw = (text or "").strip()
    allowed = f" Allowed: {wording}."
    try:
        if "_" in raw:
            raise ValueError(raw)               # Python would accept 1_000; a person typing it made a typo
        if whole:
            value = int(raw)
        else:
            value = float(raw)
    except ValueError:
        kind = "a whole number" if whole else "a number"
        return None, f"That is not {kind}.{allowed}"
    if not check(value):
        return None, allowed.strip()
    return value, ""
