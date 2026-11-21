---
status: adopted
date: '2026-11-16'
technology: fastapi
use: 의존성 릴리스 0.162.6 대응 (breaking)
affected:
- repo: decision-radar
  component: desktop
- repo: fineprint
  component: web
- repo: medical-mate-ai
  component: api
evidence:
- url: https://github.com/fastapi/fastapi/releases/tag/0.162.6
  source_type: release_note
---
# FastAPI 0.162.6 대응: Python 3.10 지원 중단

> 2026-11-21 채택 — 사용자 결정. decision-radar는 0.142.2 → 0.162.6으로 올렸다: 앱 「실험해 보기」(HYO-116)가 격리 사본에서 `desktop/backend/test_backend.py` · `export_openapi.py --check`를 올리기 전 · 뒤 모두 통과시켰고(draft PR decision-radar#356), merge 뒤 실행 환경에도 설치했다. medical-mate-ai는 `fastapi>=0.115` 범위 고정에 Python 3.12(`requires-python >=3.12`, Dockerfile `python:3.12-slim`)라 3.10 지원 중단의 영향이 없다 — 따로 할 일 없음. fineprint는 2026-11-20 감시 대상에서 뺐다.

## Context and Problem Statement
fastapi/fastapi 0.162.6 릴리스 노트에 Breaking Change로 "Drop support for Python 3.10"(PR #16737)이 적혀 있다. 영향 후보는 decision-radar(desktop), fineprint(web), medical-mate-ai(api)이다. 각 repo의 Python 런타임 버전은 소식만으로는 알 수 없다. 기존 ADR 0013(adopted)은 0.143.0 breaking 대응이었다.

## Decision Outcome
제안: 영향 repo 세 곳의 Python 버전(로컬, CI, 배포 이미지)을 확인한다. 3.10을 쓰는 repo는 Python을 업그레이드한 뒤 FastAPI 0.162.6으로 올린다. 업그레이드가 어려우면 fastapi 버전을 이전 릴리스로 고정(pin)한다. 채택 여부와 방식은 사람이 정한다.

관련 결정: 0013

## Research Note
- 재검토 조건 없음: 대응 작업
- 릴리스 노트는 3.10 지원 중단만 명시한다. 지원하는 최소 Python 버전은 PR #16737과 패키지 메타데이터(requires-python)에서 확인이 필요하다.
- ADR 0013(0.143.0 대응)과 use가 달라 서로 막지 않는다.
