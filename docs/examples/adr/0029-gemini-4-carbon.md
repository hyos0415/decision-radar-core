---
status: hold
date: '2026-11-16'
technology: Gemini 4 Carbon
use: 신규 모델 Gemini 4 Carbon을 medical-mate-ai eval의 평가 대상 후보로 검토
affected:
- repo: medical-mate-ai
  component: eval
evidence:
- url: https://aistudio.google.com/docs/changelog.md#november-10-2026
  source_type: official_doc
revisit_when:
- id: rv-1
  check_type: web
  condition: Gemini 4 Carbon의 도입 가격 적용 기간(2026-11-30)이 끝난 뒤 공식 가격 페이지의 gemini-4-carbon
    가격이 $2.00/$10.00 per 1M tokens에서 바뀌었는지 확인한다
  source_hint: https://aistudio.google.com/docs/changelog.md#november-10-2026
  basis: available at introductory [pricing](/docs/pricing#gemini-4-carbon) of $2.00
    per 1M input tokens and $10.00 per 1M output tokens through November 30, 2026.
---
# Gemini 4 Carbon(gemini-4-carbon) GA — medical-mate-ai 평가(eval) 후보 모델 검토

> 2026-11-21 보류 — 사용자 결정. medical-mate-ai 추출 평가(회귀 88호출, extract-v3)로 재 봤다(decision-radar HYO-229): 85/88 · 안전 위반 0으로 지금 비교 기준 중 가장 좋은 GPT-5.6 Terra와 같지만, 운영 nova-pro보다 약 7배 느리고(p50 5.3초 대 0.7초) 비용이 3배쯤이다($0.47 / 88호출, 도입가). 운영을 바꿀 근거가 없어 보류한다. 재검토 조건 rv-1(도입가가 끝난 뒤 가격)은 그대로 두어 매주 revisit이 본다.

## Context and Problem Statement
2026-11-10 Google AI Studio 변경 기록에 Gemini 4 Carbon(`gemini-4-carbon`)이 GA로 올라왔다. 공식 설명은 "지속적 에이전트 코딩, 멀티모달 지식 작업, 과학적 추론, 사이버 보안 방어를 위한 가장 강력한 추론 모델"이다. 도입 가격은 입력 100만 토큰당 $2.00, 출력 100만 토큰당 $10.00이고, 이 가격은 2026-11-30까지 적용된다. 영향 범위로 잡힌 곳은 medical-mate-ai의 eval 컴포넌트다. 같은 변경 기록에는 이 모델과 무관해 보이는 항목(지역 확대, File API 등)이 섞여 있다. 변경 기록 본문만으로는 이 항목들이 이번 변경인지 판단할 수 없어 이 초안에서 다루지 않는다. 의료 도메인 정확도, 지연 시간, 안전성에 대한 수치는 소식에 없다. 기존 ADR 0015(Langfuse)와 0016(Anthropic SDK)은 의존성 릴리스 대응 건이라 이 건과 직접 겹치지 않는다.

## Decision Outcome
medical-mate-ai의 eval 후보 모델 목록에 `gemini-4-carbon`을 추가해 기존 평가 세트로 비교 실행할지 사람이 판단하도록 제안한다. 채택 여부는 사람이 정한다. 비교 실행을 한다면 정확도, 안전성, 지연 시간, 비용을 현재 사용 모델과 같은 조건에서 측정해야 한다. 도입 가격은 2026-11-30까지만 보장되므로 비용 비교는 정가 확정 후 기준으로 다시 확인해야 한다. 제안 범위는 평가용 검토까지다. 운영 모델 교체는 이 초안에 포함하지 않는다.

관련 결정: 0015, 0016

## Research Note
- 소식에는 벤치마크, 의료 도메인 성능, 안전성, 지연 시간 수치가 없다. 성능 주장은 벤더 설명 그대로이고 검증되지 않았다.
- 정가는 이번에 확인하지 못했다. 소식에 적힌 값은 도입 가격($2.00 / $10.00 per 1M tokens, 2026-11-30까지)뿐이다.
- 변경 기록의 나머지 줄(지역 확대, File API, OpenAI 호환 등)은 조각만 남아 있어 해석하지 않았다.
- medical-mate-ai의 현재 eval 모델과 평가 세트는 이번에 확인하지 않았다. 사람이 확인해야 한다.
