# 수집기 (HYO-71 출처 어댑터)

수집 파이프라인 ①단계. 제한된 출처 풀에서 한 주치 원자료를 모아 **공통 항목 형식**(`item.py`)으로 낸다. 설계와 기준의 근거는 `docs/design/doc-pipeline.md`. 표준 라이브러리 + PyYAML만 쓴다.

```bash
python -m collector.run --out collector/out/<날짜>                          # 다섯 출처 전부 (Reddit 때문에 약 12분)
python -m collector.run --sources hn,github,huggingface --out <폴더>        # 일부만
python -m collector.run --sources vendor --only aws-bedrock --out <폴더>   # AWS What's New는 주 2회
python collector/tests/test_collector.py                                    # 오프라인 테스트
```

**정기 실행 (2026-10-02~)**: 이 Windows PC의 작업 스케줄러 — 화 16:00 전체 · 목 16:00 AWS만. `--out` 없이 돌면 `collector/out/<UTC 시각>-<label>/`에 쌓인다.

```powershell
powershell -ExecutionPolicy Bypass -File collector\schedule_windows.ps1 -WeeklyDay Tuesday -WeeklyTime 16:00 -AwsDay Thursday -AwsTime 16:00
powershell -ExecutionPolicy Bypass -File collector\schedule_windows.ps1 -Remove     # 해제
```

M1으로 옮길 때는 같은 두 명령을 `launchd`(StartCalendarInterval — 잠자기 중 놓친 실행은 깨어날 때 돈다)로 등록하면 된다.

출력: `items.jsonl`(항목 한 줄에 하나) + `run.json`(출처별 요청 수·실패·건수·벤더 규칙 결과·200이 아닌 응답).

## 주간 스냅숏 (HYO-72)

weekly 예약 실행은 수집이 끝나면 `collector.snapshot freeze --push`로 한 주를 **`corpus@<ISO 주차>`**로 얼려 private data repo `hyos0415/radar-snapshots`(로컬 `../radar-snapshots`, 환경 변수 `RADAR_SNAPSHOT_REPO`)에 push한다. 폴더 형식은 그 repo의 README.

```bash
python -m collector.snapshot status                    # 얼린 주차 · 안 얼린 실행
python -m collector.snapshot freeze --push             # 손으로 (예약 실행이 실패했을 때)
python collector/tests/test_snapshot.py                # 오프라인 테스트
```

- 한 주 = weekly 실행 ∪ 지난 스냅숏 이후의 aws 실행. 주차는 weekly 시각(KST)의 ISO 주. 이름이 `<UTC>-weekly|aws`인 폴더만 본다 (손으로 돌린 시험 폴더는 빠진다)
- first-seen 원장(`first_seen.jsonl`)으로 다시 나온 항목에 `new: false` — 지우지 않는다
- 벤더 페이지는 전문을 `pages/`에 두고 지난 주와 줄 단위로 비교해 `page-diff` 항목을 만든다. 그래서 수집기는 페이지만 `TEXT_MAX`에서 자르지 않고 블록마다 줄을 바꾼다
- 얼린 주는 고치지 않는다 (같은 주를 다른 실행으로 다시 얼리면 거부). 놓친 주는 다음 manifest의 `missing_weeks`

## 기준은 설정 파일에 있다

| 파일 | 무엇 |
|---|---|
| `pool.yaml` | 서브레딧·flair, HN 점수·지연, GitHub 두 통로·의존성 repo 덮어쓰기, HF 태스크 — doc-pipeline §4 결정 그대로 |
| `vendors.yaml` | 벤더 → 경로 매핑 (손으로 관리) · 일부러 뺀 것 · 벤더가 아닌 것 |

기준을 바꾸면 파일의 `version`을 올린다. `run.json`에 버전이 남는다.

## 출처별

| 출처 | 모듈 | 요점 |
|---|---|---|
| Reddit | `sources/reddit.py` | RSS, **요청 간격 61초**, top 100 + 기술 flair, 글 ID로 합침. 점수는 없고 통로별 순위만 |
| HN | `sources/hn.py` | Algolia, 25점 이상, **2~9일 전** 글을 하루씩 잘라 받음 (질의당 1,000건 상한) |
| GitHub | `sources/github.py` | 통로 ① 7일 안 ★100 · 통로 ② 8~30일 ★500 · 대상 repo CLAUDE.md 의존성 → PyPI `project_urls`로 repo 찾기 → `releases.atom` (최근 7일) |
| HF | `sources/huggingface.py` | trending 100 + 태스크 7개 × 20 + Daily Papers 7일 |
| 벤더 | `sources/vendor.py` | 규칙(대상 repo 「외부 모델·서비스」 ∪ 살아 있는 ADR의 technology) → `vendors.yaml` 경로. 매핑이 없으면 `run.json`의 `no_route`에 남는다 |

## 첫 실행에서 고친 것 (2026-10-02)

- PyPI `project_urls`의 첫 GitHub 링크가 **후원 페이지**(`github.com/sponsors/…`)인 패키지가 있었다(pydantic·uvicorn) → 키 우선순위(Source·Repository 먼저) + 소유자 `sponsors` 제외
- 벤더 규칙의 「경로 없음」에 **로컬 서빙 모델**과 **이 프로젝트 자신**이 섞였다 → `not_vendor`로 분리. ADR의 오픈소스 기술(LEANN·kb-arena)은 대상 repo 의존성이 아니라 GitHub 통로에 안 잡혀서 벤더 매핑에 릴리스 피드로 넣었다
- 남은 「경로 없음」: **TypeSafe Jev** (변경 기록 페이지를 못 찾음. SDK `typesafe_sdk` 릴리스는 의존성 통로로 들어온다)
