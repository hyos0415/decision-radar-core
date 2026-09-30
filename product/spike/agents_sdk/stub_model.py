"""정해진 답을 돌려주는 가짜 모델 — API 없이 파이프라인·어댑터·채점 연결을 확인한다.

잴 수 있는 것: 흐름(list → 판정 → decide → run_report), P4, P5, 채점기가 제품 trace를 무는지(음성 대조).
잴 수 없는 것: **P3** (어댑터 400·파싱 실패) — 실제 모델에서만 드러난다.

행동은 조건에서 정한다. 첫 턴에 source_hint를 도구로 열고, 두 번째 턴에 판정을 낸다.
- 접근 실패 → undetermined (스크립트와 무관)
- numeric: 파일이 JSON이고 metric 키가 있으면 그 값, 아니면 스크립트의 observed_value
- web: 스크립트의 result (없으면 not_met) — 페이지를 읽고 판단하는 부분만 정해진 답이다
"""

import json
import re

from agents import ModelResponse, Usage
from agents.models.interface import Model
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

COND = re.compile(r"<condition>(.*?)</condition>", re.S)


def _get(item, key):
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)


def _text(content):
    if isinstance(content, str):
        return content
    return "".join(_get(c, "text") or "" for c in content or [])


class StubModel(Model):
    def __init__(self, script):
        self.script = script  # {"0001/c1": {"result": "met"}, "0002/c2": {"observed_value": 0.15}, "…": {"evidence": [...]}}
        self.n = 0

    def _id(self, p):
        self.n += 1
        return f"{p}_{self.n}"

    def _tool_call(self, cond):
        hint = cond.get("source_hint") or ""
        m = re.match(r"^([\w.-]+):(?!//)(.+)$", hint)
        if m and "." not in m.group(1):  # finance-verifier:eval/… (repo 이름에는 점이 없다 — 도메인과 가른다)
            name, args = "read_repo_file", {"repo": m.group(1), "path": m.group(2)}
        else:
            name, args = "snapshot_fetch", {"url": hint}
        return ResponseFunctionToolCall(type="function_call", id=self._id("fc"), call_id=self._id("call"),
                                        name=name, arguments=json.dumps(args, ensure_ascii=False))

    def _verdict(self, cond, tool_out):
        key = f"{cond['adr']}/{cond['condition_id']}"
        s = self.script.get(key, {})
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

    async def get_response(self, system_instructions, input, model_settings, tools, output_schema, handoffs, tracing, **kw):
        items = input if isinstance(input, list) else [{"role": "user", "content": input}]
        prompt = next(_text(_get(i, "content")) for i in items if _get(i, "role") == "user")
        cond = json.loads(COND.search(prompt).group(1))
        outputs = [i for i in items if _get(i, "type") == "function_call_output"]
        s = self.script.get(f"{cond['adr']}/{cond['condition_id']}", {})
        if s.get("fault") == "malformed":  # 음성 대조: 스키마에 안 맞는 출력 → SDK 파싱 실패 (P3)
            out = [ResponseOutputMessage(type="message", id=self._id("msg"), role="assistant", status="completed",
                                         content=[ResponseOutputText(type="output_text", text="met, I think", annotations=[])])]
        elif s.get("fault") == "no_tool":  # 음성 대조: 아무것도 열지 않고 답 (P3 도구 호출 누락)
            text = json.dumps({"result": s.get("result", "not_met"), "reason": None, "observed_value": None, "evidence": []})
            out = [ResponseOutputMessage(type="message", id=self._id("msg"), role="assistant", status="completed",
                                         content=[ResponseOutputText(type="output_text", text=text, annotations=[])])]
        elif not outputs:
            out = [self._tool_call(cond)]
        else:
            text = json.dumps(self._verdict(cond, _get(outputs[-1], "output")), ensure_ascii=False)
            out = [ResponseOutputMessage(type="message", id=self._id("msg"), role="assistant", status="completed",
                                         content=[ResponseOutputText(type="output_text", text=text, annotations=[])])]
        return ModelResponse(output=out, usage=Usage(), response_id=None)

    def stream_response(self, *a, **k):
        raise NotImplementedError("스텁은 스트리밍을 쓰지 않는다")


def _msg(stub, obj):
    return ResponseOutputMessage(type="message", id=stub._id("msg"), role="assistant", status="completed",
                                 content=[ResponseOutputText(type="output_text", text=json.dumps(obj, ensure_ascii=False), annotations=[])])


def _call(stub, name, args):
    return ResponseFunctionToolCall(type="function_call", id=stub._id("fc"), call_id=stub._id("call"),
                                    name=name, arguments=json.dumps(args, ensure_ascii=False))


class CaptureStub(StubModel):
    """capture용 정해진 답. script = {"match": {...}, "draft": {...}, "force_propose": bool}

    match_adr 결과를 보고 exact가 있으면 link 후보로 끝내고, 없으면(또는 force_propose) 초안을 제안한다.
    제안이 실행(승인)되든 거부되든 그 결과를 받으면 끝낸다. 호출 이름은 입력 기록에서 call_id로 찾는다 —
    2턴은 다른 프로세스에서 복원한 상태로 들어오므로 스텁은 자기 기억이 아니라 입력만 보고 판단한다.
    """

    async def get_response(self, system_instructions, input, model_settings, tools, output_schema, handoffs, tracing, **kw):
        items = input if isinstance(input, list) else [{"role": "user", "content": input}]
        names = {_get(i, "call_id"): _get(i, "name") for i in items if _get(i, "type") == "function_call"}
        outs = [(names.get(_get(i, "call_id")), _get(i, "output")) for i in items if _get(i, "type") == "function_call_output"]
        s = self.script
        if not outs:
            out = [_call(self, "match_adr", s["match"])]
        elif outs[-1][0] == "match_adr":
            exact = json.loads(outs[-1][1]).get("exact") or []
            if exact and not s.get("force_propose"):
                out = [_msg(self, {"link_candidates": exact, "note": "같은 3중 키 — 새 초안 대신 link 후보"})]
            else:
                out = [_call(self, "propose_adr", {"draft": s["draft"]})]
        else:
            try:  # 잘린 JSON을 note에 넣지 않는다 — run_case.extract_report는 문자열 안 중괄호도 센다
                res = json.loads(outs[-1][1])
                note = f"저장: {res.get('id')}" if isinstance(res, dict) and res.get("written") else "제안 결과 받음"
            except (json.JSONDecodeError, TypeError):
                note = "제안이 실행되지 않음 (거부)"
            out = [_msg(self, {"link_candidates": [], "note": note})]
        return ModelResponse(output=out, usage=Usage(), response_id=None)
