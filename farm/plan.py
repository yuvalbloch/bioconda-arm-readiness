"""Build the test plan: which bioconda packages and versions get solved.

Selection (approved plan, D3):
  * the TOP_BY_DOWNLOADS most-downloaded bioconda packages (bioconda-stats), plus
  * every bioconda package used by an nf-core module or Snakemake wrapper environment;
  * per package the newest VERSIONS_PER_PKG versions.
Downloads and pipeline use only decide WHAT is tested and in which ORDER - never a result.

Output: C:\\bioarm\\plan.json and a printed preliminary build-availability summary
(availability = "a build exists", NOT "it installs": that is decided by solve.py only).
"""
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import PLAN_PATH, TARGET, TOP_BY_DOWNLOADS, VERSIONS_PER_PKG, latest_snap_dir, read_json, write_json  # noqa: E402

NAME_RX = re.compile(r"^[A-Za-z0-9_.\-]+")


def spec_name(spec):
    """'bioconda::samtools=1.21' / 'samtools =1.21' / 'r-base>=4' -> 'samtools' / 'r-base'."""
    s = spec.split("::", 1)[-1].strip()
    m = NAME_RX.match(s)
    return m.group(0).lower() if m else None


def version_key(v):
    from rattler import Version
    try:
        return Version(v)
    except Exception:  # noqa: BLE001 - unparsable versions sort first
        return Version("0")


def main():
    day = latest_snap_dir()
    av = read_json(day / "availability.json")
    downloads = read_json(day / "downloads.json", {})
    corpora = read_json(day / "corpora.json", {"envs": []})
    cf = read_json(day / "conda-forge.json", {})   # names here are served by conda-forge (strict priority)
    if not corpora["envs"]:
        print("WARNING: corpora.json missing or empty - selecting by downloads only")

    uses = Counter()
    for env in corpora["envs"]:
        for d in env["deps"]:
            n = spec_name(d)
            if n in av:
                uses[n] += 1

    top = [n for n, _ in sorted(((n, c) for n, c in downloads.items() if n in av), key=lambda x: -x[1])][:TOP_BY_DOWNLOADS]
    chosen = set(top) | set(uses)
    pkgs = []
    for n in sorted(chosen, key=lambda n: -downloads.get(n, 0)):
        if n in cf:
            # Strict priority: users get conda-forge's package; bioconda's copies are invisible.
            # channeldata only lists conda-forge's newest version, so that one is tested.
            vers = [cf[n]["version"]] if cf[n].get("version") else []
            if not vers:
                continue
            pkgs.append({"name": n, "downloads": downloads.get(n, 0), "pipeline_uses": uses.get(n, 0),
                         "versions": vers, "builds": {}, "conda_forge": cf[n]})
            continue
        vers = sorted(av[n], key=version_key, reverse=True)[:VERSIONS_PER_PKG]
        pkgs.append({"name": n, "downloads": downloads.get(n, 0), "pipeline_uses": uses.get(n, 0),
                     "versions": vers, "builds": {v: av[n][v] for v in vers},
                     "conda_forge": cf.get(n)})   # not None -> users get conda-forge's package, not bioconda's
    write_json(PLAN_PATH, {"snapshot": day.name, "packages": pkgs,
                           "rule": "downloads and pipeline use set test order only"})

    # ---- preliminary summary (build availability of the NEWEST version) ----
    cat = Counter()
    rosetta_only = []
    for p in pkgs:
        if p["conda_forge"]:
            sub = p["conda_forge"]["subdirs"]
            cat["served by conda-forge: " + ("has Apple Silicon/noarch" if (TARGET in sub or "noarch" in sub) else "NO Apple Silicon")] += 1
            continue
        b = p["builds"][p["versions"][0]]
        if b["noarch"]:
            cat["noarch (platform-independent)"] += 1
        elif TARGET in b["platforms"]:
            cat["native Apple Silicon build"] += 1
        elif "osx-64" in b["platforms"]:
            cat["Intel-Mac build only (Rosetta-only)"] += 1
            rosetta_only.append(p)
        else:
            cat["no macOS build at all"] += 1
    n = len(pkgs)
    print(f"snapshot {day.name}: {n} packages selected "
          f"({len(top)} top-downloaded, {len(uses)} used by pipelines, {len(set(top) & set(uses))} both); "
          f"{sum(len(p['versions']) for p in pkgs)} package-versions")
    for k, c in cat.most_common():
        print(f"  {k:40s} {c:5d}  ({100*c/n:.1f}%)")
    print("\nTop 25 Rosetta-only by downloads (newest version has an Intel-Mac build but no Apple Silicon build):")
    for p in rosetta_only[:25]:
        print(f"  {p['name']:28s} {p['versions'][0]:14s} downloads={p['downloads']:>10,}  pipeline_uses={p['pipeline_uses']}")


if __name__ == "__main__":
    main()
