"""스킬을 ADR repo로 배포 — `.claude/skills/jhs-decisions/`를 그대로 복사한다 (CLAUDE.md 규율 6).

실행: python scripts/deploy_skill.py <ADR repo 경로>   예) python scripts/deploy_skill.py ../adr

- 대상의 스킬 폴더는 지우고 새로 복사한다. ADR repo에서 스킬을 직접 고치지 않는다 — 고칠 곳은 이 레포다
- 테스트는 넣지 않는다: test_radar.py는 eval 기대값을 담고 있다 (러너 SKILL_EXCLUDE와 같은 규칙)
- 커밋은 하지 않는다. 출력된 원본 커밋을 ADR repo 커밋 메시지에 적는다
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SKILL_SRC = os.path.join(ROOT, ".claude", "skills", "jhs-decisions")
SKILL_EXCLUDE = shutil.ignore_patterns("test_*.py", "__pycache__", "*.pyc")  # eval/runner/run_case.py와 같게


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    target = os.path.abspath(sys.argv[1])
    if not os.path.isdir(os.path.join(target, ".git")):
        sys.exit(f"git repo가 아니다: {target}")
    if os.path.commonpath([target, ROOT]) == ROOT:
        sys.exit("대상이 이 레포 안이다 — ADR repo는 별도 레포")

    git = lambda *a: subprocess.run(["git", "-C", ROOT, *a], capture_output=True, text=True).stdout.strip()
    dirty = git("status", "--porcelain", "--", SKILL_SRC)
    commit = git("rev-parse", "--short", "HEAD")

    dst = os.path.join(target, ".claude", "skills", "jhs-decisions")
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(SKILL_SRC, dst, ignore=SKILL_EXCLUDE)

    print(f"복사: {SKILL_SRC} -> {dst}")
    print(f"원본: jhs-decisions@{commit}" + (" (+ 커밋 안 된 스킬 변경 — 먼저 커밋할 것)" if dirty else ""))


if __name__ == "__main__":
    main()
