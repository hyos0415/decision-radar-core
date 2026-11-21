"""제품 구조 회귀 — 모델·API 없이 돈다. 안전 조항이 구조로 남아 있는지, 채점기가 무력하지 않은지 본다.

  python product/radar/test_product.py        # 메인 .venv (PyYAML 필요)

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

print("2. detect 흐름 (plain Python — ADR 0030, HYO-220)")
src = open(os.path.join(HERE, "detect.py"), encoding="utf-8").read()
from radar import detect as detect_mod  # noqa: E402
flow = detect_mod.detect_flow(Ctx(radar_py="x", env={}, adr_dir=".", targets={}, dry_run=True), object())
check("단계 = {load, triage, draft, write, repair, report} — status 단계 · 승인 우회 단계 없음",
      tuple(flow.steps) == detect_mod.STEPS == ("load", "triage", "draft", "write", "repair", "report"), tuple(flow.steps))
check("흐름 코드가 langgraph를 부르지 않는다", not re.search(r"^\s*(from|import) langgraph", src, re.M))
check("detect 상태 합치기: drafts · p3는 이어 붙이고 나머지는 덮는다",
      detect_mod.merge({"p3": [1], "drafts": ["a"], "kept": [1]}, {"p3": [2], "drafts": ["b"], "kept": []}) == {"p3": [1, 2], "drafts": ["a", "b"], "kept": []})
check("모델에게 주는 도구는 읽기뿐 (snapshot_* · repo_grep)", [t.name for t in DETECT_TOOLS] == ["snapshot_fetch", "snapshot_search", "repo_grep"])

# 제품 eval이 엔진을 고를 수 있다 (HYO-99) — run_eval.py가 --engine을 안 받아 CLI 스모크가 늘 API로 돌던 틈
from radar.evalkit import build_parser  # noqa: E402
from radar.runtime import Runtime  # noqa: E402
_rt = Runtime()
check("eval --engine claude-cli → 구독 CLI 엔진", _rt.real_spec(build_parser(_rt).parse_args(["--model", "anthropic", "--engine", "claude-cli", "JHS-001"]))["kind"] == "claude-cli")
check("eval 엔진 기본값은 api", _rt.real_spec(build_parser(_rt).parse_args(["--model", "anthropic", "JHS-001"]))["kind"] == "anthropic")

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
from radar.loop import Conversation  # noqa: E402
from radar.tools import CAPTURE_TOOLS, LOOKUP_BUDGET, REVISIT_TOOLS, lookup_guard  # noqa: E402

COND = '확인할 조건:\n<condition>{"source_hint": "docs.cloudvendor.example/guardrails/languages"}</condition>'


def conv_with(*turns):
    """turns: ("tool", 이름, 결과 글) · ("ai", 글) — 조건 하나로 시작하는 대화 (Anthropic 형식)."""
    c = Conversation.start(COND)
    for i, t in enumerate(turns):
        if t[0] == "ai":
            c.messages.append({"role": "assistant", "content": [{"type": "text", "text": t[1]}]})
        else:
            c.messages.append({"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}", "name": t[1], "input": {}}]})
            c.messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": t[2], "is_error": False}]})
    return c


st = conv_with()
check("source_hint는 연다 (scheme 붙여도)", lookup_guard(st, "https://docs.cloudvendor.example/guardrails/languages/") is None)
check("지어낸 URL은 not_offered", (lookup_guard(st, "https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails.html") or {}).get("status") == "not_offered")
check("빈 URL은 not_offered", (lookup_guard(st, "") or {}).get("status") == "not_offered")
found = ("tool", "snapshot_search", '{"ok": true, "results": [{"url": "https://cloudvendor.example/blog/korean"}]}')
check("검색 결과에 나온 URL은 연다", lookup_guard(conv_with(found), "cloudvendor.example/blog/korean") is None)
check("AI가 스스로 쓴 URL은 근거가 아니다", (lookup_guard(conv_with(("ai", "github.com/x/y 를 열어 보자")), "github.com/x/y") or {}).get("status") == "not_offered")
used = conv_with(*[("tool", "snapshot_fetch", '{"ok": false, "status": 403}') for _ in range(LOOKUP_BUDGET)])
check("예산을 다 쓰면 열기·검색 모두 거절", all((x or {}).get("status") == "lookup_budget_exhausted"
                                         for x in (lookup_guard(used, "docs.cloudvendor.example/guardrails/languages"), lookup_guard(used))))
other = conv_with(*[("tool", "read_repo_file", "{}") for _ in range(LOOKUP_BUDGET)])
check("read_repo_file은 예산에 안 든다", lookup_guard(other) is None)
check("revisit·capture 도구 이름 그대로 · capture에 열기 추가", [t.name for t in REVISIT_TOOLS] == ["snapshot_fetch", "snapshot_search", "read_repo_file"]
      and [t.name for t in CAPTURE_TOOLS] == ["snapshot_fetch", "match_adr", "propose_adr"])

# R14 변경 (2026-10-23, HYO-136): 의존성 릴리스 대응형 ADR은 같은 릴리스만 막는다 — 제품 screen이 소식 제목을 --release로 넘기는지 (규율 8)
import tempfile  # noqa: E402
from radar import detect_ops  # noqa: E402
from common.core import SKILL_RADAR  # noqa: E402
with tempfile.TemporaryDirectory() as td:
    adr = os.path.join(td, "adr")
    os.makedirs(adr)
    open(os.path.join(adr, "0012-vllm.md"), "w", encoding="utf-8").write(
        "---\nstatus: adopted\ntechnology: vLLM\nuse: 의존성 릴리스 v0.31.0 대응 (breaking·security)\naffected:\n  - {repo: fv, component: serving}\n"
        "evidence:\n  - {url: github.com/vllm-project/vllm/releases/tag/v0.31.0, source_type: release_note}\n---\n# vLLM v0.31.0 대응\n")
    sc = Ctx(radar_py=SKILL_RADAR, env=dict(os.environ, PYTHONIOENCODING="utf-8"),
             adr_dir=adr, targets={"fv": td}, dry_run=True)
    feed = [{"url": "u32", "title": "vllm-project/vllm v0.32.0"}, {"url": "u31", "title": "vllm-project/vllm v0.31.0"}]
    cand = lambda u: {"technology": "vLLM", "dependency": True, "affected": [{"repo": "fv", "component": "serving"}], "source_urls": [u]}
    kept, skipped, _ = detect_ops.screen(sc, [cand("u32"), cand("u31")], feed)
    check("R14 대응형: 다음 릴리스는 초안 · 같은 릴리스는 건너뜀", [k["candidate"]["source_urls"] for k in kept] == [["u32"]]
          and skipped == [{"technology": "vLLM", "adr": "0012", "source_urls": ["u31"]}], (kept, skipped))
    check("R14 대응형: 이전 대응 ADR이 related로 초안에 간다", [r["id"] for r in kept[0]["related"]] == ["0012"] if kept else False)

# HYO-162: 관문 판정 뒤 이미 반영된 릴리스는 모델에 넘기기 전에 코드가 뺀다 — 입력 폴더의 basis.json(관문 기준) × RADAR_BASIS_NOW(지금 기준)
sys.path.append(os.path.dirname(os.path.dirname(HERE)))  # 레포 루트 — drop_applied가 pipeline.basis를 늦게 부른다
from types import SimpleNamespace  # noqa: E402
with tempfile.TemporaryDirectory() as td:
    feed = [{"url": "u45", "package": "electron", "version": "v45.0.0"}, {"url": "u46", "package": "electron", "version": "v46.0.0"}]
    gate_basis = {"repos": [{"repo": "decision-radar", "commit": "a" * 40, "versions": {"electron": ["39.2.6"]}}]}
    now_basis = {"repos": [{"repo": "decision-radar", "commit": "b" * 40, "versions": {"electron": ["45.0.0"]}}]}
    json.dump(gate_basis, open(os.path.join(td, "basis.json"), "w", encoding="utf-8"))
    json.dump(now_basis, open(os.path.join(td, "now.json"), "w", encoding="utf-8"))
    keep, out = detect_ops.drop_applied(SimpleNamespace(env={"RADAR_SNAPSHOT_DIR": td, "RADAR_BASIS_NOW": os.path.join(td, "now.json")}), feed)
    check("HYO-162: 관문 기준 39 → 지금 45 · v45.0.0은 「이미 반영됨」, v46은 남는다",
          [e["url"] for e in keep] == ["u46"] and [e["url"] for e in out] == ["u45"] and out[0]["reason"].startswith("이미 반영됨"), (keep, out))
    no_now = detect_ops.drop_applied(SimpleNamespace(env={"RADAR_SNAPSHOT_DIR": td}), feed)
    no_gate = detect_ops.drop_applied(SimpleNamespace(env={"RADAR_SNAPSHOT_DIR": os.path.join(td, "x"), "RADAR_BASIS_NOW": os.path.join(td, "now.json")}), feed)
    check("HYO-162: 기준이 하나라도 없으면 아무것도 안 뺀다 (예전 입력 · eval 픽스처)", no_now == (feed, []) and no_gate == (feed, []), (no_now, no_gate))

# HYO-246: R16을 코드로 — 매핑에 있는 기술은 affected를 CLAUDE.md 「의존성 → component」 매핑 그대로 · 폴더가 없는 대상 repo는 오류로
from collector.sources.github import names_from_cell  # noqa: E402
from radar import hitl  # noqa: E402
ROOT_MD = open(os.path.join(os.path.dirname(os.path.dirname(HERE)), "CLAUDE.md"), encoding="utf-8").read()
cells = [ln.strip().strip("|").split("|")[0].strip() for ln in hitl.stack_rows(ROOT_MD)["deps"] if ln.startswith("|")]
check("R16 표 읽기: 첫 칸 이름 규칙이 collector names_from_cell과 같다 (이 레포 CLAUDE.md)",
      cells and all(detect_ops.names_of(x) == names_from_cell(x) for x in cells), [(x, detect_ops.names_of(x), names_from_cell(x)) for x in cells])
comp_cases = {
    "schema · api · llm · dialog (환자 데이터 경로)": ["schema", "api", "llm", "dialog"],
    "앱 (온디바이스 추론 — 폰 NPU, 운영) · eval (`llama-server`로 로컬 비교). pip 의존성이 아니라 빌드한 바이너리라, manifest에 없다": ["앱", "eval"],
    "product (`radar/models.py` — API 엔진, 루프는 `radar/loop.py`, ADR 0030) · pipeline (`a.py` — 관문 (주간) 재분류)": ["product", "pipeline"],
    "arena (`arena/` — 하네스 지표 · 청크 상한 계량)": ["arena"],
    "desktop": ["desktop"],
}
got = {k: detect_ops.components_of(k) for k in comp_cases}
check("R16 표 읽기: component 칸 — 괄호(겹친 것까지) · 백틱 · 덧붙인 문장을 빼고 `·`로 나눈다", got == comp_cases, got)
with tempfile.TemporaryDirectory() as td:
    for name, table in (("a", "| 의존성 | component |\n|---|---|\n| fastapi · uvicorn | api (서버) |\n| langfuse | obs · eval |\n"),
                        ("b", "| 의존성 | component |\n|---|---|\n| FastAPI | desktop |\n| PyYAML | skill · product |\n")):
        os.makedirs(os.path.join(td, name))
        open(os.path.join(td, name, "CLAUDE.md"), "w", encoding="utf-8").write(f"# x\n\n## 스택\n\n### 의존성 → component\n\n{table}\n### 외부 모델·서비스\n\n| 모델 | component |\n|---|---|\n| Langfuse Cloud | cloud |\n")
    tc = SimpleNamespace(targets={"a": os.path.join(td, "a"), "b": os.path.join(td, "b"), "gone": os.path.join(td, "nope")})
    dmap = detect_ops.dependency_map(tc)
    check("R16 표 읽기: 의존성 절만 · 외부 모델 절은 안 본다 · CLAUDE.md가 없는 repo는 빈 목록",
          dmap == {"a": [{"names": ["fastapi", "uvicorn"], "components": ["api"]}, {"names": ["langfuse"], "components": ["obs", "eval"]}],
                   "b": [{"names": ["FastAPI"], "components": ["desktop"]}, {"names": ["PyYAML"], "components": ["skill", "product"]}], "gone": []}, dmap)
    A = lambda *xs: [{"repo": r, "component": c} for r, c in xs]   # noqa: E731
    cands = [{"technology": "FastAPI", "dependency": True, "affected": A(("a", "backend")), "source_urls": ["u1"]},       # 지어낸 component · 빠뜨린 repo
             {"technology": "Langfuse", "dependency": True, "affected": A(("a", "eval"), ("a", "obs")), "source_urls": ["u2"]},  # 순서만 다름 → 그대로
             {"technology": "pyyaml", "dependency": False, "affected": A(("b", "skill"), ("b", "product")), "source_urls": ["u3"]},  # 맞는데 dependency만 false
             {"technology": "Langfuse Cloud", "dependency": False, "affected": A(("a", "cloud")), "source_urls": ["u4"]},      # 표에 없다(서비스) → 손 안 댄다
             {"technology": "Kimi K3", "dependency": False, "affected": A(("b", "eval")), "source_urls": ["u5"]}]
    fixed = detect_ops.fix_affected(cands, dmap)
    check("R16 맞춤: 지어낸 component · 빠뜨린 repo → 매핑 그대로(대상 repo 전체의 합)",
          cands[0]["affected"] == A(("a", "api"), ("b", "desktop")) and cands[0]["dependency"] is True, cands[0])
    check("R16 맞춤: 같은 집합이면 순서까지 모델이 쓴 그대로 · 기록에도 없다", cands[1]["affected"] == A(("a", "eval"), ("a", "obs"))
          and all(f["technology"] != "Langfuse" for f in fixed), fixed)
    check("R16 맞춤: affected가 맞으면 dependency만 true로 (기록에 dependency_was)", cands[2]["dependency"] is True
          and cands[2]["affected"] == A(("b", "skill"), ("b", "product")) and any(f["technology"] == "pyyaml" and f["dependency_was"] is False for f in fixed), fixed)
    check("R16 맞춤: 표에 없는 기술(서비스 · 새 모델)은 손대지 않는다", cands[3]["affected"] == A(("a", "cloud")) and cands[3]["dependency"] is False
          and cands[4]["affected"] == A(("b", "eval")), cands[3:])
    check("R16 맞춤: 바꾼 것만 before → after로 남긴다", [(f["technology"], f["before"], f["after"]) for f in fixed] ==
          [("FastAPI", A(("a", "backend")), A(("a", "api"), ("b", "desktop"))), ("pyyaml", A(("b", "skill"), ("b", "product")), A(("b", "skill"), ("b", "product")))], fixed)
    lines = detect_ops.affected_lines({"r16": fixed, "missing_targets": ["gone"]})
    check("R16 요약 줄: 폴더 없는 repo · 고친 후보 (dependency만 바꾼 것은 그렇게)", len(lines) == 2 and "gone" in lines[0]
          and "a/backend → a/api, b/desktop" in lines[1] and "pyyaml affected 그대로 · dependency만 true로" in lines[1], lines)
    check("R16 요약 줄: 고친 것이 없으면 없다", detect_ops.affected_lines({}) == [] and detect_ops.affected_lines(None) == [])
    # HYO-219: 자리 후보의 affected = (자리 repo, 자리 설정의 component) — 모델이 고르지 않는다
    sfeed = [{"url": "s1", "candidate_for": [{"slot": "rd", "repo": "a", "component": ["arena"]}]},
             {"url": "s2", "candidate_for": [{"slot": "rd", "repo": "a", "component": ["arena"]}, {"slot": "od", "repo": "b", "component": []}]},
             {"url": "s3", "candidate_for": [{"slot": "od", "repo": "b", "component": []}]}]
    scands = [{"technology": "KURE-v3", "affected": A(("a", "retrieval-dense")), "source_urls": ["s1"]},            # 자리 id를 component로 씀 → arena
              {"technology": "Multi", "affected": A(("a", "eval"), ("b", "앱"), ("b", "x")), "source_urls": ["s2"]},   # 칸 없는 자리(od)는 모델 줄 그대로
              {"technology": "OnlyOd", "affected": A(("b", "앱")), "source_urls": ["s3"]},                             # 칸 없는 자리뿐 → 손 안 댄다
              {"technology": "Same", "affected": A(("a", "arena")), "source_urls": ["s1"]}]                            # 이미 같다 → 기록 없음
    sfix = detect_ops.fix_slot_affected(scands, sfeed)
    check("HYO-219 자리 component: 자리 id 같은 지어낸 이름 → 자리 설정의 component", scands[0]["affected"] == A(("a", "arena")), scands[0])
    check("HYO-219 자리 component: 칸 없는 자리는 그 repo의 모델 줄을 그대로 · 칸 있는 자리는 설정대로",
          scands[1]["affected"] == A(("a", "arena"), ("b", "앱"), ("b", "x")), scands[1])
    check("HYO-219 자리 component: 칸 없는 자리뿐이면 · 이미 같으면 손대지 않는다 (기록도 없다)",
          scands[2]["affected"] == A(("b", "앱")) and [f["technology"] for f in sfix] == ["KURE-v3", "Multi"], sfix)
    sl_lines = detect_ops.affected_lines({"slot_affected": sfix})
    check("HYO-219 요약 줄: 자리 설정으로 고친 후보", len(sl_lines) == 1 and "a/retrieval-dense → a/arena" in sl_lines[0], sl_lines)
    miss = detect_ops.missing_targets(tc)
    check("폴더가 없는 대상 repo를 찾는다", miss == [{"repo": "gone", "path": os.path.join(td, "nope")}], miss)
    sc2 = Ctx(radar_py=SKILL_RADAR, env=dict(os.environ, PYTHONIOENCODING="utf-8"), adr_dir=os.path.join(td, "adr"), targets=tc.targets, dry_run=True)
    os.makedirs(sc2.adr_dir)
    kept, skipped, dropped = detect_ops.screen(sc2, [{"technology": "X", "dependency": False, "affected": A(("gone", "api")), "source_urls": ["u9"]},
                                                     {"technology": "Y", "dependency": False, "affected": A(("a", "api")), "source_urls": ["u8"]}],
                                               [], missing=["gone"])
    check("R10: 폴더가 없는 대상 repo를 가리키는 후보는 뺀다 — 이유에 적는다", [k["candidate"]["technology"] for k in kept] == ["Y"]
          and len(dropped) == 1 and "폴더가 없는 대상 repo ['gone']" in dropped[0]["reason"], (kept, dropped))

print(f"\n{'FAILED ' + str(len(fails)) if fails else 'all passed'}")
sys.exit(1 if fails else 0)
