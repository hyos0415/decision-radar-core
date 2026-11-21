"""정해진 답을 돌려주는 가짜 모델 — 판단은 stub_policy, 여기는 Anthropic 응답 모양(content 블록)으로 바꾸기만 한다.

잴 수 있는 것: 흐름, P4, P5, P6, 채점기가 제품 trace를 무는지. 잴 수 없는 것: **P3** (API 요청 모양·400)와 모델의 판단.
토큰은 글자 수/4 추정이다(estimated=true) — 호출 기록(llm_log)의 배관을 타게 하려는 것이다.
처음엔 LangChain BaseChatModel이었다 — 루프를 직접 돌린 뒤로 loop.py의 모델 자리 약속(call · acall · attach_log)만 지킨다 (ADR 0030, HYO-220).
"""

import json

from . import llm_log, stub_policy
from .loop import Reply, text_of


class StubChat:
    engine = "stub"

    def __init__(self, script, flow):
        self.script = script
        self.flow = flow  # "revisit" | "capture" | "detect"
        self.n = 0
        self.log = None

    def attach_log(self, ctx, flow, model_name):
        self.log = llm_log.CallLog(ctx, flow, model_name)
        return self.log

    def _id(self, p):
        self.n += 1
        return f"{p}_{self.n}"

    def _action(self, conv_messages):
        prompt = next(m["content"] for m in conv_messages if m["role"] == "user" and isinstance(m["content"], str))
        names = {b["id"]: b["name"] for m in conv_messages if m["role"] == "assistant" and isinstance(m["content"], list)
                 for b in m["content"] if b.get("type") == "tool_use"}
        results = [(names.get(b.get("tool_use_id")), text_of(b.get("content"))) for m in conv_messages
                   if m["role"] == "user" and isinstance(m["content"], list) for b in m["content"] if b.get("type") == "tool_result"]
        if self.flow == "revisit":
            return stub_policy.revisit_step(self.script, prompt, [out for _, out in results])
        if self.flow == "detect":
            if "<feed>" in prompt:
                return stub_policy.detect_triage_step(self.script, prompt)
            if "<repair>" in prompt:
                return stub_policy.detect_repair_step(self.script, prompt)
            return stub_policy.detect_draft_step(self.script, prompt)
        return stub_policy.capture_step(self.script, results)

    def call(self, req):
        action = self._action(req.messages)
        if action[0] == "call":
            content = [{"type": "tool_use", "id": self._id("call"), "name": action[1], "input": action[2]}]
            stop = "tool_use"
        else:
            content = [{"type": "text", "text": action[1] if action[0] == "text" else json.dumps(action[1], ensure_ascii=False)}]
            stop = "end_turn"
        seen = len(req.system or "") + sum(len(text_of(m["content"])) if isinstance(m["content"], str)
                                           else len(json.dumps(m["content"], ensure_ascii=False)) for m in req.messages)
        if self.log is not None:
            self.log.add_call(agent=req.agent, model="stub", input_tokens=seen // 4,
                              output_tokens=len(json.dumps(content, ensure_ascii=False)) // 4, estimated=True)
        return Reply(content=content, stop_reason=stop)

    async def acall(self, req):
        return self.call(req)
