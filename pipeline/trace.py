"""탈락 추적 (HYO-171) — 항목마다 어느 단계를 지나 어디서 왜 빠졌나.

  python -m pipeline.trace Terra --week 2026-W44                    # 그 주 제목 · URL이 맞는 항목마다 (낱말은 전부 들어야 맞는다)
  python -m pipeline.trace litellm backdoor --week 2026-W44 --now   # 지금 코드로 다시 — 모델 답은 그 주에 기록된 것만 쓴다 (호출 없음)
  python -m pipeline.trace "Haiku 5.5" --week 2026-W42 --text       # 본문까지 찾는다

「관문」은 실제로는 여러 단계다. 단계 이름은 고정이다 — 코드 · 문서(docs/decisions/2026-11-04-missed-items.md) · 요약 줄에 같은 이름.
  1   레인 나누기          pipeline/lanes.py — 수집기가 붙인 통로로 A(내 스택에 일어난 일) · B(새로 볼 만한 것)
  2a  기술 소속            gate.resolve_technologies — 어느 스택 기술의 소식인가 (팀원 스택 · 모르는 기술 · 등급 분류 불가 · 스택에서 뺀 기술이면 여기서 끝)
  2b  변화 분류            모델 (+ 스크립트 정리 policy.normalize_changes · 후속 모델 successor.py). 오류면 여기서 끝
  2c  등급 × 변화 결정표    policy.decide — 남김 · 버림
  2d  우리 것 좁히기        scope.narrow — 우리 버전 · 우리 모델이 아닌 eol · 모델 은퇴를 뺀다 (코드에서는 2c 앞에 돈다)
  3   칸 정리              policy.apply_topk — 질의별 top-k · 「항상」 접기 (2026-11-04 전 run은 5/N 상한)
  4   자리 질의            pipeline/slots.py — 새 모델 소식을 「자리」에 대 본다
  경보 그물                pipeline/alarm.py — 레인 B의 스택 · 런타임 이름 + 사고 낱말 → 관문
  5   detect              SKILL.md 2차 확인 · basis 대조 (run_detect의 report.json을 줄 때만)
  레인 B 순위 · 레인 C 화제는 detect로 이어지지 않는 길이다 — 「레인 B 순위에만 있음」 「막다른 길」로 보인다.

trace 파일: detect_feed가 실행마다 `<detect 폴더>/trace.jsonl`(radar-snapshots detect/<주>/)을 쓴다 — 항목마다 한 줄 {id, end, steps: [[stage, pass, note]]}.
읽으면(read) 기록 {id, title, url, source, signal, steps: [{stage, name, pass, note}], end, stops: [{stage, name, note}]} — 제목 · URL · 빠진 곳은 코퍼스에서 붙인다.
end = detect(detect 입력까지 감) · list(목록에만) · out(빠짐) · unknown(그 주 detect 입력 feed.json이 없어 모른다).
pass = true(통과) · false(여기서 끝) · null(기록만 — 레인 B 순위 · 화제처럼 detect로 이어지지 않는 길, 또는 판단 없음).
명령은 그 파일이 있으면 읽고, 없으면(예전 주) run 폴더의 lane_a.json · lane_b.*.json · alarm.json · slots.json에서 다시 만든다.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from . import alarm, lanes, slots as slots_mod, topics
from .importance import policy

REPO = Path(__file__).resolve().parents[1]
TRACE = "trace.jsonl"

STAGES = {"1": "레인 나누기", "2a": "기술 소속", "2b": "변화 분류", "2c": "등급 × 변화 결정표", "2d": "우리 것 좁히기",
          "3": "칸 정리", "4": "자리 질의", "net": "경보 그물", "5": "detect", "B": "레인 B 순위", "C": "레인 C 화제"}
ORDER = ["1", "net", "2a", "2b", "2d", "2c", "3", "B", "C", "4", "5"]   # 보여 주는 순서 = 항목이 지나는 순서

REPLAY_MISSING = "기록된 모델 답 없음"     # --now 재생에서 그 주에 모델 답이 없는 항목 (모델을 부르지 않는다)


def step(stage, ok, note):
    """ok: True(통과) · False(여기서 끝) · None(기록만 — 이 길은 detect로 이어지지 않거나 판단이 없다)."""
    return {"stage": stage, "name": STAGES[stage], "pass": ok, "note": note}


def signal(it):
    s, src = it.get("signals") or {}, it.get("source")
    if src == "hn" and s.get("points") is not None:
        return f"HN {s['points']}점"
    ranks = {k: v for k, v in s.items() if k.startswith("rank:") and isinstance(v, (int, float))}
    if ranks:
        k = min(ranks, key=ranks.get)
        return f"{k[5:]} {ranks[k]}위"
    ch = it.get("channels") or []
    return ", ".join(ch) if isinstance(ch, list) else str(ch)


# ---------------------------------------------------------------------------- 관문 (2a~3)

def _scope_removed(g):
    """2d가 뺀 것 [(기술, 변화, 이유)]."""
    out = []
    for s in g.get("scope") or []:
        for n in s.get("notes") or []:
            if n.get("verdict") == "other":
                if n["change"] == "eol":
                    ev = "; ".join(f"{e['technology']} {e['announced']} (우리 {', '.join(e['ours']) or '?'})" for e in n.get("evidence") or [])
                    why = f"지원 종료 줄이 우리 줄이 아니다 — {ev}"
                else:
                    why = "은퇴하는 모델이 우리 모델이 아니다"
                out.append((s["technology"], n["change"], why))
    return out


def gate_steps(g, entry, cap):
    """관문 판정 하나(gate 칸) + 칸 정리 → 단계들. entry는 lane_a 항목(shown · listed · rank …), cap은 run.json의 lane_a_gate."""
    st = g.get("status")
    techs = ", ".join(g.get("technologies") or [])
    if st == "other-stack":
        return [step("2a", False, f"팀원 스택(수집 전용 대상)에만 있는 기술 — {techs}")]
    if st == "retired":
        return [step("2a", False, "; ".join(f"{x['technology']} — {x['why']}" for x in g.get("skipped") or []) or f"스택에서 뺀 기술 — {techs}")]
    if st == "unclassified":
        return [step("2a", False, f"등급 분류 불가 — {techs}" if techs else "소식이 걸린 스택 기술을 모른다 (제목 · 벤더 · 의존성 이름 어디에도)")]
    tiers = "; ".join(f"{t} {'·'.join(v)}" for t, v in (g.get("tiers") or {}).items())
    out = [step("2a", True, f"{techs} → {tiers}" if tiers else techs)]
    if st == "error":
        r = g.get("reason") or ""
        out.append(step("2b", False, f"{REPLAY_MISSING} — 재생은 모델을 부르지 않는다" if REPLAY_MISSING in r else r))
        return out
    model, changes = g.get("model_changes"), g.get("changes") or []
    note = "모델: " + (", ".join(model) if model else "(없음)")
    if model is not None and list(model) != list(changes):
        note += " → 정리: " + (", ".join(changes) or "(없음)")
    if g.get("defaulted_change"):
        note += " → 종류 없음이라 minor·patch로 친다"
    if g.get("successor"):
        note += " · 후속 모델(스크립트): " + ", ".join(f"{s['technology']} → {s['mentioned']}" for s in g["successor"])
    out.append(step("2b", True, note))
    removed = _scope_removed(g)
    lost = [r for r in removed if any(policy.TABLE[t][r[1]] == policy.KEEP for t in (g.get("tiers") or {}).get(r[0], []))]
    if removed:
        out.append(step("2d", False if (lost and not g.get("keep")) else True,
                        "뺌: " + "; ".join(f"{t} {policy.CHANGE_LABEL[c]} — {why}" for t, c, why in removed)))
    if not g.get("keep"):
        adj = sorted({a for r in g.get("rows") or [] if r.get("base") == policy.KEEP for a in r.get("adjusted_by") or []})
        out.append(step("2c", None if (lost and removed) else False,
                        (g.get("reason") or "남기는 행 없음") + (" — 표는 ✅지만 보정으로 버림: " + "; ".join(adj) if adj else "")))
        return out
    out.append(step("2c", True, "남김: " + (g.get("reason") or "")))
    out.append(cap_step(entry, cap))
    return out


def cap_step(entry, cap):
    cap = cap or {}
    if cap.get("mode") == "topk":
        k = cap.get("k") or policy.TOPK
        q = entry.get("query") or "?"
        if entry.get("shown"):
            return step("3", True, "「항상」 칸 — detect로" if entry.get("always") else f"기술 질의 {q} {entry.get('rank')}위 — detect로")
        if entry.get("listed"):
            adv = (entry.get("gate") or {}).get("advisory")     # OSV 공지 (HYO-170 다)
            if adv and policy.quiet_advisory({"advisory": adv}):
                return step("3", False, "목록에만 — 우리 버전 해당 없는 보안 공지 (OSV — 이미 고쳐진 버전 · 다른 줄)"
                            + (f" · 같은 패키지 공지와 한 줄로 → {entry['folded_into']}" if entry.get("folded_into") else ""))
            if entry.get("folded_into") and adv:
                return step("3", False, f"목록에만 — 같은 패키지 보안 공지로 접힘(제목 · 링크는 그 공지 detect 입력에) → {entry['folded_into']}")
            if entry.get("folded_into") and entry.get("alarm"):
                return step("3", False, f"목록에만 — 「항상」 같은 기술 경보의 더 인기 있는 글로 접힘(제목 · 링크는 그 글 detect 입력에) → {entry['folded_into']}")
            if entry.get("folded_into"):
                return step("3", False, f"목록에만 — 「항상」 같은 패키지 · 줄의 더 새 항목으로 접힘 → {entry['folded_into']}")
            return step("3", False, f"목록에만 — 기술 질의 {q} {entry.get('rank')}위 (detect 몫 밖)")
        return step("3", False, f"기술 질의 {q} {entry.get('rank')}위 — top-{k} 밖")
    label = cap.get("label") or "5/N"
    if entry.get("shown"):
        return step("3", True, f"주당 상한 {label} 안 — detect로 (2026-11-04 전 5/N 상한)")
    return step("3", False, f"주당 상한 {label}에서 잘림 (2026-11-04 전 5/N 상한)")


# ---------------------------------------------------------------------------- 자리 질의 (4)

def slot_index(slots, slots_raw):
    """→ {"cands": {id: 후보}, "judged": {id: 판정}, "per": {id: [(자리, 기록)]}, "fit_min", "k", "names"} | None."""
    if not slots:
        return None
    per = {}
    for s, xs in (slots.get("per_slot") or {}).items():
        for x in xs:
            per.setdefault(x["id"], []).append((s, x))
    judged = {}
    so = (slots_raw or {}).get("structured_output") or slots_raw
    for j in (so or {}).get("judgments") or []:
        judged[j.get("id")] = j
    return {"cands": {c["id"]: c for c in slots.get("candidate_list") or []}, "judged": judged, "per": per,
            "fit_min": slots.get("fit_min", slots_mod.FIT_MIN), "k": slots.get("k", slots_mod.TOPK), "names": slots.get("slot_names") or {}}


def slot_step(it, si):
    if si is None:
        return None
    iid = it["id"]
    if iid not in si["cands"]:
        why = slots_mod.why_candidate(it)
        return step("4", None, f"후보였을 텐데 같은 URL의 다른 항목으로 합쳐짐 ({why})" if why else "후보 아님")
    c = si["cands"][iid]
    name = lambda s: si["names"].get(s) or s   # noqa: E731
    if iid in si["per"]:
        return step("4", True, "후보(" + c["why"] + ") → " + "; ".join(f"자리 {name(s)} {x['rank']}위 · 적합도 {x['fit']}: {x['reason']}" for s, x in si["per"][iid]))
    j = si["judged"].get(iid)
    if j is None:
        return step("4", False, f"후보({c['why']}) → 판정 기록 없음" + (" (slots-raw.json이 없다)" if not si["judged"] else " (모델 답에 빠짐)"))
    if not j.get("release"):
        return step("4", False, f"후보({c['why']}) → 모델: 출시 소식이 아니다")
    ms = sorted(j.get("matches") or [], key=lambda m: -m.get("fit", 0))
    if not ms or ms[0]["fit"] < si["fit_min"]:
        best = f" (가장 높은 것: 자리 {name(ms[0]['slot'])} 적합도 {ms[0]['fit']} — {ms[0].get('reason', '')})" if ms else ""
        return step("4", False, f"후보({c['why']}) → 출시지만 적합도 {si['fit_min']} 이상인 자리 없음{best}")
    m = ms[0]
    return step("4", False, f"후보({c['why']}) → 자리 {name(m['slot'])} 적합도 {m['fit']} — top-{si['k']} 밖 (공식 출처 · 인기 순에서 밀림)")


# ---------------------------------------------------------------------------- 한 항목

def item_record(it, ctx):
    iid = it["id"]
    steps = []
    entry = ctx["lane_a"].get(iid)
    parent = None
    if entry is None and (it.get("meta") or {}).get("parent") in ctx["lane_a"]:
        parent = ctx["lane_a"][(it.get("meta") or {})["parent"]]          # 쪼개기(HYO-168) 전 run — 묶음 항목으로 판정했다
    lane, reasons = lanes.classify(it)
    if entry and not entry.get("alarm") or parent:
        e = entry or parent
        note = "레인 A — " + ", ".join(e.get("reasons") or reasons)
        if parent:
            note += f" · 이 run은 changelog 쪼개기(2026-11-04) 전이라 묶음 항목 {parent['id']}로 판정했다 — 아래는 그 묶음의 판정"
        steps.append(step("1", True, note))
        if e.get("gate") is None:
            steps.append(step("2a", None, "관문 없이 돈 run (gate: null)"))
        else:
            steps += gate_steps(e["gate"], e, ctx["cap"])
    elif lane is None:
        steps.append(step("1", None, "분석 대상 아님 — 벤더 페이지 원본(비교 재료). 바뀐 것은 page-diff 항목으로 온다"))
    elif not ctx["has_run"]:
        steps.append(step("1", None, ("레인 A" if lane == "A" else "레인 B") + " (지금 코드로 나눔 — 이 주의 관문 run이 없다)"))
    else:
        steps.append(step("1", None, "레인 B" if lane == "B" else "레인 A인데 이 run의 lane_a에 없다 (다른 코드로 돈 run)"))
        title = it.get("title") or ""
        if entry and entry.get("alarm"):
            steps.append(step("net", True, "걸린 이름: " + ", ".join(entry["alarm"])))
            if entry.get("gate") is not None:
                steps += gate_steps(entry["gate"], entry, ctx["cap"])
                if entry["gate"].get("status") == "unclassified" and iid in ctx["alarm_unknown"]:
                    rep = ctx["alarm_unknown"][iid]
                    steps.append(step("3", True, "관문이 모르는 기술이지만 버리지 않는다 — 목록 「경보」 칸 · detect로") if rep == iid else
                                 step("3", False, f"관문이 모르는 기술 — 목록 「경보」 칸에만 (같은 이름 묶음의 처음 글로 접힘 → {rep})"))
        elif iid in ctx["not_ours"]:
            steps.append(step("net", False, "사고 낱말 + 인기 높음인데 스택 · 런타임 이름이 제목에 없다 — 목록 「우리 스택 아님」 칸"))
        elif alarm.ALARM.search(title):
            terms = lanes.stack_mentions(it, ctx["alarm_terms"])
            if ctx["has_net"]:
                steps.append(step("net", None, "사고 낱말은 있지만 제목에 지금 스택 · 런타임 이름이 없다"))
            elif terms:
                steps.append(step("net", False, "이 run에는 경보 그물이 없었다 (2026-11-04 HYO-170 전 코드) — 지금 코드라면 걸린다: "
                                  + ", ".join(terms) + " (--now로 본다)"))
        ranks = [f"{s} {r}위" for s, r in sorted(ctx["b_rank"].get(iid, {}).items())]
        steps.append(step("B", None, ("관심사 순위 " + " · ".join(ranks) if ranks else "관심사 순위 밖") + " — 막다른 길 (detect로 안 이어진다)"))
        t = ctx["topic"].get(iid)
        if t:
            steps.append(step("C", None, f"화제 {t['rank']}/{ctx['n_topics']}위" + (f" (화제 탭 상위 {ctx['topic_k']})" if t["rank"] <= ctx["topic_k"] else f" (상위 {ctx['topic_k']} 밖)")
                              + (f" · {t['n_items']}건 묶음" if t["n_items"] > 1 else "")))
    s4 = slot_step(it, ctx["slots"])
    if s4:
        steps.append(s4)
    if iid in ctx["detect"]:
        steps.append(step("5", True, f"detect 입력 ({ctx['detect'][iid]})" + detect_result(it, ctx["report"])))
    elif any(s["pass"] and s["stage"] in ("3", "4") for s in steps):
        steps.append(step("5", False, "이 주 detect 입력(feed.json)에 없다 — 대 본 run · slots가 그 주 detect 입력을 만든 실행과 다르다")
                     if ctx["has_feed"] else step("5", None, "이 주 detect 입력(feed.json)이 없어 detect까지 갔는지 모른다"))
    end = "detect" if iid in ctx["detect"] else "list" if iid in ctx["listed"] else "out" if ctx["has_feed"] else "unknown"
    return finish(it, steps, end)


def finish(it, steps, end):
    """단계들 → 기록 하나 (제목 · URL · 출처 신호 · 빠진 곳을 붙인다). trace 파일을 읽을 때도 여기서 다시 붙인다."""
    steps = sorted(steps, key=lambda s: ORDER.index(s["stage"]))
    stops = [{k: s[k] for k in ("stage", "name", "note")} for s in steps if s["pass"] is False]
    if end in ("out", "unknown") and not stops:      # 끝난 곳이 기록 단계뿐(레인 B 순위 · 화제 · 자리 후보 아님) — 막다른 길
        stops = [{"stage": s["stage"], "name": s["name"], "note": s["note"]} for s in steps if s["stage"] in ("B", "1")][-1:]
    return {"id": it["id"], "title": it.get("title"), "url": it.get("url"), "source": it.get("source"), "signal": signal(it),
            "steps": steps, "end": end, "stops": stops}


def detect_result(it, report):
    if not report:
        return ""
    url = (it.get("url") or "").rstrip("/")
    for c in report.get("candidates") or []:
        if url in [u.rstrip("/") for u in c.get("source_urls") or []]:
            return f" → 초안 ADR {c.get('adr')} ({c.get('technology')})"
    for key, label in (("excluded", "detect가 버림"), ("dropped", "R10 버림"), ("skipped_existing", "이미 있는 결정이라 건너뜀")):
        for x in report.get(key) or []:
            if (x.get("url") or "").rstrip("/") == url or url in [u.rstrip("/") for u in x.get("source_urls") or []]:
                return f" → {label}: {x.get('reason') or x.get('adr') or ''}"
    return " → detect 결과에 없음"


# ---------------------------------------------------------------------------- 전체

def _terms(stack_terms):
    """경보 그물이 보는 이름 (alarm.net과 같다) — 대소문자만 다른 이름(python · Python)은 처음 것 하나."""
    out = {}
    for t in list(stack_terms) + list(alarm.RUNTIME_TERMS):
        if t.lower() not in alarm.COMMON_WORDS:
            out.setdefault(t.lower(), t)
    return list(out.values())


def build(items, *, lane_a=None, lane_b=None, manifest=None, alarm_info=None, slots=None, slots_raw=None,
          feed=None, week_list=None, report=None):
    """순수 계산 — 항목마다 trace 기록. 인자는 run 폴더 · detect 폴더 파일을 읽은 것 그대로 (없으면 None).
    lane_b = {전략: 순위 목록} · feed = detect 입력 feed.json 목록 · week_list = list.json · report = run_detect report.json."""
    manifest = manifest or {}
    lane_a = lane_a or []
    cap = manifest.get("lane_a_gate") if isinstance(manifest.get("lane_a_gate"), dict) else None
    b_rank = {}
    for s, ranked in (lane_b or {}).items():
        for i, e in enumerate(ranked, 1):
            b_rank.setdefault(e["id"], {})[s] = i
    tp = topics.build_topics(items)
    listed = set()
    wl = week_list or {}
    for e in wl.get("always") or []:
        listed.add(e["id"])
    for g in (wl.get("tech") or []) + (wl.get("slots") or []):
        listed |= {x["id"] for x in g.get("items") or []}
    listed |= {x["id"] for x in (wl.get("alarm") or []) + (wl.get("not_ours") or [])}
    listed |= {e["id"] for e in lane_a if e.get("listed")}
    from .detect_feed import alarm_unknown       # 늦게 — detect_feed가 이 모듈을 부른다
    unknown_rep = {e["id"]: rep for e, rep in alarm_unknown(lane_a)}
    stack_terms = list(((manifest.get("stack") or {}).get("terms")) or [])
    ctx = {"lane_a": {e["id"]: e for e in lane_a}, "cap": cap, "has_run": bool(manifest) or bool(lane_a),
           "has_net": bool(alarm_info) or "alarm_net" in (manifest.get("counts") or {}),
           "not_ours": {x["id"] for x in (alarm_info or {}).get("not_ours") or []},
           "alarm_terms": _terms(stack_terms),
           "alarm_unknown": unknown_rep, "b_rank": b_rank,
           "topic": {r["id"]: {"rank": t["rank"], "n_items": t["n_items"]} for t in tp["topics"] for r in t["items"]},
           "n_topics": len(tp["topics"]), "topic_k": tp["k"], "slots": slot_index(slots, slots_raw),
           "detect": {e["id"]: e.get("kind") for e in feed or []}, "has_feed": feed is not None, "listed": listed, "report": report}
    return [item_record(it, ctx) for it in items]


def write(path, records):
    """짧게 쓴다 — {id, end, steps: [[단계, 통과, 이유]]}. 제목 · URL · 빠진 곳은 읽을 때 코퍼스에서 다시 붙인다 (주당 2.6천 줄)."""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(json.dumps({"id": r["id"], "end": r["end"], "steps": [[s["stage"], s["pass"], s["note"]] for s in r["steps"]]},
                               ensure_ascii=False, separators=(",", ":")) + "\n")


def read(path, items=()):
    """trace 파일 → 기록들. items(그 주 코퍼스, read_items)가 있으면 제목 · URL · 출처 신호를 붙인다."""
    by_id = {it["id"]: it for it in items}
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out.append(finish(by_id.get(r["id"]) or {"id": r["id"]}, [step(st, ok, note) for st, ok, note in r["steps"]], r["end"]))
    return out


def _load(p):
    p = Path(p)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def from_run(items, run_dir, detect_dir=None, slots_path=None, report=None):
    """저장된 파일들에서 trace를 다시 만든다 (trace.jsonl이 없는 예전 주 · 다른 slots를 대 볼 때)."""
    run = Path(run_dir) if run_dir else None
    manifest = _load(run / "run.json") if run else None
    lane_b = {}
    if run and run.exists():
        for f in sorted(run.glob("lane_b.*.json")):
            lane_b[f.name[len("lane_b."):-len(".json")]] = _load(f)
    sp = Path(slots_path) if slots_path else (run / "slots.json" if run else None)
    if sp is not None and sp.is_dir():
        sp = sp / "slots.json"
    slots = _load(sp) if sp else None
    slots_raw = _load(sp.parent / "slots-raw.json") if sp else None
    d = Path(detect_dir) if detect_dir else None
    return build(items, lane_a=_load(run / "lane_a.json") if run else None, lane_b=lane_b, manifest=manifest,
                 alarm_info=_load(run / "alarm.json") if run else None, slots=slots, slots_raw=slots_raw,
                 feed=_load(d / "feed.json") if d else None, week_list=_load(d / "list.json") if d else None,
                 report=_load(report) if report else None)


# ---------------------------------------------------------------------------- 지금 코드로 (재생)

class NotRecorded(RuntimeError):
    pass


def replay_backend(recorded):
    """그 주 lane_a.json에 기록된 모델 답(model_changes)만 돌려준다. 없으면 NotRecorded — 관문이 error로 기록한다 (모델을 부르지 않는다)."""
    from .importance.backends import StubBackend

    def answer(q):
        if q.id not in recorded:
            raise NotRecorded(REPLAY_MISSING)
        return recorded[q.id]
    return StubBackend(changes=answer)


def recorded_labels(lane_a, items=()):
    """lane_a.json → {id: 모델 답}. items에서 쪼갠 changelog의 원래 항목(블록의 meta.parent)은 뺀다 — 그 id는 이제 블록을 뺀 나머지 줄이라
    묶음 전체에 대한 예전 답이 맞지 않는다."""
    split = {(it.get("meta") or {}).get("parent") for it in items} - {None}
    return {e["id"]: list(e["gate"]["model_changes"]) for e in lane_a or []
            if (e.get("gate") or {}).get("status") == "ok" and e["gate"].get("model_changes") is not None and e["id"] not in split}


def replay(items, gate, profile, stack_terms, *, strategies=("kw", "kw+aliases"), slots=None, slots_raw=None, report=None):
    """지금 코드로 레인 나누기 → 경보 그물 → 관문 → 칸 정리 → (자리 질의는 주어진 slots.json) → detect 입력을 다시 고르고 trace를 만든다.
    gate는 replay_backend를 꽂은 Gate — 모델을 부르지 않는다."""
    from . import detect_feed, profile_filter
    res = profile_filter.run_filter(items, profile, list(strategies), stack_terms, gate=gate)
    manifest = {"counts": res["counts"], "stack": {"terms": list(stack_terms)},
                "lane_a_gate": {"model": "replay", **res["gate_summary"]}}
    chosen = detect_feed.choose(res["lane_a"], slots)
    by_id = {it["id"]: it for it in items}
    feed = [{"id": e["id"], "kind": ("model_release" if e.get("slot") else "security_alert" if e.get("alarm") else
                                     detect_feed.feed_entry(by_id[e["id"]])["kind"])} for e in chosen if e["id"] in by_id]
    wl = detect_feed.week_list(res["lane_a"], slots, {e["id"] for e in chosen}, res.get("alarm"))
    return build(items, lane_a=res["lane_a"], lane_b=res["lane_b"], manifest=manifest, alarm_info=res.get("alarm"),
                 slots=slots, slots_raw=slots_raw, feed=feed, week_list=wl, report=report)


# ---------------------------------------------------------------------------- 명령

def match(it, words, text=False):
    hay = f"{it.get('id', '')} {it.get('title', '')} {it.get('url', '')}" + (f" {it.get('text', '')}" if text else "")
    hay = hay.lower()
    return all(w.lower() in hay for w in words)


def render(r, week):
    mark = {"detect": "detect까지 감", "list": "목록에만", "out": "빠짐", "unknown": "detect 입력 모름"}[r["end"]]
    lines = [f"[{week}] {r['id']} · {r['signal']} — {(r['title'] or '')[:110]}", f"  {r['url']}"]
    for s in r["steps"]:
        flag = {True: "→", False: "✗", None: "·"}[s["pass"]]
        lines.append(f"  {flag} {s['stage']:<3} {s['name']:<12} {s['note']}")
    tail = f"  = {mark}"
    if r["end"] != "detect" and r["stops"]:
        tail += " — 빠진 곳: " + " / ".join(f"{s['stage']} {s['name']}" for s in r["stops"])
    lines.append(tail)
    return "\n".join(lines)


def _paths(a):
    snaps = Path(a.snapshots or os.environ.get("RADAR_SNAPSHOT_REPO") or REPO.parent / "radar-snapshots")
    run = Path(a.run) if a.run else REPO / "pipeline" / "out" / a.week
    detect = Path(a.detect) if a.detect else snaps / "detect" / a.week
    return snaps, run, detect


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="pipeline.trace", description=__doc__.split("\n")[0])
    ap.add_argument("query", nargs="+", help="검색어 — 제목 · URL · id에 낱말이 전부 들어야 맞는다 (대소문자 무시)")
    ap.add_argument("--week", required=True, help="주차 (예: 2026-W44)")
    ap.add_argument("--text", action="store_true", help="본문까지 찾는다")
    ap.add_argument("--snapshots", help="radar-snapshots 폴더 (기본: env RADAR_SNAPSHOT_REPO 또는 ../radar-snapshots)")
    ap.add_argument("--run", help="관문 run 폴더 (기본: pipeline/out/<주>)")
    ap.add_argument("--detect", help="detect 입력 폴더 (기본: <snapshots>/detect/<주>)")
    ap.add_argument("--slots", help="자리 질의 결과 slots.json (또는 그 폴더) — run 폴더 것 대신")
    ap.add_argument("--report", help="run_detect의 report.json — 5 detect의 결과(초안 · 버림)를 붙인다")
    ap.add_argument("--now", action="store_true", help="지금 코드로 다시 돈다 (모델 답은 그 주 lane_a.json에 기록된 것만 — 호출 없음)")
    ap.add_argument("--tiers", help="--now: 등급 파일 (기본: <snapshots>/tiers.json)")
    ap.add_argument("--json", action="store_true", help="trace 기록을 JSON 줄로")
    a = ap.parse_args(argv)
    snaps, run, detect = _paths(a)
    from .profile_filter import read_items
    corpus = snaps / a.week
    if not (corpus / "items.jsonl").exists():
        print(f"얼린 주 폴더가 없다: {corpus}", file=sys.stderr)
        return 2
    items = read_items(corpus)
    hits = [it for it in items if match(it, a.query, a.text)]
    q = " ".join(a.query)
    if not hits:
        print(f"[{a.week}] 수집 안 됨 — 코퍼스 {len(items):,}건의 제목 · URL에 「{q}」가 없다" + ("" if a.text else " (--text로 본문까지)"))
        return 0
    ids = {it["id"] for it in hits}
    if a.now:
        records = _now(a, items, run, snaps)
        how = "지금 코드로 다시 (모델 답은 기록된 것만)"
    elif (detect / TRACE).exists() and not (a.run or a.slots or a.report):
        records = read(detect / TRACE, items)
        how = f"{detect / TRACE}"
    else:
        records = from_run(items, run if run.exists() else None, detect if detect.exists() else None, a.slots, a.report)
        how = f"run {run}" + ("" if run.exists() else " (없음)") + (f" · slots {a.slots}" if a.slots else "")
    records = [r for r in records if r["id"] in ids]
    if a.json:
        for r in records:
            print(json.dumps(r, ensure_ascii=False))
        return 0
    print(f"「{q}」 {a.week}: {len(records)}건 — {how}\n")
    for r in records:
        print(render(r, a.week) + "\n")
    return 0


def _now(a, items, run, snaps):
    from .importance.cli import build_gate
    from .interest_profile import load_profile
    from .profile_filter import load_stack
    lane_a = _load(run / "lane_a.json") or []
    tiers = a.tiers or str(snaps / "tiers.json")
    gate, _ = build_gate("stub", tiers, backend=replay_backend(recorded_labels(lane_a, items)))
    profile, _ = load_profile(os.environ.get("RADAR_PROFILE") or REPO.parent / "adr" / "radar-profile.yaml")
    stack = load_stack()
    sp = Path(a.slots) if a.slots else run / "slots.json"
    if sp.is_dir():
        sp = sp / "slots.json"
    return replay(items, gate, profile, stack["terms"], slots=_load(sp), slots_raw=_load(sp.parent / "slots-raw.json"),
                  report=_load(a.report) if a.report else None)


if __name__ == "__main__":
    sys.exit(main())
