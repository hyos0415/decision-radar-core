# 수집기 (HYO-71 출처 어댑터)

수집 파이프라인 ①단계. 제한된 출처 풀에서 한 주치 원자료를 모아 **공통 항목 형식**(`item.py`)으로 낸다. 설계와 기준의 근거는 `docs/design/doc-pipeline.md`. 표준 라이브러리 + PyYAML만 쓴다.

```bash
python -m collector.run --out collector/out/<날짜>                          # 여섯 출처 전부 (Reddit 때문에 약 12분)
python -m collector.run --sources hn,github,huggingface --out <폴더>        # 일부만
python -m collector.run --sources vendor --only aws-bedrock --out <폴더>   # AWS What's New는 주 2회
python collector/tests/test_collector.py                                    # 오프라인 테스트
```

**정기 실행 (2026-10-02~)**: 화 16:00 전체 · 목 16:00 AWS만. `--out` 없이 돌면 `collector/out/<UTC 시각>-<label>/`에 쌓인다. 화요일 작업은 **한 줄 `python -m collector.weekly`** — 수집 → 얼림(push) → 등급 → 관문 → detect 입력 → revisit 출처 → 게시 (HYO-99 3단계 · HYO-159). 사슬은 OS와 무관한 이 파이썬 한 곳에 있고, OS마다 다른 것은 예약 등록뿐이다.

```powershell
# Windows (작업 스케줄러)
powershell -ExecutionPolicy Bypass -File collector\schedule_windows.ps1 -WeeklyDay Tuesday -WeeklyTime 16:00 -AwsDay Thursday -AwsTime 16:00
powershell -ExecutionPolicy Bypass -File collector\schedule_windows.ps1 -Remove     # 해제
```

```bash
# macOS (launchd — 잠자기 중 놓친 실행은 깨어날 때 돈다). --print는 plist만 보여 준다 (어느 OS에서나)
.venv/bin/python -m collector.schedule_mac --print
.venv/bin/python -m collector.schedule_mac --check [--call] # 예약 실행과 같은 최소 환경으로 claude · 구독 로그인 · git 원격 · 대상 폴더 확인 (등록 안 함, --call = Haiku claude -p 1회)
.venv/bin/python -m collector.schedule_mac --install      # ~/Library/LaunchAgents + launchctl bootstrap. claude를 설치·로그인한 셸에서 — 찾은 경로를 plist RADAR_CLAUDE_BIN · PATH에 넣는다 (launchd는 셸 PATH를 안 읽는다, HYO-182)
.venv/bin/python -m collector.schedule_mac --remove
python -m collector.weekly --dry-run                      # 사슬이 부를 명령만 출력
python -m collector.weekly --from gate --week 2026-W44 [--no-push]   # 중간부터 다시 (앞 단계는 건너뜀)
python collector/tests/test_weekly.py                     # 오프라인 — 가짜 러너 · 임시 git
```

- 관문(`pipeline.profile_filter --gate claude-cli/claude-haiku-5-5`, `RADAR_GATE`/`--gate`로 바꿈 — W47까지 `gpt-6.1-sol` API, HYO-169)은 **수집을 돌리는 기기에 구독 로그인된 `claude`가 있어야** 한다(맥 예약은 plist의 `RADAR_CLAUDE_BIN` — `schedule_mac --check`로 확인). 소식 10개를 한 세션에 묻는다. 일부 소식만 오류(구독 한도 등)로 버려지면 사슬은 계속 가되 `lane_a.json`의 `gate.status == "error"` 수를 `last-weekly.json` gate 단계 detail(`관문 오류 N건 버림`)과 `"warn": true`에 남기고, 끝날 때 콘솔 맨 끝에 `주의 [gate]: …`를 찍는다 (HYO-243). 모델에 물은 소식이 전부 오류면 관문에서 멈추고 feed·게시는 없다 (단계 로그 `collector/out/last-weekly-steps.log` · 예약 작업 콘솔 `last-weekly.log` · 요약 `last-weekly.json`). Sol로 돌리려면 `RADAR_GATE=gpt-6.1-sol` + `.env`의 `OPENAI_API_KEY`.
- 게시는 `radar-snapshots`의 `detect/<주>/` · `revisit/<주>/`만 커밋·push한다 (그 밖의 경로가 스테이징돼 있으면 거부). 이미 커밋돼 있으면 「이미 만듦」으로 건너뛰고 유료 관문도 다시 돌리지 않는다. detect만 커밋된 주를 다시 돌리면(`--from gate --week <주>`) revisit 출처만 받아 따로 커밋한다.
- **revisit 출처** (`pipeline.revisit_feed`, HYO-159): ADR들의 `revisit_when` 중 웹 `source_hint` URL을 그 자리에서 받아 `revisit/<주>/`(index.json · pages · hints.json)로 얼린다. 같은 주 detect 입력 항목과 주간 수집본 벤더 페이지도 같은 폴더에 복사한다. 데스크톱 실사용 revisit이 이 폴더를 본다 — 모델은 웹에 안 나간다. 실패(네트워크)해도 detect 입력은 게시하고, 앱은 지난 주 출처를 본다. 출처를 하나도 못 받으면 단계 실패로 남는다. 본문이 짧은 페이지(스크립트로 그리는 문서)는 `hints.json`의 `thin`.
- 프로필·대상·ADR 경로는 env `RADAR_PROFILE` · `RADAR_TARGETS` · `RADAR_ADR_DIR`, 없으면 `<레포 부모>/adr/...`.
- **한 기기에서만** 돌린다 (아래 주간 스냅숏 참고) — Windows에서 Mac으로 옮길 때는 한쪽을 먼저 `-Remove`/`--remove`한다.

출력: `items.jsonl`(항목 한 줄에 하나) + `run.json`(출처별 요청 수·실패·건수·벤더 규칙 결과·200이 아닌 응답).

## 주간 스냅숏 (HYO-72)

weekly 예약 실행은 수집이 끝나면 `collector.snapshot freeze --push`로 한 주를 **`corpus@<ISO 주차>`**로 얼려 private data repo `hyos0415/radar-snapshots`(로컬 `../radar-snapshots`, 환경 변수 `RADAR_SNAPSHOT_REPO`)에 push한다. 폴더 형식은 그 repo의 README.

얼린 뒤에는 `.venv` 파이썬으로 `python -m pipeline.importance weekly-tiers`가 돈다 (HYO-122 PR 3) — 관문 등급 파일 `../radar-snapshots/tiers.json`(고정 경로, `RADAR_TIERS`)을 내 스택에 맞춘다. 지문이 바뀐 기술만 Sonnet 5.5(API, `.env`의 `ANTHROPIC_API_KEY`)로 다시 분류하고, 분류한 게 있으면 그 파일만 커밋·push한다. 보고서는 `collector/out/last-tiers.json`(새 기술 · 지문 바뀜 · 사라진 기술 · 실패 · 호출 · 비용). 키가 없으면 분류를 건너뛰고 「미분류 N」을 로그에 남긴다. 예약을 바꾼 뒤에는 `schedule_windows.ps1`(또는 `schedule_mac --install`)을 다시 실행해 등록을 갱신한다.

```bash
python -m collector.snapshot status                    # 얼린 주차 · 안 얼린 실행
python -m collector.snapshot freeze --push             # 손으로 (예약 실행이 실패했을 때)
python collector/tests/test_snapshot.py                # 오프라인 테스트
```

- 한 주 = weekly 실행 ∪ 지난 스냅숏 이후의 aws 실행. 주차는 weekly 시각(KST)의 ISO 주. 이름이 `<UTC>-weekly|aws`인 폴더만 본다 (손으로 돌린 시험 폴더는 빠진다)
- first-seen 원장(`first_seen.jsonl`)으로 다시 나온 항목에 `new: false` — 지우지 않는다
- 벤더 페이지는 전문을 `pages/`에 두고 지난 주와 줄 단위로 비교해 `page-diff` 항목을 만든다. 그래서 수집기는 페이지만 `TEXT_MAX`에서 자르지 않고 블록마다 줄을 바꾼다
- 얼린 주는 고치지 않는다 (같은 주를 다른 실행으로 다시 얼리면 거부). 놓친 주는 다음 manifest의 `missing_weeks`
- 주소를 옮긴 페이지(`vendors.yaml`의 `replaces`)는 옛 페이지를 한 주 `replaced`로 두고, 옛 주소의 마지막 정상 주 이후 날짜 블록을 page-diff로 메운다 (HYO-172). 옛 주소가 **지난주까지 정상**이었으면(주소만 옮김 — 리다이렉트로 받던 본문) 날짜로 메우지 않고 지난주 본문과 줄 비교한다 — 폐기 페이지는 날짜를 못 읽거나 앞으로의 은퇴일이 가장 늦은 날짜라 날짜 메우기로는 그 주 변경이 빠진다 (HYO-175~178)

### 출처 건강 검사 (HYO-172)

얼릴 때 출처마다 지난 4주와 비교해 이상(실패 · 0건 · 절반 미만 · 다른 도메인 · 3주째 그대로 · 등록부에 없음)만 `<주>/source-health.json`에 쓴다. 출처 단위와 항목 수는 데이터 등록부(`registry/registry.json`, HYO-180)로 묶어 센다 — **새 출처 · 통로를 붙이면 `registry/sources.ttl`에도 적는다**(`registry/README.md`). 규칙은 doc-pipeline §4-7. 주간 사슬 `health` 단계가 `.env`의 `LINEAR_API_KEY`로 Linear 이슈를 만든다(없으면 건너뜀 — 앱 「이번 주」 머리 한 줄은 보인다). 일부러 둘 이상은 `source-health.yaml`에 예외로.

```bash
python -m collector.health check --week 2026-W45     # 얼린 주를 다시 검사해 출력만
python -m collector.health alert --week 2026-W46 --dry-run
python collector/tests/test_health.py
```

## 기준은 설정 파일에 있다

| 파일 | 무엇 |
|---|---|
| `pool.yaml` | 서브레딧·flair, HN 점수·지연, GitHub 두 통로·의존성 repo 덮어쓰기, HF 태스크 — doc-pipeline §4 결정 그대로 |
| `vendors.yaml` | 벤더 → 경로 매핑 (손으로 관리) · 일부러 뺀 것 · 벤더가 아닌 것 · 옮긴 주소(`replaces`) |
| `source-health.yaml` | 출처 건강 검사의 알려진 예외 (HYO-172) |

기준을 바꾸면 파일의 `version`을 올린다. `run.json`에 버전이 남는다.

## 출처별

| 출처 | 모듈 | 요점 |
|---|---|---|
| Reddit | `sources/reddit.py` | RSS, **요청 간격 61초**, top 100 + 기술 flair, 글 ID로 합침. 점수는 없고 통로별 순위만 |
| HN | `sources/hn.py` | Algolia, 25점 이상, **2~9일 전** 글을 하루씩 잘라 받음 (질의당 1,000건 상한) |
| GitHub | `sources/github.py` | 통로 ① 7일 안 ★100 · 통로 ② 8~30일 ★500 · 의존성 릴리스: ① 대상 repo CLAUDE.md 표(npm 표시 행 제외) → PyPI `project_urls`로 repo 찾기 ② 대상 repo의 모든 `package.json`(dependencies+devDependencies) → npm registry `repository`로 repo 찾기 (`pool.yaml` `dependency_repos`가 먼저) → 릴리스 API (최근 8일) |
| HF | `sources/huggingface.py` | trending 100 + 태스크 7개 × 20 + Daily Papers 7일 |
| 벤더 | `sources/vendor.py` | 규칙(대상 repo 「외부 모델·서비스」 ∪ 살아 있는 ADR의 technology) → `vendors.yaml` 경로. 매핑이 없으면 `run.json`의 `no_route`에 남는다 |
| 공식 보안 채널 | `sources/security.py` | HYO-170 다 (doc-pipeline §4-6). **OSV**: 대상 repo manifest의 PyPI · npm 이름을 버전 없이 `querybatch`로 묻고, 창(8일) 안에 **발행된** 공지 + 악성 패키지 기록(`MAL-`)은 창 안에 **수정된** 것도 → 공지 하나 = 항목 하나(별칭 GHSA · PYSEC 합침). 등급 파일(`RADAR_TIERS`) `retired` 기술은 묻지 않는다. **런타임**: `pool.yaml` `security.runtimes` 피드 — Python `security-announce` · Node.js `vulnerability.xml`(본문이 비어 글 페이지를 받는다). 통로 `security-advisories` · `runtime-security:<이름>` → 레인 A, `meta.dependency`로 관문이 그 기술 하나로 판정(Node.js → electron) |

**대상 repo는 두 목록이다** (2026-10-08, 나안 — 수집은 같이, 결정 기록은 각자의 ADR repo):

| 목록 | 누가 읽나 | 무엇 |
|---|---|---|
| `adr/radar-targets.json` | 스킬 · 제품 · 데스크톱 · `pipeline/`(레인 A) · 수집기 | **내 스택** |
| `collector/targets-collect-only.json` (env `RADAR_COLLECT_ONLY`) | **수집기만** | 다른 사람의 repo — 의존성 릴리스·벤더 규칙에만 합쳐진다. `path` = 이 레포와 같은 부모 아래의 형제 폴더 이름(절대 경로도 됨). 수집 노트북에 클론돼 있어야 하고, 없으면 `run.json`의 `collect_only.missing`에 남는다. **수집 직전에 `git pull --ff-only`로 당긴다**(아무도 그 노트북에서 작업하지 않아 그대로 두면 옛 스택을 읽는다 — 결과는 `collect_only.refresh`). 두 목록에 같은 이름이 있으면 수집을 거부한다 |

내 스택 repo는 당기지 않는다(그 노트북에서 작업하는 사본이다). 단 `radar-targets.json`에 `"mirror": true`가 붙은 항목은 **레이더 전용 읽기 사본**이라 수집 직전에 당긴다(`collect_only.my_mirrors_refresh`) — 예: medical-mate-ai는 작업 사본이 다른 브랜치에 있어 `../medical-mate-ai-radar`(main 클론)를 대상으로 둔다 (2026-10-08). 목록에 있는데 폴더가 없으면 `collect_only.my_targets_missing`에 남는다 — 조용히 빠지면 그 주 수집이 영구히 빈다

**manifest는 git이 추적하는 파일만 읽는다** (HYO-122, `stack.py`) — 수집기(package.json)와 관문(`pipeline/importance`, requirements·package.json·Dockerfile·compose)이 같은 리더를 쓴다. 미추적·ignored 사본(`.dev-data-*` 등)과 `.claude`·`node_modules` 같은 폴더는 안 읽는다. 다른 사람의 사본이 레포 안에 추적돼 있으면 대상 항목에 `"exclude": ["desktop/backend/profiles"]`(root 기준 경로·폴더·glob)를 적는다 (`radar-targets.json` · `targets-collect-only.json` 둘 다). git이 아닌 폴더는 걸어서 읽고 `run.json`의 `github.dependencies.manifests`에 모드(`git`|`walk`)와 뺀 파일을 남긴다.

**등급·관문은 내 스택을 원격 기본 브랜치 HEAD로 읽는다** (HYO-162, `pipeline/basis.py`) — 주간 사슬이 대상마다 `origin`을 `collector/out/targets/repos/<repo>`에 얕게 clone해 두고 맞춘 뒤, 그 사본을 가리키는 대상 목록을 `weekly-tiers`·`profile_filter`에 넘긴다. 작업 사본(다른 브랜치·커밋 안 한 변경·묵은 clone)이 판정에 섞이지 않는다. 원격을 못 읽은 레포는 작업 사본으로 읽고 `last-weekly.json`의 `targets` 줄에 이유를 남긴다. 관문이 읽은 기준(레포별 커밋 · 우리 버전)은 `detect/<주>/basis.json`으로 같이 게시되고, 데스크톱 detect가 실행 직전의 원격 HEAD와 대조해 그 사이 이미 반영된 릴리스를 「이미 반영됨」으로 뺀다. 수집 단계(`collector.run`)는 예전처럼 작업 사본을 읽는다.

## 첫 실행에서 고친 것 (2026-10-02)

- PyPI `project_urls`의 첫 GitHub 링크가 **후원 페이지**(`github.com/sponsors/…`)인 패키지가 있었다(pydantic·uvicorn) → 키 우선순위(Source·Repository 먼저) + 소유자 `sponsors` 제외
- 벤더 규칙의 「경로 없음」에 **로컬 서빙 모델**과 **이 프로젝트 자신**이 섞였다 → `not_vendor`로 분리. ADR의 오픈소스 기술(LEANN·kb-arena)은 대상 repo 의존성이 아니라 GitHub 통로에 안 잡혀서 벤더 매핑에 릴리스 피드로 넣었다
- 남은 「경로 없음」: **TypeSafe Jev** (변경 기록 페이지를 못 찾음. SDK `typesafe_sdk` 릴리스는 의존성 통로로 들어온다)
