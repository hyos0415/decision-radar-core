"""Arena 하네스 v0 단위 테스트 — ES·네트워크 없이 돈다 (작은 픽스처 + 가짜 토큰 카운터 + 임시 git repo).

잡혀야 할 것이 잡히는지(규율 3): 새 검사마다 일부러 틀린 입력을 하나씩 넣어, 검사가 실제로 실패를 내는지 확인한다.

실행: python arena/test_arena.py   (PyYAML · ranx 필요 — arena/requirements.txt)
"""
import json
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")      # Windows 파이프(cp949)에서도 한글 출력이 안 죽게
sys.path.insert(0, str(HERE.parent))

import yaml  # noqa: E402

from arena import chunking, corpus, embed, es, harness  # noqa: E402
from arena.strategies import s2_bm25, s3b_bgem3_sparse, s4_dense, s5a_rrf, s6_rerank  # noqa: E402

FAILS = []


def case(name):
    def deco(fn):
        try:
            fn()
            print(f"PASS {name}")
        except Exception:
            FAILS.append(name)
            print(f"FAIL {name}")
            traceback.print_exc()
        return fn
    return deco


def raises(exc, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except exc:
        return True
    return False


class FakeCounter:
    """토크나이저 둘을 흉내 낸다: A = 공백 단어 수, B = 글자 수 / 3 올림. 특수 토큰 2개."""
    names = ("tok-a", "tok-b")
    overhead = (2, 2)

    def count(self, texts, special=False):
        extra = 2 if special else 0
        return [(len(t.split()) + extra, -(-len(t) // 3) + extra) for t in texts]


def policy(cap):
    return {"version": "chunk-policy@vT", "max_tokens": cap, "tokenizers": list(FakeCounter.names),
            "split_heading_levels": [2, 3], "table_header_rows": 2}


def chunker(cap=60):
    return chunking.Chunker(policy(cap), FakeCounter())


def doc(doc_id, corpus_name, title, text, meta=None):
    return corpus.make_doc(doc_id, corpus_name, title, text, meta or {"url": "u"})


def within_cap(chunks, cap):
    fc = FakeCounter()
    return all(all(c <= cap for c in fc.count([ch["text"]], special=True)[0]) for ch in chunks)


# --- source_type ------------------------------------------------------------------------------------------------

@case("source_type: id 접두어 → 정해진 값 (github-release가 github보다 먼저)")
def _():
    want = {"github-release:a/b@v1": "release_note", "github:a/b": "repo", "hf-model:x/y": "model_card",
            "hf-paper:2606.1": "paper", "vendor:anthropic:x": "official_doc", "vendor-page:openai:u": "official_doc",
            "source-hint:foo": "official_doc", "reddit:t3_x": "community", "hn:1": "community"}
    for i, st in want.items():
        assert corpus.source_type_of(i, "c-doc") == st, i
    assert corpus.source_type_of("adr:0001", "c-adr") == "adr"
    assert corpus.source_type_of("stack:r/CLAUDE.md#stack", "c-code") == "stack"


@case("source_type: 모르는 접두어는 조용히 넘기지 않고 실패한다")
def _():
    assert raises(ValueError, corpus.source_type_of, "twitter:1", "c-doc")


# --- C-ADR ------------------------------------------------------------------------------------------------------

ADR = """---
status: hold
date: 2026-09-29
technology: LEANN
affected:
- repo: decision-radar
  component: retrieval-arena
revisit_when:
- id: c1
  condition: 조건 하나
revisit_last_checked:
- condition_id: c1
  checked_at: '2026-10-05'
  result: not_met
  reason: null
  evidence_urls:
  - repo:decision-radar/arena/results
use: 저장 절감
---
# LEANN 제목

## Context
본문 `revisit_last_checked` 라는 말은 본문에 남는다.

## Decision Outcome
보류 — 기준선이 생기면 본다. 비교 상대는 S4 Dense이고 같은 임베딩에 index만 바꾼다.
"""


def _fm(text):
    fm, _ = corpus.split_frontmatter(text)
    return yaml.safe_load("\n".join(fm))


def no_revisit_answer(text):
    """frontmatter에 revisit_last_checked 칸의 흔적(키 · 안의 값)이 남지 않았는가."""
    fm, _ = corpus.split_frontmatter(text)
    blob = "\n".join(fm)
    return "revisit_last_checked" not in blob and "checked_at" not in blob and "not_met" not in blob


@case("C-ADR: revisit_last_checked 칸만 빠지고 나머지 frontmatter·본문은 그대로")
def _():
    d = corpus.adr_doc("0004-leann.md", ADR, "fa3a4cf", ["revisit_last_checked"])
    assert d["doc_id"] == "adr:0004" and d["source_type"] == "adr" and d["corpus"] == "c-adr"
    assert d["title"] == "LEANN 제목"
    assert no_revisit_answer(d["text"])
    before = _fm(ADR)
    before.pop("revisit_last_checked")
    assert _fm(d["text"]) == before                       # 다른 칸(뒤에 오는 use 포함)은 값까지 같다
    assert "## Context" in d["text"] and "본문 `revisit_last_checked` 라는 말은" in d["text"]
    assert d["meta"]["status"] == "hold" and d["meta"]["commit"] == "fa3a4cf"


@case("C-ADR 검사가 잡는다: 키 줄만 지운 틀린 제거는 no_revisit_answer가 실패시킨다")
def _():
    broken = ADR.replace("revisit_last_checked:\n", "", 1)          # 키 줄만 지우면 값이 다른 칸에 붙어 남는다
    assert not no_revisit_answer(broken)
    assert not no_revisit_answer(ADR)


@case("C-ADR: 키가 없으면 frontmatter가 그대로 (과제거 없음)")
def _():
    fm, _ = corpus.split_frontmatter(ADR)
    assert corpus.strip_frontmatter_key(fm, "없는_키") == fm


@case("C-ADR: 파일 이름이 NNNN-이 아니거나 frontmatter가 없으면 실패")
def _():
    assert raises(ValueError, corpus.adr_doc, "readme.md", ADR, "c", [])
    assert raises(ValueError, corpus.adr_doc, "0001-x.md", "# 제목만", "c", [])


# --- 임시 git repo: 특정 커밋에서 읽는다 -------------------------------------------------------------------------------

def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True,
                   capture_output=True)


def mkrepo(tmp, files):
    repo = Path(tmp)
    git(repo, "init", "-q")
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "c1")
    return repo, subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


@case("C-ADR: git show <커밋>:docs/adr/*.md — 커밋 이후 작업 트리 변경은 안 보인다")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        repo, commit = mkrepo(tmp, {"docs/adr/0001-a.md": ADR, "docs/adr/.gitkeep": "", "docs/adr/notes.txt": "x"})
        (repo / "docs/adr/0001-a.md").write_text("바뀜", encoding="utf-8")
        docs = corpus.load_c_adr(repo, commit)
        assert [d["doc_id"] for d in docs] == ["adr:0001"]
        assert "LEANN 제목" in docs[0]["text"] and no_revisit_answer(docs[0]["text"])
        assert raises(FileNotFoundError, corpus.load_c_adr, repo, "0" * 40)


# --- C-CODE -----------------------------------------------------------------------------------------------------

CLAUDE = """# 제목

## 작업 절차
```bash
# 코드 안의 주석은 heading이 아니다
## 스택 흉내
```

## 스택 · 평가셋 · 알려진 병목
스택 본문

### 의존성
| a | b |

```
## 펜스 안 heading
```

## 다음 절
여기는 아니다
"""


@case("C-CODE: CLAUDE.md는 「스택」 heading부터 같은 수준의 다음 heading 전까지만 (하위 heading 포함)")
def _():
    s = corpus.extract_stack_section(CLAUDE)
    assert s.startswith("## 스택 · 평가셋 · 알려진 병목") and "스택 본문" in s and "### 의존성" in s
    assert "## 펜스 안 heading" in s                    # 펜스 안은 절 경계가 아니다
    assert "다음 절" not in s and "작업 절차" not in s and "스택 흉내" not in s


@case("C-CODE 검사가 잡는다: 스택 절이 없으면 실패 · 마지막 절이면 끝까지")
def _():
    assert raises(ValueError, corpus.extract_stack_section, "# a\n## b\nx")
    assert corpus.extract_stack_section("# a\n### 스택\nx\n#### 하위\ny").endswith("y")


@case("C-CODE: id 규칙 — CLAUDE.md는 #stack, manifest는 경로, 커밋에서 읽는다")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        repo, commit = mkrepo(tmp, {"CLAUDE.md": CLAUDE, "product/requirements.txt": "PyYAML==6\n"})
        docs = corpus.load_c_code({"decision-radar": repo}, [
            {"name": "decision-radar", "commit": commit, "files": ["CLAUDE.md", "product/requirements.txt"]}])
        ids = [d["doc_id"] for d in docs]
        assert ids == ["stack:decision-radar/CLAUDE.md#stack", "stack:decision-radar/product/requirements.txt"], ids
        assert all(d["source_type"] == "stack" and d["corpus"] == "c-code" for d in docs)
        assert docs[1]["text"] == "PyYAML==6"
        assert raises(ValueError, corpus.load_c_code, {}, [{"name": "x", "commit": commit, "files": ["a"]}])


# --- C-DOC ------------------------------------------------------------------------------------------------------

def write_jsonl(path, rows):
    Path(path).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def vendor_item(url="https://docs.example.com/a b/c?x=1", vendor="anthropic"):
    it = {"id": f"vendor-page:{vendor}:{url}", "source": "vendor", "title": "[v] page", "url": url, "text": "",
          "created_at": "2026-10-01", "meta": {"vendor": vendor, "kind": "page"}}
    return it


@case("C-DOC: vendor-page는 page_file() 규칙대로 pages/의 전문을 읽는다 (빈 text 대신)")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "2026-W41"
        it = vendor_item()
        rel = corpus.page_file(it)
        (snap / rel).parent.mkdir(parents=True)
        (snap / rel).write_text("전문 한 줄\n둘째 줄\n", encoding="utf-8")
        gh = {"id": "github-release:o/r@v1", "source": "github", "title": "o/r v1", "url": "u", "text": "## 1\nx", "meta": {}}
        write_jsonl(snap / "items.jsonl", [it, gh])
        docs = corpus.load_c_doc(tmp, "2026-W41", [])
        v = next(d for d in docs if d["doc_id"].startswith("vendor-page:"))
        assert v["text"] == "전문 한 줄\n둘째 줄" and v["source_type"] == "official_doc"
        assert "text_file" not in v["meta"] and v["meta"]["url"] == it["url"] and v["meta"]["vendor"] == "anthropic"


@case("C-DOC 검사가 잡는다: 전문 파일 없음 · meta.text_file이 규칙과 다름")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "2026-W41"
        snap.mkdir()
        it = vendor_item()
        write_jsonl(snap / "items.jsonl", [it])
        assert raises(FileNotFoundError, corpus.load_c_doc, tmp, "2026-W41", [])
        it["meta"]["text_file"] = "pages/anthropic/다른-이름.txt"
        write_jsonl(snap / "items.jsonl", [it])
        assert raises(ValueError, corpus.load_c_doc, tmp, "2026-W41", [])
        assert raises(FileNotFoundError, corpus.load_c_doc, tmp, "2099-W01", [])


@case("C-DOC: reddit·hn의 HTML text는 텍스트로, 보충 jsonl(source-hint:*)은 추가, 다른 id는 거부")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "2026-W41"
        snap.mkdir()
        rd = {"id": "reddit:t3_a", "source": "reddit", "title": "t", "url": "u", "text": "<p>안녕 <b>hi</b></p><p>둘째</p>", "meta": {}}
        write_jsonl(snap / "items.jsonl", [rd])
        sup = Path(tmp) / "hints.jsonl"
        write_jsonl(sup, [{"id": "source-hint:x", "title": "x", "url": "https://x", "text": "hint text", "status": 200}])
        docs = corpus.load_c_doc(tmp, "2026-W41", [sup])
        assert [d["doc_id"] for d in docs] == ["reddit:t3_a", "source-hint:x"]
        assert docs[0]["text"] == "안녕 hi\n둘째" and docs[0]["source_type"] == "community"
        assert docs[1]["source_type"] == "official_doc" and docs[1]["text"] == "hint text"
        write_jsonl(sup, [{"id": "reddit:zzz", "title": "x", "url": "u", "text": "t"}])
        assert raises(ValueError, corpus.load_c_doc, tmp, "2026-W41", [sup])


@case("코퍼스 설정 c-all@v1.json — 고정값이 맞고 경로는 들어 있지 않다")
def _():
    cfg = corpus.load_config(HERE / "corpus" / "c-all@v1.json")
    assert cfg["version"] == "c-all@v1" and cfg["c-doc"]["snapshot_week"] == "2026-W41"
    assert cfg["c-doc"]["supplements"] == ["source-hints@2026-10-12"]
    assert cfg["c-adr"]["commit"] == "fa3a4cf" and cfg["c-adr"]["strip_frontmatter_keys"] == ["revisit_last_checked"]
    repos = {r["name"]: (r["commit"], r["files"]) for r in cfg["c-code"]["repos"]}
    assert repos["finance_verifier"] == ("ef426d2", ["CLAUDE.md"])
    assert repos["fineprint"] == ("18123e2", ["CLAUDE.md", "requirements.txt"])
    assert repos["decision-radar"][0] == "bb43a82" and len(repos["decision-radar"][1]) == 4
    blob = json.dumps(cfg)
    assert ":/" not in blob and "\\\\" not in blob and "Users" not in blob
    pol = chunking.load_policy(HERE / "corpus" / "chunk-policy@v1.json")
    assert pol["max_tokens"] == 512 and pol["tokenizers"] == ["BAAI/bge-m3"] and pol["status"] == "fixed"
    assert cfg["chunk_policy"] == pol["version"]


# --- chunk-policy@v1 --------------------------------------------------------------------------------------------

DOC = """# 문서 제목

머리말 한 줄

## 설치
설치 본문 하나

### 옵션
옵션 본문

#### 깊은 제목
깊은 본문 (H4는 안 자른다)

```
## 펜스 안 제목은 안 자른다
```

## 사용
사용 본문
"""


@case("chunk: H2/H3로 자르고 모든 조각 앞에 위치 경로, 조각 id = <doc_id>#<n>, 메타 상속")
def _():
    d = doc("github-release:o/r@v1", "c-doc", "o/r v1", DOC, {"url": "https://u", "tag": "v1"})
    chunks = chunker(200).chunk_document(d)
    paths = [c["path"] for c in chunks]
    assert paths == ["o/r v1", "o/r v1 > 설치", "o/r v1 > 설치 > 옵션", "o/r v1 > 사용"], paths
    assert [c["chunk_id"] for c in chunks] == [f"github-release:o/r@v1#{i}" for i in range(4)]
    for c in chunks:
        assert c["text"].split("\n")[0] == c["path"]
        assert c["meta"] == {"url": "https://u", "tag": "v1"} and c["source_type"] == "release_note"
        assert c["doc_id"] == "github-release:o/r@v1" and not c["cut"]
    opt = chunks[2]["text"]
    assert "깊은 본문" in opt and "#### 깊은 제목" in opt and "## 펜스 안 제목은 안 자른다" in opt
    assert chunks[2]["meta"] is not d["meta"]            # 복사본 (한 조각의 수정이 다른 조각에 번지지 않게)


@case("chunk: 헤딩 없는 긴 글은 문단 경계로, 모든 조각이 두 토크나이저 상한 이하")
def _():
    paras = ["단어 " * 12 for _ in range(10)]
    d = doc("hn:1", "c-doc", "제목", "\n\n".join(p.strip() for p in paras))
    ch = chunker(40).chunk_document(d)
    assert len(ch) > 1 and all(c["cut"] for c in ch)
    assert within_cap(ch, 40), [c["tokens"] for c in ch]
    joined = " ".join(c["text"].split("\n", 1)[1].replace("\n", " ") for c in ch)
    assert joined.count("단어") == 120                    # 한 글자도 빠지지 않는다


@case("chunk: 표는 행 단위로 자르고 머리행을 조각마다 반복한다")
def _():
    rows = "\n".join(f"| 행{i} | 값{i} |" for i in range(30))
    d = doc("vendor-page:x:u", "c-doc", "가격", f"## 표\n| 이름 | 값 |\n|---|---|\n{rows}")
    ch = chunker(45).chunk_document(d)
    assert len(ch) > 2 and within_cap(ch, 45)
    for c in ch:
        body = c["text"].split("\n")[1:]
        assert body[0] == "| 이름 | 값 |" and body[1] == "|---|---|", c["text"]
    seen = [ln for c in ch for ln in c["text"].split("\n") if ln.startswith("| 행")]
    assert seen == [f"| 행{i} | 값{i} |" for i in range(30)]       # 행은 순서대로 한 번씩


@case("chunk: 공백 없는 아주 긴 토막도 글자 단위로 잘라 상한 안에 넣는다")
def _():
    d = doc("hn:2", "c-doc", "t", "x" * 600)
    ch = chunker(40).chunk_document(d)
    assert within_cap(ch, 40) and "".join(c["text"].split("\n", 1)[1] for c in ch) == "x" * 600


@case("chunk: 본문 없는 문서는 제목만 가진 조각 하나 (사라지지 않는다)")
def _():
    ch = chunker().chunk_document(doc("hf-model:a/b", "c-doc", "a/b", ""))
    assert len(ch) == 1 and ch[0]["text"] == "a/b" and ch[0]["chunk_id"] == "hf-model:a/b#0"


@case("chunk: ADR — 상한 안이면 파일 하나(frontmatter 포함), 넘으면 frontmatter 조각 + 요약이 앞에 붙은 헤딩 조각")
def _():
    d = corpus.adr_doc("0004-leann.md", ADR, "fa3a4cf", ["revisit_last_checked"])
    one = chunker(400).chunk_document(d)
    assert len(one) == 1 and one[0]["text"].startswith("LEANN 제목\n---\nstatus: hold") and not one[0]["cut"]
    ch = chunker(90).chunk_document(d)
    assert len(ch) > 1 and within_cap(ch, 90)
    assert ch[0]["path"] == "LEANN 제목 > frontmatter" and ch[0]["text"].split("\n")[1] == "status: hold"
    body = [c for c in ch if "Context" in c["path"]]
    assert body and body[0]["text"].startswith("[adr:0004 | status: hold | date: 2026-09-29 | technology: LEANN")
    assert all(c["source_type"] == "adr" for c in ch)
    assert not any(c["text"].strip() == "LEANN 제목" for c in ch)    # 제목뿐인 조각은 만들지 않는다
    assert all("revisit_last_checked" not in c["text"] or "본문" in c["text"] for c in ch)   # 본문의 말은 남는다


@case("chunk: manifest는 파일 하나, 상한을 넘으면 줄 경계로 자른다 · 기타는 고정 길이")
def _():
    small = doc("stack:r/req.txt", "c-code", "r/req.txt", "a==1\nb==2")
    assert len(chunker().chunk_document(small)) == 1
    big = doc("stack:r/compose.yaml", "c-code", "r/compose.yaml", "\n".join(f"line{i}: value{i}" for i in range(40)))
    ch = chunker(30).chunk_document(big)
    assert len(ch) > 2 and within_cap(ch, 30) and all(c["kind"] == "manifest" for c in ch)
    lines = [ln for c in ch for ln in c["text"].split("\n")[1:]]
    assert lines == [f"line{i}: value{i}" for i in range(40)]            # 줄이 중간에서 안 잘렸다
    other = doc("hn:9", "c-weird", "t", "w " * 100)
    oc = chunker(30).chunk_document(other)
    assert oc[0]["kind"] == "other" and len(oc) > 1 and within_cap(oc, 30)


@case("chunk 통계: 코퍼스별 조각 수·잘린 수·본문 없는 문서 수·분포 — 상한 초과는 over_cap으로 드러난다")
def _():
    docs = [doc("hn:1", "c-doc", "t", ""), doc("hn:2", "c-doc", "t2", "단어 " * 200)]
    ck = chunker(40)
    chunks = ck.chunk_all(docs)
    st = chunking.chunk_stats(docs, chunks, FakeCounter.names, 40)["c-doc"]
    assert st["docs"] == 2 and st["empty_docs"] == 1 and st["chunks"] == len(chunks)
    assert st["cut_chunks"] == len(chunks) - 1 and st["cut_docs"] == 1 and st["over_cap_chunks"] == 0
    assert st["tokens"]["tok-a"]["max"] <= 40 and st["tokens"]["tok-a"]["p50"] <= st["tokens"]["tok-a"]["p90"]
    bad = [dict(c, tokens={"tok-a": 999, "tok-b": 1}) for c in chunks]          # 일부러 틀린 입력
    assert chunking.chunk_stats(docs, bad, FakeCounter.names, 40)["c-doc"]["over_cap_chunks"] == len(chunks)
    assert chunking.format_stats({"c-doc": st}, FakeCounter.names)


@case("chunk: 카운터 토크나이저가 정책과 다르면 거부")
def _():
    p = policy(40)
    p["tokenizers"] = ["x", "y"]
    assert raises(ValueError, chunking.Chunker, p, FakeCounter())


# --- 하네스 -----------------------------------------------------------------------------------------------------

@case("rollup: 문서마다 최고 순위 청크 하나만 남기고 문서 순위로 올린다")
def _():
    c2d = {"a#0": "A", "a#1": "A", "b#0": "B", "c#0": "C"}
    hits = [("a#1", 3.0), ("b#0", 2.5), ("a#0", 2.0), ("c#0", 1.0)]
    assert harness.rollup(hits, c2d) == [("A", 3.0), ("B", 2.5), ("C", 1.0)]
    assert harness.rollup([("a#0", 1.0), ("a#1", 5.0)], c2d) == [("A", 5.0)]       # 입력 순서가 아니라 점수 순
    assert raises(KeyError, harness.rollup, [("zzz#0", 1.0)], c2d)


@case("qrels·질의 로더: 등급 0/1/2만, 비어 있거나 짝이 안 맞으면 실패")
def _():
    assert harness.validate_qrels({"q1": {"d1": 2, "d2": 0}})
    assert raises(ValueError, harness.validate_qrels, {"q1": {"d1": 3}})
    assert raises(ValueError, harness.validate_qrels, {"q1": {"d1": True}})
    assert raises(ValueError, harness.validate_qrels, {"q1": {}})
    assert raises(ValueError, harness.validate_qrels, {})
    qs = [{"qid": "q1", "text": "t", "type": "A"}]
    harness.check_gold(qs, {"q1": {"d1": 1}}, ["d1"])
    assert raises(ValueError, harness.check_gold, qs, {"q2": {"d1": 1}}, ["d1"])
    assert raises(ValueError, harness.check_gold, qs, {"q1": {"오타": 1}}, ["d1"])
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "q.jsonl"
        write_jsonl(p, qs)
        assert harness.load_queries(p) == qs
        write_jsonl(p, [{"qid": "q1", "text": "t"}])
        assert raises(ValueError, harness.load_queries, p)
        write_jsonl(p, qs + qs)
        assert raises(ValueError, harness.load_queries, p)


@case("지표: 손으로 계산한 값과 같다 (Recall@k·MRR은 등급 2만 · nDCG@10은 등급 1도, 유형별 · 빈 결과 질의는 0)")
def _():
    queries = [{"qid": "q1", "text": "", "type": "A"}, {"qid": "q2", "text": "", "type": "B"},
               {"qid": "q3", "text": "", "type": "B"}]
    qrels = {"q1": {"d1": 2, "d5": 1}, "q2": {"d2": 2}, "q3": {"d3": 2}}
    run = {"q1": {"d5": 4.0, "d9": 3.0, "d1": 2.0},  # 1위는 등급 1 → MRR·Recall(-l2)은 세지 않는다: 등급 2가 3위 → MRR 1/3
           "q2": {"d2": 1.0},                     # 1위 → 모두 1
           "q3": {}}                              # 결과 없음 → 모두 0
    st = {d: "repo" for d in ("d1", "d2", "d3", "d5", "d9")}
    st["d9"] = "paper"
    m = harness.compute_metrics(run, qrels, queries, st)
    o = m["overall"]
    assert abs(o["mrr-l2"] - (1 / 3 + 1 + 0) / 3) < 1e-6 and abs(o["recall@5-l2"] - 2 / 3) < 1e-6, o
    # nDCG는 등급 1도 센다: q1 DCG = 1/log2(2) + 2/log2(4) = 2 · IDCG = 2 + 1/log2(3) → 0.7602
    assert abs(o["ndcg@10"] - (2 / (2 + 0.6309297536) + 1 + 0) / 3) < 1e-5, o
    assert m["by_type"]["A"]["n_queries"] == 1 and abs(m["by_type"]["A"]["mrr-l2"] - 1 / 3) < 1e-6
    assert m["by_type"]["B"]["n_queries"] == 2 and abs(m["by_type"]["B"]["mrr-l2"] - 0.5) < 1e-6
    d5 = m["source_type_at_k"]["@5"]["overall"]            # q1: repo 2/3 paper 1/3 · q2: repo 1 · q3는 결과 없어 빠진다
    assert d5 == {"paper": round(1 / 6, 4), "repo": round(5 / 6, 4)}, d5


@case("지표 검사가 잡는다: 정답을 놓친 run은 0점, 정답 문서를 1등에 둔 run은 1점 (채점기가 무력하지 않다)")
def _():
    queries = [{"qid": "q1", "text": "", "type": "A"}]
    qrels = {"q1": {"d1": 2}}
    st = {"d1": "repo", "d2": "repo"}
    miss = harness.compute_metrics({"q1": {"d2": 1.0}}, qrels, queries, st)["overall"]
    hit = harness.compute_metrics({"q1": {"d1": 1.0}}, qrels, queries, st)["overall"]
    assert all(v == 0 for v in miss.values()) and all(abs(v - 1) < 1e-9 for v in hit.values())


@case("결과 저장: 키 형식 · 4개 파일 · 같은 키 폴더는 덮어쓰지 않고 실패")
def _():
    key = harness.result_key("S2", "v1", "c-all@v1", "toy@v0", "T0", 0)
    assert key == "S2.v1__c-all@v1__toy@v0__T0__seed0"
    assert raises(ValueError, harness.result_key, "S2", "v1", "c/all", "g", "T0", 0)
    assert raises(ValueError, harness.result_key, "S2", "v1", "c__all", "g", "T0", 0)
    with tempfile.TemporaryDirectory() as tmp:
        d = harness.save_results(tmp, key, {"q1": {"d1": 1.0}}, {"m": 1}, {"s": 1}, {"e": 1})
        assert sorted(p.name for p in d.iterdir()) == ["env.json", "metrics.json", "run.json", "system.json"]
        assert json.loads((d / "run.json").read_text(encoding="utf-8")) == {"q1": {"d1": 1.0}}
        (d / "run.json").write_text("{}", encoding="utf-8")
        assert raises(FileExistsError, harness.save_results, tmp, key, {"q1": {"d1": 9.0}}, {}, {}, {})
        assert (d / "run.json").read_text(encoding="utf-8") == "{}"            # 옛 결과가 그대로다


@case("env.json 재료: 기기·OS·파이썬·라이브러리·git 커밋")
def _():
    e = harness.env_info({"corpus_version": "c-all@v1"})
    assert e["python"] and e["os"] and e["host"] and e["libraries"]["ranx"] and e["corpus_version"] == "c-all@v1"
    assert e["git"]["commit"] is None or len(e["git"]["commit"]) == 40


@case("run_queries: 질의마다 청크 순위가 문서 순위로 올라간다 · 지연 요약")
def _():
    class Fake(harness.Strategy):
        def search(self, query, k):
            return [("a#1", 2.0), ("b#0", 1.5), ("a#0", 1.0)] if query == "x" else []
    run, lat = harness.run_queries(Fake(), [{"qid": "q1", "text": "x"}, {"qid": "q2", "text": "y"}],
                                   {"a#0": "A", "a#1": "A", "b#0": "B"}, k_docs=20)
    assert run == {"q1": {"A": 2.0, "B": 1.5}, "q2": {}} and len(lat) == 2
    s = harness.latency_summary([1, 2, 3, 4, 100])
    assert s["n"] == 5 and s["p50"] == 3 and s["p95"] == 100


# --- S2 BM25 (ES 없이 확인되는 부분) ------------------------------------------------------------------------------

@case("S2: 인덱스 이름에 코퍼스 버전 · 설정에 k1=1.2 b=0.75 명시 · replicas 0 · 두 서브필드")
def _():
    assert s2_bm25.index_name_for("c-all@v1") == "arena-c-all-v1-s2"
    s = s2_bm25.S2BM25("c-all@v1")
    assert s.settings()["similarity"] == {"type": "BM25", "k1": 1.2, "b": 0.75}
    body = s._body()
    assert body["settings"]["index"]["number_of_replicas"] == 0
    sim = body["settings"]["index"]["similarity"]["bm25_s2"]
    assert sim == {"type": "BM25", "k1": 1.2, "b": 0.75}
    f = body["mappings"]["properties"]["text"]
    assert f["analyzer"] == "ko_nori" and f["fields"]["en"]["analyzer"] == "english"
    assert f["similarity"] == "bm25_s2" and f["fields"]["en"]["similarity"] == "bm25_s2"
    assert body["settings"]["analysis"]["analyzer"]["ko_nori"]["type"] == "nori"


@case("ES bulk: 응답의 errors를 보고 실패한다 (조용한 빈 색인 방지) — 가짜 응답으로 확인")
def _():
    real = es.call
    sent = []
    try:
        es.call = lambda base, method, path, body=None, ndjson=False, timeout=120: (
            sent.append(body) or (200, {"errors": True, "items": [{"index": {"_id": "a#0", "error": {"type": "x"}}}]}))
        assert raises(es.ESError, es.bulk_index, "http://x", "idx", [("a#0", {"text": "t"})])
        es.call = lambda base, method, path, body=None, ndjson=False, timeout=120: (
            sent.append(body) or (200, {"errors": False, "items": []}))
        es.bulk_index("http://x", "idx", [(f"a#{i}", {"text": "t"}) for i in range(450)], batch=200)
        assert len(sent) == 1 + 3 and sent[-1].count('"index"') == 50               # 450건 → 200+200+50
        es.call = lambda base, method, path, body=None, ndjson=False, timeout=120: (403, {"error": {"type": "e", "reason": "r"}})
        assert raises(es.ESError, es.bulk_index, "http://x", "idx", [("a#0", {"text": "t"})])
    finally:
        es.call = real


@case("S2: 색인 건수가 청크 수와 다르면 실패 · 검색은 (chunk_id, score)를 돌려준다 — 가짜 ES")
def _():
    real = es.call
    state = {"count": 2}

    def fake(base, method, path, body=None, ndjson=False, timeout=120):
        if path.endswith("/_count"):
            return 200, {"count": state["count"]}
        if path.endswith("/_search"):
            return 200, {"took": 3, "hits": {"hits": [{"_id": "a#0", "_score": 1.5}, {"_id": "b#0", "_score": 0.5}]}}
        if path == "/_bulk":
            return 200, {"errors": False, "items": []}
        return 200, {}
    try:
        es.call = fake
        s = s2_bm25.S2BM25("c-all@v1")
        chunks = [{"chunk_id": f"{c}#0", "doc_id": c, "source_type": "repo", "text": "t"} for c in "ab"]
        s.index(chunks)
        assert s.search("q", 5) == [("a#0", 1.5), ("b#0", 0.5)] and s.took_ms == [3]
        state["count"] = 1
        assert raises(es.ESError, s.index, chunks)
    finally:
        es.call = real


# --- HYO-35: 임베딩 · S4 · S3b · S5a (모델 다운로드 없음 — 가짜 인코더) ---------------------------------------------------

class FakeBGEModel:
    """BGEM3FlagModel 흉내: tokenizer(공백 단어 + 특수 토큰 2개) · encode(texts, **kw). 받은 인자를 기록한다."""

    class _Tok:
        def __call__(self, texts, add_special_tokens=True, truncation=False):
            return {"input_ids": [list(range(len(t.split()) + (2 if add_special_tokens else 0))) for t in texts]}

    def __init__(self):
        self.tokenizer = self._Tok()
        self.calls = []

    def encode(self, texts, **kw):
        import numpy as np
        self.calls.append(dict(kw))
        dense = np.zeros((len(texts), 1024), dtype=np.float32)
        lex = []
        for i, t in enumerate(texts):
            dense[i, len(t) % 1024] = 1.0
            lex.append({str(100 + j): 0.5 for j, _ in enumerate(t.split())})
        return {"dense_vecs": dense, "lexical_weights": lex}


class FakeEncoder:
    """Embedder 테스트용 인코더 — 인코딩한 텍스트를 센다. dense는 단위 벡터(텍스트 길이 자리), sparse는 단어 → 가중치."""
    model_id, revision, max_length, batch_size, device = "fake/model", "rev0123456789abcdef", 512, 16, "cpu"

    def __init__(self):
        self.encoded = []

    def _enc(self, texts):
        import numpy as np
        dense = np.zeros((len(texts), 1024), dtype=np.float32)
        sparse = []
        for i, t in enumerate(texts):
            dense[i, len(t) % 1024] = 1.0
            sparse.append({w: 1.0 for w in t.split()})
        return dense, sparse

    def encode_docs(self, texts):
        self.encoded += list(texts)
        return self._enc(texts)

    def encode_queries(self, texts):
        return self._enc(texts)


def chunks_of(*texts):
    return [{"chunk_id": f"d{i}#0", "doc_id": f"d{i}", "source_type": "repo", "text": t} for i, t in enumerate(texts)]


@case("BGEM3Encoder: 질의 · 문서가 같은 인자(fp32 설정 · max_length 512 · 지시문 없음)로 인코딩된다")
def _():
    m = FakeBGEModel()
    enc = embed.BGEM3Encoder(model=m, revision="r", device="cpu")
    enc.encode_docs(["alpha beta", "gamma"])
    enc.encode_queries(["alpha"])
    assert len(m.calls) == 2 and m.calls[0] == m.calls[1], m.calls
    assert m.calls[0]["max_length"] == 512 and m.calls[0]["return_dense"] and m.calls[0]["return_sparse"]
    assert not m.calls[0]["return_colbert_vecs"]
    assert not any("instruction" in k or "prefix" in k for k in m.calls[0])


@case("BGEM3Encoder: 512토큰(특수 토큰 포함)을 넘는 입력은 조용히 자르지 않고 실패한다 — 일부러 513 토큰을 넣는다")
def _():
    m = FakeBGEModel()
    enc = embed.BGEM3Encoder(model=m, revision="r", device="cpu")
    enc.encode_docs([" ".join(["w"] * 510)])                                            # 510 + 2 = 512 → 통과
    assert raises(embed.TruncationError, enc.encode_docs, [" ".join(["w"] * 511)])      # 513 → 실패
    assert len(m.calls) == 1                                                            # 실패한 입력은 모델에 가지도 않았다


@case("BGEM3Encoder: 모델이 엉뚱한 모양을 돌려주면 실패한다 (dense 차원 · sparse 건수)")
def _():
    class BadDense(FakeBGEModel):
        def encode(self, texts, **kw):
            out = super().encode(texts, **kw)
            out["dense_vecs"] = out["dense_vecs"][:, :512]
            return out

    class BadSparse(FakeBGEModel):
        def encode(self, texts, **kw):
            out = super().encode(texts, **kw)
            out["lexical_weights"] = out["lexical_weights"][:-1]
            return out
    assert raises(ValueError, embed.BGEM3Encoder(model=BadDense(), revision="r", device="cpu").encode_docs, ["a b"])
    assert raises(ValueError, embed.BGEM3Encoder(model=BadSparse(), revision="r", device="cpu").encode_docs, ["a b", "c d"])


@case("임베딩 캐시: 같은 텍스트는 적중 · 텍스트가 바뀌면 다시 인코딩 · 디스크에서 다시 읽어도 적중 · 버전이 다르면 적중 없음")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        enc = FakeEncoder()
        e1 = embed.Embedder(enc, "c@v1", "p@v1", cache_root=tmp)
        d1, s1 = e1.encode_chunks(chunks_of("alpha beta", "gamma delta epsilon"))
        assert len(enc.encoded) == 2 and e1.stats["cache_hits"] == 0
        # 새 Embedder(다른 프로세스 흉내) — 같은 텍스트면 인코딩 0회
        enc2 = FakeEncoder()
        e2 = embed.Embedder(enc2, "c@v1", "p@v1", cache_root=tmp)
        d2, s2 = e2.encode_chunks(chunks_of("alpha beta", "gamma delta epsilon"))
        assert enc2.encoded == [] and e2.stats["cache_hits"] == 2 and (d1 == d2).all() and s1 == s2
        # 하나만 바뀌면 그것만 다시
        enc3 = FakeEncoder()
        e3 = embed.Embedder(enc3, "c@v1", "p@v1", cache_root=tmp)
        d3, s3 = e3.encode_chunks(chunks_of("alpha beta", "gamma delta CHANGED"))
        assert enc3.encoded == ["gamma delta CHANGED"] and e3.stats["cache_hits"] == 1
        assert s3[1] == {"gamma": 1.0, "delta": 1.0, "CHANGED": 1.0}                    # 옛 값이 아니라 새 값
        # 코퍼스 버전 · 정책 버전 · 모델 revision이 다르면 이름공간이 다르다 → 적중 없음
        for corpus_v, pol_v, rev in (("c@v2", "p@v1", "rev0123456789abcdef"), ("c@v1", "p@v2", "rev0123456789abcdef"),
                                     ("c@v1", "p@v1", "otherrevision0000")):
            en = FakeEncoder()
            en.revision = rev
            embed.Embedder(en, corpus_v, pol_v, cache_root=tmp).encode_chunks(chunks_of("alpha beta"))
            assert en.encoded == ["alpha beta"], (corpus_v, pol_v, rev)
        # 캐시를 끄면 항상 인코딩
        enc4 = FakeEncoder()
        embed.Embedder(enc4, "c@v1", "p@v1", cache_root=tmp, use_cache=False).encode_chunks(chunks_of("alpha beta"))
        assert enc4.encoded == ["alpha beta"]


@case("임베딩 캐시: 항목 수가 어긋난(깨진) 캐시는 조용히 쓰지 않고 실패한다")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        e = embed.Embedder(FakeEncoder(), "c@v1", "p@v1", cache_root=tmp)
        e.encode_chunks(chunks_of("alpha beta", "gamma"))
        (Path(tmp) / e.namespace / "sparse.json").write_text(json.dumps([{"a": 1.0}]), encoding="utf-8")   # 2건이어야 하는데 1건
        assert raises(ValueError, embed.Embedder, FakeEncoder(), "c@v1", "p@v1", tmp)


@case("임베딩 info: 모델 id · revision · dtype fp32 · 기기 · 라이브러리 버전 · 인코딩 시간을 기록한다")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        e = embed.Embedder(FakeEncoder(), "c@v1", "p@v1", cache_root=tmp)
        e.encode_chunks(chunks_of("a b"))
        e.encode_query("a")
        i = e.info()
        assert i["model_id"] == "fake/model" and i["revision"] == "rev0123456789abcdef" and i["dtype"] == "float32"
        assert i["device"] == "cpu" and i["dense_dim"] == 1024 and i["max_length"] == 512 and i["instruction_prefix"] is None
        assert "torch" in i["libraries"] and "FlagEmbedding" in i["libraries"]
        assert i["encoded"] == 1 and i["chunk_encode_seconds"] >= 0 and i["queries_encoded"] == 1
        assert "c@v1" in i["cache_namespace"] and "p@v1" in i["cache_namespace"] and "rev012345678" in i["cache_namespace"]


def fake_es_for(state, hits):
    def fake(base, method, path, body=None, ndjson=False, timeout=120):
        state.setdefault("calls", []).append((method, path, body))
        if path.endswith("/_count"):
            return 200, {"count": state["count"]}
        if path.endswith("/_search"):
            return 200, {"took": 2, "hits": {"hits": [{"_id": c, "_score": s} for c, s in hits]}}
        if path == "/_bulk":
            return 200, {"errors": False, "items": []}
        return 200, {}
    return fake


@case("S4: 색인 본문 — dims 1024 · cosine · hnsw(m · ef_construction) · 양자화 없음 · replicas 0 · 인덱스 이름에 코퍼스 버전")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        emb = embed.Embedder(FakeEncoder(), "c-all@v1", "p@v1", cache_root=tmp)
        s = s4_dense.S4Dense("c-all@v1", emb, m=24, ef_construction=150, num_candidates=80)
        assert s.index_name == "arena-c-all-v1-s4"
        f = s._body()["mappings"]["properties"]["embedding"]
        assert f == {"type": "dense_vector", "dims": 1024, "index": True, "similarity": "cosine",
                     "index_options": {"type": "hnsw", "m": 24, "ef_construction": 150}}, f
        assert "int8" not in json.dumps(s._body()) and "bbq" not in json.dumps(s._body())
        assert s._body()["settings"]["index"]["number_of_replicas"] == 0
        st = s.settings()
        assert st["dense_vector"]["index_options"] == {"type": "hnsw", "m": 24, "ef_construction": 150}
        assert st["query"]["num_candidates"] == 80 and st["quantization"] is None
        assert st["embedding_model"] == "fake/model@rev0123456789abcdef"
        d = s4_dense.S4Dense("c-all@v1", emb)
        assert (d.m, d.ef_construction, d.num_candidates) == (16, 100, 100)             # 기본값
        for bad in ({"m": 1}, {"m": 16.0}, {"ef_construction": 0}, {"num_candidates": 20000}, {"m": True}):
            assert raises(ValueError, s4_dense.S4Dense, "c-all@v1", emb, **bad), bad


@case("S4: kNN 질의 본문(k · num_candidates · 1024차원 벡터) · k > num_candidates는 거부 · 건수 불일치는 실패 — 가짜 ES")
def _():
    real = es.call
    state = {"count": 2}
    try:
        es.call = fake_es_for(state, [("d0#0", 0.9), ("d1#0", 0.6)])
        with tempfile.TemporaryDirectory() as tmp:
            emb = embed.Embedder(FakeEncoder(), "c-all@v1", "p@v1", cache_root=tmp)
            s = s4_dense.S4Dense("c-all@v1", emb, num_candidates=50)
            chunks = chunks_of("alpha beta", "gamma")
            s.index(chunks)
            put = [c for c in state["calls"] if c[0] == "PUT"][0]
            assert put[2]["mappings"]["properties"]["embedding"]["dims"] == 1024
            assert s.search("alpha", 10) == [("d0#0", 0.9), ("d1#0", 0.6)] and s.took_ms == [2]
            knn = [c for c in state["calls"] if c[1].endswith("/_search")][0][2]["knn"]
            assert knn["k"] == 10 and knn["num_candidates"] == 50 and knn["field"] == "embedding" and len(knn["query_vector"]) == 1024
            assert raises(ValueError, s.search, "alpha", 51)
            state["count"] = 1
            assert raises(es.ESError, s.index, chunks)
    finally:
        es.call = real


@case("S3b: 점수 = lexical weights 내적 (손계산과 같다) · 겹침 없는 청크는 순위에 없다 · 동점은 색인 순서")
def _():
    # 손계산: q={a:.5, b:.25}  d0={a:.4, c:1} → .5*.4 = .2   d1={b:.8, a:.2} → .25*.8+.5*.2 = .3   d2={z:1} → 0   d3={a:.4} → .2
    assert abs(s3b_bgem3_sparse.dot({"a": .5, "b": .25}, {"a": .4, "c": 1.0}) - 0.2) < 1e-12
    assert abs(s3b_bgem3_sparse.dot({"b": .8, "a": .2}, {"a": .5, "b": .25}) - 0.3) < 1e-12

    class SparseEmb:
        encoder = FakeEncoder()

        def encode_chunks(self, chunks):
            return None, [{"a": .4, "c": 1.0}, {"b": .8, "a": .2}, {"z": 1.0}, {"a": .4}]

        def encode_query(self, text):
            return None, {"a": .5, "b": .25}
    s = s3b_bgem3_sparse.S3bSparse("c@v1", SparseEmb())
    s.index(chunks_of("x", "y", "z", "w"))
    got = s.search("q", 10)
    assert [c for c, _ in got] == ["d1#0", "d0#0", "d3#0"]                              # d3도 .2로 d0와 동점 → 색인 순서
    assert abs(got[0][1] - 0.3) < 1e-12 and abs(got[1][1] - 0.2) < 1e-12 and abs(got[2][1] - 0.2) < 1e-12
    assert [c for c, _ in s.search("q", 2)] == ["d1#0", "d0#0"]
    assert s.n_indexed == 4 and s.index_size_bytes() == 6 * 8 and s.server_version() is None


@case("RRF: ranx 결과가 손계산(1/(k+순위) 합)과 같다 · k 기록 · 빈 구성요소 · 잘못된 k는 거부")
def _():
    # k=60: A = 1/61 + 1/63 · B = 1/62 + 1/61 · C = 1/63 · D = 1/62
    got = dict(s5a_rrf.rrf_fuse([["A", "B", "C"], ["B", "D", "A"]], k=60))
    want = {"A": 1 / 61 + 1 / 63, "B": 1 / 62 + 1 / 61, "C": 1 / 63, "D": 1 / 62}
    assert set(got) == set(want) and all(abs(got[d] - want[d]) < 1e-9 for d in want), (got, want)
    assert [d for d, _ in s5a_rrf.rrf_fuse([["A", "B", "C"], ["B", "D", "A"]], k=60)] == ["B", "A", "D", "C"]
    # k를 바꾸면 점수가 바뀐다 (k=1: A = 1/2 + 1/4)
    got1 = dict(s5a_rrf.rrf_fuse([["A", "B", "C"], ["B", "D", "A"]], k=1))
    assert abs(got1["A"] - (1 / 2 + 1 / 4)) < 1e-9
    # 한쪽이 비어도 다른 쪽 순위가 그대로 나온다 (자리표시 문서는 결과에 없다)
    one = s5a_rrf.rrf_fuse([["A", "B"], []], k=60)
    assert [d for d, _ in one] == ["A", "B"] and abs(one[0][1] - 1 / 61) < 1e-9
    assert s5a_rrf.rrf_fuse([[], []], k=60) == []
    for bad in (0, -1, 60.0, True):
        assert raises(ValueError, s5a_rrf.rrf_fuse, [["A"]], bad), bad


class FakeComponent:
    def __init__(self, id_, hits):
        self.id, self.version, self._hits = id_, "v1", hits
        self.took_ms, self.n_indexed, self.asked = [], 0, []

    def settings(self):
        return {"marker": self.id}

    def index(self, chunks):
        self.n_indexed = len(chunks)

    def search(self, q, k):
        self.asked.append(k)
        return self._hits

    def index_size_bytes(self):
        return 10


@case("S5a: 문서 단위 융합 — 같은 문서의 두 청크는 한 번만 센다 · 손계산과 같다 · 대표 청크 · 설정 기록 · k 불일치는 거부")
def _():
    chunk_to_doc = {"A#0": "A", "A#1": "A", "B#0": "B", "C#0": "C"}
    chunks = [{"chunk_id": c, "doc_id": d, "source_type": "repo", "text": "t"} for c, d in chunk_to_doc.items()]
    # S2: A#1 (A 1위), B#0 (B 2위), A#0 (같은 문서 A — 버린다)      S4: C#0 (C 1위), B#0 (B 2위), A#0 (A 3위)
    s2 = FakeComponent("S2", [("A#1", 9.0), ("B#0", 8.0), ("A#0", 7.0)])
    s4 = FakeComponent("S4", [("C#0", 0.9), ("B#0", 0.8), ("A#0", 0.7)])
    s = s5a_rrf.S5aRRF(s2, s4, rrf_k=60, chunk_k=100)
    s.index(chunks)
    got = s.search("q", 100)
    # A = 1/61 + 1/63 · B = 1/62 + 1/62 · C = 1/61.  (A#0을 문서 순위에 두 번 세면 A가 더 커졌을 것이다)
    want = {"A": 1 / 61 + 1 / 63, "B": 2 / 62, "C": 1 / 61}
    by_doc = {chunk_to_doc[c]: sc for c, sc in got}
    assert len(got) == 3 and all(abs(by_doc[d] - want[d]) < 1e-9 for d in want), (by_doc, want)
    assert [chunk_to_doc[c] for c, _ in got] == sorted(want, key=lambda d: -want[d])
    rep = {chunk_to_doc[c]: c for c, _ in got}
    assert rep["A"] == "A#1" and rep["C"] == "C#0"                                      # 더 높은 순위로 올라온 청크가 대표
    assert s2.asked == [100] and s4.asked == [100]                                      # 두 구성요소가 같은 N
    # 하네스 rollup을 거쳐도 문서당 하나이고 순서가 같다
    assert [d for d, _ in harness.rollup(got, chunk_to_doc)] == [chunk_to_doc[c] for c, _ in got]
    st = s.settings()
    assert st["fusion"]["method"] == "rrf" and st["fusion"]["params"] == {"k": 60} and st["component_chunk_k"] == 100
    assert st["components"]["s2"]["marker"] == "S2" and st["components"]["s4"]["marker"] == "S4"
    assert st["reuse_of_saved_results"] is False
    assert raises(ValueError, s.search, "q", 50)                                        # chunk_k와 다른 k
    assert s.index_size_bytes() == 20 and s.size_breakdown() == {"s2": 10, "s4": 10}
    for bad in ({"rrf_k": 0}, {"chunk_k": 0}, {"rrf_k": 1.5}):
        assert raises(ValueError, s5a_rrf.S5aRRF, s2, s4, **bad), bad


@case("S5a: 구성요소를 둘 다 새로 색인한다 (저장된 결과 재사용 없음) · 모르는 청크 id는 실패")
def _():
    s2, s4 = FakeComponent("S2", []), FakeComponent("S4", [("ghost#0", 1.0)])
    s = s5a_rrf.S5aRRF(s2, s4)
    s.index(chunks_of("x", "y"))
    assert s2.n_indexed == 2 and s4.n_indexed == 2 and s.n_indexed == 2
    assert raises(KeyError, s.search, "q", 100)


# --- HYO-35: S6 Reranker (가짜 cross-encoder — 모델 다운로드 없음) ---------------------------------------------------------

class FakeRerankModel:
    """FlagReranker 흉내: tokenizer(질의들, 청크들) = 공백 단어 수 + 특수 토큰 4개 · compute_score(pairs, **kw) = 청크 텍스트 → 점수 표."""

    class _Tok:
        def __call__(self, qs, ps, add_special_tokens=True, truncation=False):
            return {"input_ids": [list(range(len(q.split()) + len(p.split()) + (4 if add_special_tokens else 0)))
                                  for q, p in zip(qs, ps)]}

    def __init__(self, table):
        self.tokenizer, self.table, self.calls, self.pairs_seen = self._Tok(), table, [], []

    def compute_score(self, pairs, **kw):
        self.calls.append(dict(kw))
        self.pairs_seen += [tuple(p) for p in pairs]
        return [self.table[p[1]] for p in pairs]


def s6_setup(table, depth=50, max_length=1024):
    """문서 A(청크 2개) B C D. S2 상위 A#0 B#0 C#0 · S4 상위 B#0 A#1 D#0 → 융합 순서 A B C D (A·B 동점은 먼저 등장한 A)"""
    chunks = [{"chunk_id": cid, "doc_id": cid[0], "source_type": "repo", "text": f"text-{cid}"}
              for cid in ("A#0", "A#1", "B#0", "C#0", "D#0", "D#1")]       # D#1은 두 목록 어디에도 안 나온다 — 채점 대상이 아니다
    s2 = FakeComponent("S2", [("A#0", 3.0), ("B#0", 2.0), ("C#0", 1.0)])
    s4 = FakeComponent("S4", [("B#0", 3.0), ("A#1", 2.0), ("D#0", 1.0)])
    first = s5a_rrf.S5aRRF(s2, s4, chunk_k=100)
    model = FakeRerankModel({f"text-{cid}": v for cid, v in table.items()})
    enc = s6_rerank.CrossEncoder(model=model, device="cpu", revision="revfake0123456789", max_length=max_length, batch_size=8)
    s = s6_rerank.S6Rerank(first, enc, depth=depth)
    s.index(chunks)
    return s, model, {c["chunk_id"]: c["doc_id"] for c in chunks}


S6_TABLE = {"A#0": 0.1, "A#1": 0.9, "B#0": 0.5, "C#0": 0.5, "D#0": 0.7, "D#1": 99.0}


@case("S6: 문서 점수 = 청크 점수의 최댓값 (2번째 청크가 이긴다) · 대표 청크 = 최고 점수 청크 · 동점은 1단계 순서")
def _():
    s, model, c2d = s6_setup(S6_TABLE)
    assert [d for d, _, _ in harness.rollup_chunks([(c, 0.0) for c in ["A#0"]], c2d)] == ["A"]      # 도우미 확인
    first_order = [d for d, _ in s.first._first_stage("q")[0]]
    assert first_order == ["A", "B", "C", "D"], first_order                                        # 1단계: 재정렬 전 A가 1위
    got = s.search("q", 100)
    docs = [c2d[c] for c, _ in got]
    assert docs == ["A", "D", "B", "C"], docs                           # A: max(.1,.9)=.9 · D .7 · B·C 동점 .5 → 1단계 순서(B 먼저)
    assert dict(got)["A#1"] == 0.9 and "A#0" not in dict(got)           # 대표 청크는 2번째 청크 A#1
    assert [round(sc, 3) for _, sc in got] == [0.9, 0.7, 0.5, 0.5]
    assert s.first.s2.asked == [100, 100] and s.first.s4.asked == [100, 100]       # 1단계(직접 호출 1 + search 1) 모두 chunk_k=100
    # 하네스 rollup을 거쳐도 순서·대표가 그대로다
    assert [(d, c) for d, c, _ in harness.rollup_chunks(got, c2d)] == [(c2d[c], c) for c, _ in got]
    # 후보 = 두 목록에 나온 청크만 (D#1은 채점하지 않았다)
    assert sorted(p[1] for p in model.pairs_seen) == sorted(f"text-{c}" for c in ("A#0", "A#1", "B#0", "C#0", "D#0"))
    assert all(p[0] == "q" for p in model.pairs_seen)


@case("S6: 깊이 컷 — 1단계 상위 depth 문서만 채점·반환한다")
def _():
    s, model, c2d = s6_setup(S6_TABLE, depth=2)
    got = s.search("q", 100)
    assert [c2d[c] for c, _ in got] == ["A", "B"], got                  # C·D는 점수가 높아도 후보가 아니다
    assert sorted(p[1] for p in model.pairs_seen) == ["text-A#0", "text-A#1", "text-B#0"]
    assert s.pairs_per_query == [3] and len(s.rerank_ms) == 1
    for bad in (0, -1, 1.5, True):
        assert raises(ValueError, s6_rerank.S6Rerank, s.first, s.reranker, depth=bad), bad


@case("S6: 쌍이 max_length를 넘으면 채점 전에 실패한다 (조용한 잘림 없음) · 안 넘으면 통과")
def _():
    s, model, _ = s6_setup(S6_TABLE, max_length=7)                      # 질의 1 + 청크 1 + 특수 4 = 6 ≤ 7
    assert len(s.search("q", 100)) == 4
    n_calls = len(model.calls)
    assert raises(s6_rerank.TruncationError, s.search, "긴 질의 하나 둘", 100)    # 4 + 1 + 4 = 9 > 7
    assert len(model.calls) == n_calls                                  # 모델은 부르지 않았다
    assert s.reranker.max_pair_tokens == 9
    assert raises(ValueError, s.search, "q", 50)                        # chunk_k와 다른 k


@case("S6: 설정·모델·revision·기기·dtype·인자 기록 (system.json 재료)")
def _():
    s, model, _ = s6_setup(S6_TABLE, depth=3)
    s.search("q", 100)
    st = s.settings()
    assert st["model"] == s6_rerank.MODEL_ID
    assert st["revision"] == "revfake0123456789" and st["dtype"] == "float32" and st["max_length"] == 1024
    assert st["rerank_depth"] == 3 and st["batch_size"] == 8 and "logit" in st["score"]
    assert st["first_stage"]["id"] == "S5a" and st["first_stage"]["component_chunk_k"] == 100
    assert st["first_stage"]["fusion"]["params"] == {"k": 60} and "s2" in st["first_stage"]["components"]
    assert model.calls == [{"batch_size": 8, "max_length": 1024, "normalize": False}]       # 정규화 끔 · 배치 · 길이가 모델까지 간다
    info = s.reranker.info()
    assert info["model_id"] == s6_rerank.MODEL_ID and info["dtype"] == "float32" and info["device"] == "cpu"
    assert info["pairs_scored"] == 4 and "FlagEmbedding" in info["libraries"]
    assert s.index_size_bytes() == 20 and s.n_indexed == 6


@case("CLI: --strategy에 s2 · s3b · s4 · s5a · s6이 있다 · 결과 키에 전략 id/버전이 들어간다")
def _():
    import importlib
    cli = importlib.import_module("arena.cli")
    assert cli.STRATEGY_CHOICES == ["s2", "s3b", "s4", "s5a", "s6"] and "s6" in cli.EMBEDDING_STRATEGIES
    assert harness.result_key("S5a", "v1", "c-all@v1", "toy@v0").startswith("S5a.v1__")
    assert harness.result_key("S6", "v1", "c-all@v1", "toy@v0").startswith("S6.v1__")
    assert harness.result_key("S3b", "v1", "c-all@v1", "toy@v0").startswith("S3b.v1__")
    assert harness.result_key("S4", "v1", "c-all@v1", "toy@v0").startswith("S4.v1__")


if FAILS:
    print(f"\nFAILED {len(FAILS)} — {FAILS}")
    sys.exit(1)
print("\nOK — 실패 0")
