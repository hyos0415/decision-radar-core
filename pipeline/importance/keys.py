"""관문 백엔드들이 같이 쓰는 것 — `.env` 찾기 · 재시도 정책 · 데이터 경계 가드를 불러오기.
처음엔 agents_backend.py에 있었다 — Agents SDK를 걷어내며(ADR 0030, HYO-220) 따로 뺐다. claude_backend · jev_backend · openai_backend가 쓴다.
"""
import importlib.util
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_retry():
    return _load(REPO / "product" / "radar" / "retry.py", "importance_retry")     # 다른 에이전트 영역 — import만 (수정 금지)


def load_guard():
    return _load(REPO / "product" / "spike" / "common" / "guard.py", "importance_guard")


def find_env_file():
    cands = [os.environ.get("RADAR_ENV_FILE"), REPO / ".env"]
    try:    # worktree면 메인 체크아웃의 .env
        common = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=REPO, capture_output=True, text=True, timeout=10).stdout.strip()
        if common:
            cands.append((REPO / common).resolve().parent / ".env")
    except (OSError, subprocess.SubprocessError):
        pass
    return next((Path(c) for c in cands if c and Path(c).exists()), None)


def env_key(name):
    """`.env`에서 name=… 한 줄만 읽는다 → 값 | None. os.environ에 넣지 않는다."""
    f = find_env_file()
    if f:
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(name + "="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v
    return None
