"""Nightly report: what changed since the previous snapshot. Derived from stored statuses only.

    python farm/report.py [--run-url URL]

Writes, in the data dir:
    news.json   the "What's new" list (newest first): a tool became native, lost its arm64 build,
                broke because of a dependency, a new version appeared, a pipeline step broke / was fixed
    report.md   the body of the "Farm status" GitHub issue (rewritten every night, no email)
    alert.md    only when something needs attention: a popular tool or many pipeline steps lost
                Apple Silicon / arm64 Linux support, or the farm itself had many problems
                (the workflow posts it as a comment, which emails the owner)
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from classify import GOOD  # noqa: E402
from common import DATA, PLAN_PATH, SITE_URL, latest_snap_dir, read_json, write_json  # noqa: E402
from db import connect  # noqa: E402

ARM = {"osx-arm64": "Apple Silicon", "linux-aarch64": "arm64 Linux"}
NEWS = DATA / "news.json"
REPORT = DATA / "report.md"
ALERT = DATA / "alert.md"
POPULAR = 300          # alert when one of the 300 most-downloaded tested packages loses arm64 support
MANY_ENVS = 10         # ...or when this many pipeline steps break on one platform in one night


def event_text(kind, unit, ver, plat, old, new, bl, env_changed=False):
    where = ARM[plat]
    what = f"{unit} {ver}" if kind == "pkg" else f"pipeline step {unit}" + (" (environment file updated)" if env_changed else "")
    dep = f" (blocked by {', '.join(bl[:3])})" if bl else ""
    if old is None:
        good = "installs" if new in GOOD else "does not install"
        new_what = f"New version {unit} {ver}" if kind == "pkg" else f"New pipeline step {unit}"
        return "new_version", f"{new_what} {good} on {where}{dep if new not in GOOD else ''}"
    if new in GOOD and old not in GOOD:
        return "fixed", (f"{what} became native on {where}" if new == "native" else f"{what} now installs on {where}")
    if old in GOOD and new not in GOOD:
        if new in ("no_build", "rosetta_only"):
            return "lost", f"{what} lost its {where} build"
        return "broke", f"{what} no longer installs on {where}{dep}"
    return None, None


def merge_platforms(events):
    """The same news on both arm64 platforms becomes one line ("... on Apple Silicon and arm64 Linux")."""
    key = lambda e: (e["type"], e["kind"], e["unit"], e["version"], e["to"] in GOOD, tuple(e["blockers"]))  # noqa: E731
    groups = {}
    for e in events:
        groups.setdefault(key(e), []).append(e)
    out = []
    for es in groups.values():
        if len(es) == 2 and {e["platform"] for e in es} == set(ARM):
            mac = next(e for e in es if e["platform"] == "osx-arm64")
            out.append({**mac, "platform": "osx-arm64+linux-aarch64",
                        "text": mac["text"].replace(ARM["osx-arm64"], "Apple Silicon and arm64 Linux")})
        else:
            out += es
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-url", default="")
    a = ap.parse_args()
    con = connect()
    snaps = [s for (s,) in con.execute("SELECT DISTINCT last_snapshot FROM checks ORDER BY 1")]
    today = snaps[-1] if snaps else None
    plan = read_json(PLAN_PATH, {"packages": []})
    rank = {p["name"]: i for i, p in enumerate(sorted(plan["packages"], key=lambda p: -p["downloads"]))}
    planned = {(p["name"], v) for p in plan["packages"] for v in p["versions"]}
    first_snapshot = con.execute("SELECT MIN(snapshot) FROM status").fetchone()[0]

    # status rows that start tonight, with the cell's previous status (if any)
    # for pipeline steps: did the environment file itself change between the two stored runs?
    env_specs = {}
    for u, p, s, sp in con.execute("SELECT unit, platform, snapshot, specs FROM runs WHERE unit_type='env' ORDER BY snapshot"):
        env_specs.setdefault((u, p), []).append((s, sp))

    def env_changed(u, p):
        hist = env_specs.get((u, p), [])
        return len(hist) >= 2 and hist[-1][0] == today and hist[-1][1] != hist[-2][1]

    events = []
    rows = con.execute("SELECT unit_type, unit, version, platform, snapshot, status, blockers FROM status ORDER BY snapshot").fetchall()
    prev = {}
    for k, u, v, p, s, st, bl in rows:
        key = (k, u, v, p)
        if s == today and s != first_snapshot and p in ARM and k in ("pkg", "env") and st != "infra_error":
            old = prev.get(key)
            if not (k == "pkg" and (u, v) not in planned):
                typ, text = event_text(k, u, v, p, old, st, json.loads(bl), k == "env" and env_changed(u, p))
                if typ:
                    events.append({"date": s, "type": typ, "kind": k, "unit": u, "version": v, "platform": p,
                                   "from": old, "to": st, "blockers": json.loads(bl)[:5], "text": text})
        if st != "infra_error":
            prev[key] = st

    shown = merge_platforms(events)          # display; `events` stays per platform for counting and alerts
    news = [n for n in read_json(NEWS, []) if n["date"] != today]
    news = sorted(shown, key=lambda e: (rank.get(e["unit"], 10**6), e["unit"])) + news
    write_json(NEWS, news[:2000])

    # ---- status issue ----
    day = latest_snap_dir()
    stats = read_json(day / "solve-stats.json", {}) if day else {}
    c = Counter((e["type"], e["platform"]) for e in events)
    lines = [f"# Farm status: {today}", "",
             f"Table: {SITE_URL}", ""]
    if a.run_url:
        lines += [f"Run: {a.run_url}", ""]
    if stats:
        lines += [f"- Solves tonight: {stats.get('solves', 0):,} on {stats.get('machines', 1)} machines "
                  f"({stats.get('same', 0):,} unchanged, {stats.get('new', 0):,} new or changed results)",
                  f"- Farm problems (solver/download errors after one retry): {stats.get('errors', 0)}"
                  + (f", of which {stats.get('error_kept_previous', 0)} kept their previous result" if stats.get('error_kept_previous') else "")]
    lines += ["", "## Changes on arm64 tonight", ""]
    if not shown:
        lines.append("None.")
    for typ, label in (("lost", "Lost an arm64 build"), ("broke", "No longer installs"), ("fixed", "Became native / installs again"),
                       ("new_version", "New versions")):
        es = [e for e in shown if e["type"] == typ]
        if es:
            lines.append(f"### {label} ({len(es)})")
            lines += [f"- {e['text']}" for e in es[:50]]
            if len(es) > 50:
                lines.append(f"- … and {len(es) - 50} more")
            lines.append("")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---- alert (comment -> email) only when something needs attention ----
    bad = [e for e in events if e["type"] in ("lost", "broke") and e["kind"] == "pkg" and rank.get(e["unit"], 10**6) < POPULAR]
    env_bad = {p: n for (t, p), n in Counter((e["type"], e["platform"]) for e in events if e["kind"] == "env" and e["type"] in ("lost", "broke")).items() if n >= MANY_ENVS}
    alert = []
    if bad:
        alert.append("Popular tools lost arm64 support tonight:\n" + "\n".join(f"- {e['text']}" for e in bad[:20]))
    for p, n in env_bad.items():
        alert.append(f"{n} pipeline steps stopped installing on {ARM[p]} tonight.")
    if stats and stats.get("solves") and stats.get("errors", 0) > 0.02 * stats["solves"]:
        alert.append(f"The farm had {stats['errors']} solver/download errors out of {stats['solves']} solves (more than 2%).")
    if alert:
        ALERT.write_text("\n\n".join(alert) + f"\n\nDetails: {SITE_URL}#news\n", encoding="utf-8")
    elif ALERT.exists():
        ALERT.unlink()
    print(f"report {today}: {len(events)} events {dict(c)}; alert={'yes' if alert else 'no'}")


if __name__ == "__main__":
    main()
