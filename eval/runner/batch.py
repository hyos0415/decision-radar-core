"""여러 케이스 × k회를 동시 실행 수 제한 하에 돌리고 요약한다.

  python batch.py --flow revisit [--cases JHS-001,JHS-002] [--k-override N] [--jobs 6] [--model sonnet] --python <py>

산출물: eval/runs/<시각>-batch/ (케이스별 <case>-<i>/ + summary.json + summary.md)
집계 규칙(cases/README.md): pass_pow_k·fallback_call_rate → k회 전부 통과해야 통과,
pass_rate_ci → 통과 횟수/k 와 95% Wilson 구간. 안전 불변식은 모든 케이스에서 pass^k로 따로 센다.
"""

import argparse
import concurrent.futures as cf
import datetime as dt
import glob
import json
import math
import os
import shutil
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_case as rc  # noqa: E402

lock = threading.Lock()


def wilson(x, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = x / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0, c - h), 3), round(min(1, c + h), 3))


def job(case, i, root, model, py):
    out_dir = os.path.join(root, f"{case['id']}-{i}")
    os.makedirs(out_dir)
    work = tempfile.mkdtemp(prefix="jhs-")
    try:
        if case["flow"] == "capture":
            import capture
            g = capture.job(case, work, model, py, out_dir)
        else:
            before = rc.build(case, work)
            events, wall, code = rc.run(case, work, model, py, out_dir)
            g = rc.grade(case, before, work, events)
            g["diag"].update(wall_s=round(wall, 1), exit_code=code)
    except Exception as e:  # 한 회차의 인프라 실패가 배치 전체를 죽이지 않게 — 실패로 기록
        g = {"case": case["id"], "pass": False, "safety_pass": False, "infra_error": repr(e), "checks": [], "diag": {}}
    json.dump(g, open(os.path.join(out_dir, "grade.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    shutil.copytree(work, os.path.join(out_dir, "work"), dirs_exist_ok=True)
    shutil.rmtree(work, ignore_errors=True)
    with lock:
        failed = [c["check"] for c in g["checks"] if not c["ok"]]
        print(f"{case['id']}-{i}: {'PASS' if g['pass'] else 'FAIL'}"
              f"{'' if g['safety_pass'] else ' [SAFETY]'} {failed[:3] if failed else ''} {g.get('infra_error', '')}", flush=True)
    return g


def main():
    sys.stdout.reconfigure(encoding="utf-8")  # Windows cp949 콘솔에서 요약 출력(—)이 크래시 (2026-09-30 스모크)
    ap = argparse.ArgumentParser()
    ap.add_argument("--flow", default="revisit")
    ap.add_argument("--cases")
    ap.add_argument("--k-override", type=int)
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--python", default=sys.executable)
    a = ap.parse_args()

    cases = [json.loads(l) for f in sorted(glob.glob(os.path.join(rc.EVAL, "cases", "batch-*.jsonl")))
             for l in open(f, encoding="utf-8") if l.strip()]
    cases = [c for c in cases if c["flow"] == a.flow]
    if a.cases:
        want = set(a.cases.split(","))
        cases = [c for c in cases if c["id"] in want]
    root = os.path.join(rc.EVAL, "runs", dt.datetime.now().strftime("%Y%m%d-%H%M%S") + f"-batch-{a.flow}")
    os.makedirs(root)
    jobs = [(c, i + 1) for c in cases for i in range(a.k_override or c["k"])]
    print(f"{len(cases)} cases, {len(jobs)} sessions, jobs={a.jobs}, model={a.model} → {root}", flush=True)
    t0 = dt.datetime.now()
    results = {}
    with cf.ThreadPoolExecutor(a.jobs) as ex:
        futs = {ex.submit(job, c, i, root, a.model, a.python): c["id"] for c, i in jobs}
        for f in cf.as_completed(futs):
            results.setdefault(futs[f], []).append(f.result())

    rows = []
    for c in cases:
        gs = results.get(c["id"], [])
        n, x = len(gs), sum(g["pass"] for g in gs)
        sx = sum(g["safety_pass"] for g in gs)
        fails = {}
        for g in gs:
            for ch in g["checks"]:
                if not ch["ok"]:
                    fails[ch["check"]] = fails.get(ch["check"], 0) + 1
            if g.get("infra_error"):
                fails["infra_error"] = fails.get("infra_error", 0) + 1
        agg = c["aggregation"]
        verdict = (x == n) if agg in ("pass_pow_k", "fallback_call_rate") else None
        tc = [g["diag"].get("tool_calls") for g in gs if g["diag"].get("tool_calls") is not None]
        ws = [g["diag"].get("wall_s") for g in gs if g["diag"].get("wall_s") is not None]
        rows.append({"id": c["id"], "category": c["category"], "aggregation": agg, "k": n, "passes": x,
                     "pass_rate": round(x / n, 3) if n else None, "ci95": wilson(x, n), "verdict_pow_k": verdict,
                     "safety_passes": sx, "safety_pow_k": sx == n, "fail_checks": fails,
                     "tool_calls_mean": round(sum(tc) / len(tc), 1) if tc else None,
                     "wall_s_mean": round(sum(ws) / len(ws), 1) if ws else None, "title": c["title"]})
    summary = {"model": a.model, "flow": a.flow, "sessions": len(jobs),
               "elapsed_min": round((dt.datetime.now() - t0).total_seconds() / 60, 1), "cases": rows}
    json.dump(summary, open(os.path.join(root, "summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    md = [f"# 배치 요약 — {a.flow} · {a.model}", "",
          f"- 세션 {len(jobs)} · 소요 {summary['elapsed_min']}분", "",
          "| case | category | 집계 | 통과 | 판정 | 안전 pass^k | 도구 호출 평균 | 실패한 검사 |", "|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (r["pass_rate"] if r["pass_rate"] is not None else 2)):
        v = ("✅" if r["verdict_pow_k"] else "❌") if r["verdict_pow_k"] is not None else f"{r['pass_rate']} {r['ci95']}"
        md.append(f"| {r['id']} | {r['category']} | {r['aggregation']} | {r['passes']}/{r['k']} | {v} | "
                  f"{'✅' if r['safety_pow_k'] else '❌ ' + str(r['k'] - r['safety_passes'])} | {r['tool_calls_mean']} | "
                  f"{'; '.join(f'{k}×{n}' for k, n in r['fail_checks'].items())} |")
    open(os.path.join(root, "summary.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
