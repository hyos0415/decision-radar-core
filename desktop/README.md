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
- **개발 데이터 프로필 고르기** — 기본(default)은 위의 레포 주인 스택이다. `RADAR_DEVDATA=andbread`로 프론트 담당의 스택(Andbread_Frontend = 엔빵)을 고른다. 프로필마다 데이터 폴더가 따로라(`desktop/.dev-data-andbread/`, gitignore) 바꿔 띄워도 섞이지 않는다.
  - Windows (PowerShell): `$env:RADAR_DEVDATA='andbread'; npm run dev` (끝나면 `Remove-Item Env:RADAR_DEVDATA`)
  - macOS: `RADAR_DEVDATA=andbread npm run dev`
  - 다시 만들기: `<python> desktop/backend/devdata.py --profile andbread --reset` · 사이드바 「스텁」 옆에 프로필 이름이 뜬다(`/health`의 `profile`)
  - 내용은 `desktop/backend/profiles/andbread/`에 있다 — 대상 repo 사본(package.json + 예시 CLAUDE.md) · 예시 ADR 4개(제목이 `(예시)`) · Inbox 대기 3건(Supabase Realtime · Next.js 캐싱 · zod) · 실행 화면용 소식·출처. **전부 예시다** — 소식은 `[예시]`·`example.invalid`로 지어낸 것이고 Andbread 팀의 실제 문서·결정이 아니다. 진짜 W42 소식으로 바꾸는 건 별도 작업
  - 빠른 capture에 `profiles/andbread/conversations.json`의 `link-zustand`를 붙이면 「이미 같은 결정이 있다: 0001」(linked)이 나온다
  - 폴더는 만든 프로필을 `profile.txt`에 적어 둔다 — 다른 프로필로 같은 폴더를 열려 하면 백엔드가 거부한다. `RADAR_APP_DATA`(명시한 폴더)가 있으면 그 폴더가 프로필 기본 폴더보다 먼저다
- 백엔드만: `<python> desktop/backend/server.py --port 8765 [--profile andbread]`
- 회귀: `<python> desktop/backend/test_backend.py` (스텁, 임시 폴더, 네트워크 없음) · 화면은 `npm run build`(타입 검사 포함)
- 환경 변수: `RADAR_PYTHON` · `RADAR_APP_DATA`(데이터 폴더) · `RADAR_DEVDATA`(개발 데이터 프로필, 비우면 default) · `RADAR_BACKEND_URL`(이미 떠 있는 백엔드에 붙고 띄우지 않음) · `RADAR_SCREENSHOT=<png>`(백엔드 준비 뒤 화면을 저장하고 종료 — 확인용, `RADAR_SCREENSHOT_DELAY_MS`)
- 창을 닫으면 트레이로 숨는다. 끝내기는 트레이 메뉴 「종료」 — 백엔드 자식 프로세스도 같이 끈다(Windows는 `taskkill /T`)
- 백엔드는 localhost 출처만 CORS로 허용한다(개발 화면 `localhost:5173`이 다른 포트라서). `npm start`(빌드본, file://)는 아직 확인하지 않았다

## 안전

| 무엇 | 어떻게 |
|---|---|
| 실제 모델(과금) | 기본은 스텁. **데모 모드**(아래)에서만 — `RADAR_APP_MODEL=anthropic`과 `RADAR_APP_DEMO=1`을 같이 줘야 하고, 하나만 있으면 시작을 거부한다 |
| 내보내기(git push) | 없다. 백엔드가 물려받은 `RADAR_EXPORT`를 지우고 **로컬 내보내기**(`RADAR_EXPORT=local` — git을 부르지 않고 승인 기록만, `export.LocalExporter`) + `RADAR_DRY_RUN=1`로 띄운다. Electron main도 자식 환경에서 `RADAR_EXPORT`를 지운다 |
| 쓰는 곳 | 데이터 폴더의 ADR 사본뿐(스텁: `adr-fixture`, 데모: `adr` = 실제 ADR repo의 복사본). 실제 ADR repo는 읽기만 한다 |
| 키 | 백엔드만 `.env`를 읽는다(스텁 모드는 읽지도 않는다). main·화면에는 넘기지 않는다 |
| status | 화면에 status 칸이 없다. edit에 status·번호 칸이 오면 코어(`hitl.check_edit`)가 거부한다 (R8) |

## 백엔드 API (D1)

| | 무엇 | 감싸는 코어 |
|---|---|---|
| `GET /health` | `{ok, mode(stub · demo), profile, model, flows, data_dir, adr_dir, demo?}` — `profile`은 개발 데이터 프로필(`default` · `andbread`), `flows`는 이 모드에서 돌릴 수 있는 흐름(데모는 `detect`만), `demo`는 `{adr_source, adr_copy, snapshot, funnel}` | — |
| `GET /inbox` | 대기 항목 — capture(승인 대기 thread) + detect 초안(검토 대기 별도 파일). 오래된 순, `waiting_days`·`state`(awaiting_approval · stale · review …)·`actions`·`why`(detect만: 관문 판정 「왜 왔나」, 예 `anthropic L5 × deprecation` — 관문 없이 만든 입력이면 null) | `StateStore.threads` + checkpoint `get_state` · `hitl.review_queue` · `gate.json` |
| `GET /inbox/{id}` | + `draft`(detect는 초안 파일을 읽어 편집용으로) · `markdown`(저장될 ADR 미리보기) · `changes`(대기 중 바뀐 점) | `ops.render` · 지문 `compute`·`same`·`diff` (읽기만) |
| `POST /inbox/{id}/approve` `{stale_answer?}` | → `{outcome, message, adr?, path?, changes?, answers?, errors?, validate_errors?}` | capture `capture_turn2(approve)` · detect `hitl.review_item(approve)` |
| `POST /inbox/{id}/edit` `{draft, stale_answer?}` | 고친 초안. 검사 실패면 `edit_errors` — 쓰지 않는다 | capture `capture_turn2(edit)` · detect `check_edit` → `rewrite_adr`(같은 번호) → `review_item` |
| `POST /inbox/{id}/reject` | detect는 초안 파일을 지우고 닫는다 | capture `capture_turn2(reject)` · detect `hitl.review_item(reject)` |
| `POST /jobs/{capture\|detect\|revisit}` `{conversation?}` | **작업 + 폴링** (HYO-85) — 202 `{job}`으로 바로 돌아온다. 입력 오류는 4xx(작업이 되지 않음). 일꾼 하나가 접수 순서대로 돈다 | `jobs.py` → `submit_capture` · `run` |
| `GET /jobs` · `/jobs/{id}` | 상태 창 — `{id, kind, state(queued · running · done · error), created, started, finished, message(한 줄), result(아래 동기 엔드포인트와 같은 모양), error, item(capture가 만든 Inbox 항목), mode}`. 끝나면 화면이 Inbox를 다시 읽고 알림. 백엔드가 다시 뜨면 끝나지 않은 작업은 error(다시 돌리지 않음) | `<data>/jobs.json` |
| `POST /capture` `{conversation}` | 빠른 capture(동기 — 작업 API로 옮기는 동안 남겨 둔다) → `awaiting`(Inbox에 승인 대기) · `linked`(같은 결정이 이미 있음, `link_candidates`) · `no_draft` | `capture_turn1` (R9: 쓰기 0) |
| `GET /decisions` · `/decisions/{id}` | ADR 목록(번호·제목·status·technology·재검토 조건별 최근 결과·요약) · 상세(+원문). **읽기만** — status를 바꾸는 엔드포인트가 없다(R8) | `radar.py list` + 파일 제목 |
| `POST /runs/detect` · `/runs/revisit` · `GET /runs` | 흐름을 한 번 돌리고(동기) 요약(`summary`·`drafts`·`alerts`, 데모는 `usage`) — `<data>/runs.jsonl`. detect 입력이 수집 스냅숏에서 왔으면(`funnel.json`) `summary` 첫 줄이 건수(수집 → 레인 A → 관문 남김 → detect 입력). 이 모드의 `flows` 밖이면 409 | `detect` · `revisit` 그래프 (스냅숏 모드 + dry-run) |
| `GET /schedule` | 정기 실행 칸·지난 실행·차례 여부. 켜기: `RADAR_SCHEDULE=on`(기본 꺼짐) — revisit 월 09:00 · detect 화 18:00(수집 화 16:00 뒤), 앱이 꺼져 있던 동안 지난 칸은 켜질 때 한 번 따라잡는다 | `scheduling.py` |
| `POST /events` `{event, item?, detail?}` | 화면 행동 로그 (`app_opened` · `item_opened` · `notification_shown` · `notification_clicked` · `capture_opened` · `decision_opened`) | — |

`outcome`: `written` · `rejected` · `stale`(바뀐 점을 보이고 계속·다시 판정·버림을 `stale_answer`로 다시 보낸다) · `edit_errors` · `discarded` · `rejudge`(원래 대화로 capture 1턴을 다시 돌려 새 대기 항목) · `blocked` · `recovered` · `error`.
detect 초안은 Inbox에서 승인·수정·거부한다 (로컬 내보내기 — 승인은 사본에 `proposed`로 남고 별도 파일에 승인이 적힌다, git 없음). 검토 대기 초안은 아직 「세상」이 아니어서(지문이 보지 않는다) 다른 대기 항목을 낡게 하지 않는다 — 승인하면 낡게 한다.
읽기(Inbox·상세·Decisions)는 쓰기 잠금을 기다리지 않는다 — 실제 모델 실행(분 단위) 중에도 화면이 멈추지 않는다.

## 데모 모드 (HYO-85 — 실제 모델, 과금)

```bash
# 1) detect 입력 만들기 (HYO-84): 수집 스냅숏 → 관문(Sol) → radar.py 스냅숏 폴더
python -m pipeline.profile_filter run --snapshot ../radar-snapshots/2026-W41 --gate gpt-6.1-sol --tiers tiers.json --real-api --out pipeline/out/w41
python -m pipeline.detect_feed --run pipeline/out/w41 --out pipeline/out/w41/detect-snapshot
# 2) 앱(또는 백엔드만) — 환경 변수 셋이 있어야 한다
RADAR_APP_DEMO=1 RADAR_APP_MODEL=anthropic RADAR_DEMO_SNAPSHOT=pipeline/out/w41/detect-snapshot npm run dev
```

- 데이터 폴더 = `RADAR_APP_DATA` 또는 `desktop/.dev-data-demo` (`--data-dir`은 보지 않는다 — Electron이 스텁 폴더를 넘기므로). 스텁 픽스처가 든 폴더면 거부
- 처음 뜰 때 실제 ADR repo(`RADAR_DEMO_ADR`, 기본 `../adr`)를 `<data>/adr`로 복사한다 — 그 뒤로는 사본에만 쓴다. 처음부터 다시 하려면 데이터 폴더를 지운다
- detect만 실제 모델(Sonnet 5.5)로 돈다. revisit은 409. capture도 실제 모델이 대화를 읽는다
- W41 실측(10/8): detect 작업 14초 · 호출 2회 · 입력 3.8만 토큰 — 약 $0.1

**스텁 모드에서 무엇이 진짜인가.** 그래프·도구·`radar.py`·HITL(write-ahead·지문·edit 검사)은 코어 그대로 돈다. 가짜는 모델의 판단뿐이다 —
capture는 대화에 나온 기술·repo로 미리 적은 대본(`product/radar/stub_script.json`)을 고르고(`service.pick_stub`, 맞는 게 없으면 기본 대본 + 표시),
detect·revisit는 eval 스냅숏(`<data>/snapshots` — Jev v0.9.0 릴리스로 0001 c1 충족, 소식 6건)에 정해진 판단표로 답한다. 웹에는 나가지 않는다.

## 행동 로그 (실사용 지표 — 비교 프로토콜 §7)

`<data>/events.jsonl`, 한 줄에 하나:

```json
{"at": "2026-10-06T16:36:51+09:00", "event": "approve", "item": "2026-10-06-capture-e4598395", "source": "backend", "mode": "stub", "detail": {"outcome": "written", "adr": "0006"}}
```

- `source: backend` — `approve` · `edit` · `reject`(detail에 outcome·stale_answer·adr·errors) · `item_created` · `capture_submitted`(outcome·글자 수·대본) · `run`(flow·초안·알림 수)
- `source: app` — 화면이 `POST /events`로 보낸 것
- **무시**는 기록하지 않고 계산한다: 알림(`notification_shown`)·항목 생성 뒤 답이 없는 항목

## 마일스톤 2 — 빠른 capture · 알림 · Decisions · 실행 (HYO-51·52)

- **빠른 capture 창**: 전역 단축키 기본 `CommandOrControl+Shift+Space`(`RADAR_CAPTURE_SHORTCUT`로 바꿈). 같은 렌더러 번들을 `#/capture` 해시로 띄운 틀 없는 창이고, 숨기고 다시 보여 재사용한다. 블러·Esc로 닫힘. 등록 실패는 로그와 트레이 메뉴 라벨에 나온다. 사이드바 「빠른 capture」로도 연다. (`RADAR_CAPTURE_KEEP=1`: 블러로 안 닫힘 — 스크린샷 확인용)
- **알림·폴링**: 백엔드 준비 뒤 main이 5초마다(`RADAR_POLL_MS`) `/inbox`·`/runs`를 본다. 첫 폴링은 기준선만 잡는다. 새 항목·새 실행(초안/재검토 조건 충족)은 OS 알림 + `notification_shown`/`notification_clicked` 이벤트, 클릭하면 메인 창이 해당 항목으로 간다. 트레이 툴팁에 대기 수. 네트워크는 127.0.0.1 백엔드뿐.
- **Decisions**: 읽기 전용 표 + 오른쪽 상세(재검토 조건·ADR 원문). status 편집 없음(R8). **실행**: detect/revisit 스텁 실행 · 기록. 트레이 메뉴에서도 실행 가능.
- 확인: `npm run build` 통과. 백엔드 v1이 없는 worktree에서 임시 mock(v0+v1)에 `RADAR_BACKEND_URL`로 붙여 CDP로 화면·알림 이벤트 확인. macOS 동작(전역 단축키 권한·트레이 템플릿·알림)은 확인하지 못했다.
