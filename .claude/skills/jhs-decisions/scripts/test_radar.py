"""radar.py 회귀 테스트 — 스크립트 층만.

eval 케이스(eval/cases/*.jsonl) 중 결론이 스크립트 규칙으로 정해지는 부분을 재현한다.
모델 층(페이지를 읽고 판단)은 여기서 보지 않는다 — 대신 **모델이 틀린 결과를 넘겼을 때
규칙이 막는지**를 본다. 스크립트가 막아주는 실패는 eval에서 모델 실패로 잡히지 않는다.

실행: python test_radar.py   (PyYAML 필요, 픽스처: ../../../../eval/fixture/adr-fixture)
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")  # Windows cp949 콘솔에서 — 출력에서 크래시 (radar.py와 같게)

HERE = os.path.dirname(os.path.abspath(__file__))
JHS = os.path.join(HERE, "radar.py")
FIXTURE = os.path.normpath(os.path.join(HERE, "..", "..", "..", "..", "eval", "fixture", "adr-fixture", "docs", "adr"))
NOW = "2026-09-28T09:07:00+09:00"

QUIET = {  # cases/README.md 베이스 — 모델이 올바르게 읽었을 때의 결과
    ("0001", "c1"): ("not_met", [("jev.example/releases", "release_note")], None),
    ("0001", "c2"): ("not_met", [("repo:finance-verifier/eval/results/jev-latest.json", "repo")], 912),
    ("0001", "c3"): ("undetermined", [], None),
    ("0002", "c1"): ("not_met", [("docs.cloudvendor.example/guardrails/languages", "official_doc")], None),
    ("0002", "c2"): ("not_met", [("cloudvendor.example/pricing/guardrails", "official_doc")], 0.15),
    ("0004", "c1"): ("not_met", [("github.example/leann/releases", "release_note")], None),
    ("0004", "c2"): ("not_met", [("repo:medical-mate/data/corpus_stats.json", "repo")], 48210),
}

failures = []


def run(*args, env=None):
    p = subprocess.run([sys.executable, JHS, *args], capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, **(env or {})})
    if p.returncode not in (0, 1):
        raise RuntimeError(p.stderr)
    return json.loads(p.stdout)


def results(overrides=None, drop=()):
    out = []
    for (adr, cid), (res, ev, val) in QUIET.items():
        if (adr, cid) in drop:
            continue
        o = (overrides or {}).get((adr, cid))
        if o:
            res, ev, val = o
        r = {"adr": adr, "condition_id": cid, "result": res, "reason": "human" if cid == "c3" and adr == "0001" else None,
             "evidence": [{"url": u, "source_type": t} for u, t in ev]}
        if res == "undetermined" and not r["reason"]:
            r["reason"] = "fetch_failed"
        if val is not None:
            r["observed_value"] = val
        out.append(r)
    return out


def decide(tmp, res, env=None):
    rp = os.path.join(tmp, "results.json")
    json.dump(res, open(rp, "w", encoding="utf-8"))
    return run("decide", os.path.join(tmp, "adr"), rp, "--now", NOW, env=env)


def case(name):
    def wrap(fn):
        tmp = tempfile.mkdtemp()
        shutil.copytree(FIXTURE, os.path.join(tmp, "adr"))
        try:
            fn(tmp)
            print(f"  ok   {name}")
        except AssertionError as e:
            failures.append(name)
            print(f"  FAIL {name}: {e}")
        except Exception as e:  # 단언 전에 터진 것도 실패로 센다 — 여기서 멈추면 뒤 케이스가 안 돈다 (2026-09-30)
            failures.append(name)
            print(f"  FAIL {name}: {type(e).__name__}: {e}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return fn
    return wrap


def alerts(d):
    return sorted(f"{a['adr']}/{a['condition_id']}" for a in d["alerts"])


def result_of(d, key):
    adr, cid = key.split("/")
    return next(r for r in d["results"] if r["adr"] == adr and r["condition_id"] == cid)


print("radar.py 스크립트 층 회귀")


@case("JHS-043 QUIET → PR 0, 알림 0")
def _(t):
    d = decide(t, results())
    assert d["pr_needed"] is False, d["updates"]
    assert d["alerts"] == []


@case("JHS-001 0001/c1 met → PR, 알림 1")
def _(t):
    d = decide(t, results({("0001", "c1"): ("met", [("jev.example/releases", "release_note")], None)}))
    assert d["pr_needed"] and alerts(d) == ["0001/c1"]


@case("JHS-008 met→not_met 역전 → PR, 알림 0 (R4)")
def _(t):
    p = os.path.join(t, "adr", "0002-guardrails-injection.md")
    s = open(p, encoding="utf-8").read().replace(
        "{condition_id: c2, checked_at: \"2026-09-21T09:07:00+09:00\", result: not_met",
        "{condition_id: c2, checked_at: \"2026-09-21T09:07:00+09:00\", result: met")
    open(p, "w", encoding="utf-8").write(s)
    d = decide(t, results())
    assert d["pr_needed"] and d["alerts"] == []


@case("JHS-015/016 경계 799 met / 800 not_met — 모델이 반대로 넘겨도 threshold가 교정")
def _(t):
    d = decide(t, results({("0001", "c2"): ("not_met", [("repo:finance-verifier/eval/results/jev-latest.json", "repo")], 799)}))
    assert result_of(d, "0001/c2")["result"] == "met" and alerts(d) == ["0001/c2"]
    assert any(o["rule"] == "threshold" for o in d["overrides"])
    d = decide(t, results({("0001", "c2"): ("met", [("repo:finance-verifier/eval/results/jev-latest.json", "repo")], 800)}))
    assert result_of(d, "0001/c2")["result"] == "not_met" and d["pr_needed"] is False


@case("JHS-017/018 경계 ≤0.10 met, ≥100000 met")
def _(t):
    d = decide(t, results({("0002", "c2"): ("not_met", [("cloudvendor.example/pricing/guardrails", "official_doc")], 0.10),
                           ("0004", "c2"): ("not_met", [("repo:medical-mate/data/corpus_stats.json", "repo")], 100000)}))
    assert alerts(d) == ["0002/c2", "0004/c2"]


@case("JHS-021 403 → undetermined, PR 있음(변화), 알림 0")
def _(t):
    d = decide(t, results({("0002", "c1"): ("undetermined", [], None)}))
    assert result_of(d, "0002/c1")["result"] == "undetermined" and d["pr_needed"] and d["alerts"] == []


@case("JHS-022 모델이 블로그만으로 met → R2가 undetermined로 강등, 알림 0")
def _(t):
    d = decide(t, results({("0002", "c1"): ("met", [("blog.devnews.example/guardrails-korean", "community")], None)}))
    r = result_of(d, "0002/c1")
    assert r["result"] == "undetermined" and r["evidence_urls"] == [] and d["alerts"] == []
    assert any(o["rule"] == "R2" for o in d["overrides"])


@case("JHS-025 numeric인데 observed_value 없음 → undetermined (대체 지표 금지)")
def _(t):
    d = decide(t, results({("0001", "c2"): ("met", [("repo:finance-verifier/eval/results/jev-latest.json", "repo")], None)}))
    assert result_of(d, "0001/c2")["result"] == "undetermined" and d["alerts"] == []


@case("JHS-044 직전 met(알림 기발송) + 이번 met → PR 0, 알림 0 (R3)")
def _(t):
    p = os.path.join(t, "adr", "0004-leann-index.md")
    s = open(p, encoding="utf-8").read().replace(
        "{condition_id: c1, checked_at: \"2026-09-21T09:07:00+09:00\", result: not_met",
        "{condition_id: c1, checked_at: \"2026-09-21T09:07:00+09:00\", result: met")
    open(p, "w", encoding="utf-8").write(s)
    d = decide(t, results({("0004", "c1"): ("met", [("github.example/leann/releases", "release_note")], None)}))
    assert d["pr_needed"] is False and d["alerts"] == []


@case("JHS-045 직전 undetermined + 이번 undetermined(reason 다름) → PR 0")
def _(t):
    p = os.path.join(t, "adr", "0002-guardrails-injection.md")
    s = open(p, encoding="utf-8").read().replace(
        "{condition_id: c1, checked_at: \"2026-09-21T09:07:00+09:00\", result: not_met, reason: null",
        "{condition_id: c1, checked_at: \"2026-09-21T09:07:00+09:00\", result: undetermined, reason: fetch_failed")
    open(p, "w", encoding="utf-8").write(s)
    res = results({("0002", "c1"): ("undetermined", [], None)})
    next(r for r in res if r["adr"] == "0002" and r["condition_id"] == "c1")["reason"] = "403 again"
    d = decide(t, res)
    assert d["pr_needed"] is False


@case("JHS-049 모델이 human 조건을 met으로 → R5가 undetermined(human)")
def _(t):
    d = decide(t, results({("0001", "c3"): ("met", [("repo:finance-verifier/CLAUDE.md", "repo")], None)}))
    r = result_of(d, "0001/c3")
    assert r["result"] == "undetermined" and r["reason"] == "human" and d["pr_needed"] is False and d["alerts"] == []


@case("JHS-050 모델이 adopted 0003 결과를 넘김 → R13이 버림")
def _(t):
    res = results() + [{"adr": "0003", "condition_id": "c1", "result": "met",
                        "evidence": [{"url": "docs.cloudvendor.example/guardrails/pii-patterns", "source_type": "official_doc"}]}]
    d = decide(t, res)
    assert d["adrs_checked"] == ["0001", "0002", "0004"] and d["alerts"] == [] and d["pr_needed"] is False


@case("JHS-052 첫 실행(prior 없음) → PR, 알림은 met만 (R15)")
def _(t):
    for f in os.listdir(os.path.join(t, "adr")):
        p = os.path.join(t, "adr", f)
        s = open(p, encoding="utf-8").read()
        if "revisit_last_checked:" in s:
            head, rest = s.split("revisit_last_checked:", 1)
            s = head + rest[rest.index("---"):]
            open(p, "w", encoding="utf-8").write(s)
    d = decide(t, results({("0004", "c1"): ("met", [("github.example/leann/releases", "release_note")], None)}))
    assert d["pr_needed"] and alerts(d) == ["0004/c1"]
    assert sum(len(v) for v in d["updates"].values()) == 7


@case("누락 조건 → undetermined(not checked)로 채움 + override")
def _(t):
    d = decide(t, results(drop={("0002", "c2")}))
    assert result_of(d, "0002/c2")["result"] == "undetermined"
    assert any(o.get("why") == "결과 누락" for o in d["overrides"])


@case("INV-2 스냅숏 모드: 제공 안 된 URL 근거 → 제거 후 R2로 강등")
def _(t):
    log = os.path.join(t, "served.log")
    open(log, "w").write("jev.example/releases\n")
    env = {"RADAR_SNAPSHOT_DIR": t, "RADAR_SERVED_LOG": log}
    d = decide(t, results({("0004", "c1"): ("met", [("github.example/leann/releases", "release_note")], None)}), env=env)
    assert result_of(d, "0004/c1")["result"] == "undetermined" and d["alerts"] == []
    assert any(o["rule"] == "INV-2" for o in d["overrides"])


@case("apply: last_checked만 바뀌고 status·본문 불변 (R8)")
def _(t):
    d = decide(t, results({("0004", "c1"): ("met", [("github.example/leann/releases", "release_note")], None)}))
    dp = os.path.join(t, "decision.json")
    json.dump(d, open(dp, "w", encoding="utf-8"))
    p = os.path.join(t, "adr", "0004-leann-index.md")
    before = open(p, encoding="utf-8").read()
    w = run("apply", os.path.join(t, "adr"), dp)
    after = open(p, encoding="utf-8").read()
    assert len(w["files_written"]) == 1
    assert "status: hold" in after and before.split("---", 2)[2] == after.split("---", 2)[2]
    rows = run("list", os.path.join(t, "adr"))
    lc = {r["condition_id"]: r for r in next(x for x in rows if x["id"] == "0004")["revisit_last_checked"]}
    assert lc["c1"]["result"] == "met" and lc["c1"]["checked_at"] == NOW and lc["c2"]["result"] == "not_met"
    assert run("validate", os.path.join(t, "adr"))["ok"]


@case("match: R7 exact / R14 overlap / related (JHS-030·031·032·033·051)")
def _(t):
    a = os.path.join(t, "adr")
    m = run("match", a, "--technology", "Bedrock Guardrails", "--repo", "medical-mate", "--component", "pii-guard", "--use", "PII masking")
    assert m["exact"] == ["0003"], m                                    # JHS-030
    m = run("match", a, "--technology", "Bedrock Guardrails", "--repo", "finance-verifier", "--component", "verifier-core", "--use", "prompt injection 방어")
    assert m["exact"] == [] and [r["id"] for r in m["related"]] == ["0001", "0002", "0003"]   # JHS-031 (0001은 같은 component)
    m = run("match", a, "--technology", "Bedrock Guardrails", "--repo", "medical-mate", "--component", "chat-api", "--use", "prompt injection 방어")
    assert m["exact"] == ["0002"]                                       # JHS-032
    m = run("match", a, "--technology", "Bedrock Guardrails", "--repo", "medical-mate", "--component", "pii-guard", "--use", "prompt injection 방어")
    assert m["exact"] == [] and m["overlap"] == ["0003"]                # JHS-033
    m = run("match", a, "--technology", "LEANN", "--repo", "medical-mate", "--component", "retrieval")
    assert m["overlap"] == ["0004"]                                     # JHS-051 (R14)
    m = run("match", a, "--technology", "vllm", "--repo", "finance-verifier", "--component", "serving-layer")
    assert m["overlap"] == [] and m["related"] == []                    # JHS-011


@case("decide stdin + --apply: 임시 파일 없이 한 번에 (스모크 경합 수정)")
def _(t):
    res = results({("0004", "c1"): ("met", [("github.example/leann/releases", "release_note")], None)})
    p = subprocess.run([sys.executable, JHS, "decide", os.path.join(t, "adr"), "-", "--now", NOW, "--apply"],
                       input=json.dumps(res), capture_output=True, text=True, encoding="utf-8")
    d = json.loads(p.stdout)
    assert len(d["files_written"]) == 1 and alerts(d) == ["0004/c1"]
    lc = {r["condition_id"]: r for r in next(x for x in run("list", os.path.join(t, "adr")) if x["id"] == "0004")["revisit_last_checked"]}
    assert lc["c1"]["result"] == "met"


@case("decide stdin 한국어 reason + python -I: Windows cp949에서도 UTF-8로 읽는다 (HYO-28 Haiku 실측)")
def _(t):
    # -I는 PYTHONUTF8·PYTHONIOENCODING을 무시한다 → Windows에서 stdin이 cp949+surrogateescape로 읽혀
    # 깨진 reason을 ADR에 쓴 뒤 출력에서 죽었다. 환경 변수로 가리지 않게 일부러 뺀다
    reason = "github.example/leann/releases 접근 실패(403)"
    res = results({("0004", "c1"): ("undetermined", [], None)})
    next(r for r in res if r["adr"] == "0004" and r["condition_id"] == "c1")["reason"] = reason
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    p = subprocess.run([sys.executable, "-I", JHS, "decide", os.path.join(t, "adr"), "-", "--now", NOW, "--apply"],
                       input=json.dumps(res, ensure_ascii=False).encode("utf-8"), capture_output=True, env=env)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")[-300:]
    d = json.loads(p.stdout.decode("utf-8"))
    assert result_of(d, "0004/c1")["reason"] == reason
    assert reason in open(os.path.join(t, "adr", "0004-leann-index.md"), encoding="utf-8").read()


@case("numeric: 모델이 result=undetermined/null + observed_value → 스크립트가 계산 (배치 실측 F2)")
def _(t):
    res = results()
    for r in res:
        if (r["adr"], r["condition_id"]) == ("0001", "c2"):
            r["result"], r["observed_value"] = "undetermined", 640
        if (r["adr"], r["condition_id"]) == ("0004", "c2"):
            r["result"], r["observed_value"] = None, 48210
    d = decide(t, res)
    assert result_of(d, "0001/c2")["result"] == "met" and alerts(d) == ["0001/c2"]
    assert result_of(d, "0004/c2")["result"] == "not_met"
    assert not [o for o in d["overrides"] if o["rule"] == "threshold"]  # 모델이 판정을 안 한 것은 뒤집기가 아니다


# --- 여러 주에 걸친 재알림 (틈 ①, HYO-18) — eval은 케이스당 1회 실행이라 여기서만 잡힌다 ---
LEANN = [("github.example/leann/releases", "release_note")]


def weeks(t, seq):
    """0004/c1의 주별 결과를 --apply로 이어서 기록하고 주마다 알림을 돌려준다."""
    out = []
    for i, res in enumerate(seq):
        rp = os.path.join(t, f"w{i}.json")
        json.dump(results({("0004", "c1"): (res, LEANN if res != "undetermined" else [], None)}),
                  open(rp, "w", encoding="utf-8"))
        d = run("decide", os.path.join(t, "adr"), rp, "--now", f"2026-10-{5 + 7 * i:02d}T09:07:00+09:00", "--apply")
        out.append(alerts(d))
    return out


@case("재알림 met → undetermined → met → 알림 1번 (R4 개정)")
def _(t):
    assert weeks(t, ["met", "undetermined", "met"]) == [["0004/c1"], [], []]


@case("met → not_met → met → 알림 2번 (확정된 되돌림은 새 사건)")
def _(t):
    assert weeks(t, ["met", "not_met", "met"]) == [["0004/c1"], [], ["0004/c1"]]


@case("not_met → undetermined → met → 알림 (처음 met)")
def _(t):
    assert weeks(t, ["undetermined", "met"]) == [[], ["0004/c1"]]


@case("옛 기록(last_determined 없음) met → undetermined → met → 재알림 없음")
def _(t):
    p = os.path.join(t, "adr", "0004-leann-index.md")
    s = open(p, encoding="utf-8").read().replace(
        "{condition_id: c1, checked_at: \"2026-09-21T09:07:00+09:00\", result: not_met",
        "{condition_id: c1, checked_at: \"2026-09-21T09:07:00+09:00\", result: met")
    open(p, "w", encoding="utf-8").write(s)
    assert weeks(t, ["undetermined", "met"]) == [[], []]
    s = open(p, encoding="utf-8").read()
    assert "last_determined: met" in s, "undetermined 기록에 직전 확정 결과가 이어지지 않았다"


def watch_0003(t, value="always"):
    """픽스처 사본의 adopted 0003/c1에 watch를 단다 (R13 개정, HYO-19)."""
    p = next(os.path.join(t, "adr", f) for f in os.listdir(os.path.join(t, "adr")) if f.startswith("0003"))
    s = open(p, encoding="utf-8").read()
    anchor = "    source_hint: docs.cloudvendor.example/guardrails/pii-patterns\n"
    assert anchor in s, "픽스처 0003/c1 모양이 바뀌었다"
    open(p, "w", encoding="utf-8").write(s.replace(anchor, anchor + f"    watch: {value}\n"))


PII = {"adr": "0003", "condition_id": "c1", "result": "met",
       "evidence": [{"url": "docs.cloudvendor.example/guardrails/pii-patterns", "source_type": "official_doc"}]}


@case("R13 개정: adopted 0003/c1에 watch: always → 확인 대상, met → 알림 (첫 실행이라 PR)")
def _(t):
    watch_0003(t)
    d = decide(t, results() + [PII])
    assert d["adrs_checked"] == ["0001", "0002", "0003", "0004"], d["adrs_checked"]
    assert alerts(d) == ["0003/c1"] and d["pr_needed"] is True
    assert not [o for o in d["overrides"] if o.get("rule") == "R13"], d["overrides"]


@case("R13 개정: watch 조건 결과가 안 오면 undetermined(not checked)로 채움 — 조용히 빠지지 않는다")
def _(t):
    watch_0003(t)
    d = decide(t, results())
    r = result_of(d, "0003/c1")
    assert r["result"] == "undetermined" and r["reason"] == "not checked", r
    assert any(o.get("rule") == "R1" and o.get("adr") == "0003" for o in d["overrides"]), d["overrides"]


@case("R13 개정: list --in-scope — watch 없는 adopted는 빠지고, 달면 그 조건만 나온다")
def _(t):
    ids = [a["id"] for a in run("list", os.path.join(t, "adr"), "--in-scope")]
    assert ids == ["0001", "0002", "0004"], ids
    watch_0003(t)
    rows = {a["id"]: a for a in run("list", os.path.join(t, "adr"), "--in-scope")}
    assert sorted(rows) == ["0001", "0002", "0003", "0004"] and [c["id"] for c in rows["0003"]["revisit_when"]] == ["c1"]


@case("R13 개정: validate — adopted 조건에 watch 없으면 경고, 이상한 값이면 오류")
def _(t):
    v = run("validate", os.path.join(t, "adr"))
    a3 = next(a for a in v["adrs"] if a["id"] == "0003")
    assert v["ok"] and any("watch: always" in w for w in a3["warnings"]), a3
    watch_0003(t, "sometimes")
    v = run("validate", os.path.join(t, "adr"))
    a3 = next(a for a in v["adrs"] if a["id"] == "0003")
    assert not v["ok"] and any("watch 값" in e for e in a3["errors"]), a3


DRAFT_OK = """---
status: proposed
date: 2026-09-30
technology: KoPII-small
use: PII 탐지 보완
affected:
  - {repo: medical-mate, component: pii-guard}
evidence:
  - {url: "models.example/kopii-small", source_type: official_doc}
  - {url: "github.example/kopii/issues/12", source_type: issue_pr}
revisit_when:
  - id: c1
    check_type: web
    condition: 외국인등록번호를 주민등록번호로 오탐하는 이슈 #12가 해결된다
    source_hint: github.example/kopii/issues/12
    basis: "외국인등록번호를 주민등록번호로 오탐하는 이슈(#12)가 열려 있다"
---
# KoPII-small로 pii-guard를 보완할지

## Research Note
"""


def draft_errors(t, text):
    open(os.path.join(t, "adr", "0006-kopii-small.md"), "w", encoding="utf-8").write(text)
    v = run("validate", os.path.join(t, "adr"))
    return next(a for a in v["adrs"] if a["id"] == "0006")["errors"]


@case("R17: 근거 있는 초안 조건(basis + evidence의 1차 출처) → validate 통과")
def _(t):
    assert draft_errors(t, DRAFT_OK) == []


@case("R17-3: basis 없는 조건 → 오류 (결정 자체를 적은 human 조건 포함)")
def _(t):
    bad = DRAFT_OK.replace('    basis: "외국인등록번호를 주민등록번호로 오탐하는 이슈(#12)가 열려 있다"\n', "") + ""
    assert any("R17-3" in e for e in draft_errors(t, bad))
    human = DRAFT_OK.replace("""  - id: c1
    check_type: web
    condition: 외국인등록번호를 주민등록번호로 오탐하는 이슈 #12가 해결된다
    source_hint: github.example/kopii/issues/12
    basis: "외국인등록번호를 주민등록번호로 오탐하는 이슈(#12)가 열려 있다"
""", """  - id: c1
    check_type: human
    condition: KoPII-small 도입을 검토할지 사람이 판단한다
""")
    assert any("R17-3" in e for e in draft_errors(t, human))


@case("R17-5: source_hint가 evidence 밖(블로그) → 오류, repo 경로는 통과")
def _(t):
    blog = DRAFT_OK.replace("    source_hint: github.example/kopii/issues/12\n", "    source_hint: blog.devnews.example/kopii-review\n")
    assert any("R17-5" in e for e in draft_errors(t, blog))
    repo = DRAFT_OK.replace("    source_hint: github.example/kopii/issues/12\n", "    source_hint: medical-mate:eval/pii-eval.jsonl\n")
    assert not any("R17-5" in e for e in draft_errors(t, repo))


@case("R17-2: 조건 없는 초안 — 본문에 「재검토 조건 없음」 없으면 오류, 있으면 통과")
def _(t):
    head, rest = DRAFT_OK.split("revisit_when:\n", 1)
    empty = head + rest[rest.index("---"):]
    assert any("R17-2" in e for e in draft_errors(t, empty))
    noted = empty.replace("## Research Note\n", "## Research Note\n- 재검토 조건 없음: 대화에 판단 근거가 없다 (블로그 후기뿐)\n")
    assert draft_errors(t, noted) == []


@case("R17은 proposed 초안만 — 사람이 쓴 기존 ADR(basis 없음)은 오류 아님")
def _(t):
    v = run("validate", os.path.join(t, "adr"))
    assert v["ok"] and not any("R17" in e for a in v["adrs"] for e in a["errors"]), v


def _snap(t):
    snap = os.path.join(t, "snap")
    os.makedirs(snap, exist_ok=True)
    urls = [f"docs.example/page-{i:02d}" for i in range(40)]
    json.dump({u: {"file": "p.md", "source_type": "official_doc"} for u in urls}, open(os.path.join(snap, "index.json"), "w", encoding="utf-8"))
    open(os.path.join(snap, "p.md"), "w", encoding="utf-8").write("body")
    return snap, urls


@case("served.log 잠금 — 다른 프로세스가 잠근 동안 fetch는 기다렸다가 쓴다 (INV-2 경쟁, LangGraph 병렬 판정에서 실측)")
def _(t):
    """결정론 — 확률에 기대는 동시 쓰기 테스트는 고치기 전 코드에서 12라운드 중 0번 걸렸다 (2026-09-30)."""
    import time
    snap, urls = _snap(t)
    log = os.path.join(t, "served.log")
    open(log, "w").close()
    env = {**os.environ, "RADAR_SNAPSHOT_DIR": snap, "RADAR_SERVED_LOG": log}
    fd = os.open(log, os.O_RDWR)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        p = subprocess.Popen([sys.executable, JHS, "snapshot", "fetch", urls[0]], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)
        waiting = p.poll() is None
        written_early = os.path.getsize(log)  # 잠근 동안 읽지 않는다 — Windows 잠금은 강제라 읽기도 막힌다
    finally:
        if os.name == "nt":
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    p.wait(timeout=30)
    assert waiting and written_early == 0, ("잠금을 기다리지 않았다", waiting, written_early)
    assert open(log, encoding="utf-8").read() == urls[0] + "\n", "잠금이 풀린 뒤에 써야 한다"


@case("snapshot fetch 동시 40회 → served.log 줄 유실·섞임 0 (스모크 — 위 잠금 테스트가 본체)")
def _(t):
    snap, urls = _snap(t)
    log = os.path.join(t, "served.log")
    env = {**os.environ, "RADAR_SNAPSHOT_DIR": snap, "RADAR_SERVED_LOG": log}
    procs = [subprocess.Popen([sys.executable, JHS, "snapshot", "fetch", u], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
             for u in urls]
    for q in procs:
        q.wait()
    got = [l.rstrip("\n") for l in open(log, encoding="utf-8")]
    assert sorted(got) == sorted(urls), (len(got), [g for g in got if g not in urls][:3])


@case("next-id")
def _(t):
    assert run("next-id", os.path.join(t, "adr"))["next_id"] == "0006"


print(f"\n{'FAILED ' + str(len(failures)) if failures else 'all passed'}")
sys.exit(1 if failures else 0)
