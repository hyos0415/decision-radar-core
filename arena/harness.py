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
# Recall·MRR은 등급 2(답이 되는 1차 출처)만 정답으로 센다(-l2). 등급 1까지는 등급을 쓰는 nDCG가 반영한다 (사용자 2026-10-12)
METRICS = ["recall@5-l2", "recall@10-l2", "recall@20-l2", "ndcg@10", "mrr-l2"]
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

def rollup_chunks(chunk_hits, chunk_to_doc):
    """청크 순위를 문서 순위로 올린다 — 문서마다 최고 순위 청크 하나만 남기고 나머지는 버린다.
    점수 내림차순(동점은 들어온 순서)으로 정렬한 뒤 문서별 첫 청크를 그 문서의 대표로 쓴다.
    -> [(doc_id, 대표 chunk_id, 점수)]"""
    seen, out = set(), []
    for chunk_id, score in sorted(chunk_hits, key=lambda h: -h[1]):
        if chunk_id not in chunk_to_doc:
            raise KeyError(f"전략이 모르는 청크 id를 돌려줬다: {chunk_id}")
        doc = chunk_to_doc[chunk_id]
        if doc not in seen:
            seen.add(doc)
            out.append((doc, chunk_id, score))
    return out


def rollup(chunk_hits, chunk_to_doc):
    """rollup_chunks에서 대표 청크를 뺀 [(doc_id, 점수)]"""
    return [(d, s) for d, _, s in rollup_chunks(chunk_hits, chunk_to_doc)]


def run_queries(strategy, queries, chunk_to_doc, k_docs=20, chunk_k=200, rep_chunks=None, seeds=None):
    """-> (run {qid: {doc_id: score}}, 질의 지연 ms 목록). 문서 k_docs개가 채워지게 청크를 넉넉히(chunk_k) 받는다.
    rep_chunks(dict)를 주면 {qid: {doc_id: 대표 chunk_id}}를 채운다 — component 근거(jhs_metrics)가 청크 단위로 본다.
    seeds(dict)를 주면 전략의 `last_seeds`(S7 — 시드 노드 id 목록)를 {qid: [...]}로 채운다 — Seed Recall(jhs_metrics.seed_recall)이 쓴다."""
    run, latencies = {}, []
    for q in queries:
        t0 = time.perf_counter()
        if getattr(strategy, "uses_query_nodes", False):         # S1 — 질의 글이 아니라 구조화된 nodes로 찾는다
            if not q.get("nodes"):
                raise ValueError(f"{strategy.id}는 질의에 nodes가 필요하다: {q['qid']}")
            hits = strategy.search(q["text"], chunk_k, nodes=q["nodes"])
        else:
            hits = strategy.search(q["text"], chunk_k)
        latencies.append((time.perf_counter() - t0) * 1000)
        top = rollup_chunks(hits, chunk_to_doc)[:k_docs]
        run[q["qid"]] = {d: s for d, _, s in top}
        if rep_chunks is not None:
            rep_chunks[q["qid"]] = {d: c for d, c, _ in top}
        if seeds is not None and hasattr(strategy, "last_seeds"):
            seeds[q["qid"]] = list(strategy.last_seeds)
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


def save_results(results_root, key, run, metrics, system, env, extra=None):
    """같은 키 폴더가 이미 있으면 덮어쓰지 않고 실패한다 — 옛 결과는 그 버전의 기록이다.
    extra {파일 이름: 객체} — 예: rep_chunks.json (문서별 대표 청크)."""
    files = {"run.json": run, "metrics.json": metrics, "system.json": system, "env.json": env}
    for name in extra or {}:
        if name in files or not name.endswith(".json") or Path(name).name != name:
            raise ValueError(f"extra 파일 이름으로 쓸 수 없다: {name!r}")
    d = Path(results_root) / key
    d.mkdir(parents=True, exist_ok=False)          # FileExistsError
    for name, obj in {**files, **(extra or {})}.items():
        (d / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return d


def peak_memory():
    """구축 비용 (§5 변경 2026-10-13) — 이 프로세스의 최대 RSS와 GPU 최대 메모리. 잴 수 없는 항목은 None.
    ES 서버(도커)의 메모리는 들어가지 않는다 — 색인 크기(index_size_bytes)로 따로 본다."""
    rss = None
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            get = ctypes.windll.psapi.GetProcessMemoryInfo
            get.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
            proc = ctypes.windll.kernel32.GetCurrentProcess
            proc.restype = wintypes.HANDLE
            if get(proc(), ctypes.byref(pmc), pmc.cb):
                rss = int(pmc.PeakWorkingSetSize)
        else:
            import resource
            r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            rss = int(r if sys.platform == "darwin" else r * 1024)          # macOS는 바이트, Linux는 KB
    except Exception:                                                        # noqa: BLE001 — 계측 실패가 실행을 죽이지 않게
        rss = None
    gpu = {"device": None, "peak_bytes": None}
    torch = sys.modules.get("torch")                                         # 임베딩 전략만 torch를 불러온다
    if torch is not None:
        try:
            if torch.cuda.is_available() and torch.cuda.is_initialized():
                gpu = {"device": "cuda", "peak_bytes": int(torch.cuda.max_memory_allocated())}
            elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                gpu = {"device": "mps", "peak_bytes": None,
                       "driver_allocated_at_end_bytes": int(torch.mps.driver_allocated_memory()),
                       "note": "MPS는 최대값 API가 없다 — 끝날 때 드라이버 할당량"}
        except Exception:                                                    # noqa: BLE001
            pass
    return {"peak_rss_bytes": rss, "gpu": gpu}


def _git(*args):
    try:
        r = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, timeout=20)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def env_info(extra=None):
    libs = {}
    for name in ("ranx", "tokenizers", "numpy", "PyYAML", "torch", "FlagEmbedding", "transformers"):
        try:
            libs[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            libs[name] = None
    info = {
        "host": platform.node(),
        "machine": platform.machine(),
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "device": "cpu",                      # BM25(S2)는 가속기를 쓰지 않는다 — 임베딩 전략은 extra로 덮어쓴다
        "libraries": libs,
        "git": {"commit": _git("rev-parse", "HEAD"), "dirty": bool(_git("status", "--porcelain"))},
    }
    info.update(extra or {})
    return info
