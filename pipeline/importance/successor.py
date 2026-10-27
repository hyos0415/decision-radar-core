"""후속 모델 찾기 — 소식 제목에 우리 L2 모델과 같은 계열의 더 높은 버전이 나오는가 (importance-policy §3 변경 2026-10-16, HYO-129).

모델을 부르지 않는다 (규율 1). 기술 이름을 계열(숫자 아닌 토막)과 버전(숫자 토막)으로 나눠 제목에서 같은 계열 + 버전을 찾고,
그 버전이 우리 것보다 높을 때만 후속 모델로 본다.

  claude-haiku-4-5-20251001 → 계열 claude haiku · 버전 (4, 5)   제목 「Claude Haiku 5.5」 → (5, 5) > (4, 5) → 후속
  gpt-6-luna                → 계열 gpt luna · 버전 (6,)          제목 「GPT-6.1 Luna」 → 후속 · 「GPT-6 Luna」 → 같은 버전, 아님
  Amazon Bedrock            → 버전 없음 → 찾지 않는다 (한계: usage 글 안의 모델 id `nova-pro-v1:0`은 보지 않는다)

다른 계열로 옮기는 것(Haiku → Sonnet)은 후속으로 보지 않는다. 버전 토막 중 6자리 이상(날짜 스냅숏 20251001 등)은 버전에서 뺀다.
"""
import re

from .signals import norm

_SEP = r"[\s\-_.]*"
_VER = r"(\d+(?:[.\-]\d+)*)"
_DATE_DIGITS = 6


def _version(parts):
    """숫자 토막들 → 비교용 튜플. 날짜 토막을 빼고, 끝의 0을 지운다 (6.0 = 6)."""
    v = [int(p) for p in parts if len(p) < _DATE_DIGITS]
    while v and v[-1] == 0:
        v.pop()
    return tuple(v)


def parse(name):
    """기술 이름 → (제목에서 찾을 정규식, 우리 버전) | None (계열 토막이나 버전이 없으면 찾지 않는다)."""
    tokens = [t for t in re.split(r"[-_.:/\s]+", norm(name)) if t]
    pieces, nums, has_word = [], [], False
    for t in tokens:
        if t.isdigit():
            if len(t) >= _DATE_DIGITS:          # 날짜 스냅숏은 이름의 일부가 아니다
                continue
            nums.append(t)
            if not pieces or pieces[-1] != _VER:
                pieces.append(_VER)
        else:
            has_word = True
            pieces.append(re.escape(t))
    if not has_word or not nums or pieces.count(_VER) != 1:
        return None
    pattern = r"(?<![0-9a-z])" + _SEP.join(pieces) + r"(?![0-9a-z])"
    return re.compile(pattern, re.I), _version(nums)


def mentions(text, name):
    """글에 우리 모델(같은 계열 + **같은** 버전)이 나오는가 → 찾은 표기 목록 | None (이름에 버전이 없어 볼 수 없다).
    모델 은퇴를 우리 모델로 좁힐 때 쓴다 (scope.narrow, HYO-148) — 「Claude Sonnet 3.7」은 claude-sonnet-5-5가 아니다."""
    p = parse(name)
    if p is None:
        return None
    rx, ours = p
    return list(dict.fromkeys(m.group(0) for m in rx.finditer(text or "") if _version(re.split(r"[.\-]", m.group(1))) == ours))


def find(title, names):
    """제목 + L2 기술 이름들 → {기술 이름: 제목에서 찾은 후속 모델 표기}. 같은 기술에 여러 표기가 걸리면 처음 것."""
    out = {}
    for n in names:
        p = parse(n)
        if p is None:
            continue
        rx, ours = p
        for m in rx.finditer(title or ""):
            if _version(re.split(r"[.\-]", m.group(1))) > ours:
                out[n] = m.group(0)
                break
    return out
