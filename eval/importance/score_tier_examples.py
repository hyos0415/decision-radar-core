"""HYO-124 채점 — 등급 분류에 §2 예시를 넣기 전(before)·뒤(after) 등급 파일을 검토본(효과 값)에 대어 본다.

등록: eval/importance/tier-examples.md §1. 결과를 본 뒤 이 채점 방식을 바꾸지 않는다 (규율 7).

  python eval/importance/score_tier_examples.py --gold <검토본 tiers.json> --before b1.json b2.json b3.json --after a1.json a2.json a3.json

- 모델 답 = 등급 파일 기록의 `tiers`(스크립트 검증 S1~S3 뒤의 값). 분류 불가면 답 없음(틀림으로 세고 따로도 센다)
- 정답 = 검토본의 효과 값(TierStore.effective — 자동 분류 위에 사람 수정을 덮은 값). 등급 집합이 같아야 일치
- 예시 기술(EXAMPLE_TECHS)과 그 밖을 따로 센다 — 예시 기술은 프롬프트에 답이 들어 있다

변경 (2026-10-16, HYO-128 — 결과 0건): `--t3`를 주면 T3(그 밖 분류 불가 평균이 before보다 늘지 않는다)도 판정에 넣는다.
주지 않으면 HYO-124(§1-4)와 같은 판정이다. 등록: tier-examples.md §3.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pipeline.importance.signals import norm  # noqa: E402
from pipeline.importance.tiers import TierStore  # noqa: E402

# importance-policy §2-1 예 열 · §4 두 등급 예에 이름이 나오고 지금 스택(+stack-extra)에 있는 기술 (2026-10-16, 24개)
EXAMPLE_TECHS = ["python", "vllm", "llama.cpp", "langfuse", "fastapi", "pydantic", "uvicorn", "jinja2",
                 "boto3", "anthropic", "openai", "google-genai", "typesafe-sdk", "kiwipiepy", "pdfplumber", "torch",
                 "transformers", "pytest", "ruff", "k6", "python-dotenv", "tzdata", "requests", "httpx"]
OUT_TOLERANCE = 2.0     # 예시 밖 일치 수(k 평균)가 이만큼까지 떨어지는 것은 해로 보지 않는다


def answer(rec):
    if rec is None or rec.get("unresolved"):
        return None
    return tuple(sorted(rec.get("tiers") or []))


def load_run(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))["technologies"]


def score_run(run, gold_keys, gold):
    ex = {norm(n) for n in EXAMPLE_TECHS}
    out = {"ex": 0, "out": 0, "ex_unres": 0, "out_unres": 0, "missing": []}
    for k in gold_keys:
        rec = run.get(k)
        if rec is None:
            out["missing"].append(k)
        a = answer(rec)
        side = "ex" if k in ex else "out"
        if a is None:
            out[f"{side}_unres"] += 1
        elif a == gold[k]:
            out[side] += 1
    return out


def majority(runs, k):
    c = Counter(answer(r.get(k)) for r in runs)
    return c.most_common(1)[0][0], c


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True)
    ap.add_argument("--before", nargs="+", required=True)
    ap.add_argument("--after", nargs="+", required=True)
    ap.add_argument("--json", help="결과 JSON을 여기에")
    ap.add_argument("--t3", action="store_true", help="T3도 판정에 넣는다 (HYO-128)")
    a = ap.parse_args(argv)

    store = TierStore(a.gold)
    before = [load_run(p) for p in a.before]
    after = [load_run(p) for p in a.after]
    keys = sorted(set().union(*[set(r) for r in before + after]))      # 이번 측정에서 분류한 기술 = 스택 + stack-extra
    gold = {}
    for k in keys:
        name = next(r[k]["technology"] for r in before + after if k in r)
        eff = store.effective(name)
        gold[k] = None if eff is None or eff.get("unresolved") else tuple(sorted(eff.get("tiers") or []))
    no_gold = [k for k in keys if gold[k] is None]
    keys = [k for k in keys if gold[k] is not None]
    ex = {norm(n) for n in EXAMPLE_TECHS}
    n_ex, n_out = sum(1 for k in keys if k in ex), sum(1 for k in keys if k not in ex)
    missing_ex = sorted(ex - set(keys))

    res = {"n": len(keys), "n_ex": n_ex, "n_out": n_out, "no_gold": no_gold, "example_not_in_stack": missing_ex, "arms": {}}
    print(f"채점 기술 {len(keys)}개 (예시 {n_ex} · 그 밖 {n_out}) · 정답 없음 {no_gold} · 스택에 없는 예시 {missing_ex}")
    for arm, runs in (("before", before), ("after", after)):
        per = [score_run(r, keys, gold) for r in runs]
        mean = {f: sum(p[f] for p in per) / len(per) for f in ("ex", "out", "ex_unres", "out_unres")}
        res["arms"][arm] = {"runs": per, "mean": mean}
        print(f"{arm:6}: 예시 일치 {[p['ex'] for p in per]} 평균 {mean['ex']:.2f}/{n_ex} · "
              f"그 밖 일치 {[p['out'] for p in per]} 평균 {mean['out']:.2f}/{n_out} · "
              f"분류 불가 예시 {[p['ex_unres'] for p in per]} 그 밖 {[p['out_unres'] for p in per]}"
              + (f" · 기록 없음 {per[0]['missing']}" if per[0]["missing"] else ""))

    mb, ma = res["arms"]["before"]["mean"], res["arms"]["after"]["mean"]
    c1 = ma["ex"] > mb["ex"]
    c2 = ma["out"] >= mb["out"] - OUT_TOLERANCE
    c3 = ma["out_unres"] <= mb["out_unres"]
    k_ok = min(len(before), len(after)) >= 3
    res["criteria"] = {"T1_example_up": c1, "T2_out_no_harm": c2, "k_ok": k_ok,
                       "adopt": bool(c1 and c2 and k_ok and (c3 or not a.t3))}
    if a.t3:
        res["criteria"]["T3_out_unres_no_rise"] = c3
    print(f"T1 예시 일치가 오른다: {mb['ex']:.2f} → {ma['ex']:.2f} → {'통과' if c1 else '불통과'}")
    print(f"T2 그 밖 일치가 {OUT_TOLERANCE}개 넘게 떨어지지 않는다: {mb['out']:.2f} → {ma['out']:.2f} → {'통과' if c2 else '불통과'}")
    if a.t3:
        print(f"T3 그 밖 분류 불가 평균이 늘지 않는다: {mb['out_unres']:.2f} → {ma['out_unres']:.2f} → {'통과' if c3 else '불통과'}")
    print(f"판정: {'채택' if res['criteria']['adopt'] else ('k<3 — 스모크, 판정하지 않는다' if not k_ok else '채택하지 않는다')}")

    print("\n다수결 답이 두 쪽에서 다른 기술 — 기술 | 예시? | before | after | 정답")
    diffs = []
    for k in keys:
        b, cb = majority(before, k)
        a_, ca = majority(after, k)
        if b != a_ or cb.most_common(1)[0][1] < len(before) or ca.most_common(1)[0][1] < len(after):
            row = {"tech": k, "example": k in ex, "before": dict((str(x), n) for x, n in cb.items()),
                   "after": dict((str(x), n) for x, n in ca.items()), "gold": list(gold[k])}
            diffs.append(row)
            mark = "" if b != a_ else " (흔들림만)"
            print(f"  {k:28} {'예시' if k in ex else '':4} {row['before']!s:28} {row['after']!s:28} {list(gold[k])}{mark}")
    res["diffs"] = diffs
    if a.json:
        Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
