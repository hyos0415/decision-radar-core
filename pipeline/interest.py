"""관심사 질의 (HYO-187) — 레인 B에서 관심사마다 「실험해 볼 만한 새 도구·기법」 후보를 고른다.

근거: `eval/interest/results-v2.md`. 네 주(질의 56)에서 BM25가 하이브리드 + 리랭커와 가를 수 없었고(+0.027 [−0.022, 0.080]),
시멘틱 서치(0.409)보다 분명히 나았다. 지금 키워드 매칭보다는 0.20 덜 놓쳤다.

1단계 검색 — BM25를 파이썬으로 돌린다(ES·GPU 없음).
  관심사마다 영어 낱말 질의(영어 topic + 별칭 + 예시)로 레인 B 후보 상위 TOPK(60)를 뽑는다.
  사용자 11/12 「상위 60으로」: 상위 30에서 이름이 그대로 나오는 정답이 밀려나는 것 같아서다.
  문서는 「제목 + 본문 창(WINDOW 낱말)」 조각으로 나누고, 문서 점수는 가장 높은 조각의 점수다(Arena S2와 같은 모양).
2단계 판정 — 구독 `claude -p`로 관심사마다 한 세션 돌린다. 등급 0~2는 eval 정답과 같은 규칙이다.
  2 = 이 관심사의 특정한 새 도구·모델·기법·논문·벤치마크, 1 = 주제만 맞음, 0 = 주제 아님.

  python -m pipeline.interest run --snapshot <주차 폴더> --profile <radar-profile.yaml> --out <run 폴더> [--engine claude|stub] [--reuse]
  → <out>/interest.json (관심사마다 등급 2 · 1, 검색 순위대로) · interest-raw.json (모델 원답)

목록 · detect에 잇는 것은 이 파일 밖이다 — 판정 결과의 양을 한 주 보고 정한다 (query-topk.md 변경 줄).
"""
import argparse
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

from . import lanes
from .interest_profile import load_profile
from .profile_filter import read_items, write_json

TOPK = 60                    # 1단계 상위 몇 개를 판정에 넘기나 (사용자 11/12)
WINDOW = 250                 # 조각 하나의 본문 낱말 수 (Arena 청크 상한 512 bge 토큰 ≈ 영어 300~380 낱말보다 조금 작게)
K1, B = 1.2, 0.75            # Arena S2와 같은 BM25 값
BODY_CHARS = 800             # 판정자에게 보이는 본문 앞부분 (eval과 같다)
BATCH_MAX = 100              # 판정 한 세션의 글 수 상한
JUDGE_SEED = 20261112
DEFAULT_MODEL = os.environ.get("RADAR_INTEREST_MODEL", "claude-sonnet-5-5")
STRIP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")      # 구독으로만 (pipeline.slots와 같다)
HTML_SOURCES = ("reddit:", "hn:")                              # text가 HTML 조각인 출처 (arena.corpus와 같다)

# 관심사 topic의 영어 이름 — 프로필에 `topic_en`이 있으면 그것을 쓴다. 없고 여기에도 없으면 별칭 · 예시만으로 질의한다
TOPIC_EN = {
    "multimodal": "Multimodal models",
    "hybrid-sparse-retrieval": "Hybrid and sparse retrieval",
    "embeddings-rerankers": "Embedding and reranker models",
    "graph-rag": "Graph-based retrieval (GraphRAG)",
    "vector-index-efficiency": "Reducing vector index storage and search cost",
    "agent-runtime": "Agent runtimes and human-in-the-loop",
    "llm-eval": "LLM evaluation and LLM judges",
    "small-local-models": "Small local models and quantized serving",
    "answer-verification": "Answer verification and factuality judgment",
    "guardrails-pii": "Guardrails and PII masking",
    "speech": "Speech recognition and synthesis (STT, TTS)",
    "preference-optimization": "Reinforcement learning and preference optimization (DPO, ORPO)",
    "code-impact": "Code understanding and impact analysis",
    "model-routing-cost": "Model routing and cost reduction",
}

# ---- 1단계: BM25 -----------------------------------------------------------------------------------------------

_TOKEN = re.compile(r"[a-z0-9]+|[가-힣]+")
STOP = frozenset("""a an and are as at be but by for from has have how i if in into is it its of on or our so such that the their
then there these they this to was we were what when which who will with you your""".split())


def stem(t):
    """가벼운 영어 어미 떼기 (Porter 일부 — 의존성 없이). 숫자 · 한글은 그대로."""
    if not t.isascii() or not t.isalpha() or len(t) <= 3:
        return t
    if t.endswith("ies") and len(t) > 4:
        t = t[:-3] + "y"
    elif t.endswith("sses"):
        t = t[:-2]
    elif t.endswith("s") and not t.endswith("ss") and not t.endswith("us"):
        t = t[:-1]
    if t.endswith("ing") and len(t) > 5:
        t = t[:-3]
    elif t.endswith("ed") and len(t) > 4:
        t = t[:-2]
    return t


def tokens(text):
    return [stem(t) for t in _TOKEN.findall((text or "").lower()) if t not in STOP]


def item_text(item):
    text = item.get("text") or ""
    if item["id"].startswith(HTML_SOURCES):
        from collector.item import html_to_text
        text = html_to_text(text, lines=True)
    return text


def chunks_of(item):
    """[(조각 토큰)] — 제목 + 본문 창. 본문이 없으면 제목만."""
    title = tokens(item.get("title") or "")
    body = tokens(item_text(item))
    if not body:
        return [title]
    return [title + body[i:i + WINDOW] for i in range(0, len(body), WINDOW)]


class BM25:
    """조각 단위 BM25 — 문서 점수 = 가장 높은 조각 점수. idf는 Lucene 식 ln(1 + (N − df + 0.5)/(df + 0.5))."""

    def __init__(self, items):
        self.ids, self.owner, self.tf, self.len = [], [], [], []
        for it in items:
            self.ids.append(it["id"])
            for c in chunks_of(it):
                self.owner.append(len(self.ids) - 1)
                self.tf.append(Counter(c))
                self.len.append(len(c))
        self.n = len(self.tf)
        self.avg = (sum(self.len) / self.n) if self.n else 0.0
        df = Counter(t for tf in self.tf for t in tf)
        self.idf = {t: math.log(1 + (self.n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def search(self, query, k):
        q = tokens(query)
        best = {}
        for ci, tf in enumerate(self.tf):
            s = 0.0
            for t in q:
                f = tf.get(t)
                if f:
                    s += self.idf[t] * f * (K1 + 1) / (f + K1 * (1 - B + B * self.len[ci] / self.avg))
            if s > 0:
                d = self.owner[ci]
                if s > best.get(d, 0.0):
                    best[d] = s
        ranked = sorted(best.items(), key=lambda x: (-x[1], self.ids[x[0]]))[:k]
        return [(self.ids[d], round(s, 4)) for d, s in ranked]


def query_text(interest):
    """영어 낱말 질의 — 「영어 topic: 별칭들. Examples: 예시들.」 (eval의 영어 질의와 같은 틀)."""
    head = interest.get("topic_en") or TOPIC_EN.get(interest["id"])
    s = (f"{head}: " if head else "") + ", ".join(interest.get("aliases") or []) + "."
    if interest.get("examples"):
        s += f" Examples: {', '.join(interest['examples'])}."
    return s


# ---- 2단계: 판정 ------------------------------------------------------------------------------------------------

JUDGE_SYSTEM = """너는 기술 소식 분류 판정자다. 관심사 하나의 정의와 이번 주 글 목록을 받는다. 글마다 그 관심사와의 관련 등급을 매긴다.

등급
- 2: 이 관심사에 속하는 **특정한 새** 도구·모델·라이브러리·기법·논문·벤치마크를 다룬다(출시, 공개, 큰 업데이트). 실험 후보가 될 수 있는 것.
- 1: 이 관심사가 글의 주제지만 특정한 새것은 아니다(토론, 질문, 경험담, 이미 있는 것의 튜토리얼, 일반 소식).
- 0: 이 관심사가 주제가 아니다(지나가는 언급, 정의의 `제외`에 해당, 무관).

규칙
- 글의 제목과 본문 앞부분만 보고 판단한다. 링크를 따라가지 않는다.
- 관심사 낱말이 나와도 글의 주제가 아니면 0이다. 낱말이 안 나와도 주제가 맞으면 1이나 2다.
- 목록의 모든 번호에 정확히 한 번씩 답한다. why는 한국어 한 줄(40자 안쪽)."""
JUDGE_SCHEMA = {"type": "object", "required": ["labels"], "properties": {"labels": {"type": "array", "items": {
    "type": "object", "required": ["n", "grade", "why"],
    "properties": {"n": {"type": "integer"}, "grade": {"type": "integer", "enum": [0, 1, 2]}, "why": {"type": "string"}}}}}}


class InterestError(RuntimeError):
    pass


def interest_card(it, excludes=()):
    lines = [f"관심사: {it['topic']} ({it['id']})", f"설명: {it.get('description') or ''}",
             f"별칭: {', '.join(it.get('aliases') or [])}", f"예시: {', '.join(it.get('examples') or []) or '없음'}",
             f"제외: {', '.join(list(it.get('not') or []) + [e['topic'] for e in excludes]) or '없음'}"]
    return "\n".join(lines)


def doc_brief(item):
    body = " ".join(item_text(item).split())[:BODY_CHARS]
    return f"제목: {item.get('title') or item['id']}\n출처: {item.get('source')}\n본문 앞부분: {body}"


def batches(ids, interest_id):
    ids = list(ids)
    random.Random(f"{JUDGE_SEED}:{interest_id}").shuffle(ids)     # 검색 순위를 판정자에게 보이지 않는다
    n = -(-len(ids) // BATCH_MAX)
    size = -(-len(ids) // n) if n else 0
    return [ids[b * size:(b + 1) * size] for b in range(n)]


def check_labels(so, n):
    """번호 1..n이 정확히 한 번씩 · 등급 0~2 — 아니면 문제 목록 (규율 1: 스크립트가 검사)."""
    got, problems = {}, []
    for x in (so or {}).get("labels") or []:
        k = x.get("n")
        if not isinstance(k, int) or not 1 <= k <= n:
            problems.append(f"모르는 번호 {k}")
        elif k in got:
            problems.append(f"겹친 번호 {k}")
        elif x.get("grade") not in (0, 1, 2):
            problems.append(f"{k}: 등급 {x.get('grade')}")
        else:
            got[k] = x
    missing = [k for k in range(1, n + 1) if k not in got]
    if missing:
        problems.append(f"빠진 번호 {missing[:10]}")
    return got, problems


def judge_claude(user, model=DEFAULT_MODEL, effort="medium", timeout=900):
    """구독 `claude -p` 한 번 — 도구 없음 · 설정 안 읽음 · API 키 지움 (pipeline.slots와 같은 설정)."""
    b = os.environ.get("RADAR_CLAUDE_BIN") or shutil.which("claude") or "claude"
    cmd = [*([sys.executable, b] if b.endswith(".py") else [b]), "-p", "--output-format", "json", "--system-prompt", JUDGE_SYSTEM,
           "--json-schema", json.dumps(JUDGE_SCHEMA, ensure_ascii=False), "--tools", "", "--setting-sources", "",
           "--no-session-persistence", "--model", model, "--effort", effort, "--permission-mode", "dontAsk"]
    env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
    env["PYTHONIOENCODING"] = "utf-8"
    with tempfile.TemporaryDirectory(prefix="radar-interest-") as run:
        p = subprocess.run(cmd, input=user.encode("utf-8"), capture_output=True, cwd=run, env=env, timeout=timeout)
    try:
        res = json.loads(p.stdout.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise InterestError(f"claude 결과가 JSON이 아니다 (exit {p.returncode}) {p.stderr.decode('utf-8', 'replace')[-300:]}")
    if res.get("is_error") or res.get("structured_output") is None:
        raise InterestError(f"claude 실패: {res.get('subtype')} {str(res.get('result'))[:300]}")
    return res["structured_output"], res


def judge_stub(user, n):
    """테스트 · 배관용 — 글 번호를 3으로 나눈 나머지 1 → 2, 2 → 1, 0 → 0. 모델 판단이 아니다."""
    return {"labels": [{"n": k, "grade": {1: 2, 2: 1, 0: 0}[k % 3], "why": "stub"} for k in range(1, n + 1)]}, {}


def judge_interest(interest, ids, items, excludes, engine, model):
    """관심사 하나 → ({문서: {grade, why}}, [원답]). 답이 어긋나면 한 번 더 묻고, 그래도 어긋나면 실패."""
    labels, raws = {}, []
    for part in batches(ids, interest["id"]):
        listing = "\n\n".join(f"[{i}]\n{doc_brief(items[d])}" for i, d in enumerate(part, 1))
        user = f"{interest_card(interest, excludes)}\n\n글 {len(part)}개:\n\n{listing}"
        for attempt in (1, 2):
            so, res = judge_stub(user, len(part)) if engine == "stub" else judge_claude(user, model)
            got, problems = check_labels(so, len(part))
            if not problems:
                break
        if problems:
            raise InterestError(f"{interest['id']}: 두 번 다 답이 어긋났다 — {problems[:5]}")
        labels.update({part[k - 1]: {"grade": x["grade"], "why": x["why"]} for k, x in got.items()})
        raws.append({"ids": part, "structured_output": so, "usage": res.get("usage"), "cost_usd_equiv": res.get("total_cost_usd")})
    return labels, raws


# ---- 3단계: 주 PICK_N건 고르기 (사용자 11/13 「그냥 10개 정도」 — W46에 새것이 161건이었다) ---------------------------

PICK_N = 10
PRIORITY = {"high": 0, "normal": 1, "low": 2}
PICK_SYSTEM = """너는 기술 스택 관리자의 조수다. 관심사별로 이번 주 「새 도구·모델·기법」 후보가 있다.
사용자가 자기 프로젝트에서 **직접 실험해 볼 만한 것**을 최대 {n}개, 중요한 순서로 고른다.

규칙
- 실제로 받아서 돌려 볼 수 있는 것(공개 모델, 라이브러리, 도구, 코드가 있는 기법)을 먼저 본다. 소개 · 팟캐스트 · 의견 글은 뒤로.
- 같은 것을 다룬 글이 여럿이면 하나만 고른다(1차 출처에 가까운 것).
- 관심사마다 적힌 「이미 아는 것」 자체를 다룬 글은 고르지 않는다. 그것의 새 버전 · 큰 업데이트면 고를 수 있다. 우리는 놓치던 것을 찾는다.
- 한 관심사에 몰리지 않게 하되, 정말 중요한 게 몰려 있으면 그대로 고른다.
- 후보 번호(c1, c2 …)로만 답한다. reason은 한국어 한 줄(50자 안쪽) — 어느 프로젝트에서 무엇을 해 볼 수 있나."""
PICK_SCHEMA = {"type": "object", "required": ["picks"], "properties": {"picks": {"type": "array", "items": {
    "type": "object", "required": ["c", "reason"], "properties": {"c": {"type": "string"}, "reason": {"type": "string"}}}}}}


def _norm_title(t):
    return " ".join(tokens(t))[:80]


def pick_candidates(per):
    """관심사별 새것 → 후보 목록 [{c, id, interest, title, …}] — 같은 글(id)이나 같은 제목은 검색 순위가 높은 쪽 하나만."""
    rows = sorted(((x["rank"], i, x) for i, v in per.items() for x in v["picks"]), key=lambda r: (r[0], r[1], r[2]["id"]))
    seen_id, seen_title, out = set(), set(), []
    for _rank, i, x in rows:
        nt = _norm_title(x.get("title") or "")
        if x["id"] in seen_id or (nt and nt in seen_title):
            continue
        seen_id.add(x["id"])
        seen_title.add(nt)
        out.append({"c": f"c{len(out) + 1}", "id": x["id"], "interest": i, "title": x.get("title"), "source": x.get("source"),
                    "rank": x["rank"], "why": x.get("why")})
    return out


def pick_fallback(cands, profile, n=PICK_N):
    """모델 없이 — 관심사마다 검색 순위가 가장 높은 새것 1건, 프로필 우선순위(high → normal → low) · 검색 순위 순으로 n건."""
    prio = {it["id"]: PRIORITY.get(it.get("priority"), 1) for it in profile["interests"]}
    first = {}
    for c in cands:
        if c["interest"] not in first or c["rank"] < first[c["interest"]]["rank"]:
            first[c["interest"]] = c
    ordered = sorted(first.values(), key=lambda c: (prio.get(c["interest"], 1), c["rank"], c["id"]))[:n]
    return [{"id": c["id"], "interest": c["interest"], "reason": f"관심사 1위 (검색 {c['rank']}위)"} for c in ordered]


def check_picks(so, cands, n=PICK_N):
    """모델 답 → ([{id, interest, reason}], 버린 수). 모르는 번호 · 겹친 번호는 버리고, n건을 넘으면 자른다 (규율 1)."""
    by_c = {c["c"]: c for c in cands}
    out, seen, dropped = [], set(), 0
    for p in (so or {}).get("picks") or []:
        c = by_c.get(str(p.get("c", "")).strip())
        if c is None or c["id"] in seen:
            dropped += 1
            continue
        seen.add(c["id"])
        out.append({"id": c["id"], "interest": c["interest"], "reason": str(p.get("reason") or "")[:120]})
    dropped += max(0, len(out) - n)
    return out[:n], dropped


def pick_prompt(cands, profile):
    defs = "\n".join(f"- {it['id']} ({it['topic']}): {it.get('description') or ''}" + (f" · 왜: {it['why']}" if it.get("why") else "")
                     + (f" · 관련: {', '.join(it['relates_to'])}" if it.get("relates_to") else "")
                     + (f" · 이미 아는 것: {', '.join(it['examples'])}" if it.get("examples") else "")
                     for it in profile["interests"] if it.get("active", True))
    rows = "\n".join(f"[{c['c']}] ({c['interest']}) {c['title']} | 출처 {c['source']} | {c['why']}" for c in cands)
    return f"관심사:\n{defs}\n\n후보 {len(cands)}개:\n{rows}"


def pick_claude(cands, profile, model, n=PICK_N, timeout=900):
    b = os.environ.get("RADAR_CLAUDE_BIN") or shutil.which("claude") or "claude"
    cmd = [*([sys.executable, b] if b.endswith(".py") else [b]), "-p", "--output-format", "json", "--system-prompt", PICK_SYSTEM.format(n=n),
           "--json-schema", json.dumps(PICK_SCHEMA, ensure_ascii=False), "--tools", "", "--setting-sources", "",
           "--no-session-persistence", "--model", model, "--effort", "medium", "--permission-mode", "dontAsk"]
    env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
    env["PYTHONIOENCODING"] = "utf-8"
    with tempfile.TemporaryDirectory(prefix="radar-interest-pick-") as run:
        p = subprocess.run(cmd, input=pick_prompt(cands, profile).encode("utf-8"), capture_output=True, cwd=run, env=env, timeout=timeout)
    try:
        res = json.loads(p.stdout.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise InterestError(f"claude 결과가 JSON이 아니다 (exit {p.returncode}) {p.stderr.decode('utf-8', 'replace')[-300:]}")
    if res.get("is_error") or res.get("structured_output") is None:
        raise InterestError(f"claude 실패: {res.get('subtype')} {str(res.get('result'))[:300]}")
    return res["structured_output"], res


def choose_week(per, profile, engine, model, old=None, n=PICK_N):
    """주 n건 → (to_detect, 기록). 모델이 실패하거나 하나도 못 고르면 코드 규칙(pick_fallback)으로."""
    cands = pick_candidates(per)
    info = {"candidates": len(cands), "n": n}
    if not cands:
        return [], {**info, "engine": "none"}
    if old and old.get("ids") == [c["id"] for c in cands]:
        return old["to_detect"], {**info, **old["info"], "reused": True}
    if engine == "stub":
        picks, dropped = check_picks({"picks": [{"c": c["c"], "reason": "stub"} for c in cands]}, cands, n)
        return picks, {**info, "engine": "stub", "dropped": dropped}
    for attempt in (1, 2):
        try:
            so, res = pick_claude(cands, profile, model, n)
        except (InterestError, subprocess.TimeoutExpired) as e:
            err = str(e)[:300]
            continue
        picks, dropped = check_picks(so, cands, n)
        if picks:
            return picks, {**info, "engine": "claude", "model": model, "dropped": dropped, "attempts": attempt,
                           "usage": res.get("usage"), "cost_usd_equiv": res.get("total_cost_usd")}
        err = f"고른 것이 없다 (버림 {dropped})"
    return pick_fallback(cands, profile, n), {**info, "engine": "fallback", "error": err}


def run(items, profile, engine="claude", model=DEFAULT_MODEL, out=None, reuse=False, k=TOPK):
    """레인 B 후보 → {관심사: {query, picks(등급 2), topical(등급 1), searched}}."""
    _a, lane_b, _s = lanes.split_lanes(items)
    by_id = {it["id"]: it for it in lane_b}
    index = BM25(lane_b)
    raw_path = Path(out) / "interest-raw.json" if out else None
    old = json.loads(raw_path.read_text(encoding="utf-8")) if reuse and raw_path and raw_path.exists() else {}
    interests = [it for it in profile["interests"] if it.get("active", True)]
    excludes = profile.get("excludes") or []
    per, raw, failed = {}, {}, {}
    for it in interests:
        q = query_text(it)
        hits = index.search(q, k)
        rank = {d: n for n, (d, _) in enumerate(hits, 1)}
        try:
            if it["id"] in old and set(old[it["id"]]["labels"]) == set(rank):
                labels, raws = old[it["id"]]["labels"], old[it["id"]]["raw"]
            else:
                labels, raws = judge_interest(it, list(rank), by_id, excludes, engine, model) if hits else ({}, [])
        except (InterestError, subprocess.TimeoutExpired) as e:
            failed[it["id"]] = str(e)[:300]
            continue
        raw[it["id"]] = {"labels": labels, "raw": raws}
        def row(d):
            x = by_id[d]
            return {"id": d, "title": x.get("title"), "url": x.get("url"), "source": x.get("source"), "rank": rank[d],
                    "grade": labels[d]["grade"], "why": labels[d]["why"]}
        ordered = sorted(rank, key=rank.get)
        per[it["id"]] = {"topic": it["topic"], "query": q, "searched": len(hits),
                         "picks": [row(d) for d in ordered if labels[d]["grade"] == 2],
                         "topical": [row(d) for d in ordered if labels[d]["grade"] == 1]}
    to_detect, pick_info = choose_week(per, profile, engine, model, old=old.get("_pick"))
    topic = {i: v["topic"] for i, v in per.items()}
    to_detect = [dict(p, topic=topic.get(p["interest"]), order=n) for n, p in enumerate(to_detect, 1)]
    if raw_path:
        raw["_pick"] = {"ids": [c["id"] for c in pick_candidates(per)], "to_detect": to_detect, "info": pick_info}
        write_json(raw_path, raw)
    return {"engine": engine, "model": model if engine == "claude" else None, "k": k, "lane_b": len(lane_b),
            "interests": per, "failed": failed, "to_detect": to_detect, "pick": pick_info,
            "counts": {"picks": sum(len(v["picks"]) for v in per.values()), "topical": sum(len(v["topical"]) for v in per.values()),
                       "to_detect": len(to_detect),
                       "sessions": sum(len(v["raw"]) for k_, v in raw.items() if k_ != "_pick") + (1 if pick_info.get("engine") == "claude" and not pick_info.get("reused") else 0)}}


def summary_line(res):
    c = res["counts"]
    return (f"관심사 질의: 레인 B {res['lane_b']} → 관심사 {len(res['interests'])}개 × 상위 {res['k']} → "
            f"새것(2) {c['picks']} · 주제만(1) {c['topical']} → detect {c['to_detect']} ({res['pick'].get('engine')})"
            + (f" · 실패 {len(res['failed'])}" if res["failed"] else ""))


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="pipeline.interest")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--snapshot", required=True, help="주차 폴더 또는 items.jsonl")
    r.add_argument("--profile", default=os.environ.get("RADAR_PROFILE"), required=not os.environ.get("RADAR_PROFILE"),
                   help="radar-profile.yaml (기본: RADAR_PROFILE)")
    r.add_argument("--out", required=True, help="결과 폴더 — profile_filter run과 같은 폴더")
    r.add_argument("--engine", default=os.environ.get("RADAR_INTEREST_ENGINE", "claude"), choices=["claude", "stub"])
    r.add_argument("--model", default=DEFAULT_MODEL)
    r.add_argument("--k", type=int, default=TOPK)
    r.add_argument("--reuse", action="store_true", help="interest-raw.json에 같은 후보의 판정이 있으면 모델을 다시 부르지 않는다")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    profile, _warnings = load_profile(a.profile)
    res = run(read_items(a.snapshot), profile, engine=a.engine, model=a.model, out=out, reuse=a.reuse, k=a.k)
    write_json(out / "interest.json", res)
    print(summary_line(res))
    for i, v in res["interests"].items():
        print(f"  {i}: 새것 {len(v['picks'])} · 주제만 {len(v['topical'])}")
    titles = {x["id"]: x["title"] for v in res["interests"].values() for x in v["picks"]}
    for p in res["to_detect"]:
        print(f"  detect {p['order']}. [{p['interest']}] {(titles.get(p['id']) or '')[:80]} — {p['reason']}")
    return 1 if res["failed"] and not res["interests"] else 0


if __name__ == "__main__":
    sys.exit(main())
