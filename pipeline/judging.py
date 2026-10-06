"""pooling 판정 시트와 지표 (doc-pipeline §5).

- `pool_depth` = 판정 분량을 정하는 값: 전략마다 상위 몇 개를 판정 풀에 넣을지. **필터가 보여 줄 개수 k가 아니다.**
- `cutoffs` = 지표를 재는 자리 (Recall@k·nDCG@k의 k). 이것도 필터의 k를 정하는 값이 아니다 — k를 고르는 건 사용자 몫이다
- 시트에는 순위·점수·전략 이름이 없다. 풀 열쇠(pool-key.json)에만 있다. 시트 순서는 어느 전략의 순서도 아니다 (id 해시 순)
- 판정은 「관련 여부」만. 판정이 없으면 계산하지 않는다 (일부만 있으면 --partial을 줘야 하고, 그래도 표시한다)
- 순위에 있는데 판정되지 않은 항목(cutoff > pool_depth)은 관련 없음으로 치되 건수를 함께 낸다
"""
import csv
import hashlib
import json
import math
from pathlib import Path

SHEET = "judgment-sheet.csv"
KEY = "pool-key.json"
COLUMNS = ["item_id", "relevant", "source", "title", "url", "snippet", "memo"]
YES = {"1", "y", "yes", "o", "관련", "true"}
NO = {"0", "n", "no", "x", "무관", "false"}


def shuffle_key(seed, item_id):
    return hashlib.sha1(f"{seed}|{item_id}".encode("utf-8")).hexdigest()


def snippet(item, n=240):
    return " ".join((item.get("text") or "").split())[:n]


def build_pool(rankings, pool_depth, seed="pool-v1"):
    """rankings = {전략: [entry...]} → (pool 항목 목록(섞인 순서), key). 전략 이름은 key에만 간다."""
    members = {}
    for strat, ranked in rankings.items():
        for e in ranked[:pool_depth]:
            members.setdefault(e["id"], e)
    order = sorted(members, key=lambda i: shuffle_key(seed, i))
    ranks = {}
    for strat, ranked in rankings.items():
        for e in ranked:
            ranks.setdefault(e["id"], {})[strat] = e["rank"]
    key = {"pool_depth": pool_depth, "seed": seed, "strategies": list(rankings),
           "items": {i: {"ranks": {s: ranks.get(i, {}).get(s) for s in rankings},
                         "known_example": members[i]["known_example"]} for i in order}}
    return [members[i] for i in order], key


def write_sheet(path, pool, texts):
    """texts = {id: 항목 본문} (스니펫용). 엑셀에서 한글이 깨지지 않게 BOM을 붙인다."""
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for e in pool:
            w.writerow([e["id"], "", e["source"], e["title"], e["url"], snippet({"text": texts.get(e["id"], "")}), ""])


def read_judgments(path, pool_ids=None):
    """CSV → ({id: 0|1}, 비어 있는 id 목록). 알아볼 수 없는 값은 오류."""
    rel, blank = {}, []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for n, row in enumerate(csv.DictReader(f), 2):
            iid, v = row["item_id"], (row.get("relevant") or "").strip().lower()
            if v == "":
                blank.append(iid)
            elif v in YES:
                rel[iid] = 1
            elif v in NO:
                rel[iid] = 0
            else:
                raise ValueError(f"{path}:{n}: relevant 칸을 알아볼 수 없다 {row.get('relevant')!r} (1/0 또는 y/n)")
    if pool_ids is not None:
        extra = set(rel) - set(pool_ids)
        if extra:
            raise ValueError(f"풀에 없는 항목이 판정돼 있다: {sorted(extra)[:3]}")
    return rel, blank


def _discount(position):
    return math.log2(position + 1)


def _ndcg(gains, n_relevant, k):
    dcg = sum(g / _discount(i) for i, g in enumerate(gains[:k], 1))
    idcg = sum(1 / _discount(i) for i in range(1, min(k, n_relevant) + 1))
    return dcg / idcg if idcg else None


def evaluate(rankings, rel, cutoffs, exclude_known=False):
    """전략별·cutoff별 Recall@k · nDCG@k · 노이즈 비율.

    recall 분모 = 판정된 관련 항목 전체(풀 기준). 노이즈 비율 = 상위 k 중 관련 아닌 비율 (미판정은 관련 아님).
    exclude_known=True면 known_example 항목을 순위와 정답에서 모두 뺀다 (적어 준 것을 찾는 건 필터 성능이 아니다)."""
    known = {e["id"] for r in rankings.values() for e in r if e["known_example"]}
    relevant = {i for i, v in rel.items() if v == 1 and not (exclude_known and i in known)}
    out = {}
    for strat, ranked in rankings.items():
        ids = [e["id"] for e in ranked if not (exclude_known and e["id"] in known)]
        res = {}
        for k in cutoffs:
            top = ids[:k]
            gains = [1 if i in relevant else 0 for i in top]
            hits = sum(gains)
            res[k] = {
                "recall": hits / len(relevant) if relevant else None,
                "ndcg": _ndcg(gains, len(relevant), k),
                "noise": 1 - hits / len(top) if top else None,
                "hits": hits, "shown": len(top), "unjudged": sum(1 for i in top if i not in rel),
            }
        out[strat] = res
    return {"relevant_total": len(relevant), "strategies": out}


def score_run(run_dir, judgments_path, cutoffs, partial=False):
    """run 폴더의 lane_b.<전략>.json + pool-key.json + 판정 시트 → 지표. 판정이 없으면 ValueError."""
    run_dir = Path(run_dir)
    key = json.loads((run_dir / KEY).read_text(encoding="utf-8"))
    rel, blank = read_judgments(judgments_path, key["items"])
    if not rel:
        raise ValueError("판정이 하나도 없다 — judgment-sheet.csv의 relevant 칸을 채운 뒤 다시 실행한다 (계산하지 않음)")
    if blank and not partial:
        raise ValueError(f"판정이 비어 있는 항목이 {len(blank)}건 있다 — 다 채우거나 --partial로 일부만으로 계산한다 (편향 경고)")
    rankings = {s: json.loads((run_dir / f"lane_b.{s}.json").read_text(encoding="utf-8")) for s in key["strategies"]}
    res = {"judged": len(rel), "blank": len(blank), "pool_size": len(key["items"]), "pool_depth": key["pool_depth"],
           "cutoffs": list(cutoffs), "all": evaluate(rankings, rel, cutoffs),
           "excluding_known_examples": evaluate(rankings, rel, cutoffs, exclude_known=True)}
    ki = {i for i, m in key["items"].items() if m["known_example"]}
    res["known_examples_in_pool"] = {"count": len(ki), "judged_relevant": sum(1 for i in ki if rel.get(i) == 1)}
    return res


def format_scores(res):
    lines = [f"판정 {res['judged']}/{res['pool_size']} (비어 있음 {res['blank']}) · 풀 깊이 {res['pool_depth']} · "
             f"정답 {res['all']['relevant_total']}건 (알려진 정답 제외 시 {res['excluding_known_examples']['relevant_total']}건)"]
    for label in ("all", "excluding_known_examples"):
        lines.append(f"[{'전체' if label == 'all' else '알려진 정답 제외'}]")
        for s, byk in res[label]["strategies"].items():
            for k, m in byk.items():
                f = lambda x: "  - " if x is None else f"{x:.3f}"  # noqa: E731
                lines.append(f"  {s:12} @{k:<3} recall {f(m['recall'])}  nDCG {f(m['ndcg'])}  noise {f(m['noise'])}  "
                             f"(적중 {m['hits']}/{m['shown']}, 미판정 {m['unjudged']})")
    return "\n".join(lines)
