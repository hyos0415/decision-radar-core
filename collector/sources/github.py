"""GitHub (doc-pipeline §4-3). 신규 repo 두 통로 + 스택 의존성의 릴리스(조회).

repo 자체가 1차 출처라 2단계 검증이 거의 공짜다. topic·언어·키워드로 미리 자르지 않는다.
토큰: GITHUB_TOKEN 환경 변수, 없으면 `gh auth token`.
"""
import json
import os
import re
import subprocess
import time
import urllib.parse

from .. import feeds
from ..item import Item, extract_links

API = "https://api.github.com"
_GH_REPO = re.compile(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")


def token():
    t = os.environ.get("GITHUB_TOKEN")
    if t:
        return t
    try:
        p = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
        return p.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def day(now, days_ago):
    return time.strftime("%Y-%m-%d", time.gmtime(now - days_ago * 86400))


def queries(cfg, now):
    n = cfg["new_repos"]
    yield "new-repos", f"created:>{day(now, n['created_within_days'])} stars:>={n['min_stars']}"
    s = cfg["slow_growers"]
    yield ("slow-growers",
           f"created:{day(now, s['created_from_days'])}..{day(now, s['created_to_days'])} stars:>={s['min_stars']}")


def parse_repos(items_json, channel):
    out = []
    for r in items_json:
        out.append(Item(
            id=f"github:{r['full_name'].lower()}", source="github", channels=[channel], title=r["full_name"],
            url=r["html_url"], created_at=r.get("created_at") or "", text=r.get("description") or "",
            signals={"stars": r.get("stargazers_count"), "forks": r.get("forks_count")},
            links=[r["homepage"]] if r.get("homepage") else [],
            meta={"language": r.get("language"), "topics": r.get("topics") or [], "pushed_at": r.get("pushed_at"),
                  "license": (r.get("license") or {}).get("spdx_id")}))
    return out


def search(cfg, fetcher, q, channel, report, headers):
    items, page = [], 1
    while True:
        url = f"{API}/search/repositories?" + urllib.parse.urlencode(
            {"q": q, "sort": "stars", "order": "desc", "per_page": 100, "page": page})
        report["requests"] += 1
        try:
            d = fetcher.get_json(url, headers)
        except Exception as e:  # noqa: BLE001
            report["failed"].append({"channel": channel, "error": str(e)[:200]})
            return items
        items += parse_repos(d.get("items", []), channel)
        if page * 100 >= min(d.get("total_count", 0), 1000):
            report.setdefault("totals", {})[channel] = d.get("total_count")
            return items
        page += 1


# ---- 스택 의존성의 릴리스 ---------------------------------------------------

_DEP_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$")


def dependencies_from_claude_md(text):
    """대상 repo CLAUDE.md의 「의존성 → component」 표에서 의존성 이름을 뽑는다."""
    names, in_table = [], False
    for line in text.splitlines():
        if line.startswith("### "):
            in_table = "의존성" in line
            continue
        if not in_table:
            continue
        m = _DEP_ROW.match(line.strip())
        if not m or set(m.group(1)) <= set("-: ") or m.group(1).startswith("의존성"):
            continue
        cell = re.sub(r"\(.*?\)", "", m.group(1))      # 괄호 설명 제거
        cell = re.sub(r"`[^`]*`", "", cell)
        for part in re.split(r"[·,]", cell):
            name = part.strip().split(" ")[0].strip()
            if name and name not in names:
                names.append(name)
    return names


# project_urls에는 후원(github.com/sponsors/…)·문서 링크도 섞여 있다 (2026-10-02 실측: pydantic·uvicorn이 sponsors로 잡힘)
_NOT_OWNER = {"sponsors", "orgs", "features", "apps", "marketplace"}
_PREFERRED_KEYS = ("source", "source code", "repository", "code", "github", "homepage")


def repo_from_pypi_info(info):
    urls = info.get("project_urls") or {}
    ordered = sorted(urls.items(), key=lambda kv: next((i for i, k in enumerate(_PREFERRED_KEYS)
                                                        if kv[0].strip().lower() == k), len(_PREFERRED_KEYS)))
    for _, u in ordered + [("home_page", info.get("home_page") or "")]:
        m = _GH_REPO.search(u or "")
        if m and m.group(1).lower() not in _NOT_OWNER:
            return f"{m.group(1)}/{m.group(2).removesuffix('.git')}"
    return None


def resolve_repo(name, overrides, fetcher, cache):
    key = name.lower()
    if key in cache:
        return cache[key]
    for k, v in overrides.items():
        if k.lower() == key:
            cache[key] = v
            return v
    repo = None
    st, body = fetcher.get(f"https://pypi.org/pypi/{urllib.parse.quote(name)}/json")
    if st == 200:
        repo = repo_from_pypi_info(json.loads(body).get("info", {}))
    cache[key] = repo
    return repo


def parse_releases(body, repo, dep):
    out = []
    for e in feeds.parse(body):
        tag = (e["link"] or "").rstrip("/").split("/")[-1]
        out.append(Item(
            id=f"github-release:{repo.lower()}@{tag}", source="github", channels=["dependency-releases"],
            title=f"{repo} {e['title']}", url=e["link"] or "", created_at=e["updated"] or e["published"],
            text=e["content"] or "", links=extract_links(e["content"] or ""),
            meta={"repo": repo, "tag": tag, "dependency": dep}))
    return out


def releases(cfg, fetcher, claude_md_texts, report, since_ts, headers):
    deps = []
    for repo_name, text in claude_md_texts.items():
        for d in dependencies_from_claude_md(text):
            deps.append((repo_name, d))
    cache, items, unresolved = {}, [], []
    seen = set()
    for target, dep in deps:
        repo = resolve_repo(dep, cfg.get("dependency_repos") or {}, fetcher, cache)
        if not repo:
            unresolved.append({"target": target, "dependency": dep})
            continue
        if repo in seen:
            continue
        seen.add(repo)
        items += fetch_releases(repo, fetcher, headers, since_ts, report, "dependency-releases", {"dependency": dep})
    report["dependencies"] = {"total": len(deps), "repos": len(seen), "unresolved": unresolved}
    return items


# releases.atom은 최근 10건뿐이다 — llama.cpp는 하루치도 안 되고 litellm은 4일 (2026-10-02 실측).
# REST API는 100건씩 지난 기간까지 넘겨 받을 수 있고 전체 이력이 있어 놓친 주도 다시 받는다.
def parse_api_releases(rels, repo, channel, meta=None):
    out = []
    for r in rels:
        tag = r.get("tag_name") or ""
        body = r.get("body") or ""
        out.append(Item(
            id=f"github-release:{repo.lower()}@{tag}", source="github", channels=[channel],
            title=f"{repo} {r.get('name') or tag}", url=r.get("html_url") or "",
            created_at=r.get("published_at") or r.get("created_at") or "", text=body, links=extract_links(body),
            meta={"repo": repo, "tag": tag, "prerelease": r.get("prerelease"), "draft": r.get("draft"), **(meta or {})}))
    return out


def fetch_releases(repo, fetcher, headers, since_ts, report, channel, meta=None, max_pages=10):
    items, page = [], 1
    while page <= max_pages:
        report["requests"] += 1
        try:
            rels = fetcher.get_json(f"{API}/repos/{repo}/releases?per_page=100&page={page}", headers)
        except Exception as e:  # noqa: BLE001
            report["failed"].append({"channel": channel, "repo": repo, "error": str(e)[:200]})
            return items
        fresh = [r for r in rels if _ts(r.get("published_at") or r.get("created_at") or "") >= since_ts]
        items += parse_api_releases(fresh, repo, channel, meta)
        # 최신순이라 창보다 오래된 게 하나라도 나오면 끝. 100건이 다 창 안이면 다음 쪽
        if len(rels) < 100 or len(fresh) < len(rels):
            return items
        page += 1
    report["failed"].append({"channel": channel, "repo": repo, "error": f"truncated at {max_pages} pages"})
    return items


def api_headers():
    tok = token()
    return {"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {tok}"} if tok else {})}, bool(tok)


def _ts(iso):
    try:
        return time.mktime(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except ValueError:
        return 0


def collect(cfg, fetcher, now=None, claude_md_texts=None):
    now = now or time.time()
    fetcher.min_interval.setdefault("api.github.com", cfg.get("min_interval_s", 2.1))
    headers, authed = api_headers()
    items, report = [], {"requests": 0, "failed": [], "authenticated": authed}
    for channel, q in queries(cfg, now):
        items += search(cfg, fetcher, q, channel, report, headers)
    if claude_md_texts:
        # 하루 겹치게 받는다 — 같은 릴리스는 id가 같아 스냅숏 단계에서 합쳐진다
        window = cfg.get("release_window_days", 8)
        items += releases(cfg, fetcher, claude_md_texts, report, now - window * 86400, headers)
    return items, report
