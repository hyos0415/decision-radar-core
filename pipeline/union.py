"""합집합 — 스킬 줄이 찾은 항목을 수집 깔때기에 합친다 (HYO-167, docs/design/union-pipeline.md). **기본 꺼짐** (`RADAR_UNION=on`).

  python -m pipeline.union run    --week 2026-W46 [--snapshots ../radar-snapshots] [--adr ../adr] --out <폴더>   # 스킬 줄 실행 → 얼리기 → 합치기
  python -m pipeline.union replay --week 2026-W43 --drafts ../adr/docs/adr/0015-*.md [--no-fetch] --out <폴더>  # 지난 스킬 초안으로 (재생 데이터)

구성 (§4): 스킬 줄(pipeline.skill_line — 그림자 HYO-166과 같은 실행기 · 같은 후보 뽑기) → 원문 받기 · 얼리기(D2: 스킬은 URL만,
원문은 수집기가 받는다 — 스킬이 쓴 요약은 쓰지 않는다) → 정규화(수집 항목과 같은 모양, `source: skill`) → 합치기 · 중복 제거(같은 항목 =
정규화 패키지 이름 + 주 버전, 없으면 정규화 URL — skill_line.item_key) → `found_by`(collect · skill · both).

출력 폴더 (실사용에서는 radar-snapshots/detect/<주>/skill/ 자리, §4):
  items.jsonl   스킬 항목 — collector/item.py 모양 (+ meta.skill · meta.fetch · meta.found_by). 겹친 것도 남긴다(found_by both)
  pages/S01.md  받은 원문
  fetch.json    url마다 받음 · 못 받음(상태) · 받은 시각
  union.json    합치기 결과 — 건수(찾음 · 받음 · 못 받음 · 겹침 · 스킬만) · 겹친 짝(스킬 id → 수집 id들) · 스킬만 id

합류 = **관문 앞**(D6) · 몫 = **같은 몫**(D7) (사용자 2026-11-04). 주간 사슬(collector.weekly)의 `skill` 단계가 관문 앞에서
`run`을 부르고 결과를 `<run 폴더>/union/skill/`에 둔다. 관문(profile_filter) · 자리 질의(slots) · detect 입력(detect_feed)은 코퍼스에
`extra_items(<run 폴더>)` — 스킬만 잡은 항목 — 를 더해 읽는다. 의존성 릴리스는 `dependency-releases` 통로로 레인 A(관문)에,
그 밖(새 모델 · 서비스)은 자리 질의 후보로 간다 — 11/4 조건(스킬 항목도 자리 질의 · 질의별 top-k 후보로)을 지키는 길.
꺼져 있으면(`RADAR_UNION`이 on이 아니면) `extra_items`는 늘 빈 목록이다 — 폴더가 남아 있어도.
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline import skill_line  # noqa: E402
from pipeline.detect_feed import url_key  # noqa: E402
from pipeline.importance.signals import norm  # noqa: E402
from pipeline.profile_filter import read_items, write_json  # noqa: E402
from pipeline.revisit_feed import page_text  # noqa: E402

FLAG = "RADAR_UNION"
SOURCE = "skill"
RUN_DIR = "union"  # <run 폴더>/union/ — skill-run/(스킬 줄 실행) · skill/(얼린 항목 · union.json)
_GH_REPO = re.compile(r"github\.com/([^/\s]+/[^/\s#?]+)", re.I)
_PKG_URL = re.compile(r"(?:pypi\.org/project|npmjs\.com/package)/((?:@[^/\s]+/)?[^/\s#?]+)", re.I)
URL_ORDER = ("release_note", "official_doc", "issue_pr", "paper", None)  # 원문을 받을 근거 링크 — 1차 출처 먼저 (community · repo: 는 안 받는다)


def enabled(env=None):
    return (os.environ if env is None else env).get(FLAG, "off") == "on"


# ── 정규화 ─────────────────────────────────────────────────────

def known_packages(corpus):
    """코퍼스가 아는 의존성 — {"deps": {정규화 이름: 원래 이름}, "repos": {owner/repo 소문자: 의존성}} (GitHub 릴리스 meta.repo · meta.dependency)."""
    deps, repos = {}, {}
    for it in corpus:
        m = it.get("meta") or {}
        if m.get("dependency"):
            deps[norm(m["dependency"])] = m["dependency"]
            if m.get("repo"):
                repos[m["repo"].lower()] = m["dependency"]
    return {"deps": deps, "repos": repos}


def resolve_package(cand, known):
    """스킬 후보의 패키지 이름. 근거 링크의 GitHub 저장소 → 코퍼스의 의존성 · pypi/npm 주소 → 기술 이름이 아는 의존성과 같다 →
    기술 이름 낱말이 아는 의존성 낱말을 다 품는다(가장 긴 것 — 「Anthropic Python SDK」 → anthropic). 못 찾으면 None."""
    for u in cand.get("urls") or []:
        m = _GH_REPO.search(u)
        if m and m.group(1).lower().removesuffix(".git") in known["repos"]:
            return known["repos"][m.group(1).lower().removesuffix(".git")]
        m = _PKG_URL.search(u)
        if m:
            return m.group(1)
    tech = norm(cand.get("technology") or "")
    if tech in known["deps"]:
        return known["deps"][tech]
    words = set(re.split(r"[\s-]+", tech))
    hits = [n for n in known["deps"] if n and set(n.split("-")) <= words]
    return known["deps"][max(hits, key=len)] if hits else None


def primary_evidence(cand):
    """원문을 받을 링크 하나 → (url, source_type) — 근거 중 1차 출처 순(URL_ORDER). `repo:`(사용자 레포 파일) · community는 받지 않는다."""
    ev = [e for e in cand.get("evidence") or [] if not str(e.get("url", "")).startswith("repo:") and e.get("source_type") != "community"]
    for st in URL_ORDER:
        for e in ev:
            if st is None or e.get("source_type") == st:
                return e["url"], e.get("source_type")
    # 유형을 모르는 링크(run_report source_urls)로 물러날 때 — 초안이 community라고 적은 링크는 빼고, 유형은 모름(None)으로 둔다.
    # 모르는 유형을 official_doc로 채우면 커뮤니티 글이 공식 근거로 둔갑한다 (R2, 리뷰 11/4)
    community = {url_key(e["url"]) for e in cand.get("evidence") or [] if e.get("url") and e.get("source_type") == "community"}
    return next(((u, None) for u in cand.get("urls") or [] if not u.startswith("repo:") and url_key(u) not in community), (None, None))


def primary_url(cand):
    return primary_evidence(cand)[0]


def corpus_key(item):
    m = item.get("meta") or {}
    return skill_line.item_key(m.get("dependency"), m.get("tag") or skill_line.version_of(item.get("title")), item.get("url"))


def skill_item(cand, url, known, week, fetched, text, source_type=None):
    """스킬 후보 → 수집 항목 모양. 의존성 릴리스(스킬이 dependency로 본 것)만 meta.dependency · tag와 `dependency-releases` 통로를 단다 —
    레인 A(관문)로 가고 detect_feed가 kind를 정한다. 나머지(새 모델 · 서비스)는 레인 B 통로 없이 자리 질의 후보로 (slots.why_candidate)."""
    pkg = resolve_package(cand, known)
    ver = cand.get("version")
    meta = {"skill": {k: cand.get(k) for k in ("adr", "technology", "use", "version", "dependency", "affected", "evidence")},
            "fetch": fetched, "found_by": "skill", "source_type": source_type}   # 모르면 None — slot_entry가 community로 본다 (R2)
    channels = ["skill-line"]
    if cand.get("dependency") is not False and pkg and ver:
        meta.update(dependency=pkg, tag=ver if ver.startswith("v") else "v" + ver)
        channels.append("dependency-releases")
    elif pkg:
        meta["package"] = pkg  # 같은 항목 키에는 쓴다 (버전이 있으면)
    return {"id": f"{SOURCE}:{url_key(url)}", "source": SOURCE, "channels": channels,
            "title": cand.get("title") or " ".join(x for x in (cand.get("technology"), ver) if x), "url": "https://" + url_key(url),
            "created_at": None, "first_seen": week, "new": True, "links": [], "signals": {}, "text": text,
            "meta": meta}


def item_key(item):
    m = item.get("meta") or {}
    return skill_line.item_key(m.get("dependency") or m.get("package"), m.get("tag") or (m.get("skill") or {}).get("version"), item.get("url"))


# ── 원문 받기 · 얼리기 (D2) ──────────────────────────────────────

_GH_TAG = re.compile(r"^github\.com/([^/]+/[^/]+)/releases/tag/([^/?#]+)$", re.I)


def fetch_text(fetcher, key, headers=None):
    """→ (상태, 본문). GitHub 릴리스 태그 주소면 수집기처럼 releases API 본문 먼저(HTML은 메뉴 소음) — release 객체가 없으면(Langfuse v5.0.0) 페이지로."""
    m = _GH_TAG.match(key)
    if m:
        from collector.sources import github
        code, body = fetcher.get(f"{github.API}/repos/{m.group(1)}/releases/tags/{m.group(2)}", headers=headers)
        if code == 200:
            try:
                rel = json.loads(body)
                text = f"{rel.get('name') or m.group(2)}\n\n{rel.get('body') or ''}".strip()
            except (ValueError, AttributeError):
                text = ""
            if text:
                return 200, text
    code, body = fetcher.get("https://" + key)
    text = page_text(body) if code == 200 else ""
    return (200 if text else "empty") if code == 200 else code, text

def freeze(cands, out_dir, *, week, corpus, fetcher=None, now=None, fetch=True):
    """스킬 후보 → out_dir/items.jsonl · pages/ · fetch.json. 원문을 못 받으면 이유를 남기고 본문은 비운다(스킬 요약을 쓰지 않는다). → 항목들."""
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    (out / "pages").mkdir(parents=True)
    headers = None
    if fetch and fetcher is None:
        from collector.fetch import Fetcher
        from collector.sources import github
        fetcher, t = Fetcher(), github.token()
        headers = {"Authorization": f"Bearer {t}", "Accept": "application/vnd.github+json"} if t else None
    known = known_packages(corpus)
    at = (now or dt.datetime.now().astimezone()).isoformat(timespec="seconds")
    items, log, seen = [], {}, {}
    for c in cands:
        url, src_type = primary_evidence(c)
        if not url:
            log[f"(링크 없음) {c.get('technology')}"] = {"status": "no_url", "adr": c.get("adr")}
            continue
        key = url_key(url)
        if key not in seen:
            if not fetch:
                st, text = "not_fetched", ""
            else:
                try:
                    st, text = fetch_text(fetcher, key, headers)
                    st = st if st is not None else "fetch_error"
                except Exception as e:  # noqa: BLE001 — 받기가 죽어도 다음 링크로
                    st, text = f"fetch_error: {type(e).__name__}", ""
            name = None
            if text:
                name = f"pages/S{sum(1 for v in log.values() if v.get('file')) + 1:02d}.md"
                (out / name).write_text(f"# {key}\n\n{text}\n", encoding="utf-8", newline="\n")
            seen[key] = ({"status": st, "file": name, "fetched_at": at}, text)
            log[key] = {"status": st, "file": name, "fetched_at": at, "adr": c.get("adr")}
        it = skill_item(c, key, known, week, dict(seen[key][0]), seen[key][1], src_type)
        if all(x["id"] != it["id"] for x in items):  # 같은 링크를 근거로 든 초안이 둘이면 한 번만
            items.append(it)
    write_items(out, items)
    write_json(out / "fetch.json", {"week": week, "fetched_at": at, "urls": log})
    return items


def write_items(out, items):
    with open(Path(out) / "items.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")


# ── 합치기 · 중복 제거 · found_by ──────────────────────────────────

def merge(corpus, skill_items):
    """→ union dict. 겹치면 수집 쪽 항목을 남기고(found_by both), 스킬만 잡은 항목만 더한다. 같은 키의 수집 항목이 여럿이면(v5.0.1 · v5.0.2) 모두 both."""
    by_key = {}
    for it in corpus:
        k = corpus_key(it)
        if k:
            by_key.setdefault(k, []).append(it["id"])
    pairs, only, found_by = {}, [], {}
    for it in skill_items:
        k = item_key(it)
        hit = by_key.get(k) if k else None
        if hit:
            pairs[it["id"]] = hit
            it["meta"]["found_by"] = "both"
            for cid in hit:
                found_by[cid] = "both"
        else:
            only.append(it["id"])
            found_by[it["id"]] = "skill"
    fetched = sum(1 for it in skill_items if (it["meta"].get("fetch") or {}).get("status") == 200)
    return {"counts": {"found": len(skill_items), "fetched": fetched, "not_fetched": len(skill_items) - fetched,
                       "both": len(pairs), "skill_only": len(only)},
            "both": pairs, "skill_only": only, "found_by": found_by}


def merged_items(corpus, skill_items, union):
    """관문 앞(D6)에서 읽을 목록 — 코퍼스 + 스킬만 항목. 수집 항목에 found_by를 단 사본을 돌려준다(원본은 그대로)."""
    fb = union["found_by"]
    out = [{**it, "meta": {**(it.get("meta") or {}), "found_by": fb.get(it["id"], "collect")}} for it in corpus]
    return out + skill_only(skill_items, union)


def skill_only(skill_items, union):
    """스킬만 잡은 항목 — 코퍼스에 더할 것."""
    only = set(union["skill_only"])
    return [it for it in skill_items if it["id"] in only]


def skill_dir(run_dir):
    return Path(run_dir) / RUN_DIR / "skill"


def load(run_dir, env=None):
    """<run 폴더>의 합치기 결과 → (스킬 항목들, union dict). 꺼져 있거나 없으면 None."""
    d = skill_dir(run_dir)
    if not enabled(env) or not (d / "union.json").exists():
        return None
    items = [json.loads(x) for x in (d / "items.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    return items, json.loads((d / "union.json").read_text(encoding="utf-8"))


def extra_items(run_dir, env=None):
    """관문 · 자리 질의 · detect 입력이 코퍼스에 더해 읽을 것 (D6) — 스킬만 잡은 항목. 꺼져 있으면 []."""
    got = load(run_dir, env)
    return skill_only(*got) if got else []


def funnel_info(run_dir, lane_a, detect_ids, env=None):
    """funnel.json 칸 — 스킬 줄 건수 + 스킬만 항목이 관문에서 남았나 · detect로 갔나(질의별 top-k에서 잘렸나, D7). 꺼져 있으면 None."""
    got = load(run_dir, env)
    if not got:
        return None
    only = set(got[1]["skill_only"])
    gated = [e for e in lane_a if e["id"] in only]
    return {**got[1]["counts"], "gated": len(gated), "kept": sum(1 for e in gated if (e.get("gate") or {}).get("keep")),
            "to_detect": len(only & set(detect_ids))}


def summary_line(union):
    c = union["counts"]
    return f"스킬 줄 {c['found']} (원문 받음 {c['fetched']} · 못 받음 {c['not_fetched']}) → 수집과 겹침 {c['both']} · 스킬만 {c['skill_only']}"


def build(cands, out_dir, *, week, snapshot, fetcher=None, now=None, fetch=True):
    """후보 → 얼리기 → 합치기. out_dir/union.json을 쓰고 union dict를 돌려준다."""
    corpus = read_items(snapshot)
    items = freeze(cands, out_dir, week=week, corpus=corpus, fetcher=fetcher, now=now, fetch=fetch)
    u = merge(corpus, items)
    write_items(out_dir, items)  # found_by가 정해진 뒤 다시 쓴다
    write_json(Path(out_dir) / "union.json", {"week": week, "snapshot": str(snapshot), **u, "line": summary_line(u)})
    return u


def run(week, out_dir, *, snapshots, adr_root, claude_bin=None, fetcher=None):
    """스킬 줄 한 번(skill@v1 라이브 · 구독) → 후보 → build. ADR은 사본에서 — 스킬 초안이 실제 ADR repo에 쓰이지 않게."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "product"))
    from radar.adr_copy import copy_adr
    work = Path(out_dir) / "skill-run"
    shutil.rmtree(work, ignore_errors=True)
    adr_dir, targets = copy_adr(adr_root, str(work / "adr"))
    bin_ = claude_bin or os.environ.get("RADAR_CLAUDE_BIN") or shutil.which("claude")
    if not bin_:
        raise SystemExit("claude CLI를 못 찾았다 — RADAR_CLAUDE_BIN")
    meta = skill_line.run_detect(str(work), adr_dir=adr_dir, targets=targets,
                                 now=dt.datetime.now().astimezone().isoformat(timespec="seconds"), claude_bin=bin_)
    if not meta["ok"]:
        raise SystemExit(f"스킬 줄 실패: {meta['error']}")
    cands = json.loads((work / "candidates.json").read_text(encoding="utf-8"))["drafts"]
    return build(cands, Path(out_dir) / "skill", week=week, snapshot=Path(snapshots) / week, fetcher=fetcher)  # = skill_dir(<run 폴더>)


def replay_candidates(draft_files):
    """지난 스킬 초안(ADR 파일) → 후보 — 스킬 줄과 같은 extract_candidates로 (run_report 없이 파일만)."""
    out = []
    for f in draft_files:
        p = Path(f)
        out += skill_line.extract_candidates(None, str(p.parent), [p.name])["drafts"]
    return out


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    root = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(prog="pipeline.union", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help=f"스킬 줄 실행 → 얼리기 → 합치기 ({FLAG}=on일 때만)")
    r.add_argument("--week", required=True)
    r.add_argument("--snapshots", default=str(root / "radar-snapshots"))
    r.add_argument("--adr", default=str(root / "adr"))
    r.add_argument("--out", required=True)
    p = sub.add_parser("replay", help="지난 스킬 초안으로 얼리기 → 합치기 (재생 데이터 시험)")
    p.add_argument("--week", required=True)
    p.add_argument("--snapshots", default=str(root / "radar-snapshots"))
    p.add_argument("--drafts", nargs="+", required=True)
    p.add_argument("--no-fetch", action="store_true", help="원문을 받지 않는다 (네트워크 없음)")
    p.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "run":
        if not enabled():
            print(f"합집합은 꺼져 있다 ({FLAG}=on으로 켠다 — 실사용 기간에는 켜지 않는다, union-pipeline.md §7)")
            return 0
        u = run(a.week, a.out, snapshots=a.snapshots, adr_root=a.adr)
    else:
        u = build(replay_candidates(a.drafts), a.out, week=a.week, snapshot=Path(a.snapshots) / a.week, fetch=not a.no_fetch)
    print(summary_line(u))
    return 0


if __name__ == "__main__":
    sys.exit(main())
