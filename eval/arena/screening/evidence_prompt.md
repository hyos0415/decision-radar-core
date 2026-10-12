# 근거 발췌 지시문 (2026-10-12, Sonnet 5.5 서브에이전트 5개)

사람이 직접 볼 카드 158장(2점 · ? · 스크리닝 누락 · 표본)을 글자 수로 5묶음(`ev-in/batch<N>.md`, 카드 = 질의 · 유형 기준 · 제목 · 저장된 본문 전문)으로 나눠 아래 지시를 그대로 줬다. 출력 `ev-out/batch<N>.json`은 `build_ev.py`가 검사한다 — 인용이 본문(또는 제목)의 연속 부분 문자열이 아니면 버린다.

왜: 카드 본문이 길어(최대 8,000자, vendor 페이지는 더 김) 사람이 원문을 대조해야 판정할 수 있었다. 등급은 주지 않는다 — 판정은 사람 몫, 근거는 판정 재료.

---

You are extracting evidence for a human who is grading a retrieval gold set. Read the whole file `ev-in/batch<N>.md` (read it in chunks until the end; do not skip any card).

It contains CARDS. Each card has a header `===== CARD <qid> | <doc_id> =====`, a Korean query (질의), a rubric for that query type (유형 기준), a title, and a document body between `----- 본문 시작 -----` and `----- 본문 끝 -----`.

For EVERY card, find the passage(s) in the body that bear on the query — the sentence/line that answers it, or the closest thing to it. The human will not read the whole body; your quote + explanation is what lets them grade quickly. Do NOT assign a grade.

Output: a JSON array `ev-out/batch<N>.json`, one object per card, in file order:
{"qid": "...", "doc_id": "...", "quotes": ["..."], "ko": "..."}

Rules:
- `quotes`: 0–3 strings, each copied EXACTLY from that card's body or title — a contiguous substring, ≤ 300 characters each. A script will verify each quote is a substring of the body (whitespace-normalized), so do not paraphrase, translate, fix typos, or join non-adjacent pieces. Prefer the single most relevant line.
- `ko`: ONE Korean sentence (≤ 120 chars) for a Korean reader who does not know the technology: what the quoted passage says, in relation to the query. Plain facts from the body only; no background knowledge, no grade words (관련/무관/정답 같은 판정어 금지).
- If the body has nothing that bears on the query, `quotes` = [] and `ko` says so specifically, naming what is missing. If the body is empty, judge from the title and say "본문 없음 — 제목만: …".
- Output must be valid JSON; the count must equal the number of CARD headers.
