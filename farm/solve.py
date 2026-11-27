"""Run the real solver for every planned unit and platform.

  pkg units: "<name> ==<version>" alone in an empty environment
  env units: every dependency of a real nf-core module / Snakemake wrapper environment file

Solver: py-rattler (the engine behind pixi), channels conda-forge + bioconda, strict priority,
platform + noarch, virtual packages from common.VIRTUAL. The solver reads the live package index;
the snapshot date recorded with each row is the day of the run (the bioconda index files of that
day are stored by sources/repodata.py).

Storage (see db.py): a NEW row + log only when the outcome differs from the cell's newest stored
row; otherwise only checks.last_snapshot moves forward. A solver/download error is retried once;
if a cell already has a real outcome, an error never replaces it (it is counted in the report).

Resumable: cells already checked for this snapshot are skipped.

  python farm/solve.py [--units pkg env] [--platforms osx-arm64 ...] [--only samtools ...]
                       [--shard 2/8] [--limit N] [--workers 4]
"""
import argparse
import asyncio
import datetime as dt
import gzip
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CHANNELS, DATA, LOGS, PLAN_PATH, PLATFORMS, RATTLER_CACHE, VIRTUAL, latest_snap_dir, read_json, virtual_packages, write_json  # noqa: E402
from db import connect, latest_rows, signature  # noqa: E402
from plan import spec_name  # noqa: E402


def solver_id():
    try:
        from importlib.metadata import version
        return f"py-rattler {version('py-rattler')}"
    except Exception:  # noqa: BLE001
        return "py-rattler"


def env_specs(deps):
    """Environment-file deps -> solver specs. Channel prefixes are dropped (the channel list is fixed);
    conda env-file 'name=1.2' keeps conda's meaning (fuzzy 1.2.*) via rattler's lenient parser."""
    out = []
    for d in deps:
        s = d.split("::", 1)[-1].strip()
        if s and spec_name(s):
            out.append(s)
    return out


def units(plan, corpora, kinds, only):
    if "pkg" in kinds:
        for p in plan["packages"]:
            if only and p["name"] not in only:
                continue
            for v in p["versions"]:
                yield "pkg", p["name"], v, [f"{p['name']} =={v}"]
    if "env" in kinds and corpora:
        for e in corpora["envs"]:
            if only and e["id"] not in only:
                continue
            specs = env_specs(e["deps"])
            if specs:
                yield "env", e["id"], "-", specs


async def run_one(gateway, kind, unit, version, specs, platform):
    from rattler import MatchSpec, solve
    t = time.time()
    try:
        ms = [MatchSpec(s, strict=False) for s in specs]
        kw = {"gateway": gateway} if gateway is not None else {}
        recs = await solve(sources=CHANNELS, specs=ms, platforms=[platform, "noarch"],
                           virtual_packages=virtual_packages(platform), timeout=dt.timedelta(minutes=5), **kw)
        sol = sorted(f"{r.name.normalized}={r.version}={r.build} [{r.subdir}] {r.channel}" for r in recs)
        picked = None
        if kind == "pkg":
            me = [r for r in recs if r.name.normalized == unit]
            if me:
                picked = json.dumps({"subdir": str(me[0].subdir), "build": str(me[0].build), "channel": str(me[0].channel)})
        return "solved", picked, len(recs), None, "\n".join(sol), time.time() - t
    except Exception as e:  # noqa: BLE001 - the solver's message IS the evidence
        msg = str(e)
        raw = "unsolvable" if ("Cannot solve" in msg or "No candidates" in msg or "cannot be installed" in msg) else "error"
        return raw, None, None, msg[:4000], msg, time.time() - t


def log_relpath(snapshot, kind, unit, version, platform):
    return f"{snapshot}/{platform}/{kind}/" + unit.replace("/", "__").replace(":", "_") + f"@{version}.log.gz"


def store_log(snapshot, kind, unit, version, platform, specs, text):
    rel = log_relpath(snapshot, kind, unit, version, platform)
    p = LOGS / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    vp = ", ".join(f"{n}={v}" + (f"={b}" if b != "0" else "") for n, v, b in VIRTUAL[platform])
    header = (f"# unit: {kind} {unit} {version}\n# platform: {platform} (+noarch)\n# channels: {', '.join(CHANNELS)} (strict priority)\n"
              f"# virtual packages: {vp}\n# specs: {json.dumps(specs)}\n# solver: {solver_id()}\n# index snapshot: {snapshot}\n\n")
    with gzip.open(p, "wt", encoding="utf-8") as f:
        f.write(header + (text or ""))
    return rel


async def main_async(args):
    from rattler import Gateway
    day = latest_snap_dir()
    snapshot = day.name
    plan = read_json(PLAN_PATH)
    corpora = read_json(day / "corpora.json", {"envs": []})
    con = connect()
    done = {tuple(r) for r in con.execute("SELECT unit_type, unit, version, platform FROM checks WHERE last_snapshot=?", (snapshot,))}
    prev = latest_rows(con)
    todo = [(k, u, v, s, pl) for (k, u, v, s) in units(plan, corpora, args.units, set(args.only or []))
            for pl in args.platforms]
    if args.shard:
        i, n = map(int, args.shard.split("/"))
        todo = [t for j, t in enumerate(todo) if j % n == i - 1]
    todo = [t for t in todo if (t[0], t[1], t[2], t[4]) not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"snapshot {snapshot}: {len(todo)} solves to run ({len(done)} already checked)", flush=True)
    RATTLER_CACHE.mkdir(parents=True, exist_ok=True)
    gateway = Gateway(cache_dir=str(RATTLER_CACHE))
    sem = asyncio.Semaphore(args.workers)
    solver = solver_id()
    stats = {"new": 0, "same": 0, "error_kept_previous": 0}
    errors = []
    n = 0
    t0 = time.time()

    async def worker(item):
        nonlocal n
        k, u, v, specs, pl = item
        async with sem:
            res = await run_one(gateway, k, u, v, specs, pl)
            if res[0] == "error":   # a download hiccup or solver crash: one retry
                res = await run_one(gateway, k, u, v, specs, pl)
        raw, picked, npk, msg, text, secs = res
        cell = (k, u, v, pl)
        sig = signature(raw, picked, msg)
        old = prev.get(cell)
        if raw == "error":
            errors.append({"cell": cell, "message": (msg or "")[:500]})
        if raw == "error" and old and not old[1].startswith("error"):
            stats["error_kept_previous"] += 1          # never let a farm problem replace a real outcome
        elif old and old[1] == sig:
            con.execute("UPDATE checks SET last_snapshot=?, n=n+1 WHERE unit_type=? AND unit=? AND version=? AND platform=?",
                        (snapshot, *cell))
            stats["same"] += 1
        else:
            log = store_log(snapshot, k, u, v, pl, specs, text)
            con.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (k, u, v, pl, snapshot, raw, json.dumps(specs), picked, npk, msg, log, round(secs, 2),
                         solver, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")))
            con.execute("INSERT OR REPLACE INTO checks VALUES (?,?,?,?,?,1)", (*cell, snapshot))
            stats["new"] += 1
        n += 1
        if n % 500 == 0:
            con.commit()
            print(f"  {n}/{len(todo)}  {time.time()-t0:.0f}s  {stats}", flush=True)

    await asyncio.gather(*(worker(i) for i in todo))
    con.commit()
    out = DATA / f"solve-stats-{args.shard.replace('/', 'of') if args.shard else 'all'}.json"
    write_json(out, {"snapshot": snapshot, "solves": n, **stats, "errors": len(errors), "error_samples": errors[:20],
                     "seconds": round(time.time() - t0)})
    print(f"done: {n} solves in {time.time()-t0:.0f}s  {stats}  errors={len(errors)}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--units", nargs="+", default=["pkg", "env"], choices=["pkg", "env"])
    ap.add_argument("--platforms", nargs="+", default=PLATFORMS)
    ap.add_argument("--only", nargs="+")
    ap.add_argument("--shard")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=4)
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
