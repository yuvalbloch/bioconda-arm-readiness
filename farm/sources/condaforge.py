"""Snapshot conda-forge's channeldata.json (one entry per package, with the platforms it has builds for).

Why: with the standard setup (conda-forge first, strict priority), a package name that exists on
conda-forge is ALWAYS taken from conda-forge; bioconda's copies are ignored. Many old bioconda
packages (perl-*, biopython, libdeflate, mafft...) moved there. Without this, they would look like
Apple Silicon gaps when they aren't. Still only availability: the solver decides results.
Output: snapshots/<day>/conda-forge.json  {package: {"subdirs": [...], "version": latest}}
"""
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import save_raw, snap_dir, urlopen, write_json  # noqa: E402

URL = "https://conda.anaconda.org/conda-forge/channeldata.json"


def main():
    day = snap_dir()
    for attempt in range(4):
        try:
            with urlopen(URL, headers={"Accept-Encoding": "gzip"}, timeout=300) as r:
                raw, enc = r.read(), r.headers.get("Content-Encoding", "")
            break
        except Exception:  # noqa: BLE001
            if attempt == 3:
                raise
    body = gzip.decompress(raw) if enc == "gzip" else raw
    sha = save_raw(day, "conda-forge-channeldata.json", body)
    pk = json.loads(body)["packages"]
    out = {n: {"subdirs": sorted(v.get("subdirs") or []), "version": v.get("version")} for n, v in pk.items()}
    write_json(day / "conda-forge.json", out)
    write_json(day / "conda-forge-meta.json", {"url": URL, "sha256": sha})
    print(f"conda-forge: {len(out)} packages")


if __name__ == "__main__":
    main()
