---
status: adopted
date: 2026-08-10
technology: zustand
use: 클라이언트 상태 관리
affected:
  - {repo: Andbread_Frontend, component: web-app}
evidence:
  - {url: "repo:Andbread_Frontend/src/stores/useNbreadStore.tsx", source_type: repo}
revisit_when: []
---
# (예시) 상태 관리: zustand 채택 (Redux Toolkit 대비)

## Context and Problem Statement
인증 정보·엔빵 생성 폼·알림 개수처럼 화면을 가로지르는 클라이언트 상태가 몇 개 생겼다. 전역 상태 라이브러리가 필요한데 store마다 보일러플레이트가 적어야 한다.

## Considered Options
- zustand — store 하나가 파일 하나, Provider 없이 hook으로 읽는다
- Redux Toolkit — slice·store 설정·Provider 배선이 필요하다. 상태 흐름 추적 도구는 더 낫다

## Decision Outcome
채택: zustand. 상태가 작고 서버 상태는 Supabase 조회로 두기 때문에 Redux의 추적 도구가 주는 이득보다 배선 비용이 컸다.

> 예시 — Decision Radar 개발 데이터용으로 레포 주인이 지어 쓴 ADR이다. Andbread 팀의 실제 결정이 아니다.
