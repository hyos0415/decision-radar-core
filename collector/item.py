"""공통 항목 형식. 모든 출처 어댑터는 Item을 만든다.

수집 단계는 자르지 않는다 (doc-pipeline §3 원칙 1) — 점수·스타·나이 같은 값은 signals/meta에 그대로 담아
필터(HYO-73)가 특징으로 쓰게 한다.
"""
import hashlib
import html
import re
from dataclasses import asdict, dataclass, field

TEXT_MAX = 8000
_HREF = re.compile(r"""href=["']([^"']+)["']""", re.I)
_URL = re.compile(r"https?://[^\s<>\"')\]]+")
# 링크로 치지 않는 것: 출처 자체의 사용자·댓글·이미지 페이지
_SKIP_LINK = re.compile(r"(reddit\.com/(user|u)/|reddit\.com/r/[^/]+/comments/|redd\.it|i\.redd\.it|"
                        r"preview\.redd\.it|news\.ycombinator\.com/(user|item))", re.I)


@dataclass
class Item:
    id: str                      # "<source>:<원래 id>" — 출처 안에서 유일
    source: str                  # reddit · hn · github · huggingface · vendor
    channels: list               # 이 항목을 가져온 통로들 (중복 제거 때 합친다)
    title: str
    url: str
    created_at: str = ""         # ISO 8601, 출처가 주는 값
    text: str = ""               # 본문·설명·초록 (TEXT_MAX에서 자름)
    signals: dict = field(default_factory=dict)   # points · comments · stars · rank · trending_score · upvotes
    links: list = field(default_factory=list)     # 본문에서 뽑은 외부 링크 (2단계 1차 출처 확인의 첫 후보)
    meta: dict = field(default_factory=dict)      # 출처별 부가 정보

    def to_dict(self):
        d = asdict(self)
        d["text"] = d["text"][:TEXT_MAX]
        return d


def extract_links(markup):
    """HTML·텍스트에서 외부 링크를 순서대로, 중복 없이 뽑는다."""
    if not markup:
        return []
    found = _HREF.findall(markup) + _URL.findall(html.unescape(markup))
    out = []
    for u in found:
        u = html.unescape(u).strip().rstrip(".,;")
        if not u.startswith("http") or _SKIP_LINK.search(u) or u in out:
            continue
        out.append(u)
    return out


_SCRIPT = re.compile(r"<(script|style|noscript|svg)\b.*?</\1>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def html_to_text(markup):
    """페이지 비교용 본문 텍스트. 원본 HTML 해시는 요청마다 달라서(빌드 ID 등) 텍스트로 비교한다."""
    t = _SCRIPT.sub(" ", markup or "")
    t = _TAG.sub(" ", t)
    return _WS.sub(" ", html.unescape(t)).strip()


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def merge(items):
    """같은 id를 하나로 합친다. 통로는 합치고, 나머지는 먼저 본 값을 두고 빈 칸만 채운다."""
    by_id = {}
    for it in items:
        cur = by_id.get(it.id)
        if cur is None:
            by_id[it.id] = it
            continue
        for ch in it.channels:
            if ch not in cur.channels:
                cur.channels.append(ch)
        for k, v in it.signals.items():
            cur.signals.setdefault(k, v)
        for k, v in it.meta.items():
            cur.meta.setdefault(k, v)
        for u in it.links:
            if u not in cur.links:
                cur.links.append(u)
        cur.text = cur.text or it.text
        cur.created_at = cur.created_at or it.created_at
    return list(by_id.values())
