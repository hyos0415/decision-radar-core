"""S3a SPLADE-v3 (HYO-35, 이슈 #63) — `naver/splade-v3`(Hugging Face 게이트 모델)의 학습된 희소 표현으로 **로컬 정확 점수**를 낸다.

S3b와 같은 점수 방식(희소 내적 · 메모리 역색인 · ANN 없음)이고 표현만 다르다 — SPLADE는 입력에 없는 **확장어**에도 가중치를 준다.

인코딩 (고정 — 바꾸면 전략 새 버전):
- `AutoModelForMaskedLM` + 같은 모델의 토크나이저. 표현 = **토큰 위치별 log(1 + relu(logits))을 attention mask로 가려 위치 축 최댓값**
  (어휘 크기 벡터 하나 → 0이 아닌 칸만 {토큰 id: 가중치})
- 질의와 문서는 **같은 인코더 · 같은 설정**. fp32. 기기 cuda > mps > cpu 자동 (embed.pick_device)
- **512토큰(특수 토큰 포함)에서 자른다 — 사전에 정한 정책**: 청크는 bge-m3 토큰 512로 끊겨 있어 BERT 토큰(한국어는 더 잘게 쪼개진다)으로는
  넘을 수 있다. BGE-M3 쪽처럼 실패하지 않고 **SPLADE의 한계에서 자르되 잘린 청크 수를 센다** → system.json `splade.truncated_chunks`
- 게이트 모델이라 `.env`의 `HF_TOKEN`이 필요하다 (`.env.example` 참고). 읽기만 하고 os.environ에 넣지 않으며 `token=`으로 명시해 넘긴다.
  값은 출력·기록하지 않는다. 없으면 한국어 메시지로 실패한다.

캐시 (`arena/.cache/`, gitignore): 이름공간(코퍼스 · 청크 정책 · 모델 id+revision · fp32/max_length)마다 폴더,
항목은 chunk_id + 청크 텍스트 sha256 + 잘림 여부.
"""
import hashlib
import json
import os
import subprocess
import time
from importlib import metadata
from pathlib import Path

import numpy as np

from ..embed import CACHE_ROOT, namespace_name, pick_device, resolve_revision, text_sha256
from .s3b_bgem3_sparse import S3bSparse

MODEL_ID = "naver/splade-v3"
MAX_LENGTH = 512
BATCH_SIZE = 16
REPO = Path(__file__).resolve().parents[2]
TOKEN_KEY = "HF_TOKEN"


class HFTokenError(RuntimeError):
    pass


def _env_files():
    """읽을 .env 후보 — 이 체크아웃의 .env, 없으면 같은 repo의 메인 체크아웃 .env (git worktree는 .env가 없다)."""
    files = [REPO / ".env"]
    try:
        r = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=REPO,
                           capture_output=True, text=True, timeout=20)
        if r.returncode == 0 and r.stdout.strip():
            files.append(Path(r.stdout.strip()).parent / ".env")
    except (OSError, subprocess.SubprocessError):
        pass
    return files


def load_hf_token(env_files=None, key=TOKEN_KEY):
    """.env의 HF_TOKEN을 읽어 돌려준다 (product/spike/common/env.py load_dotenv와 같은 방식 — KEY=VALUE 줄만 읽고 os.environ에 넣지 않는다).
    없거나 비었으면 HFTokenError. 메시지에 값은 넣지 않는다."""
    for path in (_env_files() if env_files is None else env_files):
        path = Path(path)
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if k.strip() == key and v:
                    return v
    raise HFTokenError(f"S3a SPLADE-v3는 Hugging Face 게이트 모델이라 .env의 {key}가 필요한데 비어 있거나 없다. "
                       f".env.example의 {key} 칸 안내대로 라이선스에 동의하고 Read 토큰을 .env에 넣는다.")


def splade_pool(logits, attention_mask):
    """logits [배치, 위치, 어휘] · attention_mask [배치, 위치] → [배치, 어휘] = max_위치( log(1 + relu(logits)) · mask )."""
    import torch
    act = torch.log1p(torch.relu(logits)) * attention_mask.unsqueeze(-1).to(logits.dtype)
    return act.max(dim=1).values


class SpladeEncoder:
    """AutoModelForMaskedLM + 토크나이저 얇은 포장. `model=` · `tokenizer=`로 가짜를 넣을 수 있다(테스트 — 다운로드 없이).

    encode(texts) -> ([{토큰 id 문자열: 가중치}], [잘렸나 bool])  — 입력 순서 그대로
    질의용 · 문서용이 같은 `encode`를 쓴다.
    """

    def __init__(self, model_id=MODEL_ID, device=None, max_length=MAX_LENGTH, batch_size=BATCH_SIZE, model=None,
                 tokenizer=None, revision=None, token=None, env_files=None):
        self.model_id = model_id
        self.max_length = max_length
        self.batch_size = batch_size
        self.device = device or pick_device()
        if model is None:
            import torch
            from transformers import AutoModelForMaskedLM, AutoTokenizer
            token = token or load_hf_token(env_files)
            path, revision = resolve_revision(model_id, token=token)
            tokenizer = AutoTokenizer.from_pretrained(path, token=token)
            model = AutoModelForMaskedLM.from_pretrained(path, token=token, dtype=torch.float32)
            model = model.to(self.device).eval()
        self.model, self.tokenizer = model, tokenizer
        self.revision = revision or "unknown"
        self.max_tokens_seen = 0

    def token_lengths(self, texts):
        ids = self.tokenizer(list(texts), add_special_tokens=True, truncation=False)["input_ids"]
        return [len(x) for x in ids]

    def encode(self, texts):
        import torch
        texts = list(texts)
        out, truncated = [], []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i:i + self.batch_size]
            lengths = self.token_lengths(batch)
            self.max_tokens_seen = max(self.max_tokens_seen, *lengths)
            truncated += [n > self.max_length for n in lengths]
            enc = self.tokenizer(batch, add_special_tokens=True, truncation=True, max_length=self.max_length,
                                 padding=True, return_tensors="pt")
            enc = {k: v.to(self.device) for k, v in enc.items()}
            with torch.no_grad():
                logits = self.model(**enc).logits
            rep = splade_pool(logits.float(), enc["attention_mask"]).cpu().numpy()
            for row in rep:
                nz = np.nonzero(row)[0]
                out.append({str(int(j)): float(row[j]) for j in nz})
        if len(out) != len(texts):
            raise ValueError(f"SPLADE 표현 {len(out)}건 ≠ 입력 {len(texts)}건")
        return out, truncated


class SparseCache:
    """디스크 캐시 한 이름공간. index.json {chunk_id: {sha, row, truncated}} · sparse.json [{...}]"""

    def __init__(self, directory):
        self.dir = Path(directory)
        self.index, self.sparse, self.dirty = {}, [], False
        if (self.dir / "index.json").is_file():
            self.index = json.loads((self.dir / "index.json").read_text(encoding="utf-8"))
            self.sparse = json.loads((self.dir / "sparse.json").read_text(encoding="utf-8"))
            if len(self.index) != len(self.sparse):
                raise ValueError(f"캐시가 깨졌다(항목 수 불일치) — 폴더를 지우고 다시 만든다: {self.dir}")

    def get(self, chunk_id, sha):
        e = self.index.get(chunk_id)
        if e is None or e["sha"] != sha:
            return None
        return self.sparse[e["row"]], e["truncated"]

    def put_many(self, items):
        """items = [(chunk_id, sha, sparse, truncated)] — 같은 chunk_id는 덮어쓴다."""
        if not items:
            return
        rows = {cid: (sha, s, tr) for cid, sha, s, tr in items}
        new_index, new_sparse = {}, []
        for cid, e in self.index.items():
            if cid not in rows:
                new_index[cid] = {"sha": e["sha"], "row": len(new_sparse), "truncated": e["truncated"]}
                new_sparse.append(self.sparse[e["row"]])
        for cid, (sha, s, tr) in rows.items():
            new_index[cid] = {"sha": sha, "row": len(new_sparse), "truncated": tr}
            new_sparse.append(s)
        self.index, self.sparse, self.dirty = new_index, new_sparse, True

    def save(self):
        if not self.dirty:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        for name, obj in (("sparse.json", self.sparse), ("index.json", self.index)):
            (self.dir / (name + ".tmp")).write_text(json.dumps(obj), encoding="utf-8")
        for name in ("sparse.json", "index.json"):          # index.json을 마지막에 — 중간에 죽으면 옛 캐시로 읽힌다
            os.replace(self.dir / (name + ".tmp"), self.dir / name)
        self.dirty = False


class SpladeEmbedder:
    """인코더 + 캐시 + 측정. S3b가 쓰는 Embedder와 같은 모양(`encode_chunks`/`encode_query`가 (dense 자리 None, sparse)를 돌려준다).

    잘림 집계: `truncated_chunks`는 인코딩을 요청한 청크 중 SPLADE 토큰이 max_length를 넘은 수 (캐시에서 읽은 것도 센다)."""

    def __init__(self, encoder, corpus_version, policy_version, cache_root=CACHE_ROOT, use_cache=True):
        self.encoder = encoder
        self.corpus_version, self.policy_version = corpus_version, policy_version
        self.namespace = namespace_name(corpus_version, policy_version, encoder.model_id, encoder.revision, encoder.max_length)
        self.cache = SparseCache(Path(cache_root) / self.namespace) if use_cache else None
        self.stats = {"chunks": 0, "cache_hits": 0, "encoded": 0, "encode_seconds": 0.0, "truncated_chunks": 0,
                      "truncated_chunk_ids": [], "nnz_total": 0,
                      "queries": 0, "query_encode_seconds": 0.0, "queries_truncated": 0}
        self._memo = {}

    def encode_chunks(self, chunks):
        key = hashlib.sha256("\n".join(f"{c['chunk_id']}:{text_sha256(c['text'])}" for c in chunks).encode()).hexdigest()
        if key in self._memo:
            return self._memo[key]
        shas = [text_sha256(c["text"]) for c in chunks]
        sparse, trunc, todo = [None] * len(chunks), [False] * len(chunks), []
        for i, (c, sha) in enumerate(zip(chunks, shas)):
            hit = self.cache.get(c["chunk_id"], sha) if self.cache else None
            if hit is None:
                todo.append(i)
            else:
                sparse[i], trunc[i] = hit
        self.stats["chunks"] += len(chunks)
        self.stats["cache_hits"] += len(chunks) - len(todo)
        if todo:
            t0 = time.perf_counter()
            s, tr = self.encoder.encode([chunks[i]["text"] for i in todo])
            self.stats["encode_seconds"] += time.perf_counter() - t0
            self.stats["encoded"] += len(todo)
            for j, i in enumerate(todo):
                sparse[i], trunc[i] = s[j], tr[j]
            if self.cache:
                self.cache.put_many([(chunks[i]["chunk_id"], shas[i], sparse[i], trunc[i]) for i in todo])
                self.cache.save()
        self.stats["truncated_chunks"] += sum(trunc)
        self.stats["truncated_chunk_ids"] += [c["chunk_id"] for c, t in zip(chunks, trunc) if t]
        self.stats["nnz_total"] += sum(len(s) for s in sparse)
        self._memo[key] = (None, sparse)
        return None, sparse

    def encode_query(self, text):
        t0 = time.perf_counter()
        s, tr = self.encoder.encode([text])
        self.stats["query_encode_seconds"] += time.perf_counter() - t0
        self.stats["queries"] += 1
        self.stats["queries_truncated"] += int(tr[0])
        return None, s[0]

    def info(self):
        """system.json `splade`에 기록할 것: 모델 · revision · 기기 · dtype · 버전 · 잘린 청크 수 · 인코딩 시간. (토큰 값은 없다.)"""
        libs = {}
        for name in ("torch", "transformers", "huggingface_hub"):
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
        n = self.stats["chunks"]
        return {
            "model_id": self.encoder.model_id, "revision": self.encoder.revision,
            "dtype": "float32", "max_length": self.encoder.max_length, "batch_size": self.encoder.batch_size,
            "pooling": "max over tokens of log(1 + relu(logits)), attention-masked",
            "same_encoder_for_queries_and_docs": True,
            "hf_token": f"{TOKEN_KEY} read from .env (value not recorded)",
            **dev, "libraries": libs, "cache_namespace": self.namespace,
            "chunks": n, "cache_hits": self.stats["cache_hits"], "encoded": self.stats["encoded"],
            "truncated_chunks": self.stats["truncated_chunks"],
            "truncated_chunk_ids_sample": self.stats["truncated_chunk_ids"][:20],
            "truncation_policy": "SPLADE의 max_length(특수 토큰 포함)에서 자르고 잘린 청크 수를 센다 — 청크는 bge-m3 토큰 기준이라 BERT 토큰으로는 넘을 수 있다 (사전 결정)",
            "max_tokens_seen_in_encoded": self.encoder.max_tokens_seen,
            "mean_nonzero_per_chunk": round(self.stats["nnz_total"] / n, 1) if n else None,
            "chunk_encode_seconds": round(self.stats["encode_seconds"], 3),
            "query_encode_seconds_total": round(self.stats["query_encode_seconds"], 3),
            "queries_encoded": self.stats["queries"], "queries_truncated": self.stats["queries_truncated"],
        }


class S3aSplade(S3bSparse):
    id = "S3a"
    version = "v1"

    def settings(self):
        e = self.embedder.encoder
        return {
            "embedding_model": f"{e.model_id}@{e.revision}",
            "representation": "SPLADE — 토큰별 log(1+relu(logits)) 위치 최댓값 (확장어 포함)",
            "scoring": "희소 내적 (정확 점수, ANN 없음) — S3b와 같은 방식",
            "max_length": e.max_length,
            "truncation": "SPLADE max_length에서 자르고 잘린 청크 수를 system.json splade.truncated_chunks에 센다",
            "zero_overlap_chunks": "순위에 넣지 않는다 (점수 0)",
            "index": "메모리 역색인 (토큰 id → [(청크 행, 가중치)])",
            "index_size_estimate": "비영 항목 수 × 8바이트 (int32 토큰 id + float32 가중치) — ES 인덱스 크기와 같은 잣대가 아니다",
        }
