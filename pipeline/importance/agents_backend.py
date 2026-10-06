"""Agents SDK(OpenAI 본래 경로) 백엔드 — 구조화 출력(output_type)으로 등급·변화 종류를 받는다 (ADR 0008 최적화 모드).

- 모델: 설정 (`gpt-6-luna` 기본, 다음 실험 `gpt-6.1-sol`). 어댑터 없이 OpenAIResponsesModel
- 키: 레포 루트 `.env`의 `OPENAI_API_KEY` (이 용도 전용 OpenAI 프로젝트). os.environ에 넣지 않는다 — 명시한 클라이언트로만 쓴다
- **트레이싱 켬** (ADR 0008 사용자 결정). 트레이스는 프롬프트·출력을 OpenAI 플랫폼에 저장한다. 트레이스 수는 호출 기록에 센다
- 데이터 경계: NetGuard(`product/spike/common/guard.py`)로 `api.openai.com`만 허용 (트레이스 업로드 포함). 밖으로 나가려는 시도는 막고 기록하고, 재시도하지 않고 즉시 중단한다
- 재시도: `product/radar/retry.py`와 같은 정책(연결 오류·타임아웃·429·5xx만, 2·4·8·16초). SDK 자체 재시도는 끈다(max_retries=0)
- 호출마다 모델·토큰·비용·시도 횟수를 CallLog에 남긴다. 단가표에 없는 모델은 cost_usd=None (모르는 값을 0으로 적지 않는다)
"""
import asyncio
import importlib.util
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from . import policy
from .backends import CHANGE_SET, TIER_SET, Backend, BoundaryError, LabelAnswer, check_labels  # noqa: F401
from .prompts import CHANGE_INSTRUCTIONS, TIER_INSTRUCTIONS, change_prompt, tier_prompt

REPO = Path(__file__).resolve().parents[2]
ALLOWED_HOSTS = {"api.openai.com"}
PRICES = {"gpt-6-luna": (0.10, 0.50)}   # $/MTok 입력·출력 — ADR 0007 (2026-09-30). gpt-6.1-sol 단가는 확인된 값이 없다 → None으로 기록
DEFAULT_MODEL = "gpt-6-luna"
DEFAULT_EFFORT = "medium"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_retry():
    return _load(REPO / "product" / "radar" / "retry.py", "importance_retry")     # 다른 에이전트 영역 — import만 (수정 금지)


def load_guard():
    return _load(REPO / "product" / "spike" / "common" / "guard.py", "importance_guard")


def find_env_file():
    cands = [os.environ.get("RADAR_ENV_FILE"), REPO / ".env"]
    try:    # worktree면 메인 체크아웃의 .env
        common = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=REPO, capture_output=True, text=True, timeout=10).stdout.strip()
        if common:
            cands.append((REPO / common).resolve().parent / ".env")
    except (OSError, subprocess.SubprocessError):
        pass
    return next((Path(c) for c in cands if c and Path(c).exists()), None)


def openai_key():
    """`.env`에서 OPENAI_API_KEY 한 줄만 읽는다."""
    f = find_env_file()
    if f:
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("OPENAI_API_KEY="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
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


def cost_usd(model, i, o):
    p = PRICES.get(model)
    return None if p is None else round(i / 1e6 * p[0] + o / 1e6 * p[1], 6)


def _canon(labels, order):
    s = set(labels)
    return [x for x in order if x in s]


class TraceCounter:
    """트레이스·스팬 수 — 트레이싱이 실제로 켜져 있었는지 기록에 남긴다 (0이 빈 검사가 아니게)."""

    def __init__(self):
        self.traces = self.spans = 0

    def processor(self):
        from agents.tracing import TracingProcessor
        c = self

        class _P(TracingProcessor):
            def on_trace_start(self, trace):
                c.traces += 1

            def on_trace_end(self, trace):
                pass

            def on_span_start(self, span):
                c.spans += 1

            def on_span_end(self, span):
                pass

            def shutdown(self):
                pass

            def force_flush(self):
                pass

        return _P()


class AgentsBackend(Backend):
    def __init__(self, model=DEFAULT_MODEL, *, key=None, log=None, effort=DEFAULT_EFFORT, export_traces=True, guard=True,
                 model_factory=None, max_turns=2):
        """key를 주지 않으면 .env에서 읽는다. model_factory(client, model_name) → Agents SDK Model — 테스트가 가짜 모델을 꽂는 자리.
        export_traces=False면 OpenAI로 올리는 기본 exporter를 빼고 세기만 한다 (오프라인 테스트용 — 실제 실행은 True)."""
        super().__init__(log)
        from agents import ModelSettings, add_trace_processor, set_trace_processors, set_tracing_export_api_key
        from openai.types.shared import Reasoning
        self.name, self.model, self.max_turns = model, model, max_turns
        self.retry = load_retry()
        self.key = key if key is not None else openai_key()
        self.settings = ModelSettings(reasoning=Reasoning(effort=effort)) if effort else ModelSettings()
        self.model_factory = model_factory or self._real_model
        self.counter = TraceCounter()
        if export_traces:
            set_tracing_export_api_key(self.key)
            add_trace_processor(self.counter.processor())
        else:
            set_trace_processors([self.counter.processor()])
        self.guard = None
        if guard:
            self.guard = load_guard().NetGuard(allow=ALLOWED_HOSTS)
            self.guard.__enter__()

    # -- 실제 모델
    @staticmethod
    def _real_model(client, name):
        from agents import OpenAIResponsesModel
        return OpenAIResponsesModel(model=name, openai_client=client)

    def _run(self, flow, agent_name, instructions, prompt, out_type):
        from agents import Agent, RunConfig, Runner
        from openai import AsyncOpenAI
        guard, retry_mod = self.guard, self.retry
        info_id = uuid.uuid4().hex
        t0 = time.perf_counter()

        async def one():
            before = len(guard.attempts) if guard else 0
            client = AsyncOpenAI(api_key=self.key, max_retries=0)        # SDK 재시도는 끄고 retry.py가 한 곳에서 맡는다. 클라이언트는 호출마다 새로
            try:
                agent = Agent(name=agent_name, instructions=instructions, model=self.model_factory(client, self.model),
                              model_settings=self.settings, output_type=out_type)
                return await Runner.run(agent, prompt, max_turns=self.max_turns,
                                        run_config=RunConfig(workflow_name="importance-classifier", trace_metadata={"flow": flow}))
            except Exception as e:
                if guard and len(guard.attempts) > before:
                    raise BoundaryError(f"허용 밖 호스트 시도: {sorted(set(guard.attempts[before:]))}") from e
                raise
            finally:
                await client.close()

        rec = {"flow": flow, "agent": agent_name, "model": self.model, "estimated": False}
        try:
            result = asyncio.run(retry_mod.call_async(one, info_id))
        except Exception as e:  # noqa: BLE001
            info = retry_mod.pop_info(info_id)
            self.log.add({**rec, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "latency_s": round(time.perf_counter() - t0, 3),
                          "error": f"{type(e).__name__}: {str(e)[:200]}", "attempts": info["attempts"],
                          **({"retried": info["retried"]} if info["retried"] else {}),
                          **({"blocked_hosts": sorted(set(guard.attempts))} if isinstance(e, BoundaryError) else {})})
            raise
        info = retry_mod.pop_info(info_id)
        i = sum(r.usage.input_tokens for r in result.raw_responses)
        o = sum(r.usage.output_tokens for r in result.raw_responses)
        self.log.add({**rec, "input_tokens": i, "output_tokens": o, "cost_usd": cost_usd(self.model, i, o),
                      "latency_s": round(time.perf_counter() - t0, 3), "attempts": info["attempts"],
                      **({"retried": info["retried"]} if info["retried"] else {})})
        return result.final_output

    def classify_tiers(self, q):
        out = self._run("tiers", "tier-classifier", TIER_INSTRUCTIONS, tier_prompt(q), TierOut)
        labels = check_labels(_canon(out.tiers, policy.TIERS), TIER_SET, "tiers")
        return LabelAnswer(labels=[] if out.unsure else labels, unsure=bool(out.unsure) or not labels, reason=out.reason)

    def classify_changes(self, q):
        out = self._run("changes", "change-classifier", CHANGE_INSTRUCTIONS, change_prompt(q), ChangeOut)
        return LabelAnswer(labels=check_labels(_canon(out.changes, policy.CHANGES), CHANGE_SET, "changes"), reason=out.reason)

    def trace_info(self):
        return {"traces": self.counter.traces, "spans": self.counter.spans,
                "blocked_hosts": sorted(set(self.guard.attempts)) if self.guard else []}

    def close(self):
        try:
            from agents import flush_traces
            flush_traces()          # 트레이스 업로드가 NetGuard 안에서 끝나게 — 가드를 풀기 전에 보낸다
        finally:
            if self.guard:
                self.guard.__exit__(None, None, None)
                self.guard = None
