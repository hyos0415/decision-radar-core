"""Anthropic API 백엔드 — 구조화 출력(output_config.format json_schema)으로 등급·변화 종류를 받는다 (HYO-122 PR 3: 주간 재분류 모델 Sonnet 5.5).

- 모델: `claude-sonnet-5-5`(기본, effort medium — CLAUDE.md 「외부 모델·서비스」) · `claude-haiku-5-5`(HYO-67 비교군 자리)
- 프롬프트: GPT 백엔드와 같은 것(prompts.py) — 모델만 바꿔 같은 정답지로 견줄 수 있게
- 키: `.env`의 `ANTHROPIC_API_KEY` 한 줄만 읽는다. os.environ에 넣지 않는다 (agents_backend.openai_key와 같은 방식)
- 데이터 경계: NetGuard로 `api.anthropic.com`만 허용. 밖으로 나가려는 시도는 BoundaryError — 재시도하지 않고 멈춘다
- 재시도: product/radar/retry.py (429·5xx·연결 오류만, 2·4·8·16초). SDK 재시도는 끈다(max_retries=0) — 시도 횟수를 한 곳에서 센다
- 거절(stop_reason refusal)은 오류로 기록하고 올린다 — 서버 쪽 fallback(다른 모델이 대신 답함)은 켜지 않는다: 등급 파일의 `model`이 실제로 답한 모델과 달라지면 비교·재현이 깨진다
- 호출마다 모델(응답의 model)·토큰·비용·시도 횟수를 CallLog에 남긴다. 단가표에 없는 모델은 cost_usd=None
"""
import json
import time
import uuid

from . import policy
from .agents_backend import find_env_file, load_guard, load_retry
from .backends import CHANGE_SET, TIER_SET, Backend, BoundaryError, LabelAnswer, check_labels
from .prompts import CHANGE_INSTRUCTIONS, TIER_INSTRUCTIONS, change_prompt, tier_prompt

ALLOWED_HOSTS = {"api.anthropic.com"}
PRICES = {"claude-sonnet-5-5": (2.00, 10.00),   # $/MTok 입력·출력 — claude-api 스킬 모델 표 (cached 2026-10-06)
          "claude-haiku-5-5": (0.10, 0.50)}     # 프롬프트 100K 토큰 이하 단가 (분류 프롬프트는 그보다 훨씬 짧다)
DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_EFFORT = "medium"
MAX_TOKENS = 16000

TIER_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["tiers", "unsure", "reason"],
               "properties": {"tiers": {"type": "array", "items": {"type": "string", "enum": list(policy.TIERS)}},
                              "unsure": {"type": "boolean"}, "reason": {"type": "string"}}}
CHANGE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["changes", "reason"],
                 "properties": {"changes": {"type": "array", "items": {"type": "string", "enum": list(policy.MODEL_CHANGES)}},
                                "reason": {"type": "string"}}}


def anthropic_key():
    f = find_env_file()
    if f:
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v
    raise SystemExit(".env에 ANTHROPIC_API_KEY가 없다 (RADAR_ENV_FILE로 경로를 줄 수 있다)")


def cost_usd(model, i, o):
    m = (model or "").lower()
    p = next((v for k, v in PRICES.items() if m == k or m.startswith(k + "-")), None)
    return None if p is None else round(i / 1e6 * p[0] + o / 1e6 * p[1], 6)


def _canon(labels, order):
    s = set(labels)
    return [x for x in order if x in s]


class RefusalError(RuntimeError):
    """모델이 거절했다 (stop_reason refusal). 안전 실패로 다룬다."""


class ClaudeBackend(Backend):
    def __init__(self, model=DEFAULT_MODEL, *, key=None, log=None, effort=None, guard=True, client=None):
        """client: 테스트가 가짜 클라이언트(messages.create)를 꽂는 자리. 없으면 anthropic.Anthropic."""
        super().__init__(log)
        self.name, self.model, self.effort = model, model, effort or DEFAULT_EFFORT
        self.retry = load_retry()
        if client is None:
            import anthropic
            client = anthropic.Anthropic(api_key=key if key is not None else anthropic_key(), max_retries=0)
        self.client = client
        self.guard = None
        if guard:
            self.guard = load_guard().NetGuard(allow=ALLOWED_HOSTS)
            self.guard.__enter__()

    def _run(self, flow, instructions, prompt, schema):
        guard, retry_mod, info_id = self.guard, self.retry, uuid.uuid4().hex
        t0 = time.perf_counter()

        def one():
            before = len(guard.attempts) if guard else 0
            try:
                return self.client.messages.create(
                    model=self.model, max_tokens=MAX_TOKENS, system=instructions,
                    messages=[{"role": "user", "content": prompt}],
                    output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}})
            except Exception as e:
                if guard and len(guard.attempts) > before:
                    raise BoundaryError(f"허용 밖 호스트 시도: {sorted(set(guard.attempts[before:]))}") from e
                raise

        rec = {"flow": flow, "model": self.model, "effort": self.effort, "estimated": False}
        resp = None
        try:
            resp = retry_mod.call_sync(one, info_id)
            if resp.stop_reason == "refusal":
                sd = getattr(resp, "stop_details", None)
                raise RefusalError(f"refusal ({getattr(sd, 'category', None)})")
            if resp.stop_reason == "max_tokens":
                raise RuntimeError("max_tokens — 출력이 잘렸다")
            out = json.loads(next(b.text for b in resp.content if b.type == "text"))
        except Exception as e:  # noqa: BLE001
            info = retry_mod.pop_info(info_id)
            usage = getattr(resp, "usage", None)
            i, o = (usage.input_tokens, usage.output_tokens) if usage else (0, 0)
            self.log.add({**rec, "input_tokens": i, "output_tokens": o, "cost_usd": cost_usd(self.model, i, o) if usage else 0.0,
                          "latency_s": round(time.perf_counter() - t0, 3), "error": f"{type(e).__name__}: {str(e)[:200]}",
                          "attempts": info["attempts"], **({"retried": info["retried"]} if info["retried"] else {}),
                          **({"blocked_hosts": sorted(set(guard.attempts))} if isinstance(e, BoundaryError) else {})})
            raise
        info = retry_mod.pop_info(info_id)
        i, o = resp.usage.input_tokens, resp.usage.output_tokens
        self.log.add({**rec, "served_model": resp.model, "input_tokens": i, "output_tokens": o, "cost_usd": cost_usd(resp.model or self.model, i, o),
                      "latency_s": round(time.perf_counter() - t0, 3), "attempts": info["attempts"],
                      **({"retried": info["retried"]} if info["retried"] else {})})
        return out

    def classify_tiers(self, q):
        out = self._run("tiers", TIER_INSTRUCTIONS, tier_prompt(q), TIER_SCHEMA)
        labels = check_labels(_canon(out["tiers"], policy.TIERS), TIER_SET, "tiers")
        return LabelAnswer(labels=[] if out["unsure"] else labels, unsure=bool(out["unsure"]) or not labels, reason=out["reason"])

    def classify_changes(self, q):
        out = self._run("changes", CHANGE_INSTRUCTIONS, change_prompt(q), CHANGE_SCHEMA)
        return LabelAnswer(labels=check_labels(_canon(out["changes"], policy.MODEL_CHANGES), CHANGE_SET, "changes"), reason=out["reason"])

    def close(self):
        if self.guard:
            self.guard.__exit__(None, None, None)
            self.guard = None
