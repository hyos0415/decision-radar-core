"""분류 백엔드 인터페이스 — 등급 분류·변화 종류 분류가 「백엔드」 하나를 거쳐 결과를 받는다.

갈아 끼울 수 있게 만든 이유 (2026-10-06 사용자 추가 요구): 같은 정답지로 GPT(Agents SDK)와 TypeSafe Jev를 비교한다.
Jev는 문장을 생성하지 않는다 — **정해진 선택지 하나 + 확률**을 돌려주고, `state` 하나에 여러 질문을 병렬로 묻는다 (`POST /v1/systemone`,
speculative fan-out). 판정 근거 텍스트(reason)가 없다. 그래서 결과 형식은 두 모양을 모두 담는다:

  LabelAnswer.labels     라벨 집합 (GPT: 모델이 직접 낸다 / Jev: 질문별 예·아니오와 임계값에서 코드가 만든다)
  LabelAnswer.per_label  라벨별 {"yes": bool, "prob": float|None} — 선택. Jev는 종류마다 「이 소식은 X인가?」 예/아니오 질문 + 확률
  LabelAnswer.reason     근거 텍스트 — 선택 (Jev는 None)
  LabelAnswer.calls      호출 기록 (모델·토큰·비용·시도 횟수 — llm_call 형식)

백엔드를 고르는 곳은 `make_backend(model)` 하나다. 지금: `stub` · `gpt-*`(Agents SDK) · `jev-1.13.0`(urllib 직접 — jev_backend.py).
"""
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from . import policy


class BoundaryError(RuntimeError):
    """허용 호스트 밖으로 나가려는 시도 — 재시도하지 않고 실행을 멈춘다 (게이트·평가는 이 예외를 삼키지 않는다)."""


@dataclass
class TechQuery:
    """등급을 물을 기술 하나. signals는 스크립트 검증용이라 백엔드에는 넘기지 않는다 (모델이 신호를 보고 맞추면 검증이 무의미하다)."""
    name: str
    usage: str = ""        # 이 레포에서 쓰는 방식 (v1.2)
    source: str = ""       # 어디서 나온 기술인가 (pyproject 직접 의존성 · 코드 속 모델 id …)
    signals: dict = field(default_factory=dict)


@dataclass
class NewsQuery:
    """변화 종류를 물을 소식 하나."""
    id: str
    technology: str
    title: str
    text: str = ""
    source: str = ""


@dataclass
class LabelAnswer:
    labels: list
    per_label: dict | None = None
    unsure: bool = False
    reason: str | None = None
    calls: list = field(default_factory=list)


def labels_from_scores(per_label, threshold=None, order=None):
    """라벨별 {"yes","prob"} → 라벨 집합. threshold가 있고 prob가 있으면 prob >= threshold, 아니면 yes.

    Jev 백엔드가 쓸 자리다: 질문별 선택(예/아니오)과 확률을 받아 임계값으로 라벨을 만든다. Jev의 임계값은 p ≥ 0.5로 **고정**이다 (importance-policy §8 등록값 — 정답지로 맞추지 않는다).
    """
    out = []
    for lab in (order or list(per_label)):
        s = per_label.get(lab)
        if not s:
            continue
        hit = s["prob"] >= threshold if (threshold is not None and s.get("prob") is not None) else bool(s.get("yes"))
        if hit:
            out.append(lab)
    return out


class CallLog:
    """호출 기록 — llm_call 줄(product/radar/llm_log.py와 같은 칸: type·flow·model·input_tokens·output_tokens·estimated·cost_usd·attempts·retried·error)."""

    def __init__(self, path=None):
        self.calls, self.path = [], (Path(path) if path else None)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def add(self, rec):
        rec = {"type": "llm_call", **rec, "seq": len(self.calls) + 1}
        self.calls.append(rec)
        if self.path:
            with open(self.path, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def totals(self):
        c = self.calls
        priced = [x["cost_usd"] for x in c if x.get("cost_usd") is not None]
        return {"model_calls": len(c), "errors": sum(1 for x in c if x.get("error")),
                "input_tokens": sum(x.get("input_tokens", 0) for x in c), "output_tokens": sum(x.get("output_tokens", 0) for x in c),
                "cost_usd": round(sum(priced), 5), "unpriced_calls": len(c) - len(priced)}


class Backend(ABC):
    name = "?"

    def __init__(self, log=None):
        self.log = log if log is not None else CallLog()

    @abstractmethod
    def classify_tiers(self, q: TechQuery) -> LabelAnswer:
        """기술 하나 → 등급 라벨 (L0~L8, 두 등급 가능). 정말 모르면 unsure=True와 labels=[]."""

    @abstractmethod
    def classify_changes(self, q: NewsQuery) -> LabelAnswer:
        """소식 하나 → 변화 종류 (policy.CHANGES 중 해당하는 것 전부)."""

    def close(self):
        pass


# ---------------------------------------------------------------------------- 스텁

_STUB_TIERS = {"pytest": "L7", "ruff": "L7", "fastapi": "L4", "pydantic": "L4", "uvicorn": "L4", "anthropic": "L5", "openai": "L5",
               "boto3": "L5", "requests": "L8", "httpx": "L8", "python-dotenv": "L8"}
_STUB_CHANGE_WORDS = [("security", ("security", "cve", "vulnerab")), ("model_retirement", ("retire", "retirement", "sunset", "shut down")),
                      ("breaking", ("breaking",)), ("deprecation", ("deprecat",)), ("yanked", ("yanked",)),
                      ("pricing", ("pricing", "price", "rate limit")), ("eol", ("end of life", "eol")), ("major", ("major",))]


class StubBackend(Backend):
    """모델 없이 도는 백엔드 — 배관 확인용(스텁은 정확도를 재지 않는다). 답을 주입할 수 있다.

    tiers: {기술 이름: [등급…]} 또는 호출 가능 (TechQuery → LabelAnswer | list)
    changes: {소식 id: [변화…]} 또는 호출 가능 (NewsQuery → LabelAnswer | list). 없으면 제목·본문 키워드로 거칠게 짚는다.
    """
    name = "stub"

    def __init__(self, tiers=None, changes=None, log=None):
        super().__init__(log)
        self._tiers, self._changes = tiers, changes

    @staticmethod
    def _wrap(v, flow):
        return v if isinstance(v, LabelAnswer) else LabelAnswer(labels=list(v), reason=f"stub:{flow}")

    def _record(self, flow):
        self.log.add({"flow": flow, "model": "stub", "input_tokens": 0, "output_tokens": 0, "estimated": True, "cost_usd": 0.0, "attempts": 1})

    def classify_tiers(self, q):
        t = self._tiers
        v = t(q) if callable(t) else (t or {}).get(q.name) if t is not None and q.name in t else None
        if v is None:
            v = [_STUB_TIERS.get(q.name.lower(), "L6")]
        self._record("tiers")
        return self._wrap(v, "tiers")

    def classify_changes(self, q):
        c = self._changes
        if callable(c):
            v = c(q)
        elif c is not None and q.id in c:
            v = c[q.id]
        else:
            hay = f"{q.title} {q.text}".lower()
            v = [lab for lab, words in _STUB_CHANGE_WORDS if any(w in hay for w in words)]
        self._record("changes")
        return self._wrap(v, "changes")


# ---------------------------------------------------------------------------- 고르는 곳

def make_backend(model, *, allow_real=False, **kw):
    """모델 이름 → 백엔드. 실제 API를 부르는 백엔드는 allow_real=True 없이는 만들지 않는다 (키 파일도 읽기 전에 거부)."""
    if model == "stub":
        return StubBackend(**{k: v for k, v in kw.items() if k in ("tiers", "changes", "log")})
    if model.startswith("gpt-"):
        if not allow_real:
            raise PermissionError(f"{model}: 실제 API 실행은 --real-api 없이는 거부한다")
        from .agents_backend import AgentsBackend
        return AgentsBackend(model, **kw)
    if model.startswith("jev-"):
        if not allow_real:
            raise PermissionError(f"{model}: 실제 API 실행은 --real-api 없이는 거부한다")    # 키 파일도 읽기 전에 거부
        from .jev_backend import JevBackend
        return JevBackend(model, **{k: v for k, v in kw.items() if k in ("key", "log", "base_url", "opener", "guard", "timeout")})
    raise ValueError(f"모르는 모델 {model!r} (stub · gpt-6-luna · gpt-6.1-sol · jev-1.13.0)")


def check_labels(labels, universe, what):
    bad = [x for x in labels if x not in universe]
    if bad:
        raise ValueError(f"{what}: 모르는 라벨 {bad}")
    return labels


TIER_SET, CHANGE_SET = set(policy.TIERS), set(policy.CHANGES)
