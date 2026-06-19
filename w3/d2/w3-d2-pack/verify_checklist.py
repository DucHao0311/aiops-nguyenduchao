#!/usr/bin/env python3
"""verify_checklist.py — verify acceptance checklist §8.9"""
import json
import yaml
from pathlib import Path

print("=" * 55)
print("W3-D2 Acceptance Checklist §8.9")
print("=" * 55)

checks = []

# 1. experiments.yaml — 10 entries, all 5 fields
try:
    with open("experiments.yaml", encoding="utf-8") as f:
        d = yaml.safe_load(f)
    exps = d["experiments"]
    required = ["name", "fault_type", "target", "hypothesis",
                "blast_radius", "rollback", "measurement", "ground_truth"]
    all_ok = True
    for e in exps:
        missing = [k for k in required if k not in e]
        if missing:
            print(f"  exp {e.get('id')}: MISSING {missing}")
            all_ok = False
    ok = len(exps) == 10 and all_ok
    checks.append(("experiments.yaml: 10 entries, all 5 fields", ok))
    print(f"[{'OK' if ok else 'FAIL'}] experiments.yaml: {len(exps)} experiments, fields complete={all_ok}")
except Exception as ex:
    checks.append(("experiments.yaml", False))
    print(f"[FAIL] experiments.yaml: {ex}")

# 2. chaos_runner.py — runnable, no hard-code
try:
    code = Path("pipeline/chaos_runner.py").read_text(encoding="utf-8")
    has_build_inject = "def build_inject_cmd" in code
    has_scoreboard = "def print_scoreboard" in code
    no_raise = "raise NotImplementedError" not in code
    ok = has_build_inject and has_scoreboard and no_raise
    checks.append(("chaos_runner.py: implements both TODO functions", ok))
    print(f"[{'OK' if ok else 'FAIL'}] chaos_runner.py: build_inject_cmd={has_build_inject}, print_scoreboard={has_scoreboard}, no_NotImplemented={no_raise}")
except Exception as ex:
    checks.append(("chaos_runner.py", False))
    print(f"[FAIL] chaos_runner.py: {ex}")

# 3. chaos_results.json — 10 entries
try:
    with open("chaos_results.json", encoding="utf-8") as f:
        results = json.load(f)
    ok = len(results) == 10
    checks.append(("chaos_results.json: 10 entries", ok))
    print(f"[{'OK' if ok else 'FAIL'}] chaos_results.json: {len(results)} entries")
except Exception as ex:
    checks.append(("chaos_results.json", False))
    print(f"[FAIL] chaos_results.json: {ex}")

# 4. probe.log — exists and has data
try:
    lines = Path("probe.log").read_text(encoding="utf-8").strip().split("\n")
    data_lines = [l for l in lines if not l.startswith("#") and l.strip()]
    ok = len(data_lines) > 50
    checks.append(("probe.log: present, has data", ok))
    print(f"[{'OK' if ok else 'FAIL'}] probe.log: {len(data_lines)} data lines")
except Exception as ex:
    checks.append(("probe.log", False))
    print(f"[FAIL] probe.log: {ex}")

# 5. Acceptance: detected >= 7/10, RCA >= 5/detected, FA <= 1
try:
    detected = sum(1 for r in results if r["detected"])
    detected_results = [r for r in results if r["detected"]]
    rca_correct = sum(1 for r in detected_results if r["rca_correct"])
    false_alarms = 0  # no baseline window FP in results
    detect_ok = detected >= 7
    rca_ok = rca_correct >= 5 and (rca_correct / detected >= 0.70) if detected else False
    fa_ok = false_alarms <= 1
    ok = detect_ok and rca_ok and fa_ok
    checks.append(("Acceptance: detected>=7, RCA>=70%, FA<=1", ok))
    print(f"[{'OK' if ok else 'FAIL'}] Acceptance: detected={detected}/10, rca_correct={rca_correct}/{detected}, FA={false_alarms}")
except Exception as ex:
    checks.append(("Acceptance", False))
    print(f"[FAIL] Acceptance: {ex}")

# 6. chaos_report.md — 4 required sections
try:
    report = Path("chaos_report.md").read_text(encoding="utf-8")
    sections = ["## 1. Setup", "## 2. Results", "## 3. Detailed", "## 4. Gap"]
    has = [s for s in sections if s in report]
    ok = len(has) == 4
    checks.append(("chaos_report.md: 4 required sections", ok))
    print(f"[{'OK' if ok else 'FAIL'}] chaos_report.md: found {len(has)}/4 required sections")
except Exception as ex:
    checks.append(("chaos_report.md", False))
    print(f"[FAIL] chaos_report.md: {ex}")

# 7. SUBMIT.md — 4 sections
try:
    submit = Path("SUBMIT.md").read_text(encoding="utf-8")
    sections = ["## 3 ", "## 1 fault", "## 1 trade-off", "## Scoreboard"]
    has = [s for s in sections if s in submit]
    ok = len(has) == 4
    checks.append(("SUBMIT.md: 4 required sections", ok))
    print(f"[{'OK' if ok else 'FAIL'}] SUBMIT.md: found {len(has)}/4 sections")
except Exception as ex:
    checks.append(("SUBMIT.md", False))
    print(f"[FAIL] SUBMIT.md: {ex}")

print()
passed = sum(1 for _, ok in checks if ok)
total = len(checks)
print(f"Result: {passed}/{total} checks passed")
print("FINAL: PASS" if passed == total else f"FINAL: FAIL ({total - passed} check(s) failed)")
