"""git 내보내기 — 승인 = ADR repo main에 바로 커밋·push (설계 docs/design/hitl-state.md 결정 8, §5-2, HYO-44).

이 모듈은 git 기계장치만 안다 (ADR repo의 어느 항목이 무엇인지는 hitl.py의 몫). 안전장치 넷:

  1. **경로 허용 목록** — 새 ADR 파일(`docs/adr/NNNN-*.md`)과 `.radar/items/` 아래만 커밋한다. 허용 목록 밖의 경로를 넘기거나,
     이미 git이 추적하는 ADR 파일이거나(= 기존 ADR 수정), 스테이징에 허용 목록 밖의 변경이 끼어 있으면 **거부**한다
  2. **fast-forward push만** — `--force`·`+refspec`은 `_git`이 아예 실행하지 않는다
  3. **승인 하나 = 커밋 하나** — 한 번의 publish가 커밋 하나. push가 경합으로 거부되면 그 커밋을 취소(`reset --soft` + 언스테이지)하고
     파일은 작업 트리에 그대로 둔 채 같은 경로(동기화 → 재검사 → 커밋 → push)를 다시 돈다
  4. **eval·dry-run에서는 git을 부르지 않는다** — `dry_run`이면 publish가 건너뛰고, `_git`을 직접 불러도 실패한다 (이중 잠금)

원격이 앞서 있으면(사람이 그 사이 push) publish가 fetch → fast-forward한 뒤 호출자가 준 `check()`(재개 검사 = 지문 비교)를 다시 돈다.
check가 차이를 돌려주면 커밋하지 않고 그 결과를 그대로 돌려준다 — 호출자가 다시 interrupt한다 (§4).
"""

import os
import re
import subprocess

# 새 ADR 파일 · 항목 별도 파일 — 이 밖은 커밋하지 않는다 (결정 8)
ALLOW_ADR = re.compile(r"^docs/adr/\d{4}-[^/\\]+\.md$")
ALLOW_ITEM = re.compile(r"^\.radar/items/[^/\\][^\\]*$")
RACE_MARKERS = ("non-fast-forward", "fetch first", "rejected", "stale info")  # push가 「앞서 있어서」 거부된 것으로 본다


def path_allowed(rel):
    return bool(ALLOW_ADR.match(rel) or ALLOW_ITEM.match(rel))


class ExportRefused(Exception):
    """허용되지 않는 일 — 하지 않았다 (eval·dry-run에서 git 호출, 허용 목록 밖 경로, force push …)."""


class Exporter:
    """내보내기 자리. 기본은 아무것도 안 한다 — eval·dry-run·스텁은 늘 이것이다."""
    active = False

    def sync(self):
        return {"state": "skipped", "reason": "no_exporter"}

    def publish(self, paths, message, **kw):
        return {"state": "skipped", "reason": "no_exporter"}

    def is_tracked(self, rel):
        return False


class GitExporter(Exporter):
    """ADR repo 작업 사본(root)에서 main으로 내보낸다. 경로는 root 기준 상대 경로(/)다."""
    active = True

    def __init__(self, root, *, dry_run=False, remote="origin", branch="main", max_attempts=3, identity=None, hook=None, git="git"):
        self.root, self.dry_run, self.remote, self.branch = os.path.abspath(root), dry_run, remote, branch
        self.max_attempts, self.identity, self.hook, self.git = max_attempts, identity or {}, hook, git

    # ── git 호출 한 곳 ───────────────────────────────────────────────
    def _git(self, *args, ok=(0,)):
        if self.dry_run:  # MUT:dry_run_git — eval·dry-run에서는 git을 부르지 않는다
            raise ExportRefused(f"dry-run·eval에서는 git을 부르지 않는다: git {' '.join(args[:2])}")
        for a in args:  # MUT:force_guard
            if a in ("-f", "--force", "--force-with-lease", "--mirror", "--delete") or a.startswith("+") or a.startswith("--force"):
                raise ExportRefused(f"force·삭제 push는 하지 않는다: {a}")
        cfg = []
        if self.identity.get("name"):
            cfg += ["-c", f"user.name={self.identity['name']}"]
        if self.identity.get("email"):
            cfg += ["-c", f"user.email={self.identity['email']}"]
        p = subprocess.run([self.git, "-c", "core.quotepath=off", *cfg, *args], cwd=self.root, capture_output=True, text=True,
                           encoding="utf-8", env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
        if ok is not None and p.returncode not in ok:
            raise ExportRefused(f"git {' '.join(args[:2])} exit {p.returncode}: {(p.stderr or p.stdout).strip()[:300]}")
        return p

    def _out(self, *args):
        return self._git(*args).stdout.strip()

    def _hook(self, name):
        if self.hook:
            self.hook(name)  # 테스트가 단계 사이에서 사람의 push를 끼워 넣는 자리

    def is_tracked(self, rel):
        return self._git("ls-files", "--error-unmatch", "--", rel, ok=None).returncode == 0

    # ── 동기화 ───────────────────────────────────────────────────────
    def sync(self):
        """fetch → 뒤처졌으면 fast-forward. {"state": "ok", "advanced": 앞선 커밋 수} 또는 {"state": "blocked", "reason", …}.
        로컬에 푸시 안 된 커밋이 있거나 갈라졌으면 막는다 — 낯선 커밋을 같이 밀지 않는다."""
        if self.dry_run:  # MUT:dry_run_sync
            return {"state": "skipped", "reason": "dry_run"}
        try:
            branch = self._out("rev-parse", "--abbrev-ref", "HEAD")
            if branch != self.branch:
                return {"state": "blocked", "reason": "wrong_branch", "branch": branch}
            f = self._git("fetch", self.remote, ok=None)
            if f.returncode:
                return {"state": "blocked", "reason": "fetch_failed", "detail": f.stderr.strip()[:200]}
            ahead, behind = (int(x) for x in self._out("rev-list", "--left-right", "--count", f"HEAD...{self.remote}/{self.branch}").split())
            if ahead:
                return {"state": "blocked", "reason": "local_ahead_or_diverged", "ahead": ahead, "behind": behind}
            if behind:
                m = self._git("merge", "--ff-only", f"{self.remote}/{self.branch}", ok=None)
                if m.returncode:
                    return {"state": "blocked", "reason": "ff_failed", "detail": (m.stderr or m.stdout).strip()[:300]}
            return {"state": "ok", "advanced": behind}
        except ExportRefused as e:
            return {"state": "blocked", "reason": "git_error", "detail": str(e)}

    # ── 스테이징 검사 ────────────────────────────────────────────────
    def _staged(self):
        out = self._out("diff", "--cached", "--name-status")
        res = {}
        for line in out.splitlines():
            parts = line.split("\t")
            res[parts[-1]] = parts[0][:1]
        return res

    def _check_paths(self, paths):
        """커밋하려는 경로 검사 — 허용 목록 · 새 ADR만 (이미 추적 중인 ADR은 기존 ADR 수정이다)."""
        for rel in paths:
            if not path_allowed(rel):  # MUT:allowlist
                raise ExportRefused(f"허용 목록 밖의 경로: {rel}")
            if ALLOW_ADR.match(rel) and self.is_tracked(rel):  # MUT:existing_adr
                raise ExportRefused(f"기존 ADR 파일은 고치지 않는다: {rel}")

    def _verify_staged(self, paths):
        staged = self._staged()
        extra = sorted(set(staged) - set(paths))
        if extra:  # MUT:staged_scope
            raise ExportRefused(f"허용 목록 밖의 변경이 스테이징에 있다: {extra}")
        for rel, st in staged.items():
            if ALLOW_ADR.match(rel) and st != "A":  # MUT:adr_added_only
                raise ExportRefused(f"ADR 파일은 새로 추가만 한다 ({st}): {rel}")

    # ── 내보내기 ─────────────────────────────────────────────────────
    def publish(self, paths, message, *, check=None, before=None, undo=None):
        """paths를 커밋 1개로 main에 fast-forward push한다.

        check()  동기화 뒤마다 부른다 — None이면 진행, dict면 그대로 돌려주고 커밋하지 않는다 (재개 검사에서 달라짐)
        before() 커밋 직전, 커밋에 들어갈 파일을 최종 상태로 쓴다
        undo()   push가 경합으로 거부돼 커밋을 취소한 뒤, 파일을 직전 상태로 돌린다
        돌려주는 state: exported · skipped · blocked · (check가 준 것: stale 등)"""
        if self.dry_run:  # MUT:dry_run_publish
            return {"state": "skipped", "reason": "dry_run"}
        paths = list(paths)
        try:
            self._check_paths(paths)
            attempts = 0
            while attempts < self.max_attempts:
                attempts += 1
                s = self.sync()
                if s["state"] != "ok":
                    return {**s, "state": "blocked"}
                self._hook("synced")
                if check is not None:  # MUT:recheck — 원격이 앞섰으면(advanced) 세상이 바뀌었다: 재개 검사를 다시 돈다
                    r = check()
                    if r:
                        return r
                if self._staged():  # MUT:prestaged — 다른 것이 이미 스테이징돼 있다 — 섞어 커밋하지 않는다
                    raise ExportRefused(f"스테이징에 이미 변경이 있다: {sorted(self._staged())}")
                if before is not None:
                    before()
                self._git("add", "--", *paths)
                self._verify_staged(paths)
                parent = self._out("rev-parse", "HEAD")
                self._git("commit", "-q", "-m", message)
                sha = self._out("rev-parse", "HEAD")
                self._hook("committed")
                p = self._git("push", self.remote, f"HEAD:{self.branch}", ok=None)
                if p.returncode == 0:
                    return {"state": "exported", "commit": sha, "attempts": attempts, "paths": paths}
                err = (p.stderr or p.stdout).strip()
                if self._out("rev-parse", "HEAD") == sha:  # MUT:undo — 되돌린다: 로컬 커밋 취소, 파일은 작업 트리에 그대로
                    self._git("reset", "-q", "--soft", parent)
                    self._git("reset", "-q", "--", *paths)
                if undo is not None:
                    undo()
                if not any(m in err for m in RACE_MARKERS):  # 경합이 아니라 다른 실패(인증·네트워크) — 다시 돌지 않는다
                    return {"state": "blocked", "reason": "push_failed", "detail": err[:300]}
            return {"state": "blocked", "reason": "push_rejected", "attempts": attempts}
        except ExportRefused as e:
            return {"state": "blocked", "reason": "refused", "detail": str(e)}
