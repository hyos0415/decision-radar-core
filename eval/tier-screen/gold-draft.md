# 정답지 — 기술 등급 (정책 표 예시)

> **변경 (2026-09-30)**: 첫 초안은 정책 표 **밖**(medical-mate-backend Java/Spring·web3 React·CI)에서 뽑았다. 사용자가 그 스택의 정답을 판정할 수 없어서, 정답지가 사실상 Claude가 쓴 답이 됐다 — 폐기.
> 대신 **정책 표(`docs/design/importance-policy.md` §2-1) 예시를 정답지로 쓰고, 모델에게는 예시 열을 뺀 정의만 준다.** 라벨은 사용자가 이미 받아들인 정책 표 그대로다 (2026-09-30 「등급도 맞아」).
> 이 파일은 시험 받는 모델이 보는 디렉터리에 넣지 않는다.

| id | 기술 | 정답 | 비고 |
|---|---|---|---|
| T01 | Python 3.12 | L0 | |
| T02 | `python:3.12-slim` (Docker 베이스 이미지) | L1 | |
| T03 | `vllm/vllm-openai` (Docker 이미지, 서빙 엔진) | L1 | |
| T04 | llama.cpp `llama-server` | L1 | |
| T05 | CUDA | L1 | |
| T06 | AWS Bedrock `apac.amazon.nova-pro-v1:0` | L2 | |
| T07 | Anthropic `claude-sonnet-5` | L2 | |
| T08 | OpenAI `gpt-5.6-terra` | L2 | |
| T09 | TypeSafe Jev `jev-1.13.0` | L2 | |
| T10 | Langfuse Cloud | L3 | 서비스. SDK(T21)와 별개 단위 |
| T11 | Amazon Bedrock Guardrails | L3 | |
| T12 | 금융감독원 Finlife Open API | L3 | |
| T13 | 국가법령정보 DRF Open API | L3 | |
| T14 | fastapi | L4 | |
| T15 | pydantic | L4 | |
| T16 | uvicorn | L4 | |
| T17 | jinja2 | L4 | |
| T18 | boto3 | L5 | |
| T19 | anthropic (Python SDK) | L5 | |
| T20 | openai (Python SDK) | L5 | |
| T21 | langfuse (Python SDK) | L5 | 서비스(T10)와 별개 단위 |
| T22 | google-genai | L5 | |
| T23 | typesafe-sdk | L5 | |
| T24 | kiwipiepy | L6 | |
| T25 | pdfplumber | L6 | |
| T26 | torch | L6 | 표에는 torch·transformers 한 칸 |
| T27 | transformers | L6 | |
| T28 | pytest | L7 | |
| T29 | ruff | L7 | |
| T30 | k6 | L7 | |
| T31 | python-dotenv | L8 | |
| T32 | tzdata | L8 | |
| T33 | requests | L8 | |
| T34 | httpx | L8 | |

집계: **34개**, 전부 채점. 경계 항목 없음 — 라벨이 정책 자체다.

## 알고 볼 점

- 라벨은 처음에 Claude가 채운 초안을 사용자가 받아들인 것이다. 정책을 정의하는 표라서 정답의 근거로 쓸 수 있다 — 다만 "Claude 초안과 비슷한 판단을 하는 모델"이 유리할 수 있다
- 예시 없이 정의만 주므로 few-shot보다 어렵다. 실제 파이프라인은 예시를 줄 것이라 이 점수는 **하한**에 가깝다
