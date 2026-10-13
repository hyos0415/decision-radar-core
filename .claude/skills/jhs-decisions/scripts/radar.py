#!/usr/bin/env python3
"""jhs — 결정 기록(ADR)의 결정론 부분을 맡는 CLI.

모델이 판단할 것(페이지를 읽고 조건 충족 여부를 판단)과 코드가 강제할 것
(변화 판정·알림 대상·근거 규율·status 불변)을 가른다. 안전 조항은 프롬프트가
아니라 여기서 집행한다 — 모델이 흔들려도 결과가 흔들리지 않게.

명세: 작업 지시서 「B부 명세 v1 개정」 R1~R16.

  radar.py list      <adr_dir> [--in-scope]
  radar.py validate  <adr_dir>
  radar.py match     <adr_dir> --technology T --repo R --component C [--use U]
  radar.py decide    <adr_dir> <results.json|-> [--now ISO] [--apply]
  radar.py apply     <adr_dir> <decision.json>
  radar.py next-id   <adr_dir>
  radar.py snapshot  fetch <url> | search <query> | feed

출력은 전부 stdout JSON. 표준 라이브러리 + PyYAML.
"""

import argparse
import datetime as dt
import glob
import json
import os
import re
import sys

try:
    import yaml
except ImportError:  # 조용히 넘어가지 않는다 — 이 스크립트 없이는 R1~R4 집행이 모델 몫으로 돌아간다
    sys.stderr.write("jhs: PyYAML이 필요하다 — pip install pyyaml\n")
    sys.exit(2)

STATUSES = {"proposed", "adopted", "rejected", "hold", "revisit", "superseded"}
IN_SCOPE = {"rejected", "hold", "revisit"}  # R13
WATCH = {"always"}  # R13 개정 (2026-09-30, HYO-19): 조건에 watch: always가 있으면 status와 상관없이 확인한다
CHECK_TYPES = {"web", "repo", "numeric", "human"}
RESULTS = {"met", "not_met", "undetermined"}
DETERMINED = {"met", "not_met"}
SOURCE_TYPES = {"official_doc", "release_note", "repo", "paper", "issue_pr", "community"}
OPS = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
       ">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "==": lambda a, b: a == b}

FM = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.S)


def out(obj):
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def load_json_arg(path):
    """경로 또는 - (stdin)의 JSON. stdin도 UTF-8로 — Windows에서 `python -I`는 PYTHONUTF8를 무시해
    cp949로 읽고, 깨진 reason을 ADR에 쓴 뒤 out()에서 죽었다 (HYO-28 Haiku 실측, 2026-10-13)"""
    if path == "-":
        sys.stdin.reconfigure(encoding="utf-8")
        return json.load(sys.stdin)
    return json.load(open(path, encoding="utf-8"))


def read_adr(path):
    text = open(path, encoding="utf-8").read()
    m = FM.match(text)
    if not m:
        return None, text
    return (yaml.safe_load(m.group(1)) or {}), m.group(2)


def adr_id(path):
    m = re.match(r"(\d{4})", os.path.basename(path))
    return m.group(1) if m else None


def load_all(adr_dir):
    adrs = []
    for path in sorted(glob.glob(os.path.join(adr_dir, "[0-9][0-9][0-9][0-9]-*.md"))):
        fm, _ = read_adr(path)
        adrs.append({"id": adr_id(path), "file": path, "fm": fm})
    return adrs


def watched(cond):
    return (cond or {}).get("watch") == "always"


def in_scope(fm, cond):
    """R13 (2026-09-30 개정): status가 rejected·hold·revisit이거나, 조건에 watch: always가 붙었으면 확인 대상.

    adopted·proposed ADR의 조건은 기본으로 확인하지 않는다 — 채택한 뒤에도 볼 조건만 쓰는 사람이 표시한다.
    표시를 빠뜨리면 validate가 경고한다.
    """
    return (fm or {}).get("status") in IN_SCOPE or watched(cond)


def scoped_conditions(fm):
    return [c for c in (fm or {}).get("revisit_when") or [] if in_scope(fm, c)]


def cmd_list(a):
    rows = []
    for x in load_all(a.adr_dir):
        fm = x["fm"] or {}
        conds = scoped_conditions(fm) if a.in_scope else (fm.get("revisit_when") or [])
        if a.in_scope and fm.get("status") not in IN_SCOPE and not conds:
            continue
        rows.append({"id": x["id"], "file": x["file"], "status": fm.get("status"),
                     "technology": fm.get("technology"), "use": fm.get("use"),
                     "affected": fm.get("affected") or [],
                     "revisit_when": conds,
                     "revisit_last_checked": fm.get("revisit_last_checked") or []})
    out(rows)


def validate_fm(fm):
    errs, warns = [], []
    if not isinstance(fm, dict):
        return ["frontmatter 없음 또는 파싱 실패"], warns
    if fm.get("status") not in STATUSES:
        errs.append(f"status 값이 {sorted(STATUSES)} 밖: {fm.get('status')!r}")
    for k in ("technology", "use"):
        if not fm.get(k):
            errs.append(f"{k} 없음 (R7 동일성 키)")
    aff = fm.get("affected") or []
    if not aff or not all(isinstance(e, dict) and e.get("repo") and e.get("component") for e in aff):
        errs.append("affected는 {repo, component} 1개 이상")
    for e in fm.get("evidence") or []:
        if not isinstance(e, dict) or not e.get("url") or e.get("source_type") not in SOURCE_TYPES:
            errs.append(f"evidence 항목은 {{url, source_type}} (R6): {e!r}")
        elif e["source_type"] == "community":
            errs.append(f"community 출처는 evidence가 아니라 Research Note로 (R6): {e['url']}")
    ids = set()
    for c in fm.get("revisit_when") or []:
        cid = c.get("id")
        if not cid or cid in ids:
            errs.append(f"revisit_when id 없음 또는 중복: {cid!r}")
        ids.add(cid)
        if c.get("check_type") not in CHECK_TYPES:
            errs.append(f"{cid}: check_type 값이 {sorted(CHECK_TYPES)} 밖")
        if c.get("check_type") == "human":
            warns.append(f"{cid}: 사람만 판정 가능 — 재검토 루프가 확인하지 않는다 (B1-2, R5)")
        if "watch" in c and c.get("watch") not in WATCH:
            errs.append(f"{cid}: watch 값은 {sorted(WATCH)}뿐: {c.get('watch')!r}")
        if fm.get("status") in ("adopted", "proposed") and not watched(c):
            warns.append(f"{cid}: {fm.get('status')} ADR의 조건 — watch: always가 없으면 재검토 루프가 확인하지 않는다 (R13)")
        if c.get("check_type") == "numeric":
            t = c.get("threshold") or {}
            if not (t.get("metric") and t.get("op") in OPS and isinstance(t.get("value"), (int, float))):
                errs.append(f"{cid}: numeric 조건은 threshold {{metric, op, value}} 필요")
        if c.get("check_type") != "human" and not c.get("source_hint"):
            warns.append(f"{cid}: source_hint 없음 — 어디서 확인할지 모델이 추측하게 된다")
    for r in fm.get("revisit_last_checked") or []:
        if r.get("result") not in RESULTS:
            errs.append(f"last_checked {r.get('condition_id')}: result 값 이상")
        if r.get("result") == "undetermined" and not r.get("reason"):
            errs.append(f"last_checked {r.get('condition_id')}: undetermined는 reason 필수 (R1)")
    return errs, warns


NO_CONDITION_NOTE = "재검토 조건 없음"  # R17-2 — 근거가 없어 조건을 만들지 않았다는 본문 표시


def norm_url(u):
    return re.sub(r"^https?://", "", (u or "").strip()).rstrip("/")


def is_repo_hint(h):
    """`repo:<repo>/<path>` 또는 `<repo>:<path>` (repo 이름에는 점이 없다 — 도메인과 가른다)."""
    h = (h or "").strip()
    if h.startswith("repo:"):
        return True
    m = re.match(r"^([\w-]+):(?!//)\S", h)
    return bool(m) and "." not in m.group(1)


def validate_draft(fm, body):
    """R17 (2026-09-30): proposed 초안의 재검토 조건. 사람이 쓴 기존 ADR(proposed 아님)은 대상이 아니다.

    ADR 파일만 보고 알 수 있는 것만 여기서 막는다. basis가 대화·소식의 문장을 그대로 인용했는지는
    대화가 있어야 알 수 있어서 eval 채점이 본다.
    """
    errs = []
    if not isinstance(fm, dict) or fm.get("status") != "proposed":
        return errs
    conds = fm.get("revisit_when") or []
    if not conds and NO_CONDITION_NOTE not in (body or ""):  # R17-2
        errs.append(f"재검토 조건이 없으면 본문 Research Note에 「{NO_CONDITION_NOTE}: …」를 적는다 (R17-2)")
    ev = {norm_url(e.get("url")) for e in fm.get("evidence") or [] if isinstance(e, dict) and e.get("source_type") != "community"}
    for c in conds:
        cid = c.get("id")
        if not str(c.get("basis") or "").strip():  # R17-3
            errs.append(f"{cid}: basis(근거 문장 인용)가 없다 — 근거 없는 조건은 쓰지 않는다 (R17-3)")
        if c.get("check_type") in ("web", "numeric", "repo"):  # R17-5
            h = c.get("source_hint")
            if not (is_repo_hint(h) or norm_url(h) in ev):
                errs.append(f"{cid}: source_hint는 evidence의 1차 출처 URL이거나 repo 경로여야 한다 (R17-5): {h!r}")
    return errs


def cmd_validate(a):
    report = []
    for x in load_all(a.adr_dir):
        errs, warns = validate_fm(x["fm"])
        errs += validate_draft(x["fm"], read_adr(x["file"])[1])
        report.append({"id": x["id"], "file": x["file"], "errors": errs, "warnings": warns})
    out({"ok": not any(r["errors"] for r in report), "adrs": report})
    sys.exit(0 if not any(r["errors"] for r in report) else 1)


def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def cmd_match(a):
    """R7(F-CAPTURE) / R14(F-DETECT) 동일성 판정.

    exact  = (technology, component, use) 셋 다 일치 → link/supersede 후보 (R7)
    overlap = technology 같고 component 겹침 → DETECT가 건너뜀 (R14)
    related = technology 같거나 component 같음 → 신규 초안 본문에 적을 ID
    use 일치는 정규화한 문자열 비교다. 의미가 같은데 표현이 다르면 모델이 기존 ADR의
    use 문구를 그대로 가져와 다시 호출해야 한다 — 그 판단은 여기서 하지 않는다.
    """
    exact, overlap, related = [], [], []
    for x in load_all(a.adr_dir):
        fm = x["fm"] or {}
        same_tech = norm(fm.get("technology")) == norm(a.technology)
        comps = {(norm(e.get("repo")), norm(e.get("component"))) for e in fm.get("affected") or []}
        same_comp = (norm(a.repo), norm(a.component)) in comps
        if same_tech and same_comp:
            overlap.append(x["id"])
            if a.use is not None and norm(fm.get("use")) == norm(a.use):
                exact.append(x["id"])
        if same_tech or same_comp:
            related.append({"id": x["id"], "status": fm.get("status"), "technology": fm.get("technology"),
                            "use": fm.get("use"), "same_technology": same_tech, "same_component": same_comp})
    out({"exact": exact, "overlap": overlap, "related": related})


def prior_map(fm):
    return {r.get("condition_id"): r for r in (fm.get("revisit_last_checked") or [])}


def last_determined(prev):
    """직전 확정 결과(met/not_met). undetermined를 거쳐도 이어받는다 (R4 개정).

    `last_determined` 필드가 없는 옛 기록은 result가 확정값이면 그것을 쓴다 — undetermined였으면 모른다(None).
    """
    if prev is None:
        return None
    if "last_determined" in prev:
        return prev["last_determined"]
    return prev.get("result") if prev.get("result") in DETERMINED else None


def cmd_decide(a):
    """이번 실행의 판정 결과 → 기록·알림 결정. 모델의 판정을 규칙으로 걸러낸다.

    results.json: [{"adr","condition_id","result","reason","evidence":[{url,source_type}],
                    "observed_value": <numeric일 때>}]
    """
    now = a.now or dt.datetime.now().astimezone().isoformat(timespec="seconds")
    # 결과는 stdin("-")으로 받는 것을 기본으로 한다 — 고정 임시 파일은 병렬 실행끼리 덮어쓴다 (스모크 실측)
    results = load_json_arg(a.results)
    by_adr = {x["id"]: x for x in load_all(a.adr_dir)}
    served = None
    log = os.environ.get("RADAR_SERVED_LOG")
    if os.environ.get("RADAR_SNAPSHOT_DIR") and log and os.path.exists(log):
        served = {l.strip() for l in open(log, encoding="utf-8") if l.strip()}

    decided, changed, alerts, overrides = [], [], [], []
    seen = set()
    for r in results:
        adr = by_adr.get(r.get("adr"))
        if not adr:
            overrides.append({"adr": r.get("adr"), "rule": "unknown_adr", "action": "dropped"})
            continue
        fm = adr["fm"] or {}
        cond = {c.get("id"): c for c in fm.get("revisit_when") or []}.get(r.get("condition_id"))
        if not cond:
            overrides.append({"adr": r["adr"], "condition_id": r.get("condition_id"), "rule": "unknown_condition", "action": "dropped"})
            continue
        if not in_scope(fm, cond):  # R13 — 조건 단위 (watch: always 개정)
            overrides.append({"adr": r["adr"], "condition_id": cond["id"], "rule": "R13", "action": "dropped", "status": fm.get("status")})
            continue
        seen.add((r["adr"], cond["id"]))
        res, reason = r.get("result"), r.get("reason")
        ev = [e for e in (r.get("evidence") or []) if isinstance(e, dict) and e.get("url")]

        # INV-2: 스냅숏 모드에서는 이번 실행에 실제로 제공된 URL만 근거로 인정
        if served is not None:
            dropped = [e["url"] for e in ev if e["url"] not in served and not e["url"].startswith("repo:")]
            if dropped:
                overrides.append({"adr": r["adr"], "condition_id": cond["id"], "rule": "INV-2", "dropped_urls": dropped})
            ev = [e for e in ev if e["url"] in served or e["url"].startswith("repo:")]

        if cond.get("check_type") == "human":  # R5
            if res != "undetermined" or reason != "human":
                overrides.append({"adr": r["adr"], "condition_id": cond["id"], "rule": "R5", "from": res})
            res, reason, ev = "undetermined", "human", []
        elif cond.get("check_type") == "numeric":
            # numeric 판정은 스크립트만 한다 — 모델이 넘긴 result는 무시한다.
            # 배치 실측: 모델이 "비교는 decide가 한다"를 result=undetermined로 표현해 기록이 오염됐다.
            t = cond.get("threshold") or {}
            v = r.get("observed_value")
            if isinstance(v, (int, float)) and not isinstance(v, bool) and t.get("op") in OPS and isinstance(t.get("value"), (int, float)):
                computed = "met" if OPS[t["op"]](v, t["value"]) else "not_met"
                if res in ("met", "not_met") and computed != res:
                    overrides.append({"adr": r["adr"], "condition_id": cond["id"], "rule": "threshold", "from": res, "to": computed,
                                      "observed_value": v, "threshold": t})
                res = computed
            else:  # 값을 못 읽었으면 판정하지 않는다 (R1)
                if res in ("met", "not_met"):
                    overrides.append({"adr": r["adr"], "condition_id": cond["id"], "rule": "R1", "from": res, "why": "observed_value 없음"})
                res, reason = "undetermined", reason or "metric_missing"

        if res == "met" and not any(e.get("source_type") in SOURCE_TYPES - {"community"} for e in ev):  # R2
            overrides.append({"adr": r["adr"], "condition_id": cond["id"], "rule": "R2", "from": "met"})
            res, reason = "undetermined", "non-community evidence missing"
        if res not in RESULTS:
            overrides.append({"adr": r["adr"], "condition_id": cond["id"], "rule": "R1", "from": res})
            res, reason = "undetermined", reason or "invalid result"
        if res == "undetermined" and not reason:
            reason = "unspecified"
        if res != "undetermined":
            reason = None

        prev = prior_map(fm).get(cond["id"])
        prev_det = last_determined(prev)
        entry = {"condition_id": cond["id"], "checked_at": now, "result": res, "reason": reason,
                 "evidence_urls": [e["url"] for e in ev] if res != "undetermined" else [],
                 "last_determined": res if res in DETERMINED else prev_det}
        is_change = prev is None or prev.get("result") != res  # R3 (결과 기준), R15 (prior 없음 = 변화)
        decided.append({"adr": r["adr"], **entry, "changed": is_change,
                        "prior": None if prev is None else prev.get("result")})
        if is_change:
            changed.append((r["adr"], entry))
        # R4 (2026-09-30 개정): 직전 **확정** 결과가 met이 아닐 때만 — undetermined는 정보가 없으니 건너뛴다.
        # met → undetermined(403 한 주) → met에 알림이 두 번 나가던 재알림(틈 ①)을 막는다. met → not_met → met은 다시 알린다.
        if res == "met" and prev_det != "met":
            alerts.append({"adr": r["adr"], "condition_id": cond["id"], "kind": "issue",
                           "evidence_urls": entry["evidence_urls"]})

    # 확인 범위 안인데 결과가 안 온 조건 — 조용히 빠지지 않게 undetermined로 채운다 (R1)
    for aid, x in by_adr.items():
        fm = x["fm"] or {}
        for c in scoped_conditions(fm):
            if (aid, c.get("id")) in seen:
                continue
            human = c.get("check_type") == "human"
            prev = prior_map(fm).get(c.get("id"))
            entry = {"condition_id": c.get("id"), "checked_at": now, "result": "undetermined",
                     "reason": "human" if human else "not checked", "evidence_urls": [],
                     "last_determined": last_determined(prev)}
            if not human:
                overrides.append({"adr": aid, "condition_id": c.get("id"), "rule": "R1", "why": "결과 누락"})
            is_change = prev is None or prev.get("result") != "undetermined"
            decided.append({"adr": aid, **entry, "changed": is_change, "prior": None if prev is None else prev.get("result")})
            if is_change:
                changed.append((aid, entry))

    updates = {}
    for aid, entry in changed:
        updates.setdefault(aid, []).append(entry)
    decision = {"now": now, "adrs_checked": sorted({d["adr"] for d in decided}), "results": decided,
                "pr_needed": bool(changed), "updates": updates, "alerts": alerts, "overrides": overrides}
    decision["files_written"] = write_updates(a.adr_dir, updates) if a.apply else []
    out(decision)


def cmd_apply(a):
    """decide의 updates만 ADR에 쓴다. revisit_last_checked 외 필드는 건드리지 않는다 (R8)."""
    d = load_json_arg(a.decision)
    out({"files_written": write_updates(a.adr_dir, d.get("updates") or {})})


def write_updates(adr_dir, updates):
    by_adr = {x["id"]: x for x in load_all(adr_dir)}
    written = []
    for aid, entries in updates.items():
        x = by_adr[aid]
        text = open(x["file"], encoding="utf-8").read()
        m = FM.match(text)
        fm = yaml.safe_load(m.group(1)) or {}
        before_status = fm.get("status")
        lc = {r.get("condition_id"): r for r in fm.get("revisit_last_checked") or []}
        for e in entries:
            lc[e["condition_id"]] = {k: v for k, v in e.items() if v is not None or k == "reason"}
        order = [c.get("id") for c in fm.get("revisit_when") or []]
        fm["revisit_last_checked"] = [lc[k] for k in order if k in lc] + [v for k, v in lc.items() if k not in order]
        assert fm.get("status") == before_status  # R8
        new = "---\n" + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).rstrip() + "\n---\n" + m.group(2)
        open(x["file"], "w", encoding="utf-8", newline="\n").write(new)
        written.append(x["file"])
    return written


def cmd_next_id(a):
    ids = [int(x["id"]) for x in load_all(a.adr_dir) if x["id"]]
    out({"next_id": f"{(max(ids) + 1) if ids else 1:04d}"})


def append_line(path, line):
    """여러 프로세스가 동시에 덧붙여도 줄이 섞이지 않게 잠그고 쓴다.

    2026-09-30 실측: LangGraph가 조건 판정을 병렬로 돌리자 served.log가 4줄 → 3줄 + 조각("uages")이 됐고,
    INV-2가 멀쩡한 근거 URL을 버려 met이 undetermined로 강등됐다. 스킬도 모델이 도구를 병렬로 부르면 같다.
    """
    data = (line + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)  # 0번 바이트를 뮤텍스로 — 쓰기는 O_APPEND라 늘 끝에 붙는다
            try:
                os.write(fd, data)
            finally:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                os.write(fd, data)
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def snapshot_dir():
    d = os.environ.get("RADAR_SNAPSHOT_DIR")
    if not d:
        sys.stderr.write("jhs snapshot: RADAR_SNAPSHOT_DIR가 없다 — 라이브 모드에서는 WebFetch/WebSearch를 쓴다\n")
        sys.exit(2)
    return d


def cmd_snapshot(a):
    d = snapshot_dir()
    if a.op == "fetch":
        idx = json.load(open(os.path.join(d, "index.json"), encoding="utf-8"))
        url = re.sub(r"^https?://", "", a.arg or "").rstrip("/")
        ent = idx.get(url)
        if ent is None:
            out({"url": url, "ok": False, "status": 404, "error": "not in snapshot index"})
            return
        if ent.get("status", 200) != 200:
            out({"url": url, "ok": False, "status": ent["status"]})
            return
        body = open(os.path.join(d, ent["file"]), encoding="utf-8").read()
        log = os.environ.get("RADAR_SERVED_LOG")
        if log:
            append_line(log, url)
        out({"url": url, "ok": True, "status": 200, "source_type_hint": ent.get("source_type"), "body": body})
    elif a.op == "search":
        p = os.path.join(d, "search.json")
        urls = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else []
        out({"query": a.arg, "results": [{"url": u} for u in urls]})
    elif a.op == "feed":
        p = os.path.join(d, "feed.json")
        out(json.load(open(p, encoding="utf-8")) if os.path.exists(p) else [])


def main():
    p = argparse.ArgumentParser(prog="jhs")
    sp = p.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("list"); s.add_argument("adr_dir"); s.add_argument("--in-scope", action="store_true"); s.set_defaults(f=cmd_list)
    s = sp.add_parser("validate"); s.add_argument("adr_dir"); s.set_defaults(f=cmd_validate)
    s = sp.add_parser("match"); s.add_argument("adr_dir")
    for k in ("--technology", "--repo", "--component"):
        s.add_argument(k, required=True)
    s.add_argument("--use"); s.set_defaults(f=cmd_match)
    s = sp.add_parser("decide"); s.add_argument("adr_dir"); s.add_argument("results", help="결과 JSON 경로 또는 - (stdin)"); s.add_argument("--now")
    s.add_argument("--apply", action="store_true", help="updates를 바로 ADR에 쓴다 (decide+apply 원자화)"); s.set_defaults(f=cmd_decide)
    s = sp.add_parser("apply"); s.add_argument("adr_dir"); s.add_argument("decision"); s.set_defaults(f=cmd_apply)
    s = sp.add_parser("next-id"); s.add_argument("adr_dir"); s.set_defaults(f=cmd_next_id)
    s = sp.add_parser("snapshot"); s.add_argument("op", choices=["fetch", "search", "feed"]); s.add_argument("arg", nargs="?"); s.set_defaults(f=cmd_snapshot)
    a = p.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
