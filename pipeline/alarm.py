"""경보 그물 (HYO-170 가 · 나) — 레인 B에 묻힌 우리 스택의 사고를 관문으로 건진다.

레인 나누기(lanes.py)는 통로로만 가른다 — 의존성 GitHub releases · 벤더 채널이 아니면 레인 B다. 그래서 W44의
「Compromised LiteLLM 1.107.0 on PyPI」(HN 596점 — 1.107.0은 PyPI에만 있었다) · 「http2shell is an RCE in Node, Python」 ·
「Broccoli2Shell: Brotli decoder RCE in Node.js, Python」 · 「PSA: llama.cpp CVE-…」가 레인 B 순위에만 있다가 아무 데도 안 갔다.

- 가. 그물: 레인 B 항목 중 제목에 **스택 이름(+ 런타임 · 생태계 이름) + 사고 · 종료 낱말**이 함께 나오면 인기와 상관없이
  관문에도 보낸다 (레인은 그대로 — 레인 B 순위에도 남는다). 관문이 등급 × 변화로 남김 · 버림을 정한다 (규율 1 — 거르기는 스크립트와 결정표)
- 나. 「우리 스택 아님」: 사고 낱말 + 인기 높음인데 스택에 안 걸린 것 상위 몇 건 — 「보고 넘겼다」를 화면에 보인다 (PHP2Shell 주)

낱말 · 문턱은 W41~W45로 정한 첫 값이다 (2026-11-04). 바꾸면 doc-pipeline §2-3 변경 줄에 남긴다.
"""
import re

from . import lanes

# 사고 · 종료 낱말 (제목만 본다 — 본문까지 보면 「security」가 든 릴리스 노트 · 글이 다 걸린다)
ALARM = re.compile(r"(?i)\bCVE-\d|vulnerab|exploit|backdoor|malware|infostealer|credential.steal|compromis|supply.chain|hijack|breach|\bRCE\b|"
                   r"\bPSA\b|yank|deprecat|retir|end.of.life|\bEOL\b|sunset|shut(?:ting)?.down|discontinu|security (?:fix|advisory|update|patch|release)")
# 스택 manifest에 이름으로 안 나오지만 우리가 그 위에서 돈다 (Python 백엔드 · Electron 안의 Node · Chromium · 패키지 저장소 · 이미지 · CI ·
# 구독 CLI 엔진 — eval 러너 · 자리 질의 · 제품 엔진이 claude -p, judge가 codex exec)
RUNTIME_TERMS = ("Python", "CPython", "Node.js", "Chromium", "PyPI", "npm", "Docker", "GitHub Actions", "Claude Code", "Codex")
# 스택 이름이지만 영어 낱말로 더 흔하다 — 제목에서 이름으로 보지 않는다 (W43 「… in response to requests」)
COMMON_WORDS = {"requests"}
NOT_OURS_HN_POINTS = 100      # 「우리 스택 아님」 인기 문턱 — HN 점수
NOT_OURS_REDDIT_RANK = 10     # Reddit 피드 순위 (RSS는 점수를 안 준다)
NOT_OURS_TOP = 5


def net(lane_b, stack_terms):
    """레인 B → [(항목, 걸린 이름들)] — 제목에 사고 낱말과 스택 · 런타임 이름이 함께 나오는 것 (인기 무관)."""
    terms = [t for t in dict.fromkeys(list(stack_terms) + list(RUNTIME_TERMS)) if t.lower() not in COMMON_WORDS]
    out = []
    for it in lane_b:
        if ALARM.search(it.get("title") or ""):
            m = lanes.stack_mentions(it, terms)
            if m:
                out.append((it, m))
    return out


def _popular(it):
    if it.get("source") == "hn":
        return ((it.get("signals") or {}).get("points") or 0) >= NOT_OURS_HN_POINTS
    if it.get("source") == "reddit":
        ranks = [v for k, v in (it.get("signals") or {}).items() if k.startswith("rank:") and isinstance(v, (int, float))]   # slots._rank와 같은 읽기
        return bool(ranks) and min(ranks) <= NOT_OURS_REDDIT_RANK
    return False


def not_ours(lane_b, caught_ids, top=None):
    """사고 낱말 + 인기 높음 + 그물에 안 걸림 → 인기 순 상위 top건 [{id, title, url, source, points}]."""
    top = NOT_OURS_TOP if top is None else top
    hits = [it for it in lane_b if it["id"] not in caught_ids and ALARM.search(it.get("title") or "") and _popular(it)]
    hits.sort(key=lambda it: (-((it.get("signals") or {}).get("points") or 0), it["id"]))
    return [{"id": it["id"], "title": it.get("title"), "url": it.get("url"), "source": it.get("source"),
             "points": (it.get("signals") or {}).get("points")} for it in hits[:top]]
