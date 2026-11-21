# pipeline — 프로필 필터 (HYO-73: 배관 + 키워드 기준선)

수집 파이프라인 ② 스냅숏을 읽어 프로필(`radar-profile.yaml`)로 거른다. 설계: `docs/design/doc-pipeline.md` §2-2 · §2-3 · §5.
**이 패키지는 실험을 돌릴 배관과 기준선(키워드)만 만든다.** 매칭 전략(벡터·하이브리드·질의 변환)과 자르는 기준 k는 사용자가 설계하고 dev 주에 실험한다.
표준 라이브러리 + PyYAML만 쓴다 (새 의존성 없음). 네트워크·LLM 호출 없음.

## 사용

```bash
export RADAR_PROFILE=../adr/radar-profile.yaml RADAR_TARGETS=../adr/radar-targets.json RADAR_ADR_DIR=../adr/docs/adr

python -m pipeline.profile_filter validate                       # 프로필 형식 검사
python -m pipeline.profile_filter run --snapshot ../radar-snapshots/2026-W41 --strategy kw,kw+aliases --out pipeline/out/w41
python -m pipeline.profile_filter pool --run pipeline/out/w41 --pool-depth 30     # 판정 시트 (전략·순위 숨김)
#  → judgment-sheet.csv의 relevant 칸에 1(관련)/0(무관)만 적는다 (pool-key.json은 다 채우기 전에 열지 않는다)
python -m pipeline.profile_filter score --run pipeline/out/w41 --cutoffs 5,10,20   # Recall@k · nDCG@k · 노이즈 비율

python pipeline/tests/test_profile_filter.py                      # 오프라인 테스트 + 변이 검사
```

- `--snapshot`은 주차 폴더(`items.jsonl`이 든) 또는 `items.jsonl` 파일. 수집기 원자료(`collector/out/…`, `first_seen`·`new` 칸 없음)도 읽는다 (없으면 `new`는 true로 친다)
- 결과 폴더는 커밋하지 않는다 (`pipeline/out/`는 gitignore)

## 파일

| 파일 | 역할 |
|---|---|
| `interest_profile.py` | 프로필 로더 + 형식 검사 + 자동 필드(id slug · review_after = added+28일). **형식 검사는 원래 `radar.py validate`의 몫 — radar.py는 skill@v2(HYO-75)까지 못 고치므로 지금은 여기 둔다. v2에서 radar.py로 옮긴다** |
| `lanes.py` | 레인 A/B 분리 · 스택 이름 추출(`stack_mentions` 표시용) |
| `keyword_match.py` | 레인 B 키워드 기준선: 전략 `kw`·`kw+aliases`, 점수, `matched_by`, `known_example`. **가중치 상수는 기준선 — 사용자가 dev 주에 바꾼다** |
| `judging.py` | pooling 판정 시트 · 지표(Recall@k · nDCG@k · 노이즈) · 판정 읽기 |
| `profile_filter.py` | CLI (`validate` · `run` · `pool` · `score`) |

## 출력 (`--out` 폴더)

`lane_a.json` · `lane_b.<전략>.json`(순위) · `run.json`(프로필 버전·건수·가중치·스택 출처) · `profile.resolved.json` · `texts.json` · (`pool` 뒤) `judgment-sheet.csv` + `pool-key.json` · (`score` 뒤) `scores.json`

## 두 레인

- **레인 A** = 수집기가 붙인 통로로 가른다: 의존성 릴리스(`dependency-releases`) · 벤더 피드(`vendor:*`) · 벤더 페이지 변경(`page-diff`). 한 항목은 한 레인에만 있고, A에 걸리면 A다 — 관심사에도 맞으면 `matched_by`에 레인 표시와 관심사 매칭이 함께 남는다
- **importance-policy 관문은 코드에 없다** (문서·tier-screen 시험뿐). 그래서 A는 「스택 기술에 걸린 항목」 분류까지만 하고 `gate: null`, `gate_status: "not-implemented"`로 비워 둔다
- 벤더 페이지 원본(`kind: page`)은 소식이 아니라 비교 재료라 어느 레인에도 넣지 않는다 (건수만 센다)
- 레인 B는 점수 순위만. 제목·URL에 스택 이름이 나오는 항목은 레인을 옮기지 않고 `stack_mentions`로 표시만 한다

## 키워드 기준선 (`keyword_match.py`)

- 전략: `kw` = id·topic·examples / `kw+aliases` = kw + aliases. 둘 다 항목 `title`·`text`·`url`에서 찾는다
- 매칭: 한국어가 든 단어는 그대로 부분 일치, 그 밖은 영숫자 단어 경계 + 대소문자 무시 (`OCR`은 `TeleOCR`에서 걸리지 않는다)
- topic은 통째 + 괄호 앞뒤 + 괄호 안을 `·`로 쪼갠 것(`topic_terms`) — 쪼개는 규칙도 전략 변수라 한 함수에만 있다
- 점수 = Σ(관심사별 [MATCH_BASE + EXTRA_TERM × (서로 다른 단어 수 − 1, 상한)] × priority 가중) − 감점(관심사 `not`: 그 관심사가 걸렸을 때만 · 전역 `excludes`)
- 감점만 있고 관심사가 안 걸린 항목은 순위에 오르지 않는다 (제외는 감점이지 후보를 만들지 않는다)
- `known_example`: `examples`에 적힌 이름에 걸리면 true (전략과 무관). 지표는 「전체」와 「알려진 정답 제외」를 따로 낸다

## 측정에서 구분할 이름 둘

- `--pool-depth` (= `--top`): 전략마다 상위 몇 개를 판정 풀에 넣나. **판정 분량이다. 필터의 k가 아니다**
- `--cutoffs`: 지표를 재는 자리(Recall@k의 k). 이것도 필터가 보여 줄 개수를 정하는 값이 아니다 — k는 사용자가 dev 주에 정한다
- 판정이 없으면 `score`는 계산하지 않는다. 일부만 있으면 `--partial`을 줘야 하고 그래도 비어 있는 수를 표시한다. 순위에 있지만 판정되지 않은 항목(cutoff > pool-depth)은 관련 없음으로 치되 `미판정` 건수를 낸다

## 레인 A 관문 (HYO-67: `pipeline/importance/`)

설계·기준: `docs/design/importance-policy.md` §4·§8. 모델이 등급·변화 종류를 분류하고 **판정은 결정표(코드)** 가 내린다. Python은 메인 `.venv` (GPT 비교 경로는 `openai`, 운영 관문은 구독 `claude -p`).

```bash
# 1) 스택의 기술 등급 — 스택이 바뀔 때만. 스크립트 검증(Dockerfile FROM·dev 그룹·manifest)이 모델을 덮는다
python -m pipeline.importance classify-stack --targets --extra pipeline/importance/stack-extra.json --tiers tiers.json --model stub [--plan]   # --targets = 내 대상 전부 (env RADAR_TARGETS 또는 ../adr/radar-targets.json), 각자의 exclude 적용 · 폴더 없는 대상은 출력
#   질의 = 매니페스트 신호 + 대상 repo CLAUDE.md 「의존성 → component」 표(수집기와 같은 파서): 표의 행은 usage에 붙고, 매니페스트 밖 이름(llama.cpp · litellm …)은
#   표에서 바로 질의가 된다 (npm 행은 usage만). stack-extra.json은 「외부 모델·서비스」 몫(모델 id · 서비스)만 (HYO-122 PR 4)
python -m pipeline.importance stack-diff --tiers tiers.json --targets      # 수집기가 보는 스택 vs 등급 파일 — 수집기만 · 관문만 · 양쪽
python -m pipeline.importance override --tiers tiers.json --tech PyYAML --set-tiers L6      # 사람이 고친 값 (최우선, 재분류해도 유지)
# 2) 레인 A에 붙이기 (gate: keep/drop + 어느 행 때문인지, 5/N 상한)
python -m pipeline.profile_filter run --snapshot ... --gate stub --tiers tiers.json --out pipeline/out/w41
# 3) 평가 (C1·C2·C3 + 등급 정확도 진단). 실제 모델은 --real-api 없이는 거부
python -m pipeline.importance evaluate --gold eval/importance/gold.json --model gpt-6-luna --k 3 --plan
python -m pipeline.importance evaluate --gold eval/importance/gold.json --model gpt-6-luna --k 3 --real-api

python pipeline/tests/test_importance.py      # 오프라인 테스트 + 변이 13개
```

- `backends.py`가 백엔드 인터페이스다 (`stub` · `gpt-*` openai SDK 직접 · `jev-*` · `claude-*` · `claude-cli/<모델>`). 정답지 형식은 `evaluate.py` 머리말, 예시는 `eval/importance/gold.example.json`
- 파일: `policy`(결정표) · `tiers`(등급 저장·검증) · `signals`(repo 신호) · `gate`(레인 A 연결) · `openai_backend`(GPT 비교 경로 — 처음엔 Agents SDK의 `agents_backend`) · `cli_backend`(구독 `claude -p` — 운영) · `keys` · `prompts` · `evaluate` · `cli`
- 위의 「관문은 코드에 없다」는 `--gate` 없이 돌릴 때의 모습이다 (`gate: null`, `not-implemented`)
- `card_action.py` (HYO-132) = 남김 카드의 행동 표시. 관문 `kept_by`의 변화 종류로 **코드가** 정한다: 모델 은퇴·EOL → 「강제 이전」 + 본문에서 뽑은 마감일(D-day), 후속 모델 → 「선택 실험」, 그 밖(security·deprecation 등)은 표시 없음
  - 마감일은 보수적으로: 마감 키워드(retire·sunset·EOL·지원 종료 등) 든 줄에서 오늘 이후 날짜가 **하나뿐일 때만**. 없거나 여럿이면 null → 카드는 「마감일 확인 필요」. 「Not sooner than」 줄과 페이지 변경의 `## 빠진 줄`은 건너뛴다. 데스크톱 `GET /inbox`의 `action` 칸으로 나간다
  - 테스트: `python pipeline/tests/test_card_action.py` (변이 7개)

## 관문 → detect 입력 (HYO-84: `detect_feed.py`)

관문이 남기고 주당 상한(5/N) 안에 든 항목만 `radar.py snapshot` 폴더 모양(`feed.json` · `index.json` · `items/`)으로 바꾼다. 제품 detect는 이 폴더를 `RADAR_SNAPSHOT_DIR`로 읽는다.

```bash
python -m pipeline.profile_filter run --snapshot ../radar-snapshots/2026-W41 --gate gpt-6.1-sol --tiers tiers.json --real-api --out pipeline/out/w41
python -m pipeline.detect_feed --run pipeline/out/w41 --out pipeline/out/w41/detect-snapshot     # [--all-kept] 상한에 잘린 것까지
.venv/Scripts/python product/radar/run_detect.py --snapshot pipeline/out/w41/detect-snapshot --adr ../adr --out pipeline/out/w41/detect [--model anthropic --real-api]

python pipeline/tests/test_detect_feed.py      # 오프라인 — radar.py feed·fetch 왕복 + 변이 3개
python pipeline/tests/test_page_entries.py     # 오프라인 — changelog diff 날짜 블록 쪼개기(HYO-168) + 변이 4개
python pipeline/tests/test_alarm.py            # 오프라인 — 경보 그물 · 「우리 스택 아님」(HYO-170) + 변이 6개
```

- changelog · release notes 페이지 diff는 `read_items`가 **날짜 블록마다 항목 하나로** 쪼갠다(`page_entries.py`, HYO-168) — 블록 id · url 끝에 `#<날짜>`. 첫 날짜 앞의 줄과 빠진 줄은 원래 id에 남는다. 얼린 `items.jsonl`은 그대로

- `funnel.json` = 건수(수집 → 레인 A → 관문 남김 → detect 입력) + 관문이 판정 못 한 것(`gate_counts`: 모르는 기술 · 분류 불가 · 팀원 스택(`other-stack`) · 오류). 실행 요약의 첫 줄이 된다 (`run_detect` · 데스크톱 `_summarize_detect`). (모르는 기술+분류 불가)/(레인 A−팀원 스택)이 `policy.UNKNOWN_WARN_RATIO`(5%, 잠정 — 2026-10-15 20%에서 변경, 다시 잰 정상 비율 W41 0.9% · W42 0.8%)를 넘으면 같은 줄에 경고 (HYO-122)
- `gate.json` = url → 왜 남았나(`kept_by`). **detect 입력(feed)에는 넣지 않는다** — 모델 판단을 흔들지 않게. 화면의 「왜 왔나」 칸(HYO-85)이 읽는다
- 버린 이유(결정표의 행)는 옮기지 않는다 — `lane_a.json`·`gate-calls.jsonl`에 있다 (깔때기 화면은 만들지 않는다, 10/8)
- manifest는 수집기와 같은 리더(`collector/stack.py`)로 읽는다 — git 추적 파일만, 대상별 `exclude`(radar-targets.json) 적용. `--repo`만 주면 exclude 없이 그 폴더의 추적 파일을 읽는다 (HYO-122)
- 등급 파일은 `classify-stack`으로 만든다. 등급 스크립트 집행(HYO-67)은 데모 뒤

## 탈락 추적 (HYO-171: `trace.py`)

항목마다 어느 단계를 지나 어디서 왜 빠졌나. 단계 이름(1 레인 나누기 · 2a 기술 소속 · 2b 변화 분류 · 2c 등급 × 변화 결정표 · 2d 우리 것 좁히기 · 3 칸 정리 · 4 자리 질의 · 경보 그물 · 5 detect)과
「놓친 것」 기록은 `docs/decisions/2026-11-04-missed-items.md`. 모델 호출 없음.

```bash
python -m pipeline.trace Terra --week 2026-W44                  # 제목 · URL · id에 낱말이 전부 든 항목마다 · 없으면 「수집 안 됨」 (--text: 본문까지)
python -m pipeline.trace litellm 1.107 --week 2026-W44 --now    # 지금 코드로 다시 — 모델 답은 그 주 lane_a.json에 기록된 것만
python pipeline/tests/test_trace.py                              # 「놓친 것」 5건 회귀 + 변이 7개
```

- `detect_feed`가 실행마다 `<detect 폴더>/trace.jsonl`을 쓴다 (항목마다 `{id, end, steps: [[단계, 통과, 이유]]}` — 제목 · URL · 빠진 곳은 읽을 때 코퍼스에서 붙인다). 명령은 이 파일이 있으면 읽고, 없으면(예전 주) run 폴더의 `lane_a.json` · `lane_b.*.json` · `alarm.json` · `slots.json`에서 다시 만든다
- `end` = detect · list(목록에만) · out(빠짐) · unknown(그 주 detect 입력이 없다). 레인 B 순위 · 레인 C 화제는 기록만 하는 길(「막다른 길」)
- `--now` 재생: 쪼갠 changelog의 원래 항목 · 경보 그물 글처럼 그 주에 모델 답이 없는 것은 2b에서 「기록된 모델 답 없음」으로 멈춘다 (모델을 부르지 않는다). 자리 질의는 run 폴더(또는 `--slots`)의 결과를 그대로 쓴다
- `--report <report.json>`이면 5 detect의 결과(초안 · 버림 + 이유 · 이미 반영됨)를 붙인다 — `list.json`에 되돌려 붙이는 건 화면(HYO-168) 뒤

## 같은 주 재생 (HYO-164: `replay.py`)

W41~W43 입력으로 제품 detect를 다시 돌려 그 주 스킬이 실제로 낸 detect 초안과 견준다. 등록: `docs/design/comparison-protocol.md` §7 「같은 주 재생」 + 변경 (2026-11-11). 보고: `docs/decisions/replay-W41-W43.md`.

```bash
export RADAR_ADR_REPO=../adr RADAR_SNAPSHOT_REPO=../radar-snapshots          # 둘 다 읽기만 한다
python -m pipeline.replay plan    --out pipeline/out/replay-W41-W43            # 무료 — 날짜 기준 basis · ADR · 등급 파일 + 유료 호출 수
python -m pipeline.replay run     --out pipeline/out/replay-stub --engine stub # 배관 확인 (모델 없음)
python -m pipeline.replay run     --out pipeline/out/replay-W41-W43 --engine real --confirm-real   # 사용자 승인 뒤 (관문 API · 등급 API · 구독 claude -p)
python -m pipeline.replay compare --out pipeline/out/replay-W41-W43            # 둘 다 · 스킬만 · 제품만 + 갈린 단계 → compare.json · compare.md
python pipeline/tests/test_replay.py                                          # 오프라인 — 임시 git repo, 변이 포함
```

- **날짜 기준 basis**: `dated_tree(at)`를 `basis.materialize`의 tree 자리에 꽂는다 — 대상 레포 작업 사본의 원격 추적 기록에서 그 시각 이전 first-parent 마지막 커밋을 `git clone --shared` 사본으로. 원격은 부르지 않고, 기록이 그 시각을 덮지 못하면 멈춘다(`--allow-stale`로만 넘긴다)
- 실제 실행(`--engine real`)은 제품 코드(pipeline · product · collector · 스킬 — 러너 자신 파일 제외)가 고정 커밋 `CODE`와 다르면 거부한다 (등록: 재생 중 코드 수정 금지)
- `Guard`: ADR repo · radar-snapshots의 HEAD · 작업 트리 상태와 데스크톱 데이터 폴더의 `events.jsonl` 등을 단계마다 견준다. 출력 폴더가 그 안이나 대상 레포 작업 사본 안이면 거부
- 단계는 주간 사슬처럼 제품 CLI를 자식 프로세스로 부른다 (`profile_filter run` → `slots run` → `detect_feed --no-fetch` → `run_detect.py --model claude-cli` × k). 자식 env에서 `RADAR_UNION` · `RADAR_EXPORT` · 실제 폴더 env를 지운다
- 출력 `<out>/<주>/`: `week.json`(시각 · 커밋 · 자른 등급 · 사람 결정) · `basis.json` · `adr/`(그 시점 사본) · `tiers.json` · `gate/` · `feed/`(trace.jsonl 포함) · `detect/k1..k3/` · `skill/` · `steps.log`. 루트에 `plan.json` · `replay.json`(실행 기록) · `compare.json` · `compare.md`

## 승인 뒤 — 올려 보기 (HYO-239: `tryout.py`)

레이더는 「볼 만하다」까지 알린다. 사람이 승인한 **의존성 릴리스**를 대상 프로젝트의 격리된 사본에서 올려 보고, 그 component의 기존 검사를 올리기 전 · 후로 돌려 결과를 남긴다. 모델을 부르지 않는다.

```bash
python -m pipeline.tryout --adr ../adr/docs/adr/0017-fastapi.md --repo decision-radar --component desktop --checks ../adr/radar-checks.json
python -m pipeline.tryout --adr ../adr/docs/adr/0019-langchain.md --repo decision-radar --component product --with langchain-core==1.8.0   # 혼자서는 설치가 안 될 때

python pipeline/tests/test_tryout.py           # 오프라인 — 가짜 대상 repo · 가짜 환경 + 변이 6개
```

- 순서: 사본(대상의 `origin/<기본>`) → 기준(지금 고정 버전으로 설치 · 검사) → manifest의 `이름==버전` 줄을 올림 → 다시 설치 · 같은 검사 → `result.json` · `report.md` (기본 `pipeline/out/tryout/<기록>-<패키지>-<버전>-<repo>-<component>/`)
- 판정: `pass`(통과 + 검사가 그 패키지를 실제로 올렸다) · `pass_not_exercised`(통과했지만 올린 검사가 없다 — **이 검사로는 모른다**) · `fail`(기준에서 통과하던 검사가 깨짐) · `install_failed` · `not_applied` · `baseline_failed` · `no_pin` · `no_checks` · `already`. 기준에서 실패하던 검사는 「원래 실패」로 따로 적고 올린 탓으로 치지 않는다
- **대상에 쓰지 않는다** — clone한 사본에서만 돈다. 브랜치 push · PR · ADR 수정은 하지 않는다 (결정은 사람, HYO-116). uv가 필요하다(임시 가상환경)
- 검사 설정(`--checks`, 기본 = 대상 목록 옆 `radar-checks.json`): repo · component마다 `manifests`(고정 줄을 찾을 파일) · `install`(`uv pip install -r …`) · `run`(스크립트와 인자). 버전은 결정 기록의 `use`(「의존성 릴리스 0.162.6 대응」)에서 읽고, 못 읽으면 `--to`
- 한계: `이름==버전` 고정 줄만 고친다(범위 고정 · package.json은 아직) · 「그 패키지를 올렸나」는 검사 프로세스 안의 `sys.modules`만 본다(자식 프로세스에서만 쓰면 「아니오」) · 기존 검사가 덮지 않는 동작은 알 수 없다

## 레인 C 화제 (HYO-127: `topics.py`)

분야를 가리지 않고 그 주에 인기 있던 소식을 토픽으로 묶어 상위 k (doc-pipeline §2-3 변경 2026-10-19). A·B와 점수를 합치지 않는다. 네트워크·모델 호출 없음.

```bash
python -m pipeline.topics run --snapshot ../radar-snapshots/2026-W42 --out pipeline/out/topics     # → pipeline/out/topics/2026-W42.json
python pipeline/tests/test_topics.py           # 오프라인 + 변이 5개
```

- **1단계 묶기 = 같은 링크 + 크로스포스트만** (`link+crosspost@v1`). 링크 키는 정규화한다: arXiv abs·pdf·HF papers → `arxiv:<id>` · GitHub은 repo 단위(issues·pull·release는 따로) · HF 모델·데이터셋·스페이스 · YouTube `v`. 토론 페이지(HN·Reddit)·이미지 업로더·사이트 첫 화면·localhost는 키가 아니다. 링크가 `MAX_LINKS`(3)개를 넘는 글은 모음 글로 보고 링크로 묶지 않는다. 같은 글쓴이 + 같은 제목의 Reddit 글은 한 글
- **인기 = 출처 안 순위의 백분위** `1 − (순위 − 0.5)/pool 크기` — pool은 HN points · GitHub stars(new-repos·slow-growers 따로) · HF 논문 upvotes · Reddit·HF의 피드 순위(`rank:*`). 항목은 가장 높은 pool을 쓰고, 화면용 한 마디(`signal`, 예 「HN 2,123점」 「r/LocalLLaMA 주간 3위」)를 붙인다
- **순위 = 자리 나눔**: 상위 k(잠정 10)의 절반은 여러 출처 토픽(출처 수 → 인기), 나머지는 한 출처 토픽(인기). 한쪽이 모자라면 다른 쪽으로 채운다. 파일에는 전체 순위(`rank` · `section`)가 들어간다
- 후보 = 레인 B 중 인기 신호가 있는 것. 레인 A(의존성 릴리스 · 벤더)는 관문의 일이라 넣지 않는다
- 토픽 id = 묶인 항목 id들의 해시 — 같은 주를 다시 돌리면 같다 (저장 버튼이 쓴다)
- W41·W42: 후보 2,062 · 1,993 → 토픽 1,988 · 1,908, 출처를 넘어 묶인 토픽 28 · 31. **묶기 정밀도는 아직 재지 않았다** — 2단계 손 묶음 정답지(HYO-127)에서. 눈으로 본 느슨한 묶음: 한 Reddit 글이 다른 앱 repo를 같이 링크해 두 앱이 한 토픽(W42 DigUp) · 모델 카드와 그 모델을 쓰는 도구 글(GLM-5.3-Flash + Project Maya)
- 같은 소식이 링크가 달라 두 토픽으로 남을 수 있다(W42 Strata 둘) — precision 우선이라 의도한 쪽이다

## 알려진 한계

- 한국어 `not`·`excludes`(「이미지 생성」「이미지·영상 생성 도구」)는 영어 항목에 걸리지 않는다 — 감점이 거의 안 일어난다. 번역·질의 변환 전략의 몫
