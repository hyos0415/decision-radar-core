"""스텁 모델의 판단 — 런타임 무관. 두 런타임의 스텁은 이 함수가 돌려준 행동을 자기 메시지 형식으로 바꾸기만 한다.

같은 입력에 같은 행동을 내야 "두 런타임이 같은 모델을 쓴다"가 스텁에서도 참이다.
행동: ("call", 도구 이름, 인자) · ("final", dict) · ("text", 스키마에 안 맞는 문자열 — 음성 대조용)

revisit (조건 하나):
- 첫 턴에 source_hint를 도구로 열고, 두 번째 턴에 판정을 낸다
- 접근 실패 → undetermined (스크립트와 무관)
- numeric: 파일이 JSON이고 metric 키가 있으면 그 값, 아니면 스크립트의 observed_value
- web: 스크립트의 result (없으면 not_met) — 페이지를 읽고 판단하는 부분만 정해진 답이다
capture:
- match_adr 결과를 보고 exact가 있으면 link 후보로 끝내고, 없으면(또는 force_propose) 초안을 제안한다
- 제안이 실행(승인)되든 거부되든 그 결과를 받으면 끝낸다 — 기억이 아니라 입력 기록만 본다 (2턴은 다른 프로세스)
"""

import json
import re

COND = re.compile(r"<condition>(.*?)</condition>", re.S)


def parse_condition(prompt):
    return json.loads(COND.search(prompt).group(1))


def _tool_for(cond):
    hint = cond.get("source_hint") or ""
    m = re.match(r"^([\w.-]+):(?!//)(.+)$", hint)
    if m and "." not in m.group(1):  # finance-verifier:eval/… (repo 이름에는 점이 없다 — 도메인과 가른다)
        return "read_repo_file", {"repo": m.group(1), "path": m.group(2)}
    return "snapshot_fetch", {"url": hint}


def _verdict(cond, s, tool_out):
    out = json.loads(tool_out)
    if not out.get("ok"):
        return {"result": "undetermined", "reason": f"fetch_failed: {out.get('status') or out.get('error')}",
                "observed_value": None, "evidence": []}
    st = out.get("source_type_hint") or "community"
    ev = [{"url": u, "source_type": st} for u in s.get("evidence", [out["url"]])]
    if cond["check_type"] == "numeric":
        v = s.get("observed_value")
        if v is None:
            try:
                v = json.loads(out["body"]).get((cond.get("threshold") or {}).get("metric"))
            except (json.JSONDecodeError, AttributeError):
                v = None
        return {"result": None, "reason": None if v is not None else "metric_missing", "observed_value": v, "evidence": ev}
    return {"result": s.get("result", "not_met"), "reason": s.get("reason"), "observed_value": None, "evidence": ev}


def revisit_step(script, prompt, tool_outputs):
    """tool_outputs: 이번 판정에서 지금까지 받은 도구 결과 문자열 목록."""
    cond = parse_condition(prompt)
    s = script.get(f"{cond['adr']}/{cond['condition_id']}", {})
    if s.get("fault") == "malformed":  # 음성 대조: 스키마에 안 맞는 출력 → 파싱 실패 (P3)
        return ("text", "met, I think")
    if s.get("fault") == "no_tool":  # 음성 대조: 아무것도 열지 않고 답 (P3 도구 호출 누락)
        return ("final", {"result": s.get("result", "not_met"), "reason": None, "observed_value": None, "evidence": []})
    if not tool_outputs:
        name, args = _tool_for(cond)
        return ("call", name, args)
    return ("final", _verdict(cond, s, tool_outputs[-1]))


def capture_step(script, history):
    """history: [(도구 이름, 결과 문자열)] — 이번 run에서 받은 도구 결과 순서대로."""
    if not history:
        return ("call", "match_adr", script["match"])
    name, out = history[-1]
    if name == "match_adr":
        exact = json.loads(out).get("exact") or []
        if exact and not script.get("force_propose"):
            return ("final", {"link_candidates": exact, "note": "같은 3중 키 — 새 초안 대신 link 후보"})
        return ("call", "propose_adr", {"draft": script["draft"]})
    try:  # 잘린 JSON을 note에 넣지 않는다 — run_case.extract_report는 문자열 안 중괄호도 센다
        res = json.loads(out)
        note = f"저장: {res.get('id')}" if isinstance(res, dict) and res.get("written") else "제안 결과 받음"
    except (json.JSONDecodeError, TypeError):
        note = "제안이 실행되지 않음 (거부)"
    return ("final", {"link_candidates": [], "note": note})
