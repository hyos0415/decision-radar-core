# desktop — Decision Radar 데스크톱 앱 (E6, HYO-49)

설계: `docs/design/desktop-app.md` · 결정: ADR adr/0011, Linear HYO-50 · Inbox HYO-51 · Decisions HYO-52.
**프론트엔드는 학습 모드가 아니다** — 굴러가게만 한다.

```
Electron main (src/main)      트레이 · 창 · 백엔드 자식 프로세스 기동/종료 · 헬스 체크
  │  <레포 .venv python> desktop/backend/server.py --port P --data-dir D
  ▼
FastAPI 백엔드 (backend/)     product/radar의 capture_turn2·hitl을 함수로 부른다 — 코어 동작은 바꾸지 않는다
  ▲  fetch http://127.0.0.1:P (JSON만)
Renderer (src/renderer)       React + TS + Tailwind + shadcn/ui(neutral) · Geist
```

## 실행

```bash
cd desktop
npm install
npm run dev          # Electron 창 + 트레이 + 백엔드 자동 기동. 처음엔 개발 데이터를 만든다 (몇 초)
```

- Python: `RADAR_PYTHON` → `<레포>/.venv` → 메인 worktree의 `.venv` 순으로 찾는다
- 개발 데이터: `desktop/.dev-data/` (gitignore) — eval 픽스처(ADR 5개·대상 repo 2개) 사본 + 스텁 capture 대기 3건. 다시 만들기: `<python> desktop/backend/devdata.py --reset`
- 백엔드만: `<python> desktop/backend/server.py --port 8765`
- 회귀: `<python> desktop/backend/test_backend.py` (스텁, 임시 폴더, 네트워크 없음) · 화면은 `npm run build`(타입 검사 포함)
- 환경 변수: `RADAR_PYTHON` · `RADAR_APP_DATA`(데이터 폴더) · `RADAR_BACKEND_URL`(이미 떠 있는 백엔드에 붙고 띄우지 않음) · `RADAR_SCREENSHOT=<png>`(백엔드 준비 뒤 화면을 저장하고 종료 — 확인용, `RADAR_SCREENSHOT_DELAY_MS`)
- 창을 닫으면 트레이로 숨는다. 끝내기는 트레이 메뉴 「종료」 — 백엔드 자식 프로세스도 같이 끈다(Windows는 `taskkill /T`)
- 백엔드는 localhost 출처만 CORS로 허용한다(개발 화면 `localhost:5173`이 다른 포트라서). `npm start`(빌드본, file://)는 아직 확인하지 않았다

## 안전

| 무엇 | 어떻게 |
|---|---|
| 실제 모델(과금) | 스텁만. `RADAR_APP_MODEL`이 stub이 아니면 백엔드가 시작을 거부한다 — 실제 경로는 호출 수·비용을 말하고 승인받은 뒤 연다 |
| 내보내기(git push) | 백엔드가 `RADAR_EXPORT`를 물려받지 않고 `RADAR_DRY_RUN=1`을 강제한다 — `hitl.exporter_for`가 둘 중 하나만으로도 no-op. Electron main도 자식 환경에서 `RADAR_EXPORT`를 지운다 |
| 쓰는 곳 | 개발 데이터 폴더의 ADR 사본뿐. 실제 ADR repo는 건드리지 않는다 |
| 키 | 백엔드만 `.env`를 읽는다(스텁 모드는 읽지도 않는다). main·화면에는 넘기지 않는다 |
| status | 화면에 status 칸이 없다. edit에 status·번호 칸이 오면 코어(`hitl.check_edit`)가 거부한다 (R8) |

## 백엔드 API (D1)

| | 무엇 | 감싸는 코어 |
|---|---|---|
| `GET /health` | `{ok, mode, data_dir, adr_dir}` | — |
| `GET /inbox` | 대기 항목 — capture(승인 대기 thread) + 검토 대기 별도 파일. 오래된 순, `waiting_days`·`state`(awaiting_approval · stale · review …)·`actions` | `StateStore.threads` + checkpoint `get_state` · `hitl.review_queue` |
| `GET /inbox/{id}` | + `draft` · `markdown`(저장될 ADR 미리보기) · `changes`(대기 중 바뀐 점) | `ops.render` · 지문 `compute`·`same`·`diff` (읽기만) |
| `POST /inbox/{id}/approve` `{stale_answer?}` | → `{outcome, message, adr?, path?, changes?, answers?, errors?}` | `capture_turn2(approve)` |
| `POST /inbox/{id}/edit` `{draft, stale_answer?}` | 고친 초안. 검사 실패면 `edit_errors` — 쓰지 않는다 | `capture_turn2(edit)` → `hitl.check_edit` |
| `POST /inbox/{id}/reject` | | `capture_turn2(reject)` |
| `POST /events` `{event, item?, detail?}` | 화면 행동 로그 (`app_opened` · `item_opened` · `notification_shown` · `notification_clicked` · `capture_opened`) | — |

`outcome`: `written` · `rejected` · `stale`(바뀐 점을 보이고 계속·다시 판정·버림을 `stale_answer`로 다시 보낸다) · `edit_errors` · `discarded` · `rejudge`(원래 대화로 capture 1턴을 다시 돌려 새 대기 항목) · `blocked` · `recovered` · `error`.
detect 검토 항목은 목록·상세만 — 답하려면 내보내기가 켜져 있어야 한다(`hitl.review_item`).

## 행동 로그 (실사용 지표 — 비교 프로토콜 §7)

`<data>/events.jsonl`, 한 줄에 하나:

```json
{"at": "2026-10-06T16:36:51+09:00", "event": "approve", "item": "2026-10-06-capture-e4598395", "source": "backend", "mode": "stub", "detail": {"outcome": "written", "adr": "0006"}}
```

- `source: backend` — `approve` · `edit` · `reject`(detail에 outcome·stale_answer·adr·errors) · `item_created`
- `source: app` — 화면이 `POST /events`로 보낸 것
- **무시**는 기록하지 않고 계산한다: 알림(`notification_shown`)·항목 생성 뒤 답이 없는 항목
