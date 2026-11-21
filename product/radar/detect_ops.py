"""detect의 코드 쪽 — 모델이 아니라 흐름의 단계 함수(detect.py)가 부른다. 안전 조항은 여기와 radar.py가 집행한다.

도구 호출은 스킬 transcript와 같은 모양으로 ctx.events에 남는다 (ops.radar / ops.read_repo_file이 기록한다).
"""

import json
import os
import re

from common import ops
from common.core import radar

from . import hitl


def snapshot_feed(c):
    """스냅숏 모드의 소식 수집 — 웹 대신 radar.py snapshot feed (HYO-45)."""
    c.calls += 1
    return radar(c, "snapshot", "feed")


def drop_rejected(c, feed):
    """사람이 전에 거부한 소식을 모델에 넘기기 전에 뺀다 (HYO-142) → (남은 소식, excluded 줄). 안전 조항처럼 코드가 집행한다 (규율 1).
    단위는 피드 항목 id(릴리스 `repo@tag`, 벤더 페이지는 변경마다 다른 id) — 같은 기술의 새 소식은 막지 않는다.
    RADAR_DETECT_RESURFACE=1이면 끈다 (데스크톱 스텁 모드 — 같은 초안을 다시 띄워 알림을 시험한다)."""
    if c.env.get("RADAR_DETECT_RESURFACE") == "1":
        return feed, []
    seen = hitl.rejected_sources(c)
    keep, out = [], []
    for it in feed:
        if it.get("id") in seen:
            out.append({"url": it.get("url") or "", "reason": f"이전에 거부한 소식 — {seen[it['id']]} (다시 올리지 않는다)"})
        else:
            keep.append(it)
    return keep, out


def drop_applied(c, feed):
    """관문 판정 뒤 대상 레포에 이미 반영된 의존성 릴리스를 모델에 넘기기 전에 뺀다 (HYO-162) → (남은 소식, excluded 줄). 코드가 집행한다 (규율 1).
    관문 기준 = 입력 폴더의 basis.json(수집 PC가 판정한 커밋·버전), 지금 기준 = env RADAR_BASIS_NOW(앱이 실행 직전에 원격 HEAD로 만든 것).
    둘 중 하나라도 없으면 아무것도 빼지 않는다 — 예전 입력·eval 픽스처는 그대로 돈다."""
    snap, now_path = c.env.get("RADAR_SNAPSHOT_DIR"), c.env.get("RADAR_BASIS_NOW")
    if not snap or not now_path or not os.path.exists(os.path.join(snap, "basis.json")):
        return feed, []
    from pipeline import basis  # 늦게 — 판정 기준이 있을 때만 수집 파이프라인을 부른다
    old, new = basis.load(os.path.join(snap, basis.BASIS)), basis.load(now_path)
    if not old or not new:
        return feed, []
    return basis.applied(feed, old, new)


MANIFESTS = ("requirements.txt", "package.json")


def read_stack(c):
    """대상 repo마다 CLAUDE.md(스택·의존성→component 매핑)와 manifest를 읽어 한 덩어리 글로 만든다.

    읽기는 ops.read_repo_file이 한다 — repo 밖 경로를 막고 Read 호출을 기록한다.
    """
    parts = []
    for repo in sorted(c.targets):
        parts.append(f"## repo: {repo}")
        for name in ("CLAUDE.md", *MANIFESTS):
            out = json.loads(ops.read_repo_file(c, repo, name))
            if out.get("ok"):
                parts.append(f"### {name}\n{out['body'].rstrip()}")
            elif name == "CLAUDE.md":
                parts.append("### CLAUDE.md\n(없음 — manifest와 import 위치로 추정해야 한다)")
    return "\n\n".join(parts)


def missing_targets(c):
    """폴더가 없는 대상 repo → [{repo, path}]. 없으면 CLAUDE.md · manifest를 하나도 못 읽고 모델이 component를 지어낸다 (HYO-246 —
    11/21 미리 검증: 사본 ADR 폴더 기준 상대 경로가 없는 곳을 가리켜 초안 21건이 llm-inference · agent-engine 같은 이름이었다). 조용히 넘기지 않는다."""
    return [{"repo": r, "path": c.targets[r]} for r in sorted(c.targets) if not os.path.isdir(c.targets[r])]


# ── R16: 의존성 릴리스의 affected = CLAUDE.md 「의존성 → component」 매핑 그대로 (HYO-246) ──────────────
# 프롬프트(detect_prompts · detect_schemas)에만 있던 규칙을 코드가 집행한다 (규율 1). 표는 hitl.stack_rows(스택 절 — 같은 절 찾기 규칙)로 읽는다.
# 첫 칸 이름 뽑기는 collector `names_from_cell`과 같은 규칙 — product는 collector를 import하지 않으므로 여기 같은 규칙을 두고,
# 같은 이름이 나오는지는 test_product가 collector 함수와 맞춰 본다.

def names_of(cell):
    """표 첫 칸 → 이름들 (= collector.sources.github.names_from_cell)."""
    cell = re.sub(r"\(.*?\)", "", cell)
    cell = re.sub(r"`[^`]*`", "", cell)
    out = []
    for part in re.split(r"[·,]|\s\+\s", cell):
        name = part.strip().split(" ")[0].strip()
        if name and name not in out:
            out.append(name)
    return out


def components_of(cell):
    """표 둘째 칸 → component 이름들. 「앱 (설명) · eval (`코드`로 설명). 덧붙인 문장 — …」 → [앱, eval].
    괄호 설명(겹친 것까지) · 백틱을 지우고, 덧붙인 문장(「. 」 · 「 — 」 뒤)을 자른 뒤 `·` `,`로 나눠 조각마다 첫 단어."""
    prev = None
    while prev != cell:
        prev, cell = cell, re.sub(r"\([^()]*\)", "", cell)
    cell = re.sub(r"`[^`]*`", "", cell)
    cell = re.split(r"\.\s|\.$|\s—\s", cell)[0]
    out = []
    for part in re.split(r"[·,]", cell):
        words = part.split()
        name = words[0].strip(".;:") if words else ""
        if name and name not in out:
            out.append(name)
    return out


def _key(name):
    """이름 비교용 — 대소문자 · 기호 무시 (llama.cpp = llama-cpp, Langfuse = langfuse)."""
    return re.sub(r"[^0-9a-z가-힣]", "", (name or "").lower())


def dependency_map(c):
    """{repo: [{"names", "components"}]} — 대상 repo CLAUDE.md 「의존성 → component」 표. CLAUDE.md가 없는 repo는 빈 목록 (그때는 맞추지 않는다).
    **표(`|` 줄)만 읽는다** — 수집기(collector dependency_rows_from_claude_md)와 같은 규칙. 표가 아닌 목록 형식(eval 픽스처의
    「- `fastapi`, `pydantic` → chat-api」)은 맞추지 않고 모델이 쓴 그대로 둔다 — 그 경로는 채점기의 affected 정확 일치 검사가 보고,
    음성 대조 CTRL-detect-widened-affected가 그대로 잡히는 것으로 확인했다 (HYO-246)."""
    out = {}
    for repo in sorted(c.targets):
        p = os.path.join(c.targets[repo], "CLAUDE.md")
        rows = []
        if os.path.isfile(p):
            for line in hitl.stack_rows(open(p, encoding="utf-8").read())["deps"]:
                if not line.startswith("|"):
                    continue
                cells = [x.strip() for x in line.strip().strip("|").split("|")]
                if len(cells) < 2 or not cells[0] or cells[0].startswith("의존성"):
                    continue
                rows.append({"names": names_of(cells[0]), "components": components_of(cells[1])})
        out[repo] = rows
    return out


def mapped_affected(dmap, technology):
    """technology가 표 첫 칸의 이름과 같으면 그 행들의 component 전부(대상 repo 전체의 합) → [{repo, component}]. 표에 없으면 None."""
    key, out = _key(technology), []
    for repo in sorted(dmap):
        for row in dmap[repo]:
            if key and key in {_key(n) for n in row["names"]}:
                out += [a for a in ({"repo": repo, "component": comp} for comp in row["components"]) if a not in out]
    return out or None


def fix_affected(cands, dmap):
    """R16을 코드로 — 후보의 technology가 매핑 표에 있으면 affected를 매핑 그대로로, dependency를 true로 맞춘다 → 바꾼 목록
    [{technology, source_urls, before, after, dependency_was}]. 표에 없는 기술(새 모델 · 서비스)은 손대지 않는다 — R10만 적용된다.
    빠뜨림 · 덧붙임 모두 위반이다(R16). 같은 집합이면(순서만 다르면) 모델이 쓴 그대로 둔다. fix_names 뒤에 부른다 — 이름이 관문 이름으로 맞춰진 뒤."""
    changed = []
    for cand in cands:
        exact = mapped_affected(dmap, cand["technology"])
        if not exact:
            continue
        same = {(a["repo"], a["component"]) for a in cand.get("affected") or []} == {(a["repo"], a["component"]) for a in exact}
        if same and cand.get("dependency"):
            continue
        changed.append({"technology": cand["technology"], "source_urls": list(cand.get("source_urls") or []),
                        "before": list(cand.get("affected") or []), "after": exact, "dependency_was": bool(cand.get("dependency"))})
        if not same:
            cand["affected"] = exact
        cand["dependency"] = True
    return changed


def r16_lines(triage):
    """보고서 triage 칸 → 사람이 읽는 요약 줄 (CLI run_detect · 앱 실행 요약이 같이 쓴다). 고친 것이 없으면 빈 목록."""
    tr, lines = triage or {}, []
    if tr.get("missing_targets"):
        lines.append(f"폴더가 없는 대상 repo {', '.join(tr['missing_targets'])} — CLAUDE.md · manifest를 못 읽었다. 그 repo 후보는 뺐다 (radar-targets.json 경로 확인)")
    fixed = tr.get("r16") or []
    if fixed:
        def one(f):
            names = lambda xs: ", ".join(f"{a['repo']}/{a['component']}" for a in xs) or "(없음)"
            if {(a["repo"], a["component"]) for a in f["before"]} == {(a["repo"], a["component"]) for a in f["after"]}:
                return f"{f['technology']} affected 그대로 · dependency만 true로"
            return f"{f['technology']} {names(f['before'])} → {names(f['after'])}"
        lines.append(f"R16: affected를 CLAUDE.md 매핑대로 고친 후보 {len(fixed)}건 — " + "; ".join(one(f) for f in fixed))
    return lines


def release_of(cand, feed):
    """의존성 릴리스 후보의 릴리스 표기 = 근거 소식의 제목 (radar.py match --release가 버전 토막을 뽑는다). 의존성 밖이면 None."""
    if not cand.get("dependency"):
        return None
    urls = set(cand.get("source_urls") or [])
    return next((it.get("title") for it in feed or [] if it.get("url") in urls and it.get("title")), None)


def screen(c, candidates, feed=None, missing=()):
    """후보마다 R10·R14를 코드로 집행한다. R16(의존성 릴리스의 affected = 매핑)은 그 앞의 fix_affected가 맞춘다.

    R10  affected가 비었거나 대상 repo에 없는 repo · 폴더가 없는 대상 repo(missing — 매핑을 못 읽어 component가 근거 없다)를 가리키면 버린다 → dropped
    R14  radar.py match가 overlap(기술 같고 component 겹침)을 주면 초안을 만들지 않는다 → skipped.
         의존성 릴리스 대응형 ADR은 같은 릴리스일 때만 overlap — 릴리스 표기는 소식 제목 (R14 변경 2026-10-23, HYO-136)
    남은 후보는 related(같은 기술이거나 같은 component의 ADR)와 함께 돌려준다 — 초안 본문에 적는다.
    """
    kept, skipped, dropped = [], [], []
    for i, cand in enumerate(candidates):
        aff = cand["affected"]
        bad = [a for a in aff if a["repo"] not in c.targets]
        gone = [a for a in aff if a["repo"] in set(missing)]
        if not aff or bad or gone:
            dropped.append({"technology": cand["technology"], "source_urls": cand.get("source_urls") or [],
                            "reason": "R10: affected가 비었다" if not aff else f"R10: 대상 repo에 없는 repo {sorted({a['repo'] for a in bad})}" if bad
                            else f"R10: 폴더가 없는 대상 repo {sorted({a['repo'] for a in gone})} — 매핑을 못 읽어 component가 근거 없다"})
            continue
        overlap, related = [], {}
        rel = release_of(cand, feed)
        for a in aff:
            m = radar(c, "match", c.adr_dir, "--technology", cand["technology"], "--repo", a["repo"], "--component", a["component"],
                      *(("--release", rel) if rel else ()))
            overlap += [x for x in m["overlap"] if x not in overlap]
            for r in m["related"]:
                related.setdefault(r["id"], r)
        if overlap:
            skipped += [{"technology": cand["technology"], "adr": x, "source_urls": cand.get("source_urls") or []} for x in overlap]
            continue
        kept.append({"idx": i, "candidate": cand, "related": sorted(related.values(), key=lambda r: r["id"])})
    return kept, skipped, dropped


def grep_log(c):
    """이번 실행에서 실제로 돈 repo_grep → {패턴: 기록}. 같은 패턴을 여러 번 돌렸으면 마지막 것."""
    out = {}
    for e in c.events:
        if e.get("type") != "assistant":
            continue
        t = e["message"]["content"][0]
        if t.get("name") == "Grep" and t["input"].get("ok"):
            out[t["input"]["pattern"]] = t["input"]
    return out


def _why(c):
    """입력 폴더의 gate.json(url → 왜 왔나 — detect_feed가 쓴다). 없으면 {} — 예전 입력 · eval 픽스처는 그대로 돈다."""
    snap = c.env.get("RADAR_SNAPSHOT_DIR")
    path = os.path.join(snap, "gate.json") if snap else None
    if not path or not os.path.exists(path):
        return {}
    try:
        gate = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {url: v for url, v in gate.items() if isinstance(v, dict)}


def security_urls(c):
    """관문이 security로 남긴 소식의 url들."""
    return {url for url, v in _why(c).items() if any(k.get("change") == "security" for k in v.get("kept_by") or [])}


def slot_reasons(c):
    """자리 질의가 후보로 고른 소식 → {url: [{slot, name, repo, role, fit, reason}]} (HYO-205). 대상 repo가 적힌 자리만 —
    detect는 「어디에 쓰나」를 다시 판단하지 않고 이 자리의 repo · 역할로 쓴다."""
    return {url: hits for url, v in _why(c).items()
            if (hits := [h for h in v.get("slots") or [] if h.get("repo") in c.targets])}


def unused_security(excluded, sec):
    """버린 것 가운데 관문이 security로 남긴 소식 — **이유가 무엇이든** 전부 (HYO-206). 같은 내용이라 다른 후보에 붙인 것(fold_duplicates)은 이미 빠져 있다.
    처음(HYO-205, 사용자 2026-11-20)에는 「우리 코드가 안 쓴다」는 이유만 막았다. 그 뒤 같은 입력을 세 번씩 돌리니 보안 소식이 다른 이유로도 실행마다 갈렸다 —
    W46 transformers 보안 패치를 「어느 manifest에도 없다」(거르기는 루트 manifest만 읽는다 — arena/requirements.txt에 있다)며 세 번 중 한 번,
    W47 eslint 보안 표시 릴리스를 「일상 릴리스」라며 세 번 중 두 번. 이유의 종류를 가려 막으면 모델이 다른 종류를 고르는 것으로 다시 갈린다.
    보안 소식은 놓치는 쪽이 비싸다 — 관문이 남겼으면 초안으로 올리고 사람이 기각한다 (기각한 소식은 다시 올라오지 않는다, HYO-142)."""
    return [e for e in excluded if e.get("url") in sec]


def to_rescue(excluded, sec, slotted, missing=()):
    """거르기가 버렸지만 버릴 수 없는 소식 · 답을 빠뜨린 소식 → [(줄, 왜 되돌리나)] (HYO-205 · HYO-206).
    slot = 자리 질의가 이미 후보로 고른 것 — 어디에 쓰나를 detect가 다시 판단해 빼면 실행마다 갈린다 · security = 관문이 security로 남긴 소식(이유와 무관) ·
    missing = 후보에도 버린 것에도 없는 소식 (다시 묻는다 — 조용히 사라지게 두지 않는다)."""
    unused = {id(e) for e in unused_security(excluded, sec)}
    out = [(e, "slot" if e.get("url") in slotted else "security") for e in excluded if e.get("url") in slotted or id(e) in unused]
    return out + [({"url": u, "reason": "거르기가 답하지 않았다", "checked": []}, "slot" if u in slotted else "security" if u in sec else "missing")
                  for u in missing]


def unanswered(feed, cands, excluded):
    """거르기가 후보에도 버린 것에도 적지 않은 소식의 url (HYO-206 — W46 세 번 중 두 번, 경보 그물 소식 하나가 답 없이 사라졌다)."""
    seen = {u for c in cands for u in c.get("source_urls") or []} | {e.get("url") for e in excluded}
    return [it["url"] for it in feed if it.get("url") and it["url"] not in seen]


def fold_duplicates(cands, excluded):
    """「후보로 올린 소식과 같은 내용」이라며 버린 소식을 그 후보의 근거(source_urls)에 붙인다 → (남은 excluded, 붙인 목록) (HYO-206).
    같은 소식 둘을 한 번은 따로 올리고 한 번은 하나만 올리면, 버려진 쪽은 어느 초안에 들어갔는지 남지 않고 다음 주에 다시 올라온다.
    duplicate_of가 후보의 소식을 가리키지 않으면 그대로 둔다 — 붙일 곳을 코드가 지어내지 않는다. 후보에도 있는 url의 버린 줄은 뺀다(후보가 이긴다)."""
    by_url = {u: c for c in cands for u in c.get("source_urls") or []}
    left, folded = [], []
    for e in excluded:
        if e.get("url") in by_url:
            continue
        target = by_url.get(e.get("duplicate_of") or "") if e.get("why") == "duplicate" else None
        if target is None:
            left.append(e)
            continue
        target["source_urls"] = list(target["source_urls"]) + [e["url"]]
        by_url[e["url"]] = target
        folded.append({"url": e["url"], "into": e["duplicate_of"], "reason": e.get("reason") or ""})
    return left, folded


def gate_tech(c):
    """앞 단계가 붙인 이름 → {url: 기술 이름}. 이름이 하나로 정해지는 소식만 — 둘 이상이면 어느 이름인지 코드가 고르지 않는다.
    자리 질의가 대상 repo의 자리 후보로 고른 소식은 그 출시의 모델 이름(slots의 model)이 먼저다 — 후보는 새 모델이지 관문이 걸어 준 우리 기술이 아니다.
    그 밖은 관문이 남긴 기술(kept_by)."""
    out = {}
    for url, v in _why(c).items():
        models = list(dict.fromkeys(h.get("model") for h in v.get("slots") or [] if h.get("repo") in c.targets and h.get("model")))
        names = models or list(dict.fromkeys(k.get("technology") for k in v.get("kept_by") or [] if k.get("technology")))
        if len(names) == 1:
            out[url] = names[0]
    return out


def fix_names(cands, tech_of):
    """후보의 technology를 관문이 붙인 기술 이름으로 맞춘다 → 바꾼 목록 [{source_urls, model, gate}] (HYO-206 · HYO-214).
    같은 소식에 모델이 실행마다 다른 이름을 붙였다 (Node.js 보안 릴리스 → Electron / Node.js · Langfuse / langfuse) — technology는 기존 ADR과 겹침을 보는
    동일성 키라 이름이 갈리면 겹침 검사와 「거부한 소식 다시 안 올리기」가 빗나간다. 근거 소식이 전부 관문이 남긴 것이고 기술 이름이 하나일 때만 바꾼다
    (자리 질의 · 관심사로 온 소식은 관문 이름이 없다 — 그대로 둔다)."""
    changed = []
    for cand in cands:
        urls = cand.get("source_urls") or []
        names = {tech_of.get(u) for u in urls}
        if not urls or None in names or len(names) != 1:
            continue
        gate = names.pop()
        if cand["technology"] != gate:
            changed.append({"source_urls": list(urls), "model": cand["technology"], "gate": gate})
            cand["technology"] = gate
    return changed


def usage_lines(c, patterns):
    """찾아본 패턴 → 초안에 적을 줄. 개수는 모델의 말이 아니라 repo_grep 기록에서 읽는다. 돌린 기록이 없는 패턴은 그렇게 적는다."""
    log, out = grep_log(c), []
    for p in dict.fromkeys(patterns or []):
        r = log.get(p)
        if not r:
            out.append(f"코드 찾기 `{p}`: 돌린 기록 없음")
            continue
        per = " · ".join(f"{repo} {n}곳" for repo, n in sorted((r.get("by_repo") or {}).items()))
        out.append(f"코드 찾기 `{p}`: {per or '대상 없음'}" + (f" · 못 읽은 repo {', '.join(r['unread'])}" if r.get("unread") else "")
                   + " (우리 코드만 — 의존성 안은 보지 않았다)")
    return out


def with_usage(c, body, cand):
    """초안 서술에 코드 찾기 결과를 덧붙인다 — 코드가 적는다 (모델이 옮겨 적다 틀리지 않게)."""
    lines = usage_lines(c, cand.get("checked"))
    return {**body, "research_note": list(body.get("research_note") or []) + lines} if lines else body


def check_unused(c, excluded):
    """「우리 코드가 안 쓴다」며 버린 소식을 repo_grep 기록과 대조한다 (HYO-205, 규율 1 — 모델의 말이 아니라 기록으로).
    확인됨 = checked가 하나 이상 있고, 그 패턴이 전부 이번 실행에서 실제로 돌았고, 전부 0곳이며, 코드를 못 읽은 repo가 없다.
    excluded의 해당 항목에 usage_check를 붙여 돌려준다 — 버림은 그대로 두고, 확인 안 된 것은 보고서가 따로 센다(조용히 믿지 않는다)."""
    log, out = grep_log(c), []
    for e in excluded:
        if not e.get("unused_claim"):
            out.append(e)
            continue
        rows = [{"pattern": p, "ran": p in log, "total": (log.get(p) or {}).get("total"), "by_repo": (log.get(p) or {}).get("by_repo"),
                 "unread": (log.get(p) or {}).get("unread") or []} for p in e.get("checked") or []]
        ok = bool(rows) and all(r["ran"] and r["total"] == 0 and not r["unread"] for r in rows)
        why = None if ok else ("찾아본 패턴을 적지 않았다" if not rows else
                               "적은 패턴을 실제로 돌린 기록이 없다" if not all(r["ran"] for r in rows) else
                               "코드를 못 읽은 repo가 있다" if any(r["unread"] for r in rows) else "쓰는 곳이 나왔다")
        out.append({**e, "usage_check": {"verified": ok, "patterns": rows, **({"why": why} if why else {})}})
    return out


def detect_report(c, now, *, skipped, dropped, excluded, written, failed, security=None, notes=None):
    """run_report (references/schema.md). pr.needed는 이번 실행에서 초안을 쓴 것이 1개 이상일 때 (detect).

    report의 candidates는 **초안을 쓴 후보**다 — 거른 뒤 남아 초안이 된 것. skipped_existing·dropped·excluded는 따로 둔다.
    """
    files = list(dict.fromkeys(e["message"]["content"][0]["input"]["file_path"] for e in c.events  # repair가 같은 파일을 다시 써도 한 번만
                               if e.get("type") == "assistant" and e["message"]["content"][0]["name"] == "Write"))
    needed = bool(written)
    return {
        "flow": "detect", "now": now, "dry_run": c.dry_run,
        "candidates": [{"technology": w["candidate"]["technology"], "affected": w["candidate"]["affected"],
                        "source_urls": w["candidate"]["source_urls"], "dependency": w["candidate"]["dependency"],
                        "adr": w["id"], "validate_errors": w["validate_errors"], "repaired": w["repaired"]} for w in written],
        "skipped_existing": skipped, "dropped": dropped, "excluded": excluded, "draft_failed": failed,
        "pr": {"needed": needed, "opened": False, "branch": f"claude/radar-detect-{now[:10]}" if needed else None},
        "files_written": files, "remote_side_effects": [],
        "adrs_checked": [], "results": [], "alerts": [], "link_candidates": [], "overrides": [],
        "tool_calls": c.tool_calls(),
        # 「안 쓴다」며 버린 소식 (HYO-205) — verified: 찾아보니 0곳 · unverified: 찾아본 기록과 안 맞는다(사람이 본다)
        "unused": {"verified": [e["url"] for e in excluded if (e.get("usage_check") or {}).get("verified")],
                   "unverified": [{"url": e["url"], "why": e["usage_check"].get("why")} for e in excluded
                                  if e.get("usage_check") and not e["usage_check"]["verified"]],
                   "greps": len(grep_log(c))},
        # 관문이 security로 남긴 소식이 어떻게 됐나 (HYO-205) — rescued: 「안 쓴다」며 버린 것을 코드가 후보로 되돌림 · excluded: 다른 이유로 버림(사람이 본다)
        **({"security": security} if security else {}),
        # 거르기의 답을 코드가 맞춘 것 (HYO-206) — folded: 같은 내용이라 다른 후보의 근거에 붙인 소식 · missing: 거르기가 답을 빠뜨려 다시 물은 소식 ·
        # unanswered: 다시 물어도 답이 없던 소식(사람이 본다) · renamed: 관문 이름으로 고친 technology ·
        # r16: affected를 CLAUDE.md 매핑대로 고친 후보 (before → after, HYO-246) · missing_targets: 폴더가 없어 못 읽은 대상 repo
        **({"triage": notes} if notes and any(notes.values()) else {}),
    }



def compose(body, w):
    """DraftBody → AdrDraft 입력. technology·affected는 triage가 정한 후보, related는 radar.py match 결과 — 코드가 덮어쓴다."""
    cand = w["candidate"]
    return {**body, "technology": cand["technology"], "affected": cand["affected"], "related": [r["id"] for r in w["related"]]}


def rewrite(c, path, adr_id, draft, item_id=None):
    """repair가 낸 초안으로 **같은 번호의 파일을 코드가** 다시 쓰고 radar.py validate를 다시 돈다 → 남은 오류.
    item_id가 있으면 write-ahead로 쓴다(의도 → 파일 → 완료) — 사람이 고친 파일이면 덮지 않는다 (HYO-44)."""
    if item_id:
        return hitl.rewrite_adr(c, item_id, adr_id, draft)
    c.record("Write", {"file_path": path})
    open(path, "w", encoding="utf-8", newline="\n").write(ops.render(draft, c.now))
    v = radar(c, "validate", c.adr_dir, ok_codes=(0, 1))
    return next((a["errors"] for a in v["adrs"] if a["id"] == adr_id), ["validate에 없음"])
