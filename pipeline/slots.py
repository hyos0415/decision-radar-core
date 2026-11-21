"""자리 질의 — 새 모델 소식을 이름이 아니라 「자리」로 대 본다 (HYO-168 나, `docs/design/query-topk.md` §3).

  python -m pipeline.slots run --snapshot ../radar-snapshots/2026-W45 --slots ../adr/radar-slots.yaml --out pipeline/out/2026-W45 [--engine claude|stub] [--reuse]

관문(레인 A)은 「내가 쓰는 이름에 급한 일이 생겼나」를 묻는다. 이 단계는 「내 자리에 들어올 새 후보가 나왔나」를 묻는다 —
GPT-6.1 Terra(관문 분류 자리) · Claude Haiku 5.5(조건 추출 자리)가 이름 기반 관문에서 빠졌다.

1. 후보 모으기 (스크립트, 결정론): 어느 레인이든 새 모델·서비스 출시로 보이는 소식 — 벤더 changelog 블록의 출시 문구 ·
   Bedrock What's New · HN(점수 문턱 + 모델 이름 꼴) · Reddit LocalLLaMA 「New Model」 · HF trending 상위
2. 판정 (모델, 구독 CLI 한 세션): 후보 + 자리 프로필(ADR repo `radar-slots.yaml`) → 후보마다 출시인가 · 맞는 자리 · 적합도 0~3 · 이유.
   스크립트가 id · 자리 이름 · 점수를 검사한다. 원본 응답은 `slots-raw.json`
3. 순위: 자리마다 적합도 → 공식 출처 먼저 → 출처 안 인기 → id. 문턱(FIT_MIN) 이상 top-k. 없으면 「없음」

4. 언어 확인 (스크립트, HYO-244): 자리가 `lang: ko`를 적었으면, 판정이 「언어 정보가 모자라 1점」(`missing == ["language"]`)으로 낮춘 것을 HF 태그로 코드가 다시 본다 —
   한국어 · 다국어(≥3개 언어) 태그가 있으면 2점으로 올리고, 영어 태그만 있으면 0점으로 내린다. 태그가 없으면 그대로. 고친 것은 `lang_adjusted`에 남긴다.
   판정 입력에는 후보마다 태그에서 뽑은 `langs` · `base_model` · `size`가 붙는다 (HF 소식은 text가 비어 모델이 태그를 못 봤다 — W47 LFM3-1.7B)

출력 `<out>/slots.json`: {slots_version, candidates, judged, per_slot: {자리: [후보…]}, to_detect: [자리마다 top-k id], unjudged, lang_adjusted}
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
# 넓은 후보 (2026-11-12 사용자 「건 수 늘어도 돼, 놓치지만 않으면」) — W41 판정 모델 소식 42건 중 후보에 든 것이 11건이었다:
# HF 작업별 순위(분류 · 토큰 분류 · 임베딩 …) 상위 · LocalLLaMA 상위인데 출시 낱말이 없는 글 · LocalLLaMA News 꼬리표가 빠졌다
WIDE_CANDIDATES = True
HF_TASK_TOP_RANK = 10
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

# 자리 프로필 v2 (2026-11-12 사용자 「무슨 제품을 쓰냐보다 그 제품이 내 프로젝트에서 어떤 역할을 하느냐」) — 역할로 대 본다.
# v1 조건 칸에는 역할 조건(구조화 출력 · 한국어 · 데이터 지역)과 지금 쓰는 방식(구독 CLI · API · 같은 벤더)이 섞여 있었고, 하나라도 못 지키면
# 최대 1이라 W43에서 출시 71건 중 Haiku 5.5 on AWS 하나만 남았다(Gemini 4 Argon · DeepSeek V4.1 Flash가 1로 잘림, 임베딩 모델은 자리 없음).
# v2는 역할 조건(must)만 거르고, 지금 쓰는 방식(now_via)과 다른 것은 깎지 않고 「바꾸려면 필요한 것」(switch)으로 적는다. 사람이 고른다
SYSTEM_V2 = """너는 기술 스택 관리자의 조수다. 이번 주 수집한 소식 중 「새 모델·모델 서비스 출시」를 골라, 스택의 각 「역할」을 더 싸게 또는 더 잘 맡을 후보인지 판정한다.

역할마다 하는 일(role) · 지금 맡은 것(current) · 역할 조건(must) · 지금 쓰는 방식(now_via) · 있으면 좋은 것(prefer) · 받는 후보 종류(kinds)가 주어진다.
- must는 그 역할의 일을 하려면 반드시 지켜야 하는 것이다. 하나라도 못 지키면 fit은 최대 1이다
- now_via는 지금 그 역할을 맡은 것을 부르는 방식(구독 CLI · 특정 회사 API · 특정 런타임)일 뿐 조건이 아니다. 후보가 다른 회사 · 다른 구독 · 다른 런타임이어도 fit을 깎지 않는다. 대신 switch에 바꾸려면 필요한 것(새 결제 · 새 API 키 · 다른 런타임 등)을 한 문장으로 적는다. 지금 방식 그대로 쓸 수 있으면 switch는 빈 문자열
- kinds에 적힌 종류면 생성형 LLM이 아니어도 후보다(판정 모델 · 분류기 · 임베딩 모델 · 리랭커 등)
후보마다:
- release: 새 모델(가중치 공개 포함)이나 모델 서비스(새 모델 제공 · 새 지역 제공 · 새 API 등)의 출시·공개 소식이면 true. 파인튜닝 · 증류 모델도 가중치나 서비스를 새로 내놓은 것이면 true다(쓸 만한지는 fit이 가른다). 사용 후기 · 벤치 글 · 도구 · 논문은 false
- model: release가 true면 그 모델·서비스의 짧은 이름(예: Clef, Gemini 4 Argon, GLiNER2.5-Decide). 같은 모델을 다룬 여러 소식(HF 페이지 · HN 글 · Reddit 글)은 같은 이름으로 적는다
- matches: release가 true일 때만, 맞는 역할마다 {slot, fit, reason, switch}. fit은
  3 = must를 다 지키고 그 역할을 지금 것보다 더 싸게 또는 더 잘 할 것이 글에 분명하다
  2 = must를 다 지키고 그 역할에서 지금 것과 비교 실험해 볼 만하다
  1 = 관련은 있으나 must 일부를 못 지키거나 불확실하다
  0이면 넣지 않는다
- reason: 한국어 한 문장 — 왜 그 역할인지, 지금 것과 무엇을 비교할지
- missing: 모르거나 불분명한 정보 때문에 fit을 낮췄으면 그 정보 종류를 language · size · runtime · license · other 중에서 담는다. 정보가 충분해 낮추지 않았으면 빈 배열
후보의 langs(HF 태그에서 뽑은 언어 코드) · base_model · size는 코드가 뽑아 준 확인된 값이다. 있으면 그것을 근거로 쓰고, 없을 때만 「모른다」로 본다
모든 후보 id에 대해 한 줄씩 답한다. 주어진 글만 근거로 한다 — 모르는 건 지어내지 않고 fit을 낮춘다."""
SLOT_FIELDS = {1: ("id", "name", "repo", "role", "current", "constraints", "prefer"),
               2: ("id", "name", "repo", "role", "current", "must", "now_via", "prefer", "kinds")}

MISSING_KINDS = ("language", "size", "runtime", "license", "other")

SCHEMA = {
    "type": "object",
    "properties": {"judgments": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "release": {"type": "boolean"}, "model": {"type": "string"},   # model은 v2만 (같은 모델 묶기)
                       "matches": {"type": "array", "items": {"type": "object", "properties": {
                           "slot": {"type": "string"}, "fit": {"type": "integer", "minimum": 0, "maximum": 3}, "reason": {"type": "string"},
                           "switch": {"type": "string"},      # v2만 쓴다 (바꾸려면 필요한 것) — 필수가 아니라 v1 답도 그대로 통과
                           "missing": {"type": "array", "items": {"type": "string", "enum": list(MISSING_KINDS)}}},   # 낮춘 까닭이 정보 부족일 때 (HYO-244)
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
    for sl in slots:
        if "lang" in sl and not (isinstance(sl["lang"], str) and re.fullmatch(r"[a-z]{2}", sl["lang"])):
            raise SlotError(f"{path}: 자리 {sl.get('id')}의 lang은 소문자 두 글자 언어 코드여야 한다 ({sl['lang']!r})")
        if "component" in sl and not slot_components(sl):
            raise SlotError(f"{path}: 자리 {sl.get('id')}의 component는 비지 않은 이름 하나 또는 이름 목록이어야 한다 ({sl['component']!r})")
    return {"version": d.get("version"), "slots": slots}


def slot_components(slot):
    """자리 설정의 component 칸 → 이름 목록 (이름 하나 · 목록 둘 다 받는다). 없으면 [] — 그 자리 후보는 detect 모델이 고른 그대로 둔다.
    자리 후보의 affected는 repo는 자리가, component는 이 칸이 정한다 — 모델이 고르지 않는다 (HYO-219, 규율 1)."""
    c = slot.get("component")
    if isinstance(c, str):
        return [c.strip()] if c.strip() else []
    if isinstance(c, list):
        return [x.strip() for x in c if isinstance(x, str) and x.strip()]
    return []


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
        if r is not None and r <= REDDIT_TOP_RANK and (WIDE_CANDIDATES or RELEASE.search(title)):
            return f"reddit:top-week:{r}"
        if WIDE_CANDIDATES and any("r/LocalLLaMA:flair:News" in c for c in chans):    # 출시 낱말 없는 소식 (W41 「llama, server: add /v1/systemone API」)
            return "reddit:news"
        return None
    if src == "skill" and not meta.get("dependency"):  # 합집합 스킬 줄(HYO-167) — 스택에 닿는다고 본 새 모델 · 서비스 (의존성 릴리스는 관문으로)
        return "skill-line"
    if src == "huggingface" and str(it.get("id", "")).startswith("hf-model:"):
        r = _rank(it, "rank:trending")
        if r is not None and r <= HF_TOP_RANK:
            return f"hf-trending:{r}"
        if WIDE_CANDIDATES:      # 작업별 순위 — 판정 · 임베딩 모델은 전체 급상승이 아니라 분류 · 임베딩 순위 위에 있다 (W41 GLiNER2.5-Decide 토큰 분류 1위)
            best = min(((v, k.split(":", 2)[2]) for k, v in (it.get("signals") or {}).items()
                        if k.startswith("rank:task:") and isinstance(v, (int, float))), default=None)
            if best and best[0] <= HF_TASK_TOP_RANK:
                return f"hf-task:{best[1]}:{best[0]}"
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


ISO639_1 = frozenset(
    "aa ab ae af ak am an ar as av ay az ba be bg bh bi bm bn bo br bs ca ce ch co cr cs cu cv cy da de dv dz ee el en eo es et eu fa ff fi fj fo fr fy "
    "ga gd gl gn gu gv ha he hi ho hr ht hu hy hz ia id ie ig ii ik io is it iu ja jv ka kg ki kj kk kl km kn ko kr ks ku kv kw ky la lb lg li ln lo lt "
    "lu lv mg mh mi mk ml mn mr ms mt my na nb nd ne ng nl nn no nr nv ny oc oj om or os pa pi pl ps pt qu rm rn ro ru rw sa sc sd se sg si sk sl sm sn "
    "so sq sr ss st su sv sw ta te tg th ti tk tl tn to tr ts tt tw ty ug uk ur uz ve vi vo wa wo xh yi yo za zh zu".split())
SIZE = re.compile(r"(?<![A-Za-z0-9.])(E?\d+(?:\.\d+)?[BM])(?![A-Za-z0-9])")    # 1.7B · 0.6B · E2B · 27B · 135M (30B-A3B의 A3B는 앞이 글자라 빠진다)


def derive_fields(it):
    """소식의 meta.tags · 제목에서 결정론으로 뽑은 확인된 값 → {langs: [코드…, multilingual?], base_model, size} (HYO-244). 없으면 빈 값."""
    tags = [t for t in ((it.get("meta") or {}).get("tags") or []) if isinstance(t, str)]
    langs = sorted({t for t in tags if t in ISO639_1})
    if "multilingual" in tags:
        langs.append("multilingual")
    base = next((t.split(":", 1)[1] for t in tags if t.startswith("base_model:") and t.count(":") == 1), "")   # base_model:finetune:X 같은 한정 꼴은 건너뛴다
    m = SIZE.search(f"{it.get('title') or ''} {str(it.get('id') or '').split(':', 1)[-1]}")
    return {"langs": langs, "base_model": base, "size": m.group(1) if m else ""}


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
                    "text": (it.get("text") or "")[:TEXT_CHARS], "popularity": popularity(it), **derive_fields(it)})
    return sorted(out, key=lambda c: str(c["id"]))


def is_v2(slots):
    return (slots.get("version") or 1) >= 2


def system_for(slots):
    return SYSTEM_V2 if is_v2(slots) else SYSTEM


def prompt(cands, slots):
    fields = SLOT_FIELDS[2 if is_v2(slots) else 1]
    sl = [{k: s.get(k) for k in fields if s.get(k) is not None} for s in slots["slots"]]
    lines = [json.dumps({**{k: c[k] for k in ("id", "source", "title", "url", "text")},
                         **{k: c[k] for k in ("langs", "base_model", "size") if c.get(k)}}, ensure_ascii=False) for c in cands]   # 태그에서 뽑은 값 — 있는 것만
    return ("## 자리\n" + yaml.safe_dump(sl, allow_unicode=True, sort_keys=False) +
            f"\n## 후보 ({len(cands)}건, 한 줄에 하나)\n" + "\n".join(lines) + "\n")


def judge_claude(user, model="claude-sonnet-5-5", effort="medium", timeout=900, system=SYSTEM):
    """구독 `claude -p` 한 번 — 도구 없음 · 설정 안 읽음 · API 키 지움 (HYO-99와 같은 설정). → structured_output, 원본"""
    b = os.environ.get("RADAR_CLAUDE_BIN") or shutil.which("claude") or "claude"
    cmd = [*([sys.executable, b] if b.endswith(".py") else [b]), "-p", "--output-format", "json", "--system-prompt", system,
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
                ms.append({"slot": m["slot"], "fit": m["fit"], "reason": str(m.get("reason") or ""), "switch": str(m.get("switch") or ""),
                           "missing": [x for x in (m.get("missing") or []) if x in MISSING_KINDS] if isinstance(m.get("missing"), list) else []})
        got[j["id"]] = {"release": bool(j.get("release")), "matches": ms if j.get("release") else [],
                        "model": str(j.get("model") or "").strip()}
    return got, dropped


def lang_status(c):
    """후보의 언어 상태 (HF 태그만 근거) → (pass | english_only | unknown, 보일 코드 문자열)."""
    langs = set(c.get("langs") or [])
    codes = langs - {"multilingual"}
    if "ko" in langs or "multilingual" in langs or len(codes) >= 3:
        return "pass", "ko" if "ko" in langs else ("multilingual" if "multilingual" in langs else ",".join(sorted(codes)))
    if codes == {"en"}:
        return "english_only", "en"
    return "unknown", ""


def apply_lang(cands, judged, slots):
    """`lang`을 적은 자리의 1점 중 「언어만 모자라」 낮춘 것을 HF 태그로 다시 본다 (HYO-244, 규율 1). judged를 고치고 기록 목록을 돌려준다."""
    by_id = {c["id"]: c for c in cands}
    need = {s["id"]: s["lang"] for s in slots["slots"] if s.get("lang")}
    adj = []
    for cid, j in judged.items():
        for m in j["matches"]:
            if m["slot"] not in need or m["fit"] != 1 or m.get("missing") != ["language"]:
                continue
            status, codes = lang_status(by_id[cid])
            if status == "pass":
                to, why, note = 2, f"HF 태그로 확인({codes})", f" · 언어: HF 태그로 확인({codes}) — 코드가 2로 올림"
            elif status == "english_only":
                to, why, note = 0, "영어 전용 태그", " · 언어: 영어 전용 태그 — 코드가 0으로 내림"
            else:
                continue
            adj.append({"id": cid, "slot": m["slot"], "from": m["fit"], "to": to, "why": why})
            m["fit"], m["reason"] = to, m["reason"] + note
        j["matches"] = [m for m in j["matches"] if m["fit"] > 0]      # 0점은 「넣지 않는다」와 같다
    return adj


def fold_same_model(xs):
    """같은 모델(모델 답의 model 이름 — 대소문자 · 띄어쓰기 무시)은 한 줄로. 정렬 순서의 첫 것이 대표, 나머지 id는 also에 (v2)."""
    out, by = [], {}
    for x in xs:
        key = re.sub(r"\s+", " ", x.get("model") or "").strip().lower()
        if key and key in by:
            by[key].setdefault("also", []).append(x["id"])
            continue
        if key:
            by[key] = x
        out.append(x)
    return out


def check_list(cands, judged, per):
    """v2 「확인 필요」 — 최고 적합도가 1인 출시(정보가 모자라거나 역할 조건 일부를 못 지킨 것). detect로는 안 보내고 목록에 한 줄씩.
    같은 model은 한 줄로, 이미 어느 역할에 2 이상으로 보인 model · id는 뺀다 (2026-11-12: W41 「Gemini 4 Argon」 HN 제목만 있는 글이 실행마다 2 ↔ 1로 흔들렸다)."""
    def key(model, cid):
        return re.sub(r"\s+", " ", model or "").strip().lower() or cid
    by_id = {c["id"]: c for c in cands}
    shown_ids = {i for xs in per.values() for x in xs for i in [x["id"]] + list(x.get("also") or [])}
    shown_models = {key(x.get("model"), x["id"]) for xs in per.values() for x in xs}
    rows = []
    for cid, j in judged.items():
        ms = j["matches"]
        if not j["release"] or not ms or max(m["fit"] for m in ms) != 1 or cid in shown_ids or key(j.get("model"), cid) in shown_models:
            continue
        c = by_id[cid]
        rows.append({"id": cid, "title": c["title"], "url": c["url"], "source": c["source"], "model": j.get("model", ""),
                     "slots": [m["slot"] for m in ms], "reason": ms[0]["reason"], "switch": ms[0].get("switch", ""), "popularity": c["popularity"]})
    rows.sort(key=lambda x: (x["source"] != "vendor", x["popularity"], x["id"]))
    out, seen = [], {}
    for x in rows:
        k = key(x["model"], x["id"])
        if k in seen:
            seen[k].setdefault("also", []).append(x["id"])
            continue
        seen[k] = x
        out.append(x)
    return out


def rank(cands, judged, slots, k=None, fit_min=None):
    v2 = is_v2(slots)
    if k is None:
        k = None if v2 else TOPK     # v2: 상한 없음 — 문턱 넘은 것은 다 보인다 (사용자 11/12 「건 수 늘어도 돼, 놓치지만 않으면」)
    fit_min = FIT_MIN if fit_min is None else fit_min
    by_id = {c["id"]: c for c in cands}
    per = {s["id"]: [] for s in slots["slots"]}
    for cid, j in judged.items():
        for m in j["matches"]:
            if m["fit"] >= fit_min:
                c = by_id[cid]
                per[m["slot"]].append({"id": cid, "title": c["title"], "url": c["url"], "source": c["source"], "why": c["why"],
                                       "fit": m["fit"], "reason": m["reason"], "switch": m.get("switch", ""), "popularity": c["popularity"],
                                       "model": j.get("model", "")})
    for s in per:
        xs = sorted(per[s], key=lambda x: (-x["fit"], x["source"] != "vendor", x["popularity"], x["id"]))
        if v2:
            xs = fold_same_model(xs)
        per[s] = xs if k is None else xs[:k]
        for i, x in enumerate(per[s], 1):
            x["rank"] = i
    return per


DEFAULT_MODEL = "claude-sonnet-5-5"


def run(items, slots, engine="claude", out=None, reuse=False, model=None):
    model = model or DEFAULT_MODEL
    cands = collect(items)
    raw_path = Path(out) / "slots-raw.json" if out else None
    if reuse and raw_path and raw_path.exists():
        so = json.loads(raw_path.read_text(encoding="utf-8"))["structured_output"]
    elif not cands:
        so = {"judgments": []}
    elif engine == "stub":
        so = judge_stub(cands, slots)
    elif engine == "claude":
        so, res = judge_claude(prompt(cands, slots), model=model, system=system_for(slots))
        if raw_path:
            write_json(raw_path, {"engine": "claude-cli", "model": model, "structured_output": so, "usage": res.get("usage"),
                                  "cost_usd_equiv": res.get("total_cost_usd"), "billing": "subscription"})
    else:
        raise SlotError(f"모르는 엔진 {engine!r} (claude · stub — codex는 HYO-169)")
    judged, dropped = validate(so, cands, slots)
    lang_adjusted = apply_lang(cands, judged, slots)
    per = rank(cands, judged, slots)
    to_detect = []      # 자리마다 top-k 전부 (detect_feed.slot_picks와 같은 규칙 — query-topk.md §2 변경 2026-11-04)
    for s in slots["slots"]:
        to_detect += [x["id"] for x in per[s["id"]] if x["id"] not in to_detect]
    return {"slots_version": slots.get("version"), "engine": engine, "k": None if is_v2(slots) else TOPK, "fit_min": FIT_MIN,
            "candidates": len(cands), "judged": len(judged), "releases": sum(1 for j in judged.values() if j["release"]),
            "unjudged": sorted({c["id"] for c in cands} - set(judged)), "dropped": dropped, "lang_adjusted": lang_adjusted,
            "slot_names": {s["id"]: s.get("name") for s in slots["slots"]},
            "slot_current": {s["id"]: s.get("current") or [] for s in slots["slots"]},
            "slot_repos": {s["id"]: s.get("repo") for s in slots["slots"]},       # detect가 「어느 repo의 어느 역할 후보인가」를 받게 (HYO-205)
            "slot_roles": {s["id"]: s.get("role") for s in slots["slots"]},
            "slot_components": {s["id"]: slot_components(s) for s in slots["slots"]},  # 자리 후보의 affected component — detect가 그대로 쓴다 (HYO-219)
            "per_slot": per, "to_detect": to_detect, "candidate_list": cands,
            "check": check_list(cands, judged, per) if is_v2(slots) else []}     # 「확인 필요」 — 목록에만, detect 아님 (v2)


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
    r.add_argument("--model", default=os.environ.get("RADAR_SLOTS_MODEL", DEFAULT_MODEL),
                   help="claude -p 모델 (전체 id, 기본 claude-sonnet-5-5 — 2026-11-12 Haiku 비교)")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    from . import union  # 합집합(HYO-167 D6) — 스킬만 잡은 항목도 자리 질의 후보로. 꺼져 있으면 빈 목록
    res = run(read_items(a.snapshot) + union.extra_items(out), load_slots(a.slots), engine=a.engine, out=out, reuse=a.reuse, model=a.model)
    write_json(out / "slots.json", res)
    print(summary_line(res))
    for s, v in res["per_slot"].items():
        for x in v:
            print(f"  {s} #{x['rank']} fit {x['fit']} | {x['title'][:90]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
