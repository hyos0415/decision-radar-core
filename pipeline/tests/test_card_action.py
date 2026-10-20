"""남김 카드 행동 표시·마감일 추출 오프라인 테스트 (HYO-132) — 네트워크·LLM 없이 손으로 만든 본문만.

  python pipeline/tests/test_card_action.py

뒤쪽에 변이 검사가 있다 (CLAUDE.md 규율 3): 코드를 일부러 망가뜨려서 위 테스트가 실제로 빨개지는지 본다.
잡아야 하는 것: 「빨라도 이날」 줄 건너뛰기 없음 · 지난 날짜 허용 · 날짜가 여럿이어도 첫 날짜 · deprecation을 강제로 · 빠진 줄(옛 내용) 읽기.
"""
import datetime as dt
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline.importance import card_action as ca  # noqa: E402

TODAY = dt.date(2026, 10, 20)


def kb(change, tech="anthropic", tier="L2"):
    return {"technology": tech, "tier": tier, "change": change}


def dl(text, today=TODAY):
    return ca.extract_deadline(text, today)


# ---------------- 마감일 추출 ----------------

def test_date_formats():
    assert dl("The model will be retired on 2027-04-01.")["date"] == "2027-04-01"
    assert dl("Claude X will be retired on April 1, 2027.")["date"] == "2027-04-01"
    assert dl("Claude X will be retired on Apr 1, 2027.")["date"] == "2027-04-01"
    assert dl("Claude X will be retired on Apr. 1, 2027.")["date"] == "2027-04-01"
    assert dl("Claude X will be retired on 1 April 2027.")["date"] == "2027-04-01"
    assert dl("Sept 3, 2027: model shutdown")["date"] == "2027-09-03"
    assert dl("이 모델은 2027년 4월 1일 지원 종료됩니다.")["date"] == "2027-04-01"


def test_invalid_calendar_date_ignored():
    assert dl("retired on 2027-02-30") is None
    assert dl("retired on February 30, 2027") is None


def test_quote_is_trimmed_line():
    r = dl("intro\n   Retirement date: 2027-04-01   \nouter")
    assert r["quote"] == "Retirement date: 2027-04-01", r
    r = dl("retired 2027-04-01 " + "x" * 400)
    assert len(r["quote"]) == ca.QUOTE_MAX


def test_no_keyword_no_deadline():
    assert dl("Released on April 1, 2027. New features.") is None


def test_past_date_ignored():
    assert dl("The model was retired on 2026-01-05.") is None
    assert dl("retired on 2026-10-20")["date"] == "2026-10-20"      # 오늘은 아직 지난 게 아니다
    assert dl("retired 2026-01-05 and again retired 2027-04-01")["date"] == "2027-04-01"


def test_not_sooner_than_ignored():
    assert dl("claude-haiku-5-5 retired Not sooner than October 7, 2027") is None
    assert dl("Shutdown no sooner than 2027-12-01") is None
    assert dl("sunset not before 2027-12-01") is None


def test_two_distinct_future_dates_is_none():
    assert dl("retired 2027-04-01\nshutdown 2027-06-01") is None
    assert dl("Retired on 2027-04-01 and shutdown on 2027-06-01") is None


def test_same_date_twice_is_one():
    r = dl("Retirement: 2027-04-01\nShutdown: April 1, 2027")
    assert r["date"] == "2027-04-01" and r["quote"] == "Retirement: 2027-04-01", r


def test_removed_lines_section_ignored():
    body = "# 지원 모델\n\n## 더해진 줄\n(없음)\n\n## 빠진 줄\nclaude-old will be retired on 2027-04-01\n"
    assert dl(body) is None
    body2 = "# t\n\n## 더해진 줄\nclaude-new retires 2027-05-01\n\n## 빠진 줄\nclaude-old retired on 2027-04-01\n"
    assert dl(body2)["date"] == "2027-05-01"


def test_deprecation_word_alone_is_not_deadline():
    assert dl("This API is deprecated as of 2027-04-01.") is None


def test_real_anthropic_page_diff_fragment():
    frag = ("## 더해진 줄\nclaude-haiku-5-5 Active N/A Not sooner than October 7, 2027\n"
            "June 15, 2026 Retired claude-sonnet-4-20250514 claude-sonnet-5-5\n"
            "February 19, 2026 Retired claude-3-7-sonnet-20250219 claude-sonnet-5-5\n")
    assert dl(frag) is None                                   # 오늘 기준: 앞의 둘은 지났고 셋째는 날짜가 아니다
    assert dl(frag, dt.date(2026, 1, 1)) is None              # 둘 다 미래면 모호하다 → None
    only_one = frag.replace("February 19, 2026 Retired", "February 19, 2026 Active")
    assert dl(only_one, dt.date(2026, 1, 1))["date"] == "2026-06-15"


# ---------------- 카드 표시 ----------------

def test_retirement_is_forced_with_deadline():
    a = ca.card_action([kb("model_retirement")], "claude-x will be retired on 2026-11-01", TODAY)
    assert a["kind"] == "forced_migration" and a["label"] == "강제 이전", a
    assert a["deadline"] == "2026-11-01" and a["days_left"] == 12 and a["deadline_note"] is None
    assert a["deadline_quote"] == "claude-x will be retired on 2026-11-01"
    assert a["reason"] == "anthropic · 모델 은퇴 공지 → 날짜부터 호출 실패", a


def test_eol_without_date_says_check():
    a = ca.card_action([kb("eol", "Python", "L0")], "Python 3.9 reaches end of life soon.", TODAY)
    assert a["kind"] == "forced_migration" and a["deadline"] is None and a["days_left"] is None, a
    assert a["deadline_note"] == "마감일 확인 필요" and a["deadline_quote"] is None
    assert a["reason"] == "Python · 우리 버전 EOL 공지 → 날짜부터 보안 패치 없음", a


def test_successor_is_optional():
    a = ca.card_action([kb("successor_model")], "claude-y released. retired nothing 2027-01-01", TODAY)
    assert a["kind"] == "optional_trial" and a["label"] == "선택 실험", a
    assert a["deadline"] is None and a["days_left"] is None and a["deadline_note"] is None
    assert a["reason"] == "anthropic · 후속 모델 → 지금 것도 계속 돈다 (시험은 선택)", a


def test_retirement_beats_successor():
    a = ca.card_action([kb("successor_model"), kb("model_retirement")], "retired 2026-12-01", TODAY)
    assert a["kind"] == "forced_migration" and a["deadline"] == "2026-12-01", a


def test_retirement_preferred_over_eol_row():
    a = ca.card_action([kb("eol", "Python", "L0"), kb("model_retirement", "anthropic")], "", TODAY)
    assert a["reason"].startswith("anthropic · 모델 은퇴"), a


def test_other_changes_have_no_badge():
    assert ca.card_action([kb("deprecation")], "retired on 2027-04-01", TODAY) is None
    assert ca.card_action([kb("security"), kb("breaking")], "retired on 2027-04-01", TODAY) is None
    assert ca.card_action([], "retired on 2027-04-01", TODAY) is None
    assert ca.card_action(None, "", TODAY) is None


def test_ambiguous_dates_forced_without_deadline():
    a = ca.card_action([kb("model_retirement")], "retired 2027-04-01\nshutdown 2027-06-01", TODAY)
    assert a["kind"] == "forced_migration" and a["deadline"] is None and a["deadline_note"] == "마감일 확인 필요", a


# ---------------- 변이 검사 ----------------

class Mutation:
    def __init__(self, mod, name, value):
        self.mod, self.name, self.value = mod, name, value

    def __enter__(self):
        self.old = getattr(self.mod, self.name)
        setattr(self.mod, self.name, self.value)

    def __exit__(self, *exc):
        setattr(self.mod, self.name, self.old)


def mutations():
    import re
    return [
        ("「빨라도 이날」 줄을 건너뛰지 않음", [(ca, "_NON_DATE", re.compile(r"(?!x)x"))], test_not_sooner_than_ignored),
        ("지난 날짜도 받음", [(ca, "_is_future", lambda d, today: True)], test_past_date_ignored),
        ("날짜가 여럿이어도 첫 날짜", [(ca, "_pick", lambda found: found[0])], test_two_distinct_future_dates_is_none),
        ("deprecation도 강제 이전", [(ca, "FORCED_CHANGES", ("model_retirement", "eol", "deprecation")),
                                  (ca, "CONSEQUENCE", {**ca.CONSEQUENCE, "deprecation": "x"})], test_other_changes_have_no_badge),
        ("빠진 줄(옛 내용)도 읽음", [(ca, "_cut_removed", lambda t: t)], test_removed_lines_section_ignored),
        ("deprecat 말만으로 마감", [(ca, "_KEYWORD", re.compile(r"retire|deprecat", re.I))], test_deprecation_word_alone_is_not_deadline),
        ("EOL이 후속 모델에 밀림", [(ca, "FORCED_CHANGES", ("model_retirement",))], test_eol_without_date_says_check),
    ]


def run_mutations():
    failed = 0
    for name, patches, test in mutations():
        ctxs = [Mutation(m, n, v) for m, n, v in patches]
        for c in ctxs:
            c.__enter__()
        try:
            test()
            caught = False
        except Exception:  # noqa: BLE001  — 망가뜨린 코드는 KeyError 등으로도 빨개진다
            caught = True
        finally:
            for c in reversed(ctxs):
                c.__exit__()
        print(f"  {'잡힘' if caught else '놓침 !!'}  변이: {name}  ({test.__name__})")
        failed += 0 if caught else 1
    return failed


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    bad = 0
    for n, f in tests:
        try:
            f()
            print(f"  ok   {n}")
        except Exception:  # noqa: BLE001
            bad += 1
            print(f"  FAIL {n}")
            traceback.print_exc()
    print(f"{len(tests) - bad}/{len(tests)} 통과")
    if bad:
        return 1
    print("변이 검사 — 망가뜨린 코드를 테스트가 잡아야 한다")
    missed = run_mutations()
    print(f"변이 {len(mutations())}개 중 {len(mutations()) - missed}개 잡힘")
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
