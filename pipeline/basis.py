"""판정 기준 — 대상 레포를 원격 기본 브랜치 HEAD로 읽고, 어느 커밋으로 판정했는지 남긴다 (HYO-162).

11/2 실사용 첫 detect가 6일 묵은 feed(관문 판정 때 Electron 39)로 「v45로 올릴지 검토」 초안을 올렸다 — 10/28에 이미 45로 올린 뒤였다.
수집(관문, Windows)과 detect(맥)는 시점도 기기도 달라서, 같은 「우리 버전」을 서로 다른 사본에서 읽는다. 그래서:

- 읽는 곳: 대상 레포 작업 사본이 아니라 그 `origin`의 기본 브랜치 HEAD — `<cache>/<repo>`에 얕게 clone해 두고 매번 맞춘다.
  매니페스트 파서(signals.read_repo_versions)는 그대로 쓴다 (git 추적 파일만 읽는 규칙도 그대로 — 캐시도 git 작업 트리다)
- 원격을 못 읽으면 조용히 넘어가지 않는다: 그 레포만 작업 사본으로 읽고 `source: local` + 이유를 남긴다
- 관문은 판정 기준을 detect 입력 옆 `basis.json`에 남기고, detect는 지금 기준과 대조해 이미 반영된 릴리스를 뺀다 (`applied`)

basis 모양: {"made_at", "targets", "repos": [{repo, source: remote|local|missing, commit, remote?, error?, versions: {정규화 이름: [버전]}}]}
"""
import datetime as dt
import json
import os
import re
import subprocess
from pathlib import Path

from .importance.scope import vtuple
from .importance.signals import norm, read_repo_versions

BASIS = "basis.json"
GIT_TIMEOUT = 180


def _git(*args, cwd=None):
    """→ (성공, 출력 | 실패 이유)."""
    try:
        p = subprocess.run(["git", *(["-C", str(cwd)] if cwd else []), *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT,
                           env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})  # 예약 실행 — 인증을 묻지 않고 실패로 끝낸다
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"git 실행 실패: {type(e).__name__}"
    if p.returncode != 0:
        return False, (p.stderr or p.stdout).strip().splitlines()[-1][:200] if (p.stderr or p.stdout).strip() else f"종료 코드 {p.returncode}"
    return True, p.stdout.strip()


def remote_tree(name, root, cache_dir):
    """대상 레포 하나 → {repo, source, commit, root(읽을 폴더), remote?, error?}. 캐시는 이 함수만 쓴다 — 작업 사본은 건드리지 않는다."""
    ok, url = _git("remote", "get-url", "origin", cwd=root)
    if ok and url:
        dest = Path(cache_dir) / re.sub(r"[^0-9A-Za-z._-]", "_", name)
        if (dest / ".git").exists():
            ok, why = _git("fetch", "-q", "--depth", "1", "--no-tags", "origin", "HEAD", cwd=dest)
            if ok:
                ok, why = _git("reset", "-q", "--hard", "FETCH_HEAD", cwd=dest)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            ok, why = _git("clone", "-q", "--depth", "1", "--no-tags", url, str(dest))
        if ok:
            ok, commit = _git("rev-parse", "HEAD", cwd=dest)
            if ok:
                return {"repo": name, "source": "remote", "commit": commit, "remote": url, "root": str(dest)}
            why = commit
        error = f"원격을 못 읽었다: {why}"
    else:
        error = f"origin 원격이 없다: {url}"
    ok, commit = _git("rev-parse", "HEAD", cwd=root)
    return {"repo": name, "source": "local", "commit": commit if ok else None, "root": str(root), "error": error}


def _versions(root, exclude, name):
    return {k: sorted({r["version"] for r in rows}) for k, rows in read_repo_versions(root, exclude, name).items()}


def materialize(targets_path, cache_dir, tree=None):
    """radar-targets.json → (원격 HEAD를 가리키는 targets 파일 경로 | None, basis).
    대상 목록을 못 읽으면 경로 None — 부르는 쪽은 원래 targets로 돈다 (basis.error에 이유)."""
    tree = tree or remote_tree
    made = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    p = Path(targets_path)
    try:
        targets = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, {"made_at": made, "targets": str(p), "repos": [], "error": f"대상 목록을 못 읽었다: {type(e).__name__}"}
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    derived, repos = [], []
    for t in targets:
        root = (p.parent / t["path"]).resolve()
        if not root.is_dir():
            repos.append({"repo": t["repo"], "source": "missing", "commit": None, "error": f"폴더가 없다: {root}", "versions": {}})
            derived.append({**t, "path": str(root)})
            continue
        r = tree(t["repo"], root, cache / "repos")
        r["versions"] = _versions(r["root"], list(t.get("exclude") or []), t["repo"])
        derived.append({**t, "path": r.pop("root")})
        repos.append(r)
    out = cache / "radar-targets.json"
    out.write_text(json.dumps(derived, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out, {"made_at": made, "targets": str(p), "repos": repos}


def write(basis, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(basis, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def load(path):
    """basis 파일 → dict | None (없거나 못 읽으면)."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def summary_line(basis):
    """실행 요약 한 줄 — 원격 N · 작업 사본 M(이유) · 없음 K."""
    if basis.get("error"):
        return f"판정 기준: {basis['error']} — 대상 레포 작업 사본으로 돈다"
    rs = basis.get("repos") or []
    by = {s: [r for r in rs if r["source"] == s] for s in ("remote", "local", "missing")}
    line = f"판정 기준: 원격 HEAD {len(by['remote'])}개"
    if by["local"]:
        line += f" · 작업 사본 {len(by['local'])}개(" + "; ".join(f"{r['repo']} — {r.get('error')}" for r in by["local"]) + ")"
    if by["missing"]:
        line += f" · 폴더 없음 {len(by['missing'])}개(" + ", ".join(r["repo"] for r in by["missing"]) + ")"
    return line


def changed(old, new):
    """두 기준 사이에 커밋이 바뀐 레포 → {repo: (옛 커밋, 새 커밋)}. 한쪽이라도 커밋을 모르면 넣지 않는다 (모르는 것을 바뀜으로 치지 않는다)."""
    before = {r["repo"]: r for r in (old or {}).get("repos") or []}
    out = {}
    for r in (new or {}).get("repos") or []:
        b = before.get(r["repo"])
        if b and b.get("commit") and r.get("commit") and b["commit"] != r["commit"]:
            out[r["repo"]] = (b["commit"], r["commit"])
    return out


def applied(feed, old, new):
    """관문 판정 뒤 대상 레포가 바뀌어 이미 반영된 의존성 릴리스를 뺀다 → (남은 소식, excluded 줄).

    빼는 조건 (셋 다): 커밋이 바뀐 레포에서 · 지금 우리 버전 ≥ 릴리스 버전 · 관문 기준에서는 그 버전이 없었거나 릴리스보다 낮았다.
    관문 기준에서 이미 ≥였던 것은 이 함수 몫이 아니다 — 판정 시점이 묵은 문제만 바로잡는다. 기준이 하나라도 없으면 아무것도 빼지 않는다."""
    moved = changed(old, new)
    if not moved:
        return list(feed), []
    before = {r["repo"]: r.get("versions") or {} for r in old.get("repos") or []}
    now = {r["repo"]: r.get("versions") or {} for r in new.get("repos") or []}
    keep, out = [], []
    for it in feed:
        rel = vtuple(it.get("version")) if it.get("package") else None
        hit = None
        for repo, (c0, c1) in moved.items():
            if not rel:
                break
            key = norm(it["package"])
            ours = [v for v in now[repo].get(key, []) if vtuple(v) and vtuple(v) >= rel]
            was = [v for v in before.get(repo, {}).get(key, []) if vtuple(v)]
            if ours and not any(vtuple(v) >= rel for v in was):
                hit = (f"이미 반영됨 — {repo}의 {it['package']} 버전: 관문 기준 {c0[:7]}에서 {', '.join(was) or '없음'}"
                       f" → 지금 {c1[:7]}에서 {ours[0]} · 릴리스 {it['version']}")
                break
        if hit:
            out.append({"url": it.get("url") or "", "reason": hit})
        else:
            keep.append(it)
    return keep, out
