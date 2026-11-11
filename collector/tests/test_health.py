"""출처 건강 검사(collector/health.py, HYO-172) 오프라인 테스트 — 규칙마다 · N주째 · 알려진 예외 · Linear 알림(가짜) · 실제 W45 재검사.

  python collector/tests/test_health.py

규율 3: 2026-W45 Gemini changelog(본문 45자, run.json failed 한 줄)는 잡혀야 하고, 매주 오는 `pypi python` 404는 예외로 빠져야 한다.
실제 radar-snapshots가 옆에 있으면 W42~W45를 다시 검사해 같은 결과인지 본다 (CI에는 없어서 건너뛴다).
"""
import json
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from collector import health  # noqa: E402

GEM = "https://ai.google.dev/gemini-api/docs/changelog"
AWS = "https://aws.amazon.com/about-aws/whats-new/recent/feed/"
PYPI = "https://pypi.org/pypi/python/json"


def prow(url, chars, status="changed", vendor="google-gemini"):
    return {"id": f"vendor-page:{vendor}:{url}", "vendor": vendor, "url": url, "file": "x.txt", "text_chars": chars, "status": status}


def wk(week, pages=(), by_source=None, failed=(), non_200=(), redirects=(), feeds=None, picked=("google-gemini",), crash=None):
    sources = {"hn": {"failed": []}, "vendor": {"failed": list(failed), "picked": {p: [] for p in picked}}}
    if crash:
        sources["hn"] = {"crash": crash}
    manifest = {"week": week, "pages": list(pages), "counts": {"by_source": by_source or {"hn": {"items": 600}, "vendor": {"items": 40}}},
                "runs": [{"crashed": ["hn"] if crash else []}]}
    return {"week": week, "manifest": manifest, "runs": [{"sources": sources, "http": {"non_200": list(non_200), "redirects": list(redirects)}}],
            "feeds": feeds or {}, "texts": {}}


def healthy(n=4, start=42):
    return [wk(f"2026-W{start + i}", [prow(GEM, 47000 + i * 100)], non_200=[{"url": PYPI, "status": 404}]) for i in range(n)]


KNOWN = {f"url:{PYPI}": "python은 PyPI 패키지가 아니다"}


def test_w45_gemini_shape_caught_and_pypi_known():
    hist = healthy(3, 42)
    cur = wk("2026-W45", [prow(GEM, 45)], failed=[{"vendor": "google-gemini", "url": GEM, "status": "page text too short"}],
             non_200=[{"url": PYPI, "status": 404}])
    doc = health.check(hist, cur, {}, KNOWN)
    assert [p["key"] for p in doc["problems"]] == [f"page:{GEM}"], doc
    p = doc["problems"][0]
    assert {r["rule"] for r in p["reasons"]} == {"half", "failed"} and p["name"] == "google-gemini" and p["streak"] == 1, p
    assert p["evidence"]["chars"] == 45 and p["evidence"]["baseline_chars"] > 40000
    assert doc["line"] == "출처 고장 1 — google-gemini" and doc["known"] == [{"key": f"url:{PYPI}", "why": KNOWN[f"url:{PYPI}"]}], doc
    # 예외를 안 주면 pypi 404도 문제다 — 예외 목록이 실제로 빼고 있다는 확인
    assert f"url:{PYPI}" in {p["key"] for p in health.check(hist, cur, {}, {})["problems"]}


def test_streak_counts_consecutive_broken_weeks():
    hist = healthy(3, 41) + [wk("2026-W44", [prow(GEM, 45)])]
    doc = health.check(hist, wk("2026-W45", [prow(GEM, 45)]), {}, KNOWN)
    p = doc["problems"][0]
    assert p["streak"] == 2 and p["since"] == "2026-W44" and doc["line"] == "출처 고장 1 — google-gemini (2주째)", doc


def test_healthy_weeks_are_quiet():
    hist = healthy(4, 41)
    doc = health.check(hist, wk("2026-W45", [prow(GEM, 48000)], non_200=[{"url": PYPI, "status": 404}]), {}, KNOWN)
    assert doc["problems"] == [] and doc["line"].startswith("출처 이상 없음"), doc


def test_moved_only_across_hosts():
    cur = wk("2026-W46", [prow(GEM, 47000)], redirects=[{"url": GEM, "final": "https://aistudio.google.com/docs/changelog"},
                                                        {"url": "https://langfuse.com/changelog", "final": "https://langfuse.com/changelog/"}])
    ps = health.evaluate(healthy(), cur, {})
    assert list(ps) == [f"page:{GEM}"] and ps[f"page:{GEM}"]["reasons"][0]["rule"] == "moved", ps
    assert ps[f"page:{GEM}"]["evidence"]["final_url"] == "https://aistudio.google.com/docs/changelog"


def test_stale_page_after_three_unchanged_weeks():
    two = healthy(2, 42) + [wk("2026-W44", [prow(GEM, 47000, "unchanged")])]
    assert health.evaluate(two, wk("2026-W45", [prow(GEM, 47000, "unchanged")]), {}) == {}       # 2주째 — 아직
    three = healthy(2, 41) + [wk("2026-W43", [prow(GEM, 47000, "unchanged")]), wk("2026-W44", [prow(GEM, 47000, "unchanged")])]
    ps = health.evaluate(three, wk("2026-W45", [prow(GEM, 47000, "unchanged")]), {})
    assert [r["rule"] for r in ps[f"page:{GEM}"]["reasons"]] == ["stale"], ps


def test_rss_stale_only_when_vendor_picked():
    cfg = {AWS: "aws-bedrock"}
    zero = {AWS: {"items": 10, "new": 0}}
    hist = [wk(f"2026-W{w}", feeds=zero, picked=("aws-bedrock",)) for w in (42, 43, 44)]
    ps = health.evaluate(hist, wk("2026-W45", feeds=zero, picked=("aws-bedrock",)), cfg)
    assert [r["rule"] for r in ps[f"feed:{AWS}"]["reasons"]] == ["stale"], ps
    assert health.evaluate(hist, wk("2026-W45", feeds=zero, picked=()), cfg) == {}               # 규칙이 안 고른 벤더 — 받지도 않았다
    some = [wk("2026-W44", feeds={AWS: {"items": 12, "new": 3}}, picked=("aws-bedrock",))]
    assert health.evaluate(hist[:2] + some, wk("2026-W45", feeds=zero, picked=("aws-bedrock",)), cfg) == {}


def test_rss_feeds_from_vendors_yaml_skip_github_releases():
    import yaml
    feeds = health.rss_feeds(yaml.safe_load((REPO / "collector" / "vendors.yaml").read_text(encoding="utf-8")))
    assert AWS in feeds and not any("github.com" in u for u in feeds), feeds   # GitHub releases는 조용한 게 정상 (사용자 11/5)


def test_source_zero_half_crash():
    hist = [wk(f"2026-W{w}", by_source={"hn": {"items": 600}, "reddit": {"items": 470}}) for w in (42, 43, 44)]
    cur = wk("2026-W45", by_source={"hn": {"items": 0}, "reddit": {"items": 200}}, crash="ConnectionError: x")
    cur["runs"][0]["sources"]["reddit"] = {"failed": []}
    ps = health.evaluate(hist, cur, {})
    assert {r["rule"] for r in ps["source:hn"]["reasons"]} == {"failed", "zero"}, ps
    assert [r["rule"] for r in ps["source:reddit"]["reasons"]] == ["half"], ps


def test_security_zero_is_quiet_but_crash_is_caught():
    """HYO-179 — 보안 채널은 0건이 정상이다(§4-7). W46: 항목 4건이 `python` · `nodejs`로 세어져 `security`는 0건 → 거짓 경보였다."""
    cur = wk("2026-W46", by_source={"hn": {"items": 600}, "vendor": {"items": 40}, "python": {"items": 3}, "nodejs": {"items": 1}})
    cur["runs"][0]["sources"]["security"] = {"failed": [], "runtimes": {"python": {}, "nodejs": {}}}
    assert "source:security" not in health.evaluate(healthy(), cur, {})
    cur["manifest"]["runs"][0]["crashed"] = ["security"]
    cur["runs"][0]["sources"]["security"]["crash"] = "URLError: osv"
    ps = health.evaluate(healthy(), cur, {})
    assert [r["rule"] for r in ps["source:security"]["reasons"]] == ["failed"], ps


def test_missing_page_failed_replaced_skipped_other_failures_keyed():
    cur = wk("2026-W46", [prow(GEM, 47000, "replaced"), prow("https://docs.typesafe.ai/models.md", 8000, "missing", "typesafe")],
             failed=[{"channel": "vendor:langchain", "repo": "langchain-ai/langgraph", "error": "404"}])
    cur["runs"][0]["sources"]["reddit"] = {"failed": [{"channel": "r/LocalLLaMA", "status": 403}]}
    ps = health.evaluate(healthy(), cur, {})
    assert f"page:{GEM}" not in ps and ps["page:https://docs.typesafe.ai/models.md"]["reasons"][0]["rule"] == "failed", ps
    assert "url:vendor:langchain-ai/langgraph" in ps and "url:reddit:r/LocalLLaMA" in ps, list(ps)


class FakeLinear:
    def __init__(self, opened=()):
        self.calls, self.opened = [], list(opened)

    def __call__(self, payload):
        q = payload["query"]
        self.calls.append((q.split("(")[0].split()[0] + ":" + q.split("{")[1].split("(")[0].strip(), payload["variables"]))
        if "issues(" in q:
            return {"data": {"issues": {"nodes": self.opened}}}
        if "issueCreate" in q:
            return {"data": {"issueCreate": {"success": True, "issue": {"id": "new-id", "identifier": "HYO-900"}}}}
        return {"data": {"commentCreate": {"success": True}}}


def test_alert_create_comment_recover_once():
    doc = health.check(healthy(3, 42), wk("2026-W45", [prow(GEM, 45)]), {}, KNOWN)
    fake = FakeLinear()
    state = {}
    done = health.alert(doc, health.Linear("k", post=fake), state)
    creates = [v for k, v in fake.calls if "issueCreate" in k]
    assert len(creates) == 1 and done == ["google-gemini → 이슈 HYO-900"], (fake.calls, done)
    body = creates[0]["input"]["description"]
    assert creates[0]["input"]["title"].startswith(health.TITLE_PREFIX) and f"{health.KEY_LINE}page:{GEM}" in body and "45자" in body, body
    assert creates[0]["input"]["projectId"] == health.LINEAR_PROJECT and creates[0]["input"]["teamId"] == health.LINEAR_TEAM
    again = FakeLinear()
    assert health.alert(doc, health.Linear("k", post=again), state) == [] and not [k for k, _ in again.calls if "Create" in k]   # 같은 주 다시 — 안 쓴다
    # 다음 주: 열린 이슈가 있으면 새로 만들지 않고 N주째 댓글
    opened = [{"id": "iss-1", "identifier": "HYO-900", "description": f"…\n`{health.KEY_LINE}page:{GEM}`"}]
    doc2 = health.check(healthy(3, 42) + [wk("2026-W45", [prow(GEM, 45)])], wk("2026-W46", [prow(GEM, 45)]), {}, KNOWN)
    f2 = FakeLinear(opened)
    done = health.alert(doc2, health.Linear("k", post=f2), state)
    comments = [v for k, v in f2.calls if "commentCreate" in k]
    assert done == ["google-gemini → 댓글 HYO-900"] and comments[0]["input"]["issueId"] == "iss-1" and "2주째" in comments[0]["input"]["body"], f2.calls
    # 고친 주: 문제가 없으면 열린 이슈에 「정상으로 돌아왔다」 한 번
    doc3 = health.check(healthy(4, 43), wk("2026-W47", [prow(GEM, 47000)]), {}, KNOWN)
    f3 = FakeLinear(opened)
    done = health.alert(doc3, health.Linear("k", post=f3), state)
    assert len(done) == 1 and "정상" in [v for k, v in f3.calls if "commentCreate" in k][0]["input"]["body"], f3.calls
    assert health.alert(doc3, health.Linear("k", post=FakeLinear(opened)), state) == []


def test_alert_cli_without_key_skips(tmp=None):
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "2026-W45").mkdir()
        doc = health.check(healthy(3, 42), wk("2026-W45", [prow(GEM, 45)]), {}, KNOWN)
        (Path(d) / "2026-W45" / health.FILE).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        orig = health.env_value
        health.env_value = lambda k: None
        try:
            assert health.main(["--snapshots", d, "alert", "--week", "2026-W45", "--state", str(Path(d) / "s.json")]) == 0
            assert not (Path(d) / "s.json").exists()
            assert health.main(["--snapshots", d, "alert", "--week", "2026-W99"]) == 0       # 파일 없음 — 알림 없이 0
        finally:
            health.env_value = orig


def test_real_frozen_weeks_regression():
    """옆에 radar-snapshots가 있으면 — W42~W44는 조용하고 W45는 Gemini 한 건(pypi python은 예외).
    W46은 문서 주소 이동 4건(HYO-175~178)만 — security 0건(HYO-179)은 빠져야 한다."""
    snaps = REPO.parent / "radar-snapshots"
    if not (snaps / "2026-W45" / "manifest.json").exists():
        print("  (radar-snapshots 없음 — 건너뜀)")
        return
    import yaml
    cfg = health.rss_feeds(yaml.safe_load((REPO / "collector" / "vendors.yaml").read_text(encoding="utf-8")))
    for w in ("2026-W42", "2026-W43", "2026-W44"):
        assert health.check(health.history(snaps, w), health.load_week(snaps, w), cfg, health.load_known())["problems"] == [], w
    doc = health.check(health.history(snaps, "2026-W45"), health.load_week(snaps, "2026-W45", texts=True), cfg, health.load_known())
    assert [p["key"] for p in doc["problems"]] == [f"page:{GEM}"] and doc["problems"][0]["evidence"]["head"].startswith("Release notes"), doc
    if not (snaps / "2026-W46" / "manifest.json").exists():
        return
    doc = health.check(health.history(snaps, "2026-W46"), health.load_week(snaps, "2026-W46"), cfg, health.load_known())
    moved = ["https://docs.claude.com/en/docs/about-claude/model-deprecations", "https://docs.claude.com/en/release-notes/api",
             "https://platform.openai.com/docs/changelog", "https://platform.openai.com/docs/deprecations"]
    assert sorted(p["key"] for p in doc["problems"]) == [f"page:{u}" for u in moved], doc
    assert all([r["rule"] for r in p["reasons"]] == ["moved"] for p in doc["problems"]), doc


def main():
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}\n{traceback.format_exc()}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
