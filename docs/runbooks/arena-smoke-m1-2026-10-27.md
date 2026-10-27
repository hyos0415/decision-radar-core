# Arena 엔진 스모크 M1 결과 — 2026-10-27 (HYO-76)

HYO-76(Arena: M1 Mac에서 ES·Neo4j 엔진 스모크). `infra/compose.yaml`로 띄운 ES·Neo4j에 `infra/smoke.py`를 돌렸다. 코드·설정은 바꾸지 않았다. 기준 커밋 `73fbd86`.

## 요약

- **smoke 20/20 통과** (`failed: 0`). 막혀야 하는 RRF·`bbq_disk`도 basic 라이선스에서 실제로 막혔다
- Neo4j 5.26.31 + n10s 5.26.0이 M1(arm64)에서 뜬다. n10s는 첫 기동 때 인터넷에서 받았다
- 메모리는 Windows 실측(infra/README.md)과 거의 같다: ES 1.46GiB · Neo4j 1.44GiB
- 확인할 점 1개: `stop` 때 Neo4j가 10초 안에 내려가지 않아 SIGKILL(exit 137)로 꺼졌다

## 머신

| 항목 | 값 |
|---|---|
| 칩·메모리 | Apple M1 · 16GB · macOS 27.0.1 |
| Docker | Docker Desktop 4.6.0 (엔진·클라이언트 20.10.13, Compose v2.3.3) · VM 4 CPU / 7.67GiB · aarch64 |
| ES | `jhs-arena/elasticsearch-nori:9.5.4` (처음 빌드, arm64, 1.21GB) |
| Neo4j | `neo4j:5.26.31` community (arm64, 621MB) + n10s 5.26.0 (`n10s.version()`) |

Docker Desktop 4.6.0은 2022년 3월 버전이다. 이번 실행엔 문제없었다.

## smoke

| 검사 | 결과 | detail |
|---|---|---|
| es.version | ✅ | 9.5.4 |
| es.license_basic | ✅ | basic |
| es.plugin_nori · es.nori_tokenize | ✅ | analysis-nori · `LEANN/이/incremental/insert/를/지원/하/기/시작/하/았/나` |
| es.bm25_custom_similarity · es.bm25_search | ✅ | k1=1.2 b=0.75 |
| es.dense_{hnsw, int8_hnsw, bbq_hnsw, flat, int8_flat, bbq_flat} | ✅ | 모두 top1=2 |
| es.dense_default_index_options (기록) | ✅ | `bbq_hnsw` · cosine (dims=1024) |
| es.bbq_disk_blocked (막혀야 정상) | ✅ | 매핑은 통과, 색인에서 `security_exception … [bbq_disk]` |
| es.rrf_blocked (막혀야 정상) | ✅ | `security_exception … [Reciprocal Rank Fusion (RRF)]` |
| es.cleanup | ✅ | 남은 인덱스 없음 |
| neo4j.version | ✅ | 5.26.31 community |
| neo4j.n10s_loaded | ✅ | 프로시저 55개 |
| neo4j.n10s_shacl | ✅ | `n10s.validation.shacl.*` 8개 |
| neo4j.n10s_graphconfig_init | ✅ | ok |

## 메모리 (`docker stats --no-stream`, smoke 직후)

| 엔진 | M1 | Windows (infra/README.md, 10/02) | 상한 |
|---|---|---|---|
| ES | 1.458GiB (72.9%) | 약 1.5GB | 2GiB |
| Neo4j | 1.442GiB (72.1%) | 약 1.4GB | 2GiB |

합계 약 2.9GiB — Docker VM 7.67GiB 안에 여유 있다.

## 확인할 점 — Neo4j 정지

`docker compose -f infra/compose.yaml stop`에서 ES는 exit 143(SIGTERM 정상 종료), Neo4j는 exit 137(기본 10초 안에 안 내려가 SIGKILL)이었다. 이번 결과엔 영향 없지만, 데이터를 쌓은 뒤 이렇게 꺼지면 다음 기동 때 복구를 거칠 수 있다. 바꾸지 않았다. 안:

- `compose.yaml` neo4j에 `stop_grace_period: 30s`
- 또는 끌 때 `docker compose -f infra/compose.yaml stop -t 30`

## 원자료 (`smoke.py --json`)

```json
{
 "results": [
  {
   "check": "es.version",
   "pass": true,
   "expect": "ok",
   "detail": "9.5.4"
  },
  {
   "check": "es.license_basic",
   "pass": true,
   "expect": "ok",
   "detail": "basic"
  },
  {
   "check": "es.plugin_nori",
   "pass": true,
   "expect": "ok",
   "detail": [
    "analysis-nori"
   ]
  },
  {
   "check": "es.nori_tokenize",
   "pass": true,
   "expect": "ok",
   "detail": [
    "LEANN",
    "이",
    "incremental",
    "insert",
    "를",
    "지원",
    "하",
    "기",
    "시작",
    "하",
    "았",
    "나"
   ]
  },
  {
   "check": "es.bm25_custom_similarity",
   "pass": true,
   "expect": "ok",
   "detail": "k1=1.2 b=0.75"
  },
  {
   "check": "es.bm25_search",
   "pass": true,
   "expect": "ok",
   "detail": [
    "0"
   ]
  },
  {
   "check": "es.dense_hnsw",
   "pass": true,
   "expect": "ok",
   "detail": "top1=['2']"
  },
  {
   "check": "es.dense_int8_hnsw",
   "pass": true,
   "expect": "ok",
   "detail": "top1=['2']"
  },
  {
   "check": "es.dense_bbq_hnsw",
   "pass": true,
   "expect": "ok",
   "detail": "top1=['2']"
  },
  {
   "check": "es.dense_flat",
   "pass": true,
   "expect": "ok",
   "detail": "top1=['2']"
  },
  {
   "check": "es.dense_int8_flat",
   "pass": true,
   "expect": "ok",
   "detail": "top1=['2']"
  },
  {
   "check": "es.dense_bbq_flat",
   "pass": true,
   "expect": "ok",
   "detail": "top1=['2']"
  },
  {
   "check": "es.dense_default_index_options (기록)",
   "pass": true,
   "expect": "record",
   "detail": "index_options=bbq_hnsw similarity=cosine (dims=1024, Basic)"
  },
  {
   "check": "es.bbq_disk_blocked",
   "pass": true,
   "expect": "blocked",
   "detail": "매핑은 통과, 색인에서 막힘: security_exception: current license is non-compliant for [bbq_disk]"
  },
  {
   "check": "es.rrf_blocked",
   "pass": true,
   "expect": "blocked",
   "detail": "security_exception: current license is non-compliant for [Reciprocal Rank Fusion (RRF)]"
  },
  {
   "check": "es.cleanup",
   "pass": true,
   "expect": "ok",
   "detail": []
  },
  {
   "check": "neo4j.version",
   "pass": true,
   "expect": "ok",
   "detail": [
    [
     "Neo4j Kernel",
     "5.26.31",
     "community"
    ]
   ]
  },
  {
   "check": "neo4j.n10s_loaded",
   "pass": true,
   "expect": "ok",
   "detail": "n10s procedures=55"
  },
  {
   "check": "neo4j.n10s_shacl",
   "pass": true,
   "expect": "ok",
   "detail": [
    "n10s.validation.shacl.dropShapes",
    "n10s.validation.shacl.import.fetch",
    "n10s.validation.shacl.import.inline",
    "n10s.validation.shacl.listShapes",
    "n10s.validation.shacl.validate",
    "n10s.validation.shacl.validateSet",
    "n10s.validation.shacl.validateTransaction",
    "n10s.validation.shacl.viewCypher"
   ]
  },
  {
   "check": "neo4j.n10s_graphconfig_init",
   "pass": true,
   "expect": "ok",
   "detail": "ok"
  }
 ],
 "failed": 0
}

```
