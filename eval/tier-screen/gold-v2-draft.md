# 정답지 v2 — 새 항목 15개 (예시 효과 측정용) — **사용자 확정 2026-09-30**

> 목적: 같은 15개로 Luna를 **(A) 정의만 / (B) 정의 + 정책 표 예시 34개** 두 번 돌려 예시 효과를 본다. 15개는 정책 표 예시와 겹치지 않는다.
> 등급은 **v1.2 — 이 레포에서 쓰는 방식**으로 매긴다. 그래서 항목마다 「쓰는 방식」을 붙인다 (모델에게도 준다).
> 출처: 대부분 Decision Radar의 **설계된 쓰임새**(retrieval-arena §6 전략 표 · ADR 0006·0007) — 코드는 아직 없다. N01은 이 레포의 실제 사용, N02는 사용자의 다른 프로젝트 실제 사용.
> 「초안」 열은 Claude가 채웠다. **「확정」 열을 사용자가 채운다.** 이 파일은 실행 디렉터리에 넣지 않는다.

| id | 기술 | 이 레포에서 쓰는 방식 | 초안 | 근거 (v1 정의 + v1.2) | 확정 |
|---|---|---|---|---|---|
| N01 | PyYAML | 스킬 `radar.py`가 ADR frontmatter를 읽고 쓴다 — 모든 명령이 거친다 | L6 | 우리가 부른다(규약 안에 살지 않음) · 표준 라이브러리에 YAML 없음 → 기능 하나 | L6 |
| N02 | GLiNER | (사용자의 다른 프로젝트 — 이 레포들 밖) NER 모델을 **토크나이저로만** 썼다 | L6 | v1.2 — 모델이 아니라 토큰화 기능으로 쓰임 | L6 |
| N03 | Elasticsearch 서버 (docker) | Arena S2 BM25 · S4 kNN의 엔진 | L1 | DB·검색 엔진 → L1 (v1.1 U3 매핑 확장) | L1 |
| N04 | elasticsearch-py | Arena 하네스가 ES에 색인·질의를 보내는 클라이언트 | L5 | v1.2 V2-2 — L5 = L1·L2·L3을 부르는 클라이언트 | L5 |
| N05 | Neo4j 서버 (docker) | Arena S7·S8·S9 그래프 저장소 | L1 | DB → L1 | L1 |
| N06 | neo4j Python 드라이버 | Arena 하네스가 Cypher 질의를 보내는 클라이언트 | L5 | v1.2 V2-2 | L5 |
| N07 | ranx | Arena의 RRF fusion(S5a·S5b)과 IR 지표(Recall·nDCG) 계산 | L6 | 기능 하나 | L6 |
| N08 | sentence-transformers | BGE-M3 임베딩을 로드해 S4 Dense 벡터를 만든다 | L6 | 기능 하나 (모델을 부르는 라이브러리) | L6 |
| N09 | BAAI/bge-m3 | 로컬에서 도는 임베딩 모델 (S4 Dense, S3b sparse) | L2 | 로컬 서빙 모델 (v1.1 U2) | L2 |
| N10 | LightRAG | Arena S9 추출 그래프 전략 하나 | L6 | 전략 하나 = 기능 하나 | L6 |
| N11 | openai-agents (Agents SDK) | 제품 런타임 — 에이전트 루프가 우리 도구 함수를 부른다 (ADR 0006) | L4 | 우리 코드를 부른다 → 뼈대 | L4 |
| N12 | litellm | Agents SDK에서 Claude Sonnet 5.5를 붙이는 어댑터 (ADR 0006) | L5 | L2를 부르는 클라이언트 | L5 |
| N13 | GPT-6 Luna | 가벼운 태스크 모델, OpenAI API (ADR 0007) | L2 | 호스팅 모델 | L2 |
| N14 | GitHub API | routine이 PR·issue를 연다 (`gh`) | L3 | 호스팅 서비스 | L3 |
| N15 | Codex CLI | 개발 중 선별 시험을 돌린다 (`codex exec`) — 운영에 안 들어감 | L7 | 개발·테스트 도구 | L7 |

분포: L1 ×2 · L2 ×2 · L3 ×1 · L4 ×1 · L5 ×3 · L6 ×5 · L7 ×1. L0·L8 없음 — 이 두 등급의 예시 효과는 이 세트로 볼 수 없다.

## 확정 (2026-09-30, 사용자)

- N04·N06 → L5 (정책 v1.2 V2-2: L5를 L1·L2·L3 클라이언트로 넓힘)
- N02 GLiNER → L6 (사용자 사례)
- 나머지는 초안대로
