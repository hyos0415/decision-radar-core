"""데이터 등록부 조회 (HYO-180) — 빌드 산출 registry.json만 읽는다. 표준 라이브러리만 (수집기 · 파이프라인 · 스킬이 import한다).

  from registry import lookup
  reg = lookup.load()
  reg.unit_of_source("python")          # → "security"
  reg.channel_kind("r/LocalLLaMA")      # → "channel:subreddit"

조회는 언제나 종류(클래스) 안에서 한다 — `python`은 항목 출처이면서 (뒤에는) 기술이고, `vendor`는 수집 단위이면서 통로다.
정규화 규칙은 하나 (설계 docs/design/data-registry.md §5-5): 앞뒤 공백을 떼고 소문자, [-_.\\s]+ → "-".
shapes.ttl의 SPARQL 겹침 검사와 같은 규칙이다 (registry/tests/test_registry.py가 대조한다).
"""
import json
import re
from pathlib import Path

PATH = Path(__file__).resolve().parent / "registry.json"
_SEP = re.compile(r"[-_.\s]+")


def normalize(name):
    """이름 → 조회 키. PEP 503에 공백을 더한 것 (llama.cpp → llama-cpp, Amazon Bedrock → amazon-bedrock)."""
    return _SEP.sub("-", (name or "").strip()).lower()


class Registry:
    def __init__(self, doc):
        self.doc = doc
        self.names = doc.get("names") or {}
        self.objects = doc.get("objects") or {}
        # 긴 접두부터 — 접두가 다른 접두를 품어도 더 구체적인 쪽이 이긴다
        self._prefixed = sorted(((o["prefix"], oid) for oid, o in self.objects.items() if o.get("prefix")),
                                key=lambda x: (-len(x[0]), x[0]))

    def resolve(self, kind, name):
        """(종류, 이름) → ID | None. kind = "CollectionUnit" · "ItemSource" · "Channel"."""
        return (self.names.get(kind) or {}).get(normalize(name))

    def label(self, oid):
        return (self.objects.get(oid) or {}).get("label")

    def of_type(self, kind):
        """그 종류의 ID들 (정렬)."""
        return sorted(oid for oid, o in self.objects.items() if kind in (o.get("types") or []))

    # ── 출처 ──

    def collection_units(self):
        """수집 단위 이름들 (정렬) — health.py의 출처 단위."""
        return sorted(self.label(oid) for oid in self.of_type("CollectionUnit"))

    def unit_of_source(self, item_source):
        """항목 source 값 → 수집 단위 이름 | None(등록부에 없음)."""
        oid = self.resolve("ItemSource", item_source)
        if oid is None:
            return None
        return self.label(self.objects[oid].get("collectedBy"))

    def channel_kind(self, channel):
        """항목 channels 값 하나 → 통로 ID | None(등록부에 없음). 접두가 있는 통로(r/ · vendor: …)는 접두로, 나머지는 이름 그대로."""
        for prefix, oid in self._prefixed:
            if channel.startswith(prefix) and len(channel) > len(prefix):
                return oid
        oid = self.resolve("Channel", channel)
        if oid is not None and not self.objects[oid].get("prefix"):
            return oid
        return None


_CACHE = {}


def load(path=None):
    """registry.json → Registry (경로마다 한 번 읽는다)."""
    p = Path(path) if path else PATH
    key = str(p.resolve())
    if key not in _CACHE:
        _CACHE[key] = Registry(json.loads(p.read_text(encoding="utf-8")))
    return _CACHE[key]
