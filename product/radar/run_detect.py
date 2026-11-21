"""스냅숏 폴더 하나로 제품 detect를 한 번 돈다 (HYO-84, 데모 ②) — eval 케이스가 아니라 실제 수집 주차를 넣는 길.

  .venv/Scripts/python product/radar/run_detect.py --snapshot pipeline/out/w41/detect-snapshot --adr ../adr --out pipeline/out/w41/detect
  .venv/Scripts/python product/radar/run_detect.py ... --model anthropic --real-api     # 실제 Claude (API 과금, .env 키)

입력 스냅숏은 `python -m pipeline.detect_feed`가 만든다 (수집 스냅숏 → 관문 → radar.py snapshot 모양).
안전: ADR repo는 **사본**(`<out>/adr`)에만 쓴다 — 원본 `--adr`은 읽기만 한다. RADAR_DRY_RUN=1을 강제하고 RADAR_EXPORT는 지운다(내보내기·push 없음).
네트워크는 NetGuard가 막는다 (스텁: 전부, anthropic: api.anthropic.com만).
결과: <out>/report.json (run_report) · transcript.jsonl (도구 호출 + llm_call) · summary.json (건수 한 줄 + 초안 목록) · adr/ (사본 + 새 초안)
"""

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import sys

# 스크립트 폴더(product/radar)를 경로에서 빼고 product/를 넣는다 (resume.py와 같다)
sys.path[0] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(sys.path[0])
sys.path.insert(1, REPO)

from radar import detect_ops, runtime  # noqa: E402
from radar.adr_copy import copy_adr  # noqa: E402
from radar import script as stub_scripts  # noqa: E402
from common.core import SKILL_RADAR, Ctx  # noqa: E402
from common.eval_adapter import ANTHROPIC  # noqa: E402
from common.guard import NetGuard  # noqa: E402


def corpus_files(snap, weeks):
    """detect 입력 `<snapshots>/detect/<주>`에서 같은 주까지의 코퍼스 `<snapshots>/<주>/items.jsonl` 최근 weeks개 (HYO-201).
    radar.py `snapshot search`가 RADAR_CORPUS로 읽는다 — 0이면 끈다(예전처럼 search.json)."""
    if weeks <= 0:
        return []
    week = os.path.basename(os.path.normpath(snap))
    root = os.path.dirname(os.path.dirname(os.path.normpath(snap)))
    have = sorted(w for w in os.listdir(root) if re.fullmatch(r"\d{4}-W\d{2}", w) and w <= week
                  and os.path.exists(os.path.join(root, w, "items.jsonl")))
    if week not in have:
        raise SystemExit(f"--corpus-weeks: {root}/{week}/items.jsonl이 없다 — --snapshot은 radar-snapshots/detect/<주>여야 한다")
    return [os.path.join(root, w, "items.jsonl") for w in have[-weeks:]]


def summarize(rep, funnel, adr_dir):
    """데스크톱 `_summarize_detect`와 같은 줄 + 건수 한 줄 (funnel.json이 있으면)."""
    from pipeline.detect_feed import funnel_line
    cands = rep.get("candidates") or []
    lines = [funnel_line(funnel)] if funnel else []
    lines.append(f"후보 {len(cands)}건 · 이미 있는 결정이라 건너뜀 {len(rep.get('skipped_existing') or [])}건 · "
                 f"제외 {len(rep.get('excluded') or [])}건 · R10 버림 {len(rep.get('dropped') or [])}건")
    un = rep.get("unused") or {}
    if un.get("verified") or un.get("unverified"):  # 「안 쓴다」며 버린 소식 — 찾아보니 0곳 / 찾아본 기록과 안 맞음 (HYO-205)
        lines.append(f"코드에 없어 뺀 소식 {len(un.get('verified') or [])}건 (찾아봄) · 확인 안 된 「미사용」 {len(un.get('unverified') or [])}건 — 사람이 본다")
    sec = rep.get("security") or {}
    sc = sec.get("slot_candidates") or {}
    if sc:  # 자리 질의가 고른 후보 — 거르기가 다시 빼지 못한다 (HYO-205)
        lines.append(f"자리 질의 후보 {sc.get('total', 0)}건 · 거르기가 버려 되돌림 {len(sc.get('rescued') or [])}건 · 되돌리지 못함 {len(sc.get('excluded') or [])}건")
    if "total" in sec:  # 관문이 security로 남긴 소식 — 거르기가 못 뺀다 (HYO-205 「안 쓴다」 → HYO-206 이유와 무관)
        lines.append(f"보안 소식 {sec.get('total', 0)}건 · 거르기가 버려 되돌림 {len(sec.get('rescued') or [])}건 · 되돌리지 못함 {len(sec.get('excluded') or [])}건")
    tr = rep.get("triage") or {}
    if tr:  # 거르기의 답을 코드가 맞춘 것 (HYO-206)
        lines.append(f"같은 내용이라 다른 후보에 붙임 {len(tr.get('folded') or [])}건 · 거르기가 답을 빠뜨려 다시 물음 {len(tr.get('missing') or [])}건 · "
                     f"끝내 답 없음 {len(tr.get('unanswered') or [])}건 · 관문 이름으로 고친 기술 이름 {len(tr.get('renamed') or [])}건")
        lines += detect_ops.r16_lines(tr)
    drafts = []
    for c in cands:
        f = next(iter(sorted(p for p in os.listdir(adr_dir) if p.startswith(f"{c['adr']}-"))), None) if c.get("adr") else None
        drafts.append({"adr": c.get("adr"), "file": f, "technology": c["technology"], "source_urls": c["source_urls"],
                       "validate_errors": c.get("validate_errors") or []})
    lines.append(f"초안 {len(drafts)}건 작성 (proposed)" if drafts else "새 초안 없음")
    return {"summary": lines, "drafts": drafts}


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--snapshot", required=True, help="detect가 읽을 스냅숏 폴더 (pipeline.detect_feed 출력)")
    ap.add_argument("--adr", required=True, help="ADR repo 폴더 (docs/adr · radar-targets.json) — 읽기만, 사본에 쓴다")
    ap.add_argument("--out", required=True, help="결과 폴더 (새 폴더여야 한다)")
    ap.add_argument("--model", choices=["stub", "anthropic", "claude-cli"], default="stub",
                    help="claude-cli = 구독 `claude -p` 엔진 (HYO-99) — API 과금 없음, 구독 사용량을 쓴다")
    ap.add_argument("--real-api", action="store_true", help="실제 API 호출 허용 (--model anthropic에 필요)")
    ap.add_argument("--corpus-weeks", type=int, default=0,
                    help="snapshot_search가 얼린 코퍼스 최근 N주를 BM25로 검색한다 (HYO-201, 기본 0 = 끔)")
    ap.add_argument("--now", help="ISO 시각 (기본: 지금)")
    a = ap.parse_args(argv)
    if a.model == "anthropic" and not a.real_api:
        raise SystemExit("--model anthropic은 과금이다 — --real-api를 같이 준다")
    snap = os.path.abspath(a.snapshot)
    if not os.path.exists(os.path.join(snap, "feed.json")):
        raise SystemExit(f"{snap}/feed.json이 없다 — python -m pipeline.detect_feed로 먼저 만든다")
    out = os.path.abspath(a.out)
    if os.path.exists(os.path.join(out, "adr")):
        raise SystemExit(f"{out}/adr가 이미 있다 — 실행마다 새 폴더를 쓴다 (사본에 지난 초안이 섞이지 않게)")
    os.makedirs(out, exist_ok=True)
    adr_dir, targets = copy_adr(a.adr, os.path.join(out, "adr"))

    env = {k: v for k, v in os.environ.items() if k != "RADAR_EXPORT"}
    env.update({"RADAR_ADR_DIR": adr_dir, "RADAR_TARGETS": targets, "RADAR_DRY_RUN": "1", "RADAR_SNAPSHOT_DIR": snap,
                "RADAR_SERVED_LOG": os.path.join(out, "served.log"), "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false",
                "RADAR_CORPUS": os.pathsep.join(corpus_files(snap, a.corpus_weeks))})
    ctx = Ctx.from_env(env, radar_py=SKILL_RADAR)
    now = a.now or dt.datetime.now().astimezone().isoformat(timespec="seconds")
    rt = runtime.Runtime()
    spec = {"kind": "stub", "script": stub_scripts.detect_script()} if a.model == "stub" else {"kind": a.model}
    shapes = rt.new_shapes(spec)
    with NetGuard(() if a.model == "stub" else ANTHROPIC) as ng:
        rep, diag = asyncio.run(rt.detect(ctx, rt.make_model(spec, shapes, "detect"), now, rt.settings(spec)))

    fpath = os.path.join(snap, "funnel.json")
    funnel = json.load(open(fpath, encoding="utf-8")) if os.path.exists(fpath) else None
    s = summarize(rep, funnel, adr_dir)
    s.update({"now": now, "model": rt.settings(spec), "snapshot": snap, "p3_errors": diag.get("p3_errors") or [],
              "usage": {**{k: diag.get(k) for k in ("model_calls", "input_tokens", "output_tokens")},  # 비용은 호출마다 llm_call 줄에 있다
                        "cost_usd": None if a.model == "claude-cli" else round(sum(e.get("cost_usd") or 0 for e in ctx.events if e.get("type") == "llm_call"), 5),
                        "billing": "subscription" if a.model == "claude-cli" else ("stub" if a.model == "stub" else "api"),
                        "cost_usd_equiv": round(sum(e.get("cost_usd_equiv") or 0 for e in ctx.events if e.get("type") == "llm_call"), 5)},
              "net_attempts": ng.attempts})
    if s["p3_errors"]:
        s["summary"].append(f"오류 {len(s['p3_errors'])}건: " + "; ".join(e.get("kind", "?") for e in s["p3_errors"]))
    for name, obj in (("report.json", rep), ("summary.json", s)):
        with open(os.path.join(out, name), "w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    with open(os.path.join(out, "transcript.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        for e in ctx.events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print("\n".join(s["summary"]))
    for d in s["drafts"]:
        print(f"  {d['adr']} {d['technology']} — {d['file']}" + (f" (validate 실패 {len(d['validate_errors'])})" if d["validate_errors"] else ""))
    print(f"-> {out}")
    return 1 if s["p3_errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
