"""How much same-cycle leak does the crosstalk check need before it flags a pair?

Writes a known fraction of one channel into another (the Task 18 judge's `inject`), runs the analysis exactly as
the judge does, and records the two crosstalk measures against their cut-offs for each dose. It is a slow,
one-off measurement (about 3 minutes per dose on the example crop), so it is NOT part of the test suite.

    python scripts/crosstalk_dose_series.py --out docs/crosstalk_dose_series.csv

The crop is read only and is never written to. The result describes this one section and this one pair of
channels; it is not a general guarantee of sensitivity.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "tests"), str(REPO / "src")]

# (source, target): both in the same cycle, different fluors, neither near-blank in the example crop.
PAIRS = [("ICOS", "MPO"), ("CD3e", "CD38")]
DOSES = [0, 5, 10, 20, 30, 50]            # percent of the source's pixels added to the target; 0 is the baseline
FIELDS = ["pair", "source", "target", "kind", "dose_percent", "source_signal", "target_signal",
          "texture", "texture_cut", "asymmetry", "asymmetry_cut", "flagged", "flag_reason"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--crop", default=str(REPO / "examples" / "tonsil31_crop.ome.tif"))
    ap.add_argument("--out", default=str(REPO / "docs" / "crosstalk_dose_series.csv"))
    ap.add_argument("--scratch", default=str(REPO / "runs" / "dose_series"))
    a = ap.parse_args(argv)

    from synth import inject, judge_run                       # noqa: E402  (tests/synth.py)

    from phenocycler_qc_eval.example import example_panel
    from phenocycler_qc_eval.layout import load_layout
    from phenocycler_qc_eval.readers import open_image
    from phenocycler_qc_eval.settings import default_settings
    from phenocycler_qc_eval.tissue import sample_windows

    crop, scratch = Path(a.crop), Path(a.scratch)
    if not crop.exists():
        print(f"The example crop was not found: {crop}")
        return 2
    layout = load_layout(example_panel())
    with open_image(crop) as src:
        sample = sample_windows(src, default_settings().advanced)
    print(f"windows: {len(sample.pixels)}", flush=True)

    rows = []
    baseline = None                                            # one unmodified run serves every pair
    for source, target in PAIRS:
        for dose in DOSES:
            smp = inject(sample, leak={(source, target): dose / 100.0}) if dose else sample
            if dose == 0 and baseline is not None:
                run = baseline
            else:
                run = judge_run(smp, layout, scratch / f"{source}_to_{target}_{dose}")
                baseline = run if dose == 0 else baseline
            row = run.pair(source, target)
            rows.append({
                "pair": f"{source} -> {target}", "source": source, "target": target, "kind": row["kind"],
                "dose_percent": dose, "source_signal": f"{run.channels.loc[source, 'signal_p99_5']:.0f}",
                "target_signal": f"{run.channels.loc[target, 'signal_p99_5']:.0f}",
                "texture": f"{row['highpass_corr']:.3f}", "texture_cut": f"{row['highpass_cut']:.3f}",
                "asymmetry": f"{row['asymmetry']:.3f}", "asymmetry_cut": f"{row['asym_cut']:.3f}",
                "flagged": bool(row["flag"]), "flag_reason": str(row["flag_reason"]).replace("nan", ""),
            })
            print(rows[-1]["pair"], dose, "texture", rows[-1]["texture"], "/", rows[-1]["texture_cut"],
                  "asym", rows[-1]["asymmetry"], "/", rows[-1]["asymmetry_cut"], "flagged", rows[-1]["flagged"],
                  flush=True)
            # written after every dose, so a stopped run keeps what it measured
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            with open(a.out, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=FIELDS)
                w.writeheader()
                w.writerows(rows)
    print("wrote", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
