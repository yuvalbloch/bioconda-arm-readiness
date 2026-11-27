"""Results database: immutable solver runs, stored when the outcome CHANGES.

runs    one row per (unit_type, unit, version, platform, snapshot) at which a cell's outcome was
        first seen or changed. unit_type is 'pkg' (one package version alone) or 'env' (a real
        nf-core / Snakemake environment file). Rows are never updated or deleted.
        raw = what the solver said: 'solved' | 'unsolvable' | 'error'.
checks  per cell: the last snapshot at which the solver was run again and gave the SAME outcome
        (so the table can say "re-checked on <date>" without storing 31,000 identical rows a night).

The outcome signature (what counts as "the same") is `signature()` below. The STATUS shown to
users is derived from runs by classify.py only.
"""
import json
import re
import sqlite3

from common import DATA, DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  unit_type TEXT NOT NULL,
  unit TEXT NOT NULL,
  version TEXT NOT NULL,
  platform TEXT NOT NULL,
  snapshot TEXT NOT NULL,
  raw TEXT NOT NULL,            -- solved | unsolvable | error
  specs TEXT NOT NULL,          -- the exact match specs given to the solver (JSON list)
  picked TEXT,                  -- for pkg units: subdir/channel/build of the package itself
  n_pkgs INTEGER,
  message TEXT,                 -- solver error text (first 4000 chars)
  log TEXT,                     -- path of the gzipped full solution / error log (relative to DATA/logs)
  seconds REAL,
  solver TEXT NOT NULL,         -- e.g. py-rattler 0.29.0
  created_at TEXT NOT NULL,
  PRIMARY KEY (unit_type, unit, version, platform, snapshot)
);
CREATE TABLE IF NOT EXISTS checks (
  unit_type TEXT NOT NULL,
  unit TEXT NOT NULL,
  version TEXT NOT NULL,
  platform TEXT NOT NULL,
  last_snapshot TEXT NOT NULL,  -- last snapshot whose solve gave the stored outcome again
  n INTEGER NOT NULL,           -- how many snapshots confirmed it
  PRIMARY KEY (unit_type, unit, version, platform)
);
"""

NO_CAND_TOP = re.compile(r"No candidates were found for ([A-Za-z0-9_.\-]+)")
# solver tree lines look like "│  └─ perl >=5.26.2,<5.26.3.0a0, for which no candidates were found."
NO_CAND_DEP = re.compile(r"─ ([A-Za-z0-9_.\-]+)[^\n]*?, for which no candidates were found")


def blockers(message):
    """Package names the solver itself reported as having no candidates (labels, never guessed)."""
    if not message:
        return []
    seen, out = set(), []
    for n in NO_CAND_TOP.findall(message) + NO_CAND_DEP.findall(message):
        n = n.lower()
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def signature(raw, picked, message):
    """What must stay equal for a re-run to count as 'the same outcome'.
    solved: where the package itself came from (platform subdir + channel; build numbers ignored);
    unsolvable: the set of blockers the solver named; error: just 'error'."""
    if raw == "solved":
        p = json.loads(picked) if picked else {}
        return f"solved|{p.get('subdir', '')}|{p.get('channel', '')}"
    if raw == "unsolvable":
        return "unsolvable|" + ",".join(sorted(blockers(message)))
    return raw


def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=60)
    con.executescript(SCHEMA)
    if not con.execute("SELECT 1 FROM checks LIMIT 1").fetchone():   # first use after the change-only switch
        con.execute("INSERT OR IGNORE INTO checks SELECT unit_type, unit, version, platform, MAX(snapshot), 1 "
                    "FROM runs GROUP BY unit_type, unit, version, platform")
        con.commit()
    return con


def latest_rows(con):
    """cell -> (snapshot, signature) of the newest stored row."""
    out = {}
    for k, u, v, p, s, raw, picked, msg in con.execute(
            "SELECT unit_type, unit, version, platform, snapshot, raw, picked, message FROM runs ORDER BY snapshot"):
        out[(k, u, v, p)] = (s, signature(raw, picked, msg))
    return out
