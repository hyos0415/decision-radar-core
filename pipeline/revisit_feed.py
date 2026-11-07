"""ADR 재검토 출처 → revisit 입력 (HYO-159). 실사용 revisit이 조건의 `source_hint` 문서를 열 수 있게 그 주에 받아 얼린다.

  python -m pipeline.revisit_feed --adr-dir ../adr/docs/adr --snapshots ../radar-snapshots --week 2026-W45

왜: 실사용 revisit이 detect 입력(관문을 통과한 릴리스 노트만)을 스냅숏으로 써서 출처 문서가 없었다 — 11/2 첫 revisit 12조건 전부
판정 불가(404 not in snapshot index). 스킬은 웹을 직접 열었다. 사용자 결정 11/2: 스냅숏 원칙(모델은 웹에 안 나간다)은 지키고 수집에 넣는다.

출력 폴더 radar-snapshots/revisit/<주>/ (RADAR_SNAPSHOT_DIR로 쓴다 — radar.py `snapshot fetch`가 읽는 모양, detect_feed와 같다):
  index.json   url(scheme·끝 / 뺀 것) → {source_type, file} — 받지 못한 출처는 {status}만 (모델은 404 대신 실제 상태를 본다)
  pages/R01.md source_hint 문서 전문 (이번에 받은 것)
  feed/…       같은 주 detect 입력 항목 · 주간 수집본의 벤더 페이지 사본 — 각자 자기 url로 연다 (폴더 하나로 닫혀 있게 복사한다)
  search.json  빈 목록 (snapshot search는 질의를 무시한다 — 알려진 병목)
  hints.json   조건별 출처 — 어느 ADR·조건 · url · 상태 · 글자 수 · thin(본문이 짧다 = JS로 그리는 페이지일 수 있다)
               · week_copy(못 받았지만 같은 주 사본이 있어 그것을 연다)

같은 url이면 이번에 받은 source_hint 문서가 이긴다 — 못 받았으면 같은 주 사본을 남긴다. repo 조건(`repo:` 힌트)은 read_repo_file 몫이라 여기 없다.
"""
import argparse
import datetime as dt
import json
import re
import shutil
import sys
from pathlib import Path

import yaml

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector.fetch import Fetcher  # noqa: E402
from collector.item import html_to_text  # noqa: E402
from pipeline.detect_feed import url_key  # noqa: E402
from pipeline.profile_filter import write_json  # noqa: E402

THIN_CHARS = 500  # 이보다 짧으면 hints.json에 thin — 본문이 스크립트로 그려지는 페이지일 수 있다 (사람이 본다)
_URLISH = re.compile(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(/\S*)?$")
_FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


def hints(adr_dir):
    """ADR들의 웹 재검토 출처 → [{adr, condition, url}] (url은 url_key 모양). 파일 이름 순."""
    out = []
    for p in sorted(Path(adr_dir).glob("*.md")):
        m = _FRONT.match(p.read_text(encoding="utf-8"))
        if not m:
            continue
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            continue
        for c in fm.get("revisit_when") or []:
            hint = url_key(str((c or {}).get("source_hint") or "").strip())
            if c.get("check_type") == "web" and _URLISH.match(hint):
                out.append({"adr": p.name[:4], "condition": c.get("id"), "url": hint})
    return out


def page_text(body):
    """받은 본문 → 텍스트. HTML이면 블록마다 줄을 바꾸고(벤더 페이지와 같다), 아니면(마크다운·텍스트) 그대로."""
    raw = body.decode("utf-8", errors="replace")
    return html_to_text(raw, lines=True) if raw.lstrip()[:1] == "<" else raw.strip()


def _source_type(url):
    return "release_note" if re.search(r"github\.com/[^/]+/[^/]+/releases", url) else "official_doc"


def _copy_week(snapshots, week, out, index):
    """같은 주 detect 입력 항목 · 주간 수집본 벤더 페이지를 feed/ 아래로 복사해 index에 더한다 → 건수."""
    n = 0
    det = Path(snapshots) / "detect" / week
    if (det / "index.json").exists():
        for key, ent in json.loads((det / "index.json").read_text(encoding="utf-8")).items():
            src = det / ent["file"] if ent.get("file") else None   # 못 받은 1차 출처 링크는 상태만 있다 (HYO-174)
            if src and src.exists():
                dst = Path("feed") / "detect" / ent["file"]
                (out / dst).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, out / dst)
                index[key] = {"source_type": ent.get("source_type"), "file": dst.as_posix()}
                n += 1
    wk = Path(snapshots) / week
    if (wk / "manifest.json").exists():
        for pg in json.loads((wk / "manifest.json").read_text(encoding="utf-8")).get("pages") or []:
            src = wk / (pg.get("file") or "")
            if pg.get("status") != "missing" and pg.get("file") and src.exists():
                dst = Path("feed") / pg["file"]
                (out / dst).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, out / dst)
                index[url_key(pg["url"])] = {"source_type": "official_doc", "file": dst.as_posix()}
                n += 1
    return n


def build(adr_dir, out_dir, *, week, snapshots=None, fetcher=None, now=None):
    """→ 요약 dict {week, hints, fetched, failed, thin, copied}. 출처는 url마다 한 번만 받는다."""
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    (out / "pages").mkdir(parents=True)
    fetcher = fetcher or Fetcher()
    index = {}
    copied = _copy_week(snapshots, week, out, index) if snapshots else 0
    rows, got = [], {}
    for h in hints(adr_dir):
        if h["url"] not in got:
            st, body = fetcher.get("https://" + h["url"])
            text = page_text(body) if st == 200 else ""
            if st == 200 and text:
                name = f"pages/R{len([g for g in got.values() if g.get('file')]) + 1:02d}.md"
                (out / name).write_text(f"# {h['url']}\n\n{text}\n", encoding="utf-8", newline="\n")
                index[h["url"]] = {"source_type": _source_type(h["url"]), "file": name}
                got[h["url"]] = {"status": 200, "file": name, "text_chars": len(text), "thin": len(text) < THIN_CHARS, "week_copy": None}
            else:  # 못 받았다 — 같은 주 사본(detect 입력·벤더 페이지)에 그 url이 있으면 그것을 남기고, 없을 때만 실제 상태를 적는다
                status = st if isinstance(st, int) and st != 200 else "fetch_error"
                if h["url"] not in index:
                    index[h["url"]] = {"source_type": _source_type(h["url"]), "status": status}
                got[h["url"]] = {"status": status, "file": None, "text_chars": 0, "thin": False, "week_copy": index[h["url"]].get("file")}
        rows.append({**h, **got[h["url"]]})
    write_json(out / "index.json", index)
    write_json(out / "search.json", [])
    fetched_at = (now or dt.datetime.now().astimezone()).isoformat(timespec="seconds")
    write_json(out / "hints.json", {"week": week, "fetched_at": fetched_at, "hints": rows})
    ok = [g for g in got.values() if g["file"]]
    return {"week": week, "hints": len(rows), "urls": len(got), "fetched": len(ok), "failed": len(got) - len(ok),
            "thin": sum(1 for g in ok if g["thin"]), "copied": copied}


def summary_line(s):
    line = f"재검토 출처 {s['urls']}곳(조건 {s['hints']}) — 받음 {s['fetched']} · 못 받음 {s['failed']}"
    if s["thin"]:
        line += f" · 본문 짧음 {s['thin']}"
    return line + f" · 같은 주 detect 입력·벤더 페이지 {s['copied']}"


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="pipeline.revisit_feed", description=__doc__.split("\n")[0])
    ap.add_argument("--adr-dir", required=True, help="ADR 폴더 (docs/adr)")
    ap.add_argument("--snapshots", required=True, help="radar-snapshots 작업 사본")
    ap.add_argument("--week", required=True, help="YYYY-Www — 같은 주 detect 입력·주간 수집본을 함께 넣는다")
    ap.add_argument("--out", help="기본: <snapshots>/revisit/<week>")
    a = ap.parse_args(argv)
    out = a.out or str(Path(a.snapshots) / "revisit" / a.week)
    s = build(a.adr_dir, out, week=a.week, snapshots=a.snapshots)
    print(summary_line(s))
    print(f"-> {out}")
    return 0 if s["urls"] == 0 or s["fetched"] else 1  # 출처가 있는데 하나도 못 받았으면 실패 (네트워크)


if __name__ == "__main__":
    sys.exit(main())
