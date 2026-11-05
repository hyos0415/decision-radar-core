"""벤더 피드 (doc-pipeline §4-5, 혼합 방식).

- 어느 벤더를 볼지 = 규칙: 대상 repo CLAUDE.md 「외부 모델·서비스」 표 ∪ 재검토 조건이 살아 있는 ADR의 technology
- 벤더마다 어느 경로를 볼지 = vendors.yaml (손으로 관리)
- 규칙이 뽑은 이름이 어느 벤더에도 안 걸리면 「경로 없음」, 일부러 뺀 것이면 「제외」로 보고한다
- page는 본문 텍스트(블록마다 한 줄, 전문)와 해시만 낸다. 지난 주와의 비교는 collector/snapshot.py(HYO-72)가 한다
"""
import re
import time

from .. import feeds
from ..item import Item, extract_links, html_to_text, text_hash
from . import github

_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|")


def services_from_claude_md(text):
    """「외부 모델·서비스」 표의 첫 열."""
    out, in_table = [], False
    for line in text.splitlines():
        if line.startswith("### "):
            in_table = "외부 모델" in line
            continue
        if not in_table:
            continue
        m = _ROW.match(line.strip())
        if m and not set(m.group(1)) <= set("-: ") and not m.group(1).startswith("모델·서비스"):
            out.append(m.group(1))
    return out


def select(names, vendors_cfg):
    """규칙이 뽑은 이름 → (볼 벤더, 경로 없음, 제외·벤더 아님). 같은 이름은 한 번만 센다."""
    picked, missing, excluded = {}, [], []
    seen = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        low = name.lower()
        nv = [e for e in vendors_cfg.get("not_vendor", []) if any(k.lower() in low for k in e["match"])]
        if nv:
            excluded.append({"name": name, "why": nv[0]["why"], "kind": "not_vendor"})
            continue
        hit = [v for v, spec in vendors_cfg["vendors"].items() if any(k in low for k in spec["match"])]
        if hit:
            for v in hit:
                picked.setdefault(v, []).append(name)
            continue
        ex = [e for e in vendors_cfg.get("excluded", []) if any(k.lower() in low for k in e["match"])]
        if ex:
            excluded.append({"name": name, "why": ex[0]["why"], "kind": "excluded"})
        else:
            missing.append(name)
    return picked, missing, excluded


def parse_feed(body, vendor, url, include=None, since_ts=0):
    out = []
    pat = re.compile(include) if include else None
    for e in feeds.parse(body):
        blob = f"{e['title']} {e['content']}"
        if pat and not pat.search(blob):
            continue
        when = e["updated"] or e["published"]
        out.append(Item(
            id=f"vendor:{vendor}:{e['id'] or e['link']}", source="vendor", channels=[f"vendor:{vendor}"],
            title=e["title"], url=e["link"] or url, created_at=when, text=html_to_text(e["content"] or ""),
            links=extract_links(e["content"] or ""), meta={"vendor": vendor, "feed": url}))
    return out


def parse_page(body, vendor, url, now, fmt="html"):
    raw = body.decode("utf-8", "replace")
    text = raw.strip() if fmt == "markdown" else html_to_text(raw, lines=True)
    return Item(id=f"vendor-page:{vendor}:{url}", source="vendor", channels=[f"vendor:{vendor}"],
                title=f"[{vendor}] {url}", url=url, created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                text=text, links=extract_links(raw) if fmt == "markdown" else [],
                meta={"vendor": vendor, "kind": "page", "format": fmt, "text_hash": text_hash(text), "text_chars": len(text)})


def collect(cfg, fetcher, now=None, names=None, only=None):
    now = now or time.time()
    picked, missing, excluded = select(names or [], cfg)
    if only:
        picked = {v: n for v, n in picked.items() if v in only}
    items, report = [], {"requests": 0, "failed": [], "picked": picked, "no_route": missing, "excluded": excluded}
    headers = None
    window = cfg.get("release_window_days", 8)
    for vendor in picked:
        for f in cfg["vendors"][vendor]["feeds"]:
            if f["kind"] == "github_releases":
                if headers is None:
                    fetcher.min_interval.setdefault("api.github.com", 2.1)
                    headers, _ = github.api_headers()
                for it in github.fetch_releases(f["repo"], fetcher, headers, now - window * 86400, report,
                                                f"vendor:{vendor}", {"vendor": vendor}):
                    it.source = "vendor"
                    items.append(it)
                continue
            report["requests"] += 1
            st, body = fetcher.get(f["url"])
            if st != 200:
                report["failed"].append({"vendor": vendor, "url": f["url"], "status": st})
                continue
            if f["kind"] == "page":
                fmt = f.get("format", "html")
                it = parse_page(body, vendor, f["url"], now, fmt)
                if f.get("replaces"):  # 주소를 옮긴 페이지 — 얼릴 때 옛 페이지를 바뀜으로 두고 빈 주를 메운다 (HYO-172)
                    it.meta["replaces"] = f["replaces"]
                # 자바스크립트로 그리는 HTML 페이지는 본문이 거의 없다 → 비교 불가. markdown 원문은 짧아도 정상
                if fmt == "html" and it.meta["text_chars"] < 2000:
                    report["failed"].append({"vendor": vendor, "url": f["url"], "status": "page text too short"})
                items.append(it)
            else:
                items += parse_feed(body, vendor, f["url"], f.get("include"))
    return items, report
