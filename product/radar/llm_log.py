"""호출마다 모델·입력/출력 토큰·비용을 남긴다 (HYO-46 최소판).

모델 호출 하나가 끝날 때마다 ctx.events에 `{"type": "llm_call", ...}` 한 줄을 넣는다. ctx.events는 그대로
transcript*.jsonl이 되므로 호출 기록이 도구 호출 기록과 같은 파일에, 일어난 순서대로 남는다.
채점기는 type이 assistant·result·system인 줄만 읽어서 이 줄은 채점에 영향이 없다.

스텁 모델의 토큰은 글자 수/4 추정이다(estimated=true) — 배관 검증용이고 비용은 0으로 둔다.
"""

import threading

from langchain_core.callbacks import BaseCallbackHandler

from common.env import cost_usd

from . import retry


class CallLog(BaseCallbackHandler):
    def __init__(self, ctx, flow, default_model):
        self.ctx, self.flow, self.default_model = ctx, flow, default_model
        self._start, self._lock, self.calls = {}, threading.Lock(), []

    def on_chat_model_start(self, serialized, messages, *, run_id, metadata=None, **kw):
        md = metadata or {}
        self._start[run_id] = {"agent": md.get("lc_agent_name") or md.get("langgraph_node") or "?",
                               "model": (kw.get("invocation_params") or {}).get("model")}

    @staticmethod
    def _attempts(run_id):
        """시도 횟수·재시도한 오류 종류 (retry.py). 재시도가 없었으면 attempts=1만 남긴다. 실패한 시도는 토큰 0이라 비용에 안 든다."""
        info = retry.pop_info(run_id)
        return {"attempts": info["attempts"], **({"retried": info["retried"]} if info["retried"] else {})}

    def _emit(self, rec):
        with self._lock:
            rec["seq"] = len(self.calls) + 1
            self.calls.append(rec)
            self.ctx.events.append(rec)

    def on_llm_end(self, response, *, run_id, **kw):
        st = self._start.pop(run_id, {})
        msg = response.generations[0][0].message
        um = getattr(msg, "usage_metadata", None) or {}
        meta = getattr(msg, "response_metadata", None) or {}
        model = meta.get("model_name") or meta.get("model") or st.get("model") or self.default_model
        i, o = um.get("input_tokens", 0), um.get("output_tokens", 0)
        stub = self.default_model == "stub"
        rec = {"type": "llm_call", "flow": self.flow, "agent": st.get("agent"), "model": model,
               "input_tokens": i, "output_tokens": o, "estimated": stub,
               "cost_usd": 0.0 if stub else cost_usd(i, o)}
        self._emit({**rec, **self._attempts(run_id)})

    def on_llm_error(self, error, *, run_id, **kw):
        st = self._start.pop(run_id, {})
        self._emit({"type": "llm_call", "flow": self.flow, "agent": st.get("agent"), "model": self.default_model,
                    "input_tokens": 0, "output_tokens": 0, "estimated": False, "cost_usd": 0.0,
                    "error": f"{type(error).__name__}: {str(error)[:200]}", **self._attempts(run_id)})

    def totals(self):
        return {"model_calls": len(self.calls), "input_tokens": sum(c["input_tokens"] for c in self.calls),
                "output_tokens": sum(c["output_tokens"] for c in self.calls)}


def attach(model, ctx, flow, default_model):
    """모델 객체에 호출 기록 핸들러를 붙인다 (모델 수준 콜백 — create_agent가 bind해도 같은 객체의 호출은 잡힌다)."""
    log = CallLog(ctx, flow, default_model)
    model.callbacks = [log]
    return log


def read_calls(events):
    return [e for e in events if e.get("type") == "llm_call"]
