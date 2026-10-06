"""Decision Radar 제품 — LangGraph 비교 모드 (ADR 0009). 기준: docs/design/comparison-protocol.md.

스파이크(product/spike/)는 기록이다. 여기는 그 위에 올린 제품 패키지다:
  revisit · capture  스파이크 langgraph_spike의 그래프를 옮겨 왔다 (호출 단위 기록을 붙였다)
  detect             새로 만들었다 (스킬 SKILL.md 흐름 2와 같은 명세)
프롬프트(revisit·capture)·도구 본체·스키마 기반·가드·채점 어댑터는 spike/common을 import한다 — 이유는 README 「설계 판단」.
"""

import os

os.environ["LANGSMITH_TRACING"] = "false"  # P5 — LangSmith로 실행을 올리지 않는다 (langchain import보다 먼저)
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from . import _spike  # noqa: E402,F401  — spike/common을 import 경로에 올린다
