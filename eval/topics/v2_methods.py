"""화제 묶기 v2 후보 — HYO-127 (dev = W42에서 매개변수를 고르고, 얼린 뒤 W41로 확인).

name_rare(K)   이름 토큰 매칭에서 흔한 이름을 끊는다: 그 이름이 풀 안에서 K개 넘는 글에 나오면 묶는 데 쓰지 않는다.
               (v1+name의 무관 묶음은 「Qwen3.8」처럼 흔한 이름이 서로 다른 글을 union-find 사슬로 이은 것)
owner_series(D, A)  같은 GitHub 계정의 repo 둘이 D일 안에 만들어졌고, repo 이름이 길이 A 이상의 같은 앞부분이나 뒷부분을 가지면 같음
               (T8 — storytold의 photocraft · wordcraft …. 계정만 같은 것은 묶지 않는다: openai/math ≠ openai/privacy-filter)
"""
import datetime as dt
import re
from collections import defaultdict

import build_pairs as bp


def name_index(its):
    """항목마다 이름 집합, 이름마다 그 이름이 걸리는 항목 집합 (같은 토큰 · 제목에 들어 있음 둘 다)."""
    N = [bp.names(it) for it in its]
    S = [bp.squash(it.get("title")) for it in its]
    where = defaultdict(set)
    for i, ns in enumerate(N):
        for x in ns:
            where[x].add(i)
    for x in list(where):
        for j, s in enumerate(S):
            if x in s:
                where[x].add(j)
    return where


def name_rare_edges(its, K):
    edges = []
    for x, idx in name_index(its).items():
        if 2 <= len(idx) <= K:
            idx = sorted(idx)
            edges += [(idx[0], j) for j in idx[1:]]
    return edges


def _repo(it):
    if it.get("source") != "github" or not str(it.get("id", "")).startswith("github:"):
        return None
    owner, _, name = it["id"].split(":", 1)[1].partition("/")
    return owner.lower(), name.lower()


def _affix(a, b):
    """두 이름이 함께 가진 가장 긴 앞부분·뒷부분 길이 (구두점 제거)."""
    a, b = re.sub(r"[^a-z0-9]", "", a), re.sub(r"[^a-z0-9]", "", b)
    pre = 0
    while pre < min(len(a), len(b)) and a[pre] == b[pre]:
        pre += 1
    suf = 0
    while suf < min(len(a), len(b)) and a[-1 - suf] == b[-1 - suf]:
        suf += 1
    return max(pre, suf)


def _created(it):
    try:
        return dt.datetime.fromisoformat(str(it.get("created_at")).replace("Z", "+00:00"))
    except ValueError:
        return None


def owner_series_edges(its, D, A):
    by = defaultdict(list)
    for i, it in enumerate(its):
        r = _repo(it)
        if r:
            by[r[0]].append((i, r[1], _created(it)))
    edges = []
    for rows in by.values():
        for x in range(len(rows)):
            for y in range(x + 1, len(rows)):
                (i, ni, ci), (j, nj, cj) = rows[x], rows[y]
                if ci and cj and abs((ci - cj).total_seconds()) <= D * 86400 and _affix(ni, nj) >= A:
                    edges.append((i, j))
    return edges


def url_names(it):
    """URL에서만 뽑은 repo·모델 이름 (제목 토큰 없이) — 「0.6b」 「GPT-6」처럼 제목에 지나가며 나온 숫자 토큰을 뺀다."""
    out = set()
    for u in [it.get("url")] + list(it.get("links") or []):
        k = bp.topics.link_key(u) or ""
        if k.startswith(("github:", "hf:")) and k.count("/") >= 1:
            name = bp.re.sub(r"[^a-z0-9]", "", k.split(":", 1)[1].split("/")[1].lower())
            if len(name) >= 4:
                out.add(name)
    return out


def name_url_edges(its, K):
    """URL 이름이 다른 글의 URL 이름과 같거나 그 글 제목에 들어 있으면 묶는다. 그 이름이 K개 넘는 글에 걸리면 쓰지 않는다."""
    U = [url_names(it) for it in its]
    S = [bp.squash(it.get("title")) for it in its]
    where = defaultdict(set)
    for i, ns in enumerate(U):
        for x in ns:
            where[x].add(i)
    for x in list(where):
        for j, s in enumerate(S):
            if x in s:
                where[x].add(j)
    edges = []
    for x, idx in where.items():
        if 2 <= len(idx) <= K:
            idx = sorted(idx)
            edges += [(idx[0], j) for j in idx[1:]]
    return edges
