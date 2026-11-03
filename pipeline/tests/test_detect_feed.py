"""관문 결과 → detect 입력 오프라인 테스트 (HYO-84) — 네트워크·모델 없이 임시 폴더만.

  python pipeline/tests/test_detect_feed.py

핵심은 왕복이다: 만든 폴더를 radar.py `snapshot feed|fetch`가 그대로 읽고, feed의 url마다 fetch가 전문을 준다 (모델이 여는 길).
뒤쪽 변이 검사(규율 3): url 정규화를 망가뜨리거나 상한·관문을 무시하면 위 테스트가 빨개져야 한다.
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

from pipeline import detect_feed  # noqa: E402

RADAR = REPO / ".claude" / "skills" / "jhs-decisions" / "scripts" / "radar.py"

ITEMS = [
    {"id": "rel-1", "source": "github", "channels": ["dependency-releases"], "title": "acme/sdk v2.0.0",
     "url": "https://github.com/acme/sdk/releases/tag/v2.0.0", "text": "BREAKING: drops Python 3.9\n" + "x" * 3000,
     "meta": {"dependency": "acme-sdk", "tag": "v2.0.0"}},
    {"id": "news-1", "source": "vendor", "channels": ["vendor:acme"], "title": "Acme retires model A",
     "url": "https://acme.example/news/retire-a/", "text": "Model A is retired on 2026-12-01.", "meta": {"vendor": "acme"}},
    {"id": "diff-1", "source": "vendor", "channels": ["vendor:acme"], "title": "[acme] pricing changed",
     "url": "https://acme.example/pricing", "text": "Model B: $3 / MTok", "meta": {"vendor": "acme", "kind": "page-diff", "removed": ["Model B: $2 / MTok"]}},
    {"id": "rel-2", "source": "github", "channels": ["dependency-releases"], "title": "acme/sdk v1.9.1",
     "url": "https://github.com/acme/sdk/releases/tag/v1.9.1", "text": "fix typo", "meta": {"dependency": "acme-sdk", "tag": "v1.9.1"}},
    {"id": "cut-1", "source": "vendor", "channels": ["vendor:acme"], "title": "Acme new region",
     "url": "https://acme.example/news/region", "text": "New region.", "meta": {"vendor": "acme"}},
]


def gate(keep, reason="acme-sdk L5 × breaking"):
    kb = [{"tier": "L5", "change": "breaking", "technology": "acme-sdk"}] if keep else []
    return {"keep": keep, "status": "ok", "kept_by": kb, "reason": reason if keep else "남기는 행 없음"}


LANE_A = [
    {"id": "rel-1", "gate": gate(True), "shown": True},
    {"id": "news-1", "gate": gate(True, "acme-model L2 × retirement"), "shown": True},
    {"id": "diff-1", "gate": gate(True, "acme-model L2 × pricing"), "shown": True},
    {"id": "rel-2", "gate": gate(False), "shown": None},
    {"id": "cut-1", "gate": gate(True, "acme-model L2 × feature"), "shown": False},
    {"id": "unk-1", "gate": {"keep": False, "status": "unclassified", "kept_by": []}, "shown": None},
]


def make_run(root, gated=True):
    run, snap = Path(root) / "run", Path(root) / "snap"
    run.mkdir()
    snap.mkdir()
    (snap / "items.jsonl").write_text("".join(json.dumps(i) + "\n" for i in ITEMS), encoding="utf-8")
    manifest = {"snapshot": str(snap), "counts": {"items": 120, "lane_a": 6},
                "lane_a_gate": {"model": "stub", "label": "3/4", "total_kept": 4} if gated else "not-implemented"}
    (run / "run.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run / "lane_a.json").write_text(json.dumps(LANE_A), encoding="utf-8")
    return run


def radar(snap_dir, *args):
    env = dict(os.environ, RADAR_SNAPSHOT_DIR=str(snap_dir), PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, str(RADAR), "snapshot", *args], env=env, capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def built(all_kept=False):
    tmp = tempfile.mkdtemp(prefix="detect-feed-")
    run = make_run(tmp)
    out = Path(tmp) / "out"
    f = detect_feed.build(run, out, all_kept=all_kept)
    return out, f


def test_only_shown_kept():
    out, f = built()
    feed = json.loads((out / "feed.json").read_text(encoding="utf-8"))
    assert [e["id"] for e in feed] == ["rel-1", "news-1", "diff-1"], [e["id"] for e in feed]
    assert f["to_detect"] == 3 and f["kept"] == 4 and f["cap"] == "3/4"


def test_all_kept_includes_cut():
    out, _ = built(all_kept=True)
    assert [e["id"] for e in json.loads((out / "feed.json").read_text(encoding="utf-8"))] == ["rel-1", "news-1", "diff-1", "cut-1"]


def test_radar_reads_feed_and_fetches_every_url():
    out, _ = built()
    feed = radar(out, "feed")
    assert len(feed) == 3
    for e in feed:
        r = radar(out, "fetch", e["url"])
        assert r["ok"], (e["url"], r)
        assert e["title"] in r["body"]
        assert r["source_type_hint"] == e["source_type"]


def test_feed_fields():
    out, _ = built()
    rel, news, diff = json.loads((out / "feed.json").read_text(encoding="utf-8"))
    assert rel["kind"] == "dependency_release" and rel["package"] == "acme-sdk" and rel["version"] == "v2.0.0" and rel["source_type"] == "release_note"
    assert len(rel["summary"]) < 1700 and rel["summary"].endswith("(전문은 snapshot_fetch)")
    assert news["kind"] == "vendor_news" and news["source_type"] == "official_doc" and "package" not in news
    assert diff["kind"] == "page_change" and "$3" in diff["summary"]
    body = radar(out, "fetch", diff["url"])["body"]
    assert "## 빠진 줄" in body and "$2 / MTok" in body
    assert "x" * 3000 in radar(out, "fetch", rel["url"])["body"]   # 전문은 자르지 않는다


def test_gate_why_and_funnel_line():
    out, f = built()
    why = json.loads((out / "gate.json").read_text(encoding="utf-8"))
    assert why[ITEMS[1]["url"]]["reason"] == "acme-model L2 × retirement"
    assert why[ITEMS[0]["url"]]["kept_by"][0]["tier"] == "L5"
    assert "tier" not in json.dumps(json.loads((out / "feed.json").read_text(encoding="utf-8")))   # 관문 판단은 detect 입력에 넣지 않는다
    assert detect_feed.funnel_line(f) == ("수집 120 → 레인 A 6 → 관문 남김 4 (모르는 기술 1 · 분류 불가 0 · 팀원 스택 0) → detect 입력 3 (3/4)"
                                          " — 경고: 관문이 판정 못 한 소식 17% (모르는 기술+분류 불가 / 팀원 스택 뺀 레인 A — 기준 5%, 잠정)")
    assert f["gate_status"] == {"ok": 5, "unclassified": 1}
    assert f["gate_counts"]["unknown"] == 1 and f["gate_counts"]["warn"]              # 1/6 ≈ 17% — 기준(5%, 잠정) 위


def test_funnel_line_warns_and_old_funnel_unchanged():
    old = {"items": 2290, "lane_a": 222, "kept": 8, "cap": "5/8", "to_detect": 5}       # gate_counts 없는 예전 funnel.json (데스크톱 데모)
    assert detect_feed.funnel_line(old) == "수집 2290 → 레인 A 222 → 관문 남김 8 → detect 입력 5 (5/8)"
    lane_a = ([{"gate": {"status": "unclassified", "technologies": []}}] * 6 + [{"gate": {"status": "unclassified", "technologies": ["x"]}}] * 2
              + [{"gate": {"status": "other-stack", "technologies": ["next"]}}] * 4 + [{"gate": {"status": "ok", "keep": True}}] * 2)
    f = {**old, "lane_a": 14, "kept": 2, "cap": "2/2", "to_detect": 2, "gate_counts": detect_feed.policy.gate_counts(lane_a)}
    line = detect_feed.funnel_line(f)
    assert line.startswith("수집 2290 → 레인 A 14 → 관문 남김 2 (모르는 기술 6 · 분류 불가 2 · 팀원 스택 4) → detect 입력 2 (2/2) — 경고: "), line
    assert "80%" in line and "기준 5%, 잠정" in line, line                                # (6+2) / (14−4)
    calm = [{"gate": {"status": "unclassified", "technologies": []}}] + [{"gate": {"status": "ok", "keep": False}}] * 20
    f2 = {**old, "lane_a": 21, "kept": 0, "cap": "0/0", "to_detect": 0, "gate_counts": detect_feed.policy.gate_counts(calm)}
    assert "경고" not in detect_feed.funnel_line(f2)                                         # 1/21 ≈ 4.8% — 기준 아래면 경고 없음


def test_refuses_run_without_gate():
    tmp = tempfile.mkdtemp(prefix="detect-feed-")
    run = make_run(tmp, gated=False)
    try:
        detect_feed.build(run, Path(tmp) / "out")
    except SystemExit as e:
        assert "--gate" in str(e)
    else:
        raise AssertionError("관문 없는 run을 받아들였다")


def test_basis_moves_with_feed():
    """관문의 판정 기준(HYO-162)은 run 폴더 → detect 입력 폴더로 그대로 옮긴다. 없으면 만들지 않는다 (예전 run)."""
    tmp = tempfile.mkdtemp(prefix="detect-feed-")
    run = make_run(tmp)
    b = {"made_at": "2026-10-27T16:30:00+09:00", "repos": [{"repo": "app", "source": "remote", "commit": "abc", "versions": {"acme-sdk": ["1.0"]}}]}
    (run / "basis.json").write_text(json.dumps(b), encoding="utf-8")
    detect_feed.build(run, Path(tmp) / "out")
    assert json.loads((Path(tmp) / "out" / "basis.json").read_text(encoding="utf-8")) == b
    out, _ = built()
    assert not (out / "basis.json").exists()


TESTS = [(n, f) for n, f in globals().items() if n.startswith("test_")]


def mutations():
    orig_key, orig_select = detect_feed.url_key, detect_feed.select
    return [
        ("url 정규화 안 함 (fetch 404)", lambda: setattr(detect_feed, "url_key", lambda u: u), lambda: setattr(detect_feed, "url_key", orig_key)),
        ("상한 무시 (keep 전부)", lambda: setattr(detect_feed, "select", lambda la, all_kept=False: orig_select(la, True)),
         lambda: setattr(detect_feed, "select", orig_select)),
        ("관문 무시 (레인 A 전부)", lambda: setattr(detect_feed, "select", lambda la, all_kept=False: [e for e in la if e["id"] != "unk-1"]),
         lambda: setattr(detect_feed, "select", orig_select)),
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
        missed += not caught
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
