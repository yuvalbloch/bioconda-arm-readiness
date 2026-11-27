"""The nightly pipeline: snapshot -> plan -> solve -> classify -> reasons -> report -> export.

    python farm/update.py                 # everything, on this machine
    python farm/update.py snapshot        # only: fetch tonight's sources and build the plan
    python farm/update.py post            # only: classify, reasons, report, export (after solve)

The cloud version (.github/workflows/nightly.yml) runs `snapshot` on one machine, solve.py split over
several machines, then `post`.
"""
import subprocess
import sys
from pathlib import Path

FARM = Path(__file__).resolve().parent
SNAPSHOT = ["sources/downloads.py", "sources/repodata.py", "sources/condaforge.py", "sources/corpora.py", "plan.py"]
POST = ["classify.py", "reasons.py", "report.py", "export.py"]


def run(scripts, extra=None):
    for s in scripts:
        args = [sys.executable, "-I", str(FARM / s)] + (extra or {}).get(s, [])
        print(f"== {s}", flush=True)
        subprocess.run(args, check=True)


def main():
    step = sys.argv[1] if len(sys.argv) > 1 else "all"
    rest = sys.argv[2:]
    if step in ("snapshot", "all"):
        run(SNAPSHOT)
    if step == "all":
        run(["solve.py"])
    if step in ("post", "all"):
        run(POST, {"report.py": rest})


if __name__ == "__main__":
    main()
