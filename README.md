# Bioconda on Apple Silicon

**Live table: https://yuvalbloch.github.io/bioconda-arm-readiness/**

Which bioconda tools really install natively on **Apple Silicon Macs (osx-arm64)** and **arm64 Linux (linux-aarch64)**, at which versions, what broke and when, and what to pin instead. Updated every night.

**Why now:** macOS 27 is the last macOS with full Rosetta 2. From macOS 28 (autumn 2027), Intel-only (`osx-64`) conda packages, which many Apple Silicon Macs run today through Rosetta without anyone noticing, stop working. In bioconda, Apple Silicon and arm64 Linux builds are opt-in per recipe, so coverage is patchy.

**The one rule:** every ✓ and ✗ comes from a real solver run (or a real install log), stored with the exact command and the date of the package index it used. Nothing in the table is typed in by hand or guessed. Download counts and pipeline use only decide *what* is tested and in which order. Reason labels ("the recipe never opted in to osx-arm64") and pin suggestions come from fixed rules and are never a status.

## What is tested

- **Packages:** the 1,000 most-downloaded bioconda packages, plus every bioconda package used by an [nf-core module](https://github.com/nf-core/modules) or a [Snakemake wrapper](https://github.com/snakemake/snakemake-wrappers): about 1,700 packages, newest version plus the 3 before it.
- **Pipelines:** every nf-core module and Snakemake wrapper environment file (about 2,600), exactly as written.
- **Platforms:** `osx-arm64` (Apple Silicon) and `linux-aarch64` (arm64 Linux), compared with `osx-64` (Intel Mac) and `linux-64`.
- **Setup:** conda-forge + bioconda, strict channel priority (bioconda's documented setup); solved for macOS 14.0 and glibc 2.28 machines.
- **Calibration:** a sample of solver answers is checked against real `micromamba create` installs on real Apple Silicon and arm64 Linux machines (GitHub Actions `macos-14` and `ubuntu-24.04-arm`).

How results are produced, in detail: [docs/methodology.md](docs/methodology.md). Running it yourself: [docs/running.md](docs/running.md).

## Repository layout

| Path | What |
|---|---|
| `farm/` | the solver farm (Python 3.11 + [py-rattler](https://github.com/conda/rattler), no conda install needed) |
| `farm/sources/` | snapshot collectors: package indexes, download counts, nf-core / Snakemake environment files |
| `calibration/` | solver-vs-real-install checks on real arm64 machines |
| `site/` | the static website |
| `.github/workflows/` | the nightly run and the calibration run |

Results and logs live on the `results` branch.

Built with AI (Claude) as part of an experiment; every result is machine-produced and links to its log. Found a wrong result? Please open an issue with the tool, version and platform.

License: MIT.
