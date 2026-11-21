"""재시도 정책 회귀 (HYO-48 전제) — 실제 API 없이 가짜 SDK 클라이언트로(models.AnthropicModel의 클라이언트 자리). 대기는 주입해서 실제로 기다리지 않는다.

  python product/radar/test_retry.py        # 메인 .venv

시나리오: ① 연결 오류 2번 뒤 성공 → 통과·attempts=3·재시도 종류 기록·P3 아님 ② 계속 실패 → 끝내 예외(=P3)·attempts=상한
③ 400 → 재시도 안 함 ④ 429·529·500은 재시도 ⑤ 대기가 지수로 늘고 상한을 안 넘는다 ⑥ 동기 경로도 같다.
규율 3: 정책을 일부러 틀리게 바꾼 변이(4xx도 재시도 · 재시도 0회 · 대기 없음 · 대기 과다)에서 시나리오가 실제로 실패하는지 본다.
"""

import asyncio
import os
import sys

sys.path[0] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # product/
sys.stdout.reconfigure(encoding="utf-8")

import anthropic  # noqa: E402
import httpx2 as httpx  # noqa: E402  — anthropic 1.x는 httpx2 위에 있다 (httpx는 langchain을 따라 들어왔었다 — ADR 0030 뒤 없다)

from radar import llm_log, models, retry  # noqa: E402
from radar.loop import Request  # noqa: E402
from common.schemas import Verdict  # noqa: E402

REQ = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def status_err(code):
    return anthropic.APIStatusError(f"http {code}", response=httpx.Response(code, request=REQ), body=None)


def conn_err():
    return anthropic.APIConnectionError(request=REQ)


OK = anthropic.types.Message.model_validate({"id": "msg_x", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
                                             "stop_reason": "end_turn", "stop_sequence": None, "content": [{"type": "text", "text": "{}"}],
                                             "usage": {"input_tokens": 10, "output_tokens": 5}})
REQUEST = Request(agent="detect-triage", system="s", messages=[{"role": "user", "content": "hi"}], tools=[], schema=Verdict)


class FakeMessages:
    """앞에서부터 script를 하나씩 소비한다 — 예외면 던지고, 아니면 성공 응답. 호출 수를 센다 (SDK의 client.messages 자리)."""

    def __init__(self, script):
        self.script, self.calls = script, 0

    def _next(self):
        item = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return OK

    def create(self, **body):
        return self._next()


class AsyncFakeMessages(FakeMessages):
    async def create(self, **body):
        return self._next()


class Ctx:
    events = None

    def __init__(self):
        self.events = []


def run(script, sync=False):
    """한 번 호출 → (성공 여부, 예외, 모델 호출 수, llm_call 줄들, 대기 기록). 모델은 models.AnthropicModel — SDK 클라이언트 자리에 가짜를 꽂는다."""
    waits = []
    retry.SLEEPER.sleep = lambda s: waits.append(s)

    async def _asleep(s):
        waits.append(s)

    retry.SLEEPER.asleep = _asleep
    ctx, m = Ctx(), models.AnthropicModel(api_key="test-key")
    fake = (FakeMessages if sync else AsyncFakeMessages)(script)
    client = type("C", (), {"messages": fake})()
    m._client = lambda: client
    m._aclient = lambda: client
    llm_log.attach(m, ctx, "detect", "claude-sonnet-5-5")
    exc = None
    try:
        m.call(REQUEST) if sync else asyncio.run(m.acall(REQUEST))
    except Exception as e:  # noqa: BLE001
        exc = e
    return exc is None, exc, fake.calls, llm_log.read_calls(ctx.events), waits


def scenarios():
    out = []

    def check(name, ok, detail=None):
        print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok or detail is None else f" — {detail}"))
        if not ok:
            out.append(name)

    ok, exc, n, rec, waits = run([conn_err(), conn_err(), "성공"])
    check("① 연결 오류 2번 뒤 성공 — 통과", ok and n == 3, (ok, n, exc))
    check("① llm_call 한 줄 · attempts=3 · retried 2건", len(rec) == 1 and rec[0].get("attempts") == 3 and len(rec[0].get("retried", [])) == 2, rec)
    check("① 오류로 안 세어짐(error 칸 없음) · 비용은 성공 호출 기준", len(rec) == 1 and "error" not in rec[0] and rec[0]["input_tokens"] == 10, rec)
    check("① 대기 2번(실제로 안 기다림)", len(waits) == 2, waits)

    ok, exc, n, rec, waits = run([conn_err()])
    check("② 계속 실패 → 끝내 예외 (P3 경로)", (not ok) and isinstance(exc, anthropic.APIConnectionError), exc)
    check("② 시도 = 상한", n == retry.MAX_ATTEMPTS and len(waits) == retry.MAX_ATTEMPTS - 1, (n, waits))
    check("② llm_call error 줄 · 토큰 0 · attempts=상한", len(rec) == 1 and "error" in rec[0] and rec[0]["input_tokens"] == 0 and rec[0]["cost_usd"] == 0.0
          and rec[0].get("attempts") == retry.MAX_ATTEMPTS, rec)

    ok, exc, n, rec, waits = run([status_err(400), "성공"])
    check("③ 400 → 재시도 안 함", (not ok) and n == 1 and not waits, (n, waits))
    check("③ 기록 attempts=1 · error", len(rec) == 1 and rec[0].get("attempts") == 1 and "error" in rec[0], rec)
    ok, exc, n, rec, waits = run([status_err(401), "성공"])
    check("③ 401도 재시도 안 함", (not ok) and n == 1, n)

    for code in (429, 529, 500, 503):
        ok, exc, n, rec, waits = run([status_err(code), "성공"])
        check(f"④ {code} → 재시도 뒤 성공", ok and n == 2 and rec and rec[0].get("retried") == [f"APIStatusError({code})"], (ok, n, rec))

    ok, exc, n, rec, waits = run([anthropic.APITimeoutError(request=REQ), "성공"])
    check("④ 타임아웃 → 재시도 뒤 성공", ok and n == 2, (ok, n))

    ok, exc, n, rec, waits = run([conn_err()])
    # 절대값으로 본다(정책 상수를 따라가면 상수를 바꾼 변이를 못 잡는다): 2·4·8·16초에 지터 25% → 구간이 겹치지 않아 순증가
    check("⑤ 대기가 지수로 늘어난다 (엄밀 증가·0 아님)", len(waits) == 4 and all(0 < w for w in waits) and all(waits[i] < waits[i + 1] for i in range(3)), waits)
    check("⑤ 대기 1회 ≤ 16초 · 합 ≤ 30초", all(w <= 16 for w in waits) and sum(waits) <= 30, (waits, sum(waits)))

    ok, exc, n, rec, waits = run([conn_err(), "성공"], sync=True)
    check("⑥ 동기 경로도 재시도", ok and n == 2 and rec and rec[0].get("attempts") == 2, (ok, n, rec))
    ok, exc, n, rec, waits = run([status_err(400), "성공"], sync=True)
    check("⑥ 동기 경로 400은 재시도 안 함", (not ok) and n == 1, n)
    return out


def with_patch(**attrs):
    class P:
        def __enter__(self):
            self.old = {k: getattr(retry, k) for k in attrs}
            for k, v in attrs.items():
                setattr(retry, k, v)

        def __exit__(self, *a):
            for k, v in self.old.items():
                setattr(retry, k, v)
    return P()


print("정책 (정상)")
base = scenarios()
bad = []
mutants = [
    ("변이 A: 4xx도 재시도", dict(is_transient=lambda e: True)),
    ("변이 B: 재시도 0회 (MAX_ATTEMPTS=1)", dict(MAX_ATTEMPTS=1)),
    ("변이 C: 대기 없음 (delay=0)", dict(delay=lambda n: 0.0)),
    ("변이 E: 대기가 너무 김 (BACKOFF_BASE=100, 상한 1e6)", dict(BACKOFF_BASE=100.0, BACKOFF_CAP=1e6)),
    ("변이 D: 아무 것도 재시도 안 함", dict(is_transient=lambda e: False)),
]
for name, patch in mutants:
    print(name)
    with with_patch(**patch):
        caught = scenarios()
    check = bool(caught)
    print(f"  => {'잡힘 ' + str(len(caught)) + '건' if check else 'FAIL 못 잡음'}")
    if not check:
        bad.append(name)
print(f"\n{'FAILED' if base or bad else 'all passed'}" + (f" — 정상 실패 {base}" if base else "") + (f" — 못 잡은 변이 {bad}" if bad else ""))
sys.exit(1 if base or bad else 0)
