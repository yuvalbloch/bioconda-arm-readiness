# Methodology

How every result in the table is produced. The code is the reference: the files named here are in [`farm/`](../farm).

## The one rule

Every ✓ and ✗ comes from a real solver run (or a real install log), stored with the exact specs, the solver version and the date of the package index it used. Nothing is typed in by hand. Download counts and pipeline use only decide **what** is tested and in which order. Reason labels and pin suggestions come from fixed rules and are never a status.

## What is tested ([`plan.py`](../farm/plan.py))

- **Packages:** the 1,000 most-downloaded bioconda packages ([bioconda-stats](https://github.com/bioconda/bioconda-stats)), plus every bioconda package named in an nf-core module or Snakemake wrapper environment file. For each, the newest version plus the 3 before it (by conda version order).
- **Packages that conda-forge serves:** with strict channel priority, a package name that exists on conda-forge is always taken from conda-forge (many Perl modules, biopython, mafft… moved there). For these, conda-forge's newest version is tested, because that is what users get. They are marked `cf` in the table.
- **Pipeline environments:** every `environment.yml` under `modules/nf-core/` of [nf-core/modules](https://github.com/nf-core/modules) and under `bio/` of [snakemake-wrappers](https://github.com/snakemake/snakemake-wrappers), at the commit recorded for that night. Channel prefixes (`bioconda::`) are dropped because the channel list is fixed; version specs are kept exactly as written (`name=1.2` keeps conda's fuzzy meaning `1.2.*`). `pip:` sections are not solved.

## How a result is produced ([`solve.py`](../farm/solve.py))

| | |
|---|---|
| Solver | [py-rattler](https://github.com/conda/rattler) (the solver behind pixi), pinned in [`requirements.txt`](../farm/requirements.txt) |
| Channels | `conda-forge`, `bioconda`, strict priority (bioconda's documented setup) |
| Subdirs | the platform plus `noarch` |
| Package unit | `<name> ==<version>` alone, in an empty environment |
| Environment unit | every dependency of the environment file |
| Timeout | 5 minutes per solve |

**The machines we model.** A solve depends on the system it is for, described by virtual packages. They are set explicitly ([`common.py`](../farm/common.py)):

| Platform | Virtual packages |
|---|---|
| `osx-arm64` (Apple Silicon) | `__osx=14.0`, `__archspec=1=arm64`, `__unix` |
| `osx-64` (Intel Mac) | `__osx=14.0`, `__archspec=1=x86_64`, `__unix` |
| `linux-aarch64` (arm64 Linux) | `__glibc=2.28`, `__linux=5.10`, `__archspec=1=aarch64`, `__unix` |
| `linux-64` | `__glibc=2.28`, `__linux=5.10`, `__archspec=1=x86_64`, `__unix` |

macOS 14 is an older macOS still in wide use on Apple Silicon; glibc 2.28 is the RHEL 8 / Rocky 8 baseline common on HPC clusters. A newer machine can only make a result better, never worse.

## Statuses ([`classify.py`](../farm/classify.py))

Fixed rules over the stored solver output; the first matching rule wins.

| Status | Shown | Rule |
|---|---|---|
| `native` | ✓ native | solves, and the package itself is a build for this platform |
| `noarch` | ✓ noarch | solves; the package itself is from `noarch` |
| `solves` | ✓ installs | (environments) the environment solves |
| `rosetta_only` | ✗ Intel only | Apple Silicon only: no candidates for this version on `osx-arm64`, but the same version solves natively on `osx-64`. For environments: fails on `osx-arm64`, solves on `osx-64` |
| `no_build` | ✗ no build | the solver found no candidates for the package version itself |
| `unsolvable` | ✗ dependency | a build exists, but the solve fails: the solver names the dependencies with no candidates ("blocked by") |
| `needs_newer_os` | ⚙ newer OS | fails **only** because of a virtual package above the modeled machine (e.g. `__osx >=14.5`) |
| `infra_error` | ? farm | the solver or a download failed (after one retry); never shown as ✗ |

"Blocked by" names are copied from the solver's own message (packages "for which no candidates were found"); they are labels, not guesses.

## Reason labels ([`reasons.py`](../farm/reasons.py))

For the newest version of each bioconda-served package that is `rosetta_only` or `no_build` on an arm64 platform, the recipe in [bioconda-recipes](https://github.com/bioconda/bioconda-recipes) (at the commit recorded for that night) is read:

| Label | Rule |
|---|---|
| recipe not opted in | `extra: additional-platforms` does not list the platform (bioconda builds arm64 only on request) |
| opted in, build missing | the recipe lists the platform but no build of this version exists |
| build dropped | an older version had a build for this platform; the newest has none |
| recipe gone | the recipe is not in bioconda-recipes any more |

**Use instead** (pin suggestion) = the newest tested version that is ✓ on that platform in our own runs. Nothing else is ever suggested.

## History and storage ([`db.py`](../farm/db.py))

- Every night the whole plan is solved again.
- A result is stored as a new, permanent row (with its full solver log) only when its outcome **changes**. The outcome is: solved from which subdir and channel, or unsolvable with which blockers. A re-check with the same outcome only moves the cell's "checked" date (`checks` table). So the database holds the full history without 31,000 identical rows a night.
- A farm problem (download or solver error) never replaces an existing real result.
- "What's new" is derived from status changes between nights: became native, lost its build, stopped installing (with the blocker), new version.
- Evidence per night (`snapshots/<date>/` on the [`results` branch](https://github.com/yuvalbloch/bioconda-arm-readiness/tree/results)): URLs and sha256 of the bioconda package indexes and download counts, commits of nf-core/modules, snakemake-wrappers and bioconda-recipes. The raw bioconda indexes are kept for 90 days as workflow artifacts. The solver reads the live package index during the run; the stored index of the same night is what bioconda served minutes earlier.

## Calibration ([`calibration/`](../calibration))

Does "the solver says ✓" mean "it really installs"? A sample is checked on real machines:

1. **Cases** ([`select_cases.py`](../calibration/select_cases.py)): ~100 packages and environments, picked by a fixed rule (hash order within each result group: installs, Intel-Mac only, fails on both Macs, fails on arm64 Linux only, native, noarch, dependency missing, no build), plus known answers (subread 2.0.6 ✗ / 2.0.8 ✓ on Apple Silicon; preseq, quast, prokka ✗; samtools ✓).
2. **On real machines** ([`calibrate.py`](../calibration/calibrate.py), workflow [`calibrate.yml`](../.github/workflows/calibrate.yml)): GitHub's `macos-15` (Apple Silicon) and `ubuntu-24.04-arm` runners. For each case, the farm's solver runs, then `micromamba create` really installs into an empty prefix with the same channels and strict priority, minutes apart. The machine's system version is pinned to the farm's model (`CONDA_OVERRIDE_OSX=14.0`, `CONDA_OVERRIDE_GLIBC=2.28`) so both answer the same question.
3. **Compare** ([`compare.py`](../calibration/compare.py)): solver ✓ ⇔ real install succeeded. Every mismatch is listed.

**Result (27 Nov 2026):** 102 cases × 2 platforms, **204 of 204 agree** (Apple Silicon: 47 installed, 55 failed; arm64 Linux: 57 installed, 45 failed). The table's earlier results (index of 26 Nov) also agreed on all 204. All known answers matched. Details: [`calibration/report.md`](../calibration/report.md).

## Real machines, weekly: install + the package's own test ([`smoke.py`](../farm/smoke.py))

Every week (workflow [`smoke.yml`](../.github/workflows/smoke.yml)), the 300 most-downloaded tools whose newest version the table marks ✓ on a platform are installed for real on GitHub's `macos-15` (Apple Silicon) and `ubuntu-24.04-arm` machines: `micromamba create` into an empty prefix, conda-forge + bioconda, strict priority, the system pinned to the farm's model as in calibration.

Then the package's **own test** is run. We don't write tests: a conda package carries the test that bioconda's CI ran when it built it (`info/test/run_test.sh`, `.py`, `.pl`, `.r` from conda-build, or the `script` and `python: imports` tests in `info/tests/tests.yaml` from rattler-build). If the test declares extra requirements, they are installed first. Each test runs in the activated environment (its `bin/` first on `PATH`, `$PREFIX`, `$R`, `$PYTHON`, `$PERL` set and the packages' activation scripts sourced, as in conda-build's test step), with a 15-minute limit.

| Status | Shown | Rule |
|---|---|---|
| `runs` | ✓✓ | installed, and the package's own test exited 0 |
| `installs` | ✓i | installed; the package carries no runnable test |
| `test_fail` | ✗t | installed, but its own test failed (log attached) |
| `install_fail` | ✗i | the real install failed although the solver said it installs (log attached) |
| `infra_error` | ? | timeout or download problem; never shown as a failure |

These are stored like solver results (a new row only when the outcome changes) and shown next to the solver status. A package's own test is usually a quick check (`--help`, `--version`, an import), not a full analysis.

## Limits

- Only the tools marked ✓✓ were run on a real machine, and only with their package's own (usually quick) test.
- Pipeline steps are tested as written; many also run from containers instead of conda.
- `pip:` dependencies in environment files are not solved.
- Packages served by conda-forge are tested at conda-forge's newest version only.

## Corrections

Wrong results are reported through [issues](https://github.com/yuvalbloch/bioconda-arm-readiness/issues) and every correction is listed here with its date.

- **27 Nov 2026, real-machine test:** the first weekly run marked all 600 installs as "installed, no test", because the farm looked for each package's test in the wrong folder of micromamba's cache. The installs themselves were real, but the "no test" label was a farm bug. Those 600 rows and their logs were removed (`ci.py invalidate smoke 2026-11-27`), the farm now reads the test from the package's own file, and the run was repeated the same day. That repeat exposed a second farm bug: tests were started through `micromamba run`, which failed before any test ran ("exec: --: invalid option"), so 595 tests showed as failed. Those rows were removed the same way, and tests now run in the environment activated directly (as conda-build's test step does). No solver result was affected.
