# Running the farm

The farm is a few Python scripts around [py-rattler](https://github.com/conda/rattler). It needs Python 3.11+ and no conda installation.

```bash
python -m pip install -r farm/requirements.txt
```

Data goes to `~/bioarm` (Linux/macOS) or `C:\bioarm` (Windows); override with the `BIOARM` environment variable. Keep it on a short path **without spaces or non-ASCII characters**: conda tooling breaks on such paths.

## Steps

```bash
python farm/update.py snapshot   # tonight's sources: download counts, bioconda indexes, conda-forge channeldata,
                                 # nf-core / Snakemake environment files; then the test plan (plan.json)
python farm/solve.py             # solve everything (resumable; --shard 2/8 for one machine's share)
python farm/update.py post       # classify -> reason labels -> report -> build the site in $BIOARM/site
python -m http.server -d ~/bioarm/site 8000
```

`python farm/update.py` runs all three. On Windows use `py -3.11 -I` instead of `python`.

**Time and disk:** ~31,000 solves. With the package index cached, a solve takes a median of 0.2–0.3 s; a full run is ~40 min on 4 cores. The rattler cache is ~1 GB; the logs of one full run are ~36 MB gzipped.

### `solve.py` options

| Option | Effect |
|---|---|
| `--units pkg` / `--units env` | only package versions / only pipeline environments |
| `--platforms osx-arm64` | subset of platforms |
| `--only samtools nf-core:samtools/sort` | only these units |
| `--shard 2/8` | this machine's share of the work |
| `--workers N` | parallel solves (default 4) |
| `--limit N` | stop after N solves (pilot) |

## In the cloud

[`.github/workflows/nightly.yml`](../.github/workflows/nightly.yml) runs every night at 02:41 UTC:

1. **snapshot:** loads the previous results from the `results` branch, fetches tonight's sources, builds the plan.
2. **solve:** 8 machines, each solves its share of all units × 4 platforms.
3. **publish:** merges the shares, classifies, reads the recipes, writes "What's new", saves to the `results` branch (one commit, replaced nightly; the data itself is append-only), and deploys the site to GitHub Pages.

It also rewrites the *Farm status* issue every night. It adds a comment (which emails the repository owner) only when a popular tool loses arm64 support, many pipeline steps break, the farm has many errors, or the run fails. The workflow re-enables itself through the API, so GitHub never pauses the schedule.

## Calibration

[`.github/workflows/calibrate.yml`](../.github/workflows/calibrate.yml) (run by hand) installs every case of [`calibration/cases.json`](../calibration/cases.json) for real on `macos-15` and `ubuntu-24.04-arm`. Then:

```bash
gh run download <run id> -D cal-run
python calibration/compare.py cal-run --run-url <run url>   # -> calibration/summary.json + report.md
```

## Weekly real-machine test

[`.github/workflows/smoke.yml`](../.github/workflows/smoke.yml) runs every Sunday at 07:17 UTC (or by hand, with the number of tools per platform): `smoke.py select` picks the tools from the current results, 4 Apple Silicon and 4 arm64 Linux machines run `smoke.py run`, and the results are merged, saved and published like the nightly ones. Its publish step never runs at the same time as the nightly one.

## After changing a rule

Statuses are rebuilt from the stored runs every night (`classify.py`), so a rule change applies to all history at once. Record the change under *Corrections* in [methodology.md](methodology.md) when it changes a published result.
