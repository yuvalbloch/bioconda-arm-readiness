"""Reason labels for arm64 gaps, from the bioconda-recipes repository. Labels only - never a status.

For every package whose NEWEST tested version is `rosetta_only` / `no_build` on osx-arm64 or
linux-aarch64 (and that bioconda serves, i.e. not shadowed by conda-forge), read its recipe:

  not_opted_in       the recipe doesn't list the platform under extra.additional-platforms
                     (arm64 builds are opt-in per recipe in bioconda; a one-line change may fix it)
  opted_in_no_build  the recipe asks for the platform but no build of this version exists
                     (the build failed or timed out in bioconda's CI)
  dropped            an older version had a build for this platform and the newest has none
  no_recipe          the recipe is no longer in bioconda-recipes (removed or renamed)

Recipes are fetched from raw.githubusercontent.com at the commit recorded in the snapshot.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import PLAN_PATH, http_get, latest_snap_dir, read_json, write_json  # noqa: E402
from classify import current  # noqa: E402
from db import connect  # noqa: E402

ARM = ["osx-arm64", "linux-aarch64"]
ADD_RX = re.compile(r"additional-platforms:\s*\n((?:\s*-\s*[\w\-]+\s*\n?)+)")


def recipe_platforms(text):
    m = ADD_RX.search(text)
    return re.findall(r"-\s*([\w\-]+)", m.group(1)) if m else []


def main():
    day = latest_snap_dir()
    plan = read_json(PLAN_PATH)
    av = read_json(day / "availability.json")
    commit = json.loads(http_get("https://api.github.com/repos/bioconda/bioconda-recipes/commits/master"))["sha"]
    cache = day / "recipes"
    cache.mkdir(exist_ok=True)

    con = connect()
    newest = {p["name"]: p["versions"][0] for p in plan["packages"] if not p.get("conda_forge") and p["versions"]}
    gaps = {}
    for (kind, unit, ver, plat), c in current(con).items():
        if kind == "pkg" and plat in ARM and c["status"] in ("rosetta_only", "no_build") and newest.get(unit) == ver:
            gaps.setdefault(unit, {})[plat] = c["status"]

    rows = []
    for name, plats in sorted(gaps.items()):
        f = cache / f"{name}.meta.yaml"
        if not f.exists():
            text = None
            for fn in ("meta.yaml", "recipe.yaml"):
                try:
                    text = http_get(f"https://raw.githubusercontent.com/bioconda/bioconda-recipes/{commit}/recipes/{name}/{fn}", retries=2).decode("utf-8", "replace")
                    break
                except Exception:  # noqa: BLE001 - 404 = not under that file name
                    continue
            f.write_text(text if text is not None else "", encoding="utf-8")
        text = f.read_text(encoding="utf-8")
        opted = recipe_platforms(text) if text else []
        for plat in plats:
            older = [v for v, b in av.get(name, {}).items() if plat in b["platforms"]]
            if not text:
                label = "no_recipe"
            elif plat not in opted:
                label = "dropped" if older else "not_opted_in"
            else:
                label = "dropped" if older else "opted_in_no_build"
            rows.append({"package": name, "version": newest[name], "platform": plat, "reason": label,
                         "recipe_platforms": opted, "older_versions_with_build": sorted(older)[-3:]})
    write_json(day / "reasons.json", {"recipes_commit": commit, "rows": rows})
    from collections import Counter
    for (p, r), n in sorted(Counter((x["platform"], x["reason"]) for x in rows).items()):
        print(f"  {p:14s} {r:18s} {n}")


if __name__ == "__main__":
    main()
