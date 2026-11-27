"""Real-machine layer: install a tool for real and run the package's OWN test, on real arm64 machines.

The test is not written by us: conda packages carry the test that bioconda's CI ran when it built
them (info/test/run_test.sh / .py / .pl / .r from conda-build, or info/tests/tests.yaml from
rattler-build). We install the package alone (conda-forge + bioconda, strict priority, the same
machine model as the farm), install the test's own extra requirements if it declares any, and run it.

    python farm/smoke.py select --top 300 --shards 4 --out smoke-cases.json      (any machine, needs results.db)
    python farm/smoke.py run --cases smoke-cases.json --platform osx-arm64 --shard 1/4 \
                             --micromamba ./bin/micromamba --out out             (ON the real machine)

Raw outcomes (stored as unit_type 'smoke'; the status rules are in classify.py):
  passed          installed, and the package's own test exited 0
  no_test         installed; the package carries no runnable test
  test_failed     installed, but its own test failed (log attached)
  install_failed  `micromamba create` failed although the solver said it installs (log attached)
  error           timeout or a download problem (never shown as a failure)
"""
import argparse
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CHANNELS, PLAN_PATH, VIRTUAL, read_json, write_json  # noqa: E402

ARM = ["osx-arm64", "linux-aarch64"]
OVERRIDES = {
    "osx-arm64": {"CONDA_OVERRIDE_OSX": dict((n, v) for n, v, _ in VIRTUAL["osx-arm64"])["__osx"]},
    "linux-aarch64": {"CONDA_OVERRIDE_GLIBC": dict((n, v) for n, v, _ in VIRTUAL["linux-aarch64"])["__glibc"]},
}
RUNNERS = {"conda-build": [("run_test.sh", ["bash", "-e"]), ("run_test.py", ["python"]), ("run_test.pl", ["perl"]),
                           ("run_test.r", ["Rscript"]), ("run_test.R", ["Rscript"])]}


# ------------------------------------------------------------------ select (needs the results db)

def select(a):
    from classify import current
    from db import connect, latest_rows
    con = connect()
    cur = current(con)
    prev = latest_rows(con)
    checks = {(k, u, v, p): n for k, u, v, p, n in con.execute("SELECT unit_type, unit, version, platform, n FROM checks WHERE unit_type='smoke'")}
    plan = read_json(PLAN_PATH)
    cases = {p: [] for p in ARM}
    for pk in sorted(plan["packages"], key=lambda x: -x["downloads"]):
        if not pk["versions"]:
            continue
        v = pk["versions"][0]
        for plat in ARM:
            c = cur.get(("pkg", pk["name"], v, plat))
            if c and c["status"] in ("native", "noarch") and len(cases[plat]) < a.top:
                old = prev.get(("smoke", pk["name"], v, plat))
                cases[plat].append({"name": pk["name"], "version": v, "prev_sig": old[1] if old else None,
                                    "prev_n": checks.get(("smoke", pk["name"], v, plat), 0)})
    write_json(Path(a.out), {"shards": a.shards, "cases": cases})
    print({p: len(c) for p, c in cases.items()})


# ------------------------------------------------------------------ run (on the real machine)

def sh(cmd, env, timeout, cwd=None):
    t = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout, cwd=cwd)
        return p.returncode, (p.stdout or "") + (p.stderr or ""), time.time() - t
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return "timeout", f"{out}\nTIMEOUT after {timeout}s", time.time() - t


def find_info(root, name, version):
    hits = sorted((root / "pkgs").glob(f"{name}-{version}-*/info"), key=lambda p: p.stat().st_mtime)
    return hits[-1] if hits else None


def tests_of(info):
    """[(label, argv-prefix, file)] for conda-build packages, or rattler-build tests.yaml scripts."""
    out = []
    tdir = info / "test"
    for fn, argv in RUNNERS["conda-build"]:
        if (tdir / fn).exists():
            out.append((fn, argv, tdir / fn))
    ty = info / "tests" / "tests.yaml"
    if not out and ty.exists():
        import yaml
        doc = yaml.safe_load(ty.read_text(encoding="utf-8")) or []
        for i, t in enumerate(doc if isinstance(doc, list) else []):
            if isinstance(t, dict) and t.get("script"):
                script = t["script"]
                body = "\n".join(script) if isinstance(script, list) else str(script.get("content", "")) if isinstance(script, dict) else str(script)
                f = info / "tests" / f"_smoke_{i}.sh"
                f.write_text("set -e\n" + body + "\n", encoding="utf-8")
                out.append((f"tests.yaml script {i + 1}", ["bash", "-e"], f))
            elif isinstance(t, dict) and isinstance(t.get("python"), dict) and t["python"].get("imports"):
                f = info / "tests" / f"_smoke_{i}.py"
                f.write_text("".join(f"import {m}\n" for m in t["python"]["imports"]), encoding="utf-8")
                out.append((f"tests.yaml python imports {i + 1}", ["python"], f))
    return out


def test_requirements(info):
    f = info / "test" / "test_time_dependencies.json"
    try:
        return [str(x) for x in json.loads(f.read_text(encoding="utf-8"))] if f.exists() else []
    except ValueError:
        return []


def run_case(mm, c, plat, root, timeout):
    prefix = Path(tempfile.mkdtemp(prefix="smk-", dir=root)) / "env"
    env = {**os.environ, **OVERRIDES[plat], "MAMBA_NO_BANNER": "1", "CI": "1", "MAMBA_ROOT_PREFIX": str(root / "mamba")}
    base = lambda sub: [mm, sub, "--no-rc", "-r", str(root / "mamba")]  # noqa: E731
    chans = [x for ch in CHANNELS for x in ("-c", ch)] + ["--override-channels", "--strict-channel-priority"]
    spec = f"{c['name']}=={c['version']}"
    log = [f"$ {' '.join(f'{k}={v}' for k, v in OVERRIDES[plat].items())} micromamba create -p <prefix> {' '.join(chans)} {spec}"]
    code, out, secs = sh(base("create") + ["-y", "-p", str(prefix), *chans, spec], env, timeout)
    log.append(out[-20000:])
    tests, total = [], secs
    if code != 0:
        low = out.lower()
        download_problem = "could not solve" not in low and ("download" in low or "transfer" in low or "connection" in low)
        raw = "error" if code == "timeout" or download_problem else "install_failed"
    else:
        info = find_info(root / "mamba", c["name"], c["version"])
        found = tests_of(info) if info else []
        reqs = test_requirements(info) if info else []
        if reqs:
            log.append(f"\n$ micromamba install (the test's own requirements): {' '.join(reqs)}")
            rc, o, s2 = sh(base("install") + ["-y", "-p", str(prefix), *chans, *reqs], env, timeout)
            log.append(o[-8000:])
            total += s2
        if not found:
            raw = "no_test"
            log.append("\n(the package carries no runnable test)")
        else:
            raw = "passed"
            for label, argv, f in found:
                tenv = {**env, "PREFIX": str(prefix)}   # conda-build tests expect $PREFIX, as in bioconda's CI
                rc, o, s3 = sh(base("run") + ["-p", str(prefix), *argv, str(f)], tenv, timeout, cwd=str(f.parent))
                total += s3
                tests.append(label)
                log.append(f"\n## test: {label} -> exit {rc} ({s3:.1f}s)\n--- {f.name} ---\n{f.read_text(encoding='utf-8', errors='replace')[:4000]}\n--- output ---\n{o[-12000:]}")
                if rc != 0:
                    raw = "error" if rc == "timeout" else "test_failed"
                    break
    shutil.rmtree(prefix.parent, ignore_errors=True)
    return raw, tests, "\n".join(log), total


def run(a):
    data = json.loads(Path(a.cases).read_text(encoding="utf-8"))
    i, n = map(int, a.shard.split("/"))
    mine = [c for j, c in enumerate(data["cases"][a.platform]) if j % n == i - 1]
    out = Path(a.out)
    root = Path(tempfile.mkdtemp(prefix="smkroot-"))
    mmv = subprocess.run([a.micromamba, "--version"], capture_output=True, text=True).stdout.strip()
    snap = dt.datetime.now(dt.timezone.utc).date().isoformat()
    runs, checks = [], []
    for k, c in enumerate(mine, 1):
        raw, tests, text, secs = run_case(a.micromamba, c, a.platform, root, a.timeout)
        cell = {"unit_type": "smoke", "unit": c["name"], "version": c["version"], "platform": a.platform}
        print(f"[{k}/{len(mine)}] {c['name']} {c['version']}: {raw} ({secs:.0f}s) {tests}", flush=True)
        if raw == "error" and c["prev_sig"] and c["prev_sig"] != "error":
            pass                                          # a farm problem never replaces a real outcome
        elif c["prev_sig"] == raw:
            checks.append({**cell, "last_snapshot": snap, "n": c["prev_n"] + 1})
        else:
            rel = f"{snap}/{a.platform}/smoke/{c['name']}@{c['version']}.log.gz"
            p = out / "logs" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(p, "wt", encoding="utf-8") as f:
                f.write(f"# unit: smoke {c['name']} {c['version']}\n# platform: {a.platform} (real machine: {a.runner}, {pyplatform.platform()})\n"
                        f"# channels: {', '.join(CHANNELS)} (strict priority)\n# system pinned to: {OVERRIDES[a.platform]}\n"
                        f"# installer: micromamba {mmv}\n# date: {snap}\n# outcome: {raw}\n\n{text}\n")
            runs.append({**cell, "snapshot": snap, "raw": raw, "specs": json.dumps([f"{c['name']} =={c['version']}"]),
                         "picked": json.dumps({"tests": tests, "runner": a.runner}), "n_pkgs": None,
                         "message": text[-4000:] if raw in ("test_failed", "install_failed", "error") else None,
                         "log": rel, "seconds": round(secs, 1), "solver": f"micromamba {mmv}",
                         "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
            checks.append({**cell, "last_snapshot": snap, "n": 1})
        if k % 5 == 0:
            subprocess.run([a.micromamba, "clean", "-a", "-y", "-r", str(root / "mamba")], capture_output=True)
        out.mkdir(parents=True, exist_ok=True)
        (out / "rows.json").write_text(json.dumps({"snapshot": snap, "runs": runs, "checks": checks}))
    print(f"done: {len(runs)} new results, {len(checks)} checks")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("select")
    s.add_argument("--top", type=int, default=300)
    s.add_argument("--shards", type=int, default=4)
    s.add_argument("--out", default="smoke-cases.json")
    r = sub.add_parser("run")
    r.add_argument("--cases", required=True)
    r.add_argument("--platform", required=True, choices=ARM)
    r.add_argument("--shard", default="1/1")
    r.add_argument("--micromamba", required=True)
    r.add_argument("--runner", default=os.environ.get("RUNNER_LABEL", "local"))
    r.add_argument("--timeout", type=int, default=900)
    r.add_argument("--out", default="out")
    a = ap.parse_args()
    select(a) if a.cmd == "select" else run(a)


if __name__ == "__main__":
    main()
