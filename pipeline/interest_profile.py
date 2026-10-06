"""프로필(`radar-profile.yaml`) 로더 + 형식 검사 (doc-pipeline §2-2).

원래 형식 검사는 `radar.py validate`의 몫이지만 radar.py는 skill@v2(HYO-75)까지 못 고친다 — 그때까지 여기에 둔다.
v2에서 radar.py로 옮길 때 이 파일의 규칙(FIELDS·PRIORITIES·검사 함수)을 그대로 가져간다.

- 오류는 첫 번째에서 멈추지 않고 모아서 낸다 (사용자가 한 번에 고치게)
- 자동 필드: id(없으면 slug) · added(없으면 오늘) · review_after(기본 added+28일) · active(기본 true) · priority(기본 normal)
- 모르는 필드는 오류다 (오타가 조용히 무시되면 `aliass:`가 필터에 안 들어간 채 통과한다)
"""
import datetime as dt
import hashlib
import re
from pathlib import Path

import yaml

PRIORITIES = ("high", "normal", "low")
INTEREST_FIELDS = {"id", "topic", "description", "examples", "not", "aliases", "priority",
                   "relates_to", "why", "added", "review_after", "active"}
EXCLUDE_FIELDS = {"topic", "description", "aliases", "why", "added"}
LIST_FIELDS = ("examples", "not", "aliases", "relates_to")
REVIEW_DAYS = 28
_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_RELATES = re.compile(r"^(adr:\d+|repo:[A-Za-z0-9_.-]+)$")


class ProfileError(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


def slugify(topic):
    s = re.sub(r"[^a-z0-9]+", "-", str(topic).lower()).strip("-")
    return s or "interest-" + hashlib.sha1(str(topic).encode("utf-8")).hexdigest()[:6]


def _date(v):
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, str):
        try:
            return dt.date.fromisoformat(v)
        except ValueError:
            return None
    return None


def _nonempty_str(v):
    return isinstance(v, str) and v.strip() != ""


def _check_list(where, name, v, errors):
    if v is None:
        return []
    if not isinstance(v, list):
        errors.append(f"{where}.{name}: 목록이어야 한다 (받은 값: {type(v).__name__})")
        return []
    out = []
    for x in v:
        if not _nonempty_str(x):
            errors.append(f"{where}.{name}: 빈 값이나 문자열이 아닌 항목 {x!r}")
        else:
            out.append(x.strip())
    return out


def parse_profile(data, today=None):
    """dict → (profile, errors, warnings). profile은 자동 필드를 채운 JSON 직렬화 가능 dict."""
    today = today or dt.date.today()
    errors, warnings = [], []
    if not isinstance(data, dict):
        return None, ["최상위가 매핑(version·interests·excludes)이어야 한다"], []
    for k in data:
        if k not in ("version", "interests", "excludes"):
            errors.append(f"최상위: 모르는 필드 {k!r}")
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        errors.append("version: 1 이상의 정수가 필요하다 (스냅숏·eval이 profile@vN으로 참조한다)")
    raw = data.get("interests")
    if not isinstance(raw, list) or not raw:
        errors.append("interests: 비어 있지 않은 목록이 필요하다")
        raw = []

    interests, ids = [], {}
    for n, it in enumerate(raw):
        where = f"interests[{n}]"
        if not isinstance(it, dict):
            errors.append(f"{where}: 매핑이어야 한다")
            continue
        for k in it:
            if k not in INTEREST_FIELDS:
                errors.append(f"{where}: 모르는 필드 {k!r}")
        for req in ("topic", "description"):
            if not _nonempty_str(it.get(req)):
                errors.append(f"{where}: 필수 필드 {req}가 없거나 비었다")
        topic = it.get("topic") if _nonempty_str(it.get("topic")) else ""
        iid = it.get("id")
        if iid is None:
            iid = slugify(topic) if topic else f"interest-{n}"
        elif not (isinstance(iid, str) and _ID.match(iid)):
            errors.append(f"{where}.id: 소문자·숫자·하이픈 slug여야 한다 (받은 값: {iid!r})")
            iid = str(iid)
        where = f"interests[{n}]({iid})"
        if iid in ids:
            errors.append(f"{where}: id 중복 — interests[{ids[iid]}]와 같다")
        ids.setdefault(iid, n)

        prio = it.get("priority", "normal")
        if prio not in PRIORITIES:
            errors.append(f"{where}.priority: {'/'.join(PRIORITIES)} 중 하나여야 한다 (받은 값: {prio!r})")
            prio = "normal"
        lists = {f: _check_list(where, f, it.get(f), errors) for f in LIST_FIELDS}
        for r in lists["relates_to"]:
            if not _RELATES.match(r):
                errors.append(f"{where}.relates_to: `adr:0004` · `repo:이름` 형식이어야 한다 (받은 값: {r!r})")
        if it.get("why") is not None and not isinstance(it.get("why"), str):
            errors.append(f"{where}.why: 문자열이어야 한다")
        active = it.get("active", True)
        if not isinstance(active, bool):
            errors.append(f"{where}.active: true/false여야 한다 (받은 값: {active!r})")
            active = True

        added = _date(it["added"]) if it.get("added") is not None else today
        if added is None:
            errors.append(f"{where}.added: 날짜(YYYY-MM-DD)여야 한다 (받은 값: {it.get('added')!r})")
            added = today
        if it.get("review_after") is not None:
            review = _date(it["review_after"])
            if review is None:
                errors.append(f"{where}.review_after: 날짜(YYYY-MM-DD)여야 한다 (받은 값: {it.get('review_after')!r})")
                review = added + dt.timedelta(days=REVIEW_DAYS)
            elif review < added:
                errors.append(f"{where}.review_after: added({added})보다 앞이다")
        else:
            review = added + dt.timedelta(days=REVIEW_DAYS)
        if active and review <= today:
            warnings.append(f"{where}: review_after({review})가 지났다 — 「아직 관심 있나요?」")
        interests.append({
            "id": iid, "topic": topic, "description": it.get("description") if _nonempty_str(it.get("description")) else "",
            "examples": lists["examples"], "not": lists["not"], "aliases": lists["aliases"],
            "priority": prio, "relates_to": lists["relates_to"], "why": it.get("why") or "",
            "added": added.isoformat(), "review_after": review.isoformat(), "active": active})

    excludes = []
    raw_ex = data.get("excludes") or []
    if not isinstance(raw_ex, list):
        errors.append("excludes: 목록이어야 한다")
        raw_ex = []
    for n, ex in enumerate(raw_ex):
        where = f"excludes[{n}]"
        if not isinstance(ex, dict):
            errors.append(f"{where}: 매핑이어야 한다")
            continue
        for k in ex:
            if k not in EXCLUDE_FIELDS:
                errors.append(f"{where}: 모르는 필드 {k!r}")
        if not _nonempty_str(ex.get("topic")):
            errors.append(f"{where}: 필수 필드 topic이 없거나 비었다")
            continue
        added = _date(ex["added"]) if ex.get("added") is not None else today
        if added is None:
            errors.append(f"{where}.added: 날짜(YYYY-MM-DD)여야 한다")
            added = today
        excludes.append({"topic": ex["topic"].strip(), "description": ex.get("description") or "",
                         "aliases": _check_list(where, "aliases", ex.get("aliases"), errors),
                         "why": ex.get("why") or "", "added": added.isoformat()})

    if errors:
        return None, errors, warnings
    return {"version": version, "interests": interests, "excludes": excludes}, [], warnings


def load_profile(path, today=None):
    """파일 → (profile, warnings). 형식 오류가 있으면 ProfileError(errors 전부)."""
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ProfileError([f"YAML을 읽을 수 없다: {e}"]) from e
    profile, errors, warnings = parse_profile(data, today)
    if errors:
        raise ProfileError(errors)
    return profile, warnings
