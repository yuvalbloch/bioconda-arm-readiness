"""Snapshot bioconda's own download counts (bioconda/bioconda-stats, 'data' branch).

Used ONLY to decide which packages to test and in what order. Never affects a result.
Output: snapshots/<day>/downloads.json  {package: total_downloads}
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import http_get, save_raw, snap_dir, write_json  # noqa: E402

URL = "https://raw.githubusercontent.com/bioconda/bioconda-stats/data/package-downloads/anaconda.org/bioconda/packages.tsv"


def main():
    day = snap_dir()
    raw = http_get(URL)
    sha = save_raw(day, "bioconda-stats-packages.tsv", raw)
    counts = {}
    for line in raw.decode("utf-8").splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) == 2 and parts[1].isdigit():
            counts[parts[0]] = int(parts[1])
    write_json(day / "downloads.json", counts)
    write_json(day / "downloads-meta.json", {"url": URL, "sha256": sha})
    print(f"downloads: {len(counts)} packages")


if __name__ == "__main__":
    main()
