"""레인 C — 화제 (HYO-127, doc-pipeline §2-3 변경 2026-10-19).

레인 B는 프로필 관심사로 거르기 때문에 관심사 밖의 화제는 인기와 상관없이 버려진다. 레인 C는 분야를 가리지 않고
그 주에 인기 있던 소식을 토픽으로 묶어 상위 k개를 보여 준다. A·B와 점수를 합치지 않는다.

1단계 (이 파일): **같은 링크 + 크로스포스트로만 묶는다** — precision 우선. W42 측정(2026-10-16)에서 링크로 묶은 것은
전부 맞았고, 드문 낱말 매칭은 무관한 글을 사슬로 엮었다. 이름·임베딩 묶기는 손 묶음 정답지를 등록한 뒤(2단계).

- 후보 = 레인 B 항목 중 인기 신호가 있는 것 (레인 A·벤더 페이지는 빠진다 — 그건 관문의 일)
- 인기 = 출처 안 순위의 백분위 (`pool`마다 따로). HN points · GitHub stars · HF trending/upvotes는 값으로 순위를 내고,
  Reddit은 피드 순위(`rank:*`)를 그대로 쓴다 (RSS는 upvote를 주지 않는다). 항목의 인기 = 걸린 pool 중 가장 높은 백분위
- 순위 = 자리 나눔: 상위 k의 절반은 여러 출처(hn · reddit · github · huggingface)에 나온 토픽(출처 수 → 인기),
  나머지는 한 출처 토픽(인기). 10/16 결정 「출처 수 먼저」를 W42로 재 보고 바꿈 (사용자 2026-10-19) — `rank_topics`
- 크로스포스트(같은 글쓴이가 같은 제목으로 서브레딧 여러 곳에 올린 글)는 한 글로 센다

이 파일의 상수(MAX_LINKS 등)와 정규화 규칙은 1단계 잠정값이다. k는 W41·W42(dev)에서 정하고 고정한다 (규율 7).
"""
import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import lanes

METHOD = "link+crosspost@v1"
K_DEFAULT = 10            # 잠정 (dev 주에 정한다)
MAX_LINKS = 3             # 링크 키가 이보다 많은 글은 모음 글로 보고 링크로 묶지 않는다 (한 글이 무관한 토픽들을 잇는 것을 막는다)
SOURCES = ("hn", "reddit", "github", "huggingface")

# 링크 키로 쓰지 않는 호스트 — 토론 페이지 자체이거나 미디어 업로더라 같은 토픽이라는 뜻이 없다
SKIP_HOSTS = {"news.ycombinator.com", "reddit.com", "old.reddit.com", "np.reddit.com", "redd.it", "i.redd.it",
              "v.redd.it", "preview.redd.it", "external-preview.redd.it", "i.imgur.com", "imgur.com"}
HF_SECTIONS = {"datasets", "spaces"}
HF_SKIP = {"", "models", "docs", "learn", "pricing", "join", "login", "settings", "organizations", "collections", "posts", "tasks"}
GITHUB_REPO_VIEWS = {"tree", "blob", "wiki"}          # 이 뒤의 경로는 같은 repo를 보는 것 → repo 키로 접는다
GITHUB_SKIP = {"", "orgs", "topics", "search", "settings", "features", "marketplace", "sponsors", "trending", "collections"}
ARXIV_ID = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")
LOCAL_HOST = re.compile(r"^(127\.|10\.|192\.168\.|0\.0\.0\.0$)")


def link_key(url):
    """URL → 같은 대상을 가리키면 같은 키 (없으면 None). 쿼리·조각은 버린다 (YouTube `v`만 예외)."""
    if not url:
        return None
    p = urlparse(url.strip())
    host = p.netloc.lower().split(":")[0].removeprefix("www.").removeprefix("m.")
    if not host or host in SKIP_HOSTS or host.endswith(".reddit.com") or "." not in host or LOCAL_HOST.match(host):
        return None                                            # 토론 페이지 · 미디어 업로더 · localhost · 사설 IP
    segs = [s for s in p.path.split("/") if s]
    if host in ("youtube.com", "youtu.be"):
        vid = segs[0] if host == "youtu.be" and segs else (parse_qs(p.query).get("v") or [None])[0]
        return f"youtube:{vid}" if vid else None
    if host == "arxiv.org" and len(segs) >= 2 and segs[0] in ("abs", "pdf", "html"):
        m = ARXIV_ID.search(segs[1])
        return f"arxiv:{m.group(1)}" if m else None
    if host == "huggingface.co":
        if not segs or segs[0] in HF_SKIP:
            return None
        if segs[0] == "papers" and len(segs) >= 2:
            m = ARXIV_ID.search(segs[1])
            return f"arxiv:{m.group(1)}" if m else None
        if segs[0] in HF_SECTIONS:
            return f"hf:{segs[0]}/{segs[1]}/{segs[2]}".lower() if len(segs) >= 3 else None
        if segs[0] == "blog":
            return f"hf:blog/{segs[1]}".lower() if len(segs) >= 2 else None
        return f"hf:{segs[0]}/{segs[1]}".lower() if len(segs) >= 2 else None
    if host == "github.com":
        if len(segs) < 2 or segs[0].lower() in GITHUB_SKIP:
            return None
        repo = f"github:{segs[0]}/{segs[1].removesuffix('.git')}".lower()
        if len(segs) == 2 or segs[2] in GITHUB_REPO_VIEWS:
            return repo
        return (repo + "/" + "/".join(segs[2:5])).lower()      # issues/123 · pull/45 · releases/tag/v1 — repo와 다른 토픽
    if not segs:
        return None                                            # 사이트 첫 화면은 토픽이 아니다
    return (host + "/" + "/".join(segs)).lower()


def item_link_keys(item):
    """항목이 가리키는 대상들. 링크가 MAX_LINKS개를 넘으면 모음 글로 보고 빈 집합 (+ 표시)."""
    keys = []
    for u in [item.get("url")] + list(item.get("links") or []):
        k = link_key(u)
        if k and k not in keys:
            keys.append(k)
    if len(keys) > MAX_LINKS:
        return [], True
    return keys, False


def norm_title(t):
    return re.sub(r"\s+", " ", (t or "").strip().lower())


def crosspost_key(item):
    if item.get("source") != "reddit":
        return None
    author = (item.get("meta") or {}).get("author")
    t = norm_title(item.get("title"))
    return f"xpost:{author}:{t}" if author and t else None


# ── 인기 ──────────────────────────────────────────────────────────

def pool_values(item):
    """항목 → [(pool, 값, 순위?)]. 값이 있으면 큰 쪽이 위, 순위가 있으면 작은 쪽이 위."""
    src, s, chans = item.get("source"), item.get("signals") or {}, item.get("channels") or []
    out = []
    for k, v in s.items():
        if k.startswith("rank:") and isinstance(v, (int, float)):
            out.append((f"{src}:{k[5:]}", None, v))
    # 값 pool 이름은 `=신호`로 끝난다 — 피드 순위 pool(`rank:*`)과 섞이지 않게. HF trending은 피드 순위가 따로 있다
    if src == "hn" and s.get("points") is not None:
        out.append(("hn=points", s["points"], None))
    elif src == "github" and s.get("stars") is not None:
        for ch in chans:
            if ch in ("new-repos", "slow-growers"):
                out.append((f"github:{ch}=stars", s["stars"], None))
    elif src == "huggingface" and "daily-papers" in chans and s.get("upvotes") is not None:
        out.append(("huggingface:daily-papers=upvotes", s["upvotes"], None))
    return out


def popularity(items):
    """항목 id → {"pop": 0~1, "pool", "rank", "size", "value"} (가장 높은 백분위의 pool). 신호가 없는 항목은 빠진다.

    백분위 = 1 − (순위 − 0.5) / pool 크기 — 큰 pool의 1위가 작은 pool의 1위보다 조금 위다 (570개 중 1위 > 11개 중 1위).
    같은 값은 같은 순위(가장 좋은 순위)를 받는다.
    """
    pools = defaultdict(list)
    for it in items:
        for pool, val, rank in pool_values(it):
            pools[pool].append((it["id"], val, rank))
    best = {}
    for pool, rows in pools.items():
        n = len(rows)
        if rows[0][1] is not None:                              # 값 pool → 순위 매기기
            ordered = sorted(rows, key=lambda r: -r[1])
            ranks, prev, cur = {}, None, 0
            for i, (iid, val, _) in enumerate(ordered, 1):
                if val != prev:
                    cur, prev = i, val
                ranks[iid] = (cur, val)
        else:                                                   # 피드 순위 pool → 그대로 (빠진 번호가 있어도 pool 크기로 나눈다)
            ranks = {iid: (rank, None) for iid, _, rank in rows}
            n = max(n, max(r for _, _, r in rows))
        for iid, (rank, val) in ranks.items():
            pct = 1 - (rank - 0.5) / n
            if iid not in best or pct > best[iid]["pop"]:
                best[iid] = {"pop": round(pct, 4), "pool": pool, "rank": rank, "size": n, "value": val}
    return best


FEED_NAMES = {"top-week": "주간", "trending": "트렌딩"}


def signal_label(pool, rank, value):
    """화면에 보일 인기 한 마디 — 「HN 2,123점」 「r/LocalLLaMA 주간 3위」 「GitHub ★9,691」 「HF 논문 265표」."""
    name, _, sig = pool.partition("=")
    if sig == "points":
        return f"HN {value:,}점"
    if sig == "stars":
        return f"GitHub ★{value:,}"
    if sig == "upvotes":
        return f"HF 논문 {value:,}표"
    src, _, feed = name.partition(":")
    if src == "reddit":
        sub, _, f = feed.partition(":")
        f = f.removeprefix("flair:")
        return f"{sub} {FEED_NAMES.get(f, f)} {rank}위"
    if src == "huggingface":
        f = feed.removeprefix("task:")
        return f"HF {FEED_NAMES.get(f, f)} {rank}위"
    return f"{name} {rank}위"


# ── 묶기 ──────────────────────────────────────────────────────────

def group(items):
    """같은 링크 키 또는 같은 크로스포스트 키를 가진 항목을 한 토픽으로 (union-find). → ([[항목 인덱스]], 키 목록, 모음 글 수)"""
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    index = defaultdict(list)
    keys_of, list_posts = [], 0
    for i, it in enumerate(items):
        keys, is_list = item_link_keys(it)
        list_posts += is_list
        xk = crosspost_key(it)
        if xk:
            keys = keys + [xk]
        keys_of.append(keys)
        for k in keys:
            index[k].append(i)
    for ids in index.values():
        for j in ids[1:]:
            a, b = find(ids[0]), find(j)
            if a != b:
                parent[b] = a
    groups = defaultdict(list)
    for i in range(len(items)):
        groups[find(i)].append(i)
    return list(groups.values()), keys_of, list_posts


def build_topics(items, week=None, k=K_DEFAULT):
    """순수 계산. 스냅숏 항목 → {"topics": [순위대로], "counts": {...}}."""
    _, lane_b, _ = lanes.split_lanes(items)
    pop = popularity(lane_b)
    cands = [it for it in lane_b if it["id"] in pop]
    groups, keys_of, list_posts = group(cands)
    topics = []
    for g in groups:
        members = sorted(g, key=lambda i: -pop[cands[i]["id"]]["pop"])
        xposts = set()
        rows, sources = [], defaultdict(int)
        for i in members:
            it = cands[i]
            xk = crosspost_key(it)
            if xk and xk in xposts:
                continue                                       # 크로스포스트는 한 글로 센다
            if xk:
                xposts.add(xk)
            p = pop[it["id"]]
            sources[it["source"]] += 1
            rows.append({"id": it["id"], "source": it["source"], "title": it.get("title") or "", "url": it.get("url") or "",
                         "pop": p["pop"], "pool": p["pool"], "rank": p["rank"], "pool_size": p["size"], "value": p["value"],
                         "signal": signal_label(p["pool"], p["rank"], p["value"]), "new": it.get("new", True)})
        ids = sorted(r["id"] for r in rows)
        keys = sorted({k for i in g for k in keys_of[i] if not k.startswith("xpost:")})
        head = rows[0]
        topics.append({
            "id": "t-" + hashlib.sha1("\n".join(ids).encode()).hexdigest()[:12],
            "title": head["title"], "url": head["url"],
            "n_sources": len(sources), "sources": dict(sorted(sources.items())), "n_items": len(rows),
            "popularity": head["pop"], "links": keys, "items": rows,
        })
    topics = rank_topics(topics, k)
    counts = {"items": len(items), "lane_b": len(lane_b), "candidates": len(cands), "list_posts": list_posts,
              "topics": len(topics), "multi_item_topics": sum(t["n_items"] > 1 for t in topics),
              "cross_source_topics": sum(t["n_sources"] > 1 for t in topics)}
    return {"lane": "C", "method": METHOD, "week": week, "k": k, "multi_slots": multi_slots(k), "counts": counts,
            "topics": topics}


def multi_slots(k):
    return k // 2


def rank_topics(topics, k):
    """자리 나눔 (사용자 결정 2026-10-19): 상위 k = 여러 출처 토픽 k//2 (출처 수 → 인기) + 한 출처 토픽 (인기).

    출처 수만으로 줄 세우면 W42에서 여러 출처 토픽 31개가 상위를 다 차지해 HN 2,042점 Mistral Large 4가 35위로 밀렸다.
    한쪽이 모자라면 다른 쪽으로 채운다. 상위 k 뒤는 두 줄을 같은 방식으로 이어 붙인다 (보이지 않는 순위 — 측정용).
    각 토픽에 `section`(multi · single)과 `rank`를 붙인다.
    """
    multi = sorted((t for t in topics if t["n_sources"] > 1), key=lambda t: (-t["n_sources"], -t["popularity"], t["id"]))
    single = sorted((t for t in topics if t["n_sources"] == 1), key=lambda t: (-t["popularity"], t["id"]))
    for t in multi:
        t["section"] = "multi"
    for t in single:
        t["section"] = "single"
    m = min(multi_slots(k), len(multi))
    s = min(k - m, len(single))
    m = min(k - s, len(multi))                                  # 한 출처가 모자라면 여러 출처로 채운다
    head = multi[:m] + single[:s]
    out = head + multi[m:] + single[s:]
    for r, t in enumerate(out, 1):
        t["rank"] = r
    return out


# ── CLI ───────────────────────────────────────────────────────────

def read_items(path):
    p = Path(path)
    if p.is_dir():
        p = p / "items.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def week_of(path):
    p = Path(path)
    m = re.search(r"\d{4}-W\d{2}", str(p if p.is_dir() else p.parent))
    return m.group(0) if m else None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m pipeline.topics", description="레인 C 화제 — 같은 링크·크로스포스트로 묶어 순위")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="스냅숏 → <주차>.json")
    r.add_argument("--snapshot", required=True, help="주차 폴더(items.jsonl이 든) 또는 items.jsonl")
    r.add_argument("--out", required=True, help="결과 폴더 — `<주차>.json`을 쓴다 (앱 백엔드의 화제 폴더를 바로 줘도 된다)")
    r.add_argument("--week", help="주차 이름 (기본: 스냅숏 경로의 YYYY-Www)")
    r.add_argument("--k", type=int, default=K_DEFAULT, help=f"보일 상위 개수 (잠정 {K_DEFAULT}, 절반은 여러 출처 자리) — 파일에는 전체 순위가 들어간다")
    a = ap.parse_args(argv)

    week = a.week or week_of(a.snapshot)
    if not week:
        ap.error("스냅숏 경로에서 주차(YYYY-Www)를 못 찾았다 — --week로 준다")
    res = build_topics(read_items(a.snapshot), week=week, k=a.k)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{week}.json"
    path.write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    c = res["counts"]
    print(f"{res['week'] or '?'} · 후보 {c['candidates']} (레인 B {c['lane_b']}, 모음 글 {c['list_posts']}) → 토픽 {c['topics']} · "
          f"여러 글 {c['multi_item_topics']} · 출처를 넘은 {c['cross_source_topics']}")
    for t in res["topics"][:a.k]:
        srcs = " · ".join(f"{s} {n}" for s, n in t["sources"].items())
        print(f"{t['rank']:>3}. {t['section'][0]} [{srcs}] {t['title'][:80]}  ({t['items'][0]['signal']})")
    print(f"-> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
