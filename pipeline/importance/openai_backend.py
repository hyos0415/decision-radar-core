"""OpenAI 백엔드 — openai SDK의 Responses API 구조화 출력(responses.parse)으로 등급·변화 종류를 받는다.

처음엔 OpenAI Agents SDK(Agent + Runner + output_type)였다(ADR 0008). 도구 없는 단발 분류라 하네스 기능을 쓰지 않았다 —
ADR 0030 · HYO-220에서 걷어내고 같은 요청을 SDK로 직접 보낸다. 요청 body는 예전과 같다(로컬 서버로 받아 대조 —
pipeline/out/hyo206/openai-body-*.json): model · instructions · input(user 메시지 하나) · reasoning effort · text.format json_schema strict.
다른 것은 스키마 이름(final_output → 모델 이름)과 빈 칸(include · tools []) 둘뿐이다.

- 모델: 설정 (`gpt-6-luna` 기본, `gpt-6.1-sol`). 운영 관문은 W48부터 Claude(구독 CLI)다 — 이 경로는 GPT와 견주는 평가용
- 키: 레포 루트 `.env`의 `OPENAI_API_KEY` (이 용도 전용 OpenAI 프로젝트). os.environ에 넣지 않는다 — 명시한 클라이언트로만 쓴다
- **OpenAI 트레이싱은 없어졌다** — Agents SDK가 자동으로 올리던 것이다. 호출 기록(모델 · 토큰 · 비용 · 시도)은 CallLog에 그대로 남는다
- 데이터 경계: NetGuard(`product/spike/common/guard.py`)로 `api.openai.com`만 허용. 밖으로 나가려는 시도는 막고 기록하고, 재시도하지 않고 즉시 중단한다
- 재시도: `product/radar/retry.py`와 같은 정책(연결 오류·타임아웃·429·5xx만, 2·4·8·16초). SDK 자체 재시도는 끈다(max_retries=0)
- 단가표에 없는 모델은 cost_usd=None (모르는 값을 0으로 적지 않는다)
"""
import asyncio
import time
import uuid
from typing import Literal

from pydantic import BaseModel

from . import policy
from .backends import CHANGE_SET, TIER_SET, Backend, BoundaryError, LabelAnswer, check_labels
from .keys import env_key, load_guard, load_retry
from .prompts import CHANGE_INSTRUCTIONS, TIER_INSTRUCTIONS, change_prompt, tier_prompt

ALLOWED_HOSTS = {"api.openai.com"}
PRICES = {"gpt-6-luna": (0.10, 0.50),   # $/MTok 입력·출력 Standard — ADR 0007 (2026-09-30)
          "gpt-6.1-sol": (2.00, 10.00)}  # developers.openai.com/api/docs/pricing 확인 (2026-10-06)
FLEX_FACTOR = 0.5   # Flex(service_tier="flex") = Batch 단가 = Standard의 절반 (같은 페이지, 두 모델 모두 확인). 느리고 자원이 없으면 429 — retry.py가 재시도
SERVICE_TIERS = (None, "flex")
DEFAULT_MODEL = "gpt-6-luna"
DEFAULT_EFFORT = "medium"


def openai_key():
    """`.env`에서 OPENAI_API_KEY 한 줄만 읽는다."""
    v = env_key("OPENAI_API_KEY")
    if v:
        return v
    raise SystemExit(".env에 OPENAI_API_KEY가 없다 (RADAR_ENV_FILE로 경로를 줄 수 있다)")


TierLit = Literal["L0", "L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"]
ChangeLit = Literal["breaking", "deprecation", "eol", "security", "yanked", "major", "minor_patch", "model_retirement", "pricing"]


class TierOut(BaseModel):
    tiers: list[TierLit]
    unsure: bool
    reason: str


class ChangeOut(BaseModel):
    changes: list[ChangeLit]
    reason: str


def cost_usd(model, i, o, service_tier=None):
    p = PRICES.get(model)
    f = FLEX_FACTOR if service_tier == "flex" else 1.0
    return None if p is None else round((i / 1e6 * p[0] + o / 1e6 * p[1]) * f, 6)


def _canon(labels, order):
    s = set(labels)
    return [x for x in order if x in s]


def _real_client(key):
    from openai import AsyncOpenAI
    return AsyncOpenAI(api_key=key, max_retries=0)        # SDK 재시도는 끄고 retry.py가 한 곳에서 맡는다. 클라이언트는 호출마다 새로


class OpenAIBackend(Backend):
    def __init__(self, model=DEFAULT_MODEL, *, key=None, log=None, effort=DEFAULT_EFFORT, guard=True, client_factory=None, service_tier=None):
        """key를 주지 않으면 .env에서 읽는다. client_factory(key) → AsyncOpenAI와 같은 모양(responses.parse · close) — 테스트가 가짜를 꽂는 자리."""
        super().__init__(log)
        self.name, self.model, self.effort = model, model, effort
        self.retry = load_retry()
        self.key = key if key is not None else openai_key()
        if service_tier not in SERVICE_TIERS:
            raise ValueError(f"service_tier {service_tier!r} — {SERVICE_TIERS} 중 하나")
        self.service_tier = service_tier
        self.client_factory = client_factory or _real_client
        self.guard = None
        if guard:
            self.guard = load_guard().NetGuard(allow=ALLOWED_HOSTS)
            self.guard.__enter__()

    def request(self, instructions, prompt, out_type):
        """responses.parse에 넘기는 인자 — 테스트가 모양을 본다."""
        return {"model": self.model, "instructions": instructions, "input": [{"role": "user", "content": prompt}], "text_format": out_type,
                **({"reasoning": {"effort": self.effort}} if self.effort else {}),
                **({"service_tier": self.service_tier} if self.service_tier else {})}

    def _run(self, flow, agent_name, instructions, prompt, out_type):
        guard, retry_mod = self.guard, self.retry
        info_id = uuid.uuid4().hex
        t0 = time.perf_counter()
        kw = self.request(instructions, prompt, out_type)

        async def one():
            before = len(guard.attempts) if guard else 0
            client = self.client_factory(self.key)
            try:
                return await client.responses.parse(**kw)
            except Exception as e:
                if guard and len(guard.attempts) > before:
                    raise BoundaryError(f"허용 밖 호스트 시도: {sorted(set(guard.attempts[before:]))}") from e
                raise
            finally:
                await client.close()

        rec = {"flow": flow, "agent": agent_name, "model": self.model, "estimated": False,
               **({"service_tier": self.service_tier} if self.service_tier else {})}
        try:
            resp = asyncio.run(retry_mod.call_async(one, info_id))
        except Exception as e:  # noqa: BLE001
            info = retry_mod.pop_info(info_id)
            self.log.add({**rec, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "latency_s": round(time.perf_counter() - t0, 3),
                          "error": f"{type(e).__name__}: {str(e)[:200]}", "attempts": info["attempts"],
                          **({"retried": info["retried"]} if info["retried"] else {}),
                          **({"blocked_hosts": sorted(set(guard.attempts))} if isinstance(e, BoundaryError) else {})})
            raise
        info = retry_mod.pop_info(info_id)
        i, o = resp.usage.input_tokens, resp.usage.output_tokens
        served = getattr(resp, "service_tier", None)   # 서버가 실제로 쓴 등급 (있으면 기록)
        self.log.add({**rec, "input_tokens": i, "output_tokens": o, "cost_usd": cost_usd(self.model, i, o, self.service_tier),
                      **({"served_tier": [served]} if served and self.service_tier else {}),
                      "latency_s": round(time.perf_counter() - t0, 3), "attempts": info["attempts"],
                      **({"retried": info["retried"]} if info["retried"] else {})})
        out = resp.output_parsed
        if out is None:
            raise ValueError("구조화 출력 없음 (output_parsed)")
        return out

    def classify_tiers(self, q):
        out = self._run("tiers", "tier-classifier", TIER_INSTRUCTIONS, tier_prompt(q), TierOut)
        labels = check_labels(_canon(out.tiers, policy.TIERS), TIER_SET, "tiers")
        return LabelAnswer(labels=[] if out.unsure else labels, unsure=bool(out.unsure) or not labels, reason=out.reason)

    def classify_changes(self, q):
        out = self._run("changes", "change-classifier", CHANGE_INSTRUCTIONS, change_prompt(q), ChangeOut)
        return LabelAnswer(labels=check_labels(_canon(out.changes, policy.MODEL_CHANGES), CHANGE_SET, "changes"), reason=out.reason)

    def trace_info(self):
        """OpenAI 트레이싱은 없다(Agents SDK와 함께 빠졌다) — 차단된 호스트만 남긴다."""
        return {"traces": 0, "spans": 0, "blocked_hosts": sorted(set(self.guard.attempts)) if self.guard else []}

    def close(self):
        if self.guard:
            self.guard.__exit__(None, None, None)
            self.guard = None
