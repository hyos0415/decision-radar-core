# 데스크톱 앱 설계 (M8 인터페이스 · E6)

> 초안 v0 · 2026-10-06 · 상위 문서: `overview.md` (§9 MoSCoW 변경 · Q6 · M8, 에픽 E6 HYO-49) · Linear HYO-50(프레임워크 결정) · ADR adr/0011
> 관련: `hitl-state.md`(Inbox가 받는 답 — 승인·edit·거부·detect 검토·재개 때 바뀐 점) · `comparison-protocol.md` §7(실사용 지표·교란)
> **이 영역은 학습 모드가 아니다 (사용자, 2026-10-06)**: 프론트엔드는 깊이 공부하지 않고 「굴러가게만」 한다. 구현은 맡기고, 사용자 결정은 범위·우선순위만.

## 1. 왜 데스크톱인가 (Won't → Must 변경 근거)

overview §9는 desktop 앱을 Won't에 뒀다. 2026-10-06 사용자가 바꿨다 — 「재미있어서」가 아니라 **필요한 것에서 고른 스택**이고, 실측은 아직 없다(가설). 재판정 c3(「Won't 항목이 재미있어서 착수됐다」)에 걸리지 않게 근거를 남긴다.

| 필요 (사용자) | 웹·PWA | Electron |
|---|---|---|
| 로컬 CLI 백그라운드 실행 (git push · `radar.py` · Python 백엔드 · `claude`/`codex`) | 화면은 못 함 — 따로 띄운 백엔드만 | 앱이 자식 프로세스로 띄우고 관리 |
| OS 네이티브 알림 | 브라우저·서비스 워커에 묶임 | 알림 센터 직접, 버튼 |
| 항상 떠 있는 창 (트레이) | ✗ | ✓ |
| 대화 붙여넣기 (전역 단축키 → 빠른 capture) | 전역 단축키 불가 | ✓ |

**검증**: 실사용(11/2~) 첫 2주 일지에 트레이·전역 단축키·네이티브 알림을 실제로 쓰는지 남긴다. 안 쓰면 웹(PWA)으로 줄인다 (adr/0011 재검토 조건).

## 2. 구조

```
Electron main (TypeScript)  ── 트레이 · 전역 단축키(빠른 capture) · 네이티브 알림 · 창
   │ 자식 프로세스로 띄우고 끈다 (레포 .venv의 Python — PyInstaller로 묶지 않는다)
   ▼
Python 백엔드 FastAPI (localhost)  ── product/radar의 hitl·review·resume·capture를 함수로 부른다
   │                                  git·radar.py 같은 CLI도 여기서 (백그라운드)
   ▼
Renderer: React + TypeScript (Vite, electron-vite)  ── FastAPI JSON API만 부른다
```

- 백엔드가 Python인 이유: 제품 코어(LangGraph)와 `radar.py`가 Python이다. TypeScript는 Electron 껍데기와 화면만
- 화면은 백엔드 API만 부르므로 브라우저로도 열린다 — 이식성을 남긴다
- 정기 실행(revisit·detect 매주)은 앱이 떠 있으면 앱이 백엔드에 시킨다. 수집기는 지금처럼 OS 스케줄러(`collector/schedule_windows.ps1`, 나중에 M1 launchd)
- 키는 지금처럼 레포 `.env` — 백엔드만 읽고 화면·main에는 넘기지 않는다
- M1 맥 단독으로도 돌아야 한다 (overview §8) — 두 PC 모두 소스에서 `npm start`

## 3. MVP 범위 (11/2 실사용 시작까지)

| | 무엇 |
|---|---|
| **Must** | 트레이 상주 · **Inbox** — 대기 항목 승인·edit·거부, detect 초안 검토, 재개 때 「바뀐 점」 표시, 대기 일수 · **빠른 capture 창** — 전역 단축키 → 대화 붙여넣기 → capture 흐름 → 승인 · 새 항목 **네이티브 알림** · **Decisions** 목록(읽기 — ADR·status·재검토 상태) · 행동 이벤트 로깅(실사용 지표, 비교 프로토콜 §7: 알림 대비 행동·후보 대비 검토·무시) |
| **Won't (지금)** | 설치 파일 패키징·코드 서명·자동 업데이트 · 여러 사용자·인증 · 앱 안 터미널 창(CLI는 백그라운드 실행이면 된다 — 사용자) · 화면에서 status 바꾸기(R8 — status는 ADR 파일에서 사람이) |

## 3-1. 화면 설계 — 정할 것 4건 결정 (2026-10-08, 프론트 담당과)

원본: 프론트 담당과의 결정 기록(`2026-10-08-desktop-screen-design.md`)을 근거로 옮겼다. 백엔드 API는 HYO-85(②·작업 API)·HYO-92(①·③)에서 확정했다 — 칸의 정확한 모양은 `desktop/openapi.json`.

| # | 질문 | 결정 | 백엔드(API) | 프론트(UI) |
|---|---|---|---|---|
| 1 | capture 창은 기다리지 않고 「접수됨」만 → 초안은 Inbox로 | **채택** | 작업 API(`POST /jobs/capture` → `GET /jobs`) + Inbox에 `kind: pending` 행(queued · running · error · linked · no_draft). 초안이 나오면 그 행은 빠지고 capture 항목이 잇는다. 초안 없이 끝난 것은 「확인함」(`POST /inbox/{id}/ack`)으로 치운다 | Inbox에 「접수된 항목」 표시 |
| 2 | detect 항목 맨 위에 「왜 왔나」 한 줄 (관문 이유) | **채택** | Inbox 항목 `why` (관문 kept_by, 예 `anthropic L5 × deprecation`) | 목록 항목에 선정 이유 |
| 3 | revisit 충족 알림을 Inbox에 넣을지, Decisions에서 표시만 할지 | **Inbox에 넣는다** | revisit 실행의 충족 알림 → Inbox `kind: revisit` 행(`adr` · 조건 문장 `message`). 「확인함」은 Inbox에서 치울 뿐 ADR 파일·status를 바꾸지 않는다(R8). 안 치운 같은 (ADR, 조건)은 다시 만들지 않는다. 기록은 앱 데이터(`alerts.json`)에만 | 「ADR 열기 · 확인함」 — 열기는 `/decisions/{adr}` |
| 4 | 실행 화면을 사이드바에서 내릴지 | **보류** — 주말 Figma Make 시안 뒤 결정 | — | Figma Make로 실행 버튼 위치·UI 시안 |

## 4. 비교 공정성

스킬은 GitHub 알림(issue·PR 메일), 제품은 OS 네이티브 알림이다. 실사용에서 제품이 이겨도 「한곳에 모여서」(G4)인지 「알림이 더 세서」인지 섞인다 — 막지 않고 **판정문에 교란으로 적는다** (비교 프로토콜 §7 교란 표).

## 5. 열린 질문

| # | 질문 | 누가 |
|---|---|---|
| D1 | Python 백엔드 API 모양 (Inbox 항목·답·이벤트) — `hitl`·`review`의 함수 경계 그대로 노출 | 구현 |
| D2 | 앱이 꺼져 있을 때 정기 실행 (OS 스케줄러로 백엔드만 띄울지) | 구현 중 |
| D3 | 패키징(설치 파일) — 실사용 2주 뒤 필요하면 Should로 | 사용자 |
