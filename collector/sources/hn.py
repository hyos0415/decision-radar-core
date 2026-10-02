"""Hacker News — Algolia (doc-pipeline §4-2). points ≥ 25, 2일 지연, 「2~9일 전에 올라온 글」.

Algolia는 질의당 1,000건이 상한이라 하루씩 잘라 받는다. 지난 기간을 다시 받을 수 있다(되돌릴 수 있음).
"""
import time
import urllib.parse

from ..item import Item, extract_links

API = "https://hn.algolia.com/api/v1/search_by_date"


def windows(cfg, now):
    end = int(now) - cfg.get("lag_days", 2) * 86400
    start = end - cfg.get("window_days", 7) * 86400
    t = start
    while t < end:
        yield t, min(t + 86400, end)
        t += 86400


def parse(hits, channel="stories"):
    items = []
    for h in hits:
        url = h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}"
        text = h.get("story_text") or ""
        items.append(Item(
            id=f"hn:{h['objectID']}", source="hn", channels=[channel], title=h.get("title") or "", url=url,
            created_at=h.get("created_at") or "", text=text,
            signals={"points": h.get("points"), "comments": h.get("num_comments")},
            links=([h["url"]] if h.get("url") else []) + [u for u in extract_links(text) if u != h.get("url")],
            meta={"author": h.get("author"), "hn_url": f"https://news.ycombinator.com/item?id={h['objectID']}",
                  "show_hn": (h.get("title") or "").startswith("Show HN")}))
    return items


def collect(cfg, fetcher, now=None):
    now = now or time.time()
    items, report = [], {"requests": 0, "failed": [], "windows": []}
    for lo, hi in windows(cfg, now):
        q = {"tags": "story", "hitsPerPage": 1000,
             "numericFilters": f"created_at_i>={lo},created_at_i<{hi},points>={cfg.get('min_points', 25)}"}
        report["requests"] += 1
        try:
            d = fetcher.get_json(API + "?" + urllib.parse.urlencode(q))
        except Exception as e:  # noqa: BLE001 — 한 구간 실패가 전체를 멈추지 않게
            report["failed"].append({"window": [lo, hi], "error": str(e)[:200]})
            continue
        if d.get("nbHits", 0) > len(d.get("hits", [])):
            report["failed"].append({"window": [lo, hi], "error": f"truncated {d.get('nbHits')} > 1000"})
        report["windows"].append({"from": lo, "to": hi, "hits": len(d.get("hits", []))})
        items += parse(d.get("hits", []))
    return items, report
