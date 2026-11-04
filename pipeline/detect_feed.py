"""관문 결과 → detect 입력 (HYO-84, 데모 ②). 수집 스냅숏에서 관문이 남긴 항목만 `radar.py snapshot` 폴더 모양으로 바꾼다.

  python -m pipeline.profile_filter run --snapshot ../radar-snapshots/2026-W41 --gate gpt-6.1-sol --tiers tiers.json --real-api --out pipeline/out/w41
  python -m pipeline.detect_feed --run pipeline/out/w41 --out pipeline/out/w41/detect-snapshot
  .venv/Scripts/python product/radar/run_detect.py --snapshot pipeline/out/w41/detect-snapshot --adr ../adr --out pipeline/out/w41/detect

출력 폴더 (RADAR_SNAPSHOT_DIR로 쓴다 — radar.py `snapshot feed|fetch|search`가 읽는 모양):
  feed.json    소식 목록 (id · title · kind · package · version · summary · url · source_type)
  index.json   url(scheme·끝 / 뺀 것) → {source_type, file} — snapshot_fetch가 전문을 준다
  items/F01.md 항목 전문 (릴리스 노트 · 벤더 소식 본문 · 페이지 변경의 더해진/빠진 줄)
  search.json  빈 목록 (snapshot search는 질의를 무시한다 — 알려진 병목)
  funnel.json  건수 (수집 → 레인 A → 관문 남김 → 표시). 실행 요약 한 줄의 재료
  gate.json    url → 왜 남았나(관문 kept_by). detect 입력에는 넣지 않는다 — 모델 판단을 흔들지 않게 화면(HYO-85)만 읽는다
  basis.json   관문의 판정 기준 — 대상 레포별 커밋·우리 버전 (pipeline.basis, HYO-162). run 폴더에 있으면 그대로 옮긴다.
               detect가 지금 기준과 대조해 이미 반영된 릴리스를 뺀다 (모델에는 안 보인다)
  list.json    이번 주 목록 — 「항상」 · 기술 질의 top-k · 자리 질의 top-k (HYO-168, docs/design/query-topk.md). 화면이 읽는다

무엇을 넣나: 관문이 남기고(keep) detect로 고른 것(shown — 「항상」을 (패키지 × 줄)마다 1건으로 접은 것 + 기술마다 top-k, 2026-11-04 전에는 5/N 상한 안)
+ run 폴더에 slots.json(pipeline.slots)이 있으면 자리마다 top-k 전부 (kind model_release). `--all-kept`면 관문이 남긴 것 전부.
버린 이유(결정표의 행)는 여기 옮기지 않는다 — `lane_a.json`·`gate-calls.jsonl`에 이미 있다 (깔때기 화면은 만들지 않는다, 10/8).
"""
import argparse
import json
import re
import sys
from pathlib import Path

from .basis import BASIS
from .importance import policy
from .profile_filter import read_items, write_json

SUMMARY_CHARS = 1500    # triage 프롬프트에 그대로 들어간다. 전문은 snapshot_fetch로
SLOTS = "slots.json"    # 자리 질의 결과 (pipeline.slots, HYO-168) — run 폴더에 있으면 자리마다 top-k를 detect에 더한다
LIST = "list.json"      # 이번 주 목록 — 「항상」 · 기술 질의 · 자리 질의 (query-topk.md). 화면이 읽는다


def url_key(url):
    """radar.py cmd_snapshot의 fetch와 같은 정규화 — 다르면 모델이 연 url이 404가 된다."""
    return re.sub(r"^https?://", "", url or "").rstrip("/")


def _lines(v):
    if not v:
        return []
    return v.splitlines() if isinstance(v, str) else [str(x) for x in v]


def body_of(item, snapshot_dir=None):
    """항목 전문. 페이지 변경은 더해진 줄(text)과 빠진 줄(meta.removed)을 나눠 적는다."""
    meta = item.get("meta") or {}
    if meta.get("kind") == "page-diff":
        added, removed = _lines(item.get("text")), _lines(meta.get("removed"))
        return "\n".join([f"# {item.get('title', '')}", "", "## 더해진 줄", *(added or ["(없음)"]), "", "## 빠진 줄", *(removed or ["(없음)"])])
    text = item.get("text") or ""
    if not text and meta.get("text_file") and snapshot_dir:
        p = Path(snapshot_dir) / meta["text_file"]
        text = p.read_text(encoding="utf-8") if p.exists() else ""
    return f"# {item.get('title', '')}\n\n{text}".rstrip() + "\n"


def feed_entry(item):
    meta = item.get("meta") or {}
    if meta.get("kind") == "page-diff":
        kind, source_type, text = "page_change", "official_doc", "더해진 줄: " + " / ".join(_lines(item.get("text")))
    elif meta.get("dependency"):
        kind, source_type, text = "dependency_release", "release_note", item.get("text") or ""
    elif meta.get("tag"):
        kind, source_type, text = "vendor_release", "release_note", item.get("text") or ""
    else:
        kind, source_type, text = "vendor_news", "official_doc", item.get("text") or ""
    summary = text if len(text) <= SUMMARY_CHARS else text[:SUMMARY_CHARS].rstrip() + " …(전문은 snapshot_fetch)"
    e = {"id": item["id"], "title": item.get("title", ""), "kind": kind}
    if meta.get("dependency"):
        e["package"] = meta["dependency"]
    if meta.get("tag"):
        e["version"] = meta["tag"]
    e.update({"summary": summary, "url": item["url"], "source_type": source_type})
    return e


def select(lane_a, all_kept=False):
    """관문이 남긴 것 중 detect에 넣을 것 — 기본은 상한 안(shown)."""
    return [e for e in lane_a if (e.get("gate") or {}).get("keep") and (all_kept or e.get("shown"))]


def slot_picks(slots):
    """자리 질의 결과(slots.json) → {항목 id: (자리, 기록)} — 자리마다 top-k 전부 detect로 (query-topk.md §2 변경 2026-11-04:
    처음엔 1위만이었는데 Haiku 5.5(W42)는 2 · 3위, Terra(W44)는 2위라 목록에만 떴다). 같은 항목이 여러 자리에 들면 처음 자리 · 높은 순위."""
    out = {}
    for slot, xs in ((slots or {}).get("per_slot") or {}).items():
        for x in xs:
            if x["id"] not in out or x["rank"] < out[x["id"]][1]["rank"]:
                out[x["id"]] = (slot, x)
    return out


SLOT_SOURCE_TYPE = {"vendor": "official_doc", "huggingface": "repo", "hn": "community", "reddit": "community"}


def slot_entry(item):
    """자리 질의로 온 항목의 feed 칸 — kind는 model_release. HN · Reddit은 community(근거가 아니다 — detect가 1차 출처를 찾는다, R2)."""
    e = feed_entry(item)
    e["kind"] = "model_release"
    e["source_type"] = SLOT_SOURCE_TYPE.get(item.get("source"), "community")
    return e


def week_list(lane_a, slots, detect_ids):
    """이번 주 목록 (query-topk.md §2) — 「항상」 · 기술 질의(top-k) · 자리 질의(top-k). 화면이 읽는다. detect로 간 것은 to_detect."""
    def brief(e):
        g = e.get("gate") or {}
        best = min(g.get("kept_by") or [{}], key=lambda r: (policy.tier_rank(r["tier"]), policy.severity(r["change"])) if r else (99, 99))
        return {"id": e["id"], "title": e.get("title"), "url": e.get("url"), "tier": best.get("tier"), "change": best.get("change"),
                "reason": g.get("reason"), "rank": e.get("rank"), "to_detect": e["id"] in detect_ids,
                "folded_into": e.get("folded_into")}     # 「항상」에서 같은 패키지 · 줄의 더 새 버전으로 접혔으면 그 대표 id (화면이 「외 N건」으로 묶는다)
    listed = [e for e in lane_a if e.get("listed")]
    always = [brief(e) for e in listed if e.get("always")]
    tech = {}
    for e in listed:
        if not e.get("always"):
            tech.setdefault(e.get("query") or "?", []).append(brief(e))
    slot_groups = []
    for slot, xs in ((slots or {}).get("per_slot") or {}).items():
        slot_groups.append({"slot": slot, "name": slots["slot_names"].get(slot), "current": slots.get("slot_current", {}).get(slot) or [],
                            "items": [dict({k: x[k] for k in ("id", "title", "url", "source", "fit", "reason", "rank")}, to_detect=x["id"] in detect_ids)
                                      for x in xs]})
    return {"always": always, "tech": [{"query": q, "items": v} for q, v in tech.items()], "slots": slot_groups,
            "counts": {"always": len(always), "tech": sum(len(v) for v in tech.values()),
                       "slots": sum(len(g["items"]) for g in slot_groups), "to_detect": len(detect_ids)}}


def funnel(manifest, lane_a, chosen, slots=None):
    c = manifest.get("counts") or {}
    g = manifest.get("lane_a_gate") or {}
    statuses = {}
    for e in lane_a:
        s = (e.get("gate") or {}).get("status") or "none"
        statuses[s] = statuses.get(s, 0) + 1
    f = {"snapshot": manifest.get("snapshot"), "gate_model": g.get("model"), "items": c.get("items"), "lane_a": c.get("lane_a"),
         "gate_status": statuses, "gate_counts": policy.gate_counts(lane_a), "kept": g.get("total_kept"), "cap": g.get("label"),
         "to_detect": len(chosen)}
    if g.get("mode") == "topk":
        f["listed"] = len(g.get("listed") or [])
    if slots:
        f["slots"] = {"candidates": slots.get("candidates"), "releases": slots.get("releases"),
                      "listed": sum(len(v) for v in slots["per_slot"].values()), "to_detect": len(slots.get("to_detect") or [])}
    return f


def funnel_line(f):
    """실행 요약 한 줄 — 데스크톱 `_summarize_detect`와 run_detect가 같이 쓴다.
    관문이 판정 못 한 건수(모르는 기술 · 분류 불가 · 팀원 스택)를 같은 줄에 보이고, 비율이 기준을 넘으면 경고를 붙인다 (HYO-122 ⑤ — 조용히 빠지지 않게).
    gate_counts가 없는 예전 funnel.json은 예전 줄 그대로."""
    cap = f" ({f['cap']})" if f.get("cap") else ""
    kept = f"관문 남김 {f.get('kept')}"
    gc = f.get("gate_counts")
    if gc:
        kept += f" (모르는 기술 {gc['unknown']} · 분류 불가 {gc['unresolved']} · 팀원 스택 {gc['other_stack']}" + (f" · 오류 {gc['error']}" if gc.get("error") else "") + ")"
    line = f"수집 {f.get('items')} → 레인 A {f.get('lane_a')} → {kept} → detect 입력 {f.get('to_detect')}{cap}"
    if f.get("slots"):
        s = f["slots"]
        line += f" · 자리 질의 후보 {s['candidates']} → 출시 {s['releases']} → 목록 {s['listed']} (detect {s['to_detect']})"
    if gc and gc.get("warn"):
        line += (f" — 경고: 관문이 판정 못 한 소식 {gc['ratio']:.0%} (모르는 기술+분류 불가 / 팀원 스택 뺀 레인 A — 기준 {gc['warn_ratio']:.0%}, 잠정)")
    return line


def build(run_dir, out_dir, snapshot=None, all_kept=False):
    run_dir, out = Path(run_dir), Path(out_dir)
    manifest = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    if not isinstance(manifest.get("lane_a_gate"), dict):
        raise SystemExit(f"{run_dir}는 관문 없이 돌린 결과다 — profile_filter run에 --gate·--tiers를 준다")
    lane_a = json.loads((run_dir / "lane_a.json").read_text(encoding="utf-8"))
    slots = json.loads((run_dir / SLOTS).read_text(encoding="utf-8")) if (run_dir / SLOTS).exists() else None
    snap = Path(snapshot or manifest["snapshot"])
    by_id = {it["id"]: it for it in read_items(snap)}
    chosen = select(lane_a, all_kept)
    taken = {e["id"] for e in chosen}
    for sid, (slot, x) in slot_picks(slots).items():   # 자리 질의 top-k (HYO-168) — 관문이 이미 넣은 항목이면 한 번만
        if sid not in taken:
            chosen.append({"id": sid, "slot": slot, "slot_hit": x})
            taken.add(sid)
    missing = [e["id"] for e in chosen if e["id"] not in by_id]
    if missing:
        raise SystemExit(f"스냅숏 {snap}에 없는 항목 {len(missing)}개 (다른 주의 run인가?): {missing[:3]}")
    snap_dir = snap if snap.is_dir() else snap.parent
    (out / "items").mkdir(parents=True, exist_ok=True)
    feed, index, why = [], {}, {}
    for i, e in enumerate(chosen, 1):
        item = by_id[e["id"]]
        entry = slot_entry(item) if e.get("slot") else feed_entry(item)
        name = f"items/F{i:02d}.md"
        (out / name).write_text(body_of(item, snap_dir), encoding="utf-8", newline="\n")
        feed.append(entry)
        index[url_key(item["url"])] = {"source_type": entry["source_type"], "file": name}
        if e.get("slot"):
            x = e["slot_hit"]
            why[item["url"]] = {"id": e["id"], "reason": f"자리 {slots['slot_names'].get(e['slot']) or e['slot']} · 적합도 {x['fit']}: {x['reason']}",
                                "kept_by": [], "shown": True, "slot": e["slot"], "fit": x["fit"]}
        else:
            g = e["gate"]
            why[item["url"]] = {"id": e["id"], "reason": g.get("reason"), "kept_by": g.get("kept_by") or [], "shown": e.get("shown")}
    f = funnel(manifest, lane_a, chosen, slots)
    write_json(out / "feed.json", feed)
    write_json(out / "index.json", index)
    write_json(out / "search.json", [])
    write_json(out / "funnel.json", f)
    write_json(out / "gate.json", why)
    write_json(out / LIST, week_list(lane_a, slots, {e["id"] for e in chosen}))
    if (run_dir / BASIS).exists():
        (out / BASIS).write_bytes((run_dir / BASIS).read_bytes())
    return f


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="pipeline.detect_feed", description=__doc__.split("\n")[0])
    ap.add_argument("--run", required=True, help="profile_filter run --gate의 결과 폴더 (run.json · lane_a.json)")
    ap.add_argument("--out", required=True, help="detect가 읽을 스냅숏 폴더 (RADAR_SNAPSHOT_DIR)")
    ap.add_argument("--snapshot", help="주차 폴더 (기본: run.json에 적힌 것)")
    ap.add_argument("--all-kept", action="store_true", help="주당 상한에 잘린 것까지 넣는다")
    a = ap.parse_args(argv)
    f = build(a.run, a.out, a.snapshot, a.all_kept)
    print(funnel_line(f))
    print(f"-> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
