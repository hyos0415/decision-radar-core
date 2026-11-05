"""주간 스냅숏 얼리기 (HYO-72) — 수집 결과를 코퍼스 버전 `corpus@<ISO 주차>`로 data repo에 남긴다.

  python -m collector.snapshot freeze                      # 아직 안 얼린 최신 weekly 실행 + 그 사이의 aws 실행
  python -m collector.snapshot freeze --weekly collector/out/20261006-0700-weekly --push
  python -m collector.snapshot status                      # 얼린 주차 · 안 얼린 실행

- 한 주 = 화요일 weekly 실행 ∪ 지난 스냅숏 이후의 aws 실행 (doc-pipeline §8). 주차는 weekly 실행 시각(KST)의 ISO 주
- 얼린 주는 고치지 않는다. 같은 주를 다른 실행으로 다시 얼리려 하면 거부한다 (규율 7)
- first-seen: `first_seen.jsonl`(id → 처음 본 주차)로 다시 나온 항목에 `new: false`를 붙인다. 지우지 않는다 (§3 원칙 1)
- 벤더 페이지: 전문은 `pages/`에, 지난 주와 줄 단위로 비교해 바뀐 페이지마다 `page-diff` 항목을 만든다

data repo 위치: 환경 변수 RADAR_SNAPSHOT_REPO (기본 ../radar-snapshots). 스킬의 RADAR_SNAPSHOT_DIR(eval 스냅숏 모드)와 다르다.
"""
import argparse
import datetime as dt
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from pipeline import page_entries  # 빈 주 메우기가 날짜 블록을 읽는다 (HYO-172)

from . import health as health_mod
from .item import TEXT_MAX

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
KST = dt.timezone(dt.timedelta(hours=9))
_RUN_DIR = re.compile(r"^\d{8}-\d{4}-(weekly|aws)$")
_WEEK_DIR = re.compile(r"^\d{4}-W\d{2}$")
_SLUG = re.compile(r"[^A-Za-z0-9._-]+")


def parse_ts(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def week_of(started):
    y, w, _ = parse_ts(started).astimezone(KST).isocalendar()
    return f"{y}-W{w:02d}"


def load_run(d):
    d = Path(d)
    run = json.loads((d / "run.json").read_text(encoding="utf-8"))
    items = [json.loads(l) for l in (d / "items.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    return run, items


def scan_runs(out_root):
    """예약 실행 폴더(<UTC>-weekly|aws)만. 손으로 돌린 시험 폴더는 이름이 달라서 빠진다."""
    runs = []
    for d in sorted(Path(out_root).iterdir()) if Path(out_root).exists() else []:
        m = _RUN_DIR.match(d.name)
        if m and (d / "run.json").exists() and (d / "items.jsonl").exists():
            started = json.loads((d / "run.json").read_text(encoding="utf-8"))["started"]
            runs.append({"dir": d, "name": d.name, "label": m.group(1), "started": started})
    return runs


def frozen_weeks(repo):
    return sorted(p.name for p in Path(repo).iterdir() if p.is_dir() and _WEEK_DIR.match(p.name)) if Path(repo).exists() else []


def read_manifest(repo, week):
    return json.loads((Path(repo) / week / "manifest.json").read_text(encoding="utf-8"))


def frozen_run_names(repo):
    return {r["name"] for w in frozen_weeks(repo) for r in read_manifest(repo, w)["runs"]}


def pick_runs(repo, out_root, weekly=None):
    """이번에 얼릴 실행들: weekly 하나 + 지난 스냅숏의 weekly 이후(없으면 7일 안)의 aws 실행."""
    runs = scan_runs(out_root)
    done = frozen_run_names(repo)
    if weekly:
        w = Path(weekly)
        started = json.loads((w / "run.json").read_text(encoding="utf-8"))["started"]
        target = {"dir": w, "name": w.name, "label": "weekly", "started": started}
    else:
        pending = [r for r in runs if r["label"] == "weekly" and r["name"] not in done]
        if not pending:
            return None, []
        target = pending[-1]
    prev = [read_manifest(repo, w) for w in frozen_weeks(repo)]
    prev = [m for m in prev if m["weekly_started"] < target["started"]]
    since = prev[-1]["weekly_started"] if prev else (parse_ts(target["started"]) - dt.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    extra = [r for r in runs if r["label"] == "aws" and since < r["started"] <= target["started"] and r["name"] not in done]
    return target, extra


def merge_dicts(items):
    """collector.item.merge와 같은 규칙을 dict에 — 실행·출처를 가로질러 같은 id를 합친다 (aws는 weekly에도 들어 있다)."""
    by_id = {}
    for it in items:
        cur = by_id.get(it["id"])
        if cur is None:
            by_id[it["id"]] = json.loads(json.dumps(it))
            continue
        cur["channels"] += [c for c in it["channels"] if c not in cur["channels"]]
        cur["links"] += [u for u in it["links"] if u not in cur["links"]]
        for k in ("signals", "meta"):
            for kk, v in it[k].items():
                cur[k].setdefault(kk, v)
        cur["text"] = cur["text"] or it["text"]
        cur["created_at"] = cur["created_at"] or it["created_at"]
    return [by_id[k] for k in sorted(by_id)]


def load_ledger(repo):
    p = Path(repo) / "first_seen.jsonl"
    seen = {}
    if p.exists():
        for l in p.read_text(encoding="utf-8").splitlines():
            if l.strip():
                e = json.loads(l)
                seen.setdefault(e["id"], e["week"])
    return seen


def page_file(item):
    url = re.sub(r"^https?://", "", item["url"]).rstrip("/")
    return f"pages/{item['meta']['vendor']}/{_SLUG.sub('-', url)}.txt"


def line_diff(old, new):
    """줄 단위로 더해진 줄 · 빠진 줄. 순서를 보는 비교라 같은 줄이 자리를 옮기면 양쪽에 나온다."""
    a, b = old.split("\n"), new.split("\n")
    added, removed = [], []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op in ("replace", "delete"):
            removed += a[i1:i2]
        if op in ("replace", "insert"):
            added += b[j1:j2]
    return added, removed


def build(repo, target, extra):
    """얼릴 내용을 메모리에서 만든다 (쓰지 않는다). 돌려주는 값: week, items, pages{file: text}, manifest."""
    week = week_of(target["started"])
    runs = [(r, *load_run(r["dir"])) for r in [target] + extra]
    items = merge_dicts([it for _, _, its in runs for it in its])
    seen = load_ledger(repo)
    weeks = [w for w in frozen_weeks(repo) if w < week]
    previous = weeks[-1] if weeks else None
    prev_pages = {p["id"]: p for p in read_manifest(repo, previous)["pages"]} if previous else {}

    pages, page_rows, diffs, replaced = {}, [], [], {}
    for it in items:
        it["first_seen"] = seen.get(it["id"], week)
        it["new"] = it["id"] not in seen
        if it["meta"].get("kind") != "page":
            continue
        f = page_file(it)
        text, it["text"] = it["text"], ""                 # 전문은 pages/에. items.jsonl에는 경로만
        it["meta"]["text_file"] = f
        pages[f] = text
        old = prev_pages.get(it["id"])
        row = {"id": it["id"], "vendor": it["meta"]["vendor"], "url": it["url"], "file": f,
               "text_hash": it["meta"]["text_hash"], "text_chars": it["meta"]["text_chars"]}
        if old is None:
            row["status"] = "baseline"
            rep = it["meta"].get("replaces")
            if rep:  # 주소를 옮긴 첫 주 — 옛 페이지는 「바뀜」, 마지막 정상 주 이후 날짜 블록을 메운다 (HYO-172)
                replaced[f"vendor-page:{it['meta']['vendor']}:{rep}"] = it["url"]
                fill = backfill(repo, weeks, f"vendor-page:{it['meta']['vendor']}:{rep}", it, text, week)
                if fill:
                    diffs.append(fill)
                    row["backfill"] = fill["meta"]["backfill"]
        elif old["text_hash"] == row["text_hash"]:
            row["status"] = "unchanged"
        else:
            row["status"] = "changed"
            old_text = (Path(repo) / previous / old["file"]).read_text(encoding="utf-8").removesuffix("\n")   # write()가 붙인 끝 줄바꿈
            added, removed = line_diff(old_text, text)
            diffs.append({
                "id": f"vendor-diff:{it['meta']['vendor']}:{it['url']}@{week}", "source": "vendor",
                "channels": list(it["channels"]), "title": f"[{it['meta']['vendor']}] {it['url']} 변경 ({previous} → {week})",
                "url": it["url"], "created_at": it["created_at"], "text": "\n".join(added)[:TEXT_MAX],
                "signals": {"added_lines": len(added), "removed_lines": len(removed)}, "links": [],
                "meta": {"vendor": it["meta"]["vendor"], "kind": "page-diff", "previous": previous,
                         "removed": "\n".join(removed)[:TEXT_MAX]},
                "first_seen": week, "new": True})
        page_rows.append(row)
    here = {r["id"] for r in page_rows}
    page_rows += [dict(p, status="replaced", replaced_by=replaced[i]) if i in replaced else dict(p, status="missing")   # 이번 주에 못 받은 페이지
                  for i, p in prev_pages.items() if i not in here and p.get("status") != "replaced"]                # 옮긴 옛 주소는 한 주만 남긴다
    items = sorted(items + diffs, key=lambda x: x["id"])

    by_source = {}
    for it in items:
        s = by_source.setdefault(it["source"], {"items": 0, "new": 0})
        s["items"] += 1
        s["new"] += it["new"]
    manifest = {
        "version": f"corpus@{week}", "week": week, "previous": previous,
        "weekly_started": target["started"],
        "runs": [{"name": r["name"], "label": r["label"], "started": run["started"], "finished": run.get("finished"),
                  "pool_version": run.get("pool_version"), "vendors_version": run.get("vendors_version"),
                  "items": run.get("items"), "non_200": len(run.get("http", {}).get("non_200", [])),
                  "failed": {s: len(v.get("failed", [])) for s, v in run["sources"].items()},
                  "crashed": [s for s, v in run["sources"].items() if "crash" in v]}
                 for r, run, _ in runs],
        "collector_commit": git_head(REPO),
        "counts": {"items": len(items), "new": sum(it["new"] for it in items), "page_diffs": len(diffs),
                   "by_source": dict(sorted(by_source.items()))},
        "pages": page_rows,
        "missing_weeks": missing_weeks(previous, week),
    }
    return week, items, pages, manifest, runs


def backfill(repo, weeks, old_id, it, text, week):
    """주소를 옮긴 페이지의 빈 주 메우기 (HYO-172) — 옛 주소가 마지막으로 정상이던 주(본문이 그 페이지 최대의 절반 이상)의
    가장 늦은 날짜보다 뒤인 날짜 블록만 page-diff 항목 하나로. 새 주소는 「기준」이라 비교할 지난 본문이 없다 — 고장 난 동안
    올라온 소식이 영영 안 보이는 것을 막는다. 정상 주가 없거나 날짜를 못 읽으면 None."""
    hist = []
    for w in weeks:
        row = next((p for p in read_manifest(repo, w)["pages"] if p["id"] == old_id and p.get("status") not in ("missing", "replaced")), None)
        if row:
            hist.append((w, row))
    if not hist:
        return None
    top = max(r["text_chars"] for _, r in hist)
    good_week, good = [(w, r) for w, r in hist if r["text_chars"] * 2 >= top][-1]
    after = page_entries.latest_date((Path(repo) / good_week / good["file"]).read_text(encoding="utf-8"), int(good_week[:4]))
    lines = page_entries.blocks_after(text, after, int(week[:4])) if after else []
    if not lines:
        return None
    vendor = it["meta"]["vendor"]
    return {"id": f"vendor-diff:{vendor}:{it['url']}@{week}", "source": "vendor", "channels": list(it["channels"]),
            "title": f"[{vendor}] {it['url']} 메우기 ({good_week} 이후 → {week})", "url": it["url"], "created_at": it["created_at"],
            "text": "\n".join(lines)[:TEXT_MAX], "signals": {"added_lines": len(lines), "removed_lines": 0}, "links": [],
            "meta": {"vendor": vendor, "kind": "page-diff", "previous": good_week, "removed": "",
                     "backfill": {"replaces": good["url"], "from_week": good_week, "after": after.isoformat()}},
            "first_seen": week, "new": True}


def missing_weeks(previous, week):
    """지난 스냅숏과 이번 주 사이에 비어 있는 주 — Reddit·HF trending·벤더 페이지는 그 주가 영구 손실이다 (§3-1)."""
    if not previous:
        return []
    a = dt.date.fromisocalendar(*map(int, previous.replace("-W", " ").split()), 1)
    b = dt.date.fromisocalendar(*map(int, week.replace("-W", " ").split()), 1)
    out, d = [], a + dt.timedelta(days=7)
    while d < b:
        y, w, _ = d.isocalendar()
        out.append(f"{y}-W{w:02d}")
        d += dt.timedelta(days=7)
    return out


def git_head(path):
    p = subprocess.run(["git", "-C", str(path), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    if p.returncode != 0:
        return None
    dirty = subprocess.run(["git", "-C", str(path), "status", "--porcelain", "--", "collector"], capture_output=True, text=True).stdout.strip()
    return p.stdout.strip() + ("+dirty" if dirty else "")


def write(repo, week, items, pages, manifest, runs, health=None):
    root = Path(repo) / week
    tmp = Path(repo) / f".{week}.tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    body = "".join(json.dumps(it, ensure_ascii=False) + "\n" for it in items).encode("utf-8")
    (tmp / "items.jsonl").write_bytes(body)
    manifest["items_sha256"] = hashlib.sha256(body).hexdigest()
    for f, text in pages.items():
        (tmp / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp / f).write_text(text + "\n", encoding="utf-8", newline="\n")
    (tmp / "runs").mkdir()
    for r, run, _ in runs:
        (tmp / "runs" / f"{r['name']}.json").write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    (tmp / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    if health is not None:   # 출처 건강 검사 (HYO-172) — 같은 주와 함께 얼린다
        (tmp / health_mod.FILE).write_text(json.dumps(health, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    tmp.rename(root)                                       # 다 쓴 뒤에 이름을 바꾼다 — 반쯤 쓴 주가 얼린 주로 보이지 않게
    seen = load_ledger(repo)
    with open(Path(repo) / "first_seen.jsonl", "a", encoding="utf-8", newline="\n") as f:
        for it in items:
            if it["id"] not in seen:
                f.write(json.dumps({"id": it["id"], "week": week}, ensure_ascii=False) + "\n")


def git_commit(repo, week, manifest, push):
    c = manifest["counts"]
    msg = f"corpus@{week} — {c['items']}건 (새 항목 {c['new']} · 페이지 변경 {c['page_diffs']})"
    for cmd in (["add", week, "first_seen.jsonl"], ["commit", "-q", "-m", msg]) + ((["push", "-q", "origin", "HEAD"],) if push else ()):
        p = subprocess.run(["git", "-C", str(repo), *cmd], capture_output=True, text=True)
        if p.returncode != 0:
            raise SystemExit(f"git {cmd[0]} 실패: {p.stderr.strip()}")
    return msg


def cmd_freeze(a):
    target, extra = pick_runs(a.repo, a.out_root, a.weekly)
    if target is None:
        print("얼릴 weekly 실행이 없다 (모두 얼렸거나 아직 안 돌았다)")
        return 0
    week = week_of(target["started"])
    if (Path(a.repo) / week).exists():
        names = [r["name"] for r in read_manifest(a.repo, week)["runs"]]
        if target["name"] in names:
            print(f"이미 얼림: corpus@{week} ({target['name']})")
            return 0
        print(f"거부: corpus@{week}는 이미 {names}로 얼렸다 — 얼린 주는 고치지 않는다", file=sys.stderr)
        return 1
    week, items, pages, manifest, runs = build(a.repo, target, extra)
    try:   # 검사가 죽어도 얼림은 한다 — 파일이 없으면 주간 사슬 health 단계가 「파일 없음」으로 남긴다
        health = health_mod.check_build(a.repo, week, items, pages, manifest, runs)
        manifest["source_health"] = health["line"]
    except Exception as e:  # noqa: BLE001
        health = None
        print(f"출처 건강 검사 실패 (얼림은 계속): {type(e).__name__}: {e}", file=sys.stderr)
    write(a.repo, week, items, pages, manifest, runs, health)
    c = manifest["counts"]
    print(f"corpus@{week}: {c['items']}건 · 새 항목 {c['new']} · 페이지 변경 {c['page_diffs']} · 실행 {[r['name'] for r in manifest['runs']]}")
    if manifest["missing_weeks"]:
        print(f"빠진 주: {manifest['missing_weeks']}")
    if health:
        print(health["line"])
    if not a.no_commit:
        print(git_commit(a.repo, week, manifest, a.push))
    return 0


def cmd_status(a):
    done = frozen_run_names(a.repo)
    for w in frozen_weeks(a.repo):
        m = read_manifest(a.repo, w)
        print(f"{m['version']}  {m['counts']['items']:5}건  새 {m['counts']['new']:5}  페이지 변경 {m['counts']['page_diffs']}  {[r['name'] for r in m['runs']]}")
    pending = [r["name"] for r in scan_runs(a.out_root) if r["name"] not in done]
    print(f"안 얼린 실행: {pending or '없음'}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="collector.snapshot")
    ap.add_argument("--repo", default=os.environ.get("RADAR_SNAPSHOT_REPO") or str(REPO.parent / "radar-snapshots"))
    ap.add_argument("--out-root", default=str(HERE / "out"))
    sp = ap.add_subparsers(dest="cmd", required=True)
    f = sp.add_parser("freeze")
    f.add_argument("--weekly", help="얼릴 weekly 실행 폴더 (없으면 아직 안 얼린 최신)")
    f.add_argument("--push", action="store_true")
    f.add_argument("--no-commit", action="store_true")
    f.set_defaults(fn=cmd_freeze)
    sp.add_parser("status").set_defaults(fn=cmd_status)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
