"""호출마다 모델·입력/출력 토큰·비용을 남긴다 (HYO-46 최소판).

모델 호출 하나가 끝날 때마다 ctx.events에 `{"type": "llm_call", ...}` 한 줄을 넣는다. ctx.events는 그대로
transcript*.jsonl이 되므로 호출 기록이 도구 호출 기록과 같은 파일에, 일어난 순서대로 남는다.
채점기는 type이 assistant·result·system인 줄만 읽어서 이 줄은 채점에 영향이 없다.

스텁 모델의 토큰은 글자 수/4 추정이다(estimated=true) — 배관 검증용이고 비용은 0으로 둔다.
단가표(common.env.PRICES)에 없는 모델은 cost_usd가 None이다(모름 — 0으로 세지 않는다).
처음엔 LangChain 콜백(on_llm_end · on_llm_error)이었다 — 지금은 모델 자리(models · stub_model)가 호출마다 add_call을 부른다 (ADR 0030).
"""

import threading

from common.env import cost_for


class CallLog:
    def __init__(self, ctx, flow, default_model):
        self.ctx, self.flow, self.default_model = ctx, flow, default_model
        self._lock, self.calls = threading.Lock(), []

    def add_call(self, *, agent, model=None, input_tokens=0, output_tokens=0, estimated=False, attempts=1, retried=None, error=None):
        """호출 한 번(재시도를 합친 것) = 한 줄. 실패한 시도는 토큰 0이라 비용은 성공한 호출 기준이다."""
        model = model or self.default_model
        stub = estimated or self.default_model == "stub"
        rec = {"type": "llm_call", "flow": self.flow, "agent": agent, "model": model,
               "input_tokens": input_tokens, "output_tokens": output_tokens, "estimated": stub,
               "cost_usd": 0.0 if (stub or error) else cost_for(model, input_tokens, output_tokens),
               **({"error": error} if error else {}), "attempts": attempts, **({"retried": list(retried)} if retried else {})}
        with self._lock:
            rec["seq"] = len(self.calls) + 1
            self.calls.append(rec)
            self.ctx.events.append(rec)
        return rec

    def totals(self):
        return {"model_calls": len(self.calls), "input_tokens": sum(c["input_tokens"] for c in self.calls),
                "output_tokens": sum(c["output_tokens"] for c in self.calls)}


def attach(model, ctx, flow, default_model):
    """모델에 호출 기록을 붙인다 — 모델 자리(models.AnthropicModel · stub_model.StubChat · engines.ClaudeCliRunner)가 각자 attach_log를 가진다."""
    return model.attach_log(ctx, flow, default_model)


def read_calls(events):
    return [e for e in events if e.get("type") == "llm_call"]
