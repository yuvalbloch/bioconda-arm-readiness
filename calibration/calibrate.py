"""Calibration on a real machine: does the farm's solver answer match a real install?

Runs ON the machine of the platform being checked (GitHub's macos-14 = osx-arm64,
ubuntu-24.04-arm = linux-aarch64). For every case in cases.json (this shard only):

  1. farm solve:   the exact same py-rattler call the farm uses (farm/solve.py run_one), live index;
  2. real install: `micromamba create` into an empty prefix, conda-forge + bioconda, strict priority,
                   with the machine's system version pinned to the farm's model through
                   CONDA_OVERRIDE_OSX / CONDA_OVERRIDE_GLIBC, so both answer the same question.

Both run within the same minute, so they see the same package index. The comparison itself is done
later by compare.py; this script only records what happened (exit code, output tail, duration).

    python calibration/calibrate.py --platform osx-arm64 --shard 1/4 --micromamba ./bin/micromamba --out out
"""
import argparse
import asyncio
import datetime as dt
import gzip
import json
import os
import platform as pyplatform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "farm"))
from common import CHANNELS, VIRTUAL  # noqa: E402
from solve import run_one, solver_id  # noqa: E402

OVERRIDES = {  # same machine model as the farm (common.VIRTUAL)
    "osx-arm64": {"CONDA_OVERRIDE_OSX": dict((n, v) for n, v, _ in VIRTUAL["osx-arm64"])["__osx"]},
    "linux-aarch64": {"CONDA_OVERRIDE_GLIBC": dict((n, v) for n, v, _ in VIRTUAL["linux-aarch64"])["__glibc"]},
}
SOLVE_FAIL = ("could not solve", "cannot solve", "nothing provides", "is not installable", "cannot be installed",
              "following packages are incompatible", "no candidates", "conflicting requests", "not found in packages")


def mm_create(mm, specs, plat, root, timeout):
    prefix = Path(tempfile.mkdtemp(prefix="cal-", dir=root)) / "env"
    cmd = [mm, "create", "-y", "--no-rc", "-r", str(root / "mamba"), "-p", str(prefix), "--override-channels",
           *sum((["-c", c] for c in CHANNELS), []), "--strict-channel-priority", *specs]
    env = {**os.environ, **OVERRIDES[plat], "MAMBA_NO_BANNER": "1", "CI": "1"}
    t = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)
        code, out = p.returncode, (p.stdout or "") + "\n" + (p.stderr or "")
    except subprocess.TimeoutExpired as e:
        code, out = "timeout", f"TIMEOUT after {timeout}s\n{(e.stdout or b'')[-4000:]!r}"
    secs = time.time() - t
    shutil.rmtree(prefix.parent, ignore_errors=True)
    if code == 0:
        outcome = "installed"
    elif code == "timeout":
        outcome = "timeout"
    elif any(s in out.lower() for s in SOLVE_FAIL):
        outcome = "solve_failed"
    else:
        outcome = "install_failed"
    return {"cmd": " ".join(cmd), "env_overrides": OVERRIDES[plat], "exit": code, "outcome": outcome,
            "seconds": round(secs, 1)}, out


async def main_async(a):
    from rattler import Gateway
    cases = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))["cases"]
    i, n = map(int, a.shard.split("/"))
    mine = [c for j, c in enumerate(cases) if j % n == i - 1]
    out = Path(a.out)
    (out / "logs").mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="calroot-"))
    mmv = subprocess.run([a.micromamba, "--version"], capture_output=True, text=True).stdout.strip()
    gateway = Gateway()
    rows = []
    for k, c in enumerate(mine, 1):
        when = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        raw, picked, npk, msg, solve_text, ssecs = await run_one(gateway, c["kind"], c["unit"], c["version"], c["specs"], a.platform)
        real, real_text = mm_create(a.micromamba, c["specs"], a.platform, root, a.timeout)
        name = f"{a.platform}__{c['kind']}__{c['unit'].replace('/', '__').replace(':', '_')}@{c['version']}"
        with gzip.open(out / "logs" / f"{name}.log.gz", "wt", encoding="utf-8") as f:
            f.write(f"# case: {c['kind']} {c['unit']} {c['version']}\n# specs: {json.dumps(c['specs'])}\n"
                    f"# machine: {a.runner} ({pyplatform.platform()})\n# started: {when}\n\n"
                    f"## 1. farm solve ({solver_id()}, virtual packages {VIRTUAL[a.platform]}): {raw}\n\n{solve_text}\n\n"
                    f"## 2. real install (micromamba {mmv}): {real['outcome']} (exit {real['exit']}, {real['seconds']}s)\n"
                    f"$ {' '.join(f'{k2}={v}' for k2, v in real['env_overrides'].items())} {real['cmd']}\n\n{real_text}\n")
        rows.append({**c, "platform": a.platform, "runner": a.runner, "started": when, "micromamba": mmv,
                     "solver": solver_id(), "farm_raw": raw, "farm_picked": picked, "farm_message": (msg or "")[:2000],
                     "real": real, "log": f"logs/{name}.log.gz"})
        print(f"[{k}/{len(mine)}] {c['kind']} {c['unit']} {c['version']}: farm={raw} real={real['outcome']} "
              f"({real['seconds']}s)", flush=True)
        if k % 5 == 0:   # keep the runner's disk free
            subprocess.run([a.micromamba, "clean", "-a", "-y", "-r", str(root / "mamba")], capture_output=True)
        (out / f"rows-{a.platform}-{i}.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", required=True, choices=list(OVERRIDES))
    ap.add_argument("--shard", default="1/1")
    ap.add_argument("--micromamba", required=True)
    ap.add_argument("--runner", default=os.environ.get("RUNNER_LABEL", "local"))
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--out", default="out")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
