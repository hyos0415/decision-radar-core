"""분류 프롬프트. 정의 문구는 importance-policy §2-1(+v1.1~v1.3)·§3에서 가져왔다 — 바꾸면 정책 쪽을 먼저 본다.

백엔드가 GPT면 이 프롬프트를 그대로 쓴다. Jev는 문장을 읽고 답하는 모델이 아니라 선택지 + 확률을 내므로,
같은 정의를 질문의 `instructions`·`criteria`로 나눠 담게 된다 (같은 판정 기준이지 같은 프롬프트가 아니다 — finance_verifier run_eval_jev.py와 같은 단서).
"""
from . import policy

TIER_INSTRUCTIONS = """당신은 소프트웨어 프로젝트의 기술 하나에 「중요도 등급」을 매긴다. 주어진 정보만 쓰고 외부를 조회하지 않는다.

## 등급 정의

| 등급 | 이름 | 무엇 |
|---|---|---|
| L0 | 언어 런타임 | 인터프리터·언어 버전 |
| L1 | 실행 기반 | 베이스 이미지 · 서빙 엔진 · GPU 스택 · 우리가 띄우는 서버(자체 호스팅 DB·검색 엔진, docker 포함) |
| L2 | 모델 (호스팅·로컬) | 외부 API로 부르는 모델, 로컬에서 서빙하는 모델 |
| L3 | 호스팅 서비스 | 모델이 아닌, 남이 운영하는 외부 서비스·공공 API |
| L4 | 앱 프레임워크 | 앱의 뼈대 — 바꾸면 코드 전반이 바뀜 |
| L5 | 공급자 SDK | L1·L2·L3을 부르는 클라이언트 (DB·검색 엔진 드라이버 포함) |
| L6 | 도메인 라이브러리 | 특정 기능 하나를 맡음 |
| L7 | 개발·테스트 도구 | 운영에 안 들어감 |
| L8 | 유틸 | 작고 대체 쉬움 |

규칙:
- 등급은 **이 프로젝트에서 그 기술을 쓰는 방식**(`usage`)으로 매긴다. 같은 기술도 쓰임에 따라 등급이 달라진다
- 언어(TypeScript·Java 등) → L0 · DB·검색 엔진·그래프 DB → L1 · CI(GitHub Actions)·빌드 도구·타입 정의 → L7
- 한 기술이 정말로 두 등급에 걸치면 두 등급을 다 적어도 된다
- 쓰임새 정보가 없거나 정의로 판단이 안 서면 억지로 고르지 말고 `unsure`를 true로 하고 이유를 적는다. 이때 `tiers`는 빈 목록이다
- `reason`은 한 문장
"""

CHANGE_INSTRUCTIONS = """당신은 기술 소식(릴리스 노트·공지·페이지 변경) 하나가 **어떤 종류의 변화인지** 짚는다. 주어진 글만 쓰고 외부를 조회하지 않는다.
해당하는 종류를 **전부** 고른다 (여러 개 가능). 어느 것에도 안 맞으면 빈 목록.

| 종류 | 뜻 |
|---|---|
| breaking | 사용하는 코드가 깨질 수 있는 변경 (릴리스 노트의 Breaking changes) |
| deprecation | 곧 없어질 예정인 기능·API |
| eol | 특정 버전의 지원 종료 (예: Python 3.12 EOL 일정) |
| security | 보안 패치·CVE·보안 권고 |
| yanked | 배포 철회 (PyPI yanked 등) |
| major | 메이저 버전 릴리스 |
| minor_patch | 그 밖의 minor·patch 릴리스 |
| model_retirement | 호스팅 모델의 은퇴(retirement)·지원 종료 일정 공지 |
| pricing | 호스팅 서비스의 가격·사용 한도(rate limit) 변경 |

규칙:
- 「새 버전이 나왔다」만 있고 위의 어느 것도 말하지 않으면 해당 종류는 minor_patch 또는 major(버전 숫자로 판단)뿐이다
- 글에 적힌 사실만 쓴다. 짐작으로 breaking·security를 붙이지 않는다
- `reason`은 한 문장
"""


# ---------------------------------------------------------------------------- Jev (TypeSafe System One) 질문 정의
# 위 GPT 프롬프트의 표와 같은 뜻(같은 판정 기준)을 질문별 지시에 나눠 담는다. test_importance.py가 두 표의 문구가 같은지 대조한다.

CHANGE_MEANING = {
    "breaking": "사용하는 코드가 깨질 수 있는 변경 (릴리스 노트의 Breaking changes)",
    "deprecation": "곧 없어질 예정인 기능·API",
    "eol": "특정 버전의 지원 종료 (예: Python 3.12 EOL 일정)",
    "security": "보안 패치·CVE·보안 권고",
    "yanked": "배포 철회 (PyPI yanked 등)",
    "major": "메이저 버전 릴리스",
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
    "major": "버전 숫자로 판단한다 (예: 3.x → 4.0).",
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


def jev_change_state(q, text_max=3000):
    return {"technology": q.technology, "title": q.title, "body": (q.text or "")[:text_max] or "(본문 없음)"}


def jev_tier_state(q):
    return {"technology": q.name, "usage": q.usage or "(정보 없음)", "source": q.source or "(정보 없음)"}


def tier_prompt(q):
    return (f"기술: {q.name}\n이 프로젝트에서 쓰는 방식(usage): {q.usage or '(정보 없음)'}\n출처: {q.source or '(정보 없음)'}\n")


def change_prompt(q, text_max=3000):
    text = (q.text or "")[:text_max]
    return f"대상 기술: {q.technology}\n제목: {q.title}\n\n본문:\n{text or '(본문 없음)'}\n"


assert set(policy.TIERS) == {f"L{i}" for i in range(9)}
