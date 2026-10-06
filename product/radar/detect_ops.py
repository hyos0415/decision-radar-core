"""detect의 코드 쪽 — 모델이 아니라 그래프의 노드가 부른다. 안전 조항은 여기와 radar.py가 집행한다.

도구 호출은 스킬 transcript와 같은 모양으로 ctx.events에 남는다 (ops.radar / ops.read_repo_file이 기록한다).
"""

import json

from common import ops
from common.core import radar


def snapshot_feed(c):
    """스냅숏 모드의 소식 수집 — 웹 대신 radar.py snapshot feed (HYO-45)."""
    c.calls += 1
    return radar(c, "snapshot", "feed")


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


def screen(c, candidates):
    """후보마다 R10·R14를 코드로 집행한다.

    R10  affected가 비었거나 대상 repo에 없는 repo를 가리키면 버린다 → dropped
    R14  radar.py match가 overlap(기술 같고 component 겹침)을 주면 초안을 만들지 않는다 → skipped
    남은 후보는 related(같은 기술이거나 같은 component의 ADR)와 함께 돌려준다 — 초안 본문에 적는다.
    """
    kept, skipped, dropped = [], [], []
    for i, cand in enumerate(candidates):
        aff = cand["affected"]
        bad = [a for a in aff if a["repo"] not in c.targets]
        if not aff or bad:
            dropped.append({"technology": cand["technology"],
                            "reason": "R10: affected가 비었다" if not aff else f"R10: 대상 repo에 없는 repo {sorted({a['repo'] for a in bad})}"})
            continue
        overlap, related = [], {}
        for a in aff:
            m = radar(c, "match", c.adr_dir, "--technology", cand["technology"], "--repo", a["repo"], "--component", a["component"])
            overlap += [x for x in m["overlap"] if x not in overlap]
            for r in m["related"]:
                related.setdefault(r["id"], r)
        if overlap:
            skipped += [{"technology": cand["technology"], "adr": x} for x in overlap]
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


def rewrite(c, path, adr_id, draft):
    """repair가 낸 초안으로 **같은 번호의 파일을 코드가** 다시 쓰고 radar.py validate를 다시 돈다 → 남은 오류."""
    c.record("Write", {"file_path": path})
    open(path, "w", encoding="utf-8", newline="\n").write(ops.render(draft, c.now))
    v = radar(c, "validate", c.adr_dir, ok_codes=(0, 1))
    return next((a["errors"] for a in v["adrs"] if a["id"] == adr_id), ["validate에 없음"])
