"""API 키·모델·단가 — 두 런타임 공통."""

import os

from .core import ROOT

MODEL = "claude-sonnet-5-5"  # 비교 모드 (비교 프로토콜 P1, e2 결정) — effort medium
EFFORT = "medium"
PRICE = {"input": 2.00, "output": 10.00}  # $/MTok — claude-api 스킬 모델 표 (cached 2026-09-25)


def load_dotenv(path=os.path.join(ROOT, ".env")):
    """KEY=VALUE 줄만 읽는다. os.environ에 넣지 않는다 — radar.py 하위 프로세스에 키가 흘러가지 않게."""
    out = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def anthropic_key():
    key = load_dotenv().get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit(".env에 ANTHROPIC_API_KEY가 없다")
    return key


def cost_usd(input_tokens, output_tokens):
    return round(input_tokens / 1e6 * PRICE["input"] + output_tokens / 1e6 * PRICE["output"], 5)
