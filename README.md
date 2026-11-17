# Phenocycler QC Eval

Phenocycler QC Eval checks the staining quality of a PhenoCycler (CODEX-style) multiplex image. For each antibody
it measures the signal level, the stain index (how well stained pixels stand out), the share of stained pixels and the
share of saturated pixels. It also looks for crosstalk between channels.

For each antibody you get one plain **Next run** instruction, such as "Raise concentration" or "Keep", with the
evidence beside it. The tool gives directions only. It never gives a dilution or an exposure time.

It comes as a window (`pqe-gui`) and a command-line tool (`pqe`). Both read the same settings and write the same
results.

The cut-offs behind the calls are lab-defined and were tuned on a tonsil section. They are not published standards.
Use a call as a pointer to where to look.

## Install

You need Python 3.11 or newer.

    pip install git+https://github.com/cateyephotos/phenocycler-qc-eval

The repository is not published yet. Until it is, install from a local copy instead. In the folder that holds
`pyproject.toml`:

    pip install -e .

Both give you the two commands `pqe` and `pqe-gui`.

## Try the example

The example is a 1 mm piece of a 32-channel tonsil scan (about 83 MB). It contains B cell follicles, a T cell
zone and epithelium, and it shows a known carryover from TOX into CD163.

> **Not yet available.** The example image has not been published yet, so `pqe --download-example` will report
> that the download is missing. Use your own section in the meantime (see below).

1. **Get the example.** In a terminal: `pqe --download-example`. It saves the image and the matching panel file
   in your per-user folder and prints both paths. If the download is not possible (offline, or the file is not
   published yet), it tells you the link to download by hand and the folder to put the file in. You can also
   point it at a copy you already have: `pqe --download-example --from path\to\tonsil31_crop.ome.tif`.
2. **Run it from the command line.** Use the command the previous step printed, for example
   `pqe <image> --panel <panel file> --out qc_example_results`. It takes a few minutes. Open
   `STAIN_QC_REPORT.md` in the new run folder, or read the summary printed at the end.
3. **Or open the window.** Start `pqe-gui` and press **Try the example**. It gets the image (once), fills in the
   Image and Panel boxes and starts the run. Results fill in as they are ready, and they are saved in the
   `qc_results` folder next to the example image (the status line names the exact run folder).
4. **Load the result later.** In the window, press **Load a past run** and choose the run folder
   (`<image_stem>_<YYYYMMDD-HHMMSS>`).

The example has fewer tissue windows than a full scan, so the report says the results rest on fewer windows
than usual.

## Check your own section

In the window: press **Choose file** (a `.qptiff`, `.tif` or `.tiff` image) or **Choose folder** (a Xenium output
folder). If a panel file (`.xpd`) sits next to the image, it is filled in for you. Press **Run**. The Titration
tab fills in first, and crosstalk follows. **Cancel** stops the run and keeps what was saved.

From the command line:

    pqe path\to\scan.qptiff --panel path\to\panel.xpd --out qc_results

The results go into a new folder, `<image name>_<date>-<time>`, inside the output folder. Nothing from an earlier
run is overwritten.

Useful options:

| Option | What it does |
|---|---|
| `--panel FILE` | A `.xpd` panel file, or a CSV with the columns `marker,cycle,fluor`. A lone `.xpd` next to the image is used if you leave this out. |
| `--out DIR` | Where results go. The default is `qc_results` next to the image. |
| `--settings FILE` | Use this settings file instead of your saved settings. |
| `--nuclear-channel NAME` | The nuclear channel, if it is not called DAPI, Hoechst or DNA. |
| `--n-windows N`, `--seed N` | How many tissue windows to sample, and which. The same seed gives the same windows. |
| `--layout-template IMAGE` | Write a layout CSV to fill in, from the image's channel names, and stop. |

## Panel file and crosstalk

The crosstalk check needs to know which channels were imaged together. Give it a panel file (`.xpd`) or a layout
CSV with one row per marker: `marker,cycle,fluor`. Without one, crosstalk is switched off, and the report and the
window say so. In the window, **Save a layout CSV template** writes a CSV from the image's channel names for you to
fill in.

## Settings

**Settings** in the window opens every cut-off, with a one-line explanation for each. Settings are saved per user,
and the command line reads the same file. A change applies to the next run, not one in progress. The window shows
"Settings: defaults" or "Settings: modified" beside the button, and every run records the settings it used. The
cut-offs are fractions of the image's range, so they work for 8-bit and 16-bit images alike.

If you close the Settings window with unsaved changes, it asks whether to discard them.

## Read the results

See [How to read the results](docs/how_to_read_results.md). It explains each column, each Next run line, how to
judge a follow-up panel for crosstalk, and what the check can miss. The window opens the same guide from the
**How to read these results** button above the results. Next to it are **Open report**, **Open results folder** and
**Copy table**, so the directions can go to the bench. While a run goes on, the window lists its steps (finding
tissue, reading windows, scoring antibodies, checking crosstalk, drawing figures and writing the report) with the
current one marked, and tells you when it has finished (the banner changes, the window title says Done, and the
window beeps once).

## Limits

- **Directions only.** There is no dilution or exposure time in the output, on purpose.
- **Lab-defined cut-offs.** The defaults were set on tonsil. Check them against your own judgment and change them in
  Settings if your tissue needs it.
- **One section at a time.** Results describe the windows sampled from that image. A few windows give less certain calls.
- **The crosstalk check can miss a weak leak into a dim channel.** On the example section it did not flag
  a same-cycle leak of 10% of the source channel, and did flag one of 20% (one section, two pairs; see the measured
  table in [How to read the results](docs/how_to_read_results.md#what-the-crosstalk-check-can-miss)).
- **It cannot tell a leak from real co-staining.** A flagged pair is a place to look. The follow-up panel is there
  so you can judge.
- **Float images skip the saturation check.** The banner and report say "Saturation not checked".
- **Crosstalk needs enough unrelated channel pairs.** The cut-off is set from at least 6 pairs of markers two or more
  cycles apart in different fluors. A panel of one or two cycles, or a single-round image, has too few, and crosstalk is
  reported as "not judged" (nothing flagged, nothing cleared).
- **Near-blank channels are left out of crosstalk,** as source and as target.
- **Windows only so far.** The window is built and tested on Windows.

## Check the checker (the judge)

The judge writes known faults into real pixels and checks that the tool catches them and flags nothing else:
a channel dimmed to 12% or over-exposed, 10% of one channel leaked into another in the same cycle, and 10% carried
over into the same fluor in the next cycle. It also checks that pairs which should be clean stay clean.

    pytest tests/test_judge_synthetic.py            # a synthetic image; a few minutes
    pytest -m example tests/test_judge_example.py   # the tonsil example piece; about 15 minutes

The second command needs the example image (see above) and skips with a message when it is missing. It never
changes the example file. On the example piece one check is a known miss (a 10% same-cycle leak into a dim
channel is not flagged); see `examples/README.md`. If a check fails, the numbers are the finding: do not loosen a threshold to make it pass.

How sensitive the same-cycle check is to a weak leak into a dim channel is measured in
`docs/crosstalk_dose_series.csv` (made by `scripts/crosstalk_dose_series.py`, which is slow and not part of the tests).

## Develop

    pip install -e ".[dev]"
    pytest -q                      # everything; the full suite takes about an hour
    pytest -m "not slow" -q        # without the full synthetic runs


## License

MIT.
