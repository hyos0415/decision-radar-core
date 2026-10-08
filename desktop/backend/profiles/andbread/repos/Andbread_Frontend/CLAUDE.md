> 예시 — Decision Radar 개발 데이터용으로 레포 주인이 작성. Andbread 팀의 실제 문서가 아님

# Andbread_Frontend (예시 사본)

구독 공유 관리 서비스 **엔빵**의 프론트엔드. 이 파일은 레이더가 읽는 「스택」 절만 둔다.

## 스택 · 평가셋 · 알려진 병목

기술 결정 기록의 `jhs-decisions` 스킬이 이 절을 읽고, 이 스택에 영향을 주는 새 릴리스·모델만 골라낸다. 의존성이나 모델을 바꾸면 이 절도 같이 고친다.

### 의존성 → component

| 의존성 | component |
|---|---|
| Next.js 15 (App Router) · React 19 · TypeScript (npm, `package.json`) | web-app (`src/app` · `src/components`) |
| zustand · react-hook-form · react-toastify (npm) | web-app (상태·폼·토스트, `src/stores`) |
| Tailwind CSS 3 · tailwindcss-preset-px-to-rem (npm) | web-app (스타일, `tailwind.config.ts`) |
| @supabase/supabase-js (npm) · Supabase Edge Functions · Postgres 마이그레이션 | supabase (`supabase/`) |
| firebase (FCM) · web-push · google-auth-library (npm) | notifications (`src/utils/firebase` · `supabase/functions/*-notification`) |
| @sentry/nextjs (npm) | web-app (오류 모니터링, `src/sentry.*.config.ts`) |
| Playwright · vitest (npm) | e2e (`e2e/` · 단위 테스트) |

### 외부 모델·서비스

| 모델·서비스 | component |
|---|---|
| Supabase (Auth · Postgres · Realtime · Edge Functions) | supabase |
| Firebase Cloud Messaging | notifications |
| Sentry | web-app |
