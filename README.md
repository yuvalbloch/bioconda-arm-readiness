# Results data

Machine-written by the nightly solver farm. `results.db` (SQLite) holds every stored solver run (a new row only when a result changes; the `checks` table records each re-confirmation). `logs/` holds the full solver output of every stored run, `snapshots/` the evidence of each night's package index (URLs, sha256, commits). See `docs/methodology.md` on the main branch.
