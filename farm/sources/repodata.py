"""Snapshot bioconda's package index for every platform and build the availability index.

Raw files: snapshots/<day>/bioconda-<subdir>-repodata.json.gz (exactly what the channel served).
Derived:   snapshots/<day>/availability.json
    { "<package>": { "<version>": { "platforms": ["linux-64", ...], "noarch": bool,
                                    "first_seen": {platform: timestamp_ms} } } }

Availability = "a build of this version exists for this platform". It is NOT a result:
whether it installs is decided only by solve.py (a real solver run). This file only decides
what to test and gives labels like "no build" vs "build exists but won't solve".
"""
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import PLATFORMS, save_raw, snap_dir, urlopen, write_json  # noqa: E402

SUBDIRS = PLATFORMS + ["noarch"]


def fetch(day_dir):
    meta = {}
    data = {}
    for sd in SUBDIRS:
        url = f"https://conda.anaconda.org/bioconda/{sd}/repodata.json"
        # The CDN serves gzip; ask for it explicitly and decompress ourselves.
        for attempt in range(4):
            try:
                with urlopen(url, headers={"Accept-Encoding": "gzip"}, timeout=300) as r:
                    raw = r.read()
                    enc = r.headers.get("Content-Encoding", "")
                    last_mod = r.headers.get("Last-Modified", "")
                break
            except Exception:  # noqa: BLE001
                if attempt == 3:
                    raise
        body = gzip.decompress(raw) if enc == "gzip" else raw
        sha = save_raw(day_dir, f"bioconda-{sd}-repodata.json", body)
        meta[sd] = {"url": url, "sha256": sha, "last_modified": last_mod, "bytes": len(body)}
        data[sd] = json.loads(body)
        print(f"  {sd}: {len(body)/1e6:.0f} MB", flush=True)
    return meta, data


def build_availability(data):
    av = {}
    for sd, rd in data.items():
        recs = {}
        recs.update(rd.get("packages", {}))
        recs.update(rd.get("packages.conda", {}))
        for rec in recs.values():
            name, ver = rec["name"], rec["version"]
            v = av.setdefault(name, {}).setdefault(ver, {"platforms": [], "noarch": False, "first_seen": {}})
            if sd == "noarch":
                v["noarch"] = True
            elif sd not in v["platforms"]:
                v["platforms"].append(sd)
            ts = rec.get("timestamp")
            if ts:
                prev = v["first_seen"].get(sd)
                v["first_seen"][sd] = min(prev, ts) if prev else ts
    for vers in av.values():
        for v in vers.values():
            v["platforms"].sort()
    return av


def main():
    day = snap_dir()
    print(f"bioconda repodata -> {day}")
    meta, data = fetch(day)
    av = build_availability(data)
    write_json(day / "availability.json", av)
    write_json(day / "repodata-meta.json", meta)
    print(f"{len(av)} packages, {sum(len(v) for v in av.values())} versions")


if __name__ == "__main__":
    main()
