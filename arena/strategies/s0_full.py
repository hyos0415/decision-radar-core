"""S0 Full Context — 검색기로서의 Full Context. 정의는 `docs/design/retrieval-arena.md` §6 변경 (2026-10-28, S0 설계).

T0 코퍼스 전체를 시스템 프롬프트에 넣고, 질의마다 모델이 관련 문서 id를 최대 20개 고른다.
순서 = C-ADR → C-CODE → C-DOC, 각 안에서 문서 id 순. 질의 글은 사용자 메시지 맨 끝(유형·nodes는 주지 않는다).
이 파일은 컨텍스트 조립과 출력 해석만 한다 — 실행(구독 claude -p)은 따로 붙인다.
"""
import json
import re

VERSION = "v1"
MAX_DOCS = 20
CORPUS_ORDER = ("c-adr", "c-code", "c-doc")

INSTRUCTIONS = """너는 문서 검색기다. 아래 <corpus> 안의 문서들이 검색 대상 전부다. 각 문서는 <doc id="…"> 로 시작한다.
사용자가 질의를 주면, 그 질의에 답하는 데 필요한 문서를 관련이 높은 순서로 최대 20개 고른다.
- 문서 id는 <doc id="…">에 적힌 그대로 쓴다. 코퍼스에 없는 id를 만들지 않는다
- 관련 문서가 20개보다 적으면 적게 낸다
- 출력은 JSON 한 줄만: {"docs": ["문서 id", …]} — 설명·코드 블록 없이"""


def ordered(docs):
    """등록한 순서 — 코퍼스 순 → 문서 id 순. 모르는 코퍼스는 끝에."""
    rank = {c: i for i, c in enumerate(CORPUS_ORDER)}
    return sorted(docs, key=lambda d: (rank.get(d.get("corpus"), len(rank)), d["doc_id"]))


def render_doc(d):
    body = (d.get("title") or "").strip() + "\n" + (d.get("text") or "").strip()
    return f'<doc id="{d["doc_id"]}">\n{body.strip()}\n</doc>'


def build_system(docs):
    """시스템 프롬프트 = 지시 + 코퍼스. 질의마다 같으므로 캐시된다."""
    return INSTRUCTIONS + "\n\n<corpus>\n" + "\n".join(render_doc(d) for d in ordered(docs)) + "\n</corpus>"


def build_user(query_text):
    return "질의: " + query_text.strip()


_JSON = re.compile(r"\{.*\}", re.S)


def parse(output, known_ids):
    """모델 출력 → (문서 id 순위, 보고). 코퍼스에 없는 id는 버리고 센다 · 중복은 첫 것만 · 파싱 실패는 빈 순위."""
    rep = {"parse_error": False, "invented": 0, "duplicates": 0, "over_limit": 0}
    m = _JSON.search(output or "")
    try:
        ids = json.loads(m.group(0))["docs"] if m else None
        if not isinstance(ids, list):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        rep["parse_error"] = True
        return [], rep
    out, seen = [], set()
    for x in ids:
        x = str(x)
        if x not in known_ids:
            rep["invented"] += 1
        elif x in seen:
            rep["duplicates"] += 1
        else:
            seen.add(x)
            out.append(x)
    rep["over_limit"] = max(0, len(out) - MAX_DOCS)
    return out[:MAX_DOCS], rep
