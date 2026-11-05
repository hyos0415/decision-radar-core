"""수집 한 번 돌리기 (HYO-71). 결과는 --out 폴더의 items.jsonl + run.json.

  python -m collector.run --out collector/out/2026-10-05
  python -m collector.run --sources hn,huggingface --out /tmp/x
  python -m collector.run --sources vendor --only aws-bedrock --out /tmp/x    # AWS는 주 2회

환경 변수 (스킬과 같은 이름): RADAR_TARGETS(대상 repo 목록) · RADAR_ADR_DIR(ADR 폴더) · GITHUB_TOKEN(없으면 gh auth token)
          · RADAR_TIERS(등급 파일 — security 출처가 retired 기술을 OSV에 묻지 않으려고 읽는다)
주간 스냅숏으로 얼리고 지난 주와 비교하는 일은 collector/snapshot.py(HYO-72) — 여기서는 한 번 모은 원자료만 낸다.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

from .fetch import Fetcher
from .item import merge
from .sources import github, hn, huggingface, reddit, security, vendor

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SOURCES = ("reddit", "hn", "github", "huggingface", "vendor", "security")


def load_yaml(p):
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def targets():
    path = Path(os.environ.get("RADAR_TARGETS") or (REPO.parent / "adr" / "radar-targets.json"))
    if not path.exists():
        return {}, f"targets 없음: {path}"
    texts = {}
    for t in json.loads(path.read_text(encoding="utf-8")):
        md = (path.parent / t["path"] / "CLAUDE.md").resolve()
        if md.exists():
            texts[t["repo"]] = md.read_text(encoding="utf-8")
    return texts, str(path)


def target_roots():
    """{repo: 대상 repo 루트 폴더} — package.json을 CLAUDE.md 없이도 읽으려고 따로 둔다 (targets()는 pipeline도 쓴다)."""
    path = Path(os.environ.get("RADAR_TARGETS") or (REPO.parent / "adr" / "radar-targets.json"))
    if not path.exists():
        return {}
    roots = {}
    for t in json.loads(path.read_text(encoding="utf-8")):
        root = (path.parent / t["path"]).resolve()
        if root.is_dir():
            roots[t["repo"]] = root
    return roots


def target_excludes():
    """{repo: exclude 목록} — radar-targets.json 각 항목의 `exclude`(root 기준 경로·폴더·glob). manifest를 읽을 때 뺀다 (stack.py)."""
    path = Path(os.environ.get("RADAR_TARGETS") or (REPO.parent / "adr" / "radar-targets.json"))
    if not path.exists():
        return {}
    return {t["repo"]: list(t.get("exclude") or []) for t in json.loads(path.read_text(encoding="utf-8"))}


COLLECT_ONLY = HERE / "targets-collect-only.json"


def collect_only_targets():
    """수집만 하는 대상 repo (다른 사람의 repo — 사용자 결정 2026-10-08 나안: 수집은 같이, 결정 기록은 각자의 ADR repo).

    `adr/radar-targets.json`은 스킬·제품·데스크톱·파이프라인이 「내 스택」으로 읽는다 — 거기에 넣으면 내 detect가 남의 스택까지 본다.
    그래서 수집기만 읽는 별도 파일에 둔다. 형식: {"targets": [{"repo": 이름, "path": 형제 폴더 이름(이 레포의 부모 기준), ...}]}.
    → ({repo: CLAUDE.md 본문}, {repo: 루트}, {"file", "repos", "missing", "excludes"}). 폴더가 없으면 조용히 빼지 않고 missing에 남긴다.
    excludes = {repo: 항목의 `exclude` 목록}.
    """
    path = Path(os.environ.get("RADAR_COLLECT_ONLY") or COLLECT_ONLY)
    info = {"file": str(path), "repos": [], "missing": [], "excludes": {}}
    if not path.exists():
        return {}, {}, info
    texts, roots = {}, {}
    for t in json.loads(path.read_text(encoding="utf-8")).get("targets", []):
        root = (REPO.parent / t["path"]).resolve()
        if not root.is_dir():
            info["missing"].append({"repo": t["repo"], "path": str(root)})
            continue
        roots[t["repo"]] = root
        info["excludes"][t["repo"]] = list(t.get("exclude") or [])
        info["repos"].append(t["repo"])
        md = root / "CLAUDE.md"
        if md.exists():
            texts[t["repo"]] = md.read_text(encoding="utf-8")
    return texts, roots, info


def refresh_collect_only(roots):
    """수집 전용 대상은 아무도 그 노트북에서 작업하지 않는다 — 그대로 두면 점점 옛 스택을 읽는다. 수집 직전에 fast-forward만 당긴다.
    내 repo는 당기지 않는다 (작업 중인 사본이다). 실패해도 수집은 계속하고 기록만 남긴다."""
    rep = {"pulled": [], "failed": []}
    for name, root in roots.items():
        if not (Path(root) / ".git").exists():
            rep["failed"].append({"repo": name, "why": "git 사본이 아니다"})
            continue
        p = subprocess.run(["git", "-C", str(root), "pull", "--ff-only", "--quiet"], capture_output=True, text=True, encoding="utf-8")
        if p.returncode == 0:
            rep["pulled"].append(name)
        else:
            rep["failed"].append({"repo": name, "why": (p.stderr or p.stdout).strip()[:200]})
    return rep


def my_mirror_roots():
    """내 스택 중 `"mirror": true`인 대상 — 이 노트북에서 작업하지 않는 레이더 전용 읽기 사본(예: 작업 사본이 다른 브랜치에 있는 repo).
    수집 전용 대상처럼 수집 직전에 당긴다. 표시가 없는 내 repo는 그대로 둔다 (작업 중인 사본이다)."""
    path = Path(os.environ.get("RADAR_TARGETS") or (REPO.parent / "adr" / "radar-targets.json"))
    if not path.exists():
        return {}
    return {t["repo"]: (path.parent / t["path"]).resolve() for t in json.loads(path.read_text(encoding="utf-8"))
            if t.get("mirror") and (path.parent / t["path"]).resolve().is_dir()}


def my_targets_missing():
    """내 스택 목록(radar-targets.json)에 있는데 폴더가 없는 repo — 조용히 빠지면 그 주 수집이 영구히 비므로 run.json에 남긴다."""
    path = Path(os.environ.get("RADAR_TARGETS") or (REPO.parent / "adr" / "radar-targets.json"))
    if not path.exists():
        return []
    return [{"repo": t["repo"], "path": str((path.parent / t["path"]).resolve())}
            for t in json.loads(path.read_text(encoding="utf-8")) if not (path.parent / t["path"]).resolve().is_dir()]


def merge_targets(mine, extra, what):
    """내 대상 + 수집 전용 대상. 같은 이름이 양쪽에 있으면 거부한다 (어느 쪽 스택인지 모호해진다)."""
    clash = set(mine) & set(extra)
    if clash:
        raise SystemExit(f"{what}: 대상 이름이 radar-targets.json과 수집 전용 목록에 둘 다 있다 — {sorted(clash)}")
    return {**mine, **extra}


def adr_technologies():
    adr_dir = os.environ.get("RADAR_ADR_DIR") or str(REPO.parent / "adr" / "docs" / "adr")
    radar = REPO / ".claude" / "skills" / "jhs-decisions" / "scripts" / "radar.py"
    p = subprocess.run([sys.executable, str(radar), "list", adr_dir, "--in-scope"],
                       capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        return [], p.stderr[:200]
    return sorted({a["technology"] for a in json.loads(p.stdout) if a.get("technology")}), adr_dir


def main(argv=None):
    ap = argparse.ArgumentParser(prog="collector.run")
    ap.add_argument("--out", help="출력 폴더. 없으면 --out-root 아래 <UTC 시각>-<label>")
    ap.add_argument("--out-root", default=str(HERE / "out"))
    ap.add_argument("--label", default="weekly", help="예약 실행 구분 (weekly · aws)")
    ap.add_argument("--sources", default=",".join(SOURCES))
    ap.add_argument("--only", default="", help="vendor: 이 벤더만 (쉼표)")
    a = ap.parse_args(argv)
    if not a.out:
        a.out = str(Path(a.out_root) / f"{time.strftime('%Y%m%d-%H%M', time.gmtime())}-{a.label}")

    pool = load_yaml(HERE / "pool.yaml")
    vendors_cfg = load_yaml(HERE / "vendors.yaml")
    wanted = [s for s in a.sources.split(",") if s]
    now = time.time()
    fetcher = Fetcher()
    mirror_pull = refresh_collect_only(my_mirror_roots())          # 내 스택의 읽기 전용 사본 — 읽기 전에 당긴다
    claude_md, targets_path = targets()
    roots = target_roots()
    _, pre_roots, _ = collect_only_targets()
    pull = refresh_collect_only(pre_roots)                         # 당긴 뒤에 다시 읽는다 (CLAUDE.md·package.json이 바뀌었을 수 있다)
    extra_md, extra_roots, collect_only = collect_only_targets()
    collect_only["refresh"] = pull
    collect_only["my_mirrors_refresh"] = mirror_pull
    collect_only["my_targets_missing"] = my_targets_missing()
    claude_md = merge_targets(claude_md, extra_md, "CLAUDE.md")     # 수집에만 합친다 — targets()를 쓰는 pipeline은 여전히 내 스택만 본다
    roots = merge_targets(roots, extra_roots, "repo 루트")
    excludes = {**target_excludes(), **collect_only["excludes"]}
    run = {"started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "pool_version": pool["version"],
           "vendors_version": vendors_cfg["version"], "targets": targets_path, "collect_only": collect_only, "sources": {}}
    all_items = []
    for name in wanted:
        t0 = time.time()
        try:
            if name == "reddit":
                items, rep = reddit.collect(pool["reddit"], fetcher, now)
            elif name == "hn":
                items, rep = hn.collect(pool["hn"], fetcher, now)
            elif name == "github":
                items, rep = github.collect(pool["github"], fetcher, now, claude_md, roots, excludes)
            elif name == "security":
                items, rep = security.collect(pool["security"], fetcher, now, roots, excludes)
            elif name == "huggingface":
                items, rep = huggingface.collect(pool["huggingface"], fetcher, now)
            elif name == "vendor":
                techs, adr_dir = adr_technologies()
                names = [s for txt in claude_md.values() for s in vendor.services_from_claude_md(txt)] + techs
                only = {x for x in a.only.split(",") if x} or None
                items, rep = vendor.collect(vendors_cfg, fetcher, now, names, only)
                rep["rule_inputs"] = {"services": len(names) - len(techs), "adr_technologies": techs, "adr_dir": adr_dir}
            else:
                raise SystemExit(f"모르는 출처: {name}")
        except Exception as e:  # noqa: BLE001 — 한 출처가 죽어도 나머지는 돈다
            items, rep = [], {"crash": f"{type(e).__name__}: {e}"}
        merged = merge(items)
        rep.update(raw=len(items), unique=len(merged), seconds=round(time.time() - t0, 1))
        run["sources"][name] = rep
        all_items += merged
        print(f"{name:12} {len(merged):5} items ({len(items)} raw)  failed={len(rep.get('failed', []))}"
              f"{'  CRASH ' + rep['crash'] if 'crash' in rep else ''}", flush=True)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "items.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for it in all_items:
            f.write(json.dumps(it.to_dict(), ensure_ascii=False) + "\n")
    run["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    run["items"] = len(all_items)
    run["http"] = {"requests": len(fetcher.log),
                   "non_200": [{"url": u, "status": s, "attempts": n} for u, s, n in fetcher.log if s != 200],
                   "redirects": [{"url": u, "final": f} for u, f in dict(fetcher.redirects).items()]}
    (out / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(f"-> {out} ({len(all_items)} items)")
    return 0 if not any("crash" in r for r in run["sources"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
