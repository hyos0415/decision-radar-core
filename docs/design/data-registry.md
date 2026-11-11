# 데이터 등록부 설계 (HYO-180)

> 초안 v0 · 2026-11-11 · Linear HYO-180 · GitHub #282
> 관련: `doc-pipeline.md` §4-6(공식 보안 채널) · §4-7(출처 건강 검사) · `importance-policy.md`(등급 파일) · `hitl-state.md`(ADR 파일이 이긴다) · `retrieval-arena.md` S7 · S8(HYO-38 — 경계는 §10)
> 정해진 것 (사용자, 2026-11-11): 목적 · 형식 · 첫 조각 회귀 목표 · §5-1 ID 규칙 · §5-3 출처 계층 · §5-5 정규화 · 빌드 의존성은 공용 `.venv`. **§4 조건 1 · 2 · 4 · 5와 §5-2 · 5-4는 Claude 초안이고 사용자가 확정한다.** 확정 전 칸은 「미정」이다.

## 1. 무엇을 푸는가

같은 대상(기술 · 출처 · component · 결정)이 파일마다 다른 이름으로 적혀 있다. 이름을 맞추는 코드가 기능마다 새로 생기고, 이름이 어긋나서 난 고장이 이미 여럿이다. 숫자는 부록 「이름 지도」에 있다. 요약하면 이렇다.

- 기술 하나가 많게는 다섯 가지 이름으로 적힌다. `llama.cpp`(CLAUDE.md · 항목) · `llama-cpp`(`tiers.json` 키) · `llama-server`(Arena 별칭) · `ggml-org/llama.cpp`(`pool.yaml`) · `tech:llama-cpp`(`nodes@v1`)
- 이름을 맞추는 규칙이 6가지이고 코드 8곳에 흩어져 있다. 공백+소문자 · PEP 503 · 영숫자만 · 토큰 교집합 · 단어 경계 · 대소문자 구분 별칭
- **관계가 설정값이나 상수로 숨어 있다.** 「Node.js는 Electron 안에서 돈다」는 `pool.yaml` `dependency: electron`에, 「Guardrails는 Bedrock의 일부」는 `stack-extra.json` 주석에, 「python · nodejs · osv는 security에 속한다」는 아무 데도 없었다(HYO-179)
- 고장 10건 넘게 근거가 있다(부록 표)

목적은 **데이터 관리와 확장성**이다. 새 출처 · 기술 · 관계를 한 곳에 덧붙이면 쓰는 곳들이 코드를 고치지 않고 따라오게 한다. 검색 강화(Arena S8, HYO-38)가 목적이 아니다. 용량 문제도 아니다(주당 약 5MB).

## 2. 범위

| 넣는 것 (사람이 정한다) | 넣지 않는 것 |
|---|---|
| 정체성 — 대상마다 바뀌지 않는 ID 하나 | **기계가 정하는 값**: `tiers.json` 등급(매주 Sonnet) · 우리 버전(`basis.json`) · manifest 쓰임새. 이 파일들의 키만 등록부 ID를 따른다 (조건 2) |
| 이름 — 고정 이름 하나 · 별칭 여럿 (옛 이름 포함) | **결정 내용**: ADR 파일이 원본이다(`hitl-state.md`). 등록부는 ADR의 `technology` · `affected`가 등록부 이름으로 찾아지는지 **검사만** 한다 |
| 사람이 정한 관계 — 상하 · 「안에서 돈다」 · 「릴리스가 나오는 곳」 · 벤더 | **주소(URL)**: `vendors.yaml` 경로 · `replaces`는 그대로 둔다(자주 바뀌고, 메인 세션의 운영 몫) |
| | **얼린 주 · Arena `nodes@v1` · `nodes@v2`**: 고치지 않는다(규율 7). 옛 이름은 별칭이 받는다 |

## 3. 네 층의 역할

| 층 | 맡는 것 | 이 등록부에서 예 | 맡지 않는 것 |
|---|---|---|---|
| **RDF** (Turtle) | 원본. 대상과 관계를 트리플로 적는다. 사람이 고치는 파일은 이것뿐이다 | `<llama.cpp> <릴리스가 나오는 곳> "ggml-org/llama.cpp"` | — |
| **SKOS** | 고정 이름(`prefLabel`) · 별칭(`altLabel`, 숨은 별칭 `hiddenLabel`) · 상하(`broader`/`narrower`) | prefLabel `llama.cpp` · altLabel `llama-server` · `python`의 altLabel `CPython` | 클래스 구분 · 검증 |
| **OWL** | 스키마. 클래스 · 속성 · domain/range를 정의한다. 추론은 **빌드 때만** 돌린다 | `runsInside`의 domain과 range는 Technology다 | **검증을 하지 않는다** (아래) |
| **SHACL** | 등록부 자체 검사(닫힌 세계). 빌드가 실패하는 조건이다 | 정규화 이름이 두 대상에 겹치면 실패 · 항목 출처는 수집 단위 하나 아래 | 실행 중 검사 (실행 경로는 `registry.json`만 읽는다) |

**OWL의 domain/range는 검증이 아니라 추론이다.** OWL은 열린 세계를 가정한다. 「모르는 것은 거짓이 아니라 아직 모르는 것」이라는 뜻이다. 그래서 `runsInside`의 range가 Technology라고 적어 두고 오타 낸 ID(`tech:electorn`)를 대상으로 쓰면, 오류가 나지 않는다. 대신 「`tech:electorn`도 Technology다」라는 새 사실이 추론되고 빌드가 조용히 지나간다. 「대상이 등록된 Technology여야 한다」를 실패로 만드는 것은 SHACL(`sh:class` · `sh:nodeKind`)이다. 두 층을 다 두는 이유가 이것이다. OWL은 「무엇이 있나」를 정의해 추론에 쓰고, SHACL은 「이 파일이 규칙대로 적혔나」를 확인한다.

추론이 주는 것 (빌드 때 owlrl로 돌린 뒤 JSON에 펼쳐 넣는다):
- 하위 클래스를 상위로 올린다. 예: Runtime이 Technology의 하위 클래스면 모든 Runtime이 Technology 목록에도 든다
- `skos:broader`를 전이로 닫는다. SKOS 명세에서 `broader`는 전이 속성 `broaderTransitive`의 하위 속성이다. rdflib은 SKOS 공리를 갖고 있지 않아서, 필요한 공리 몇 줄을 `ontology.ttl`에 직접 둔다
- 역관계(`broader` ↔ `narrower`, `collectedBy` → 수집 단위의 항목 출처 목록). 사람은 한쪽만 적으면 된다

## 4. 조건 (Claude 제안 — 사용자 확정)

| # | 조건 | 왜 | 확정 |
|---|---|---|---|
| 1 | **실행 경로에는 rdflib을 넣지 않는다.** Turtle은 빌드 때 `registry.json` 조회표로 바꾼다. 수집기(표준 라이브러리 + PyYAML) · 파이프라인 · 스킬 `radar.py`(ADR repo에 배포하는 단위) · 앱은 JSON만 읽는다. rdflib · pySHACL · owlrl은 빌드 · 테스트 의존성이다(`registry/requirements.txt`, 공용 `.venv`에 설치 — 사용자 11/11). CLAUDE.md 스택 절도 같이 고친다 | 수집기는 표준 라이브러리 + PyYAML만 쓴다는 원칙(`collector/README.md`). 스킬은 다른 repo에 복사돼 돈다 | 미정 |
| 2 | **사람이 정하는 것과 기계가 정하는 것을 섞지 않는다.** 등록부 = 정체성 · 관계. `tiers.json` 등급 · 쓰임새 · 버전은 기계 파일에 그대로 두고 키만 등록부 ID를 쓴다 | 매주 바뀌는 값이 사람이 검토하는 파일에 섞이면 diff가 소음이 된다. 「누가 정했나」가 흐려지면 사람 수정(overrides)과 자동 분류가 다시 엉킨다 | 미정 |
| 3 | **첫 조각 = 출처 계층.** `collector/health.py`가 등록부로 출처를 묶어 세고, 등록부에 없는 항목 source가 나오면 알린다 (§9 1번). 그다음 기술 이름 — 소비처마다 PR 하나 | 변경 (2026-11-11, 사용자): 처음 목표는 「W46 다시 검사 때 HYO-179 거짓 경보가 사라지면 통과」였다. HYO-179는 PR #281이 `QUIET_SOURCES`로 먼저 막아서 목표를 바꿨다 | **확정** (11/11) |
| 4 | **얼린 주는 고치지 않는다.** 옛 이름은 별칭이 받는다. Arena `nodes@v1` · `v2`는 등록된 평가 자료라 옮기지 않는다. 필요하면 바깥 짝 표(등록부 ID ↔ nodes id)를 둔다 | 규율 7 | 미정 |
| 5 | **맥 수집 이전(11/12 AWS · 11/17 주간)보다 뒤.** 이 이슈가 그 마감을 막지 않는다. `collector/`는 PR #281 merge 뒤 main 기준으로 고친다 | 운영이 먼저다 | 미정 |

## 5. 사용자가 고를 것 — 클래스 계층과 속성 이름

첫 조각(출처)에 필요한 것은 5-1 · 5-3 · 5-5다. 5-2 · 5-4는 기술 이름 단계(§9 2번부터) 전까지 정하면 된다.

### 5-1. ID 규칙 — **가안으로 확정 (사용자 2026-11-11)**

종류 접두 + 정규화 이름. **ID는 한 번 정하면 바꾸지 않는다.** 이름이 바뀌면 prefLabel만 바꾸고 옛 이름은 altLabel로 내린다.

| 안 | 예 | 좋은 점 | 반론 |
|---|---|---|---|
| 가. 종류 접두 + 정규화 이름 | `tech:llama-cpp` · `source:security` · `component:decision-radar/collector` · `adr:0011` | 사람이 읽을 수 있다. `tiers.json` 키 · `nodes@v1` id · 레포 안 `adr:0006` 표기(3,667회)와 거의 같아 옮기기 쉽다 | 이름이 바뀌어도(react 옛 repo처럼) ID는 옛 이름을 계속 지닌다. 「ID는 한 번 정하면 안 바꾸고 prefLabel만 바꾼다」는 규칙이 필요하다 |
| 나. 의미 없는 번호 | `tech:T0042` | 이름이 바뀌어도 안전하다 | `tiers.json` · `trace.jsonl` · 로그를 읽을 때마다 찾아봐야 한다. 이 프로젝트는 사람이 원자료를 자주 읽는다 |

네임스페이스 URI는 열리지 않아도 된다(private repo). 제안: `https://github.com/hyos0415/decision-radar/registry#`.

### 5-2. 기술 클래스 계층 (미정)

| 안 | 모양 | 다루는 고장 | 반론 |
|---|---|---|---|
| 가. 평평하게 | `Technology` 하나 + SKOS 상하 | Node 별칭 · CPython · Guardrails ⊂ Bedrock (이름 · 상하만으로 풀림) | `alarm.py` `RUNTIME_TERMS`(「우리가 그 위에서 돈다」)를 가를 방법이 없다. 결국 속성이나 목록으로 다시 적는다 |
| 나. 종류별 하위 클래스 | Technology ⊃ Package · Runtime · ContainerImage · HostedService · Model. 한 기술이 여러 클래스에 들 수 있다(Electron = Package + Runtime) | 위 + `RUNTIME_TERMS` = 「Runtime인 것의 이름」 | 종류가 등급(L0 런타임 · L2 모델 · L5 공급자 SDK …)과 비슷하다. 사람이 정하는 종류와 기계가 정하는 등급이 다시 섞일 위험이 있다(조건 2) — 「종류는 정체성, 등급은 우리에게 얼마나 중요한가」로 선을 그어야 한다 |
| 다. 개념과 배포물을 나눈다 | Technology(개념: Elasticsearch) + Artifact(PyPI `elasticsearch` 클라이언트 · docker `elasticsearch-nori:9.5.4` · GitHub `elastic/elasticsearch`), 관계 `distributedAs` | 위 + 10/2 고장(패키지 이름 = 서버 이름) · 10/8(react 옛 repo) · 이미지 키 3개 · `python` PyPI 404를 정면으로 다룬다 | 노드가 두 배다. `tiers.json` 키가 개념인지 배포물인지 정해야 한다. 첫 판에는 과할 수 있다 |

### 5-3. 출처 계층 — **나안으로 확정 (사용자 2026-11-11)**

| 안 | 모양 | 좋은 점 | 반론 |
|---|---|---|---|
| 가. SKOS 개념 체계 하나 | 수집 단위(reddit · hn · github · huggingface · vendor · security)가 맨 위, 항목 source는 `broader`로 그 아래(security ← osv · python · nodejs, reddit ← reddit). 통로(`channels`)는 넣지 않는다 | 첫 조각에 딱 필요한 만큼이다. 새 런타임 공지 = Turtle 한 줄 | 통로 접두(`vendor:` · `runtime-security:` …)와 벤더 이름의 관계는 여전히 코드에 남는다 |
| 나. OWL 클래스 + 통로까지 | `CollectionUnit` · `ItemSource` · `Channel` 클래스, `collectedBy` · `viaChannel` 속성. 통로 11종도 등록한다 | 레인 나누기(`lanes.py`)의 통로 규칙까지 등록부로 옮길 수 있다 | 통로는 `pool.yaml` 설정에서 생긴다(`r/<서브>` · `task:<태스크>`). 등록부와 두 곳에서 관리하게 된다 |
| 다. 등록부 없이 이름을 맞춘다 | 수집기가 항목 `source`를 `security`로 쓰고 런타임 이름은 `meta`로 옮긴다 | HYO-179의 뿌리를 코드 한 줄로 없앤다 | 얼린 W46은 `python` · `nodejs`로 남아 결국 별칭이 필요하다. 다음에 새 출처를 붙일 때 같은 일이 되풀이된다(확장성이 목적) |

**나안의 모양 (Claude 초안 — 클래스 · 속성 이름은 PR 리뷰에서 바꿀 수 있다).** 얼린 W41~W46 항목을 센 결과에 맞췄다.

| 클래스 | 무엇 | 개체 (ID) |
|---|---|---|
| `CollectionUnit` (수집 단위) | 수집기가 한 번에 돌리고 `run.json`에 실패를 남기는 단위. `health.py` `SOURCES` · `pool.yaml` 절과 같다 | `source:reddit` · `source:hn` · `source:github` · `source:huggingface` · `source:vendor` · `source:security` (6) |
| `ItemSource` (항목 출처) | 항목의 `source` 값 | 위 다섯(reddit · hn · github · huggingface · vendor)은 **같은 개체가 두 클래스에 다 든다**. 그 밖에 `source:osv` · `source:python` · `source:nodejs`는 ItemSource만이다 (모두 8) |
| `Channel` (통로) | 항목의 `channels` 값의 종류. 접두(`channelPrefix`)로 알아본다 | `channel:dependency-releases` · `channel:new-repos` · `channel:slow-growers` · `channel:stories` · `channel:trending` · `channel:task` · `channel:daily-papers` · `channel:subreddit`(`r/`) · `channel:vendor`(`vendor:`) · `channel:runtime-security`(`runtime-security:`) · `channel:security-advisories` (11) |

| 속성 | 주어 → 대상 | 왜 |
|---|---|---|
| `collectedBy` | ItemSource → CollectionUnit | python · nodejs · osv → security (HYO-179). reddit → reddit처럼 자기 자신도 적는다(빠짐없이 검사하려고) |
| `channelOf` | Channel → CollectionUnit | 통로마다 수집 단위가 하나로 정해진다(W41~W46 측정: 예외 없음). 항목은 합쳐지면 두 단위의 통로를 함께 가질 수 있어서(의존성 릴리스 + `vendor:` 51건), 항목 → 단위는 `source`로만 정한다 |
| `channelPrefix` | Channel → 문자열 | `r/` · `vendor:` · `runtime-security:` 처럼 뒤에 값이 붙는 통로 |

- 통로 뒤에 붙는 값(서브레딧 · HF 태스크 · 벤더)은 등록하지 않는다. 그 값을 정하는 곳은 `pool.yaml` · `vendors.yaml`이고, 두 곳에서 관리하지 않게 하려는 것이다. 예외로 `runtime-security:<이름>`의 이름은 ItemSource 이름과 같아야 한다(검사)
- 속성을 둘로 나눈 이유: `collectedBy` 하나로 ItemSource와 Channel 둘 다에 쓰고 domain을 둘 다 적으면, OWL은 이를 **교집합**으로 읽는다. 그러면 통로 하나에 `collectedBy`를 다는 순간 「그 통로는 ItemSource이기도 하다」가 추론된다. §3에서 말한 「domain은 추론」의 실제 예다
- 레인 나누기 규칙(`lanes.py` — 어느 통로가 레인 A인가)을 Channel 속성으로 옮기는 것은 뒤로 미룬다. 규칙(정책)이지 정체성이 아니라서, 옮길지부터 따로 정한다

### 5-4. 관계 속성 이름 (미정)

지금 설정값이나 상수에 숨어 있는 관계를 옮길 후보다. 이름은 사용자가 바꿔도 된다. 영어 camelCase를 제안하는 이유는 JSON 키 · 코드에서 그대로 쓰기 때문이다.

| 관계 | 지금 어디 | 후보 속성 | 대안 · 반론 |
|---|---|---|---|
| Node.js는 Electron 안에서 돈다 | `pool.yaml` `nodejs.dependency: electron` | `runsInside` | — |
| Guardrails는 Bedrock의 일부 | `stack-extra.json` 주석 | `partOf` | `skos:broader`도 되지만, SKOS broader는 「종류」인지 「부분」인지 가리지 않는다. 관문은 「부분」을 하나로 합쳐 판정해야 한다 |
| 릴리스가 나오는 repo | `pool.yaml` `dependency_repos` | `releaseRepo` | 5-2 다안이면 Artifact로 간다 |
| 벤더 | `vendors.yaml` match 단어 | `vendor` (Technology → Vendor) | match 단어는 제목 검색용이라 남는다 |
| 항목 출처 → 수집 단위 | 없음 (HYO-179) | `collectedBy` (5-3에서 정함) | — |
| 대소문자를 가리는 별칭 (`Node`) | `alarm.py` `CASED_ALIASES` | `casedLabel` (`skos:hiddenLabel`의 하위 속성) | 정규화가 대소문자를 지우므로 따로 표시해야 한다 (5-5) |

### 5-5. 정규화 하나 — **제안대로 확정 (사용자 2026-11-11)**

등록부가 주는 조회는 「(종류, 정규화한 이름) → ID」 하나다. **종류(클래스) 안에서만 찾는다.** `python`은 항목 출처이면서 기술이고, `vendor`는 수집 단위이면서 통로 이름이다. 종류를 묻지 않고 찾으면 둘이 부딪힌다. 쓰는 쪽은 언제나 자기가 무엇을 찾는지 안다(건강 검사는 출처, 경보 그물은 기술). 정규화 규칙은 하나만 둔다. 제목에서 이름을 찾는 방식(단어 경계 정규식 등)은 소비처마다 남는다. 등록부는 「무엇을 찾을지」(이름 목록)를 주고, 「어떻게 찾을지」는 정하지 않는다.

- 제안: **PEP 503 + 공백** — 소문자로 바꾸고 `[-_.\s]+`를 `-`로. `Amazon Bedrock` → `amazon-bedrock` · `llama.cpp` → `llama-cpp` · `Node.js` → `node-js`. 지금 `signals.norm`(PEP 503)에 공백 하나만 더한 것이라 `tiers.json` 키 97/99와 그대로 같다. 다른 둘은 공백이 든 `amazon bedrock` · `amazon bedrock guardrails`다
- 반론: 대소문자를 지우면 `Node`가 「Kubernetes node」에도 걸린다. 그래서 `casedLabel`은 조회표에 넣지 않고, 제목 검색용 목록에만 대소문자를 살려서 준다
- 검사: 같은 종류 안에서 정규화 이름이 두 대상에 겹치면 빌드 실패(SHACL-SPARQL — `LCASE` · `REPLACE`로 같은 규칙). 파이썬 정규화와 SPARQL 정규화가 모든 이름에서 같은 결과를 내는지는 테스트로 대조한다(`s1_match.norm` ↔ `radar.py norm` 대조와 같은 방식)

## 6. 파일 배치 (제안)

```
registry/
  README.md           사람용: 새 출처 · 기술 · 관계 덧붙이는 법
  ontology.ttl        OWL 스키마 — 클래스 · 속성 · domain/range · 쓰는 SKOS 공리 몇 줄
  sources.ttl         출처 (첫 조각)
  technologies.ttl    기술 (§9 2번부터)
  components.ttl      component (뒤)
  shapes.ttl          SHACL 모양
  build.py            Turtle → 추론(owlrl) → 검사(pySHACL) → registry.json   [--check]
  lookup.py           registry.json 읽기 + 정규화 함수 하나 (표준 라이브러리만 — 수집기 · 파이프라인이 import)
  registry.json       빌드 산출. git에 넣는다 — 실행 경로가 빌드 의존성 없이 읽게
  requirements.txt    rdflib · pyshacl · owlrl (빌드 · 테스트만)
  tests/test_registry.py
```

- 맨 위 폴더로 두는 이유: 수집기 · 파이프라인 · 스킬 · 앱이 모두 읽는다. 어느 한 곳 아래에 두면 다른 곳이 그 안을 import하게 된다
- `registry.json`은 git에 넣고 CI가 다시 만들어 비교한다. `desktop/openapi.json`(`export_openapi.py --check`)과 같은 방식이다
- 스킬은 ADR repo에 복사돼 돈다. 스킬이 등록부를 읽게 되면(§9 5번) `deploy_skill.py`가 `registry.json`을 스킬 폴더에 같이 복사한다. 그때까지 스킬은 그대로다

## 7. 빌드 · 검사

```bash
pip install -r registry/requirements.txt     # 빌드 기기 · CI만 (공용 .venv에 넣기 전에 사용자 확인)
python registry/build.py                     # registry.json 다시 만들기
python registry/build.py --check             # Turtle과 registry.json이 어긋나거나 SHACL이 실패하면 0 아님 (CI)
python registry/tests/test_registry.py
```

빌드 순서: Turtle 읽기 → owlrl 추론(RDFS + 위 SKOS 공리) → pySHACL 검사 → JSON. 출력은 키를 정렬하고 시각을 넣지 않는다. 같은 Turtle이면 바이트까지 같은 JSON이 나와야 diff가 의미를 갖는다. 원본 Turtle들의 sha256을 JSON에 적는다.

**실패로 막는 것** (SHACL, 등록부 안):
- 같은 종류 안에서 정규화 이름이 두 대상에 겹친다 — 이 등록부가 지키는 핵심이다. SKOS 명세의 무결성 조건은 개념 하나 안의 prefLabel 중복만 막고, 개념 사이의 겹침은 막지 않아서 직접 더한다
- prefLabel이 없거나 둘 이상이다
- ItemSource의 `collectedBy`, Channel의 `channelOf`가 없거나 둘 이상이다. 또는 대상이 CollectionUnit이 아니다
- `channelPrefix`가 두 통로에 겹친다
- 관계의 대상이 등록되지 않은 ID다 (§3 — OWL로는 못 잡는다)

**보고만 하는 것** (경고 — 다른 repo나 기계 파일이라 빌드를 막지 않는다): ADR `technology` · `affected`, 대상 repo CLAUDE.md 의존성 이름, `tiers.json` 키 가운데 등록부로 찾아지지 않는 것의 수와 목록.

## 8. `registry.json` 모양 (첫 판 예시 — §5 결정 뒤 고정)

```json
{
  "version": 1,
  "sources_sha256": {"ontology.ttl": "…", "sources.ttl": "…"},
  "normalize": "lower; [-_.\\s]+ -> -",
  "names": {
    "CollectionUnit": {"security": "source:security", "reddit": "source:reddit"},
    "ItemSource":     {"python": "source:python", "osv": "source:osv", "reddit": "source:reddit"},
    "Channel":        {"stories": "channel:stories", "vendor": "channel:vendor"}
  },
  "objects": {
    "source:security": {"label": "security", "types": ["CollectionUnit"], "items": ["source:nodejs", "source:osv", "source:python"],
                        "channels": ["channel:runtime-security", "channel:security-advisories"]},
    "source:python":   {"label": "python", "types": ["ItemSource"], "collectedBy": "source:security"},
    "source:reddit":   {"label": "reddit", "types": ["CollectionUnit", "ItemSource"], "collectedBy": "source:reddit", "items": ["source:reddit"], "channels": ["channel:subreddit"]},
    "channel:subreddit": {"label": "subreddit", "types": ["Channel"], "channelOf": "source:reddit", "prefix": "r/"}
  }
}
```

`items` · `channels`는 사람이 적지 않는다. `collectedBy` · `channelOf`의 역관계를 빌드가 추론해서 채운다.

`lookup.py`가 줄 것: `resolve(kind, name) -> id | None` · `collection_of_source(name) -> 수집 단위 id | None` · `channel_kind(channel) -> id | None`(접두로 찾는다) · `labels(id, cased=False)`.

## 9. 소비처를 옮기는 순서 (PR마다 하나)

| # | 소비처 | 지금 | 옮긴 뒤 | 회귀 |
|---|---|---|---|---|
| 1 | `collector/health.py` | `SOURCES` 상수 6개. `by_source`를 항목 source 이름 그대로 센다 | 수집 단위를 등록부에서 읽고, 항목 source를 `collectedBy`로 묶어 센다. **등록부에 없는 항목 source나 통로 종류가 나오면 알린다**(새 규칙 「미등록」 — doc-pipeline §4-7에 `변경` 줄). `QUIET_SOURCES`(#281)는 그대로 둔다 | W46 다시 검사: security 4건(python 3 · nodejs 1) · `checked` 13 → 14 · 페이지 경보 4건 그대로 · security 경보 없음 · 미등록 0. W41~W45도 미등록 0. 가짜 주: 등록부에 하위 출처를 하나 더하면 코드 수정 없이 security로 묶이고, 등록부에 없는 source · 통로는 경보가 난다 |
| 2 | `pipeline/alarm.py` | `RUNTIME_TERMS` 10 · `CASED_ALIASES` 1 · `COMMON_WORDS` 1 | 등록부의 런타임 이름 · 별칭 · `casedLabel` | W44 「RCE in Node, Python」이 걸리고 W43 「… requests」는 안 걸린다 (지금 테스트 그대로) |
| 3 | `collector/pool.yaml` `dependency_repos` · `security.runtimes[].dependency` | 관계가 설정값에 있다 | 등록부 `releaseRepo` · `runsInside` | 수집기 오프라인 테스트 · 얼린 W46 보안 항목의 `meta.dependency`가 같다 |
| 4 | `tiers.json` 키 · 관문 `resolve_technologies` | PEP 503 이름 | 등록부 ID. 기계 값(등급 · 쓰임새)은 그대로 둔다. radar-snapshots의 `tiers.json` 키를 바꾸는 방법은 이 단계에서 따로 정한다 | 얼린 W46 관문 trace가 같다. Gemini 고장은 메인 세션이 먼저 고친다(11/11 넘김) |
| 5 | ADR `technology` · 스킬 `radar.py` | 자유 문구 | 빌드 보고 → (뒤) 스킬 배포에 `registry.json` | `test_radar.py` 38/38 |

옮기지 않는 것: `vendors.yaml` 주소 · `replaces` · `nodes@v1` · `nodes@v2` · 얼린 주.

## 10. HYO-38(S8 온톨로지 계층)과의 경계

- S8은 Arena 그래프(`graph@v1`) 위에 온톨로지 계층을 얹는 **검색 전략 실험**이다. 등록된 평가 자료(`nodes@v1`/`v2` · gold)를 쓴다
- 이 등록부는 **운영 데이터의 이름 원본**이다. 수집 · 관문 · 건강 검사 · 스킬이 쓴다
- S8이 나중에 이 등록부의 SKOS 계층을 쓰고 싶으면, 그것은 S8 등록 절차(결과를 보기 전)로 정한다. 이 등록부는 Arena 파일을 고치지 않는다

## 11. 열린 질문

| 질문 | 누가 |
|---|---|
| §4 조건 1 · 2 · 4 · 5 확정 | 사용자 (이 PR 리뷰) |
| §5-3 클래스 · 속성 이름(`CollectionUnit` · `ItemSource` · `Channel` · `collectedBy` · `channelOf` · `channelPrefix`) | 사용자 (이 PR 리뷰) |
| §5-2 기술 클래스 계층 · §5-4 관계 속성 이름 | 사용자 · §9 2번 전 |
| 레인 나누기 규칙(어느 통로가 레인 A인가)을 Channel 속성으로 옮길지 | 사용자 · 뒤 |
| 팀원 스택(수집 전용 대상)에만 있는 기술 14개를 등록부에 넣을지. 지금 관문은 `other-stack`으로 따로 다룬다 | 사용자 · §9 4번 때 |
| component 이름: ADR `affected`의 decision-radar 쪽 이름(retrieval-arena · runtime · ui · docs · eval)을 스택 표 이름(arena · product · desktop …)의 별칭으로 볼지. 같은 대상인지는 사람이 판단한다 | 사용자 · component 단계 때 |
| `tiers.json`에 남은 `amazon bedrock guardrails` 키(10/15 분류 — `stack-extra.json`은 Guardrails를 따로 두지 않기로 했다). ADR 0002 · 0003은 지금 이 키로만 찾아진다 | 사용자 · §9 4번 때 |

## 부록: 이름 지도 (2026-11-11, 판단 없이 센 것)

W41~W46 · `../radar-snapshots` · `../adr` · 대상 repo 4곳 CLAUDE.md 기준, 읽기만 했다. 「찾아짐」은 그 이름을 PEP 503으로 정규화했을 때 `tiers.json` 키로 찾아지는지다.

**기술**

| 어디 | 개수 | 이름 모양 | 찾아짐 |
|---|---|---|---|
| `tiers.json` 키 | 99 (overrides 44 · retired 1) | `llama-cpp` · `amazon bedrock` · `neo4j:5-26-31` | 기준 |
| `tiers.json` `technology` 값 | 99 (12개가 키와 다름) | `llama.cpp` · `huggingface_hub` | 12/12 |
| CLAUDE.md 의존성 표 (4 repo) | 이름 62 (37행) | `Electron` · `vLLM` · `shadcn/ui` | 59/62 |
| CLAUDE.md 외부 모델 · 서비스 표 | 28행 | 문장 그대로 | 0/28 (`stack-extra.json` 5개가 손으로 대신) |
| `pool.yaml` `dependency_repos` · `runtimes.dependency` | 6 · 2 | 패키지 이름 | 6/6 · 2/2 |
| `vendors.yaml` match | 15 (벤더 9) | `claude` · `gpt` · `google-genai` | 7/15 |
| `alarm.py` `RUNTIME_TERMS` | 10 (+ 별칭 1 · 흔한 낱말 1) | `Node.js` · `Claude Code` | 1/10 |
| ADR `technology` | 16 (고유 14) | `Amazon Bedrock Guardrails` · `GPT-6 Luna` | 7/16 |
| 항목 `meta.dependency` | 고유 48 | `Elasticsearch` · `typesafe_sdk` | 34/48 (나머지 14는 팀원 스택) |
| `nodes@v1` | 기술 45 · 별칭 72 | `tech:anthropic-python` | label 25/45 · 별칭까지 32/45 |

**출처**: 수집 단위 6(reddit · hn · github · huggingface · vendor · security) ↔ 항목 `source` 8(security 대신 osv · python · nodejs) · 통로 접두 11종(주당 고유 30~33) · 벤더 9(경로 15) · 건강 검사 키 4종 · 레인 이유 5종.

**component**: 스택 표 26 · ADR `affected` 18 · 합 31 (ADR에만 있는 5개는 모두 decision-radar) · `nodes@v1` 15 · `nodes@v2` 21. **결정**: ADR 16 (adopted 10 · hold 3 · rejected 3). 레포 안 표기 `adr:0006` 3,667회 · `ADR 0006` 160회 · `adr/0006` 44회.

**이름을 맞추는 규칙 (6가지 · 8곳)**: 공백+소문자(`radar.py` · `arena/strategies/s1_match.py`) · PEP 503(`pipeline/importance/signals.py` · `collector/sources/security.py`) · 영숫자만(`eval/runner/run_case.py`) · 토큰 교집합(`gate.py` `_tokens`) · 단어 경계(`lanes.py` · 관문 제목) · 대소문자 구분 별칭(`alarm.py`).

**이름 · 관계 때문에 난 고장**

| 언제 | 무엇 | 원인 | 처리 | 근거 |
|---|---|---|---|---|
| 10/2 | elasticsearch · neo4j 릴리스가 Python 클라이언트 repo로 감 | 패키지 이름 = 서버 이름 | `dependency_repos` | cce29e6 |
| 10/8 | react 릴리스가 두 번 들어옴 | npm이 옛 repo 이름을 줌 | `dependency_repos` | 82abd14 |
| W42부터 매주 | `pypi.org/pypi/python/json` 404 | 런타임을 PyPI 패키지로 조회 | `source-health.yaml` 예외 | 4253fe5 |
| 10/15 | Bedrock 소식 하나가 두 번 판정됨 | Guardrails ⊂ Bedrock 관계를 모름 | `stack-extra.json`에서 뺌 (`tiers.json` 키는 남음) | d5f0ffe |
| 10/27 | Arena K 질의가 그래프에 안 붙음 (dev 4 · test 6) | ADR component ≠ 스택 표 component | `nodes@v2` · `RECORDED_AS` | eb891cd |
| W43 | 「… requests」가 경보로 걸림 | 스택 이름이 영어 낱말이기도 함 | `COMMON_WORDS` | dd5098e |
| W44 | 「RCE in Node, Python」을 놓침 | Node ↔ Node.js | `CASED_ALIASES` | f87ba46 |
| 11/4 | CPython 공지를 관문이 버림 | 제목에 CPython 낱말이 없음 | `dependency_when` 링크 조건 | 9fc0bdd |
| 11/4 | Node.js 공지의 기술이 등급 파일에 없음 | Node는 Electron 안에서 돈다 | `pool.yaml` `dependency: electron` | ade82c2 |
| W46 | HYO-179 security 0건 거짓 경보 | 수집 단위 ↔ 항목 source | PR #281 `QUIET_SOURCES` (이름은 첫 조각) | `2026-W46/source-health.json` |
| W43~W46 | Gemini 변경 기록 항목이 관문에서 「스택 기술을 모른다」 | match `google-genai` ↔ `_tokens`가 등급 이름을 쪼갬 | 메인 세션으로 넘김 (11/11) | W46 `trace.jsonl` · `handoffs/hyo-180-gemini-gate-brief.md` |
