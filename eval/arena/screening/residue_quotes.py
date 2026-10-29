"""test 잔여 카드의 인용 뽑기 — 구독 `codex exec`(GPT-6 Luna, API 비용 없음) (확정 시트 §4 변경 2026-10-29, HYO-155)

python residue_quotes.py prep <residue_dir> --docs-json docs.json --queries test-queries.jsonl --out <dir> [--chars 90000]
python residue_quotes.py run <dir> [--model gpt-6-luna]        # 묶음마다 codex exec를 동시에 띄운다
python residue_quotes.py merge <dir>                           # → <dir>/quotes.json (residue_cards.py --quotes 입력)

- 묻는 것: 「이 문서에서 질의에 닿는 줄은 어디인가」 — 등급은 묻지 않고, Luna의 1차 등급·이유도 주지 않는다
- 10/12 근거 발췌(`evidence_prompt.md`)와 같은 지시 — 인용 0~3개(본문 글자 그대로) + 한국어 한 줄(판정어 금지)
- 인용 검사는 residue_cards.py가 한다 — 본문·제목에 글자 그대로 없는 인용은 카드에 보이지 않는다(규율 1). merge는 센 수만 알린다
"""
import argparse
import concurrent.futures
import json
import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from residue_cards import alias_map, build_cards, checked_quotes  # noqa: E402

# G4 — 유형별 「답」 (gold-precondition-sheet §1, residue_cards.html의 ANSWER와 같다)
ANSWER = {
    "B": ("묻는 일을 하는 기술의 1차 출처 (repo · 모델 카드 · 논문 · 릴리스 노트)", "그 기술을 언급만 하는 글 · 비슷하지만 묻는 일을 하지 않는 기술"),
    "C": ("그 변경이 처음 들어간 릴리스 항목", "같은 기능을 언급하는 다른 버전의 릴리스 · 그 버전을 다루는 기타 문서"),
    "E": ("① 그 패키지·버전의 호환성 변경(breaking·deprecation·의존성 하한)을 적은 문서 ② 그 패키지의 우리 쪽 버전 고정을 적은 manifest·스택 절", "같은 패키지의 다른 릴리스 · 버전 고정이 없는 스택 절"),
    "J": ("그 재검토 조건의 충족·미충족을 본문만으로 판정할 수 있는 문서", "조건의 기술을 다루지만 판정 근거가 안 되는 문서 · 해당 ADR 자체"),
    "K": ("technology와 use가 같은 ADR", "technology만 같고 use가 다른 ADR"),
}

PROMPT = """You are extracting evidence for a human who is grading a retrieval gold set. The CARDS are in the <stdin> block below.

Each card has a header `===== CARD <n> | <qid> | <doc_id> =====`, a Korean query (질의), what counts as an answer for that query type (답 / 답 아님), clues the query author wrote (단서), a title, and a document body between `----- 본문 시작 -----` and `----- 본문 끝 -----`.

For EVERY card, find the line(s) in the body that bear on the query — the line that answers it, or the closest thing to it, or the line that shows why it is only related. The human will not read the whole body; your quote + explanation is what lets them check the document quickly. Do NOT assign a grade.

Output ONLY a JSON array (no prose, no code fence), one object per card, in card order:
{"card": <n>, "qid": "...", "doc_id": "...", "quotes": ["..."], "ko": "..."}

Rules:
- `quotes`: 0–3 strings, each copied EXACTLY from that card's body or title — a contiguous substring, ≤ 300 characters each. A script verifies each quote is a substring of the body (whitespace-normalized) and drops any that is not, so do not paraphrase, translate, fix typos, or join non-adjacent pieces. Prefer the single most relevant line.
- `ko`: ONE Korean sentence (≤ 120 chars) for a Korean reader who does not know the technology: what the quoted line says, in relation to the query. Plain facts from the body only; no background knowledge, no grade words (관련/무관/정답/답이 된다 같은 판정어 금지).
- If the body has nothing that bears on the query, `quotes` = [] and `ko` says so specifically, naming what is missing. If the body is empty, use the title and start `ko` with "본문 없음 — 제목만: ".
- The count of objects must equal the number of CARD headers.
"""


def card_md(c, g):
    ans, not_ans = ANSWER[g["type"]]
    clues = []
    if g.get("expected_version"):
        clues.append(f"찾는 버전 {g['expected_version']}")
    clues += [" / ".join(v for v in n.values()) for n in g["nodes"]]
    return "\n".join([
        f"===== CARD {c['card']} | {c['qid']} | {c['doc_id']} =====",
        f"질의 ({g['type']}): {g['query']}",
        f"답: {ans}",
        f"답 아님(관련만): {not_ans}",
        f"단서: {' · '.join(clues) or '없음'}",
        f"제목: {c['title']}",
        "----- 본문 시작 -----",
        c["text"],
        "----- 본문 끝 -----",
        "",
    ])


def prep(a):
    docs = {d["doc_id"]: d for d in json.loads(pathlib.Path(a.docs_json).read_text(encoding="utf-8"))}
    queries = {}
    for line in pathlib.Path(a.queries).read_text(encoding="utf-8").splitlines():
        if line.strip():
            q = json.loads(line)
            queries[q["qid"]] = q
    aliases = alias_map(json.loads(pathlib.Path(a.nodes).read_text(encoding="utf-8")))
    _, groups = build_cards(a.residue, docs, queries, aliases)
    out = pathlib.Path(a.out)
    (out / "in").mkdir(parents=True, exist_ok=True)
    batches, cur, size = [], [], 0
    for g in groups:
        for c in g["cards"]:
            md = card_md(c, g)
            if cur and size + len(md) > a.chars:
                batches.append(cur)
                cur, size = [], 0
            cur.append(md)
            size += len(md)
    if cur:
        batches.append(cur)
    for i, b in enumerate(batches, 1):
        (out / "in" / f"batch{i}.md").write_text("\n".join(b), encoding="utf-8", newline="\n")
    (out / "prompt.md").write_text(PROMPT, encoding="utf-8", newline="\n")
    print(f"카드 {sum(len(b) for b in batches)}장 → 묶음 {len(batches)}개 ({', '.join(str(len(b)) for b in batches)})")


def run_one(out, batch, model):
    res = out / "out" / f"{batch.stem}.json"
    log = out / "out" / f"{batch.stem}.log"
    # 지시문과 카드를 모두 stdin으로 — Windows에서 codex.cmd 인자로 여러 줄 문자열을 넘기면 깨진다
    cmd = [shutil.which("codex"), "exec", "-m", model, "-s", "read-only", "--skip-git-repo-check",
           "-c", "windows.sandbox=unelevated", "-c", "model_reasoning_effort=medium", "-o", str(res), "-"]
    stdin = (PROMPT + "\n<stdin>\n" + batch.read_text(encoding="utf-8") + "\n</stdin>\n").encode("utf-8")
    with open(log, "wb") as lf:
        rc = subprocess.run(cmd, input=stdin, stdout=lf, stderr=subprocess.STDOUT, cwd=out).returncode
    return batch.stem, rc


def run(a):
    out = pathlib.Path(a.dir)
    (out / "out").mkdir(exist_ok=True)
    batches = sorted((out / "in").glob("batch*.md"), key=lambda p: int(p.stem[5:]))
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(batches)) as ex:
        for stem, rc in ex.map(lambda b: run_one(out, b, a.model), batches):
            print(stem, "exit", rc)


def merge(a):
    out = pathlib.Path(a.dir)
    texts, by_card = {}, {}
    for f in (out / "in").glob("batch*.md"):
        for block in f.read_text(encoding="utf-8").split("===== CARD ")[1:]:
            head, _, rest = block.partition(" =====\n")
            n, qid, doc = [s.strip() for s in head.split("|")]
            title = rest.split("제목: ", 1)[1].split("\n", 1)[0]
            body = rest.split("----- 본문 시작 -----\n", 1)[1].rsplit("\n----- 본문 끝 -----", 1)[0]
            texts[(qid, doc)] = (body, title)
            by_card[int(n)] = (qid, doc)
    quotes, st = {}, {"cards": 0, "quotes": 0, "kept": 0, "empty": 0, "unknown": 0}
    for f in sorted((out / "out").glob("batch*.json")):
        raw = f.read_text(encoding="utf-8").strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        for r in json.loads(raw):
            key = (r.get("qid"), r.get("doc_id"))
            # 칸을 빠뜨리거나 밀려 적으면(qid 칸에 doc_id) 카드 번호로 — 번호의 짝과 하나는 맞아야 한다
            if key not in texts and set(key) & set(by_card.get(r.get("card"), ())):
                key = by_card[r["card"]]
                r = {**r, "qid": key[0], "doc_id": key[1]}
            if key not in texts:
                st["unknown"] += 1
                print("모르는 카드", key)
                continue
            st["cards"] += 1
            qs = [s for s in r.get("quotes") or [] if isinstance(s, str)]
            body, title = texts[key]
            kept = checked_quotes(qs, body) + [s for s in checked_quotes(qs, title) if s not in checked_quotes(qs, body)]
            st["quotes"] += len(qs)
            st["kept"] += len(kept)
            st["empty"] += not kept
            quotes.setdefault(r["qid"], {})[r["doc_id"]] = {"quotes": qs, "ko": r.get("ko", "")}
    print("빠진 카드", sorted(set(texts) - {(q, d) for q, m in quotes.items() for d in m}))
    (out / "quotes.json").write_text(json.dumps(quotes, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(st)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prep")
    p.add_argument("residue")
    p.add_argument("--docs-json", required=True)
    p.add_argument("--queries", required=True)
    p.add_argument("--nodes", default=str(HERE.parent / "nodes@v2.json"))
    p.add_argument("--out", required=True)
    p.add_argument("--chars", type=int, default=90000)
    r = sub.add_parser("run")
    r.add_argument("dir")
    r.add_argument("--model", default="gpt-6-luna")
    m = sub.add_parser("merge")
    m.add_argument("dir")
    a = ap.parse_args()
    {"prep": prep, "run": run, "merge": merge}[a.cmd](a)


if __name__ == "__main__":
    main()
