# 놓친 것 — 수집하고도 못 보여 준 소식 (2026-11-04~)

importance-policy §6 지표 「놓친 것」(사람이 따로 발견했는데 후보에 없던 소식)의 기록이다. 건마다 **무엇을 · 언제 수집했나 · 어느 단계에서 빠졌나 · 지금 코드로는 어떻게 되나**를 적는다.
아래 5건은 회귀 정답이기도 하다 — `pipeline/tests/test_trace.py`가 모델 호출 없이 각 건이 기대한 단계에 나오는지 본다 (픽스처 `pipeline/tests/fixtures/missed/`, 실제 기록에서 뽑음).
실사용 기간(11/2~11/22)에는 건만 쌓고 비율은 내지 않는다. 새로 놓친 것이 나오면 아래 표에 행을 더한다 — 지우지 않는다.

## 단계 이름 (고정 — 코드 · 문서 · 요약 줄에 같은 이름)

「관문」은 실제로는 여러 단계다. 무언가를 놓쳤을 때 어디서 빠졌는지 손으로 추적하던 것(11/4 Terra)을 `python -m pipeline.trace`가 한다 (HYO-171).

| 단계 | 이름 | 코드 | 여기서 끝나는 경우 |
|---|---|---|---|
| 1 | 레인 나누기 | `pipeline/lanes.py` | 레인 B(새로 볼 만한 것)는 관문으로 안 간다 — 경보 그물 · 자리 질의만 건진다 |
| 경보 그물 | 경보 그물 | `pipeline/alarm.py` (레인 B → 관문) | 제목에 스택 · 런타임 이름이 없다 · 사고 낱말 + 인기면 「우리 스택 아님」 칸 |
| 2a | 기술 소속 | `gate.resolve_technologies` | 모르는 기술 · 등급 분류 불가(`unclassified`) · 팀원 스택(`other-stack`) · 스택에서 뺀 기술(`retired`) |
| 2b | 변화 분류 | 모델 (+ `policy.normalize_changes` · `successor.py`) | 호출 실패(`error`) |
| 2c | 등급 × 변화 결정표 | `policy.decide` | 남기는 행 없음 (보정: 경로=개발이면 security만) |
| 2d | 우리 것 좁히기 | `scope.narrow` (코드에서는 2c 앞에 돈다) | 우리 버전 · 우리 모델이 아닌 eol · 모델 은퇴를 뺐다 |
| 3 | 칸 정리 | `policy.apply_topk` (11/4 전 run은 5/N 상한) | 질의별 top-k 밖 · 「항상」 같은 패키지 · 줄로 접힘(목록에만) |
| 4 | 자리 질의 | `pipeline/slots.py` | 후보 아님 · 출시 아님 · 적합도 문턱 아래 · 자리 top-k 밖 |
| 5 | detect | SKILL.md 2차 확인 · basis 대조 | 버림 + 이유 · 이미 반영됨 (`run_detect`의 report.json을 줄 때만 보인다) |
| 레인 B 순위 · 레인 C 화제 | | `keyword_match` · `topics.py` | detect로 이어지지 않는 길 — 「막다른 길」 |

```bash
python -m pipeline.trace Terra --week 2026-W44                  # 그 주 실제 run (detect/<주>/trace.jsonl, 없으면 run 폴더에서 다시 만든다)
python -m pipeline.trace litellm 1.107 --week 2026-W44 --now    # 지금 코드로 다시 — 모델 답은 그 주 lane_a.json에 기록된 것만 (호출 없음)
python -m pipeline.trace terra --week 2026-W45 --slots <slots.json 폴더> --report <report.json>   # 다른 자리 질의 결과 · detect 결과를 대 본다
```

`trace.jsonl`은 `detect_feed`가 실행마다 detect 입력 폴더(radar-snapshots `detect/<주>/`)에 쓴다 — 항목마다 한 줄 `{id, end, steps: [[단계, 통과, 이유]]}` (W45 크기로 570KB). 수집에 아예 없으면 명령이 「수집 안 됨」이라고 한다.

## 5건

「그때」 = 그 주에 실제로 돈 run(`pipeline/out/<주>/lane_a.json` — W44는 HYO-148 전 코드, W45는 HYO-162 코드). 「지금」 = 2026-11-04 main 코드 재생 + 11/4에 지금 코드로 돌린 자리 질의 결과(구독 `claude -p`).

| 소식 | 수집 | 그때 — 빠진 곳 | 지금 코드로는 |
|---|---|---|---|
| **GPT-6.1 Terra** — OpenAI changelog 「Oct 26 · gpt-6.1-terra」 블록 | W44 (10/27 얼림) · OpenAI changelog 페이지 diff | 1 레인 A이지만 changelog diff 한 항목으로 묶여 **2c가 같은 diff의 「gpt-6-luna Decisions API」 줄로 판정**(luna L2 × 가격·한도 변경, 남김) → **3 칸 정리: 5/31 상한에서 잘림** (Electron 줄들이 칸을 채웠다) | 블록 하나가 항목 하나(HYO-168 가) → **4 자리 질의 「관문 분류」 2위 · 적합도 2** → detect. 블록의 관문 판정은 모델 답 기록이 없어 재생하지 않는다 (11/4 다시 건 결과는 minor로 버림 — `real-use-changes.md` 11/4 줄) |
| 〃 — HN 「GPT-6.1 Terra」 637점 | W45 (11/3 얼림) · HN | 1 레인 B → **레인 B 순위 밖 — 막다른 길** (화제 141/2187위, 화제 탭 상위 10 밖) | **4 자리 질의 「관문 분류」 1위** → detect |
| 〃 — Bedrock 「OpenAI GPT-6.1 Terra is now generally available on Amazon Bedrock」 | W44 · W45 (같은 항목) · AWS What's New | 1 레인 A → 2a Amazon Bedrock · openai → 2b 종류 없음 → **2c minor·patch 버림** | 그대로 2c 버림 · 4 자리 질의는 「의료 운영 LLM」 적합도 1(서울 · apac 제공 확인 안 됨)이라 문턱 아래. Terra 자체는 위 두 줄이 건진다 |
| **Claude Haiku 5.5** — HN 1047점 (+ Bedrock 「now available on AWS」) | W42 · HN · AWS What's New | **주간 사슬 전** — W42는 detect 입력이 없다(실사용 사슬은 W43부터). 사용자가 직접 알려 줬다 | **4 자리 질의 「조건 추출」 2위 · 적합도 3**(HN) · Bedrock 3위 → detect. Bedrock 항목은 2a Amazon Bedrock까지 가고 2b는 기록 없음 |
| **LiteLLM 1.107.0 PyPI 백도어** — HN 596점 · Reddit r/LLMDevs 주간 11위 | W44 · HN · Reddit | 1 레인 B → **레인 B 순위 밖 — 막다른 길** (그 run에는 경보 그물이 없었다 — HYO-170 전) | **경보 그물**(PyPI · Docker) → **2a 스택에서 뺀 기술**(litellm `retired`, 11/4 기각 — 우리 고정 1.103.1은 해당 없음). **맞는 결과** |
| **http2shell** (Node · Python RCE) — HN 186점 | W44 · HN | 1 레인 B → 막다른 길 (경보 그물 전) | **경보 그물**(Python — 제목 「Node」는 이름 `Node.js`에 안 걸린다) → 2a python L0 → 2b *security라고 가정* → 2c L0 × security 남김 → 3 「항상」 → detect |
| **Broccoli2Shell** (Brotli 디코더 RCE, Node.js · Python) — HN 400점 | W44 · HN | 1 레인 B → 막다른 길 (경보 그물 전) | 경보 그물(Python · Node.js) → … → 2c 남김 → **3 칸 정리: 「항상」에서 http2shell로 접힘 — 목록에만** (아래 찾은 것 1) |

재생의 한계: 모델 답은 그 주에 기록된 것만 쓴다 — 쪼갠 블록 · 경보 그물 글은 그 주에 관문을 안 거쳐 기록이 없다. 테스트는 경보 그물 두 글에 security를 **가정**한다(사고 낱말 RCE). 관문 경보를 실제 모델(sol)로 판정하는 건 W46(11/10)이 처음이다.

## 찾은 것 (추적을 만들며)

1. **다른 사고가 기술 하나로 접힌다** — 「항상」 접기 열쇠가 경보 글은 `("alarm", 관문이 붙인 기술)`이라(`policy.fold_key`, HYO-170) http2shell과 Broccoli2Shell이 둘 다 python이어서 detect로는 한 건만 간다. 대표는 정렬 순서(같은 등급 · 심각도면 id 순)라 **점수가 높은 Broccoli2Shell(400점)이 낮은 http2shell(186점)에 접힌다**. 목록에는 둘 다 남는다. LiteLLM 백도어 HN · Reddit처럼 같은 사고를 묶으려던 규칙이다 — 고칠지(사고 묶음을 링크 · 이름으로 · 대표를 인기 순으로)는 사용자 결정. 실제 W44 재생에서는 경보 글의 모델 답이 없어 아직 일어나지 않았다 — W46 sol 판정에서 처음 드러날 수 있다
   - 변경 (2026-11-04, 사용자 나안 — PR #266): 대표를 **인기 순**(HN 점수 → Reddit 순위)으로 고르고 접힌 글의 제목 · 링크를 대표 detect 입력에 붙인다 · 줄임 이름 `Node` → `Node.js`. 위 표 http2shell · Broccoli2Shell의 「지금 코드로는」 칸은 #266 전 기준이다 — 이제 **http2shell이 Broccoli2Shell로 접히고**(목록에만, 그물 이름 Python · Node.js) Broccoli2Shell이 detect로 간다. 둘은 같은 Aikido 발표 계열이라 접기 자체는 맞았고 대표가 틀렸다. 회귀 정답(`test_trace.py`)도 이 결과로 바꿨다
2. 자리 질의는 그 주 run 뒤에 생겼다(11/4) — `trace`에 지금 자리 질의 결과를 예전 run에 대 보면 「자리 2위인데 그 주 detect 입력에는 없다」로 나온다. 실행이 다르다는 표시이지 버그가 아니다

## 하지 않은 것 (지금)

- detect 2차 판정(초안 · 버림 + 이유 · 이미 반영됨)을 `list.json` · `desktop/backend/schemas.py`에 되돌려 붙이기 — 화면(HYO-168)이 같은 계약을 건드리고 있다. `trace`는 `--report`로 report.json을 받아 5단계 결과를 붙일 수 있게만 해 뒀다. 계약 변경은 화면 PR merge 뒤 사용자에게 묻는다
- 실행 요약 한 줄(`detect_feed.funnel_line`)에 단계별 건수 — 화면을 보고 사용자와 정한다
