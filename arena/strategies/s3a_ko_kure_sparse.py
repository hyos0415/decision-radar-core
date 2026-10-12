"""S3a.ko KURE-Sparse-nano (HYO-35, 이슈 #66) — `nlpai-lab/KURE-Sparse-nano`(한국어 SPLADE, ModernBERT 계열)의 희소 표현으로 **로컬 정확 점수**를 낸다.

S3a(SPLADE-v3)와 같은 점수 방식(희소 내적 · 메모리 역색인 · ANN 없음)이고 모델만 다르다 — 그래서 `S3aSplade` 점수 코드와 `SpladeEmbedder` 캐시 · 집계를
그대로 쓰고 인코더 · 이름 · 기록만 갈라 둔다.

인코딩 (고정 — 바꾸면 전략 새 버전):
- `sentence_transformers.SparseEncoder(model_id, revision=<고정>, trust_remote_code=True)` — 이 모델은 연구실 자체 풀링 모듈
  (`1_ShiftedTopKSpladePooling/pooling.py`)을 쓴다. **원격 코드를 실행하므로 revision을 항상 고정해 코드가 바뀌지 않게 한다.**
  pooling.py는 전부 읽었다(텐서 연산 · 어휘 정규화 · 저장 때의 자기 복사만 있다 — 네트워크 · 서브프로세스 없음). 고정 revision 기준이다.
- 모델 자신의 prompts를 쓴다 — 질의 `"[Q] "` · 문서 `"[D] "` → `encode_query` / `encode_document`. 유사도 = dot. fp32.
- 길이: 청크는 bge-m3 토큰 기준으로 끊겨 있다. 이 모델의 토큰(WordPiece)으로 접두 지시문 포함 길이를 세어 `max_length`(8192 — 모델 위치 한도 안)까지
  잘림 없이 넣고, 넘으면 **실패**한다(조용히 자르지 않는다). 가장 긴 입력의 토큰 수 · 잘린 수(0)를 기록한다.
- 출력 = 어휘 정규화(canonical ids)로 접은 36282차원 중 0이 아닌 칸만 {칸 번호 문자열: 가중치}

캐시 (`arena/.cache/`, gitignore): 이름공간(코퍼스 · 청크 정책 · 모델 id+revision · fp32/max_length)마다 폴더 — S3a와 같은 `SparseCache`.
"""
from importlib import metadata

import numpy as np

from ..embed import TruncationError, pick_device
from .s3a_splade import S3aSplade, SpladeEmbedder

MODEL_ID = "nlpai-lab/KURE-Sparse-nano"
REVISION = "1eb281e81edcf44cbb401d475054f817b49fddbd"
MAX_LENGTH = 8192
BATCH_SIZE = 16


class KureSparseEncoder:
    """SparseEncoder 얇은 포장. `model=`로 가짜 모델을 넣을 수 있다(테스트 — 다운로드 없이).
    가짜 모델 계약: `.tokenizer(list, add_special_tokens=, truncation=)["input_ids"]` · `.prompts` {"query","document"} ·
    `.encode_query(list, ...)` · `.encode_document(list, ...)` → torch 텐서 [n, 차원] (희소 · 밀집 모두 가능) · `.max_seq_length` 속성.

    encode_docs(texts) / encode_queries(texts) -> ([{칸 번호 문자열: 가중치}], [잘렸나 bool])  — 입력 순서 그대로
    """
    library_names = ("torch", "sentence-transformers", "transformers")

    def __init__(self, model_id=MODEL_ID, revision=REVISION, device=None, max_length=MAX_LENGTH, batch_size=BATCH_SIZE, model=None):
        self.model_id = model_id
        self.max_length = max_length
        self.batch_size = batch_size
        self.device = device or pick_device()
        self.trust_remote_code = True
        if model is None:
            from sentence_transformers import SparseEncoder
            model = SparseEncoder(model_id, revision=revision, trust_remote_code=True, device=self.device)
        model.max_seq_length = max_length
        self.model = model
        self.revision = revision or "unknown"
        self.prompts = dict(getattr(model, "prompts", None) or {})
        for need in ("query", "document"):
            if not self.prompts.get(need):
                raise ValueError(f"{model_id}의 prompts에 {need!r}가 없다 — 모델의 접두 지시문을 쓰기로 했다: {self.prompts}")
        self.max_tokens_seen = 0
        self.call_log = []                     # 어느 메서드로 불렀나 — "query" / "document"

    def token_lengths(self, texts, kind):
        """모델 토크나이저로, 접두 지시문을 붙인 입력의 특수 토큰 포함 길이."""
        prefixed = [self.prompts[kind] + t for t in texts]
        return [len(x) for x in self.model.tokenizer(prefixed, add_special_tokens=True, truncation=False)["input_ids"]]

    def _encode(self, texts, kind):
        import torch
        texts = list(texts)
        method = self.model.encode_query if kind == "query" else self.model.encode_document
        out, truncated = [], []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i:i + self.batch_size]
            lengths = self.token_lengths(batch, kind)
            self.max_tokens_seen = max([self.max_tokens_seen] + lengths)
            over = [j for j, n in enumerate(lengths) if n > self.max_length]
            if over:
                raise TruncationError(f"{len(over)}건이 {self.max_length}토큰(접두 지시문 · 특수 토큰 포함)을 넘는다 — 조용히 자르지 않는다: "
                                      f"길이 {[lengths[j] for j in over[:5]]}")
            truncated += [False] * len(batch)
            self.call_log.append(kind)
            emb = method(batch, batch_size=len(batch), convert_to_tensor=True, convert_to_sparse_tensor=False,
                         show_progress_bar=False)
            if getattr(emb, "is_sparse", False):
                emb = emb.to_dense()
            rep = emb.detach().to(torch.float32).cpu().numpy()
            if rep.shape[0] != len(batch):
                raise ValueError(f"표현 {rep.shape[0]}건 ≠ 입력 {len(batch)}건")
            for row in rep:
                nz = np.nonzero(row)[0]
                out.append({str(int(j)): float(row[j]) for j in nz})
        if len(out) != len(texts):
            raise ValueError(f"표현 {len(out)}건 ≠ 입력 {len(texts)}건")
        return out, truncated

    def encode_docs(self, texts):
        return self._encode(texts, "document")

    def encode_queries(self, texts):
        return self._encode(texts, "query")


class KureSparseEmbedder(SpladeEmbedder):
    """`SpladeEmbedder`와 같은 캐시 · 측정 · 집계 — 문서용 · 질의용 메서드를 따로 부르는 것과 기록 내용만 다르다."""

    def _encode_chunk_texts(self, texts):
        return self.encoder.encode_docs(texts)

    def _encode_query_text(self, text):
        return self.encoder.encode_queries([text])

    def info(self):
        e = self.encoder
        libs = {}
        for name in e.library_names:
            try:
                libs[name] = metadata.version(name)
            except metadata.PackageNotFoundError:
                libs[name] = None
        dev = {"device": e.device}
        try:
            import torch
            if e.device.startswith("cuda") and torch.cuda.is_available():
                dev["device_name"] = torch.cuda.get_device_name(0)
                dev["cuda"] = torch.version.cuda
        except Exception:
            pass
        n = self.stats["chunks"]
        return {
            "model_id": e.model_id, "revision": e.revision, "trust_remote_code": e.trust_remote_code,
            "remote_code": "1_ShiftedTopKSpladePooling/pooling.py (revision 고정 · 전부 읽음 — 텐서 연산뿐)",
            "dtype": "float32", "max_length": e.max_length, "batch_size": e.batch_size,
            "prompts": {"query": e.prompts["query"], "document": e.prompts["document"]},
            "similarity": "dot", "same_encoder_for_queries_and_docs": False,
            "query_method": "encode_query", "document_method": "encode_document",
            **dev, "libraries": libs, "cache_namespace": self.namespace,
            "chunks": n, "cache_hits": self.stats["cache_hits"], "encoded": self.stats["encoded"],
            "truncated_chunks": self.stats["truncated_chunks"],
            "truncated_chunk_ids_sample": self.stats["truncated_chunk_ids"][:20],
            "truncation_policy": "청크 · 질의의 토큰 수(접두 지시문 포함)를 인코딩 전에 세어 max_length를 넘으면 실패한다 — 잘림 0이 보장된다",
            "max_tokens_seen_in_encoded": e.max_tokens_seen,
            "mean_nonzero_per_chunk": round(self.stats["nnz_total"] / n, 1) if n else None,
            "chunk_encode_seconds": round(self.stats["encode_seconds"], 3),
            "query_encode_seconds_total": round(self.stats["query_encode_seconds"], 3),
            "queries_encoded": self.stats["queries"], "queries_truncated": self.stats["queries_truncated"],
        }


class S3aKo(S3aSplade):
    """S3a.ko — 점수 · 색인은 S3a와 같고 표현만 KURE-Sparse-nano다."""
    id = "S3a.ko"
    version = "v1"

    def settings(self):
        e = self.embedder.encoder
        return {
            "embedding_model": f"{e.model_id}@{e.revision}",
            "representation": "ShiftedTopKSplade — 로짓 이동(−10) · relu · log1p 위치 최댓값 · 어휘 정규화 접기 (모델 자체 풀링 모듈)",
            "prompts": {"query": e.prompts["query"], "document": e.prompts["document"]},
            "scoring": "희소 내적 (정확 점수, ANN 없음) — S3a · S3b와 같은 방식",
            "max_length": e.max_length, "trust_remote_code": True,
            "truncation": "없다 — 토큰 수를 세어 넘으면 실패한다 (system.json splade.truncated_chunks = 0)",
            "zero_overlap_chunks": "순위에 넣지 않는다 (점수 0)",
            "index": "메모리 역색인 (칸 번호 → [(청크 행, 가중치)])",
            "index_size_estimate": "비영 항목 수 × 8바이트 (int32 칸 번호 + float32 가중치) — ES 인덱스 크기와 같은 잣대가 아니다",
        }
