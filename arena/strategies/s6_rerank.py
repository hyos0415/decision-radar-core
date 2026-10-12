"""S6 Reranker (HYO-35, 이슈 #60) — S5a RRF 상위 50 문서를 cross-encoder로 다시 줄 세운다.

1. 1단계 = **S5a RRF**를 같은 실행 안에서 새로 돈다 (S2 · S4 새 색인·검색 — 저장된 결과 재사용 없음)
2. 융합 순위 상위 `depth`(기본 50) 문서만 재정렬한다. 그 밖의 문서는 돌려주지 않는다 (S6은 최대 depth개 문서)
3. 각 문서에서 **두 구성요소(S2 · S4)의 상위 `chunk_k` 청크 목록에 한 번이라도 나온 청크 전부**를 (질의, 청크) 쌍으로 채점한다
   (S5a의 대표 청크 하나만이 아니다)
4. 문서 점수 = 그 문서 청크 점수의 **최댓값**. 대표 청크 = 최고 점수 청크. 하네스 rollup은 문서당 하나뿐인 목록을 그대로 통과시킨다
5. 동점은 1단계(RRF) 순서가 앞선 문서가 앞이다 (문서 안에서 동점 청크는 후보 순서 — S2 순위 → S4 순위 — 가 앞선 쪽)

모델 `BAAI/bge-reranker-v2-m3` (cross-encoder, 로컬) · FlagEmbedding `FlagReranker` · **fp32** · 기기 cuda > mps > cpu ·
`max_length=1024`(질의+청크 쌍, 특수 토큰 포함). **조용히 잘리지 않게** 채점 전에 쌍마다 길이를 세어 넘으면 실패한다.
점수는 **원시 logit**이다 (sigmoid 정규화 없음 — 순위만 쓴다).
"""
import time
from importlib import metadata

from ..embed import TruncationError, pick_device, resolve_revision

MODEL_ID = "BAAI/bge-reranker-v2-m3"
MAX_LENGTH = 1024
BATCH_SIZE = 16
DEPTH = 50


class CrossEncoder:
    """FlagEmbedding `FlagReranker` 얇은 포장. `model=`로 가짜 모델을 넣을 수 있다(테스트 — 다운로드 없이).

    score(pairs) -> [float] (원시 logit, 입력 순서 그대로). model은 `.tokenizer`와 `.compute_score(pairs, **kw)`를 가진다.
    """

    def __init__(self, model_id=MODEL_ID, device=None, max_length=MAX_LENGTH, batch_size=BATCH_SIZE, model=None,
                 revision=None, model_path=None):
        self.model_id = model_id
        self.max_length = max_length
        self.batch_size = batch_size
        self.device = device or pick_device()
        if model is None:
            from FlagEmbedding import FlagReranker
            path, rev = (model_path, revision) if model_path and revision else resolve_revision(model_id)
            # query_max_length를 max_length와 같게 둔다 — 기본값(max_length의 3/4)이면 긴 질의가 조용히 잘린다
            model = FlagReranker(path, use_fp16=False, devices=self.device, max_length=max_length,
                                 query_max_length=max_length, normalize=False)
            revision = rev
        self.model = model
        self.revision = revision or "unknown"
        self.call_log = []               # 호출마다 쓴 인자
        self.pairs_scored = 0
        self.score_seconds = 0.0
        self.max_pair_tokens = 0         # 지금까지 본 가장 긴 쌍(특수 토큰 포함)

    def _settings(self):
        return {"batch_size": self.batch_size, "max_length": self.max_length, "normalize": False}

    def check_no_truncation(self, pairs):
        """특수 토큰 포함 쌍 길이가 max_length를 넘으면 실패한다 (FlagReranker는 조용히 자른다). -> 쌍별 길이"""
        if not pairs:
            return []
        ids = self.model.tokenizer([q for q, _ in pairs], [p for _, p in pairs],
                                   add_special_tokens=True, truncation=False)["input_ids"]
        lengths = [len(x) for x in ids]
        self.max_pair_tokens = max([self.max_pair_tokens, *lengths])
        over = [i for i, n in enumerate(lengths) if n > self.max_length]
        if over:
            raise TruncationError(f"질의+청크 쌍 {len(over)}건이 {self.max_length}토큰(특수 토큰 포함)을 넘는다 — 잘리면 점수가 달라진다: "
                                  f"index {over[:5]} 길이 {[lengths[i] for i in over[:5]]}")
        return lengths

    def score(self, pairs):
        pairs = [(q, p) for q, p in pairs]
        if not pairs:
            return []
        self.check_no_truncation(pairs)
        kw = self._settings()
        self.call_log.append(dict(kw))
        t0 = time.perf_counter()
        out = self.model.compute_score([list(p) for p in pairs], **kw)
        self.score_seconds += time.perf_counter() - t0
        if not isinstance(out, (list, tuple)):          # 쌍 하나면 숫자 하나로 돌려주는 구현이 있다
            out = [out]
        scores = [float(x) for x in out]
        if len(scores) != len(pairs):
            raise ValueError(f"점수 {len(scores)}건 ≠ 쌍 {len(pairs)}건")
        self.pairs_scored += len(pairs)
        return scores

    def info(self):
        libs = {}
        for name in ("torch", "FlagEmbedding", "transformers"):
            try:
                libs[name] = metadata.version(name)
            except metadata.PackageNotFoundError:
                libs[name] = None
        dev = {"device": self.device}
        try:
            import torch
            if self.device.startswith("cuda") and torch.cuda.is_available():
                dev["device_name"] = torch.cuda.get_device_name(0)
                dev["cuda"] = torch.version.cuda
        except Exception:
            pass
        return {
            "model_id": self.model_id, "revision": self.revision, "library_path": "FlagEmbedding.FlagReranker",
            "dtype": "float32", "max_length": self.max_length, "batch_size": self.batch_size,
            "score": "raw logit (sigmoid 정규화 없음)",
            **dev, "libraries": libs,
            "pairs_scored": self.pairs_scored, "max_pair_tokens": self.max_pair_tokens,
            "rerank_seconds_total": round(self.score_seconds, 3),
        }


class S6Rerank:
    id = "S6"
    version = "v1"

    def __init__(self, first_stage, reranker, depth=DEPTH):
        """first_stage = S5aRRF (`first_stage_candidates(query)`를 가진 것) · reranker = CrossEncoder 꼴(`score(pairs)`)"""
        if isinstance(depth, bool) or not isinstance(depth, int) or depth < 1:
            raise ValueError(f"rerank depth={depth!r} — 1 이상의 정수여야 한다")
        self.first, self.reranker, self.depth = first_stage, reranker, depth
        self.chunk_text = {}
        self.n_indexed = 0
        self.rerank_ms = []                  # 질의마다 재정렬(채점)에 쓴 시간
        self.pairs_per_query = []

    @property
    def took_ms(self):
        return self.first.took_ms

    @property
    def chunk_k(self):
        return self.first.chunk_k

    def settings(self):
        r = self.reranker
        return {
            "model": r.model_id, "revision": r.revision, "library": "FlagEmbedding.FlagReranker",
            "dtype": "float32", "max_length": r.max_length, "batch_size": r.batch_size,
            "score": "raw logit (sigmoid 정규화 없음)",
            "rerank_depth": self.depth,
            "candidates": f"1단계(S5a) 융합 상위 {self.depth} 문서 × 각 문서의 청크 중 S2·S4 상위 chunk_k 목록에 나온 것 전부",
            "document_score": "청크 점수의 최댓값 (대표 청크 = 최고 점수 청크)",
            "ties": "1단계 문서 순서 → 후보 청크 순서",
            "first_stage": {"id": self.first.id, "version": self.first.version, **self.first.settings()},
            "output": f"최대 {self.depth} 문서 (그 밖은 돌려주지 않는다)",
        }

    def index(self, chunks):
        self.chunk_text = {c["chunk_id"]: c["text"] for c in chunks}
        self.first.index(chunks)
        self.n_indexed = self.first.n_indexed

    def search(self, query, k):
        if k != self.chunk_k:
            raise ValueError(f"S6의 1단계(S5a)는 구성요소마다 같은 상위 N 청크(chunk_k={self.chunk_k})를 쓴다 — 요청 k={k}와 달라 거부한다")
        cand = self.first.first_stage_candidates(query)[:self.depth]        # [(doc, [chunk_id, ...])] 융합 순서
        pairs, owner = [], []
        for di, (_, chunk_ids) in enumerate(cand):
            for cid in chunk_ids:
                pairs.append((query, self.chunk_text[cid]))
                owner.append((di, cid))
        t0 = time.perf_counter()
        scores = self.reranker.score(pairs)
        self.rerank_ms.append((time.perf_counter() - t0) * 1000)
        self.pairs_per_query.append(len(pairs))
        best = {}                                                          # 문서 순번 → (점수, chunk_id)
        for (di, cid), s in zip(owner, scores):
            if di not in best or s > best[di][0]:                          # 엄격히 클 때만 — 동점은 먼저 온 청크
                best[di] = (s, cid)
        order = sorted(best, key=lambda di: (-best[di][0], di))            # 동점은 1단계 순서
        return [(best[di][1], best[di][0]) for di in order]

    def index_size_bytes(self):
        return self.first.index_size_bytes()

    def size_breakdown(self):
        return self.first.size_breakdown()

    def server_version(self):
        return self.first.server_version()

    def drop(self):
        self.first.drop()
