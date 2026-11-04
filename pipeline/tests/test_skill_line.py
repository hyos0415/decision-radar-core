"""스킬 줄(pipeline/skill_line.py, HYO-166 · HYO-167) 오프라인 테스트 — claude 없이.

  python pipeline/tests/test_skill_line.py

실행기 자체(가짜 claude · 원격 차단 · 봉인)는 desktop/backend/test_backend.py 「HYO-166」 시나리오가 본다. 여기는 결정론 부분만:
- 같은 항목 기준 = 정규화 패키지 이름 + 릴리스 주 버전, 패키지가 없으면 정규화 URL (비교 프로토콜 §7 — 재생 · 그림자 · 합집합 공통)
  Langfuse v5.0.0과 v5.0.1은 같은 항목 · 0.x는 둘째 자리까지 (vLLM 0.31 ≠ 0.32)
- 원격 시도 세기 (git push · gh pr/issue/api)
- 스킬은 tag에서 꺼낸다 — 테스트 파일까지 그대로 꺼내고, 빼는 것은 실행 루트(prepare_runroot)가 한다
"""
import os
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pipeline import skill_line as sl  # noqa: E402


def test_same_item_major_version():
    assert sl.item_key("Langfuse", "5.0.0") == sl.item_key("langfuse", "v5.0.1") == "pkg:langfuse@5"
    assert sl.item_key("langfuse", "5.0.0") != sl.item_key("langfuse", "4.9.0")
    assert sl.item_key("vllm", "0.31.0") != sl.item_key("vllm", "0.32.1")
    assert sl.item_key("vllm", "0.32.0") == sl.item_key("vLLM", "0.32.1") == "pkg:vllm@0.32"
    assert sl.item_key("langchain_core", "1.2") == sl.item_key("langchain-core", "1.3.0")


def test_url_fallback():
    assert sl.item_key(None, None, "https://example.com/blog/x/") == sl.item_key(None, None, "http://Example.com/blog/x") == "url:example.com/blog/x"
    assert sl.item_key("langfuse", None, "https://langfuse.com/x") == "url:langfuse.com/x"  # 버전을 모르면 URL
    assert sl.item_key() is None


def test_version_of():
    assert sl.version_of("Langfuse Python SDK v5.0.0 대응") == "5.0.0"
    assert sl.version_of(None, "의존성 릴리스 v0.32.0 대응 (breaking)") == "0.32.0"
    assert sl.version_of("FastAPI 0.145.0rc1 breaking") == "0.145.0rc1"
    assert sl.version_of("새 모델") is None


def test_write_deny_rules():
    # 리뷰 11/4 F1: 대상 레포는 읽기만 — Claude Code 권한 규칙의 절대 경로는 `//`로 시작
    if sys.platform == "win32":
        assert sl.write_deny(["C:\\Users\\me\\repo\\"]) == ["Edit(//c/Users/me/repo/**)", "Write(//c/Users/me/repo/**)"]
    else:
        assert sl.write_deny(["/Users/me/repo/"]) == ["Edit(//Users/me/repo/**)", "Write(//Users/me/repo/**)"]
    argv = sl.command("claude", "p", ["/a", "/b"], read_only=["/b"])
    deny = argv[argv.index("--disallowedTools") + 1:]
    assert "Bash(git push:*)" in deny and len(deny) == len(sl.DENY) + 2 and all(r.startswith(("Edit(", "Write(")) for r in deny[len(sl.DENY):])
    assert sl.command("claude", "p", ["/a"])[-len(sl.DENY):] == list(sl.DENY)          # 대상 레포가 없으면 예전 그대로


def test_remote_attempts():
    uses = [{"name": "Bash", "input": {"command": c}} for c in (
        "git -C ../adr push origin HEAD", "gh pr create --title x", "cd x && gh issue create", "git status", "echo 'ghost'",
        "python radar.py validate docs/adr")] + [{"name": "WebFetch", "input": {"url": "https://github.com/x/pulls"}}]
    assert sl.remote_attempts(uses) == 3


def test_export_skill_head():
    with tempfile.TemporaryDirectory() as d:
        sha = sl.export_skill(d, "HEAD")
        assert len(sha) == 40
        assert (Path(d) / "SKILL.md").exists() and (Path(d) / "scripts" / "radar.py").exists()
        try:
            sl.export_skill(os.path.join(d, "x"), "no-such-tag-radar")
            raise AssertionError("없는 tag인데 꺼냈다")
        except RuntimeError as e:
            assert "fetch --tags" in str(e)


TESTS = [(n, f) for n, f in globals().items() if n.startswith("test_")]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    bad = 0
    for n, f in TESTS:
        try:
            f()
            print(f"  ok   {n}")
        except Exception:  # noqa: BLE001
            bad += 1
            print(f"  FAIL {n}")
            traceback.print_exc()
    print(f"{len(TESTS) - bad}/{len(TESTS)} 통과")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
