# S7 그래프 스키마 `graph@v1` — dev 경로 존재율 (2026-10-27, HYO-149)

> 등록: `docs/design/retrieval-arena.md` §6 변경 (2026-10-27). 원자료 `graph@v1-dev.json` (`arena/cli.py graph-check` 출력).
> **test는 열지 않았다** — `graph-check`는 test 질의를 개수(36)만 세고 노드·qrels를 보지 않는다.
> Neo4j에 적재하지 않았다(HYO-76 M1 smoke 전) — networkx로 같은 노드·관계를 만들어 잰 종이 위 측정이다.

## 무엇을 쟀나

**스키마만의 상한.** gold 질의 노드(nodes@v2 id)에서 시작해 방향을 무시하고 d홉 안에 등급 2 문서가 있나. 시드(BM25)와 순위(PPR)는 보지 않는다 — 그건 HYO-36 dev 파일럿. 여기서 0이면 어떤 시드·순위로도 그 문서는 그래프 쪽에서 못 올린다(BM25 뒤 채우기로만 나온다).

## 그래프

| | 수 |
|---|---|
| 노드 310 | technology 45 · component 21 · release 220 (github-release 문서 209 + 문서 없는 고정 버전 11) · adr 11 · document 13 (C-CODE 7 · 재검토 근거 C-DOC 6) |
| 관계 443 | HAS_RELEASE 217 · DECLARES 81 · USES 60 · NEXT 36 · PINS 16 · AFFECTS 13 · DECIDES 10 · REVISIT_SOURCE 7 · RECORDED_AS 3 |
| 허브 | `tech:llama-cpp` 차수 155 (릴리스 153) — 다음은 스택 절·manifest 문서(20~23) |
| RECORDED_AS | finance_verifier eval → verifier (ADR 0001) · decision-radar product → runtime (ADR 0008·0009) · decision-radar eval → retrieval-arena (ADR 0007). arena ↔ retrieval-arena는 근거 행이 없어 잇지 않았다 · ui는 혼자 |
| 연결 안 된 것 | 릴리스 3 (langchain 모노레포의 langchain-fireworks · langchain-openai · langchain-text-splitters — 노드 어휘에 없는 패키지) · compose 이미지 1 (`jhs-arena/elasticsearch-nori` — 별칭과 이름이 다르다) · 재검토 source_hint 6 (코퍼스에 같은 URL 문서가 없다) |

## dev 경로 존재율 (등급 2 문서)

| 유형 | 질의 | 등급 2 | 그래프 안 | 1홉 | 2홉 | 3홉 |
|---|---|---|---|---|---|---|
| C | 4 | 4 | 4 | 3 | 4 | 4 |
| E | 4 | 7 | 7 | 7 | 7 | 7 |
| J | 3 | 3 | 2 | 0 | 1 | 2 |
| K | 4 | 4 | 4 | 4 | 4 | 4 |
| **C·E·J·K** | 15 | 18 | **17** | 14 | 16 | 17 |
| B | 4 | 13 | **0** | — | — | — |

- **C05** 2홉 — 질의 기술 「Langfuse Python SDK」가 어휘에 없어(nodes@v2 미연결 1) component(verifier) → langfuse → 릴리스로 돈다
- **J01** 2홉 (Agents SDK ← ADR 0006 → 모델 문서) · **J07** 3홉 (product → RECORDED_AS runtime ← ADR 0009 → 가격 문서 — LangSmith는 ADR이 없어 이름 묶기를 거친다)
- **J04 놓침** — ADR 0007 c2의 source_hint는 `developers.openai.com/api/docs/deprecations`, 정답 문서는 `platform.openai.com/docs/deprecations`(vendor-page). 호스트가 달라 URL 일치 규칙이 못 잇는다. 고치려면 호스트 대응표를 사람이 만들어야 해 고치지 않았다(기계적 규칙만)
- **B는 0** — 정답이 전부 우리 스택 밖 모델·논문이라 노드가 없다. 문서 시드 + 순위 「PPR 먼저, BM25로 채움」에서 B는 시드가 그래프에 거의 없어 **S7 ≈ BM25**가 된다. 단 BM25 상위에 그래프 안 문서(예: llama.cpp 릴리스)가 섞이면 PPR이 그것을 앞으로 올려 BM25보다 나빠질 수 있다 — 파일럿에서 볼 것
- **2홉 이웃이 크다** — product를 시드로 갖는 질의는 2홉 안 노드가 74~75개다. 등급 2가 상한으로는 거의 다 닿지만 상위 10에 올리는 일은 순위(PPR)가 한다

## 다시 돌리는 법

```bash
python arena/cli.py graph-check --snapshots ../radar-snapshots --adr ../adr --repo decision-radar=. \
  --repo finance_verifier=../finance_verifier --repo fineprint=../fineprint \
  --supplement source-hints@2026-10-12=../radar-snapshots/supplements/source-hints@2026-10-12/items.jsonl \
  --queries eval/arena/gold@v1.1/queries.jsonl --qrels eval/arena/gold@v1.1/qrels.json --out eval/arena/graph@v1-dev.json
```
