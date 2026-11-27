"""Shared settings and helpers for the Bioconda-on-Apple-Silicon farm.

Heavy data (package-index snapshots, corpora, results DB, logs) lives in DATA, a short ASCII-only
path (conda tooling breaks on non-ASCII paths) outside any synced folder (hundreds of MB of indexes). Code and the published site live in the project folder.
"""
import datetime as _dt
import gzip
import hashlib
import json
import os
import time
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("BIOARM", r"C:\bioarm" if os.name == "nt" else os.path.expanduser("~/bioarm")))
SNAP_ROOT = DATA / "snapshots"          # snapshots/<YYYY-MM-DD>/<source files>
RATTLER_CACHE = DATA / "rattler-cache"  # rattler's repodata cache (reused between runs)
PLAN_PATH = DATA / "plan.json"
DB_PATH = DATA / "results.db"
LOGS = DATA / "logs"
SITE_SRC = PROJECT / "site"                # hand-written page, script and style
SITE = DATA / "site"                       # the built website (data, tool pages, logs)
SITE_URL = "https://yuvalbloch.com/bioconda-arm-readiness/"   # GitHub Pages of this repo (the owner's custom domain)
REPO_URL = "https://github.com/yuvalbloch/bioconda-arm-readiness"

# ---- test scope (from the approved plan) ----
CHANNELS = ["conda-forge", "bioconda"]          # strict priority, bioconda's documented setup
TARGET = "osx-arm64"                            # the headline platform
PLATFORMS = ["linux-64", "linux-aarch64", "osx-64", "osx-arm64"]
VERSIONS_PER_PKG = 4                            # newest + 3 previous
TOP_BY_DOWNLOADS = 1000                         # plus every bioconda package used by the corpora

# Virtual packages describing the machine each platform is solved for (see docs/methodology.md).
VIRTUAL = {
    "linux-64": [("__unix", "0", "0"), ("__linux", "5.10", "0"), ("__glibc", "2.28", "0"), ("__archspec", "1", "x86_64")],
    "linux-aarch64": [("__unix", "0", "0"), ("__linux", "5.10", "0"), ("__glibc", "2.28", "0"), ("__archspec", "1", "aarch64")],
    "osx-64": [("__unix", "0", "0"), ("__osx", "14.0", "0"), ("__archspec", "1", "x86_64")],
    "osx-arm64": [("__unix", "0", "0"), ("__osx", "14.0", "0"), ("__archspec", "1", "arm64")],
}

UA = "bioconda-arm-readiness (+https://yuvalbloch.com/wild-west/)"


def ssl_context():
    """Verified TLS using certifi's CA bundle (this laptop's Windows store has an expired root)."""
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def urlopen(url, headers=None, timeout=120):
    h = {"User-Agent": UA, **(headers or {})}
    if url.startswith("https://api.github.com/") and os.environ.get("GITHUB_TOKEN"):
        h["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"   # shared CI addresses hit the anonymous rate limit
    req = urllib.request.Request(url, headers=h)
    return urllib.request.urlopen(req, timeout=timeout, context=ssl_context())


def today():
    return _dt.date.today().isoformat()


def snap_dir(day=None):
    d = SNAP_ROOT / (day or today())
    d.mkdir(parents=True, exist_ok=True)
    return d


def latest_snap_dir():
    days = sorted(p for p in SNAP_ROOT.glob("????-??-??") if p.is_dir()) if SNAP_ROOT.exists() else []
    return days[-1] if days else None


def http_get(url, retries=4, timeout=120):
    """GET with retries (the network here drops connections now and then)."""
    last = None
    for i in range(retries):
        try:
            with urlopen(url, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001 - retried, then re-raised
            last = e
            time.sleep(2 * (i + 1))
    raise last


def save_raw(day_dir, name, data):
    """Store a raw source file gzipped, return its sha256 (snapshot evidence)."""
    p = Path(day_dir) / (name + ".gz")
    with gzip.open(p, "wb") as f:
        f.write(data)
    return hashlib.sha256(data).hexdigest()


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def virtual_packages(platform):
    from rattler import BuildString, GenericVirtualPackage, PackageName, Version
    return [GenericVirtualPackage(PackageName(n), Version(v), BuildString(b)) for n, v, b in VIRTUAL[platform]]
