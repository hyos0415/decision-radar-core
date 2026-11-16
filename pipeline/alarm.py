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
RUNTIME_TERMS = ("Python", "CPython", "Node.js", "Chromium", "PyPI", "npm", "Docker", "GitHub Actions", "Claude Code", "Codex",
                 # 서버 OS (HYO-185 후속, 2026-11-12 사용자 「medical-mate AI 서버를 직접 운영」 — Lightsail Ubuntu 24.04 · Docker Compose).
                 # 이 이름이 없어 W45 「CVE-2026-98432 … Ubuntu」 · W46 「Ubuntu … Linux 0day RCE」 · 「OpenSSH … bypass」가 「우리 스택 아님」으로 빠졌다.
                 # W41~W46에 더해 보면 새로 걸리는 글 중 엉뚱한 것은 공유기 글(「… eCos, Linux」) 1건
                 "Ubuntu", "Linux", "OpenSSH")
# 줄임 이름 → 런타임 이름. 대소문자를 가려 본다 — 「Kubernetes node」 같은 보통 낱말은 안 걸리게 (W44 「http2shell is an RCE in Node, Python」, 2026-11-04).
# apt(서버 패키지 관리자)는 소문자만 — 대문자 「APT」는 보안 기사에서 국가 해킹 조직(advanced persistent threat)이다 (W46 「Debian apt security update」, 2026-11-12)
CASED_ALIASES = {"Node": "Node.js", "apt": "apt"}
ALIAS_FLAGS = 0               # 대소문자 구분 (re.I로 바꾸면 「Kubernetes node」도 걸린다 — 테스트 변이)
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
            for short, full in CASED_ALIASES.items():
                if full not in m and re.search(r"(?<![0-9A-Za-z])" + re.escape(short) + r"(?![0-9A-Za-z]|\.js)", it.get("title") or "", ALIAS_FLAGS):
                    m.append(full)
            if m:
                out.append((it, m))
    return out


def _points(it):
    return (it.get("signals") or {}).get("points") or 0


def _best_rank(it):
    ranks = [v for k, v in (it.get("signals") or {}).items() if k.startswith("rank:") and isinstance(v, (int, float))]   # slots._rank와 같은 읽기
    return min(ranks) if ranks else None


def _popular(it):
    if it.get("source") == "hn":
        return _points(it) >= NOT_OURS_HN_POINTS
    if it.get("source") == "reddit":
        r = _best_rank(it)
        return r is not None and r <= NOT_OURS_REDDIT_RANK
    return False


def popularity(it):
    """경보 글 대표 고르기 정렬 키 — 작을수록 앞 (2026-11-04: id 순이면 W44 python 묶음 대표가 본문 없는 트윗 186점이 되고
    전용 사이트 · CVE 블로그가 붙은 400점이 접혔다). HN 점수 높은 순 → Reddit 피드 순위 → 그 밖. HN을 앞에 두는 건 1차 출처 링크가 붙어 오기 때문"""
    if it.get("source") == "hn":
        return (0, -_points(it))
    r = _best_rank(it)
    return (1, r) if it.get("source") == "reddit" and r is not None else (2, 0)


def not_ours(lane_b, caught_ids, top=None):
    """사고 낱말 + 인기 높음 + 그물에 안 걸림 → 인기 순 상위 top건 [{id, title, url, source, points}]."""
    top = NOT_OURS_TOP if top is None else top
    hits = [it for it in lane_b if it["id"] not in caught_ids and ALARM.search(it.get("title") or "") and _popular(it)]
    hits.sort(key=lambda it: (-((it.get("signals") or {}).get("points") or 0), it["id"]))
    return [{"id": it["id"], "title": it.get("title"), "url": it.get("url"), "source": it.get("source"),
             "points": (it.get("signals") or {}).get("points")} for it in hits[:top]]
