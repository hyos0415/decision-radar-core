"""토큰 수 세기 — 청크 상한을 두 토크나이저 모두에서 지키기 위한 도구.

`tokenizers` 패키지로 tokenizer.json만 받는다 (torch · transformers 없음). 처음 한 번 HF에서 내려받고 로컬 캐시를 쓴다.
계약: `count(texts, special=False) -> [(토큰 수 per tokenizer, ...)]` · `overhead`(특수 토큰 수 per tokenizer) · `names`.
테스트는 같은 모양의 가짜 카운터로 네트워크 없이 돈다.
"""
import os

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


class HFTokenCounter:
    def __init__(self, names):
        from tokenizers import Tokenizer
        self.names = tuple(names)
        self._tk = []
        for n in self.names:
            t = Tokenizer.from_pretrained(n)
            t.no_truncation()          # 자르지 않고 길이만 잰다
            t.no_padding()
            self._tk.append(t)
        self.overhead = tuple(len(t.encode("").ids) for t in self._tk)   # [CLS][SEP] · <s></s>

    def count(self, texts, special=False):
        if not texts:
            return []
        per_tok = [t.encode_batch(list(texts), add_special_tokens=special) for t in self._tk]
        return [tuple(len(enc[i].ids) for enc in per_tok) for i in range(len(texts))]
