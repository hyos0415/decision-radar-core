# 결과 — 관문 변화 분류기를 구독 `claude -p`로 (HYO-169)

> 등록: `docs/design/importance-policy.md` §8 변경 2026-11-11(⑥ Sonnet 5.5 CLI) · 같은 날 밤(⑦ Haiku · ⑧ Sonnet 다듬기 · 판정). 확정 시트 `precondition-hyo169.md`.
> 원자료(gitignore): `eval/runs/hyo169/` — `w41-gold-sonnet-cli/` · `2026-W44-sonnet-cli/` · `2026-W45-sonnet-cli/`(summary.json · calls.jsonl · answers.jsonl · report.md)

## 1. ⑥ Sonnet 5.5 CLI — 판정 (가) W41 v2 · 불통과

| | 실행 1 | 실행 2 | 실행 3 | 기준 | |
|---|---|---|---|---|---|
| C1 놓침 | 0 | 0 | 0 | 모든 실행 0 | 통과 |
| C2 소음 | 0% | 18.75% | 13.33% | ≤ 15% | **불통과** |
| C3 일치 | 98.0% | 95.1% | 96.1% | ≥ 80% | 통과 |

- 구독 세션 303(오류 0) · 입력 85만 · 출력 5.2만 토큰. 고정 등급 `eval/runs/hyo130/tiers-fixed.json` · `--no-tier-diag`
- 소음: 정식 x.0.0이 아닌 릴리스에 major(실행 2 · 3에 2건) + 가격 아닌 소식에 pricing 1건(실행 2 · 3)
- 등록에 없던 관찰(판정에 안 씀): W41 실데이터의 안전 밖 정답 남김 4건 중 2건을 세 번 다 놓쳤다 — C1~C3 어디에도 드러나지 않는다(확정 시트 §6 「통과시키는 구멍」)

## 2. ⑥ 짝 비교 (나) — 기록만

| 주 | 둘 다 답함 | 라벨 같음(정리 뒤) | 남김 Sol / Sonnet | Sol만 | Sonnet만 | detect Sol만 / Sonnet만 |
|---|---|---|---|---|---|---|
| W44 | 257 | 244 | 28 / 32 | 1 | 5 | 1 / 5 |
| W45 | 204 | 189 | 15 / 18 | 2 | 5 | 2 / 4 |

- Sonnet만 10건: 전부 정식이 아닌 버전의 major — `x.y.z` 소수 판(langchain-core 등) · llama.cpp 빌드 번호(`b11906` …)
- Sol만 3건(= Sonnet이 놓친 것): langfuse v4.64.0 deprecation · llama.cpp b12002 breaking · llama.cpp v0.8.0 breaking
- 그 주에 Sol 답이 있는데 지금 코드가 관문에 보내지 않는 소식(W44 17 · W45 9)은 양쪽 다 빠진다

## 3. 「major는 정식 x.0.0만」 코드 규칙 뒤 (모델 호출 0 — 저장된 답 다시 채점, 판정 아님)

| | 전 | 뒤 |
|---|---|---|
| 운영 Sol W43~W46 남김 · detect | — | **바뀜 0** (Sol major 26건 전부 정식 x.0.0) |
| Sonnet W44 「Sonnet만 남김」 | 5 | 0 (남김 Sol 28 · Sonnet 27) |
| Sonnet W45 「Sonnet만 남김」 | 5 | 0 (남김 Sol 15 · Sonnet 13) |
| Sonnet W41 C2 | 0 · 18.75 · 13.33% | 0 · 7.1 · 7.1% |
| Sonnet W41 C3 | 98.0 · 95.1 · 96.1% | 98.0 · 97.1 · 97.1% |
| Sonnet W41 R(안전 밖 남김 7건) | — | 5 · 5 · 5 (71%) |

W41은 다듬기 금지(§8 2026-10-16 밤)이고 이 규칙은 결과를 본 뒤 넣었으므로, 위 「뒤」 숫자는 판정이 아니다. 남은 문제는 **놓침**(안전 밖 breaking · deprecation)이다 — 다듬기의 목표.

## 4. 다듬기 (dev, ⑦ Haiku · ⑧ Sonnet)

### 0판 (2026-11-11 밤, `prompts_cli.py` ROUND 0 = 운영 문구 + 묶음 규칙 두 줄, 코드 `76ea5a3`, CLI 2.1.322, 묶음 10 · 시드 0)

원자료 `eval/runs/hyo169/dev-r0-{haiku,sonnet}` · 읽기 `dev_diag.py`. 맞힐 수 없는 1건(기술 여럿 vendor 항목)을 빼고 219건. 판정이 아니다(k=1 · Claude 관여 세트).

| | 남김 | 일치 | 소음 | 안전 남김 | R | 세션 · 입력 · 출력 토큰 |
|---|---|---|---|---|---|---|
| Haiku 5.5 | 35 | 216/219 (98.6%) | 2/35 | 11/11 | 22/23 | 22 · 20.4만 · 4.7만 |
| Sonnet 5.5 | 33 | 218/219 (99.5%) | 0/33 | 11/11 | 22/23 | 22 · 20.4만 · 1.8만 |

어긋난 3건:
- langgraph 1.1.0 deprecation 놓침(둘 다) — 근거 문장이 본문 3,442자째로 **창(3,000자) 밖**이다. 프롬프트 책임이 아니다(확정 시트 §8)
- langsmith-sdk 0.7.36(Haiku) — 「Vendor UUID to satisfy **security** warning」(JS 쪽 경고 해소)에 security. 정의 문구가 이미 막는 경우인데 한 번 넘었다
- claude-code v2.1.193(Haiku) — 업그레이드하면 OTEL 응답 로그가 켜질 수 있다는 동작 변경을 breaking으로

두 모델 사이 남김 차이는 2건 — 확정 시트의 판정 불가 띠(3건 미만)다. **dev에는 프롬프트로 고칠 신호가 거의 없다**(고칠 수 있는 오류가 Haiku의 단발 2건뿐). 이 세트로 문구를 더 깎으면 두 건에 맞추는 것이 된다.
