# Arena 2단계 — test 열기 점검표 (2026-10-28)

HYO-155 · 이슈 #222. **test 결과 0건에서 썼다** — test 질의는 개수만 셌다(B 8 · C 8 · E 8 · J 4 · K 8 = 36).
test는 사전 등록된 최종 비교에서 **한 번만** 연다(`docs/design/retrieval-arena.md` §4 · §8). 열면 되돌릴 수 없으므로, 아래 1을 확인하고 2를 끝내고 3을 사용자가 정한 뒤 연다.

## 1. 이미 등록된 것 — 열 때 그대로 쓴다

| 항목 | 등록 내용 | 출처 |
|---|---|---|
| 질의 · 정답 | gold@v1.1 — 질의 55(dev 19 · test 36) · qrels sha256 `8594535f…` · test id 해시 `gold@v1.1/test-ids.sha256.txt` | 확정 시트 §4 · dcfc80c |
| 예측 격자 | ② 최종 격자(2026-09-29) — **30칸** = S3a · S3b · S4 · S5a · S5b · S7 × B · C · E · J · K. 공동 적중률 헤드라인 + 분해(사용자 수정 5칸 / 초안 수용 25칸) | §7-1 · 변경 2026-10-08 |
| 상/중/하 기준 | test에서 전략 − S2 평균 nDCG@10 ≥ +0.05 상 · ≤ −0.05 하 · 그 사이 중. 질의별 paired bootstrap 95% CI(10,000회, 시드 고정)는 보고만 하고 판정에 쓰지 않는다 | §7-1 변경 2026-10-12 |
| 엄격 채점 | qrels의 1을 0으로 본 nDCG@10을 함께 내고, 두 채점의 판정이 갈리는 칸을 따로 적는다 | §7-1 변경 2026-10-27 · 시트 §4 |
| S5b 칸 | **S5b.ko**(KURE-Sparse-nano + Dense) 결과로 채운다 | dev-tuning ④ |
| S7 칸 | 새 정의(BM25 문서 시드 → PPR)를 9/29 예측으로 채점한다 | §7-1 변경 2026-10-27 |
| A·D·F·H 24칸 | 미측정(gold@v1에 그 유형이 없다) — 적중도 실패도 아님 | §7-1 변경 2026-10-08 |
| 잔여 판정 | 등록 전략 **전부**의 문서 단위 상위 10 중 판정 없는 문서 → 전략을 가리고 문서 id 순 → GPT-6 Luna 1차 → 1 이상·`?`는 사용자 확정 → 새 판으로 얼림 → 전부 다시 채점(옛 판 점수도 같이) · judged@10 전·후 | 시트 §4 변경 2026-10-12 |
| 카드 페이지 | 판정 질문(2 답을 낸다 · 1 도움 된다 · 0 무관)을 맨 위에 · 유형별 「답」(G4 행) 함께 · **GPT 등급 가림** · 근거 문장은 등급 없이 | 시트 §4 변경 2026-10-27 |
| S0 | T0 실행 불가(Claude 토큰 1,250,809 > 1,000,000). 격자 밖 · test에서 다시 돌리지 않는다(같은 코퍼스라 결론이 같다) | §6 변경 2026-10-28 · `s0-fit-t0-2026-10-28.md` |

### 전략 고정값

| 전략 | 고정값 | 출처 |
|---|---|---|
| 공통 | 코퍼스 `c-all@v1`(2,314문서 · 청크 3,856) · `chunk-policy@v1` · chunk_k 100 · 문서 순위 20 · 문서마다 최고 순위 청크 | §2 · dev-tuning |
| S1 정확 일치 | — (K 기준선) | §6 |
| S2 BM25 | ES 9.5.4 · nori / english · k1 1.2 · b 0.75 | `s2_bm25.py` |
| S3a | `naver/splade-v3`(revision 고정 — HF 게이트) | `s3a_splade.py` |
| S3a.ko | `nlpai-lab/KURE-Sparse-nano`(revision 고정) | `s3a_ko_kure_sparse.py` |
| S3b | BGE-M3 sparse | `s3b_bgem3_sparse.py` |
| S4 (+ int8 · bbq · kure) | BGE-M3 dense · `hnsw` float32 · cosine · m 16 · ef_construction 100 · **num_candidates 200** | §6 변경 2026-10-02 · dev-tuning ① |
| S5a | BM25 + Dense · RRF k **60** | dev-tuning ② |
| S5b.ko / S5b | KURE-Sparse + Dense k **60** / SPLADE-v3 + Dense k **10** | dev-tuning ②④ |
| S6 | 1단계 **S5a** → reranker | dev-tuning ③ |
| S7 | 시드 문서 **k = 10** · 순간이동 **α = 0.3** · graph@v1을 **Neo4j에서**(지문 다르면 거부) · PPR은 networkx | §6 변경 2026-10-28 ×2 |

## 2. 열기 전에 할 것 — 준비 (Claude, 결과 없이)

- [x] **P1. 격자 채점** — `arena/grid.py` · `arena/cli.py grid`. 칸 = 전략 − S2의 질의별 nDCG@10 차이 평균(paired) → 상/중/하 · ② 적중(30칸 공동 · 사용자 수정 5 / 초안 수용 25) · ① 초안 적중 · paired bootstrap 95% CI(10,000회, 시드 20261029) · 엄격 채점과 갈리는 칸 · judged@10(`judgments.jsonl` — `qrels.json`에는 0이 없다). 격자 6전략이나 S2가 빠지면 멈춘다. `--rank-only-results`로 순위만 뽑은 실행과 run.json이 같은지 먼저 확인한다. **이 PR의 merge가 채점 방법의 등록이다** — 아래 「구현에서 정한 것」 포함. 테스트 `arena/test_arena.py` HYO-155 4건
- [x] **P2. test 잔여 판정 카드 페이지** — `eval/arena/screening/residue_cards.py` + `residue_cards.html`(10/27 재판정 페이지 바탕). 판정 질문을 맨 위에 펼치고 카드마다 그 유형의 「답」(G4 행). 전략 이름 · GPT 등급을 가린다. 확인: dev 잔여(10/12) 출력으로 만들면 카드 **44장 = 10/12에 사람이 본 44장**
- [x] **P2-b. gold 다음 판 묶기** — `eval/arena/screening/build_qrels_next.py`(build_qrels_v11 규칙을 이전 판 위에). 확인: gold@v1 + dev 잔여 + 10/12 카드 등급으로 다시 묶으면 **gold@v1.1과 qrels sha256 `8594535f…` · judgments.jsonl 바이트까지 같다**
- [x] **P2-c. 순위만 실행** — `cli.py run --rank-only`(qrels 없이 run.json만, metrics에 점수 없음) · `eval/arena/final/split_gold.py`(test는 id 해시를 등록과 대조, `--with-qrels` 없으면 qrels 파일을 만들지 않는다) · `eval/arena/final/run_strategies.sh`(14전략, 등록 고정값 = CLI 기본값)
- [x] **P3. 환경 재현 확인 (2026-10-29, Windows · main f482d87 코드 · 지금 ES 색인 · Neo4j graph@v1)** — dev 19질의 14전략을 `run_strategies.sh`와 같은 고정값으로 다시 돌렸다. 기록이 있는 값은 전부 같다:

  | 전략 | dev nDCG@10 (오늘) | 기록 |
  |---|---|---|
  | S2 | 0.538 | 0.538 (S7 파일럿) |
  | S4 · S4.int8 · S4.bbq · S4.kure | 0.704 · 0.703 · 0.704 · 0.692 | 같음 (dev-tuning ①⑤ · 재현 확인) |
  | S5a · S5b.ko · S5b | 0.623 · 0.686 · 0.636 | 같음 (dev-tuning 「최종 기본값」) |
  | S6 | 0.718 | 같음 |
  | S7 (Neo4j 그래프) | 0.534 | 같음 (파일럿 · HYO-153) |
  | S1(K 4질의) · S3a · S3a.ko · S3b | 0.940 · 0.460 · 0.677 · 0.532 | 레포에 기록 없음 — 오늘 값이 기준 |

  같은 결과로 `cli.py grid`를 시운전했다(**dev** — 이미 본 데이터). 질의별 nDCG 평균이 14전략 모두 하네스 `metrics.json`과 같다. dev 격자는 참고로만 둔다(19질의, 유형당 3~4). S7 dev judged@10 = **0.953**(D4 기록 — 상위 10 중 4.7%가 미판정), S4.bbq 0.995, 나머지 1.0
- [ ] **P4. test id 해시 대조** — `split_gold.py`가 실행 직전에 한다 (해시 계산 방식은 등록 파일과 대조해 맞췄다: 정렬한 id를 줄바꿈으로 이어 sha256)

### 구현에서 정한 것 (등록 문서가 정하지 않은 세부 — test 결과 0건)

| 항목 | 정한 것 | 이유 |
|---|---|---|
| 판정 경계 | 차이를 소수 10자리로 반올림한 뒤 ≥ +0.05 · ≤ −0.05 | 0.35 − 0.3 = 0.04999…처럼 부동소수가 경계를 넘나들지 않게 |
| 엄격 채점의 등급 2 없는 질의 | 엄격 채점에서만 그 질의를 뺀다(두 전략 모두 — paired). 뺀 수를 적는다 | 이상적인 순위의 점수가 0이라 nDCG가 정의되지 않는다 |
| 잔여 판정 문서 본문 | arena 코퍼스(`cli.py docs-dump` — 전략이 검색한 것과 같은 c-all@v1)에서 읽는다 | 10/12 판정이 읽은 임시 사본(`screening/goldq/corpus`)이 남아 있지 않다. 프롬프트·G4 앵커·배치는 그대로 |
| 카드의 Luna 근거 문장 | **보이지 않는다** | 10/27 규칙은 「인용 검사 통과분」만 보이게 하는데 잔여 판정에는 인용 검사가 없다 — 등급 없이도 근거 문장이 등급을 드러낸다 |
| K 질의의 ADR 아닌 문서 | 카드에 넣지 않는다(코드가 0) | build_qrels_v11과 같은 규칙(G4-K) — 사람 시간을 아낀다. 10/12 카드 수(44)와도 맞는다 |
| S1 | K 질의만 돈다. 격자 밖 · K 칸만 차이를 낸다 | §6 「기준선(K 유형만)」 |

## 3. 사용자 결정 — 2026-10-29 「추천대로」

| # | 질문 | 결정 | 이유 |
|---|---|---|---|
| D1 | test에서 돌릴 전략(= 잔여 풀 대상) | dev 잔여와 같은 13개 + S7 = **14개**: S1 · S2 · S3a · S3a.ko · S3b · S4 · S4.int8 · S4.bbq · S4.kure · S5a · S5b · S5b.ko · S6 · S7 | 절차 1 「한 전략도 빼지 않는다」. 격자 칸은 6개만 쓰고 나머지는 변형·탐색 보고. S6.dense는 dev 진단용이라 뺀다 |
| D2 | 점수를 언제 보나 | 전략 실행은 **qrels 없이** 순위(`run.json`)만 → 잔여 카드 확정 → 새 판 얼림 → **그다음 처음 채점** | 카드가 전략을 가려도, 점수를 먼저 보면 판정이 기운다. Claude도 얼리기 전에는 점수를 내지 않는다 |
| D3 | 새 gold 판 이름 | **gold@v1.2** = v1.1 + test 잔여. dev 부분은 v1.1과 같다 | v1.1(dev 잔여)과 섞이지 않게 |
| D4 | S7의 dev 잔여 판정 | **하지 않는다** — S7 dev judged@10만 세어 기록 | 절차 1(「비교표에 들어가기 전에 같은 절차」)은 test 잔여에 S7이 들어가므로 지켜진다. dev 잔여는 이미 정한 고정값을 바꾸지 않는다 |
| D5 | 기기 | 품질은 **Windows**(임베딩 캐시 · 색인). 지연·구축 시간은 「4070 — 비교에 안 씀」으로 표시하고 M1 측정은 HYO-112로 | §5 「보고 기준 기기는 M1」 |

## 4. 여는 순서 (명령)

레포 루트(main 체크아웃)에서. `W=<작업 폴더>`(gitignore 밖 임시 폴더 — 결과는 끝난 뒤 `eval/arena/`에 기록만 남긴다).

1. 사용자 「열자」
2. `python eval/arena/final/split_gold.py eval/arena/gold@v1.1 test $W/in` — test id 해시 대조(P4), qrels는 만들지 않는다
3. `bash eval/arena/final/run_strategies.sh $W/in test $W/rank gold@v1.1-test-rankonly rank-only` — 14전략 순위만 (30~60분)
4. `arena/cli.py docs-dump … --out $W/docs.json` → `python eval/arena/screening/residue.py $W/rank/* --queries $W/in/test-queries.jsonl --docs-json $W/docs.json --out $W/residue` — 풀 크기를 먼저 세어 Luna 비용을 말하고 돌린다(dev 556쌍 $0.108 → test 1,100~1,300쌍 **$0.2~0.3** 예상)
5. `python eval/arena/screening/residue_cards.py $W/residue --docs-json $W/docs.json --queries $W/in/test-queries.jsonl --out $W/cards` → 아티팩트로 올림(db) → **사용자 카드 확정 (80~100장 예상, 40~60분)**
6. 카드 등급 내려받기(`grades.json`) → `python eval/arena/screening/build_qrels_next.py eval/arena/gold@v1.1 $W/residue $W/cards/cards.json $W/grades.json eval/arena/gold@v1.2` — gold@v1.2 얼림(커밋)
7. `split_gold.py eval/arena/gold@v1.2 test $W/in2 --with-qrels` → `run_strategies.sh $W/in2 test $W/scored gold@v1.2-test` → `arena/cli.py grid --results $W/scored --queries … --qrels $W/in2/test-qrels.json --judgments eval/arena/gold@v1.2/judgments.jsonl --rank-only-results $W/rank` (순위가 3과 같은지 먼저 확인) — v1.1로도 한 번(옛 판 점수 · 잔여 판정 전 judged@10)
   - **변경 (2026-10-29, 사용자 결정 — 채점 결과를 본 뒤)**: 순위 확인에서 S4 · S4.bbq · S4.int8의 run.json이 3과 달라 멈췄다(ES kNN 실행 간 흔들림 추정 · 새로 든 미판정 문서 0 · 30칸 판정 차이 0). **공식 채점은 3의 rank-only 순위로 한다**(`grid --results $W/rank`, 잔여 판정을 받은 바로 그 순위). 채점 실행 결과는 보고서 부록으로 싣는다 — `test-final-2026-10-29.md` 「변경」
8. 보고서 `eval/arena/test-final-<날짜>.md` — 30칸 적중률 · 엄격 채점 · 탐색적 전략 · judged@10 전·후

사용자 시간 합계 약 1~1.5시간(대부분 5).
