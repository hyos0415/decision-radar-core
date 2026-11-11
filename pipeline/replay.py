"""같은 주 재생 (HYO-164) — W41~W43 입력으로 제품 detect를 다시 돌려, 그 주 스킬이 실제로 낸 detect 초안과 견준다.

등록: docs/design/comparison-protocol.md §7 「같은 주 재생」 + 그 「변경 (2026-11-11)」 · §8 「변경 (2026-11-07)」(재생은 「두 체계를 합치면 더 잡나」의 근거).
보고: docs/decisions/replay-W41-W43.md

  python -m pipeline.replay plan    --out <폴더>                       # 무료 — 날짜 기준 basis · ADR · 등급 파일을 만들고 유료 호출 수를 센다
  python -m pipeline.replay run     --out <폴더> --engine stub         # 배관 확인 (모델 없음)
  python -m pipeline.replay run     --out <폴더> --engine real --confirm-real [--k 3] [--weeks 2026-W41] [--from gate]
  python -m pipeline.replay compare --out <폴더>                       # 둘 다 · 스킬만 · 제품만 + 갈린 단계 → compare.json · compare.md

경로: --adr-repo(env RADAR_ADR_REPO) · --snapshots(env RADAR_SNAPSHOT_REPO). 둘 다 **읽기만** 한다 — 실제 ADR repo · radar-snapshots ·
대상 레포 · 데스크톱 행동 로그(events.jsonl)는 실행 전후로 상태를 떠 두고, 하나라도 바뀌면 멈춘다(Guard). 출력 폴더가 그 안에 있으면 시작을 거부한다.

한 주의 사슬 (제품 코드는 main 4eab096 그대로 — 단계마다 제품 CLI를 자식 프로세스로 부른다, collector.weekly와 같다):
  prepare  ADR repo = 그 주 스킬 detect 초안 커밋의 부모(git archive) · 대상 레포 = 그 시각 이전 기본 브랜치 마지막 커밋(로컬 원격 추적 기록,
           pipeline.basis.materialize의 tree 자리에 dated_tree) · 팀원 스택(수집 전용 대상)도 같은 시각 · 등급 파일 = 지금 tiers.json을 그 주 스택으로 자름
           · 자리 프로필 = adr radar-slots.yaml(11/4 첫 버전, 그 주엔 없었다 — 결과에 표시) · 스킬 쪽 = 그 주 초안 파일 + 사람 결정
  tiers    등급 재분류 (Anthropic claude-sonnet-5-5 API, 지문 바뀐 기술만 — 주간 weekly-tiers와 같은 classify, 커밋 · push 없음)
  gate     python -m pipeline.profile_filter run --gate gpt-6.1-sol (얼린 corpus@<주> → 레인 A 관문 · 경보 그물)
  slots    python -m pipeline.slots run (구독 claude -p 한 세션)
  feed     python -m pipeline.detect_feed --no-fetch (1차 출처 링크를 지금 받으면 그 뒤 내용이 섞인다 — 끈다)
  detect   product/radar/run_detect.py --model claude-cli --now <그 주 스킬 실행 시각> × k (구독, 실행마다 새 폴더 · 새 ADR 사본)
"""
import argparse
import datetime as dt
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline import basis, skill_line, trace, union  # noqa: E402
from pipeline.importance.signals import norm  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
CODE = "4eab096"            # 제품 코드 고정 (§7 변경 2026-11-11 — 등록 429d9e5에서 바꿈, 결과 전)
GATE_MODEL = "gpt-6.1-sol"  # collector.weekly.DEFAULT_GATE와 같다
TIERS_MODEL = "claude-sonnet-5-5"   # pipeline.importance.cli.WEEKLY_MODEL과 같다
K = 3                        # 모델 단계 반복 (§7) — 「제품이 냄」 = K번 중 MIN_RUNS번 이상
MIN_RUNS = 2
STEPS = ["prepare", "tiers", "gate", "slots", "feed", "detect"]
GATE_USD_PER_CALL = 0.0031   # W43~W46 관문 실측 $3.13 / 999회 (pipeline/out/<주>/gate-calls.jsonl) — plan의 어림값
TIERS_USD_PER_CALL = 0.005   # weekly-tiers 실측 (7회 $0.035 · 1회 $0.005)
STRIP_ENV = ("RADAR_UNION", "RADAR_EXPORT", "RADAR_SNAPSHOT_REPO", "RADAR_TIERS", "RADAR_SLOTS", "RADAR_APP_DATA",
             "RADAR_SNAPSHOT_DIR", "RADAR_BASIS_NOW")   # 자식이 실제 폴더 · 합집합 · 내보내기를 보지 않게 — 필요한 것만 다시 넣는다

# 그 주 스킬 detect 초안 (adr repo) — 등록 §7 「대상」. 시각 · 부모 · 사람 결정은 prepare가 git에서 읽어 week.json에 남긴다
WEEKS = {
    "2026-W41": {"pr": 8, "draft": "908c8d4", "next": "2026-W42"},    # 10/12 vLLM v0.31.0 · FastAPI 0.143.0
    "2026-W42": {"pr": 13, "draft": "a22bf94", "next": "2026-W43"},   # 10/19 Langfuse Python SDK v5.0.0
    "2026-W43": {"pr": 16, "draft": "3a18d75", "next": "2026-W44",    # 10/26 Anthropic Python SDK v2.0.0
                 "frozen_detect": "detect/2026-W43"},                  # 얼려 둔 관문 결과(10/20 코드) — 참고로 함께 적는다
}

STAGE = {"source": "출처에 없음", "timing": "수집 시점", "gate": "관문 탈락", "cap": "상한에서 잘림", "model": "모델이 뺌", "decided": "이미 결정됨"}


class ReplayError(RuntimeError):
    pass


class IsolationError(ReplayError):
    """실제 ADR repo · radar-snapshots · 대상 레포 · 행동 로그가 바뀌었다 — 재생은 아무것도 쓰면 안 된다."""


def _git(*args, cwd=None):
    return basis._git(*args, cwd=cwd)


def _need(ok_out, what):
    ok, out = ok_out
    if not ok:
        raise ReplayError(f"{what}: {out}")
    return out


def _iso(s):
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def write_json(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")


def load_json(path, default=None):
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


# ---------------------------------------------------------------------------- 날짜 기준 basis

def origin_branch(root):
    """원격 기본 브랜치의 로컬 원격 추적 ref (refs/remotes/origin/HEAD가 가리키는 것 → origin/main · origin/master)."""
    ok, ref = _git("symbolic-ref", "-q", "refs/remotes/origin/HEAD", cwd=root)
    if ok and ref:
        return ref
    for b in ("refs/remotes/origin/main", "refs/remotes/origin/master"):
        if _git("rev-parse", "-q", "--verify", b, cwd=root)[0]:
            return b
    raise ReplayError(f"{root}: 원격 기본 브랜치(origin/HEAD · main · master)를 모른다")


def last_fetch(root):
    """마지막 fetch 시각 (FETCH_HEAD 파일 시각) | None."""
    ok, p = _git("rev-parse", "--git-path", "FETCH_HEAD", cwd=root)
    if not ok:
        return None
    f = Path(p) if Path(p).is_absolute() else Path(root) / p
    return dt.datetime.fromtimestamp(f.stat().st_mtime).astimezone() if f.exists() else None


def dated_commit(root, at):
    """그 시각 이전 기본 브랜치의 마지막 커밋 (first-parent · 커미터 시각). 로컬 원격 추적 기록이 그 시각을 덮는지(fresh)도 본다 —
    기록의 끝이 그 시각보다 앞이고 그 뒤로 fetch한 적도 없으면, 사이에 들어온 커밋을 모르는 것이라 stale."""
    at = _iso(at)
    branch = origin_branch(root)
    ok, commit = _git("rev-list", "-1", "--first-parent", f"--before={at.isoformat()}", branch, cwd=root)
    tip_at = _iso(_need(_git("log", "-1", "--format=%cI", branch, cwd=root), f"{root} {branch}"))
    fetched = last_fetch(root)
    out = {"branch": branch, "commit": commit if ok and commit else None, "committed_at": None, "tip_at": tip_at.isoformat(),
           "fetched_at": fetched.isoformat(timespec="seconds") if fetched else None, "fresh": tip_at >= at or bool(fetched and fetched >= at)}
    if out["commit"]:
        out["committed_at"] = _need(_git("log", "-1", "--format=%cI", out["commit"], cwd=root), "커밋 시각")
    return out


def _safe(name):
    return re.sub(r"[^0-9A-Za-z._-]", "_", name)


def checkout_at(root, commit, dest):
    """원본은 읽기만 — `git clone --shared --no-checkout`(객체는 원본을 빌려 본다) 뒤 그 커밋으로. 다시 부르면 같은 사본을 맞춘다."""
    dest = Path(dest)
    if not (dest / ".git").exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        _need(_git("clone", "-q", "--shared", "--no-checkout", str(root), str(dest)), f"{root} 사본")
    _need(_git("checkout", "-q", "--detach", "--force", commit, cwd=dest), f"{dest} checkout {commit[:7]}")
    head = _need(_git("rev-parse", "HEAD", cwd=dest), "사본 HEAD")
    if head != commit:
        raise ReplayError(f"{dest}: HEAD {head[:7]} ≠ {commit[:7]}")
    return dest


def dated_tree(at, allow_stale=False):
    """pipeline.basis.materialize의 tree 자리 — 대상 레포 하나를 「그 시각 이전 마지막 커밋」으로 읽는다 (원격 HEAD 대신, HYO-164).
    원격은 부르지 않는다: 그 레포 작업 사본의 원격 추적 기록(origin/<기본 브랜치>)에서 고른다. 모르면 멈춘다 — 지금 상태로 조용히 넘어가지 않는다."""
    def tree(name, root, cache_dir):
        d = dated_commit(root, at)
        if not d["commit"]:
            raise ReplayError(f"{name}: {at} 이전 커밋이 {d['branch']}에 없다")
        if not d["fresh"] and not allow_stale:
            raise ReplayError(f"{name}: 원격 추적 기록이 {at}을 덮지 못한다 (끝 {d['tip_at']} · 마지막 fetch {d['fetched_at']}) — "
                              f"`git -C {root} fetch` 뒤 다시 (읽기 전용 원칙상 재생은 fetch하지 않는다)")
        dest = checkout_at(root, d["commit"], Path(cache_dir) / _safe(name))
        ok, url = _git("remote", "get-url", "origin", cwd=root)
        return {"repo": name, "source": "dated", "at": str(at), **d, "remote": re.sub(r"//[^/@]*@", "//", url) if ok else None,
                "root": str(dest)}
    return tree


def export_tree(repo, commit, dest):
    """git archive → 풀기. 원본 repo에는 아무것도 쓰지 않는다 (worktree 메타데이터도)."""
    p = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", commit], capture_output=True)
    if p.returncode != 0:
        raise ReplayError(f"git archive {commit}: {p.stderr.decode('utf-8', 'replace')[-200:]}")
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(p.stdout)) as t:
        t.extractall(dest, filter="data")
    return dest


def show(repo, rev, path):
    ok, out = _git("show", f"{rev}:{path}", cwd=repo)
    return out if ok else None


# ---------------------------------------------------------------------------- 등급 파일

def prune_tiers(data, names):
    """지금 등급 파일 → 그 주 스택(names = 정규화 이름)에 있는 기술 · override만. 관문은 등급 파일의 이름을 「우리 스택」으로 쓰므로(gate.Gate)
    그 뒤에 들어온 기술이 남으면 그 주에 우리 것이 아니던 소식을 남긴다. 「스택에서 뺌」(retired, 11/4 litellm)은 그 주엔 스택에 있었으니 푼다.
    → (잘린 data, 보고 {removed, removed_overrides, unretired})."""
    names = set(names)
    out = {**data, "technologies": {}, "overrides": {}}
    rep = {"removed": [], "removed_overrides": [], "unretired": []}
    for k, r in (data.get("technologies") or {}).items():
        (out["technologies"].__setitem__(k, r) if k in names else rep["removed"].append(r.get("technology", k)))
    for k, o in (data.get("overrides") or {}).items():
        if k not in names:
            rep["removed_overrides"].append(o.get("technology", k))
            continue
        o = dict(o)
        if o.pop("retired", None):
            rep["unretired"].append(o.get("technology", k))
        out["overrides"][k] = o
    return out, rep


def stack_names(targets_path, extra):
    from pipeline.importance import cli
    found, missing, _ = cli.load_targets(targets_path)
    if missing:
        raise ReplayError(f"대상 폴더 없음: {missing}")
    return {norm(q.name) for q in cli.queries_from_repos([(root, ex, name) for name, root, ex in found], extra)}


# ---------------------------------------------------------------------------- 격리

def default_data_dirs(env=None):
    """데스크톱 행동 로그가 있을 수 있는 곳 — 실사용 기본(~/.decision-radar) + RADAR_APP_DATA."""
    env = os.environ if env is None else env
    ds = [Path.home() / ".decision-radar"]
    if env.get("RADAR_APP_DATA"):
        ds.append(Path(env["RADAR_APP_DATA"]))
    return ds


DATA_FILES = ("events.jsonl", "runs.jsonl", "jobs.json")


class Guard:
    """실행 전후로 실제 repo(HEAD · 작업 트리 상태) · 데이터 폴더(행동 로그 파일 크기 · 시각)를 떠 두고 견준다.
    재생이 쓰지 않아도 다른 일이 바꿀 수 있다(예: 화 16:00 수집) — 그때도 멈추고 사람이 확인한다 (조용히 넘기지 않는다).
    fence = 출력 폴더만 막는 곳 (대상 레포 작업 사본 — 다른 세션이 작업 중일 수 있어 상태는 견주지 않는다. 재생은 `clone --shared`로 읽기만)."""

    def __init__(self, repos, data_dirs=(), fence=()):
        self.repos = [Path(r).resolve() for r in repos if r and Path(r).exists()]
        self.data_dirs = [Path(d).resolve() for d in data_dirs if d]
        self.fence = [Path(f).resolve() for f in fence if f]
        self.before = self.state()

    def protected(self):
        return self.repos + self.data_dirs + self.fence

    def check_out(self, out):
        o = Path(out).resolve()
        for p in self.protected():
            if o == p or p in o.parents:
                raise IsolationError(f"출력 폴더 {o}가 보호 폴더 {p} 안이다 — 재생은 실제 repo · 데이터 폴더에 쓰지 않는다")

    def state(self):
        s = {}
        for r in self.repos:
            ok, head = _git("rev-parse", "HEAD", cwd=r)
            ok2, st = _git("status", "--porcelain", "--untracked-files=all", cwd=r)
            s[str(r)] = {"head": head if ok else None, "status": st if ok2 else f"? {st}"}
        for d in self.data_dirs:
            for name in DATA_FILES:
                f = d / name
                s[str(f)] = (f.stat().st_size, f.stat().st_mtime_ns) if f.exists() else None
        return s

    def check(self, when=""):
        now = self.state()
        moved = [k for k in self.before if now.get(k) != self.before[k]]
        if moved:
            raise IsolationError(f"{when} 보호 대상이 바뀌었다 — {moved} (재생이 썼거나 다른 작업이 바꿨다. 확인 전에는 결과를 쓰지 않는다)")


PRODUCT_PATHS = ("pipeline", "product", "collector", ".claude/skills/jhs-decisions")
OWN_PATHS = ("pipeline/replay.py", "pipeline/tests/test_replay.py", "pipeline/README.md")   # 재생 러너 자신 — 제품이 부르지 않는다


def code_drift(repo=REPO, code=CODE, paths=PRODUCT_PATHS, own=OWN_PATHS):
    """제품 코드가 고정 커밋과 다른 파일 (작업 트리 기준 — 커밋 안 한 수정도). 등록: 재생 중 코드를 고치면 위반 — 실제 실행은 거부한다."""
    ok, out = _git("diff", "--name-only", code, "--", *paths, *(f":(exclude){p}" for p in own), cwd=repo)
    if not ok:
        raise ReplayError(f"코드 고정 커밋 {code}과 견줄 수 없다: {out}")
    return [x for x in out.splitlines() if x]


def child_env(base, extra):
    env = {k: v for k, v in base.items() if k not in STRIP_ENV}
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"})
    env.update({k: str(v) for k, v in extra.items()})
    return env


# ---------------------------------------------------------------------------- 한 주

class Week:
    def __init__(self, week, cfg):
        if week not in WEEKS:
            raise ReplayError(f"등록에 없는 주 {week} — {list(WEEKS)}")
        self.week, self.spec, self.cfg = week, WEEKS[week], cfg
        self.dir = Path(cfg["out"]) / week
        self.adr = self.dir / "adr"
        self.gate = self.dir / "gate"
        self.feed = self.dir / "feed"
        self.log = self.dir / "steps.log"

    @property
    def meta(self):
        m = load_json(self.dir / "week.json")
        if m is None:
            raise ReplayError(f"{self.dir}/week.json이 없다 — prepare부터")
        return m

    def corpus(self, week=None):
        return Path(self.cfg["snapshots"]) / (week or self.week)

    # -- prepare (무료 · 읽기만)
    def prepare(self):
        adr_repo, snaps = Path(self.cfg["adr_repo"]), Path(self.cfg["snapshots"])
        if not (self.corpus() / "items.jsonl").exists():
            raise ReplayError(f"얼린 코퍼스가 없다: {self.corpus()}")
        draft = _need(_git("rev-parse", self.spec["draft"], cwd=adr_repo), "초안 커밋")
        parent = _need(_git("rev-parse", f"{draft}^", cwd=adr_repo), "초안 부모")
        at = _need(_git("show", "-s", "--format=%cI", draft, cwd=adr_repo), "초안 시각")
        export_tree(adr_repo, parent, self.adr)
        # 대상 레포: 그 시점 ADR repo의 radar-targets.json → 실제 작업 사본(원격 추적 기록) → 그 시각 커밋 사본
        targets = json.loads((self.adr / "radar-targets.json").read_text(encoding="utf-8"))
        # exclude(우리 스택이 아닌 경로 — 팀원 프론트 사본 · eval 픽스처, 10/15~16)는 읽기 규칙 수정이지 스택 상태가 아니다 — 지금 것을 덧입힌다
        now_ex = {t["repo"]: list(t.get("exclude") or []) for t in json.loads(show(adr_repo, "HEAD", "radar-targets.json") or "[]")}
        added_ex = {}
        for t in targets:
            more = [x for x in now_ex.get(t["repo"], []) if x not in (t.get("exclude") or [])]
            if more:
                t["exclude"] = list(t.get("exclude") or []) + more
                added_ex[t["repo"]] = more
        locator = [{**t, "path": str((adr_repo / t["path"]).resolve())} for t in targets]
        write_json(self.dir / "basis" / "locator-targets.json", locator)
        derived, b = basis.materialize(self.dir / "basis" / "locator-targets.json", self.dir / "basis",
                                       tree=dated_tree(at, self.cfg.get("allow_stale")))
        bad = [r for r in b.get("repos") or [] if r.get("source") != "dated"]
        if derived is None or bad or b.get("error"):
            raise ReplayError(f"날짜 기준 basis 실패: {b.get('error') or bad}")
        b["at"] = at
        basis.write(b, self.dir / basis.BASIS)
        shutil.copyfile(derived, self.adr / "radar-targets.json")   # 사본 ADR의 대상 = 날짜 기준 사본 (copy_adr는 절대 경로를 그대로 둔다)
        # 팀원 스택(수집 전용 대상) — 같은 시각. 경로는 이 레포 부모 기준 형제 폴더 = 실제 배치에선 ADR repo의 부모
        co = json.loads((REPO / "collector" / "targets-collect-only.json").read_text(encoding="utf-8"))
        co_basis, tree = [], dated_tree(at, self.cfg.get("allow_stale"))
        for t in co.get("targets") or []:
            root = (adr_repo.parent / t["path"]).resolve()
            if not root.is_dir():
                raise ReplayError(f"수집 전용 대상 폴더가 없다: {root}")
            r = tree(t["repo"], root, self.dir / "basis" / "collect-only")
            t["path"] = r.pop("root")
            co_basis.append(r)
        write_json(self.dir / "inputs" / "collect-only.json", co)
        # stack-extra(외부 모델 · 서비스 몫)는 스택 상태다 — 그 시점 decision-radar 사본의 것
        dr = next((Path(t["path"]) for t in json.loads(Path(derived).read_text(encoding="utf-8")) if t["repo"] == "decision-radar"), None)
        se = dr / "pipeline" / "importance" / "stack-extra.json" if dr else None
        extra = json.loads(se.read_text(encoding="utf-8")) if se and se.exists() else []
        write_json(self.dir / "inputs" / "stack-extra.json", extra)
        # 자리 프로필 — 그 주엔 없었다(adr 97cdf63, 11/4). 사용자 결정 11/11: 켜고 결과에 표시
        slots_rev = _need(_git("log", "-1", "--format=%H", "HEAD", "--", "radar-slots.yaml", cwd=adr_repo), "radar-slots.yaml 커밋")
        (self.dir / "inputs").mkdir(parents=True, exist_ok=True)
        (self.dir / "inputs" / "radar-slots.yaml").write_text(show(adr_repo, slots_rev, "radar-slots.yaml") or "", encoding="utf-8", newline="\n")
        # 등급 파일 — 지금 radar-snapshots tiers.json(커밋된 것)을 그 주 스택으로 자른다. 재분류는 tiers 단계
        tiers_rev = _need(_git("log", "-1", "--format=%H", "HEAD", "--", "tiers.json", cwd=snaps), "tiers.json 커밋")
        pruned, prune = prune_tiers(json.loads(show(snaps, tiers_rev, "tiers.json")), stack_names(derived, extra))
        write_json(self.dir / "tiers.json", pruned)
        # 스킬 쪽 — 그 주 초안 파일과 사람 결정(status가 proposed에서 처음 바뀐 커밋)
        skill = self.skill_side(adr_repo, draft)
        meta = {"week": self.week, "pr": self.spec["pr"], "draft": draft, "adr_parent": parent, "at": at,
                "now": _iso(at).astimezone().isoformat(timespec="seconds"), "next": self.spec["next"], "code": CODE,
                "code_head": _need(_git("rev-parse", "HEAD", cwd=REPO), "코드 HEAD"),
                "corpus": str(self.corpus()), "targets": str(derived), "collect_only": co_basis, "exclude_from_now": added_ex,
                "slots_profile": {"rev": slots_rev, "note": "그 주엔 없던 프로필(11/4 첫 버전) — 사용자 결정 11/11로 켬"},
                "tiers_source": {"rev": tiers_rev, **prune, "technologies": len(pruned["technologies"]), "overrides": len(pruned["overrides"])},
                "stack_extra": str(se) if se and se.exists() else None, "skill": skill, "fetch_links": False,
                "prepared_at": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
        write_json(self.dir / "week.json", meta)
        return f"basis {len(b['repos'])}개 @ {at} · ADR {parent[:7]} · 등급 {len(pruned['technologies'])}개 (뺀 것 {len(prune['removed'])} · 풀린 retired {prune['unretired']}) · 스킬 초안 {len(skill['drafts'])}건"

    def skill_side(self, adr_repo, draft):
        rows = _need(_git("diff-tree", "--no-commit-id", "--name-status", "-r", draft, cwd=adr_repo), "초안 파일")
        files = [ln.split("\t", 1)[1] for ln in rows.splitlines() if ln.startswith("A\t") and ln.split("\t", 1)[1].startswith("docs/adr/")]
        d = self.dir / "skill" / "drafts"
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        decisions = []
        for f in files:
            (d / Path(f).name).write_text(show(adr_repo, draft, f), encoding="utf-8", newline="\n")
            decisions.append({"file": Path(f).name, **human_decision(adr_repo, f, draft)})
        write_json(self.dir / "skill" / "decisions.json", decisions)
        return {"drafts": [Path(f).name for f in files], "decisions": decisions}

    # -- 단계
    def run_step(self, name, args, env_extra, script=False):
        env = child_env(os.environ, env_extra)
        cmd = [sys.executable, *(args if script else ["-m", *args])]
        with open(self.log, "ab") as f:
            f.write(f"\n=== {dt.datetime.now().isoformat(timespec='seconds')} [{name}] {' '.join(cmd[1:])}\n".encode("utf-8"))
            f.flush()
            return subprocess.run(cmd, cwd=str(REPO), env=env, stdout=f, stderr=subprocess.STDOUT).returncode

    def env(self):
        m = self.meta
        return {"RADAR_PROFILE": self.adr / "radar-profile.yaml", "RADAR_TARGETS": m["targets"], "RADAR_ADR_DIR": self.adr / "docs" / "adr",
                "RADAR_COLLECT_ONLY": self.dir / "inputs" / "collect-only.json"}

    def tiers(self, engine, plan=False):
        from pipeline.importance import cli
        m = self.meta
        extra = load_json(self.dir / "inputs" / "stack-extra.json", [])
        rep = cli.classify(self.dir / "tiers.json", TIERS_MODEL, targets=m["targets"], extra=extra, real_api=engine == "real",
                           plan=plan or engine != "real", call_log=self.dir / "tiers-calls.jsonl")
        write_json(self.dir / ("tiers-plan.json" if plan or engine != "real" else "tiers-report.json"), rep)
        if rep.get("failed"):
            raise ReplayError(f"등급 재분류 실패 {len(rep['failed'])}: {[f.get('technology') for f in rep['failed']]}")
        c = rep.get("calls") or {}
        return (f"분류할 기술 {rep['to_classify']} / {rep['technologies']}" + (f" · 호출 {c.get('model_calls')} · ${c.get('cost_usd')}" if c else " (호출 안 함)"))

    def gate_run(self, engine, out=None):
        out = Path(out or self.gate)
        if out.exists():
            shutil.rmtree(out)      # 관문은 다시 돌면 새로 — 남은 slots · basis가 섞이지 않게 (원자료는 이전 run.json이 아니라 이번 것)
        args = ["pipeline.profile_filter", "run", "--snapshot", str(self.corpus()), "--gate", "stub" if engine != "real" else GATE_MODEL,
                "--tiers", str(self.dir / "tiers.json"), "--profile", str(self.adr / "radar-profile.yaml"), "--targets", self.meta["targets"],
                "--adr-dir", str(self.adr / "docs" / "adr"), "--out", str(out)] + (["--real-api"] if engine == "real" else [])
        rc = self.run_step("gate", args, self.env())
        if rc != 0:
            raise ReplayError(f"관문 실패 (종료 코드 {rc}) — {self.log}")
        shutil.copyfile(self.dir / basis.BASIS, out / basis.BASIS)
        lane = load_json(out / "lane_a.json", [])
        calls = sum(1 for e in lane if (e.get("gate") or {}).get("status") in ("ok", "error"))
        s = (load_json(out / "run.json", {}).get("lane_a_gate") or {})
        return {"lane_a": len(lane), "model_calls": calls, "kept": s.get("total_kept"), "label": s.get("label"),
                "cost_usd": (s.get("calls") or {}).get("cost_usd")}

    def slots_run(self, engine):
        args = ["pipeline.slots", "run", "--snapshot", str(self.corpus()), "--slots", str(self.dir / "inputs" / "radar-slots.yaml"),
                "--out", str(self.gate), "--engine", "claude" if engine == "real" else "stub"]
        rc = self.run_step("slots", args, self.env())
        if rc != 0:     # 주간 사슬처럼 계속 — detect 입력은 관문 몫만 (weekly.py slots). 기록에 남는다
            return f"실패 (종료 코드 {rc}) — 계속, detect 입력은 관문 몫만"
        s = load_json(self.gate / "slots.json", {})
        return f"후보 {s.get('candidates')} → 출시 {s.get('releases')} → detect {len(s.get('to_detect') or [])}"

    def feed_run(self):
        if self.feed.exists():
            shutil.rmtree(self.feed)
        rc = self.run_step("feed", ["pipeline.detect_feed", "--run", str(self.gate), "--out", str(self.feed), "--no-fetch"], self.env())
        if rc != 0 or not (self.feed / "feed.json").exists():
            raise ReplayError(f"detect 입력 실패 (종료 코드 {rc}) — {self.log}")
        from pipeline.detect_feed import funnel_line
        return funnel_line(load_json(self.feed / "funnel.json", {}))

    def detect_runs(self, engine, k):
        m, lines = self.meta, []
        for i in range(1, k + 1):
            out = self.dir / "detect" / f"k{i}"
            if (out / "summary.json").exists():
                lines.append(f"k{i} 이미 있음")
                continue
            if out.exists():        # 끝나지 않은 실행 — 원자료는 지우지 않고 옆으로
                out.rename(out.with_name(f"k{i}.failed-{int(time.time())}"))
            args = [str(REPO / "product" / "radar" / "run_detect.py"), "--snapshot", str(self.feed), "--adr", str(self.adr),
                    "--out", str(out), "--model", "claude-cli" if engine == "real" else "stub", "--now", m["now"]]
            rc = self.run_step(f"detect k{i}", args, {**self.env(), "RADAR_BASIS_NOW": self.dir / basis.BASIS}, script=True)
            s = load_json(out / "summary.json", {})
            lines.append(f"k{i} 종료 코드 {rc} · 초안 {len(s.get('drafts') or [])}" + (f" · 오류 {len(s['p3_errors'])}" if s.get("p3_errors") else ""))
        return " · ".join(lines)


def human_decision(adr_repo, path, since):
    """초안 파일의 사람 결정 — since 이후 그 파일의 status가 proposed에서 처음 바뀐 커밋. 아직 proposed면 decided None."""
    ok, log = _git("log", "--reverse", "--format=%H %cI", f"{since}..HEAD", "--", path, cwd=adr_repo)
    for ln in (log.splitlines() if ok else []):
        sha, when = ln.split(" ", 1)
        txt = show(adr_repo, sha, path)
        m = re.search(r"^status:\s*(\S+)", txt or "", re.M)
        if txt is None:
            return {"status": "deleted", "decided_at": when, "commit": sha}
        if m and m.group(1) != "proposed":
            return {"status": m.group(1), "decided_at": when, "commit": sha}
    return {"status": "proposed", "decided_at": None, "commit": None}


# ---------------------------------------------------------------------------- 대조

END_RANK = {"detect": 3, "list": 2, "out": 1, "unknown": 0}


def cand_key(c, known, feed_by_url=None):
    """초안 하나 → 같은 항목 키 (skill_line.item_key — 정규화 패키지 + 주 버전, 0.x는 둘째 자리). 제품 초안은 근거 URL이 가리키는
    detect 입력 항목의 package · version이 먼저, 그다음은 스킬 줄과 같은 길(union.resolve_package · 제목/use의 버전 · 근거 URL의 버전)."""
    for u in c.get("urls") or []:
        e = (feed_by_url or {}).get(union.url_key(u))
        if e and e.get("package") and e.get("version"):
            return skill_line.item_key(e["package"], e["version"])
    ver = c.get("version") or skill_line.version_of(*(c.get("urls") or []))
    return (skill_line.item_key(union.resolve_package(c, known), ver, union.primary_url(c))
            or f"tech:{norm(c.get('technology') or '?')}")   # 패키지 · 버전 · 근거 URL이 다 없으면 기술 이름 — 서로 다른 초안이 None 하나로 뭉치지 않게


def run_outcome(report, urls):
    """detect 실행 하나에서 그 항목(urls)이 어떻게 됐나 → (draft|excluded|dropped|skipped_existing|absent, 이유)."""
    keys = {union.url_key(u) for u in urls}
    hit = lambda us: bool(keys & {union.url_key(u) for u in us if u})  # noqa: E731
    for c in (report or {}).get("candidates") or []:
        if hit(c.get("source_urls") or []):
            return "draft", c.get("technology")
    for kind in ("skipped_existing", "excluded", "dropped"):
        for x in (report or {}).get(kind) or []:
            if hit([x.get("url")] + list(x.get("source_urls") or [])):
                return kind, x.get("reason") or x.get("adr") or ""
    return "absent", ""


def attribute(key, week_items, next_items, trace_by_id, reports):
    """스킬만 낸 항목 → 갈린 단계 하나 (등록 §7): 출처에 없음 · 수집 시점 · 관문 탈락 · 상한에서 잘림 · 모델이 뺌 · 이미 결정됨."""
    hits = [it for it in week_items if union.corpus_key(it) == key]
    if not hits:
        nxt = [it for it in next_items if union.corpus_key(it) == key]
        if nxt:
            return {"stage": STAGE["timing"], "detail": "그 주 코퍼스엔 없고 다음 주 코퍼스에 있다: " + "; ".join(it["id"] for it in nxt[:3])}
        return {"stage": STAGE["source"], "detail": "그 주 · 다음 주 코퍼스 어디에도 없다"}
    recs = [trace_by_id[it["id"]] for it in hits if it["id"] in trace_by_id]
    if not recs:
        return {"stage": STAGE["gate"], "detail": "코퍼스엔 있으나 탈락 추적 기록이 없다: " + ", ".join(it["id"] for it in hits[:3])}
    best = max(recs, key=lambda r: END_RANK.get(r["end"], 0))
    ids = ", ".join(r["id"] for r in recs[:4])
    if best["end"] == "detect":
        outs = [run_outcome(rep, [it.get("url") for it in hits]) for rep in reports]
        miss = [o for o in outs if o[0] != "draft"]
        decided = sum(1 for o in miss if o[0] == "skipped_existing")
        detail = f"detect 입력({best['id']}) → 실행별: " + " · ".join(f"{o[0]}{(' — ' + str(o[1])[:120]) if o[1] else ''}" for o in outs)
        return {"stage": STAGE["decided"] if miss and decided * 2 > len(miss) else STAGE["model"], "detail": detail}
    stop = (best.get("stops") or [{}])[0]
    stage = STAGE["cap"] if stop.get("stage") == "3" else STAGE["gate"]
    return {"stage": stage, "detail": f"{best['end']} — {stop.get('stage', '?')} {stop.get('name', '')}: {str(stop.get('note', ''))[:200]} ({ids})"}


def route(feed_entry, gate_why):
    """제품 항목이 detect 입력에 온 길 — 11/3 뒤 생긴 길(자리 질의 · 경보 그물 · changelog 쪼개기)은 「고친 뒤 경로」로 표시한다 (§7 변경 11/11)."""
    if not feed_entry:
        return "?"
    if feed_entry.get("kind") == "model_release":
        return "자리 질의 (11/4 · 고친 뒤 경로)"
    if feed_entry.get("kind") == "security_alert" or (gate_why or {}).get("alarm"):
        return "경보 그물 (11/4 · 고친 뒤 경로)"
    if "#" in (feed_entry.get("id") or ""):
        return "관문 · changelog 날짜 블록 (11/4 · 고친 뒤 경로)"
    return "관문"


def compare_week(w, k=K):
    from pipeline.profile_filter import read_items
    m = w.meta
    items = read_items(w.corpus())
    nxt_dir = w.corpus(m["next"])
    next_items = read_items(nxt_dir) if (nxt_dir / "items.jsonl").exists() else []
    known = union.known_packages(items + next_items)
    feed = load_json(w.feed / "feed.json", [])
    feed_by_url = {union.url_key(e["url"]): e for e in feed}
    why = load_json(w.feed / "gate.json", {})
    trace_by_id = {r["id"]: r for r in (trace.read(w.feed / trace.TRACE, items) if (w.feed / trace.TRACE).exists() else [])}
    # 스킬
    sdir = w.dir / "skill" / "drafts"
    decisions = {d["file"]: d for d in load_json(w.dir / "skill" / "decisions.json", [])}
    skill = {}
    for c in skill_line.extract_candidates(None, str(sdir), sorted(p.name for p in sdir.glob("*.md")))["drafts"]:
        key = cand_key(c, known)
        skill[key] = {"key": key, "technology": c["technology"], "version": c.get("version"), "adr": c["adr"],
                      "decision": decisions.get(c["file"], {}), "urls": c["urls"]}
    # 제품 — 실행마다
    runs, reports, product = [], [], {}
    for i in range(1, k + 1):
        d = w.dir / "detect" / f"k{i}"
        s, rep = load_json(d / "summary.json"), load_json(d / "report.json")
        ok = s is not None and not s.get("p3_errors")
        runs.append({"k": i, "ok": ok, "missing": s is None, "drafts": len((s or {}).get("drafts") or []),
                     "errors": [e.get("kind") for e in (s or {}).get("p3_errors") or []], "usage": (s or {}).get("usage")})
        reports.append(rep or {})
        if s is None:
            continue
        files = [x["file"] for x in s.get("drafts") or [] if x.get("file")]
        for c in skill_line.extract_candidates(rep, str(d / "adr" / "docs" / "adr"), files)["drafts"]:
            key = cand_key(c, known, feed_by_url)
            p = product.setdefault(key, {"key": key, "technology": c["technology"], "version": c.get("version"), "runs": [], "urls": []})
            if i not in p["runs"]:
                p["runs"].append(i)
            p["urls"] = list(dict.fromkeys(p["urls"] + c["urls"]))
    for p in product.values():
        fe = next((feed_by_url.get(union.url_key(u)) for u in p["urls"] if union.url_key(u) in feed_by_url), None)
        p["n"] = len(p["runs"])
        p["route"] = route(fe, why.get(fe["url"]) if fe else None)
        p["title"] = (fe or {}).get("title")
    made = {kk for kk, p in product.items() if p["n"] >= MIN_RUNS}
    both = sorted(set(skill) & made)
    skill_only = sorted(set(skill) - made)
    product_only = sorted(made - set(skill))
    out = {"week": w.week, "at": m["at"], "now": m["now"], "pr": m["pr"], "runs": runs,
           "both": [{**skill[x], "product_runs": product[x]["runs"], "route": product[x]["route"]} for x in both],
           "skill_only": [{**skill[x], "product_runs": product.get(x, {}).get("runs", []),
                           **attribute(x, items, next_items, trace_by_id, reports)} for x in skill_only],
           "product_only": [product[x] for x in product_only],
           "product_once": [product[x] for x in sorted(set(product) - made)],   # 1번만 낸 것 — 「냄」은 아니지만 기록한다
           "funnel": load_json(w.feed / "funnel.json"), "notes": {"slots_profile": m["slots_profile"]["note"], "fetch_links": m["fetch_links"]}}
    if WEEKS[w.week].get("frozen_detect"):
        frozen = load_json(Path(w.cfg["snapshots"]) / WEEKS[w.week]["frozen_detect"] / "feed.json", [])
        out["frozen_reference"] = [{"title": e.get("title"), "package": e.get("package"), "version": e.get("version"),
                                    "in_replay_feed": union.url_key(e["url"]) in feed_by_url} for e in frozen]
    return out


def render(res):
    """compare 결과 → 마크다운 (보고서 docs/decisions/replay-W41-W43.md에 옮긴다)."""
    L = []
    tot = {"skill": 0, "product": 0, "both": 0}
    for r in res["weeks"]:
        s, p, b = len(r["both"]) + len(r["skill_only"]), len(r["both"]) + len(r["product_only"]), len(r["both"])
        tot["skill"] += s
        tot["product"] += p
        tot["both"] += b
        ok = sum(1 for x in r["runs"] if x["ok"])
        L.append(f"### {r['week']} (스킬 실행 {r['now'][:16].replace('T', ' ')} · adr PR #{r['pr']}) — 둘 다 {b} · 스킬만 {len(r['skill_only'])} · 제품만 {len(r['product_only'])}")
        L.append(f"제품 detect {ok}/{len(r['runs'])}번 정상" + (" — " + "; ".join(f"k{x['k']} {','.join(x['errors']) or ('없음' if x['missing'] else '')}" for x in r["runs"] if not x["ok"]) if ok < len(r["runs"]) else ""))
        L.append("")
        L.append("| 구분 | 항목 | 스킬 초안 · 사람 결정 | 제품 (냄/K) | 갈린 단계 · 경로 |")
        L.append("|---|---|---|---|---|")
        for x in r["both"]:
            L.append(f"| 둘 다 | {x['technology']} {x.get('version') or ''} | {x['adr']} · {x['decision'].get('status')} | {len(x['product_runs'])}/{len(r['runs'])} | {x['route']} |")
        for x in r["skill_only"]:
            L.append(f"| 스킬만 | {x['technology']} {x.get('version') or ''} | {x['adr']} · {x['decision'].get('status')} | {len(x['product_runs'])}/{len(r['runs'])} | **{x['stage']}** — {x['detail']} |")
        for x in r["product_only"]:
            L.append(f"| 제품만 | {x['technology']} {x.get('version') or ''} | — | {x['n']}/{len(r['runs'])} | {x['route']} · {x.get('title') or ''} |")
        for x in r["product_once"]:
            L.append(f"| (1번만) | {x['technology']} {x.get('version') or ''} | — | {x['n']}/{len(r['runs'])} | {x['route']} |")
        if r.get("frozen_reference") is not None:
            L.append("")
            L.append("얼려 둔 W43 관문(10/20 코드) detect 입력 — 참고: " + "; ".join(
                f"{e['title']}{' (재생 입력에도)' if e['in_replay_feed'] else ' (재생 입력엔 없음)'}" for e in r["frozen_reference"]))
        L.append("")
    L.insert(0, f"합계 — 스킬 {tot['skill']} · 제품 {tot['product']} · 겹침 {tot['both']} · 합집합 {tot['skill'] + tot['product'] - tot['both']} (관찰, 문턱 없음)\n")
    return "\n".join(L)


# ---------------------------------------------------------------------------- 명령

def config(a, env=None):
    env = os.environ if env is None else env
    adr = a.adr_repo or env.get("RADAR_ADR_REPO") or str(REPO.parent / "adr")
    snaps = a.snapshots or env.get("RADAR_SNAPSHOT_REPO") or str(REPO.parent / "radar-snapshots")
    for p, what in ((adr, "ADR repo (--adr-repo · RADAR_ADR_REPO)"), (snaps, "radar-snapshots (--snapshots · RADAR_SNAPSHOT_REPO)")):
        if not (Path(p) / ".git").exists():
            raise ReplayError(f"{what}가 git 사본이 아니다: {p}")
    return {"out": str(Path(a.out).resolve()), "adr_repo": str(Path(adr).resolve()), "snapshots": str(Path(snaps).resolve()),
            "allow_stale": getattr(a, "allow_stale", False)}


def guard_for(cfg):
    """보호 대상 — ADR repo · radar-snapshots(상태 비교) · 데이터 폴더(행동 로그) + 대상 레포 · 수집 전용 대상 작업 사본(출력 금지만)."""
    adr = Path(cfg["adr_repo"])
    fence = []
    try:
        fence += [(adr / t["path"]).resolve() for t in json.loads((adr / "radar-targets.json").read_text(encoding="utf-8"))]
    except (OSError, ValueError):
        pass
    co = json.loads((REPO / "collector" / "targets-collect-only.json").read_text(encoding="utf-8"))
    fence += [(adr.parent / t["path"]).resolve() for t in co.get("targets") or []]
    g = Guard([adr, Path(cfg["snapshots"])], default_data_dirs(), fence)
    g.check_out(cfg["out"])
    return g


def _weeks(a):
    return [w.strip() for w in (a.weeks or ",".join(WEEKS)).split(",") if w.strip()]


def cmd_plan(a):
    cfg = config(a)
    g = guard_for(cfg)
    total = {"gate_calls": 0, "tiers_calls": 0}
    rows = []
    for wk in _weeks(a):
        w = Week(wk, cfg)
        print(f"[{wk}] prepare: {w.prepare()}")
        g.check(f"{wk} prepare 뒤")
        print(f"[{wk}] tiers (계획): {w.tiers('stub', plan=True)}")
        t = load_json(w.dir / "tiers-plan.json", {})
        gs = w.gate_run("stub", out=w.dir / "plan-gate")      # 스텁 관문 — 모델을 부를 항목 수만 센다
        g.check(f"{wk} 스텁 관문 뒤")
        rows.append({"week": wk, "tiers_calls": t.get("to_classify", 0), "gate_calls": gs["model_calls"], "lane_a": gs["lane_a"]})
        total["gate_calls"] += gs["model_calls"]
        total["tiers_calls"] += t.get("to_classify", 0)
        print(f"[{wk}] 관문 모델 호출 {gs['model_calls']}회 (레인 A + 경보 그물 {gs['lane_a']}건 중) · 등급 재분류 {t.get('to_classify', 0)}회")
    n = len(rows)
    plan = {"weeks": rows, **total, "gate_usd": round(total["gate_calls"] * GATE_USD_PER_CALL, 2),
            "tiers_usd": round(total["tiers_calls"] * TIERS_USD_PER_CALL, 2), "slots_sessions": n, "detect_runs": n * a.k,
            "note": "detect 실행 1번 = claude -p triage 1회 + 초안마다 1회(+ validate 실패면 고쳐 쓰기 1회). 구독 — API 과금 없음"}
    write_json(Path(cfg["out"]) / "plan.json", plan)
    print(f"\n유료 API: 관문 {GATE_MODEL} {total['gate_calls']}회 ≈ ${plan['gate_usd']} · 등급 {TIERS_MODEL} {total['tiers_calls']}회 ≈ ${plan['tiers_usd']}")
    print(f"구독 claude -p: 자리 질의 {n}세션 + detect {n * a.k}번 실행 (triage {n * a.k}회 + 초안 수만큼)")
    return 0


def cmd_run(a):
    cfg = config(a)
    if a.engine == "real" and not a.confirm_real:
        raise ReplayError("--engine real은 유료 API · 구독 세션을 쓴다 — 사용자 승인 뒤 --confirm-real을 같이 준다")
    drift = code_drift()
    if drift and a.engine == "real":
        raise ReplayError(f"제품 코드가 고정 커밋 {CODE}과 다르다 — 등록 위반 (§7 변경 2026-11-11): {drift[:8]}")
    g = guard_for(cfg)
    start = STEPS.index(a.start)
    manifest_path = Path(cfg["out"]) / "replay.json"
    manifest = load_json(manifest_path, {"runs": []})
    rec = {"started": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "engine": a.engine, "k": a.k, "from": a.start,
           "weeks": {}, "code": CODE, "code_head": _need(_git("rev-parse", "HEAD", cwd=REPO), "코드 HEAD"), "code_drift": drift}
    manifest["runs"].append(rec)
    try:
        for wk in _weeks(a):
            w = Week(wk, cfg)
            steps = rec["weeks"].setdefault(wk, [])
            for name in STEPS[start:]:
                t0 = time.time()
                if name == "prepare":
                    detail = w.prepare()
                elif name == "tiers":
                    detail = w.tiers(a.engine)
                elif name == "gate":
                    detail = w.gate_run(a.engine)
                elif name == "slots":
                    detail = w.slots_run(a.engine)
                elif name == "feed":
                    detail = w.feed_run()
                else:
                    detail = w.detect_runs(a.engine, a.k)
                g.check(f"{wk} {name} 뒤")
                steps.append({"step": name, "seconds": round(time.time() - t0, 1), "detail": detail})
                print(f"[{wk}] {name}: {detail}")
                write_json(manifest_path, manifest)
    finally:
        rec["finished"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        write_json(manifest_path, manifest)
    return 0


def cmd_compare(a):
    cfg = config(a)
    res = {"code": CODE, "k": a.k, "min_runs": MIN_RUNS, "weeks": [compare_week(Week(wk, cfg), a.k) for wk in _weeks(a)]}
    write_json(Path(cfg["out"]) / "compare.json", res)
    md = render(res)
    (Path(cfg["out"]) / "compare.md").write_text(md, encoding="utf-8", newline="\n")
    print(md)
    return 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="pipeline.replay", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "run", "compare"):
        p = sub.add_parser(name)
        p.add_argument("--out", required=True, help="재생 폴더 (실제 repo · 데이터 폴더 밖 — 예: pipeline/out/replay-W41-W43)")
        p.add_argument("--adr-repo", help="ADR repo 작업 사본 (읽기만, 기본 env RADAR_ADR_REPO · <부모>/adr)")
        p.add_argument("--snapshots", help="radar-snapshots 작업 사본 (읽기만, 기본 env RADAR_SNAPSHOT_REPO · <부모>/radar-snapshots)")
        p.add_argument("--weeks", help=f"쉼표로 (기본 {','.join(WEEKS)})")
        p.add_argument("--k", type=int, default=K)
        p.add_argument("--allow-stale", action="store_true", help="원격 추적 기록이 그 시각을 못 덮어도 돈다 (기록에 fresh: false)")
        if name == "run":
            p.add_argument("--engine", choices=["stub", "real"], required=True)
            p.add_argument("--confirm-real", action="store_true", help="유료 API · 구독 세션 사용을 사용자가 승인했다")
            p.add_argument("--from", dest="start", choices=STEPS, default="prepare")
    a = ap.parse_args(argv)
    try:
        return {"plan": cmd_plan, "run": cmd_run, "compare": cmd_compare}[a.cmd](a)
    except ReplayError as e:
        print(f"멈춤: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
