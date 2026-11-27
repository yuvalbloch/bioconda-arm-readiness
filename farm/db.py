"""Results database: one immutable row per solver run.

Key: (unit_type, unit, version, platform, snapshot). unit_type is 'pkg' (one package version alone)
or 'env' (a real nf-core / Snakemake environment file). A new snapshot gives new rows; old rows stay.
`raw` = what the solver said ('solved' | 'unsolvable' | 'error'). The STATUS shown to users is
derived from raw rows by classify.py only.
"""
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
  log TEXT,                     -- path of the gzipped full solution / error log
  seconds REAL,
  solver TEXT NOT NULL,         -- e.g. py-rattler 0.29.0
  created_at TEXT NOT NULL,
  PRIMARY KEY (unit_type, unit, version, platform, snapshot)
);
"""


def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=60)
    con.executescript(SCHEMA)
    return con
