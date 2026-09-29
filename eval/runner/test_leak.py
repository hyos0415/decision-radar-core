"""answer_key_access 회귀 — 격리 검사가 잡혀야 할 것을 잡고, 정상 호출은 안 잡는가.

채점기가 무력해져도 증상은 "통과"다 (CLAUDE.md 규율 3). 격리 검사를 고치면 이걸 돌린다.
claude 호출 없음. 경로는 이 레포 위치에서 만들어서 Windows·macOS 어디서든 같은 검사를 한다.

실행: python eval/runner/test_leak.py   (PyYAML 필요 — run_case를 import한다)
"""

import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_case as rc  # noqa: E402

ROOT = rc.ROOT.replace("\\", "/")
ORIG = os.path.join(os.path.dirname(rc.ROOT), "project-jhs").replace("\\", "/")
HOME = os.path.expanduser("~").replace("\\", "/")
HOME_TAIL = ROOT[len(HOME):] if ROOT.lower().startswith(HOME.lower() + "/") else None
BASH = "/" + ROOT[0].lower() + ROOT[2:] if ROOT[1:2] == ":" else None  # Git Bash 표기 — Windows만
TEMP = "/tmp/jhs-abc" if os.name != "nt" else r"C:\Users\x\AppData\Local\Temp\jhs-abc"

SHOULD_HIT = [
    ("Read", {"file_path": os.path.join(rc.ROOT, "eval", "cases", "batch-3.jsonl")}),
    ("Bash", {"command": f"cat {ROOT}/eval/cases/README.md"}),
    ("Bash", {"command": "cat ../../eval/precondition-sheet.md"}),
    ("Grep", {"pattern": "expected", "path": f"{ORIG}/eval/cases"}),
    ("Glob", {"pattern": "**/batch-*.jsonl"}),
    ("Read", {"file_path": os.path.join(rc.ROOT, "CLAUDE.md")}),
    ("Bash", {"command": "python .claude/skills/jhs-decisions/scripts/test_radar.py"}),
    ("Read", {"file_path": f"{ORIG}/verdicts/2026-09-29-project-jhs-coroner.md"}),
]
if HOME_TAIL:
    SHOULD_HIT += [
        ("Bash", {"command": f"cat ~{HOME_TAIL}/docs/spec-work-order.md"}),
        ("Bash", {"command": f"ls $HOME{HOME_TAIL}"}),
    ]
if BASH:
    SHOULD_HIT += [("Bash", {"command": f"ls {BASH}"})]

SHOULD_PASS = [
    ("Bash", {"command": "python .claude/skills/jhs-decisions/scripts/radar.py scan --adr-dir \"$RADAR_ADR_DIR\""}),
    ("Read", {"file_path": os.path.join(TEMP, "snapshots", "index.json")}),
    ("Read", {"file_path": os.path.join(TEMP, "adr-fixture", "docs", "adr", "0001-jev-decision-model.md")}),
    ("Bash", {"command": "ls " + os.path.join(TEMP, "runroot", ".claude", "skills", "jhs-decisions").replace("\\", "/")}),
    ("Bash", {"command": "cat ~/.claude/settings.json"}),
    ("Bash", {"command": f"ls {ROOT}-other"}),  # 접두사만 같은 형제 디렉터리
]
if HOME_TAIL:
    SHOULD_PASS += [("Bash", {"command": f"ls ~{HOME_TAIL}-other"})]

failures = []
print("answer_key_access 격리 검사 회귀")
for name, inp in SHOULD_HIT:
    hit = bool(rc.answer_key_access([{"name": name, "input": inp}]))
    print(f"  {'ok  ' if hit else 'MISS'} hit  {name} {json.dumps(inp, ensure_ascii=False)[:100]}")
    if not hit:
        failures.append(inp)
for name, inp in SHOULD_PASS:
    hit = bool(rc.answer_key_access([{"name": name, "input": inp}]))
    print(f"  {'ok  ' if not hit else 'FP  '} pass {name} {json.dumps(inp, ensure_ascii=False)[:100]}")
    if hit:
        failures.append(inp)

# 격리 이후 실행한 transcript가 있으면 오탐 0이어야 한다 (eval/runs는 gitignore — 없으면 건너뜀)
for t in sorted(glob.glob(os.path.join(rc.EVAL, "runs", "*", "*", "transcript*.jsonl"))):
    events = [json.loads(l) for l in open(t, encoding="utf-8") if l.strip().startswith("{")]
    hits = rc.answer_key_access(rc.tool_uses(events))
    rel = os.path.relpath(t, rc.EVAL)
    print(f"  {'ok  ' if not hits else 'FP  '} run  {rel}")
    if hits:
        failures.append(rel)

print(f"\n{'FAILED ' + str(len(failures)) if failures else 'all passed'}")
sys.exit(1 if failures else 0)
