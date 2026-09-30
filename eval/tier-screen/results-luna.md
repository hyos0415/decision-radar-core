# tier-screen 결과 — GPT-6 Luna (2026-09-30)

- 실행: `codex exec -m gpt-6-luna -s read-only` (codex-cli 0.156.0, reasoning effort medium) · k=1 · 11,509 토큰(구독)
- 입력: `make_run.py`가 만든 빈 디렉터리(prompt.md + items.json). 로그상 두 파일만 읽음 — 정답지 접근 없음
- 원자료: `eval/runs/20260930-tier-screen-luna/` (gitignore)

## 판정 — **통과** (27/34, 기준 ≥ 27 · 보류 1/34 = 3% ≤ 30%)

경계선 바로 위다. 등록한 규칙상 재실행 구간(25~26)은 아니다. Haiku는 규칙대로 돌리지 않았다.

| | 수 |
|---|---|
| 정답 | 27 |
| 오답 | 6 |
| 보류 (분류 불가) | 1 — T23 typesafe-sdk ("이름상 SDK로 보이지만 용도가 불명확") |

## 오답 6개 — 전부 L4 · L6 · L8 경계

| id | 기술 | 정답 | Luna | 방향 | Luna 이유 |
|---|---|---|---|---|---|
| T15 | pydantic | L4 | L6 | 과소 (→ 놓침) | 데이터 검증 기능을 맡는 라이브러리 |
| T16 | uvicorn | L4 | L6 | 과소 | ASGI 서버 기능을 제공하는 라이브러리 |
| T17 | jinja2 | L4 | L6 | 과소 | 템플릿 렌더링 기능 |
| T26 | torch | L6 | L1 | 과대 (→ 소음) | GPU 연산과 학습·추론을 지원하는 프레임워크 |
| T33 | requests | L8 | L6 | 과대 | HTTP 요청 기능 |
| T34 | httpx | L8 | L6 | 과대 | HTTP 요청 기능 |

## 읽는 법

- **오답이 모델 약점이라기보다 정의의 모호함에 몰려 있다.** L4 「뼈대 — 바꾸면 코드 전반이 바뀜」·L6 「기능 하나」·L8 「작고 대체 쉬움」은 서술형이라, Luna의 이유가 정의로는 반박되지 않는다(uvicorn은 "기능 하나"로도 읽힌다). 표 밖 초안(Java/Spring)에서 경계 6개가 모두 L4/L6에 몰렸던 것과 같은 자리다 → **정책 쪽 과제** (v2 후보: L4/L6/L8을 관찰 가능한 기준으로)
- 영향은 제한적이다: 결정표에서 L4↔L6은 「우리 버전 EOL」·「major」 두 칸, L6↔L8은 「breaking」·「deprecation」 두 칸만 다르다. torch L1은 「yanked」·「major」가 추가로 남는다
- **하한이다.** 모델에게 예시 열을 뺀 정의만 줬다. 실제 파이프라인은 예시를 준다
- 정답 라벨은 Claude 초안을 사용자가 받아들인 정책 표다 — "Claude식 판단"과 가까운 모델이 유리할 수 있다
- 하네스 교란: Codex 안의 Luna다. 제품 배정의 최종 확인은 API로 따로 (ADR `adr/0007`)
- k=1 스모크다 (CLAUDE.md 규율 5). "Luna가 충분하다"가 아니라 "선별을 통과했다"까지만 말한다

---

## 참고 실행 — Claude Haiku 4.5 (2026-09-30, 사용자 요청)

> 등록한 규칙은 "Luna 불통과 시에만 Haiku"였다. 이 실행은 **판정 규칙을 바꾸지 않는 참고 실행**이다 (사용자 요청). Luna 판정(통과)은 그대로다.

- 실행: `claude -p --model haiku --setting-sources project --tools Read` (Claude Code 2.1.284 → `claude-haiku-4-5-20251001`) · k=1 · 3턴 · 31,176 토큰(구독, API 환산 $0.09)
- 입력: 같은 `make_run.py` 산출물 (items.json 바이트 동일 확인). 세션 기록상 두 파일만 읽음
- 원자료: `eval/runs/20260930-tier-screen-haiku/`

| | Luna | Haiku |
|---|---|---|
| 정답 | 27 | **30** |
| 오답 | 6 | 4 |
| 보류 | 1 | 0 |
| 과대 등급 (→ 소음) | 3 (torch·requests·httpx) | 1 (uvicorn → L1) |
| 과소 등급 (→ 놓침) | 3 (pydantic·uvicorn·jinja2) | 3 (llama-server → L2 · pydantic · jinja2) |
| 토큰 | 11,509 | 31,176 |

**짝 비교** (같은 34항목): Luna만 틀림(보류 포함) 4개 — torch · requests · httpx · typesafe-sdk(보류) / Haiku만 틀림 1개 — llama-server. 둘 다 틀림: pydantic · jinja2 · uvicorn(서로 다른 오답).
불일치 5개 중 4:1 → McNemar 정확 이항검정 p ≈ 0.375. **이 표본으로는 두 모델을 가를 수 없다** (k=1, n=34).

읽는 법:
- 두 모델이 **같은 자리(pydantic·jinja2·uvicorn = L4 경계)**에서 틀렸다 — 모델 문제보다 정의 문제라는 해석을 보강한다
- Haiku만의 오답(llama-server → L2)은 정의로 반박된다 (L1 「서빙 엔진」). Luna만의 오답 셋은 정의가 모호한 자리다(torch는 L1 「GPU 스택」으로도 읽힌다, requests·httpx는 L6/L8 경계)
- Luna의 오답은 과대 등급(소음 쪽)이 더 많다 — 헤드라인이 소음 비율이라 불리한 방향이지만 3 대 1은 k=1에서 가를 수 없는 차이다
- 가격은 Luna가 10배 싸다 (입력 $0.10 vs $1.00 / 1M, ADR 0007)
