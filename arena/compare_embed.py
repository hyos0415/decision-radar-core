"""임베딩 후보를 Arena 정답지로 지금 것(BGE-M3)과 짝 비교한다 (HYO-240 — 실험 실행기의 embedding 레시피).

  python -m arena.compare_embed --candidate Qwen/Qwen3-Embedding-0.6B --out pipeline/out/embed-qwen3

HYO-228 손 실험(`pipeline/out/hyo228/run_embed.py`, 2026-11-21, gitignore)을 옮겼다. 같은 코퍼스(c-all@v1) · 같은 청크 · 같은 하네스
(run_queries · ranx) · ES 대신 메모리 정확 탐색(flat cosine — 10/29 test에서 ES kNN이 실행마다 흔들렸다).

판정 기준 (사용자 2026-11-22, HYO-228과 같게):
- 정답지 gold@v1.2 **test 36문항** · nDCG@10 · 같은 문항끼리 짝 차이(후보 − 지금) · paired bootstrap 95% 구간(10,000회, 씨앗 고정)
- 구간 하한 > 0 → better · 상한 < 0 → worse · 0을 포함 → indistinguishable. 유형별 · dev(19문항)는 참고로만 적는다
- 지금 것(BGE-M3 revision 5617a9f)의 test 전체가 등록값 0.5791(10/29 S4 · 11/21 재현)과 ±0.002 넘게 다르면 판정하지 않는다
  → baseline_mismatch (규율 3: 하네스나 코퍼스가 바뀌었으면 차이도 믿을 수 없다)

돌리기 전 거르기 (가중치를 받기 전에, HF 카드만):
- pipeline_tag가 임베딩(sentence-similarity · feature-extraction)이 아니면 not_embedding — 리랭커 · 생성 모델을 임베딩으로 재지 않는다
- fp32 가중치가 8GB를 넘으면 too_big — M1 16GB (11/21 Qwen3-8B를 못 돌렸다)

비용 (사용자 2026-11-22 「구분 안 됨이면 메모리 · 속도 같은 부수적인 걸 재야」): 두 쪽 매개변수(→ fp32 가중치 메모리) · 같은 청크 표본 256개의
문서 인코딩 속도(캐시 없이, 데운 뒤 1회) · 질의 p50 · 색인 벡터 크기를 적고, 후보가 지금 것의 몇 배인지 한 문장으로 낸다. 구분 안 됨이면 보고서 맨 앞에 둔다.

후보 쓰는 법: sentence-transformers로 연다 · fp32 · normalize · 모델이 적어 둔 prompts 중 query(질의)와 document(문서)만 그대로 쓴다
(Qwen3는 query만 — 공식 권장) · 길이 상한 = min(모델 상한, 2048), 청크가 넘으면 자르지 않고 실패. 차원은 1024가 아니어도 된다.
결과: <out>/report.json · report.md. 결정은 사람 — 이 결과는 근거다.
"""
import argparse
import hashlib
import json
import random
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PARENT = REPO.parent
GOLD = REPO / "eval" / "arena" / "gold@v1.2"
BASE_ID = "BAAI/bge-m3"
BASE_REV = "5617a9f61b028005a4858fdac845db406aefb181"      # arena/requirements.txt에 적힌 2026-10-12 받은 것
BASE_EXPECT = {"test": 0.5791}                              # 10/29 S4 공식 · 11/21 정확 탐색 재현
BASE_TOL = 0.002
MAX_LEN_CAP = 2048
BOOT, SEED = 10000, 20261121
EMBED_TAGS = ("sentence-similarity", "feature-extraction")
MAX_FP32_BYTES = 8e9
ENCODE_SAMPLE = 256          # 비용 — 문서 인코딩 속도를 잴 청크 표본 (코퍼스에서 고르게 · 두 쪽 같다)

VERDICTS = {
    "better": "나음 — test 전체 nDCG@10 짝 차이의 95% 구간이 0보다 크다",
    "indistinguishable": "구분 안 됨 — 95% 구간이 0을 포함한다 (이 정답지로는 낫다고도 못하다고도 못한다)",
    "worse": "못함 — 95% 구간이 0보다 작다",
    "baseline_mismatch": "판정 안 함 — 지금 것(BGE-M3)의 점수가 등록값과 다르다 (하네스 · 코퍼스를 먼저 본다)",
    "not_embedding": "돌리지 않음 — HF 카드상 임베딩 모델이 아니다",
    "too_big": "돌리지 않음 — fp32로 M1 16GB에서 못 돈다",
}


def screen(info):
    """HF 카드 요약 {pipeline_tag, params} → (verdict | None, 이유). None이면 돌린다."""
    tag, params = info.get("pipeline_tag"), info.get("params")
    if tag not in EMBED_TAGS:
        return "not_embedding", f"pipeline_tag={tag!r} — 임베딩({' · '.join(EMBED_TAGS)})이 아니다"
    if params and params * 4 > MAX_FP32_BYTES:
        return "too_big", f"매개변수 {params / 1e9:.1f}B × 4바이트 = {params * 4 / 1e9:.1f}GB > {MAX_FP32_BYTES / 1e9:.0f}GB"
    return None, None


def boot_ci(diffs, rng, boot=BOOT):
    if not diffs:
        return None
    n, means = len(diffs), []
    for _ in range(boot):
        means.append(sum(diffs[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    return [round(means[int(0.025 * boot)], 4), round(means[int(0.975 * boot) - 1], 4)]


def compare(base, cand, qtype, qids, rng):
    """문항별 nDCG {qid: 값} 둘 → 유형별 + ALL 줄 {n, base, cand, diff, ci95, wins, ties, losses}."""
    rows = {}
    for t in sorted({qtype[q] for q in qids}) + ["ALL"]:
        ids = [q for q in qids if t == "ALL" or qtype[q] == t]
        d = [cand[q] - base[q] for q in ids]
        rows[t] = {"n": len(ids), "base": round(sum(base[q] for q in ids) / len(ids), 4), "cand": round(sum(cand[q] for q in ids) / len(ids), 4),
                   "diff": round(sum(d) / len(d), 4), "ci95": boot_ci(d, rng),
                   "wins": sum(x > 1e-9 for x in d), "ties": sum(abs(x) <= 1e-9 for x in d), "losses": sum(x < -1e-9 for x in d)}
    return rows


def verdict_of(test_rows):
    """test 줄 → 판정. 지금 것의 점수가 등록값과 다르면 차이를 보지 않는다."""
    all_ = test_rows["ALL"]
    if abs(all_["base"] - BASE_EXPECT["test"]) > BASE_TOL:
        return "baseline_mismatch"
    lo, hi = all_["ci95"]
    return "better" if lo > 0 else "worse" if hi < 0 else "indistinguishable"


def report_md(r):
    lines = [f"# 임베딩 비교: {r['candidate']['model']} vs {BASE_ID}", "", f"**결과: {VERDICTS[r['verdict']]}**", ""]
    if r.get("reason"):
        lines += [f"- 이유: {r['reason']}"]
    c = r["candidate"]
    lines += [f"- 후보 revision `{(c.get('revision') or '?')[:12]}` · 지금 것 revision `{BASE_REV[:12]}` · 정답지 {r['gold']} · 코퍼스 {r.get('corpus', '—')}"
              f" (문서 {r.get('docs', '—')} · 청크 {r.get('chunks', '—')})"]
    if c.get("query_prompt") is not None or c.get("doc_prompt") is not None:
        lines.append(f"- 후보 지시문: 질의 {c.get('query_prompt')!r} · 문서 {c.get('doc_prompt')!r}")
    for split, title in (("test", "test (판정)"), ("dev", "dev (참고)")):
        rows = (r.get(split) or {}).get("by_type")
        if not rows:
            continue
        lines += ["", f"## {title} — nDCG@10, 후보 − 지금", "", "| 유형 | n | 지금 | 후보 | 차이 | 95% 구간 | 이김/같음/짐 |", "|---|---|---|---|---|---|---|"]
        for t, x in rows.items():
            lines.append(f"| {t} | {x['n']} | {x['base']:.3f} | {x['cand']:.3f} | {x['diff']:+.3f} | {x['ci95']} | {x['wins']}/{x['ties']}/{x['losses']} |")
    if r.get("arms"):
        a = r["arms"]
        lines += ["", "## 비용 — 품질이 같으면 이것으로 정한다", ""]
        if r.get("verdict") == "indistinguishable":
            lines += [f"품질로는 구분이 안 된다. **{cost_summary(a)}**", ""]
        else:
            lines += [cost_summary(a), ""]
        lines += ["| | 매개변수 | 가중치 메모리(fp32) | 차원 | 색인 벡터 | 문서 인코딩 | 질의 p50 |", "|---|---|---|---|---|---|---|"]
        for name, x in a.items():
            p = x.get("params")
            lines.append(f"| {'지금 것' if name == 'base' else '후보'} | {f'{p / 1e6:,.0f}M' if p else '—'} | {f'{p * 4 / 1e9:.2f}GB' if p else '—'} | {x['dim']} | "
                         f"{x['vectors_mb']}MB | {x.get('encode_per_s', '—')}청크/초 | {x['query_ms_p50']}ms |")
        n = (a.get("cand") or {}).get("encode_sample")
        bs = " · ".join(f"{'지금 것' if k == 'base' else '후보'} {v['batch_size']}" for k, v in a.items() if v.get("batch_size"))
        lines += ["", f"문서 인코딩은 같은 청크 표본 {n}개를 캐시 없이 한 번 잰 값이다(먼저 8개로 데운 뒤 · 같은 기기"
                  + (f" · 배치 크기는 각자 기본값 {bs}" if bs else "") + "). 지금 것은 dense만 센다(검색에 쓰는 것). "
                  "가중치 메모리는 매개변수 × 4바이트 — 실행 중 활성 메모리는 따로 재지 않았다. 질의 p50은 질의 인코딩 + 정확 탐색."]
    lines += ["", "질의 수가 작다(유형당 4~8) — 유형별 차이는 참고다. 판정은 test 전체 하나로 한다. 채택 여부는 사람이 정한다.", ""]
    return "\n".join(lines)


def _ratio(cand, base):
    return cand / base if cand and base else None


def cost_summary(arms):
    """비용 줄 둘 → 한 문장 (후보가 지금 것에 비해). 잴 수 없었던 항목은 뺀다."""
    b, c = arms.get("base") or {}, arms.get("cand") or {}
    parts = []
    if (x := _ratio(c.get("params"), b.get("params"))) is not None:
        parts.append(f"모델 크기 {x:.2f}배")
    if (x := _ratio(c.get("encode_per_s"), b.get("encode_per_s"))) is not None:
        parts.append(f"문서 인코딩 속도 {x:.2f}배" + (" (느림)" if x < 0.95 else " (빠름)" if x > 1.05 else ""))
    if (x := _ratio(c.get("query_ms_p50"), b.get("query_ms_p50"))) is not None:
        parts.append(f"질의 지연 {x:.2f}배" + (" (느림)" if x > 1.05 else " (빠름)" if x < 0.95 else ""))
    if (x := _ratio(c.get("vectors_mb"), b.get("vectors_mb"))) is not None:
        parts.append(f"색인 크기 {x:.2f}배")
    return ("후보는 지금 것에 비해 " + " · ".join(parts)) if parts else "비용을 재지 못했다"


def finish(r, out):
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "report.md").write_text(report_md(r), encoding="utf-8")
    print(f"{r['candidate']['model']}: {VERDICTS[r['verdict']]}", flush=True)
    return r


def card(model_id):
    """HF 카드 요약 — 가중치를 받지 않는다."""
    from huggingface_hub import HfApi
    info = HfApi().model_info(model_id, files_metadata=False)
    params = None
    st = getattr(info, "safetensors", None)
    if st is not None:
        params = getattr(st, "total", None) or (st.get("total") if isinstance(st, dict) else None)
    return {"pipeline_tag": getattr(info, "pipeline_tag", None), "params": params, "revision": info.sha}


# ── 모델을 쓰는 쪽 (torch · sentence-transformers가 있는 Arena 환경에서만) ─────────────────────────

def _encoders():
    import numpy as np
    from arena import embed

    class Candidate(embed.SentenceTransformerDenseEncoder):
        """차원 자유 · 모델의 query/document 지시문을 그대로 쓴다. 잘림 검사 · fp32 · normalize는 부모와 같다."""

        def __init__(self, model_id, revision, device=None, batch_size=8):
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(model_id, revision=revision, device=device or embed.pick_device())
            cap = min(int(getattr(model, "max_seq_length", None) or 512), MAX_LEN_CAP)
            super().__init__(model_id, revision, device=device, max_length=cap, batch_size=batch_size, model=model)
            self.query_prompt, self.doc_prompt = self.prompts.get("query"), self.prompts.get("document")

        def _enc(self, texts, prompt_name):
            texts = list(texts)
            prompt = self.prompts.get(prompt_name) if prompt_name else None
            self.check_no_truncation([(prompt or "") + t for t in texts])
            kw = {"batch_size": self.batch_size, "normalize_embeddings": True, "convert_to_numpy": True}
            if prompt:
                kw["prompt_name"] = prompt_name
            self.call_log.append(dict(kw, max_length=self.max_length))
            return np.asarray(self.model.encode(texts, **kw), dtype=np.float32)

        def encode_docs(self, texts):
            return self._enc(texts, "document" if self.doc_prompt else None), [{} for _ in texts]

        def encode_queries(self, texts):
            return self._enc(texts, "query" if self.query_prompt else None), [{} for _ in texts]

    class AnyDimEmbedder:
        """embed.Embedder와 같은 캐시 이름공간(arena/.cache) — 차원이 1024가 아니어도 된다."""

        def __init__(self, encoder, corpus_version, policy_version):
            self.encoder = encoder
            self.namespace = embed.namespace_name(corpus_version, policy_version, encoder.model_id, encoder.revision, encoder.max_length)
            self.cache = embed.EncodingCache(embed.CACHE_ROOT / self.namespace)

        def encode_chunks(self, chunks):
            shas = [embed.text_sha256(c["text"]) for c in chunks]
            hits = [self.cache.get(c["chunk_id"], s) for c, s in zip(chunks, shas)]
            todo = [i for i, h in enumerate(hits) if h is None]
            if todo:
                d, _ = self.encoder.encode_docs([chunks[i]["text"] for i in todo])
                self.cache.put_many([(chunks[i]["chunk_id"], shas[i], d[j], {}) for j, i in enumerate(todo)])
                self.cache.save()
                hits = [self.cache.get(c["chunk_id"], s) for c, s in zip(chunks, shas)]
            return np.stack([h[0] for h in hits]).astype(np.float32), [{} for _ in chunks]

        def encode_query(self, text):
            d, s = self.encoder.encode_queries([text])
            return d[0], s[0]

    class Flat:
        """메모리 안 정확 탐색 — 하네스 전략 인터페이스(index · search). 두 쪽 모두 다시 정규화해 정확한 코사인으로."""

        def __init__(self, sid, embedder):
            self.id, self.version, self.embedder = sid, "compare-embed", embedder

        def index(self, chunks):
            dense, _ = self.embedder.encode_chunks(chunks)
            self.ids = [c["chunk_id"] for c in chunks]
            mat = np.asarray(dense, dtype=np.float32)
            norms = np.linalg.norm(mat, axis=1)
            if (norms <= 0).any():
                raise ValueError("길이 0인 문서 벡터가 있다")
            self.mat = mat / norms[:, None]

        def search(self, query, k):
            qv, _ = self.embedder.encode_query(query)
            qv = np.asarray(qv, dtype=np.float32)
            s = self.mat @ (qv / np.linalg.norm(qv))
            idx = np.argsort(-s, kind="stable")[:k]
            return [(self.ids[i], float(s[i])) for i in idx]

    return Candidate, AnyDimEmbedder, Flat


def n_params(model):
    """모델 객체(SentenceTransformer · FlagEmbedding 포장) → 매개변수 수 | None. 안쪽 .model까지 내려가 torch 모듈을 찾는다."""
    import torch
    for obj in (model, getattr(model, "model", None), getattr(getattr(model, "model", None), "model", None)):
        if isinstance(obj, torch.nn.Module):
            return int(sum(p.numel() for p in obj.parameters()))
    return None


def per_query_ndcg(qrels, run):
    from ranx import Qrels, Run, evaluate
    return {qid: float(evaluate(Qrels({qid: qrels[qid]}), Run({qid: run.get(qid) or {"__no_result__": 0.0}}), "ndcg@10")) for qid in qrels}


def corpus_args(parent=PARENT):
    """HYO-228과 같은 코퍼스 입력 — 형제 폴더(radar-snapshots · adr · 얼린 대상 repo들). parent = 그 형제들이 있는 폴더."""
    parent = Path(parent)
    return argparse.Namespace(config=str(REPO / "arena" / "corpus" / "c-all@v1.json"), policy=None,
                              snapshots=str(parent / "radar-snapshots"), adr=str(parent / "adr"),
                              repo=[f"decision-radar={REPO}", f"finance_verifier={parent / 'finance_verifier'}", f"fineprint={parent / 'fineprint'}"],
                              supplement=[f"source-hints@2026-10-12={parent / 'radar-snapshots' / 'supplements' / 'source-hints@2026-10-12' / 'items.jsonl'}"])


def run(candidate, out, revision=None, device=None, batch=8, chunk_k=100, info=None, siblings=PARENT, cache=None):
    """siblings = radar-snapshots · adr · 얼린 대상 repo가 있는 폴더 (기본: 이 레포의 부모). cache = 인코딩 캐시 폴더 (기본: arena/.cache)."""
    out = Path(out)
    info = info or card(candidate)
    revision = revision or info.get("revision")
    r = {"candidate": {"model": candidate, "revision": revision, "pipeline_tag": info.get("pipeline_tag"), "params": info.get("params")},
         "base": {"model": BASE_ID, "revision": BASE_REV, "expect_test": BASE_EXPECT["test"]}, "gold": GOLD.name, "chunk_k": chunk_k}
    v, why = screen(info)
    if v:
        r["verdict"], r["reason"] = v, why
        return finish(r, out)
    sys.path.insert(0, str(REPO))
    from huggingface_hub import snapshot_download
    from arena import cli, embed, harness
    if cache:
        embed.CACHE_ROOT = Path(cache)
    Candidate, AnyDimEmbedder, Flat = _encoders()
    cfg, policy, _, docs, chunks, _, _ = cli.build(corpus_args(siblings))
    chunk_to_doc = {c["chunk_id"]: c["doc_id"] for c in chunks}
    queries = harness.load_queries(GOLD / "queries.jsonl")
    qrels_all = harness.load_qrels(GOLD / "qrels.json")
    r.update(corpus=cfg["version"], docs=len(docs), chunks=len(chunks),
             chunk_digest=hashlib.sha256("\n".join(c["chunk_id"] for c in chunks).encode()).hexdigest()[:16])
    print(f"코퍼스 {cfg['version']} 문서 {len(docs)} · 청크 {len(chunks)} · 질의 {len(queries)}", flush=True)
    base_path = snapshot_download(BASE_ID, revision=BASE_REV)
    cand_enc = Candidate(candidate, revision, device=device, batch_size=batch)
    r["candidate"].update(max_length=cand_enc.max_length, query_prompt=cand_enc.query_prompt, doc_prompt=cand_enc.doc_prompt)
    arms = {"base": embed.Embedder(embed.BGEM3Encoder(device=device, model_path=base_path, revision=BASE_REV), cfg["version"], policy["version"],
                                   cache_root=embed.CACHE_ROOT),         # 기본 인자는 정의 때 굳는다 — --cache를 따르게 넘긴다
            "cand": AnyDimEmbedder(cand_enc, cfg["version"], policy["version"])}
    runs, r["arms"] = {}, {}
    for name, emb in arms.items():
        strat = Flat(name, emb)
        t0 = time.perf_counter()
        strat.index(chunks)
        secs = time.perf_counter() - t0
        runs[name], lat = harness.run_queries(strat, queries, chunk_to_doc, k_docs=20, chunk_k=chunk_k)
        r["arms"][name] = {"index_seconds": round(secs, 1), "query_ms_p50": round(sorted(lat)[len(lat) // 2], 1),
                           "vectors_mb": round(strat.mat.nbytes / 1e6, 1), "dim": int(strat.mat.shape[1]), "cache": emb.namespace}
        print(f"[{name}] 색인 {secs:.1f}s · 질의 p50 {r['arms'][name]['query_ms_p50']}ms · 차원 {strat.mat.shape[1]}", flush=True)
        (out / f"run-{name}.json").parent.mkdir(parents=True, exist_ok=True)
        (out / f"run-{name}.json").write_text(json.dumps(runs[name], ensure_ascii=False), encoding="utf-8")
    # 비용 — 품질이 구분 안 되면 결정은 여기서 갈린다 (사용자 2026-11-22). 색인 시간은 캐시면 0이라, 같은 표본을 캐시 없이 따로 잰다
    sample = [c["text"] for c in chunks[::max(1, len(chunks) // ENCODE_SAMPLE)][:ENCODE_SAMPLE]]
    for name, emb in arms.items():
        enc = emb.encoder
        if name == "base":       # 검색에 쓰는 dense만 — sparse까지 세면 지금 것이 불리하다
            fn = lambda xs, e=enc: e.model.encode(xs, batch_size=e.batch_size, max_length=e.max_length,  # noqa: E731
                                                  return_dense=True, return_sparse=False, return_colbert_vecs=False)
        else:
            fn = lambda xs, e=enc: e._enc(xs, "document" if e.doc_prompt else None)  # noqa: E731
        fn(sample[:8])
        t0 = time.perf_counter()
        fn(sample)
        secs = time.perf_counter() - t0
        r["arms"][name].update(params=n_params(enc.model), encode_sample=len(sample), encode_seconds=round(secs, 1), batch_size=enc.batch_size,
                               encode_per_s=round(len(sample) / secs, 1) if secs > 0 else None)
        print(f"[{name}] 매개변수 {r['arms'][name]['params']} · 인코딩 {r['arms'][name]['encode_per_s']}청크/초 (표본 {len(sample)})", flush=True)
    rng = random.Random(SEED)
    qtype = {q["qid"]: q["type"] for q in queries}
    for split in ("test", "dev"):
        qids = [q["qid"] for q in queries if q.get("split") == split and q["qid"] in qrels_all]
        qrels = {q: qrels_all[q] for q in qids}
        nd = {name: per_query_ndcg(qrels, {q: runs[name][q] for q in qids}) for name in runs}
        r[split] = {"by_type": compare(nd["base"], nd["cand"], qtype, qids, rng),
                    "per_query": {q: {"type": qtype[q], "base": round(nd["base"][q], 4), "cand": round(nd["cand"][q], 4)} for q in qids}}
    r["verdict"] = verdict_of(r["test"]["by_type"])
    if r["verdict"] == "baseline_mismatch":
        r["reason"] = f"지금 것 test 전체 {r['test']['by_type']['ALL']['base']} ≠ 등록값 {BASE_EXPECT['test']} (±{BASE_TOL})"
    return finish(r, out)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="arena.compare_embed", description="임베딩 후보를 Arena 정답지로 BGE-M3와 짝 비교 (HYO-240)")
    ap.add_argument("--candidate", required=True, help="HF 모델 id (예: Qwen/Qwen3-Embedding-0.6B)")
    ap.add_argument("--revision", help="기본: HF의 지금 revision")
    ap.add_argument("--out", required=True)
    ap.add_argument("--device", default=None)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--chunk-k", type=int, default=100)
    ap.add_argument("--siblings", default=str(PARENT), help="radar-snapshots · adr · 얼린 대상 repo가 있는 폴더 (기본: 이 레포의 부모)")
    ap.add_argument("--cache", default=None, help="인코딩 캐시 폴더 (기본: arena/.cache)")
    a = ap.parse_args(argv)
    run(a.candidate, a.out, revision=a.revision, device=a.device, batch=a.batch, chunk_k=a.chunk_k, siblings=a.siblings, cache=a.cache)
    return 0


if __name__ == "__main__":
    sys.exit(main())
