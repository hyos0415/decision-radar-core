# 화제 계층 — 부모 찾기 v1 등록 (HYO-135, 2026-10-23 — 결과 0건)

> test 결과를 보기 전에 커밋·push한다(규율 7). 바꾸려면 지우지 않고 `변경 (날짜, 이유)`로 남기거나 새 판으로 다시 등록한다.

## 방법 (`eval/topics/hier_methods.py`, 모델 없음 · 결정론)

- **P1 파생 이름**: 자식 이름에 양자화·재포장 표시(GGUF · AWQ · GPTQ · MLX · EXL2 · ONNX · int4 · fp8 · Quantized …)가 있고 부모 이름에는 없다. 부모 이름 토막이 자식 이름의 앞부분이거나, 둘이 숫자 토막을 포함한 앞 2토막 이상을 함께 가진다 (Qwen-Image-2.1-Turbo → Qwen-Image-2.1-Uncensored-Quantized)
- **P2 활용 언급**: 자식 제목에서 「쓴다」는 동사(runs · using · uses · built with/on · powered by · on top of) 바로 뒤에 부모 모델 이름이 온다 (「Mac app that runs EmbeddingGemma 2」). 「tool named X」 · X에 대한 기사는 아니다
- 이름: HF 모델·GitHub repo는 id의 repo 이름, 「owner/name · Hugging Face」 링크 글은 name, 「이름: 설명」 제목은 앞부분(4낱말 이하)
- 두 방향이 다 맞으면 내지 않는다

## dev — W42 (`gold-w42.json`, 하위 11짝) — 이 규칙을 고른 곳

| 규칙 | 하위로 낸 짝 | 맞음 | 오탐 | 재현율 |
|---|---|---|---|---|
| P1 | 8 | 8 | 0 | 0.727 |
| P2 | 3 | 3 | 0 | 0.273 |
| P1+P2 | 11 | 11 | 0 | 1.000 |

dev에서 바꾼 것 하나: P2의 첫 판(제목에 이름 + app·tool 같은 단서 낱말)은 은행 해킹 기사(「penetration tool named ARTEX」)를 ARTEX의 하위로 냈다 → 「동사 바로 뒤에 이름」으로 좁혔다. dev 하위는 세 가족(Qwen-Image · EmbeddingGemma 2 · laya)뿐이라 dev 수치는 증거가 아니다.

## test — 채택 기준 (이 등록 뒤에 처음 돌린다)

- **test = W41** (`gold-w41.json` 하위 5짝 + `gold-w41rest.json` 하위 6짝, 스냅숏 `corpus@2026-W41`) — dev와 다른 주
- **채택 (둘 다)**: ① 하위로 낸 짝 중 방향까지 맞은 비율(정밀도) **≥ 0.9** ② 하위로 낸 짝 **≥ 5**. ②가 안 되면 「보류 — 근거 부족」
- 재현율은 보고만 한다 — 놓친 하위는 지금처럼 따로 카드가 될 뿐이고, 틀린 계층은 글을 엉뚱한 부모 밑에 숨긴다. 그래서 정밀도로만 거른다
- 보고만: `gold-w42t120.json`(하위 18짝) — dev와 같은 주라 같은 가족이 겹친다. 판정 밖 짝에서 낸 하위 수(진단)
- 정답지 출처: 사람 · Claude 사전 판정 · 두 모델 일치 초안이 섞여 있다(각 정답지 `meta.sources`)

## 채택 뒤 (레포 주인)

API(`posts`에 부모·자식 표시)와 화면(HYO-133 후속) 반영은 레포 주인이 정한다. 이 등록은 방법과 기준만 정한다.
