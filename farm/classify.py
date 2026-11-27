"""The fixed rules that turn raw solver runs into the statuses shown in the table.

This is the ONLY place a status is set. Raw rows (db.runs) are never changed; the derived table
`status` is rebuilt from them on every run, so a rule change re-applies to all history.

`status` is a timeline: one row per cell (unit, version, platform) for every snapshot at which its
status changed. The current status of a cell is its newest row.

pkg units (one package version alone), first matching rule wins:
  infra_error   raw = error (solver/download problem; retried, never shown as ✗)
  noarch        solved, and the package itself came from the noarch subdir
  native        solved, and the package itself is a build for this platform
  rosetta_only  osx-arm64 only: no candidates for this version on osx-arm64, but the same version
                solves natively on osx-64 (works today through Rosetta, stops with macOS 28)
  no_build      the solver found no candidates for this package version on this platform
  unsolvable    a build exists, but the environment can't be solved (a dependency blocks it)
  needs_newer_os  unsolvable ONLY because of a system requirement (virtual package such as
                __osx >=14.5 or __glibc >=2.34) above the machine we model; shown as ⚙, never ✗

env units (a real nf-core / Snakemake environment):
  infra_error | solves | rosetta_only (osx-arm64 fails while osx-64 solves) | unsolvable
  | needs_newer_os (same rule as above)

smoke units (phase 6: a real install + the package's own test on a real arm64 machine, smoke.py):
  runs            installed, and the package's own test (the one bioconda's CI ran) passed   ✓✓
  installs        installed; the package carries no runnable test                           ✓i
  test_fail       installed, but its own test failed (log attached)                          ✗t
  install_fail    the real install failed although the solver said it installs              ✗i
  infra_error     timeout or download problem

Blockers are package names the solver itself reported as having "no candidates" (db.blockers).
"""
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import NO_CAND_TOP, blockers, connect  # noqa: E402

STATUS_SCHEMA = """
DROP TABLE IF EXISTS status;
CREATE TABLE status (
  unit_type TEXT, unit TEXT, version TEXT, platform TEXT, snapshot TEXT,
  status TEXT, blockers TEXT, run_snapshot TEXT,
  PRIMARY KEY (unit_type, unit, version, platform, snapshot)
);
"""
GOOD = {"native", "noarch", "solves"}
SMOKE = {"passed": "runs", "no_test": "installs", "test_failed": "test_fail", "install_failed": "install_fail", "error": "infra_error"}


def status_of(r, intel):
    """r: the cell's current run row; intel: the same unit's current osx-64 row (or None)."""
    kind, raw, plat = r["unit_type"], r["raw"], r["platform"]
    if kind == "smoke":
        return SMOKE.get(raw, "infra_error"), []
    bl = blockers(r["message"]) if raw == "unsolvable" else []
    intel_ok = intel is not None and intel["raw"] == "solved"
    if raw == "error":
        st = "infra_error"
    elif kind == "pkg":
        if raw == "solved":
            sub = (json.loads(r["picked"]) if r["picked"] else {}).get("subdir")
            st = "noarch" if sub == "noarch" else "native"
        else:
            top = NO_CAND_TOP.findall(r["message"] or "")
            own = any(t.lower() == r["unit"].lower() for t in top)
            intel_native = intel_ok and (json.loads(intel["picked"] or "{}").get("subdir") == "osx-64")
            if own and plat == "osx-arm64" and intel_native:
                st = "rosetta_only"
            elif own:
                st = "no_build"
            else:
                st = "unsolvable"
    else:
        if raw == "solved":
            st = "solves"
        elif plat == "osx-arm64" and intel_ok:
            st = "rosetta_only"
        else:
            st = "unsolvable"
    if st in ("unsolvable", "rosetta_only") and bl and all(b.startswith("__") for b in bl):
        st = "needs_newer_os"
    return st, bl


def timeline(rows):
    """rows: run dicts. Yields status-table tuples (one per status change of a cell)."""
    groups = defaultdict(list)
    for r in rows:
        groups[(r["unit_type"], r["unit"], r["version"])].append(r)
    for (kind, unit, ver), rs in groups.items():
        rs.sort(key=lambda r: r["snapshot"])
        state, last = {}, {}
        for snap in sorted({r["snapshot"] for r in rs}):
            for r in rs:
                if r["snapshot"] == snap:
                    state[r["platform"]] = r
            for plat, r in state.items():
                st, bl = status_of(r, state.get("osx-64"))
                if last.get(plat) != (st, bl):
                    last[plat] = (st, bl)
                    yield (kind, unit, ver, plat, snap, st, json.dumps(bl), r["snapshot"])


def current(con):
    """cell -> dict(status, blockers, since, run_snapshot) from the newest status row."""
    out = {}
    for k, u, v, p, snap, st, bl, rs in con.execute(
            "SELECT unit_type, unit, version, platform, snapshot, status, blockers, run_snapshot FROM status ORDER BY snapshot"):
        out[(k, u, v, p)] = {"status": st, "blockers": json.loads(bl), "since": snap, "run_snapshot": rs}
    return out


def main():
    con = connect()
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT unit_type, unit, version, platform, snapshot, raw, picked, message FROM runs")]
    con.row_factory = None
    res = list(timeline(rows))
    con.executescript(STATUS_SCHEMA)
    con.executemany("INSERT INTO status VALUES (?,?,?,?,?,?,?,?)", res)
    con.commit()
    cur = current(con)
    c = Counter((k[0], k[3], v["status"]) for k, v in cur.items())
    print(f"status rows: {len(res)} (timeline), {len(cur)} cells")
    for (k, p, s), n in sorted(c.items()):
        print(f"  {k:4s} {p:14s} {s:15s} {n}")


if __name__ == "__main__":
    main()
