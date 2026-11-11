"""엔진 짝 비교 — 얼린 주의 레인 A를 지금 코드로 두 번 다시 건다: 그 주에 기록된 모델 답(기준) vs 새 백엔드의 답 (HYO-169).

- 새 백엔드에는 **그 주에 기록된 답이 있는 소식만** 묻는다 — 양쪽 소식 집합이 같다. 나머지는 둘 다 「기록된 모델 답 없음」(error)
- 코드 · 등급 파일 · 자리 질의(그 주 slots.json)는 양쪽이 같다 → 차이는 변화 분류 모델뿐
- 새 답은 `<out>/answers.jsonl`에 쌓고 다시 돌면 재사용한다 (끊겨도 이어서, 실패는 쌓지 않는다)
- 보고: 변화 라벨 일치(정리 뒤) · 남김(2c) 갈림 · 끝(detect / 목록 / 빠짐) 갈림 — **판정이 아니라 기록** (importance-policy §8 변경 2026-11-11)

  python eval/importance/compare_engines.py --week 2026-W44 --model claude-cli/claude-sonnet-5-5 --plan
  python eval/importance/compare_engines.py --week 2026-W44 --model claude-cli/claude-sonnet-5-5 --real-api --out eval/runs/hyo169/w44

경로: --snapshots(기본 env RADAR_SNAPSHOT_REPO 또는 ../radar-snapshots) · --run(기본 pipeline/out/<주>) · 관심 프로필 env RADAR_PROFILE.
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pipeline import trace as tr  # noqa: E402
from pipeline.importance import policy  # noqa: E402
from pipeline.importance.backends import CallLog, StubBackend, make_backend  # noqa: E402
from pipeline.importance.cli import build_gate  # noqa: E402
from pipeline.interest_profile import load_profile  # noqa: E402
from pipeline.profile_filter import load_stack, read_items  # noqa: E402


def load_answers(path):
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                out[r["id"]] = r["labels"]
    return out


def caching_backend(inner, recorded, path):
    """기록된 답이 있는 소식만 inner에 묻고 답을 쌓는다. 없는 소식은 기준 쪽과 같이 NotRecorded."""
    cache = load_answers(path)

    def answer(q):
        if q.id not in recorded:
            raise tr.NotRecorded(tr.REPLAY_MISSING)
        if q.id not in cache:
            ans = inner.classify_changes(q)          # 실패는 올린다 — 관문이 error로 기록, 쌓지 않는다
            cache[q.id] = list(ans.labels)
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps({"id": q.id, "labels": cache[q.id], "reason": ans.reason}, ensure_ascii=False) + "\n")
        return cache[q.id]
    return StubBackend(changes=answer), cache


def _kept(rec):
    s = next((x for x in rec.get("steps") or [] if x.get("stage") == "2c"), None)
    return None if s is None else s.get("pass") is True


def compare(rec_a, rec_b, recorded, answers, titles):
    a, b = {r["id"]: r for r in rec_a}, {r["id"]: r for r in rec_b}
    ids = sorted(recorded)
    same = [i for i in ids if i in answers and
            policy.normalize_changes(recorded[i], titles.get(i, "")) == policy.normalize_changes(answers[i], titles.get(i, ""))]
    keep_a_only = [i for i in ids if _kept(a.get(i, {})) and not _kept(b.get(i, {}))]
    keep_b_only = [i for i in ids if _kept(b.get(i, {})) and not _kept(a.get(i, {}))]
    det = lambda r: (r or {}).get("end") == "detect"   # noqa: E731
    detect_a_only = sorted(i for i in set(a) | set(b) if det(a.get(i)) and not det(b.get(i)))
    detect_b_only = sorted(i for i in set(a) | set(b) if det(b.get(i)) and not det(a.get(i)))
    return {"recorded": len(ids), "answered": sum(1 for i in ids if i in answers), "labels_same": len(same),
            "kept_a": sum(1 for i in ids if _kept(a.get(i, {}))), "kept_b": sum(1 for i in ids if _kept(b.get(i, {}))),
            "keep_a_only": keep_a_only, "keep_b_only": keep_b_only,
            "detect_a": sum(1 for r in rec_a if det(r)), "detect_b": sum(1 for r in rec_b if det(r)),
            "detect_a_only": detect_a_only, "detect_b_only": detect_b_only}


def report(week, model, res, recorded, answers, titles, tiers_info):
    def line(i):
        return f"- {titles.get(i, i)[:110]} — 기준 {recorded.get(i)} · 새 {answers.get(i, '(답 없음)')}"
    pct = (lambda n, d: f"{n}/{d} ({n / d:.0%})" if d else "0/0")
    out = [f"# {week} 엔진 짝 비교 — 기준(그 주 기록) vs {model}", "",
           f"- 등급 파일 {tiers_info['path']} sha256 {tiers_info['sha256'][:12]}… · 코드 · 자리 질의는 양쪽 같다",
           f"- 기록된 답이 있는 소식 {res['recorded']} · 새 답 {res['answered']} · 변화 라벨 같음(정리 뒤) {pct(res['labels_same'], res['answered'])}",
           f"- 남김(2c) 기준 {res['kept_a']} · 새 {res['kept_b']} — 기준만 {len(res['keep_a_only'])} · 새만 {len(res['keep_b_only'])}",
           f"- detect로 감 기준 {res['detect_a']} · 새 {res['detect_b']} — 기준만 {len(res['detect_a_only'])} · 새만 {len(res['detect_b_only'])}", ""]
    for key, head in (("keep_a_only", "남김 — 기준만"), ("keep_b_only", "남김 — 새만"),
                      ("detect_a_only", "detect — 기준만"), ("detect_b_only", "detect — 새만")):
        out += [f"## {head} ({len(res[key])})", ""] + [line(i) for i in res[key]] + [""]
    return "\n".join(out)


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="compare_engines")
    ap.add_argument("--week", required=True)
    ap.add_argument("--model", required=True, help="새 백엔드 (예 claude-cli/claude-sonnet-5-5 · stub)")
    ap.add_argument("--real-api", action="store_true", help="실제 모델 실행 허용 (구독 CLI 포함)")
    ap.add_argument("--plan", action="store_true", help="새로 물을 소식 수만 세고 끝")
    ap.add_argument("--snapshots")
    ap.add_argument("--run", help="그 주 관문 run 폴더 (lane_a.json · slots.json) — 기본 pipeline/out/<주>")
    ap.add_argument("--tiers", help="등급 파일 (기본 <snapshots>/tiers.json)")
    ap.add_argument("--out", help="결과 폴더 (기본 eval/runs/hyo169/<주>-<모델>)")
    a = ap.parse_args(argv)
    snaps = Path(a.snapshots or os.environ.get("RADAR_SNAPSHOT_REPO") or REPO.parent / "radar-snapshots")
    run = Path(a.run) if a.run else REPO / "pipeline" / "out" / a.week
    tiers = Path(a.tiers) if a.tiers else snaps / "tiers.json"
    out = Path(a.out) if a.out else REPO / "eval" / "runs" / "hyo169" / f"{a.week}-{a.model.replace('/', '-')}"
    items = read_items(snaps / a.week)
    titles = {it["id"]: it.get("title") or "" for it in items}
    recorded = tr.recorded_labels(tr._load(run / "lane_a.json") or [], items)
    todo = [i for i in recorded if i not in load_answers(out / "answers.jsonl")]
    print(f"{a.week}: 기록된 답 {len(recorded)}건 · 새로 물을 것 {len(todo)}건 ({a.model}) → {out}")
    if a.plan:
        return 0
    if a.model != "stub" and not a.real_api:
        print("거부: 실제 모델 실행은 --real-api로 명시한다 (구독 CLI 포함)", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    profile, _ = load_profile(os.environ.get("RADAR_PROFILE") or REPO.parent / "adr" / "radar-profile.yaml")
    stack = load_stack()
    slots, slots_raw = tr._load(run / "slots.json"), tr._load(run / "slots-raw.json")
    inner = make_backend(a.model, allow_real=a.real_api, log=CallLog(out / "calls.jsonl"))
    try:
        gate_a, _ = build_gate("stub", str(tiers), backend=tr.replay_backend(recorded))
        rec_a = tr.replay(items, gate_a, profile, stack["terms"], slots=slots, slots_raw=slots_raw)
        be, answers = caching_backend(inner, recorded, out / "answers.jsonl")
        gate_b, _ = build_gate("stub", str(tiers), backend=be)
        rec_b = tr.replay(items, gate_b, profile, stack["terms"], slots=slots, slots_raw=slots_raw)
    finally:
        inner.close()
    tiers_info = {"path": str(tiers), "sha256": hashlib.sha256(tiers.read_bytes()).hexdigest()}
    res = compare(rec_a, rec_b, recorded, answers, titles)
    text = report(a.week, a.model, res, recorded, answers, titles, tiers_info)
    (out / "report.md").write_text(text + "\n", encoding="utf-8", newline="\n")
    (out / "summary.json").write_text(json.dumps({"week": a.week, "model": a.model, "tiers": tiers_info, "calls": inner.log.totals(), **res},
                                                 ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(text)
    print(f"호출 {inner.log.totals()} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
