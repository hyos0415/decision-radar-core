"""관문 결과 → detect 입력 (HYO-84, 데모 ②). 수집 스냅숏에서 관문이 남긴 항목만 `radar.py snapshot` 폴더 모양으로 바꾼다.

  python -m pipeline.profile_filter run --snapshot ../radar-snapshots/2026-W41 --gate gpt-6.1-sol --tiers tiers.json --real-api --out pipeline/out/w41
  python -m pipeline.detect_feed --run pipeline/out/w41 --out pipeline/out/w41/detect-snapshot
  .venv/Scripts/python product/radar/run_detect.py --snapshot pipeline/out/w41/detect-snapshot --adr ../adr --out pipeline/out/w41/detect

출력 폴더 (RADAR_SNAPSHOT_DIR로 쓴다 — radar.py `snapshot feed|fetch|search`가 읽는 모양):
  feed.json    소식 목록 (id · title · kind · package · version · summary · url · source_type)
  index.json   url(scheme·끝 / 뺀 것) → {source_type, file} — snapshot_fetch가 전문을 준다
  items/F01.md 항목 전문 (릴리스 노트 · 벤더 소식 본문 · 페이지 변경의 더해진/빠진 줄)
  items/F01-L1.md  커뮤니티 항목(자리 질의 · 경보 그물)이 가리키는 1차 출처 — 그 주에 받아 얼린 것 (HYO-174). 못 받은 링크는 index에 상태만
  search.json  빈 목록 (snapshot search는 질의를 무시한다 — 알려진 병목)
  funnel.json  건수 (수집 → 레인 A → 관문 남김 → 표시). 실행 요약 한 줄의 재료
  gate.json    url → 왜 남았나(관문 kept_by). detect 입력에는 넣지 않는다 — 모델 판단을 흔들지 않게 화면(HYO-85)만 읽는다
  basis.json   관문의 판정 기준 — 대상 레포별 커밋·우리 버전 (pipeline.basis, HYO-162). run 폴더에 있으면 그대로 옮긴다.
               detect가 지금 기준과 대조해 이미 반영된 릴리스를 뺀다 (모델에는 안 보인다)
  list.json    이번 주 목록 — 「항상」 · 기술 질의 top-k · 자리 질의 top-k (HYO-168, docs/design/query-topk.md). 화면이 읽는다
  trace.jsonl  탈락 추적 — 항목마다 지나간 단계와 빠진 곳 · 이유 (pipeline.trace, HYO-171). `python -m pipeline.trace <검색어> --week <주>`가 읽는다

무엇을 넣나: 관문이 남기고(keep) detect로 고른 것(shown — 「항상」을 (패키지 × 줄)마다 1건으로 접은 것 + 기술마다 top-k, 2026-11-04 전에는 5/N 상한 안)
+ run 폴더에 slots.json(pipeline.slots)이 있으면 자리마다 top-k 전부 (kind model_release)
+ interest.json(pipeline.interest, HYO-187)이 있으면 관심사 질의 주 10건 (kind new_technology). `--all-kept`면 관문이 남긴 것 전부.
버린 이유(결정표의 행)는 여기 옮기지 않는다 — `lane_a.json`·`gate-calls.jsonl`에 이미 있다 (깔때기 화면은 만들지 않는다, 10/8).

1차 출처 링크 (HYO-174, doc-pipeline.md ④ 「글의 링크」): HN · Reddit 글은 근거가 아니라서(R2) detect가 1차 출처를 찾아야 하는데, 모델은
스냅숏만 연다. W45 Terra 글이 가리킨 공식 모델 문서가 `404 not in snapshot index`였다. 그래서 글의 링크 중 1차 출처
(GitHub 릴리스 · `collector/vendors.yaml` 벤더 페이지와 같은 도메인)만 여기서 받아 얼린다 — 그 밖의 링크는 받지 않는다(커뮤니티가 공식 근거로
둔갑하지 않게). 모델은 여전히 웹에 나가지 않는다 (revisit_feed HYO-159와 같은 원칙). `--no-fetch`면 받지 않는다.
"""
import argparse
import json
import re
import sys
from pathlib import Path

from . import alarm, trace
from .basis import BASIS
from .importance import policy
from .profile_filter import read_items, write_json

SUMMARY_CHARS = 1500    # triage 프롬프트에 그대로 들어간다. 전문은 snapshot_fetch로
SLOTS = "slots.json"    # 자리 질의 결과 (pipeline.slots, HYO-168) — run 폴더에 있으면 자리마다 top-k를 detect에 더한다
INTEREST = "interest.json"   # 관심사 질의 결과 (pipeline.interest, HYO-187) — run 폴더에 있으면 주 10건(to_detect)을 detect에 더한다
LIST = "list.json"      # 이번 주 목록 — 「항상」 · 기술 질의 · 자리 질의 (query-topk.md) + 경보 · 우리 스택 아님 (HYO-170). 화면이 읽는다
ALARM = "alarm.json"    # 경보 그물 · 「우리 스택 아님」 (pipeline.alarm, profile_filter가 쓴다)
VENDORS = Path(__file__).resolve().parents[1] / "collector" / "vendors.yaml"   # 1차 출처 도메인의 기준 (HYO-174)
LINKS_PER_ITEM = 3      # 커뮤니티 글 하나에서 받을 1차 출처 링크 수
_GH_RELEASES = re.compile(r"^github\.com/[^/]+/[^/]+/releases(/|$)", re.I)


def url_key(url):
    """radar.py cmd_snapshot의 fetch와 같은 정규화 — 다르면 모델이 연 url이 404가 된다."""
    return re.sub(r"^https?://", "", url or "").rstrip("/")


def _lines(v):
    if not v:
        return []
    return v.splitlines() if isinstance(v, str) else [str(x) for x in v]


def body_of(item, snapshot_dir=None):
    """항목 전문. 페이지 변경은 더해진 줄(text)과 빠진 줄(meta.removed)을 나눠 적는다."""
    meta = item.get("meta") or {}
    if meta.get("kind") == "page-diff":
        added, removed = _lines(item.get("text")), _lines(meta.get("removed"))
        return "\n".join([f"# {item.get('title', '')}", "", "## 더해진 줄", *(added or ["(없음)"]), "", "## 빠진 줄", *(removed or ["(없음)"])])
    text = item.get("text") or ""
    if not text and meta.get("text_file") and snapshot_dir:
        p = Path(snapshot_dir) / meta["text_file"]
        text = p.read_text(encoding="utf-8") if p.exists() else ""
    return f"# {item.get('title', '')}\n\n{text}".rstrip() + "\n"


def feed_entry(item):
    meta = item.get("meta") or {}
    if meta.get("kind") == "page-diff":
        kind, source_type, text = "page_change", "official_doc", "더해진 줄: " + " / ".join(_lines(item.get("text")))
    elif meta.get("kind") == "advisory":       # 공식 보안 채널 (collector/sources/security.py, HYO-170 다)
        kind, source_type, text = "security_advisory", "official_doc", item.get("text") or ""
    elif meta.get("dependency"):
        kind, source_type, text = "dependency_release", "release_note", item.get("text") or ""
    elif meta.get("tag"):
        kind, source_type, text = "vendor_release", "release_note", item.get("text") or ""
    else:
        kind, source_type, text = "vendor_news", "official_doc", item.get("text") or ""
    summary = text if len(text) <= SUMMARY_CHARS else text[:SUMMARY_CHARS].rstrip() + " …(전문은 snapshot_fetch)"
    e = {"id": item["id"], "title": item.get("title", ""), "kind": kind}
    if meta.get("dependency"):
        e["package"] = meta["dependency"]
    if meta.get("tag"):
        e["version"] = meta["tag"]
    e.update({"summary": summary, "url": item["url"], "source_type": source_type})
    return e


def select(lane_a, all_kept=False):
    """관문이 남긴 것 중 detect에 넣을 것 — 기본은 상한 안(shown)."""
    return [e for e in lane_a if (e.get("gate") or {}).get("keep") and (all_kept or e.get("shown"))]


def slot_picks(slots):
    """자리 질의 결과(slots.json) → {항목 id: (자리, 기록)} — 자리마다 top-k 전부 detect로 (query-topk.md §2 변경 2026-11-04:
    처음엔 1위만이었는데 Haiku 5.5(W42)는 2 · 3위, Terra(W44)는 2위라 목록에만 떴다). 같은 항목이 여러 자리에 들면 처음 자리 · 높은 순위."""
    out = {}
    for slot, xs in ((slots or {}).get("per_slot") or {}).items():
        for x in xs:
            if x["id"] not in out or x["rank"] < out[x["id"]][1]["rank"]:
                out[x["id"]] = (slot, x)
    return out


SLOT_SOURCE_TYPE = {"vendor": "official_doc", "huggingface": "repo", "hn": "community", "reddit": "community"}


def slot_entry(item):
    """자리 질의로 온 항목의 feed 칸 — kind는 model_release. HN · Reddit은 community(근거가 아니다 — detect가 1차 출처를 찾는다, R2)."""
    e = feed_entry(item)
    e["kind"] = "model_release"
    # 합집합 스킬 항목(HYO-167)은 원문을 받은 근거의 유형 그대로 — 1차 출처만 받는다 (pipeline.union.primary_evidence)
    e["source_type"] = SLOT_SOURCE_TYPE.get(item.get("source")) or (item.get("meta") or {}).get("source_type") or "community"
    return e


INTEREST_SOURCE_TYPE = {**SLOT_SOURCE_TYPE, "huggingface": "repo", "github": "repo"}


def interest_entry(item):
    """관심사 질의로 온 항목의 feed 칸 (HYO-187) — kind는 new_technology. HN · Reddit은 community(근거가 아니다 — detect가 1차 출처를 찾는다, R2)."""
    e = feed_entry(item)
    e["kind"] = "new_technology"
    e["source_type"] = INTEREST_SOURCE_TYPE.get(item.get("source"), "community")
    return e


def alarm_entry(item):
    """경보 그물로 온 글의 feed 칸 (HYO-170) — kind는 security_alert. HN · Reddit은 community(근거가 아니다 — detect가 공식 공지를 찾는다, R2)."""
    e = feed_entry(item)
    e["kind"] = "security_alert"
    e["source_type"] = SLOT_SOURCE_TYPE.get(item.get("source"), "community")
    return e


def alarm_unknown(lane_a):
    """경보 그물에 걸렸는데 관문이 판정 못 한 것(등급 파일에 없는 런타임 — Claude Code · Node.js …) → 버리지 않고 목록 · detect로.
    같은 이름 묶음은 가장 인기 있는 글(alarm.popularity) 하나만 detect (나머지는 folded_into). → [(항목, 대표 id)] (레인 순서)"""
    hits = [e for e in lane_a if e.get("alarm") and (e.get("gate") or {}).get("status") == "unclassified"]
    key = lambda e: tuple(sorted(t.lower() for t in e["alarm"]))  # noqa: E731
    reps = {}
    for e in hits:
        if key(e) not in reps or alarm.popularity(e) < alarm.popularity(reps[key(e)]):
            reps[key(e)] = e
    return [(e, reps[key(e)]["id"]) for e in hits]


def alarm_kin(lane_a):
    """경보 대표 id → 접힌 같은 기술 경보 id들 (관문이 남긴 「항상」 접기 folded_into + 관문이 모르는 기술 묶음)."""
    kin = {}
    for e in lane_a:
        if e.get("alarm") and e.get("folded_into"):
            kin.setdefault(e["folded_into"], []).append(e["id"])
    for e, rep in alarm_unknown(lane_a):
        if rep != e["id"]:
            kin.setdefault(rep, []).append(e["id"])
    return kin


def kin_note(items):
    """접힌 경보 글들을 대표의 detect 입력에 붙이는 문단 (2026-11-04) — 대표 하나만 detect로 가도 같은 기술의 다른 사고 · 출처를 보게."""
    if not items:
        return ""
    lines = []
    for it in items:
        links = [u for u in (it.get("links") or []) if u != it.get("url")][:3]
        lines.append(f"- {it.get('title', '')} — {it.get('url', '')}" + (" (링크: " + ", ".join(links) + ")" if links else ""))
    return "## 같은 기술의 다른 경보 (이 글로 접혔다 — 다른 사고면 따로 다룬다)\n\n" + "\n".join(lines) + "\n"


def advisory_kin(lane_a):
    """OSV 공지 대표 id → 같은 패키지로 접힌 공지 id들 (관문 「항상」 접기 folded_into, HYO-170 다)."""
    kin = {}
    for e in lane_a:
        if (e.get("gate") or {}).get("advisory") and e.get("folded_into"):
            kin.setdefault(e["folded_into"], []).append(e["id"])
    return kin


def folded_note(items, heading):
    """접힌 항목들을 대표의 detect 입력에 붙이는 문단 — 대표 하나만 detect로 가도 나머지 제목 · 링크를 보게."""
    if not items:
        return ""
    lines = [f"- {it.get('title', '')} — {it.get('url', '')}" for it in items]
    return f"## {heading}\n\n" + "\n".join(lines) + "\n"


def week_list(lane_a, slots, detect_ids, alarm_info=None):
    """이번 주 목록 (query-topk.md §2) — 「항상」 · 기술 질의(top-k) · 자리 질의(top-k) + 경보(관문이 모르는 기술) · 우리 스택 아님 (HYO-170).
    화면이 읽는다. detect로 간 것은 to_detect."""
    def brief(e):
        g = e.get("gate") or {}
        best = min(g.get("kept_by") or [{}], key=lambda r: (policy.tier_rank(r["tier"]), policy.severity(r["change"])) if r else (99, 99))
        quiet = policy.quiet_advisory({"advisory": g.get("advisory")})
        return {"id": e["id"], "title": e.get("title"), "url": e.get("url"), "tier": best.get("tier"), "change": best.get("change"),
                "reason": (g.get("reason") or "") + (" · 우리 버전 해당 없음 → 목록만" if quiet else ""), "rank": e.get("rank"), "to_detect": e["id"] in detect_ids,
                "folded_into": e.get("folded_into"),     # 「항상」에서 같은 패키지 · 줄의 더 새 버전으로 접혔으면 그 대표 id (화면이 「외 N건」으로 묶는다)
                "alarm": e.get("alarm")}                 # 경보 그물로 온 글이면 걸린 이름들 (HYO-170)
    listed = [e for e in lane_a if e.get("listed")]
    always = [brief(e) for e in listed if e.get("always")]
    tech = {}
    for e in listed:
        if not e.get("always"):
            tech.setdefault(e.get("query") or "?", []).append(brief(e))
    slot_groups = []
    for slot, xs in ((slots or {}).get("per_slot") or {}).items():
        slot_groups.append({"slot": slot, "name": slots["slot_names"].get(slot), "current": slots.get("slot_current", {}).get(slot) or [],
                            "items": [dict({k: x[k] for k in ("id", "title", "url", "source", "fit", "reason", "rank")}, to_detect=x["id"] in detect_ids)
                                      for x in xs]})
    unknown = [dict(brief(e), reason="경보 그물 · 관문이 모르는 기술 (" + ", ".join(e["alarm"]) + ")", folded_into=rep if rep != e["id"] else None)
               for e, rep in alarm_unknown(lane_a)]
    not_ours = list((alarm_info or {}).get("not_ours") or [])
    return {"always": always, "tech": [{"query": q, "items": v} for q, v in tech.items()], "slots": slot_groups,
            "alarm": unknown, "not_ours": not_ours,
            "counts": {"always": len(always), "tech": sum(len(v) for v in tech.values()),
                       "slots": sum(len(g["items"]) for g in slot_groups), "alarm": len(unknown), "not_ours": len(not_ours),
                       "to_detect": len(detect_ids)}}


def funnel(manifest, lane_a, chosen, slots=None, alarm_info=None, interest=None):
    c = manifest.get("counts") or {}
    g = manifest.get("lane_a_gate") or {}
    own = [e for e in lane_a if not e.get("alarm")]        # 레인 A 건수 · 판정 못 한 비율은 레인 A만 (경보 그물 글은 따로 센다)
    statuses = {}
    for e in own:
        s = (e.get("gate") or {}).get("status") or "none"
        statuses[s] = statuses.get(s, 0) + 1
    f = {"snapshot": manifest.get("snapshot"), "gate_model": g.get("model"), "items": c.get("items"), "lane_a": c.get("lane_a"),
         "gate_status": statuses, "gate_counts": policy.gate_counts(own), "kept": g.get("total_kept"), "cap": g.get("label"),
         "to_detect": len(chosen)}
    net = [e for e in lane_a if e.get("alarm")]
    if net or alarm_info:
        f["alarm"] = {"net": len(net), "kept": sum(1 for e in net if (e.get("gate") or {}).get("keep")),
                      "unknown": len(alarm_unknown(lane_a)), "not_ours": len((alarm_info or {}).get("not_ours") or [])}
    if g.get("mode") == "topk":
        f["listed"] = len(g.get("listed") or [])
    if slots:
        f["slots"] = {"candidates": slots.get("candidates"), "releases": slots.get("releases"),
                      "listed": sum(len(v) for v in slots["per_slot"].values()), "to_detect": len(slots.get("to_detect") or [])}
    if interest:
        c = interest.get("counts") or {}
        f["interest"] = {"new": c.get("picks"), "topical": c.get("topical"), "to_detect": len(interest.get("to_detect") or []),
                         "pick": (interest.get("pick") or {}).get("engine"), "failed": len(interest.get("failed") or {})}
    return f


def funnel_line(f):
    """실행 요약 한 줄 — 데스크톱 `_summarize_detect`와 run_detect가 같이 쓴다.
    관문이 판정 못 한 건수(모르는 기술 · 분류 불가 · 팀원 스택)를 같은 줄에 보이고, 비율이 기준을 넘으면 경고를 붙인다 (HYO-122 ⑤ — 조용히 빠지지 않게).
    gate_counts가 없는 예전 funnel.json은 예전 줄 그대로."""
    cap = f" ({f['cap']})" if f.get("cap") else ""
    kept = f"관문 남김 {f.get('kept')}"
    gc = f.get("gate_counts")
    if gc:
        kept += (f" (모르는 기술 {gc['unknown']} · 분류 불가 {gc['unresolved']} · 팀원 스택 {gc['other_stack']}"
                 + (f" · 스택에서 뺌 {gc['retired']}" if gc.get("retired") else "") + (f" · 오류 {gc['error']}" if gc.get("error") else "") + ")")
    line = f"수집 {f.get('items')} → 레인 A {f.get('lane_a')} → {kept} → detect 입력 {f.get('to_detect')}{cap}"
    if f.get("slots"):
        s = f["slots"]
        line += f" · 자리 질의 후보 {s['candidates']} → 출시 {s['releases']} → 목록 {s['listed']} (detect {s['to_detect']})"
    if f.get("interest"):
        n = f["interest"]
        line += f" · 관심사 질의 새것 {n['new']} (detect {n['to_detect']}" + (f", 실패 {n['failed']}" if n.get("failed") else "") + ")"
    if f.get("alarm"):
        a = f["alarm"]
        line += f" · 경보 그물 {a['net']} (관문 남김 {a['kept']} · 관문이 모르는 기술 {a['unknown']}) · 우리 스택 아님 {a['not_ours']}"
    if f.get("links"):
        line += f" · 1차 출처 링크 {f['links']['fetched']}/{f['links']['total']} 받음"
    if f.get("union"):
        u = f["union"]
        line += f" · 스킬 줄 {u['found']} (겹침 {u['both']} · 스킬만 {u['skill_only']} → 관문 남김 {u['kept']} · detect {u['to_detect']})"
    if gc and gc.get("warn"):
        line += (f" — 경고: 관문이 판정 못 한 소식 {gc['ratio']:.0%} (모르는 기술+분류 불가 / 팀원 스택 뺀 레인 A — 기준 {gc['warn_ratio']:.0%}, 잠정)")
    return line


def _domain(host):
    return ".".join(host.lower().split(".")[-2:])


def official_domains(path=VENDORS):
    """벤더 페이지 주소의 등록 도메인(끝 두 마디) — 커뮤니티 글의 링크가 1차 출처인지 가르는 기준 (HYO-174)."""
    import yaml
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return {_domain(url_key(f["url"]).split("/")[0]) for v in (data.get("vendors") or {}).values()
            for f in v.get("feeds") or [] if f.get("url")}


def link_type(url, domains):
    """링크 → release_note · official_doc · None(받지 않는다 — R2)."""
    key = url_key(url)
    if _GH_RELEASES.match(key):
        return "release_note"
    host = key.split("/")[0]
    return "official_doc" if host and _domain(host) in domains else None


def primary_links(item, domains):
    """커뮤니티 항목이 가리키는 1차 출처 → [(url_key, source_type)] 최대 LINKS_PER_ITEM개. HN 글은 url이 곧 바깥 링크라 url도 본다."""
    seen, out = set(), []
    for u in [item.get("url"), *(item.get("links") or [])]:
        key, st = url_key(u), link_type(u or "", domains)
        if key and key not in seen and st:
            seen.add(key)
            out.append((key, st))
    return out[:LINKS_PER_ITEM]


def fetch_links(item, n, out, index, fetcher, domains):
    """1차 출처를 받아 items/F{n}-L{k}.md로 얼린다 → [{url, source_type, status}]. 같은 url이면 받은 문서가 이긴다(HN 글 본문보다) ·
    못 받으면 index에 실제 상태만 — 이미 있는 항목은 덮지 않는다 (revisit_feed와 같다)."""
    from .revisit_feed import page_text
    rows = []
    for k, (key, st) in enumerate(primary_links(item, domains), 1):
        code, body = fetcher.get("https://" + key)
        text = page_text(body) if code == 200 else ""
        if text:
            name = f"items/F{n:02d}-L{k}.md"
            (out / name).write_text(f"# {key}\n\n{text}\n", encoding="utf-8", newline="\n")
            index[key] = {"source_type": st, "file": name}
            status = 200
        else:
            status = code if isinstance(code, int) and code != 200 else "fetch_error"
            index.setdefault(key, {"source_type": st, "status": status})
        rows.append({"url": key, "source_type": st, "status": status})
    return rows


def links_note(rows):
    if not rows:
        return ""
    lines = ["1차 출처 링크 (이 글이 가리킨 곳 — snapshot_fetch로 연다):"]
    lines += [f"- https://{r['url']} ({r['source_type']}" + (")" if r["status"] == 200 else f", 받지 못함 {r['status']})") for r in rows]
    return "\n".join(lines)


def choose(lane_a, slots, all_kept=False, interest=None):
    """detect로 갈 항목 — 관문 몫(select) + 자리 질의 top-k + 관심사 질의 주 10건 + 경보 그물의 관문이 모르는 기술.
    탈락 추적의 재생(pipeline.trace --now)도 이 함수를 쓴다."""
    chosen = select(lane_a, all_kept)
    taken = {e["id"] for e in chosen}
    for sid, (slot, x) in slot_picks(slots).items():   # 자리 질의 top-k (HYO-168) — 관문이 이미 넣은 항목이면 한 번만
        if sid not in taken:
            chosen.append({"id": sid, "slot": slot, "slot_hit": x})
            taken.add(sid)
    for p in (interest or {}).get("to_detect") or []:  # 관심사 질의 주 10건 (HYO-187) — 앞에서 이미 넣은 항목이면 한 번만
        if p["id"] not in taken:
            chosen.append({"id": p["id"], "interest": p["interest"], "interest_hit": p})
            taken.add(p["id"])
    for e, rep in alarm_unknown(lane_a):               # 경보 그물 · 관문이 모르는 기술 (HYO-170) — 이름 묶음마다 하나
        if e["id"] == rep and e["id"] not in taken:
            chosen.append({**e, "alarm_unknown": True})
            taken.add(e["id"])
    return chosen


def build(run_dir, out_dir, snapshot=None, all_kept=False, fetcher=None):
    """fetcher가 있으면 커뮤니티 항목의 1차 출처 링크를 받는다 (HYO-174) — 없으면(테스트 · --no-fetch) 받지 않는다."""
    run_dir, out = Path(run_dir), Path(out_dir)
    manifest = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    if not isinstance(manifest.get("lane_a_gate"), dict):
        raise SystemExit(f"{run_dir}는 관문 없이 돌린 결과다 — profile_filter run에 --gate·--tiers를 준다")
    lane_a = json.loads((run_dir / "lane_a.json").read_text(encoding="utf-8"))
    slots = json.loads((run_dir / SLOTS).read_text(encoding="utf-8")) if (run_dir / SLOTS).exists() else None
    interest = json.loads((run_dir / INTEREST).read_text(encoding="utf-8")) if (run_dir / INTEREST).exists() else None
    snap = Path(snapshot or manifest["snapshot"])
    from . import union  # 합집합(HYO-167 D6) — 관문 · 자리 질의가 읽은 스킬만 항목. 꺼져 있으면 빈 목록
    by_id = {it["id"]: it for it in read_items(snap) + union.extra_items(run_dir)}
    chosen = choose(lane_a, slots, all_kept, interest)
    alarm_info = json.loads((run_dir / ALARM).read_text(encoding="utf-8")) if (run_dir / ALARM).exists() else None
    missing = [e["id"] for e in chosen if e["id"] not in by_id]
    if missing:
        raise SystemExit(f"스냅숏 {snap}에 없는 항목 {len(missing)}개 (다른 주의 run인가?): {missing[:3]}")
    snap_dir = snap if snap.is_dir() else snap.parent
    (out / "items").mkdir(parents=True, exist_ok=True)
    feed, index, why = [], {}, {}
    kin, adv_kin = alarm_kin(lane_a), advisory_kin(lane_a)
    domains = official_domains() if fetcher else set()
    links = []
    for i, e in enumerate(chosen, 1):
        item = by_id[e["id"]]
        entry = (slot_entry(item) if e.get("slot") else interest_entry(item) if e.get("interest") else
                 alarm_entry(item) if e.get("alarm") else feed_entry(item))
        body = body_of(item, snap_dir)
        index[url_key(item["url"])] = {"source_type": entry["source_type"], "file": f"items/F{i:02d}.md"}
        if fetcher and entry["source_type"] == "community":
            rows = fetch_links(item, i, out, index, fetcher, domains)
            links += rows
            if rows:
                entry["summary"] = (entry["summary"] + "\n\n" + links_note(rows)).strip()
                body += "\n" + links_note(rows) + "\n"
        if e.get("alarm"):      # 경보 글 (HYO-170 나 리뷰) · OSV 공지 (HYO-170 다) — 접힌 것을 대표 입력에
            note = kin_note([by_id[k] for k in kin.get(e["id"], []) if k in by_id])
        else:
            note = folded_note([by_id[k] for k in adv_kin.get(e["id"], []) if k in by_id], "같은 패키지의 다른 보안 공지 (이 공지로 접혔다 — 우리 버전 해당분)")
        if note:
            entry["summary"] = (entry["summary"] + "\n\n" + note).strip()
            body += "\n" + note
        name = f"items/F{i:02d}.md"
        (out / name).write_text(body, encoding="utf-8", newline="\n")
        feed.append(entry)
        if e.get("slot"):
            x = e["slot_hit"]
            why[item["url"]] = {"id": e["id"], "reason": f"자리 {slots['slot_names'].get(e['slot']) or e['slot']} · 적합도 {x['fit']}: {x['reason']}",
                                "kept_by": [], "shown": True, "slot": e["slot"], "fit": x["fit"]}
        elif e.get("interest"):
            x = e["interest_hit"]
            why[item["url"]] = {"id": e["id"], "reason": f"관심사 {x.get('topic') or e['interest']} · 새것 {x.get('order')}번: {x.get('reason')}",
                                "kept_by": [], "shown": True, "interest": e["interest"]}
        elif e.get("alarm_unknown"):
            why[item["url"]] = {"id": e["id"], "reason": "경보 그물 · 관문이 모르는 기술 (" + ", ".join(e["alarm"]) + ")", "kept_by": [],
                                "shown": True, "alarm": e["alarm"]}
        else:
            g = e["gate"]
            why[item["url"]] = {"id": e["id"], "reason": g.get("reason"), "kept_by": g.get("kept_by") or [], "shown": e.get("shown")}
    f = funnel(manifest, lane_a, chosen, slots, alarm_info, interest)
    if links:
        f["links"] = {"total": len(links), "fetched": sum(1 for r in links if r["status"] == 200)}
    u = union.funnel_info(run_dir, lane_a, [e["id"] for e in chosen])
    if u:
        f["union"] = u
    write_json(out / "feed.json", feed)
    write_json(out / "index.json", index)
    write_json(out / "search.json", [])
    write_json(out / "funnel.json", f)
    write_json(out / "gate.json", why)
    wl = week_list(lane_a, slots, {e["id"] for e in chosen}, alarm_info)
    write_json(out / LIST, wl)
    try:   # 탈락 추적 (HYO-171) — 보조 기록이다. 실패해도 detect 입력 · 게시는 그대로 (리뷰 11/4: 사슬을 멈추지 않게)
        lane_b = {p.name[len("lane_b."):-len(".json")]: json.loads(p.read_text(encoding="utf-8")) for p in sorted(run_dir.glob("lane_b.*.json"))}
        slots_raw = json.loads((run_dir / "slots-raw.json").read_text(encoding="utf-8")) if (run_dir / "slots-raw.json").exists() else None
        trace.write(out / trace.TRACE, trace.build(list(by_id.values()), lane_a=lane_a, lane_b=lane_b, manifest=manifest, alarm_info=alarm_info,
                                                   slots=slots, slots_raw=slots_raw, feed=feed, week_list=wl))
    except Exception as e:  # noqa: BLE001
        f["trace_error"] = f"{type(e).__name__}: {str(e)[:200]}"
        write_json(out / "funnel.json", f)
        print(f"  주의: 탈락 추적 파일을 못 썼다 — detect 입력은 그대로 ({f['trace_error']})", file=sys.stderr)
    if (run_dir / BASIS).exists():
        (out / BASIS).write_bytes((run_dir / BASIS).read_bytes())
    return f


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="pipeline.detect_feed", description=__doc__.split("\n")[0])
    ap.add_argument("--run", required=True, help="profile_filter run --gate의 결과 폴더 (run.json · lane_a.json)")
    ap.add_argument("--out", required=True, help="detect가 읽을 스냅숏 폴더 (RADAR_SNAPSHOT_DIR)")
    ap.add_argument("--snapshot", help="주차 폴더 (기본: run.json에 적힌 것)")
    ap.add_argument("--all-kept", action="store_true", help="주당 상한에 잘린 것까지 넣는다")
    ap.add_argument("--no-fetch", action="store_true", help="커뮤니티 항목의 1차 출처 링크를 받지 않는다 (HYO-174)")
    a = ap.parse_args(argv)
    fetcher = None
    if not a.no_fetch:
        from collector.fetch import Fetcher
        fetcher = Fetcher()
    f = build(a.run, a.out, a.snapshot, a.all_kept, fetcher)
    print(funnel_line(f))
    print(f"-> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
