"""구독 CLI 백엔드 — `claude -p`로 등급 · 변화 종류를 받는다 (HYO-169 첫 조각: Claude 쪽).

- 모델 이름 `claude-cli/<모델 id>` (예 `claude-cli/claude-sonnet-5-5`) — make_backend가 고른다. effort는 medium (CLAUDE.md 「외부 모델·서비스」)
- 변화 분류 프롬프트는 `prompts_cli.py`(0판 = `prompts.CHANGE_INSTRUCTIONS` 그대로 — 다듬는 대상은 그 파일뿐, 운영 Sol 프롬프트와 따로) · 등급은 `prompts.py` 그대로
- 기본은 **소식 하나 = CLI 실행 한 번.** `batch=N`이면 묶음 모드(2026-11-11 등록, Pro 한도): `prefetch_changes(소식들)`이 차례마다 시드
  (`seed` + 차례 번호)로 순서를 섞어 N개씩 한 세션에 묻고 답을 쌓는다 — 그 뒤 `classify_changes`는 쌓인 답을 돌려준다. 실패한 묶음 ·
  답에 빠진 번호는 그 소식만 안전 실패(CliError). 묶음 구성(시드 · id 순서)은 호출 기록에 남긴다(순서 효과를 센다)
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
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import policy
from .backends import CHANGE_SET, TIER_SET, Backend, LabelAnswer, check_labels
from .claude_backend import CHANGE_SCHEMA, TIER_SCHEMA, _canon
from .prompts import TIER_INSTRUCTIONS, change_prompt, tier_prompt
from .prompts_cli import BATCH_SCHEMA, CLI_CHANGE_INSTRUCTIONS, ROUND, batch_instructions, batch_prompt

PREFIX = "claude-cli/"
DEFAULT_EFFORT = "medium"
STRIP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")   # 남아 있으면 Claude Code가 API 키로 과금한다
ATTEMPTS = 2
BATCH_TIMEOUT_FACTOR = 3     # 묶음 한 세션의 시간 한도 = 단건 한도 × 3


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

    def __init__(self, model, *, log=None, effort=None, bin=None, timeout=None, batch=None, seed=None):
        super().__init__(log)
        self.model = model[len(PREFIX):] if model.startswith(PREFIX) else model
        self.name, self.effort = PREFIX + self.model, effort or DEFAULT_EFFORT
        b = bin or os.environ.get("RADAR_CLAUDE_BIN") or shutil.which("claude") or "claude"
        self.bin = [sys.executable, b] if b.endswith(".py") else [b]
        self.timeout = timeout or int(os.environ.get("RADAR_CLI_TIMEOUT", "180"))
        self._version = None
        self.batch = int(batch) if batch and int(batch) > 1 else None
        self.seed = int(seed or 0)
        self._round, self._cache = 0, {}

    @staticmethod
    def _env():
        env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def cli_version(self):
        """`claude --version` — 구독 엔진의 비결정 입력이라 실행마다 결과에 남긴다 (확정 시트 precondition-hyo169 §3). 못 읽으면 unknown."""
        if self._version is None:
            try:
                r = subprocess.run([*self.bin, "--version"], capture_output=True, stdin=subprocess.DEVNULL, env=self._env(), timeout=60)
                self._version = (r.stdout or r.stderr).decode("utf-8", "replace").strip()[:80] or "unknown"
            except (OSError, subprocess.TimeoutExpired) as e:
                self._version = f"unknown ({type(e).__name__})"
        return self._version

    def run_info(self):
        return {"engine": self.engine, "model": self.model, "effort": self.effort, "cli_version": self.cli_version(),
                "prompt_round": ROUND, "batch": self.batch, "seed": self.seed}

    def _cmd(self, system, schema):
        return [*self.bin, "-p", "--output-format", "json", "--system-prompt", system,
                "--json-schema", json.dumps(schema, ensure_ascii=False),
                "--tools", "", "--setting-sources", "", "--no-session-persistence", "--strict-mcp-config",
                "--model", self.model, "--effort", self.effort, "--permission-mode", "dontAsk"]

    def _once(self, system, prompt, schema, timeout=None):
        """한 번 실행 → (결과 JSON, 걸린 초). 실패는 CliError."""
        timeout = timeout or self.timeout
        with tempfile.TemporaryDirectory(prefix="radar-gate-cli-") as run:
            t0 = time.perf_counter()
            proc = subprocess.Popen(self._cmd(system, schema), cwd=run, env=self._env(), stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                out, err = proc.communicate(prompt.encode("utf-8"), timeout=timeout)
            except subprocess.TimeoutExpired:
                _kill_tree(proc)
                proc.communicate()
                raise CliError(f"timeout {timeout}s")
            wall = round(time.perf_counter() - t0, 3)
        try:
            res = json.loads(out.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise CliError(f"결과 JSON 아님 (exit {proc.returncode}) {err.decode('utf-8', 'replace')[-300:]}")
        return res, wall

    def _run(self, flow, system, prompt, schema, extra=None, timeout=None):
        rec = {"flow": flow, "model": self.model, "engine": self.engine, "billing": "subscription", "effort": self.effort, "estimated": False,
               **(extra or {})}
        last = None
        for attempt in range(1, ATTEMPTS + 1):
            res, wall = None, 0.0
            try:
                res, wall = self._once(system, prompt, schema, timeout)
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
        if self.batch and q.id in self._cache:
            got = self._cache[q.id]
            if isinstance(got, Exception):
                raise got
            return got
        out = self._run("changes", CLI_CHANGE_INSTRUCTIONS, change_prompt(q), CHANGE_SCHEMA)
        return LabelAnswer(labels=check_labels(_canon(out.get("changes") or [], policy.MODEL_CHANGES), CHANGE_SET, "changes"),
                           reason=out.get("reason"))

    def prefetch_changes(self, queries):
        """묶음 모드: 이번 차례 시드로 순서를 섞어 batch개씩 한 세션에 묻는다 → {id: LabelAnswer | CliError}를 쌓는다 (차례마다 새로).
        묶음 모드가 아니면 아무것도 안 한다. 같은 id가 두 번 오면 처음 것만 묻는다."""
        if not self.batch:
            return
        seed = self.seed + self._round
        self._round += 1
        seen, qs = set(), []
        for q in queries:
            if q.id not in seen:
                seen.add(q.id)
                qs.append(q)
        random.Random(seed).shuffle(qs)
        self._cache = {}
        for start in range(0, len(qs), self.batch):
            chunk = qs[start:start + self.batch]
            try:
                out = self._run("changes-batch", batch_instructions(), batch_prompt(chunk), BATCH_SCHEMA,
                                extra={"batch_seed": seed, "batch_ids": [q.id for q in chunk]}, timeout=self.timeout * BATCH_TIMEOUT_FACTOR)
            except CliError as e:
                for q in chunk:
                    self._cache[q.id] = e
                continue
            rows = {}
            for row in out.get("items") or []:
                n = row.get("n") if isinstance(row, dict) else None
                if isinstance(n, int) and 1 <= n <= len(chunk) and n not in rows:
                    rows[n] = row
            for n, q in enumerate(chunk, 1):
                row = rows.get(n)
                if row is None:
                    self._cache[q.id] = CliError(f"묶음 답에 [{n}]이 없다 (시드 {seed})")
                    continue
                try:
                    labels = check_labels(_canon(row.get("changes") or [], policy.MODEL_CHANGES), CHANGE_SET, "changes")
                    self._cache[q.id] = LabelAnswer(labels=labels, reason=row.get("reason"))
                except ValueError as e:
                    self._cache[q.id] = CliError(str(e))
