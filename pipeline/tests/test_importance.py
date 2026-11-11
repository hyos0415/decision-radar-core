"""레인 A 중요도 분류기 오프라인 테스트 (HYO-67) — 실제 API·네트워크 없이. 메인 .venv로 돌린다 (openai-agents 필요).

  python pipeline/tests/test_importance.py

뒤쪽에 변이 검사가 있다 (CLAUDE.md 규율 3): 코드를 일부러 망가뜨려 위 테스트가 실제로 빨개지는지 본다.
통과는 증거가 아니다 — 두 등급 한쪽만 적용 · 모델 은퇴 ✅→❌ · C1을 k 중 하나만 · 스크립트 검증 무시 · 사람 수정 무시 · 상한 정렬 · 경로 보정 무시 · 허용 호스트 확대를 잡아야 한다.
"""
import asyncio
import json
import re
import socket
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from collector import stack  # noqa: E402
from collector.sources import github  # noqa: E402
from pipeline import profile_filter  # noqa: E402
from pipeline import interest_profile as ip  # noqa: E402
from pipeline.importance import agents_backend, backends, cli as cli_mod, evaluate, gate as gate_mod, policy, scope, signals, successor, tiers  # noqa: E402
from pipeline.importance.backends import (Backend, BoundaryError, CallLog, LabelAnswer, NewsQuery, StubBackend,  # noqa: E402
                                           TechQuery, labels_from_scores, make_backend)
from pipeline.importance.tiers import TierStore  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
Y, N, X = "✅", "❌", "—"


# ---------------------------------------------------------------- 결정표

def _doc_table():
    text = (REPO / "docs" / "design" / "importance-policy.md").read_text(encoding="utf-8")
    sec = text.split("## 4. 결정표", 1)[1].split("보정 축 적용 순서", 1)[0]
    rows = {}
    for line in sec.splitlines():
        m = re.match(r"^\| (L\d) [^|]*\|(.*)\|\s*$", line)
        if m:
            rows[m.group(1)] = [c.strip() for c in m.group(2).split("|")]
    return rows


def test_table_matches_policy_doc():
    doc = _doc_table()
    assert sorted(doc) == policy.TIERS, "문서 표에서 L0~L8을 다 못 읽었다"
    for t, cells in doc.items():
        assert len(cells) == len(policy.CHANGES), (t, cells)
        for c, v in zip(policy.CHANGES, cells):
            assert policy.TABLE[t][c] == v, f"{t} × {c}: 코드 {policy.TABLE[t][c]} ≠ 문서 {v}"


def test_every_cell_default_context():
    for t in policy.TIERS:
        for c in policy.CHANGES:
            r = policy.cell(t, c)
            assert r["keep"] == (policy.TABLE[t][c] == Y) and r["adjusted_by"] == [], (t, c)
    # 손으로 고른 칸 — 표를 베끼지 않은 독립 확인
    keep = {("L0", "eol"), ("L1", "yanked"), ("L2", "model_retirement"), ("L2", "pricing"), ("L3", "pricing"), ("L3", "security"), ("L4", "major"),
            ("L5", "yanked"), ("L6", "yanked"), ("L7", "security"), ("L8", "security"), ("L8", "yanked")}
    drop = {("L0", "major"), ("L2", "minor_patch"), ("L2", "security"), ("L5", "eol"), ("L6", "major"), ("L7", "breaking"), ("L7", "yanked"),
            ("L8", "breaking"), ("L8", "deprecation"), ("L4", "model_retirement")}
    assert all(policy.cell(t, c)["keep"] for t, c in keep) and not any(policy.cell(t, c)["keep"] for t, c in drop)


def test_adjustments():
    dev = policy.Context(path="dev")
    assert [c for c in policy.CHANGES if policy.cell("L4", c, dev)["keep"]] == ["security"]        # 개발 → security만
    assert policy.cell("L2", "security", dev)["keep"] and not policy.cell("L2", "model_retirement", dev)["keep"]   # 표에 —인 칸도 security는 남김
    assert not policy.cell("L6", "minor_patch")["keep"]
    assert policy.cell("L6", "minor_patch", policy.Context(pin="floating"))["keep"]                  # 떠 있는 태그 → minor·patch ✅
    assert not policy.cell("L6", "minor_patch", policy.Context(pin="pinned"))["keep"]
    assert not policy.cell("L6", "major", policy.Context(pin="floating"))["keep"]                    # major는 영향 없음
    sens = policy.Context(sensitive=True)
    assert policy.cell("L7", "deprecation", sens)["keep"] and policy.cell("L8", "deprecation", sens)["keep"]
    assert not policy.cell("L8", "breaking", sens)["keep"]                                           # security·deprecation만
    # 개발 경로가 최우선 (사용자 2026-10-06, §8 판정 세부): 떠 있는 태그·민감 경로 보정이 다른 변화를 되살리지 않는다 → test_dev_path_is_top_priority
    # 로컬 서빙 모델은 모델 은퇴 칸 해당 없음 (v1.1 U2)
    assert not policy.cell("L2", "model_retirement", policy.Context(local=True))["keep"]
    assert policy.cell("L2", "pricing", policy.Context(local=True))["keep"]
    r = policy.cell("L4", "breaking", dev)
    assert r["adjusted_by"] and "개발" in r["adjusted_by"][0]


def test_dev_path_is_top_priority():
    """개발 경로면 security만 남는다 — 어떤 등급·어떤 보정 조합에서도. (문서 순서를 글자 그대로 적용하면 개발+떠 있는 태그에서 minor·patch가 ✅가 된다)"""
    combos = {"개발": policy.Context(path="dev"), "개발+떠 있는 태그": policy.Context(path="dev", pin="floating"),
              "개발+민감": policy.Context(path="dev", sensitive=True), "개발+떠 있는 태그+민감": policy.Context(path="dev", pin="floating", sensitive=True),
              "개발+로컬": policy.Context(path="dev", local=True)}
    for name, ctx in combos.items():
        for t in policy.TIERS:
            kept = [c for c in policy.CHANGES if policy.cell(t, c, ctx)["keep"]]
            assert kept == ["security"], (name, t, kept)
    r = policy.cell("L6", "minor_patch", policy.Context(path="dev", pin="floating"))
    assert not r["keep"] and not any("떠 있는 태그" in a for a in r["adjusted_by"])             # 되살리는 보정은 기록에도 안 남는다
    r = policy.cell("L7", "deprecation", policy.Context(path="dev", sensitive=True))
    assert not r["keep"] and not any("민감" in a for a in r["adjusted_by"])
    assert policy.cell("L8", "security", policy.Context(path="dev", sensitive=True))["keep"]
    # 개발이 아니면 두 보정은 그대로 일한다 (개발 우선이 보정을 통째로 꺼 버리지 않았다)
    assert policy.cell("L6", "minor_patch", policy.Context(pin="floating"))["keep"] and policy.cell("L7", "deprecation", policy.Context(sensitive=True))["keep"]
    # 관문 끝까지: 개발 그룹 기술 + 떠 있는 태그 + 민감 → 모델이 minor·patch·deprecation을 짚어도 버려진다
    st = TierStore()
    st.classify(_q("lintx", manifest="requirements-dev.txt", group="dev", pin="floating"), StubBackend(tiers={"lintx": ["L7"]}))
    st.data["overrides"][signals.norm("lintx")] = {"technology": "lintx", "sensitive": True}
    g = gate_mod.Gate(StubBackend(changes={"a": ["minor_patch"], "b": ["deprecation"], "c": ["security"]}), st)
    assert not g(_item("a", "lintx 1.1", tech="lintx"))["keep"] and not g(_item("b", "lintx deprecates", tech="lintx"))["keep"]
    assert g(_item("c", "lintx cve", tech="lintx"))["keep"]


def test_two_tiers_any_row_keeps_and_records_which():
    d = policy.decide(["L3", "L5"], ["pricing"])                  # L3 ✅ · L5 —
    assert d["keep"] and [(r["tier"], r["change"]) for r in d["kept_by"]] == [("L3", "pricing")]
    d = policy.decide(["L3", "L5"], ["yanked"])                   # L3 — · L5 ✅
    assert d["keep"] and [r["tier"] for r in d["kept_by"]] == ["L5"]
    d = policy.decide(["L3", "L5"], ["security"])                 # 둘 다 ✅ → 두 행 모두 기록
    assert [r["tier"] for r in d["kept_by"]] == ["L3", "L5"]
    d = policy.decide(["L3", "L5"], ["eol"])                      # L3 — · L5 ❌ → 버림
    assert not d["keep"] and d["kept_by"] == [] and len(d["rows"]) == 2
    d = policy.decide(["L2"], ["minor_patch", "model_retirement"])  # 변화가 여러 개 → 하나라도 ✅
    assert d["keep"] and [r["change"] for r in d["kept_by"]] == ["model_retirement"]


def test_empty_changes_default_and_empty_tiers_refused():
    d = policy.decide(["L4"], [])
    assert d["defaulted_change"] and [r["change"] for r in d["rows"]] == ["minor_patch"] and not d["keep"]
    assert policy.decide(["L4"], ["breaking"])["defaulted_change"] is False
    try:
        policy.decide([], ["security"])
        raise AssertionError("등급 없이 판정했다")
    except ValueError:
        pass


def _cand(i, tier, change):
    return {"id": i, "kept_by": [{"tier": tier, "change": change}]}


def test_cap_order_and_log():
    cands = [_cand("a", "L4", "security"), _cand("b", "L0", "eol"), _cand("c", "L4", "breaking"), _cand("d", "L1", "deprecation"),
             _cand("e", "L2", "model_retirement"), _cand("f", "L0", "security"), _cand("g", "L7", "security"), _cand("h", "L4", "security")]
    shown, cut, label = policy.apply_cap(cands, 5)
    # 등급 높은 순 L0(f security, b eol) → L1 d → L2 e → L4(a·h security 동률은 id 순)
    assert [s["id"] for s in shown] == ["f", "b", "d", "e", "a"], [s["id"] for s in shown]
    assert [c["id"] for c in cut] == ["h", "c", "g"] and label == "5/8"
    # 같은 등급에서 심각도: security > breaking > eol·모델 은퇴 > deprecation > 나머지
    same = [_cand(c, "L4", c) for c in ("major", "deprecation", "eol", "breaking", "security")]
    assert [s["id"] for s in policy.apply_cap(same, 5)[0]] == ["security", "breaking", "eol", "deprecation", "major"]
    assert policy.apply_cap(cands[:3], 5)[2] == "3/3" and policy.apply_cap(cands[:3], 5)[1] == []
    two = {"id": "x", "kept_by": [{"tier": "L5", "change": "yanked"}, {"tier": "L3", "change": "pricing"}]}   # 두 등급이면 높은 쪽 등급을 쓴다
    assert policy.rank_key(two)[0] == 3


def _tcand(i, tier, change, tech):
    return {"id": i, "kept_by": [{"tier": tier, "change": change, "technology": tech}]}


def test_cap_one_slot_per_technology_then_fill():
    # §4 변경 2026-10-27 (HYO-147) — W44 모양: Electron 한 보안 수정 묶음(42·43·44·45 줄)이 5칸을 다 가져갔다
    w44 = [_tcand(f"electron@v{v}", "L1", "security", "electron") for v in ("42.11.16", "43.8.1", "44.9.1", "45.0.0-beta.6")]
    w44 += [_tcand("electron@v45.0.0", "L1", "breaking", "electron"), _tcand("electron@v42.11.17", "L1", "eol", "electron"),
            _tcand("anthropic:model-deprecations", "L2", "breaking", "claude-sonnet-5-5"), _tcand("anthropic:release-notes", "L2", "breaking", "claude-sonnet-5-5"),
            _tcand("openai:deprecations", "L2", "model_retirement", "gpt-6-luna"), _tcand("bedrock:pricing", "L2", "pricing", "Amazon Bedrock"),
            _tcand("langfuse@v4.70.0", "L3", "security", "langfuse"), _tcand("fastapi@0.150.0", "L4", "breaking", "fastapi")]
    shown, cut, label = policy.apply_cap(w44, 5)
    assert [s["id"] for s in shown] == ["electron@v42.11.16", "anthropic:model-deprecations", "openai:deprecations", "bedrock:pricing",
                                        "langfuse@v4.70.0"], [s["id"] for s in shown]
    assert label == "5/12" and len(cut) == 7 and cut[0]["id"] == "electron@v43.8.1"                 # 잘린 것은 정렬 순서로 로그에
    assert [s["id"] for s in policy.apply_cap(w44, 5, per_tech=0)[0]][:4] == ["electron@v42.11.16", "electron@v43.8.1", "electron@v44.9.1",
                                                                             "electron@v45.0.0-beta.6"]   # 0 = 예전 동작
    # 기술이 칸보다 적으면 남은 칸을 정렬 순서로 채운다 — 결과도 정렬 순서
    few = w44[:4] + [w44[10]]
    assert [s["id"] for s in policy.apply_cap(few, 3)[0]] == ["electron@v42.11.16", "electron@v43.8.1", "langfuse@v4.70.0"]
    assert [s["id"] for s in policy.apply_cap(few, 5)[0]] == [e["id"] for e in few] and policy.apply_cap(few, 5)[2] == "5/5"
    # 묶는 기술 = 가장 높은 행의 기술. 두 기술이 같은 등급·심각도면 이름 순 (결정론)
    two = {"id": "x", "kept_by": [{"tier": "L5", "change": "breaking", "technology": "anthropic"},
                                  {"tier": "L2", "change": "pricing", "technology": "gpt-6-sol"}, {"tier": "L2", "change": "pricing", "technology": "gpt-6-luna"}]}
    assert policy.cap_technology(two) == "gpt-6-luna"
    assert policy.cap_technology(_cand("y", "L4", "security")) != policy.cap_technology(_cand("z", "L4", "security"))   # 기술 없는 옛 기록은 따로


def _rcand(i, title, tier, change, tech, line=None):
    e = _tcand(i, tier, change, tech)
    e["title"] = title
    if line:
        e["release_line"] = {"line": line}
    return e


def test_topk_always_then_per_technology():
    # §4 변경 2026-11-04 (HYO-168): 「항상」(보안 · 우리 버전 EOL · 모델 은퇴)은 몇 건이든 목록 · detect로는 (패키지 × 줄)마다 최신 1건 (안 A') ·
    # 기술마다 top-3 목록 = detect. W44 모양: Electron 다른 줄 보안 · EOL 5건 + 우리 줄 베타 보안, langfuse 3.x · 4.x(줄 기록 없음 → 주 버전)
    ele = "electron/electron electron v"
    w44 = [_rcand(f"e{v}", ele + v, "L1", "security", "electron", "other") for v in ("42.11.16", "43.8.1", "44.9.1")]
    w44 += [_rcand(f"e{v}", ele + v, "L1", "eol", "electron", "other") for v in ("42.11.17", "42.11.18")]
    w44 += [_rcand("e45b6", ele + "45.0.0-beta.6", "L1", "security", "electron", "ours")]
    w44 += [_rcand(f"lf{v}", f"langfuse/langfuse v{v}", "L3", "security", "langfuse") for v in ("3.225.14", "4.69.0", "4.70.0")]
    w44 += [_rcand("e45", ele + "45.0.0", "L1", "breaking", "electron", "ours"), _rcand("e45b7", ele + "45.0.0-beta.7", "L1", "deprecation", "electron", "ours"),
            _rcand("e451", ele + "45.1.0", "L1", "major", "electron", "ours"), _rcand("e452", ele + "45.2.0", "L1", "major", "electron", "ours"),
            _rcand("openai:deprecations", "[openai] https://platform.openai.com/docs/deprecations 변경 (2026-W43 → 2026-W44)", "L2", "model_retirement", "gpt-6-luna"),
            _tcand("bedrock:pricing", "L2", "pricing", "Amazon Bedrock"),
            _tcand("anthropic:a", "L5", "breaking", "anthropic"), _tcand("anthropic:b", "L5", "deprecation", "anthropic"),
            _tcand("anthropic:c", "L5", "major", "anthropic"), _tcand("anthropic:d", "L5", "major", "anthropic")]
    r = policy.apply_topk(w44)
    always = {e["id"] for e in r["always"]}
    assert always == {"e42.11.16", "e43.8.1", "e44.9.1", "e42.11.17", "e42.11.18", "e45b6", "lf3.225.14", "lf4.69.0", "lf4.70.0", "openai:deprecations"}, always
    # 접기: 다른 줄 5건 → 가장 새 v44.9.1 · 우리 줄 베타는 따로 · langfuse 4.x 둘 → v4.70.0 · 3.x는 따로 · 벤더 문서는 접지 않는다
    assert r["folded"] == {"e42.11.16": "e44.9.1", "e43.8.1": "e44.9.1", "e42.11.17": "e44.9.1", "e42.11.18": "e44.9.1", "lf4.69.0": "lf4.70.0"}, r["folded"]
    # electron: 「항상」은 몫을 안 쓴다 → 나머지 4건 중 top-3 목록 · detect
    assert [r["rank"][i] for i in ("e45", "e45b7", "e451", "e452")] == [1, 2, 3, 4]
    listed = {e["id"] for e in r["listed"]}
    assert always <= listed and "e451" in listed and "e452" not in listed and "anthropic:d" not in listed
    detect = {e["id"] for e in r["detect"]}
    assert detect == {"e44.9.1", "e45b6", "lf3.225.14", "lf4.70.0", "openai:deprecations", "e45", "e45b7", "e451", "bedrock:pricing",
                      "anthropic:a", "anthropic:b", "anthropic:c"}, sorted(detect)
    assert {e["id"] for e in r["cut"]} == {"e452", "anthropic:d"}
    assert len(r["listed"]) == 10 + 3 + 1 + 3 and r["query"]["anthropic:b"] == "anthropic"
    one = policy.apply_topk(w44, k=1)
    assert {e["id"] for e in one["detect"]} - always == {e["id"] for e in one["listed"]} - always == {"e45", "bedrock:pricing", "anthropic:a"}
    # 줄 기록이 없으면 주 버전(0.x는 부 버전)으로 · 패키지가 다르면 따로 (langchain-core ≠ langchain)
    assert policy.fold_key({"id": "u", "title": "astral-sh/uv 0.13.5"}) == ("uv", "line", (0, 13))
    assert policy.fold_key({"id": "c", "title": "langchain-ai/langchain langchain-core==1.7.4"}) != policy.fold_key({"id": "l", "title": "langchain-ai/langchain langchain==1.4.8"})
    assert policy.release_of({"title": "pydantic/pydantic v2.14.2 2026-10-29"}) == ("pydantic", (2, 14, 2))


# ---------------------------------------------------------------- 등급: 신호 · 검증 · 저장

def _write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


def _repo(d):
    d = Path(d)
    _write(d / "requirements.txt", "PyYAML>=6\nopenai-agents==0.22.3\n# 주석\nrequests\n-r other.txt\ngit+https://x/y.git\nfoo[extra]~=1.2\n")
    _write(d / "requirements-dev.txt", "pytest==8.0\nruff\n")
    _write(d / "pyproject.toml", '[project]\nname="x"\ndependencies=["fastapi>=0.1","pydantic==2.0"]\n[project.optional-dependencies]\ndev=["mypy"]\nweb=["jinja2"]\n'
                                 '[dependency-groups]\ntest=["hypothesis"]\n')
    _write(d / "Dockerfile", "FROM python:3.12-slim AS base\nFROM base AS final\nFROM scratch\nFROM --platform=linux/amd64 vllm/vllm-openai:latest\n")
    _write(d / "infra" / "compose.yaml", "services:\n  es:\n    image: docker.elastic.co/elasticsearch/elasticsearch:9.5.4\n  x:\n    build: .\n")
    return d


def test_signals_from_repo():
    with tempfile.TemporaryDirectory() as d:
        s = signals.read_repo_signals(_repo(d))
    g = lambda n: s[signals.norm(n)] if signals.norm(n) in s else s[n.lower()]   # noqa: E731
    assert (g("PyYAML")["group"], g("PyYAML")["pin"], g("PyYAML")["manifest"]) == ("main", "range", "requirements.txt")
    assert g("openai-agents")["pin"] == "pinned" and g("requests")["pin"] == "floating" and g("foo")["pin"] == "range"
    assert g("pytest")["group"] == "dev" and g("ruff")["group"] == "dev" and g("pytest")["pin"] == "pinned"
    assert g("fastapi")["group"] == "main" and g("pydantic")["pin"] == "pinned"
    assert g("mypy")["group"] == "dev" and g("hypothesis")["group"] == "dev" and g("jinja2")["group"] == "main"   # web 그룹은 개발이 아니다
    assert g("python:3.12-slim")["image"] and g("python:3.12-slim")["pin"] == "pinned"
    assert g("vllm/vllm-openai:latest")["pin"] == "floating"
    assert g("docker.elastic.co/elasticsearch/elasticsearch:9.5.4")["image"]
    assert "base" not in s and "scratch" not in s                       # 스테이지 이름·scratch는 기술이 아니다
    assert not any("other" in k or "git" in k for k in s)               # -r · URL 의존성은 건너뜀
    assert signals.parse_requirement("httpx>=0.27") == ("httpx", "range") and signals.parse_requirement("https://x/y.whl") is None   # httpx는 URL이 아니다


def test_main_group_beats_dev_group():
    with tempfile.TemporaryDirectory() as d:
        _write(Path(d) / "requirements.txt", "pytest==8\n")
        _write(Path(d) / "requirements-dev.txt", "pytest==8\n")
        assert signals.read_python_manifests(d)["pytest"]["group"] == "main"     # 운영에 들어가면 운영이다


def _git_add(d, *rels):
    """임시 폴더를 진짜 git repo로 만들고 rels만 추적시킨다 (커밋 없이 — ls-files는 인덱스를 본다)."""
    subprocess.run(["git", "-C", str(d), "init", "-q"], check=True, capture_output=True)
    if rels:
        subprocess.run(["git", "-C", str(d), "add", "--"] + list(rels), check=True, capture_output=True)


def _stack_repo(d):
    """추적: requirements.txt · demo/requirements.txt(제외 대상) · .claude/… · Dockerfile / 미추적: scratch/requirements.txt"""
    d = Path(d)
    _write(d / "requirements.txt", "requests\nhttpx\n")
    _write(d / "demo" / "requirements.txt", "torch\n")
    _write(d / ".claude" / "worktrees" / "x" / "requirements.txt", "numpy\n")
    _write(d / "Dockerfile", "FROM python:3.12-slim\n")
    _write(d / "scratch" / "requirements.txt", "transformers\n")
    _git_add(d, "requirements.txt", "demo/requirements.txt", ".claude/worktrees/x/requirements.txt", "Dockerfile")
    return d


def test_signals_use_tracked_files_minus_excludes_and_skip_claude():
    with tempfile.TemporaryDirectory() as d:
        _stack_repo(d)
        s = signals.read_repo_signals(d, ["demo"])
        assert set(s) == {"requests", "httpx", "python:3.12-slim"}, set(s)      # torch(제외) · numpy(.claude) · transformers(미추적)가 없다
        assert "torch" in signals.read_repo_signals(d)                          # exclude를 안 주면 추적 파일이니 읽힌다
        assert "transformers" not in signals.read_python_manifests(d) and "numpy" not in signals.read_python_manifests(d)
        assert set(signals.read_images(d, ["Dockerfile"])) == set()


def _targets_file(root):
    root = Path(root)
    a = _stack_repo(root / "a")
    _write(a / "CLAUDE.md", "### 의존성 → component\n| 의존성 | component |\n|---|---|\n| PyYAML | skill |\n")
    _write(a / "package.json", '{"dependencies": {"react": "19"}}')
    _git_add(a, "requirements.txt", "demo/requirements.txt", "Dockerfile", "CLAUDE.md", "package.json")
    b = root / "b"
    _write(b / "requirements.txt", "pydantic\n")
    _git_add(b, "requirements.txt")
    t = root / "adr" / "radar-targets.json"
    _write(t, json.dumps([{"repo": "a", "path": "../a", "exclude": ["demo"]}, {"repo": "b", "path": "../b"},
                          {"repo": "ghost", "path": "../ghost"}]))
    return t


def _cli(argv):
    import contextlib
    import io
    from pipeline.importance import cli
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.main(argv)
    return rc, buf.getvalue()


def test_classify_stack_targets_reads_all_targets_with_excludes_and_reports_missing():
    with tempfile.TemporaryDirectory() as d:
        t = _targets_file(d)
        rc, out = _cli(["classify-stack", "--targets", str(t), "--tiers", str(Path(d) / "tiers.json"), "--plan"])
        assert rc == 0 and "대상 폴더 없음: ghost" in out, out
        assert "기술 6개 중 새로 분류할 것 6개" in out, out                      # requests·httpx·python:3.12-slim·react(package.json)·PyYAML(CLAUDE.md 표)(a) + pydantic(b) — torch(제외)는 없다
        old = os.environ.get("RADAR_TARGETS")
        os.environ["RADAR_TARGETS"] = str(t)                                      # 값 없는 --targets는 env를 따른다
        try:
            rc, out2 = _cli(["classify-stack", "--targets", "--tiers", str(Path(d) / "tiers.json"), "--plan"])
        finally:
            os.environ.pop("RADAR_TARGETS") if old is None else os.environ.__setitem__("RADAR_TARGETS", old)
        assert rc == 0 and "기술 6개" in out2, out2


def _npm_repo(d):
    """내 repo: requirements(openai main · eslint dev) + package.json(next·react·openai는 dependencies, electron·prettier·eslint는 devDependencies)
    + demo/package.json(supabase — exclude 대상)."""
    d = Path(d)
    _write(d / "requirements.txt", "openai\n")
    _write(d / "requirements-dev.txt", "eslint==1\n")
    _write(d / "package.json", json.dumps({"dependencies": {"next": "15.1.0", "react": "^19.0.0"},
                                           "devDependencies": {"electron": "39.0.0", "prettier": "*", "eslint": "~9", "openai": "^5"}}))
    _write(d / "demo" / "package.json", json.dumps({"dependencies": {"supabase": "2"}}))
    _git_add(d, "requirements.txt", "requirements-dev.txt", "package.json", "demo/package.json")
    return d


def test_package_json_signals_and_dev_dependencies_are_not_s2():
    from pipeline.importance.cli import queries_from_repos
    with tempfile.TemporaryDirectory() as d:
        _npm_repo(d)
        s = signals.read_repo_signals(d, ["demo"])
        qs = {q.name: q for q in queries_from_repos([(Path(d), ["demo"])])}
    g = lambda n: (s[n]["manifest"], s[n]["group"], s[n]["pin"])   # noqa: E731
    assert g("next") == ("package.json", "main", "pinned") and g("react") == ("package.json", "main", "range")
    assert g("electron") == ("package.json", signals.NPM_DEV, "pinned") and g("prettier")[1:] == (signals.NPM_DEV, "floating")
    assert g("openai") == ("requirements.txt", "main", "floating")          # main이 이긴다 (Python main vs npm devDependencies)
    assert g("eslint") == ("package.json", signals.NPM_DEV, "range")        # npm-dev가 dev를 이긴다 (운영일 수도 있다)
    assert "supabase" not in s                                               # exclude는 package.json에도 (수집기와 같은 목록)
    # ⑩ devDependencies는 S2(→ L7)가 아니다 — Electron 앱의 런타임이 L7로 떨어지면 안 된다. 모델이 정한다
    v = tiers.verify(["L1"], False, qs["electron"].signals)
    assert v["tiers"] == ["L1"] and v["path"] == "prod" and v["mismatch"] is None, v
    assert tiers.verify(["L7"], False, qs["prettier"].signals)["tiers"] == ["L7"]
    assert tiers.verify([], True, qs["electron"].signals)["unresolved"]      # 모델이 모르면 스크립트도 정하지 않는다
    # dependencies는 S3 — L0·L1만 떨어낸다
    v = tiers.verify(["L1", "L4"], False, qs["next"].signals)
    assert v["tiers"] == ["L4"] and v["mismatch"]["rule"].startswith("S3"), v
    assert "devDependencies" in qs["electron"].usage and qs["next"].usage == "package.json 직접 의존성 (main)"


DESKTOP_ROW = ("Electron 39 · electron-vite 5 · React 19 · shadcn/ui(Radix) · Geist · Pretendard 글꼴 · react-markdown + remark-gfm · "
               "remark-frontmatter (npm, `desktop/package.json`)")


def _md(*rows, title="의존성 → component", head="의존성"):
    return f"### {title}\n| {head} | component |\n|---|---|\n" + "".join(f"| {a} | {b} |\n" for a, b in rows)


def test_table_cell_names_drop_parens_versions_and_notes():
    """⑥ 표 첫 칸 → 이름: 괄호 안의 `·`로 쪼개지지 않고, 버전·설명 단어가 이름에 붙지 않는다 (예전 run.json: `Electron 39` · `tokenizers (+ ranx가 끄는 numpy` · `numba)`)."""
    assert github.names_from_cell("ranx · tokenizers (+ ranx가 끄는 numpy·scipy·pandas·numba)") == ["ranx", "tokenizers"]
    assert github.names_from_cell(DESKTOP_ROW) == ["Electron", "electron-vite", "React", "shadcn/ui", "Geist", "Pretendard",
                                                   "react-markdown", "remark-gfm", "remark-frontmatter"]
    assert github.names_from_cell("llama.cpp `llama-server`") == ["llama.cpp"]
    assert github.names_from_cell("Elasticsearch (docker `infra/`, 9.5.4 + analysis-nori, basic)") == ["Elasticsearch"]
    md = _md((DESKTOP_ROW, "desktop"), ("litellm (별도 venv)", "product"), head="의존성 (`requirements.txt`)")
    rows = github.dependency_rows_from_claude_md(md)
    assert [(r["npm"], r["names"][0], r["component"]) for r in rows] == [(True, "Electron", "desktop"), (False, "litellm", "product")], rows
    from pipeline import lanes
    assert lanes.stack_terms_from_claude_md(md)[:3] == ["Electron", "electron-vite", "React"]      # 레인 표시도 같은 파서 (npm 행 포함)


def _table_repos(d):
    """a: requirements(langfuse · typesafe-sdk) + CLAUDE.md(langfuse 행 · llama.cpp 행 · npm 행 · 외부 서비스 표) / b: CLAUDE.md만 (langfuse 다른 쓰임 · typesafe_sdk)."""
    d = Path(d)
    a, b = d / "a", d / "b"
    _write(a / "requirements.txt", "langfuse\ntypesafe-sdk\n")
    _write(a / "package.json", json.dumps({"devDependencies": {"react": "19.0.0"}}))
    _write(a / "CLAUDE.md", _md(("langfuse", "obs (트레이싱)"), ("llama.cpp `llama-server`", "analysis"), (DESKTOP_ROW, "desktop"))
           + _md(("Langfuse Cloud", "obs"), title="외부 모델·서비스", head="모델·서비스"))
    _git_add(a, "requirements.txt", "package.json", "CLAUDE.md")
    _write(b / "CLAUDE.md", _md(("langfuse", "verifier, eval"), ("typesafe_sdk", "eval")))
    _git_add(b, "CLAUDE.md")
    return a, b


def test_queries_read_claude_md_table_usage_and_table_only_names():
    """③ 매니페스트에 있는 이름은 표의 행을 usage에 덧붙이고(repo마다, npm 행 포함), ④ 매니페스트 밖 이름은 표의 행으로 질의를 만든다 — npm 행 · 「외부 모델·서비스」 표는 아니다."""
    from pipeline.importance.cli import queries_from_repos
    with tempfile.TemporaryDirectory() as d:
        a, b = _table_repos(d)
        qs = {q.name: q for q in queries_from_repos([(a, (), "repo-a"), (b, (), "repo-b")])}
        bare = {q.name: q for q in queries_from_repos([(a, ())])}
    assert set(qs) == {"langfuse", "typesafe-sdk", "react", "llama.cpp"}, set(qs)  # typesafe_sdk(b 표) = typesafe-sdk(a 매니페스트) · npm 행의 Electron 등 · Langfuse Cloud는 없다
    assert qs["react"].signals["group"] == signals.NPM_DEV and "Electron 39" in qs["react"].usage and "→ desktop" in qs["react"].usage   # npm 행은 쓰임새만 준다
    lf = qs["langfuse"]
    assert lf.usage.startswith("requirements.txt 직접 의존성 (main) · ") and lf.signals["manifest"] == "requirements.txt", lf
    assert "repo-a CLAUDE.md 「의존성 → component」: langfuse → obs (트레이싱)" in lf.usage, lf.usage
    assert "repo-b CLAUDE.md 「의존성 → component」: langfuse → verifier, eval" in lf.usage, lf.usage
    assert "repo-b" in qs["typesafe-sdk"].usage and qs["typesafe-sdk"].signals["group"] == "main"
    ll = qs["llama.cpp"]
    assert ll.signals == {} and ll.source == "repo-a/CLAUDE.md", ll
    assert ll.usage == "repo-a CLAUDE.md 「의존성 → component」: llama.cpp `llama-server` → analysis", ll.usage
    plain = TechQuery(lf.name, usage="requirements.txt 직접 의존성 (main)", signals=lf.signals)
    assert tiers.fingerprint(lf) != tiers.fingerprint(plain)                       # 표의 쓰임이 바뀌면 다시 분류된다
    assert "a CLAUDE.md" in bare["langfuse"].usage                                  # repo 이름을 안 주면 폴더 이름


def test_fingerprint_follows_tier_prompt_version():
    q = TechQuery("pydantic", usage="requirements.txt 직접 의존성 (main)", signals={"manifest": "requirements.txt", "group": "main"})
    before = tiers.fingerprint(q)
    real = tiers.TIER_PROMPT_VERSION
    try:
        tiers.TIER_PROMPT_VERSION = "changed0"
        assert tiers.fingerprint(q) != before                                       # 프롬프트를 고치면 다시 분류된다 (HYO-124)
    finally:
        tiers.TIER_PROMPT_VERSION = real
    assert tiers.TIER_PROMPT_VERSION == prompts.TIER_PROMPT_VERSION
    assert "| L4 | 앱 프레임워크 | 앱의 뼈대 — 바꾸면 코드 전반이 바뀜 | fastapi · pydantic · uvicorn · jinja2 |" in prompts.TIER_INSTRUCTIONS   # §2-1 예 열


def test_gate_other_stack_is_counted_apart_and_needs_no_model_call():
    st = _store(pydantic={"tiers": ["L4"]})
    be = StubBackend(changes={"p": ["breaking"]})
    g = gate_mod.Gate(be, st, other_stack=["next", "google-auth-library", "pydantic"])
    r = g(_item("t", "google-auth-library v10", tech="google-auth-library"))
    assert r["status"] == "other-stack" and not r["keep"] and r["technologies"] == ["google-auth-library"] and "팀원 스택" in r["reason"], r
    assert g(_item("n", "next 15.2 released"))["status"] == "other-stack"                 # 의존성 칸이 없으면 제목으로
    u = g(_item("e", "electron 39 vs next", tech="electron"))                              # 내 스택의 모르는 기술 — 제목에 팀원 기술이 섞여도
    assert u["status"] == "unclassified" and u["technologies"] == [], u
    assert g(_item("p", "pydantic breaking", tech="pydantic"))["status"] == "ok"           # 양쪽에 있으면 내 것
    assert len(be.log.calls) == 1                                                          # 팀원 스택은 모델을 부르지 않는다
    assert gate_mod.Gate(be, st)(_item("t", "x", tech="google-auth-library"))["status"] == "unclassified"   # 팀원 스택을 안 주면 예전 그대로


def test_successor_parse_and_find():
    """후속 모델 (§3 변경 2026-10-16, HYO-129): 같은 계열 + 더 높은 버전만. 날짜 스냅숏·끝의 0은 버전이 아니다."""
    names = ["claude-haiku-4-5-20251001", "claude-sonnet-5-5", "gpt-6-luna", "gpt-6-sol", "Amazon Bedrock"]
    assert successor.parse("claude-haiku-4-5-20251001")[1] == (4, 5) and successor.parse("gpt-6-luna")[1] == (6,)
    assert successor.parse("Amazon Bedrock") is None and successor.parse("Jev") is None and successor.parse("2026") is None
    hits = {"Claude Haiku 5.5": {"claude-haiku-4-5-20251001": "Claude Haiku 5.5"},
            "Claude Sonnet 6 released": {"claude-sonnet-5-5": "Claude Sonnet 6"},
            "OpenAI GPT-6.1 Luna is now generally available": {"gpt-6-luna": "GPT-6.1 Luna"}}
    for title, want in hits.items():
        assert successor.find(title, names) == want, title
    for title in ["Claude Sonnet 5.5 now available on AWS",                                    # 같은 버전 — 이미 쓰는 모델
                  "OpenAI GPT-6 Sol and GPT-6 Luna are now generally available on Amazon Bedrock",
                  "We've launched Claude Fable 5",                                            # 다른 계열
                  "Claude Haiku 4.5", "claude-haiku-4-5-20251001 retirement",                 # 날짜는 버전이 아니다
                  "GPT-6.0 Luna", "Claude Haiku 3.7", "anthropics/anthropic-sdk-python v1.10.0"]:
        assert successor.find(title, names) == {}, title


def test_gate_successor_model_is_script_judged_l2_only_and_last():
    st = _store(**{"claude-haiku-4-5-20251001": {"tiers": ["L2"]}, "gpt-6-sol": {"tiers": ["L2"]}, "pydantic-2": {"tiers": ["L4"]}})
    st.data["overrides"]["gpt-6-sol"] = {"technology": "gpt-6-sol", "path": "dev"}                  # judge — 개발 경로
    be = StubBackend(changes={"h": [], "d": ["deprecation"]})
    g = gate_mod.Gate(be, st)
    r = g(_item("h", "Claude Haiku 5.5"))                                                            # 제목이 기술 이름을 직접 말하지 않아도 걸린다
    assert r["status"] == "ok" and r["keep"] and r["technologies"] == ["claude-haiku-4-5-20251001"], r
    assert [(k["tier"], k["change"]) for k in r["kept_by"]] == [("L2", "successor_model")], r
    assert r["changes"] == [] and r["successor"] == [{"technology": "claude-haiku-4-5-20251001", "mentioned": "Claude Haiku 5.5"}]   # 모델 라벨은 그대로
    assert r["defaulted_change"] and {x["change"] for x in r["rows"]} == {"minor_patch", "successor_model"}   # 기본 minor·patch 행도 남는다
    st.data["overrides"]["claude-haiku-4-5-20251001"] = {"technology": "claude-haiku-4-5-20251001", "pin": "floating"}
    fl = g(_item("h", "Claude Haiku 5.5"))                                                           # 떠 있는 태그 보정이 그대로 걸린다
    assert {k["change"] for k in fl["kept_by"]} == {"minor_patch", "successor_model"}, fl
    del st.data["overrides"]["claude-haiku-4-5-20251001"]
    same = g(_item("s", "Claude Haiku 4.5 now on AWS"))
    assert same["status"] == "unclassified" and not same["keep"] and same["successor"] == []
    dev = g(_item("v", "GPT-6.1 Sol is now generally available"))                                    # 개발 경로는 security만 (§4 보정)
    assert dev["status"] == "ok" and not dev["keep"] and dev["successor"][0]["technology"] == "gpt-6-sol", dev
    assert g(_item("p", "pydantic-3 is out"))["successor"] == []                                    # L2만 본다
    d = g(_item("d", "Claude Haiku 5.5 · Haiku 4.5 deprecated", tech="claude-haiku-4-5-20251001"))
    assert {k["change"] for k in d["kept_by"]} == {"deprecation", "successor_model"}
    shown, cut, label = policy.apply_cap([{"id": "a", "kept_by": r["kept_by"]}, {"id": "b", "kept_by": [{"tier": "L2", "change": "minor_patch"}]},
                                          {"id": "c", "kept_by": [{"tier": "L2", "change": "deprecation"}]}], limit=2)
    assert [s["id"] for s in shown] == ["c", "b"] and [c["id"] for c in cut] == ["a"]              # 같은 등급이면 후속 모델이 맨 뒤
    assert "successor_model" not in policy.MODEL_CHANGES and "successor_model" not in backends.CHANGE_SET   # 모델은 고를 수 없다
    assert policy.MODEL_CHANGES == ["breaking", "deprecation", "eol", "security", "yanked", "major", "minor_patch", "model_retirement", "pricing"]


def test_normalize_changes_prerelease_major_and_lone_minor_patch():
    # §3 변경 2026-10-21 ③④ — 정답지(gold-w42 · gold-backfill)의 프리릴리스 제목 표기를 그대로 쓴다
    for t in ["electron/electron electron v45.0.0-beta.2", "pydantic/pydantic v2.14.0b1 2026-08-06", "huggingface/transformers Release candidate 5.0.0rc1",
              "BerriAI/litellm v1.79.1.dev2", "BerriAI/litellm v1.80.12-nightly", "BerriAI/litellm v1.80.10.rc.2", "langgraph-checkpoint-postgres==3.1.0a1",
              "grafana/k6 v2.0.0-rc1", "vitejs/vite-plugin-react plugin-react@6.0.0-beta.0", "langchain-ai/langsmith-sdk v0.6.0rc0"]:
        assert policy.is_prerelease(t), t
    for t in ["openai/openai-python v3.0.0", "grafana/k6 v2.0.0", "googleapis/python-genai v2.0.0", "langfuse v4.0.0", "BerriAI/litellm v1.105.0",
              "Claude Haiku 5.5", "ggml-org/llama.cpp b6512", "Python 3.12 EOL", "vllm v0.11.0", "Bedrock adds model betas"]:
        assert not policy.is_prerelease(t), t
    n = policy.normalize_changes
    assert n(["major", "breaking"], "electron v45.0.0-alpha.15") == ["breaking"]                 # 프리릴리스는 major 아님, 본문 breaking은 남김
    assert n(["major"], "grafana/k6 v2.0.0-rc1") == []                                           # 다 빠지면 빈 목록 (decide가 minor·patch로 친다)
    assert n(["major", "breaking"], "grafana/k6 v2.0.0") == ["major", "breaking"]                # 정식은 그대로
    assert n(["breaking", "minor_patch"], "x v1.2.3") == ["breaking"] and n(["minor_patch"], "x v1.2.3") == ["minor_patch"]
    assert n(["minor_patch", "major"], "x v2.0.0-beta.1") == ["minor_patch"]                    # major가 빠지면 minor_patch만 남는다
    assert n([], "x") == []
    # ③ 괄호 「정식 x.0.0에서만」 + D2 (2026-11-11, HYO-169) — 정답지의 major 6건은 전부 정식 x.0.0, Sonnet CLI가 짚은 major 소음은 전부 아님
    for t in ["openai/openai-python v4.0.0", "anthropics/anthropic-sdk-python v1.0.0", "vitejs/vite-plugin-react plugin-react@6.0.0",
              "electron/electron electron v44.0.0", "x v2.0", "huggingface/transformers Transformers v5", "lucide-icons/lucide Lucide V1 🚀"]:
        assert policy.is_formal_major(t) is True and n(["major"], t) == ["major"], t
    for t in ["langchain-ai/langchain langchain-core==1.7.4", "astral-sh/ruff 0.17.0", "vllm v0.11.0", "BerriAI/litellm v1.105.0", "x v2.1.0",
              "electron/electron electron v45.0.0-beta.2", "ggml-org/llama.cpp b11906", "ggml-org/llama.cpp v0.8.0"]:
        assert policy.is_formal_major(t) is False and n(["major", "breaking"], t) == ["breaking"], t   # W44/W45 Sonnet CLI의 major 소음 (빌드 번호 포함)
    for t in ["[vendor] Claude Opus 5 출시", "[anthropic] Models overview v2", "openai/codex Codex CLI release", ""]:
        assert policy.is_formal_major(t) is None and n(["major"], t) == ["major"], t                   # 벤더 소식 · 버전을 못 읽는 제목은 판단하지 않는다 (빼기만)
    assert policy.is_prerelease("lucide-icons/lucide Lucide v1 🚀  (Release Candidate #0)") and n(["major", "breaking"], "lucide-icons/lucide Lucide v1 🚀  (Release Candidate #0)") == ["breaking"]
    assert n(["major", "minor_patch"], "x v1.7.0") == ["minor_patch"]                              # major가 빠지면 덧붙인 minor_patch가 남는다 (정리 순서)
    # 태그가 제목보다 먼저다 — 정답지 항목(과거 릴리스)의 id 그대로: 릴리스 이름 「Lucide V1」의 태그는 1.0.1
    tag = policy.item_tag
    assert tag({"id": "github-release:lucide-icons/lucide@1.0.1"}) == "1.0.1" and tag({"id": "github-release:vitejs/vite-plugin-react@plugin-react@6.0.0"}) == "6.0.0"
    assert tag({"id": "x", "meta": {"tag": "v2.4.0"}}) == "v2.4.0" and tag({"id": "vendor:aws:abc"}) is None and tag({"id": "hn:1"}) is None
    assert n(["major"], "lucide-icons/lucide Lucide V1 🚀", "1.0.1") == [] and n(["major"], "huggingface/transformers Transformers v5", "v5.0.0") == ["major"]
    assert n(["major"], "ggml-org/llama.cpp b11906", "b11906") == [] and n(["major"], "electron/electron electron v44.0.0", "v44.0.0") == ["major"]
    assert n(["major"], "x Release 2", "v2.0.0-rc.1") == []                                          # 태그가 프리릴리스
    g = gate_mod.Gate(StubBackend(changes={"github-release:lucide-icons/lucide@1.0.1": ["major"]}), _store(**{"lucide-react": {"tiers": ["L4"]}}))
    r3 = g({"id": "github-release:lucide-icons/lucide@1.0.1", "title": "lucide-icons/lucide Lucide V1 🚀", "text": "", "meta": {"dependency": "lucide-react"}}, [])
    assert r3["model_changes"] == ["major"] and r3["changes"] == [] and not r3["keep"], r3                 # 관문이 id의 태그로 정리한다 (L4 × major ✅가 사라진다)
    st = _store(electron={"tiers": ["L1"]})
    g = gate_mod.Gate(StubBackend(changes={"e": ["major", "minor_patch"]}), st)
    r = g(_item("e", "electron/electron electron v45.0.0-beta.2", tech="electron"))
    assert r["changes"] == ["minor_patch"] and r["model_changes"] == ["major", "minor_patch"] and not r["keep"], r   # L1 × major ✅가 프리릴리스로 사라진다
    r2 = gate_mod.Gate(StubBackend(changes={"e": ["major", "minor_patch"]}), st)(_item("e", "electron/electron electron v45.0.0", tech="electron"))
    assert r2["changes"] == ["major"] and r2["keep"], r2


def test_versions_from_repo_and_kept_out_of_fingerprint():
    """우리 버전 (HYO-148): manifest 버전·하한 · requires-python · 이미지 태그. 등급 신호(지문)에는 들어가지 않는다 — 패치마다 재분류하지 않게."""
    from pipeline.importance.cli import queries_from_repos
    sv = signals.spec_version
    assert (sv("^39.2.6"), sv(">=6,<7"), sv("~=1.2"), sv("==0.22.3"), sv("1.x"), sv(">=1.2.0 <2"), sv("~9")) == ("39.2.6", "6", "1.2", "0.22.3", "1", "1.2.0", "9")
    assert [sv(s) for s in ["", "*", "latest", "<2", "!=1.0", ">1.0", "workspace:*", "file:../x", "git+https://x"]] == [None] * 9   # 모르는 것을 지어내지 않는다
    assert signals.image_version("python:3.12-slim") == "3.12" and signals.image_version("vllm/vllm-openai:latest") is None
    with tempfile.TemporaryDirectory() as d:
        _repo(d)
        _write(Path(d) / "pyproject.toml", '[project]\nname="x"\nrequires-python=">=3.12"\ndependencies=["fastapi>=0.1","pydantic==2.0"]\n'
                                          '[tool.poetry.dependencies]\npython="^3.12"\nhttpx={version="^0.27"}\n')
        _write(Path(d) / "package.json", json.dumps({"devDependencies": {"electron": "^39.2.6", "vite": "*"}}))
        v = signals.read_repo_versions(d, label="r")
        qs = queries_from_repos([Path(d)])
    g = lambda n: [x["version"] for x in v.get(signals.norm(n), [])]   # noqa: E731
    assert g("openai-agents") == ["0.22.3"] and g("PyYAML") == ["6"] and g("foo") == ["1.2"] and g("requests") == [] and g("pydantic") == ["2.0"]
    assert g("electron") == ["39.2.6"] and g("vite") == [] and g("httpx") == ["0.27"]
    assert sorted(set(g("python"))) == ["3.12"]                                               # requires-python · poetry · python 이미지 태그
    assert g("python:3.12-slim") == ["3.12"] and g("docker.elastic.co/elasticsearch/elasticsearch:9.5.4") == ["9.5.4"]
    assert v[signals.norm("electron")][0]["repo"] == "r" and v[signals.norm("electron")][0]["spec"] == "^39.2.6"
    assert all(set(q.signals) <= {"manifest", "group", "pin", "image"} for q in qs)           # 버전은 지문(신호)에 없다


# 정답지(gold-w42 · gold-backfill)와 W44의 실제 문장
EOL_LINES = {
    "electron42": "> Electron 42.x.y has reached end-of-support as per the project's support policy.",
    "pydantic": "This release drops support for Python 3.9 and adds support for Python 3.15.",
    "genai": "* Drop support for Python 3.9 - EOL ([b542082](https://x))",
    "genai2": "* Removing Python 3.9 support due to EOL ([8bc2b10](https://x))",
    "tokenizers": "- **Drop Python 3.9** (#1952) — `requires-python = \">=3.10\"`; 3.9 users stay on `0.22.x`.",
    "agents": "### Python 3.9 is no longer supported\nSince Python 3.9 is EOLed three months ago, this SDK no longer support the version.",
    "vite": "Vite 7 and below are no longer supported. If you are using Vite 7, please upgrade to Vite 8.",
    "bare": "This line (41.x.y) has reached end-of-life.",
}


def test_scope_eol_targets_and_line_match():
    t = lambda k, own="x": scope.eol_targets(EOL_LINES[k], ["python", "electron", "vite"], own)   # noqa: E731
    assert t("electron42", "electron") == [("electron", (42,), "eq")]
    for k in ("pydantic", "genai", "genai2", "tokenizers", "agents"):
        assert t(k) == [("python", (3, 9), "eq")], (k, t(k))                                    # 3.15 「adds support」 · `>=3.10`은 대상이 아니다
    assert t("vite") == [("vite", (7,), "le")]
    assert t("bare", "electron") == [("electron", (41,), "eq")]                                 # 이름 없는 줄 표기는 그 행의 기술
    assert scope.eol_targets("pydantic 2.14 drops support for Python 3.9", ["pydantic", "python"], "pydantic") == [("python", (3, 9), "eq")]   # 주어인 기술 버전은 대상이 아니다
    assert scope.eol_targets("Remove support for ruff-lsp", ["ruff"], "ruff") == [] and scope.eol_targets("requires-python 3.9 dropped", ["python"], "x") == []
    m = scope.line_match
    assert m((39, 2, 6), (42,)) is False and m((42, 1), (42,)) is True and m((3, 12), (3, 9)) is False and m((3, 9, 1), (3, 9)) is True
    assert m((7, 2, 6), (7,), "le") is True and m((8, 0), (7,), "le") is False and m((3,), (3, 9)) is None   # `>=3`으로는 3.9인지 모른다
    rel = {"meta": {"dependency": "electron"}}
    assert scope.release_version({**rel, "title": "electron/electron electron v42.11.17"}) == (42, 11, 17)
    assert scope.release_version({**rel, "title": "ggml-org/llama.cpp b6512"}) is None and scope.release_version({"title": "Python 3.12 EOL"}) is None
    assert scope.line_depth("python", (3, 12)) == 2 and scope.line_depth("langgraph", (0, 6)) == 2 and scope.line_depth("electron", (42,)) == 1


def test_gate_eol_scoped_to_our_line_and_newer_line_alert():
    """W44: Electron ^39.2.6인데 「42.x.y has reached end-of-support」가 L1 × 우리 버전 EOL로 남았다 → 다른 줄이면 그 기술의 eol을 뺀다.
    우리 줄이면 남기고, 확인할 수 없으면(버전을 모름 · 문장에서 못 뽑음) 남긴다 (사용자 Q1). security는 빼지 않고 줄만 기록한다 (Q2)."""
    st = _store(electron={"tiers": ["L1"]}, pydantic={"tiers": ["L4"]})
    vers = {"electron": [{"version": "39.2.6"}], "python": [{"version": "3.12"}]}
    be = StubBackend(changes={"e42": ["eol", "minor_patch"], "e39": ["eol"], "s": ["security"], "p": ["breaking", "eol"], "p2": ["eol"], "u": ["eol"]})
    g = gate_mod.Gate(be, st, versions=vers)
    r = g(_item("e42", "electron/electron electron v42.11.17", tech="electron", text=EOL_LINES["electron42"]))
    assert not r["keep"] and r["changes"] == ["eol"] and r["model_changes"] == ["eol", "minor_patch"], r   # 모델 라벨은 그대로 남는다
    assert {(x["tier"], x["change"]) for x in r["rows"]} == {("L1", "minor_patch")}, r          # 다 빠지면 minor·patch로 친다
    note = r["scope"][0]["notes"][0]
    assert note["verdict"] == "other" and note["newer_line_eol"] == [{"technology": "electron", "ours": ["39.2.6"], "announced": "42"}]
    assert r["scope"][0]["release_line"]["line"] == "other"
    own = g(_item("e39", "electron/electron electron v39.8.9", tech="electron", text="> Electron 39.x.y has reached end-of-support."))
    assert own["keep"] and [k["change"] for k in own["kept_by"]] == ["eol"] and own["scope"][0]["release_line"]["line"] == "ours"
    sec = g(_item("s", "electron/electron electron v43.8.1", tech="electron", text="contains the fix for GHSA-jr5w-w6x2-84p9"))
    assert sec["keep"] and sec["scope"][0]["release_line"]["line"] == "other"                   # 다른 줄 security는 남긴다 (기록만)
    p = g(_item("p", "pydantic/pydantic v2.14.0", tech="pydantic", text=EOL_LINES["pydantic"]))
    assert p["keep"] and [k["change"] for k in p["kept_by"]] == ["breaking"], p                 # Python 3.9는 우리(3.12) 줄이 아니다
    assert "newer_line_eol" not in p["scope"][0]["notes"][0]                                    # 우리가 더 새 줄이면 알림도 없다
    st.data["overrides"]["python"] = {"technology": "python", "version": ["3.9"]}               # 사람이 적은 버전이 이긴다
    assert g(_item("p2", "pydantic/pydantic v2.14.0", tech="pydantic", text=EOL_LINES["pydantic"]))["keep"]
    del st.data["overrides"]["python"]
    unk = g(_item("u", "electron/electron electron v42.0.0", tech="electron", text="Support window changes; see the timeline page."))
    assert unk["keep"] and unk["scope"][0]["notes"][0]["verdict"] == "unknown"                  # 못 뽑으면 남긴다 (Q1)
    no_ver = gate_mod.Gate(StubBackend(changes={"e42": ["eol"]}), st)(_item("e42", "electron v42.11.17", tech="electron", text=EOL_LINES["electron42"]))
    assert no_ver["keep"] and no_ver["scope"][0]["notes"][0]["verdict"] == "unknown"           # 우리 버전을 모르면 예전 그대로
    entries = [{"id": "e42", "gate": r}, {"id": "e42b", "gate": r}, {"id": "p", "gate": p}]
    assert gate_mod.cap_lane_a(entries)["newer_line_eol"] == [{"technology": "electron", "ours": ["39.2.6"], "announced": "42", "ids": ["e42", "e42b"]}]


def test_gate_model_retirement_needs_our_model_mentioned():
    """댓글(2026-10-27): W44 OpenAI deprecations diff(gpt-6-* 없음)의 모델 은퇴가 gpt-6-luna에 붙었다 → L2는 같은 계열·같은 버전이 글에 있어야 남긴다."""
    st = _store(**{"gpt-6-luna": {"tiers": ["L2"]}, "openai-agents": {"tiers": ["L4"]}, "amazon bedrock": {"tiers": ["L2"]}})
    be = StubBackend(changes=lambda q: ["model_retirement", "deprecation"] if q.id != "r" else ["model_retirement"])
    g = gate_mod.Gate(be, st, {"openai": ["openai", "gpt"], "aws": ["bedrock"]})
    other = g(_item("d", "[openai] deprecations (페이지 변경)", vendor="openai", text="gpt-5.4-nano | gpt-5.4-nano-2026-03-17\ngpt-5.1 | gpt-5.1-2025-11-13\nGPT-6.1 Luna preview"))
    assert other["keep"] and ("gpt-6-luna", "L2", "model_retirement") not in {(k["technology"], k["tier"], k["change"]) for k in other["kept_by"]}, other
    assert ("gpt-6-luna", "L2", "deprecation") in {(k["technology"], k["tier"], k["change"]) for k in other["kept_by"]}   # 다른 종류는 그대로
    ours = g(_item("r", "[openai] deprecations (페이지 변경)", vendor="openai", text="2026-12-01: gpt-6-luna → shutdown"))
    assert ("gpt-6-luna", "L2", "model_retirement") in {(k["technology"], k["tier"], k["change"]) for k in ours["kept_by"]}, ours
    note = next(s for s in ours["scope"] if s["technology"] == "gpt-6-luna")["notes"][0]
    assert note == {"change": "model_retirement", "verdict": "ours", "mentioned": ["gpt-6-luna"]}
    bed = g(_item("r", "[aws] Bedrock model lifecycle", vendor="aws", text="Titan Image v1 retires"))
    assert bed["keep"] and bed["scope"][0]["notes"][0]["verdict"] == "unknown"                  # 이름에 버전이 없으면 볼 수 없다 → 남긴다
    assert successor.mentions("Claude Sonnet 3.7 retired", "claude-sonnet-5-5") == [] and successor.mentions("x", "Amazon Bedrock") is None
    assert successor.mentions("claude-haiku-4-5-20251001 · Claude Haiku 4.5", "claude-haiku-4-5-20251001") == ["claude-haiku-4-5-20251001", "Claude Haiku 4.5"]


def _ga(status, keep=False, techs=()):
    return {"id": status, "gate": {"keep": keep, "status": status, "technologies": list(techs)}}


def test_gate_counts_and_warning_ratio():
    entries = ([_ga("other-stack")] * 3 + [_ga("unclassified"), _ga("unclassified", techs=["weird"]), _ga("error")]
               + [_ga("ok", True), _ga("ok", True), _ga("ok"), _ga("ok")])
    c = policy.gate_counts(entries)
    assert (c["lane_a"], c["kept"], c["unknown"], c["unresolved"], c["other_stack"], c["error"]) == (10, 2, 1, 1, 3, 1), c
    assert abs(c["ratio"] - 2 / 7) < 1e-12 and c["warn"] and c["warn_ratio"] == policy.UNKNOWN_WARN_RATIO   # 분모에서 팀원 스택을 뺀다
    assert policy.UNKNOWN_WARN_RATIO == 0.05                                                 # 변경 (2026-10-15): 20% 잠정 → 5% (다시 잰 정상 비율 W41 0.9% · W42 0.8%)
    assert not policy.gate_counts([_ga("unclassified")] + [_ga("ok")] * 19)["warn"]         # 정확히 5%는 경고 아님 (넘어야)
    assert policy.gate_counts([_ga("unclassified")] + [_ga("ok")] * 18)["warn"]
    allother = policy.gate_counts([_ga("other-stack")] * 2)
    assert allother["ratio"] is None and not allother["warn"]
    assert policy.gate_counts([], 0.5)["ratio"] is None
    # 스택에서 뺀 기술(retired, HYO-170)은 판정 못 함이 아니다 — 따로 세고 분모에서도 뺀다 (W44 재생: litellm 17건이 경고를 울리지 않게)
    r = policy.gate_counts([_ga("retired")] * 17 + [_ga("unclassified")] + [_ga("ok")] * 19)
    assert r["retired"] == 17 and r["unknown"] == 1 and abs(r["ratio"] - 1 / 20) < 1e-12 and not r["warn"], r


def test_retired_technology_is_recorded_but_not_judged():
    # HYO-170 (2026-11-04 사용자): LiteLLM 기각 — 등급 파일에서 지우지 않고 사람 수정에 retired를 적는다
    st = _store(litellm={"tiers": ["L5"]}, python={"tiers": ["L0", "L1"]})
    st.data["overrides"]["litellm"] = {"technology": "litellm", "path": "dev", "retired": "2026-11-04 기각"}
    assert st.effective("litellm")["retired"] == "2026-11-04 기각" and "retired" not in st.effective("python")
    g = gate_mod.Gate(StubBackend(changes={"l": ["security"], "both": ["security"]}), st)
    r = g(_item("l", "BerriAI/litellm v1.107.0", tech="litellm"))
    assert r["status"] == "retired" and r["keep"] is False and "스택에서 뺀 기술" in r["reason"], r
    assert r["skipped"] == [{"technology": "litellm", "why": "스택에서 뺀 기술 (2026-11-04 기각)"}]
    both = g({"id": "both", "source": "hn", "title": "LiteLLM and Python RCE", "url": "", "text": "", "channels": ["stories"], "meta": {}})
    assert both["status"] == "ok" and both["keep"] and [k["technology"] for k in both["kept_by"]] == ["python", "python"], both   # 남은 기술은 그대로 판정


def test_cli_run_reads_team_stack_from_collect_only_targets():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        a = d / "a"
        _write(a / "requirements.txt", "pydantic\n")
        _write(a / "package.json", json.dumps({"devDependencies": {"electron": "39.0.0"}}))
        _write(a / "CLAUDE.md", "### 의존성 → component\n| 의존성 | component |\n|---|---|\n| langfuse | product |\n")
        _git_add(a, "requirements.txt", "package.json", "CLAUDE.md")
        team = d / "team"
        # 겹치는 이름을 출처별로 하나씩: pydantic(내 requirements — 관문 신호만) · langfuse(내 CLAUDE.md 표 — 수집기만)
        _write(team / "package.json", json.dumps({"dependencies": {"next": "15", "google-auth-library": "10", "pydantic": "1", "langfuse": "3"}}))
        _write(team / "fixtures" / "package.json", json.dumps({"dependencies": {"left-pad": "1"}}))
        _git_add(team, "package.json", "fixtures/package.json")
        t = d / "adr" / "radar-targets.json"
        _write(t, json.dumps([{"repo": "a", "path": "../a"}]))
        co = d / "collect-only.json"
        _write(co, json.dumps({"targets": [{"repo": "team", "path": str(team), "exclude": ["fixtures"]},
                                           {"repo": "ghost", "path": str(d / "ghost")}]}))
        _write(d / "p.yaml", "version: 1\ninterests:\n  - {topic: 하이브리드 검색, description: d}\n")
        items = [_item("a1", "pydantic 3 breaking", tech="pydantic"), _item("a2", "google-auth-library 10", tech="google-auth-library"),
                 _item("a3", "electron 39", tech="electron")]
        _write(d / "items.jsonl", "\n".join(json.dumps(i, ensure_ascii=False) for i in items) + "\n")
        st = TierStore(d / "tiers.json")
        st.ensure([TechQuery("pydantic", usage="u")], StubBackend(tiers={"pydantic": ["L4"]}))
        st.save()
        old = {k: os.environ.get(k) for k in ("RADAR_COLLECT_ONLY", "RADAR_TARGETS")}
        os.environ["RADAR_COLLECT_ONLY"] = str(co)
        try:
            from pipeline.importance.cli import other_stack_names
            names, info = other_stack_names(t)
            assert names == ["next", "google-auth-library"], names                     # pydantic·langfuse는 내 스택 · left-pad는 exclude
            assert [m["repo"] for m in info["missing"]] == ["ghost"] and info["repos"] == ["team"], info
            import contextlib
            import io
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = profile_filter.main(["run", "--snapshot", str(d / "items.jsonl"), "--profile", str(d / "p.yaml"), "--no-stack",
                                          "--out", str(d / "out"), "--tiers", str(d / "tiers.json"), "--gate", "stub", "--targets", str(t)])
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        assert rc == 0
        g = json.loads((d / "out" / "run.json").read_text(encoding="utf-8"))["lane_a_gate"]
        sc = g["status_counts"]
        assert (sc["kept"], sc["other_stack"], sc["unknown"], sc["unresolved"]) == (1, 1, 1, 0), sc
        assert g["other_stack"]["names"] == 2 and g["other_stack"]["missing"][0]["repo"] == "ghost", g["other_stack"]
        out = buf.getvalue()
        assert "모르는 기술 1 · 분류 불가 0 · 팀원 스택 1" in out and "경고" in out and "50%" in out, out   # electron 1 / (3 − 1)


def test_compare_stacks_groups_and_normalizes():
    from pipeline.importance.cli import compare_stacks
    r = compare_stacks(["PyYAML", "react", "Typing_Extensions"], ["pyyaml", "requests", "typing-extensions"])
    assert r == {"collector_only": ["react"], "gate_only": ["requests"], "both": ["pyyaml", "typing-extensions"]}, r


def test_stack_diff_command_compares_collector_names_with_tier_file():
    with tempfile.TemporaryDirectory() as d:
        t = _targets_file(d)
        st = TierStore(Path(d) / "tiers.json")
        st.ensure([TechQuery("PyYAML", usage="u"), TechQuery("requests", usage="u")], StubBackend(tiers={"PyYAML": ["L4"], "requests": ["L4"]}))
        st.save()
        rc, out = _cli(["stack-diff", "--tiers", str(Path(d) / "tiers.json"), "--targets", str(t)])
        lines = {ln.split(" ", 1)[0]: ln for ln in out.splitlines() if ln.startswith(("수집기만", "관문만", "양쪽"))}
        assert rc == 0 and "대상 폴더 없음: ghost" in out, out
        assert lines["수집기만"] == "수집기만 1: react", lines                  # CLAUDE.md 표 PyYAML은 양쪽, package.json react는 수집기만
        assert lines["관문만"] == "관문만 1: requests", lines
        assert lines["양쪽"] == "양쪽 1: pyyaml", lines


def test_verify_script_beats_model():
    v = tiers.verify(["L5"], False, {"image": True, "pin": "pinned"})                # S1 이미지
    assert v["tiers"] == ["L1"] and v["mismatch"]["model"] == ["L5"] and v["mismatch"]["script"] == ["L1"] and not v["unresolved"]
    v = tiers.verify(["L1"], False, {"image": True})
    assert v["tiers"] == ["L1"] and v["mismatch"] is None                             # 일치하면 어긋남 기록 없음
    v = tiers.verify(["L4"], False, {"manifest": "pyproject.toml", "group": "dev"})   # S2 dev 그룹
    assert v["tiers"] == ["L7"] and v["path"] == "dev" and v["mismatch"]["rule"].startswith("S2")
    # S3 (사용자 2026-10-06): manifest 패키지는 L0·L1만 떨어낸다 — 호스팅 등급(L2·L3)은 떨어내지 않는다
    v = tiers.verify(["L3", "L5"], False, {"manifest": "requirements.txt", "group": "main"})   # langfuse 같은 서비스+SDK (§4 예시)
    assert v["tiers"] == ["L3", "L5"] and v["mismatch"] is None and not v["unresolved"]
    v = tiers.verify(["L2"], False, {"manifest": "requirements.txt", "group": "main"})
    assert v["tiers"] == ["L2"] and not v["unresolved"] and v["mismatch"] is None
    v = tiers.verify(["L1", "L5"], False, {"manifest": "requirements.txt", "group": "main"})   # L0·L1은 패키지가 될 수 없다
    assert v["tiers"] == ["L5"] and v["mismatch"]["model"] == ["L1", "L5"] and v["mismatch"]["script"] == ["L5"]
    v = tiers.verify(["L0", "L1"], False, {"manifest": "requirements.txt", "group": "main"})   # 남는 게 없으면 분류 불가
    assert v["unresolved"] and v["tiers"] == [] and v["mismatch"]
    v = tiers.verify([], True, {"manifest": "requirements.txt", "group": "main"})     # 모델이 분류 불가 + 스크립트도 못 정함
    assert v["unresolved"]
    v = tiers.verify(["L7"], True, {"manifest": "pyproject.toml", "group": "dev"})    # 모델이 분류 불가여도 스크립트가 정한다
    assert v["tiers"] == ["L7"] and not v["unresolved"]
    v = tiers.verify(["L6"], False, {})                                               # 신호 없음 → 모델 그대로
    assert v["tiers"] == ["L6"] and v["mismatch"] is None and v["path"] == "prod"
    assert tiers.verify([], True, None)["unresolved"]


def _q(name, **sig):
    return TechQuery(name=name, usage=f"{name} 사용", signals=sig)


def test_store_reuse_fingerprint_and_human_override():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "tiers.json"
        be = StubBackend(tiers={"alpha": ["L6"], "beta": ["L5"]})
        st = TierStore(path)
        assert st.ensure([_q("alpha"), _q("beta")], be) == ["alpha", "beta"] and len(be.log.calls) == 2
        st.save()
        st2 = TierStore(path)
        assert st2.ensure([_q("alpha"), _q("beta")], be) == [] and len(be.log.calls) == 2         # 저장한 것은 다시 부르지 않는다
        assert st2.ensure([TechQuery("alpha", usage="쓰임이 바뀜")], be) == ["alpha"]              # 쓰임이 바뀌면 다시
        assert st2.ensure([_q("alpha", pin="floating")], be) == ["alpha"]                         # 신호가 바뀌어도
        # 사람 수정: 모델 호출 없이 자동 값을 덮고, refresh에도 남는다
        st2.data["overrides"][signals.norm("alpha")] = {"technology": "alpha", "tiers": ["L4"], "sensitive": True}
        n = len(be.log.calls)
        assert st2.ensure([_q("alpha")], be, refresh=True) == [] and len(be.log.calls) == n
        e = st2.effective("alpha")
        assert e["tiers"] == ["L4"] and e["sensitive"] is True and e["human"] and not e["unresolved"]
        assert st2.effective("beta")["tiers"] == ["L5"] and st2.effective("nope") is None
        st2.save()
        assert TierStore(path).effective("alpha")["tiers"] == ["L4"]
        # 사람이 등급을 안 줬고 다른 필드만 준 경우: 자동 등급은 그대로, 필드만 덮음
        st2.data["overrides"][signals.norm("beta")] = {"technology": "beta", "local": True}
        assert st2.effective("beta")["tiers"] == ["L5"] and st2.effective("beta")["local"] is True


def test_store_classify_applies_script_and_records_mismatch():
    be = StubBackend(tiers={"serverx": ["L5"], "pytestish": ["L4"]})
    st = TierStore()
    rec = st.classify(_q("serverx", image=True, pin="floating"), be)
    assert rec["tiers"] == ["L1"] and rec["model_tiers"] == ["L5"] and rec["mismatch"] and rec["pin"] == "floating"
    rec = st.classify(_q("pytestish", manifest="pyproject.toml", group="dev"), be)
    assert rec["tiers"] == ["L7"] and rec["path"] == "dev"
    ctx_rec = st.effective("pytestish")
    assert policy.decide(ctx_rec["tiers"], ["breaking"], policy.Context(path=ctx_rec["path"]))["keep"] is False


# ---------------------------------------------------------------- 레인 A 관문

def _store(**techs):
    st = TierStore()
    be = StubBackend(tiers={k: v["tiers"] for k, v in techs.items()})
    for name, v in techs.items():
        st.classify(TechQuery(name=name, usage="u", signals=v.get("signals", {})), be)
    return st


def _item(iid, title, tech=None, vendor=None, text=""):
    meta = {k: v for k, v in (("dependency", tech), ("vendor", vendor)) if v}
    return {"id": iid, "source": "github", "title": title, "url": "", "text": text, "channels": ["dependency-releases"], "meta": meta}


def test_resolve_technologies():
    names = ["pydantic", "openai-agents", "gpt-6-luna", "Python"]
    r = gate_mod.resolve_technologies
    assert r(_item("1", "x", tech="Pydantic"), names) == ["pydantic"]                      # 정규화 일치
    assert r(_item("2", "OpenAI deprecations", vendor="openai"), names, {"openai": ["openai", "gpt"]}) == ["openai-agents", "gpt-6-luna"]
    assert r(_item("3", "Release of pydantic v3"), names) == ["pydantic"]                   # 제목에 이름
    assert r(_item("4", "pydanticx v3"), names) == []                                       # 단어 경계
    assert r(_item("5", "nothing here"), names) == []
    assert r(_item("6", "Python and pydantic", tech="pydantic"), names) == ["pydantic"]         # 의존성 릴리스는 그 의존성으로만 (제목의 Python은 안 붙는다)
    assert r(_item("7", "openai/openai-python v3", tech="openai"), names) == []                # 모르는 의존성의 릴리스는 제목으로 다른 기술에 넘기지 않는다
    assert r(_item("8", "Python and pydantic"), names) == ["pydantic", "Python"]               # 의존성 릴리스가 아니면 제목의 기술을 전부
    # 여러 낱말 match 단어는 이름 전체로 (W43~W46 Gemini 변경 기록이 「기술을 모른다」로 빠졌다) — 토큰으로 쪼개 넓히지는 않는다
    gem = {"google-gemini": ["gemini", "google-genai"], "kb-arena": ["kb-arena"]}
    more = names + ["google-genai", "Google_GenAI", "jhs-arena/elasticsearch-nori:9.5.4", "google-cloud-storage"]
    assert r(_item("9", "[google-gemini] changelog 변경", vendor="google-gemini"), more, gem) == ["google-genai", "Google_GenAI"]
    assert r(_item("10", "[kb-arena] v0.4", vendor="kb-arena"), more, gem) == []                 # arena 토큰으로 이미지 이름에 걸리지 않는다


def test_gate_keep_drop_and_reasons():
    st = _store(pydantic={"tiers": ["L4"]}, pytest={"tiers": ["L4"], "signals": {"manifest": "p", "group": "dev"}})
    be = StubBackend(changes={"1": ["breaking"], "2": ["minor_patch"], "3": ["security"], "4": ["breaking"]})
    g = gate_mod.Gate(be, st)
    r1 = g(_item("1", "pydantic breaking", tech="pydantic"))
    assert r1["keep"] and r1["status"] == "ok" and r1["tiers"] == {"pydantic": ["L4"]} and "L4 × breaking" in r1["reason"]
    assert [(k["tier"], k["change"]) for k in r1["kept_by"]] == [("L4", "breaking")]
    r2 = g(_item("2", "pydantic patch", tech="pydantic"))
    assert not r2["keep"] and r2["status"] == "ok" and "남기는 행 없음" in r2["reason"]
    assert g(_item("3", "pytest cve", tech="pytest"))["keep"]                                 # 개발 도구도 security는 남긴다
    r4 = g(_item("4", "pytest breaking", tech="pytest"))
    assert not r4["keep"] and r4["tiers"] == {"pytest": ["L7"]}                               # 모델은 L4라 했지만 dev 그룹 → L7 (스크립트 승)
    st.data["overrides"][signals.norm("pytest")] = {"technology": "pytest", "tiers": ["L4"]}  # 사람이 L4로 고쳐도 경로는 dev 그대로 → 보정이 기록에 남는다
    r5 = g(_item("4", "pytest breaking", tech="pytest"))
    assert not r5["keep"] and r5["rows"][0]["base"] == Y and r5["rows"][0]["adjusted_by"] and "개발" in r5["rows"][0]["adjusted_by"][0]


def test_gate_unclassified_makes_no_model_call():
    st = _store(pydantic={"tiers": ["L4"]})
    st.data["technologies"][signals.norm("weird")] = {"technology": "weird", "tiers": [], "unresolved": True, "path": "prod", "pin": None}
    be = StubBackend()
    g = gate_mod.Gate(be, st)
    a = g(_item("1", "x", tech="nothing-known"))
    b = g(_item("2", "x", tech="weird"))
    assert a["status"] == b["status"] == "unclassified" and not a["keep"] and not b["keep"] and "weird" in b["reason"]
    assert be.log.calls == []                                                                 # 모를 때는 모델을 부르지 않는다
    st.data["overrides"][signals.norm("weird")] = {"technology": "weird", "tiers": ["L6"]}    # 사람이 등급을 주면 풀린다
    assert g(_item("3", "weird breaking", tech="weird"))["status"] == "ok"


class _Boom(Backend):
    name = "boom"

    def __init__(self, exc):
        super().__init__()
        self.exc = exc

    def classify_tiers(self, q):
        raise self.exc

    def classify_changes(self, q):
        raise self.exc


def test_gate_error_is_safe_failure_but_boundary_stops():
    st = _store(pydantic={"tiers": ["L4"]})
    r = gate_mod.Gate(_Boom(RuntimeError("503 down")), st)(_item("1", "x", tech="pydantic"))
    assert r["status"] == "error" and r["keep"] is False and "503 down" in r["reason"]
    try:
        gate_mod.Gate(_Boom(BoundaryError("api.example.com")), st)(_item("1", "x", tech="pydantic"))
        raise AssertionError("경계 위반이 삼켜졌다")
    except BoundaryError:
        pass


def _profile():
    p, errors, _ = ip.parse_profile({"version": 1, "interests": [{"topic": "하이브리드 검색", "description": "d"}]}, __import__("datetime").date(2026, 10, 6))
    assert not errors
    return p


def test_lane_a_gate_wiring_and_cap():
    st = _store(pydantic={"tiers": ["L4"]}, fastapi={"tiers": ["L4"]}, langfuse={"tiers": ["L3", "L5"]})
    changes = {f"d{i}": ["breaking"] for i in range(7)} | {"s": ["security"], "m": ["minor_patch"], "f": ["breaking"]}
    be = StubBackend(changes=changes)
    items = [_item(f"d{i}", f"pydantic {i}", tech="pydantic") for i in range(7)]
    items += [_item("s", "langfuse cve", tech="langfuse"), _item("m", "fastapi patch", tech="fastapi"), _item("f", "fastapi breaking", tech="fastapi")]
    res = profile_filter.run_filter(items, _profile(), ["kw"], gate=gate_mod.Gate(be, st))
    a = {e["id"]: e for e in res["lane_a"]}
    assert all(e["gate_status"] == "ok" and e["gate"] is not None for e in a.values())
    assert a["m"]["gate"]["keep"] is False and a["m"]["shown"] is None
    summ = res["gate_summary"]
    # 질의별 top-3 (§4 변경 2026-11-04, HYO-168): security는 「항상」, 기술마다 top-3 목록 = detect
    assert summ["mode"] == "topk" and summ["total_kept"] == 9 and summ["label"] == "5/9 · 목록 5", summ["label"]
    assert [x["id"] for x in summ["always"]] == ["s"]
    assert [(x["id"], x["rank"]) for x in summ["listed"]] == [("s", None), ("d0", 1), ("d1", 2), ("d2", 3), ("f", 1)], summ["listed"]
    assert [x["id"] for x in summ["shown"]] == ["s", "d0", "d1", "d2", "f"], summ["shown"]
    assert {c["id"] for c in summ["truncated"]} == {"d3", "d4", "d5", "d6"}
    assert a["s"]["always"] is True and a["d1"]["listed"] is True and a["d1"]["shown"] is True and a["d3"]["listed"] is False and a["d3"]["shown"] is False
    assert a["d0"]["query"] == "pydantic" and a["f"]["rank"] == 1
    # 예전 동작(weekly5): 5/N · 기술당 1칸 먼저 (§4 변경 2026-10-27) — pydantic 7건이 칸을 다 채우지 않고 fastapi가 들어온다
    summ5 = gate_mod.cap_lane_a(res["lane_a"], 5, mode="weekly5")
    assert summ5["total_kept"] == 9 and summ5["label"] == "5/9" and len(summ5["truncated"]) == 4
    assert summ5["shown"][0]["id"] == "s"                                                    # security가 같은 L3·L4 중 가장 먼저 (L3 행이 더 높은 등급)
    assert [(x["id"], x["technology"]) for x in summ5["shown"]] == [("s", "langfuse"), ("d0", "pydantic"), ("d1", "pydantic"), ("d2", "pydantic"),
                                                                    ("f", "fastapi")], summ5["shown"]
    assert summ5["per_tech"] == 1
    assert sum(1 for e in a.values() if e["shown"] is True) == 5 and sum(1 for e in a.values() if e["shown"] is False) == 4
    assert {c["id"] for c in summ5["truncated"]} <= {e["id"] for e in a.values() if e["shown"] is False}
    assert [(k["tier"]) for k in a["s"]["gate"]["kept_by"]] == ["L3", "L5"]                  # 두 등급 모두 기록
    # 관문 없이는 예전 그대로
    res0 = profile_filter.run_filter(items, _profile(), ["kw"])
    assert all(e["gate"] is None and e["gate_status"] == "not-implemented" for e in res0["lane_a"]) and "gate_summary" not in res0


def test_cli_run_with_stub_gate_and_real_api_refused():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        _write(d / "p.yaml", "version: 1\ninterests:\n  - {topic: 하이브리드 검색, description: d}\n")
        items = [_item("a1", "pydantic 3 breaking", tech="pydantic"), _item("a2", "pydantic 3.0.1", tech="pydantic")]
        _write(d / "items.jsonl", "\n".join(json.dumps(i, ensure_ascii=False) for i in items) + "\n")
        st = TierStore(d / "tiers.json")
        st.ensure([TechQuery("pydantic", usage="u")], StubBackend(tiers={"pydantic": ["L4"]}))
        st.save()
        base = ["run", "--snapshot", str(d / "items.jsonl"), "--profile", str(d / "p.yaml"), "--no-stack", "--out", str(d / "out"),
                "--tiers", str(d / "tiers.json")]
        assert profile_filter.main(base + ["--gate", "stub"]) == 0
        run = json.loads((d / "out" / "run.json").read_text(encoding="utf-8"))
        assert run["lane_a_gate"]["model"] == "stub" and run["lane_a_gate"]["total_kept"] == 1 and run["lane_a_gate"]["calls"]["model_calls"] == 2
        assert (d / "out" / "gate-calls.jsonl").exists()
        la = json.loads((d / "out" / "lane_a.json").read_text(encoding="utf-8"))
        assert [e["gate"]["keep"] for e in la] == [True, False]
        try:
            profile_filter.main(base + ["--gate", "gpt-6-luna"])
            raise AssertionError("--real-api 없이 실제 모델이 돌았다")
        except PermissionError:
            pass
        try:
            profile_filter.main(["run", "--snapshot", str(d / "items.jsonl"), "--profile", str(d / "p.yaml"), "--no-stack", "--out", str(d / "o2"),
                                 "--tiers", str(d / "nope.json"), "--gate", "stub"])
            raise AssertionError("등급 파일 없이 관문이 돌았다")
        except SystemExit:
            pass


# ---------------------------------------------------------------- 백엔드 인터페이스 · Jev 모양

class JevLike(Backend):
    """Jev 모양 — 문장을 쓰지 않고, 라벨마다 예/아니오 + 확률만 낸다. reason 없음. 임계값에서 라벨을 만든다."""
    name = "jev-like"

    def __init__(self, tier_probs, change_probs, threshold=0.5):
        super().__init__()
        self.tp, self.cp, self.th = tier_probs, change_probs, threshold

    def _answer(self, probs, order):
        per = {k: {"yes": p >= 0.5, "prob": p} for k, p in probs.items()}
        labels = labels_from_scores(per, self.th, order)
        self.log.add({"flow": "jev", "model": "jev-1.13.0", "input_tokens": 100, "output_tokens": 0, "estimated": False, "cost_usd": 0.0, "attempts": 1})
        return LabelAnswer(labels=labels, per_label=per, unsure=not labels, reason=None)

    def classify_tiers(self, q):
        return self._answer(self.tp[q.name], policy.TIERS)

    def classify_changes(self, q):
        return self._answer(self.cp[q.id], policy.MODEL_CHANGES)


def test_labels_from_scores():
    per = {"a": {"yes": True, "prob": 0.7}, "b": {"yes": True, "prob": 0.4}, "c": {"yes": False, "prob": None}, "d": {"yes": True, "prob": None}}
    assert labels_from_scores(per) == ["a", "b", "d"]                       # 임계값 없음 → 예/아니오 그대로
    assert labels_from_scores(per, 0.5) == ["a", "d"]                       # 확률이 있으면 임계값, 없으면 예/아니오
    assert labels_from_scores(per, 0.5, order=["d", "a"]) == ["d", "a"]


def test_interface_accepts_jev_shape_end_to_end():
    be = JevLike({"pydantic": {"L4": 0.9, "L6": 0.3}, "langfuse": {"L3": 0.8, "L5": 0.7, "L6": 0.1}},
                 {"n1": {"breaking": 0.95, "security": 0.05}, "n2": {"pricing": 0.9, "minor_patch": 0.4}})
    st = TierStore()
    r = st.classify(_q("langfuse"), be)
    assert r["tiers"] == ["L3", "L5"] and r["reason"] is None and r["per_label"]["L5"]["prob"] == 0.7     # 근거 없음·확률 있음
    st.classify(_q("pydantic"), be)
    g = gate_mod.Gate(be, st)
    assert g(_item("n1", "pydantic breaking", tech="pydantic"))["keep"]
    out = g(_item("n2", "langfuse pricing", tech="langfuse"))
    assert out["keep"] and [k["tier"] for k in out["kept_by"]] == ["L3"] and out["changes"] == ["pricing"]
    # 평가 진입점도 같은 백엔드로 끝까지
    gold = {"technologies": [{"name": "pydantic", "usage": "u"}, {"name": "langfuse", "usage": "u"}],
            "items": [{"id": "n1", "technology": "pydantic", "title": "pydantic breaking", "text": "", "gold_changes": ["breaking"], "gold_keep": True, "basis": "b"},
                      {"id": "n2", "technology": "langfuse", "title": "langfuse pricing", "text": "", "gold_changes": ["pricing"], "gold_keep": True, "basis": "b"}]}
    res = evaluate.evaluate(be, gold, 1)
    assert res["runs"][0]["c3_agree"] == 1.0 and res["runs"][0]["c1_miss_count"] == 0


def test_make_backend_gating():
    assert isinstance(make_backend("stub"), StubBackend)
    orig = agents_backend.openai_key
    agents_backend.openai_key = lambda: (_ for _ in ()).throw(AssertionError("키를 읽으려 했다"))
    try:
        for m in ("gpt-6-luna", "gpt-6.1-sol"):
            try:
                make_backend(m)
                raise AssertionError("플래그 없이 실제 백엔드가 만들어졌다")
            except PermissionError:
                pass
    finally:
        agents_backend.openai_key = orig
    from pipeline.importance import jev_backend
    orig_k = jev_backend.typesafe_key
    jev_backend.typesafe_key = lambda: (_ for _ in ()).throw(AssertionError("키를 읽으려 했다"))
    try:
        try:
            make_backend("jev-1.13.0")
            raise AssertionError("플래그 없이 Jev 백엔드가 만들어졌다")
        except PermissionError:
            pass
    finally:
        jev_backend.typesafe_key = orig_k
    try:
        make_backend("llama", allow_real=True)
        raise AssertionError("llama")
    except ValueError:
        pass


# ---------------------------------------------------------------- Agents SDK 백엔드 (가짜 모델 — 네트워크 없음)

from agents import ModelResponse, Usage  # noqa: E402
from agents.models.interface import Model  # noqa: E402
from openai.types.responses import ResponseOutputMessage, ResponseOutputText  # noqa: E402


class Transient(Exception):
    status_code = 503


class BadRequest(Exception):
    status_code = 400


class FakeModel(Model):
    """script: 호출마다 하나씩 — dict(출력 JSON) · Exception(던짐) · callable(부르고 dict를 돌려줌)."""

    def __init__(self, script, tokens=(10, 5)):
        self.script, self.tokens, self.n, self.seen = list(script), tokens, 0, []

    async def get_response(self, system_instructions, input, model_settings, tools, output_schema, handoffs, tracing, **kw):
        self.seen.append((system_instructions, input, output_schema))
        step = self.script.pop(0)
        if callable(step):
            step = step()
        if isinstance(step, Exception):
            raise step
        self.n += 1
        msg = ResponseOutputMessage(type="message", id=f"m{self.n}", role="assistant", status="completed",
                                    content=[ResponseOutputText(type="output_text", text=json.dumps(step, ensure_ascii=False), annotations=[])])
        return ModelResponse(output=[msg], usage=Usage(requests=1, input_tokens=self.tokens[0], output_tokens=self.tokens[1],
                                                         total_tokens=sum(self.tokens)), response_id=None)

    def stream_response(self, *a, **k):
        raise NotImplementedError


def _agents(fake, model="gpt-6-luna", guard=True, **kw):
    b = agents_backend.AgentsBackend(model, key="sk-test", export_traces=False, guard=guard, model_factory=lambda client, name: fake, **kw)
    b.retry.SLEEPER.sleep = lambda s: None

    async def nosleep(s):
        return None
    b.retry.SLEEPER.asleep = nosleep
    return b


def test_agents_backend_structured_output_log_and_trace():
    fake = FakeModel([{"tiers": ["L5", "L3"], "unsure": False, "reason": "서비스이자 SDK"},
                      {"changes": ["security", "breaking", "breaking"], "reason": "CVE"},
                      {"tiers": [], "unsure": True, "reason": "쓰임새 모른다"}], tokens=(2000, 100))
    b = _agents(fake)
    try:
        a = b.classify_tiers(TechQuery("langfuse", usage="u"))
        assert a.labels == ["L3", "L5"] and not a.unsure and a.reason == "서비스이자 SDK"            # 등급 순서로 정렬
        c = b.classify_changes(NewsQuery("n1", "pydantic", "t", "text"))
        assert c.labels == ["breaking", "security"]                                                  # 중복 제거 · 정책 순서
        u = b.classify_tiers(TechQuery("zzz"))
        assert u.unsure and u.labels == []
        tot = b.log.totals()
        assert tot["model_calls"] == 3 and tot["input_tokens"] == 6000 and tot["output_tokens"] == 300 and tot["errors"] == 0
        rec = b.log.calls[0]
        assert rec["type"] == "llm_call" and rec["model"] == "gpt-6-luna" and rec["attempts"] == 1 and rec["flow"] == "tiers"
        assert abs(rec["cost_usd"] - (2000 / 1e6 * 0.10 + 100 / 1e6 * 0.50)) < 1e-9                  # Luna 단가 (ADR 0007)
        assert "langfuse" in str(fake.seen[0][1]) and "L0" in str(fake.seen[0][0])                   # 프롬프트가 모델에 닿았다
        assert fake.seen[0][2] is not None                                                           # 구조화 출력(output_type)이 걸렸다
        assert b.trace_info()["traces"] >= 3 and b.trace_info()["spans"] >= 3                        # 트레이싱이 켜져 있다
    finally:
        b.close()


def test_agents_backend_unpriced_model_is_none_not_zero():
    b = _agents(FakeModel([{"changes": [], "reason": "r"}]), model="gpt-6-astra")      # 단가표에 없는 모델
    try:
        b.classify_changes(NewsQuery("n", "t", "t"))
        assert b.log.calls[0]["cost_usd"] is None and b.log.totals()["unpriced_calls"] == 1 and b.log.calls[0]["model"] == "gpt-6-astra"
    finally:
        b.close()


def test_agents_backend_flex_sends_service_tier_and_halves_cost():
    assert agents_backend.cost_usd("gpt-6.1-sol", 1_000_000, 1_000_000) == 12.0
    assert agents_backend.cost_usd("gpt-6.1-sol", 1_000_000, 1_000_000, "flex") == 6.0          # Flex = Batch = 반값
    b = _agents(FakeModel([{"changes": [], "reason": "r"}]), model="gpt-6.1-sol", service_tier="flex")
    try:
        assert b.settings.extra_args == {"service_tier": "flex"} and b.settings.reasoning.effort == "medium"   # effort는 Luna와 같다
        b.classify_changes(NewsQuery("n", "t", "t"))
        c = b.log.calls[0]
        assert c["service_tier"] == "flex" and c["cost_usd"] == agents_backend.cost_usd("gpt-6.1-sol", c["input_tokens"], c["output_tokens"], "flex")
    finally:
        b.close()
    plain = _agents(FakeModel([]), model="gpt-6.1-sol")
    try:
        assert plain.settings.extra_args is None                                                   # 기본은 Standard
    finally:
        plain.close()
    try:
        _agents(FakeModel([]), service_tier="priority")
        raise AssertionError("모르는 service_tier를 받아들였다")
    except ValueError:
        pass
    assert evaluate.main(["--gold", str(REPO / "eval" / "importance" / "gold.example.json"), "--model", "jev-1.13.0", "--plan", "--flex"]) == 2


def test_agents_backend_retry_policy_same_as_product():
    fake = FakeModel([Transient("503"), Transient("503"), {"changes": ["major"], "reason": "r"}])
    b = _agents(fake)
    try:
        assert b.classify_changes(NewsQuery("n", "t", "t")).labels == ["major"]
        rec = b.log.calls[0]
        assert rec["attempts"] == 3 and rec["retried"] == ["Transient(503)", "Transient(503)"] and not rec.get("error")
        assert b.retry.MAX_ATTEMPTS == 5 and b.retry.BACKOFF_BASE == 2.0                              # product/radar/retry.py의 상수
    finally:
        b.close()
    fake = FakeModel([BadRequest("400"), {"changes": [], "reason": "r"}])                             # 그 밖의 4xx는 재시도하지 않는다
    b = _agents(fake)
    try:
        try:
            b.classify_changes(NewsQuery("n", "t", "t"))
            raise AssertionError("400이 삼켜졌다")
        except BadRequest:
            pass
        assert b.log.calls[0]["attempts"] == 1 and "BadRequest" in b.log.calls[0]["error"] and len(fake.script) == 1
    finally:
        b.close()


def test_netguard_blocks_outside_api_openai_and_stops_without_retry():
    real = socket.getaddrinfo

    def offline(host, *a, **k):          # 실제 DNS를 부르지 않는다 — 가드가 막지 못하면 여기서 gaierror가 아니라 통과로 보인다
        raise socket.gaierror("offline test")
    socket.getaddrinfo = offline
    try:
        def leaks():
            socket.getaddrinfo("telemetry.example.invalid", 443)
            return {"changes": [], "reason": "r"}
        fake = FakeModel([leaks, {"changes": [], "reason": "r"}])
        b = _agents(fake)
        try:
            try:
                b.classify_changes(NewsQuery("n", "t", "t"))
                raise AssertionError("허용 밖 호스트가 막히지 않았다")
            except BoundaryError as e:
                assert "telemetry.example.invalid" in str(e)
            rec = b.log.calls[0]
            assert rec["attempts"] == 1 and rec["blocked_hosts"] == ["telemetry.example.invalid"] and len(fake.script) == 1   # 재시도 없음
            assert b.trace_info()["blocked_hosts"] == ["telemetry.example.invalid"]
            g = b.guard
            assert g._ok("api.openai.com") and not g._ok("api.anthropic.com") and not g._ok("evil.example")
            assert agents_backend.ALLOWED_HOSTS == {"api.openai.com"}
        finally:
            b.close()
    finally:
        socket.getaddrinfo = real


# ---------------------------------------------------------------- Jev 백엔드 (로컬 가짜 HTTP 서버 127.0.0.1 — 실제 API·외부 네트워크 없음)

import http.server  # noqa: E402
import os  # noqa: E402
import threading  # noqa: E402
import urllib.request  # noqa: E402

from pipeline.importance import jev_backend, prompts  # noqa: E402


def _noul_answers(**p):
    return {k: {"type": "noul", "noul": p.get(k, 0.0)} for k in policy.MODEL_CHANGES}


def _ok(answers, model="jev-1.13.0", i=1234, o=9):
    return 200, {}, {"model": model, "usage": {"input_tokens": i, "output_tokens": o}, "answers": answers}


def _choice(top, probs):
    return {"tier": {"type": "choice", "choice": top, "confidence": probs[top], "probabilities": probs}}


class FakeJev:
    """로컬 HTTP 서버. script: 요청마다 하나 — (status, headers, json) | 호출 가능(요청 본문 dict → 그 튜플). seen에 요청이 쌓인다."""

    def __init__(self, script):
        outer = self
        self.script, self.seen = list(script), []

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                body = json.loads(raw.decode("utf-8"))
                outer.seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                step = outer.script.pop(0)
                status, headers, obj = step(body) if callable(step) else step
                data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def _jev(fake, guard=False, **kw):
    b = jev_backend.JevBackend("jev-1.13.0", key="sk-test", base_url=fake.url, guard=guard, opener=urllib.request.build_opener(urllib.request.ProxyHandler({})), **kw)
    b.sleeps = []
    b.retry.SLEEPER.sleep = b.sleeps.append          # 실제로 기다리지 않는다
    return b


def test_jev_request_shape_and_parallel_questions():
    fake = FakeJev([_ok(_noul_answers(breaking=0.9)), _ok(_choice("L4", {"L4": 0.9, "L6": 0.1}))])
    b = _jev(fake)
    try:
        a = b.classify_changes(NewsQuery("n1", "pydantic", "pydantic v3 — breaking", "Breaking changes: validators removed."))
        t = b.classify_tiers(TechQuery("pydantic", usage="앱 뼈대", source="pyproject", signals={"manifest": "pyproject.toml", "group": "main"}))
        assert a.labels == ["breaking"] and t.labels == ["L4"] and len(fake.seen) == 2        # 소식 하나·기술 하나 = 요청 하나씩
        r = fake.seen[0]
        assert r["path"] == "/v1/systemone" and r["headers"]["authorization"] == "Bearer sk-test"
        assert r["headers"]["content-type"] == "application/json" and r["headers"]["accept"] == "application/json"
        assert sorted(r["body"]) == ["model", "questions", "state"] and r["body"]["model"] == "jev-1.13.0"
        assert list(r["body"]["questions"]) == policy.MODEL_CHANGES                              # 변화 종류 9개가 한 요청에 병렬
        assert r["body"]["state"] == {"technology": "pydantic", "title": "pydantic v3 — breaking", "body": "Breaking changes: validators removed."}
        for kind, qn in r["body"]["questions"].items():
            assert qn["type"] == "noul" and kind in qn["instructions"] and prompts.CHANGE_MEANING[kind] in qn["instructions"]
            assert set(qn["criteria"]) == {"true", "false"}
        r2 = fake.seen[1]["body"]
        assert list(r2["questions"]) == ["tier"] and r2["questions"]["tier"]["type"] == "choice"      # 등급은 선택지 한 질문
        assert list(r2["questions"]["tier"]["criteria"]) == policy.TIERS and r2["model"] == "jev-1.13.0"
        assert r2["state"] == {"technology": "pydantic", "usage": "앱 뼈대", "source": "pyproject"}
        assert "pyproject.toml" not in json.dumps(r2, ensure_ascii=False)                   # 스크립트 신호는 모델에 안 보낸다
    finally:
        b.close()
        fake.close()


def test_jev_threshold_is_fixed_half_boundary():
    assert jev_backend.THRESHOLD == 0.5
    fake = FakeJev([_ok(_noul_answers(breaking=0.5, security=0.4999, deprecation=0.5001, pricing=1.0, major=0.0))])
    b = _jev(fake)
    try:
        a = b.classify_changes(NewsQuery("n", "x", "t"))
        assert a.labels == ["breaking", "deprecation", "pricing"]       # 0.5는 예 · 0.5001도 예 · 정책 순서
        assert "security" not in a.labels                                                  # 0.4999는 아니오
        assert a.per_label["breaking"] == {"yes": True, "prob": 0.5} and a.per_label["security"] == {"yes": False, "prob": 0.4999}
        assert a.reason is None and len(a.calls) == 1                                       # 근거 텍스트 없음 (ADR 0001 c2) · 호출 기록은 붙는다
    finally:
        b.close()
        fake.close()


def test_jev_second_tier_needs_half():
    fake = FakeJev([_ok(_choice("L5", {"L3": 0.5, "L5": 0.5})),                     # 두 번째 등급 p=0.5 → 두 등급 (등급 순서로)
                    _ok(_choice("L5", {"L3": 0.4999, "L5": 0.5001})),               # 0.4999 → 한 등급
                    _ok(_choice("L4", {"L4": 0.4, "L6": 0.35, "L7": 0.25}))])      # 고른 등급은 p가 낮아도 그대로 (두 번째만 문턱)
    b = _jev(fake)
    try:
        two = b.classify_tiers(TechQuery("langfuse"))
        assert two.labels == ["L3", "L5"] and not two.unsure and two.per_label["L3"]["yes"] and two.per_label["L5"]["prob"] == 0.5
        assert b.classify_tiers(TechQuery("langfuse")).labels == ["L5"]
        assert b.classify_tiers(TechQuery("x")).labels == ["L4"]
        # 저장소·검증까지: langfuse 두 등급 L3+L5가 S3를 지나 유지된다
        st = TierStore()
        fake.script.append(_ok(_choice("L3", {"L3": 0.5, "L5": 0.5})))
        rec = st.classify(_q("langfuse", manifest="requirements.txt", group="main"), b)
        assert rec["tiers"] == ["L3", "L5"] and rec["mismatch"] is None and rec["reason"] is None and rec["per_label"]["L5"]["prob"] == 0.5
    finally:
        b.close()
        fake.close()


def test_jev_call_log_cost_and_returned_model():
    fake = FakeJev([_ok(_noul_answers(), i=2000, o=7), _ok(_noul_answers(), model="jev-1.14.0", i=1000)])
    b = _jev(fake)
    try:
        b.classify_changes(NewsQuery("n", "x", "t"))
        b.classify_changes(NewsQuery("m", "x", "t"))
        c1, c2 = b.log.calls
        assert c1["type"] == "llm_call" and c1["model"] == "jev-1.13.0" and c1["requested_model"] == "jev-1.13.0" and c1["model_mismatch"] is False   # 응답 model 필드
        assert c1["input_tokens"] == 2000 and c1["output_tokens"] == 7 and abs(c1["cost_usd"] - 2000 * 0.042 / 1e6) < 1e-12 and c1["attempts"] == 1
        assert c2["model"] == "jev-1.14.0" and c2["model_mismatch"] is True                  # 요청과 다른 모델이 답하면 숨기지 않는다
        tot = b.log.totals()
        assert tot["input_tokens"] == 3000 and abs(tot["cost_usd"] - 3000 * 0.042 / 1e6) < 1e-5 and tot["unpriced_calls"] == 0
        assert b.run_info()["models_returned"] == ["jev-1.13.0", "jev-1.14.0"] and b.run_info()["threshold"] == 0.5
    finally:
        b.close()
        fake.close()


def test_jev_retry_429_respects_retry_after_and_4xx_is_not_retried():
    fake = FakeJev([(429, {"retry-after": "7"}, {"error": "rate"}), (503, {}, {"error": "busy"}), (429, {"retry-after-ms": "9000"}, {}), _ok(_noul_answers(security=0.9))])
    b = _jev(fake)
    try:
        assert b.classify_changes(NewsQuery("n", "x", "t")).labels == ["security"]
        rec = b.log.calls[0]
        assert rec["attempts"] == 4 and rec["retried"] == ["JevHTTPError(429)", "JevHTTPError(503)", "JevHTTPError(429)"] and not rec.get("error")
        assert b.sleeps[0] >= 7.0 and b.sleeps[2] >= 9.0 and b.sleeps[1] <= 4.0           # 429는 retry-after 이상 기다리고, 503은 정책 지연(2·4·8·16초) 안
        assert b.retry.MAX_ATTEMPTS == 5 and b.retry.BACKOFF_BASE == 2.0                    # product/radar/retry.py의 상수
    finally:
        b.close()
        fake.close()
    fake = FakeJev([(400, {}, {"detail": "bad"}), _ok(_noul_answers())])                  # 그 밖의 4xx는 다시 불러도 같다
    b = _jev(fake)
    try:
        try:
            b.classify_changes(NewsQuery("n", "x", "t"))
            raise AssertionError("400이 삼켜졌다")
        except jev_backend.JevHTTPError as e:
            assert e.status_code == 400
        assert len(fake.seen) == 1 and b.sleeps == [] and b.log.calls[0]["attempts"] == 1 and "JevHTTPError" in b.log.calls[0]["error"]
        assert b.log.calls[0]["cost_usd"] == 0.0 and b.log.totals()["errors"] == 1
    finally:
        b.close()
        fake.close()
    fake = FakeJev([(429, {}, {})] * 5 + [_ok(_noul_answers())])                           # 끝내 안 풀리면 5번째에서 멈춘다
    b = _jev(fake)
    try:
        try:
            b.classify_changes(NewsQuery("n", "x", "t"))
            raise AssertionError("끝없이 재시도했다")
        except jev_backend.JevHTTPError:
            pass
        assert len(fake.seen) == 5 and b.log.calls[0]["attempts"] == 5 and len(b.log.calls[0]["retried"]) == 4
    finally:
        b.close()
        fake.close()


def test_jev_malformed_response_is_safe_failure_not_silent_no():
    miss = _noul_answers()
    del miss["pricing"]
    bad_p = _noul_answers(breaking=1.5)
    fake = FakeJev([_ok(miss), _ok(bad_p), (200, {}, {"model": "jev-1.13.0", "answers": _noul_answers()})])
    b = _jev(fake)
    try:
        for _ in range(3):
            try:
                b.classify_changes(NewsQuery("n", "x", "t"))
                raise AssertionError("깨진 응답이 통과했다")
            except jev_backend.JevResponseError:
                pass
        assert len(fake.seen) == 3 and b.sleeps == [] and b.log.totals()["errors"] == 3          # 재시도 없음 · 기록됨
        assert b.log.calls[0]["input_tokens"] == 1234 and b.log.calls[0]["cost_usd"] > 0 and "pricing" in b.log.calls[0]["error"]   # 응답은 왔으니 비용은 그대로
        # 관문: 호출 실패는 버림 + status=error (조용히 아니오로 세지 않는다)
        st = _store(pydantic={"tiers": ["L4"]})
        fake.script.append(_ok(miss))
        out = gate_mod.Gate(b, st)(_item("n", "pydantic breaking", tech="pydantic"))
        assert out["status"] == "error" and not out["keep"]
    finally:
        b.close()
        fake.close()


def test_jev_pinned_model_key_gating_and_env_is_not_exported():
    for alias in ("jev-latest", "jev-preview", "jev-1.13"):
        try:
            jev_backend.JevBackend(alias, key="k", guard=False)
            raise AssertionError(f"alias {alias}가 받아들여졌다")
        except ValueError:
            pass
    assert jev_backend.MODEL_ID == "jev-1.13.0" and jev_backend.PINNED_MODELS == {"jev-1.13.0"}
    with tempfile.TemporaryDirectory() as d:
        env = Path(d) / ".env"
        env.write_text('OPENAI_API_KEY=sk-openai\nTYPESAFE_API_KEY="ts-secret"\n', encoding="utf-8")
        old = os.environ.get("RADAR_ENV_FILE")
        os.environ["RADAR_ENV_FILE"] = str(env)
        try:
            assert jev_backend.typesafe_key() == "ts-secret" and "TYPESAFE_API_KEY" not in os.environ      # os.environ에 넣지 않는다
            env.write_text("OPENAI_API_KEY=sk-openai\n", encoding="utf-8")
            try:
                jev_backend.typesafe_key()
                raise AssertionError("키 없이 통과")
            except SystemExit:
                pass
        finally:
            if old is None:
                os.environ.pop("RADAR_ENV_FILE", None)
            else:
                os.environ["RADAR_ENV_FILE"] = old
    assert jev_backend.parse_retry_after({"retry-after": "3"}) == 3.0 and abs(jev_backend.parse_retry_after({"retry-after-ms": "1500"}) - 1.5) < 1e-9
    assert jev_backend.parse_retry_after({"retry-after": "x"}) is None and jev_backend.parse_retry_after({}) is None and jev_backend.parse_retry_after({"retry-after": "-1"}) is None


def test_jev_netguard_allows_only_typesafe_host():
    real = socket.getaddrinfo

    def offline(host, *a, **k):          # 실제 DNS를 부르지 않는다
        raise socket.gaierror("offline test")
    socket.getaddrinfo = offline
    try:
        # (1) 허용 밖 호스트(OpenAI 호스트 포함) → 막고, 재시도 없이 BoundaryError
        b = jev_backend.JevBackend("jev-1.13.0", key="k", base_url="https://api.openai.com", guard=True)
        b.sleeps = []
        b.retry.SLEEPER.sleep = b.sleeps.append
        try:
            try:
                b.classify_changes(NewsQuery("n", "x", "t"))
                raise AssertionError("허용 밖 호스트가 막히지 않았다")
            except BoundaryError as e:
                assert "api.openai.com" in str(e)
            rec = b.log.calls[0]
            assert rec["attempts"] == 1 and rec["blocked_hosts"] == ["api.openai.com"] and b.sleeps == []
            g = b.guard
            assert g._ok("api.typesafe.ai") and not g._ok("api.openai.com") and not g._ok("api.anthropic.com") and not g._ok("evil.example")
        finally:
            b.close()
        assert jev_backend.ALLOWED_HOSTS == {"api.typesafe.ai"} and agents_backend.ALLOWED_HOSTS == {"api.openai.com"}   # 백엔드별 허용 목록
        # (2) TypeSafe 호스트는 가드를 통과한다 (오프라인이라 연결 오류가 나고, 가드가 막은 것은 아니다)
        b = jev_backend.JevBackend("jev-1.13.0", key="k", guard=True)
        b.sleeps = []
        b.retry.SLEEPER.sleep = b.sleeps.append
        try:
            try:
                b.classify_changes(NewsQuery("n", "x", "t"))
                raise AssertionError("오프라인인데 성공했다")
            except BoundaryError:
                raise AssertionError("허용 호스트가 막혔다")
            except jev_backend.JevConnectionError:
                pass
            rec = b.log.calls[0]
            assert b.guard.attempts == [] and rec["attempts"] == 5 and "blocked_hosts" not in rec     # 연결 오류는 재시도 정책대로 5번
        finally:
            b.close()
    finally:
        socket.getaddrinfo = real


def test_jev_prompts_carry_same_definitions_as_gpt():
    for kind, meaning in prompts.CHANGE_MEANING.items():
        assert f"| {kind} | {meaning} |" in prompts.CHANGE_INSTRUCTIONS, kind                    # GPT 프롬프트 표와 같은 문구
    for t, (name, meaning) in prompts.TIER_MEANING.items():
        assert f"| {t} | {name} | {meaning} |" in prompts.TIER_INSTRUCTIONS, t
    assert list(prompts.CHANGE_MEANING) == policy.MODEL_CHANGES and list(prompts.TIER_MEANING) == policy.TIERS


def test_jev_end_to_end_lane_a_and_evaluate():
    tier_p = {"pydantic": ("L4", {"L4": 0.9, "L6": 0.1}), "langfuse": ("L3", {"L3": 0.5, "L5": 0.5}), "gpt-6-luna": ("L2", {"L2": 0.95, "L3": 0.05})}
    change_p = {"j1": {"breaking": 0.97, "major": 0.8}, "j2": {"pricing": 0.9}, "j3": {"model_retirement": 0.99, "deprecation": 0.7}, "j4": {"minor_patch": 0.6}}

    def respond(body):
        if list(body["questions"]) == ["tier"]:
            top, probs = tier_p[body["state"]["technology"]]
            return _ok(_choice(top, probs))
        return _ok(_noul_answers(**change_p[body["state"]["title"].split()[0]]))
    fake = FakeJev([respond] * 200)
    gold = {"technologies": [{"name": "pydantic", "usage": "u", "signals": {"manifest": "pyproject.toml", "group": "main", "pin": "pinned"}},
                             {"name": "langfuse", "usage": "u", "signals": {"manifest": "pyproject.toml", "group": "main", "pin": "pinned"}},
                             {"name": "gpt-6-luna", "usage": "u"}],
            "items": [{"id": "j1", "technology": "pydantic", "title": "j1 pydantic v3", "gold_changes": ["breaking", "major"], "gold_keep": True, "basis": "b"},
                      {"id": "j2", "technology": "langfuse", "title": "j2 langfuse pricing", "gold_changes": ["pricing"], "gold_keep": True, "basis": "b"},
                      {"id": "j3", "technology": "gpt-6-luna", "title": "j3 luna retired", "gold_changes": ["model_retirement", "deprecation"], "gold_keep": True, "basis": "b"},
                      {"id": "j4", "technology": "pydantic", "title": "j4 pydantic patch", "gold_changes": ["minor_patch"], "gold_keep": False, "basis": "b"}]}
    b = _jev(fake, guard=True)                      # 가드를 켠 채 — 루프백은 열려 있다
    try:
        res = evaluate.evaluate(b, gold, 2)
        assert all(r["c1_miss_count"] == 0 and r["c3_agree"] == 1.0 and r["c2_noise"] == 0.0 for r in res["runs"])
        tp = res["technologies"][0]["langfuse"]
        assert tp["tiers"] == ["L3", "L5"] and tp["mismatch"] is None                         # S3가 서비스 등급을 떨어내지 않는다 → 두 등급 유지
        assert res["predictions"][0]["j2"]["kept_by"] == [{"technology": "langfuse", "tier": "L3", "change": "pricing"}]
        assert b.log.totals()["model_calls"] == (3 + 4) * 2 == len(fake.seen) and b.log.totals()["errors"] == 0
        assert b.run_info()["models_returned"] == ["jev-1.13.0"]
    finally:
        b.close()
    # CLI: 플래그 없으면 거부(키 안 읽음) · --plan은 API·키 없이 · --real-api면 끝까지 돌고 summary에 응답 모델이 남는다
    gold_path = str(REPO / "eval" / "importance" / "gold.example.json")
    orig_make, orig_key = evaluate.make_backend, jev_backend.typesafe_key
    evaluate.make_backend = lambda *a, **k: (_ for _ in ()).throw(AssertionError("백엔드를 만들었다"))
    jev_backend.typesafe_key = lambda: (_ for _ in ()).throw(AssertionError("키를 읽으려 했다"))
    try:
        assert evaluate.main(["--gold", gold_path, "--model", "jev-1.13.0", "--k", "3"]) == 2
        assert evaluate.main(["--gold", gold_path, "--model", "jev-1.13.0", "--k", "3", "--plan"]) == 0
        fake.script[:] = [lambda body: _ok(_choice("L4", {"L4": 1.0})) if list(body["questions"]) == ["tier"] else _ok(_noul_answers())] * 100
        evaluate.make_backend = lambda model, allow_real=False, log=None, **k: _jev(fake, log=log)
        with tempfile.TemporaryDirectory() as d:
            evaluate.main(["--gold", gold_path, "--model", "jev-1.13.0", "--k", "1", "--no-tier-diag", "--real-api", "--out", d])
            s = json.loads((Path(d) / "summary.json").read_text(encoding="utf-8"))
            assert s["backend_info"]["models_returned"] == ["jev-1.13.0"] and s["calls"]["model_calls"] == 9
            rows = [json.loads(x) for x in (Path(d) / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
            assert all(r["model"] == "jev-1.13.0" and "cost_usd" in r for r in rows)
    finally:
        evaluate.make_backend, jev_backend.typesafe_key = orig_make, orig_key
        fake.close()


# ---------------------------------------------------------------- 채점

def _g(i, keep, changes):
    return {"id": i, "technology": "t", "title": "t", "gold_changes": changes, "gold_keep": keep, "basis": "b"}


def _p(keep, status="ok", changes=()):
    return {"keep": keep, "status": status, "changes": list(changes)}


GOLD6 = [_g("g1", True, ["security"]), _g("g2", True, ["model_retirement"]), _g("g3", True, ["breaking"]),
         _g("g4", False, ["minor_patch"]), _g("g5", False, ["major"]), _g("g6", False, ["minor_patch"])]


def test_score_run_by_hand():
    preds = {"g1": _p(True, changes=["security"]), "g2": _p(False, changes=["model_retirement"]), "g3": _p(True, changes=["breaking"]),
             "g4": _p(True, changes=["minor_patch"]), "g5": _p(False, changes=["major"]), "g6": _p(False, "unclassified")}
    r = evaluate.score_run(preds, GOLD6)
    # 남김 g1·g3·g4 = 3, 그중 정답 ❌ = g4 → 1/3 · 일치 g1·g3·g5·g6 = 4/6 · C1: 정답 ✅ 안전 항목을 버린 것 = g2
    assert r["kept"] == 3 and r["kept_but_gold_drop"] == 1 and abs(r["c2_noise"] - 1 / 3) < 1e-12
    assert abs(r["c3_agree"] - 4 / 6) < 1e-12 and r["c1_misses"] == ["g2"] and r["c1_miss_count"] == 1
    assert r["unclassified"] == ["g6"] and r["errors"] == [] and abs(r["change_label_exact"] - 5 / 6) < 1e-12
    # 안전 항목이 아닌 ✅(breaking)을 버려도 C1은 아니다 — C3만 깎인다
    r2 = evaluate.score_run({**preds, "g2": _p(True), "g3": _p(False)}, GOLD6)
    assert r2["c1_miss_count"] == 0 and r2["kept"] == 3
    # 정답이 ❌인 security는 버려도 C1이 아니다 / 정답 ✅인 security를 오류로 못 분류해도 버림으로 센다
    r3 = evaluate.score_run({"g1": _p(False, "error")}, [_g("g1", True, ["security"]), _g("g7", False, ["security"])])
    assert r3["c1_misses"] == ["g1", ] and r3["errors"] == ["g1"] and r3["unclassified"] == ["g7"]     # g7: 예측이 없다 → missing = 분류 안 됨
    r4 = evaluate.score_run({"g1": _p(True, "error")}, [_g("g1", True, ["security"])])               # 오류 상태에서 keep=True가 와도 남김으로 세지 않는다
    assert r4["kept"] == 0 and r4["c1_miss_count"] == 1
    assert evaluate.score_run({}, GOLD6)["kept"] == 0 and evaluate.score_run({}, GOLD6)["c2_noise"] == 0.0   # 하나도 안 남기면 소음 0 (C3이 막는다)


def _run(c1=0, noise=0.0, agree=1.0):
    return {"c1_miss_count": c1, "c2_noise": noise, "c3_agree": agree}


def test_judge_pass_k_and_thresholds():
    assert evaluate.judge([_run()] * 3)["pass"] is True
    j = evaluate.judge([_run(), _run(), _run(c1=1)])                              # k=3 중 한 번만 놓쳐도 불통과 (pass^k)
    assert j["C1"]["pass"] is False and j["pass"] is False and j["C1"]["misses_per_run"] == [0, 0, 1]
    assert evaluate.judge([_run(c1=1), _run(), _run()])["C1"]["pass"] is False    # 첫 번째만 놓친 경우도 (마지막만 보는 구현을 잡는다)
    assert evaluate.judge([_run(noise=0.15)] * 3)["C2"]["pass"] is True           # 경계 ≤ 15%
    assert evaluate.judge([_run(noise=0.1501)] * 3)["C2"]["pass"] is False
    assert evaluate.judge([_run(), _run(noise=0.2), _run()])["C2"]["pass"] is False
    assert evaluate.judge([_run(agree=0.80)] * 3)["C3"]["pass"] is True           # 경계 ≥ 80%
    assert evaluate.judge([_run(agree=0.7999)] * 3)["C3"]["pass"] is False
    assert evaluate.judge([_run(agree=0.9), _run(agree=0.79), _run(agree=0.9)])["C3"]["pass"] is False
    s = evaluate.judge([_run()])                                                  # k=1은 스모크 — 판정하지 않는다
    assert s["smoke"] and s["pass"] is None and "스모크" in s["note"]
    assert (evaluate.C1_MAX_MISSES, evaluate.C2_MAX_NOISE, evaluate.C3_MIN_AGREE, evaluate.K_REQUIRED) == (0, 0.15, 0.80, 3)   # 등록된 기준
    assert evaluate.SAFETY_CHANGES == {"model_retirement", "security"}


def test_validate_gold():
    ok = json.loads((REPO / "eval" / "importance" / "gold.example.json").read_text(encoding="utf-8"))
    assert evaluate.validate_gold(ok) is ok
    def bad(mut):
        import copy
        g = copy.deepcopy(ok)
        mut(g)
        try:
            evaluate.validate_gold(g)
        except evaluate.GoldError as e:
            return str(e)
        raise AssertionError("나쁜 정답지가 통과했다")
    assert "모르는 변화 종류" in bad(lambda g: g["items"][0].__setitem__("gold_changes", ["breakin"]))
    assert "id 중복" in bad(lambda g: g["items"][1].__setitem__("id", "ex-1"))
    assert "technologies에 없다" in bad(lambda g: g["items"][0].__setitem__("technology", "nope"))
    assert "gold_keep" in bad(lambda g: g["items"][0].__setitem__("gold_keep", "yes"))
    assert "basis 없음" in bad(lambda g: g["items"][0].pop("basis"))
    assert "items가 비었다" in bad(lambda g: g.__setitem__("items", []))


def _gold_oracle():
    gold = json.loads((REPO / "eval" / "importance" / "gold.example.json").read_text(encoding="utf-8"))
    be_tiers = {"pydantic": ["L4"], "pytest": ["L7"], "gpt-6-luna": ["L2"], "python:3.12-slim": ["L1"]}
    be_changes = {it["id"]: it["gold_changes"] for it in gold["items"]}
    return gold, be_tiers, be_changes


def test_evaluate_end_to_end_with_stub_oracle():
    gold, tiers_, changes = _gold_oracle()
    res = evaluate.evaluate(StubBackend(tiers=tiers_, changes=changes), gold, 3)
    assert res["judgement"]["pass"] is True and all(r["c3_agree"] == 1.0 and r["c1_miss_count"] == 0 for r in res["runs"])
    assert res["runs"][0]["kept"] == 4 and res["predictions"][0]["ex-2"]["keep"] is False
    # 모델 은퇴를 못 짚는 모델(모델 은퇴 ✅를 ❌로 버림) → C1 불통과
    blind = dict(changes, **{"ex-4": ["deprecation"]})
    blind["ex-4"] = ["minor_patch"]
    res = evaluate.evaluate(StubBackend(tiers=tiers_, changes=blind), gold, 3)
    assert res["judgement"]["C1"]["pass"] is False and res["runs"][0]["c1_misses"] == ["ex-4"] and res["judgement"]["pass"] is False
    # 매번 하나씩 번갈아 놓치는 모델 — k=3 중 일부 실행에서만 놓쳐도 불통과
    flip = {"n": 0}

    def flaky(q):
        if q.id == "ex-3":
            flip["n"] += 1
            return ["minor_patch"] if flip["n"] == 2 else ["security"]
        return changes[q.id]
    res = evaluate.evaluate(StubBackend(tiers=tiers_, changes=flaky), gold, 3)
    assert res["judgement"]["C1"]["misses_per_run"] == [0, 1, 0] and res["judgement"]["C1"]["pass"] is False
    # 스크립트 검증이 모델을 이긴다: 모델이 pytest를 L4로 매겨도 dev 그룹이라 L7 → breaking 소식은 버려진다
    res = evaluate.evaluate(StubBackend(tiers=dict(tiers_, pytest=["L4"]), changes=dict(changes, **{"ex-2": ["breaking"]})), gold, 1)
    assert res["predictions"][0]["ex-2"]["keep"] is False and res["technologies"][0]["pytest"]["mismatch"]["rule"].startswith("S2")


def test_evaluate_fixed_tiers_skips_tier_calls_and_supplements_from_gold():
    gold, _, changes = _gold_oracle()
    gold_tiers = {"pydantic": (["L4"], "prod", "pinned"), "pytest": (["L7"], "dev", "range"), "gpt-6-luna": (["L2"], "prod", None),
                  "python:3.12-slim": (["L1"], "prod", "pinned")}
    for t in gold["technologies"]:                                                   # W41 정답지처럼 기술마다 정답 등급을 단다
        t["gold_tiers"], t["gold_path"], t["gold_pin"] = gold_tiers[t["name"]]
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "tiers.json"
        st = TierStore(path)
        st.data["technologies"][signals.norm("pydantic")] = {"technology": "pydantic", "tiers": ["L6"], "unresolved": False, "path": "prod", "pin": "pinned"}
        st.data["technologies"][signals.norm("pytest")] = {"technology": "pytest", "tiers": [], "unresolved": True, "path": "dev", "pin": None}
        st.data["overrides"][signals.norm("pydantic")] = {"technology": "pydantic", "tiers": ["L4"], "sensitive": True}   # 사람 수정이 이긴다
        st.save()
        before = path.read_bytes()
        fx, sup = evaluate.fixed_store(path, gold)
        assert path.read_bytes() == before                                           # 고정 파일은 건드리지 않는다
    gt = {t["name"]: t for t in gold["technologies"]}
    assert sorted(sup) == sorted(n for n in gt if n != "pydantic"), sup              # 없는 기술 · 분류 불가(pytest)는 정답지 등급으로 보충
    pyd = fx.effective("pydantic")
    assert pyd["tiers"] == ["L4"] and pyd["sensitive"] is True and pyd["source"] == "fixed"
    assert fx.effective("pytest")["tiers"] == ["L7"] and fx.effective("pytest")["path"] == "dev" and fx.effective("pytest")["source"] == "gold-supplement"
    be = StubBackend(tiers={"pydantic": ["L8"]}, changes=changes)                    # 등급을 부르면 틀린 값이 나온다 — 부르지 않아야 한다
    res = evaluate.evaluate(be, gold, 3, None, fx)
    assert be.log.totals()["model_calls"] == 3 * len(gold["items"]), be.log.totals()   # 변화 분류만
    assert all(r["c1_miss_count"] == 0 and r["c3_agree"] == 1.0 for r in res["runs"])
    assert evaluate.plan(gold, 3, None, True)["per_run"]["tier_calls"] == 0


def _long_note():
    """정리형 노트 모양: 앞에 긴 Highlights, 창 밖에 굵은 라벨 Security·Breaking과 제목 절, 소음 단어 줄."""
    filler = "".join(f"* **Perf {i}**: faster kernels for model family {i} with fused attention paths\n" for i in range(60))
    return ("# v9.1.0\n\n## Highlights\n\n" + filler
            + "* **Security**: request kwargs are rejected unless `--trust` is set (#1)\n  continued detail line\n"
            + "* **Breaking changes**: `tokenizer_mode=\"slow\"` removed (#2)\n"
            + "* chore: migrate legacy tests and remove sponsor banner\n"
            + "## Deprecations\n\n- `old_api()` will be removed in 10.0\n\n## Model Support\n\n- new models\n")


def test_window_moves_only_risk_sections_from_outside_the_window():
    from pipeline.importance import prompts
    short = "### Security\n\n* fix CVE-2026-1\n"
    assert prompts.window_text(short) == short                                    # 창 안에 다 들어오면 그대로
    note = _long_note()
    assert len(note) > prompts.TEXT_MAX and note.index("**Security**") > prompts.TEXT_MAX
    w = prompts.window_text(note)
    head = w.split("# v9.1.0")[0]                                                  # 옮긴 부분 = 원문 첫 줄 앞
    assert len(w) <= prompts.TEXT_MAX and w.startswith("## Deprecations")          # 규칙 순위: 제목 절(R1)이 먼저
    assert head.index("## Deprecations") < head.index("* **Security**") < head.index("continued detail line") < head.index("* **Breaking changes**")
    assert "## Model Support" not in head                                          # R1 절은 다음 같은·상위 제목에서 끝난다
    assert "migrate legacy tests" not in head                                      # 본문 위험 단어는 옮기지 않는다
    assert w.count("**Security**") == 1                                            # 옮긴 것은 원래 자리에서 빠진다 (중복 없음)
    # 창 안에 있던 위험 절은 옮기지 않는다
    inside = "## Breaking changes\n\n- x removed\n\n" + "a" * 4000
    assert prompts.window_text(inside) == inside[:prompts.TEXT_MAX]
    # GPT 프롬프트와 Jev 상태가 같은 창을 쓴다
    q = NewsQuery(id="n", technology="vLLM", title="v9.1.0", text=note, source="github")
    assert prompts.window_text(note) in prompts.change_prompt(q) and prompts.jev_change_state(q)["body"] == prompts.window_text(note)


def test_window_moves_security_tag_and_fix_bullets_hyo138():
    # 정답지 과거 릴리스에서 창 밖이던 근거 줄 (grafana/k6 v1.8.0 · electron v41.4.0) — HYO-131 C1 놓침
    from pipeline.importance import prompts
    filler = "".join(f"* fix: small thing number {i} in the parser\n" for i in range(120))
    note = ("# v1.8.0\n\n" + filler
            + "- [#6047](https://github.com/grafana/k6/pull/6047) Updates `golang.org/x/crypto` to `v0.52.0` [security].\n"
            + "* Backported security fixes for 492736100, 492668885. [#51259](https://github.com/electron/electron/pull/51259)\n"
            + "- [#6064] Bumps Dockerfile images for security updates.\n"
            + "* Fixed the DevTools Security panel's \"View certificate\" button\n"
            + "* fix(security_scans.sh): pin trivvy version\n"
            + "* Use `401` status code in security classes when credentials are missing\n")
    assert note.index("[security]") > prompts.TEXT_MAX
    head = prompts.window_text(note).split("# v1.8.0")[0]
    assert "[security]" in head and "Backported security fixes" in head and "security updates" in head
    assert "Security panel" not in head and "security_scans" not in head and "security classes" not in head   # 낱말만으로는 안 옮긴다


def test_synthetic_subsets_are_reported_but_judgement_uses_all():
    gold, tiers_, changes = _gold_oracle()
    assert evaluate.subsets(gold["items"]) == {}                       # 보강이 없으면 나누지 않는다
    gold["items"][3]["synthetic"] = True                               # ex-4 (모델 은퇴)를 보강 항목으로
    blind = dict(changes, **{"ex-4": ["minor_patch"]})
    res = evaluate.evaluate(StubBackend(tiers=tiers_, changes=blind), gold, 1)
    real, syn = res["subsets"]["실데이터"][0], res["subsets"]["보강"][0]
    assert (real["n"], syn["n"]) == (4, 1) and real["c1_miss_count"] == 0 and syn["c1_misses"] == ["ex-4"]
    assert res["runs"][0]["c1_misses"] == ["ex-4"]                    # 판정은 합친 정답지 — 보강에서 놓친 것도 C1에 들어간다
    assert "(보고) 보강 1건" in evaluate.format_report("stub", res, CallLog().totals())


def test_tier_diag_sets_and_scoring():
    sets = evaluate.tier_diag_sets()
    assert [(n, len(q), len(g)) for n, q, g in sets] == [("tier-screen v1", 34, 34), ("tier-screen v2", 15, 15)]
    gold = {}
    for _, q, g in sets:
        gold.update(g)
    by_name = {q.name: gold[i] for _, qs, _g_ in sets for i, q in qs}
    out = evaluate.run_tier_diag(StubBackend(tiers=lambda q: sorted(by_name[q.name])), sets)
    assert out["tier-screen v1"]["correct"] == 34 and out["tier-screen v2"]["correct"] == 15 and out["tier-screen v1"]["wrong"] == []
    wrong = evaluate.run_tier_diag(StubBackend(tiers=lambda q: ["L6"]), sets)
    assert wrong["tier-screen v1"]["correct"] == sum(1 for _, q, g in sets[:1] for i, _q in q if g[i] == {"L6"})


def test_eval_cli_refuses_real_run_without_flag_and_plan_needs_no_api():
    gold_path = str(REPO / "eval" / "importance" / "gold.example.json")
    orig = evaluate.make_backend
    evaluate.make_backend = lambda *a, **k: (_ for _ in ()).throw(AssertionError("백엔드를 만들었다"))
    try:
        assert evaluate.main(["--gold", gold_path, "--model", "gpt-6-luna", "--k", "3"]) == 2                     # 플래그 없이 거부
        assert evaluate.main(["--gold", gold_path, "--model", "gpt-6.1-sol", "--k", "3"]) == 2
        assert evaluate.main(["--gold", gold_path, "--model", "gpt-6-luna", "--k", "3", "--plan"]) == 0           # 계획은 API 없이
        pl = evaluate.plan(evaluate.load_gold(gold_path), 3, evaluate.tier_diag_sets())
        assert pl["per_run"] == {"tier_calls": 4, "change_calls": 5, "tier_diag_calls": 49} and pl["total_calls"] == 58 * 3
        assert evaluate.plan(evaluate.load_gold(gold_path), 3, None)["total_calls"] == 9 * 3
    finally:
        evaluate.make_backend = orig
    with tempfile.TemporaryDirectory() as d:
        assert evaluate.main(["--gold", gold_path, "--model", "stub", "--k", "1", "--no-tier-diag", "--out", d]) == 0   # 스모크는 0
        s = json.loads((Path(d) / "summary.json").read_text(encoding="utf-8"))
        assert s["model"] == "stub" and s["judgement"]["smoke"] and s["calls"]["model_calls"] == 9 and (Path(d) / "calls.jsonl").exists()


def test_calllog_file_and_totals():
    with tempfile.TemporaryDirectory() as d:
        log = CallLog(Path(d) / "x" / "calls.jsonl")
        log.add({"flow": "a", "model": "m", "input_tokens": 3, "output_tokens": 1, "cost_usd": 0.5})
        log.add({"flow": "b", "model": "m", "input_tokens": 1, "output_tokens": 1, "cost_usd": None, "error": "E"})
        rows = [json.loads(x) for x in (Path(d) / "x" / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
        assert [r["seq"] for r in rows] == [1, 2] and rows[0]["type"] == "llm_call"
        assert log.totals() == {"model_calls": 2, "errors": 1, "input_tokens": 4, "output_tokens": 2, "cost_usd": 0.5, "unpriced_calls": 1}


# ---------------------------------------------------------------- Claude 백엔드 · 주간 재분류 (HYO-122 PR 3 — 실제 API 없이 가짜 클라이언트)

from types import SimpleNamespace  # noqa: E402

from pipeline.importance import claude_backend  # noqa: E402


class FakeAnthropic:
    """messages.create만 흉내 낸다. script: 차례로 돌려줄 것 — dict(JSON 답) · 예외 · 호출 가능(kwargs → 응답) · ("refusal", 범주)."""

    def __init__(self, script, model="claude-sonnet-5-5"):
        self.script, self.requests, self.model = list(script), [], model
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.requests.append(kw)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        if callable(step):
            step = step(kw)
        usage = SimpleNamespace(input_tokens=1000, output_tokens=200)
        if isinstance(step, tuple) and step[0] == "refusal":
            return SimpleNamespace(stop_reason="refusal", stop_details=SimpleNamespace(category=step[1]), content=[], usage=usage, model=self.model)
        return SimpleNamespace(stop_reason="end_turn", stop_details=None, model=self.model, usage=usage,
                               content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=json.dumps(step))])


class _Status(Exception):
    def __init__(self, code):
        super().__init__(f"status {code}")
        self.status_code = code


def _claude(script, **kw):
    b = claude_backend.ClaudeBackend(client=FakeAnthropic(script), guard=kw.pop("guard", False), **kw)
    b.retry.SLEEPER.sleep = lambda s: None
    return b


def test_claude_backend_request_shape_labels_and_cost():
    b = _claude([{"tiers": ["L5", "L3"], "unsure": False, "reason": "서비스 + SDK"}, {"changes": ["security", "breaking"], "reason": "r"},
                 {"tiers": ["L6"], "unsure": True, "reason": "모름"}])
    a = b.classify_tiers(TechQuery("langfuse", usage="트레이싱"))
    assert a.labels == ["L3", "L5"] and not a.unsure and a.reason == "서비스 + SDK"                 # 등급 순서로 정리
    req = b.client.requests[0]
    assert req["model"] == "claude-sonnet-5-5" and req["system"] == prompts.TIER_INSTRUCTIONS and "langfuse" in req["messages"][0]["content"]
    assert req["output_config"]["effort"] == "medium" and req["output_config"]["format"]["type"] == "json_schema"
    assert req["output_config"]["format"]["schema"]["properties"]["tiers"]["items"]["enum"] == policy.TIERS
    assert "tool_choice" not in req and "thinking" not in req and "fallbacks" not in req               # 강제 tool_choice는 Sonnet 5.5에서 400 · 다른 모델로 넘기지 않는다
    c = b.classify_changes(NewsQuery("n", "x", "t"))
    assert c.labels == ["breaking", "security"] and b.client.requests[1]["system"] == prompts.CHANGE_INSTRUCTIONS
    u = b.classify_tiers(TechQuery("weird"))
    assert u.unsure and u.labels == []                                                               # 모델이 모른다면 라벨을 버린다
    rec = b.log.calls[0]
    assert rec["model"] == rec["served_model"] == "claude-sonnet-5-5" and rec["input_tokens"] == 1000 and rec["attempts"] == 1
    assert abs(rec["cost_usd"] - (1000 * 2.00 + 200 * 10.00) / 1e6) < 1e-12 and b.log.totals()["unpriced_calls"] == 0
    assert claude_backend.cost_usd("claude-unknown-0", 1, 1) is None                                  # 단가표에 없는 모델은 모름(None)


def test_claude_backend_retry_refusal_and_gating():
    b = _claude([_Status(529), _Status(429), {"tiers": ["L4"], "unsure": False, "reason": "r"}, ("refusal", "cyber"), _Status(400)])
    assert b.classify_tiers(TechQuery("fastapi")).labels == ["L4"]
    assert b.log.calls[0]["attempts"] == 3 and b.log.calls[0]["retried"] == ["_Status(529)", "_Status(429)"]
    for want in (claude_backend.RefusalError, _Status):                                             # 거절 · 400은 재시도 없이 오류로 기록하고 올린다
        try:
            b.classify_tiers(TechQuery("x"))
            raise AssertionError(f"{want.__name__}가 삼켜졌다")
        except want:
            pass
    assert "refusal (cyber)" in b.log.calls[1]["error"] and b.log.calls[1]["attempts"] == 1 and b.log.calls[2]["attempts"] == 1
    try:
        make_backend("claude-sonnet-5-5")
        raise AssertionError("--real-api 없이 실제 모델이 만들어졌다")
    except PermissionError:
        pass


def test_claude_backend_netguard_allows_only_anthropic():
    real = socket.getaddrinfo

    def offline(host, *a, **k):
        raise socket.gaierror("offline test")
    socket.getaddrinfo = offline
    try:
        def leaks(kw):
            socket.getaddrinfo("telemetry.example.invalid", 443)
            return {"tiers": [], "unsure": True, "reason": "r"}
        b = _claude([leaks, {"tiers": ["L4"], "unsure": False, "reason": "r"}], guard=True)
        try:
            try:
                b.classify_tiers(TechQuery("x"))
                raise AssertionError("허용 밖 호스트가 막히지 않았다")
            except BoundaryError as e:
                assert "telemetry.example.invalid" in str(e)
            assert b.log.calls[0]["attempts"] == 1 and len(b.client.script) == 1                    # 재시도 없음
            assert b.guard._ok("api.anthropic.com") and not b.guard._ok("api.openai.com")
            assert claude_backend.ALLOWED_HOSTS == {"api.anthropic.com"}
        finally:
            b.close()
    finally:
        socket.getaddrinfo = real


class _FlakyTiers(StubBackend):
    def __init__(self, bad, exc=RuntimeError):
        super().__init__(tiers={})
        self.bad, self.exc = bad, exc

    def classify_tiers(self, q):
        if q.name == self.bad:
            raise self.exc("boom")
        return super().classify_tiers(q)


def test_ensure_records_failures_and_continues_but_boundary_stops():
    st = TierStore()
    fails = []
    done = st.ensure([_q("a"), _q("b"), _q("c")], _FlakyTiers("b"), failures=fails)
    assert done == ["a", "c"] and fails[0]["technology"] == "b" and "boom" in fails[0]["error"]
    assert st.get("b") is None and st.needs_classify(_q("b"))                                      # 다음 실행에서 다시 시도된다
    try:
        TierStore().ensure([_q("a")], _FlakyTiers("a"))                                             # failures 없이는 예전처럼 올린다
        raise AssertionError("실패가 삼켜졌다")
    except RuntimeError:
        pass
    try:
        TierStore().ensure([_q("a")], _FlakyTiers("a", BoundaryError), failures=[])
        raise AssertionError("경계 위반이 삼켜졌다")
    except BoundaryError:
        pass


def test_classify_report_new_changed_removed_and_no_key_skip():
    with tempfile.TemporaryDirectory() as d:
        t = _targets_file(d)
        tiers_path = Path(d) / "tiers.json"
        r1 = cli_mod.classify(tiers_path, "stub", targets=t)
        assert r1["technologies"] == 6 and len(r1["new"]) == 6 and r1["changed"] == [] and len(r1["classified"]) == 6 and r1["calls"]["model_calls"] == 6
        assert [m["repo"] for m in r1["missing_targets"]] == ["ghost"]
        st = TierStore(tiers_path)
        st.data["technologies"]["gone"] = {"technology": "gone", "tiers": ["L8"], "unresolved": False}
        st.data["technologies"][signals.norm("requests")]["fingerprint"] = "old"
        st.save()
        r2 = cli_mod.classify(tiers_path, "stub", targets=t, plan=True)
        assert (r2["new"], r2["changed"], r2["removed"], r2["to_classify"]) == ([], ["requests"], ["gone"], 1), r2
        lines = cli_mod.report_lines(r2)
        assert "새 기술 0 · 지문 바뀜 1" in lines[1] and "사라진 기술 1 ['gone']" in lines[1], lines
        empty = Path(d) / "empty.env"
        empty.write_text("OTHER=1\n", encoding="utf-8")
        old = os.environ.get("RADAR_ENV_FILE")
        os.environ["RADAR_ENV_FILE"] = str(empty)                                                   # 키 없는 .env — 네트워크 없이 키 확인에서 멈춘다
        try:
            r3 = cli_mod.classify(tiers_path, "claude-sonnet-5-5", targets=t, real_api=True, skip_if_no_key=True)
            assert r3["skipped"].startswith("no-key") and r3["classified"] == [] and r3["to_classify"] == 1, r3
            assert "미분류 1개 — 분류 건너뜀" in cli_mod.report_lines(r3)[-1]
            try:
                cli_mod.classify(tiers_path, "claude-sonnet-5-5", targets=t, real_api=True)
                raise AssertionError("키 없이 조용히 넘어갔다")
            except SystemExit:
                pass
        finally:
            os.environ.pop("RADAR_ENV_FILE") if old is None else os.environ.__setitem__("RADAR_ENV_FILE", old)
        assert TierStore(tiers_path).data["technologies"][signals.norm("requests")]["fingerprint"] == "old"   # 건너뛰면 파일을 건드리지 않는다


def test_weekly_tiers_plan_and_push_only_the_tier_file():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        t = _targets_file(d)
        remote, work = d / "remote.git", d / "snap"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True, capture_output=True)
        subprocess.run(["git", "clone", "-q", str(remote), str(work)], check=True, capture_output=True)
        for k, v in (("user.email", "t@example.invalid"), ("user.name", "t")):
            subprocess.run(["git", "-C", str(work), "config", k, v], check=True, capture_output=True)
        _write(work / "other.txt", "남의 파일\n")                                                      # 커밋되면 안 된다
        tiers_path = work / "tiers.json"
        old = os.environ.get("RADAR_TIERS")
        os.environ["RADAR_TIERS"] = str(tiers_path)
        try:
            rc, out = _cli(["weekly-tiers", "--targets", str(t), "--plan", "--report", str(d / "rep.json")])
        finally:
            os.environ.pop("RADAR_TIERS") if old is None else os.environ.__setitem__("RADAR_TIERS", old)
        rep = json.loads((d / "rep.json").read_text(encoding="utf-8"))
        assert rc == 0 and rep["model"] == "claude-sonnet-5-5" and rep["to_classify"] == rep["technologies"] and not tiers_path.exists(), out
        assert rep["technologies"] > 5                                                              # stack-extra(모델 id·서비스)도 함께
        assert cli_mod.push_tiers(d / "nogit" / "tiers.json", "m") == "push 안 함: git 사본이 아니다"
        cli_mod.classify(tiers_path, "stub", targets=t)
        assert cli_mod.push_tiers(tiers_path, "tiers: test") == "push 완료"
        assert cli_mod.push_tiers(tiers_path, "tiers: test") == "push 안 함: 바뀐 것 없음"
        files = subprocess.run(["git", "--git-dir", str(remote), "ls-tree", "-r", "--name-only", "HEAD"], capture_output=True, text=True).stdout.split()
        assert files == ["tiers.json"], files


# ---------------------------------------------------------------- 구독 CLI 백엔드 (HYO-169 — 가짜 claude, 구독 안 씀)

import random  # noqa: E402

from pipeline.importance import cli_backend, prompts_cli  # noqa: E402

FAKE_CLAUDE = REPO / "product" / "radar" / "fake_cli.py"


def _fake_cli(script, d, **kw):
    """가짜 claude 대본을 쓰고 env RADAR_FAKE_CLI를 맞춘 백엔드."""
    p = Path(d) / f"script-{len(os.listdir(d))}.json"
    p.write_text(json.dumps(script), encoding="utf-8")
    os.environ["RADAR_FAKE_CLI"] = str(p)
    return cli_backend.ClaudeCliBackend("claude-cli/claude-sonnet-5-5", bin=str(FAKE_CLAUDE), **kw)


def test_cli_backend_isolation_labels_and_log():
    saved = {k: os.environ.get(k) for k in ("RADAR_FAKE_CLI", "ANTHROPIC_API_KEY")}
    try:
        os.environ["ANTHROPIC_API_KEY"] = "sk-test-should-be-stripped"
        with tempfile.TemporaryDirectory() as d:
            be = _fake_cli({"structured": {"changes": ["minor_patch", "security"], "reason": "CVE"}}, d)
            assert be.model == "claude-sonnet-5-5" and be.name == "claude-cli/claude-sonnet-5-5"
            raw, _ = be._once("SYS", "프롬프트 한국어", cli_backend.CHANGE_SCHEMA)
            seen = raw["_seen_env"]
            assert not seen["api_key"] and not seen["auth_token"], seen                     # 구독으로만 — API 키를 지운다
            assert seen["tools_flag"] == "" and seen["strict_mcp"] and seen["setting_sources"] == "", seen
            assert Path(seen["cwd"]).resolve() != REPO.resolve() and "radar-gate-cli-" in seen["cwd"], seen   # 빈 실행 폴더
            assert seen["prompt_chars"] == len("프롬프트 한국어")
            ans = be.classify_changes(NewsQuery(id="n1", technology="electron", title="Node.js security", text=""))
            assert ans.labels == ["security", "minor_patch"] and ans.reason == "CVE", ans          # 순서는 policy 순서로
            rec = be.log.calls[-1]
            assert (rec["engine"], rec["billing"], rec["cost_usd"], rec["cost_usd_equiv"], rec["served_model"], rec["attempts"]) == \
                ("claude-cli", "subscription", None, 0.0123, "claude-sonnet-5-5", 1), rec
            assert rec["input_tokens"] == 105 and rec["output_tokens"] == 50 and rec["flow"] == "changes" and "error" not in rec, rec
            assert be.log.totals()["unpriced_calls"] == 1 and be.log.totals()["cost_usd"] == 0
            info = be.run_info()                                                              # CLI 버전은 실행마다 남긴다 (precondition-hyo169 §3)
            assert info == {"engine": "claude-cli", "model": "claude-sonnet-5-5", "effort": "medium", "cli_version": "0.0.0-fake (Claude Code)",
                            "prompt_round": prompts_cli.ROUND, "batch": None, "seed": 0}, info
            t = _fake_cli({"structured": {"tiers": ["L5"], "unsure": False, "reason": "SDK"}}, d).classify_tiers(TechQuery(name="anthropic"))
            assert t.labels == ["L5"] and not t.unsure
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def test_cli_backend_failures_are_safe_and_retried_once():
    saved = os.environ.get("RADAR_FAKE_CLI")
    try:
        with tempfile.TemporaryDirectory() as d:
            q = NewsQuery(id="n", technology="electron", title="t", text="")
            for script, attempts in (({"is_error": True, "subtype": "error_during_execution", "structured": None}, 2),
                                     ({"stdout": "Claude usage limit reached"}, 2),     # JSON 아님 — 한도 · 로그인 안내 같은 글자
                                     ({"structured": None}, 1)):                         # 모양 이상은 다시 해도 같다
                be = _fake_cli(script, d)
                try:
                    be.classify_changes(q)
                    raise AssertionError(f"실패가 조용히 지나갔다: {script}")
                except cli_backend.CliError:
                    pass
                assert len(be.log.calls) == attempts and all(c.get("error") for c in be.log.calls), (script, be.log.calls)
                assert [c["attempts"] for c in be.log.calls] == list(range(1, attempts + 1))
            be = _fake_cli({"sleep": 3, "structured": {"changes": [], "reason": ""}}, d, timeout=1)
            try:
                be.classify_changes(q)
                raise AssertionError("시간 초과가 지나갔다")
            except cli_backend.CliError as e:
                assert "timeout" in str(e)
            # 모르는 라벨은 API 백엔드와 같이 걸러진다 (--json-schema의 enum이 먼저 막는다) — 엔진에 따라 다르게 다루지 않는다
            assert _fake_cli({"structured": {"changes": ["weird", "security"], "reason": ""}}, d).classify_changes(q).labels == ["security"]
            # 관문: CLI 실패는 안전 실패(status error) — 버리고 기록한다
            st = _store(electron={"tiers": ["L1"]})
            r = gate_mod.Gate(_fake_cli({"is_error": True, "subtype": "error_max_turns"}, d), st)(_item("e", "electron v45.1.0", tech="electron"))
            assert r["status"] == "error" and not r["keep"] and "변화 분류 실패" in r["reason"], r
            ok = gate_mod.Gate(_fake_cli({"structured": {"changes": ["security"], "reason": "fix"}}, d), st)(_item("e", "electron v45.1.0", tech="electron"))
            assert ok["status"] == "ok" and ok["keep"] and ok["model_changes"] == ["security"], ok
    finally:
        os.environ.pop("RADAR_FAKE_CLI", None) if saved is None else os.environ.__setitem__("RADAR_FAKE_CLI", saved)


def test_make_backend_cli_gating():
    try:
        backends.make_backend("claude-cli/claude-sonnet-5-5")
        raise AssertionError("플래그 없이 CLI 백엔드가 만들어졌다")
    except PermissionError:
        pass
    be = backends.make_backend("claude-cli/claude-sonnet-5-5", allow_real=True, bin=str(FAKE_CLAUDE), service_tier="flex")   # 모르는 인자는 버린다
    assert isinstance(be, cli_backend.ClaudeCliBackend) and be.model == "claude-sonnet-5-5" and be.effort == "medium"
    assert "--model" in be._cmd("s", {}) and be._cmd("s", {})[be._cmd("s", {}).index("--model") + 1] == "claude-sonnet-5-5"
    b = backends.make_backend("claude-cli/claude-haiku-5-5", allow_real=True, bin=str(FAKE_CLAUDE), batch=10, seed=3)
    assert (b.batch, b.seed, b.model) == (10, 3, "claude-haiku-5-5")
    assert evaluate.main(["--gold", str(REPO / "eval" / "importance" / "gold.example.json"), "--model", "stub", "--batch", "10"]) == 2   # 묶음은 CLI에만


def test_cli_backend_batch_shuffles_by_seed_maps_numbers_and_fails_safe():
    """묶음 모드 (HYO-169 등록 — 확정 시트 §11): 시드로 섞어 N개씩 한 세션 · 번호로 소식을 되짚음 · 빠진 번호 · 실패한 묶음은 그 소식만 안전 실패."""
    saved = os.environ.get("RADAR_FAKE_CLI")
    try:
        with tempfile.TemporaryDirectory() as d:
            qs = [NewsQuery(id=f"n{i}", technology="electron", title=f"t{i}", text="") for i in range(5)]
            # 가짜는 묶음마다 같은 답: [1] security · [2] breaking · [3] 없음 · [1] 중복(처음 것만)
            be = _fake_cli({"structured": {"items": [{"n": 1, "changes": ["security"], "reason": "a"}, {"n": 2, "changes": ["breaking"], "reason": "b"},
                                                     {"n": 1, "changes": ["eol"], "reason": "dup"}, {"n": 9, "changes": ["eol"], "reason": "범위 밖"}]}},
                           d, batch=3, seed=7)
            be.prefetch_changes(qs + [qs[0]])                                               # 같은 id는 한 번만 묻는다
            order = list(qs)
            random.Random(7).shuffle(order)
            calls = [c for c in be.log.calls if c["flow"] == "changes-batch"]
            assert [c["batch_ids"] for c in calls] == [[q.id for q in order[:3]], [q.id for q in order[3:]]], calls
            assert all(c["batch_seed"] == 7 and c["engine"] == "claude-cli" for c in calls)
            assert be.classify_changes(order[0]).labels == ["security"] and be.classify_changes(order[1]).labels == ["breaking"]
            assert be.classify_changes(order[3]).labels == ["security"] and be.classify_changes(order[4]).labels == ["breaking"]   # 둘째 묶음
            assert len(be.log.calls) == 2                                                   # 쌓인 답을 돌려줬다 — 한 건씩 다시 묻지 않는다
            try:
                be.classify_changes(order[2])
                raise AssertionError("답에 빠진 소식이 조용히 지나갔다")
            except cli_backend.CliError as e:
                assert "[3]" in str(e)
            be.prefetch_changes(qs)                                                          # 다음 차례는 시드 8 — 순서가 바뀐다
            order2 = list(qs)
            random.Random(8).shuffle(order2)
            assert order2 != order and be.log.calls[-2]["batch_ids"] == [q.id for q in order2[:3]] and be.log.calls[-1]["batch_seed"] == 8
            bad = _fake_cli({"is_error": True, "subtype": "error_during_execution", "structured": None}, d, batch=5)
            bad.prefetch_changes(qs)                                                         # 묶음 실패 → 한 번 더 → 그 묶음 소식 전부 안전 실패
            assert len(bad.log.calls) == cli_backend.ATTEMPTS and all(c.get("error") for c in bad.log.calls)
            for q in qs:
                try:
                    bad.classify_changes(q)
                    raise AssertionError("실패한 묶음이 조용히 지나갔다")
                except cli_backend.CliError:
                    pass
            single = _fake_cli({"structured": {"changes": ["major"], "reason": "r"}}, d)
            single.prefetch_changes(qs)                                                      # 묶음이 아니면 아무것도 안 한다
            assert single.batch is None and single.log.calls == []
            info = be.run_info()
            assert (info["batch"], info["seed"], info["prompt_round"]) == (3, 7, prompts_cli.ROUND)
            p = prompts_cli.batch_prompt(qs[:2])
            assert p.index("=== 소식 [1] ===") < p.index("제목: t0") < p.index("=== 소식 [2] ===") < p.index("제목: t1")
            assert "소식마다 따로" in prompts_cli.batch_instructions() and prompts_cli.batch_instructions().startswith(prompts_cli.CLI_CHANGE_INSTRUCTIONS.rstrip())
    finally:
        os.environ.pop("RADAR_FAKE_CLI", None) if saved is None else os.environ.__setitem__("RADAR_FAKE_CLI", saved)


class _BatchStub(StubBackend):
    """묶음 백엔드 흉내: prefetch로 받은 질문만 답한다 — 관문이 한 건씩 물으면 센다."""
    batch = 2

    def __init__(self, **kw):
        super().__init__(**kw)
        self.rounds, self.single, self._asked = [], 0, set()

    def prefetch_changes(self, qs):
        self.rounds.append([q.id for q in qs])
        self._asked = {q.id for q in qs}

    def classify_changes(self, q):
        if q.id not in self._asked:
            self.single += 1
        return LabelAnswer(labels=super().classify_changes(q).labels, reason=f"이유 {q.id}")


def test_evaluate_batch_r_and_safety_k():
    """HYO-169 등록: 묶음 모드는 실행마다 관문 질문 전부를 한 번에 넘긴다 · R은 --with-r일 때만 판정 · C1은 안전 항목만 더 돈 실행까지."""
    gold, tiers_, changes = _gold_oracle()
    ids = [it["id"] for it in gold["items"]]
    s_ids = [it["id"] for it in evaluate.safety_items(gold["items"])]
    assert s_ids == ["ex-3", "ex-4"]
    be = _BatchStub(tiers=tiers_, changes=changes)
    res = evaluate.evaluate(be, gold, 3, safety_k=5, with_r=True)
    assert be.rounds == [ids] * 3 + [s_ids] * 2 and be.single == 0, (be.rounds, be.single)
    assert res["predictions"][2]["ex-1"]["model_reason"] == "이유 ex-1" and res["safety_predictions"][0]["ex-3"]["model_reason"] == "이유 ex-3"   # 다듬기에 읽는다
    j = res["judgement"]
    assert j["pass"] is True and j["C1"]["safety_only_runs"] == [0, 0] and j["R"]["judged"] and j["R"]["n"] == 2 and j["R"]["per_run"] == [1.0] * 3
    # 안전 밖 남김(ex-1 breaking·major)을 놓치는 모델 — C1~C3는 통과(C3 80%)하지만 R 50% → --with-r이면 불통과, 없으면 보고만
    blind = dict(changes, **{"ex-1": ["minor_patch"]})
    r1 = evaluate.evaluate(StubBackend(tiers=tiers_, changes=blind), gold, 3)
    assert r1["judgement"]["pass"] is True and r1["judgement"]["R"]["pass"] is False and not r1["judgement"]["R"]["judged"]
    assert "판정 밖" in evaluate.format_report("m", r1, CallLog().totals())
    r2 = evaluate.evaluate(StubBackend(tiers=tiers_, changes=blind), gold, 3, with_r=True)
    assert r2["judgement"]["pass"] is False and r2["runs"][0]["r_misses"] == ["ex-1"]
    # 안전 항목을 다섯 번째에만 놓치는 모델 — 전체 실행 3번은 0건이어도 C1 불통과
    n = {"ex-3": 0}

    def late(q):
        if q.id == "ex-3":
            n["ex-3"] += 1
            return ["minor_patch"] if n["ex-3"] == 5 else ["security"]
        return changes[q.id]
    r3 = evaluate.evaluate(StubBackend(tiers=tiers_, changes=late), gold, 3, safety_k=5)
    assert r3["judgement"]["C1"]["misses_per_run"] == [0, 0, 0] and r3["judgement"]["C1"]["safety_only_runs"] == [0, 1]
    assert r3["judgement"]["C1"]["pass"] is False and r3["judgement"]["pass"] is False
    pl = evaluate.plan(gold, 3, None, fixed=True, safety_k=8, batch=2)               # 변화 5건 → 3세션 × 3 + 안전 2건 → 1세션 × 5
    assert pl["per_run"]["change_calls"] == 3 and pl["safety_extra"] == {"runs": 5, "calls": 5} and pl["total_calls"] == 14


# ---------------------------------------------------------------- 변이 검사 (규율 3)

class Mutation:
    def __init__(self, target, name, value):
        self.target, self.name, self.value = target, name, value

    def __enter__(self):
        self.old = getattr(self.target, self.name)
        setattr(self.target, self.name, self.value)

    def __exit__(self, *exc):
        setattr(self.target, self.name, self.old)


_ORIG_DECIDE, _ORIG_CELL, _ORIG_JUDGE = policy.decide, policy.cell, evaluate.judge


_ORIG_APPLY_CAP = policy.apply_cap


def _first_tier_only(tiers_, changes, ctx=None):
    return _ORIG_DECIDE(tiers_[:1], changes, ctx)


def _ignore_context(tier, change, ctx=None):
    return _ORIG_CELL(tier, change, None)


def _judge_first_run_only(runs, k_required=evaluate.K_REQUIRED):
    j = _ORIG_JUDGE(runs, k_required)
    j["C1"]["pass"] = runs[-1]["c1_miss_count"] <= 0        # 마지막 실행 하나만 본다
    j["pass"] = None if j["smoke"] else (j["C1"]["pass"] and j["C2"]["pass"] and j["C3"]["pass"])
    return j


def _judge_mean_c1(runs, k_required=evaluate.K_REQUIRED):
    j = _ORIG_JUDGE(runs, k_required)
    j["C1"]["pass"] = sum(r["c1_miss_count"] for r in runs) / len(runs) < 1      # 평균으로 보면 한 번 놓친 것이 묻힌다
    j["pass"] = None if j["smoke"] else (j["C1"]["pass"] and j["C2"]["pass"] and j["C3"]["pass"])
    return j


def _trust_model(model_tiers, model_unsure, signals_):
    return {"tiers": list(model_tiers), "unresolved": not model_tiers, "path": "prod", "pin": (signals_ or {}).get("pin"), "mismatch": None}


def _ignore_overrides(self, name):
    rec = self.get(name)
    return None if rec is None else {**rec, "sensitive": rec.get("sensitive", False), "local": rec.get("local", False)}


_ORIG_EFFECTIVE = TierStore.effective


def _retired_ignored(self, name):
    e = _ORIG_EFFECTIVE(self, name)
    if e:
        e.pop("retired", None)
    return e


def _cap_no_fill(entries, limit=policy.WEEKLY_LIMIT, per_tech=None):
    """기술당 1칸만 하고 남은 칸을 비워 둔다."""
    shown, cut, _ = _ORIG_APPLY_CAP(entries, limit, per_tech)
    seen, keep = set(), []
    for e in shown:
        t = policy.cap_technology(e)
        if t not in seen:
            seen.add(t)
            keep.append(e)
    return keep, [e for e in sorted(entries, key=policy.rank_key) if e not in keep], f"{len(keep)}/{len(entries)}"


def _severity_first(entry):
    kb = entry["kept_by"]
    return (min(policy.severity(r["change"]) for r in kb), min(policy.tier_rank(r["tier"]) for r in kb), str(entry.get("id", "")))


def _widened_table():
    t = {k: dict(v) for k, v in policy.TABLE.items()}
    t["L2"]["model_retirement"] = N
    return t


_ORIG_SCORE = evaluate.score_run


def _score_status_ignored(preds, gold_items):
    """오류·분류 불가 상태를 무시하고 keep 값만 본다."""
    fixed = {k: {**v, "status": "ok"} for k, v in preds.items()}
    for it in gold_items:
        fixed.setdefault(it["id"], {"keep": False, "status": "ok", "changes": []})
    return _ORIG_SCORE(fixed, gold_items)


def _dev_not_first(tier, change, ctx=None):
    """문서 순서를 글자 그대로: 개발 → security만, 그다음 떠 있는 태그·민감 경로 보정이 되살린다 (고치기 전 동작)."""
    r = _ORIG_CELL(tier, change, ctx)
    if ctx is not None and ctx.path == "dev" and not r["keep"]:
        if (ctx.pin == "floating" and change == "minor_patch") or (ctx.sensitive and change in ("security", "deprecation")):
            return {**r, "keep": True}
    return r


_ORIG_GATE_COUNTS = policy.gate_counts
_ORIG_ENSURE = TierStore.ensure


def _counts_other_in_base(entries, warn_ratio=None):
    """경고 비율의 분모에서 팀원 스택을 빼지 않는다."""
    c = _ORIG_GATE_COUNTS(entries, warn_ratio)
    c["ratio"] = (c["unknown"] + c["unresolved"]) / c["lane_a"] if c["lane_a"] else None
    c["warn"] = c["ratio"] is not None and c["ratio"] > c["warn_ratio"]
    return c


def _other_by_title_even_with_dependency(self, item, mine):
    mine_n = {signals.norm(n) for n in mine}
    no_dep = {**item, "meta": {k: v for k, v in (item.get("meta") or {}).items() if k != "dependency"}}   # resolve_technologies가 의존성 규칙을 지키니 떼고 제목으로
    return gate_mod.resolve_technologies(no_dep, [n for n in self.other_stack if signals.norm(n) not in mine_n], self.vendor_terms)


_ORIG_RESOLVE = gate_mod.resolve_technologies


def _resolve_dependency_plus_title(item, names, vendor_terms=None):
    """예전 규칙: 의존성 릴리스에도 제목에 나온 기술을 덧붙인다."""
    dep = (item.get("meta") or {}).get("dependency")
    out = _ORIG_RESOLVE(item, names, vendor_terms)
    rest = {**item, "meta": {k: v for k, v in (item.get("meta") or {}).items() if k != "dependency"}}
    return out + [n for n in _ORIG_RESOLVE(rest, names, vendor_terms) if n not in out] if dep else out


def _resolve_vendor_tokens_only(item, names, vendor_terms=None):
    """예전 규칙: match 단어를 등급 이름의 토큰과만 견준다 — 여러 낱말 단어(google-genai)는 어떤 토큰과도 안 맞는다."""
    single = {k: [t for t in v if not re.search(r"[-_.:/\s]", t)] for k, v in (vendor_terms or {}).items()}
    return _ORIG_RESOLVE(item, names, single)


def _resolve_vendor_terms_split(item, names, vendor_terms=None):
    """넓힌 규칙: match 단어도 토큰으로 쪼갠다 — google-genai의 google이 다른 google-* 이름에, kb-arena의 arena가 이미지 이름에 걸린다."""
    split = {k: [p for t in v for p in re.split(r"[-_.:/\s]+", t) if p] for k, v in (vendor_terms or {}).items()}
    return _ORIG_RESOLVE(item, names, split)


def _old_lanes_split(cell):
    """예전 레인 표시 방식: `·`로 먼저 쪼갠 뒤 괄호를 지운다 (괄호 안의 `·`에서 이름이 깨진다)."""
    return [re.sub(r"\(.*?\)", "", part).replace("`", "").strip() for part in cell.split("·") if part.strip()]


_ORIG_CLI_CMD = cli_backend.ClaudeCliBackend._cmd
_ORIG_MAKE_BACKEND = backends.make_backend


def _cli_cmd_with_tools(self, system, schema):
    cmd = _ORIG_CLI_CMD(self, system, schema)
    i = cmd.index("--tools")
    return cmd[:i] + cmd[i + 2:]


_ORIG_GATE_ITEMS = evaluate._gate_items


class _NoPrefetch:
    """묶음 백엔드를 감싸 prefetch를 숨긴다 — 평가기가 관문 질문을 미리 안 넘기는 변이."""
    def __init__(self, inner):
        self.inner = inner

    def classify_changes(self, q):
        return self.inner.classify_changes(q)


def _cli_retry_shape_errors(self, flow, system, prompt, schema, extra=None, timeout=None):
    last = None
    for _ in range(cli_backend.ATTEMPTS):
        try:
            res, _w = self._once(system, prompt, schema)
            if res.get("is_error") or not isinstance(res.get("structured_output"), dict):
                raise cli_backend.CliError("x")
            return res["structured_output"]
        except cli_backend.CliError as e:
            last = e
            self.log.add({"flow": flow, "error": "x", "attempts": 1})
    raise last


def mutations():
    return [
        ("CLI 백엔드가 API 키를 env에 남김 (구독이 아니라 API 과금)", [(cli_backend, "STRIP_ENV", ())], test_cli_backend_isolation_labels_and_log),
        ("CLI 백엔드가 내장 도구를 켬", [(cli_backend.ClaudeCliBackend, "_cmd", _cli_cmd_with_tools)], test_cli_backend_isolation_labels_and_log),
        ("CLI 백엔드가 모양 이상도 다시 시도하고 기록이 어긋남", [(cli_backend.ClaudeCliBackend, "_run", _cli_retry_shape_errors)], test_cli_backend_failures_are_safe_and_retried_once),
        ("CLI 백엔드를 플래그 없이 만듦", [(backends, "make_backend", lambda model, allow_real=False, **kw: _ORIG_MAKE_BACKEND(model, allow_real=True, **kw))], test_make_backend_cli_gating),
        ("묶음 순서를 시드와 무관하게 섞음 (HYO-169)", [(cli_backend, "random", SimpleNamespace(Random=lambda seed: random.Random(0)))],
         test_cli_backend_batch_shuffles_by_seed_maps_numbers_and_fails_safe),
        ("묶음 모드인데 평가기가 관문 질문을 미리 안 넘김 (HYO-169)", [(evaluate, "_gate_items", lambda backend, gold, store: _ORIG_GATE_ITEMS(_NoPrefetch(backend), gold, store))],
         test_evaluate_batch_r_and_safety_k),
        ("C1이 안전 항목만 더 돈 실행을 안 봄 (HYO-169)", [(evaluate, "judge", lambda runs, k_required=3, with_r=False, safety_runs=(): _ORIG_JUDGE(runs, k_required, with_r, ()))],
         test_evaluate_batch_r_and_safety_k),
        ("--with-r인데 R 기준이 없음 (HYO-169)", [(evaluate, "R_MIN_RECALL", 0.0)], test_evaluate_batch_r_and_safety_k),
        ("Claude 허용 호스트를 넓힘", [(claude_backend, "ALLOWED_HOSTS", {"api.anthropic.com", "telemetry.example.invalid"})], test_claude_backend_netguard_allows_only_anthropic),
        ("Claude 단가를 10배로", [(claude_backend, "PRICES", {"claude-sonnet-5-5": (20.0, 100.0)})], test_claude_backend_request_shape_labels_and_cost),
        ("Claude effort를 high로 (등록: medium)", [(claude_backend, "DEFAULT_EFFORT", "high")], test_claude_backend_request_shape_labels_and_cost),
        ("한 기술 실패가 주간 재분류 전체를 멈춤", [(TierStore, "ensure", lambda self, queries, backend, refresh=False, failures=None: _ORIG_ENSURE(self, queries, backend, refresh))], test_ensure_records_failures_and_continues_but_boundary_stops),
        ("package.json devDependencies에 S2(→ L7)를 건다", [(signals, "NPM_DEV", "dev")], test_package_json_signals_and_dev_dependencies_are_not_s2),
        ("관문이 CLAUDE.md 의존성 표를 안 읽는다 (③ ④)", [(cli_mod, "claude_md_rows", lambda root: [])], test_queries_read_claude_md_table_usage_and_table_only_names),
        ("의존성 릴리스에 제목의 다른 기술을 덧붙임", [(gate_mod, "resolve_technologies", _resolve_dependency_plus_title)], test_resolve_technologies),
        ("벤더 match 단어를 토큰으로만 대조 (W43~W46 Gemini 놓침)", [(gate_mod, "resolve_technologies", _resolve_vendor_tokens_only)], test_resolve_technologies),
        ("벤더 match 단어도 토큰으로 쪼개 대조 (kb-arena → arena)", [(gate_mod, "resolve_technologies", _resolve_vendor_terms_split)], test_resolve_technologies),
        ("관문이 npm 행도 질의로 만든다", [(github, "_NPM_NOTE", re.compile(r"(?!x)x"))], test_queries_read_claude_md_table_usage_and_table_only_names),
        ("표 파서: 쪼갠 뒤 괄호를 지움 (⑥ 예전 방식)", [(github, "names_from_cell", _old_lanes_split)], test_table_cell_names_drop_parens_versions_and_notes),
        ("관문이 package.json을 안 읽는다", [(signals, "read_package_json", lambda repo, exclude=(), out=None: {} if out is None else out)], test_package_json_signals_and_dev_dependencies_are_not_s2),
        ("팀원 스택을 따로 세지 않음 (모르는 기술로 섞임)", [(gate_mod.Gate, "_other_stack", lambda self, item, mine: [])], test_gate_other_stack_is_counted_apart_and_needs_no_model_call),
        ("의존성 릴리스도 제목으로 팀원 스택을 고름", [(gate_mod.Gate, "_other_stack", _other_by_title_even_with_dependency)], test_gate_other_stack_is_counted_apart_and_needs_no_model_call),
        ("경고 분모에서 팀원 스택을 안 뺌", [(policy, "gate_counts", _counts_other_in_base)], test_gate_counts_and_warning_ratio),
        ("팀원 스택에서 수집기가 읽는 내 스택을 안 뺌 (CLI)", [(cli_mod, "collector_stack_names", lambda targets_path=None: ([], []))], test_cli_run_reads_team_stack_from_collect_only_targets),
        ("팀원 스택에서 관문 manifest 신호를 안 뺌 (CLI)", [(cli_mod, "read_repo_signals", lambda repo, exclude=(): {})], test_cli_run_reads_team_stack_from_collect_only_targets),
        ("팀원 스택이 exclude를 무시 (CLI)", [(stack, "_excluded", lambda p, exclude: False)], test_cli_run_reads_team_stack_from_collect_only_targets),
        ("두 등급인데 첫 등급 행만 적용", [(policy, "decide", _first_tier_only)], test_two_tiers_any_row_keeps_and_records_which),
        ("후속 모델: 버전을 안 견줌 (같은 계열이면 다 후속)", [(successor, "_version", lambda parts: (len(parts),) if len(parts) > 3 else (0,))], test_successor_parse_and_find),
        ("후속 모델: 날짜 스냅숏을 버전으로 셈", [(successor, "_DATE_DIGITS", 99)], test_successor_parse_and_find),
        ("우리 것: eol·은퇴 범위 정리를 안 함 (W44 Electron 42 EOL)", [(scope, "narrow", lambda labels, *a, **k: (list(labels), []))], test_gate_eol_scoped_to_our_line_and_newer_line_alert),
        ("우리 것: 줄 비교가 늘 같은 줄", [(scope, "line_match", lambda ours, ann, op="eq": True)], test_gate_eol_scoped_to_our_line_and_newer_line_alert),
        ("우리 것: 줄 비교가 늘 다른 줄 (우리 줄 EOL도 뺌)", [(scope, "line_match", lambda ours, ann, op="eq": False)], test_gate_eol_scoped_to_our_line_and_newer_line_alert),
        ("우리 것: 「and below」를 안 봄", [(scope, "_LE", r"()")], test_scope_eol_targets_and_line_match),
        ("우리 것: 사람 overrides 버전을 무시", [(scope.Versions, "__call__", lambda self, name: self.auto.get(signals.norm(name)) or [])], test_gate_eol_scoped_to_our_line_and_newer_line_alert),
        ("우리 것: 은퇴 언급 검사가 늘 언급 있음", [(successor, "mentions", lambda text, name: ["x"])], test_gate_model_retirement_needs_our_model_mentioned),
        ("우리 것: 은퇴 언급을 버전 없이 계열로만", [(successor, "_version", lambda parts: (0,))], test_gate_model_retirement_needs_our_model_mentioned),        ("후속 모델: L2가 아닌 기술도 봄", [(gate_mod.Gate, "_successors", lambda self, item, names: successor.find(item.get("title") or "", names))], test_gate_successor_model_is_script_judged_l2_only_and_last),
        ("후속 모델을 심각도 「나머지」와 같게", [(policy, "SEVERITY", {k: v for k, v in policy.SEVERITY.items() if k != "successor_model"})], test_gate_successor_model_is_script_judged_l2_only_and_last),
        ("프리릴리스 major를 안 뺌", [(policy, "is_prerelease", lambda title: False)], test_normalize_changes_prerelease_major_and_lone_minor_patch),
        ("정식 x.0.0 아닌 major를 안 뺌 (HYO-169)", [(policy, "is_formal_major", lambda title: None)], test_normalize_changes_prerelease_major_and_lone_minor_patch),
        ("버전 못 읽은 major도 뺌 (HYO-169)", [(policy, "is_formal_major", lambda title: False)], test_normalize_changes_prerelease_major_and_lone_minor_patch),
        ("태그를 안 보고 제목(릴리스 이름)만 봄 (HYO-169)", [(policy, "item_tag", lambda item: None)], test_normalize_changes_prerelease_major_and_lone_minor_patch),
        ("관문이 변화 종류 정리를 안 함 (모델 라벨 그대로)", [(policy, "normalize_changes", lambda changes, title="": list(changes))], test_normalize_changes_prerelease_major_and_lone_minor_patch),
        ("두 등급인데 첫 등급 행만 적용 (관문)", [(policy, "decide", _first_tier_only)], test_lane_a_gate_wiring_and_cap),
        ("모델 은퇴 ✅를 ❌로", [(policy, "TABLE", _widened_table())], test_every_cell_default_context),
        ("모델 은퇴 ✅를 ❌로 (문서 대조)", [(policy, "TABLE", _widened_table())], test_table_matches_policy_doc),
        ("보정 축(경로·고정·민감)을 무시", [(policy, "cell", _ignore_context)], test_adjustments),
        ("C1을 k번 중 마지막 하나만 봄", [(evaluate, "judge", _judge_first_run_only)], test_judge_pass_k_and_thresholds),
        ("C1을 평균으로 봄", [(evaluate, "judge", _judge_mean_c1)], test_judge_pass_k_and_thresholds),
        ("스크립트 검증 무시 (모델을 그대로)", [(tiers, "verify", _trust_model)], test_store_classify_applies_script_and_records_mismatch),
        ("스크립트 검증 무시 (평가 흐름)", [(tiers, "verify", _trust_model)], test_evaluate_end_to_end_with_stub_oracle),
        ("사람 수정 무시", [(TierStore, "effective", _ignore_overrides)], test_store_reuse_fingerprint_and_human_override),
        ("상한 정렬: 심각도 먼저", [(policy, "rank_key", _severity_first)], test_cap_order_and_log),
        ("상한: 기술당 칸 제한 없음 (W44 Electron 5/5)", [(policy, "PER_TECH", 0)], test_cap_one_slot_per_technology_then_fill),
        ("상한: 기술 대신 항목마다 따로 셈", [(policy, "cap_technology", lambda e: e["id"])], test_cap_one_slot_per_technology_then_fill),
        ("상한: 남은 칸을 안 채움", [(policy, "apply_cap", _cap_no_fill)], test_cap_one_slot_per_technology_then_fill),
        ("상한: 기술당 칸 제한 없음 (관문 배선)", [(policy, "PER_TECH", 0)], test_lane_a_gate_wiring_and_cap),
        ("top-k: 「항상」 칸 없음 (보안도 몫 경쟁)", [(policy, "ALWAYS_CHANGES", ())], test_topk_always_then_per_technology),
        ("top-k: k를 무시 (전부 목록)", [(policy, "TOPK", 99)], test_topk_always_then_per_technology),
        ("top-k: 기술 대신 항목마다 따로 셈", [(policy, "cap_technology", lambda e: e["id"])], test_topk_always_then_per_technology),
        ("top-k: 관문 배선이 예전 5칸으로", [(policy, "CAP_MODE", "weekly5")], test_lane_a_gate_wiring_and_cap),
        ("스택에서 뺀 기술(retired)을 무시하고 판정", [(TierStore, "effective", _retired_ignored)], test_retired_technology_is_recorded_but_not_judged),
        ("top-k: 기술 질의는 1위만 detect (11/4 오전 규칙)", [(policy, "DETECT_TOPK", 1)], test_topk_always_then_per_technology),
        ("top-k: 「항상」을 접지 않음 (전부 detect)", [(policy, "fold_key", lambda e: ("item", e["id"]))], test_topk_always_then_per_technology),
        ("top-k: 「항상」을 기술당 1건으로 (안 A — 줄 무시)", [(policy, "fold_key", lambda e: ("tech", policy.cap_technology(e)))], test_topk_always_then_per_technology),
        ("top-k: 우리 줄 · 다른 줄 기록 무시", [(policy, "fold_key", lambda e: (policy.release_of(e) or ("item", e["id"]))[0])], test_topk_always_then_per_technology),
        ("채점이 오류·분류 불가 상태를 무시", [(evaluate, "score_run", _score_status_ignored)], test_score_run_by_hand),
        ("허용 호스트를 넓힘", [(agents_backend, "ALLOWED_HOSTS", {"api.openai.com", "telemetry.example.invalid"})], test_netguard_blocks_outside_api_openai_and_stops_without_retry),
        ("개발 경로가 최우선이 아님 (떠 있는 태그·민감이 되살림)", [(policy, "cell", _dev_not_first)], test_dev_path_is_top_priority),
        ("S3가 호스팅 등급(L2·L3)을 떨어냄", [(tiers, "MANIFEST_DROP_TIERS", {"L0", "L1", "L2", "L3"})], test_verify_script_beats_model),
        ("S3가 호스팅 등급을 떨어냄 (Jev 두 등급 흐름)", [(tiers, "MANIFEST_DROP_TIERS", {"L0", "L1", "L2", "L3"})], test_jev_end_to_end_lane_a_and_evaluate),
        ("Jev 문턱 0.5 → 0.6", [(jev_backend, "THRESHOLD", 0.6)], test_jev_threshold_is_fixed_half_boundary),
        ("Jev 문턱 0.5 → 0.4", [(jev_backend, "THRESHOLD", 0.4)], test_jev_threshold_is_fixed_half_boundary),
        ("Jev 문턱 0.5 → 0.6 (두 번째 등급)", [(jev_backend, "THRESHOLD", 0.6)], test_jev_second_tier_needs_half),
        ("Jev alias(jev-latest) 허용", [(jev_backend, "PINNED_MODELS", {"jev-1.13.0", "jev-latest", "jev-preview", "jev-1.13"})], test_jev_pinned_model_key_gating_and_env_is_not_exported),
        ("Jev 허용 목록에 api.openai.com을 섞음", [(jev_backend, "ALLOWED_HOSTS", {"api.typesafe.ai", "api.openai.com"})], test_jev_netguard_allows_only_typesafe_host),
        ("429 retry-after를 무시", [(jev_backend, "parse_retry_after", lambda headers: None)], test_jev_retry_429_respects_retry_after_and_4xx_is_not_retried),
        ("Jev 단가를 10배로", [(jev_backend, "INPUT_USD_PER_MTOK", 0.42)], test_jev_call_log_cost_and_returned_model),
        ("스택 리더: exclude를 무시", [(stack, "_excluded", lambda p, exclude: False)], test_signals_use_tracked_files_minus_excludes_and_skip_claude),
        ("스택 리더: exclude를 무시 (--targets)", [(stack, "_excluded", lambda p, exclude: False)], test_classify_stack_targets_reads_all_targets_with_excludes_and_reports_missing),
        ("스택 리더: git 목록을 못 쓰고 폴더를 걸음 (미추적 사본이 섞임)", [(stack, "_git_files", lambda root: (None, "mutation"))], test_signals_use_tracked_files_minus_excludes_and_skip_claude),
        ("스택 리더: .claude를 건너뛰지 않음", [(stack, "SKIP_DIRS", {"node_modules", ".git"})], test_signals_use_tracked_files_minus_excludes_and_skip_claude),
        ("스택 비교: 정규화 없이 견줌", [(signals, "norm", lambda n: n)], test_compare_stacks_groups_and_normalizes),
        ("창: R5(security 꼬리표·fix 글머리)를 안 옮김", [(prompts, "_R5_SECURITY_BULLET", re.compile(r"(?!x)x"))], test_window_moves_security_tag_and_fix_bullets_hyo138),
        ("창: 재배치 없이 앞에서 자르기만", [(prompts, "window_text", lambda text, text_max=3000: (text or "")[:text_max])], test_window_moves_only_risk_sections_from_outside_the_window),
        ("창: 굵은 라벨(R2)을 못 봄", [(prompts, "_R2_LABEL", re.compile(r"(?!x)x"))], test_window_moves_only_risk_sections_from_outside_the_window),
        ("창: 본문 위험 단어 줄도 옮김", [(prompts, "_R4_COMMIT", re.compile(r"(?i)\b(?:migrate|remove)\b"))], test_window_moves_only_risk_sections_from_outside_the_window),
    ]


def run_mutations():
    missed = 0
    for name, patches, test in mutations():
        ctxs = [Mutation(t, n, v) for t, n, v in patches]
        for c in ctxs:
            c.__enter__()
        try:
            test()
            caught = False
        except Exception:  # noqa: BLE001 — 망가뜨린 코드에서 테스트가 어떤 식으로든 빨개지면 잡힌 것이다
            caught = True
        finally:
            for c in reversed(ctxs):
                c.__exit__()
        print(f"  {'잡힘' if caught else '놓침 !!'}  변이: {name}  ({test.__name__})")
        missed += 0 if caught else 1
    return missed


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
