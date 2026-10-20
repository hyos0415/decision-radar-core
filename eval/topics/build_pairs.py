"""화제 묶기 정답지 — 판정할 짝 만들기 (HYO-127 2단계, 확정 시트 `eval/topics/gold-precondition-sheet.md`).

  python eval/topics/build_pairs.py --snapshot ../radar-snapshots/2026-W42 --out eval/topics/w42

풀 = 레인 C 후보(pipeline.topics) 중 출처별 인기 상위 TOP(60)개 = 240건.
판정할 짝 = 아래 신호 중 하나라도 걸린 짝의 합집합 (방법을 가리지 않고 넓게 — 정답지의 재현율이 여기서 정해진다)
  link   같은 링크 키 (pipeline.topics — 기준선 v1과 같다)
  xpost  크로스포스트 (같은 글쓴이 + 같은 제목)
  rare   제목의 드문 낱말 공유 (스냅숏 전체에서 RARE_DF번 이하, 3글자 이상)
  name   이름 토큰 공유 — 글자+숫자가 섞인 4자 이상 토큰, repo·모델 이름(URL의 owner/name 중 name)을 이어 붙인 형태로 비교
  emb    BGE-M3 dense 코사인 상위 EMB_TOP 이웃 (제목)
  char   제목 글자 3-gram TF-IDF 코사인 상위 CHAR_TOP 이웃
  owner  같은 GitHub 계정 · OWNER_DAYS일 안 · repo 이름 앞뒤 OWNER_AFFIX자 이상 같음 (v2 후보를 재려고 W41부터 — 2026-10-19)
+ 감사용 짝 (판정하는 사람에게는 구분 없이 섞는다)
  (이웃 신호 emb · char만 걸린 짝은 emb ≥ EMB_MIN 또는 char ≥ CHAR_MIN일 때만 — 나머지는 「떨어낸 이웃」)
  random 떨어낸 이웃에서 RANDOM_N/2 + 아무 신호 없는 짝에서 RANDOM_N/2 — 후보가 놓친 짝이 있는지 (재현율 추정)
  repeat 판정 짝의 REPEAT_FRAC를 순서를 바꿔 한 번 더 — 판정이 흔들리는지 (자기 일관성)

출력 (`--out`):
  pairs.json      판정 페이지가 읽는 것 — 항목 정보 + 짝 (순서 섞음, 어느 신호인지는 없음)
  pairs-key.json  짝마다 걸린 신호 · random/repeat 표시 — **판정을 다 채우기 전에 열지 않는다**
  pool.json       풀 240건과 인기 순위 (재현용)
"""
import argparse
import hashlib
import html
import json
import math
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pipeline import lanes, topics  # noqa: E402

TOP = 60
RARE_DF = 4
EMB_TOP = 3
CHAR_TOP = 3
EMB_MIN = 0.6           # 이웃 신호(emb · char)만 걸린 짝은 이 유사도 이상만 판정 — 상위 이웃은 늘 있어서 대부분 뻔한 「다름」이다
CHAR_MIN = 0.35
OWNER_DAYS = 14         # owner 신호는 v2 후보(D=7, A=5)보다 넓게 — 판정 짝에 그 규칙이 묶을 짝이 다 들어오게
OWNER_AFFIX = 4
RANDOM_N = 80           # 절반은 떨어낸 이웃 짝, 절반은 아무 신호 없는 짝에서
REPEAT_FRAC = 0.1
SEED = 20261019
SOURCES = ("hn", "reddit", "github", "huggingface")

WORD = re.compile(r"[A-Za-z][A-Za-z0-9.+-]{1,}")
STOP = set("the and for with from that this what your you are how why can not has have into about over just new using "
           "use show ask tell its it's was were will would been than then them they when where which who all any our".split())
NAME_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
TAG = re.compile(r"<[^>]+>")


def pool_items(items):
    """레인 C 후보 → 출처별 인기 상위 TOP. 같은 인기면 id 순 (재현)."""
    _, lane_b, _ = lanes.split_lanes(items)
    pop = topics.popularity(lane_b)
    by = defaultdict(list)
    for it in lane_b:
        if it["id"] in pop and it["source"] in SOURCES:
            by[it["source"]].append(it)
    out = []
    for src in SOURCES:
        ranked = sorted(by[src], key=lambda it: (-pop[it["id"]]["pop"], it["id"]))[:TOP]
        out += [(it, pop[it["id"]]) for it in ranked]
    return out


def words(title):
    return {w.lower().strip(".-") for w in WORD.findall(title or "")} - STOP


def names(it):
    """이름 토큰: 글자와 숫자가 섞인 4자 이상(Qwen3.8 · GLM-5.3) + URL의 repo·모델 이름. 구두점을 빼고 소문자로 붙여 비교한다."""
    out = set()
    for t in NAME_TOKEN.findall(it.get("title") or ""):
        if len(t) >= 4 and re.search(r"[A-Za-z]", t) and re.search(r"\d", t):
            out.add(re.sub(r"[^a-z0-9]", "", t.lower()))
    for u in [it.get("url")] + list(it.get("links") or []):
        k = topics.link_key(u) or ""
        if k.startswith(("github:", "hf:")) and "/" in k:
            name = k.split("/")[1] if k.count("/") >= 1 else ""
            name = re.sub(r"[^a-z0-9]", "", name.lower())
            if len(name) >= 4:
                out.add(name)
    return out


def squash(title):
    return re.sub(r"[^a-z0-9]", "", (title or "").lower())


def snippet(it, n=280):
    t = html.unescape(TAG.sub(" ", it.get("text") or ""))
    t = re.sub(r"\s+", " ", t).strip()
    if it["source"] == "reddit" and t.startswith("submitted by"):
        return ""
    return t[:n]


def char_vectors(titles):
    """글자 3-gram TF-IDF (정규화된 희소 벡터 dict)."""
    grams = []
    for t in titles:
        s = f"  {(t or '').lower()}  "
        grams.append(Counter(s[i:i + 3] for i in range(len(s) - 2)))
    df = Counter(g for c in grams for g in c)
    n = len(titles)
    vecs = []
    for c in grams:
        v = {g: tf * math.log((1 + n) / (1 + df[g])) for g, tf in c.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vecs.append({g: x / norm for g, x in v.items()})
    return vecs


def char_cos(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(g, 0.0) for g, x in a.items())


def top_neighbors(sim_row, i, k):
    order = sorted((j for j in range(len(sim_row)) if j != i), key=lambda j: -sim_row[j])
    return order[:k]


def embed_titles(titles, device=None):
    from arena import embed
    enc = embed.BGEM3Encoder(device=device)
    import numpy as np
    v, _sparse = enc.encode_docs(titles)                 # (dense [n, 1024], sparse) — dense만 쓴다
    v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-12
    return v, getattr(enc, "revision", None)


def pair_id(a, b):
    return "p-" + hashlib.sha1(f"{a}\n{b}".encode()).hexdigest()[:10]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--device", default=None)
    ap.add_argument("--top", type=int, default=TOP, help=f"출처별 인기 상위 몇 개를 풀에 넣나 (기본 {TOP} — W42·W41 정답지)")
    a = ap.parse_args(argv)
    globals()["TOP"] = a.top          # pool_items가 모듈 상수를 읽는다

    items = topics.read_items(a.snapshot)
    week = topics.week_of(a.snapshot)
    pool = pool_items(items)
    its = [it for it, _ in pool]
    n = len(its)
    ids = [it["id"] for it in its]

    df = Counter(w for it in items for w in words(it.get("title")))
    W = [{w for w in words(it.get("title")) if df[w] <= RARE_DF and len(w) >= 3} for it in its]
    N = [names(it) for it in its]
    L = [set(topics.item_link_keys(it)[0]) for it in its]
    X = [topics.crosspost_key(it) for it in its]
    S = [squash(it.get("title")) for it in its]

    signals = defaultdict(set)

    def add(i, j, sig):
        if i != j:
            signals[(min(i, j), max(i, j))].add(sig)

    for i in range(n):
        for j in range(i + 1, n):
            if L[i] & L[j]:
                add(i, j, "link")
            if X[i] and X[i] == X[j]:
                add(i, j, "xpost")
            if W[i] & W[j]:
                add(i, j, "rare")
            if N[i] & N[j] or any(x in S[j] for x in N[i]) or any(x in S[i] for x in N[j]):
                add(i, j, "name")

    import v2_methods
    for i, j in v2_methods.owner_series_edges(its, OWNER_DAYS, OWNER_AFFIX):
        add(i, j, "owner")

    titles = [it.get("title") or "" for it in its]
    cv = char_vectors(titles)
    for i in range(n):
        row = [char_cos(cv[i], cv[j]) for j in range(n)]
        for j in top_neighbors(row, i, CHAR_TOP):
            add(i, j, "char")
    ev, emb_rev = embed_titles(titles, a.device)
    sims = ev @ ev.T
    for i in range(n):
        for j in top_neighbors(list(sims[i]), i, EMB_TOP):
            add(i, j, "emb")

    rng = random.Random(SEED)
    strong = {"link", "xpost", "rare", "name", "owner"}

    def keep(p):
        i, j = p
        return bool(signals[p] & strong) or sims[i][j] >= EMB_MIN or char_cos(cv[i], cv[j]) >= CHAR_MIN

    cand = sorted(p for p in signals if keep(p))
    dropped = sorted(p for p in signals if not keep(p))
    none = [(i, j) for i in range(n) for j in range(i + 1, n) if (i, j) not in signals]
    rand = rng.sample(dropped, min(RANDOM_N // 2, len(dropped))) + rng.sample(none, min(RANDOM_N - RANDOM_N // 2, len(none)))
    rand_kind = {p: ("random-dropped" if p in signals else "random-none") for p in rand}
    judged = [(p, sorted(signals[p]), "candidate") for p in cand] + [(p, sorted(signals.get(p, ())), rand_kind[p]) for p in rand]
    repeats = rng.sample(judged, round(len(judged) * REPEAT_FRAC))

    rows, key = [], {}
    for (i, j), sigs, kind in judged:
        if rng.random() < 0.5:
            i, j = j, i
        pid = pair_id(ids[i], ids[j])
        rows.append({"id": pid, "a": ids[i], "b": ids[j]})
        key[pid] = {"a": ids[i], "b": ids[j], "kind": kind, "signals": sigs,
                    "emb_cos": round(float(sims[i][j]), 4), "char_cos": round(char_cos(cv[i], cv[j]), 4)}
    for (i, j), sigs, kind in repeats:
        orig = next(pid for pid, k in key.items() if {k["a"], k["b"]} == {ids[i], ids[j]} and k["kind"] != "repeat")
        o = key[orig]
        pid = pair_id(o["b"], o["a"]) + "r"
        rows.append({"id": pid, "a": o["b"], "b": o["a"]})
        key[pid] = {**o, "a": o["b"], "b": o["a"], "kind": "repeat", "repeat_of": orig}
    rng.shuffle(rows)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    info = {}
    for it, p in pool:
        info[it["id"]] = {"source": it["source"], "title": it.get("title") or "", "url": it.get("url") or "",
                          "links": [u for u in (it.get("links") or [])][:4], "signal": topics.signal_label(p["pool"], p["rank"], p["value"]),
                          "snippet": snippet(it), "new": it.get("new", True)}
    meta = {"week": week, "pool": f"출처별 인기 상위 {TOP}", "n_items": n, "n_pairs": len(rows),
            "seed": SEED, "made_by": "eval/topics/build_pairs.py"}
    (out / "pairs.json").write_text(json.dumps({"meta": meta, "items": info, "pairs": rows}, ensure_ascii=False, indent=1) + "\n",
                                    encoding="utf-8", newline="\n")
    meta_counts = {"candidate": len(cand), "dropped_neighbor": len(dropped), "no_signal": len(none)}
    kmeta = {**meta, "universe": meta_counts, "params": {"TOP": TOP, "RARE_DF": RARE_DF, "EMB_TOP": EMB_TOP, "CHAR_TOP": CHAR_TOP,
                                "EMB_MIN": EMB_MIN, "CHAR_MIN": CHAR_MIN, "RANDOM_N": RANDOM_N,
                                "OWNER_DAYS": OWNER_DAYS, "OWNER_AFFIX": OWNER_AFFIX,
                                "REPEAT_FRAC": REPEAT_FRAC}, "embedding": {"model": "BAAI/bge-m3", "revision": emb_rev, "text": "title"}}
    (out / "pairs-key.json").write_text(json.dumps({"meta": kmeta, "pairs": key}, ensure_ascii=False, indent=1) + "\n",
                                        encoding="utf-8", newline="\n")
    (out / "pool.json").write_text(json.dumps([{"id": it["id"], **p} for it, p in pool], ensure_ascii=False, indent=1) + "\n",
                                   encoding="utf-8", newline="\n")
    by_sig = Counter(s for p in cand for s in signals[p])
    only = Counter(next(iter(signals[p])) for p in cand if len(signals[p]) == 1)
    print(f"{week}: 풀 {n}건 · 후보 짝 {len(cand)} (신호별 {dict(by_sig)} · 그 신호 하나뿐 {dict(only)}) · 떨어낸 이웃 {len(dropped)} · "
          f"신호 없음 {len(none)} → 판정 = 후보 {len(cand)} + 무작위 {len(rand)} + 다시 {len(repeats)} = {len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
