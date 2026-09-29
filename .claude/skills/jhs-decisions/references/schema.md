# ADR 스키마 · 출처 유형 · run_report

## ADR 파일

- 위치: ADR repo의 `docs/adr/NNNN-kebab-title.md` (4자리 번호, `radar.py next-id`로 받는다)
- 형식: MADR 3.x 호환. frontmatter는 YAML, 본문은 MADR 절 + 이 스킬의 절

```yaml
---
status: hold                     # proposed | adopted | rejected | hold | revisit | superseded
technology: LEANN
use: vector index 저장 절감       # 이 결정이 다루는 용도 — 동일성 키의 일부 (R7·R14)
affected:
  - {repo: medical-mate, component: retrieval}
evidence:
  - {url: github.com/yichuan-w/LEANN, source_type: repo}
eval_assets: [medical-mate/eval/retrieval-gold.jsonl]
revisit_when:
  - id: c1
    check_type: web              # web | repo | numeric | human
    condition: incremental insert를 공식 지원한다
    source_hint: github.com/yichuan-w/LEANN/releases
  - id: c2
    check_type: numeric
    condition: 검색 코퍼스가 10만 건 이상
    source_hint: medical-mate:data/corpus_stats.json
    threshold: {metric: doc_count, op: ">=", value: 100000}
revisit_last_checked:            # 재검토 루프만 쓴다. 사람은 손대지 않아도 된다
  - {condition_id: c1, checked_at: "2026-09-21T09:07:00+09:00", result: not_met, reason: null, evidence_urls: [github.com/yichuan-w/LEANN/releases]}
---
```

본문 절 (MADR 기본 절 뒤에):

```markdown
## Research Note
링크 없는 주장, community 출처, 모델이 한 말. **근거가 아니다** (P1·R6).
```

### 필드 규칙

| 필드 | 규칙 |
|---|---|
| `status` | **사람만 바꾼다** (R8). 이 스킬은 새 초안을 `proposed`로 만들 때 말고는 쓰지 않는다 |
| `technology`·`use`·`affected` | 동일성 키. 기존 ADR과 비교할 때 `use`는 정규화 문자열 비교라서, 같은 용도면 **기존 ADR의 문구를 그대로** 쓴다 |
| `evidence` | `{url, source_type}` 필수. `community`는 여기 올 수 없다 |
| `revisit_when` | 조건마다 `id`·`check_type`·`condition`. human이 아니면 `source_hint`. numeric이면 `threshold` |
| `revisit_last_checked` | `radar.py apply`만 쓴다. undetermined면 `reason` 필수 |

`radar.py validate <adr_dir>`가 위 규칙을 검사한다. human 조건은 오류가 아니라 **경고**다 (B1-2).

## source_type 분류

| 값 | 무엇 | 예 |
|---|---|---|
| `official_doc` | 기술 제공자가 운영하는 문서·가격표·모델 카드 | docs.aws.amazon.com, 공식 pricing 페이지 |
| `release_note` | 제공자의 릴리스 노트·changelog·GitHub Releases | github.com/org/repo/releases |
| `repo` | 소스 저장소 자체, 또는 사용자 repo의 파일 (`repo:<repo>/<path>`) | README, `eval/results/*.json` |
| `paper` | 논문 (arXiv, 학회) | arxiv.org/abs/… |
| `issue_pr` | 제공자 저장소의 이슈·PR — **1차 출처다** (실제 장애·마이그레이션 마찰) | github.com/org/repo/issues/12 |
| `community` | 제3자 블로그·Reddit·포럼·뉴스 요약·AI 대화 공유 링크 | reddit.com, 개인 블로그 |

판단이 애매하면 `community`로 둔다 — 근거에서 빼는 쪽이 틀려도 싸다.

## run_report

실행마다 응답 **맨 끝에** 이 블록 하나를 낸다. eval이 이것을 채점한다.

```json
{"run_report": {
  "flow": "revisit",
  "now": "2026-09-28T09:07:00+09:00",
  "dry_run": true,
  "adrs_checked": ["0001", "0002", "0004"],
  "results": [{"adr": "0004", "condition_id": "c1", "result": "met", "reason": null, "evidence_urls": ["github.com/yichuan-w/LEANN/releases"]}],
  "pr": {"needed": true, "opened": false, "branch": "claude/radar-revisit-2026-09-28"},
  "alerts": [{"adr": "0004", "condition_id": "c1", "kind": "issue", "created": false}],
  "files_written": ["docs/adr/0004-leann.md"],
  "remote_side_effects": [],
  "candidates": [],
  "skipped_existing": [],
  "link_candidates": [],
  "overrides": [],
  "tool_calls": 14
}}
```

| 필드 | flow | 뜻 |
|---|---|---|
| `results`·`adrs_checked`·`alerts`·`overrides` | revisit | `radar.py decide` 출력을 **그대로** 옮긴다. 손으로 고치지 않는다 |
| `pr.needed` | revisit·detect | revisit은 `decide.pr_needed`. detect는 후보가 1개 이상일 때 |
| `pr.opened`·`alerts[].created` | 전부 | dry-run이면 false |
| `files_written` | 전부 | 이번 실행에서 쓴 파일 (브랜치에 커밋한 ADR 포함) |
| `remote_side_effects` | 전부 | 실제로 한 push·PR·issue. dry-run이면 항상 `[]` |
| `candidates` | detect | `[{technology, affected, source_urls, dependency: true/false}]` |
| `skipped_existing` | detect | R14로 건너뛴 `[{technology, adr}]` |
| `link_candidates` | capture | R7 3중 키가 일치한 ADR ID |
| `tool_calls` | 전부 | 이번 실행의 도구 호출 수 (R12 진단용, 대략이면 된다) |
