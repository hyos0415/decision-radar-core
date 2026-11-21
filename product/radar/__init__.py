"""Decision Radar 제품. 기준: docs/design/comparison-protocol.md.

스파이크(product/spike/)는 기록이다. 여기는 그 위에 올린 제품 패키지다:
  revisit · capture  스파이크 langgraph_spike의 그래프를 옮겨 왔다 (호출 단위 기록을 붙였다)
  detect             새로 만들었다 (스킬 SKILL.md 흐름 2와 같은 명세)
런타임은 처음(0009) LangGraph + LangChain이었고, 지금은 직접 쓴 흐름(asyncio)과 에이전트 루프(loop.py)다 (ADR 0030, HYO-220).
스파이크 langgraph_spike는 그때의 기록으로 남는다 — 그 패키지들을 설치하지 않으면 돌지 않는다.
프롬프트(revisit·capture)·도구 본체·스키마 기반·가드·채점 어댑터는 spike/common을 import한다 — 이유는 README 「설계 판단」.
"""

from . import _spike  # noqa: F401  — spike/common을 import 경로에 올린다
