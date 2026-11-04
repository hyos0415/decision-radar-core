"""공식 보안 채널 (HYO-170 다, doc-pipeline §4-6) — 우리 의존성의 보안 공지(OSV) + 런타임 보안 공지(Python · Node.js).

W44 LiteLLM 1.107.0 백도어는 PyPI에만 있어 GitHub releases를 보는 레인 A가 못 봤고, http2shell · Broccoli2Shell(Node · Python RCE)은
레인 A에 런타임 출처가 없어 못 봤다. 경보 그물(pipeline/alarm.py)은 커뮤니티 글에 기대는 보조 그물이다 — 여기가 주 그물이다.
전부 공식 근거라 R2를 통과한다. 레인 A 통로: `security-advisories`(OSV) · `runtime-security:<이름>`(런타임 피드).

- OSV: 대상 repo manifest(requirements · pyproject · package.json — 관문 · basis와 같은 파서)의 PyPI · npm 이름을 버전 없이 묻는다.
  그 주 공지 = 창 안에 **발행된** 것 + 악성 패키지 기록(`MAL-`)은 창 안에 **수정된** 것도 (사용자 결정 2026-11-04 — MAL-2026-2144는
  3/24 발행 기록에 10/29 litellm 1.107.0이 덧붙었다. 일반 GHSA의 옛 공지 재수정은 내용이 그대로라 뺀다).
  수정으로 들어온 MAL은 id에 수정일을 붙여 새 항목으로 — 같은 기록에 버전이 또 붙으면 다시 올라온다.
  공지 하나 = 항목 하나 (같은 취약점의 별칭 GHSA · PYSEC은 하나로). 등급 파일에서 retired인 기술은 묻지 않는다.
  공지마다 우리 버전으로 다시 물어 `meta.affects_ours`를 단다 (mark_ours) — 해당 없는 공지는 목록에만 (관문 쪽 policy.quiet_advisory).
- 런타임: pool.yaml `security.runtimes`의 피드. 본문이 빈 피드(Node.js)는 창 안 글만 페이지를 받아 본문으로 쓴다.
  `dependency`(관문이 그 기술 하나로만 판정 — gate.resolve_technologies)는 설정에서: Node.js 공지는 electron(우리 Node는 Electron 안 — 사용자 결정 2026-11-04).
  `dependency_when`(정규식)이 있으면 제목 · 본문 · 링크에 맞을 때만 붙인다 (Python 목록의 certifi · pip 공지는 CPython이 아니다 → 제목 이름으로 관문이 찾는다).
"""
import email.utils
import json
import os
import re
import time
import urllib.parse
from pathlib import Path

from .. import feeds
from ..item import Item, extract_links, html_to_text

OSV_API = "https://api.osv.dev/v1"
CHANNEL_OSV = "security-advisories"
ECOSYSTEMS = ("PyPI", "npm")
_ALIAS_ORDER = ("GHSA-", "MAL-", "CVE-")      # 별칭 중 무엇을 대표로 — 앞일수록 먼저 (PYSEC 등은 뒤)


def _ts(iso):
    try:
        return time.mktime(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except (ValueError, TypeError):
        return 0


def _rfc822_ts(s):
    try:
        return email.utils.parsedate_to_datetime(s).timestamp()
    except (TypeError, ValueError, IndexError):
        return _ts(s)


def _norm(name):
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


def retired_names(tiers_path):
    """등급 파일(tiers.json) overrides에서 retired인 기술 → (정규화 이름 집합, 이유 | None). 파일이 없으면 빈 집합 + 이유."""
    try:
        d = json.loads(Path(tiers_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as e:
        return set(), f"등급 파일을 못 읽었다 ({type(e).__name__}: {tiers_path}) — retired 거르기 없이 묻는다"
    return {_norm(k) for k, o in (d.get("overrides") or {}).items() if isinstance(o, dict) and o.get("retired")}, None


def default_tiers_path():
    """collector.weekly.paths와 같은 해석: RADAR_TIERS > <이 레포 부모>/radar-snapshots/tiers.json."""
    return Path(os.environ.get("RADAR_TIERS") or Path(__file__).resolve().parents[3] / "radar-snapshots" / "tiers.json")


def stack_packages(target_roots, target_excludes=None):
    """대상 repo들 → [(생태계, 이름, [repo])] (정규화 이름으로 중복 제거, 처음 본 표기). 관문 · basis와 같은 manifest 파서."""
    from pipeline.importance.signals import read_package_json, read_python_manifests
    out = {}
    for repo, root in (target_roots or {}).items():
        ex = (target_excludes or {}).get(repo) or ()
        for eco, sig in (("PyPI", read_python_manifests(root, ex)), ("npm", read_package_json(root, ex))):
            for s in sig.values():
                cur = out.setdefault((eco, _norm(s["name"])), (eco, s["name"], []))
                if repo not in cur[2]:
                    cur[2].append(repo)
    return sorted(out.values(), key=lambda x: (x[0], _norm(x[1])))


def query_batch(fetcher, packages, report, batch_size=500, max_pages=5):
    """[(eco, name)] 또는 [(eco, name, version)] → {키: [{id, modified}]}. 버전을 주면 그 버전에 해당하는 공지만 (OSV가 범위를 판정한다).
    한 패키지의 공지가 많으면 OSV가 page_token을 준다 — 그 패키지만 다시 묻는다."""
    out = {p: [] for p in packages}
    todo = [(p, None) for p in packages]
    for _ in range(max_pages):
        nxt = []
        for i in range(0, len(todo), batch_size):
            chunk = todo[i:i + batch_size]
            qs = [{"package": {"name": p[1], "ecosystem": p[0]}, **({"version": p[2]} if len(p) > 2 else {}), **({"page_token": t} if t else {})}
                  for p, t in chunk]
            report["requests"] += 1
            try:
                d = fetcher.post_json(f"{OSV_API}/querybatch", {"queries": qs})
            except Exception as e:  # noqa: BLE001 — 실패는 기록하고 다음 묶음으로
                report["failed"].append({"channel": CHANNEL_OSV, "error": f"querybatch: {str(e)[:200]}"})
                continue
            for (p, _t), res in zip(chunk, d.get("results") or []):
                out[p] += res.get("vulns") or []
                if res.get("next_page_token"):
                    nxt.append((p, res["next_page_token"]))
        if not nxt:
            return out
        todo = nxt
    report["failed"].append({"channel": CHANNEL_OSV, "error": f"page_token이 {max_pages}쪽을 넘었다: {[p for p, _ in todo][:5]}"})
    return out


def _rank(vid):
    """별칭 중 무엇을 남기나 — GHSA > MAL > CVE > 나머지(PYSEC 등). 같으면 사전순."""
    return next((i for i, p in enumerate(_ALIAS_ORDER) if vid.startswith(p)), len(_ALIAS_ORDER)), vid


def _affected_line(vuln, name, eco):
    """이 패키지에 대한 영향 범위 한 줄 + 고친 버전들. 버전 목록이 길면 앞뒤만."""
    fixed, parts = [], []
    for a in vuln.get("affected") or []:
        pkg = a.get("package") or {}
        if pkg.get("ecosystem") != eco or _norm(pkg.get("name") or "") != _norm(name):
            continue
        for r in a.get("ranges") or []:
            for ev in r.get("events") or []:
                for k in ("introduced", "fixed", "last_affected"):
                    if k in ev:
                        parts.append(f"{k} {ev[k]}")
                        if k == "fixed":
                            fixed.append(ev[k])
        vs = a.get("versions") or []
        if vs:
            parts.append("versions " + (", ".join(vs) if len(vs) <= 12 else ", ".join(vs[:5]) + f", … ({len(vs)}개) …, " + ", ".join(vs[-3:])))
    return "; ".join(parts), fixed


def advisory_item(vuln, name, eco, repos, updated=False):
    vid = vuln["id"]
    summary = (vuln.get("summary") or "").strip() or (vuln.get("details") or "").strip().split("\n")[0][:160]
    affected, fixed = _affected_line(vuln, name, eco)
    head = f"Updated {vuln.get('modified', '')[:10]} (first published {vuln.get('published', '')[:10]}).\n\n" if updated else ""
    text = head + (vuln.get("details") or summary) + (f"\n\nAffected {name} ({eco}): {affected}" if affected else "")
    refs = [r.get("url") for r in vuln.get("references") or [] if r.get("url")]
    return Item(
        id=f"osv:{vid}" + (f"@{vuln.get('modified', '')[:10]}" if updated else ""), source="osv", channels=[CHANNEL_OSV],
        title=f"{name} ({eco}) security advisory {vid}: {summary}", url=f"https://osv.dev/vulnerability/{vid}",
        created_at=(vuln.get("modified") if updated else vuln.get("published")) or "", text=text,
        links=list(dict.fromkeys(refs + extract_links(vuln.get("details") or ""))),
        meta={"kind": "advisory", "dependency": name, "ecosystem": eco, "osv_id": vid, "aliases": vuln.get("aliases") or [],
              "published": vuln.get("published"), "modified": vuln.get("modified"), "updated": updated, "fixed": fixed, "targets": repos})


def collect_osv(fetcher, packages, since_ts, report):
    """packages: [(eco, name, repos)] → 그 주 공지 항목들. report["osv"]에 건수."""
    hits = query_batch(fetcher, [(e, n) for e, n, _ in packages], report)
    repos_of = {(e, n): r for e, n, r in packages}
    # 창 안에 수정된 것만 본문을 받는다 (발행 ≤ 수정이라 창 안에 발행된 것도 여기 든다)
    want = {}
    for p, vs in hits.items():
        for v in vs:
            if _ts(v.get("modified") or "") >= since_ts:
                want.setdefault(v["id"], []).append(p)
    items, seen, counts = [], set(), {"advisories": sum(len(v) for v in hits.values()), "modified_in_window": len(want),
                                      "published": 0, "malicious_updated": 0, "old_modified_skipped": 0, "alias_merged": 0}
    for vid in sorted(want, key=_rank):      # 대표로 남길 별칭(GHSA)을 먼저 — 뒤에 오는 PYSEC 같은 별칭은 합쳐진다
        report["requests"] += 1
        try:
            vuln = fetcher.get_json(f"{OSV_API}/vulns/{urllib.parse.quote(vid)}")
        except Exception as e:  # noqa: BLE001
            report["failed"].append({"channel": CHANNEL_OSV, "id": vid, "error": str(e)[:200]})
            continue
        published = _ts(vuln.get("published") or "") >= since_ts
        updated = not published and vid.startswith("MAL-")
        if not (published or updated):
            counts["old_modified_skipped"] += 1
            continue
        ids = {vid, *(vuln.get("aliases") or [])}
        for eco, name in want[vid]:
            keys = {(x, eco, _norm(name), updated) for x in ids}
            if keys & seen:
                counts["alias_merged"] += 1
                continue
            seen |= keys
            items.append(advisory_item(vuln, name, eco, repos_of[(eco, name)], updated))
            counts["malicious_updated" if updated else "published"] += 1
    report["osv"] = counts
    return items


def our_versions(target_roots, target_excludes=None):
    """대상 repo들 → {정규화 이름: [우리 버전]} — 관문 · scope와 같은 읽기(signals.read_repo_versions). 범위(`^` · `>=`)는 하한이다."""
    from pipeline.importance.signals import read_repo_versions
    rows = {}
    for repo, root in (target_roots or {}).items():
        read_repo_versions(root, (target_excludes or {}).get(repo) or (), repo, rows)
    return {k: list(dict.fromkeys(str(r["version"]) for r in rs)) for k, rs in rows.items()}


def mark_ours(fetcher, items, versions, report):
    """OSV 공지마다 우리 버전이 해당되나 — 우리 버전으로 다시 묻는다 (리뷰 2026-11-04, 사용자 나안: W44 창 OSV 20건 중 13건이 우리 버전에서
    이미 고쳐졌거나 다른 줄이었다 — electron 9 · mcp 3 · vite 6.x 1). meta.affects_ours = True | False | None(버전 모름 · 묻기 실패 — 안전 쪽).
    관문은 False인 공지(악성 기록 MAL- 빼고)를 목록에만 두고 detect로 보내지 않는다 (policy.quiet_advisory).
    범위 하한으로 물으니 실제 깔린 게 더 새 버전이면 「해당」으로 나올 수 있다 — 안전 쪽 오판."""
    keys = sorted({(it.meta["ecosystem"], it.meta["dependency"], v) for it in items for v in versions.get(_norm(it.meta["dependency"]), [])})
    before = len(report["failed"])
    hits = query_batch(fetcher, keys, report) if keys else {}
    broken = len(report["failed"]) > before
    counts = {"yes": 0, "no": 0, "unknown": 0}
    for it in items:
        dep, eco = it.meta["dependency"], it.meta["ecosystem"]
        vs = versions.get(_norm(dep), [])
        ids = {it.meta["osv_id"], *(it.meta.get("aliases") or [])}
        hit = [v for v in vs if ids & {x["id"] for x in hits.get((eco, dep, v), [])}]
        ours = None if (not vs or broken) else bool(hit)
        it.meta["our_versions"], it.meta["affects_ours"] = vs, ours
        counts["unknown" if ours is None else "yes" if ours else "no"] += 1
        it.text += f"\n\nOur {dep} versions: {', '.join(vs) or 'unknown'} — " + (
            "unknown whether affected" if ours is None else f"affected ({', '.join(hit)})" if ours else "not affected")
    report.setdefault("osv", {})["affects_ours"] = counts
    return items


# ---- 런타임 보안 공지 ---------------------------------------------------------

def runtime_item(cfg, entry, body_text=None):
    title = entry["title"]
    for p in cfg.get("strip_title") or []:
        title = title.replace(p, "").strip()
    link = entry["link"] or ""
    text = body_text if body_text is not None else html_to_text(entry["content"] or "")
    meta = {"kind": "advisory", "runtime": cfg["name"]}
    dep = cfg.get("dependency")
    when = cfg.get("dependency_when")
    links = extract_links(entry["content"] or "")
    if dep and (not when or re.search(when, "\n".join([title, text, *links]))):
        meta["dependency"] = dep
    slug = link.rstrip("/").split("/")[-1] or entry["id"]
    ts = _rfc822_ts(entry["published"] or entry["updated"] or "")
    # 런타임 이름 머리말은 그 런타임으로 판정할 때만 — 머리말이 있으면 관문이 제목에서 python을 찾아 certifi 공지도 python으로 판정한다
    return Item(id=f"runtime-security:{cfg['name']}:{slug}", source=cfg["name"], channels=[f"runtime-security:{cfg['name']}"],
                title=f"{cfg['label']} security: {title}" if "dependency" in meta else title, url=link,
                created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)) if ts else "",    # RSS 날짜(RFC 822) → ISO 8601 (Item 형식)
                text=text, links=links, meta=meta)


def collect_runtime(cfg, fetcher, since_ts, report):
    ch = f"runtime-security:{cfg['name']}"
    st, body = fetcher.get(cfg["feed"])
    report["requests"] += 1
    if st != 200:
        report["failed"].append({"channel": ch, "error": f"{st} {cfg['feed']}"})
        return []
    try:
        entries = feeds.parse(body)
    except Exception as e:  # noqa: BLE001 — 피드 모양이 바뀌었다
        report["failed"].append({"channel": ch, "error": f"피드 파싱 실패: {type(e).__name__}: {str(e)[:150]}"})
        return []
    items = []
    for e in entries:
        if _rfc822_ts(e["published"] or e["updated"] or "") < since_ts:
            continue
        body_text = None
        if cfg.get("fetch_page") and e["link"]:
            report["requests"] += 1
            pst, page = fetcher.get(e["link"])
            if pst == 200:
                body_text = html_to_text(page.decode("utf-8", "replace"))
            else:       # 본문 없이 제목만이라도 남긴다
                report["failed"].append({"channel": ch, "url": e["link"], "error": f"page {pst}"})
        items.append(runtime_item(cfg, e, body_text))
    report.setdefault("runtimes", {})[cfg["name"]] = {"entries": len(entries), "in_window": len(items)}
    return items


def collect(cfg, fetcher, now=None, target_roots=None, target_excludes=None, tiers_path=None):
    now = now or time.time()
    since = now - cfg.get("window_days", 8) * 86400
    fetcher.min_interval.setdefault("api.osv.dev", cfg.get("osv_min_interval_s", 0.3))
    report = {"requests": 0, "failed": [], "window_days": cfg.get("window_days", 8)}
    items = []
    pkgs = stack_packages(target_roots, target_excludes)
    retired, why = retired_names(tiers_path or default_tiers_path())
    skip = [n for e, n, _ in pkgs if _norm(n) in retired]
    pkgs = [p for p in pkgs if _norm(p[1]) not in retired]
    by_eco = {e: sum(1 for p in pkgs if p[0] == e) for e in ECOSYSTEMS}
    report["packages"] = {"total": len(pkgs), "by_ecosystem": by_eco, "retired_skipped": skip, **({"tiers_note": why} if why else {})}
    if pkgs:
        items += mark_ours(fetcher, collect_osv(fetcher, pkgs, since, report), our_versions(target_roots, target_excludes), report)
    for rt in cfg.get("runtimes") or []:
        try:
            items += collect_runtime(rt, fetcher, since, report)
        except Exception as e:  # noqa: BLE001 — 한 피드가 죽어도 나머지는 돈다
            report["failed"].append({"channel": f"runtime-security:{rt.get('name')}", "error": f"{type(e).__name__}: {str(e)[:200]}"})
    return items, report
