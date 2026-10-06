"""안전 보강 세트 (importance-policy §8 변경 2026-10-06 밤, D7) — Claude가 실제 릴리스·페이지 형식으로 쓴 가상 항목.

W41 실데이터에는 정답 남김이 4건, C1 안전 항목이 1건뿐이라 아무것도 못 짚는 분류기도 통과한다. 그래서 우리 스택 기술의
모델 은퇴·보안 권고·breaking을 넣는다. 형식은 같은 출처의 실제 항목(W41)을 본떴다. 버전·날짜·GHSA/CVE 번호는 지어낸 것이다.
"""

NEW_TECHS = [
    dict(name="claude-haiku-4-5-20251001", tiers=["L2"], path="prod", pin=None, signals=None,
         usage="Anthropic API로 부르는 모델 — fineprint analysis의 조건 추출(운영)과 finance_verifier eval 비교에서 쓴다",
         source="fineprint · finance_verifier CLAUDE.md 「외부 모델·서비스」"),
    dict(name="gpt-6-luna", tiers=["L2"], path="prod", pin=None, signals=None,
         usage="OpenAI API로 부르는 모델 — decision-radar 제품의 레인 A 분류기(최적화 모드)와 tier-screen에서 쓴다 (ADR 0007·0008)",
         source="decision-radar CLAUDE.md 「외부 모델·서비스」"),
]

# (id, technology, title, text, gold_changes, why)
ITEMS = [
    ("synthetic:page-diff:anthropic:model-deprecations@2026-W43", "claude-haiku-4-5-20251001",
     "[anthropic] https://docs.claude.com/en/docs/about-claude/model-deprecations (페이지 변경)",
     "claude-haiku-4-5-20251001 Deprecated October 20, 2026 April 20, 2027\n"
     "2026-10-20: Claude Haiku 4.5 model\n"
     "On October 20, 2026, Anthropic notified developers using Claude Haiku 4.5 of its upcoming retirement on the Claude API.\n"
     "Retirement date Deprecated model Recommended replacement\n"
     "April 20, 2027 claude-haiku-4-5-20251001 claude-sonnet-5-5",
     ["model_retirement", "deprecation"], "우리가 운영에서 부르는 모델의 은퇴 공지 (모델 지원 종료 페이지에 더해진 줄)"),
    ("synthetic:page-diff:openai:deprecations@2026-W43", "gpt-6-luna",
     "[openai] https://platform.openai.com/docs/deprecations (페이지 변경)",
     "2026-10-21: GPT-6 Luna\n"
     "The following model is deprecated and will be removed from the API on April 21, 2027, with six months' notice. "
     "Migrate to the recommended replacement before the shutdown date.\n"
     "Shutdown date\nModel / system\nRecommended replacement\nApr 21, 2027\ngpt-6-luna\ngpt-6.1-sol",
     ["model_retirement", "deprecation"], "레인 A 분류기가 부르는 모델의 은퇴 공지"),
    ("synthetic:github-release:anthropics/anthropic-sdk-python@v1.11.1", "anthropic",
     "anthropics/anthropic-sdk-python v1.11.1",
     "## 1.11.1 (2026-10-08)\n\nFull Changelog: [v1.11.0...v1.11.1](https://github.com/anthropics/anthropic-sdk-python/compare/v1.11.0...v1.11.1)\n\n"
     "### Security\n\n* **client:** stop writing the `x-api-key` header value to DEBUG logs when a request is retried "
     "([GHSA-7q2m-4c9x-r3vh](https://github.com/anthropics/anthropic-sdk-python/security/advisories/GHSA-7q2m-4c9x-r3vh))\n\n"
     "### Bug Fixes\n\n* **streaming:** close the response when a stream is abandoned mid-event\n\n"
     "### Chores\n\n* **internal:** update test fixtures",
     ["security"], "보안 권고(GHSA)가 붙은 SDK 패치"),
    ("synthetic:github-release:langchain-ai/langgraph@1.2.14", "langgraph",
     "langchain-ai/langgraph langgraph==1.2.14",
     "Changes since 1.2.13\n"
     "* fix(langgraph): refuse to deserialize checkpoint payloads with unregistered types — security fix for "
     "[GHSA-r6hx-5v2q-8mcw](https://github.com/langchain-ai/langgraph/security/advisories/GHSA-r6hx-5v2q-8mcw) (arbitrary object construction from a tampered checkpoint store) (#9231)\n"
     "* release(langgraph): 1.2.14 (#9233)\n"
     "* chore(deps): bump urllib3 from 2.8.0 to 2.8.1 in /libs/langgraph (#9214)",
     ["security"], "체크포인트 역직렬화 보안 권고"),
    ("synthetic:github-release:fastapi/fastapi@0.142.3", "fastapi",
     "fastapi/fastapi 0.142.3",
     "### Security Fixes\n\n"
     "* 🔒 Limit the number of parts accepted when parsing `multipart/form-data` to prevent memory exhaustion (CVE-2026-40311). "
     "PR [#16431](https://github.com/fastapi/fastapi/pull/16431) by [@tiangolo](https://github.com/tiangolo).\n\n"
     "### Internal\n\n* ⬆ Bump starlette from 1.6.0 to 1.6.1. PR [#16429](https://github.com/fastapi/fastapi/pull/16429) by [@dependabot[bot]](https://github.com/apps/dependabot).",
     ["security"], "CVE가 붙은 보안 패치"),
    ("synthetic:github-release:ggml-org/llama.cpp@b11452", "llama.cpp",
     "ggml-org/llama.cpp b11452",
     "<details open>\n\nserver : fix heap buffer overflow when tokenizing an over-long prompt (GHSA-wq8f-2m4c-9hxp) (#30012)\n\n"
     "Security: a request whose prompt exceeded n_ctx after special-token expansion could write past the end of the token buffer "
     "in `/completion` and `/v1/chat/completions`. The tokenizer now sizes the buffer from the expanded length and the server "
     "rejects the request with HTTP 400.\n\n</details>",
     ["security"], "서빙 엔진 서버의 보안 권고"),
    ("synthetic:github-release:berriai/litellm@v1.105.1", "litellm",
     "BerriAI/litellm v1.105.1",
     "## What's Changed\n"
     "* fix(security): redact provider `api_key` values from proxy error responses — "
     "[GHSA-3p8v-x6m2-q9jf](https://github.com/BerriAI/litellm/security/advisories/GHSA-3p8v-x6m2-q9jf) by @ishaan-jaff in https://github.com/BerriAI/litellm/pull/44301\n"
     "* fix(router): respect `num_retries=0` for Anthropic fallbacks by @krrishdholakia in https://github.com/BerriAI/litellm/pull/44297\n\n"
     "**Full Changelog**: https://github.com/BerriAI/litellm/compare/v1.105.0...v1.105.1",
     ["security"], "개발 경로 도구라도 security는 남긴다 (L7 × security = ✅)"),
    ("synthetic:github-release:openai/openai-python@v4.0.0", "openai",
     "openai/openai-python v4.0.0",
     "## [4.0.0](https://github.com/openai/openai-python/compare/v3.24.0...v4.0.0) (2026-10-14)\n\n"
     "### ⚠ BREAKING CHANGES\n\n"
     "* **client:** drop support for Python 3.9\n"
     "* **types:** remove the deprecated `ChatCompletion` legacy type aliases; import from `openai.types.chat` instead\n\n"
     "### Features\n\n* **api:** add batch session traces\n\n### Bug Fixes\n\n* **client:** retry on connection reset during streaming",
     ["breaking", "major"], "메이저 버전 + Breaking changes"),
    ("synthetic:page-diff:langfuse:changelog@2026-W43", "langfuse",
     "[langfuse] https://langfuse.com/changelog (페이지 변경)",
     "October 15, 2026\nDeprecation: Prompt Management API v1\n"
     "The v1 prompt endpoints (`/api/public/prompts`) are deprecated and will be removed from Langfuse Cloud on January 31, 2027. "
     "Upgrade to Python SDK v4.18 or later, which uses the v2 endpoints.",
     ["deprecation"], "호스팅 서비스 API의 deprecation (프롬프트를 Langfuse에 둔다)"),
    ("synthetic:page-diff:openai:changelog@2026-W43", "gpt-6-luna",
     "[openai] https://platform.openai.com/docs/changelog (페이지 변경)",
     "Oct 16\nPricing\nReduced the price of gpt-6-luna by 40%: input $0.06 → $0.036 and output $0.48 → $0.288 per 1M tokens. "
     "Batch API pricing for gpt-6-luna is unchanged.",
     ["pricing"], "우리가 부르는 모델의 가격 변경"),
    ("synthetic:page-diff:anthropic:release-notes-api@2026-W43", "claude-haiku-4-5-20251001",
     "[anthropic] https://docs.claude.com/en/release-notes/api (페이지 변경)",
     "October 16, 2026\nWe've added support for the `between_tools` thinking type to Claude Haiku 4.5 (`claude-haiku-4-5-20251001`).",
     [], "함정: 모델에 기능이 더해졌을 뿐 — 은퇴·가격 변경 아님"),
    ("synthetic:github-release:anthropics/anthropic-sdk-python@v1.11.2", "anthropic",
     "anthropics/anthropic-sdk-python v1.11.2",
     "## 1.11.2 (2026-10-09)\n\nFull Changelog: [v1.11.1...v1.11.2](https://github.com/anthropics/anthropic-sdk-python/compare/v1.11.1...v1.11.2)\n\n"
     "### Documentation\n\n* **security:** clarify how to load API keys from the environment instead of source files\n\n"
     "### Chores\n\n* **internal:** run the security linter on pull requests",
     ["minor_patch"], "함정: 「security」라는 단어가 문서·린터에만 나온다 — 보안 수정이 아님 (D1)"),
]
