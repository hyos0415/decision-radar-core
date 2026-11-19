"""다듬기(dev) 결과 읽기 — 종류별 맞힘 · 소음 · 놓침 · 남김 어긋남 목록 · 두 판 사이 뒤집힘 (HYO-169, 확정 시트 §7 · §10).

판정이 아니라 다듬기용이다. 어떤 모델도 맞힐 수 없는 항목(확정 시트 sets.dev.unreachable)은 뺀다.
  python eval/importance/dev_diag.py --gold eval/importance/gold-hyo169-dev.json RUN_DIR [RUN_DIR2]     # RUN_DIR2가 있으면 짝 비교
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

SAFE = {"security", "model_retirement"}
UNREACHABLE = {"vendor-diff:openai:https://platform.openai.com/docs/changelog@2026-W42"}


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def preds_of(run):
    s = load(Path(run) / "summary.json")
    return s, s["predictions"][0]


def diag(gold, preds):
    items = [it for it in gold["items"] if it["id"] not in UNREACHABLE]
    hit, noise, miss = Counter(), Counter(), Counter()
    false_keep, missed_keep, errors = [], [], []
    for it in items:
        p = preds.get(it["id"]) or {}
        if p.get("status") != "ok":
            errors.append((it, p))
        g, m = set(it["gold_changes"]), set(p.get("changes") or [])
        for c in g & m:
            hit[c] += 1
        for c in m - g:
            noise[c] += 1
        for c in g - m:
            miss[c] += 1
        keep = bool(p.get("keep")) and p.get("status") == "ok"
        if keep and not it["gold_keep"]:
            false_keep.append((it, p))
        if it["gold_keep"] and not keep:
            missed_keep.append((it, p))
    n = len(items)
    kept = sum(1 for it in items if (preds.get(it["id"]) or {}).get("keep"))
    r_t = [it for it in items if it["gold_keep"] and not SAFE & set(it["gold_changes"])]
    s_t = [it for it in items if it["gold_keep"] and SAFE & set(it["gold_changes"])]
    return {"n": n, "kept": kept, "false_keep": false_keep, "missed_keep": missed_keep, "errors": errors,
            "agree": n - len(false_keep) - len(missed_keep),
            "r": (sum(1 for it in r_t if (preds.get(it["id"]) or {}).get("keep")), len(r_t)),
            "safe": (sum(1 for it in s_t if (preds.get(it["id"]) or {}).get("keep")), len(s_t)),
            "hit": hit, "noise": noise, "miss": miss}


def line(it, p):
    why = (p.get("model_reason") or "")[:140]
    return (f"- {it['title'][:80]} — 정답 {it['gold_changes']} · 모델 {p.get('model_changes')} → 정리 {p.get('changes')}"
            + (f" · 상태 {p.get('status')}" if p.get("status") != "ok" else "") + (f"\n  모델 이유: {why}" if why else ""))


def report(name, s, d):
    info = s.get("backend_info") or {}
    out = [f"## {name} — {info.get('model')} · 판 {info.get('prompt_round')} · CLI {info.get('cli_version')} · 묶음 {info.get('batch')} 시드 {info.get('seed')}", "",
           f"- {d['n']}건(맞힐 수 없는 {len(UNREACHABLE)}건 뺌) · 남김 {d['kept']} · 일치 {d['agree']}/{d['n']} ({d['agree'] / d['n']:.1%})"
           f" · 소음 {len(d['false_keep'])}/{d['kept']} · 안전 남김 {d['safe'][0]}/{d['safe'][1]} · R {d['r'][0]}/{d['r'][1]} · 오류 {len(d['errors'])}", "",
           "| 종류 | 맞힘 | 소음(모델만) | 놓침(정답만) |", "|---|---|---|---|"]
    for c in sorted(set(d["hit"]) | set(d["noise"]) | set(d["miss"])):
        out.append(f"| {c} | {d['hit'][c]} | {d['noise'][c]} | {d['miss'][c]} |")
    out += ["", f"### 남겼는데 정답은 버림 ({len(d['false_keep'])})", ""] + [line(it, p) for it, p in d["false_keep"]]
    out += ["", f"### 정답은 남김인데 버림 ({len(d['missed_keep'])})", ""] + [line(it, p) for it, p in d["missed_keep"]]
    if d["errors"]:
        out += ["", f"### 오류 · 분류 불가 ({len(d['errors'])})", ""] + [line(it, p) for it, p in d["errors"]]
    return out


def flips(gold, pa, pb):
    out = []
    for it in gold["items"]:
        if it["id"] in UNREACHABLE:
            continue
        a, b = pa.get(it["id"]) or {}, pb.get(it["id"]) or {}
        if bool(a.get("keep")) != bool(b.get("keep")):
            better = bool(b.get("keep")) == it["gold_keep"]
            out.append(f"- {'좋아짐' if better else '나빠짐'} {it['title'][:80]} — 정답 {it['gold_changes']} {'남김' if it['gold_keep'] else '버림'}"
                       f" · 앞 {a.get('changes')} · 뒤 {b.get('changes')}")
    return out


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True)
    ap.add_argument("runs", nargs="+")
    a = ap.parse_args(argv)
    gold = load(a.gold)
    out, ps = [], []
    for r in a.runs:
        s, p = preds_of(r)
        ps.append(p)
        out += report(Path(r).name, s, diag(gold, p)) + [""]
    if len(ps) == 2:
        f = flips(gold, *ps)
        out += [f"## 남김이 뒤집힌 것 ({len(f)}) — {Path(a.runs[0]).name} → {Path(a.runs[1]).name}", ""] + f
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
