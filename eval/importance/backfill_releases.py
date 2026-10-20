"""과거 릴리스 보충 — 드문 변화 종류(breaking·security·deprecation·model_retirement)가 한 주 표본에 너무 적어서, 내 스택 저장소의 지난 릴리스를 모아 초안 후보를 만든다 (HYO-131).

  python eval/importance/backfill_releases.py --snapshots ../radar-snapshots --weeks 2026-W41 2026-W42 --tiers ../radar-snapshots/tiers.json \
      --out eval/runs/backfill-2025-10..2026-10 [--since 2025-10-20 --until 2026-10-04] [--plan | --fetch]
  → <out>/items.jsonl (스냅숏 항목과 같은 모양 — collector.sources.github.parse_api_releases가 만든다) · manifest.json · selected.jsonl(초안 대상)
  그 뒤: python eval/importance/draft_gold.py --snapshot <out>/selected --tiers ... --out eval/runs/importance-gold-draft-backfill --no-sample --total-cap 400 --real-api

저장소 = 두 주 레인 A에서 내 스택(등급 있는 기술, other-stack 제외 — draft_gold.classify_items와 같은 규칙)에 걸린 의존성 릴리스의 meta.repo.
llama.cpp는 평범한 빌드가 수천 건이라 빌드 태그(b숫자)를 빼고 최근 비빌드 릴리스 최대 10건만 받는다.

선택 편향 (결과 해석 전에 읽는다): 초안 대상은 (a) 키워드가 걸린 릴리스 전부 + (b) 키워드가 안 걸린 것 중 무작위 60건이다.
(a)는 드문 종류가 일부러 많이 든 표본이라 종류별 비율이 실제 분포가 아니다 — 비율·정밀도 추정에 쓰지 않는다. (b)가 키워드가 놓치는 양을 재는 대조군이다.
"""
import argparse
import json
import random
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).parent))

from collector.item import Item  # noqa: E402
from collector.sources.github import parse_api_releases  # noqa: E402
import draft_gold  # noqa: E402
from pipeline.importance.cli import load_vendor_terms  # noqa: E402
from pipeline.importance.signals import norm  # noqa: E402
from pipeline.importance.tiers import TierStore  # noqa: E402

SEED = 42
RANDOM_N = 60
TOTAL_CAP = 300
LLAMA_NONBUILD_N = 10
DEFAULT_SINCE, DEFAULT_UNTIL = "2025-10-20", "2026-10-04"     # W41 이전 — 기존 정답지와 겹치지 않는다

# 드문 종류 키워드 (제목·본문). 넓게 잡는다 — 놓친 것은 무작위 대조군이 재 준다
KEYWORDS = re.compile(
    r"breaking|deprecat|removed|removal|drop(?:ped|s|ping)?\s+support|security|\bCVE-\d|GHSA-|vulnerab|\bEOL\b|end[\s-]of[\s-]life|"
    r"retire|sunset|pricing|\bprice|\w+!:", re.I)


def gh_releases(repo):
    """gh api로 저장소 릴리스 전체 → 목록. --slurp로 쪽을 한 배열로 받는다."""
    r = subprocess.run(["gh", "api", f"repos/{repo}/releases?per_page=100", "--paginate", "--slurp"], capture_output=True, text=True, encoding="utf-8", timeout=300)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200])
    pages = json.loads(r.stdout)
    return [x for p in pages for x in (p if isinstance(p, list) else [p])]


def stack_repos(snapshots, weeks, store):
    """두 주 레인 A → {repo: {dependency, ecosystem, technology}} (내 스택·llama.cpp 제외)."""
    vt = load_vendor_terms()
    other, _ = draft_gold.other_stack_safe(store)
    seen = defaultdict(Counter)
    tech_of = {}
    for w in weeks:
        items = draft_gold.load_items(Path(snapshots) / w)
        cands, _ns, _n = draft_gold.classify_items(items, store, vt, other)
        for c in cands:
            m = c["item"].get("meta") or {}
            if not m.get("repo"):
                continue
            tech = c["techs"][0]
            if norm(tech) == norm(draft_gold.LLAMA):
                continue
            seen[m["repo"]][(m.get("dependency"), m.get("ecosystem"), m.get("vendor"))] += 1
            tech_of[m["repo"]] = tech
    out = {}
    for repo, cnt in seen.items():
        (dep, eco, vendor), _ = cnt.most_common(1)[0]
        # 의존성 이름이 없는 저장소(벤더 통로로 들어온 claude-code·langsmith-sdk·langfuse)는 스냅숏처럼 vendor 통로·meta.vendor로 만든다 —
        # 관문이 벤더 이름으로 기술을 찾는다 (resolve_technologies)
        out[repo] = {"dependency": dep, "ecosystem": eco, "vendor": vendor, "technology": tech_of[repo]}
    return out


def in_window(r, since, until):
    ts = (r.get("published_at") or r.get("created_at") or "")[:10]
    return not r.get("draft") and since <= ts < until


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--snapshots", required=True)
    ap.add_argument("--weeks", nargs="+", required=True)
    ap.add_argument("--tiers", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--since", default=DEFAULT_SINCE)
    ap.add_argument("--until", default=DEFAULT_UNTIL)
    ap.add_argument("--random-n", type=int, default=RANDOM_N)
    ap.add_argument("--cap", type=int, default=TOTAL_CAP)
    ap.add_argument("--plan", action="store_true", help="저장소 목록만 보인다 (gh 호출 없음)")
    ap.add_argument("--select-only", action="store_true", help="이미 받은 items.jsonl에서 선택만 다시 한다 (gh 호출 없음)")
    args = ap.parse_args(argv)

    store = TierStore(args.tiers)
    repos = stack_repos(args.snapshots, args.weeks, store)
    llama_repo = "ggml-org/llama.cpp"
    print(f"저장소 {len(repos)}곳 (+ llama.cpp 비빌드 최대 {LLAMA_NONBUILD_N}건): " + ", ".join(f"{r}→{v['dependency']}" for r, v in sorted(repos.items())))
    if args.plan:
        return 0

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    items_path = out / "items.jsonl"
    manifest = {"since": args.since, "until": args.until, "weeks": args.weeks, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "repos": {}, "failed": []}
    if args.select_only:
        items = [json.loads(l) for l in items_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    else:
        items = []
        targets = dict(repos)
        targets[llama_repo] = {"dependency": "llama.cpp", "ecosystem": "pypi", "vendor": None, "technology": draft_gold.LLAMA}
        for repo, info in sorted(targets.items()):
            try:
                rels = [r for r in gh_releases(repo) if in_window(r, args.since, args.until)]
            except Exception as e:  # noqa: BLE001 — 한 저장소 실패가 전체를 멈추지 않는다
                manifest["failed"].append({"repo": repo, "error": f"{type(e).__name__}: {str(e)[:200]}"})
                print(f"  실패 {repo}: {e}", file=sys.stderr)
                continue
            if repo == llama_repo:
                rels = [r for r in rels if not re.fullmatch(r"b\d+", r.get("tag_name") or "")]
                rels = sorted(rels, key=lambda r: r.get("published_at") or "", reverse=True)[:LLAMA_NONBUILD_N]
            if info.get("vendor") and not info.get("dependency"):
                got = parse_api_releases(rels, repo, f"vendor:{info['vendor']}", {"vendor": info["vendor"]})
            else:
                got = parse_api_releases(rels, repo, "dependency-releases", {"dependency": info["dependency"], "ecosystem": info["ecosystem"]})
            items += [it.to_dict() for it in got]
            manifest["repos"][repo] = {"releases": len(got), **info}
            print(f"  {repo}: {len(got)}건")
        # 같은 id는 하나로 (모노레포 태그가 겹칠 일은 없지만 안전)
        items = list({it["id"]: it for it in items}.values())
        items_path.write_text("".join(json.dumps(it, ensure_ascii=False) + "\n" for it in items), encoding="utf-8", newline="\n")

    # ---- 선택 (편향 기록)
    rng = random.Random(SEED)
    hit = [it for it in sorted(items, key=lambda x: x["id"]) if KEYWORDS.search(it.get("title", "") + "\n" + (it.get("text") or ""))]
    miss = [it for it in sorted(items, key=lambda x: x["id"]) if it not in hit]
    ctrl = rng.sample(miss, min(args.random_n, len(miss)))
    budget = max(args.cap - len(ctrl), 0)
    capped = {}
    if len(hit) > budget:                    # 저장소별 균등 상한으로 깎는다
        by_repo = defaultdict(list)
        for it in hit:
            by_repo[it["meta"]["repo"]].append(it)
        lo, hi = 1, max(len(v) for v in by_repo.values())
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if sum(min(len(v), mid) for v in by_repo.values()) <= budget:
                lo = mid
            else:
                hi = mid - 1
        kept = []
        for repo, v in sorted(by_repo.items()):
            if len(v) > lo:
                v2 = rng.sample(v, lo)
                capped[repo] = {"before": len(v), "after": lo}
                kept += v2
            else:
                kept += v
        hit = sorted(kept, key=lambda x: x["id"])
    selected = sorted(hit + ctrl, key=lambda x: x["id"])
    sel_dir = out / "selected"
    sel_dir.mkdir(exist_ok=True)
    (sel_dir / "items.jsonl").write_text("".join(json.dumps(it, ensure_ascii=False) + "\n" for it in selected), encoding="utf-8", newline="\n")
    manifest["selection"] = {
        "seed": SEED, "total_releases": len(items), "keyword_matched": len(hit), "keyword_regex": KEYWORDS.pattern, "random_control": len(ctrl),
        "random_n_requested": args.random_n, "selected": len(selected), "cap": args.cap, "stratified_by_repo": capped,
        "ids_keyword": [it["id"] for it in hit], "ids_control": [it["id"] for it in ctrl],
        "bias_note": "선택 편향: 키워드 걸린 릴리스 전부(드문 종류가 일부러 많음) + 안 걸린 것 중 무작위 대조군. 종류별 비율·정밀도 추정에 쓰지 않는다."}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"저장소 {len(manifest['repos'])}곳 · 릴리스 {len(items)}건 · 키워드 {len(hit)} · 대조군 {len(ctrl)} · 선택 {len(selected)}"
          + (f" · 저장소별 깎음 {len(capped)}곳" if capped else "") + f" → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
