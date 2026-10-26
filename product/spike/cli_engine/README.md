# HYO-99 0단계 스파이크 — `claude -p`를 에이전트 노드 하나로 (2026-10-26)

`spike.py` · `spike_server.py`(스파이크용 MCP 서버). Claude Code **2.1.310**, Windows, 구독 로그인. 세션 7개.

| 확인 | 결과 |
|---|---|
| 구조화 출력 | `--output-format json --json-schema '<스키마>'` → 결과 JSON의 **`structured_output`**(객체)과 `result`(같은 내용의 문자열). `is_error` · `subtype: success` · `terminal_reason: completed` · `num_turns` · `stop_reason: tool_use`(구조화 출력이 도구로 구현됨) |
| 사용량·모델 | `usage`(입력·출력·캐시 생성/읽기·thinking) · `modelUsage.{모델}.canonicalModel = claude-sonnet-5-5` · `total_cost_usd`(정가 환산 — 구독이면 `cost_usd_equiv`로만) |
| 구독으로 도는지 | env에서 `ANTHROPIC_API_KEY`·`ANTHROPIC_AUTH_TOKEN`을 지우고 `--output-format stream-json --verbose` 첫 줄(init)의 **`apiKeySource: "none"`** = OAuth(구독) |
| **`--bare` 못 씀** | `--bare`는 OAuth·keychain을 읽지 않는다(API 키·apiKeyHelper만) → 구독 실행 불가. 대신 `--setting-sources ""`(빈 값 허용됨) · 빈 cwd · `--no-session-persistence` · `--system-prompt`로 격리 |
| MCP 도구만 | `--tools ""` → init의 `tools: []`. `--mcp-config <json> --strict-mcp-config --allowedTools mcp__radar__snapshot_fetch` → 모델이 그 도구 하나만 보고 1회 호출, 서버가 `RADAR_TOOL_LOG`에 남긴 기록을 부모가 읽음 |
| 막혀야 할 것 (규율 3) | 임시 파일(SECRET 값) 읽기·`https://example.com` 열기를 시킴 → 파일 도구 없음이라 답하고 값 노출 0 · 웹은 `snapshot_fetch`로만 시도해 `ok: false`. 내장 도구·웹이 없다 |
| Windows | stdin 한국어 그대로 · 6,000자 넘는 `--json-schema` 인자 OK · 한 번에 4~9초 |
| 권한 | `--permission-mode dontAsk` — 허용 목록 밖 요청 없음(`permission_denials: []`) |

남은 확인(2단계에서): 시간 초과 때 프로세스 트리 종료(Windows `taskkill /T`) · 구독 한도 오류 모양 · Codex(`codex exec`)는 11/2 뒤
