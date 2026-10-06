"""기술 등급 — 모델 분류 + 스크립트 검증 + 저장 + 사람 수정 (importance-policy §4 「등급을 매기는 주체」).

흐름: 백엔드가 등급을 매긴다 → `verify()`가 스크립트 신호와 대조 → 어긋나면 **스크립트를 따르고** 어긋남을 기록 → 저장 파일에 남겨 재사용.
저장 파일(`tiers.json`)은 스택이 바뀔 때만(= 기술의 지문이 달라졌을 때) 다시 분류한다. `overrides`에 사람이 적은 값이 있으면 그것이 최우선이다 —
자동 분류가 다시 돌아도 `overrides`는 건드리지 않는다.

스크립트 검증 규칙 (지금 대상 repo에서 읽을 수 있는 신호만):
  S1 Dockerfile `FROM` · compose `image`로 나온 기술      → L1 (베이스 이미지·서빙 엔진·우리가 띄우는 서버, V3-1). 경로 prod
  S2 dev 그룹(requirements-dev · optional-dependencies/dependency-groups의 dev·test·lint… · poetry group)의 패키지 → L7, 경로 dev
  S3 manifest(main 그룹)에 직접 적힌 패키지 → 언어 런타임(L0)·실행 기반(L1)일 수 없다 — 모델이 낸 L0·L1만 떨어낸다. 호스팅 등급(L2·L3)은 떨어내지 않는다
     (langfuse = L3 서비스 + L5 SDK 같은 두 등급이 유지돼야 한다 — importance-policy §4 예시 · §8 판정 세부). 남는 게 없으면 분류 불가
분류 불가(unresolved)는 이번 주 후보에서 빼고 로그에 남긴다 (v1.1 U4).
NOT_IMPLEMENTED: 구현하지 못한 신호 목록 — 보고서와 같은 내용.
"""
import hashlib
import json
import time
from pathlib import Path

from . import policy
from .backends import TechQuery
from .signals import norm

NOT_IMPLEMENTED = [
    "test 경로 신호: 테스트 코드에서만 import되는 패키지 → L7 (import 위치 스캔 + 패키지명↔모듈명 매핑이 필요 — PyYAML→yaml 같은 불일치를 못 푼다)",
    "코드 속 모델 id·서비스 URL 신호 (L2·L3 확정): 코드를 읽어야 안다 — 제품 detect의 몫",
    "package.json · CI `uses` · Gemfile · go.mod · Cargo: 대상 repo가 아직 Python뿐이다",
    "민감 경로(PII 경로) · 로컬 서빙 모델 여부: 자동으로 알 수 없어 overrides(사람 입력)로만 켠다",
    "운영 기본값 vs 비교용 호스팅 모델 구분 (importance-policy §7)",
]
MANIFEST_DROP_TIERS = {"L0", "L1"}      # S3이 떨어내는 등급 (L2·L3은 두 등급 기술의 한 축이라 남긴다)


def verify(model_tiers, model_unsure, signals):
    """모델 등급 + 스크립트 신호 → {"tiers","unresolved","path","pin","mismatch"}. 신호가 없으면 모델을 그대로 (unsure면 분류 불가)."""
    sig = signals or {}
    pin = sig.get("pin")
    model_tiers = list(model_tiers or [])
    if sig.get("image"):
        script, path, rule = ["L1"], "prod", "S1 이미지(FROM·image) → L1"
    elif sig.get("group") == "dev":
        script, path, rule = ["L7"], "dev", "S2 dev 그룹 → L7"
    elif sig.get("manifest") and sig.get("group") == "main":
        kept = [t for t in model_tiers if t not in MANIFEST_DROP_TIERS]
        rule, path = "S3 manifest 직접 의존성 → L0·L1 제외", "prod"
        if not kept:
            mm = {"rule": rule, "model": model_tiers, "script": [], "note": "남는 등급이 없다 — 분류 불가"}
            return {"tiers": [], "unresolved": True, "path": path, "pin": pin, "mismatch": mm}
        script = kept
    else:
        if model_unsure or not model_tiers:
            return {"tiers": [], "unresolved": True, "path": "prod", "pin": pin, "mismatch": None}
        return {"tiers": model_tiers, "unresolved": False, "path": "prod", "pin": pin, "mismatch": None}
    differs = set(script) != set(model_tiers)
    mm = {"rule": rule, "model": model_tiers, "script": script, **({"note": "모델이 분류 불가"} if model_unsure else {})} if differs else None
    return {"tiers": script, "unresolved": False, "path": path, "pin": pin, "mismatch": mm}


def fingerprint(q: TechQuery):
    return hashlib.sha1(json.dumps({"n": q.name, "u": q.usage, "s": q.signals}, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]


class TierStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.data = {"version": 1, "technologies": {}, "overrides": {}}
        if self.path and self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            self.data.setdefault("overrides", {})
            self.data.setdefault("technologies", {})

    def save(self):
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")

    def names(self):
        return [r["technology"] for r in self.data["technologies"].values()]

    def get(self, name):
        return self.data["technologies"].get(norm(name))

    def effective(self, name):
        """자동 분류 위에 사람 수정(overrides)을 덮은 값. 없으면 None. tiers가 사람 값이면 분류 불가도 풀린다."""
        rec = self.get(name)
        ov = self.data["overrides"].get(norm(name))
        if rec is None and ov is None:
            return None
        eff = dict(rec or {"technology": name, "tiers": [], "unresolved": True, "path": "prod", "pin": None, "sensitive": False, "local": False})
        eff.setdefault("sensitive", False)
        eff.setdefault("local", False)
        if ov:
            for k in ("tiers", "path", "pin", "sensitive", "local"):
                if k in ov:
                    eff[k] = ov[k]
            if ov.get("tiers"):
                eff["unresolved"] = False
            eff["human"] = True
        return eff

    def human_tiers(self, name):
        return bool(self.data["overrides"].get(norm(name), {}).get("tiers"))

    def needs_classify(self, q):
        if self.human_tiers(q.name):
            return False                      # 사람이 등급을 정했다 — 모델을 부르지 않는다
        rec = self.get(q.name)
        return rec is None or rec.get("fingerprint") != fingerprint(q)

    def classify(self, q, backend):
        ans = backend.classify_tiers(q)
        v = verify(ans.labels, ans.unsure, q.signals)
        rec = {"technology": q.name, "tiers": v["tiers"], "unresolved": v["unresolved"], "path": v["path"], "pin": v["pin"],
               "sensitive": False, "local": False, "model": backend.name, "model_tiers": list(ans.labels), "model_unsure": ans.unsure,
               "reason": ans.reason, "mismatch": v["mismatch"], "signals": q.signals, "usage": q.usage, "fingerprint": fingerprint(q),
               "classified_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        if ans.per_label:
            rec["per_label"] = ans.per_label
        self.data["technologies"][norm(q.name)] = rec
        return rec

    def ensure(self, queries, backend, refresh=False):
        """필요한 기술만 분류해 저장 → 새로 분류한 이름 목록. refresh=True면 전부 다시 (사람 등급은 여전히 건드리지 않는다)."""
        done = []
        for q in queries:
            if not self.human_tiers(q.name) and (refresh or self.needs_classify(q)):
                self.classify(q, backend)
                done.append(q.name)
        return done
