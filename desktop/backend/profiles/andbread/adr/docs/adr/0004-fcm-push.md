---
status: adopted
date: 2026-08-20
technology: Firebase Cloud Messaging
use: 웹 푸시 알림 발송
affected:
  - {repo: Andbread_Frontend, component: notifications}
evidence:
  - {url: "repo:Andbread_Frontend/src/utils/firebase/getFCMDeviceToken.ts", source_type: repo}
revisit_when: []
---
# (예시) 푸시 알림: FCM 토큰 + Supabase Edge Function 발송

## Context and Problem Statement
친구 요청·채팅·정산 알림을 브라우저와 설치형 웹앱(PWA)에 보내야 한다. 기기마다 푸시 구독 방식이 달라 직접 구현하면 분기가 많다.

## Considered Options
- FCM — 브라우저에서 토큰을 받아 `fcm_token` 테이블에 저장하고, Edge Function이 토큰으로 발송한다
- web-push(VAPID) 직접 구현 — 의존성은 가볍지만 iOS PWA 등 구독 경로를 직접 챙겨야 한다

## Decision Outcome
채택: FCM. 토큰 저장은 Supabase, 발송은 Edge Function이 맡는다.

> 예시 — Decision Radar 개발 데이터용으로 레포 주인이 지어 쓴 ADR이다. Andbread 팀의 실제 결정이 아니다.
