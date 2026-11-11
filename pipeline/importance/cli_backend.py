"""구독 CLI 백엔드 — `claude -p`로 등급 · 변화 종류를 받는다 (HYO-169 첫 조각: Claude 쪽).

- 모델 이름 `claude-cli/<모델 id>` (예 `claude-cli/claude-sonnet-5-5`) — make_backend가 고른다. effort는 medium (CLAUDE.md 「외부 모델·서비스」)
- 프롬프트 · 스키마는 API 백엔드와 같다 (`prompts.py` · `claude_backend.CHANGE_SCHEMA` · `TIER_SCHEMA`) — 엔진만 바꿔 같은 정답지로 견준다
- **소식 하나 = CLI 실행 한 번.** 묶지 않는다 — 묶으면 프롬프트가 달라져 등록된 분류기를 처음부터 다시 재야 한다
- 격리 (HYO-99 0단계 스파이크 `product/spike/cli_engine/README.md`): 빈 실행 폴더 · `--setting-sources ""`(사용자 설정 · hook 안 읽음)
  · `--no-session-persistence` · `--tools ""`(내장 도구 끔) · `--strict-mcp-config`(MCP 서버 없음) · `--system-prompt`(Claude Code 기본 프롬프트 대신 분류 지시).
  `--bare`는 구독 로그인을 안 읽어서 못 쓴다
- env에서 API 키를 지운다 → 구독으로만 (`product/radar/engines.STRIP_ENV`와 같은 것). 비용 `cost_usd: None`, CLI가 주는 정가 환산은 `cost_usd_equiv`
- 실패(로그인 · 구독 한도 · 시간 초과 · 결과 모양 이상)는 CliError — 관문은 안전 실패(status error)로 기록하고, 평가는 버림으로 센다.
  시간 초과 · 결과 JSON 아님 · is_error는 한 번 더 시도한다(구독 한도 오류의 모양은 아직 모른다 — 기록에 그대로 남긴다)
- 테스트: env `RADAR_CLAUDE_BIN`에 `.py`(가짜 CLI, `product/radar/fake_cli.py`)를 꽂는다
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import policy
from .backends import CHANGE_SET, TIER_SET, Backend, LabelAnswer, check_labels
from .claude_backend import CHANGE_SCHEMA, TIER_SCHEMA, _canon
from .prompts import CHANGE_INSTRUCTIONS, TIER_INSTRUCTIONS, change_prompt, tier_prompt

PREFIX = "claude-cli/"
DEFAULT_EFFORT = "medium"
STRIP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")   # 남아 있으면 Claude Code가 API 키로 과금한다
ATTEMPTS = 2


class CliError(RuntimeError):
    """CLI 실행 실패 — 안전 실패로 다룬다."""


def _kill_tree(proc):
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        else:
            proc.kill()
    except (ProcessLookupError, OSError):
        pass


class ClaudeCliBackend(Backend):
    engine = "claude-cli"

    def __init__(self, model, *, log=None, effort=None, bin=None, timeout=None):
        super().__init__(log)
        self.model = model[len(PREFIX):] if model.startswith(PREFIX) else model
        self.name, self.effort = PREFIX + self.model, effort or DEFAULT_EFFORT
        b = bin or os.environ.get("RADAR_CLAUDE_BIN") or shutil.which("claude") or "claude"
        self.bin = [sys.executable, b] if b.endswith(".py") else [b]
        self.timeout = timeout or int(os.environ.get("RADAR_CLI_TIMEOUT", "180"))

    def _cmd(self, system, schema):
        return [*self.bin, "-p", "--output-format", "json", "--system-prompt", system,
                "--json-schema", json.dumps(schema, ensure_ascii=False),
                "--tools", "", "--setting-sources", "", "--no-session-persistence", "--strict-mcp-config",
                "--model", self.model, "--effort", self.effort, "--permission-mode", "dontAsk"]

    def _once(self, system, prompt, schema):
        """한 번 실행 → (결과 JSON, 걸린 초). 실패는 CliError."""
        env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
        env["PYTHONIOENCODING"] = "utf-8"
        with tempfile.TemporaryDirectory(prefix="radar-gate-cli-") as run:
            t0 = time.perf_counter()
            proc = subprocess.Popen(self._cmd(system, schema), cwd=run, env=env, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                out, err = proc.communicate(prompt.encode("utf-8"), timeout=self.timeout)
            except subprocess.TimeoutExpired:
                _kill_tree(proc)
                proc.communicate()
                raise CliError(f"timeout {self.timeout}s")
            wall = round(time.perf_counter() - t0, 3)
        try:
            res = json.loads(out.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise CliError(f"결과 JSON 아님 (exit {proc.returncode}) {err.decode('utf-8', 'replace')[-300:]}")
        return res, wall

    def _run(self, flow, system, prompt, schema):
        rec = {"flow": flow, "model": self.model, "engine": self.engine, "billing": "subscription", "effort": self.effort, "estimated": False}
        last = None
        for attempt in range(1, ATTEMPTS + 1):
            res, wall = None, 0.0
            try:
                res, wall = self._once(system, prompt, schema)
                if res.get("is_error") or res.get("subtype") != "success":
                    raise CliError(f"{res.get('subtype')} {str(res.get('result'))[:200]}")
                so = res.get("structured_output")
                if not isinstance(so, dict):
                    raise ValueError("structured_output 없음")
                self._record(rec, res, wall, attempt)
                return so
            except CliError as e:
                last = e
                self._record(rec, res, wall, attempt, error=f"CliError: {str(e)[:200]}")
            except ValueError as e:        # 모양 이상은 다시 해도 같다 — 바로 올린다
                self._record(rec, res, wall, attempt, error=f"ValueError: {str(e)[:200]}")
                raise CliError(str(e)) from e
        raise last

    def _record(self, rec, res, wall, attempt, error=None):
        res = res or {}
        u = res.get("usage") or {}
        served = next(iter(res.get("modelUsage") or {}), None)
        self.log.add({**rec, **({"served_model": served} if served else {}),
                      "input_tokens": u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0),
                      "output_tokens": u.get("output_tokens", 0), "cost_usd": None, "cost_usd_equiv": res.get("total_cost_usd"),
                      "num_turns": res.get("num_turns"), "latency_s": wall, "attempts": attempt, **({"error": error} if error else {})})

    def classify_tiers(self, q):
        out = self._run("tiers", TIER_INSTRUCTIONS, tier_prompt(q), TIER_SCHEMA)
        labels = check_labels(_canon(out.get("tiers") or [], policy.TIERS), TIER_SET, "tiers")
        return LabelAnswer(labels=[] if out.get("unsure") else labels, unsure=bool(out.get("unsure")) or not labels, reason=out.get("reason"))

    def classify_changes(self, q):
        out = self._run("changes", CHANGE_INSTRUCTIONS, change_prompt(q), CHANGE_SCHEMA)
        return LabelAnswer(labels=check_labels(_canon(out.get("changes") or [], policy.MODEL_CHANGES), CHANGE_SET, "changes"),
                           reason=out.get("reason"))
