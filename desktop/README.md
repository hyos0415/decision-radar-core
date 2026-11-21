# desktop — Decision Radar 데스크톱 앱 (E6, HYO-49)

설계: `docs/design/desktop-app.md` · 결정: ADR adr/0011, Linear HYO-50 · Inbox HYO-51 · Decisions HYO-52.
**프론트엔드는 학습 모드가 아니다** — 굴러가게만 한다.

```
Electron main (src/main)      트레이 · 창 · 백엔드 자식 프로세스 기동/종료 · 헬스 체크
  │  <레포 .venv python> desktop/backend/server.py --port P --data-dir D
  ▼
FastAPI 백엔드 (backend/)     product/radar의 capture_turn2·hitl을 함수로 부른다 — 코어 동작은 바꾸지 않는다
  ▲  fetch http://127.0.0.1:P (JSON만)
Renderer (src/renderer)       React + TS + Tailwind + shadcn/ui(neutral) · Geist + Pretendard · 다크 기준·시스템 테마
```

## 실행

```bash
cd desktop
npm install
npm run dev          # Electron 창 + 트레이 + 백엔드 자동 기동. 처음엔 개발 데이터를 만든다 (몇 초)
```

- `npm install`의 `postinstall`(`scripts/postinstall.mjs`)이 Electron 실행 파일을 받고, macOS에서는 ad-hoc으로 다시 서명한다. Electron 42부터 설치 때 실행 파일을 받지 않고(→ electron-vite가 `Electron uninstall`로 멈춤), macOS 알림이 서명된 앱에서만 뜬다(→ `failed: UNErrorDomain 1`) (HYO-146, 10/28 M1 확인). `--ignore-scripts`로 설치했다면 `node scripts/postinstall.mjs`를 한 번 돌린다

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

## 화면 문구 (HYO-117)

화면 · 트레이 · 알림 문구는 모두 `src/copy.ts`에서 고친다. 컴포넌트에 문구를 직접 쓰지 않는다.

- 해요체, 한 문장이면 마침표 없이 — [토스 UX 라이팅 원칙](https://toss.tech/article/8-writing-principles-of-toss)을 따른다
- 한국어가 기본. 사이드바만 영어
- 고정 용어는 그대로: Inbox · Decisions · ADR · detect · revisit · capture
- 사용자에게 「백엔드」라고 하지 않는다. 서버가 보내는 `message` · `errors`는 고치지 않는다(받은 그대로 보여 준다)

## 안전

| 무엇 | 어떻게 |
|---|---|
| 실제 모델(과금) | 기본은 스텁. **데모 모드**(아래)에서만 — `RADAR_APP_MODEL=anthropic`과 `RADAR_APP_DEMO=1`을 같이 줘야 하고, 하나만 있으면 시작을 거부한다. **실사용 모드**(아래, `RADAR_APP_MODE=real`)는 레포 주인 기기에서만 |
| 내보내기(git push) | 스텁·데모는 없다. 백엔드가 물려받은 `RADAR_EXPORT`를 지우고 **로컬 내보내기**(`RADAR_EXPORT=local` — git을 부르지 않고 승인 기록만, `export.LocalExporter`) + `RADAR_DRY_RUN=1`로 띄운다. Electron main도 자식 환경에서 `RADAR_EXPORT`를 지운다 |
| 쓰는 곳 | 데이터 폴더의 ADR 사본뿐(스텁: `adr-fixture`, 데모: `adr` = 실제 ADR repo의 복사본). 실제 ADR repo는 읽기만 한다 |
| 키 | 백엔드만 `.env`를 읽는다(스텁 모드는 읽지도 않는다). main·화면에는 넘기지 않는다 |
| status | 화면에 status 칸이 없다. edit에 status·번호 칸이 오면 코어(`hitl.check_edit`)가 거부한다 (R8) |

## 백엔드 API (D1)

**계약은 OpenAPI다 (HYO-91).** 요청·응답은 `backend/schemas.py`의 pydantic 모델이고(코어 `AdrDraft` 재사용), FastAPI가 응답을 그 모델로 검사한다 — 어긋나면 500이라 `test_backend.py`에서 드러난다.
`<python> backend/export_openapi.py` → `openapi.json`(커밋) → `npm run gen:api` → `src/renderer/src/lib/api.gen.ts`(openapi-typescript, 커밋). `api.ts`는 생성 타입에 화면이 쓰던 이름으로 별칭만 준다.
CI는 둘 다 다시 뽑아 커밋된 것과 다르면 실패한다. 아래 표는 사람이 읽는 요약이고, 칸의 정확한 모양은 `openapi.json`이 이긴다.
edit의 `draft`만 모양 검사 없이(dict) 받는다 — 모델로 받으면 `status` 같은 모르는 칸이 조용히 버려져 「status 칸 거부」(R8)가 무력해진다.
입력 칸이 빠졌거나 타입이 틀리면 422(`detail`이 목록), 서비스가 거부하면 400·404·409·413·500(`detail` 한 줄).

| | 무엇 | 감싸는 코어 |
|---|---|---|
| `GET /health` | `{ok, mode(stub · demo), profile, model, flows, data_dir, adr_dir, demo?}` — `profile`은 개발 데이터 프로필(`default` · `andbread`), `flows`는 이 모드에서 돌릴 수 있는 흐름(데모는 `detect`만), `demo`는 `{adr_source, adr_copy, snapshot, funnel}` | — |
| `GET /inbox` | 대기 항목 — capture(승인 대기 thread) + detect 초안(검토 대기 별도 파일) + pending(접수된 capture: queued · running · error · linked · no_draft — 화면 설계 ①) + revisit(충족 알림, `adr`·`message` — 화면 설계 ③). 오래된 순, `waiting_days`·`state`(awaiting_approval · stale · review …)·`actions`·`why`(detect만: 관문 판정 「왜 왔나」, 예 `anthropic L5 × deprecation` — 관문 없이 만든 입력이면 null) | `StateStore.threads` + checkpoint `get_state` · `hitl.review_queue` · `gate.json` |
| `GET /inbox/{id}` | + `draft`(detect는 초안 파일을 읽어 편집용으로) · `markdown`(저장될 ADR 미리보기) · `changes`(대기 중 바뀐 점) · `usage`(capture 항목을 만드는 데 든 사용량) · `affected_errors`(affected repo가 레이더 대상이 아니면 이유 — 그대로 승인하면 쓰지 않는다, R18) | `ops.render` · 지문 `compute`·`same`·`diff` (읽기만) |
| `POST /inbox/{id}/approve` `{stale_answer?}` | → `{outcome, message, adr?, path?, changes?, answers?, errors?, validate_errors?, usage?}` (`usage` = capture 2턴에서 모델을 불렀을 때) | capture `capture_turn2(approve)` · detect `hitl.review_item(approve)` |
| `POST /inbox/{id}/edit` `{draft, stale_answer?}` | 고친 초안. 검사 실패면 `edit_errors` — 쓰지 않는다 | capture `capture_turn2(edit)` · detect `check_edit` → `rewrite_adr`(같은 번호) → `review_item` |
| `POST /inbox/{id}/reject` | detect는 초안 파일을 지우고 닫는다 | capture `capture_turn2(reject)` · detect `hitl.review_item(reject)` |
| `POST /inbox/{id}/ack` | **확인함** (HYO-92) — `kind: pending`(끝난 접수)·`kind: revisit`(충족 알림)을 Inbox에서 치운다. ADR은 안 바꾼다(R8). 초안(capture·detect)에는 409 | `jobs.ack` · `<data>/alerts.json` |
| `POST /jobs/{capture\|detect\|revisit}` `{conversation?}` | **작업 + 폴링** (HYO-85) — 202 `{job}`으로 바로 돌아온다. 입력 오류는 4xx(작업이 되지 않음). 일꾼 하나가 접수 순서대로 돈다 | `jobs.py` → `submit_capture` · `run` |
| `GET /jobs` · `/jobs/{id}` | 상태 창 — `{id, kind, state(queued · running · done · error), created, started, finished, message(한 줄), result(아래 동기 엔드포인트와 같은 모양), error, item(capture가 만든 Inbox 항목), mode}`. 끝나면 화면이 Inbox를 다시 읽고 알림. 백엔드가 다시 뜨면 끝나지 않은 작업은 error(다시 돌리지 않음) | `<data>/jobs.json` |
| `POST /capture` `{conversation, repo?, topic?}` | 빠른 capture(동기 — 작업 API로 옮기는 동안 남겨 둔다) → `awaiting`(Inbox에 승인 대기) · `linked`(같은 결정이 이미 있음, `link_candidates`) · `no_draft`, + `usage`. `repo`(HYO-95) = 사람이 정한 대상 repo — 초안의 affected repo로 고정된다(`capture.PinRepo`), 대상 밖이면 400. 없으면 「자동」. `topic`(HYO-127) = 화제 탭에서 연 capture면 `{id, week}` — 행동 로그 `capture_submitted`에만 남고 동작은 그대로(작업 API도 같다) | `capture_turn1` (R9: 쓰기 0) |
| `GET /topics?week=` | **화제 탭** (레인 C, HYO-127) — `{week, weeks(새 것 먼저), method, k, counts, items}`. `items` = 그 주 파일의 상위 k개 `{id, week, rank, section(multi · single), title, url, n_sources, sources, n_items, signal, posts[{source, title, url, signal}], saved, capture_seed}` — `capture_seed`는 capture 창에 미리 채울 글(`화제 (주차): 제목` + 글 줄 최대 5 + `결정 질문: `). `week` 없으면 가장 새 주차. 화제 파일이 없으면 `week: null, weeks: [], items: []`(오류 아님), 주차 파일이 있는데 모르는 주차면 404. 읽기만 | `<화제 폴더>/<주차>.json` (`python -m pipeline.topics run --out`) |
| `GET /topics/saved` · `POST /topics/{id}/save` `{week}` · `POST /topics/{id}/unsave` | 저장한 화제 `{items: [{id, week, title, url, n_sources, sources, n_items, posts, saved_at}]}`(최근 저장 먼저). 저장은 주차 파일에서 **복사본**을 `<data>/topics-saved.json`에 둔다 — 주차 파일을 다시 만들거나 지워도 남는다. 응답 `{ok, saved, message}`, 두 번 저장은 멱등(이미 저장함), 주차·화제가 없으면 404, 저장하지 않은 것을 빼면 404 | `<data>/topics-saved.json` |
| `GET /targets` | 레이더 대상 repo `{items: [{repo}]}` (`radar-targets.json`) — 빠른 capture의 repo 드롭다운 (HYO-95) | `Ctx.targets` |
| `GET /decisions` · `/decisions/{id}` | ADR 목록(번호·제목·status·technology·재검토 조건별 최근 결과·요약) · 상세(+원문). **읽기만** — status를 바꾸는 엔드포인트가 없다(R8) | `radar.py list` + 파일 제목 |
| `POST /runs/detect` · `/runs/revisit` · `GET /runs` | 흐름을 한 번 돌리고(동기) 요약(`summary`·`drafts`·`alerts`·`excluded`·`usage`) — `excluded`는 detect가 버린 소식마다 `{url, title, reason, why}`(why = 관문이 남긴 이유, HYO-97) — `<data>/runs.jsonl`. detect 입력이 수집 스냅숏에서 왔으면(`funnel.json`) `summary` 첫 줄이 건수(수집 → 레인 A → 관문 남김 → detect 입력). 이 모드의 `flows` 밖이면 409 | `detect` · `revisit` 그래프 (스냅숏 모드 + dry-run) |
| `GET /engine?refresh=` | **실행 엔진 상태** (HYO-99) — `{mode, engine(claude-cli · api · 스텁 null), ready, reason, cli{installed, path, version, logged_in, auth_method, subscription_type, error}, api_key, capture_ready, login_command}`. `claude --version` · `claude auth status --json`을 **읽기만**(env에서 API 키를 지우고). `ready`는 claude-cli면 구독(`auth_method: claude.ai`) 로그인일 때만 — 다른 방식이면 `claude -p`가 API로 과금된다. CLI 상태는 15초 다시 쓴다(`refresh=true`면 새로). 스텁에서도 CLI는 읽는다 — 이메일·조직은 담지 않는다 | `engine_status.py` |
| `POST /engine/login` | OS 터미널에 `claude auth login --claudeai`를 연다 → `{opened, command, detail}`. 로그인은 사람이 터미널에서 마치고 화면은 `GET /engine?refresh=true`로 다시 본다. **앱은 로그인 코드·토큰을 받지 않는다**(HYO-114 약관). Windows 새 콘솔(`cmd /k`) · macOS `.command`를 Terminal로 · Linux `x-terminal-emulator` — 못 열면 `opened: false` + 직접 칠 `command`. 화면 버튼은 HYO-139 | `engine_status.py` |
| `GET /schedule` | 정기 실행 칸·지난 실행·차례 여부. 켜기: `RADAR_SCHEDULE=on`(기본 꺼짐) — revisit 월 09:00 · detect 화 18:00(수집 화 16:00 뒤), 앱이 꺼져 있던 동안 지난 칸은 켜질 때 한 번 따라잡는다 | `scheduling.py` |
| `POST /events` `{event, item?, detail?}` | 화면 행동 로그 (`app_opened` · `item_opened` · `notification_shown` · `notification_clicked` · `capture_opened` · `decision_opened` · `topics_opened`(화제 탭 엶) · `topic_opened`(화제 링크 누름, item=화제 id)) | — |
| `GET /notifications?limit=` | **지난 알림** (HYO-110) — `notification_shown` 한 줄이 한 통, 최근 것 먼저(기본 100통). `{id, at, kind, title, body, clicked, clicked_at, items}` — `items`는 알림이 가리킨 항목마다 지금 상태 `status`(`inbox` 지금 Inbox에 있음 + `kind`·`title`·`state` / `answered` 답함 + `answer`·`outcome`·`answered_at` / `gone`) · `adr`(revisit 항목 · 승인으로 쓴 ADR — 거부·버림·다시 판정으로 지운 초안이면 null) · `new_item` · `opened`(알림 뒤에 `item_opened`). 새로 저장하지 않는다 — 행동 로그를 읽기만 | `<data>/events.jsonl` |

`outcome`: `written` · `rejected` · `stale`(바뀐 점을 보이고 계속·다시 판정·버림을 `stale_answer`로 다시 보낸다) · `edit_errors` · `discarded` · `rejudge`(원래 대화로 capture 1턴을 다시 돌려 새 대기 항목) · `blocked` · `recovered` · `error`.
detect 초안은 Inbox에서 승인·수정·거부한다 (로컬 내보내기 — 승인은 사본에 `proposed`로 남고 별도 파일에 승인이 적힌다, git 없음). 검토 대기 초안은 아직 「세상」이 아니어서(지문이 보지 않는다) 다른 대기 항목을 낡게 하지 않는다 — 승인하면 낡게 한다.
읽기(Inbox·상세·Decisions)는 쓰기 잠금을 기다리지 않는다 — 실제 모델 실행(분 단위) 중에도 화면이 멈추지 않는다.

**화제 폴더 (HYO-127)** — `RADAR_TOPICS_DIR`(없으면 `<data>/topics`)의 `<YYYY-Www>.json`이다. 주차 이름이 아닌 파일은 무시한다. 스텁 개발 데이터는 처음 뜰 때 합성 항목(`backend/topics_example.jsonl`, 제목이 전부 `[예시]`)으로 `2026-W42.json`을 만든다(`devdata.ensure_topics`) — 비공개 스냅숏 repo 없이도 화제 탭이 보인다. 데모 모드는 만들지 않는다. 저장·저장 빼기는 행동 로그에 `topic_saved`(detail.week) · `topic_unsaved`.

**모델 사용량 (`usage`, HYO-98)** — 모델을 부르는 응답(실행 · capture 1턴 · 승인/수정/거부의 capture 2턴)과 Inbox capture 상세에 붙는다:
`{model, model_calls, input_tokens, output_tokens, cost_usd, estimated}`. 호출마다 남는 `llm_call` 기록(`product/radar/llm_log.py`)의 합이고, 행동 로그(`run` · `capture_submitted` · `approve`/`edit`/`reject` 이벤트 detail)에도 같은 값이 남는다.
비용은 모델별 단가표(`product/spike/common/env.py` `PRICES`)로 셈한다 — 표에 없는 모델이 섞이면 `cost_usd: null`(모름, 0이 아니다), 로컬 모델(`ollama/…`)은 0, 스텁은 `estimated: true`(글자 수/4 추정 · 비용 0). 프롬프트 캐시 토큰은 아직 따로 세지 않는다.

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

## 실사용 모드 (HYO-99 3단계 — 레포 주인 기기, 실제 ADR repo에 push)

```bash
RADAR_APP_MODE=real npm run dev     # [RADAR_REAL_ADR=../adr] [RADAR_SNAPSHOT_REPO=../radar-snapshots] [RADAR_APP_ENGINE=claude-cli|api]
```

- 시작 검사: ADR repo가 git 작업 사본의 맨 위 · main · origin 있음 · `docs/adr`·`radar-targets.json` 있음 · 데이터 폴더와 안 겹침 · (claude-cli 엔진이면) CLI 바이너리. 틀리면 시작을 거부한다. 데모·`RADAR_APP_MODEL`과 같이 주면 거부
- 데이터 폴더 = `RADAR_APP_DATA` 또는 `~/.decision-radar` (행동 로그 = 실사용 지표라 레포 밖에 둔다)
- 엔진: detect·revisit = 구독 CLI(`claude -p`, 기본) 또는 api. capture는 API — `.env`에 `ANTHROPIC_API_KEY`가 없으면 capture가 409
- 실행 전에 radar-snapshots를 **pull만** 하고 가장 새 `detect/<주차>/`(pipeline.detect_feed 출력)를 detect 입력으로 쓴다 — 만드는 쪽은 수집 PC의 주간 작업. ADR repo는 원격을 따라잡는다(ff)
- 그래프 실행은 dry-run — detect 초안은 작업 사본에만 쓰고 Inbox 검토 대기. **승인 = 커밋 1개를 main에 push**, 거부 = 초안을 지우고 닫힌 항목 기록만 커밋
- revisit 직후 `revisit_last_checked`만 바뀐 ADR은 승인 없이 커밋·push한다(`hitl.export_record` — 기록용 칸 밖이 바뀌었으면 거부하고 작업 사본에 남긴다)
- 정기 실행이 기본 켜짐(`RADAR_SCHEDULE=off`로 끈다) — revisit 월 09:00 · detect 화 18:00
- **검토 PR** (HYO-158, `RADAR_REVIEW=app`으로 끈다): detect 초안마다 ADR repo에 브랜치 `radar/<항목>`(origin/main 위 커밋 1개 — 작업 사본은 안 건드린다) + PR. **merge = 채택**(PR 브랜치에서 고친 뒤 merge = 수정 채택) · **close = 기각**. 앱이 5분마다(정기 확인) PR 상태를 읽어 Inbox 항목을 닫는다 — merge면 로컬 초안을 비우고 main을 따라잡은 뒤 항목 기록을 커밋. 앱의 승인·거부 버튼은 그 PR을 merge·close한다(고치기는 PR에서). `gh` 로그인이 필요하다
- **revisit 입력** (HYO-159): revisit은 가장 새 `radar-snapshots/revisit/<주>/`(조건의 `source_hint` 문서 + 같은 주 detect 입력 · 벤더 페이지, 수집 PC가 화요일에 만든다)를 본다. 없으면 detect 입력으로 돌고 요약 줄에 그렇다고 적는다 — 그때 출처 문서는 판정 불가로 남는다. API 그림자도 같은 폴더를 본다
- **API 그림자** (엔진이 claude-cli일 때, `RADAR_SHADOW=off`로 끈다): detect·revisit이 끝나면 **같은 입력**(실행 직전에 뜬 ADR 사본 + 같은 detect 입력)을 API 엔진으로 한 번 더 돌려 `<data>/shadow.jsonl`에 두 엔진 결과를 나란히 한 줄 — detect는 후보·초안·제외, revisit은 조건별 판정·알림, 각자 사용량. **Inbox·알림·실행 기록·ADR repo에는 아무것도 안 쓴다**. 잠금 밖 백그라운드라 화면을 막지 않고, 죽거나 키가 없으면 그 사실만 남는다. 4주 뒤 엔진 비교용 (사용자 10/26). API 과금이다
- **스킬 그림자** (HYO-166, 비교 프로토콜 §7 「같은 순간 짝」 — 위 API 그림자와 다른 것. 엔진이 claude-cli일 때, `RADAR_SKILL_SHADOW=off`로 끈다): **정기** detect가 끝나면 같은 기기에서 `skill@v1` detect를 라이브 모드(웹 검색) · 구독 `claude -p` · 같은 모델과 effort로 한 번 (`pipeline/skill_line.py` — 합집합 스킬 줄과 같은 실행기). 실행 루트는 tag에서 꺼낸 스킬만 든 빈 폴더(eval 러너 `prepare_runroot`), ADR은 제품 실행 직전에 뜬 사본(git 아님). 원격은 dry-run + 도구 규칙(git push · gh) + 가짜 gh · GH_TOKEN + git push 주소 바꿔치기로 막는다. 출력 · transcript는 `<data>/shadow-skill/<주>/`에만, 성공 · 실패 · 이유는 `<data>/shadow-skill/index.jsonl` 한 줄. **PR · issue · 알림 · Inbox · events · runs · 화면 · API에 아무것도 안 낸다 — 11/22까지 봉인**(사용자도 열지 않는다, index.jsonl의 ok만 본다). 주마다 한 번(k=1) · 손 실행 · 건너뛴 실행에는 안 붙는다 · 제품 실행 결과와 무관하게 돈다 · `RADAR_SKILL_SHADOW_UNTIL`(기본 2026-11-22) 뒤에는 안 돈다. 구독 세션 주 1개

## Mac 검증 (10/26 — Windows로 쓰다 Mac으로 옮긴다, 두 경로 다 검증)

CI(`.github/workflows/desktop.yml`) 백엔드 검사는 Windows에서 PR마다 돈다. macOS는 켤 때만 돈다 — GitHub의 Mac 러너는 사용 시간을 10배로 센다(private repo).

> **변경 (2026-11-21, 사용자 — HYO-242, 11월 무료 시간을 거의 다 씀)**: 백엔드 검사는 PR마다 **우분투**(1배)에서 돈다. Windows(2배)도 켤 때만 — PR에 `windows` 라벨 · Actions 탭 수동 실행. main push에서는 돌지 않고, draft PR은 건너뛰며(「Ready for review」로 바꿀 때 돈다), 같은 PR에 새 push가 오면 앞선 실행을 멈춘다. 주 기기가 M1이 되어 Windows는 교육장 기기에서 쓸 때 확인한다

- **한 번 돌려 보기**: PR에 `mac`(또는 `windows`) 라벨을 붙이거나 Actions 탭에서 desktop → Run workflow(수동 실행은 Windows · Mac 둘 다 돈다). 끝나면 라벨을 뗀다 (붙어 있으면 push마다 돈다)
- **M1을 러너로 등록** (차감 없음 — 그 기기가 켜져 있을 때만 돈다):
  1. GitHub → Settings → Actions → Runners → New self-hosted runner → macOS · ARM64. 나오는 명령으로 받고 `./config.sh` (라벨은 기본값 `self-hosted, macOS, ARM64` 그대로)
  2. 로그인할 때 뜨게: `./svc.sh install && ./svc.sh start`
  3. Settings → Secrets and variables → Actions → Variables에 `MAC_RUNNER` = `on` — 이제 PR마다 M1에서 돈다(main push 검사는 2026-11-21에 껐다). 러너가 꺼져 있으면 그 검사는 대기 상태로 남는다(24시간 뒤 실패) → 오래 비울 땐 `off`
  - 쓰기 권한이 있는 팀원의 PR 코드도 이 Mac에서 실행된다는 점을 알고 켠다

**CI가 못 보는 것 — 기기 앞에서 손으로** (Mac으로 옮길 때 · Mac 쪽 코드를 바꿨을 때):
- [ ] `RADAR_APP_MODE=real npm run dev`가 뜬다 — 시작 검사 통과 (`claude`는 `which claude`, 안 잡히면 `RADAR_CLAUDE_BIN`)
- [ ] `curl localhost:<포트>/engine` → `ready: true` · `auth_method: claude.ai` · detect·revisit 한 번 (구독 사용량을 쓴다)
- [ ] `POST /engine/login` → Terminal 창이 뜨고 `claude auth login --claudeai`가 돈다 (Windows는 새 콘솔 창) · 끝낸 뒤 `GET /engine?refresh=true`
- [ ] 승인 1건이 `../adr` main에 push된다 · revisit 기록 커밋이 push된다
- [ ] 앱을 끄면 백엔드·`claude` 자식 프로세스가 남지 않는다 (`ps aux | grep -E "uvicorn|claude -p"`)

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
- 알림 이벤트의 detail (`GET /notifications`가 읽는다): `kind` · `title`·`body`(OS 알림 문구 그대로) · `items`(묶은 알림이면 항목 id 목록 — 없으면 `[item]`) · `nid`(알림 하나의 id — `notification_shown`과 `notification_clicked`에 같은 값을 넣으면 클릭을 정확히 짝짓는다. 없으면 같은 item·kind의 바로 앞 알림에 붙인다)
- **무시**는 기록하지 않고 계산한다: 알림(`notification_shown`)·항목 생성 뒤 답이 없는 항목

## 마일스톤 2 — 빠른 capture · 알림 · Decisions · 실행 (HYO-51·52)

- **빠른 capture 창**: 전역 단축키 기본 `CommandOrControl+Shift+Space`(`RADAR_CAPTURE_SHORTCUT`로 바꿈). 같은 렌더러 번들을 `#/capture` 해시로 띄운 틀 없는 창이고, 숨기고 다시 보여 재사용한다. 블러·Esc로 닫힘. 등록 실패는 로그와 트레이 메뉴 라벨에 나온다. 사이드바 「빠른 capture」로도 연다. (`RADAR_CAPTURE_KEEP=1`: 블러로 안 닫힘 — 스크린샷 확인용)
- **알림·폴링**: 백엔드 준비 뒤 main이 5초마다(`RADAR_POLL_MS`) `/inbox`·`/runs`를 본다. 첫 폴링은 기준선만 잡는다. 알림은 새 Inbox 항목에서만 낸다 — detect 초안·revisit 충족도 Inbox 항목이라 새 실행은 화면만 새로 고친다 (HYO-111). 항목마다 한 통, 제목은 항목 종류(`kind`)별(승인 대기 초안 · 검토 대기 detect 초안 · 접수한 capture · 재검토 조건 충족). 폴링당 3통을 넘치면 나머지는 「새 Inbox 항목 N건」 한 통. OS 알림 + `notification_shown`/`notification_clicked` 이벤트(`item` = 첫 항목, `detail` = `{kind, count, items, title, body, nid}` — `title`·`body`는 OS 알림 문구 그대로, `nid`는 알림 하나의 id로 shown·clicked가 같은 값), 클릭하면 메인 창이 그 Inbox 항목으로 간다. 트레이 툴팁에 대기 수. 네트워크는 127.0.0.1 백엔드뿐.
- **지난 알림 (HYO-113)**: 사이드바 벨을 누르면 메인 영역 왼쪽에 `GET /notifications` 목록 패널이 열린다(최근 것 먼저, 행 40px). 행을 누르면 항목이 Inbox에 있으면 그 항목, 다시 판정으로 생긴 새 항목(`new_item`)이 Inbox에 있으면 그 항목, 답한 뒤 `adr`이 있으면 Decisions의 ADR로 간다(거부한 항목은 빼고). 셋 다 아니면 누를 수 없다. 읽음(`clicked` 또는 항목의 `opened`)은 흐리게만 표시하고 배지·굵은 글씨는 쓰지 않는다(HYO-110). Esc·벨·바깥 클릭으로 닫히고, Inbox 상세 패널과 따로 열고 닫힌다.
- **Decisions**: 읽기 전용 표 + 오른쪽 상세(재검토 조건·ADR 원문). status 편집 없음(R8). **실행**: detect/revisit 스텁 실행 · 기록. 트레이 메뉴에서도 실행 가능.
- 확인: `npm run build` 통과. 백엔드 v1이 없는 worktree에서 임시 mock(v0+v1)에 `RADAR_BACKEND_URL`로 붙여 CDP로 화면·알림 이벤트 확인. macOS 동작(전역 단축키 권한·트레이 템플릿·알림)은 확인하지 못했다.
