"""제품 구조 회귀 — 모델·API 없이 돈다. 안전 조항이 구조로 남아 있는지, 채점기가 무력하지 않은지 본다.

  python product/radar/test_product.py        # 메인 .venv (PyYAML·langgraph 필요)

e2e(53케이스 + 음성 대조)는 run_eval.py --stub이 한다. 여기는 그 앞의 빠른 검사다:
  1. R8  status를 쓰는 길이 구조적으로 없다 — 스키마에 칸이 없고, 렌더가 proposed로 박고, 소스에 다른 status 쓰기가 없다
  2. detect 그래프의 노드가 설계(load·triage·draft·write)와 같다 — status 노드·승인 우회 노드가 끼어들면 여기서 걸린다
  3. 격리 검사가 detect trace 모양(Ctx.record가 만드는 도구 호출)을 문다 — 잡혀야 할 것은 잡히고, 정상 호출은 안 잡힌다
  4. 호출 기록 줄(llm_call)이 채점기가 읽는 도구 호출에 섞이지 않는다
"""

import glob
import json
import os
import re
import sys

sys.path[0] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # product/

from radar import llm_log  # noqa: E402
from radar.tools import DETECT_TOOLS  # noqa: E402
from radar.detect_schemas import DraftBody  # noqa: E402
from common import ops  # noqa: E402
from common.core import Ctx  # noqa: E402
from common.eval_adapter import rc  # noqa: E402
from common.schemas import AdrDraft  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
fails = []


def check(name, ok, detail=None):
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok or detail is None else f" — {detail}"))
    if not ok:
        fails.append(name)


print("1. R8 — status를 쓰는 길이 없다")
check("AdrDraft에 status 칸 없음", "status" not in AdrDraft.model_fields)
check("DraftBody(detect 초안 서술)에 status 칸 없음", "status" not in DraftBody.model_fields)
draft = AdrDraft(title="t", technology="x", use="u", affected=[], evidence=[], context="c", decision="d", related=[], research_note=[], revisit_when=[])
check("렌더 결과 status: proposed", "\nstatus: proposed\n" in ops.render(draft, "2026-09-28T00:00:00+09:00"))
bad = []
for f in sorted(glob.glob(os.path.join(HERE, "*.py"))):
    if os.path.basename(f) == "test_product.py":
        continue
    for i, line in enumerate(open(f, encoding="utf-8"), 1):
        if re.search(r"""status["']?\]?\s*[:=]\s*["'](adopted|rejected|hold|revisit|superseded)["']""", line):
            bad.append(f"{os.path.basename(f)}:{i}")
check("소스에 다른 status 값을 쓰는 곳 없음", not bad, bad)

print("2. detect 그래프")
src = open(os.path.join(HERE, "detect.py"), encoding="utf-8").read()
added = set(re.findall(r'g\.add_node\("(\w+)"', src))
check("노드 = {load, triage, draft, write, repair, report}", added == {"load", "triage", "draft", "write", "repair", "report"}, sorted(added))
check("모델에게 주는 도구는 읽기뿐 (snapshot_*)", [t.name for t in DETECT_TOOLS] == ["snapshot_fetch", "snapshot_search"])

print("3. 격리 검사가 detect trace 모양을 문다")
c = Ctx(radar_py=os.path.join(rc.ROOT, ".claude", "skills", "jhs-decisions", "scripts", "radar.py"), env={}, adr_dir=".", targets={}, dry_run=True)
c.record("Bash", {"command": f"python {c.radar_py} snapshot feed"})  # 레포의 radar.py를 직접 부름 (CTRL-repo-path-detect와 같은 모양)
c.record("Read", {"file_path": os.path.join(rc.ROOT, "eval", "cases", "batch-4.jsonl")})
hits = rc.answer_key_access(rc.tool_uses(c.events))
check("레포 radar.py 호출 + 정답지 Read가 잡힘", len(hits) == 2, hits)
ok = Ctx(radar_py="/tmp/jhs-x/runroot/scripts/radar.py", env={}, adr_dir=".", targets={}, dry_run=True)
ok.record("Bash", {"command": "python /tmp/jhs-x/runroot/scripts/radar.py snapshot feed"})
ok.record("Read", {"file_path": "/tmp/jhs-x/medical-mate/CLAUDE.md"})
check("runroot 사본 호출은 안 잡힘", not rc.answer_key_access(rc.tool_uses(ok.events)))

print("4. llm_call 줄은 도구 호출이 아니다")
ok.events.append({"type": "llm_call", "flow": "detect", "model": "stub", "input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0})
check("tool_uses·tool_calls가 llm_call을 세지 않음", len(rc.tool_uses(ok.events)) == 2 and ok.tool_calls() == 2)
check("read_calls가 llm_call만 거름", len(llm_log.read_calls(ok.events)) == 1)

print("5. 열기·검색 가드 (HYO-137) — 보인 적 없는 URL 거절 · 예산 넘으면 거절")
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402
from radar.tools import CAPTURE_TOOLS, LOOKUP_BUDGET, REVISIT_TOOLS, lookup_guard  # noqa: E402

cond = HumanMessage(content='확인할 조건:\n<condition>{"source_hint": "docs.cloudvendor.example/guardrails/languages"}</condition>')
st = {"messages": [cond]}
check("source_hint는 연다 (scheme 붙여도)", lookup_guard(st, "https://docs.cloudvendor.example/guardrails/languages/") is None)
check("지어낸 URL은 not_offered", (lookup_guard(st, "https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails.html") or {}).get("status") == "not_offered")
check("빈 URL은 not_offered", (lookup_guard(st, "") or {}).get("status") == "not_offered")
found = ToolMessage(content='{"ok": true, "results": [{"url": "https://cloudvendor.example/blog/korean"}]}', name="snapshot_search", tool_call_id="t1")
check("검색 결과에 나온 URL은 연다", lookup_guard({"messages": [cond, found]}, "cloudvendor.example/blog/korean") is None)
check("AI가 스스로 쓴 URL은 근거가 아니다", (lookup_guard({"messages": [cond, AIMessage(content="github.com/x/y 를 열어 보자")]}, "github.com/x/y") or {}).get("status") == "not_offered")
used = [cond] + [ToolMessage(content='{"ok": false, "status": 403}', name="snapshot_fetch", tool_call_id=f"f{i}") for i in range(LOOKUP_BUDGET)]
check("예산을 다 쓰면 열기·검색 모두 거절", all((x or {}).get("status") == "lookup_budget_exhausted"
                                         for x in (lookup_guard({"messages": used}, "docs.cloudvendor.example/guardrails/languages"), lookup_guard({"messages": used}))))
other = [cond] + [ToolMessage(content="{}", name="read_repo_file", tool_call_id=f"r{i}") for i in range(LOOKUP_BUDGET)]
check("read_repo_file은 예산에 안 든다", lookup_guard({"messages": other}) is None)
check("revisit·capture 도구 이름 그대로 · capture에 열기 추가", [t.name for t in REVISIT_TOOLS] == ["snapshot_fetch", "snapshot_search", "read_repo_file"]
      and [t.name for t in CAPTURE_TOOLS] == ["snapshot_fetch", "match_adr", "propose_adr"])

print(f"\n{'FAILED ' + str(len(fails)) if fails else 'all passed'}")
sys.exit(1 if fails else 0)
