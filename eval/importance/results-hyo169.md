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

## 5. 판정 (test v2, 0판) — ⑦ Haiku · ⑧ Sonnet 둘 다 불통과

2026-11-12 · 등록 `28af4ac`(다듬기 끝 · 거부 0건) · CLI 2.1.322 · 묶음 10 · 시드 0 · 원자료 `eval/runs/hyo169/test-{haiku,sonnet}` · 로그 `eval/runs/hyo169-test-*.log`

| | C1 놓침 (전체 3 + 안전만 5) | C2 소음 | C3 일치 | R (23건) | 판정 |
|---|---|---|---|---|---|
| ⑦ Haiku 5.5 | 3 · 2 · 3 + 2 · 3 · 2 · 2 · 2 | 8.1 · 7.9 · 7.9% | 92.4 · 93.2 · 93.2% | 87 · 87 · 91% | **불통과** (C1) |
| ⑧ Sonnet 5.5 | 3 · 2 · 2 + 8 · 1 · 1 · 1 · 2 | **17.1** · 12.5 · 15.0% | 89.0 · 91.5 · 89.8% | 87 · 87 · 83% | **불통과** (C1 · C2) |
| 기준 | 실행 8번 모두 0 | ≤ 15% | ≥ 80% | ≥ 80% | |

- 세션 46 · 46(오류 0) · 입력 50.3만 · 50.2만 · 출력 16.0만 · 4.4만 토큰
- 고르는 규칙(결과 전): 둘 다 불통과 → **운영 관문은 Sol API 그대로**
- 안전 항목 17건 중 놓친 것(8번 중 몇 번): openai-python v3.28.0 **Haiku 8 · Sonnet 8** · langchain-core 1.6.8 Haiku 8 · Sonnet 2 · litellm v1.82.2-dynamoai.dev 2 · 2 · 그 밖은 1~2번씩. Sonnet의 안전만 첫 실행은 8건을 한꺼번에 놓쳤다(오류 0 — 묶음 이웃이 안전 항목뿐인 실행)
- 두 모델 모두 16번 중 16번 놓친 openai-python v3.28.0은 정답(security)이 정의(D1)에 맞는지 따로 본다(§8 변경 2026-11-12 — 고치면 원래 판정은 두고 저장된 답을 다시 채점)
- 다음: ⑨ Opus 5.5 — 같은 0판 · 같은 test · 같은 기준, 46세션 (§8 변경 2026-11-12)

### ⑨ Opus 5.5 (등록 `6847cc0`, 원자료 `eval/runs/hyo169/test-opus`)

| | C1 놓침 (전체 3 + 안전만 5) | C2 소음 | C3 일치 | R (23건) | 판정 |
|---|---|---|---|---|---|
| ⑨ Opus 5.5 | 2 · 2 · 2 + 3 · 2 · 2 · 2 · 2 | 10.0 · 14.3 · 9.8% | 93.2 · 91.5 · 94.1% | 91 · 91 · 96% | **불통과** (C1) |

- 세션 46(오류 0) · 입력 50.2만 · 출력 7.2만 토큰. 세 모델 다 불통과 → 운영 관문은 Sol API 그대로

### 관찰 (판정 아님 — 결과를 본 뒤)

세 모델의 C1 놓침은 같은 세 소식에 몰린다(8번 중 몇 번 놓쳤나 — Haiku · Sonnet · Opus).

| 소식 | 본문의 보안 근거 | 놓침 | 모델 이유 (요지) |
|---|---|---|---|
| openai-python v3.28.0 | `deps: update PyJWT security constraint to 2.14.0` | 8 · 8 · 7 | 의존성 chore — SDK 자체 보안 수정이 아니다 |
| litellm v1.82.2-dynamoai.dev | `fix(security): bump tar to 7.5.11 and tornado to 6.5.5` | 2 · 2 · 6 | 의존성 올림 — litellm 자체 보안 패치가 아니다 |
| langchain-core 1.6.8 | `fix(core): harden scoped and compatible IPv6 SSRF checks` | 8 · 2 · 4 | (Haiku) 보안 · CVE라고 밝히지 않았다 |

- 앞의 두 건은 **정의와 프롬프트가 어긋난 곳**이다. 확정 시트 D1은 「실행 의존성 CVE 올림 포함」(정답지의 k6 의존성 올림도 security)인데, 프롬프트는 「대상 기술 자체의 수정이어야 한다(… 개발 의존성 · CI 스캔 설정은 아니다)」만 적어 세 모델 모두 의존성 올림을 chore로 읽는다. openai-python은 HYO-131의 Luna도 3번 다 minor_patch였다
- 셋째는 「보안」 낱말 없이 취약점 종류(SSRF)만 적은 수정 — 프롬프트의 「글이 밝힌 것만(security · CVE · GHSA · vulnerability · advisory)」 목록에 없다
- 앞의 두 건을 맞힌 것으로 치면(저장된 답 다시 세기, 호출 0): Opus 전체 3번 **0 · 0 · 0**, 안전만 5번 중 4번에 langchain-core 1건 · Haiku 매 실행 langchain-core(+ uv 0.11.7 한 번) · Sonnet 안전만 첫 실행 7건(묶음 이웃이 안전 항목뿐인 실행) — 결과를 본 뒤의 셈이라 판정에 쓰지 않는다
- 운영 Sol은 이 test 세트에서 잰 적이 없다 — 지금 바(C1 실행 8번 0건)를 Sol이 넘는지 모른다

### ⑩ Sol 기준점 (구독 codex, `gpt-6-sol` — 운영 `gpt-6.1-sol`보다 한 판 앞, 등록 `7c79cf6`, 원자료 `eval/runs/hyo169/test-sol-codex`)

| | C1 놓침 (전체 3 + 안전만 5) | C2 소음 | C3 일치 | R (23건) | 판정 |
|---|---|---|---|---|---|
| ⑩ Sol (gpt-6-sol) | **5 · 4 · 3** + 2 · 3 · 3 · 2 · 3 | 12.8 · 10.3 · 12.2% | 90.7 · 92.4 · 92.4% | 96 · 96 · 96% | 불통과 (C1) |

- 세션 46(오류 0) · 입력 98.0만(codex 기본 지시가 세션마다 붙는다) · 출력 4.0만 토큰
- **이 test에서 C1(실행 8번 모두 0건)을 넘은 모델은 없다 — Sol 포함.** 안전 놓침은 Sol이 가장 많다(다수결 4건 · Claude 셋은 2건씩)

### ⑪ Jev 1.13.0 (TypeSafe API, 문턱 0.5 고정 · 묶음 없음, 등록 `8c2ae3f`, 원자료 `eval/runs/hyo169/test-jev`)

| | C1 놓침 (전체 3 + 안전만 5) | C2 소음 | C3 일치 | R (23건) | 판정 |
|---|---|---|---|---|---|
| ⑪ Jev 1.13.0 | **7 · 7 · 6** + 7 · 7 · 6 · 7 · 6 | **16.2 · 18.4 · 15.8%** | 87.3 · 86.4 · 88.1% | 91 · 91 · 91% | 불통과 (C1 · C2) |

- 439회(오류 0) · 입력 120.9만 · 출력 6.9만 토큰 · **$0.051**
- 놓친 안전 7건이 **전부 의존성 보안 올림**이다 — k6 1.7.1 · 1.8.0 · 1.8.1 · 1.8.2(grpc CVE) · uv 0.11.7(OpenSSL) · litellm · openai-python. SSRF(langchain-core)는 잡았다. 이유 문장을 내지 않는다(확률만)
- 다섯 모델 표는 `eval/runs/hyo169/cross-model-test-5.md`

## 6. 네 모델 나란히 — 오류 지점 · 편향 (판정 아님, `cross_model_diag.py`, 원자료 `eval/runs/hyo169/cross-model-test.md`)

전체 실행 3번의 다수결로 읽는다.

| | 남김 일치 | 남겼는데 정답 버림 | 정답 남김인데 버림 (안전) | 실행마다 남김이 갈린 소식 |
|---|---|---|---|---|
| Haiku | **110**/118 | **3** | 5 (2) | 3 |
| Sonnet | 107/118 | 6 | 5 (2) | **10** |
| Opus | **110**/118 | 4 | 4 (2) | 3 |
| Sol | 108/118 | 5 | 5 (**4**) | 3 |

**네 모델이 모두 틀린 5건** — 두 회사 모델이 같은 쪽으로 답했다. 정의로 다시 보면:

| 소식 | 정답 | 네 모델 | 본문 | 읽음 |
|---|---|---|---|---|
| langchain 0.3.30 | minor_patch (버림) | deprecation (남김) | `... and deprecate hub` | 정답 의심 — 정의상 deprecation |
| langfuse-python 4.0.0b1 | minor_patch (버림) | breaking (남김) | 첫 줄 `## Breaking changes` | 정답 의심 — 프리릴리스는 major만 아니다(③ 「본문의 breaking은 그대로」) |
| vLLM 0.16.0 | minor_patch (버림) | breaking · deprecation · security (남김) | protobuf CVE-2026-0994 패치 · BitBlas 제거 · `Deprecated IPEX`(938자째) | 정답 의심 — D1 실행 의존성 CVE 올림 포함 |
| openai-python 3.28.0 | security (남김) | minor_patch (버림) | `deps: update PyJWT security constraint` — CVE · 실행 의존성인지 안 밝힘 | 정의가 가르지 못함 |
| claude-code 2.1.295 | breaking (남김) | minor_patch (버림) | 8,000자 노트, 3,000자 창 안에 breaking 근거 없음 | 창 책임으로 보임 `[확인]` |

**편향 — 회색 지대 두 곳에서 모델마다 다른 쪽으로 기운다**

| 회색 지대 | 소식 | Haiku | Sonnet | Opus | Sol |
|---|---|---|---|---|---|
| 의존성 보안 올림 (CVE 있음) | uv 0.11.7 (번들 CPython OpenSSL) · k6 1.7.1 (grpc CVE) | 잡음 | 잡음 | 잡음 | **놓침** (6/8 · 2/8) |
| 의존성 보안 올림 (CVE 없음) | litellm 1.82.2 (`fix(security)` tar · tornado) | 잡음 2/3 | 놓침 | 놓침 | 잡음 2/3 |
| 〃 | openai-python 3.28.0 (PyJWT 「security constraint」) | 놓침 | 놓침 | 놓침 | 놓침 |
| 「보안」 낱말 없는 취약점 수정 | langchain-core 1.6.8 (IPv6 SSRF 검사 강화) | **놓침** 8/8 | 잡음 | 잡음(전체) · 놓침(안전만 4/5) | **놓침** 8/8 |
| 목차의 「Security」 절 | PyTorch 2.14 · 2.10 | 안 붙임 | **붙임**(소음) | 2.14만 붙임 | 안 붙임 |
| Python 버전 지원 중단 | pydantic 2.14 · langfuse 3.8.0 · langgraph prebuilt 1.0.2 | eol 또는 없음 | minor_patch | eol | **breaking**(남김 ↑) |

- **Sol**: 의존성 CVE는 「대상 기술 자체가 아니다」로 엄격하게 읽어 안전 놓침이 가장 많고, Python 버전 지원 중단은 breaking으로 넉넉히 붙여 R(96%)이 가장 높다
- **Haiku**: 글자 그대로 읽는다 — 「security · CVE」가 적힌 의존성 올림은 잡고, 낱말이 없는 SSRF는 놓친다. 남겼는데 틀린 것이 가장 적다(3). model_retirement를 넉넉히 붙이지만(6, 정답 1) 다른 모델 은퇴라 우리 것 좁히기에서 빠져 남김은 안 바뀐다
- **Sonnet**: 목차 낱말 · 짐작으로 security를 붙여 소음이 가장 많고, 실행마다 가장 흔들린다(10건 · 안전만 첫 실행 7건 한꺼번에)
- **Opus**: 뜻으로 읽어 SSRF를 잡지만, 묶음 이웃이 안전 항목뿐일 때는 놓친다(전체 0/3 · 안전만 4/5 놓침 — 묶음 이웃 효과). eol을 가장 잘 붙인다(3/9)
- **공통**: eol(정답 9, 붙인 수 1~3)과 deprecation(맞힘 11~13/21)은 넷 다 약하다. eol은 대부분 다른 버전 지원 중단이라 남김에는 영향이 적다

**⑪ Jev를 더한 뒤 (변경 2026-11-12 저녁)**: 의존성 보안 올림에서 모델들이 한 줄로 늘어선다 — 「대상 기술 자체의 수정이어야 한다」를 얼마나 무겁게 읽나의 차이다.

| 의존성 보안 올림 7건(k6 4 · uv · litellm · openai) 중 다수결로 놓친 수 | Haiku | Sonnet | Opus | Sol | Jev |
|---|---|---|---|---|---|
| | 1 (openai) | 2 | 2 | 4 | **7** |
| 「보안」 낱말 없는 SSRF (langchain-core) | 놓침 | 잡음 | 잡음 | 놓침 | 잡음 |

Jev는 종류마다 따로 묻는 형식이라 security 질문 하나에 정의 문장 전체(「대상 기술 자체 … 개발 의존성은 아니다」)가 바로 붙어 그 문장을 가장 강하게 따른다. eol도 따로 물어 넉넉히 붙인다(11, 정답 9 · 소음 5).

**읽은 것**: 안전 놓침의 대부분은 모델이 약해서가 아니라 **정의가 가르지 못하는 두 회색 지대**(의존성 보안 올림 · 「보안」 낱말 없는 취약점 수정)에서 생기고, 모델마다 다른 쪽으로 기운다. 이 test에서 Haiku는 Sol보다 안전 놓침이 적고(2 대 4) 소음도 적다(3 대 5) — 「Claude가 떨어져서 Sol 유지」는 품질 근거가 아니다(판정은 등록대로 그대로 둔다)
