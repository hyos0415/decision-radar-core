"""자리 질의 — 새 모델 소식을 이름이 아니라 「자리」로 대 본다 (HYO-168 나, `docs/design/query-topk.md` §3).

  python -m pipeline.slots run --snapshot ../radar-snapshots/2026-W45 --slots ../adr/radar-slots.yaml --out pipeline/out/2026-W45 [--engine claude|stub] [--reuse]

관문(레인 A)은 「내가 쓰는 이름에 급한 일이 생겼나」를 묻는다. 이 단계는 「내 자리에 들어올 새 후보가 나왔나」를 묻는다 —
GPT-6.1 Terra(관문 분류 자리) · Claude Haiku 5.5(조건 추출 자리)가 이름 기반 관문에서 빠졌다.

1. 후보 모으기 (스크립트, 결정론): 어느 레인이든 새 모델·서비스 출시로 보이는 소식 — 벤더 changelog 블록의 출시 문구 ·
   Bedrock What's New · HN(점수 문턱 + 모델 이름 꼴) · Reddit LocalLLaMA 「New Model」 · HF trending 상위
2. 판정 (모델, 구독 CLI 한 세션): 후보 + 자리 프로필(ADR repo `radar-slots.yaml`) → 후보마다 출시인가 · 맞는 자리 · 적합도 0~3 · 이유.
   스크립트가 id · 자리 이름 · 점수를 검사한다. 원본 응답은 `slots-raw.json`
3. 순위: 자리마다 적합도 → 공식 출처 먼저 → 출처 안 인기 → id. 문턱(FIT_MIN) 이상 top-k. 없으면 「없음」

출력 `<out>/slots.json`: {slots_version, candidates, judged, per_slot: {자리: [후보…]}, to_detect: [자리마다 1위 id], unjudged}
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from .profile_filter import read_items, write_json

TOPK = 3
FIT_MIN = 2              # 자리 적합도 문턱 (0~3) — top-k는 늘 채우므로 「없음」을 허용하는 문턱 (사용자 11/4)
HN_MIN_POINTS = 100
REDDIT_TOP_RANK = 30     # LocalLLaMA 주간 상위 몇 위까지 (New Model flair는 순위 없이 전부)
HF_TOP_RANK = 30
TEXT_CHARS = 300
STRIP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")   # 남아 있으면 claude가 API 키로 과금한다 — 구독으로만 (engines.ClaudeCliRunner와 같다)

RELEASE = re.compile(r"(?i)\b(?:releas(?:e|ed|es|ing)|introduc(?:e|es|ed|ing)|now available|generally available|launch(?:ed|es|ing)?|"
                     r"new model|open[- ]?weights?|weights)\b")
MODEL_NAME = re.compile(r"\b[A-Za-z][A-Za-z]*[- ]?\d+(?:\.\d+)*[A-Za-z]?\b")   # GPT-6.1 · Qwen3.5 · GLM-5.3 · Kimi K3 · 새 이름-2

SYSTEM = """너는 기술 스택 관리자의 조수다. 이번 주 수집한 소식 중 「새 모델·모델 서비스 출시」를 골라, 스택의 각 「자리」에 들어올 후보인지 판정한다.

자리마다 하는 일(role) · 지금 모델(current) · 조건(constraints) · 있으면 좋은 것(prefer)이 주어진다.
후보마다:
- release: 새 모델(가중치 공개 포함)이나 모델 서비스(새 모델 제공 · 새 지역 제공 등)의 출시·공개 소식이면 true. 사용 후기 · 벤치 글 · 도구 · 논문 · 파인튜닝 변형은 false
- matches: release가 true일 때만, 맞는 자리마다 {slot, fit, reason}. fit은
  3 = 그 자리의 지금 모델을 바로 비교·대체할 후보 (조건 모두 충족, 같은 일을 더 낫거나 싸게)
  2 = 조건은 충족하고 비교해 볼 만함
  1 = 관련은 있으나 조건 일부를 못 지키거나 불확실
  0이면 넣지 않는다. 조건(constraints)을 하나라도 못 지키면 최대 1이다
- reason: 한국어 한 문장 — 왜 그 자리인지, 무엇과 비교할지
모든 후보 id에 대해 한 줄씩 답한다. 주어진 글만 근거로 한다 — 모르는 건 지어내지 않고 fit을 낮춘다."""

SCHEMA = {
    "type": "object",
    "properties": {"judgments": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "release": {"type": "boolean"},
                       "matches": {"type": "array", "items": {"type": "object", "properties": {
                           "slot": {"type": "string"}, "fit": {"type": "integer", "minimum": 0, "maximum": 3}, "reason": {"type": "string"}},
                           "required": ["slot", "fit", "reason"]}}},
        "required": ["id", "release", "matches"]}}},
    "required": ["judgments"],
}


class SlotError(RuntimeError):
    pass


def load_slots(path):
    d = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    slots = d.get("slots") or []
    ids = [s.get("id") for s in slots]
    if not slots or any(not i for i in ids) or len(set(ids)) != len(ids):
        raise SlotError(f"{path}: 자리 id가 비었거나 겹친다")
    return {"version": d.get("version"), "slots": slots}


def _channels(it):
    ch = it.get("channels") or []
    return ch if isinstance(ch, list) else [ch]


def _rank(it, prefix):
    vals = [v for k, v in (it.get("signals") or {}).items() if k.startswith(prefix) and isinstance(v, (int, float))]
    return min(vals) if vals else None


def why_candidate(it):
    """새 모델 소식 후보인가 → 이유 문자열 또는 None (결정론)."""
    meta, src, chans = it.get("meta") or {}, it.get("source"), _channels(it)
    title, text = it.get("title") or "", it.get("text") or ""
    if src == "vendor":
        if meta.get("parent") and RELEASE.search(text):
            return f"changelog:{meta.get('vendor')}"
        if "vendor:aws-bedrock" in chans:
            return "bedrock-whats-new"
        return None
    if src == "hn":
        pts = (it.get("signals") or {}).get("points") or 0
        if pts >= HN_MIN_POINTS and (MODEL_NAME.search(title) or RELEASE.search(title)):
            return f"hn:{pts}"
        return None
    if src == "reddit":
        if any("flair:New Model" in c for c in chans):
            return "reddit:new-model"
        r = _rank(it, "rank:r/LocalLLaMA:top-week")
        if r is not None and r <= REDDIT_TOP_RANK and RELEASE.search(title):
            return f"reddit:top-week:{r}"
        return None
    if src == "huggingface" and str(it.get("id", "")).startswith("hf-model:"):
        r = _rank(it, "rank:trending")
        if r is not None and r <= HF_TOP_RANK:
            return f"hf-trending:{r}"
    return None


def popularity(it):
    """출처 안 인기 → 작을수록 먼저. 공식(vendor)은 0."""
    src = it.get("source")
    if src == "vendor":
        return 0
    if src == "hn":
        return -((it.get("signals") or {}).get("points") or 0)
    if src == "reddit":
        return _rank(it, "rank:") or 999
    if src == "huggingface":
        return _rank(it, "rank:trending") or 999
    return 999


def collect(items):
    """후보 목록 (같은 url은 하나만 — 공식 출처 먼저)."""
    out, seen = [], set()
    for it in sorted(items, key=lambda x: (x.get("source") != "vendor", popularity(x), str(x.get("id")))):
        why = why_candidate(it)
        if not why:
            continue
        key = re.sub(r"^https?://(www\.)?", "", it.get("url") or "").rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"id": it["id"], "source": it.get("source"), "why": why, "title": it.get("title") or "", "url": it.get("url") or "",
                    "text": (it.get("text") or "")[:TEXT_CHARS], "popularity": popularity(it)})
    return sorted(out, key=lambda c: str(c["id"]))


def prompt(cands, slots):
    sl = [{k: s.get(k) for k in ("id", "name", "repo", "role", "current", "constraints", "prefer")} for s in slots["slots"]]
    lines = [json.dumps({k: c[k] for k in ("id", "source", "title", "url", "text")}, ensure_ascii=False) for c in cands]
    return ("## 자리\n" + yaml.safe_dump(sl, allow_unicode=True, sort_keys=False) +
            f"\n## 후보 ({len(cands)}건, 한 줄에 하나)\n" + "\n".join(lines) + "\n")


def judge_claude(user, model="claude-sonnet-5-5", effort="medium", timeout=900):
    """구독 `claude -p` 한 번 — 도구 없음 · 설정 안 읽음 · API 키 지움 (HYO-99와 같은 설정). → structured_output, 원본"""
    b = os.environ.get("RADAR_CLAUDE_BIN") or shutil.which("claude") or "claude"
    cmd = [*([sys.executable, b] if b.endswith(".py") else [b]), "-p", "--output-format", "json", "--system-prompt", SYSTEM,
           "--json-schema", json.dumps(SCHEMA, ensure_ascii=False), "--tools", "", "--setting-sources", "",
           "--no-session-persistence", "--model", model, "--effort", effort, "--permission-mode", "dontAsk"]
    env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
    env["PYTHONIOENCODING"] = "utf-8"
    with tempfile.TemporaryDirectory(prefix="radar-slots-") as run:
        p = subprocess.run(cmd, input=user.encode("utf-8"), capture_output=True, cwd=run, env=env, timeout=timeout)
    try:
        res = json.loads(p.stdout.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise SlotError(f"claude 결과가 JSON이 아니다 (exit {p.returncode}) {p.stderr.decode('utf-8', 'replace')[-300:]}")
    if res.get("is_error") or res.get("structured_output") is None:
        raise SlotError(f"claude 실패: {res.get('subtype')} {str(res.get('result'))[:300]}")
    return res["structured_output"], res


def judge_stub(cands, slots):
    """테스트·배관용 — 제목에 자리의 지금 모델 계열 낱말이 있으면 fit 2. 모델 판단이 아니다."""
    out = []
    for c in cands:
        m = []
        for s in slots["slots"]:
            fam = {w.lower() for cur in s.get("current") or [] for w in re.findall(r"[A-Za-z]{3,}", cur)}
            if fam & {w.lower() for w in re.findall(r"[A-Za-z]{3,}", c["title"])}:
                m.append({"slot": s["id"], "fit": 2, "reason": "stub: 계열 낱말 일치"})
        out.append({"id": c["id"], "release": bool(m), "matches": m})
    return {"judgments": out}


def validate(so, cands, slots):
    """모델 답 → {id: 판정}. 모르는 id · 자리 · 범위 밖 점수는 버리고 센다 (규율 1 — 스크립트가 검사)."""
    ids, slot_ids = {c["id"] for c in cands}, {s["id"] for s in slots["slots"]}
    got, dropped = {}, {"unknown_id": 0, "unknown_slot": 0, "bad_fit": 0}
    for j in (so or {}).get("judgments") or []:
        if j.get("id") not in ids:
            dropped["unknown_id"] += 1
            continue
        ms = []
        for m in j.get("matches") or []:
            if m.get("slot") not in slot_ids:
                dropped["unknown_slot"] += 1
            elif not isinstance(m.get("fit"), int) or not 0 <= m["fit"] <= 3:
                dropped["bad_fit"] += 1
            else:
                ms.append({"slot": m["slot"], "fit": m["fit"], "reason": str(m.get("reason") or "")})
        got[j["id"]] = {"release": bool(j.get("release")), "matches": ms if j.get("release") else []}
    return got, dropped


def rank(cands, judged, slots, k=None, fit_min=None):
    k = TOPK if k is None else k
    fit_min = FIT_MIN if fit_min is None else fit_min
    by_id = {c["id"]: c for c in cands}
    per = {s["id"]: [] for s in slots["slots"]}
    for cid, j in judged.items():
        for m in j["matches"]:
            if m["fit"] >= fit_min:
                c = by_id[cid]
                per[m["slot"]].append({"id": cid, "title": c["title"], "url": c["url"], "source": c["source"], "why": c["why"],
                                       "fit": m["fit"], "reason": m["reason"], "popularity": c["popularity"]})
    for s in per:
        per[s] = sorted(per[s], key=lambda x: (-x["fit"], x["source"] != "vendor", x["popularity"], x["id"]))[:k]
        for i, x in enumerate(per[s], 1):
            x["rank"] = i
    return per


def run(items, slots, engine="claude", out=None, reuse=False):
    cands = collect(items)
    raw_path = Path(out) / "slots-raw.json" if out else None
    if reuse and raw_path and raw_path.exists():
        so = json.loads(raw_path.read_text(encoding="utf-8"))["structured_output"]
    elif not cands:
        so = {"judgments": []}
    elif engine == "stub":
        so = judge_stub(cands, slots)
    elif engine == "claude":
        so, res = judge_claude(prompt(cands, slots))
        if raw_path:
            write_json(raw_path, {"engine": "claude-cli", "structured_output": so, "usage": res.get("usage"),
                                  "cost_usd_equiv": res.get("total_cost_usd"), "billing": "subscription"})
    else:
        raise SlotError(f"모르는 엔진 {engine!r} (claude · stub — codex는 HYO-169)")
    judged, dropped = validate(so, cands, slots)
    per = rank(cands, judged, slots)
    to_detect = []
    for s in slots["slots"]:
        if per[s["id"]] and per[s["id"]][0]["id"] not in to_detect:
            to_detect.append(per[s["id"]][0]["id"])
    return {"slots_version": slots.get("version"), "engine": engine, "k": TOPK, "fit_min": FIT_MIN,
            "candidates": len(cands), "judged": len(judged), "releases": sum(1 for j in judged.values() if j["release"]),
            "unjudged": sorted({c["id"] for c in cands} - set(judged)), "dropped": dropped,
            "slot_names": {s["id"]: s.get("name") for s in slots["slots"]},
            "slot_current": {s["id"]: s.get("current") or [] for s in slots["slots"]},
            "per_slot": per, "to_detect": to_detect, "candidate_list": cands}


def summary_line(res):
    hit = {s: len(v) for s, v in res["per_slot"].items() if v}
    return (f"자리 질의: 후보 {res['candidates']} → 출시 {res['releases']} → 자리에 든 것 {sum(hit.values())} "
            f"(자리 {len(hit)}/{len(res['per_slot'])}) · detect {len(res['to_detect'])}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pipeline.slots")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--snapshot", required=True, help="주차 폴더 또는 items.jsonl")
    r.add_argument("--slots", required=True, help="자리 프로필 (ADR repo radar-slots.yaml)")
    r.add_argument("--out", required=True, help="결과 폴더 — profile_filter run과 같은 폴더 (detect_feed가 slots.json을 읽는다)")
    r.add_argument("--engine", default=os.environ.get("RADAR_SLOTS_ENGINE", "claude"), help="claude(구독 claude -p) · stub")
    r.add_argument("--reuse", action="store_true", help="slots-raw.json이 있으면 모델을 다시 부르지 않는다")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    res = run(read_items(a.snapshot), load_slots(a.slots), engine=a.engine, out=out, reuse=a.reuse)
    write_json(out / "slots.json", res)
    print(summary_line(res))
    for s, v in res["per_slot"].items():
        for x in v:
            print(f"  {s} #{x['rank']} fit {x['fit']} | {x['title'][:90]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
