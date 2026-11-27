# Calibration: 2026-11-27

Workflow run: https://github.com/yuvalbloch/bioconda-arm-readiness/actions/runs/46005140050

102 cases (picked by a fixed rule, see `select_cases.py`), each solved by the farm's solver and then really installed with `micromamba create` on a real machine, minutes apart.

| Platform | Machine | Cases | Farm solver agrees with the real install | Really installed | Failed | Table (earlier index) agrees |
|---|---|---|---|---|---|---|
| linux-aarch64 | ubuntu-24.04-arm | 102 | **102/102** | 57 | 45 | 102/102 |
| osx-arm64 | macos-15 | 102 | **102/102** | 47 | 55 | 102/102 |

## Mismatches (farm solver vs real install)

None.

## Table vs real install (the table was built from an earlier index)

None.

## Known answers

| Platform | Package | Version | Farm | Real install |
|---|---|---|---|---|
| linux-aarch64 | subread | 2.0.6 | solved | installed |
| linux-aarch64 | prokka | 1.15.6 | solved | installed |
| linux-aarch64 | subread | 2.0.8 | solved | installed |
| linux-aarch64 | samtools | 1.25 | solved | installed |
| linux-aarch64 | preseq | 3.2.0 | solved | installed |
| linux-aarch64 | quast | 5.3.0 | solved | installed |
| osx-arm64 | subread | 2.0.6 | unsolvable | solve_failed |
| osx-arm64 | prokka | 1.15.6 | unsolvable | solve_failed |
| osx-arm64 | subread | 2.0.8 | solved | installed |
| osx-arm64 | samtools | 1.25 | solved | installed |
| osx-arm64 | preseq | 3.2.0 | unsolvable | solve_failed |
| osx-arm64 | quast | 5.3.0 | unsolvable | solve_failed |
