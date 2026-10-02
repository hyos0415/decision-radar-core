"""Reddit RSS (doc-pipeline §4-1). 공식 Data API는 승인제라 RSS를 쓴다 — 어댑터만 바꾸면 API로 갈아탈 수 있게.

제약 (probe 2026-10-02): 요청 간격 60초 · 피드에 점수 없음(순위만) · `t`는 굴러가는 기간(지난 주를 다시 못 받는다)
· flair 검색은 부분 일치 → 글 ID로 합친다 (item.merge).
"""
import urllib.parse

from .. import feeds
from ..item import Item, extract_links

HOST = "www.reddit.com"


def urls(cfg):
    lim = cfg.get("limit", 100)
    for sub in cfg["subreddits"]:
        name = sub["name"]
        yield f"r/{name}:top-week", f"https://{HOST}/r/{name}/top.rss?t=week&limit={lim}", name, None
        for fl in sub.get("flairs") or []:
            q = urllib.parse.quote(f'flair:"{fl}"')
            yield (f"r/{name}:flair:{fl}",
                   f"https://{HOST}/r/{name}/search.rss?q={q}&restrict_sr=1&sort=top&t=week&limit={lim}", name, fl)


def parse(body, channel, sub, flair):
    items = []
    for rank, e in enumerate(feeds.parse(body), 1):
        native = (e["id"] or "").split("/")[-1] or e["link"]
        content = e["content"] or ""
        items.append(Item(
            id=f"reddit:{native}", source="reddit", channels=[channel], title=e["title"], url=e["link"] or "",
            created_at=e["published"] or e["updated"], text=content,
            signals={f"rank:{channel}": rank}, links=extract_links(content),
            meta={"subreddit": sub, "author": e["author"], **({"flair_query": flair} if flair else {})}))
    return items


def collect(cfg, fetcher, now=None):
    fetcher.min_interval.setdefault(HOST, cfg.get("min_interval_s", 61))
    items, report = [], {"requests": 0, "failed": []}
    for channel, url, sub, flair in urls(cfg):
        report["requests"] += 1
        st, body = fetcher.get(url)
        if st != 200:
            report["failed"].append({"channel": channel, "status": st})
            continue
        items += parse(body, channel, sub, flair)
    return items, report
