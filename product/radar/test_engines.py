"""실행 엔진 회귀 — 구독 없이 돈다 (HYO-99 2단계). 가짜 CLI(fake_cli.py)가 MCP 서버(mcp_tools.py)를 실제로 띄워 도구를 부른다.

  python product/radar/test_engines.py

1. ClaudeCliRunner가 API 키를 지우고 · 내장 도구를 끄고 · 사용자 설정을 안 읽고 · strict MCP로만 띄우는지
2. MCP 서버가 허용한 읽기 도구만 보이고, 도구 기록이 부모 ctx.events·도구 수에 합쳐지는지 (P3 tool_call_missing의 근거)
3. revisit 가드(본 URL만 · 열기·검색 4회)가 LangChain 판과 같은 함수로 집행되는지
4. 실패(로그인·한도 is_error · 결과 모양 이상 · 시간 초과 · 턴 초과)가 EngineError로 끊기는지 — 노드가 model_error로 센다
5. 호출 기록이 billing=subscription · cost_usd None · cost_usd_equiv로 남는지
"""

import asyncio
import json
import os
import sys
import subprocess
import tempfile
import time
from pathlib import Path

sys.path[0] = str(Path(__file__).resolve().parents[1])  # product/

from radar import engines, mcp_tools  # noqa: E402  — 먼저: spike/common을 import 경로에 올린다
from common import ops  # noqa: E402
from common.core import SKILL_RADAR, Ctx  # noqa: E402
from common.schemas import Verdict  # noqa: E402
from radar.detect_schemas import Triage  # noqa: E402
from radar.tools import DETECT_TOOLS, LOOKUP_BUDGET, REVISIT_TOOLS, detect_tools  # noqa: E402

FAKE = str(Path(__file__).resolve().parent / "fake_cli.py")
fails = []


def check(name, ok, detail=None):
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f" — {detail}"))
    if not ok:
        fails.append(name)


def make_ctx(td):
    repo = Path(td) / "repo"
    (repo / "data").mkdir(parents=True)
    (repo / "data" / "stats.json").write_text('{"doc_count": 120000}', encoding="utf-8")
    targets = Path(td) / "radar-targets.json"
    targets.write_text(json.dumps([{"repo": "medical-mate", "path": str(repo)}]), encoding="utf-8")
    env = {**os.environ, "ANTHROPIC_API_KEY": "sk-test-should-be-stripped", "ANTHROPIC_AUTH_TOKEN": "tok-test",
           "RADAR_TARGETS": str(targets), "RADAR_ADR_DIR": str(Path(td) / "adr"), "RADAR_DRY_RUN": "1"}
    c = Ctx.from_env(env, radar_py=SKILL_RADAR)
    CTX.append(c)
    return c


CTX = []  # 대본을 넣을 ctx (runner는 ctx.env로 CLI를 띄운다)


def script(td, name, obj):
    p = Path(td) / f"{name}.json"
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    os.environ["RADAR_FAKE_CLI"] = str(p)
    for c in CTX:
        c.env["RADAR_FAKE_CLI"] = str(p)


VERDICT = {"result": "met", "reason": "corpus_stats 120000 >= 100000", "observed_value": 120000,
           "evidence": [{"url": "repo:medical-mate/data/stats.json", "source_type": "repo"}]}


async def main():
    with tempfile.TemporaryDirectory() as td:
        ctx = make_ctx(td)
        r = engines.ClaudeCliRunner(bin=FAKE, timeout=60)
        log = r.attach_log(ctx, "revisit", "claude-sonnet-5-5")

        # 1·2·5 revisit 판정: 허용 도구(read_repo_file) + 가드에 걸리는 fetch(본 적 없는 URL)
        script(td, "ok", {"calls": [{"tool": "read_repo_file", "args": {"repo": "medical-mate", "path": "data/stats.json"}},
                                     {"tool": "snapshot_fetch", "args": {"url": "https://invented.example/page"}}],
                           "structured": VERDICT})
        before = len(ctx.events)
        res = await r.run(name="condition-judge", system="sys", user="조건: 코퍼스 10만 건 이상. source_hint repo:medical-mate/data/stats.json",
                          schema=Verdict, tools=REVISIT_TOOLS, ctx=ctx, max_turns=8)
        check("구조화 출력이 스키마로 검증된다", isinstance(res.structured, Verdict) and res.structured.result == "met", res)
        check("도구 수 = MCP 서버가 받은 호출(거절 포함) 2", res.tool_calls == 2, res.tool_calls)
        new = ctx.events[before:]
        reads = [e for e in new if e.get("type") == "assistant" and e["message"]["content"][0]["name"] == "Read"]
        check("도구 기록(Read)이 부모 transcript에 합쳐진다", len(reads) == 1 and reads[0]["message"]["content"][0]["input"]["file_path"].endswith("stats.json"), new)
        call = [e for e in new if e.get("type") == "llm_call"]
        check("호출 기록: billing subscription · cost_usd None · cost_usd_equiv", len(call) == 1 and call[0]["billing"] == "subscription"
              and call[0]["cost_usd"] is None and call[0]["cost_usd_equiv"] == 0.0123 and call[0]["agent"] == "condition-judge", call)
        check("totals는 llm_log와 같은 칸", log.totals()["model_calls"] == 1 and log.totals()["input_tokens"] == 105, log.totals())

        # 1 격리 — fake가 본 env·인자
        script(td, "env", {"structured": VERDICT})
        p = await asyncio.create_subprocess_exec(*r.bin, "-p", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                 env=ctx.env)  # 대조: runner를 안 거치고 ctx.env 그대로 띄우면 키가 보인다
        o, _ = await p.communicate(b"x")
        raw = json.loads(o)
        check("대조: runner를 거치지 않으면 fake가 키를 본다 (검사가 무력하지 않다)", raw["_seen_env"]["api_key"] is True, raw["_seen_env"])
        cmd_seen = {}
        orig = engines.asyncio.create_subprocess_exec

        async def spy(*a, **kw):
            cmd_seen["argv"], cmd_seen["env"], cmd_seen["cwd"] = a, kw.get("env"), kw.get("cwd")
            return await orig(*a, **kw)
        engines.asyncio.create_subprocess_exec = spy
        try:
            await r.run(name="x", system="s", user="u", schema=Verdict, tools=REVISIT_TOOLS, ctx=ctx, max_turns=8)
        finally:
            engines.asyncio.create_subprocess_exec = orig
        argv = list(cmd_seen["argv"])
        check("API 키를 지운다 (구독으로만)", not {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"} & set(cmd_seen["env"]), sorted(cmd_seen["env"])[:5])
        check("내장 도구 끔 · 사용자 설정 안 읽음 · strict MCP · 세션 저장 안 함", argv[argv.index("--tools") + 1] == ""
              and argv[argv.index("--setting-sources") + 1] == "" and "--strict-mcp-config" in argv and "--no-session-persistence" in argv, argv)
        check("--bare를 쓰지 않는다 (구독 로그인을 안 읽는다)", "--bare" not in argv)
        check("빈 실행 폴더에서 띄운다", Path(cmd_seen["cwd"]).name.startswith("radar-cli-") and str(Path(cmd_seen["cwd"])) not in str(Path.cwd()))
        check("허용 목록 = revisit 도구 3개", [x for x in argv if x.startswith("mcp__radar__")] ==
              ["mcp__radar__snapshot_fetch", "mcp__radar__snapshot_search", "mcp__radar__read_repo_file"], argv)

        # 2 detect 도구에는 read_repo_file이 없다 — 서버도 안 보이고 허용 목록에도 없다
        script(td, "detect", {"calls": [{"tool": "read_repo_file", "args": {"repo": "medical-mate", "path": "data/stats.json"}}],
                              "structured": {"candidates": [], "excluded": []}})
        res = await r.run(name="detect-triage", system="s", user="u", schema=Triage, tools=DETECT_TOOLS, ctx=ctx, max_turns=8)
        check("detect: 허용 밖 도구는 안 불린다 (도구 수 0)", res.tool_calls == 0, res)

        # 3 코퍼스가 켜지면 detect에 corpus_grep이 붙고 MCP로 실제로 돈다 (DCI, HYO-201) — 꺼지면 예전 두 개
        check("detect 도구: 코퍼스 끔 = snapshot 두 개 + repo_grep", [t.name for t in detect_tools(ctx)] == ["snapshot_fetch", "snapshot_search", "repo_grep"])
        corpus, snap = Path(td) / "items.jsonl", Path(td) / "snap"
        snap.mkdir()
        corpus.write_text(json.dumps({"url": "https://github.com/vllm-project/vllm/releases/tag/v1", "title": "vllm v1",
                                      "text": "Breaking: drop CUDA 12"}) + "\n", encoding="utf-8")
        ctx.env.update({"RADAR_CORPUS": str(corpus), "RADAR_SNAPSHOT_DIR": str(snap)})
        tools = detect_tools(ctx)
        check("detect 도구: 코퍼스 켬 = + corpus_grep", [t.name for t in tools] == ["snapshot_fetch", "snapshot_search", "repo_grep", "corpus_grep"])
        script(td, "grep", {"calls": [{"tool": "corpus_grep", "args": {"pattern": "cuda 1[23]"}}],
                            "structured": {"candidates": [], "excluded": []}})
        res = await r.run(name="detect-triage", system="s", user="u", schema=Triage, tools=tools, ctx=ctx, max_turns=8)
        cmd = [e for e in ctx.events if "snapshot grep" in json.dumps(e)]
        check("corpus_grep이 MCP로 불린다 (radar.py snapshot grep)", res.tool_calls == 1 and cmd, res)
        out = json.loads(ops.corpus_grep(ctx, "cuda 1[23]"))
        check("corpus_grep은 맞은 항목 · 줄을 돌려준다", out["ok"] and out["results"][0]["lines"] == ["Breaking: drop CUDA 12"], out)
        for k in ("RADAR_CORPUS", "RADAR_SNAPSHOT_DIR"):
            ctx.env.pop(k)

        # 3b 우리 코드 찾기 (HYO-205) — repo_grep이 MCP로 돌고, 「안 쓴다」는 말은 그 기록과 대조된다
        from radar import detect_ops
        code = Path(td) / "code"
        (code / "src").mkdir(parents=True)
        (code / "fixture").mkdir()
        (code / "src" / "app.py").write_text("import zz_widget\n\nzz_widget.spin()\n", encoding="utf-8")
        (code / "README.md").write_text("zz_gadget 을 쓴다고 적힌 문서", encoding="utf-8")               # 문서는 코드가 아니다
        (code / "fixture" / "fake.py").write_text("import zz_gadget\n", encoding="utf-8")              # exclude — 내 스택이 아닌 폴더
        subprocess.run(["git", "init", "-q", str(code)], check=True)
        subprocess.run(["git", "-C", str(code), "add", "-A"], check=True)
        ctx.targets, ctx.excludes = {"mine": str(code), "nogit": str(Path(td) / "repo")}, {"mine": ["fixture"]}
        out = json.loads(ops.repo_grep(ctx, "zz_widget"))
        check("repo_grep: 쓰는 곳을 줄 단위로 센다", out["ok"] and out["by_repo"] == {"mine": 2} and out["hits"][0]["path"] == "src/app.py", out)
        check("repo_grep: git이 아닌 repo는 0곳이 아니라 못 읽음", out["unread_repos"] == ["nogit"], out)
        ctx.targets = {"mine": str(code)}
        out = json.loads(ops.repo_grep(ctx, "zz_gadget"))
        check("repo_grep: 문서 · exclude 폴더는 우리 코드가 아니다 (0곳)", out["ok"] and out["total"] == 0 and not out["unread_repos"], out)
        check("repo_grep: 정규식 오류는 ok=false", json.loads(ops.repo_grep(ctx, "zz_(")).get("ok") is False)
        tmap = Path(td) / "targets-code.json"
        tmap.write_text(json.dumps([{"repo": "mine", "path": str(code), "exclude": ["fixture"]}]), encoding="utf-8")
        ctx.env["RADAR_TARGETS"] = str(tmap)
        n0 = len(ctx.events)
        script(td, "rgrep", {"calls": [{"tool": "repo_grep", "args": {"pattern": "zz_absent"}}], "structured": {"candidates": [], "excluded": []}})
        res = await r.run(name="detect-triage", system="s", user="u", schema=Triage, tools=DETECT_TOOLS, ctx=ctx, max_turns=8)
        rec = [e["message"]["content"][0] for e in ctx.events[n0:] if e.get("type") == "assistant" and e["message"]["content"][0]["name"] == "Grep"]
        check("repo_grep이 MCP로 불리고 기록(패턴 · 개수)이 부모에 남는다", res.tool_calls == 1 and rec and rec[0]["input"]["pattern"] == "zz_absent"
              and rec[0]["input"]["total"] == 0, rec)
        ex = lambda **kw: {"url": "u", "reason": "미사용", **kw}   # noqa: E731
        cu = lambda e: detect_ops.check_unused(ctx, [e])[0].get("usage_check")   # noqa: E731
        check("대조: 주장이 없으면 건드리지 않는다", cu(ex()) is None)
        check("대조: 돌린 패턴이 0곳이면 확인됨", cu(ex(unused_claim=True, checked=["zz_absent"]))["verified"] is True)
        check("대조: 패턴을 안 적으면 확인 안 됨", cu(ex(unused_claim=True))["verified"] is False)
        check("대조: 돌린 적 없는 패턴이면 확인 안 됨", cu(ex(unused_claim=True, checked=["zz_never_ran"]))["why"] == "적은 패턴을 실제로 돌린 기록이 없다")
        ops.repo_grep(ctx, "zz_widget")   # 지금 대상(mine만)으로 다시 — 기록은 패턴마다 마지막 것
        check("대조: 쓰는 곳이 나온 패턴이면 확인 안 됨", cu(ex(unused_claim=True, checked=["zz_widget"]))["why"] == "쓰는 곳이 나왔다")
        ctx.targets = {"mine": str(code), "nogit": str(Path(td) / "repo")}
        ops.repo_grep(ctx, "zz_absent2")
        check("대조: 코드를 못 읽은 repo가 있으면 확인 안 됨", cu(ex(unused_claim=True, checked=["zz_absent2"]))["why"] == "코드를 못 읽은 repo가 있다")
        rep = detect_ops.detect_report(ctx, "2026-11-20T00:00:00+09:00", skipped=[], dropped=[], written=[], failed=[],
                                       excluded=detect_ops.check_unused(ctx, [ex(unused_claim=True, checked=["zz_absent"]), ex(unused_claim=True)]))
        check("보고서: 확인된 것과 안 된 것을 따로 센다", rep["unused"]["verified"] == ["u"] and rep["unused"]["unverified"][0]["why"] == "찾아본 패턴을 적지 않았다", rep["unused"])
        # 3c 보안 소식은 「안 쓴다」로 못 뺀다 — 관문의 security 표시 · 되돌릴 것 고르기 · 초안에 적는 줄 (사용자 2026-11-20)
        snapd = Path(td) / "snap-sec"
        snapd.mkdir()
        (snapd / "gate.json").write_text(json.dumps({
            "https://x/sec": {"kept_by": [{"technology": "python", "tier": "L0", "change": "security"}]},
            "https://x/brk": {"kept_by": [{"technology": "fastapi", "tier": "L4", "change": "breaking"}]}}), encoding="utf-8")
        check("보안 표시: gate.json이 없으면 빈 집합 (예전 입력 · 픽스처는 그대로)", detect_ops.security_urls(ctx) == set())
        ctx.env["RADAR_SNAPSHOT_DIR"] = str(snapd)
        sec = detect_ops.security_urls(ctx)
        check("보안 표시: 관문이 security로 남긴 url만", sec == {"https://x/sec"}, sec)
        exs = [{"url": "https://x/sec", "reason": "문제 모듈 미사용", "unused_claim": True, "checked": ["zz_absent"]},
               {"url": "https://x/sec", "reason": "HTTP/2 서버를 열지 않는다"},                      # 표시 없이 버린 것도 말로 잡는다 (W47)
               {"url": "https://x/sec", "reason": "스택에 없는 기술의 권고"},
               {"url": "https://x/brk", "reason": "미사용", "unused_claim": True, "checked": ["zz_absent"]}]
        back = detect_ops.unused_security(exs, sec)
        check("되돌릴 것: 보안 소식을 안 쓴다는 이유로 버린 것만 (표시 · 말 둘 다)", back == exs[:2], back)
        # 3d 자리 질의가 고른 후보는 거르기가 다시 빼지 못한다 — 대상 repo가 적힌 자리만 넘긴다
        gate = json.loads((snapd / "gate.json").read_text(encoding="utf-8"))
        gate["https://x/model"] = {"kept_by": [], "slots": [{"slot": "judge", "name": "채점", "repo": "mine", "role": "루브릭 채점", "fit": 2, "reason": "비교해 볼 만"},
                                                            {"slot": "old", "name": "뺀 repo의 자리", "repo": "gone", "role": "r", "fit": 2, "reason": "x"}]}
        gate["https://x/other"] = {"kept_by": [], "slots": [{"slot": "old", "name": "n", "repo": "gone", "role": "r", "fit": 2, "reason": "x"}]}
        (snapd / "gate.json").write_text(json.dumps(gate), encoding="utf-8")
        ctx.targets = {"mine": str(code)}
        sl = detect_ops.slot_reasons(ctx)
        check("자리 후보: 대상 repo의 자리만 (뺀 repo의 자리는 넘기지 않는다)", list(sl) == ["https://x/model"] and [h["slot"] for h in sl["https://x/model"]] == ["judge"], sl)
        exs2 = exs + [{"url": "https://x/model", "reason": "코드에 아직 채점 모델 사용처 없음"}, {"url": "https://x/other", "reason": "스택 밖"}]
        pairs = detect_ops.to_rescue(exs2, sec, set(sl))
        check("되돌릴 것: 보안(안 쓴다) 2 + 자리 후보 1, 이유가 붙는다", [(e["url"], w) for e, w in pairs] ==
              [("https://x/sec", "security"), ("https://x/sec", "security"), ("https://x/model", "slot")], pairs)
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 레포 루트 — pipeline 패키지
        from pipeline import detect_feed
        sj ={"per_slot": {"judge": [{"id": "a", "fit": 3, "reason": "r1", "rank": 1}], "gate": [{"id": "a", "fit": 2, "reason": "r2", "rank": 2}, {"id": "b", "fit": 2, "reason": "r3", "rank": 1}]},
              "slot_names": {"judge": "채점", "gate": "관문"}, "slot_repos": {"judge": "mine", "gate": "mine"}, "slot_roles": {"judge": "루브릭 채점"}}
        hits = detect_feed.slot_hits(sj, "a")
        check("입력 만들기: 항목이 든 자리를 전부 넘긴다 (repo · 역할 · 이유)", [(h["slot"], h["repo"], h["fit"], h["role"]) for h in hits] ==
              [("judge", "mine", 3, "루브릭 채점"), ("gate", "mine", 2, None)], hits)
        check("입력 만들기: 예전 slots.json(repo 없음)도 돈다", detect_feed.slot_hits({"per_slot": {"j": [{"id": "a", "fit": 2, "reason": "r"}]}}, "a")[0]["repo"] is None)
        ops.repo_grep(ctx, "zz_absent")
        lines = detect_ops.usage_lines(ctx, ["zz_absent", "zz_widget", "zz_never_ran"])
        check("초안에 적는 줄: 개수는 기록에서 읽는다", "mine 0곳" in lines[0] and "mine 2곳" in lines[1] and "돌린 기록 없음" in lines[2], lines)
        body = detect_ops.with_usage(ctx, {"research_note": ["a"]}, {"checked": ["zz_absent"]})
        check("초안 서술에 코드 찾기 줄을 코드가 덧붙인다", body["research_note"][0] == "a" and "zz_absent" in body["research_note"][1], body)
        check("찾아본 것이 없으면 서술은 그대로", detect_ops.with_usage(ctx, {"research_note": ["a"]}, {}) == {"research_note": ["a"]})
        ctx.env.pop("RADAR_SNAPSHOT_DIR")
        ctx.targets, ctx.excludes = {"medical-mate": str(Path(td) / "repo")}, {}
        ctx.env["RADAR_TARGETS"] = str(Path(td) / "radar-targets.json")

        # 4 실패
        for nm, sc, kw in [("is_error(로그인·한도)", {"is_error": True, "subtype": "error_during_execution", "structured": None}, {}),
                           ("결과 모양 이상", {"stdout": "not json"}, {}),
                           ("structured_output 없음", {"structured": None}, {}),
                           ("스키마 불일치", {"structured": {"result": "met"}}, {}),
                           ("턴 초과", {"structured": VERDICT, "num_turns": 30}, {}),
                           ("시간 초과 → 프로세스 종료", {"structured": VERDICT, "sleep": 20}, {"timeout": 2})]:
            script(td, "f", sc)
            rr = engines.ClaudeCliRunner(bin=FAKE, **({"timeout": 60} | kw))
            t0 = time.time()
            try:
                await rr.run(name="x", system="s", user="u", schema=Verdict, tools=REVISIT_TOOLS, ctx=ctx, max_turns=8)
                check(f"실패: {nm} → EngineError", False, "예외 없음")
            except engines.EngineError as e:
                check(f"실패: {nm} → EngineError", "engine_error" in str(e), e)
            if nm.startswith("시간"):
                check("시간 초과는 제한 시간 안에 끊긴다", time.time() - t0 < 15, time.time() - t0)

    # 3 가드 — 서버 세션을 직접 (LangChain 판과 같은 check_lookup)
    with tempfile.TemporaryDirectory() as td:
        ctx = make_ctx(td)
        seen = Path(td) / "seen.txt"
        seen.write_text("source_hint: https://docs.example/guard/page", encoding="utf-8")
        env = {**ctx.env, "RADAR_SEEN_FILE": str(seen)}
        _, s = mcp_tools.build(["snapshot_fetch", "snapshot_search"], guard=True, env=env)
        out = json.loads(s.call("snapshot_fetch", {"url": "https://invented.example/x"}, lambda: '{"ok": true}'))
        check("가드: 본 적 없는 URL은 not_offered", out.get("status") == "not_offered", out)
        out = json.loads(s.call("snapshot_fetch", {"url": "docs.example/guard/page/"}, lambda: '{"ok": true, "body": "see https://docs.example/next"}'))
        check("가드: 프롬프트에 나온 URL은 연다 (scheme·끝 / 무시)", out.get("ok") is True, out)
        out = json.loads(s.call("snapshot_fetch", {"url": "https://docs.example/next"}, lambda: '{"ok": true}'))
        check("가드: 도구 결과에 나온 URL도 연다", out.get("ok") is True, out)
        for _ in range(LOOKUP_BUDGET):
            out = json.loads(s.call("snapshot_search", {"query": "q"}, lambda: '{"ok": true, "results": []}'))
        check(f"가드: 열기·검색 합쳐 {LOOKUP_BUDGET}회 넘으면 거절 (거절도 센다)", out.get("status") == "lookup_budget_exhausted", out)
        _, s2 = mcp_tools.build(["snapshot_fetch"], guard=False, env=env)
        out = json.loads(s2.call("snapshot_fetch", {"url": "https://invented.example/x"}, lambda: '{"ok": true}'))
        check("가드 없는 판(detect)은 거절하지 않는다", out.get("ok") is True, out)
        try:
            mcp_tools.main(["--allow", "propose_adr"])
            check("쓰기 도구는 서버가 거부", False)
        except SystemExit as e:
            check("쓰기 도구는 서버가 거부", "읽기 도구만" in str(e), e)

    # LangChain 판 가드도 같은 함수 — 시그니처가 갈라지면 여기서 걸린다
    from radar.tools import check_lookup, lookup_guard
    check("LangChain 가드 = check_lookup", lookup_guard.__code__.co_names.count("check_lookup") == 1 and callable(check_lookup))


if __name__ == "__main__":
    asyncio.run(main())
    print(f"\n{'FAILED ' + str(len(fails)) if fails else 'all passed'}")
    sys.exit(1 if fails else 0)
