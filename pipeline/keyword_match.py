"""레인 B 키워드 기준선 (doc-pipeline §2-2 전략 후보의 첫 둘).

전략 (STRATEGIES):
  kw          id · topic · examples
  kw+aliases  kw + aliases      — aliases를 쓸지가 전략 변수다. kw에는 절대 섞지 않는다

이 파일은 기준선이다. 매칭 전략(벡터·하이브리드·질의 변환)과 자르는 기준 k는 사용자가 정한다.
새 전략은 `rank(items, profile) -> [entry...]`(rank_items와 같은 모양)을 STRATEGIES 옆에 등록하면 된다.
"""
import re

# ---- 기준선 상수 — 사용자가 dev 주(1~2주차 스냅숏)에 바꾼다. 값에 근거 없음: 시작점일 뿐이다 ----
PRIORITY_WEIGHT = {"high": 1.5, "normal": 1.0, "low": 0.5}
MATCH_BASE = 1.0          # 관심사에 한 단어라도 걸리면
EXTRA_TERM = 0.25         # 같은 관심사에 서로 다른 단어가 더 걸릴 때마다
EXTRA_TERM_CAP = 3        # 더하는 횟수 상한
NOT_PENALTY = 1.0         # 관심사 `not` 단어가 걸리면 (그 관심사가 걸렸을 때만, 단어마다)
EXCLUDE_PENALTY = 1.0     # 전역 `excludes` 단어가 걸리면 (항목마다 한 번씩, 단어마다)

SEARCH_FIELDS = ("title", "text", "url")
STRATEGIES = {
    "kw": ("id", "topic", "examples"),
    "kw+aliases": ("id", "topic", "examples", "aliases"),
}

_HANGUL = re.compile(r"[ㄱ-ㆎ가-힣]")
_cache = {}


def term_regex(term):
    """한국어가 든 단어는 그대로 부분 일치, 그 밖에는 영숫자 단어 경계 + 대소문자 무시.
    (`OCR`은 `TeleOCR` 안에서 걸리지 않고, `RRF`는 `RRF-based`에서 걸린다.)"""
    t = term.strip()
    rx = _cache.get(t)
    if rx is None:
        if _HANGUL.search(t):
            rx = re.compile(re.escape(t), re.I)
        else:
            rx = re.compile(r"(?<![0-9A-Za-z])" + re.escape(t) + r"(?![0-9A-Za-z])", re.I)
        _cache[t] = rx
    return rx


def where_found(item, term):
    rx = term_regex(term)
    return [f for f in SEARCH_FIELDS if rx.search(item.get(f) or "")]


def topic_terms(topic):
    """topic 문구에서 검색 단어를 뽑는다: 통째 + 괄호 앞뒤 + 괄호 안을 `·`로 쪼갠 것 (끝의 「등」 제외).
    「그래프 기반 검색 (GraphRAG)」→ 통째 · 「그래프 기반 검색」 · 「GraphRAG」. 쪼개는 규칙은 전략 변수이므로 여기 한 곳에만 둔다."""
    out = [topic.strip()]
    m = re.match(r"^(.*?)\s*\((.*)\)\s*$", topic.strip())
    if m:
        outside, inside = m.group(1).strip(), m.group(2)
        out.append(outside)
        for part in inside.split("·"):
            part = re.sub(r"\s*등$", "", part.strip())
            out.append(part)
    seen, uniq = set(), []
    for t in out:
        if t and t.lower() not in seen:
            seen.add(t.lower())
            uniq.append(t)
    return uniq


def interest_terms(interest, fields):
    """(field, term) 목록."""
    out = []
    if "id" in fields:
        out.append(("id", interest["id"]))
    if "topic" in fields:
        out += [("topic", t) for t in topic_terms(interest["topic"])]
    for f in ("examples", "aliases"):
        if f in fields:
            out += [(f, t) for t in interest[f]]
    return out


def exclude_terms(ex):
    return [("topic", t) for t in topic_terms(ex["topic"])] + [("aliases", t) for t in ex.get("aliases", [])]


def known_examples(item, profile):
    """`examples`에 적힌 이름에 걸린 관심사 — eval에서 따로 센다 (전략과 무관)."""
    out = []
    for it in profile["interests"]:
        if not it["active"]:
            continue
        for t in it["examples"]:
            if where_found(item, t):
                out.append({"interest": it["id"], "term": t})
    return out


def score_item(item, profile, strategy):
    """항목 하나 → {"score", "matched_by", "interests"} 또는 걸린 관심사가 없으면 None."""
    fields = STRATEGIES[strategy]
    matched_by, score, hit_interests = [], 0.0, []
    for it in profile["interests"]:
        if not it["active"]:
            continue
        seen_terms, hits = set(), []
        for field, term in interest_terms(it, fields):
            w = where_found(item, term)
            if w:
                hits.append({"kind": "match", "interest": it["id"], "field": field, "term": term, "where": w})
                seen_terms.add(term.lower())
        if not hits:
            continue
        pw = PRIORITY_WEIGHT[it["priority"]]
        points = (MATCH_BASE + EXTRA_TERM * min(len(seen_terms) - 1, EXTRA_TERM_CAP)) * pw
        score += points
        hit_interests.append(it["id"])
        matched_by += hits
        matched_by.append({"kind": "interest", "interest": it["id"], "priority": it["priority"], "points": round(points, 4)})
        for term in it["not"]:
            w = where_found(item, term)
            if w:
                score -= NOT_PENALTY
                matched_by.append({"kind": "penalty", "source": "not", "interest": it["id"], "term": term,
                                   "where": w, "points": -NOT_PENALTY})
    if not hit_interests:
        return None
    for ex in profile["excludes"]:
        for _field, term in exclude_terms(ex):
            w = where_found(item, term)
            if w:
                score -= EXCLUDE_PENALTY
                matched_by.append({"kind": "penalty", "source": "exclude", "exclude": ex["topic"], "term": term,
                                   "where": w, "points": -EXCLUDE_PENALTY})
    return {"score": round(score, 4), "matched_by": matched_by, "interests": hit_interests}


def rank_items(items, profile, strategy):
    """레인 B 순위 목록. 점수 내림차순, 동점은 id 순(재현). 걸린 관심사가 없는 항목은 순위에 없다."""
    if strategy not in STRATEGIES:
        raise KeyError(f"모르는 전략 {strategy!r} (있는 것: {', '.join(STRATEGIES)})")
    scored = []
    for item in items:
        s = score_item(item, profile, strategy)
        if s:
            scored.append((item, s))
    scored.sort(key=lambda p: (-p[1]["score"], p[0]["id"]))
    out = []
    for n, (item, s) in enumerate(scored, 1):
        out.append({"rank": n, "id": item["id"], "source": item["source"], "title": item["title"], "url": item["url"],
                    "score": s["score"], "interests": s["interests"], "matched_by": s["matched_by"],
                    "known_example": bool(known_examples(item, profile))})
    return out
