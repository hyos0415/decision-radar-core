"""주간 스냅숏 오프라인 테스트 — 가짜 수집 폴더로 주차·실행 고르기·first-seen·페이지 비교·재동결 거부를 확인한다.

  python collector/tests/test_snapshot.py
"""
import json
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from collector import snapshot  # noqa: E402
from collector.item import text_hash  # noqa: E402


def item(id_, source="hn", text="", **meta):
    return {"id": id_, "source": source, "channels": [source], "title": id_, "url": f"https://x/{id_}",
            "created_at": "", "text": text, "signals": {}, "links": [], "meta": meta}


def page(vendor, url, text):
    it = item(f"vendor-page:{vendor}:{url}", "vendor", text, vendor=vendor, kind="page", format="html",
              text_hash=text_hash(text), text_chars=len(text))
    it["url"] = url
    return it


def make_run(out, name, started, items):
    d = Path(out) / name
    d.mkdir(parents=True)
    (d / "items.jsonl").write_text("".join(json.dumps(i) + "\n" for i in items), encoding="utf-8")
    (d / "run.json").write_text(json.dumps({"started": started, "finished": started, "pool_version": 1, "vendors_version": 1,
                                            "items": len(items), "sources": {"hn": {"failed": []}}, "http": {"non_200": []}}), encoding="utf-8")
    return d


def env():
    t = Path(tempfile.mkdtemp())
    (t / "repo").mkdir()
    subprocess.run(["git", "init", "-q", str(t / "repo")], check=True)
    return t / "repo", t / "out"


def freeze(repo, out, *extra):
    return snapshot.main(["--repo", str(repo), "--out-root", str(out), "freeze", "--no-commit", *extra])


def test_week_is_iso_week_in_kst():
    assert snapshot.week_of("2026-10-06T07:00:00Z") == "2026-W41"      # 화 16:00 KST
    assert snapshot.week_of("2026-10-04T15:30:00Z") == "2026-W41"      # UTC로는 일요일, KST로는 월요일 00:30
    assert snapshot.missing_weeks("2026-W41", "2026-W44") == ["2026-W42", "2026-W43"]
    assert snapshot.missing_weeks("2026-W52", "2027-W02") == ["2026-W53", "2027-W01"]


def test_pick_runs_weekly_plus_aws_since_last():
    repo, out = env()
    make_run(out, "20260925-0700-aws", "2026-09-25T07:00:00Z", [])        # 7일보다 이전 — 첫 스냅숏에서 빠짐
    make_run(out, "20261001-0700-aws", "2026-10-01T07:00:00Z", [])
    make_run(out, "20261006-0700-weekly", "2026-10-06T07:00:00Z", [])
    make_run(out, "live-2026-10-02", "2026-10-02T07:00:00Z", [])            # 시험 폴더 — 이름이 달라 빠짐
    make_run(out, "20261008-0700-aws", "2026-10-08T07:00:00Z", [])         # 다음 주 몫
    t, extra = snapshot.pick_runs(repo, out)
    assert t["name"] == "20261006-0700-weekly" and [r["name"] for r in extra] == ["20261001-0700-aws"]


def test_first_seen_marks_reappearing_items():
    repo, out = env()
    make_run(out, "20261006-0700-weekly", "2026-10-06T07:00:00Z", [item("hn:1"), item("hn:2")])
    assert freeze(repo, out) == 0
    make_run(out, "20261013-0700-weekly", "2026-10-13T07:00:00Z", [item("hn:2"), item("hn:3")])
    assert freeze(repo, out) == 0
    rows = {json.loads(l)["id"]: json.loads(l) for l in (repo / "2026-W42" / "items.jsonl").read_text(encoding="utf-8").splitlines()}
    assert rows["hn:2"]["new"] is False and rows["hn:2"]["first_seen"] == "2026-W41"
    assert rows["hn:3"]["new"] is True and rows["hn:3"]["first_seen"] == "2026-W42"
    ledger = [json.loads(l)["id"] for l in (repo / "first_seen.jsonl").read_text(encoding="utf-8").splitlines()]
    assert ledger == ["hn:1", "hn:2", "hn:3"]                            # 다시 나온 항목은 원장에 한 번만
    m = snapshot.read_manifest(repo, "2026-W42")
    assert m["previous"] == "2026-W41" and m["counts"]["new"] == 1 and m["missing_weeks"] == []


def test_runs_merged_by_id_across_sources():
    repo, out = env()
    rel = item("github-release:openai/openai-agents-python@v0.23.0", "github")
    rel_v = dict(rel, source="vendor", channels=["vendor:openai"])
    make_run(out, "20261001-0700-aws", "2026-10-01T07:00:00Z", [item("vendor:aws:1", "vendor")])
    make_run(out, "20261006-0700-weekly", "2026-10-06T07:00:00Z", [rel, rel_v, item("vendor:aws:1", "vendor")])
    assert freeze(repo, out) == 0
    rows = [json.loads(l) for l in (repo / "2026-W41" / "items.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert next(r for r in rows if r["id"].startswith("github-release"))["channels"] == ["github", "vendor:openai"]


def test_page_diff_baseline_changed_unchanged_missing():
    repo, out = env()
    a = "Messages\nSep 30: Sonnet 5.5\nSep 1: Opus 5"
    make_run(out, "20261006-0700-weekly", "2026-10-06T07:00:00Z",
             [page("anthropic", "https://docs/rn", a), page("openai", "https://op/cl", "x"), page("openai", "https://op/dep", "d")])
    assert freeze(repo, out) == 0
    m = snapshot.read_manifest(repo, "2026-W41")
    assert {p["status"] for p in m["pages"]} == {"baseline"} and m["counts"]["page_diffs"] == 0
    f = next(p["file"] for p in m["pages"] if p["vendor"] == "anthropic")
    assert (repo / "2026-W41" / f).read_text(encoding="utf-8") == a + "\n"   # 전문은 pages/에
    b = "Messages\nOct 7: Haiku 5 released\nSep 30: Sonnet 5.5\nSep 1: Opus 5"
    make_run(out, "20261013-0700-weekly", "2026-10-13T07:00:00Z", [page("anthropic", "https://docs/rn", b), page("openai", "https://op/cl", "x")])
    assert freeze(repo, out) == 0
    m = snapshot.read_manifest(repo, "2026-W42")
    st = {p["url"]: p["status"] for p in m["pages"]}
    assert st == {"https://docs/rn": "changed", "https://op/cl": "unchanged", "https://op/dep": "missing"}, st
    rows = [json.loads(l) for l in (repo / "2026-W42" / "items.jsonl").read_text(encoding="utf-8").splitlines()]
    d = next(r for r in rows if r["meta"].get("kind") == "page-diff")
    assert d["text"] == "Oct 7: Haiku 5 released" and d["signals"] == {"added_lines": 1, "removed_lines": 0}
    assert d["meta"]["previous"] == "2026-W41"
    assert all(r["text"] == "" for r in rows if r["meta"].get("kind") == "page")   # items.jsonl에는 경로만


def test_moved_page_replaced_and_backfilled():
    """HYO-172 — 주소를 옮긴 페이지(vendors.yaml replaces): 옛 페이지는 한 주만 「replaced」, 새 페이지는 기준이지만
    옛 주소가 마지막으로 정상이던 주(W41)의 가장 늦은 날짜 뒤 블록만 page-diff로 메운다. 고장 난 W42(본문 급감)는 기준으로 안 쓴다."""
    repo, out = env()
    old, new = "https://ai.google.dev/gemini-api/docs/changelog", "https://aistudio.google.com/docs/changelog.md"
    make_run(out, "20261006-0700-weekly", "2026-10-06T07:00:00Z",
             [page("google-gemini", old, "Gemini API\nOctober 13, 2026\nReleased 3.6 Flash\nOctober 6, 2026\nReleased 3.5 Pro")])
    assert freeze(repo, out) == 0
    make_run(out, "20261013-0700-weekly", "2026-10-13T07:00:00Z", [page("google-gemini", old, "Gemini")])   # JS 페이지 — 본문 급감
    assert freeze(repo, out) == 0
    md = '## November 4, 2026  {% id="11-04-2026" %}\nVoice remixing\n## October 20, 2026  {% id="10-20-2026" %}\n2.5 Pro deprecation\n' \
         '## October 13, 2026  {% id="10-13-2026" %}\nReleased 3.6 Flash'
    moved = page("google-gemini", new, md)
    moved["meta"].update(format="markdown", replaces=old)
    make_run(out, "20261020-0700-weekly", "2026-10-20T07:00:00Z", [moved])
    assert freeze(repo, out) == 0
    m = snapshot.read_manifest(repo, "2026-W43")
    rows = {p["url"]: p for p in m["pages"]}
    assert rows[old]["status"] == "replaced" and rows[old]["replaced_by"] == new, rows
    assert rows[new]["status"] == "baseline" and rows[new]["backfill"] == {"replaces": old, "from_week": "2026-W41", "after": "2026-10-13"}, rows[new]
    items = [json.loads(l) for l in (repo / "2026-W43" / "items.jsonl").read_text(encoding="utf-8").splitlines()]
    d = next(i for i in items if i["meta"].get("kind") == "page-diff")
    assert "Voice remixing" in d["text"] and "2.5 Pro deprecation" in d["text"] and "3.6 Flash" not in d["text"], d["text"]
    assert m["counts"]["page_diffs"] == 1   # 메우기 항목도 페이지 변경으로 센다
    h = json.loads((repo / "2026-W43" / "source-health.json").read_text(encoding="utf-8"))
    assert h["week"] == "2026-W43" and not any(p["url"] == old for p in h["problems"]), h   # 옮긴 옛 주소는 고장이 아니다
    h42 = json.loads((repo / "2026-W42" / "source-health.json").read_text(encoding="utf-8"))
    assert any(p["url"] == old and any(r["rule"] == "half" for r in p["reasons"]) for p in h42["problems"]), h42   # W42에는 잡혔어야 한다
    make_run(out, "20261027-0700-weekly", "2026-10-27T07:00:00Z", [moved])
    assert freeze(repo, out) == 0
    rows = {p["url"]: p for p in snapshot.read_manifest(repo, "2026-W44")["pages"]}
    assert old not in rows and rows[new]["status"] == "unchanged", rows   # replaced는 한 주만 남는다


def test_frozen_week_is_not_rewritten():
    repo, out = env()
    make_run(out, "20261006-0700-weekly", "2026-10-06T07:00:00Z", [item("hn:1")])
    assert freeze(repo, out) == 0
    before = (repo / "2026-W41" / "items.jsonl").read_bytes()
    assert freeze(repo, out) == 0                                         # 같은 실행 — 이미 얼림, 아무것도 안 함
    rerun = make_run(out, "20261006-0900-weekly", "2026-10-06T09:00:00Z", [item("hn:9")])
    assert freeze(repo, out, "--weekly", str(rerun)) == 1                  # 같은 주 다른 실행 — 거부
    assert (repo / "2026-W41" / "items.jsonl").read_bytes() == before
    assert "hn:9" not in (repo / "first_seen.jsonl").read_text(encoding="utf-8")


def test_missing_week_recorded():
    repo, out = env()
    make_run(out, "20261006-0700-weekly", "2026-10-06T07:00:00Z", [item("hn:1")])
    freeze(repo, out)
    make_run(out, "20261020-0700-weekly", "2026-10-20T07:00:00Z", [item("hn:2")])
    freeze(repo, out)
    assert snapshot.read_manifest(repo, "2026-W43")["missing_weeks"] == ["2026-W42"]


def test_commit_in_data_repo():
    repo, out = env()
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    make_run(out, "20261006-0700-weekly", "2026-10-06T07:00:00Z", [item("hn:1")])
    assert snapshot.main(["--repo", str(repo), "--out-root", str(out), "freeze"]) == 0
    log = subprocess.run(["git", "-C", str(repo), "log", "--format=%s"], capture_output=True, text=True, encoding="utf-8").stdout
    assert log.startswith("corpus@2026-W41 — 1건"), log
    assert subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True).stdout == ""


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception:  # noqa: BLE001 — 예외는 전부 실패로 센다
            failed += 1
            print(f"FAIL {name}\n{traceback.format_exc()}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
