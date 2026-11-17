# How to read the results

Phenocycler QC Eval looks at one stained section and tells you, for each antibody, what to change at the next
staining. It also lists pairs of channels where one channel seems to leak into another (crosstalk).

The window and the report (`STAIN_QC_REPORT.md`) show the same results. This page explains what each part means.

Two things to keep in mind from the start:

- The tool gives **directions only**. It never gives a dilution or an exposure time.
- The cut-offs behind each call are **lab-defined**. They were tuned on a tonsil section. They are not published
  standards. Treat a call as a pointer to where to look, not as a verdict.

## The headline

At the top of the results you see two lines.

- **Next run:** how many antibodies to change, how many to compare with expected biology, and how many to keep.
- **Crosstalk:** how many channel pairs were flagged, and which one is strongest. If you gave no panel file, it says
  crosstalk was not checked.

Each line is a link to its tab. Notes under the lines tell you about anything that limits the results, such as a
panel marker that was not found in the image, or "Saturation not checked".

## Titration tab: the Next run column

Each antibody gets one instruction. These are the six you can see:

| Next run | What it means |
|---|---|
| Raise concentration (or lengthen exposure) | The stain is too weak. Use more antibody, or expose longer. |
| Raise concentration | The stain is too weak. Exposure is left alone because the background is already high. |
| Lower concentration (or shorten exposure) | The stain is too strong, with bright pixels at the top of the range. Use less antibody, or expose shorter. |
| Lower concentration | The stain is too strong. Exposure is left alone because the background is already high. |
| Compare with expected biology | One number looks weak, but the antibody may be a rare target. Look at the tiles and decide. |
| Keep | Nothing to change. |

The arrow beside each row repeats the direction: up means raise, down means lower, a question mark means compare,
a check mark means keep. Rows that need a change come first and are tinted orange. Rows to compare come next.
Rows to keep are tinted green and come last. You can hide them with **Hide the antibodies to keep**.

Inside the rows to change, the one furthest past its cut-off comes first, and its evidence starts with a word:
**Strong**, **Moderate** or **Mild**. The word says how many times over its cut-off the worst finding is. It does not
change the call. When the section loads, the window selects the strongest row to change that has something to look at
(an antibody with almost no signal teaches nothing as a first picture).

### What to do with a "Compare with expected biology" row

Select the row. The pane names the one weak measure and says what to compare, using the control marker from
**Settings** where there is one ("CD163 should look like CD68"). Then it says what each outcome means:

- If the marker is clearly weaker than expected, raise the concentration at the next run.
- If the pattern is right and the marker is simply rare in this tissue, keep it.

No amount is given. How much to raise is your call, from your own titration history.

### Antibodies with little or no signal

If the brightest 0.5% of an antibody's pixels are below the near-blank cut-off, the pane says **Little or no
signal** instead of drawing tiles, because the tiles would show only noise. A dim antibody that is not near-blank
is drawn **brightened for display**, and the line above the tiles says the true signal is low.

### The evidence beside each instruction

Every instruction carries its evidence. The table shows a short phrase. Select a row to see all four numbers and
example tiles from the section.

- **Signal (brightest 0.5%).** How bright the brightest 0.5% of tissue pixels are, out of the top of the range
  (for example "15 of 255"). A low number means a dim stain.
- **Stain index.** How far stained pixels stand above the background, measured in units of how much the
  background itself varies. Higher is cleaner. Below 3 is weak. (The report and the window use this one name.)
- **Stained pixels.** The share of tissue pixels that count as stained. Very low can mean a rare target, or a stain
  that did not work.
- **Saturated pixels.** The share of tissue pixels at the very top of the range, where bright detail is lost. More
  than 0.5% means the stain is too strong.

The tiles show six small regions of the section for that antibody. By default the tiles share one display range,
from zero to that antibody's own bright end. This keeps a dim antibody readable. The line above the tiles gives the
range, and each tile says how much of it is tissue. A tile with little tissue can look empty for that reason.
Choose **Show tiles on the full scale** to see how dim the antibody really is next to the top of the range.

The histogram below the tiles shows how many pixels have each value, on a log scale. The red line is the automatic
cut between stained and background pixels.

### How the calls are made

These are the default cut-offs. They are fractions of the image's range, so they work for 8-bit and 16-bit images
alike. You can change every one of them in **Settings**.

| Finding | Default cut-off |
|---|---|
| Dim | Signal (brightest 0.5%) below 0.10 of the range |
| Near-blank | Signal below 0.05 of the range. Almost nothing was stained. |
| Low stain index | Stain index below 3.0 |
| Too few stained pixels | Stained share below 0.5% of tissue pixels |
| Saturated | More than 0.5% of tissue pixels at the top of the range |
| High background | Background above 0.05 of the range |

The call follows from these findings:

- **Lower:** too many pixels are saturated.
- **Raise:** two or more weaknesses at once (dim, low stain index, too few stained pixels).
- **Compare with expected biology:** exactly one weakness. One alone can be real biology, such as a rare target.
- **Keep:** no weakness and no saturation.

The exposure part of the instruction depends on the background. If the background is low, the instruction offers a
change in exposure as an alternative. If the background is high, the problem is the concentration, so the
exposure is left alone.

### Why directions and not numbers

How much to change depends on your antibody lot, your tissue, your fixation and your imaging setup. A number from
one section on one run would look precise and be wrong for the next. A direction is what the data supports. Pick
the amount from your own titration history, change one thing at a time, and run the check again.

## Crosstalk tab

Crosstalk means signal from one channel showing up in another. It needs a panel layout (a `.xpd` file, or a CSV
with `marker,cycle,fluor`), because the tool has to know which channels were imaged together. Without one,
crosstalk is off, and the window offers a layout CSV template you can fill in.

The tool checks two kinds of pair:

- **Carryover from the previous cycle.** A channel with the same dye in the next cycle picks up leftover signal.
- **Same-cycle bleed-through.** Two channels imaged in the same cycle with different dyes. These are checked in
  both directions.

Channels that are near-blank are left out, as source and as target. A leak into or out of a channel with almost no
signal cannot be measured.

A pair is flagged when either of two things is true. One direction is much stronger than the other (the
"asymmetry"), or the fine detail of the target copies the source (the "texture match"). Both cut-offs come from
pairs that cannot leak, plus a margin.

The table has four columns: **Source**, **Target**, **Result** and **Likely cause**. The cause is shortened to
**Carryover** (from the previous cycle) or **Same cycle** (bleed-through); the pane beside the table has the whole
phrase. The **Result** column says it in words for every pair: **Flagged** (a number is past its cut-off, with the reason),
**Not flagged**, or **Not tested: near-blank channel**. Only flagged pairs are listed at first; tick **Also list pairs
that were not flagged** to see the rest. Each number in the pane is printed next to its cut-off, for example
"0.48 (cut-off 0.30, over)". The cut-offs are the ones the run used. The pane has three short lines: what to look
for, what the numbers say (including the spill from the source into the target and the other way), and what to do.

**Not flagged does not mean clean.** A weak leak can be missed (see "What the crosstalk check can miss" below). A run
with no flagged pairs says "No pairs flagged. This does not prove the channels are clean."

### How to judge a follow-up panel

Select a flagged pair and the window shows a follow-up panel. It has four rows of tiles, in the windows where the
source is brightest:

- **Source:** the channel that seems to leak.
- **Target:** the channel that seems to receive it.
- **Control:** a marker that is expected to follow the target's biology. It is not a leak partner.
- **Nuclei:** DAPI, to show where the cells are.

Each row has its own display range, printed at the left. Read it like this:

- It looks like **crosstalk** if the target is brighter where the source is bright, copies the source's shape
  (same cells, same edges), and the control does not do the same.
- It looks like **real biology** if the control is bright in the same places. Both markers may be on the same cell
  type. The target's own signal is then expected.
- It is **unclear** if the target is bright only on a few cells, or the pattern does not match the source. Look at
  more of the section before you decide.

The sentence above the tiles gives the target's average where the source is bright and where it is low. The
tool cannot tell a leak from real co-staining. The panel is there so you can judge.

### Markers that should not overlap

Below the flagged pairs is a second table: pairs of markers that should not be stained in the same cells, such as
a T cell marker and a B cell marker. For each pair the table shows the **Result** and the **Ratio** (overlap divided by what chance gives). Select a row
to see the two numbers behind the ratio, in the pane beside the table:

- **Both positive:** the share of tissue pixels positive for both.
- **Chance alone would give:** the overlap you would get if the two markers were unrelated.

The **Result** column says **As expected** (ratio below 1) or **Overlap higher than expected** (ratio of 1 or more).
A pair with a channel that is almost blank says **Not tested: near-blank channel**, with a dash for the ratio: the
overlap of a channel with almost no signal would only measure noise, so no ratio is judged. Select a row to see the
tiles of the two markers on the same windows, and the numbers in a sentence. A near-blank pair has a plain sentence
instead of tiles. The report's table follows the same rule.

A ratio below 1 is the expected direction. A ratio near or above 1 means more overlap than these markers should
have. It does not say why. Possible reasons include crosstalk, tissue where the two cell types touch, and a marker
that is not as specific as assumed. You can edit the list in **Settings**.

The default pairs are:

| Marker | Marker |
|---|---|
| CD3e | CD20 |
| Pan-Cytokeratin | CD45 |
| CD31 | CD20 |
| CD8 | CD20 |
| CD4 | CD20 |
| Pan-Cytokeratin | CD20 |
| CD68 | CD20 |
| SMA | CD20 |

## Figures tab

The Figures tab lists the overview pictures saved in the run folder: the detection plot, the intensity distributions
and the crosstalk heatmap. The follow-up panels are on the Crosstalk tab, so they are not repeated here.
Double-click a picture to open it full size.
In the heatmap, red means the row channel leaks into the column channel, and blue means the other way round.
**Open this figure full size** opens the picture. **Open full report** opens the report.

## Images with decimal (float) values

Some images store decimal numbers instead of whole numbers. They have no fixed top of the range. For these images:

- The range is the 99.99th percentile of tissue pixels.
- Saturation is **not checked**, because nothing can be at a top that does not exist.
- The banner and the report both say "Saturation not checked".

All other checks work as usual.

## Few windows

The tool samples up to 200 square windows (about 111 micrometers on a side) from the tissue. A small image can have
fewer. The report says how many it used. With fewer windows, a call close to a cut-off is less certain. The run
stops if it finds fewer than 20 windows.

## What the crosstalk check can miss

**A panel needs enough unrelated channel pairs.** The cut-off is set from pairs of markers two or more cycles apart in different fluors, which cannot leak into each other. With fewer than 6 such pairs (a panel of one or two cycles, or a single-round image with every marker in cycle 1), crosstalk is **not judged**: the banner says so, no pair is flagged or cleared, and every pair reads "Not tested: too few unrelated channel pairs to set a cut-off". That is not a clean result.

A pair that is not flagged is not proven clean. The check flags a leak only when it stands out from the pairs that
cannot leak. **A weak leak into a dim channel can go unflagged.**

We measured how weak. On the example section we wrote a known share of one channel into another channel imaged in the
same cycle, then ran the check. The "dose" is that share of the source channel's pixel values. We tried two pairs:

- **ICOS into MPO.** MPO is a dim target (its brightest 0.5% reach 35 of 255).
- **CD3e into CD38.** CD38 is a bright target (168 of 255).

The table shows the texture match (how closely the target copies the source's fine detail) against its cut-off.
A pair is flagged when the texture match is above its cut-off, or when one direction is much stronger than the other.

| Dose | ICOS into MPO: texture match (cut-off) | Flagged | CD3e into CD38: texture match (cut-off) | Flagged |
|---|---|---|---|---|
| 0% (nothing added) | 0.134 (0.313) | no | 0.114 (0.313) | no |
| 5% | 0.218 (0.320) | no | 0.175 (0.313) | no |
| 10% | 0.298 (0.325) | no | 0.233 (0.316) | no |
| 20% | 0.441 (0.339) | **yes** | 0.344 (0.329) | **yes** |
| 30% | 0.557 (0.340) | yes | 0.442 (0.339) | yes |
| 50% | 0.716 (0.340) | yes | 0.599 (0.339) | yes |

**On this section, neither pair was flagged at 10% or less, and both were flagged at 20%.** So a leak of about a fifth
of the source is the weakest one this check reliably caught here. The detection point lies between 10% and 20% for
both pairs. A 10% leak into the dim channel came close (0.298 against 0.325) but was not flagged. The cut-off moves a
little from run to run because it is worked out again from the pairs that cannot leak. The full numbers, including
the one-direction measure, are in `crosstalk_dose_series.csv` next to this page. The script that made them is
`scripts/crosstalk_dose_series.py`.

What this does not tell you:

- It is **one section** and two pairs of channels, each run once. It is not a general guarantee for other tissue,
  other panels or other staining.
- The leak was added as an exact proportional copy of the source. A real leak can be patchier, which can make it
  easier or harder to flag.
- A bright target did not catch a leak any earlier than a dim one in this test. Do not read a dim target as the only
  risk.

If you suspect a leak that was not flagged, such as a bright channel next to a dim one in the same cycle, look at
the two channels yourself in the image. Lowering the **Margin** in Settings makes the check more sensitive, at the
cost of more false flags.

## Taking the answer away

Above the results, on every tab: **Open report** (the full `STAIN_QC_REPORT.md`), **Open results folder**,
**Copy table** (the Titration table as tab-separated text, ready to paste into a notebook or a spreadsheet), and
**How to read these results** (this page, in a window of its own).

## Where the files are

Each run writes a new folder named `<image name>_<date>-<time>`. Nothing from an earlier run is overwritten.
It holds the tables (`stain_qc_channels.csv`, `stain_qc_crosstalk_pairs.csv` and others), the figures, the report, and
`run_info.json`, which records every setting used. Use **Load a past run** to open an old folder in the window.
