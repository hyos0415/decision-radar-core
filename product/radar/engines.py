"""실행 엔진 — 에이전트 노드 하나를 무엇으로 돌리나 (HYO-99, 「그래프는 지휘자, 에이전트 노드만 갈아 끼운다」).

그래프(LangGraph)는 그대로다. 노드는 `await runner.run(...)` 한 줄로 에이전트를 부르고 스키마로 검증된 답과 도구 호출 수를 받는다.

  LangChainRunner   지금 코드 — create_agent(모델 ↔ 도구 루프) + ProviderStrategy 구조화 출력. 기본값, 동작 변화 0 (1단계)
  ClaudeCliRunner   구독 `claude -p` 한 번 = 에이전트 한 번, 도구는 읽기 전용 MCP 서버 (2단계)

capture는 루프 중간에 사람 승인(HITL)으로 멈춰야 해서 지금은 엔진을 갈지 않는다 (HYO-99 §1-5 — 5단계).
"""

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import NamedTuple

from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy
from langchain_core.messages import ToolMessage

from common.core import Ctx

from . import llm_log


def limit(max_turns):
    return 2 * max_turns + 2  # create_agent 한 턴 = 모델 노드 + 도구 노드 두 단계


class AgentResult(NamedTuple):
    structured: object   # 스키마(pydantic)로 검증된 최종 답
    tool_calls: int      # 이 실행에서 부른 도구 수 (P3 tool_call_missing)


class LangChainRunner:
    """create_agent를 이름별로 한 번 만들어 두고 다시 쓴다 (전에는 그래프를 만들 때 노드마다 하나씩 만들었다 — 같은 일)."""

    engine = "api"

    def __init__(self, model):
        self.model = model
        self._agents = {}

    def attach_log(self, ctx, flow, model_name):
        return llm_log.attach(self.model, ctx, flow, model_name)

    def _agent(self, name, system, schema, tools):
        if name not in self._agents:
            self._agents[name] = create_agent(self.model, tools=tools, system_prompt=system,
                                              response_format=ProviderStrategy(schema), context_schema=Ctx, name=name)
        return self._agents[name]

    async def run(self, *, name, system, user, schema, tools, ctx, max_turns):
        r = await self._agent(name, system, schema, tools).ainvoke({"messages": [{"role": "user", "content": user}]},
                                                                  context=ctx, config={"recursion_limit": limit(max_turns)})
        return AgentResult(r["structured_response"], sum(isinstance(m, ToolMessage) for m in r["messages"]))


class EngineError(RuntimeError):
    """CLI 엔진 실패 — 로그인 안 됨 · 구독 한도 · 시간 초과 · 결과 모양 이상. 노드가 model_error로 센다 (안전 실패)."""


class CliLog:
    """llm_log.CallLog와 같은 모양의 호출 기록 — CLI 실행 한 번 = llm_call 한 줄 (그 안의 여러 턴을 합친 사용량)."""

    def __init__(self, ctx, flow):
        self.ctx, self.flow, self.calls, self._lock = ctx, flow, [], threading.Lock()

    def add(self, rec):
        with self._lock:
            rec = {"type": "llm_call", "flow": self.flow, **rec, "seq": len(self.calls) + 1}
            self.calls.append(rec)
            self.ctx.events.append(rec)

    def totals(self):
        return {"model_calls": len(self.calls), "input_tokens": sum(c["input_tokens"] for c in self.calls),
                "output_tokens": sum(c["output_tokens"] for c in self.calls)}


MCP_SERVER = Path(__file__).resolve().parent / "mcp_tools.py"
STRIP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")  # 남아 있으면 Claude Code가 API 키로 과금한다 — 구독으로만 돌린다


def _kill_tree(proc):
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass


class ClaudeCliRunner:
    """구독 `claude -p` 한 번 = 에이전트 한 번 (HYO-99 §2, 0단계 스파이크 product/spike/cli_engine/README.md).

    - 빈 실행 폴더 · `--setting-sources ""`(사용자 설정·hook 안 읽음) · `--no-session-persistence` · `--tools ""`(내장 도구 끔)
      · `--strict-mcp-config`로 읽기 MCP 서버(mcp_tools.py)만. `--bare`는 구독 로그인(OAuth)을 안 읽어서 못 쓴다
    - env에서 API 키를 지운다 → 구독(`apiKeySource: none`)으로만. 비용은 `cost_usd: None`, CLI가 주는 정가 환산은 `cost_usd_equiv`
    - 도구 기록(RADAR_TOOL_LOG)을 부모 ctx.events에 합치고 ctx.calls에 더한다 → 채점기·P3(tool_call_missing)가 지금처럼 돈다
    - 동시 실행은 세마포어(구독 한도) · 시간 초과는 프로세스 트리 종료
    """

    engine = "claude-cli"

    def __init__(self, model="claude-sonnet-5-5", effort="medium", *, bin=None, timeout=None, concurrency=None, radar_py=None):
        self.model, self.effort = model, effort
        b = bin or os.environ.get("RADAR_CLAUDE_BIN") or shutil.which("claude") or "claude"
        self.bin = [sys.executable, b] if b.endswith(".py") else [b]  # .py = 테스트용 가짜 CLI (fake_cli.py)
        self.timeout = timeout or int(os.environ.get("RADAR_CLI_TIMEOUT", "300"))
        self._sem = asyncio.Semaphore(concurrency or int(os.environ.get("RADAR_CLI_CONCURRENCY", "2")))
        self.radar_py = radar_py
        self.log = None

    def attach_log(self, ctx, flow, model_name):
        self.log = CliLog(ctx, flow)
        return self.log

    def _cmd(self, system, schema, mcp_cfg, allowed):
        return [*self.bin, "-p", "--output-format", "json", "--system-prompt", system,
                "--json-schema", json.dumps(schema.model_json_schema(), ensure_ascii=False),
                "--tools", "", "--setting-sources", "", "--no-session-persistence",
                "--mcp-config", str(mcp_cfg), "--strict-mcp-config", "--allowedTools", *allowed,
                "--model", self.model, "--effort", self.effort, "--permission-mode", "dontAsk"]

    async def run(self, *, name, system, user, schema, tools, ctx, max_turns):
        from .tools import guarded_fetch, guarded_search  # 순환 import 피함
        names = [t.name for t in tools]
        guard = any(t is guarded_fetch or t is guarded_search for t in tools)
        run = Path(tempfile.mkdtemp(prefix="radar-cli-"))
        log_path, seen_path = run / "tools.jsonl", run / "seen.txt"
        seen_path.write_text(user, encoding="utf-8")
        env = {k: v for k, v in ctx.env.items() if k not in STRIP_ENV}
        env.update({"PYTHONIOENCODING": "utf-8", "RADAR_TOOL_LOG": str(log_path), "RADAR_SEEN_FILE": str(seen_path),
                    "RADAR_PY": self.radar_py or ctx.radar_py})
        for k in STRIP_ENV:
            env.pop(k, None)
        server = {"command": sys.executable, "args": [str(MCP_SERVER), "--allow", ",".join(names), *(["--guard"] if guard else [])], "env": env}
        (run / "mcp.json").write_text(json.dumps({"mcpServers": {"radar": server}}, ensure_ascii=False), encoding="utf-8")
        cmd = self._cmd(system, schema, run / "mcp.json", [f"mcp__radar__{n}" for n in names])
        async with self._sem:
            t0 = time.time()
            proc = await asyncio.create_subprocess_exec(*cmd, cwd=run, env=env, stdin=asyncio.subprocess.PIPE,
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                        **({} if sys.platform == "win32" else {"start_new_session": True}))
            try:
                out, err = await asyncio.wait_for(proc.communicate(user.encode("utf-8")), timeout=self.timeout)
            except asyncio.TimeoutError:
                _kill_tree(proc)
                raise EngineError(f"engine_error: timeout {self.timeout}s ({name})")
            wall = round(time.time() - t0, 1)
        tool_lines = [json.loads(x) for x in log_path.read_text(encoding="utf-8").splitlines()] if log_path.exists() else []
        for t in tool_lines:  # 도구 기록을 부모에 — 스킬 transcript와 같은 모양으로 채점기가 읽는다
            ctx.events.extend(t.get("events") or [])
        ctx.calls += len(tool_lines)
        try:
            res = json.loads(out.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise EngineError(f"engine_error: 결과 JSON 아님 (exit {proc.returncode}) {err.decode('utf-8', 'replace')[-300:]}")
        self._record(name, res, wall, len(tool_lines))
        if res.get("is_error") or res.get("subtype") != "success":
            raise EngineError(f"engine_error: {res.get('subtype')} {str(res.get('result'))[:300]}")
        if res.get("num_turns", 0) > max_turns + 2:  # 도구 없이 2턴 · 도구 n번이면 n+2턴 (스파이크). LangChain 판의 recursion limit와 같은 몫
            raise EngineError(f"engine_error: 턴 {res.get('num_turns')} > {max_turns} ({name})")
        so = res.get("structured_output")
        if so is None:
            raise EngineError(f"engine_error: structured_output 없음 ({name})")
        try:
            return AgentResult(schema.model_validate(so), len(tool_lines))
        except ValueError as ex:  # pydantic ValidationError — CLI가 스키마를 어겼다
            raise EngineError(f"engine_error: 스키마 불일치 ({name}): {str(ex)[:200]}")

    def _record(self, name, res, wall, n_tools):
        if self.log is None:
            return
        u = res.get("usage") or {}
        mu = res.get("modelUsage") or {}
        model = next(iter(mu), None) or self.model
        self.log.add({"agent": name, "model": model, "engine": self.engine, "billing": "subscription",
                      "input_tokens": u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0),
                      "output_tokens": u.get("output_tokens", 0), "estimated": False, "cost_usd": None,
                      "cost_usd_equiv": res.get("total_cost_usd"), "num_turns": res.get("num_turns"), "tool_calls": n_tools,
                      "wall_s": wall, "attempts": 1, **({"error": str(res.get("result"))[:200]} if res.get("is_error") else {})})


def as_runner(model_or_runner):
    """그래프 입구에서 — 모델이 오면 지금처럼 LangChainRunner로 감싼다 (eval 어댑터·데스크톱이 모델을 넘기는 자리를 안 바꾼다)."""
    return model_or_runner if hasattr(model_or_runner, "run") and hasattr(model_or_runner, "attach_log") else LangChainRunner(model_or_runner)
