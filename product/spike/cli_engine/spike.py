"""HYO-99 0단계 스파이크 — `claude -p`를 제품 에이전트 노드 하나로 쓸 수 있는지 확인한다.

  .venv/Scripts/python product/spike/cli_engine/spike.py <check> [--keep]

check:
  shape     구조화 출력(--json-schema) 결과 JSON 모양 · usage · 과금 표시 · 모델 이름
  mcp       --tools "" + --strict-mcp-config로 MCP 도구만 보이고, 도구 호출이 RADAR_TOOL_LOG에 남는지
  deny      도구를 끈 상태에서 파일 읽기·웹 요청을 시키면 못 하는지 (규율 3 — 막혀야 할 게 막히는지)
  korean    stdin 한국어 프롬프트 · 긴 스키마 인자 (Windows)

공통: 빈 실행 폴더(cwd) · env에서 ANTHROPIC_API_KEY·ANTHROPIC_AUTH_TOKEN 제거(구독으로만) · --setting-sources "" · --no-session-persistence.
--bare는 쓰지 않는다 — OAuth(구독 로그인)를 읽지 않는다 (claude --help, 2.1.310).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable

VERDICT = {
    "type": "object",
    "properties": {
        "result": {"type": "string", "enum": ["met", "not_met", "undetermined"]},
        "reason": {"type": "string"},
        "evidence_urls": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["result", "reason", "evidence_urls"],
    "additionalProperties": False,
}
SYSTEM = ("너는 기술 결정 기록(ADR)의 재검토 조건 하나를 판정한다. 주어진 도구로 출처를 열어 확인하고, "
          "확인하지 못하면 undetermined와 이유를 적는다. 지어내지 않는다.")


def run_claude(prompt, *, schema=None, tools_mcp=None, allowed=(), extra=(), timeout=300):
    run = Path(tempfile.mkdtemp(prefix="radar-cli-"))
    log = run / "tools.jsonl"
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    env["PYTHONIOENCODING"] = "utf-8"
    env["RADAR_TOOL_LOG"] = str(log)
    cmd = [shutil.which("claude") or "claude", "-p", "--output-format", "json", "--system-prompt", SYSTEM,
           "--tools", "", "--setting-sources", "", "--no-session-persistence",
           "--model", "claude-sonnet-5-5", "--effort", "medium", "--permission-mode", "dontAsk"]
    if schema:
        cmd += ["--json-schema", json.dumps(schema, ensure_ascii=False)]
    if tools_mcp:
        cfg = run / "mcp.json"
        cfg.write_text(json.dumps({"mcpServers": {"radar": {"command": PY, "args": [str(tools_mcp)], "env": {"RADAR_TOOL_LOG": str(log)}}}}), encoding="utf-8")
        cmd += ["--mcp-config", str(cfg), "--strict-mcp-config"]
    if allowed:
        cmd += ["--allowedTools", *allowed]
    cmd += list(extra)
    t0 = time.time()
    p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", cwd=run, env=env, timeout=timeout)
    wall = time.time() - t0
    tools = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    try:
        out = json.loads(p.stdout)
    except json.JSONDecodeError:
        out = {"_raw_stdout": p.stdout[:2000]}
    return {"exit": p.returncode, "wall_s": round(wall, 1), "stderr": p.stderr[-1500:], "out": out, "tool_log": tools, "run": str(run)}


def show(r):
    o = dict(r["out"])
    for k in ("result",):
        if isinstance(o.get(k), str) and len(o[k]) > 400:
            o[k] = o[k][:400] + "…"
    print(json.dumps({**r, "out": o}, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("check", choices=["shape", "mcp", "deny", "korean"])
    a = ap.parse_args()
    srv = HERE / "spike_server.py"
    if a.check == "shape":
        show(run_claude("조건: LEANN이 incremental insert를 공식 지원한다. 도구 없이 판단할 수 없으면 undetermined.", schema=VERDICT))
    elif a.check == "mcp":
        show(run_claude("조건: LEANN이 incremental insert를 공식 지원한다. 출처 github.example/leann/releases 를 snapshot_fetch로 열어 판정하라.",
                        schema=VERDICT, tools_mcp=srv, allowed=["mcp__radar__snapshot_fetch"]))
    elif a.check == "deny":
        secret = Path(tempfile.gettempdir()) / "radar-spike-secret.txt"
        secret.write_text("SECRET-7731", encoding="utf-8")
        show(run_claude(f"다음 두 가지를 해 보고 결과를 그대로 적어라: (1) 파일 {secret} 내용 읽기 (2) https://example.com 열기. "
                        "할 수 있는 도구가 없으면 그렇다고 적어라.", tools_mcp=srv, allowed=["mcp__radar__snapshot_fetch"]))
    elif a.check == "korean":
        big = {**VERDICT, "description": "가" * 6000}
        show(run_claude("조건: 「한국어 입력이 깨지지 않는다」. 입력의 이 문장(한국어)을 reason에 그대로 옮기고 result는 met.", schema=big))


if __name__ == "__main__":
    main()
