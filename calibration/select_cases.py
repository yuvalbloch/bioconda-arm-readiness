"""Pick the calibration cases: ~100 units whose solver result is checked against a REAL install.

Selection is a fixed rule, not a hand pick: within each stratum (result group from the farm's
stored run), units are ordered by sha256(unit name) and the first N are taken. Strata cover both
"installs" and "doesn't install" results, so a solver that is wrong in either direction shows up.
The known answers from the project plan (subread 2.0.6 / 2.0.8, preseq, quast, prokka) are always in.

    py -3.11 -I calibration/select_cases.py [--snapshot 2026-11-26]
Output: calibration/cases.json (committed; the workflow reads it).
"""
import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "farm"))
from common import DB_PATH, PLAN_PATH, read_json  # noqa: E402

ENV_STRATA = [  # (label, osx-arm64 status, linux-aarch64 status or None, n)
    ("env: solves on Apple Silicon", "solves", "solves", 20),
    ("env: Intel-Mac only", "rosetta_only", None, 20),
    ("env: fails on both Macs", "unsolvable", None, 10),
    ("env: fails on arm64 Linux only", "solves", "unsolvable", 10),
]
PKG_STRATA = [  # (label, platform, status, n), newest version only
    ("pkg: native on Apple Silicon", "osx-arm64", "native", 8),
    ("pkg: noarch", "osx-arm64", "noarch", 6),
    ("pkg: Intel-Mac only", "osx-arm64", "rosetta_only", 8),
    ("pkg: dependency missing on Apple Silicon", "osx-arm64", "unsolvable", 6),
    ("pkg: no build on Apple Silicon", "osx-arm64", "no_build", 4),
    ("pkg: no build on arm64 Linux", "linux-aarch64", "no_build", 4),
]
KNOWN = [("subread", "2.0.6"), ("subread", "2.0.8"), ("preseq", None), ("quast", None), ("prokka", None), ("samtools", None)]


def h(s):
    return hashlib.sha256(s.encode()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", default="2026-11-26")
    a = ap.parse_args()
    con = sqlite3.connect(DB_PATH)
    st = {}
    for kind, unit, ver, plat, s in con.execute(
            "SELECT unit_type, unit, version, platform, status FROM status WHERE snapshot=?", (a.snapshot,)):
        st[(kind, unit, ver, plat)] = s
    specs = {(k, u, v): json.loads(sp) for k, u, v, sp in con.execute(
        "SELECT DISTINCT unit_type, unit, version, specs FROM runs WHERE snapshot=?", (a.snapshot,))}
    plan = read_json(PLAN_PATH)
    newest = {p["name"]: p["versions"][0] for p in plan["packages"] if p["versions"]}

    cases, seen = [], set()

    def add(kind, unit, ver, label):
        if (kind, unit, ver) in seen or (kind, unit, ver) not in specs:
            return False
        seen.add((kind, unit, ver))
        cases.append({"kind": kind, "unit": unit, "version": ver, "specs": specs[(kind, unit, ver)], "stratum": label,
                      "farm_status_at_selection": {p: st.get((kind, unit, ver, p)) for p in ("osx-arm64", "linux-aarch64")}})
        return True

    for name, ver in KNOWN:
        add("pkg", name, ver or newest[name], "known answer")
    envs = sorted({u for (k, u, v, p) in st if k == "env"}, key=h)
    for label, mac, lin, n in ENV_STRATA:
        got = 0
        for u in envs:
            if got >= n:
                break
            if st.get(("env", u, "-", "osx-arm64")) == mac and (lin is None or st.get(("env", u, "-", "linux-aarch64")) == lin):
                got += add("env", u, "-", label)
    for label, plat, want, n in PKG_STRATA:
        got = 0
        for name in sorted(newest, key=h):
            if got >= n:
                break
            if st.get(("pkg", name, newest[name], plat)) == want:
                got += add("pkg", name, newest[name], label)
    out = HERE / "cases.json"
    out.write_text(json.dumps({"selected_from_snapshot": a.snapshot, "rule": __doc__.strip().splitlines()[2:6],
                               "cases": cases}, indent=1), encoding="utf-8")
    from collections import Counter
    for k, v in Counter(c["stratum"] for c in cases).items():
        print(f"  {k:45s} {v}")
    print(f"{len(cases)} cases -> {out}")


if __name__ == "__main__":
    main()
