"""분류기 CLI.

  python -m pipeline.importance classify-stack --targets [radar-targets.json] [--repo <폴더> ...] [--extra stack-extra.json] --tiers tiers.json --model stub
      내 대상 전부(각자의 exclude 포함)의 manifest·Dockerfile·compose에서 기술을 뽑아 등급을 매겨 저장한다 (스택이 바뀐 기술만). 실제 모델은 --real-api가 있어야 한다.
      읽는 파일은 수집기와 같다 — git 추적 파일만 (collector/stack.py)
  python -m pipeline.importance stack-diff --tiers tiers.json [--targets [radar-targets.json]]
      수집기가 보는 이름과 등급 파일이 아는 이름을 견준다 (수집기만 · 관문만 · 양쪽)
  python -m pipeline.importance override --tiers tiers.json --tech PyYAML --set-tiers L6 [--sensitive] [--local] [--path dev]
      사람이 고친 값 — 자동 분류보다 우선하고, 다시 분류해도 지워지지 않는다
  python -m pipeline.importance show --tiers tiers.json
  python -m pipeline.importance evaluate ...       (evaluate.py 참고)

레인 A에 붙이기: `python -m pipeline.profile_filter run ... --gate stub --tiers tiers.json`
"""
import argparse
import json
import os
import sys
from pathlib import Path

from .backends import TechQuery, make_backend
from .signals import NPM_DEV, read_repo_signals
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


def queries_from_repos(repos, extra=None):
    """repos: 폴더 경로 또는 (경로, exclude) 쌍."""
    qs = []
    for repo in repos:
        repo, exclude = repo if isinstance(repo, tuple) else (repo, ())
        for key, s in read_repo_signals(repo, exclude).items():
            if s["image"]:
                usage, source = f"컨테이너 이미지 ({s['image_ref']})", s["image_ref"]
            elif s["group"] == NPM_DEV:      # 개발 도구일 수도, 번들돼 운영에 들어갈 수도 있다 (⑩) — 모델이 정하도록 그대로 알린다
                usage, source = "package.json devDependencies (개발 도구일 수도, 앱에 번들돼 운영 코드에 들어갈 수도 있다)", s["manifest"]
            else:
                usage, source = f"{s['manifest']} 직접 의존성 ({s['group']})", s["manifest"]
            qs.append(TechQuery(name=s["name"], usage=usage, source=source, signals={k: s[k] for k in ("manifest", "group", "pin", "image")}))
    for e in extra or []:
        qs.append(TechQuery(name=e["name"], usage=e.get("usage", ""), source=e.get("source", ""), signals=e.get("signals") or {}))
    seen, out = set(), []
    for q in qs:
        if q.name.lower() not in seen:
            seen.add(q.name.lower())
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
    gate = Gate(backend, store, load_vendor_terms(), limit or policy.WEEKLY_LIMIT, other_stack=other_stack)
    gate.other_info = {**info, "names": len(other_stack)}
    return gate, backend


def cmd_classify(a):
    extra = json.loads(Path(a.extra).read_text(encoding="utf-8")) if a.extra else []
    repos = list(a.repo or [])
    if a.targets:
        found, missing, path = load_targets(None if a.targets is True else a.targets)
        for name, root in missing:
            print(f"대상 폴더 없음: {name} -> {root}")       # 조용히 빼지 않는다
        repos += [(root, ex) for _, root, ex in found]
        print(f"대상 {len(found)}개 읽음 ({path})")
    qs = queries_from_repos(repos, extra)
    store = TierStore(a.tiers)
    todo = [q for q in qs if store.needs_classify(q) or a.refresh]
    print(f"기술 {len(qs)}개 중 새로 분류할 것 {len(todo)}개 (모델 호출 {len(todo)}회)")
    if a.plan:
        return 0
    from .backends import CallLog
    backend = make_backend(a.model, allow_real=a.real_api, log=CallLog(a.call_log))
    try:
        done = store.ensure(qs, backend, refresh=a.refresh)
        store.save()
    finally:
        backend.close()
    unresolved = [r["technology"] for r in store.data["technologies"].values() if r["unresolved"]]
    mism = [r["technology"] for r in store.data["technologies"].values() if r.get("mismatch")]
    print(f"분류 {len(done)}개 · 분류 불가 {len(unresolved)} {unresolved} · 스크립트가 모델을 덮어쓴 것 {len(mism)} {mism}")
    print(f"-> {a.tiers}")
    return 0


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
