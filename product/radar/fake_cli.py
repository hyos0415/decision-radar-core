"""가짜 `claude` — 구독 없이 ClaudeCliRunner 배관을 검증한다 (HYO-99 2단계). `RADAR_CLAUDE_BIN=product/radar/fake_cli.py`로 꽂는다.

진짜 CLI처럼 --mcp-config의 MCP 서버를 **실제로** 띄워(stdio) 대본의 도구를 부르고, `--output-format json` 결과 모양으로 답한다.
대본: env RADAR_FAKE_CLI(JSON 파일 경로) 또는 기본(도구 없이 답).
  {"calls": [{"tool": "snapshot_fetch", "args": {"url": "…"}}],   # 차례로 부른다. --allowedTools 밖이면 부르지 않고 permission_denials에
   "structured": {...},                                           # structured_output으로 그대로
   "is_error": false, "subtype": "success", "num_turns": null, "sleep": 0, "stdout": null}   # stdout을 주면 그 글자만 낸다 (모양 이상 시험)
결과에 `_seen_env`(API 키가 env에 있었나 · cwd)를 넣어 runner가 키를 지웠는지 테스트가 확인한다.
"""

import asyncio
import json
import os
import sys
import time


def arg(argv, name, many=False):
    if name not in argv:
        return [] if many else None
    i = argv.index(name) + 1
    if not many:
        return argv[i]
    out = []
    while i < len(argv) and not argv[i].startswith("--"):
        out.append(argv[i])
        i += 1
    return out


async def call_tools(cfg_path, calls, allowed):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    srv = json.load(open(cfg_path, encoding="utf-8"))["mcpServers"]["radar"]
    params = StdioServerParameters(command=srv["command"], args=srv["args"], env=srv.get("env"))
    results, denied = [], []
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            listed = [t.name for t in (await s.list_tools()).tools]
            for c in calls:
                if f"mcp__radar__{c['tool']}" not in allowed or c["tool"] not in listed:
                    denied.append(c["tool"])
                    continue
                res = await s.call_tool(c["tool"], c.get("args") or {})
                results.append({"tool": c["tool"], "text": "".join(getattr(x, "text", "") for x in res.content)})
    return results, denied, listed


def main():
    argv = sys.argv[1:]
    if argv == ["--version"]:          # 진짜 CLI처럼 버전 한 줄 (관문 CLI 백엔드가 실행마다 기록한다, HYO-169)
        print("0.0.0-fake (Claude Code)")
        return
    prompt = sys.stdin.read()
    script = {}
    if os.environ.get("RADAR_FAKE_CLI"):
        script = json.load(open(os.environ["RADAR_FAKE_CLI"], encoding="utf-8"))
    time.sleep(script.get("sleep", 0))
    if script.get("stdout") is not None:
        sys.stdout.write(script["stdout"])
        return
    calls = script.get("calls") or []
    results, denied, listed = (asyncio.run(call_tools(arg(argv, "--mcp-config"), calls, arg(argv, "--allowedTools", many=True)))
                               if arg(argv, "--mcp-config") else ([], [c["tool"] for c in calls], []))
    out = {"type": "result", "subtype": script.get("subtype", "success"), "is_error": script.get("is_error", False),
           "num_turns": script.get("num_turns") or len(results) + 2, "result": json.dumps(script.get("structured")),
           "structured_output": script.get("structured"), "total_cost_usd": 0.0123,
           "usage": {"input_tokens": 5, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 0, "output_tokens": 50},
           "modelUsage": {arg(argv, "--model") or "claude-sonnet-5-5": {}},
           "permission_denials": denied, "_tools_listed": listed, "_tool_results": results,
           "_seen_env": {"api_key": "ANTHROPIC_API_KEY" in os.environ, "auth_token": "ANTHROPIC_AUTH_TOKEN" in os.environ,
                         "cwd": os.getcwd(), "prompt_chars": len(prompt), "tools_flag": arg(argv, "--tools"),
                         "strict_mcp": "--strict-mcp-config" in argv, "setting_sources": arg(argv, "--setting-sources")}}
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
