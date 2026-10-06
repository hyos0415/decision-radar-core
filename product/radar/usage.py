"""호출 단위 사용량 집계 (HYO-46 최소판) — transcript*.jsonl의 llm_call 줄을 읽는다.

  python product/radar/usage.py eval/runs/<실행>          # 케이스·flow별 호출 수·토큰·비용 + 호출 목록 요약

호출 한 줄: {"type":"llm_call","seq","flow","agent","model","input_tokens","output_tokens","estimated","cost_usd"[,"error"]}
스텁의 토큰은 추정(estimated=true)이고 비용은 0이다 — 실제 모델 실행에서만 의미가 있다.
"""

import glob
import json
import os
import sys


def calls_in(path):
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line.startswith("{") and '"llm_call"' in line:
            e = json.loads(line)
            if e.get("type") == "llm_call":
                out.append(e)
    return out


def run_calls(out_dir):
    """한 실행 폴더(<case>-<i>)의 모든 transcript에서 호출 기록을 모은다 (capture는 1턴·2턴 두 파일)."""
    calls = []
    for t in sorted(glob.glob(os.path.join(out_dir, "transcript*.jsonl"))):
        calls += calls_in(t)
    return calls


def totals(calls):
    return {"calls": len(calls), "input_tokens": sum(c["input_tokens"] for c in calls),
            "output_tokens": sum(c["output_tokens"] for c in calls),
            "cost_usd": round(sum(c["cost_usd"] for c in calls), 5), "errors": sum(1 for c in calls if c.get("error")),
            "models": sorted({c["model"] for c in calls})}


def summarize(root):
    rows = []
    for d in sorted(glob.glob(os.path.join(root, "*"))):
        if os.path.isdir(d):
            cs = run_calls(d)
            if cs:
                rows.append({"run": os.path.basename(d), "flow": cs[0]["flow"], **totals(cs)})
    return rows


if __name__ == "__main__":
    root = sys.argv[1]
    rows = summarize(root)
    for r in rows:
        print(json.dumps(r, ensure_ascii=False))
    by_flow = {}
    for r in rows:
        by_flow.setdefault(r["flow"], []).append(r)
    for f, rs in sorted(by_flow.items()):
        print(json.dumps({"flow": f, "runs": len(rs), **{k: sum(r[k] for r in rs) for k in ("calls", "input_tokens", "output_tokens", "cost_usd")}}))
