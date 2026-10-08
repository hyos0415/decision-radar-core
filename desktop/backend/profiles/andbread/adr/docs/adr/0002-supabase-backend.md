---
status: adopted
date: 2026-08-10
technology: Supabase
use: 백엔드(Auth · Postgres · Edge Functions)
affected:
  - {repo: Andbread_Frontend, component: supabase}
evidence:
  - {url: "repo:Andbread_Frontend/supabase/config.toml", source_type: repo}
revisit_when: []
---
# (예시) 백엔드: Supabase 채택

## Context and Problem Statement
프론트엔드 팀이 서버를 따로 두지 않고 인증·DB·알림 발송 로직까지 맡아야 한다. 구독 공유 서비스라 친구·정산 같은 관계형 데이터가 많다.

## Considered Options
- Supabase — Postgres, Auth, Edge Functions, 마이그레이션을 한 곳에서 쓴다
- Firebase(Firestore) — 문서형 DB라 정산·친구 관계 조회가 어색하다
- 자체 백엔드 서버 — 운영 부담이 팀 규모에 비해 크다

## Decision Outcome
채택: Supabase. 알림 발송은 Edge Function이 맡고 DB 변경을 트리거로 받는다.

> 예시 — Decision Radar 개발 데이터용으로 레포 주인이 지어 쓴 ADR이다. Andbread 팀의 실제 결정이 아니다.
