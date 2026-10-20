"""남김 카드의 행동 표시 (HYO-132) — 모델 없이 코드가 정한다 (CLAUDE.md 규율 1).

관문 kept_by의 변화 종류로 정한다:
  model_retirement · eol 이 하나라도 → 「강제 이전」 + 본문에서 뽑은 마감일
  아니고 successor_model → 「선택 실험」 (마감 없음)
  나머지(security·deprecation 등) → 표시 없음 (None)

마감일은 보수적으로 뽑는다 — 지어내지 않는다. 마감 키워드가 든 줄에서, 오늘 이후의 날짜가 **하나뿐일 때만** 쓴다.
없거나 여럿이면 None (카드는 「마감일 확인 필요」). 페이지 변경이면 `## 빠진 줄`(옛 내용)은 보지 않는다.
"""
import datetime as dt
import re

from .policy import CHANGE_LABEL

FORCED_CHANGES = ("model_retirement", "eol")      # 앞이 우선 (모델 은퇴 > EOL)
OPTIONAL_CHANGES = ("successor_model",)
CONSEQUENCE = {"model_retirement": "날짜부터 호출 실패", "eol": "날짜부터 보안 패치 없음"}
QUOTE_MAX = 200

# 마감 키워드 — deprecat*은 일부러 뺐다 (지원 중단 예고는 마감이 아니다)
_KEYWORD = re.compile(r"retire|shut\s*down|sunset|end[ -]of[ -]life|\beol\b|no longer (?:supported|available)|will be removed|지원 종료|은퇴|종료", re.I)
# 날짜가 아닌 「빨라도 이날」 표현 — 이 줄은 통째로 건너뛴다
_NON_DATE = re.compile(r"not sooner than|no sooner than|not before", re.I)
_REMOVED_HEAD = "## 빠진 줄"

_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12}
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_MDY = re.compile(r"\b" + _MON + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", re.I)
_DMY = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+" + _MON + r"\.?,?\s+(\d{4})\b", re.I)
_KO = re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")


def _mk(y, m, d):
    try:
        return dt.date(int(y), int(m), int(d))
    except ValueError:
        return None      # 2027-02-30 같은 없는 날짜


def _month(name):
    return _MONTHS[name.lower()[:4] if name.lower().startswith("sept") else name.lower()[:3]]


def dates_in(line):
    """한 줄의 날짜들 (유효한 달력 날짜만)."""
    out = [_mk(*m.groups()) for m in _ISO.finditer(line)]
    out += [_mk(m.group(3), _month(m.group(1)), m.group(2)) for m in _MDY.finditer(line)]
    out += [_mk(m.group(3), _month(m.group(2)), m.group(1)) for m in _DMY.finditer(line)]
    out += [_mk(*m.groups()) for m in _KO.finditer(line)]
    return [d for d in out if d]


def _cut_removed(text):
    """페이지 변경 본문이면 빠진 줄(옛 내용) 앞까지만."""
    return text.split(_REMOVED_HEAD, 1)[0]


def _is_future(d, today):
    return d >= today


def _pick(found):
    """found = [(date, line)] 순서대로. 서로 다른 날짜가 정확히 하나일 때만 돌려준다."""
    if len({d for d, _ in found}) != 1:
        return None
    return found[0]


def extract_deadline(text, today):
    """본문에서 마감일 하나 → {"date": ISO, "quote": 그 줄} · 없거나 여럿이면 None."""
    found = []
    for raw in _cut_removed(text or "").splitlines():
        line = raw.strip()
        if not line or not _KEYWORD.search(line) or _NON_DATE.search(line):
            continue
        found += [(d, line) for d in dates_in(line) if _is_future(d, today)]
    hit = _pick(found) if found else None
    if not hit:
        return None
    d, line = hit
    return {"date": d.isoformat(), "quote": line[:QUOTE_MAX]}


def card_action(kept_by, text, today):
    """관문 kept_by(행 목록) + 본문 → 카드 표시 dict · 표시 없음이면 None."""
    rows = [r for r in kept_by or [] if isinstance(r, dict)]
    for change in FORCED_CHANGES:
        row = next((r for r in rows if r.get("change") == change), None)
        if row is None:
            continue
        dl = extract_deadline(text, today)
        days = (dt.date.fromisoformat(dl["date"]) - today).days if dl else None
        return {"kind": "forced_migration", "label": "강제 이전",
                "reason": f"{row.get('technology')} · {CHANGE_LABEL[change]} 공지 → {CONSEQUENCE[change]}",
                "deadline": dl["date"] if dl else None, "days_left": days,
                "deadline_quote": dl["quote"] if dl else None, "deadline_note": None if dl else "마감일 확인 필요"}
    row = next((r for r in rows if r.get("change") in OPTIONAL_CHANGES), None)
    if row is not None:
        return {"kind": "optional_trial", "label": "선택 실험",
                "reason": f"{row.get('technology')} · 후속 모델 → 지금 것도 계속 돈다 (시험은 선택)",
                "deadline": None, "days_left": None, "deadline_quote": None, "deadline_note": None}
    return None
