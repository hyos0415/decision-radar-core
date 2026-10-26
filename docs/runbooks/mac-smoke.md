# Mac 연습장 점검 — 실사용 모드 첫 실행 (HYO-99 3단계)

> **Mac의 Claude에게**: 이 파일을 위에서부터 순서대로 따라 한다. 단계마다 결과를 짧게 사용자에게 말하고, **「멈춤」 표시가 있는 곳은 사용자 답을 받은 뒤** 넘어간다. 사용자에게는 한국어로 답한다 (CLAUDE.md).
> 작성: 2026-10-26 Windows 세션 (main `560b29e` 이후). 끝나면 맨 아래 「보고」 표를 채워 사용자에게 준다 — 사용자가 Windows 세션에 붙여 넣는다.

## 왜 하나

데스크톱 앱 실사용 모드(`RADAR_APP_MODE=real`)는 지금까지 **Windows와 가짜 claude로만** 검증됐다 (CI 클라우드 Mac도 가짜 CLI). 진짜 Mac에서 처음으로 지나가는지 본다:

1. `GET /engine` — Mac의 `claude` CLI가 **구독(claude.ai) 로그인**으로 준비됨인가
2. `POST /engine/login` — Terminal 창이 **실제로 뜨는가** (CI는 화면이 없어 못 봤다)
3. revisit 한 번이 **진짜 `claude -p`**(구독)로 끝까지 도는가 → 기록 커밋이 push되는가 → **API 그림자** 한 줄이 쌓이는가
4. 오프라인 회귀가 Mac에서 통과하는가
5. (detect 입력이 있으면) detect 한 번 — 초안은 연습장 작업 사본에만
6. **HYO-77**(데스크톱: macOS(M1)에서 전부 눌러 보기 — 단축키 권한 · 트레이 · 알림 · 창) — **스텁 모드**라 비용 0 (8단계)

## 왜 「연습장」인가 — 지켜야 할 것

- **진짜 ADR repo(`hyos0415/adr`)에 쓰지 않는다.** 지금(~11/1)은 스킬 실사용 기간이고, 비교 프로토콜 §7·§9는 스킬과 제품을 같이 켜는 것을 금지한다. 그래서 `adr`을 복사한 `adr-smoke` + 로컬 bare 원격 `adr-smoke.git`으로 돈다. push는 로컬 원격으로만 간다
- **데이터 폴더는 `~/.decision-radar-smoke`.** 기본 `~/.decision-radar`는 실사용 지표(행동 로그)라 쓰지 않는다
- **정기 실행 끔** (`RADAR_SCHEDULE=off`) — 켜 두면 띄우자마자 지난 칸을 따라잡아 혼자 돈다
- **detect는 입력이 있을 때만** — detect 입력(`radar-snapshots/detect/<주차>/`)은 Windows가 만든다. 10/26 저녁 Windows 세션이 W43으로 `detect/2026-W43/`을 게시했을 수 있다(0단계 pull 뒤 `ls radar-snapshots/detect`). 없으면 5b를 건너뛴다
- **HYO-78**(데스크톱: 빌드본(npm start) 확인 — file:// 출처 CORS)은 **하지 않는다** — Windows에서 이미 막히는 것을 확인했다(빌드본 출처 `null`에 CORS 허용 헤더 없음 · preflight 400). 고치는 PR이 따로 간다
- 수집 예약(`collector.schedule_mac --install`)을 **등록하지 않는다** — 수집은 Windows 한 곳에서만 (CLAUDE.md)
- 코드를 고치지 않는다. 버그를 찾으면 증상·출력을 보고에 적고, 고치자고 하면 issue → 브랜치 → PR (main 직접 push 금지, CLAUDE.md 규율 9)
- 키·이메일·토큰 값을 출력하지 않는다 (`.env`는 있는지만 본다)

---

## 0. 폴더 · 최신으로

`jhs-decisions`의 부모 폴더를 `$P`라 한다. 형제 폴더가 있어야 한다: `jhs-decisions` · `adr` · `radar-snapshots` (+ `adr/radar-targets.json`이 가리키는 대상 repo들 — 경로를 읽어 있는지만 확인).

```bash
cd "$P"
for d in jhs-decisions adr radar-snapshots; do echo "== $d"; git -C $d status --short --branch | head -3; done
```

- 작업 사본이 깨끗하지 않거나 main이 아니면 **멈춤** — 사용자에게 보여 주고 묻는다 (지우거나 덮지 않는다)
- 깨끗하면:

```bash
for d in jhs-decisions adr radar-snapshots; do git -C $d pull --ff-only; done
git -C jhs-decisions log --oneline -1      # 560b29e 이후여야 한다 (HYO-99 API 그림자 merge)
python3 -c "import json;[print(t['repo'], t['path']) for t in json.load(open('adr/radar-targets.json'))]"   # 각 경로가 adr 기준으로 있는지 ls로 확인
```

대상 repo가 빠져 있으면 보고에 적고 계속한다 (revisit이 그 repo 파일을 못 읽을 뿐).

## 1. 준비

```bash
cd "$P/jhs-decisions"
python3.12 --version || python3 --version          # 3.12
[ -x .venv/bin/python ] || python3.12 -m venv .venv
.venv/bin/pip install -q -r requirements.txt -r product/requirements.txt
(cd desktop && node --version && npm ci)            # node 22
grep -q '^ANTHROPIC_API_KEY=.' .env && echo "anthropic key: 있음" || echo "anthropic key: 없음"
```

- `.env`가 없거나 키가 없으면 **멈춤** — 사용자에게 Windows의 `.env`에서 `ANTHROPIC_API_KEY` 줄을 옮겨 달라고 한다 (그림자·capture용. OpenAI 키는 Mac에 필요 없다). 키를 직접 만들거나 묻지 않는다
- `claude` CLI:

```bash
which claude && claude --version
claude auth status --json | python3 -c "import json,sys;d=json.load(sys.stdin);print({k:d.get(k) for k in ('loggedIn','authMethod','subscriptionType')})"
```

- `loggedIn: True` · `authMethod: claude.ai`가 아니면: 3단계의 로그인 창으로 로그인한다 (지금 하지 않아도 된다)

## 2. 오프라인 회귀 (API·구독 0)

```bash
cd "$P/jhs-decisions"
export PYTHONIOENCODING=utf-8
.venv/bin/python desktop/backend/test_backend.py | tail -3      # OK — 실패 0
.venv/bin/python product/radar/test_engines.py | tail -1        # all passed
.venv/bin/python product/radar/test_export.py | tail -1         # all passed (몇 분 걸린다)
.venv/bin/python collector/tests/test_weekly.py | tail -1       # 19/19
(cd desktop && npm run lint && npm run build) | tail -3
```

실패가 있으면 그 FAIL 줄과 앞뒤 출력을 보고에 넣고 **멈춤**.

## 3. 연습장 만들고 백엔드 띄우기

```bash
cd "$P"
rm -rf adr-smoke adr-smoke.git ~/.decision-radar-smoke        # 지난 연습장만 지운다 (adr 원본은 건드리지 않는다)
git clone -q --bare adr adr-smoke.git
git clone -q adr-smoke.git adr-smoke
git -C adr-smoke remote get-url origin                         # …/adr-smoke.git 이어야 한다 — GitHub면 멈춤
git -C adr log --oneline -1                                    # 원본 HEAD — 끝에 다시 본다
```

백엔드 — **Claude Code의 Bash 도구에서 `run_in_background`로** 띄운다 (끝날 때까지 떠 있어야 한다. 셸 `&`는 쓰지 않는다):

```bash
cd "$P/jhs-decisions"
RADAR_APP_MODE=real RADAR_REAL_ADR=../adr-smoke RADAR_APP_DATA="$HOME/.decision-radar-smoke" RADAR_SCHEDULE=off \
  .venv/bin/python desktop/backend/server.py --port 8765 > /tmp/radar-smoke-backend.log 2>&1
```

다른 호출에서:

```bash
for i in $(seq 1 30); do curl -s localhost:8765/health > /dev/null && break; sleep 1; done
head -5 /tmp/radar-smoke-backend.log
curl -s localhost:8765/health | python3 -m json.tool
```

- 첫 줄이 `실사용 모드 — ADR repo …/adr-smoke …`여야 한다. 시작 거부 문구가 나오면 그대로 보고하고 **멈춤**
- health: `mode: real` · `engine: claude-cli` · `real.adr_repo`가 `adr-smoke` · `real.shadow: true` · `real.api_key: true` · `real.week`: `2026-W43`(Windows가 게시했으면) 또는 `null`(아직 없음 — 정상)

## 4. 엔진 상태 · 로그인 창

```bash
curl -s localhost:8765/engine | python3 -m json.tool
```

- 기대: `ready: true` · `cli.installed: true` · `cli.auth_method: "claude.ai"` · `reason: null`. 아니면 `reason`을 보고

```bash
curl -s -X POST localhost:8765/engine/login | python3 -m json.tool
```

- **멈춤** — 사용자에게 「Terminal 창이 떴고 `claude auth login --claudeai`가 돌았나?」를 묻는다 (Claude는 화면을 못 본다). 이미 로그인돼 있으면 그 창은 닫아도 된다. 로그인이 필요했다면 마친 뒤:

```bash
curl -s "localhost:8765/engine?refresh=true" | python3 -m json.tool    # ready: true
```

## 5. revisit 한 번 (구독 `claude -p` + API 그림자)

먼저 세션 수를 말한다 (CLAUDE.md — 구독 사용량):

```bash
curl -s localhost:8765/decisions | python3 -c "import json,sys;c=[r for x in json.load(sys.stdin)['items'] for r in x['revisit'] if r.get('check_type')!='human'];print('모델이 판정할 조건', len(c))"
```

- **멈춤** — 「revisit은 조건마다(사람이 보는 human 조건 제외) `claude -p` 세션 1개 → 약 N세션(구독), 이어서 그림자가 같은 일을 API로 한 번 (몇 센트). 돌릴까?」 답을 받는다
- 알아 둘 것: detect 입력이 없으면 출처를 못 연다 → 조건은 대부분 **판정 불가**로 나온다. W43 입력이 있으면 그 주 소식만 근거로 본다. 어느 쪽이든 이번 점검은 배관 확인이라 괜찮다

```bash
time curl -s --max-time 1800 -X POST localhost:8765/runs/revisit > /tmp/radar-smoke-revisit.json
python3 -c "import json;r=json.load(open('/tmp/radar-smoke-revisit.json'));print(r['ok'], r['error']);print('\n'.join(r['summary']));print(r.get('usage'))"
```

- 기대: `ok: True` · 요약에 「입력 2026-W43」 또는 「detect 입력이 없어 출처를 못 연다 …」 · 「재검토 기록 커밋 <sha> (ADR n개)」 · `usage.billing: "subscription"`, `cost_usd: null`, `cost_usd_equiv`(정가 환산) 있음
- `ok: False`면 `error`와 `/tmp/radar-smoke-backend.log` 끝 40줄을 보고하고 **멈춤**

그림자 (주 실행이 끝난 뒤 백그라운드로 돈다 — 몇 분):

```bash
for i in $(seq 1 60); do [ -s ~/.decision-radar-smoke/shadow.jsonl ] && break; sleep 10; done
python3 - <<'EOF'
import json, os
recs = [json.loads(x) for x in open(os.path.expanduser("~/.decision-radar-smoke/shadow.jsonl"))]
r = recs[-1]; m, s = r["main"], r["shadow"]
print("flow", r["flow"], "| main", m["engine"], m["ok"], "| shadow", s["engine"], s.get("ok"), s.get("skipped"), s.get("error"))
print("shadow usage", s.get("usage"))
same = sum(1 for a, b in zip(m.get("results", []), s.get("results", [])) if a["result"] == b["result"])
print("조건", len(m.get("results", [])), "| 두 엔진 판정 같음", same)
EOF
```

- 기대: `shadow api True` · `usage.billing: "api"`, `cost_usd` 숫자. `skipped: no_key`면 키 문제, `error`면 그대로 보고

git (push가 연습장 원격으로만 갔는지):

```bash
cd "$P"
git -C adr-smoke.git log --oneline -2 main     # 맨 위 = radar revisit … 기록 커밋
git -C adr-smoke.git show --stat --oneline main | head -10   # docs/adr/*.md 수정만
git -C adr log --oneline -1                    # 3단계에서 본 원본 HEAD 그대로
git -C adr status --short | head               # 비어 있어야 한다
```

## 5b. detect 한 번 (입력이 있을 때만)

```bash
ls "$P/radar-snapshots/detect" 2>/dev/null       # 2026-W43 등 주차 폴더가 있어야 한다 — 없으면 이 절을 건너뛴다
python3 -c "import json,glob;f=sorted(glob.glob('$P/radar-snapshots/detect/*/feed.json'))[-1];print(f, len(json.load(open(f))))"
```

- **멈춤** — 세션 수를 먼저 말한다: detect = 분류 1 + 후보마다 초안 1 (+ validate 실패면 고치기 1). 후보 수는 돌기 전엔 모르니 `feed.json` 건수(위 숫자)를 상한으로 「최대 약 N+1세션(구독) + 그림자 같은 수만큼 API」. 답을 받는다

```bash
time curl -s --max-time 3600 -X POST localhost:8765/runs/detect > /tmp/radar-smoke-detect.json
python3 -c "import json;r=json.load(open('/tmp/radar-smoke-detect.json'));print(r['ok'], r['error']);print('\n'.join(r['summary']));print([d['technology'] for d in r['drafts']]);print(r.get('usage'))"
git -C "$P/adr-smoke" status --short                       # 초안은 미추적(??)으로만 — 승인 전 push 0
git -C "$P/adr-smoke.git" log --oneline -1 main            # revisit 기록 커밋에서 그대로
```

그림자 줄(1~수 분 뒤, `shadow.jsonl` 마지막 줄 `flow: detect`)에서 `main.candidates`·`drafts`와 `shadow.candidates`·`drafts`를 나란히 보고 보고에 적는다. (선택) 6단계 화면에서 이 초안을 하나 승인하면 `adr-smoke.git`에 커밋 1개가 push되는지 본다.

## 6. (선택) 실사용 모드 화면

백엔드를 끄고 같은 환경으로 앱을 띄운다 — 사용자가 Inbox에서 revisit 항목이 보이는지 본다:

```bash
pkill -f "desktop/backend/server.py --port 8765"
cd "$P/jhs-decisions/desktop"
RADAR_APP_MODE=real RADAR_REAL_ADR=../adr-smoke RADAR_APP_DATA="$HOME/.decision-radar-smoke" RADAR_SCHEDULE=off npm run dev
```

(Electron은 레포 루트를 cwd로 백엔드를 띄운다 — `../adr-smoke` 그대로 맞다.) 사용자가 앱을 닫은 뒤:

```bash
ps aux | grep -E "server.py|uvicorn|claude -p" | grep -v grep      # 남은 프로세스가 없어야 한다
```

## 7. 연습장 정리

```bash
pkill -f "desktop/backend/server.py --port 8765" 2>/dev/null
cd "$P" && rm -rf adr-smoke adr-smoke.git ~/.decision-radar-smoke /tmp/radar-smoke-*
```

정리 전에 사용자가 그림자 기록을 남기고 싶어 하면 `~/.decision-radar-smoke/shadow.jsonl`만 따로 복사해 둔다.

## 8. HYO-77 — macOS에서 전부 눌러 보기 (스텁 모드, 비용 0)

HYO-77(데스크톱: macOS(M1)에서 전부 눌러 보기 — 단축키 권한 · 트레이 · 알림 · 창). 스텁 모드는 API·구독·git push가 없다 — 개발 데이터(가짜 ADR)로 화면만 돈다. 환경 변수 없이:

```bash
cd "$P/jhs-decisions/desktop"
env -u RADAR_APP_MODE -u RADAR_REAL_ADR -u RADAR_APP_DATA npm run dev
```

**멈춤** — Claude는 화면을 못 본다. 아래 목록을 사용자에게 보여 주고 하나씩 결과(된다 / 안 된다 + 무엇이)를 받는다:

- [ ] Inbox: capture 항목 **승인 · 수정 · 거부** 각각 한 번 · detect 초안 승인 · 「왜 왔나」 표시
- [ ] **빠른 capture `Cmd+Shift+Space`** — 처음이면 손쉬운 사용(접근성) 권한을 묻는다. 허용 뒤 다른 앱 위에서도 창이 뜨나 · 제출 → Inbox에 접수 항목
- [ ] Decisions 목록 · 상세 · 화제 탭
- [ ] 실행 화면에서 **revisit 실행** → 충족 알림이 **macOS 알림**으로 뜨나 → 알림 클릭 → 앱의 해당 항목으로 가나
- [ ] **트레이(메뉴바) 아이콘** — 밝은·어두운 메뉴바 둘 다에서 보이나 (템플릿 이미지)
- [ ] **창 닫기 → 트레이에 남나 → 트레이에서 종료** → 백엔드도 꺼지나:

```bash
ps aux | grep -E "backend/server.py|uvicorn" | grep -v grep       # 종료 뒤 아무것도 없어야 한다
```

안 되거나 어색한 곳은 보고에 한 줄씩 (화면 · 무엇을 했나 · 무엇이 일어났나). 하위 이슈는 Windows 세션에서 만든다.

## 보고 (사용자에게 — Windows 세션에 붙여 넣는다)

| 단계 | 결과 | 메모 |
|---|---|---|
| 0 pull · 대상 repo | | jhs-decisions HEAD · 빠진 대상 repo |
| 1 준비 · 키 · claude 로그인 | | python·node 버전 · authMethod · subscriptionType |
| 2 오프라인 회귀 | | 실패 줄 |
| 3 백엔드 시작 · health | | 첫 줄 · shadow · week |
| 4 /engine · 로그인 창 | | ready · reason · 창이 떴나 |
| 5 revisit | | ok · 시간 · 세션(model_calls) · cost_usd_equiv · 기록 커밋 |
| 5 그림자 | | ok · cost_usd · 판정 같음 n/m |
| 5 git | | 연습장 원격에만 push · 원본 무변경 |
| 5b detect (입력 있으면) | | 주차 · ok · 후보·초안 · 세션 · 그림자 후보·초안 비교 · push 0 |
| 6 실사용 화면 (선택) | | Inbox · 승인 push · 남은 프로세스 |
| 8 HYO-77 | | 항목별 된다/안 된다 · 접근성 권한 · 알림 클릭 · 트레이 · 종료 후 프로세스 |

막힌 곳이 있으면 그 명령 · 출력 끝 40줄 · 백엔드 로그 끝 40줄을 함께.
