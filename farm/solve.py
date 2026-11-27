"""Run the real solver for every planned unit and platform; store one immutable row per run.

  pkg units: "<name> ==<version>" alone in an empty environment
  env units: every dependency of a real nf-core module / Snakemake wrapper environment file

Solver: py-rattler (the engine behind pixi), channels conda-forge + bioconda, strict priority,
platform + noarch, virtual packages from common.VIRTUAL. Resumable: rows that already exist for
the snapshot are skipped.

  py -3.11 -I farm/solve.py [--units pkg env] [--platforms osx-arm64 ...] [--only samtools ...]
                            [--limit N] [--workers 4]
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
from common import CHANNELS, LOGS, PLAN_PATH, PLATFORMS, RATTLER_CACHE, latest_snap_dir, read_json, virtual_packages  # noqa: E402
from db import connect  # noqa: E402
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
        recs = await solve(sources=CHANNELS, specs=ms, platforms=[platform, "noarch"],
                           virtual_packages=virtual_packages(platform), gateway=gateway,
                           timeout=dt.timedelta(minutes=5))
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


def store_log(snapshot, kind, unit, version, platform, specs, text):
    p = LOGS / snapshot / platform / kind / (unit.replace("/", "__").replace(":", "_") + f"@{version}.log.gz")
    p.parent.mkdir(parents=True, exist_ok=True)
    header = (f"# unit: {kind} {unit} {version}\n# platform: {platform} (+noarch)\n# channels: {', '.join(CHANNELS)} (strict)\n"
              f"# specs: {json.dumps(specs)}\n# solver: {solver_id()}\n# index snapshot: {snapshot}\n\n")
    with gzip.open(p, "wt", encoding="utf-8") as f:
        f.write(header + (text or ""))
    return str(p)


async def main_async(args):
    from rattler import Gateway
    day = latest_snap_dir()
    snapshot = day.name
    plan = read_json(PLAN_PATH)
    corpora = read_json(day / "corpora.json", {"envs": []})
    con = connect()
    done = {tuple(r) for r in con.execute("SELECT unit_type, unit, version, platform FROM runs WHERE snapshot=?", (snapshot,))}
    todo = [(k, u, v, s, pl) for (k, u, v, s) in units(plan, corpora, args.units, set(args.only or []))
            for pl in args.platforms if (k, u, v, pl) not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"snapshot {snapshot}: {len(todo)} solves to run ({len(done)} already stored)", flush=True)
    RATTLER_CACHE.mkdir(parents=True, exist_ok=True)
    gateway = Gateway(cache_dir=str(RATTLER_CACHE))
    sem = asyncio.Semaphore(args.workers)
    solver = solver_id()
    n = 0
    t0 = time.time()

    async def worker(item):
        nonlocal n
        k, u, v, specs, pl = item
        async with sem:
            raw, picked, npk, msg, text, secs = await run_one(gateway, k, u, v, specs, pl)
        log = store_log(snapshot, k, u, v, pl, specs, text)
        con.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (k, u, v, pl, snapshot, raw, json.dumps(specs), picked, npk, msg, log, round(secs, 2),
                     solver, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")))
        n += 1
        if n % 200 == 0:
            con.commit()
            print(f"  {n}/{len(todo)}  {time.time()-t0:.0f}s", flush=True)

    await asyncio.gather(*(worker(i) for i in todo))
    con.commit()
    print(f"done: {n} solves in {time.time()-t0:.0f}s", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--units", nargs="+", default=["pkg", "env"], choices=["pkg", "env"])
    ap.add_argument("--platforms", nargs="+", default=PLATFORMS)
    ap.add_argument("--only", nargs="+")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=4)
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
