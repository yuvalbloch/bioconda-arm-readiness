"""Glue for the GitHub Actions nightly run (.github/workflows/nightly.yml).

State between runs lives on the `results` branch of this repository:
    (one commit, replaced every night: the data itself is append-only and holds the history)
    results.db                  every solver run (immutable, change-only) + the checks table
    plan.json                   the current test plan
    news.json                   the "What's new" list
    corpora.json                the newest nf-core / Snakemake environment files
    snapshots/<day>/*.json      small evidence files of each night: source URLs, sha256 hashes,
                                commits of nf-core/modules, snakemake-wrappers, bioconda-recipes,
                                reason labels, the night's solve statistics
    logs/<day>/<platform>/...   the gzipped solver output of every stored run

    python farm/ci.py load-state [--logs]     results branch -> $BIOARM
    python farm/ci.py pack-shard OUT          tonight's new rows + checks + logs -> OUT/
    python farm/ci.py merge-shards DIR...     merge shard outputs into results.db + logs
    python farm/ci.py save-state              $BIOARM -> results branch (commit + push)
"""
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATA, LOGS, PROJECT, SNAP_ROOT, latest_snap_dir  # noqa: E402
from db import connect  # noqa: E402

STATE = PROJECT / "state"          # git worktree of the results branch
BRANCH = "results"
STATE_FILES = ("results.db", "plan.json", "news.json")
BIG = {"availability.json", "conda-forge.json", "corpora.json", "downloads.json"}   # rebuilt nightly, not kept


def git(*args, cwd=PROJECT, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


def load_state(with_logs=False):
    DATA.mkdir(parents=True, exist_ok=True)
    if not STATE.exists():
        if git("fetch", "--depth=1", "origin", BRANCH, check=False).returncode != 0:
            print("no results branch yet: starting fresh")
            return
        git("worktree", "add", "--detach", str(STATE), f"origin/{BRANCH}")
    for name in STATE_FILES + ("corpora.json",):
        if (STATE / name).exists():
            shutil.copy2(STATE / name, DATA / name)
    if (STATE / "snapshots").exists():
        shutil.copytree(STATE / "snapshots", SNAP_ROOT, dirs_exist_ok=True)
    if with_logs and (STATE / "logs").exists():
        shutil.copytree(STATE / "logs", LOGS, dirs_exist_ok=True)
    n = connect().execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    print(f"loaded state: {n} stored runs")


def pack_shard(out):
    out = Path(out)
    snap = latest_snap_dir().name
    con = connect()
    con.row_factory = sqlite3.Row
    # only rows solved on THIS machine (their logs are here); on a same-day rerun the state already
    # holds earlier rows of the same snapshot, whose logs live on the results branch.
    runs = [dict(r) for r in con.execute("SELECT * FROM runs WHERE snapshot=?", (snap,)) if (LOGS / r["log"]).exists()]
    checks = [dict(r) for r in con.execute("SELECT * FROM checks WHERE last_snapshot=?", (snap,))]
    out.mkdir(parents=True, exist_ok=True)
    for r in runs:
        dst = out / "logs" / r["log"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(LOGS / r["log"], dst)
    out.mkdir(parents=True, exist_ok=True)
    (out / "rows.json").write_text(json.dumps({"snapshot": snap, "runs": runs, "checks": checks}))
    for f in DATA.glob("solve-stats-*.json"):
        shutil.copy2(f, out / f.name)
    print(f"packed {len(runs)} new rows, {len(checks)} checks")


def merge_shards(dirs):
    con = connect()
    nr = nc = 0
    stats = []
    for d in map(Path, dirs):
        f = d / "rows.json"
        if not f.exists():
            continue
        data = json.loads(f.read_text())
        for r in data["runs"]:
            cols = list(r)
            nr += con.execute(f"INSERT OR IGNORE INTO runs ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                              [r[c] for c in cols]).rowcount
        for c in data["checks"]:
            key = (c["unit_type"], c["unit"], c["version"], c["platform"])
            old = con.execute("SELECT last_snapshot, n FROM checks WHERE unit_type=? AND unit=? AND version=? AND platform=?", key).fetchone()
            if old is None or old[0] < c["last_snapshot"]:
                con.execute("INSERT OR REPLACE INTO checks VALUES (?,?,?,?,?,?)", (*key, c["last_snapshot"], c["n"]))
                nc += 1
        if (d / "logs").exists():
            shutil.copytree(d / "logs", LOGS, dirs_exist_ok=True)
        stats += [json.loads(s.read_text()) for s in d.glob("solve-stats-*.json")]
    con.commit()
    if stats and sum(s.get("solves", 0) for s in stats):     # a rerun with nothing left to solve keeps the night's numbers
        snap = latest_snap_dir()
        tot = {k: sum(s.get(k, 0) for s in stats) for k in ("solves", "new", "same", "error_kept_previous", "errors", "seconds")}
        tot["error_samples"] = [e for s in stats for e in s.get("error_samples", [])][:30]
        tot["machines"] = len(stats)
        (snap / "solve-stats.json").write_text(json.dumps(tot, indent=1))
    print(f"merged {nr} new rows, {nc} checks")


def save_state():
    if not STATE.exists():
        git("worktree", "add", "--orphan", "-b", BRANCH, str(STATE))
    for name in STATE_FILES:
        if (DATA / name).exists():
            shutil.copy2(DATA / name, STATE / name)
    corp = latest_snap_dir() / "corpora.json"     # the newest environment files (the site needs them)
    if corp.exists():
        shutil.copy2(corp, STATE / "corpora.json")
    for day in SNAP_ROOT.glob("????-??-??"):
        for f in day.glob("*.json"):
            if f.name not in BIG:
                dst = STATE / "snapshots" / day.name / f.name
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst)
    if LOGS.exists():
        shutil.copytree(LOGS, STATE / "logs", dirs_exist_ok=True)
    (STATE / "README.md").write_text(
        "# Results data\n\nMachine-written by the nightly solver farm. `results.db` (SQLite) holds every stored solver run "
        "(a new row only when a result changes; the `checks` table records each re-confirmation). `logs/` holds the full "
        "solver output of every stored run, `snapshots/` the evidence of each night's package index (URLs, sha256, commits). "
        "See `docs/methodology.md` on the main branch.\n")
    git("add", "-A", cwd=STATE)
    if not git("status", "--porcelain", cwd=STATE).stdout.strip():
        print("state unchanged")
        return
    n = connect().execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    snap = latest_snap_dir().name
    # One parentless commit, force-pushed: the data is append-only and carries its own history,
    # so git history would only duplicate a 16+ MB binary database every night.
    tree = git("write-tree", cwd=STATE).stdout.strip()
    commit = git("-c", "user.name=github-actions[bot]", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                 "commit-tree", tree, "-m", f"Results {snap}: {n} stored runs", cwd=STATE).stdout.strip()
    git("push", "-f", "origin", f"{commit}:refs/heads/{BRANCH}", cwd=STATE)
    print(f"saved state: {n} stored runs")


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "load-state":
        load_state("--logs" in args)
    elif cmd == "pack-shard":
        pack_shard(*args)
    elif cmd == "merge-shards":
        merge_shards(args)
    elif cmd == "save-state":
        save_state()
    else:
        sys.exit(__doc__)
