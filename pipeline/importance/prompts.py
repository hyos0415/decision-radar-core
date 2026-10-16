"""분류 프롬프트. 정의 문구는 importance-policy §2-1(+v1.1~v1.3)·§3에서 가져왔다 — 바꾸면 정책 쪽을 먼저 본다.
등급 표의 「예」 칸은 §2-1 예 열과 §4 두 등급 예(langfuse)다 (HYO-124 — 모델이 예시 등급을 따르지 않았다). Jev 질문(TIER_MEANING)에는 넣지 않았다.
v2(HYO-128): 예에 없는 기술을 분류 불가로 내던 부작용 · langfuse L5만 · python L0+L1을 고치는 규칙 세 줄 — eval/importance/tier-examples.md §3.

백엔드가 GPT면 이 프롬프트를 그대로 쓴다. Jev는 문장을 읽고 답하는 모델이 아니라 선택지 + 확률을 내므로,
같은 정의를 질문의 `instructions`·`criteria`로 나눠 담게 된다 (같은 판정 기준이지 같은 프롬프트가 아니다 — finance_verifier run_eval_jev.py와 같은 단서).
"""
import hashlib
import re

from . import policy

TIER_INSTRUCTIONS = """당신은 소프트웨어 프로젝트의 기술 하나에 「중요도 등급」을 매긴다. 주어진 정보만 쓰고 외부를 조회하지 않는다.

## 등급 정의

| 등급 | 이름 | 무엇 | 예 (이 정책이 정한 배정) |
|---|---|---|---|
| L0 | 언어 런타임 | 인터프리터·언어 버전 | 언어 Python 3.12 |
| L1 | 실행 기반 | 베이스 이미지 · 서빙 엔진 · GPU 스택 · 우리가 띄우는 서버(자체 호스팅 DB·검색 엔진, docker 포함) | 이미지 `python:3.12-slim` · vLLM(`vllm/vllm-openai`) · llama.cpp `llama-server` · CUDA |
| L2 | 모델 (호스팅·로컬) | 외부 API로 부르는 모델, 로컬에서 서빙하는 모델 | Bedrock `nova-pro` · `claude-sonnet-5` · `gpt-5.6-terra` · Jev |
| L3 | 호스팅 서비스 | 모델이 아닌, 남이 운영하는 외부 서비스·공공 API | Langfuse Cloud · Bedrock Guardrails · Finlife API · 국가법령정보 API |
| L4 | 앱 프레임워크 | 앱의 뼈대 — 바꾸면 코드 전반이 바뀜 | fastapi · pydantic · uvicorn · jinja2 |
| L5 | 공급자 SDK | L1·L2·L3을 부르는 클라이언트 (DB·검색 엔진 드라이버 포함) | boto3 · anthropic · openai · google-genai · langfuse(L3+L5) · typesafe-sdk |
| L6 | 도메인 라이브러리 | 특정 기능 하나를 맡음 | kiwipiepy · pdfplumber · torch · transformers |
| L7 | 개발·테스트 도구 | 운영에 안 들어감 | pytest · ruff · k6 |
| L8 | 유틸 | 작고 대체 쉬움 | python-dotenv · tzdata · requests · httpx |

규칙:
- 등급은 **이 프로젝트에서 그 기술을 쓰는 방식**(`usage`)으로 매긴다. 같은 기술도 쓰임에 따라 등급이 달라진다
- 언어(TypeScript·Java 등) → L0 · DB·검색 엔진·그래프 DB → L1 · CI(GitHub Actions)·빌드 도구·타입 정의 → L7
- 예 칸의 기술은 그 등급이 기본이다. `usage`가 예와 분명히 다른 쓰임을 말할 때만 다른 등급을 고른다
- 예 칸은 몇 개만 보여 준 것이다. **예에 없는 기술은 「무엇」 칸의 정의로 고른다 — 예에 없다는 것은 `unsure`의 이유가 아니다.** 그 기술이 무엇인지(이름으로 널리 알려진 용도)는 아는 대로 판단해도 된다
- 언어와 그 언어의 베이스 이미지는 따로 분류되는 다른 기술이다 — 기술이 언어(`python`)면 L0, 이미지(`python:3.12-slim`)면 L1
- 한 기술이 정말로 두 등급에 걸치면 두 등급을 다 적어도 된다. langfuse는 L3+L5다 — SDK가 Langfuse 서비스로 보내므로 `usage`에 Cloud라는 말이 없어도 그렇다
- 그 기술이 무엇인지 모르거나, 알려진 용도와 `usage`로도 등급이 정해지지 않으면 억지로 고르지 말고 `unsure`를 true로 하고 이유를 적는다. 이때 `tiers`는 빈 목록이다
- `reason`은 한 문장
"""

# 등급 프롬프트 판 — 등급 파일의 지문에 들어간다(tiers.fingerprint). 프롬프트를 고치면 사람이 고치지 않은 기술이 한 번 다시 분류된다 (HYO-124)
TIER_PROMPT_VERSION = hashlib.sha1(TIER_INSTRUCTIONS.encode("utf-8")).hexdigest()[:8]

CHANGE_INSTRUCTIONS = """당신은 기술 소식(릴리스 노트·공지·페이지 변경) 하나가 **어떤 종류의 변화인지** 짚는다. 주어진 글만 쓰고 외부를 조회하지 않는다.
해당하는 종류를 **전부** 고른다 (여러 개 가능). 어느 것에도 안 맞으면 빈 목록.

| 종류 | 뜻 |
|---|---|
| breaking | 사용하는 코드가 깨질 수 있는 변경 (릴리스 노트의 Breaking changes·커밋 제목의 `!:` 표시, 또는 업그레이드하려면 마이그레이션이 필요하다는 안내) |
| deprecation | 곧 없어질 예정인 기능·API |
| eol | 특정 버전의 지원 종료 (예: Python 3.12 EOL 일정) |
| security | 보안 패치·CVE·보안 권고 — 글이 보안 문제라고 밝힌 것만(security·CVE·GHSA·vulnerability·advisory). 대상 기술 자체의 수정이어야 한다(같은 저장소의 다른 언어 패키지·개발 의존성·CI 스캔 설정은 아니다) |
| yanked | 배포 철회 (PyPI yanked 등) |
| major | 메이저 버전 릴리스 (첫 숫자가 오른 것. 0.x에서 둘째 숫자만 오른 것은 major가 아니다) |
| minor_patch | 그 밖의 minor·patch 릴리스 |
| model_retirement | 호스팅 모델의 은퇴(retirement)·지원 종료 일정 공지 |
| pricing | 호스팅 서비스의 가격·사용 한도(rate limit) 변경 |

규칙:
- 「새 버전이 나왔다」만 있고 위의 어느 것도 말하지 않으면 해당 종류는 minor_patch 또는 major(버전 숫자로 판단 — 0.x에서 둘째 숫자만 오른 것은 minor_patch)뿐이다
- 글에 적힌 사실만 쓴다. 짐작으로 breaking·security를 붙이지 않는다
- `reason`은 한 문장
"""


# ---------------------------------------------------------------------------- Jev (TypeSafe System One) 질문 정의
# 위 GPT 프롬프트의 표와 같은 뜻(같은 판정 기준)을 질문별 지시에 나눠 담는다. test_importance.py가 두 표의 문구가 같은지 대조한다.

CHANGE_MEANING = {
    "breaking": "사용하는 코드가 깨질 수 있는 변경 (릴리스 노트의 Breaking changes·커밋 제목의 `!:` 표시, 또는 업그레이드하려면 마이그레이션이 필요하다는 안내)",
    "deprecation": "곧 없어질 예정인 기능·API",
    "eol": "특정 버전의 지원 종료 (예: Python 3.12 EOL 일정)",
    "security": "보안 패치·CVE·보안 권고 — 글이 보안 문제라고 밝힌 것만(security·CVE·GHSA·vulnerability·advisory). 대상 기술 자체의 수정이어야 한다(같은 저장소의 다른 언어 패키지·개발 의존성·CI 스캔 설정은 아니다)",
    "yanked": "배포 철회 (PyPI yanked 등)",
    "major": "메이저 버전 릴리스 (첫 숫자가 오른 것. 0.x에서 둘째 숫자만 오른 것은 major가 아니다)",
    "minor_patch": "그 밖의 minor·patch 릴리스",
    "model_retirement": "호스팅 모델의 은퇴(retirement)·지원 종료 일정 공지",
    "pricing": "호스팅 서비스의 가격·사용 한도(rate limit) 변경",
}
TIER_MEANING = {
    "L0": ("언어 런타임", "인터프리터·언어 버전"),
    "L1": ("실행 기반", "베이스 이미지 · 서빙 엔진 · GPU 스택 · 우리가 띄우는 서버(자체 호스팅 DB·검색 엔진, docker 포함)"),
    "L2": ("모델 (호스팅·로컬)", "외부 API로 부르는 모델, 로컬에서 서빙하는 모델"),
    "L3": ("호스팅 서비스", "모델이 아닌, 남이 운영하는 외부 서비스·공공 API"),
    "L4": ("앱 프레임워크", "앱의 뼈대 — 바꾸면 코드 전반이 바뀜"),
    "L5": ("공급자 SDK", "L1·L2·L3을 부르는 클라이언트 (DB·검색 엔진 드라이버 포함)"),
    "L6": ("도메인 라이브러리", "특정 기능 하나를 맡음"),
    "L7": ("개발·테스트 도구", "운영에 안 들어감"),
    "L8": ("유틸", "작고 대체 쉬움"),
}
_CHANGE_RULE = "소식(state.title·state.body)에 적힌 사실만 쓴다. 외부를 조회하지 않고, 짐작으로 breaking·security를 붙이지 않는다. 대상 기술은 state.technology."
_CHANGE_EXTRA = {
    "major": "버전 숫자로 판단한다 (예: 3.x → 4.0. 0.22 → 0.23은 major가 아니다).",
    "minor_patch": "「새 버전이 나왔다」만 있고 다른 종류를 말하지 않는 minor·patch 릴리스일 때만 예.",
}
_TIER_RULE = ("기술(state.technology)이 이 프로젝트에서 쓰이는 방식(state.usage)으로 등급을 고른다. 같은 기술도 쓰임에 따라 등급이 달라진다. "
              "언어(TypeScript·Java 등) → L0 · DB·검색 엔진·그래프 DB → L1 · CI(GitHub Actions)·빌드 도구·타입 정의 → L7. 주어진 정보만 쓰고 외부를 조회하지 않는다.")


def jev_change_questions():
    """변화 종류마다 예/아니오(noul) 질문 하나 — 한 요청에 병렬로 묻는다. 키 = policy.CHANGES의 이름."""
    out = {}
    for kind, meaning in CHANGE_MEANING.items():
        out[kind] = {"type": "noul",
                     "instructions": f"이 소식은 「{kind}」 종류의 변화인가? 「{kind}」 = {meaning}. {_CHANGE_RULE} {_CHANGE_EXTRA.get(kind, '')}".strip(),
                     "criteria": {"true": f"소식이 「{kind}」({meaning})에 해당하는 내용을 말한다", "false": f"소식이 「{kind}」에 해당하는 내용을 말하지 않는다"}}
    return out


def jev_tier_question():
    """등급 L0~L8 선택지 한 질문. 두 등급에 걸치면 두 번째 등급도 확률로 읽는다 (백엔드가 p ≥ 0.5로 거른다)."""
    return {"type": "choice", "instructions": f"이 기술은 어느 중요도 등급인가? {_TIER_RULE}",
            "criteria": {t: f"{name} — {meaning}" for t, (name, meaning) in TIER_MEANING.items()}}


# ---------------------------------------------------------------------------- 본문 창 — 위험 절 순서 재배치 (importance-policy §8 변경 2026-10-06 밤)
# 분류기는 본문 앞 TEXT_MAX자만 읽는다(비용). 정리형 노트(vLLM·transformers)는 Breaking·Security 절이 Highlights·새 모델 뒤에 있어
# 창 밖으로 밀린다. 그래서 창 밖에서 시작하는 위험 절만 원문에서 빼 맨 앞으로 옮긴다 — 내용을 더하거나 줄이지 않고 순서만 바꾼다.
# 규칙은 문서 구조에 붙은 표시만 본다. 본문 줄의 위험 단어(remove·migrate…)는 W41 창 밖 42줄이 전부 소음이라 쓰지 않는다.
TEXT_MAX = 3000
_RISK_WORD = r"(?:⚠\W*)?(?:breaking|security|deprecat\w*|removals?|migration)"
_R1_HEADING = re.compile(r"^\s*(#{1,6})\s*" + _RISK_WORD, re.I)                       # 위험 제목 → 다음 같은·상위 제목까지 절 전체
_R2_LABEL = re.compile(r"^(\s*)[*-]\s*\*\*\s*" + _RISK_WORD + r"[^*]*\*\*", re.I)      # * **Security**: … → 그 글머리 하나
_R3_ADVISORY = re.compile(r"\b(?:CVE-\d{4}-\d+|GHSA(?:-[0-9a-z]{4}){3})\b", re.I)     # 권고 번호가 든 줄
_R4_COMMIT = re.compile(r"^\s*[*-]\s*\w+(?:\([^)]*\))?!:|\(security\)", re.I)          # Conventional Commits breaking 표시 · security 범위
_HEADING = re.compile(r"^\s*(#{1,6})\s")
_BULLET = re.compile(r"^(\s*)[*-]\s")


def _risk_spans(lines):
    """[(규칙 순위, 시작 줄, 끝 줄(제외))] — 위험 절의 줄 범위."""
    spans = []
    for i, line in enumerate(lines):
        m = _R1_HEADING.match(line)
        if m:
            level, j = len(m.group(1)), i + 1
            while j < len(lines) and not ((h := _HEADING.match(lines[j])) and len(h.group(1)) <= level):
                j += 1
            spans.append((1, i, j))
            continue
        m = _R2_LABEL.match(line)
        if m:
            indent, j = len(m.group(1)), i + 1
            while j < len(lines) and lines[j].strip() and not _HEADING.match(lines[j]) \
                    and not ((b := _BULLET.match(lines[j])) and len(b.group(1)) <= indent):
                j += 1
            spans.append((2, i, j))
        elif _R3_ADVISORY.search(line):
            spans.append((3, i, i + 1))
        elif _R4_COMMIT.search(line):
            spans.append((4, i, i + 1))
    return spans


def window_text(text, text_max=TEXT_MAX):
    """본문 → 분류기가 읽을 창. 창 밖에서 시작하는 위험 절을 맨 앞으로 옮긴 뒤 text_max에서 자른다. 창 안에 다 들어오면 그대로."""
    text = text or ""
    if len(text) <= text_max:
        return text
    lines = text.splitlines(keepends=True)
    starts, pos = [], 0
    for line in lines:
        starts.append(pos)
        pos += len(line)
    taken, moved = set(), []
    for rank, i, j in sorted(_risk_spans(lines), key=lambda s: (s[0], s[1])):
        if starts[i] < text_max or taken & set(range(i, j)):
            continue                                   # 이미 창 안이거나 앞 규칙이 가져간 줄
        taken |= set(range(i, j))
        moved.append("".join(lines[i:j]).rstrip("\n") + "\n")
    if not moved:
        return text[:text_max]
    rest = "".join(line for k, line in enumerate(lines) if k not in taken)
    return ("".join(moved) + "\n" + rest)[:text_max]


def jev_change_state(q, text_max=TEXT_MAX):
    return {"technology": q.technology, "title": q.title, "body": window_text(q.text, text_max) or "(본문 없음)"}


def jev_tier_state(q):
    return {"technology": q.name, "usage": q.usage or "(정보 없음)", "source": q.source or "(정보 없음)"}


def tier_prompt(q):
    return (f"기술: {q.name}\n이 프로젝트에서 쓰는 방식(usage): {q.usage or '(정보 없음)'}\n출처: {q.source or '(정보 없음)'}\n")


def change_prompt(q, text_max=TEXT_MAX):
    text = window_text(q.text, text_max)
    return f"대상 기술: {q.technology}\n제목: {q.title}\n\n본문:\n{text or '(본문 없음)'}\n"


assert set(policy.TIERS) == {f"L{i}" for i in range(9)}
