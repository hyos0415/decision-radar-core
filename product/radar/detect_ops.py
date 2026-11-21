"""detect의 코드 쪽 — 모델이 아니라 그래프의 노드가 부른다. 안전 조항은 여기와 radar.py가 집행한다.

도구 호출은 스킬 transcript와 같은 모양으로 ctx.events에 남는다 (ops.radar / ops.read_repo_file이 기록한다).
"""

import json
import os
import re

from common import ops
from common.core import radar

from . import hitl


def snapshot_feed(c):
    """스냅숏 모드의 소식 수집 — 웹 대신 radar.py snapshot feed (HYO-45)."""
    c.calls += 1
    return radar(c, "snapshot", "feed")


def drop_rejected(c, feed):
    """사람이 전에 거부한 소식을 모델에 넘기기 전에 뺀다 (HYO-142) → (남은 소식, excluded 줄). 안전 조항처럼 코드가 집행한다 (규율 1).
    단위는 피드 항목 id(릴리스 `repo@tag`, 벤더 페이지는 변경마다 다른 id) — 같은 기술의 새 소식은 막지 않는다.
    RADAR_DETECT_RESURFACE=1이면 끈다 (데스크톱 스텁 모드 — 같은 초안을 다시 띄워 알림을 시험한다)."""
    if c.env.get("RADAR_DETECT_RESURFACE") == "1":
        return feed, []
    seen = hitl.rejected_sources(c)
    keep, out = [], []
    for it in feed:
        if it.get("id") in seen:
            out.append({"url": it.get("url") or "", "reason": f"이전에 거부한 소식 — {seen[it['id']]} (다시 올리지 않는다)"})
        else:
            keep.append(it)
    return keep, out


def drop_applied(c, feed):
    """관문 판정 뒤 대상 레포에 이미 반영된 의존성 릴리스를 모델에 넘기기 전에 뺀다 (HYO-162) → (남은 소식, excluded 줄). 코드가 집행한다 (규율 1).
    관문 기준 = 입력 폴더의 basis.json(수집 PC가 판정한 커밋·버전), 지금 기준 = env RADAR_BASIS_NOW(앱이 실행 직전에 원격 HEAD로 만든 것).
    둘 중 하나라도 없으면 아무것도 빼지 않는다 — 예전 입력·eval 픽스처는 그대로 돈다."""
    snap, now_path = c.env.get("RADAR_SNAPSHOT_DIR"), c.env.get("RADAR_BASIS_NOW")
    if not snap or not now_path or not os.path.exists(os.path.join(snap, "basis.json")):
        return feed, []
    from pipeline import basis  # 늦게 — 판정 기준이 있을 때만 수집 파이프라인을 부른다
    old, new = basis.load(os.path.join(snap, basis.BASIS)), basis.load(now_path)
    if not old or not new:
        return feed, []
    return basis.applied(feed, old, new)


MANIFESTS = ("requirements.txt", "package.json")


def read_stack(c):
    """대상 repo마다 CLAUDE.md(스택·의존성→component 매핑)와 manifest를 읽어 한 덩어리 글로 만든다.

    읽기는 ops.read_repo_file이 한다 — repo 밖 경로를 막고 Read 호출을 기록한다.
    """
    parts = []
    for repo in sorted(c.targets):
        parts.append(f"## repo: {repo}")
        for name in ("CLAUDE.md", *MANIFESTS):
            out = json.loads(ops.read_repo_file(c, repo, name))
            if out.get("ok"):
                parts.append(f"### {name}\n{out['body'].rstrip()}")
            elif name == "CLAUDE.md":
                parts.append("### CLAUDE.md\n(없음 — manifest와 import 위치로 추정해야 한다)")
    return "\n\n".join(parts)


def release_of(cand, feed):
    """의존성 릴리스 후보의 릴리스 표기 = 근거 소식의 제목 (radar.py match --release가 버전 토막을 뽑는다). 의존성 밖이면 None."""
    if not cand.get("dependency"):
        return None
    urls = set(cand.get("source_urls") or [])
    return next((it.get("title") for it in feed or [] if it.get("url") in urls and it.get("title")), None)


def screen(c, candidates, feed=None):
    """후보마다 R10·R14를 코드로 집행한다.

    R10  affected가 비었거나 대상 repo에 없는 repo를 가리키면 버린다 → dropped
    R14  radar.py match가 overlap(기술 같고 component 겹침)을 주면 초안을 만들지 않는다 → skipped.
         의존성 릴리스 대응형 ADR은 같은 릴리스일 때만 overlap — 릴리스 표기는 소식 제목 (R14 변경 2026-10-23, HYO-136)
    남은 후보는 related(같은 기술이거나 같은 component의 ADR)와 함께 돌려준다 — 초안 본문에 적는다.
    """
    kept, skipped, dropped = [], [], []
    for i, cand in enumerate(candidates):
        aff = cand["affected"]
        bad = [a for a in aff if a["repo"] not in c.targets]
        if not aff or bad:
            dropped.append({"technology": cand["technology"], "source_urls": cand.get("source_urls") or [],
                            "reason": "R10: affected가 비었다" if not aff else f"R10: 대상 repo에 없는 repo {sorted({a['repo'] for a in bad})}"})
            continue
        overlap, related = [], {}
        rel = release_of(cand, feed)
        for a in aff:
            m = radar(c, "match", c.adr_dir, "--technology", cand["technology"], "--repo", a["repo"], "--component", a["component"],
                      *(("--release", rel) if rel else ()))
            overlap += [x for x in m["overlap"] if x not in overlap]
            for r in m["related"]:
                related.setdefault(r["id"], r)
        if overlap:
            skipped += [{"technology": cand["technology"], "adr": x, "source_urls": cand.get("source_urls") or []} for x in overlap]
            continue
        kept.append({"idx": i, "candidate": cand, "related": sorted(related.values(), key=lambda r: r["id"])})
    return kept, skipped, dropped


def grep_log(c):
    """이번 실행에서 실제로 돈 repo_grep → {패턴: 기록}. 같은 패턴을 여러 번 돌렸으면 마지막 것."""
    out = {}
    for e in c.events:
        if e.get("type") != "assistant":
            continue
        t = e["message"]["content"][0]
        if t.get("name") == "Grep" and t["input"].get("ok"):
            out[t["input"]["pattern"]] = t["input"]
    return out


def _why(c):
    """입력 폴더의 gate.json(url → 왜 왔나 — detect_feed가 쓴다). 없으면 {} — 예전 입력 · eval 픽스처는 그대로 돈다."""
    snap = c.env.get("RADAR_SNAPSHOT_DIR")
    path = os.path.join(snap, "gate.json") if snap else None
    if not path or not os.path.exists(path):
        return {}
    try:
        gate = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {url: v for url, v in gate.items() if isinstance(v, dict)}


def security_urls(c):
    """관문이 security로 남긴 소식의 url들."""
    return {url for url, v in _why(c).items() if any(k.get("change") == "security" for k in v.get("kept_by") or [])}


def slot_reasons(c):
    """자리 질의가 후보로 고른 소식 → {url: [{slot, name, repo, role, fit, reason}]} (HYO-205). 대상 repo가 적힌 자리만 —
    detect는 「어디에 쓰나」를 다시 판단하지 않고 이 자리의 repo · 역할로 쓴다."""
    return {url: hits for url, v in _why(c).items()
            if (hits := [h for h in v.get("slots") or [] if h.get("repo") in c.targets])}


# 「우리 코드가 안 쓴다」는 뜻의 말 — unused_claim을 안 붙이고 버린 보안 소식을 잡는 뒷문 (W47: 「HTTP/2 서버를 열지 않는다」가 표시 없이 빠졌다)
_UNUSED_WORDS = re.compile(r"미사용|안 쓴|안 씀|쓰지 않|쓰는 곳(이)? 없|사용(하지| 안| 코드 없| 흔적 없|처 없)|열지 않|호출(하지| 안)|not used|unused|no usage", re.I)


def unused_security(excluded, sec):
    """버린 것 가운데 「보안 소식인데 안 쓴다는 이유」인 것 (HYO-205 사용자 2026-11-20: 보안 소식은 코드에 없다고 빼지 않는다 — 찾아본 결과를 적어 올린다).
    이유가 사용 여부가 아닌 것(스택 밖 · 내용 없음 · 다른 줄의 백포트)은 그대로 둔다 — 보고서가 따로 센다."""
    return [e for e in excluded if e.get("url") in sec and (e.get("unused_claim") or _UNUSED_WORDS.search(e.get("reason") or ""))]


def to_rescue(excluded, sec, slotted):
    """거르기가 버렸지만 버릴 수 없는 소식 → [(버린 줄, 왜 못 버리나)] (HYO-205).
    slot = 자리 질의가 이미 후보로 고른 것 — 어디에 쓰나를 detect가 다시 판단해 빼면 실행마다 갈린다 · security = 보안 소식을 안 쓴다는 이유로 버린 것."""
    unused = {id(e) for e in unused_security(excluded, sec)}
    return [(e, "slot" if e.get("url") in slotted else "security") for e in excluded if e.get("url") in slotted or id(e) in unused]


def usage_lines(c, patterns):
    """찾아본 패턴 → 초안에 적을 줄. 개수는 모델의 말이 아니라 repo_grep 기록에서 읽는다. 돌린 기록이 없는 패턴은 그렇게 적는다."""
    log, out = grep_log(c), []
    for p in dict.fromkeys(patterns or []):
        r = log.get(p)
        if not r:
            out.append(f"코드 찾기 `{p}`: 돌린 기록 없음")
            continue
        per = " · ".join(f"{repo} {n}곳" for repo, n in sorted((r.get("by_repo") or {}).items()))
        out.append(f"코드 찾기 `{p}`: {per or '대상 없음'}" + (f" · 못 읽은 repo {', '.join(r['unread'])}" if r.get("unread") else "")
                   + " (우리 코드만 — 의존성 안은 보지 않았다)")
    return out


def with_usage(c, body, cand):
    """초안 서술에 코드 찾기 결과를 덧붙인다 — 코드가 적는다 (모델이 옮겨 적다 틀리지 않게)."""
    lines = usage_lines(c, cand.get("checked"))
    return {**body, "research_note": list(body.get("research_note") or []) + lines} if lines else body


def check_unused(c, excluded):
    """「우리 코드가 안 쓴다」며 버린 소식을 repo_grep 기록과 대조한다 (HYO-205, 규율 1 — 모델의 말이 아니라 기록으로).
    확인됨 = checked가 하나 이상 있고, 그 패턴이 전부 이번 실행에서 실제로 돌았고, 전부 0곳이며, 코드를 못 읽은 repo가 없다.
    excluded의 해당 항목에 usage_check를 붙여 돌려준다 — 버림은 그대로 두고, 확인 안 된 것은 보고서가 따로 센다(조용히 믿지 않는다)."""
    log, out = grep_log(c), []
    for e in excluded:
        if not e.get("unused_claim"):
            out.append(e)
            continue
        rows = [{"pattern": p, "ran": p in log, "total": (log.get(p) or {}).get("total"), "by_repo": (log.get(p) or {}).get("by_repo"),
                 "unread": (log.get(p) or {}).get("unread") or []} for p in e.get("checked") or []]
        ok = bool(rows) and all(r["ran"] and r["total"] == 0 and not r["unread"] for r in rows)
        why = None if ok else ("찾아본 패턴을 적지 않았다" if not rows else
                               "적은 패턴을 실제로 돌린 기록이 없다" if not all(r["ran"] for r in rows) else
                               "코드를 못 읽은 repo가 있다" if any(r["unread"] for r in rows) else "쓰는 곳이 나왔다")
        out.append({**e, "usage_check": {"verified": ok, "patterns": rows, **({"why": why} if why else {})}})
    return out


def detect_report(c, now, *, skipped, dropped, excluded, written, failed, security=None):
    """run_report (references/schema.md). pr.needed는 이번 실행에서 초안을 쓴 것이 1개 이상일 때 (detect).

    report의 candidates는 **초안을 쓴 후보**다 — 거른 뒤 남아 초안이 된 것. skipped_existing·dropped·excluded는 따로 둔다.
    """
    files = list(dict.fromkeys(e["message"]["content"][0]["input"]["file_path"] for e in c.events  # repair가 같은 파일을 다시 써도 한 번만
                               if e.get("type") == "assistant" and e["message"]["content"][0]["name"] == "Write"))
    needed = bool(written)
    return {
        "flow": "detect", "now": now, "dry_run": c.dry_run,
        "candidates": [{"technology": w["candidate"]["technology"], "affected": w["candidate"]["affected"],
                        "source_urls": w["candidate"]["source_urls"], "dependency": w["candidate"]["dependency"],
                        "adr": w["id"], "validate_errors": w["validate_errors"], "repaired": w["repaired"]} for w in written],
        "skipped_existing": skipped, "dropped": dropped, "excluded": excluded, "draft_failed": failed,
        "pr": {"needed": needed, "opened": False, "branch": f"claude/radar-detect-{now[:10]}" if needed else None},
        "files_written": files, "remote_side_effects": [],
        "adrs_checked": [], "results": [], "alerts": [], "link_candidates": [], "overrides": [],
        "tool_calls": c.tool_calls(),
        # 「안 쓴다」며 버린 소식 (HYO-205) — verified: 찾아보니 0곳 · unverified: 찾아본 기록과 안 맞는다(사람이 본다)
        "unused": {"verified": [e["url"] for e in excluded if (e.get("usage_check") or {}).get("verified")],
                   "unverified": [{"url": e["url"], "why": e["usage_check"].get("why")} for e in excluded
                                  if e.get("usage_check") and not e["usage_check"]["verified"]],
                   "greps": len(grep_log(c))},
        # 관문이 security로 남긴 소식이 어떻게 됐나 (HYO-205) — rescued: 「안 쓴다」며 버린 것을 코드가 후보로 되돌림 · excluded: 다른 이유로 버림(사람이 본다)
        **({"security": security} if security else {}),
    }



def compose(body, w):
    """DraftBody → AdrDraft 입력. technology·affected는 triage가 정한 후보, related는 radar.py match 결과 — 코드가 덮어쓴다."""
    cand = w["candidate"]
    return {**body, "technology": cand["technology"], "affected": cand["affected"], "related": [r["id"] for r in w["related"]]}


def rewrite(c, path, adr_id, draft, item_id=None):
    """repair가 낸 초안으로 **같은 번호의 파일을 코드가** 다시 쓰고 radar.py validate를 다시 돈다 → 남은 오류.
    item_id가 있으면 write-ahead로 쓴다(의도 → 파일 → 완료) — 사람이 고친 파일이면 덮지 않는다 (HYO-44)."""
    if item_id:
        return hitl.rewrite_adr(c, item_id, adr_id, draft)
    c.record("Write", {"file_path": path})
    open(path, "w", encoding="utf-8", newline="\n").write(ops.render(draft, c.now))
    v = radar(c, "validate", c.adr_dir, ok_codes=(0, 1))
    return next((a["errors"] for a in v["adrs"] if a["id"] == adr_id), ["validate에 없음"])
