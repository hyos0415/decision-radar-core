"""ADR 재검토 출처 → revisit 입력 오프라인 테스트 (HYO-159) — 가짜 fetcher · 임시 폴더만.

  python pipeline/tests/test_revisit_feed.py

핵심은 왕복이다: ADR의 source_hint를 모델이 쓰는 그대로 radar.py `snapshot fetch`에 넣으면 전문이 나와야 한다 (11/2 실사용 revisit은 전부 404였다).
뒤쪽 변이 검사(규율 3): 힌트를 못 읽거나 url 정규화를 망가뜨리거나 같은 주 detect 입력을 빼면 위 테스트가 빨개져야 한다.
"""
import json
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pipeline import revisit_feed  # noqa: E402

RADAR = REPO / ".claude" / "skills" / "jhs-decisions" / "scripts" / "radar.py"
WEEK = "2026-W45"

ADR_A = """---
id: '0007'
status: adopted
revisit_when:
- id: c1
  check_type: web
  condition: 다음 Haiku가 나온다
  source_hint: platform.claude.com/docs/en/about-claude/models/overview
- id: c2
  check_type: web
  condition: Luna 은퇴 공지
  source_hint: https://developers.openai.com/api/docs/deprecations/
- id: c3
  check_type: human
  condition: 사람이 본다
- id: c4
  check_type: repo
  source_hint: repo:decision-radar/arena/results
---
# 0007
"""
ADR_B = """---
id: '0008'
status: adopted
revisit_when:
- id: c2
  check_type: web
  source_hint: developers.openai.com/api/docs/deprecations
- id: c3
  check_type: web
  source_hint: 공식 블로그를 본다
---
# 0008
"""
PAGES = {
    "https://platform.claude.com/docs/en/about-claude/models/overview": (200, b"<html><script>x()</script><h1>Models</h1><p>Claude Haiku 5.5</p>"
                                                                          + b"<p>" + b"y" * 600 + b"</p></html>"),
    "https://developers.openai.com/api/docs/deprecations": (403, b"forbidden"),
}


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url, headers=None):
        self.calls.append(url)
        return self.pages.get(url, (None, b""))


def world():
    tmp = Path(tempfile.mkdtemp(prefix="revisit-feed-"))
    adr = tmp / "adr"
    adr.mkdir()
    (adr / "0007-luna.md").write_text(ADR_A, encoding="utf-8")
    (adr / "0008-agents.md").write_text(ADR_B, encoding="utf-8")
    snaps = tmp / "radar-snapshots"
    det = snaps / "detect" / WEEK
    (det / "items").mkdir(parents=True)
    (det / "items" / "F01.md").write_text("# acme v2\n\nBREAKING\n", encoding="utf-8")
    (det / "index.json").write_text(json.dumps({"github.com/acme/sdk/releases/tag/v2.0.0": {"source_type": "release_note", "file": "items/F01.md"}}), encoding="utf-8")
    wk = snaps / WEEK
    (wk / "pages" / "openai").mkdir(parents=True)
    (wk / "pages" / "openai" / "dep.txt").write_text("GPT-6 Luna: no deprecation\n", encoding="utf-8")
    (wk / "manifest.json").write_text(json.dumps({"pages": [
        {"url": "https://platform.openai.com/docs/deprecations", "file": "pages/openai/dep.txt", "status": "changed"},
        {"url": "https://gone.example/x", "file": "pages/openai/gone.txt", "status": "missing"}]}), encoding="utf-8")
    return tmp, adr, snaps


def built():
    tmp, adr, snaps = world()
    out = snaps / "revisit" / WEEK
    f = FakeFetcher(PAGES)
    s = revisit_feed.build(adr, out, week=WEEK, snapshots=snaps, fetcher=f)
    return s, out, f


def radar(snap_dir, *args):
    env = dict(os.environ, RADAR_SNAPSHOT_DIR=str(snap_dir), PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, str(RADAR), "snapshot", *args], env=env, capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_hints_web_urls_only():
    _, adr, _ = world()
    h = revisit_feed.hints(adr)
    assert [(x["adr"], x["condition"], x["url"]) for x in h] == [
        ("0007", "c1", "platform.claude.com/docs/en/about-claude/models/overview"),
        ("0007", "c2", "developers.openai.com/api/docs/deprecations"),
        ("0008", "c2", "developers.openai.com/api/docs/deprecations")], h   # human·repo·글로 쓴 힌트는 빠진다


def test_fetch_roundtrip():
    """모델이 ADR에 적힌 그대로(scheme·끝 / 있든 없든) 열면 전문 — 11/2의 404가 사라진다."""
    s, out, f = built()
    r = radar(out, "fetch", "https://platform.claude.com/docs/en/about-claude/models/overview/")
    assert r["ok"] and "Claude Haiku 5.5" in r["body"] and "x()" not in r["body"] and r["source_type_hint"] == "official_doc", r
    assert f.calls.count("https://developers.openai.com/api/docs/deprecations") == 1, f.calls   # 같은 url은 한 번만 받는다
    assert s["urls"] == 2 and s["hints"] == 3 and s["fetched"] == 1 and s["failed"] == 1, s


def test_failed_fetch_shows_status():
    """못 받은 출처는 404(색인에 없음)가 아니라 실제 상태를 준다 — 판정 불가 사유가 정확해진다."""
    _, out, _ = built()
    r = radar(out, "fetch", "developers.openai.com/api/docs/deprecations")
    assert r == {"url": "developers.openai.com/api/docs/deprecations", "ok": False, "status": 403}, r


def test_same_week_detect_and_vendor_pages():
    """같은 주 detect 입력 항목과 주간 수집본 벤더 페이지도 각자 자기 url로 열린다 (폴더 하나로 닫혀 있다). 못 받은 페이지는 안 넣는다."""
    s, out, _ = built()
    r = radar(out, "fetch", "https://github.com/acme/sdk/releases/tag/v2.0.0")
    assert r["ok"] and "BREAKING" in r["body"] and r["source_type_hint"] == "release_note", r
    r = radar(out, "fetch", "platform.openai.com/docs/deprecations")
    assert r["ok"] and "GPT-6 Luna" in r["body"], r
    assert radar(out, "fetch", "gone.example/x")["status"] == 404
    assert s["copied"] == 2, s
    idx = json.loads((out / "index.json").read_text(encoding="utf-8"))
    assert all(not e.get("file") or not e["file"].startswith("..") for e in idx.values()), idx   # 폴더 밖을 가리키지 않는다


def test_failed_fetch_keeps_week_copy():
    """못 받은 출처가 같은 주 detect 입력에 있으면 그 사본을 남긴다 — 실패가 열리던 문서를 가리면 안 된다 (test_backend가 잡은 것)."""
    tmp, adr, snaps = world()
    det = snaps / "detect" / WEEK
    (det / "items" / "F02.md").write_text("# deprecations\n\nLuna retires 2027-01\n", encoding="utf-8")
    idx = json.loads((det / "index.json").read_text(encoding="utf-8"))
    idx["developers.openai.com/api/docs/deprecations"] = {"source_type": "official_doc", "file": "items/F02.md"}
    (det / "index.json").write_text(json.dumps(idx), encoding="utf-8")
    out = snaps / "revisit" / WEEK
    revisit_feed.build(adr, out, week=WEEK, snapshots=snaps, fetcher=FakeFetcher(PAGES))
    r = radar(out, "fetch", "developers.openai.com/api/docs/deprecations")
    assert r["ok"] and "Luna retires" in r["body"], r
    rows = json.loads((out / "hints.json").read_text(encoding="utf-8"))["hints"]
    assert {x["status"] for x in rows if x["condition"] == "c2"} == {403} and all(x["week_copy"] for x in rows if x["condition"] == "c2"), rows


def test_hints_json():
    _, out, _ = built()
    h = json.loads((out / "hints.json").read_text(encoding="utf-8"))
    assert h["week"] == WEEK and h["fetched_at"]
    rows = {(x["adr"], x["condition"]): x for x in h["hints"]}
    ok = rows[("0007", "c1")]
    assert ok["status"] == 200 and ok["file"] == "pages/R01.md" and ok["text_chars"] > revisit_feed.THIN_CHARS and not ok["thin"], ok
    assert rows[("0008", "c2")]["status"] == 403 and rows[("0008", "c2")]["file"] is None


def test_search_empty_and_rebuild_clean():
    s, out, _ = built()
    assert radar(out, "search", "anything") == {"query": "anything", "results": []}
    (out / "pages" / "stale.md").write_text("old", encoding="utf-8")
    revisit_feed.build(out.parents[2] / "adr", out, week=WEEK, snapshots=out.parents[1], fetcher=FakeFetcher(PAGES))
    assert not (out / "pages" / "stale.md").exists()


def test_cli_exit_codes():
    """출처가 있는데 하나도 못 받았으면 1 (네트워크) — 주간 사슬이 revisit 단계를 실패로 남긴다."""
    tmp, adr, snaps = world()
    orig = revisit_feed.Fetcher
    try:
        revisit_feed.Fetcher = lambda: FakeFetcher({})
        assert revisit_feed.main(["--adr-dir", str(adr), "--snapshots", str(snaps), "--week", WEEK]) == 1
        revisit_feed.Fetcher = lambda: FakeFetcher(PAGES)
        assert revisit_feed.main(["--adr-dir", str(adr), "--snapshots", str(snaps), "--week", WEEK]) == 0
        assert (snaps / "revisit" / WEEK / "index.json").exists()
    finally:
        revisit_feed.Fetcher = orig


TESTS = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]


def mutations():
    orig_key, orig_hints, orig_copy = revisit_feed.url_key, revisit_feed.hints, revisit_feed._copy_week
    return [
        ("url 정규화 안 함 (fetch 404)", lambda: setattr(revisit_feed, "url_key", lambda u: u), lambda: setattr(revisit_feed, "url_key", orig_key)),
        ("힌트를 못 읽음 (출처 0)", lambda: setattr(revisit_feed, "hints", lambda d: []), lambda: setattr(revisit_feed, "hints", orig_hints)),
        ("같은 주 detect 입력·벤더 페이지 뺌", lambda: setattr(revisit_feed, "_copy_week", lambda *a: 0), lambda: setattr(revisit_feed, "_copy_week", orig_copy)),
    ]


def run_mutations():
    missed = 0
    for name, apply, undo in mutations():
        apply()
        try:
            caught = False
            for _, f in TESTS:
                try:
                    f()
                except Exception:  # noqa: BLE001
                    caught = True
                    break
        finally:
            undo()
        print(f"  {'잡힘' if caught else '놓침'} {name}")
        missed += 0 if caught else 1
    return missed


def main():
    bad = 0
    for n, f in TESTS:
        try:
            f()
            print(f"  ok   {n}")
        except Exception:  # noqa: BLE001
            bad += 1
            print(f"  FAIL {n}")
            traceback.print_exc()
    print(f"{len(TESTS) - bad}/{len(TESTS)} 통과")
    if bad:
        return 1
    print("변이 검사 — 망가뜨린 코드를 테스트가 잡아야 한다")
    missed = run_mutations()
    print(f"변이 {len(mutations())}개 중 {len(mutations()) - missed}개 잡힘")
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
