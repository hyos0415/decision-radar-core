# 데이터 등록부 (HYO-180)

같은 대상(기술 · 출처 · component · 결정)을 파일마다 다른 이름으로 적지 않도록, **정체성 · 이름 · 사람이 정한 관계**를 한 곳에 둔다. 설계와 이유는 `docs/design/data-registry.md`에 있다.

지금 들어 있는 것은 **출처**뿐이다. 수집 단위 6 · 항목 출처 8 · 통로 11이다. 기술 · component · 결정은 설계 §9 순서대로 붙인다.

| 파일 | 무엇 | 누가 고치나 |
|---|---|---|
| `sources.ttl` | 출처 원본 (Turtle) | 사람 |
| `ontology.ttl` | OWL 스키마 — 클래스 · 속성 · domain/range | 사람 (드물게) |
| `shapes.ttl` | SHACL 검사 — 빌드가 실패하는 조건 | 사람 (드물게) |
| `registry.json` | 빌드 산출 — 실행 경로(수집기 · 파이프라인 · 스킬 · 앱)는 이것만 읽는다 | **빌드만** |
| `build.py` · `lookup.py` | 빌드(rdflib · pySHACL · owlrl) · 조회(표준 라이브러리만) | — |

## 새 출처를 붙일 때

예를 들어 Go 보안 공지를 security에 붙인다면:

1. `collector/pool.yaml` `security.runtimes`에 한 줄을 더한다 (수집기)
2. `sources.ttl`에 한 줄을 더한다: `source:go a radar:ItemSource ; skos:prefLabel "go" ; radar:collectedBy source:security .`
3. 빌드하고 `registry.json`을 같이 커밋한다

```bash
.venv/Scripts/python registry/build.py              # macOS: .venv/bin/python
.venv/Scripts/python registry/tests/test_registry.py
```

2를 빠뜨리면 다음 주 출처 건강 검사가 「등록부에 없음」으로 알린다. 조용히 안 세어지는 일은 없다.

## 규칙

- **ID는 한 번 정하면 바꾸지 않는다.** 이름이 바뀌면 `skos:prefLabel`만 바꾸고, 옛 이름은 `skos:altLabel`로 내린다. 얼린 주의 옛 이름은 별칭이 받는다
- **같은 종류 안에서** 정규화한 이름(소문자 · `[-_.\s]+` → `-`)이 두 대상에 겹치면 빌드가 실패한다. 종류가 다르면 겹쳐도 된다(`vendor`는 수집 단위이면서 통로다)
- 기계가 정하는 값(`tiers.json` 등급 · 버전 · 쓰임새)은 넣지 않는다
- `registry.json`은 손으로 고치지 않는다. CI가 `build.py --check`로 Turtle에서 다시 만들어 비교한다
- SHACL은 추론 없이 돈다. 추론을 켜면 오타 낸 대상 ID가 「그 클래스다」로 추론돼 관계 검사를 통과한다 (설계 §3)
