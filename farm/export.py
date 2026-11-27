"""Results -> the static website, built in $BIOARM/site (the source files in the repo's site/ are copied in).

Everything shown is read from the results database (statuses from classify.py, which only applies
fixed rules to stored solver runs). Pin suggestions are only versions that passed in our own runs.

Writes:
  site/data/summary.json     headline numbers
  site/data/packages.json    every tested package: all tested versions x platforms (status codes)
  site/data/envs.json        every nf-core module / Snakemake wrapper environment x platform
  site/data/news.json        "What's new" (from report.py)
  site/pkg/<name>/index.html one static page per tool (versions, logs, history)
  site/logs/<...>.txt        the full solver output behind every shown result
  site/feed.xml, sitemap.xml, robots.txt
"""
import gzip
import html
import json
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from classify import GOOD, current  # noqa: E402
from common import DATA, LOGS, PLAN_PATH, PLATFORMS, SITE_SRC, SITE, SITE_URL, TARGET, VIRTUAL, latest_snap_dir, read_json, write_json  # noqa: E402
from db import connect  # noqa: E402

CODE = {"native": "N", "noarch": "A", "solves": "S", "rosetta_only": "R", "no_build": "B", "unsolvable": "U",
        "needs_newer_os": "O", "infra_error": "E"}
PLAT_NAME = {"osx-arm64": "Apple Silicon", "linux-aarch64": "arm64 Linux", "osx-64": "Intel Mac", "linux-64": "x86 Linux"}
COLS = ["linux-64", "linux-aarch64", "osx-64", "osx-arm64"]
LABEL = {"native": "✓ native", "noarch": "✓ noarch", "solves": "✓ installs", "rosetta_only": "✗ Intel only (Rosetta)",
         "no_build": "✗ no build", "unsolvable": "✗ dependency missing", "needs_newer_os": "⚙ newer OS",
         "infra_error": "? farm problem"}
REASON = {"not_opted_in": "the recipe never opted in to this platform (bioconda builds arm64 only on request)",
          "opted_in_no_build": "the recipe asks for this platform, but no build of this version exists (build failed or timed out)",
          "dropped": "an older version had a build for this platform; this one has none",
          "no_recipe": "the recipe is no longer in bioconda-recipes (removed or renamed)"}


def esc(s):
    return html.escape(str(s), quote=True)


def slug(unit):
    return unit.replace("/", "__").replace(":", "_")


def log_url(rel):
    """logs/<snap>/<plat>/<kind>/<unit>@<ver>.log.gz -> site path of its text copy."""
    return "logs/" + rel[:-len(".log.gz")] + ".txt" if rel and rel.endswith(".log.gz") else None


def env_link(eid):
    src, path = eid.split(":", 1)
    if src == "nf-core":
        return f"https://github.com/nf-core/modules/tree/master/modules/nf-core/{path}"
    return f"https://github.com/snakemake/snakemake-wrappers/tree/master/bio/{path}"


def build_model(con, plan, corpora, reasons):
    cur = current(con)
    logs = {(k, u, v, p, s): l for k, u, v, p, s, l in con.execute("SELECT unit_type, unit, version, platform, snapshot, log FROM runs")}
    checked = {(k, u, v, p): (s, n) for k, u, v, p, s, n in con.execute("SELECT unit_type, unit, version, platform, last_snapshot, n FROM checks")}
    reason = defaultdict(dict)
    for r in reasons.get("rows", []):
        reason[r["package"]][r["platform"]] = r["reason"]

    def cell(kind, unit, ver, plat):
        c = cur.get((kind, unit, ver, plat))
        if not c:
            return None
        return {"status": c["status"], "blockers": c["blockers"], "since": c["since"],
                "log": log_url(logs.get((kind, unit, ver, plat, c["run_snapshot"]))),
                "checked": checked.get((kind, unit, ver, plat), (c["run_snapshot"], 1))[0]}

    used_by = defaultdict(list)
    for e in corpora.get("envs", []):
        for d in e["deps"]:
            n = d.split("::", 1)[-1].strip().split()[0].split("=")[0].split("<")[0].split(">")[0].lower()
            used_by[n].append(e["id"])

    pkgs = []
    for p in plan["packages"]:
        name = p["name"]
        vers = []
        for v in p["versions"]:
            vers.append({"version": v, "cells": {pl: cell("pkg", name, v, pl) for pl in COLS}})
        pin = {}
        for pl in COLS:
            newest = vers[0]["cells"][pl] if vers else None
            if newest and newest["status"] not in GOOD:
                ok = [x["version"] for x in vers[1:] if x["cells"][pl] and x["cells"][pl]["status"] in GOOD]
                if ok:
                    pin[pl] = ok[0]
        pkgs.append({"name": name, "downloads": p["downloads"], "pipeline_uses": p["pipeline_uses"],
                     "conda_forge": bool(p.get("conda_forge") and not p.get("builds")),
                     "versions": vers, "pin": pin, "reasons": reason.get(name, {}),
                     "used_by": sorted(set(used_by.get(name, [])))})
    envs = []
    for e in corpora.get("envs", []):
        cells = {pl: cell("env", e["id"], "-", pl) for pl in COLS}
        if any(cells.values()):
            envs.append({"id": e["id"], "deps": e["deps"], "cells": cells})
    return pkgs, envs


def summarize(pkgs, envs, snapshot):
    def st(p, pl):
        c = p["versions"][0]["cells"][pl] if p["versions"] else None
        return c["status"] if c else None
    s = {"snapshot": snapshot, "packages": len(pkgs), "package_versions": sum(len(p["versions"]) for p in pkgs),
         "envs": len(envs), "newest": {}, "envs_by_source": {}}
    for pl in COLS:
        s["newest"][pl] = dict(Counter(st(p, pl) for p in pkgs if st(p, pl)))
    s["intel_only_tools"] = sum(1 for p in pkgs if st(p, "osx-64") in GOOD and st(p, TARGET) not in GOOD | {"needs_newer_os", "infra_error", None})
    for src in ("nf-core", "snakemake"):
        es = [e for e in envs if e["id"].startswith(src + ":")]
        row = {"total": len(es)}
        for pl in COLS:
            row[pl] = dict(Counter(e["cells"][pl]["status"] for e in es if e["cells"][pl]))
        row["intel_mac_only"] = sum(1 for e in es if e["cells"]["osx-arm64"] and e["cells"]["osx-arm64"]["status"] == "rosetta_only")
        s["envs_by_source"][src] = row
    s["solve_cells"] = sum(len([c for c in v["cells"].values() if c]) for p in pkgs for v in p["versions"]) + \
        sum(len([c for c in e["cells"].values() if c]) for e in envs)
    return s


def compact_packages(pkgs):
    out = []
    for p in pkgs:
        v, b = [], {}
        for x in p["versions"]:
            codes = "".join(CODE.get(x["cells"][pl]["status"], "-") if x["cells"][pl] else "-" for pl in COLS)
            v.append([x["version"], codes])
            for pl in COLS:
                c = x["cells"][pl]
                if c and c["blockers"]:
                    b[f"{x['version']}|{pl}"] = c["blockers"][:6]
        newest = p["versions"][0]["cells"] if p["versions"] else {}
        out.append({"n": p["name"], "d": p["downloads"], "u": len(p["used_by"]), "cf": int(p["conda_forge"]),
                    "v": v, "b": b, "pin": p["pin"], "r": p["reasons"],
                    "since": newest.get(TARGET, {}).get("since") if newest.get(TARGET) else None})
    return out


def compact_envs(envs):
    out = []
    for e in envs:
        codes = "".join(CODE.get(e["cells"][pl]["status"], "-") if e["cells"][pl] else "-" for pl in COLS)
        out.append({"id": e["id"], "s": codes, "deps": e["deps"],
                    "b": {pl: e["cells"][pl]["blockers"][:6] for pl in COLS if e["cells"][pl] and e["cells"][pl]["blockers"]},
                    "l": {pl: e["cells"][pl]["log"] for pl in COLS if e["cells"][pl]}})
    return out


# ---------------------------------------------------------------- per-tool pages

def verdict(p):
    """One plain sentence from fixed templates (never free text)."""
    if not p["versions"]:
        return "Not tested."
    v0 = p["versions"][0]
    c = v0["cells"].get(TARGET)
    n, ver = esc(p["name"]), esc(v0["version"])
    if not c:
        return f"{n} {ver} has not been solved for Apple Silicon yet."
    s = c["status"]
    if s == "native":
        t = f"<b>Yes.</b> {n} {ver} (the newest version) has a native Apple Silicon build and installs with the standard bioconda setup."
    elif s == "noarch":
        t = f"<b>Yes.</b> {n} {ver} (the newest version) is platform-independent (noarch) and installs on Apple Silicon."
    elif s == "rosetta_only":
        t = (f"<b>Only through Rosetta.</b> {n} {ver} (the newest version) has an Intel-Mac build but no Apple Silicon build. "
             f"It runs today through Rosetta 2, which macOS 28 (autumn 2027) removes.")
    elif s == "no_build":
        t = f"<b>No.</b> {n} {ver} (the newest version) has no build for Apple Silicon (and none for Intel Macs either)."
    elif s == "unsolvable":
        t = (f"<b>No.</b> {n} {ver} (the newest version) has a build, but it can't be installed on Apple Silicon: "
             f"a dependency has no Apple Silicon build ({esc(', '.join(c['blockers'][:4]) or 'see the log')}).")
    elif s == "needs_newer_os":
        t = f"<b>Needs a newer macOS.</b> {n} {ver} requires a newer macOS than the 14.0 we test for ({esc(', '.join(c['blockers']))})."
    else:
        t = f"The last check of {n} {ver} hit a farm problem; it will be retried."
    if p["pin"].get(TARGET):
        t += f" The newest tested version that installs on Apple Silicon is <b>{esc(p['pin'][TARGET])}</b>."
    return t


def page(title, desc, body, depth):
    up = "../" * depth
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><meta name="description" content="{esc(desc)}">
<link rel="stylesheet" href="{up}style.css"><link rel="alternate" type="application/rss+xml" title="What's new" href="{up}feed.xml">
</head><body><header class="top"><a class="brand" href="{up}">Bioconda on Apple Silicon</a>
<nav><a href="{up}#gaps">Apple Silicon gaps</a><a href="{up}#tools">All tools</a><a href="{up}#pipelines">Pipelines</a><a href="{up}#news">What's new</a><a href="{up}#check">Check an environment</a><a href="{up}#about">About</a></nav></header>
<main>{body}</main>
<footer>Every result comes from a stored solver run; click a cell for its log. <a href="https://github.com/yuvalbloch/bioconda-arm-readiness">Code and data</a> ·
<a href="https://github.com/yuvalbloch/bioconda-arm-readiness/blob/main/docs/methodology.md">Methodology</a> ·
<a href="https://github.com/yuvalbloch/bioconda-arm-readiness/issues/new?title=Wrong%20result">Report a wrong result</a></footer>
</body></html>
"""


def tool_page(p, history, snapshot):
    name = p["name"]
    rows = []
    for x in p["versions"]:
        tds = []
        for pl in COLS:
            c = x["cells"][pl]
            if not c:
                tds.append("<td>–</td>")
                continue
            extra = f"<small>{esc(', '.join(c['blockers'][:3]))}</small>" if c["blockers"] else ""
            link = f' href="../../{esc(c["log"])}"' if c["log"] else ""
            tds.append(f'<td class="st-{c["status"]}"><a{link} title="solver log (checked {esc(c["checked"])})">'
                       f'{esc(LABEL[c["status"]])}</a>{extra}</td>')
        rows.append(f"<tr><th>{esc(x['version'])}</th>{''.join(tds)}</tr>")
    head = "".join(f"<th>{esc(PLAT_NAME[pl])}<br><small>{pl}</small></th>" for pl in COLS)
    reasons = "".join(f"<li><b>{esc(PLAT_NAME[pl])}:</b> {esc(REASON[r])}"
                      f"{' (<a href=https://github.com/bioconda/bioconda-recipes/tree/master/recipes/' + esc(name) + '>recipe</a>)' if r != 'no_recipe' else ''}</li>"
                      for pl, r in sorted(p["reasons"].items()))
    pin = p["pin"].get(TARGET) or (p["versions"][0]["version"] if p["versions"] and
                                   (p["versions"][0]["cells"].get(TARGET) or {}).get("status") in GOOD else None)
    install = (f"<h2>Install on Apple Silicon</h2><pre>micromamba create -n {esc(name)} -c conda-forge -c bioconda "
               f"--strict-channel-priority {esc(name)}={esc(pin)}</pre>") if pin else ""
    used = ""
    if p["used_by"]:
        items = "".join(f'<li><a href="{esc(env_link(e))}">{esc(e)}</a></li>' for e in p["used_by"][:60])
        more = f"<li>… and {len(p['used_by']) - 60} more</li>" if len(p["used_by"]) > 60 else ""
        used = f"<h2>Used by {len(p['used_by'])} pipeline steps</h2><ul class=cols>{items}{more}</ul>"
    hist = ""
    if history:
        items = "".join(f"<li>{esc(s)}: {esc(v)} on {esc(PLAT_NAME[pl])}: {esc(LABEL[st])}</li>" for s, v, pl, st in history[-40:])
        hist = f"<h2>History</h2><ul>{items}</ul>"
    cf = ("<p class=note>With the standard setup (conda-forge first, strict priority) this package comes from "
          "<b>conda-forge</b>, so the conda-forge version is tested.</p>") if p["conda_forge"] else ""
    body = (f"<p class=crumb><a href='../../#tools'>All tools</a> / {esc(name)}</p>"
            f"<h1>Does {esc(name)} work on Apple Silicon?</h1><p class=verdict>{verdict(p)}</p>{cf}"
            f"<table class=matrix><thead><tr><th>Version</th>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
            f"<p class=small>Solved for macOS 14.0 / glibc 2.28 machines with conda-forge + bioconda (strict priority), "
            f"package index of {esc(snapshot)}. Click a result for the full solver log.</p>"
            + (f"<h2>Why the arm64 build is missing</h2><ul>{reasons}</ul>" if reasons else "")
            + install + used + hist)
    desc = f"Does {name} install natively on Apple Silicon (M1–M4, osx-arm64) and arm64 Linux? Status of every recent version, from real solver runs."
    return page(f"{name} on Apple Silicon (osx-arm64) and arm64 Linux", desc, body, 2)


# ---------------------------------------------------------------- feed / sitemap

def feed(news):
    items = []
    for n in news[:100]:
        link = SITE_URL + (f"pkg/{n['unit']}/" if n["kind"] == "pkg" else "#pipelines")
        items.append(f"<item><title>{esc(n['text'])}</title><link>{esc(link)}</link>"
                     f"<guid isPermaLink='false'>{esc(n['date'] + '|' + n['unit'] + '|' + n['version'] + '|' + n['platform'] + '|' + n['to'])}</guid>"
                     f"<pubDate>{esc(n['date'])}</pubDate></item>")
    return ("<?xml version='1.0' encoding='utf-8'?><rss version='2.0'><channel><title>Bioconda on Apple Silicon: what's new</title>"
            f"<link>{SITE_URL}</link><description>Bioconda tools that became native, lost a build or broke on Apple Silicon and arm64 Linux.</description>"
            + "".join(items) + "</channel></rss>\n")


def copy_logs(con, wanted):
    dst_root = SITE / "logs"
    n = 0
    for rel in wanted:
        src = LOGS / rel
        dst = SITE / log_url(rel)
        if dst.exists() or not src.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(src, "rt", encoding="utf-8", errors="replace") as f:
            dst.write_text(f.read(), encoding="utf-8")
        n += 1
    return dst_root, n


def main():
    con = connect()
    day = latest_snap_dir()
    plan = read_json(PLAN_PATH)
    corpora = read_json(day / "corpora.json", {"envs": []})
    reasons = read_json(day / "reasons.json", {"rows": []})
    snapshot = max(r[0] for r in con.execute("SELECT MAX(last_snapshot) FROM checks"))
    pkgs, envs = build_model(con, plan, corpora, reasons)
    summary = summarize(pkgs, envs, snapshot)
    cal = read_json(SITE_SRC.parent / "calibration" / "summary.json")
    if cal:
        summary["calibration"] = cal
    summary["virtual_packages"] = {pl: [f"{n}={v}" for n, v, b in VIRTUAL[pl] if n != "__unix"] for pl in COLS}
    summary["stats"] = read_json(day / "solve-stats.json")

    SITE.mkdir(parents=True, exist_ok=True)
    for f in SITE_SRC.glob("*"):
        if f.is_file():
            shutil.copy2(f, SITE / f.name)
    data = SITE / "data"
    write_json(data / "summary.json", summary)
    (data / "packages.json").write_text(json.dumps({"cols": COLS, "pkgs": compact_packages(pkgs)}, separators=(",", ":")), encoding="utf-8")
    (data / "envs.json").write_text(json.dumps({"cols": COLS, "envs": compact_envs(envs)}, separators=(",", ":")), encoding="utf-8")
    news = read_json(DATA / "news.json", [])
    (data / "news.json").write_text(json.dumps(news[:500], separators=(",", ":")), encoding="utf-8")

    hist = defaultdict(list)
    first = con.execute("SELECT MIN(snapshot) FROM status").fetchone()[0]
    for u, v, pl, s, st in con.execute("SELECT unit, version, platform, snapshot, status FROM status "
                                       "WHERE unit_type='pkg' AND snapshot > ? ORDER BY snapshot", (first,)):
        hist[u].append((s, v, pl, st))
    pkg_root = SITE / "pkg"
    if pkg_root.exists():
        shutil.rmtree(pkg_root)
    for p in pkgs:
        d = pkg_root / p["name"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(tool_page(p, hist.get(p["name"]), snapshot), encoding="utf-8")

    wanted = {c["log"] for p in pkgs for x in p["versions"] for c in x["cells"].values() if c and c["log"]}
    wanted |= {c["log"] for e in envs for c in e["cells"].values() if c and c["log"]}
    rels = [r for (r,) in con.execute("SELECT log FROM runs") if log_url(r) in wanted]
    _, n = copy_logs(con, rels)

    (SITE / "feed.xml").write_text(feed(news), encoding="utf-8")
    urls = [SITE_URL] + [f"{SITE_URL}pkg/{p['name']}/" for p in pkgs]
    (SITE / "sitemap.xml").write_text("<?xml version='1.0' encoding='utf-8'?><urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
                                      + "".join(f"<url><loc>{esc(u)}</loc><lastmod>{snapshot}</lastmod></url>" for u in urls)
                                      + "</urlset>\n", encoding="utf-8")
    (SITE / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}sitemap.xml\n", encoding="utf-8")
    print(f"exported {len(pkgs)} packages, {len(envs)} envs, {len(pkgs)} tool pages, {n} new log files; "
          f"intel-only tools: {summary['intel_only_tools']}")


if __name__ == "__main__":
    main()
