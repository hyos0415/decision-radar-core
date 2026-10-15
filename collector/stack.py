"""스택 manifest 공용 리더 (HYO-122) — 수집기(package.json)와 관문(requirements·package.json·Dockerfile·compose)이 같은 파일 목록을 본다.

규칙: git이 추적하는 파일만 (untracked·ignored 사본이 섞이지 않게) + 대상별 `exclude`. git이 아니면 폴더를 걷되 info에 이유를 남긴다.
"""
import fnmatch
import json
import os
import subprocess
from pathlib import Path, PurePosixPath

MANIFEST_PATTERNS = ("package.json", "requirements*.txt", "pyproject.toml", "Dockerfile*", "compose*.y*ml", "docker-compose*.y*ml")
# .claude: worktrees/ 아래 다른 브랜치 사본이 있다 — main에 없는 의존성이 섞인다 · eval/runs 같은 실행 산출물도 대상이 아니다
SKIP_DIRS = {"node_modules", ".git", "out", "dist", ".venv", "venv", ".claude"}


def is_manifest(name):
    """파일 이름(basename)이 manifest 패턴에 맞나. `compose*.y*ml`은 yml·yaml만 (Dockerfile*과 달리 확장자를 좁힌다)."""
    return any(fnmatch.fnmatchcase(name, p) for p in MANIFEST_PATTERNS) and not (
        name.startswith(("compose", "docker-compose")) and not name.endswith((".yml", ".yaml")))


def _git_files(root):
    """→ (root 기준 상대 POSIX 경로 목록 | None, 실패 이유 | None). 추적 파일만."""
    try:
        p = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"git 실행 실패: {type(e).__name__}"
    if p.returncode != 0:
        return None, "git 작업 트리가 아니다: " + p.stderr.decode("utf-8", "replace").strip()[:120]
    return [s for s in p.stdout.decode("utf-8", "replace").split("\0") if s], None


def _walk_files(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        rel = Path(dirpath).relative_to(root)
        out += [(rel / f).as_posix() for f in sorted(filenames)]
    return out


def _excluded(p, exclude):
    """root 기준 POSIX 경로 p가 exclude 항목(경로 · 폴더 접두 · glob) 중 하나에 맞나."""
    for item in exclude:
        if p == item or p.startswith(item.rstrip("/") + "/") or fnmatch.fnmatchcase(p, item):
            return True
    return False


def manifest_files(root, exclude=()):
    """→ (manifest 파일 절대 경로 목록(정렬), {"mode": "git"|"walk", "excluded": [제외된 상대 경로], "why": str|None})."""
    root = Path(root)
    if not root.is_dir():
        return [], {"mode": "walk", "excluded": [], "why": f"폴더 없음: {root}"}
    rels, why = _git_files(root)
    mode = "git"
    if rels is None:
        rels, mode = _walk_files(root), "walk"
    keep, dropped = [], []
    for rel in sorted(rels):
        parts = PurePosixPath(rel).parts
        if not is_manifest(parts[-1]) or any(d in SKIP_DIRS for d in parts[:-1]):
            continue
        if not (root / rel).is_file():           # 추적은 되지만 지운 파일
            continue
        (dropped if _excluded(rel, exclude) else keep).append(rel)
    return [root / r for r in keep], {"mode": mode, "excluded": dropped, "why": why}


def package_json_entries(root, exclude=()):
    """root의 package.json들 → [{name, file(상대 POSIX), section, spec}]. 파일 순 → dependencies 먼저 → 파일 안 순서. spec = 버전 문자열(관문 pin 신호)."""
    root = Path(root)
    paths, _ = manifest_files(root, exclude)
    out = []
    for f in paths:
        if f.name != "package.json":
            continue
        try:
            pkg = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(pkg, dict):
            continue
        rel = f.relative_to(root).as_posix()
        for sect in ("dependencies", "devDependencies"):
            deps = pkg.get(sect)
            for n, spec in (deps.items() if isinstance(deps, dict) else ()):
                out.append({"name": n, "file": rel, "section": sect, "spec": spec if isinstance(spec, str) else ""})
    return out
