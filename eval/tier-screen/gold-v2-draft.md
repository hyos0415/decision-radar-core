# 정답지 v2 초안 — 새 항목 15개 (예시 효과 측정용, 사용자 확정 필요)

> 목적: 같은 15개로 Luna를 **(A) 정의만 / (B) 정의 + 정책 표 예시 34개** 두 번 돌려 예시 효과를 본다. 15개는 정책 표 예시와 겹치지 않는다.
> 등급은 **v1.2 — 이 레포에서 쓰는 방식**으로 매긴다. 그래서 항목마다 「쓰는 방식」을 붙인다 (모델에게도 준다).
> 출처: 대부분 Decision Radar의 **설계된 쓰임새**(retrieval-arena §6 전략 표 · ADR 0006·0007) — 코드는 아직 없다. N01·N02는 실제 사용.
> 「초안」 열은 Claude가 채웠다. **「확정」 열을 사용자가 채운다.** 이 파일은 실행 디렉터리에 넣지 않는다.

| id | 기술 | 이 레포에서 쓰는 방식 | 초안 | 근거 (v1 정의 + v1.2) | 확정 |
|---|---|---|---|---|---|
| N01 | PyYAML | 스킬 `radar.py`가 ADR frontmatter를 읽고 쓴다 — 모든 명령이 거친다 | L6 | 우리가 부른다(규약 안에 살지 않음) · 표준 라이브러리에 YAML 없음 → 기능 하나 | |
| N02 | GLiNER | (과거 프로젝트) NER 모델을 **토크나이저로만** 썼다 | L6 | v1.2 — 모델이 아니라 토큰화 기능으로 쓰임 | |
| N03 | Elasticsearch 서버 (docker) | Arena S2 BM25 · S4 kNN의 엔진 | L1 | DB·검색 엔진 → L1 (v1.1 U3 매핑 확장) | |
| N04 | elasticsearch-py | Arena 하네스가 ES에 색인·질의를 보내는 클라이언트 | **?** L5/L6 | L5는 「L2·L3을 부르는 클라이언트」 — ES는 L1이라 정의 밖 (정책 표의 PostgreSQL JDBC와 같은 빈틈) | |
| N05 | Neo4j 서버 (docker) | Arena S7·S8·S9 그래프 저장소 | L1 | DB → L1 | |
| N06 | neo4j Python 드라이버 | Arena 하네스가 Cypher 질의를 보내는 클라이언트 | **?** L5/L6 | N04와 같은 빈틈 | |
| N07 | ranx | Arena의 RRF fusion(S5a·S5b)과 IR 지표(Recall·nDCG) 계산 | L6 | 기능 하나 | |
| N08 | sentence-transformers | BGE-M3 임베딩을 로드해 S4 Dense 벡터를 만든다 | L6 | 기능 하나 (모델을 부르는 라이브러리) | |
| N09 | BAAI/bge-m3 | 로컬에서 도는 임베딩 모델 (S4 Dense, S3b sparse) | L2 | 로컬 서빙 모델 (v1.1 U2) | |
| N10 | LightRAG | Arena S9 추출 그래프 전략 하나 | L6 | 전략 하나 = 기능 하나 | |
| N11 | openai-agents (Agents SDK) | 제품 런타임 — 에이전트 루프가 우리 도구 함수를 부른다 (ADR 0006) | L4 | 우리 코드를 부른다 → 뼈대 | |
| N12 | litellm | Agents SDK에서 Claude Sonnet 5.5를 붙이는 어댑터 (ADR 0006) | L5 | L2를 부르는 클라이언트 | |
| N13 | GPT-6 Luna | 가벼운 태스크 모델, OpenAI API (ADR 0007) | L2 | 호스팅 모델 | |
| N14 | GitHub API | routine이 PR·issue를 연다 (`gh`) | L3 | 호스팅 서비스 | |
| N15 | Codex CLI | 개발 중 선별 시험을 돌린다 (`codex exec`) — 운영에 안 들어감 | L7 | 개발·테스트 도구 | |

분포: L1 ×2 · L2 ×2 · L3 ×1 · L4 ×1 · L5 ×1(+?) · L6 ×6 · L7 ×1 · ? ×2. L0·L8 없음.

## 사용자가 정할 것

1. **N04·N06 — DB·검색 엔진의 클라이언트는 몇 등급인가?** 정책 빈틈이다(v1 표의 PostgreSQL JDBC가 경계였던 것과 같은 자리). 제안: L5 정의를 「L1·L2·L3을 부르는 클라이언트」로 넓힌다 → v1.2에 함께 넣는다
2. **N02 GLiNER** — 토크나이저로 쓴 경우 L6이 맞는지 (사용자 사례라 사용자가 정답)
3. 나머지 초안 등급
