# Background: conda, bioconda and Apple Silicon, in plain words

A short primer for reading this project's results without a bioinformatics background.

## The pieces

- **Bioinformatics tools** are programs that biologists run on DNA and RNA data: aligners (match sequencing reads to a genome), quantifiers (count genes), QC tools, assemblers. Most are command-line programs written in C/C++, Python, R, Perl or Java.
- **conda** is a package manager. You write a list of the tools you want in an **environment file** (`environment.yml`). conda then downloads ready-made builds of those tools *and of everything they depend on*, into an isolated folder called an *environment*.
- **Channels** are the shops conda buys from. Two matter here:
  - **conda-forge**: the big general shop (35,000+ packages: Python, R, compilers, libraries).
  - **bioconda**: the bioinformatics shop (~13,000 packages), built on top of conda-forge.

  The standard setup lists conda-forge first, with **strict priority**: if conda-forge has a package name, conda never looks for it in bioconda. That's why many old bioconda packages (Perl modules, biopython, mafft) are irrelevant today: the conda-forge copy wins.
- **The solver** is the part of conda that picks an exact version of every package so that all requirements fit together. "The environment solves" means a consistent set exists. "Unsolvable" means it doesn't, and the solver says which package is missing.
- **Platforms (subdirs).** A compiled tool must be built separately for each kind of computer:

  | Name | Computer |
  |---|---|
  | `linux-64` | normal Linux servers and clusters (Intel/AMD) |
  | `linux-aarch64` | arm64 Linux: AWS Graviton, arm64 containers, some new HPC |
  | `osx-64` | Intel Macs |
  | `osx-arm64` | **Apple Silicon Macs** (M1–M4 and later) |
  | `noarch` | platform-independent (pure Python, R, Java…): works everywhere |

## Why Apple Silicon is a problem now

- Apple Silicon Macs can run Intel-Mac (`osx-64`) programs through a translator called **Rosetta 2**. That's how many labs run bioconda tools on new Macs today, often without knowing.
- **macOS 27 is the last version with full Rosetta.** macOS 28 (autumn 2027) keeps only a small part of it, for games. Intel-only tools stop working.
- In bioconda, Apple Silicon and arm64 Linux builds are **opt-in per recipe**: a tool's maintainer has to add a line saying "also build for osx-arm64". Many never did.
- Anaconda stopped making new Intel-Mac builds in August 2025, so the Intel route is drying up anyway.

## What the table says, per tool and platform

| Status | Meaning in plain words |
|---|---|
| **native** ✓ | A real build for this computer exists and installs |
| **noarch** ✓ | Platform-independent; installs everywhere |
| **rosetta_only** ✗R | Only an Intel-Mac build exists. It works today through Rosetta and stops with macOS 28 |
| **no_build** ✗ | No build of this version for this computer at all |
| **unsolvable** ✗d | A build exists, but something it needs is missing for this computer (the table names it) |
| **infra_error** ? | Our run had a technical problem; retried, never counted as ✗ |

**Reasons** (labels, not results) say why a gap exists: *not opted in* (the recipe never asked for this platform), *opted in, no build* (it asked, but the build failed), *dropped* (older versions had it, the newest doesn't).

## How a result is produced

For every tool version and platform, the farm asks the real conda solver: "install exactly this, on this kind of computer, from conda-forge + bioconda". The answer and the solver's full output are stored with the date of the package lists used. Nobody, human or AI, sets a result by hand. Later, the top tools are also really installed on real Apple Silicon and arm64 Linux machines (GitHub provides them free for public projects).
