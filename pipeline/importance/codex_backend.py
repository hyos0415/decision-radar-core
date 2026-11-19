"""구독 `codex exec` 백엔드 — GPT Sol을 Claude CLI 후보와 같은 조건으로 잰다 (HYO-169, 2026-11-12 사용자: 「Sol도 test로 — API로 하면 공정하지 않다」).

- 모델 이름 `codex-cli/<모델 id>` (예 `codex-cli/gpt-6-sol`) — make_backend가 고른다. effort medium (Claude CLI 후보 · 운영 Sol API와 같다)
- ChatGPT 구독의 codex는 운영 모델 `gpt-6.1-sol`을 받지 않는다(`model_not_found`, 11/12 확인) — 한 판 앞 `gpt-6-sol`만 된다
- `ClaudeCliBackend`의 문구(`prompts_cli`) · 묶음 · 순서 섞기 · 번호 되짚기 · 안전 실패를 그대로 쓴다. 다른 것은 실행 한 번의 모양뿐이다
- codex에는 시스템 프롬프트를 바꾸는 플래그가 없다 — 분류 지시를 사용자 메시지 앞에 붙인다. codex 자체 기본 지시(약 1.4만 토큰)는 그대로 붙는다
- 격리: 빈 실행 폴더 · `--ignore-user-config` · `mcp_servers={}` · `--ephemeral` · `-s read-only` · 프롬프트는 stdin(`-`) ·
  `--output-schema`(묶음 · 단건 스키마) · `-o`(마지막 답 파일) · `--json`(사용량 이벤트). Windows는 `windows.sandbox="unelevated"`
- env에서 OpenAI 키를 지운다 → ChatGPT 구독으로만
- 실패(로그인 · 한도 · 시간 초과 · 마지막 답 없음 · JSON 아님)는 CliError — 한 번 더 시도하고, 관문 · 평가는 안전 실패로 다룬다
- 테스트: env `RADAR_CODEX_BIN`에 `.py`(가짜 codex, `pipeline/tests/fake_codex.py`)를 꽂는다
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .cli_backend import CliError, ClaudeCliBackend, _kill_tree

PREFIX = "codex-cli/"
STRIP_ENV = ("OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
TASK_HEAD = ("아래 지시대로 분류 답만 낸다. 셸 · 파일 · 웹 같은 도구를 쓰지 않는다. "
             "최종 답은 주어진 JSON 스키마 하나뿐이다.\n\n")
SEP = "\n\n---\n\n"


class CodexCliBackend(ClaudeCliBackend):
    engine = "codex-cli"

    def __init__(self, model, *, log=None, effort=None, bin=None, timeout=None, batch=None, seed=None):
        b = bin or os.environ.get("RADAR_CODEX_BIN") or shutil.which("codex") or "codex"
        super().__init__(model, log=log, effort=effort, bin=b, timeout=timeout, batch=batch, seed=seed)
        self.model = model[len(PREFIX):] if model.startswith(PREFIX) else model
        self.name = PREFIX + self.model

    @staticmethod
    def _env():
        env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def _cmd(self, schema_path, last_path):
        cmd = [*self.bin, "exec", "-m", self.model, "-s", "read-only", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config",
               "-c", f'model_reasoning_effort="{self.effort}"', "-c", "mcp_servers={}",
               "--output-schema", str(schema_path), "-o", str(last_path), "--json"]
        if sys.platform == "win32":
            cmd += ["-c", 'windows.sandbox="unelevated"']
        return cmd + ["-"]

    def _once(self, system, prompt, schema, timeout=None):
        """한 번 실행 → (Claude CLI 결과 모양으로 맞춘 dict, 걸린 초). 실패는 CliError."""
        timeout = timeout or self.timeout
        with tempfile.TemporaryDirectory(prefix="radar-gate-codex-") as run:
            sp, lp = Path(run) / "schema.json", Path(run) / "last.json"
            sp.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
            t0 = time.perf_counter()
            proc = subprocess.Popen(self._cmd(sp, lp), cwd=run, env=self._env(), stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                out, err = proc.communicate((TASK_HEAD + system + SEP + prompt).encode("utf-8"), timeout=timeout)
            except subprocess.TimeoutExpired:
                _kill_tree(proc)
                proc.communicate()
                raise CliError(f"timeout {timeout}s")
            wall = round(time.perf_counter() - t0, 3)
            last = lp.read_text(encoding="utf-8") if lp.exists() else None
        usage, failed = {}, None
        for line in out.decode("utf-8", "replace").splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "turn.completed":
                usage = ev.get("usage") or {}
            elif ev.get("type") in ("turn.failed", "error"):
                failed = str((ev.get("error") or {}).get("message") or ev.get("message"))[:300]
        if failed or not (last or "").strip():
            raise CliError(f"codex 실패 (exit {proc.returncode}) {failed or err.decode('utf-8', 'replace')[-300:]}")
        try:
            so = json.loads(last)
        except json.JSONDecodeError:
            raise CliError(f"마지막 답이 JSON 아님: {last[:200]}")
        return {"subtype": "success", "is_error": False, "structured_output": so, "num_turns": 1, "total_cost_usd": None,
                "usage": {"input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0)},
                "modelUsage": {self.model: {}}}, wall
