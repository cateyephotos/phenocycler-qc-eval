"""`pqe`: run the staining QC from a terminal. All output is plain ASCII.

Exit codes: 0 done, 2 a problem the user can fix (message on stderr), 130 cancelled with Ctrl+C.
`pqe --download-example` fetches the example image first (see example.py).
Figures are drawn with matplotlib's FigureCanvasAgg only; the CLI never touches pyplot or a GUI backend, and it
does not set MPLBACKEND (no environment variable is needed or changed).
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace
from pathlib import Path


def _ascii(text: str) -> str:
    return str(text).encode("ascii", "replace").decode("ascii")


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def _seed(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must be 0 or more")
    return value


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pqe",
        description="Staining QC for PhenoCycler images: which antibodies need a different concentration, "
                    "and which channels leak into each other.")
    p.add_argument("image", nargs="?", help="QPTIFF, OME-TIFF, or a Xenium output folder")
    p.add_argument("--panel", metavar="FILE", help="panel file (.xpd) or layout CSV (marker,cycle,fluor); "
                   "a lone .xpd next to the image is used when this is left out")
    p.add_argument("--out", metavar="DIR", help="where results go (default: qc_results next to the image); "
                   "each run gets its own subfolder")
    p.add_argument("--settings", metavar="FILE", help="settings file (default: your saved app settings)")
    p.add_argument("--nuclear-channel", metavar="NAME", help="name of the nuclear channel, if it is not "
                   "called DAPI, Hoechst or DNA")
    p.add_argument("--n-windows", type=_positive_int, metavar="N", help="number of tissue windows to sample")
    p.add_argument("--seed", type=_seed, metavar="N", help="random seed for choosing windows")
    p.add_argument("--save-sample", action="store_true", help="also save the sampled windows (sample.npz)")
    p.add_argument("--download-example", action="store_true",
                   help="get the example image (about 1 mm of a 32-channel tonsil scan) and its panel file, then stop")
    p.add_argument("--from", dest="example_source", metavar="LINK_OR_FILE",
                   help="with --download-example: take the example from this link or file instead of the release")
    p.add_argument("--layout-template", metavar="IMAGE",
                   help="write <image name>_layout.csv next to IMAGE, to fill in, and stop")
    return p


def _layout_template(image: Path) -> int:
    from .layout import write_layout_template
    from .readers import open_image
    from .run import image_stem

    with open_image(image) as src:
        channels = list(src.channels)
    path = write_layout_template(channels, Path(image).parent / f"{image_stem(image)}_layout.csv")
    print(f"Wrote {_ascii(path)}")
    print("Fill in the cycle and fluor columns, then run pqe with --panel pointing at it.")
    return 0


def _download_example(source) -> int:
    from . import example

    state = {"shown": -1}

    def progress(done: int, total) -> None:
        step = done * 10 // total if total else done // 10_000_000
        if step != state["shown"]:
            state["shown"] = step
            print(f"Downloading the example: {done / 1e6:.0f}" + (f" of {total / 1e6:.0f}" if total else "") + " MB",
                  flush=True)

    try:
        image, panel = example.download_example(progress, source=source)
    except Exception as exc:               # noqa: BLE001 - offline, not published yet, or a damaged file
        code = getattr(exc, "code", None)
        reason = f"HTTP {code}" if code else f"{type(exc).__name__}: {exc}"
        print(f"Error: the example image could not be downloaded ({_ascii(reason)}). "
              f"{_ascii(example.manual_instructions())}", file=sys.stderr)
        return 2
    print(f"Example image: {_ascii(image)}")
    print(f"Panel file:    {_ascii(panel)}")
    print("Run it with:")
    print(f'  pqe "{_ascii(image)}" --panel "{_ascii(panel)}" --out qc_example_results')
    return 0


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    # Everything below can reach the engine; import it here so --help stays instant.
    from .readers import ReaderError
    from .run import RunCancelled
    from .tissue import TooLittleTissueError

    started: dict = {}
    try:
        if args.download_example:
            return _download_example(args.example_source)
        if args.layout_template:
            return _layout_template(Path(args.layout_template))
        if not args.image:
            print("Error: give the image to check, for example: pqe scan.qptiff", file=sys.stderr)
            return 2
        return _run(args, started)
    except (RunCancelled, KeyboardInterrupt):
        where = f" Partial results are in {_ascii(started['out'])}" if "out" in started else ""
        print(f"Cancelled.{where}", file=sys.stderr)
        return 130
    except (ReaderError, TooLittleTissueError) as exc:
        where = f" The run folder is {_ascii(started['out'])}" if "out" in started else ""
        text = _ascii(exc).rstrip().rstrip(".")
        print(f"Error: {text}.{where}" if where else f"Error: {text}", file=sys.stderr)
        return 2


def _run(args, started: dict) -> int:
    from .advice import headline
    from .results import load_results
    from .run import RunRequest, find_lone_xpd, run_qc
    from .settings import load_settings

    image = Path(args.image)
    settings, warnings = load_settings(args.settings)
    for w in warnings:
        print(f"Warning: {_ascii(w)}")
    if args.n_windows is not None or args.seed is not None:
        adv = settings.advanced
        adv = replace(adv, n_windows=args.n_windows or adv.n_windows,
                      seed=adv.seed if args.seed is None else args.seed)
        settings = replace(settings, advanced=adv)

    panel = Path(args.panel) if args.panel else None
    if panel is None:
        panel = find_lone_xpd(image)
        if panel is not None:
            print(f"Using panel file {_ascii(panel.name)} (the only .xpd next to the image).")
        else:
            print("No panel file: crosstalk will be off. (Use --panel, or --layout-template to make a CSV.)")
    out_root = Path(args.out) if args.out else image.parent / "qc_results"

    state = {"stage": None, "shown": -1.0}
    began = time.monotonic()

    def progress(stage: str, fraction: float, message: str) -> None:
        if stage != state["stage"] or fraction - state["shown"] >= 0.05 or fraction >= 1.0:
            state["stage"], state["shown"] = stage, fraction
            print(f"[{fraction * 100:3.0f}%, {time.monotonic() - began:5.0f} s] {_ascii(message)}", flush=True)

    req = RunRequest(image, panel, out_root, settings, nuclear_channel=args.nuclear_channel,
                     save_sample=args.save_sample, settings_warnings=list(warnings),
                     on_started=lambda folder: started.__setitem__("out", folder))
    out, _summary = run_qc(req, progress)
    r = load_results(out)
    print()
    print(_ascii(headline(r).text))
    print(f"Report: {_ascii(out / 'STAIN_QC_REPORT.md')}")
    print(f"Results folder: {_ascii(out)}")
    return 0


if __name__ == "__main__":   # python -m phenocycler_qc_eval.cli
    raise SystemExit(main())
