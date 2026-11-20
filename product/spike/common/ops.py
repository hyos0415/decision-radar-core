"""도구 본체와 보고서 — 런타임은 이걸 자기 도구 형식(Agents SDK function_tool · LangChain tool)으로 감싸기만 한다.

모델에게 주는 도구는 읽기(snapshot_fetch·snapshot_search·read_repo_file·match_adr)와 사람 승인 뒤의 쓰기(propose_adr)뿐이다.
revisit에서 ADR을 쓰는 길은 decide --apply 하나라서 status를 바꿀 도구가 구조적으로 없다 (R8).
"""

import json
import os
import re

import yaml

from .core import radar

REVISIT_MAX_TURNS = 8  # 조건 하나에 도구 몇 번 + 답 한 번이면 끝난다. 넘으면 루프로 보고 오류로 센다 (P3)
CAPTURE_MAX_TURNS = 10


# ── 읽기 도구 ────────────────────────────────────────────────────────

def snapshot_fetch(c, url):
    c.calls += 1
    return json.dumps(radar(c, "snapshot", "fetch", url), ensure_ascii=False)


def snapshot_search(c, query):
    c.calls += 1
    return json.dumps(radar(c, "snapshot", "search", query), ensure_ascii=False)


def corpus_grep(c, pattern):
    c.calls += 1
    return json.dumps(radar(c, "snapshot", "grep", pattern), ensure_ascii=False)


def read_repo_file(c, repo, path):
    c.calls += 1
    root = c.targets.get(repo)
    if root is None:
        return json.dumps({"ok": False, "error": f"unknown repo {repo!r}", "repos": sorted(c.targets)})
    full = os.path.realpath(os.path.join(root, path))
    if not full.startswith(os.path.realpath(root) + os.sep):
        return json.dumps({"ok": False, "error": "path outside repo"})
    c.record("Read", {"file_path": full})
    if not os.path.isfile(full):
        return json.dumps({"ok": False, "status": 404})
    body = open(full, encoding="utf-8").read()
    return json.dumps({"ok": True, "url": f"repo:{repo}/{path}", "source_type_hint": "repo", "body": body}, ensure_ascii=False)


CODE_SUFFIXES = (".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".sh", ".ps1", ".sql", ".toml", ".cfg", ".ini", ".yml", ".yaml")
CODE_NAMES = ("Dockerfile", "Makefile")
GREP_SHOWN = 8           # 돌려주는 줄 수 — 개수(total · by_repo · 파일별)는 다 센다. 20이면 넓은 패턴마다 4천 자씩 돌아왔다
GREP_MAX_BYTES = 1_000_000


def _code_files(root, exclude=()):
    """대상 repo의 「우리 코드」 — git이 추적하는 코드 · 설정 파일 (문서 · 데이터 · 잠금 파일 · 설치된 의존성은 아니다). git이 아니면 [].
    exclude = radar-targets.json의 exclude(폴더 접두) — 내 스택이 아닌 폴더(eval 픽스처의 가짜 repo 등)는 뺀다."""
    import subprocess
    try:
        p = subprocess.run(["git", "-C", root, "ls-files", "-z"], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if p.returncode != 0:
        return []
    rels = [s for s in p.stdout.decode("utf-8", "replace").split("\0") if s]
    skip = tuple(x.rstrip("/") + "/" for x in exclude)
    return [r for r in rels if (r.endswith(CODE_SUFFIXES) or os.path.basename(r).startswith(CODE_NAMES)) and not r.startswith(skip)]


def repo_grep(c, pattern):
    """대상 repo 전부의 코드에서 정규식을 찾는다 (HYO-205). 기록에 패턴과 개수를 남긴다 — detect가 「안 쓴다」고 적은 것을 코드가 이 기록과 대조한다."""
    c.calls += 1
    try:
        rx = re.compile(pattern)
    except re.error as e:
        c.record("Grep", {"pattern": pattern, "ok": False})
        return json.dumps({"ok": False, "pattern": pattern, "error": f"정규식 오류: {e}"}, ensure_ascii=False)
    by_repo, hits, unread, by_file = {}, [], [], {}
    for repo, root in sorted(c.targets.items()):
        files = _code_files(root, getattr(c, "excludes", {}).get(repo) or ())
        if not files:
            unread.append(repo)            # 코드를 못 읽은 repo는 0곳이 아니라 「모름」이다
            continue
        n = 0
        for rel in files:
            full = os.path.join(root, rel)
            try:
                if os.path.getsize(full) > GREP_MAX_BYTES:
                    continue
                text = open(full, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    n += 1
                    by_file[f"{repo}/{rel}"] = by_file.get(f"{repo}/{rel}", 0) + 1
                    if len(hits) < GREP_SHOWN:
                        hits.append({"repo": repo, "path": rel, "line": i, "text": line.strip()[:160]})
        by_repo[repo] = n
    total = sum(by_repo.values())
    c.record("Grep", {"pattern": pattern, "ok": True, "total": total, "by_repo": by_repo, "unread": unread})
    files = sorted(by_file.items(), key=lambda kv: -kv[1])
    return json.dumps({"ok": True, "pattern": pattern, "total": total, "by_repo": by_repo, "unread_repos": unread,
                       "files": [{"file": f, "lines": k} for f, k in files[:8]], "more_files": max(0, len(files) - 8),
                       "hits": hits, "scope": "대상 repo의 추적되는 코드 · 설정 파일 (설치된 의존성 안은 보지 않는다)"}, ensure_ascii=False)


def match_adr(c, technology, repo, component, use):
    c.calls += 1
    return json.dumps(radar(c, "match", c.adr_dir, "--technology", technology, "--repo", repo,
                            "--component", component, "--use", use), ensure_ascii=False)


DOCS = {  # 도구 설명 — 두 런타임이 같은 글자로 모델에게 보인다
    "snapshot_fetch": "URL 하나를 연다 (스냅숏 모드). ok=false면 접근 실패다 — status에 이유가 있다.",
    "snapshot_search": "1차 출처를 검색한다 (스냅숏 모드). 결과는 URL 목록 — 내용은 snapshot_fetch로 연다.",
    "corpus_grep": "이번 주까지 얼린 수집 코퍼스 전체에 정규식(대소문자 무시)을 돌린다. 피드에 없는 소식 · 같은 기술의 다른 글을 찾을 때 쓴다. "
                   "결과는 많이 맞은 순 30건(url · 제목 · 날짜 · 맞은 줄) — 본문은 snapshot_fetch로 연다.",
    "repo_grep": "대상 repo 전부의 **우리 코드**(추적되는 코드 · 설정 파일)에서 정규식을 찾는다 (대소문자 구분). 소식이 가리키는 모듈 · 함수 · 옵션을 "
                 "우리가 쓰는지 볼 때 쓴다 — 예: `http\\.cookies|SimpleCookie`, `get_server_certificate`. 결과는 repo별 · 파일별 개수와 맞은 줄 8개까지 — 좁은 패턴으로 찾는다(`eval` · `usage`처럼 넓으면 수백 곳이 걸려 쓸모가 없다). "
                 "찾을 것이 여럿이면 한 번에 여러 개를 함께 부른다. "
                 "설치된 의존성 안은 보지 않는다.",
    "read_repo_file": "대상 repo 안의 파일 하나를 읽는다. 근거 URL은 결과의 url(`repo:<repo>/<path>`)을 그대로 쓴다.",
    "match_adr": "같은 결정이 이미 있는지 본다. exact = (technology, component, use) 일치, related = 기술이나 component가 같은 ADR.",
    "propose_adr": "ADR 초안을 제안한다. 사람이 승인해야 실행된다 — 실행되면 다음 번호로 파일을 쓰고 validate한다.",
}


# ── 승인 뒤 쓰기 ─────────────────────────────────────────────────────

def slug(s):
    return re.sub(r"[^0-9a-z]+", "-", s.lower()).strip("-") or "adr"


def render(draft, now):
    fm = {"status": "proposed", "date": now[:10], "technology": draft.technology, "use": draft.use,
          "affected": [a.model_dump() for a in draft.affected], "evidence": [e.model_dump() for e in draft.evidence]}
    if draft.revisit_when:  # R17 — 없으면 칸을 두지 않는다 (본문 「재검토 조건 없음」)
        fm["revisit_when"] = [c.model_dump(exclude_none=True) for c in draft.revisit_when]
    body = [f"# {draft.title}", "", "## Context and Problem Statement", draft.context, "", "## Decision Outcome", draft.decision, ""]
    if draft.related:
        body += ["관련 결정: " + ", ".join(draft.related), ""]
    body += ["## Research Note"] + [f"- {n}" for n in draft.research_note] + [""]
    return "---\n" + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).rstrip() + "\n---\n" + "\n".join(body)


def propose_adr(c, draft):
    """사람이 승인한 뒤에만 부른다 — 런타임의 승인 게이트가 이 호출 앞에 선다."""
    c.calls += 1
    nid = radar(c, "next-id", c.adr_dir)["next_id"]
    path = os.path.join(c.adr_dir, f"{nid}-{slug(draft.technology)}.md")
    c.record("Write", {"file_path": path})
    open(path, "w", encoding="utf-8", newline="\n").write(render(draft, c.now))
    v = radar(c, "validate", c.adr_dir, ok_codes=(0, 1))
    errs = next((a["errors"] for a in v["adrs"] if a["id"] == nid), ["validate에 없음"])
    return json.dumps({"written": path, "id": nid, "validate_errors": errs}, ensure_ascii=False)


# ── 보고서 ───────────────────────────────────────────────────────────

def human_row(adr, cond):
    """human 조건은 모델에게 묻지 않는다 (R5) — 물어도 decide가 뒤집는다."""
    return {"adr": adr["id"], "condition_id": cond.get("id"), "result": "undetermined", "reason": "human", "evidence": []}


def verdict_row(adr, cond, v):
    row = {"adr": adr["id"], "condition_id": cond.get("id"), "result": v.result, "reason": v.reason,
           "evidence": [e.model_dump() for e in v.evidence]}
    if v.observed_value is not None:
        row["observed_value"] = v.observed_value
    return row


def error_row(adr, cond, e):
    """모델 호출 실패 — 판정하지 않고 undetermined로 넘긴다 (R1). P3 오류로 센다."""
    head = {"adr": adr["id"], "condition_id": cond.get("id")}
    err = {**head, "kind": "model_error", "type": type(e).__name__, "message": str(e)[:300]}
    return {**head, "result": "undetermined", "reason": f"model_error: {type(e).__name__}", "evidence": []}, err


def decide(c, rows, now):
    return radar(c, "decide", c.adr_dir, "-", "--now", now, "--apply", stdin=json.dumps(rows, ensure_ascii=False))


def revisit_report(c, d, now):
    return {
        "flow": "revisit", "now": now, "dry_run": c.dry_run,
        "adrs_checked": d["adrs_checked"], "results": d["results"],
        "pr": {"needed": d["pr_needed"], "opened": False,
               "branch": f"claude/radar-revisit-{now[:10]}" if d["pr_needed"] else None},
        "alerts": [{**a, "created": False} for a in d["alerts"]],
        "files_written": d["files_written"], "remote_side_effects": [],
        "candidates": [], "skipped_existing": [], "link_candidates": [],
        "overrides": d["overrides"],
        "tool_calls": c.tool_calls(),
    }


def capture_report(c, now, *, link_candidates, awaiting, preview=None, note=None):
    files = [e["message"]["content"][0]["input"]["file_path"] for e in c.events
             if e["message"]["content"][0]["name"] == "Write"]
    return {"flow": "capture", "now": now, "dry_run": c.dry_run, "link_candidates": link_candidates,
            "awaiting_approval": awaiting, "preview": preview, "note": note,
            "files_written": files, "remote_side_effects": [], "candidates": [], "skipped_existing": [],
            "adrs_checked": [], "results": [], "alerts": [], "pr": {"needed": False, "opened": False, "branch": None},
            "overrides": [], "tool_calls": c.tool_calls()}


def matched(c):
    """capture 1턴에 match_adr를 불렀나 — 안 불렀으면 P3 「도구 호출 누락」."""
    return any(" match " in (e["message"]["content"][0]["input"].get("command") or "") for e in c.events if e["type"] == "assistant")
