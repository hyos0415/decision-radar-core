"""벤더 changelog diff 쪼개기(pipeline/page_entries.py, HYO-168 가) 오프라인 테스트.

  python pipeline/tests/test_page_entries.py

2026-W44 OpenAI changelog diff를 줄인 모양으로 재현한다: 메뉴 줄 · 「Oct 26 · Released GPT-6.1 Terra」 · 「Oct 22 · gpt-6-luna Decisions API」가
한 항목에 섞여 관문이 luna 줄로만 판정했다. 쪼개면 Terra가 제 항목이 되고, detect 입력까지 제 url로 간다.
뒤쪽 변이 검사(규율 3): 쪼개지 않거나, deprecations도 쪼개거나, 목차 날짜 줄을 빈 항목으로 남기면 위 테스트가 빨개져야 한다.
"""
import json
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pipeline import detect_feed, page_entries, profile_filter  # noqa: E402

CHANGELOG = "https://platform.openai.com/docs/changelog"


def diff(url=CHANGELOG, text="", removed="", vendor="openai"):
    return {"id": f"vendor-diff:{vendor}:{url}@2026-W44", "source": "vendor", "channels": [f"vendor:{vendor}"],
            "title": f"[{vendor}] {url} 변경 (2026-W43 → 2026-W44)", "url": url, "created_at": "2026-10-27T07:00:02Z",
            "text": text, "signals": {"added_lines": len(text.splitlines()), "removed_lines": 0}, "links": [],
            "meta": {"vendor": vendor, "kind": "page-diff", "previous": "2026-W43", "removed": removed},
            "first_seen": "2026-W44", "new": True}


W44_OPENAI = "\n".join([
    "Scheduled automations", "Model catalogs",
    "Oct 26", "Feature", "gpt-6.1-terra", "v1/responses",
    "Released GPT-6.1 Terra , a fast, efficient model for visual reasoning, precise computer use, and clear, natural writing.",
    "Oct 22", "Update", "gpt-6-luna", "v1/decisions", "The Decisions API is now generally available with gpt-6-luna .",
    "Oct 20", "Feature", "You can now choose 12-hour prompt caching in the API.",
])


def test_terra_gets_its_own_item():
    out = page_entries.split_item(diff(text=W44_OPENAI))
    terra = [o for o in out if "terra" in o["text"].lower()]
    assert len(terra) == 1, [o["id"] for o in out]
    t = terra[0]
    assert "luna" not in t["text"].lower(), t["text"]
    assert t["id"].endswith("#oct-26") and t["url"] == CHANGELOG + "#oct-26", (t["id"], t["url"])
    assert "Oct 26" in t["title"] and "gpt-6.1-terra" in t["title"], t["title"]
    assert t["meta"]["entry_date"] == "Oct 26" and t["meta"]["parent"].endswith("@2026-W44")
    assert t["meta"]["kind"] == "page-diff"            # 레인 A · 관문 · detect 입력은 페이지 변경으로 다룬다


def test_menu_lines_stay_on_original_id():
    out = page_entries.split_item(diff(text=W44_OPENAI))
    orig = [o for o in out if o["id"] == "vendor-diff:openai:" + CHANGELOG + "@2026-W44"]
    assert len(orig) == 1 and orig[0]["text"] == "Scheduled automations\nModel catalogs", orig
    assert len(out) == 4, [o["id"] for o in out]       # 메뉴 + Oct 26 · Oct 22 · Oct 20


def test_no_head_no_removed_drops_original():
    out = page_entries.split_item(diff(text="Oct 26\nReleased X"))
    assert [o["id"][-7:] for o in out] == ["#oct-26"], [o["id"] for o in out]


def test_removed_lines_keep_original():
    out = page_entries.split_item(diff(text="Oct 26\nReleased X", removed="gpt-old row"))
    assert len(out) == 2 and out[0]["meta"]["removed"] == "gpt-old row" and out[1]["meta"]["removed"] == ""


def test_anthropic_toc_dates_dropped_and_merged():
    text = "\n".join(["October 23, 2026 ", "October 22, 2026 ",       # 목차 — 내용 없는 날짜 줄
                      "October 23, 2026", "We launched the Models API v2.", "October 22, 2026", "Fixed a bug."])
    out = page_entries.split_item(diff(url="https://docs.claude.com/en/release-notes/api", text=text, vendor="anthropic"))
    assert [o["meta"].get("entry_date") for o in out] == ["October 23, 2026", "October 22, 2026"], [o["id"] for o in out]
    assert out[0]["text"] == "We launched the Models API v2."


def test_deprecations_not_split():
    text = "Shutdown date\nModel\nDec 11, 2026\ngpt-5\nDec 11, 2026\ngpt-5-mini"
    it = diff(url="https://platform.openai.com/docs/deprecations", text=text)
    assert page_entries.split_item(it) == [it]


def test_no_dates_untouched():
    it = diff(text="Some new paragraph\nAnother line")
    assert page_entries.split_item(it) == [it]


def test_non_diff_untouched():
    it = {"id": "rel-1", "url": "https://github.com/acme/changelog/releases/tag/v1", "text": "Oct 26\nx", "meta": {}}
    assert page_entries.split_item(it) == [it]


def test_date_line_shapes():
    for s in ["Oct 26", "October 23, 2026", "## September 22, 2026", "Sep 18, 2026", "2026-10-26", "Oct 3rd, 2026",
              '## November 4, 2026  {% id="11-04-2026" %}',
              "October 23, 2026 "]:
        assert page_entries.date_of(s), s
    for s in ["v1/decisions", "May 2026", "Last updated 2026-10-26 UTC.", "Decisions API Oct 26 launch", "Feature"]:
        assert page_entries.date_of(s) is None, s


def test_read_items_and_detect_feed_roundtrip():
    """profile_filter.read_items가 쪼개고, detect_feed가 같은 함수로 읽어 블록 항목을 제 url로 detect 입력에 넣는다."""
    with tempfile.TemporaryDirectory() as d:
        snap = Path(d) / "snap"
        snap.mkdir()
        (snap / "items.jsonl").write_text(json.dumps(diff(text=W44_OPENAI)) + "\n", encoding="utf-8")
        items = profile_filter.read_items(snap)
        ids = [i["id"] for i in items]
        assert len(ids) == 4 and any(i.endswith("#oct-26") for i in ids), ids
        terra_id = next(i for i in ids if i.endswith("#oct-26"))
        run = Path(d) / "run"
        run.mkdir()
        (run / "run.json").write_text(json.dumps({"snapshot": str(snap), "lane_a_gate": {"model": "stub"}, "counts": {"items": 4}}), encoding="utf-8")
        gate = {"keep": True, "status": "ok", "reason": "test", "kept_by": [], "rows": []}
        (run / "lane_a.json").write_text(json.dumps([{"id": terra_id, "url": CHANGELOG + "#oct-26", "title": "t", "lane": "A",
                                                      "gate": gate, "gate_status": "ok", "shown": True}]), encoding="utf-8")
        out = Path(d) / "out"
        detect_feed.build(run, out)
        feed = json.loads((out / "feed.json").read_text(encoding="utf-8"))
        assert [f["url"] for f in feed] == [CHANGELOG + "#oct-26"], feed
        body = (out / "items" / "F01.md").read_text(encoding="utf-8")
        assert "Released GPT-6.1 Terra" in body and "Decisions API" not in body, body


GEMINI_MD = """# Release notes

## November 4, 2026  {% id="11-04-2026" %}

*   **Voice remixing for Gemini 3.8 TTS**

## November 2, 2026  {% id="11-02-2026" %}

*   **Gemini 2.5 Pro deprecation**: shut down on 2026-12-01

## October 26, 2026  {% id="10-26-2026" %}

*   Released gemini-3.6-flash"""


def test_gemini_markdown_anchor_dates_split():
    """HYO-172 — Gemini 마크다운 원문은 날짜 머리글 뒤에 `{% id %}` 꼬리가 붙는다. 떼지 않으면 날짜 줄이 하나도 안 잡혀 통째 한 항목이다."""
    url = "https://aistudio.google.com/docs/changelog.md"
    out = page_entries.split_item(diff(url, GEMINI_MD, vendor="google-gemini"))
    assert [o["meta"].get("entry_date") for o in out] == [None, "November 4, 2026", "November 2, 2026", "October 26, 2026"], out
    assert out[2]["url"] == url + "#november-2-2026" and "2.5 Pro deprecation" in out[2]["text"]


def test_parse_date_and_blocks_after():
    import datetime as dt
    P = page_entries.parse_date
    assert P("October 26, 2026") == dt.date(2026, 10, 26) and P("Oct. 3rd, 2026") == dt.date(2026, 10, 3) and P("2026-10-26") == dt.date(2026, 10, 26)
    assert P("Oct 26") is None and P("Oct 26", 2026) == dt.date(2026, 10, 26) and P("Feb 30, 2026") is None
    assert page_entries.latest_date(GEMINI_MD) == dt.date(2026, 11, 4)
    after = page_entries.blocks_after(GEMINI_MD, dt.date(2026, 10, 26))   # 10/26은 이미 본 날 — 그 뒤만
    assert after[0].startswith("## November 4") and not any("3.6-flash" in l for l in after) and any("2.5 Pro" in l for l in after), after
    assert page_entries.blocks_after(GEMINI_MD, dt.date(2026, 11, 4)) == []


TESTS = [(n, f) for n, f in globals().items() if n.startswith("test_")]


def mutations():
    orig_split, orig_url, orig_date_of, orig_tag = page_entries.split_item, page_entries.SPLIT_URL, page_entries.date_of, page_entries._TAG
    import re

    def keep_empty(item):          # 목차 날짜 줄도 항목으로 — 빈 블록이 관문을 탄다
        out = orig_split(item)
        if len(out) > 1:
            out.append(dict(out[-1], id=out[-1]["id"] + "-empty", text=""))
        return out

    return [
        ("쪼개지 않음", lambda: setattr(page_entries, "split_item", lambda it: [it]),
         lambda: setattr(page_entries, "split_item", orig_split)),
        ("deprecations도 쪼갬", lambda: setattr(page_entries, "SPLIT_URL", re.compile(".")),
         lambda: setattr(page_entries, "SPLIT_URL", orig_url)),
        ("빈 블록을 남김", lambda: setattr(page_entries, "split_item", keep_empty),
         lambda: setattr(page_entries, "split_item", orig_split)),
        ("아이콘 글자를 안 뗌", lambda: setattr(page_entries, "date_of",
                                          lambda line: (line.strip().lstrip("#").strip() if page_entries.DATE_LINE.match(line.strip().lstrip("#").strip()) else None)),
         lambda: setattr(page_entries, "date_of", orig_date_of)),
        ("앵커 꼬리를 안 뗌", lambda: setattr(page_entries, "_TAG", re.compile(r"(?!x)x")),
         lambda: setattr(page_entries, "_TAG", orig_tag)),
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
