"""화제 계층 — 부모 찾기 후보 (HYO-135). dev = W42 정답지(gold-w42.json) 하위 11짝에서 고른다. 모델 없음, 결정론.

정의 (gold-precondition-sheet — 사용자 2026-10-19): 하위 = ① 양자화·재포장본(GGUF 등), 부모 = 원본 모델
                                                    ② 한쪽을 써서 만든 도구·앱·활용 사례, 부모 = 쓰인 쪽
                                                    (같은 부모의 하위끼리는 다름 · 「X에 대한 글」은 하위가 아니다 · 다시 학습한 파생 모델은 하위가 아니다)

P1 파생 이름   자식 이름에 양자화·재포장 표시(GGUF · AWQ · MLX …)가 있고 부모 이름에는 없으며,
               부모 이름 토막이 자식 이름의 앞부분이다 — 또는 둘이 숫자 토막을 포함한 앞 2토막 이상을 함께 가진다
               (Qwen-Image-2.1-Turbo → Qwen-Image-2.1-Uncensored-Quantized)
P2 활용 언급   자식 제목에서 「쓴다」는 동사(runs · using · uses · built with/on · powered by · on top of) 바로 뒤(이름 낱말 수 + 3 안)에
               부모 모델 이름이 온다 — 「X로 만든 것」. 다른 낱말 뒤에 나오면(「tool named X」 · X에 대한 기사) 아니다.
               부모는 모델 이름을 가진 항목(HF 모델 · HF 링크 글 · GitHub repo · 「이름: 설명」 제목)
"""
import re

MARKERS = {"gguf", "quantized", "quant", "quants", "awq", "gptq", "mlx", "exl2", "exl3", "onnx", "int4", "int8", "fp8", "nvfp4",
           "mxfp4", "bnb", "4bit", "8bit", "w4a16", "coreml"}
USE_VERB = re.compile(r"\b(?:runs?|running|using|uses|built\s+(?:with|on)|powered\s+by|on\s+top\s+of)\s+", re.I)
HF_LINK = re.compile(r"([\w.-]+)/([\w.-]+)\s*·\s*Hugging Face", re.I)


def model_name(it):
    """항목의 모델·repo 이름 (owner 뺌). 없으면 None."""
    iid = str(it.get("id", ""))
    for pre in ("hf-model:", "github:"):
        if iid.startswith(pre) and "/" in iid:
            return iid[len(pre):].split("/", 1)[1]
    m = HF_LINK.search(it.get("title") or "")
    if m:
        return m.group(2)
    t = it.get("title") or ""
    if ":" in t:                                   # 「EmbeddingGemma 2: An open, lightweight …」 — 앞부분이 짧은 이름이면
        head = t.split(":", 1)[0].strip()
        if 1 <= len(head.split()) <= 4 and re.search(r"[A-Za-z]", head):
            return head
    return None


def tokens(name):
    """이름 → 토막. 구분자(- _ 공백 /)로 나누고 숫자 사이 점은 둔다 (2.1)."""
    return [x for x in re.split(r"[-_\s/]+", (name or "").lower()) if x]


def squash(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _derivative_parent(p, c):
    """P1: c가 p의 파생(양자화·재포장)인가."""
    tp, tc = tokens(model_name(p)), tokens(model_name(c))
    if not tp or not tc or MARKERS & set(tp) or not (MARKERS & set(tc)):
        return False
    if tc[:len(tp)] == tp and len(tc) > len(tp):
        return True
    k = 0
    while k < min(len(tp), len(tc)) and tp[k] == tc[k]:
        k += 1
    return k >= 2 and any(re.search(r"\d", x) for x in tp[:k])


def _use_parent(p, c):
    """P2: c의 제목이 p의 모델 이름을 말하고 「만든 것」 단서가 있다. c 자신이 모델 링크 글·파생이면 아니다."""
    name = model_name(p)
    if not name or len(squash(name)) < 5:
        return False
    title = c.get("title") or ""
    if HF_LINK.search(title) or MARKERS & set(tokens(model_name(c))):
        return False
    if squash(name) == squash(model_name(c)):
        return False
    for m in USE_VERB.finditer(title):
        after = " ".join(title[m.end():].split()[:len(name.split()) + 3])      # 동사 뒤 (이름 낱말 수 + 3) 낱말
        if squash(name) in squash(after):
            return True
    return False


def parent(a, b, rules=("P1", "P2")):
    """→ "a"(a가 부모) · "b"(b가 부모) · None. 두 방향이 다 맞으면 None (모르면 내지 않는다)."""
    def is_parent(p, c):
        return ("P1" in rules and _derivative_parent(p, c)) or ("P2" in rules and _use_parent(p, c))
    pa, pb = is_parent(a, b), is_parent(b, a)
    if pa == pb:
        return None
    return "a" if pa else "b"
