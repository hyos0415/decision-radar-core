# Mac 연습장 점검 결과 — 2026-10-26 (HYO-99 3단계)

런북: `docs/runbooks/mac-smoke.md`. M1 Mac 세션이 0~8단계를 돌렸다 (6단계 실사용 화면은 선택이라 건너뜀). Windows 세션에 넘기는 보고다.

## 요약

- **실사용 모드가 진짜 Mac에서 처음으로 끝까지 지나갔다** — 구독 `claude -p` revisit · detect, API 그림자, 연습장 원격으로만 push. 원본 `adr`은 무변경
- **HYO-77**(데스크톱: macOS(M1)에서 전부 눌러 보기 — 단축키 권한 · 트레이 · 알림 · 창) 전 항목 통과
- **HYO-78**(데스크톱: 빌드본(npm start) 확인 — file:// 출처 CORS) Mac에서도 GET·POST 정상 — 정정한 런북대로 막히지 않는다
- 버그(막힘)는 없다. 다듬을 곳 5개와 확인할 점 2개가 아래에 있다

## 보고 표

| 단계 | 결과 | 메모 |
|---|---|---|
| 0 pull · 대상 repo | ⚠️ | jhs-decisions `5065d95` · adr `8c0c3fb`. **finance_verifier · medical-mate-ai(`../medical-mate-ai-radar`) · fineprint가 Mac에 없다** (decision-radar만 있음) |
| 1 준비 · 키 · claude 로그인 | ✅ | Python 3.12.12 · node 22.23.3 · ANTHROPIC 키 있음 · claude 2.1.310 · `authMethod: claude.ai` · `subscriptionType: team` |
| 2 오프라인 회귀 | ✅ | test_backend 실패 0 · test_engines·test_export all passed · test_weekly 19/19 · lint·build 통과 |
| 3 백엔드 시작 · health | ✅ | 첫 줄 `실사용 모드 — ADR repo …/adr-smoke …` · `mode: real` · `engine: claude-cli` · `shadow: true` · `api_key: true` · `week: 2026-W43` |
| 4 /engine · 로그인 창 | ✅ | `ready: true` · `reason: null` · `POST /engine/login` → `opened: true`, Terminal 창 뜸 (사용자 확인) |
| 5 revisit | ✅ | ok · 62초 · `model_calls` 15 (구독) · `cost_usd_equiv` $0.18 · ADR 12개 · 조건 25개 · 충족 0 · 지난번과 달라짐 12 · 알림 0 · 기록 커밋 `f1c4f9c` (ADR 8개) |
| 5 그림자 | ✅ ⚠️ | `shadow api True` · `cost_usd` **$0.565** · 판정 같음 **25/25**. 아래 확인할 점 1 |
| 5 git | ✅ | `adr-smoke.git` main 맨 위 = `f1c4f9c radar revisit …` (`docs/adr/*.md` 8개만) · 원본 `adr` HEAD `8c0c3fb` 그대로 · `status` 비어 있음 |
| 5b detect | ✅ | 입력 2026-W43 (feed 5) · ok · 11초 · 소식 5 → 후보 0 · 제외 5 · 새 초안 없음 · 구독 1세션 ($0.06 환산) · 그림자 후보 0 · 초안 0 ($0.039) · push 0 |
| 6 실사용 화면 | 건너뜀 | 선택 |
| 7 정리 | ✅ | 연습장·데이터 폴더 지움 · 남은 프로세스 0 |
| 8 HYO-77 Inbox | ✅ | capture 승인 · 고쳐서 승인 · 거부(확인 창) · detect 초안 승인 · 키보드 A · E · R · 1 · stale 「기다리는 동안 바뀐 점」 → 그대로 저장 |
| 8 HYO-77 Decisions | ✅ | 목록 10건 · 상태 필터 · 상세(재검토 조건별 충족/미충족/판단 못 함). **화제 탭은 렌더러에 아직 없다** — 백엔드 `/topics`만 있음 (Mac 문제 아님) |
| 8 HYO-77 빠른 capture | ✅ | `Cmd+Shift+Space` 등록 성공 · 다른 앱 위에서 창이 뜸 (사용자 확인) · 창은 alwaysOnTop · 제출하면 백엔드 응답 |
| 8 HYO-77 알림 | ✅ | 새 Inbox 항목이 생기면 macOS 배너가 뜨고 클릭하면 해당 Inbox 항목으로 간다 (사용자 확인). 새 항목이 없는 실행은 알림 없음 — 정상 |
| 8 HYO-77 트레이 · 창 | ✅ | 메뉴바 아이콘 보임 (사용자 확인) · 창 닫기 → 숨김, 앱·백엔드 상주 · 종료 → `server.py`·uvicorn 남지 않음 |
| 8 HYO-78 빌드본 | ✅ | `file://…/out/renderer/index.html`에서 Inbox GET · 승인 POST 모두 정상 · 종료 뒤 남은 프로세스 0 |

## 다듬을 곳 (하위 이슈 후보 — Windows 세션에서 만든다)

1. **토스트가 처리 버튼을 가린다** — 「ADR 0010 저장했어요」 같은 토스트가 몇 초 동안 상세 하단의 승인·고쳐서 승인·거부 줄과 「고쳐서 승인」 대화상자의 버튼 위에 겹친다. 연달아 처리할 때 걸린다
2. **지난 알림 목록에 `inbox_new`가 원시 키로 보인다** — 10/11에 쌓인 예전 기록(`kind: inbox_new`, title·body 없음). 예전 모양의 기록을 읽을 때의 표시 문구가 필요하다
3. **거부한 detect 초안이 다음 detect에 되살아난다** — vllm 0.9.0 초안을 거부(「ADR 0007 초안을 지웠어요」)한 뒤 스텁 detect를 다시 돌리자 같은 초안이 새 Inbox 항목으로 돌아왔다. 스텁이 같은 소식을 다시 넣어서일 수 있지만, 실사용 모드에서도 거부 기록이 다음 detect의 중복 판정에 들어가는지 확인이 필요하다
4. **Inbox의 revisit 항목에 「왜 왔나」가 없다** — 「재검토 조건 충족 — Jev…」을 열면 ADR 본문만 보이고 어느 조건이 충족됐는지·처리 버튼이 없다. 같은 정보는 Decisions 상세에 있다
5. **대기 표시가 바뀌었다** — HanNER-pii 초안이 처음 「15일째 대기」였는데 다른 항목을 처리한 뒤 다시 열자 「오늘 도착」으로 보였다

## 확인할 점

1. **그림자 비용이 런북 예상보다 크다** — revisit 그림자가 API 호출 90회(주 실행 15회), $0.565. 런북은 「몇 센트」로 적었다. 판정은 25/25로 같으니 결과 문제는 아니고, 그림자 경로가 조건마다 여러 번 부르는 구조인지 보면 된다. 주 1회면 월 약 $2.3
   - **확인 (10/27, Windows)**: 구조 문제가 아니라 세는 단위가 다르다 — 구독 CLI는 세션마다 1(`engines._record`), API는 턴마다 1(`llm_log.on_llm_end`)이고 API 비용은 턴마다 입력 전체를 정가로 센다. 조건 25개 × 평균 3.6턴 ≈ 90. 런북 5단계 문구를 「회당 약 $0.5」로 고쳤다
2. **대상 repo 3개가 Mac에 없다** — 실사용 기기가 Mac이면 finance_verifier · medical-mate-ai-radar · fineprint를 형제 폴더로 받아야 revisit이 그 repo 파일을 읽는다

## 점검 방법 메모

- 이 세션에는 Computer Use가 없어서 HYO-77·78 화면 조작은 Playwright `_electron`(빌드본 `out/`)으로 했다. 메뉴바·OS 알림 배너·전역 단축키처럼 앱 밖에서 일어나는 일은 터미널 앱에 화면 기록·손쉬운 사용 권한이 없어 사용자가 직접 봤다
- 그림자 기록 원본(`shadow.jsonl`)은 커밋하지 않았다 — 세션 scratchpad에만 있다
