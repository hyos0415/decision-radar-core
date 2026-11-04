"""「놓친 것」 회귀 픽스처를 실제 기록에서 뽑는다 (HYO-171) — 한 번 돌려 결과를 커밋한다. 테스트는 이 스크립트를 부르지 않는다.

  python pipeline/tests/fixtures/missed/make_fixture.py --projects <jhs-decisions · radar-snapshots의 부모> --slots <slots-2026-W4x 폴더들의 부모>

뽑는 것 (주마다 폴더 하나):
  items.jsonl   얼린 코퍼스 radar-snapshots/<주>/items.jsonl에서 5건에 닿는 항목만. 본문은 TEXT_CHARS로 자른다 — changelog diff는
                날짜 블록 쪼개기가 본문을 읽어 통째로 둔다. links는 뺀다
  lane_a.json   그 주에 실제로 돈 관문 판정(pipeline/out/<주>/lane_a.json)에서 그 항목들만 — 「그때」
  run.json      그 run의 건수 · 칸 정리 요약(5/N 상한 라벨) · 스택 이름 (5건 제목에 닿는 이름만)
  now/slots.json · now/slots-raw.json   2026-11-04에 지금 코드로 돌린 자리 질의 결과(구독 claude -p)에서 그 항목들(쪼갠 블록 포함)만 — 「지금」.
                순위는 원래 순위 그대로. 「그때」 run 폴더와 나눠 둔다 (그 주에는 자리 질의가 없었다)
  feed.json     그 주 실제 detect 입력(radar-snapshots/detect/<주>/feed.json)에서 그 항목들만 (W42는 주간 사슬 전이라 없다)
tiers.json = 실제 등급 파일(radar-snapshots/tiers.json)에서 닿는 기술만.
"""
import argparse
import json
from pathlib import Path

TEXT_CHARS = 400
WEEKS = {
    "2026-W42": {"run": None, "ids": ["hn:49996437", "vendor:aws-bedrock:5a50599c4c217241c21e7c6d72c35ac8787ec30c"]},
    "2026-W44": {"run": "2026-W44", "ids": [
        "vendor-diff:openai:https://platform.openai.com/docs/changelog@2026-W44",
        "vendor:aws-bedrock:0c5c946db9a84a5a290bb7e1c3b4577820eb630a",
        "hn:50181114", "reddit:t3_1xbuk6l", "hn:50216791", "hn:50219177"]},
    "2026-W45": {"run": "2026-W45", "ids": ["hn:50248124", "hn:50248075", "vendor:aws-bedrock:0c5c946db9a84a5a290bb7e1c3b4577820eb630a"]},
}
TECHS = ["openai", "openai-agents", "gpt-6-luna", "gpt-6-sol", "amazon bedrock", "amazon bedrock guardrails", "python", "litellm"]
STACK = ["openai", "openai-agents", "gpt-6-luna", "gpt-6-sol", "Amazon Bedrock", "python", "litellm"]


def jdump(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects", required=True)
    ap.add_argument("--slots", required=True)
    a = ap.parse_args()
    root, here = Path(a.projects), Path(__file__).resolve().parent
    for wk, spec in WEEKS.items():
        out = here / wk
        out.mkdir(exist_ok=True)
        ids = set(spec["ids"])
        lines = []
        for l in (root / "radar-snapshots" / wk / "items.jsonl").read_text(encoding="utf-8").splitlines():
            d = json.loads(l)
            if d["id"] in ids:
                d.pop("links", None)
                if (d.get("meta") or {}).get("kind") != "page-diff":
                    d["text"] = (d.get("text") or "")[:TEXT_CHARS]
                lines.append(json.dumps(d, ensure_ascii=False))
        assert len(lines) == len(ids), (wk, len(lines))
        (out / "items.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        if spec["run"]:
            run = root / "jhs-decisions" / "pipeline" / "out" / spec["run"]
            lane_a = [e for e in json.loads((run / "lane_a.json").read_text(encoding="utf-8")) if e["id"] in ids]
            for e in lane_a:
                e.pop("matched_by", None)
            jdump(out / "lane_a.json", lane_a)
            m = json.loads((run / "run.json").read_text(encoding="utf-8"))
            g = m["lane_a_gate"]
            jdump(out / "run.json", {"snapshot": f"radar-snapshots/{wk}", "counts": m["counts"],
                                     "stack": {"terms": [t for t in m["stack"]["terms"] if t in STACK]},
                                     "lane_a_gate": {k: g.get(k) for k in ("model", "mode", "label", "limit", "total_kept")}})
            feed = json.loads((root / "radar-snapshots" / "detect" / wk / "feed.json").read_text(encoding="utf-8"))
            jdump(out / "feed.json", [{"id": e["id"], "kind": e["kind"]} for e in feed if e["id"] in ids])
        mine = lambda i: i.split("#")[0] in ids    # noqa: E731 — 쪼갠 블록(<원래 id>#<날짜>)도
        now = out / "now"
        now.mkdir(exist_ok=True)
        s = json.loads((Path(a.slots) / f"slots-{wk}" / "slots.json").read_text(encoding="utf-8"))
        s["candidate_list"] = [c for c in s["candidate_list"] if mine(c["id"])]
        s["per_slot"] = {k: [x for x in v if mine(x["id"])] for k, v in s["per_slot"].items()}
        s["to_detect"] = [i for i in s["to_detect"] if mine(i)]
        s["note"] = "2026-11-04 지금 코드 자리 질의 결과에서 픽스처 항목만 (make_fixture.py) — 건수 칸은 원래 주 전체"
        jdump(now / "slots.json", s)
        raw = json.loads((Path(a.slots) / f"slots-{wk}" / "slots-raw.json").read_text(encoding="utf-8"))
        jdump(now / "slots-raw.json", {"structured_output": {"judgments": [j for j in raw["structured_output"]["judgments"] if mine(j["id"])]}})
    t = json.loads((root / "radar-snapshots" / "tiers.json").read_text(encoding="utf-8"))
    jdump(here / "tiers.json", {"version": t["version"], "technologies": {k: v for k, v in t["technologies"].items() if k in TECHS},
                                "overrides": {k: v for k, v in t["overrides"].items() if k in TECHS}})


if __name__ == "__main__":
    main()
