"""HYO-99 0단계 — 스파이크용 MCP 서버 (stdio). 읽기 도구 하나와 호출 기록만 확인한다.

snapshot_fetch(url)는 고정 글을 돌려주고, 호출을 RADAR_TOOL_LOG(JSONL)에 덧붙인다 — 부모가 도구 수를 셀 수 있는지 본다.
"""
import json
import os
import sys

from mcp.server.mcpserver import MCPServer

app = MCPServer("radar")
PAGES = {
    "github.example/leann/releases": "LEANN v0.5.0 — Added incremental insert support (update the index without a full rebuild).",
}


@app.tool()
def snapshot_fetch(url: str) -> str:
    """수집 스냅숏에서 URL 하나의 본문을 연다 (읽기 전용)."""
    log = os.environ.get("RADAR_TOOL_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"tool": "snapshot_fetch", "input": {"url": url}}, ensure_ascii=False) + "\n")
    body = PAGES.get(url.replace("https://", "").rstrip("/"))
    return json.dumps({"ok": body is not None, "url": url, "body": body}, ensure_ascii=False)


if __name__ == "__main__":
    print("radar spike MCP server", file=sys.stderr)
    app.run("stdio")
