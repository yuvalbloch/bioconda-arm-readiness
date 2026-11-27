"""The fixed rules that turn raw solver runs into the statuses shown in the table.

This is the ONLY place a status is set. Raw rows (db.runs) are never changed; the derived table
`status` is rebuilt from them on every run, so a rule change re-applies to all history.

pkg units (one package version alone), first matching rule wins:
  infra_error   raw = error (solver/download problem; retried, never shown as ✗)
  noarch        solved, and the package itself came from the noarch subdir
  native        solved, and the package itself is a build for this platform
  rosetta_only  osx-arm64 only: no candidates for this version on osx-arm64, but the same unit
                solved natively on osx-64 (works today through Rosetta, stops with macOS 28)
  no_build      the solver found no candidates for this package version on this platform
  unsolvable    a build exists, but the environment can't be solved (a dependency blocks it)
  needs_newer_os  unsolvable ONLY because of a system requirement (virtual package such as
                __osx >=14.5 or __glibc >=2.34) above the machine we model; shown as ⚙, never ✗

env units (a real nf-core / Snakemake environment):
  infra_error | solves | rosetta_only (osx-arm64 fails while osx-64 solves) | unsolvable

Blockers are package names the solver itself reported as having "no candidates"; they are labels,
copied from the solver message, never guessed.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import connect  # noqa: E402

NO_CAND_TOP = re.compile(r"No candidates were found for ([A-Za-z0-9_.\-]+)")
# solver tree lines look like "│  └─ perl >=5.26.2,<5.26.3.0a0, for which no candidates were found."
NO_CAND_DEP = re.compile(r"─ ([A-Za-z0-9_.\-]+)[^\n]*?, for which no candidates were found")

STATUS_SCHEMA = """
DROP TABLE IF EXISTS status;
CREATE TABLE status (
  unit_type TEXT, unit TEXT, version TEXT, platform TEXT, snapshot TEXT,
  status TEXT, blockers TEXT,
  PRIMARY KEY (unit_type, unit, version, platform, snapshot)
);
"""


def blockers(message):
    if not message:
        return []
    names = NO_CAND_TOP.findall(message) + NO_CAND_DEP.findall(message)
    seen, out = set(), []
    for n in names:
        n = n.lower()
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def classify_rows(rows):
    """rows: dicts with unit_type, unit, version, platform, snapshot, raw, picked, message."""
    by_key = {(r["unit_type"], r["unit"], r["version"], r["snapshot"], r["platform"]): r for r in rows}
    out = []
    for r in rows:
        kind, raw, plat = r["unit_type"], r["raw"], r["platform"]
        bl = blockers(r["message"]) if raw == "unsolvable" else []
        intel = by_key.get((kind, r["unit"], r["version"], r["snapshot"], "osx-64"))
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
        out.append((kind, r["unit"], r["version"], plat, r["snapshot"], st, json.dumps(bl)))
    return out


def main():
    con = con_ = connect()
    con.row_factory = __import__("sqlite3").Row
    rows = [dict(r) for r in con.execute("SELECT unit_type, unit, version, platform, snapshot, raw, picked, message FROM runs")]
    res = classify_rows(rows)
    con_.executescript(STATUS_SCHEMA)
    con_.executemany("INSERT INTO status VALUES (?,?,?,?,?,?,?)", res)
    con_.commit()
    from collections import Counter
    c = Counter((k, p, s) for k, _, _, p, _, s, _ in res)
    for (k, p, s), n in sorted(c.items()):
        print(f"  {k:4s} {p:14s} {s:13s} {n}")


if __name__ == "__main__":
    main()
