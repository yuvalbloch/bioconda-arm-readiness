"""Compare calibration runs: farm solver vs real install, and the published table vs real install.

    python calibration/compare.py RUN_DIR [--run-url URL]
RUN_DIR holds the downloaded workflow artifacts (cal-<platform>-<shard>/rows-*.json + logs/).

Two checks per case and platform:
  same-minute   the farm's solver, run on the same machine minutes before the install, says
                solved <=> the real `micromamba create` succeeded
  table         the status the table showed when the cases were picked (an earlier index) says
                "installs" <=> the real install succeeded; differences here can be real changes in
                the channels between the two dates and are listed, not hidden

Writes calibration/summary.json (shown on the site) and calibration/report.md.
"""
import argparse
import glob
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
GOOD = {"native", "noarch", "solves"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--run-url", default="")
    a = ap.parse_args()
    rows = [r for f in sorted(glob.glob(str(Path(a.run_dir) / "cal-*" / "rows-*.json")))
            for r in json.loads(Path(f).read_text(encoding="utf-8"))]
    out = {"run_url": a.run_url, "cases": len({(r["kind"], r["unit"], r["version"]) for r in rows}),
           "date": min(r["started"] for r in rows)[:10] if rows else None, "platforms": {}, "mismatches": [], "table_differences": [],
           "known": []}
    for plat in sorted({r["platform"] for r in rows}):
        rs = [r for r in rows if r["platform"] == plat]
        real_ok = lambda r: r["real"]["outcome"] == "installed"  # noqa: E731
        same = [r for r in rs if r["farm_raw"] in ("solved", "unsolvable") and r["real"]["outcome"] in ("installed", "solve_failed", "install_failed")]
        agree = sum(1 for r in same if (r["farm_raw"] == "solved") == real_ok(r))
        tab = [r for r in rs if r["farm_status_at_selection"].get(plat)]
        tab_agree = sum(1 for r in tab if (r["farm_status_at_selection"][plat] in GOOD) == real_ok(r))
        out["platforms"][plat] = {
            "runner": rs[0]["runner"], "micromamba": rs[0]["micromamba"], "solver": rs[0]["solver"],
            "checked": len(same), "agree": agree,
            "installed": sum(1 for r in rs if real_ok(r)), "failed": sum(1 for r in rs if not real_ok(r)),
            "outcomes": dict(Counter(r["real"]["outcome"] for r in rs)),
            "table_checked": len(tab), "table_agree": tab_agree,
            "excluded": [f"{r['unit']} {r['version']}: farm={r['farm_raw']} real={r['real']['outcome']}" for r in rs if r not in same]}
        for r in same:
            if (r["farm_raw"] == "solved") != real_ok(r):
                out["mismatches"].append({"platform": plat, "unit": r["unit"], "version": r["version"], "farm": r["farm_raw"],
                                          "real": r["real"]["outcome"], "log": r["log"]})
        for r in tab:
            if (r["farm_status_at_selection"][plat] in GOOD) != real_ok(r):
                out["table_differences"].append({"platform": plat, "unit": r["unit"], "version": r["version"],
                                                  "table": r["farm_status_at_selection"][plat], "real": r["real"]["outcome"]})
        for r in rs:
            if r["stratum"] == "known answer":
                out["known"].append({"platform": plat, "unit": r["unit"], "version": r["version"], "farm": r["farm_raw"],
                                     "real": r["real"]["outcome"], "seconds": r["real"]["seconds"]})
    (HERE / "summary.json").write_text(json.dumps(out, indent=1), encoding="utf-8")

    L = [f"# Calibration: {out['date']}", "", f"Workflow run: {a.run_url}" if a.run_url else "", "",
         f"{out['cases']} cases (picked by a fixed rule, see `select_cases.py`), each solved by the farm's solver and then "
         "really installed with `micromamba create` on a real machine, minutes apart.", "",
         "| Platform | Machine | Cases | Farm solver agrees with the real install | Really installed | Failed | Table (earlier index) agrees |",
         "|---|---|---|---|---|---|---|"]
    for plat, p in out["platforms"].items():
        L.append(f"| {plat} | {p['runner']} | {p['checked']} | **{p['agree']}/{p['checked']}** | {p['installed']} | {p['failed']} | {p['table_agree']}/{p['table_checked']} |")
    L += ["", "## Mismatches (farm solver vs real install)", ""]
    L += [f"- {m['platform']} {m['unit']} {m['version']}: farm {m['farm']}, real {m['real']} ({m['log']})" for m in out["mismatches"]] or ["None."]
    L += ["", "## Table vs real install (the table was built from an earlier index)", ""]
    L += [f"- {d['platform']} {d['unit']} {d['version']}: table said {d['table']}, real install {d['real']}" for d in out["table_differences"]] or ["None."]
    L += ["", "## Known answers", "", "| Platform | Package | Version | Farm | Real install |", "|---|---|---|---|---|"]
    L += [f"| {k['platform']} | {k['unit']} | {k['version']} | {k['farm']} | {k['real']} |" for k in out["known"]]
    (HERE / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
