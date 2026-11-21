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


def test_feed_entry_security_advisory():
    # 공식 보안 채널 (HYO-170 다): OSV 공지는 의존성 이름을 달고 공식 문서(R2 통과)로 — 릴리스 노트가 아니다
    e = detect_feed.feed_entry({"id": "osv:MAL-2026-2144@2026-10-29", "title": "litellm (PyPI) security advisory MAL-2026-2144: Malicious code",
                                "url": "https://osv.dev/vulnerability/MAL-2026-2144", "text": "litellm 1.107.0 … backdoor",
                                "meta": {"kind": "advisory", "dependency": "litellm", "ecosystem": "PyPI"}})
    assert (e["kind"], e["source_type"], e["package"]) == ("security_advisory", "official_doc", "litellm") and "version" not in e, e


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


def _adv(i, pkg, affects, malicious=False):
    item = {"id": i, "source": "osv", "channels": ["security-advisories"], "title": f"{pkg} (npm) security advisory {i}: issue",
            "url": f"https://osv.dev/vulnerability/{i}", "text": "details", "meta": {"kind": "advisory", "dependency": pkg, "osv_id": i}}
    g = {"keep": True, "status": "ok", "technologies": [pkg], "kept_by": [{"tier": "L1", "change": "security", "technology": pkg}],
         "reason": f"{pkg} L1 × security", "advisory": {"package": pkg, "affects_ours": affects, "malicious": malicious}}
    return item, {"id": i, "title": item["title"], "url": item["url"], "gate": g}


def test_advisories_quiet_when_not_ours_and_folded_per_package():
    # W44 창 모양 (리뷰 2026-11-04, 사용자 나안): OSV 공지 중 우리 버전 해당 없는 것은 목록에만 · 해당분은 패키지마다 1건 ·
    # 악성 기록은 버전과 상관없이 · 해당 여부를 모르면 detect로 (안전 쪽)
    from pipeline.importance import gate as gate_mod
    pairs = [_adv("GHSA-e1", "electron", False), _adv("GHSA-e2", "electron", False),
             _adv("GHSA-v1", "vite", True), _adv("GHSA-v2", "vite", True), _adv("GHSA-v3", "vite", False),
             _adv("MAL-2026-1", "litellm", False, malicious=True), _adv("GHSA-l1", "litellm", True), _adv("GHSA-m1", "mcp", None)]
    lane_a = [e for _, e in pairs]
    gate_mod.cap_lane_a(lane_a)
    tmp = Path(tempfile.mkdtemp(prefix="detect-feed-adv-"))
    run, snap, out = tmp / "run", tmp / "snap", tmp / "out"
    run.mkdir(), snap.mkdir()
    (snap / "items.jsonl").write_text("".join(json.dumps(it) + "\n" for it, _ in pairs), encoding="utf-8")
    (run / "run.json").write_text(json.dumps({"snapshot": str(snap), "counts": {"items": 8, "lane_a": 8},
                                               "lane_a_gate": {"model": "stub", "label": "x", "total_kept": 8}}), encoding="utf-8")
    (run / "lane_a.json").write_text(json.dumps(lane_a), encoding="utf-8")
    detect_feed.build(run, out)
    feed = {e["id"]: e for e in json.loads((out / "feed.json").read_text(encoding="utf-8"))}
    assert set(feed) == {"GHSA-v1", "MAL-2026-1", "GHSA-m1"}, sorted(feed)          # electron 2 · vite 6.x분은 detect 안 감 · litellm은 악성 기록이 대표
    assert "GHSA-v2" in feed["GHSA-v1"]["summary"] and "GHSA-v3" not in feed["GHSA-v1"]["summary"]   # 접힌 해당분만 대표 입력에
    assert "GHSA-l1" in feed["MAL-2026-1"]["summary"]
    files = json.loads((out / "index.json").read_text(encoding="utf-8"))
    assert "GHSA-v2" in (out / files[detect_feed.url_key(feed["GHSA-v1"]["url"])]["file"]).read_text(encoding="utf-8")
    lst = {x["id"]: x for x in json.loads((out / "list.json").read_text(encoding="utf-8"))["always"]}
    assert set(lst) == {e["id"] for e in lane_a}, sorted(lst)                         # 목록에는 전부
    assert not lst["GHSA-e1"]["to_detect"] and "우리 버전 해당 없음" in lst["GHSA-e1"]["reason"] and lst["GHSA-e1"]["folded_into"] is None
    assert lst["GHSA-e2"]["folded_into"] == "GHSA-e1" and not lst["GHSA-e2"]["to_detect"]   # 해당 없는 공지도 목록에선 패키지마다 한 줄
    assert lst["GHSA-v3"]["folded_into"] is None                                           # 해당분 대표(v1)와 섞지 않는다
    assert lst["GHSA-v2"]["folded_into"] == "GHSA-v1" and lst["GHSA-l1"]["folded_into"] == "MAL-2026-1"
    assert "우리 버전 해당 없음" not in lst["MAL-2026-1"]["reason"] and "우리 버전 해당 없음" not in lst["GHSA-m1"]["reason"]


TERRA_HTML = b"<html><body><h1>GPT-X Terra</h1><p>Context window 1M. $1 input / $5 output.</p></body></html>"


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url, headers=None):
        self.calls.append(url)
        return self.pages.get(url, (None, b""))


def slot_run(fetcher):
    """W45 Terra 모양: 자리 질의가 HN 글을 detect로 보낸다. 글 url(공식 블로그)은 403, 링크 중 공식 모델 문서 · GitHub 릴리스 · 남의 블로그."""
    tmp = Path(tempfile.mkdtemp(prefix="detect-feed-links-"))
    run = make_run(tmp)
    hn = {"id": "hn:1", "source": "hn", "channels": ["hn"], "title": "GPT-X Terra", "url": "https://openai.com/index/introducing-x-terra/",
          "text": "Discussion: 637 points", "meta": {},
          "links": ["https://openai.com/index/introducing-x-terra/", "https://developers.openai.com/api/docs/models/x-terra",
                    "https://someblog.example/terra-review", "https://github.com/acme/sdk/releases/tag/v2.0.0"]}
    with (tmp / "snap" / "items.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(hn) + "\n")
    (run / "slots.json").write_text(json.dumps({"per_slot": {"gate-classifier": [{"id": "hn:1", "rank": 1, "fit": 2, "reason": "새 모델", "title": hn["title"],
                                                                                  "url": hn["url"], "source": "hn"}]},
                                                "slot_names": {"gate-classifier": "관문 분류"}, "candidates": 3, "releases": 1,
                                                "to_detect": ["hn:1"]}), encoding="utf-8")
    out = tmp / "out"
    f = detect_feed.build(run, out, fetcher=fetcher)
    return out, f


def test_community_item_links_fetched_and_frozen():
    # HYO-174: Terra 글이 가리킨 공식 모델 문서가 `404 not in snapshot index`였다 — 이제 그 주에 받아 얼리고 radar.py fetch로 열린다
    fetcher = FakeFetcher({"https://developers.openai.com/api/docs/models/x-terra": (200, TERRA_HTML),
                           "https://openai.com/index/introducing-x-terra": (403, b""),
                           "https://github.com/acme/sdk/releases/tag/v2.0.0": (200, b"BREAKING: drops Python 3.9")})
    out, f = slot_run(fetcher)
    doc = radar(out, "fetch", "https://developers.openai.com/api/docs/models/x-terra")
    assert doc["ok"] and "$5 output" in doc["body"] and doc["source_type_hint"] == "official_doc", doc
    blog = radar(out, "fetch", "https://someblog.example/terra-review")
    assert not blog["ok"] and blog["status"] == 404 and not any("someblog" in u for u in fetcher.calls), fetcher.calls  # 커뮤니티 링크는 받지 않는다 (R2)
    post = radar(out, "fetch", "https://openai.com/index/introducing-x-terra/")
    assert post["ok"] and "637 points" in post["body"] and post["source_type_hint"] == "community", post   # 못 받은 글 url은 HN 본문 그대로
    hn = next(e for e in json.loads((out / "feed.json").read_text(encoding="utf-8")) if e["id"] == "hn:1")
    assert hn["source_type"] == "community" and "https://developers.openai.com/api/docs/models/x-terra (official_doc)" in hn["summary"], hn
    assert "받지 못함 403" in hn["summary"]
    assert f["links"] == {"total": 3, "fetched": 2} and "1차 출처 링크 2/3 받음" in detect_feed.funnel_line(f), f
    assert sum("acme/sdk" in u for u in fetcher.calls) == 1   # 관문 항목(릴리스 노트)은 링크를 받지 않는다 — 커뮤니티 글의 링크로 한 번만


def test_fetched_page_wins_over_post_body_and_no_fetcher_unchanged():
    ok = FakeFetcher({"https://openai.com/index/introducing-x-terra": (200, b"<p>Introducing Terra, our new model.</p>")})
    out, _ = slot_run(ok)
    post = radar(out, "fetch", "https://openai.com/index/introducing-x-terra/")
    assert "Introducing Terra" in post["body"] and post["source_type_hint"] == "official_doc", post   # 같은 url이면 받은 공식 문서가 이긴다
    out2, f2 = slot_run(None)
    assert "links" not in f2 and not list((out2 / "items").glob("*-L*.md"))
    assert "1차 출처 링크" not in (out2 / "feed.json").read_text(encoding="utf-8")


HF_CHECK = {"id": "hf:acme/tiny-judge", "source": "huggingface", "channels": ["huggingface"], "title": "acme/tiny-judge",
            "url": "https://huggingface.co/acme/tiny-judge", "text": "", "created_at": "2026-11-17T00:00:00Z",
            "meta": {"text_file": "cards/tiny-judge.md", "tags": ["en"]}}
HN_CHECK = {"id": "hn:9", "source": "hn", "channels": ["hn"], "title": "Kopii 3 Flash is out", "url": "https://news.ycombinator.com/item?id=9",
            "text": "Discussion: 120 points", "meta": {}, "links": ["https://openai.com/index/kopii-3-flash/", "https://someblog.example/k3"]}


def check_run(fetcher=None, check=True):
    """W47 모양의 자리 질의 결과: 자리 top-k 하나(detect로) + 「확인 필요」 두 줄(HF 모델 카드 · HN 글, detect로는 안 간다) + 스냅숏에 없는 줄 하나."""
    tmp = Path(tempfile.mkdtemp(prefix="detect-feed-check-"))
    run = make_run(tmp)
    snap = tmp / "snap"
    (snap / "cards").mkdir()
    (snap / "cards" / "tiny-judge.md").write_text("Tiny judge model. Korean: no.", encoding="utf-8")
    with (snap / "items.jsonl").open("a", encoding="utf-8") as f:
        for it in (HF_CHECK, HN_CHECK):
            f.write(json.dumps(it) + "\n")
    rows = [{"id": "hf:acme/tiny-judge", "title": HF_CHECK["title"], "url": HF_CHECK["url"], "source": "huggingface", "model": "tiny-judge",
             "slots": ["eval-judge", "gate-classifier"], "reason": "판정 모델이지만 한국어 표시가 없다", "switch": "한국어 확인", "popularity": 3},
            {"id": "hn:9", "title": HN_CHECK["title"], "url": HN_CHECK["url"], "source": "hn", "model": "Kopii 3 Flash", "slots": ["gate-classifier"],
             "reason": "제목뿐 — 구조화 출력 불명", "switch": "", "popularity": 1, "also": ["reddit:k3"]},
            {"id": "gone:1", "title": "x", "url": "https://x.example", "source": "vendor", "model": "x", "slots": ["eval-judge"], "reason": "", "switch": ""}]
    slots = {"per_slot": {"gate-classifier": [{"id": "news-1", "rank": 1, "fit": 2, "reason": "새 모델", "title": "t", "url": ITEMS[1]["url"], "source": "vendor"}]},
             "slot_names": {"gate-classifier": "관문 분류", "eval-judge": "eval judge"},
             "slot_repos": {"gate-classifier": "decision-radar", "eval-judge": "decision-radar"},
             "slot_roles": {"gate-classifier": "변화 종류 분류", "eval-judge": "답 채점"},
             "candidates": 5, "releases": 3, "to_detect": ["news-1"], "check": rows if check else []}
    (run / "slots.json").write_text(json.dumps(slots, ensure_ascii=False), encoding="utf-8")
    out = tmp / "out"
    f = detect_feed.build(run, out, fetcher=fetcher)
    return tmp, out, f


def test_check_json_written_not_sent_to_detect():
    # HYO-245: 「확인 필요」는 detect 입력(feed)에 넣지 않고 check.json으로 — 자리 이름 · repo · 역할 · 소식 전문을 담는다
    fetcher = FakeFetcher({"https://openai.com/index/kopii-3-flash": (200, b"<p>Kopii 3 Flash: JSON mode, $0.1 / MTok.</p>")})
    _tmp, out, f = check_run(fetcher)
    feed_ids = [e["id"] for e in json.loads((out / "feed.json").read_text(encoding="utf-8"))]
    assert "hf:acme/tiny-judge" not in feed_ids and "hn:9" not in feed_ids, feed_ids
    chk = json.loads((out / detect_feed.CHECK).read_text(encoding="utf-8"))
    assert chk["week"] == "snap" and chk["made_at"] and chk["missing"] == ["gone:1"], chk.get("missing")
    hf, hn = chk["items"]
    assert hf["slots"] == [{"id": "eval-judge", "name": "eval judge", "repo": "decision-radar", "role": "답 채점", "component": []},   # 자리 설정에 칸이 없으면 [] (HYO-219)
                           {"id": "gate-classifier", "name": "관문 분류", "repo": "decision-radar", "role": "변화 종류 분류", "component": []}], hf["slots"]
    assert (hf["model"], hf["reason"], hf["switch"]) == ("tiny-judge", "판정 모델이지만 한국어 표시가 없다", "한국어 확인")
    assert hf["item"]["text"] == "Tiny judge model. Korean: no." and hf["item"]["meta"]["tags"] == ["en"], hf["item"]   # text_file은 지금 풀어 둔다
    assert hn["also"] == ["reddit:k3"] and "primary" not in hf["item"]
    prim = {p["url"]: p for p in hn["item"]["primary"]}
    assert "Kopii 3 Flash: JSON mode" in prim["openai.com/index/kopii-3-flash"]["text"] and not any("someblog" in u for u in prim), prim  # 커뮤니티 링크는 안 받는다 (R2)
    assert f["slots"]["check"] == 3 and "확인 필요 3" in detect_feed.funnel_line(f), f["slots"]
    _tmp2, out2, f2 = check_run(check=False)
    assert not (out2 / detect_feed.CHECK).exists() and "check" not in f2["slots"]


def test_check_input_one_item_detect_reads():
    # 사람이 「초안 만들기」를 고른 줄 → detect 한 건 입력. radar.py feed · fetch가 그대로 읽고, gate.json의 자리를 제품 detect가 candidate_for로 받는다
    fetcher = FakeFetcher({"https://openai.com/index/kopii-3-flash": (200, b"<p>Kopii 3 Flash: JSON mode.</p>")})
    tmp, out, _ = check_run(fetcher)
    hf, hn = json.loads((out / detect_feed.CHECK).read_text(encoding="utf-8"))["items"]
    d = tmp / "one-hf"
    detect_feed.check_input(hf, d)
    feed = radar(d, "feed")
    assert len(feed) == 1 and feed[0]["kind"] == "model_release" and feed[0]["source_type"] == "repo" and feed[0]["url"] == HF_CHECK["url"], feed
    body = radar(d, "fetch", HF_CHECK["url"])
    assert body["ok"] and "Korean: no." in body["body"], body
    sys.path.insert(0, str(REPO / "product"))
    from radar import detect_ops
    ctx = type("C", (), {"env": {"RADAR_SNAPSHOT_DIR": str(d)}, "targets": {"decision-radar": "/x"}})()
    hits = detect_ops.slot_reasons(ctx)[HF_CHECK["url"]]
    assert [h["slot"] for h in hits] == ["eval-judge", "gate-classifier"] and hits[0]["repo"] == "decision-radar" and hits[0]["model"] == "tiny-judge", hits
    assert detect_ops.gate_tech(ctx)[HF_CHECK["url"]] == "tiny-judge"          # 기술 이름은 자리 질의가 붙인 모델 이름
    ctx.targets = {"other": "/y"}
    assert detect_ops.slot_reasons(ctx) == {}                                  # 대상 repo가 아닌 자리는 넘기지 않는다 (HYO-205 그대로)
    d2 = tmp / "one-hn"
    detect_feed.check_input(hn, d2)
    doc = radar(d2, "fetch", "https://openai.com/index/kopii-3-flash/")
    assert doc["ok"] and "JSON mode" in doc["body"] and doc["source_type_hint"] == "official_doc", doc   # 얼려 둔 1차 출처가 열린다
    assert "openai.com/index/kopii-3-flash (official_doc)" in radar(d2, "feed")[0]["summary"]


def test_link_type():
    d = detect_feed.official_domains()
    assert {"openai.com", "claude.com"} <= d, d
    assert detect_feed.link_type("https://developers.openai.com/api/docs/models/x", d) == "official_doc"
    assert detect_feed.link_type("https://github.com/acme/sdk/releases/tag/v1", d) == "release_note"
    assert detect_feed.link_type("https://github.com/acme/sdk", d) is None
    assert detect_feed.link_type("https://news.ycombinator.com/item?id=1", d) is None
    assert detect_feed.link_type("https://notopenai.com/x", d) is None   # 끝 두 마디가 다르면 아니다


TESTS = [(n, f) for n, f in globals().items() if n.startswith("test_")]


def mutations():
    from pipeline.importance import policy
    orig_key, orig_select = detect_feed.url_key, detect_feed.select
    orig_quiet, orig_note = policy.quiet_advisory, detect_feed.folded_note
    orig_type, orig_lnote = detect_feed.link_type, detect_feed.links_note
    orig_crows, orig_citem = detect_feed.check_rows, detect_feed.check_item
    orig_cinput = detect_feed.check_input

    def input_without_slots(rec, out_dir):
        e = orig_cinput(rec, out_dir)
        p = Path(out_dir) / "gate.json"
        why = json.loads(p.read_text(encoding="utf-8"))
        p.write_text(json.dumps({u: {**v, "slots": []} for u, v in why.items()}), encoding="utf-8")
        return e
    return [
        ("확인 필요를 안 씀 (HYO-245)", lambda: setattr(detect_feed, "check_rows", lambda *a, **k: ([], [])),
         lambda: setattr(detect_feed, "check_rows", orig_crows)),
        ("확인 필요 소식 전문을 안 풂", lambda: setattr(detect_feed, "check_item", lambda item, snap_dir=None: {k: item.get(k) for k in ("id", "title", "url", "source")}),
         lambda: setattr(detect_feed, "check_item", orig_citem)),
        ("한 건 입력에 자리를 안 적음 (거르기가 뺄 수 있다)", lambda: setattr(detect_feed, "check_input", input_without_slots),
         lambda: setattr(detect_feed, "check_input", orig_cinput)),
        ("모든 링크를 1차 출처로 (R2 깨짐)", lambda: setattr(detect_feed, "link_type", lambda u, d: "official_doc"),
         lambda: setattr(detect_feed, "link_type", orig_type)),
        ("받은 링크를 소식에 안 적음", lambda: setattr(detect_feed, "links_note", lambda rows: ""), lambda: setattr(detect_feed, "links_note", orig_lnote)),
        ("공지 해당 여부 무시", lambda: setattr(policy, "quiet_advisory", lambda e: False), lambda: setattr(policy, "quiet_advisory", orig_quiet)),
        ("악성 기록도 해당 없으면 뺌", lambda: setattr(policy, "quiet_advisory", lambda e: bool(e.get("advisory")) and e["advisory"].get("affects_ours") is False),
         lambda: setattr(policy, "quiet_advisory", orig_quiet)),
        ("모르면 뺌", lambda: setattr(policy, "quiet_advisory", lambda e: bool(e.get("advisory")) and not e["advisory"].get("affects_ours") and not e["advisory"].get("malicious")),
         lambda: setattr(policy, "quiet_advisory", orig_quiet)),
        ("접힌 공지를 detect 입력에 안 붙임", lambda: setattr(detect_feed, "folded_note", lambda items, heading: ""), lambda: setattr(detect_feed, "folded_note", orig_note)),
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
