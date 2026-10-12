"""BGE-M3 임베딩 (HYO-35) — 한 번 통과로 dense(1024차원)와 lexical(sparse) 가중치를 같이 얻는다.

S4 Dense · S3b BGE-M3 sparse · S5a가 이 모듈 하나를 공유한다 (같은 모델 · 같은 입력 → 같은 벡터).

고정한 것 (결과를 본 뒤 바꾸지 않는다 — 바꾸면 전략 새 버전):
- 정밀도 **fp32** (`use_fp16=False`) — M1(MPS)과 Windows(CUDA)가 비슷한 벡터를 내야 기기 사이 비교가 된다
- `max_length=512` — 청크는 이미 bge-m3 토큰 512(특수 토큰 포함)로 끊겨 있다. **조용히 잘리지 않게** 인코딩 전에 길이를 세어 넘으면 실패한다
- 질의와 문서는 **같은 설정**(접두 지시문 없음 · 같은 max_length · 같은 batch 처리)으로 인코딩한다 — BGE-M3는 지시문이 필요 없다
- 기기 자동: cuda > mps > cpu

캐시 (`arena/.cache/`, gitignore): 이름공간(코퍼스 버전 · 청크 정책 버전 · 모델 id+revision · fp32/max_length)마다 폴더,
항목은 chunk_id + 청크 텍스트 sha256. 텍스트가 바뀌면 sha가 달라 다시 인코딩한다.
"""
import hashlib
import json
import os
import re
import time
from importlib import metadata
from pathlib import Path

import numpy as np

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

MODEL_ID = "BAAI/bge-m3"
DIM = 1024
MAX_LENGTH = 512
BATCH_SIZE = 16
CACHE_ROOT = Path(__file__).resolve().parent / ".cache"


class TruncationError(RuntimeError):
    pass


def pick_device():
    """cuda > mps > cpu"""
    import torch
    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"


def text_sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolve_revision(model_id=MODEL_ID, token=None):
    """-> (로컬 스냅숏 경로, HF revision 해시). 받아 둔 게 있으면 네트워크 없이도 된다.
    token = 게이트 모델(S3a SPLADE-v3)용 HF 토큰 — 명시로만 넘긴다(환경 변수에 넣지 않는다)."""
    from huggingface_hub import snapshot_download
    try:
        path = snapshot_download(model_id, token=token)
    except Exception:                                   # 오프라인이면 로컬 캐시만
        path = snapshot_download(model_id, local_files_only=True)
    return path, Path(path).name


def namespace_name(corpus_version, policy_version, model_id, revision, max_length=MAX_LENGTH):
    raw = f"{corpus_version}__{policy_version}__{model_id}-{revision[:12]}__fp32-L{max_length}"
    return re.sub(r"[^A-Za-z0-9._@-]+", "-", raw)


# --- 인코더 -------------------------------------------------------------------------------------------------------

class BGEM3Encoder:
    """FlagEmbedding BGEM3FlagModel 얇은 포장. `model=`로 가짜 모델을 넣을 수 있다(테스트 — 다운로드 없이).

    encode(texts) -> (dense float32 [n, 1024], sparse [{토큰 id 문자열: 가중치}])
    질의용 · 문서용 메서드가 따로 있지만 둘 다 같은 `_encode`를 같은 인자로 부른다.
    """

    def __init__(self, model_id=MODEL_ID, device=None, max_length=MAX_LENGTH, batch_size=BATCH_SIZE, model=None,
                 revision=None, model_path=None):
        self.model_id = model_id
        self.max_length = max_length
        self.batch_size = batch_size
        self.device = device or pick_device()
        if model is None:
            from FlagEmbedding import BGEM3FlagModel
            path, rev = (model_path, revision) if model_path and revision else resolve_revision(model_id)
            model = BGEM3FlagModel(path, use_fp16=False, devices=self.device,
                                   query_max_length=max_length, passage_max_length=max_length)
            revision = rev
        self.model = model
        self.revision = revision or "unknown"
        self.call_log = []               # 호출마다 쓴 인자 (질의·문서가 같은지 검사할 수 있게)

    def _settings(self):
        return {"batch_size": self.batch_size, "max_length": self.max_length,
                "return_dense": True, "return_sparse": True, "return_colbert_vecs": False}

    def check_no_truncation(self, texts):
        """특수 토큰 포함 길이가 max_length를 넘는 텍스트가 있으면 실패한다 (FlagEmbedding은 조용히 자른다)."""
        if not texts:
            return []
        ids = self.model.tokenizer(list(texts), add_special_tokens=True, truncation=False)["input_ids"]
        lengths = [len(x) for x in ids]
        over = [i for i, n in enumerate(lengths) if n > self.max_length]
        if over:
            raise TruncationError(f"{len(over)}건이 {self.max_length}토큰(특수 토큰 포함)을 넘는다 — 청크 상한이 어긋났다: "
                                  f"index {over[:5]} 길이 {[lengths[i] for i in over[:5]]}")
        return lengths

    def _encode(self, texts):
        texts = list(texts)
        if not texts:
            return np.zeros((0, DIM), dtype=np.float32), []
        self.check_no_truncation(texts)
        kw = self._settings()
        self.call_log.append(dict(kw))
        out = self.model.encode(texts, **kw)
        dense = np.asarray(out["dense_vecs"], dtype=np.float32)
        if dense.shape != (len(texts), DIM):
            raise ValueError(f"dense 모양 {dense.shape} ≠ ({len(texts)}, {DIM})")
        sparse = [{str(k): float(v) for k, v in lw.items()} for lw in out["lexical_weights"]]
        if len(sparse) != len(texts):
            raise ValueError(f"lexical_weights {len(sparse)}건 ≠ 입력 {len(texts)}건")
        return dense, sparse

    def encode_docs(self, texts):
        return self._encode(texts)

    def encode_queries(self, texts):
        return self._encode(texts)


# --- 캐시 ---------------------------------------------------------------------------------------------------------

class EncodingCache:
    """디스크 캐시 한 이름공간. index.json {chunk_id: {sha, row}} · dense.npy [n, 1024] · sparse.json [{...}]"""

    def __init__(self, directory):
        self.dir = Path(directory)
        self.index, self.dense, self.sparse = {}, np.zeros((0, DIM), dtype=np.float32), []
        self.dirty = False
        if (self.dir / "index.json").is_file():
            self.index = json.loads((self.dir / "index.json").read_text(encoding="utf-8"))
            self.dense = np.load(self.dir / "dense.npy")
            self.sparse = json.loads((self.dir / "sparse.json").read_text(encoding="utf-8"))
            if not (len(self.index) == len(self.dense) == len(self.sparse)):
                raise ValueError(f"캐시가 깨졌다(항목 수 불일치) — 폴더를 지우고 다시 만든다: {self.dir}")

    def get(self, chunk_id, sha):
        """sha가 같을 때만 (dense, sparse). 다르거나 없으면 None."""
        e = self.index.get(chunk_id)
        if e is None or e["sha"] != sha:
            return None
        return self.dense[e["row"]], self.sparse[e["row"]]

    def put_many(self, items):
        """items = [(chunk_id, sha, dense, sparse)] — 같은 chunk_id는 덮어쓴다(새 행으로, 옛 행은 save 때 정리)."""
        if not items:
            return
        rows = {cid: (sha, d, s) for cid, sha, d, s in items}
        keep = {cid: e for cid, e in self.index.items() if cid not in rows}
        new_dense, new_sparse, new_index = [], [], {}
        for cid, e in keep.items():
            new_index[cid] = {"sha": e["sha"], "row": len(new_sparse)}
            new_dense.append(self.dense[e["row"]])
            new_sparse.append(self.sparse[e["row"]])
        for cid, (sha, d, s) in rows.items():
            new_index[cid] = {"sha": sha, "row": len(new_sparse)}
            new_dense.append(d)
            new_sparse.append(s)
        self.index, self.sparse = new_index, new_sparse
        self.dense = np.stack(new_dense).astype(np.float32) if new_dense else np.zeros((0, DIM), dtype=np.float32)
        self.dirty = True

    def save(self):
        if not self.dirty:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = {"index.json": json.dumps(self.index), "sparse.json": json.dumps(self.sparse)}
        for name, text in tmp.items():
            (self.dir / (name + ".tmp")).write_text(text, encoding="utf-8")
        with open(self.dir / "dense.npy.tmp", "wb") as f:
            np.save(f, self.dense)
        for name in ("dense.npy", "sparse.json", "index.json"):          # index.json을 마지막에 — 중간에 죽으면 옛 캐시로 읽힌다
            os.replace(self.dir / (name + ".tmp"), self.dir / name)
        self.dirty = False


# --- 청크 · 질의 임베딩 (전략이 쓰는 쪽) -------------------------------------------------------------------------------

class Embedder:
    """인코더 + 캐시 + 측정. 같은 실행 안에서 전략들이 하나를 공유한다.

    encode_chunks(chunks) -> (dense [n, 1024], sparse [dict])  — chunks 순서 그대로
    encode_query(text)    -> (dense [1024], sparse dict)
    """

    def __init__(self, encoder, corpus_version, policy_version, cache_root=CACHE_ROOT, use_cache=True):
        self.encoder = encoder
        self.corpus_version, self.policy_version = corpus_version, policy_version
        self.namespace = namespace_name(corpus_version, policy_version, encoder.model_id, encoder.revision, encoder.max_length)
        self.cache = EncodingCache(Path(cache_root) / self.namespace) if use_cache else None
        self.stats = {"chunks": 0, "cache_hits": 0, "encoded": 0, "encode_seconds": 0.0,
                      "queries": 0, "query_encode_seconds": 0.0}
        self._memo = {}          # 같은 실행 안에서 두 번째 전략이 다시 안 세게 — (chunk_id 목록 해시 → 결과)

    def encode_chunks(self, chunks):
        key = hashlib.sha256("\n".join(f"{c['chunk_id']}:{text_sha256(c['text'])}" for c in chunks).encode()).hexdigest()
        if key in self._memo:
            return self._memo[key]
        shas = [text_sha256(c["text"]) for c in chunks]
        dense = np.zeros((len(chunks), DIM), dtype=np.float32)
        sparse = [None] * len(chunks)
        todo = []
        for i, (c, sha) in enumerate(zip(chunks, shas)):
            hit = self.cache.get(c["chunk_id"], sha) if self.cache else None
            if hit is None:
                todo.append(i)
            else:
                dense[i], sparse[i] = hit
        self.stats["chunks"] += len(chunks)
        self.stats["cache_hits"] += len(chunks) - len(todo)
        if todo:
            t0 = time.perf_counter()
            d, s = self.encoder.encode_docs([chunks[i]["text"] for i in todo])
            self.stats["encode_seconds"] += time.perf_counter() - t0
            self.stats["encoded"] += len(todo)
            for j, i in enumerate(todo):
                dense[i], sparse[i] = d[j], s[j]
            if self.cache:
                self.cache.put_many([(chunks[i]["chunk_id"], shas[i], dense[i], sparse[i]) for i in todo])
                self.cache.save()
        self._memo[key] = (dense, sparse)
        return dense, sparse

    def encode_query(self, text):
        t0 = time.perf_counter()
        d, s = self.encoder.encode_queries([text])
        self.stats["query_encode_seconds"] += time.perf_counter() - t0
        self.stats["queries"] += 1
        return d[0], s[0]

    def info(self):
        """env.json · system.json에 기록할 것: 모델 · revision · 기기 · dtype · 라이브러리 버전 · 인코딩 시간."""
        libs = {}
        for name in ("torch", "FlagEmbedding", "transformers"):
            try:
                libs[name] = metadata.version(name)
            except metadata.PackageNotFoundError:
                libs[name] = None
        dev = {"device": self.encoder.device}
        try:
            import torch
            if self.encoder.device.startswith("cuda") and torch.cuda.is_available():
                dev["device_name"] = torch.cuda.get_device_name(0)
                dev["cuda"] = torch.version.cuda
        except Exception:
            pass
        return {
            "model_id": self.encoder.model_id, "revision": self.encoder.revision,
            "dtype": "float32", "max_length": self.encoder.max_length, "batch_size": self.encoder.batch_size,
            "dense_dim": DIM, "instruction_prefix": None,
            **dev, "libraries": libs,
            "cache_namespace": self.namespace,
            "chunks": self.stats["chunks"], "cache_hits": self.stats["cache_hits"], "encoded": self.stats["encoded"],
            "chunk_encode_seconds": round(self.stats["encode_seconds"], 3),
            "query_encode_seconds_total": round(self.stats["query_encode_seconds"], 3),
            "queries_encoded": self.stats["queries"],
        }
