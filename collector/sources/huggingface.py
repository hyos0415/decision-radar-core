"""Hugging Face (doc-pipeline §4-4). 전체 trending + 태스크별 trending + Daily Papers.

trending은 그 순간의 순위라 지난 주를 다시 받을 수 없다 — 처음 본 날짜는 스냅숏 단계(HYO-72)가 기록한다.
모델 나이·trendingScore·upvote는 필터 특징으로 넘긴다 (수집에서 자르지 않는다).
"""
import time
import urllib.parse

from ..item import Item

API = "https://huggingface.co/api"


def parse_models(models, channel):
    out = []
    for rank, m in enumerate(models, 1):
        mid = m.get("id") or m.get("modelId")
        out.append(Item(
            id=f"hf-model:{mid}", source="huggingface", channels=[channel], title=mid,
            url=f"https://huggingface.co/{mid}", created_at=m.get("createdAt") or "",
            signals={"trending_score": m.get("trendingScore"), "likes": m.get("likes"),
                     "downloads": m.get("downloads"), f"rank:{channel}": rank},
            meta={"pipeline_tag": m.get("pipeline_tag"), "library": m.get("library_name"),
                  "tags": (m.get("tags") or [])[:30]}))
    return out


def parse_papers(papers, date):
    out = []
    for p in papers:
        paper = p.get("paper") or {}
        pid = paper.get("id") or p.get("id")
        out.append(Item(
            id=f"hf-paper:{pid}", source="huggingface", channels=["daily-papers"], title=p.get("title") or paper.get("title") or "",
            url=f"https://huggingface.co/papers/{pid}", created_at=p.get("publishedAt") or paper.get("publishedAt") or "",
            text=paper.get("summary") or p.get("summary") or "",
            signals={"upvotes": paper.get("upvotes"), "comments": p.get("numComments")},
            links=[f"https://arxiv.org/abs/{pid}"] if pid else [],
            meta={"daily_date": date, "authors": [a.get("name") for a in (paper.get("authors") or [])][:10]}))
    return out


def collect(cfg, fetcher, now=None):
    now = now or time.time()
    items, report = [], {"requests": 0, "failed": []}

    def get(url, what):
        report["requests"] += 1
        try:
            return fetcher.get_json(url)
        except Exception as e:  # noqa: BLE001
            report["failed"].append({"what": what, "error": str(e)[:200]})
            return []

    q = {"sort": "trendingScore", "limit": cfg.get("trending_limit", 100)}
    items += parse_models(get(f"{API}/models?{urllib.parse.urlencode(q)}", "trending"), "trending")
    for task in cfg.get("tasks", []):
        q = {"sort": "trendingScore", "limit": cfg.get("task_limit", 20), "pipeline_tag": task}
        items += parse_models(get(f"{API}/models?{urllib.parse.urlencode(q)}", task), f"task:{task}")
    for i in range(1, cfg.get("daily_papers_days", 7) + 1):
        d = time.strftime("%Y-%m-%d", time.gmtime(now - i * 86400))
        items += parse_papers(get(f"{API}/daily_papers?date={d}", f"papers:{d}"), d)
    return items, report
