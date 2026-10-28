"""분류기 CLI.

  python -m pipeline.importance classify-stack --targets [radar-targets.json] [--repo <폴더> ...] [--extra stack-extra.json] --tiers tiers.json --model stub
      내 대상 전부(각자의 exclude 포함)의 manifest·Dockerfile·compose에서 기술을 뽑아 등급을 매겨 저장한다 (스택이 바뀐 기술만). 실제 모델은 --real-api가 있어야 한다.
      읽는 파일은 수집기와 같다 — git 추적 파일만 (collector/stack.py)
  python -m pipeline.importance weekly-tiers [--plan] [--report collector/out/last-tiers.json]
      주간 예약(freeze 뒤, HYO-122 PR 3): 내 대상 전부 + stack-extra.json → 고정 등급 파일(<부모>/radar-snapshots/tiers.json)에 이어 쓴다.
      실제 모델(claude-sonnet-5-5)로 지문 바뀐 기술만 부르고, 키가 없으면 「미분류 N」을 남기고 건너뛴다. 분류한 게 있으면 그 파일만 커밋·push
  python -m pipeline.importance stack-diff --tiers tiers.json [--targets [radar-targets.json]]
      수집기가 보는 이름과 등급 파일이 아는 이름을 견준다 (수집기만 · 관문만 · 양쪽)
  python -m pipeline.importance override --tiers tiers.json --tech PyYAML --set-tiers L6 [--sensitive] [--local] [--path dev] [--set-version 6.0]
      사람이 고친 값 — 자동 분류보다 우선하고, 다시 분류해도 지워지지 않는다. --set-version은 manifest가 없는 기술의 우리 버전 (HYO-148)
  python -m pipeline.importance show --tiers tiers.json
  python -m pipeline.importance evaluate ...       (evaluate.py 참고)

레인 A에 붙이기: `python -m pipeline.profile_filter run ... --gate stub --tiers tiers.json`
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

from .backends import TechQuery, make_backend
from .signals import NPM_DEV, norm, read_repo_signals
from .tiers import NOT_IMPLEMENTED, TierStore


REPO = Path(__file__).resolve().parents[2]


def load_targets(path=None):
    """radar-targets.json(내 스택) → ([(repo 이름, 루트, exclude)], 폴더 없는 [(repo 이름, 루트)], 파일 경로).
    경로 해석은 collector/run.py와 같다: 인자 > env RADAR_TARGETS > `<이 레포 부모>/adr/radar-targets.json`."""
    p = Path(path or os.environ.get("RADAR_TARGETS") or (REPO.parent / "adr" / "radar-targets.json"))
    if not p.exists():
        raise SystemExit(f"대상 목록이 없다: {p}")
    found, missing = [], []
    for t in json.loads(p.read_text(encoding="utf-8")):
        root = (p.parent / t["path"]).resolve()
        if root.is_dir():
            found.append((t["repo"], root, list(t.get("exclude") or [])))
        else:
            missing.append((t["repo"], root))
    return found, missing, p


def claude_md_rows(root):
    """대상 repo 루트 CLAUDE.md 「의존성 → component」 표의 행 (수집기와 같은 파서 · 같은 파일). 없으면 []."""
    from collector.sources import github
    md = Path(root) / "CLAUDE.md"
    return github.dependency_rows_from_claude_md(md.read_text(encoding="utf-8")) if md.exists() else []


def queries_from_repos(repos, extra=None):
    """repos: 폴더 경로 · (경로, exclude) · (경로, exclude, repo 이름).

    매니페스트 신호 + 대상 repo CLAUDE.md 「의존성 → component」 표 (HYO-122 ③ ④):
    - 매니페스트에 있는 이름은 표의 행(첫 칸 · component)을 usage에 덧붙인다 — 매니페스트 줄만으로는 쓰임새를 몰라 분류 불가가 나던 것 (langfuse 등)
    - 매니페스트에 없는 표의 이름(llama.cpp · litellm(별도 venv) · vLLM …)은 그 행으로 질의를 만든다 (신호 없음 — 모델이 정한다).
      수집기도 같은 표를 읽으니 「수집기와 관문의 스택이 같다」가 구조로 지켜진다. npm 행은 쓰임새만 주고 질의는 만들지 않는다 — package.json 신호가 있다
    한 이름이 여러 repo에 있으면 신호는 처음 것, usage에는 repo마다의 행을 모두 붙인다."""
    qs, notes, table_only = [], {}, []
    for repo in repos:
        repo, exclude, label = (tuple(repo) + (None,))[:3] if isinstance(repo, tuple) else (repo, (), None)
        label = label or Path(repo).name
        for row in claude_md_rows(repo):
            for n in row["names"]:
                note = f"{label} CLAUDE.md 「의존성 → component」: {row['cell']} → {row['component']}"
                if note not in notes.setdefault(norm(n), []):
                    notes[norm(n)].append(note)
                if not row["npm"]:                       # npm 행은 쓰임새만 준다 — 질의는 package.json 신호로
                    table_only.append(TechQuery(name=n, usage="", source=f"{label}/CLAUDE.md", signals={}))
        for key, s in read_repo_signals(repo, exclude).items():
            if s["image"]:
                usage, source = f"컨테이너 이미지 ({s['image_ref']})", s["image_ref"]
            elif s["group"] == NPM_DEV:      # 개발 도구일 수도, 번들돼 운영에 들어갈 수도 있다 (⑩) — 모델이 정하도록 그대로 알린다
                usage, source = "package.json devDependencies (개발 도구일 수도, 앱에 번들돼 운영 코드에 들어갈 수도 있다)", s["manifest"]
            else:
                usage, source = f"{s['manifest']} 직접 의존성 ({s['group']})", s["manifest"]
            qs.append(TechQuery(name=s["name"], usage=usage, source=source, signals={k: s[k] for k in ("manifest", "group", "pin", "image")}))
    qs += table_only                                     # 매니페스트 뒤 — 같은 이름이면 매니페스트 신호가 이긴다
    for e in extra or []:
        qs.append(TechQuery(name=e["name"], usage=e.get("usage", ""), source=e.get("source", ""), signals=e.get("signals") or {}))
    seen, out = set(), []
    for q in qs:
        if norm(q.name) in seen:
            continue
        seen.add(norm(q.name))
        rows = notes.get(norm(q.name))
        if rows:
            q = TechQuery(name=q.name, usage=" · ".join(([q.usage] if q.usage else []) + rows), source=q.source, signals=q.signals)
        out.append(q)
    return out


def load_vendor_terms(path=None):
    """collector/vendors.yaml의 벤더 → match 단어. 없으면 {}."""
    import yaml
    p = Path(path) if path else Path(__file__).resolve().parents[2] / "collector" / "vendors.yaml"
    if not p.exists():
        return {}
    d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {v: list((spec or {}).get("match") or [v]) for v, spec in (d.get("vendors") or {}).items()}


def build_gate(model, tiers_path, *, real_api=False, call_log=None, limit=None, backend=None, other_stack=None, targets=None):
    """profile_filter가 부르는 자리. 등급 파일이 없으면 거부한다 (등급 없이는 판정할 수 없다 — classify-stack을 먼저).
    other_stack: 팀원 스택 이름 (None이면 수집 전용 대상에서 읽는다 — other_stack_names). 어디서 읽었나는 gate.other_info."""
    from .backends import CallLog
    from .gate import Gate
    from . import policy
    if not Path(tiers_path).exists():
        raise SystemExit(f"등급 파일이 없다: {tiers_path} — `python -m pipeline.importance classify-stack`을 먼저 돌린다")
    store = TierStore(tiers_path)
    if other_stack is None:
        other_stack, info = other_stack_names(targets, store.names() + [o.get("technology", k) for k, o in store.data["overrides"].items()])
    else:
        info = {"given": True}
    backend = backend or make_backend(model, allow_real=real_api, log=CallLog(call_log))
    versions, vinfo = stack_versions(targets)
    gate = Gate(backend, store, load_vendor_terms(), limit or policy.WEEKLY_LIMIT, other_stack=other_stack, versions=versions)
    gate.other_info = {**info, "names": len(other_stack)}
    gate.version_info = vinfo
    return gate, backend


def stack_versions(targets_path=None):
    """내 대상 전부의 manifest → 우리 버전 {정규화 이름: [{name, manifest, spec, version, repo}]} (HYO-148). 등급 파일에 넣지 않고 매번 읽는다.
    → (versions, info {"file", "technologies", "missing", "error"?}). 대상을 못 읽으면 빈 버전 + error — eol은 확인 불가로 남는다 (조용히 넘기지 않는다)."""
    from .signals import read_repo_versions
    try:
        found, missing, path = load_targets(targets_path)
    except SystemExit as e:
        return {}, {"error": f"대상 목록을 못 읽어 우리 버전을 모른다: {e}"}
    out = {}
    for name, root, ex in found:
        read_repo_versions(root, ex, name, out)
    return out, {"file": str(path), "technologies": len(out), "missing": [n for n, _ in missing]}


def classify(tiers, model, *, repos=(), targets=None, extra=None, real_api=False, refresh=False, plan=False, call_log=None,
             skip_if_no_key=False, backend=None):
    """등급 파일을 스택에 맞춘다 → 보고서 dict. 한 기술이 실패해도 나머지는 돈다(failed). 경계 위반은 멈춘다.
    보고서: 기술 수 · 새 기술 · 지문 바뀜 · 사라진 기술(등급 파일엔 있는데 스택에 없음 — 지우지 않고 알린다) · 분류 · 실패 · 호출 · 시간.
    skip_if_no_key: 키가 없으면 분류를 건너뛰고 skipped="no-key"로 미분류 수를 남긴다 (주간 예약 — 조용히 빠지지 않게)."""
    from .backends import CallLog
    from .signals import norm
    t0 = time.perf_counter()
    rep = {"model": model, "tiers": str(tiers), "targets": None, "missing_targets": []}
    repos = list(repos)
    if targets:
        found, missing, path = load_targets(None if targets is True else targets)
        rep["targets"], rep["missing_targets"] = str(path), [{"repo": n, "path": str(r)} for n, r in missing]   # 조용히 빼지 않는다
        repos += [(root, ex, name) for name, root, ex in found]
    qs = queries_from_repos(repos, extra or [])
    store = TierStore(tiers)
    todo = [q for q in qs if not store.human_tiers(q.name) and (refresh or store.needs_classify(q))]
    current = {norm(q.name) for q in qs}
    rep.update({"technologies": len(qs), "new": [q.name for q in todo if store.get(q.name) is None],
                "changed": [q.name for q in todo if store.get(q.name) is not None],
                "removed": sorted(r["technology"] for k, r in store.data["technologies"].items() if k not in current),
                "to_classify": len(todo), "classified": [], "failed": [], "skipped": None, "calls": None})
    if plan or not todo:
        rep["seconds"] = round(time.perf_counter() - t0, 1)
        return rep
    if backend is None:
        try:
            backend = make_backend(model, allow_real=real_api, log=CallLog(call_log))
        except SystemExit as e:                      # 키 파일에 키가 없다
            if not skip_if_no_key:
                raise
            rep.update({"skipped": f"no-key: {e}", "seconds": round(time.perf_counter() - t0, 1)})
            return rep
    try:
        rep["classified"] = store.ensure(qs, backend, refresh=refresh, failures=rep["failed"])
        store.save()
    finally:
        backend.close()
    recs = store.data["technologies"].values()
    rep.update({"unresolved": sorted(r["technology"] for r in recs if r["unresolved"]),
                "script_overrode_model": sorted(r["technology"] for r in recs if r.get("mismatch")),
                "calls": backend.log.totals(), "seconds": round(time.perf_counter() - t0, 1)})
    return rep


def report_lines(rep):
    """보고서 → 사람이 읽는 줄들 (주간 로그 · 콘솔)."""
    out = [f"대상 폴더 없음: {m['repo']} -> {m['path']}" for m in rep["missing_targets"]]
    out.append(f"기술 {rep['technologies']}개 중 새로 분류할 것 {rep['to_classify']}개 (새 기술 {len(rep['new'])} · 지문 바뀜 {len(rep['changed'])}) · "
               f"사라진 기술 {len(rep['removed'])}" + (f" {rep['removed']}" if rep["removed"] else ""))
    if rep["skipped"]:
        out.append(f"미분류 {rep['to_classify']}개 — 분류 건너뜀 ({rep['skipped']})")
    elif rep["calls"] is not None:
        c = rep["calls"]
        out.append(f"분류 {len(rep['classified'])}개 · 실패 {len(rep['failed'])}" + (f" {[f['technology'] for f in rep['failed']]}" if rep["failed"] else "")
                   + f" · 분류 불가 {len(rep['unresolved'])} {rep['unresolved']} · 스크립트가 모델을 덮어쓴 것 {len(rep['script_overrode_model'])}")
        out.append(f"모델 {rep['model']} · 호출 {c['model_calls']}회 · 오류 {c['errors']} · 토큰 {c['input_tokens']}/{c['output_tokens']} · "
                   f"비용 ${c['cost_usd']}" + (f" (단가 모름 {c['unpriced_calls']}회)" if c["unpriced_calls"] else "") + f" · {rep['seconds']}초")
    return out


def cmd_classify(a):
    extra = json.loads(Path(a.extra).read_text(encoding="utf-8")) if a.extra else []
    rep = classify(a.tiers, a.model, repos=a.repo or [], targets=a.targets, extra=extra, real_api=a.real_api, refresh=a.refresh,
                   plan=a.plan, call_log=a.call_log)
    for line in report_lines(rep):
        print(line)
    if rep["targets"]:
        print(f"대상 목록: {rep['targets']}")
    if not a.plan:
        print(f"-> {a.tiers}")
    return 1 if rep["failed"] else 0


# 주간 예약이 쓰는 고정 등급 파일 (HYO-122 PR 3) — 주마다 새로 만들지 않고 이어 쓴다(사람 overrides가 남게). 수집 스냅숏 repo(private,
# 매주 push)에 둔다: 기계가 쓰는 상태이고 기기를 옮겨도 따라온다. RADAR_TIERS로 바꿀 수 있다
WEEKLY_MODEL = "claude-sonnet-5-5"     # 재분류 모델 (사용자 2026-10-15: API · Sonnet 5.5)
STACK_EXTRA = Path(__file__).resolve().parent / "stack-extra.json"


def default_tiers_path():
    return Path(os.environ.get("RADAR_TIERS") or (REPO.parent / "radar-snapshots" / "tiers.json"))


def push_tiers(path, message):
    """등급 파일이 git 사본 안에 있으면 그 파일만 커밋·push. → 결과 문자열 (실패해도 예외 없이 — 분류 결과는 이미 파일에 있다)."""
    import subprocess
    d = Path(path).resolve().parent

    def git(*args):
        return subprocess.run(["git", "-C", str(d), *args], capture_output=True, text=True, encoding="utf-8")
    if git("rev-parse", "--is-inside-work-tree").returncode != 0:
        return "push 안 함: git 사본이 아니다"
    if not git("status", "--porcelain", "--", Path(path).name).stdout.strip():
        return "push 안 함: 바뀐 것 없음"
    for args in (("add", "--", Path(path).name), ("commit", "-q", "-m", message, "--", Path(path).name), ("push", "-q", "origin", "HEAD")):
        p = git(*args)
        if p.returncode != 0:
            return f"push 실패 (git {args[0]}): {(p.stderr or p.stdout).strip()[:200]}"
    return "push 완료"


def cmd_weekly(a):
    """주간 예약(수집 → freeze 뒤): 내 대상 전부 + stack-extra를 고정 등급 파일에 맞춘다. 실제 모델 — 지문 바뀐 기술만 부른다."""
    tiers = Path(a.tiers) if a.tiers else default_tiers_path()
    extra = json.loads(STACK_EXTRA.read_text(encoding="utf-8")) if STACK_EXTRA.exists() else []
    rep = classify(tiers, a.model, targets=a.targets or True, extra=extra, real_api=True, plan=a.plan, skip_if_no_key=True,
                   call_log=a.call_log)
    rep["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    lines = report_lines(rep)
    if not a.plan and not a.no_push:                    # 파일에 커밋 안 된 변경이 있으면 올린다 (이번에 분류한 게 없어도 — 손으로 돌린 결과가 남지 않게)
        rep["push"] = push_tiers(tiers, f"tiers: {rep['finished_at'][:10]} {a.model} 분류 {len(rep['classified'])} · 기술 {rep['technologies']}")
        lines.append(rep["push"])
    for line in lines:
        print(line)
    print(f"-> {tiers}")
    if a.report:
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        Path(a.report).write_text(json.dumps(rep, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    return 1 if rep["failed"] else 0


def compare_stacks(collector_names, gate_names):
    """수집기가 보는 이름 vs 관문이 아는 이름 (signals.norm으로 정규화) → {"collector_only", "gate_only", "both"} 정렬 목록."""
    from .signals import norm
    c, g = {norm(n) for n in collector_names}, {norm(n) for n in gate_names}
    return {"collector_only": sorted(c - g), "gate_only": sorted(g - c), "both": sorted(c & g)}


def collector_stack_names(targets_path=None):
    """수집기가 내 대상에서 읽는 의존성 이름 — CLAUDE.md 표(npm 표시 행 제외) + package.json(git 추적 − exclude). 폴더 없는 대상은 (이름, 경로)로 따로."""
    from collector.sources import github
    found, missing, _ = load_targets(targets_path)
    names = []
    for _, root, ex in found:
        md = root / "CLAUDE.md"
        if md.exists():
            names += github.dependencies_from_claude_md(md.read_text(encoding="utf-8"))
        names += github.dependencies_from_package_json(root, ex)
    return names, missing


def other_stack_names(targets_path=None, mine=()):
    """수집 전용 대상(팀원 repo — collector/targets-collect-only.json)에만 있는 의존성 이름 (HYO-122 결정 1: 팀원 스택은 내 관문 밖으로 따로 센다).
    내 스택(수집기가 내 대상에서 읽는 이름 + 관문 manifest 신호 + mine)에도 있는 이름은 뺀다 — 겹치면 내 것이다.
    → (이름 목록, info {"file", "repos", "missing", "error"?}). 내 스택을 못 읽으면 빼 줄 수 없으니 이름 없이 error만 남긴다 (조용히 넘기지 않는다)."""
    from collector.run import collect_only_targets
    from collector.sources import github
    from .signals import norm
    texts, roots, raw = collect_only_targets()
    info = {"file": raw["file"], "repos": raw["repos"], "missing": raw["missing"]}
    try:
        my_names, _ = collector_stack_names(targets_path)
        found, _, _ = load_targets(targets_path)
    except SystemExit as e:
        return [], {**info, "error": f"내 스택을 못 읽어 팀원 스택을 가를 수 없다: {e}"}
    my_names = list(my_names) + [s["name"] for _, root, ex in found for s in read_repo_signals(root, ex).values()]   # requirements 등 수집기가 안 읽는 것
    mine_n = {norm(n) for n in my_names + list(mine)}
    out, seen = [], set()
    for repo, root in roots.items():
        names = github.dependencies_from_claude_md(texts[repo]) if repo in texts else []
        names += github.dependencies_from_package_json(root, raw["excludes"].get(repo) or ())
        for n in names:
            if norm(n) not in mine_n and norm(n) not in seen:
                seen.add(norm(n))
                out.append(n)
    return out, info


def cmd_stack_diff(a):
    store = TierStore(a.tiers)
    gate = [r["technology"] for r in store.data["technologies"].values()] + [o.get("technology", k) for k, o in store.data["overrides"].items()]
    names, missing = collector_stack_names(None if a.targets is True else a.targets)
    for name, root in missing:
        print(f"대상 폴더 없음: {name} -> {root}")
    d = compare_stacks(names, gate)
    for label, key in (("수집기만", "collector_only"), ("관문만", "gate_only"), ("양쪽", "both")):
        print(f"{label} {len(d[key])}: {', '.join(d[key]) or '-'}")
    return 0


def cmd_override(a):
    from .signals import norm
    store = TierStore(a.tiers)
    ov = store.data["overrides"].setdefault(norm(a.tech), {"technology": a.tech})
    if a.set_tiers:
        ov["tiers"] = [t.strip() for t in a.set_tiers.split(",") if t.strip()]
    if a.sensitive is not None:
        ov["sensitive"] = a.sensitive
    if a.local is not None:
        ov["local"] = a.local
    if a.path:
        ov["path"] = a.path
    if a.set_version:
        ov["version"] = [v.strip() for v in a.set_version.split(",") if v.strip()]
    if a.note:
        ov["note"] = a.note
    store.save()
    print(json.dumps(ov, ensure_ascii=False))
    return 0


def cmd_show(a):
    store = TierStore(a.tiers)
    for r in store.data["technologies"].values():
        e = store.effective(r["technology"])
        flag = "사람" if e.get("human") else ("불가" if e["unresolved"] else "")
        mm = "  [스크립트가 모델을 덮음]" if r.get("mismatch") else ""
        print(f"{r['technology']:<32} {','.join(e['tiers']) or '-':<8} path={e['path']} pin={e['pin']} {flag}{mm}")
    print("\n구현하지 못한 신호:")
    for n in NOT_IMPLEMENTED:
        print(" -", n)
    return 0


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="pipeline.importance")
    sp = ap.add_subparsers(dest="cmd", required=True)
    c = sp.add_parser("classify-stack")
    c.add_argument("--repo", action="append")
    c.add_argument("--targets", nargs="?", const=True, default=None, help="내 대상 전부(+각자의 exclude) — 경로 생략 시 env RADAR_TARGETS 또는 <부모>/adr/radar-targets.json. --repo와 함께 쓸 수 있다")
    c.add_argument("--extra", help="[{name, usage, source}] — 모델 id·서비스처럼 manifest에 없는 기술")
    c.add_argument("--tiers", required=True)
    c.add_argument("--model", default="stub")
    c.add_argument("--real-api", action="store_true", help="실제 API 호출 허용 (없으면 stub 외에는 거부)")
    c.add_argument("--refresh", action="store_true", help="지문이 같아도 다시 분류 (사람이 고친 값은 그대로)")
    c.add_argument("--plan", action="store_true", help="호출 수만 세고 끝")
    c.add_argument("--call-log")
    c.set_defaults(fn=cmd_classify)
    w = sp.add_parser("weekly-tiers", help="주간 예약: 고정 등급 파일을 내 스택에 맞춘다 (실제 모델, 지문 바뀐 기술만)")
    w.add_argument("--tiers", help="기본: env RADAR_TIERS 또는 <부모>/radar-snapshots/tiers.json")
    w.add_argument("--targets", help="기본: env RADAR_TARGETS 또는 <부모>/adr/radar-targets.json")
    w.add_argument("--model", default=WEEKLY_MODEL)
    w.add_argument("--plan", action="store_true", help="호출 수만 세고 끝 (API를 부르지 않는다)")
    w.add_argument("--no-push", action="store_true", help="등급 파일을 커밋·push하지 않는다")
    w.add_argument("--report", help="보고서 JSON 경로 (예약: collector/out/last-tiers.json)")
    w.add_argument("--call-log")
    w.set_defaults(fn=cmd_weekly)
    d = sp.add_parser("stack-diff", help="수집기가 보는 스택 vs 관문(등급 파일)이 아는 스택")
    d.add_argument("--tiers", required=True)
    d.add_argument("--targets", nargs="?", const=True, default=None)
    d.set_defaults(fn=cmd_stack_diff)
    o = sp.add_parser("override")
    o.add_argument("--tiers", required=True)
    o.add_argument("--tech", required=True)
    o.add_argument("--set-tiers")
    o.add_argument("--sensitive", action=argparse.BooleanOptionalAction, default=None)
    o.add_argument("--local", action=argparse.BooleanOptionalAction, default=None)
    o.add_argument("--path", choices=["prod", "dev"])
    o.add_argument("--set-version", help="우리가 쓰는 버전 (쉼표로 여럿) — manifest가 없는 기술용, manifest 값보다 이긴다 (HYO-148)")
    o.add_argument("--note")
    o.set_defaults(fn=cmd_override)
    s = sp.add_parser("show")
    s.add_argument("--tiers", required=True)
    s.set_defaults(fn=cmd_show)
    e = sp.add_parser("evaluate", help="평가 진입점 (evaluate.py)")
    e.set_defaults(fn=None)
    args, rest = ap.parse_known_args(argv)
    if args.cmd == "evaluate":
        from .evaluate import main as ev
        return ev(rest)
    return args.fn(args)
