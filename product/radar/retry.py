"""모델 호출 재시도 정책 (HYO-48 전제) — 일시적 오류만 지수 백오프로 다시 부른다.

배경: 2026-10-06 제품 실제 모델 53케이스 k=1에서 4건이 detect triage 첫 호출의 `AnthropicConnectionError: Connection error.`
(토큰 0)로 실패했고, 같은 4건을 다시 돌리니 전부 통과했다. 게이트(pass^k k=10, 150회)에서 이런 오류는 안전 실패로 집계된다.
anthropic SDK는 max_retries=2가 기본이고 0.5초·1초 안팎의 짧은 대기(총 1.5초 안쪽)로
재시도한다 — 순간 끊김보다 긴 끊김은 못 넘는다. 그래서 SDK 재시도는 끄고(models.py max_retries=0) 이 정책이 한 곳에서 맡는다:
시도 횟수를 세어 llm_call 기록에 남길 수 있고, 대기 상한이 한 군데 상수다. (처음엔 ChatAnthropic 앞에 섞는 RetryMixin이었다 — 지금은
models.AnthropicModel이 call_sync · call_async로 SDK 호출을 직접 감싼다, ADR 0030)

후속(2026-10-06): 게이트의 첫 호출 연결 오류 590건은 일시 장애가 아니라 루프를 건너 재사용된 비동기 연결이었다 — models.py가 고쳤다(test_loop_client.py).
재시도 대상: 연결 오류·타임아웃(상태 코드 없음) · 429 · 5xx(529 overloaded 포함). 그 밖의 4xx(요청 형식 오류 등)는 다시 불러도
같은 결과이므로 재시도하지 않는다 — 어댑터 400이 P3로 드러나야 한다.
재시도 끝에 성공한 호출은 오류가 아니다(llm_call에 error 칸이 없다). 끝내 실패하면 그 예외가 그대로 올라가 지금처럼 P3다.
실패한 시도는 토큰 0이라 비용은 성공한 호출 기준이다.
"""

import asyncio
import random
import threading
import time

# 시도 상한 5회(재시도 4회). 대기는 BASE·2^n에 상한 — 2·4·8·16초, 최악 합 30초.
# 근거: SDK 기본(총 ~1.5초)이 못 넘은 끊김을 넘으려면 한 자릿수 초 이상의 대기가 필요하고, 합 30초면 일시 장애의 대부분을 덮되
# 호출 하나가 게이트(150회)를 오래 붙잡지 않는다. 스킬 쪽 `claude -p`(Claude Code)도 API 오류를 내부에서 재시도하지만
# 그 횟수·대기는 모른다 — 같다고 주장하지 않는다(README 「스킬과의 비대칭」 #13).
MAX_ATTEMPTS = 5
BACKOFF_BASE = 2.0
BACKOFF_CAP = 16.0
JITTER = 0.25  # 대기를 (1-JITTER)~1배로 흩뜨려 병렬 호출(Send fan-out)이 같은 순간에 다시 몰리지 않게 한다

_CONNECTION_NAMES = {"APIConnectionError", "APITimeoutError", "ConnectError", "ConnectTimeout", "ReadTimeout", "TimeoutException"}


def error_kind(e):
    """기록에 남길 오류 종류 — 클래스 이름, 상태 코드가 있으면 붙인다."""
    code = getattr(e, "status_code", None)
    return f"{type(e).__name__}({code})" if code else type(e).__name__


def is_transient(e):
    code = getattr(e, "status_code", None)
    if isinstance(code, int):
        return code == 429 or code >= 500
    if isinstance(e, (ConnectionError, TimeoutError)):
        return True
    return any(c.__name__ in _CONNECTION_NAMES for c in type(e).__mro__)


def delay(n):
    """n번째 재시도(0부터) 앞의 대기 초."""
    return min(BACKOFF_CAP, BACKOFF_BASE * 2 ** n) * (1 - JITTER * random.random())


# run_id별 시도 기록 — models.AnthropicModel이 호출이 끝나면(성공·실패) 꺼내 llm_call 줄에 붙인다.
_INFO, _LOCK = {}, threading.Lock()


def _note(run_id, attempts, retried):
    if run_id is not None:
        with _LOCK:
            _INFO[run_id] = {"attempts": attempts, "retried": list(retried)}


def pop_info(run_id):
    with _LOCK:
        return _INFO.pop(run_id, None) or {"attempts": 1, "retried": []}


class _Sleeper:
    """테스트가 주입하는 자리 — 기본은 실제 대기."""
    sleep = staticmethod(time.sleep)
    asleep = staticmethod(asyncio.sleep)


SLEEPER = _Sleeper()


def call_sync(fn, run_id=None):
    retried = []
    for n in range(MAX_ATTEMPTS):
        try:
            out = fn()
            _note(run_id, n + 1, retried)
            return out
        except Exception as e:  # noqa: BLE001
            if n + 1 >= MAX_ATTEMPTS or not is_transient(e):
                _note(run_id, n + 1, retried)
                raise
            retried.append(error_kind(e))
            SLEEPER.sleep(delay(n))


async def call_async(fn, run_id=None):
    retried = []
    for n in range(MAX_ATTEMPTS):
        try:
            out = await fn()
            _note(run_id, n + 1, retried)
            return out
        except Exception as e:  # noqa: BLE001
            if n + 1 >= MAX_ATTEMPTS or not is_transient(e):
                _note(run_id, n + 1, retried)
                raise
            retried.append(error_kind(e))
            await SLEEPER.asleep(delay(n))
