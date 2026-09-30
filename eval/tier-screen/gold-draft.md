# 정답지 초안 — 기술 등급 (사용자 확정 필요)

> **Claude 초안.** 「확정」 열을 사용자가 채운다. `경계`는 정의로 하나로 안 정해져 채점에서 뺄 후보다.
> 출처: 정책 표(§2-1) 예시에 **없는** 기술만 — medical-mate-backend(`build.gradle`·Dockerfile·compose) · medical-mate-web3(`package.json`) · GitHub Actions(backend·medical-mate-ai) · 로컬/호스팅 모델·서비스 id(medical-mate-ai·finance_verifier 코드)
> 이 파일은 시험 받는 모델이 보는 디렉터리에 넣지 않는다.

| id | 기술 | 출처 | 초안 등급 | 근거 | 결정론 신호 | 확정 |
|---|---|---|---|---|---|---|
| T01 | Java 21 | backend `languageVersion` | L0 | 언어 (v1.1 U3) | toolchain | |
| T02 | TypeScript | web3 devDep | L0 | 언어 (v1.1 U3) | — | |
| T03 | `eclipse-temurin:21-jre-alpine` | backend Dockerfile `FROM` | L1 | 베이스 이미지 | `FROM` | |
| T04 | `postgres:16-alpine` | backend compose `image` | L1 | DB (v1.1 U3) | `image` | |
| T05 | Spring Boot (plugin 3.5.16) | backend plugins | L4 | 앱 뼈대 | — | |
| T06 | spring-boot-starter-web | backend | L4 | 웹 프레임워크 | — | |
| T07 | spring-boot-starter-data-jpa | backend | **경계** L4/L6 | ORM — 바꾸면 영속층 전반이 바뀜(L4) vs 기능 하나(L6) | — | |
| T08 | spring-boot-starter-security | backend | **경계** L4/L6 | 인증·인가 뼈대 vs 기능 하나 | — | |
| T09 | spring-boot-starter-validation | backend | L6 | 기능 하나(검증) | — | |
| T10 | spring-boot-starter-actuator | backend | L6 | 기능 하나(운영 지표) | — | |
| T11 | springdoc-openapi-starter-webmvc-ui 2.9.0 | backend | **경계** L6/L8 | API 문서 — 기능 하나 vs 작고 대체 쉬움 | — | |
| T12 | jjwt-api 0.12.7 | backend | L6 | 기능 하나(JWT) | — | |
| T13 | caffeine | backend | L6 | 기능 하나(캐시) | — | |
| T14 | flyway-core | backend | **경계** L4/L6 | DB 마이그레이션 — 뼈대 vs 기능 하나 | — | |
| T15 | org.postgresql:postgresql (JDBC) | backend runtimeOnly | **경계** L5/L6 | DB 클라이언트 — L5는 L2·L3을 부르는 클라이언트로 정의, DB는 L1 | — | |
| T16 | spring-boot-starter-test | backend testImplementation | L7 | 테스트 도구 | test 구성 | |
| T17 | testcontainers junit-jupiter | backend testImplementation | L7 | 테스트 도구 | test 구성 | |
| T18 | io.spring.dependency-management plugin | backend plugins | L7 | 빌드 도구 (v1.1 U3) | — | |
| T19 | `actions/checkout@v4` | CI `uses` | L7 | CI (v1.1 U3) | `uses` | |
| T20 | `docker/build-push-action@v6` | CI `uses` | L7 | CI | `uses` | |
| T21 | `astral-sh/setup-uv@v5` | CI `uses` | L7 | CI | `uses` | |
| T22 | react | web3 dep | L4 | UI 프레임워크 | — | |
| T23 | react-dom | web3 dep | L4 | react와 한 몸 | — | |
| T24 | react-router-dom | web3 dep | **경계** L4/L6 | 라우팅 — 뼈대 vs 기능 하나 | — | |
| T25 | three | web3 dep | L6 | 기능 하나(3D) | — | |
| T26 | vite | web3 devDep | L7 | 빌드 도구 (v1.1 U3) | devDep | |
| T27 | @vitejs/plugin-react | web3 devDep | L7 | 빌드 도구 | devDep | |
| T28 | oxlint | web3 devDep | L7 | 린터 | devDep | |
| T29 | @types/react | web3 devDep | L7 | 타입 정의 (v1.1 U3) | devDep | |
| T30 | `Intel/Qwen3.5-4B-int4-AutoRound` (vLLM 서빙) | finance_verifier | L2 | 로컬 서빙 모델 (v1.1 U2) | — | |
| T31 | `kakaocorp/kanana-2-3b-instruct` (vLLM 서빙) | finance_verifier | L2 | 로컬 서빙 모델 | — | |
| T32 | `qwen3-1.7b-q4_0` (GGUF, llama-server) | medical-mate-ai | L2 | 로컬 서빙 모델 | — | |
| T33 | `gemini-3.5-flash` | medical-mate-ai providers | L2 | 호스팅 모델 | — | |
| T34 | `nvidia/nemotron-3-ultra-550b-a55b` (NVIDIA API) | finance_verifier | L2 | 호스팅 모델 | — | |
| T35 | NVIDIA API (`integrate.api.nvidia.com`) | finance_verifier | L3 | 호스팅 서비스 | — | |
| T36 | GHCR (`ghcr.io`) | medical-mate-ai publish-image | L3 | 호스팅 서비스(레지스트리) | — | |
| T37 | uv | Dockerfile·CI | L7 | 빌드 도구 (v1.1 U3) | — | |

집계: **37행** — 명확 **31개** · 경계 **6개** (T07·T08·T11·T14·T15·T24)
