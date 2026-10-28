"""프로필 필터 — 스냅숏 → 두 레인 → 레인 B 키워드 기준선 → 판정 시트 → 지표 (HYO-73, 배관 + 기준선).

  python -m pipeline.profile_filter validate --profile <radar-profile.yaml>
  python -m pipeline.profile_filter run --snapshot <주차 폴더|items.jsonl> --profile <yaml> --strategy kw,kw+aliases --out <폴더>
  python -m pipeline.profile_filter pool --run <폴더> --pool-depth 30
  python -m pipeline.profile_filter score --run <폴더> --judgments <시트.csv> --cutoffs 5,10,20

환경 변수: RADAR_PROFILE · RADAR_TARGETS · RADAR_ADR_DIR (collector/run.py와 같은 이름)
이 파일은 배관이다. 전략 선택·가중치·k는 정하지 않는다 (사용자가 dev 주에 실험한다).
"""
import argparse
import json
import os
import sys
from pathlib import Path

from . import judging, keyword_match, lanes
from .interest_profile import ProfileError, load_profile

REPO = Path(__file__).resolve().parents[1]


def read_items(path):
    p = Path(path)
    if p.is_dir():
        p = p / "items.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def load_stack(targets_path=None, adr_dir=None):
    """레인 A·자동 재료. collector/run.py의 targets()·adr_technologies()를 그대로 쓴다 (환경 변수로 경로를 받는다)."""
    if targets_path:
        os.environ["RADAR_TARGETS"] = str(targets_path)
    if adr_dir:
        os.environ["RADAR_ADR_DIR"] = str(adr_dir)
    os.environ.setdefault("PYTHONUTF8", "1")              # radar.py(자식 프로세스)의 출력이 윈도우에서 cp949로 나가는 것을 막는다
    from collector.run import adr_technologies, targets   # 늦게 가져온다 — validate·pool·score는 수집기가 필요 없다
    texts, targets_src = targets()
    deps = []
    for txt in texts.values():
        for t in lanes.stack_terms_from_claude_md(txt):
            if t not in deps:
                deps.append(t)
    adr_techs, adr_src = adr_technologies()
    adr_techs = [t for t in (lanes.clean_term(x) for x in adr_techs) if t]
    return {"dependencies": deps, "adr_technologies": adr_techs, "sources": {"targets": targets_src, "adr_dir": adr_src},
            "terms": sorted(set(deps) | set(adr_techs), key=str.lower)}


def write_json(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")


def run_filter(items, profile, strategies, stack_terms=(), only_new=False, gate=None):
    """순수 계산 (파일을 쓰지 않는다). → {"lane_a": [...], "lane_b": {전략: 순위}, "skipped": n, ...}

    gate: 레인 A 관문 `pipeline.importance.gate.Gate` (HYO-67). 없으면 `gate: null`, `gate_status: "not-implemented"`로 비워 둔다.
    """
    lane_a, lane_b, skipped = lanes.split_lanes(items)
    dropped_old = 0
    if only_new:
        before = len(lane_b)
        lane_b = [it for it in lane_b if it.get("new", True)]
        dropped_old = before - len(lane_b)
    a_out = []
    for it, reasons in lane_a:
        by = {}
        for s in strategies:
            sc = keyword_match.score_item(it, profile, s)
            by[s] = [{"kind": "lane", "lane": "A", "why": reasons}] + (sc["matched_by"] if sc else [])
        verdict = gate(it, reasons) if gate is not None else lanes.GATE
        a_out.append({"id": it["id"], "source": it["source"], "title": it["title"], "url": it["url"],
                      "lane": "A", "reasons": reasons, "gate": verdict, "gate_status": "not-implemented" if verdict is None else "ok",
                      "also_matches_interest": {s: any(e["kind"] == "match" for e in by[s]) for s in strategies},
                      "known_example": bool(keyword_match.known_examples(it, profile)), "matched_by": by})
    b_out = {s: keyword_match.rank_items(lane_b, profile, s) for s in strategies}
    mentions = {it["id"]: m for it in lane_b if (m := lanes.stack_mentions(it, stack_terms))}
    for ranked in b_out.values():
        for e in ranked:
            e["stack_mentions"] = mentions.get(e["id"], [])
    counts = {"items": len(items), "lane_a": len(a_out), "lane_b_candidates": len(lane_b), "skipped_pages": len(skipped),
              "dropped_not_new": dropped_old, "lane_b_ranked": {s: len(r) for s, r in b_out.items()}}
    res = {"lane_a": a_out, "lane_b": b_out, "counts": counts, "texts": {it["id"]: it.get("text", "") for it in lane_b}}
    if gate is not None:
        from .importance.gate import cap_lane_a      # 5/N 상한 — 잘린 것은 summary에 남는다 (버리지 않는다)
        res["gate_summary"] = cap_lane_a(a_out, gate.limit)
        counts["lane_a_kept"] = res["gate_summary"]["total_kept"]
    return res


def cmd_validate(a):
    try:
        profile, warnings = load_profile(a.profile)
    except ProfileError as e:
        print(f"형식 오류 {len(e.errors)}건:")
        for m in e.errors:
            print(f"  - {m}")
        return 1
    n = sum(1 for i in profile["interests"] if i["active"])
    print(f"OK profile@v{profile['version']} — 관심사 {len(profile['interests'])} (active {n}) · 제외 {len(profile['excludes'])}")
    for w in warnings:
        print(f"  주의: {w}")
    return 0


def cmd_run(a):
    profile, warnings = load_profile(a.profile)
    strategies = [s for s in a.strategy.split(",") if s]
    for s in strategies:
        if s not in keyword_match.STRATEGIES:
            raise SystemExit(f"모르는 전략 {s!r} (있는 것: {', '.join(keyword_match.STRATEGIES)})")
    stack = {"terms": [], "sources": {"targets": "건너뜀 (--no-stack)"}}
    if not a.no_stack:
        try:
            stack = load_stack(a.targets, a.adr_dir)
        except Exception as e:  # noqa: BLE001 — 스택을 못 읽어도 레인 분리·순위는 돈다 (표시만 빠진다)
            stack = {"terms": [], "sources": {"error": f"{type(e).__name__}: {e}"}}
    items = read_items(a.snapshot)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    gate = backend = None
    if a.gate:                                   # 레인 A 관문 (HYO-67). stub이면 모델 없이 돈다; 실제 모델은 --real-api가 있어야 한다
        from .importance.cli import build_gate
        if not a.tiers:
            raise SystemExit("--gate에는 --tiers(등급 파일)가 필요하다")
        gate, backend = build_gate(a.gate, a.tiers, real_api=a.real_api, call_log=out / "gate-calls.jsonl", limit=a.gate_limit, targets=a.targets)
    try:
        res = run_filter(items, profile, strategies, stack["terms"], a.only_new, gate=gate)
    finally:
        if backend:
            backend.close()
    write_json(out / "lane_a.json", res["lane_a"])
    for s, ranked in res["lane_b"].items():
        write_json(out / f"lane_b.{s}.json", ranked)
    write_json(out / "texts.json", res["texts"])
    manifest = {"snapshot": str(a.snapshot), "profile": str(a.profile), "profile_version": profile["version"],
                "strategies": strategies, "counts": res["counts"], "stack": stack, "profile_warnings": warnings,
                "weights": {"PRIORITY_WEIGHT": keyword_match.PRIORITY_WEIGHT, "MATCH_BASE": keyword_match.MATCH_BASE,
                            "EXTRA_TERM": keyword_match.EXTRA_TERM, "EXTRA_TERM_CAP": keyword_match.EXTRA_TERM_CAP,
                            "NOT_PENALTY": keyword_match.NOT_PENALTY, "EXCLUDE_PENALTY": keyword_match.EXCLUDE_PENALTY},
                "lane_a_gate": ({"model": a.gate, "tiers": str(a.tiers), **res["gate_summary"], "calls": backend.log.totals(),
                                 "other_stack": getattr(gate, "other_info", None), "versions": getattr(gate, "version_info", None)}
                                if gate is not None else "not-implemented (--gate로 켠다: pipeline.importance)")}
    write_json(out / "run.json", manifest)
    write_json(out / "profile.resolved.json", profile)
    c = res["counts"]
    print(f"profile@v{profile['version']} · 항목 {c['items']} → 레인 A {c['lane_a']} · 레인 B 후보 {c['lane_b_candidates']} "
          f"(페이지 원본 제외 {c['skipped_pages']}) · 순위에 오른 항목 {c['lane_b_ranked']}")
    if gate is not None:
        from .detect_feed import funnel_line
        s = res["gate_summary"]
        print(funnel_line({"items": c["items"], "lane_a": c["lane_a"], "kept": s["total_kept"], "cap": s["label"],
                           "gate_counts": s["status_counts"], "to_detect": len(s["shown"])}))
        for x in s.get("newer_line_eol") or []:      # 판정이 아니라 알림 (HYO-148) — 우리 줄도 지원 밖일 수 있다
            print(f"  알림: {x['technology']} 우리 {', '.join(x['ours'])} < 지원 종료된 {x['announced']} 줄 — 우리 줄도 지원 밖일 수 있다 (확인 필요)")
    print(f"-> {out}")
    return 0


def cmd_pool(a):
    run = Path(a.run)
    manifest = json.loads((run / "run.json").read_text(encoding="utf-8"))
    rankings = {s: json.loads((run / f"lane_b.{s}.json").read_text(encoding="utf-8")) for s in manifest["strategies"]}
    pool, key = judging.build_pool(rankings, a.pool_depth, a.seed)
    texts = json.loads((run / "texts.json").read_text(encoding="utf-8"))
    judging.write_sheet(run / judging.SHEET, pool, texts)
    write_json(run / judging.KEY, key)
    print(f"판정 시트: {run / judging.SHEET} — {len(pool)}건 (전략 {len(rankings)}개 × 상위 {a.pool_depth}, 중복 제거)")
    print("relevant 칸에 1(관련) / 0(무관)만 적는다. pool-key.json은 판정이 끝나기 전에 열지 않는다")
    return 0


def cmd_score(a):
    judgments = a.judgments or str(Path(a.run) / judging.SHEET)
    cutoffs = [int(x) for x in a.cutoffs.split(",") if x]
    try:
        res = judging.score_run(a.run, judgments, cutoffs, a.partial)
    except ValueError as e:
        print(f"계산하지 않음: {e}", file=sys.stderr)
        return 2
    write_json(Path(a.run) / "scores.json", res)
    print(judging.format_scores(res))
    return 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="pipeline.profile_filter")
    sp = ap.add_subparsers(dest="cmd", required=True)
    prof = {"default": os.environ.get("RADAR_PROFILE"), "required": not os.environ.get("RADAR_PROFILE"),
            "help": "radar-profile.yaml (기본: 환경 변수 RADAR_PROFILE)"}
    v = sp.add_parser("validate", help="프로필 형식 검사")
    v.add_argument("--profile", **prof)
    v.set_defaults(fn=cmd_validate)
    r = sp.add_parser("run", help="레인 분리 + 전략별 레인 B 순위")
    r.add_argument("--snapshot", required=True, help="주차 폴더 또는 items.jsonl")
    r.add_argument("--profile", **prof)
    r.add_argument("--strategy", default="kw,kw+aliases", help=f"쉼표로 여러 개 ({', '.join(keyword_match.STRATEGIES)})")
    r.add_argument("--out", required=True)
    r.add_argument("--targets", help="radar-targets.json (기본: RADAR_TARGETS)")
    r.add_argument("--adr-dir", help="ADR 폴더 (기본: RADAR_ADR_DIR)")
    r.add_argument("--no-stack", action="store_true", help="스택 표시(stack_mentions)를 건너뛴다")
    r.add_argument("--only-new", action="store_true", help="스냅숏의 new: false 항목을 레인 B에서 뺀다")
    r.add_argument("--gate", help="레인 A 관문 백엔드: stub | gpt-6-luna | gpt-6.1-sol (HYO-67). 없으면 관문 없이 gate: null")
    r.add_argument("--tiers", help="기술 등급 파일 (python -m pipeline.importance classify-stack으로 만든다)")
    r.add_argument("--real-api", action="store_true", help="실제 API 호출 허용 (stub 외 모델에 필요)")
    r.add_argument("--gate-limit", type=int, help="주당 표시 상한 (기본 5 — importance-policy §4)")
    r.set_defaults(fn=cmd_run)
    p = sp.add_parser("pool", help="사용자 판정용 시트 (전략 상위를 합쳐 중복 제거, 전략·순위 숨김)")
    p.add_argument("--run", required=True)
    p.add_argument("--pool-depth", "--top", dest="pool_depth", type=int, required=True,
                   help="전략마다 상위 몇 개를 판정 풀에 넣나 (판정 분량 — 필터의 k가 아니다)")
    p.add_argument("--seed", default="pool-v1")
    p.set_defaults(fn=cmd_pool)
    s = sp.add_parser("score", help="판정 → Recall@k · nDCG@k · 노이즈 비율")
    s.add_argument("--run", required=True)
    s.add_argument("--judgments", help="판정을 채운 시트 (기본: <run>/judgment-sheet.csv)")
    s.add_argument("--cutoffs", default="5,10,20", help="지표를 재는 자리 (Recall@k의 k) — 필터의 k를 정하는 값이 아니다")
    s.add_argument("--partial", action="store_true", help="판정이 일부만 있어도 계산 (편향 경고)")
    s.set_defaults(fn=cmd_score)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
