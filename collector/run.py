"""수집 한 번 돌리기 (HYO-71). 결과는 --out 폴더의 items.jsonl + run.json.

  python -m collector.run --out collector/out/2026-10-05
  python -m collector.run --sources hn,huggingface --out /tmp/x
  python -m collector.run --sources vendor --only aws-bedrock --out /tmp/x    # AWS는 주 2회

환경 변수 (스킬과 같은 이름): RADAR_TARGETS(대상 repo 목록) · RADAR_ADR_DIR(ADR 폴더) · GITHUB_TOKEN(없으면 gh auth token)
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
from .sources import github, hn, huggingface, reddit, vendor

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SOURCES = ("reddit", "hn", "github", "huggingface", "vendor")


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
    claude_md, targets_path = targets()
    roots = target_roots()
    run = {"started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "pool_version": pool["version"],
           "vendors_version": vendors_cfg["version"], "targets": targets_path, "sources": {}}
    all_items = []
    for name in wanted:
        t0 = time.time()
        try:
            if name == "reddit":
                items, rep = reddit.collect(pool["reddit"], fetcher, now)
            elif name == "hn":
                items, rep = hn.collect(pool["hn"], fetcher, now)
            elif name == "github":
                items, rep = github.collect(pool["github"], fetcher, now, claude_md, roots)
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
                   "non_200": [{"url": u, "status": s, "attempts": n} for u, s, n in fetcher.log if s != 200]}
    (out / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(f"-> {out} ({len(all_items)} items)")
    return 0 if not any("crash" in r for r in run["sources"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
