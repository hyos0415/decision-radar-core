"""읽기 도구 MCP 서버 (stdio) — 구독 CLI 엔진(`claude -p`)에게 제품의 읽기 도구를 준다 (HYO-99 §1-3).

  python product/radar/mcp_tools.py --allow snapshot_fetch,snapshot_search [--guard]

- 도구 본체는 LangChain 도구(tools.py)와 같다 — common/ops를 그대로 부르고, 설명도 같은 글자(ops.DOCS · tools.GUARDED_DOCS)
- **쓰기 도구는 없다** (propose_adr 안 줌). ADR 쓰기는 지금처럼 그래프의 코드(write 노드 · hitl.write_adr)만 → R8·R9가 구조로 지켜진다
- `--guard`(revisit 판정): 본 글(첫 프롬프트 RADAR_SEEN_FILE + 도구 결과)에 나온 URL만 열고, 열기·검색 합쳐 LOOKUP_BUDGET회 (HYO-137) —
  LangChain 판과 같은 함수(tools.check_lookup). 거절도 열기·검색 수에 든다(LangChain 판에서 거절이 ToolMessage로 세어지는 것과 같게)
- 호출 기록: 도구 한 번마다 RADAR_TOOL_LOG(JSONL)에 {"tool", "input", "events"} 한 줄 — events는 ops가 ctx.record로 남긴 것(스킬 transcript 모양).
  부모(engines.ClaudeCliRunner)가 실행 뒤 읽어 자기 ctx.events에 합치고 도구 수를 센다
- 환경: 부모가 넘긴 RADAR_* (ADR 폴더 · 스냅숏 · dry-run …)로 Ctx.from_env. radar.py 경로는 RADAR_PY (없으면 스킬 radar.py)
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # product/

from mcp.server.mcpserver import MCPServer  # noqa: E402

import radar  # noqa: E402,F401  — spike/common을 import 경로에 올린다 (radar/_spike)
from common import ops  # noqa: E402
from common.core import SKILL_RADAR, Ctx  # noqa: E402
from radar.tools import GUARDED_DOCS, LOOKUPS, _SCHEME, check_lookup  # noqa: E402

READ_TOOLS = ("snapshot_fetch", "snapshot_search", "read_repo_file")


class Session:
    """서버 프로세스 하나 = 에이전트 실행 한 번. 본 글·열기 수·기록을 여기 둔다."""

    def __init__(self, env, guard):
        self.ctx = Ctx.from_env(env, radar_py=env.get("RADAR_PY") or SKILL_RADAR)
        self.guard = guard
        seen = env.get("RADAR_SEEN_FILE")
        self.seen = _SCHEME.sub("", Path(seen).read_text(encoding="utf-8")) if seen and Path(seen).exists() else ""
        self.lookups = 0
        self.log = env.get("RADAR_TOOL_LOG")

    def call(self, name, inp, fn):
        before = len(self.ctx.events)
        refused = None
        if self.guard and name in LOOKUPS:
            refused = check_lookup(self.seen, self.lookups, inp.get("url") if name == "snapshot_fetch" else None)
            self.lookups += 1
        out = json.dumps(refused, ensure_ascii=False) if refused else fn()
        self.seen += "\n" + _SCHEME.sub("", out)
        if self.log:
            with open(self.log, "a", encoding="utf-8") as f:
                f.write(json.dumps({"tool": name, "input": inp, "refused": bool(refused), "events": self.ctx.events[before:]},
                                   ensure_ascii=False) + "\n")
        return out


def build(allow, guard, env=None):
    s = Session(dict(os.environ) if env is None else env, guard)
    app = MCPServer("radar")
    docs = {**ops.DOCS, **(GUARDED_DOCS if guard else {})}

    if "snapshot_fetch" in allow:
        @app.tool(name="snapshot_fetch", description=docs["snapshot_fetch"])
        def snapshot_fetch(url: str) -> str:
            return s.call("snapshot_fetch", {"url": url}, lambda: ops.snapshot_fetch(s.ctx, url))

    if "snapshot_search" in allow:
        @app.tool(name="snapshot_search", description=docs["snapshot_search"])
        def snapshot_search(query: str) -> str:
            return s.call("snapshot_search", {"query": query}, lambda: ops.snapshot_search(s.ctx, query))

    if "read_repo_file" in allow:
        @app.tool(name="read_repo_file", description=docs["read_repo_file"])
        def read_repo_file(repo: str, path: str) -> str:
            return s.call("read_repo_file", {"repo": repo, "path": path}, lambda: ops.read_repo_file(s.ctx, repo, path))

    return app, s


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow", required=True, help="쉼표로 — " + ",".join(READ_TOOLS))
    ap.add_argument("--guard", action="store_true", help="revisit 판정용 가드(본 URL만 · 열기·검색 예산)")
    a = ap.parse_args(argv)
    allow = [x for x in a.allow.split(",") if x]
    bad = [x for x in allow if x not in READ_TOOLS]
    if bad:
        raise SystemExit(f"읽기 도구만 줄 수 있다: {bad}")
    app, _ = build(allow, a.guard)
    app.run("stdio")


if __name__ == "__main__":
    main()
