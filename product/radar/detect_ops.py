"""detect의 코드 쪽 — 모델이 아니라 그래프의 노드가 부른다. 안전 조항은 여기와 radar.py가 집행한다.

도구 호출은 스킬 transcript와 같은 모양으로 ctx.events에 남는다 (ops.radar / ops.read_repo_file이 기록한다).
"""

import json
import os

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


def detect_report(c, now, *, skipped, dropped, excluded, written, failed):
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
