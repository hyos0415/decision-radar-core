"""tier-screen 실행 디렉터리 만들기 — prompt.md + items.json만 넣는다 (정답지는 넣지 않는다).

실행: python eval/tier-screen/make_run.py <실행 디렉터리>
"""

import json
import os
import random
import sys

# (id, 기술, 출처) — 출처는 실제 파이프라인이 결정론적으로 아는 정도만. 등급 이름·정의 문구를 넣지 않는다
ITEMS = [
    ("T01", "Python 3.12", "Dockerfile의 언어 버전 / pyproject requires-python"),
    ("T02", "python:3.12-slim", "Dockerfile FROM"),
    ("T03", "vllm/vllm-openai:latest", "scripts/run_vllm.sh 의 docker run 이미지"),
    ("T04", "llama.cpp llama-server", "로컬에서 띄우는 바이너리, 127.0.0.1:8081"),
    ("T05", "CUDA", "vLLM 컨테이너가 쓰는 GPU 런타임"),
    ("T06", "apac.amazon.nova-pro-v1:0", "코드 속 모델 id (AWS Bedrock converse 호출)"),
    ("T07", "claude-sonnet-5", "코드 속 모델 id (Anthropic API 호출)"),
    ("T08", "gpt-5.6-terra", "코드 속 모델 id (OpenAI API 호출)"),
    ("T09", "jev-1.13.0", "코드 속 모델 id (TypeSafe API 호출)"),
    ("T10", "Langfuse Cloud", ".env 의 LANGFUSE_HOST 가 가리키는 외부 서비스"),
    ("T11", "Amazon Bedrock Guardrails", "코드 속 apply_guardrail 호출 (guardrail id)"),
    ("T12", "금융감독원 Finlife Open API", "코드 속 URL finlife.fss.or.kr/finlifeapi"),
    ("T13", "국가법령정보 DRF Open API", "코드 속 URL law.go.kr"),
    ("T14", "fastapi", "requirements.txt / pyproject 직접 의존성"),
    ("T15", "pydantic", "pyproject 직접 의존성"),
    ("T16", "uvicorn", "requirements.txt 직접 의존성"),
    ("T17", "jinja2", "requirements.txt 직접 의존성"),
    ("T18", "boto3", "pyproject 직접 의존성"),
    ("T19", "anthropic", "pyproject 직접 의존성 (Python 패키지)"),
    ("T20", "openai", "pyproject 직접 의존성 (Python 패키지)"),
    ("T21", "langfuse", "pyproject 직접 의존성 (Python 패키지)"),
    ("T22", "google-genai", "pyproject 직접 의존성"),
    ("T23", "typesafe-sdk", "pyproject 직접 의존성"),
    ("T24", "kiwipiepy", "pyproject 직접 의존성"),
    ("T25", "pdfplumber", "requirements.txt 직접 의존성"),
    ("T26", "torch", "코드 import (scripts/)"),
    ("T27", "transformers", "코드 import (scripts/)"),
    ("T28", "pytest", "pyproject dev 그룹"),
    ("T29", "ruff", "pyproject dev 그룹"),
    ("T30", "k6", "tools/k6/*.js 부하 시험 스크립트 (외부 도구)"),
    ("T31", "python-dotenv", "pyproject 직접 의존성"),
    ("T32", "tzdata", "pyproject 직접 의존성"),
    ("T33", "requests", "코드 import"),
    ("T34", "httpx", "pyproject 직접 의존성"),
]

PROMPT = """# 기술 등급 분류

아래 정의로 `items.json`의 각 기술에 등급을 매겨라. **파일을 더 찾거나 웹을 조회하지 말고**, 이 문서와 `items.json`만 보고 답한다.

## 등급 정의

| 등급 | 이름 | 무엇 |
|---|---|---|
| L0 | 언어 런타임 | 인터프리터·언어 버전 |
| L1 | 실행 기반 | 베이스 이미지 · 서빙 엔진 · GPU 스택 |
| L2 | 모델 (호스팅·로컬) | 외부 API로 부르는 모델, 로컬에서 서빙하는 모델 |
| L3 | 호스팅 서비스 | 모델이 아닌 외부 서비스·공공 API |
| L4 | 앱 프레임워크 | 앱의 뼈대 — 바꾸면 코드 전반이 바뀜 |
| L5 | 공급자 SDK | L2·L3을 부르는 클라이언트 |
| L6 | 도메인 라이브러리 | 특정 기능 하나를 맡음 |
| L7 | 개발·테스트 도구 | 운영에 안 들어감 |
| L8 | 유틸 | 작고 대체 쉬움 |

추가 규칙:
- 등급은 **기술의 종류**로 매긴다. 운영에서 쓰는지 개발에서만 쓰는지는 등급이 아니다 (별도로 처리한다)
- 언어(TypeScript·Java 등) → L0 · DB(PostgreSQL 등) → L1 · CI(GitHub Actions)·빌드 도구·타입 정의 → L7
- 한 기술이 정말로 두 등급에 걸치면 두 등급을 다 적어도 된다
- 정의로 판단이 안 서면 억지로 고르지 말고 `"unsure": true`와 이유를 적는다

## 출력

JSON 배열 하나만 출력한다. 다른 문장은 쓰지 않는다.

```json
[{"id": "T01", "tiers": ["L0"], "unsure": false, "reason": "한 문장"}]
```
`unsure`가 true면 `tiers`는 빈 배열이어도 된다.
"""


def main():
    d = os.path.abspath(sys.argv[1])
    os.makedirs(d, exist_ok=True)
    items = [{"id": i, "technology": t, "source": s} for i, t, s in ITEMS]
    random.Random(20260930).shuffle(items)  # 등급 순서대로 주지 않는다 — 순서 고정
    open(os.path.join(d, "prompt.md"), "w", encoding="utf-8", newline="\n").write(PROMPT)
    json.dump(items, open(os.path.join(d, "items.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(d, len(items))


if __name__ == "__main__":
    main()
