"""API 키·모델·단가 — 두 런타임 공통."""

import os

from .core import ROOT

MODEL = "claude-sonnet-5-5"  # 비교 모드 (비교 프로토콜 P1, e2 결정) — effort medium
EFFORT = "medium"
PRICE = {"input": 2.00, "output": 10.00}  # $/MTok — claude-api 스킬 모델 표 (cached 2026-09-25)
# 모델별 단가 ($/MTok) — 앱이 호출마다 비용을 보인다 (HYO-98). 표에 없는 모델은 비용 None(모름)이지 0이 아니다.
# 로컬 모델(HYO-90 Ollama 등)은 과금이 없어 0. pipeline/importance/agents_backend.py의 PRICES와 같은 방식
PRICES = {MODEL: PRICE}
LOCAL_MODEL_PREFIXES = ("ollama/", "ollama:", "local/")


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


def cost_for(model, input_tokens, output_tokens):
    """모델 이름으로 단가를 찾는다 — 응답의 모델 이름에 날짜가 붙어도(`claude-sonnet-5-5-2026…`) 앞부분으로 맞춘다. 모르면 None."""
    m = (model or "").lower()
    if m.startswith(LOCAL_MODEL_PREFIXES):
        return 0.0
    p = next((v for k, v in PRICES.items() if m == k or m.startswith(k + "-")), None)
    return None if p is None else round(input_tokens / 1e6 * p["input"] + output_tokens / 1e6 * p["output"], 5)
