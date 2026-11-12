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
    if raw_path:
        write_json(raw_path, raw)
    return {"engine": engine, "model": model if engine == "claude" else None, "k": k, "lane_b": len(lane_b),
            "interests": per, "failed": failed,
            "counts": {"picks": sum(len(v["picks"]) for v in per.values()), "topical": sum(len(v["topical"]) for v in per.values()),
                       "sessions": sum(len(v["raw"]) for v in raw.values())}}


def summary_line(res):
    c = res["counts"]
    return (f"관심사 질의: 레인 B {res['lane_b']} → 관심사 {len(res['interests'])}개 × 상위 {res['k']} → "
            f"새것(2) {c['picks']} · 주제만(1) {c['topical']}" + (f" · 실패 {len(res['failed'])}" if res["failed"] else ""))


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
        print(f"  {i}: 새것 {len(v['picks'])} · 주제만 {len(v['topical'])}" + "".join(f"\n    #{x['rank']} {x['title'][:90]}" for x in v["picks"][:3]))
    return 1 if res["failed"] and not res["interests"] else 0


if __name__ == "__main__":
    sys.exit(main())
