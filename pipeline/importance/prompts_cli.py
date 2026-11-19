"""구독 `claude -p` 관문(HYO-169)의 변화 분류 프롬프트 — 다듬는 대상은 이 파일뿐이다.

- 운영(Sol API · `prompts.CHANGE_INSTRUCTIONS`)과 따로 둔다: 여기를 고쳐도 지금 도는 관문은 바뀌지 않는다
- 0판 = `prompts.CHANGE_INSTRUCTIONS` 그대로 (다듬기 전 기준). 판을 바꿀 때마다 아래 ROUND와 고친 줄을 남기고 커밋한다 —
  커밋 해시가 고정 대상이다 (eval/importance/precondition-hyo169.md §3)
- 묶음: 소식 여러 개를 한 세션에 [1]…[N]으로 넣고 소식마다 답을 받는다 (Pro 한도 · 확정 시트 §11 「묶음」). 소식마다 따로 판단하라는 줄을 더한다
"""
from . import policy
from .prompts import CHANGE_INSTRUCTIONS, change_prompt

ROUND = 0

CLI_CHANGE_INSTRUCTIONS = CHANGE_INSTRUCTIONS

BATCH_RULES = """
## 여러 소식을 한 번에 받을 때
- 소식이 `=== 소식 [번호] ===`로 나뉘어 여러 개 온다. **소식마다 따로** 위 표와 규칙으로 판단한다 — 다른 소식의 내용·종류를 옮겨 붙이지 않는다
- `items`에 소식마다 하나씩, 받은 번호(`n`)와 함께 답한다. 빠뜨리거나 합치지 않는다
"""


def batch_instructions():
    return CLI_CHANGE_INSTRUCTIONS.rstrip("\n") + "\n" + BATCH_RULES


def batch_prompt(queries):
    """queries(순서대로) → 사용자 프롬프트. 번호는 1부터."""
    return "\n".join(f"=== 소식 [{n}] ===\n{change_prompt(q)}" for n, q in enumerate(queries, 1))


BATCH_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"],
                "properties": {"items": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "required": ["n", "changes", "reason"],
                    "properties": {"n": {"type": "integer"},
                                   "changes": {"type": "array", "items": {"type": "string", "enum": list(policy.MODEL_CHANGES)}},
                                   "reason": {"type": "string"}}}}}}
