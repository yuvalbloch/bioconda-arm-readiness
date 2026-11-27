"""Snapshot the real-world environment files of nf-core modules and Snakemake wrappers.

The repos are read as tarballs, streamed in memory: only environment files are parsed (as YAML
data), nothing is extracted or executed.
Output: snapshots/<day>/corpora.json
    {"envs": [{"id": "nf-core:samtools/sort", "channels": [...], "deps": [...], "pip": [...]}],
     "sources": {name: {"url", "sha256", "commit"}}}
"""
import hashlib
import io
import json
import re
import sys
import tarfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import DATA, http_get, snap_dir, write_json  # noqa: E402

CORPORA = {
    # name: (repo, branch, path regex -> id)
    "nf-core": ("nf-core/modules", "master", re.compile(r"^[^/]+/modules/nf-core/(.+)/environment\.ya?ml$")),
    "snakemake": ("snakemake/snakemake-wrappers", "master", re.compile(r"^[^/]+/bio/(.+)/environment\.ya?ml$")),
}


def head_commit(repo, branch):
    raw = http_get(f"https://api.github.com/repos/{repo}/commits/{branch}")
    return json.loads(raw)["sha"]


def collect(name, repo, branch, rx):
    commit = head_commit(repo, branch)
    url = f"https://codeload.github.com/{repo}/tar.gz/{commit}"
    cache = DATA / "cache" / f"{repo.replace('/', '_')}-{commit}.tar.gz"   # reruns reuse the same commit
    if cache.exists():
        blob = cache.read_bytes()
    else:
        blob = http_get(url, timeout=1800)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(blob)
    envs = []
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        for m in tf:
            if not m.isfile():
                continue
            mm = rx.match(m.name)
            if not mm:
                continue
            try:
                doc = yaml.safe_load(tf.extractfile(m).read()) or {}
            except yaml.YAMLError:
                continue
            deps, pip = [], []
            for d in doc.get("dependencies") or []:
                if isinstance(d, str):
                    deps.append(d.strip())
                elif isinstance(d, dict) and "pip" in d:
                    pip.extend(str(x) for x in d["pip"] or [])
            envs.append({"id": f"{name}:{mm.group(1)}", "channels": doc.get("channels") or [],
                         "deps": deps, "pip": pip})
    meta = {"url": url, "commit": commit, "sha256": hashlib.sha256(blob).hexdigest(), "envs": len(envs)}
    return envs, meta


def main():
    day = snap_dir()
    all_envs, sources = [], {}
    for name, (repo, branch, rx) in CORPORA.items():
        envs, meta = collect(name, repo, branch, rx)
        print(f"  {name}: {len(envs)} environment files @ {meta['commit'][:10]}", flush=True)
        all_envs += envs
        sources[name] = meta
    write_json(day / "corpora.json", {"envs": sorted(all_envs, key=lambda e: e["id"]), "sources": sources})


if __name__ == "__main__":
    main()
