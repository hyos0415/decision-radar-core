"""하네스 (HYO-34) — 전략 인터페이스 · 청크→문서 순위 올림 · qrels·질의 로더 · 지표 · 결과 저장.

채점은 **문서 단위**다. 전략은 청크 순위를 내고, 하네스가 문서마다 최고 순위 청크 하나만 남겨 문서 순위로 올린다.
gold(질의·정답)는 사람이 만드는 중이다 — 이 모듈은 로더·채점기만 갖고, 실제 gold로 전략을 돌리지 않는다.
"""
import json
import math
import platform
import subprocess
import sys
import time
from collections import Counter, defaultdict
from importlib import metadata
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
METRICS = ["recall@5", "recall@10", "recall@20", "ndcg@10", "mrr"]
SOURCE_TYPE_KS = (5, 10, 20)
GRADES = (0, 1, 2)


class Strategy:
    """전략 인터페이스. 구현은 arena/strategies/에 둔다."""
    id = "S?"
    version = "v?"

    def index(self, chunks):
        raise NotImplementedError

    def search(self, query, k):
        """-> [(chunk_id, score)] 점수 내림차순"""
        raise NotImplementedError


# --- 청크 순위 → 문서 순위 ------------------------------------------------------------------------------------------

def rollup(chunk_hits, chunk_to_doc):
    """청크 순위를 문서 순위로 올린다 — 문서마다 최고 순위 청크 하나만 남기고 나머지는 버린다.
    점수 내림차순(동점은 들어온 순서)으로 정렬한 뒤 문서별 첫 청크의 점수를 그 문서의 점수로 쓴다."""
    seen, out = set(), []
    for chunk_id, score in sorted(chunk_hits, key=lambda h: -h[1]):
        if chunk_id not in chunk_to_doc:
            raise KeyError(f"전략이 모르는 청크 id를 돌려줬다: {chunk_id}")
        doc = chunk_to_doc[chunk_id]
        if doc not in seen:
            seen.add(doc)
            out.append((doc, score))
    return out


def run_queries(strategy, queries, chunk_to_doc, k_docs=20, chunk_k=200):
    """-> (run {qid: {doc_id: score}}, 질의 지연 ms 목록). 문서 k_docs개가 채워지게 청크를 넉넉히(chunk_k) 받는다."""
    run, latencies = {}, []
    for q in queries:
        t0 = time.perf_counter()
        hits = strategy.search(q["text"], chunk_k)
        latencies.append((time.perf_counter() - t0) * 1000)
        run[q["qid"]] = {d: s for d, s in rollup(hits, chunk_to_doc)[:k_docs]}
    return run, latencies


# --- gold 로더 (로더만 — gold 자체는 사람이 만든다) -----------------------------------------------------------------

def load_queries(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            q = json.loads(line)
            for k in ("qid", "text", "type"):
                if not q.get(k):
                    raise ValueError(f"{path}:{n} 질의에 {k!r}가 없다")
            out.append(q)
    ids = [q["qid"] for q in out]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path}: qid가 겹친다")
    return out


def load_qrels(path):
    """ranx 형식 {qid: {doc_id: grade}} (grade 0/1/2)."""
    q = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_qrels(q)


def validate_qrels(qrels):
    if not isinstance(qrels, dict) or not qrels:
        raise ValueError("qrels는 비어 있지 않은 {qid: {doc_id: grade}} 이어야 한다")
    for qid, docs in qrels.items():
        if not isinstance(docs, dict) or not docs:
            raise ValueError(f"qrels[{qid!r}]에 정답 문서가 없다")
        for doc, g in docs.items():
            if isinstance(g, bool) or g not in GRADES:
                raise ValueError(f"qrels[{qid!r}][{doc!r}] 등급 {g!r} — 0/1/2만 쓴다")
    return qrels


def check_gold(queries, qrels, doc_ids):
    """질의 ↔ qrels 짝과 정답 문서 id가 코퍼스에 있는지 확인한다 (오타가 조용히 Recall 0이 되지 않게)."""
    qids = {q["qid"] for q in queries}
    if qids != set(qrels):
        raise ValueError(f"질의와 qrels의 qid가 다르다: 질의만 {sorted(qids - set(qrels))[:5]} · qrels만 {sorted(set(qrels) - qids)[:5]}")
    unknown = sorted({d for docs in qrels.values() for d in docs} - set(doc_ids))
    if unknown:
        raise ValueError(f"qrels에 코퍼스에 없는 문서 id가 있다: {unknown[:5]}")


# --- 지표 -------------------------------------------------------------------------------------------------------

def _ranx_eval(qrels, run):
    from ranx import Qrels, Run, evaluate
    # 결과가 빈 질의는 ranx가 못 받는다 → 아무 문서와도 겹치지 않는 자리표시 문서를 넣는다(채점상 0이다)
    safe = {qid: (docs or {"__no_result__": 0.0}) for qid, docs in run.items()}
    res = evaluate(Qrels(qrels), Run(safe), METRICS)
    return {m: float(res[m]) for m in METRICS}


def source_type_distribution(run, doc_source_types, k):
    """상위 k 문서의 source_type 분포 — 질의별 비율의 평균. (Correct Source Type@k의 재료 —
    「맞는 source_type」은 질의 메타가 확정된 뒤에 정한다.)"""
    shares, n = defaultdict(float), 0
    for docs in run.values():
        top = list(docs)[:k]
        if not top:
            continue
        n += 1
        for st, c in Counter(doc_source_types[d] for d in top).items():
            shares[st] += c / len(top)
    return {st: round(v / n, 4) for st, v in sorted(shares.items())} if n else {}


def compute_metrics(run, qrels, queries, doc_source_types):
    qtype = {q["qid"]: q["type"] for q in queries}
    metrics = {"n_queries": len(qrels), "overall": _ranx_eval(qrels, run), "by_type": {}, "source_type_at_k": {}}
    for t in sorted(set(qtype.values())):
        ids = [qid for qid in qrels if qtype[qid] == t]
        sub = _ranx_eval({i: qrels[i] for i in ids}, {i: run[i] for i in ids})
        metrics["by_type"][t] = {"n_queries": len(ids), **sub}
    for k in SOURCE_TYPE_KS:
        metrics["source_type_at_k"][f"@{k}"] = {
            "overall": source_type_distribution(run, doc_source_types, k),
            "by_type": {t: source_type_distribution({i: run[i] for i in qrels if qtype[i] == t}, doc_source_types, k)
                        for t in sorted(set(qtype.values()))}}
    return metrics


def latency_summary(ms):
    s = sorted(ms)

    def pct(q):
        return round(s[min(len(s) - 1, math.ceil(q * len(s)) - 1)], 3) if s else None
    return {"p50": pct(.5), "p95": pct(.95), "n": len(s)}


# --- 결과 저장 (§9) ---------------------------------------------------------------------------------------------

def result_key(strategy_id, version, corpus_version, gold_version, tier="T0", seed=0):
    """`<전략>.<버전>__<코퍼스버전>__<gold버전>__<티어>__seed<n>`"""
    parts = [f"{strategy_id}.{version}", corpus_version, gold_version, tier, f"seed{seed}"]
    for p in parts:
        if not p or any(ch in p for ch in '/\\:*?"<>|') or "__" in p:
            raise ValueError(f"결과 키에 쓸 수 없는 값: {p!r}")
    return "__".join(parts)


def save_results(results_root, key, run, metrics, system, env):
    """같은 키 폴더가 이미 있으면 덮어쓰지 않고 실패한다 — 옛 결과는 그 버전의 기록이다."""
    d = Path(results_root) / key
    d.mkdir(parents=True, exist_ok=False)          # FileExistsError
    for name, obj in (("run.json", run), ("metrics.json", metrics), ("system.json", system), ("env.json", env)):
        (d / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return d


def _git(*args):
    try:
        r = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, timeout=20)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def env_info(extra=None):
    libs = {}
    for name in ("ranx", "tokenizers", "numpy", "PyYAML"):
        try:
            libs[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            libs[name] = None
    info = {
        "host": platform.node(),
        "machine": platform.machine(),
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "device": "cpu",                      # 이번 전략(ES BM25)은 가속기를 쓰지 않는다
        "libraries": libs,
        "git": {"commit": _git("rev-parse", "HEAD"), "dirty": bool(_git("status", "--porcelain"))},
    }
    info.update(extra or {})
    return info
