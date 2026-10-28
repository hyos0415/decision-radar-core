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
from arena.strategies import (s1_match, s2_bm25, s3a_splade, s3b_bgem3_sparse, s4_dense, s5a_rrf,  # noqa: E402
                              s5b_rrf, s6_rerank)

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
        assert (d.m, d.ef_construction, d.num_candidates) == (16, 100, 200)             # 기본값 (num_candidates는 dev 2026-10-13)
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


@case("S5a 고정(pin) — 리팩터링 전후 출력이 바이트까지 같다: search · 후보 · settings sha256 · took · 크기 · 서버 버전")
def _():
    class FC:
        def __init__(self, i, h):
            self.id, self.version, self._h = i, "v1", h
            self.took_ms, self.n_indexed = ([1] if i == "S2" else [2, 3]), 0

        def settings(self):
            return {"marker": self.id}

        def index(self, c):
            self.n_indexed = len(c)

        def search(self, q, k):
            return self._h

        def index_size_bytes(self):
            return 10 if self.id == "S2" else 7

        def server_version(self):
            return "9.5.4" if self.id == "S2" else None
    c2d = {"A#0": "A", "A#1": "A", "B#0": "B", "C#0": "C", "D#0": "D", "E#0": "E"}
    chunks = [{"chunk_id": c, "doc_id": d} for c, d in c2d.items()]
    s2 = FC("S2", [("A#1", 9.0), ("B#0", 8.0), ("A#0", 7.0), ("D#0", 6.0)])
    s4 = FC("S4", [("C#0", .9), ("B#0", .8), ("A#0", .7), ("E#0", .6), ("D#0", .5)])
    s = s5a_rrf.S5aRRF(s2, s4, rrf_k=30, chunk_k=100)
    s.index(chunks)
    s.warmup_seconds = 0.5
    assert json.dumps(s.search("q", 100)) == ('[["A#1", 0.062561094819], ["B#0", 0.0625], ["D#0", 0.058874458874], '
                                              '["C#0", 0.032258064516], ["E#0", 0.029411764706]]')
    assert json.dumps(s.first_stage_candidates("q")) == ('[["A", ["A#1", "A#0"]], ["B", ["B#0"]], ["D", ["D#0"]], '
                                                         '["C", ["C#0"]], ["E", ["E#0"]]]')
    st = s.settings()
    st["fusion"]["ranx_version"] = "X"          # 설치된 버전에 따라 달라지는 칸만 고정
    import hashlib
    assert hashlib.sha256(json.dumps(st, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest() == \
        "582e9a4aa578393cc64353bb186498542b9e8667e28cac53d99c5dd06d2042ed"   # #75: fusion.status 문구만 바뀜 (옛 문구로 되돌리면 옛 해시 057dabb8…)
    assert (s.took_ms, s.index_size_bytes(), s.size_breakdown(), s.server_version(), s.n_indexed) == \
        ([1, 2, 3], 17, {"s2": 10, "s4": 7}, "9.5.4", 6)
    assert s.s2 is s2 and s.s4 is s4


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


@case("S6.dense (이슈 #75): 1단계 = S4 하나 — 문서 순서는 최고 순위 청크 · 후보는 S4 목록에 나온 청크만 · 결과 id S6.dense · 설정 기록")
def _():
    chunks = [{"chunk_id": cid, "doc_id": cid[0], "source_type": "repo", "text": f"text-{cid}"}
              for cid in ("A#0", "A#1", "B#0", "C#0", "D#0", "D#1")]
    s4 = FakeComponent("S4", [("B#0", 3.0), ("A#1", 2.0), ("D#0", 1.5), ("A#0", 1.0)])      # C는 S4 목록에 없다
    first = s6_rerank.DenseFirstStage(s4, chunk_k=100)
    model = FakeRerankModel({f"text-{cid}": v for cid, v in S6_TABLE.items()})
    enc = s6_rerank.CrossEncoder(model=model, device="cpu", revision="revfake0123456789", batch_size=8)
    s = s6_rerank.S6Rerank(first, enc, depth=50)
    s.index(chunks)
    assert first.first_stage_candidates("q") == [("B", ["B#0"]), ("A", ["A#1", "A#0"]), ("D", ["D#0"])]
    c2d = {c["chunk_id"]: c["doc_id"] for c in chunks}
    got = s.search("q", 100)
    assert [c2d[c] for c, _ in got] == ["A", "D", "B"], got               # A .9 · D .7 · B .5 — C는 후보가 아니다 (S5a 1단계와 다른 점)
    assert sorted(p[1] for p in model.pairs_seen) == sorted(f"text-{c}" for c in ("A#0", "A#1", "B#0", "D#0"))
    assert s4.asked == [100, 100] and s.chunk_k == 100
    assert s.id == "S6.dense" and harness.result_key(s.id, s.version, "c-all@v1", "toy@v0") == "S6.dense.v1__c-all@v1__toy@v0__T0__seed0"
    st = s.settings()
    assert st["first_stage"]["id"] == "S4" and "S4 상위 chunk_k" in st["candidates"] and st["first_stage"]["component_chunk_k"] == 100
    assert st["first_stage"]["components"]["s4"]["marker"] == "S4" and s.size_breakdown() == {"s4": 10}
    assert s6_setup(S6_TABLE)[0].id == "S6"                               # S5a 1단계는 그대로 S6
    assert raises(ValueError, s6_rerank.S6Rerank, FakeComponent("S2", []), enc)          # 모르는 1단계는 거부
    assert raises(ValueError, s6_rerank.DenseFirstStage, s4, chunk_k=0)
    bad = s6_rerank.DenseFirstStage(FakeComponent("S4", [("Z#9", 1.0)]), chunk_k=100)
    bad.index(chunks)
    assert raises(KeyError, bad.first_stage_candidates, "q")              # 모르는 청크 id는 실패
    import importlib
    cli = importlib.import_module("arena.cli")
    try:                                                                  # 다른 전략에 --s6-first를 쓰면 코퍼스를 읽기 전에 거부
        cli.main(["run", "--strategy", "s5a", "--s6-first", "s4", "--queries", "q", "--qrels", "r", "--gold-version", "toy@v0",
                  "--snapshots", "s", "--adr", "a"])
        raise AssertionError("거부되지 않았다")
    except SystemExit as e:
        assert e.code == 2


# --- HYO-35 (이슈 #63): S1 · S4.int8 · S4.bbq · S3a SPLADE-v3 · S5b ----------------------------------------------------------

def adr_doc_of(n, technology, affected, use="u"):
    fm = ["status: adopted", f"technology: {technology}", f"use: {use}", "affected:"]
    fm += [f"- repo: {r}\n  component: {c}" for r, c in affected]
    text = "---\n" + "\n".join(fm) + "\n---\n# 제목\n본문"
    return doc(f"adr:{n:04d}", "c-adr", f"ADR {n}", text)


S1_DOCS = [
    adr_doc_of(1, "Foo DB", [("a", "x")]),
    adr_doc_of(2, "foo db", [("b", "y")]),
    adr_doc_of(3, "Bar", [("a", "x"), ("a", "z")]),
    adr_doc_of(4, "Baz", [("c", "z")]),
    adr_doc_of(5, "  Bar   Lib ", [("A", " X ")]),
    doc("github:o/r", "c-doc", "릴리스", "technology: Foo DB"),                 # C-ADR이 아니면 쓰지 않는다
]


def s1_chunks():
    return [{"chunk_id": f"{d['doc_id']}#{s}", "doc_id": d["doc_id"], "source_type": "adr", "text": "t"}
            for d in S1_DOCS for s in ("a", "b")]


def s1_ready():
    s = s1_match.S1Match(S1_DOCS)
    s.index(s1_chunks())
    return s


@case("S1: norm이 radar.py의 norm과 모든 입력에서 같다 (두 구현이 어긋나지 않게)")
def _():
    radar_norm = s1_match.load_radar_norm()
    for x in ("", None, "  A  B ", "Foo\tBar\n baz", "ÄB  ß", "한글  테스트 ", "Amazon Bedrock Guardrails", "x"):
        assert s1_match.norm(x) == radar_norm(x), repr(x)
    assert s1_match.norm("  Foo   DB ") == "foo db"


@case("S1: 등급 순위 — 1(technology+컴포넌트) > 2(technology만) > 3(컴포넌트만) · 같은 등급은 ADR id 순서 · 정규화 · 첫 청크")
def _():
    s = s1_ready()
    got = s.search("전혀 상관없는 질의 글", 100, nodes=[{"technology": "Foo DB", "repo": "a", "component": "x"}])
    # 0001 = 등급 1 · 0002 = foo db 대소문자 정규화로 등급 2 · 0003(a/x) · 0005(A/ X 공백·대소문자 정규화)는 등급 3 → id 순서
    assert got == [("adr:0001#a", 3.0), ("adr:0002#a", 2.0), ("adr:0003#a", 1.0), ("adr:0005#a", 1.0)], got
    assert [c for c, _ in s.search("q", 2, nodes=[{"technology": "Foo DB", "repo": "a", "component": "x"}])] == ["adr:0001#a", "adr:0002#a"]
    assert s.search("q", 5, nodes=[{"technology": "Nope", "repo": "q", "component": "q"}]) == []      # 안 맞으면 빈 결과
    # 질의 글은 결과에 영향이 없다
    n = [{"technology": "Baz"}]
    assert s.search("A", 5, nodes=n) == s.search("완전히 다른 글", 5, nodes=n) == [("adr:0004#a", 2.0)]
    # 빈 technology / 빈 repo·component는 일치로 세지 않는다
    assert s.search("q", 5, nodes=[{"technology": "", "repo": "a", "component": "x"}]) == \
        [("adr:0001#a", 1.0), ("adr:0003#a", 1.0), ("adr:0005#a", 1.0)]
    assert s.search("q", 5, nodes=[{"technology": "Foo DB", "repo": "a"}]) == [("adr:0001#a", 2.0), ("adr:0002#a", 2.0)]


@case("S1: 노드가 여럿이면 합집합 — ADR마다 가장 좋은 등급")
def _():
    s = s1_ready()
    got = s.search("q", 100, nodes=[{"technology": "Baz"}, {"technology": "Bar", "repo": "a", "component": "x"}])
    # 0003: 노드2 등급 1 · 0004: 노드1 등급 2 · 0001: 노드2의 a/x만 → 3 · 0005: "bar lib" ≠ "bar"지만 A/X 정규화로 3
    assert got == [("adr:0003#a", 3.0), ("adr:0004#a", 2.0), ("adr:0001#a", 1.0), ("adr:0005#a", 1.0)], got
    # 한 ADR이 두 노드에 걸리면 좋은 쪽: 0001은 노드1(컴포넌트만)에서 3, 노드2(technology+컴포넌트)에서 1
    got = dict(s.search("q", 100, nodes=[{"technology": "x", "repo": "a", "component": "x"}, {"technology": "Foo DB", "repo": "a", "component": "x"}]))
    assert got["adr:0001#a"] == 3.0


@case("S1: nodes 없음·빈 노드는 실패 · 하네스가 nodes를 넘긴다(질의 글만 보는 전략과 구분) · settings에 uses_query_nodes")
def _():
    s = s1_ready()
    for bad in (None, [], [{}], [{"repo": "a"}], "Foo"):
        assert raises(ValueError, s.search, "q", 5, nodes=bad), bad
    assert s.settings()["uses_query_nodes"] is True and s.index_size_bytes() == 0 and s.server_version() is None
    c2d = {c["chunk_id"]: c["doc_id"] for c in s1_chunks()}
    queries = [{"qid": "k1", "type": "K", "text": "x", "nodes": [{"technology": "Baz"}]},
               {"qid": "k2", "type": "K", "text": "y", "nodes": [{"technology": "Nope"}]}]
    run, lat = harness.run_queries(s, queries, c2d, k_docs=20, chunk_k=100)
    assert run == {"k1": {"adr:0004": 2.0}, "k2": {}} and len(lat) == 2
    assert raises(ValueError, harness.run_queries, s, [{"qid": "k3", "type": "K", "text": "x"}], c2d)     # nodes 없는 질의
    # 다른 전략은 nodes 없이 그대로 돈다
    run, _ = harness.run_queries(FakeComponent("S2", [("adr:0001#a", 1.0)]), [{"qid": "q", "type": "K", "text": "x"}], c2d)
    assert run == {"q": {"adr:0001": 1.0}}
    # C-ADR이 없으면 만들 수 없다 · ADR에 청크가 없으면 색인이 실패한다
    assert raises(ValueError, s1_match.S1Match, [S1_DOCS[-1]])
    assert raises(ValueError, s1_match.S1Match(S1_DOCS).index, [])


@case("S4 변형: S4.int8 · S4.bbq는 index_options.type만 다르다 (본문 나머지는 S4와 같다) · 인덱스 이름·id 구분 · oversample 3.0은 bbq만")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        emb = embed.Embedder(FakeEncoder(), "c-all@v1", "p@v1", cache_root=tmp)
        base = s4_dense.S4Dense("c-all@v1", emb, m=24, ef_construction=150, num_candidates=80)
        int8 = s4_dense.S4Int8("c-all@v1", emb, m=24, ef_construction=150, num_candidates=80)
        bbq = s4_dense.S4BBQ("c-all@v1", emb, m=24, ef_construction=150, num_candidates=80)
        assert (base.id, int8.id, bbq.id) == ("S4", "S4.int8", "S4.bbq") and int8.version == bbq.version == "v1"
        assert (base.index_name, int8.index_name, bbq.index_name) == \
            ("arena-c-all-v1-s4", "arena-c-all-v1-s4-int8", "arena-c-all-v1-s4-bbq")

        def body_with_type(typ):                          # S4 본문에서 type만 바꾼 것
            b = json.loads(json.dumps(base._body()))
            b["mappings"]["properties"]["embedding"]["index_options"]["type"] = typ
            return b
        assert int8._body() == body_with_type("int8_hnsw")
        assert bbq._body() == body_with_type("bbq_hnsw")
        f = bbq._body()["mappings"]["properties"]["embedding"]
        assert f == {"type": "dense_vector", "dims": 1024, "index": True, "similarity": "cosine",
                     "index_options": {"type": "bbq_hnsw", "m": 24, "ef_construction": 150}}, f
        assert "int8" not in json.dumps(base._body()) and "bbq" not in json.dumps(base._body())     # 기본 S4는 그대로
        for s, typ, quant in ((base, "hnsw", None), (int8, "int8_hnsw", "int8"), (bbq, "bbq_hnsw", "bbq")):
            st = s.settings()
            assert st["dense_vector"]["index_options"]["type"] == typ and st["embedding_model"] == base.settings()["embedding_model"]
            assert st["quantization"] == quant and st["index"] == s.index_name
        assert base.settings()["query"] == {k: v for k, v in bbq.settings()["query"].items() if k != "rescore_vector"}
        assert bbq.settings()["query"]["rescore_vector"] == {"oversample": 3.0}
        assert "rescore_vector" not in int8.settings()["query"] and "rescore_vector" not in base.settings()["query"]
        assert bbq.oversample == 3.0 and int8.oversample is None and base.oversample is None


@case("S4 변형: kNN 질의 본문 — bbq는 rescore_vector.oversample 3.0을 넣고 int8·S4는 넣지 않는다 · 색인은 변형 이름으로 생성 — 가짜 ES")
def _():
    real = es.call
    try:
        for cls, want_rescore in ((s4_dense.S4Dense, None), (s4_dense.S4Int8, None), (s4_dense.S4BBQ, {"oversample": 3.0})):
            state = {"count": 2}
            es.call = fake_es_for(state, [("d0#0", 0.9)])
            with tempfile.TemporaryDirectory() as tmp:
                emb = embed.Embedder(FakeEncoder(), "c-all@v1", "p@v1", cache_root=tmp)
                s = cls("c-all@v1", emb, num_candidates=50)
                s.index(chunks_of("alpha beta", "gamma"))
                put = [c for c in state["calls"] if c[0] == "PUT"][0]
                assert put[1] == "/" + s.index_name
                assert put[2]["mappings"]["properties"]["embedding"]["index_options"]["type"] == s.index_type
                assert s.search("alpha", 10) == [("d0#0", 0.9)]
                knn = [c for c in state["calls"] if c[1].endswith("/_search")][0][2]["knn"]
                assert knn.get("rescore_vector") == want_rescore, (cls.__name__, knn.keys())
                assert knn["k"] == 10 and knn["num_candidates"] == 50 and len(knn["query_vector"]) == 1024
    finally:
        es.call = real


# SPLADE — 가짜 토크나이저·모델 (다운로드 없음). 단어 j → 토큰 id 3+j, [CLS]=1 · [SEP]=2 · 패딩 0. 모델은 입력 토큰 id%8 칸에 logit 1.0을 낸다.
class FakeSpladeTok:
    def __init__(self):
        self.calls = []

    def _ids(self, t, special):
        ids = [3 + j for j, _ in enumerate(t.split())]
        return [1] + ids + [2] if special else ids

    def __call__(self, texts, add_special_tokens=True, truncation=False, max_length=None, padding=False, return_tensors=None):
        import torch
        self.calls.append({"truncation": truncation, "max_length": max_length})
        rows = [self._ids(t, add_special_tokens) for t in texts]
        if not truncation:
            return {"input_ids": rows}
        rows = [r[:max_length] for r in rows]
        width = max(len(r) for r in rows)
        ids = torch.tensor([r + [0] * (width - len(r)) for r in rows])
        mask = torch.tensor([[1] * len(r) + [0] * (width - len(r)) for r in rows])
        return {"input_ids": ids, "attention_mask": mask}


class FakeSpladeModel:
    V = 8

    def __call__(self, input_ids, attention_mask):
        import torch
        from types import SimpleNamespace
        logits = torch.zeros(*input_ids.shape, self.V)
        for b in range(input_ids.shape[0]):
            for pos in range(input_ids.shape[1]):
                logits[b, pos, int(input_ids[b, pos]) % self.V] = 1.0        # 패딩 칸(id 0)도 켠다 — mask가 가려야 한다
        return SimpleNamespace(logits=logits)


def fake_splade_encoder(max_length=5, batch_size=2):
    return s3a_splade.SpladeEncoder(device="cpu", max_length=max_length, batch_size=batch_size,
                                    model=FakeSpladeModel(), tokenizer=FakeSpladeTok(), revision="splrev0123456789")


@case("S3a: 풀링 = max_위치 log(1+relu(logits)) · mask — 손계산 · 마스크 밖 위치는 무시 · 음수는 0")
def _():
    import math
    import torch
    e = math.e
    logits = torch.tensor([[[1.0, -2.0, 0.0, e - 1], [0.0, 3.0, -1.0, 0.0], [100.0, 100.0, 100.0, 100.0]],
                           [[0.5, 0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 0.0], [0.0, 0.0, 7.0, 0.0]]])
    mask = torch.tensor([[1, 1, 0], [1, 1, 1]])
    rep = s3a_splade.splade_pool(logits, mask)
    # 행0: 위치0 → [ln2, 0, 0, ln(e)=1] · 위치1 → [0, ln4, 0, 0] · 위치2는 가려짐 → max = [ln2, ln4, 0, 1]
    want0 = [math.log(2), math.log(4), 0.0, 1.0]
    # 행1: [log1p(.5), 0, log1p(7)=ln8, 0]
    want1 = [math.log(1.5), 0.0, math.log(8), 0.0]
    assert rep.shape == (2, 4)
    assert all(abs(float(a) - b) < 1e-6 for a, b in zip(rep[0], want0)), rep[0]
    assert all(abs(float(a) - b) < 1e-6 for a, b in zip(rep[1], want1)), rep[1]


@case("S3a: 인코더 — 희소 표현은 0이 아닌 칸만 · 패딩은 mask로 가려진다 · 잘림 표시 · 잘린 입력은 max_length에서 실제로 잘린다")
def _():
    enc = fake_splade_encoder(max_length=5, batch_size=2)
    sparse, trunc = enc.encode(["a b", "a b c", "a b c d", "a"])
    ln2 = 0.6931471805599453
    # "a b" = [1,3,4,2] → 칸 1·3·4·2 · 길이 4 ≤ 5
    assert sorted(sparse[0]) == ["1", "2", "3", "4"] and all(abs(w - ln2) < 1e-6 for w in sparse[0].values())
    # "a b c" = 길이 5 → 안 잘림 [1,3,4,5,2] → 칸 1·3·4·5·2
    assert sorted(sparse[1]) == ["1", "2", "3", "4", "5"] and trunc[:2] == [False, False]
    # "a b c d" = 길이 6 > 5 → 잘림 [1,3,4,5,6]: [SEP](2)가 사라지고 6%8=6 칸이 켜진다
    assert trunc[2] is True and sorted(sparse[2]) == ["1", "3", "4", "5", "6"], sparse[2]
    assert trunc[3] is False and sorted(sparse[3]) == ["1", "2", "3"]
    assert "0" not in sparse[0] and "0" not in sparse[3]             # 패딩(id 0 칸)은 mask로 가려져 들어오지 않는다
    assert enc.max_tokens_seen == 6 and enc.model_id == "naver/splade-v3" and enc.revision == "splrev0123456789"
    assert {c["max_length"] for c in enc.tokenizer.calls if c["truncation"]} == {5}


@case("S3a: Embedder — 잘린 청크 수를 센다(캐시에서 읽어도) · info에 모델·revision·dtype·잘림 정책 · 토큰 값 없음 · 캐시 재사용")
def _():
    texts = ["a b", "a b c d", "a b c d e", "a"]
    chunks = chunks_of(*texts)
    with tempfile.TemporaryDirectory() as tmp:
        emb = s3a_splade.SpladeEmbedder(fake_splade_encoder(max_length=5), "c@v1", "p@v1", cache_root=tmp)
        dense, sp = emb.encode_chunks(chunks)
        assert dense is None and len(sp) == 4
        assert emb.stats["truncated_chunks"] == 2 and emb.stats["truncated_chunk_ids"] == ["d1#0", "d2#0"]
        emb.encode_chunks(chunks)                                        # 같은 실행 안 — 메모(다시 세지 않는다)
        assert emb.stats["truncated_chunks"] == 2
        emb2 = s3a_splade.SpladeEmbedder(fake_splade_encoder(max_length=5), "c@v1", "p@v1", cache_root=tmp)
        _, sp2 = emb2.encode_chunks(chunks)
        assert sp2 == sp and emb2.stats["cache_hits"] == 4 and emb2.stats["encoded"] == 0
        assert emb2.stats["truncated_chunks"] == 2                       # 캐시에서 읽어도 잘림은 센다
        _, q = emb2.encode_query("a b c d")
        assert emb2.stats["queries_truncated"] == 1 and sorted(q) == ["1", "3", "4", "5", "6"]
        i = emb2.info()
        assert i["truncated_chunks"] == 2 and i["model_id"] == "naver/splade-v3" and i["revision"] == "splrev0123456789"
        assert i["dtype"] == "float32" and i["max_length"] == 5 and i["device"] == "cpu" and "torch" in i["libraries"]
        assert i["chunks"] == 4 and i["cache_hits"] == 4 and i["queries_truncated"] == 1 and "자르고" in i["truncation_policy"]
        assert "splrev012345" in i["cache_namespace"] and "naver-splade-v3" in i["cache_namespace"]
        assert i["mean_nonzero_per_chunk"] > 0 and "value not recorded" in i["hf_token"]
        # 텍스트가 바뀌면 sha가 달라 다시 인코딩한다
        emb3 = s3a_splade.SpladeEmbedder(fake_splade_encoder(max_length=5), "c@v1", "p@v1", cache_root=tmp)
        emb3.encode_chunks(chunks_of("a b", "a b c d", "NEW text", "a"))
        assert emb3.stats["cache_hits"] == 3 and emb3.stats["encoded"] == 1


@case("S3a: HF_TOKEN — .env에서 읽기만 한다(os.environ 불변) · 없거나 비면 .env.example을 가리키는 한국어 실패 · 인코더·CLI도 같다")
def _():
    import os
    before = dict(os.environ)
    with tempfile.TemporaryDirectory() as tmp:
        ok = Path(tmp) / "ok.env"
        ok.write_text("# 주석\nOTHER=1\nHF_TOKEN = \"hf_secret_value\"\n", encoding="utf-8")
        assert s3a_splade.load_hf_token([ok]) == "hf_secret_value"
        assert dict(os.environ) == before                                  # 환경 변수에 넣지 않았다
        for name, body in (("empty.env", "HF_TOKEN=\n"), ("none.env", "OTHER=1\n"), ("missing.env", None)):
            p = Path(tmp) / name
            if body is not None:
                p.write_text(body, encoding="utf-8")
            try:
                s3a_splade.load_hf_token([p])
                raise AssertionError("토큰이 없는데 통과했다")
            except s3a_splade.HFTokenError as e:
                assert ".env.example" in str(e) and "HF_TOKEN" in str(e) and "게이트" in str(e)
        # 여러 후보 중 앞 파일에 없으면 다음 파일에서 찾는다
        assert s3a_splade.load_hf_token([Path(tmp) / "none.env", ok]) == "hf_secret_value"
        # 인코더를 모델 없이 만들면(실제 로딩 경로) 다운로드 전에 토큰 오류로 끝난다
        assert raises(s3a_splade.HFTokenError, s3a_splade.SpladeEncoder, device="cpu", env_files=[Path(tmp) / "none.env"])
        # CLI는 SystemExit로 같은 메시지를 낸다
        import importlib
        from types import SimpleNamespace
        cli = importlib.import_module("arena.cli")
        real = s3a_splade._env_files
        s3a_splade._env_files = lambda: [Path(tmp) / "none.env"]
        try:
            try:
                cli.make_splade_embedder(SimpleNamespace(device="cpu", encode_batch=4, no_cache=True), {"version": "c"}, {"version": "p"})
                raise AssertionError("SystemExit가 아니다")
            except SystemExit as e:
                assert ".env.example" in str(e)
        finally:
            s3a_splade._env_files = real


@case("S3a: 실제 로딩 경로는 토큰을 token=으로 명시해 다운로드·from_pretrained에 넘긴다 (환경 변수 아님) — 가짜 HF")
def _():
    import os
    import transformers
    seen = {}

    class FM:
        def to(self, d):
            seen["device"] = d
            return self

        def eval(self):
            seen["eval"] = True
            return self

    def fake_resolve(model_id, token=None):
        seen["resolve"] = (model_id, token)
        return "/fake/snap", "revfake0000000000"

    def fake_tok(path, token=None, **kw):
        seen["tok"] = (path, token)
        return FakeSpladeTok()

    def fake_model(path, token=None, **kw):
        seen["model"] = (path, token, kw)
        return FM()
    real = (s3a_splade.resolve_revision, transformers.AutoTokenizer.from_pretrained, transformers.AutoModelForMaskedLM.from_pretrained)
    before = dict(os.environ)
    with tempfile.TemporaryDirectory() as tmp:
        env = Path(tmp) / ".env"
        env.write_text("HF_TOKEN=tok123\n", encoding="utf-8")
        try:
            s3a_splade.resolve_revision = fake_resolve
            transformers.AutoTokenizer.from_pretrained = staticmethod(fake_tok)
            transformers.AutoModelForMaskedLM.from_pretrained = staticmethod(fake_model)
            e = s3a_splade.SpladeEncoder(device="cpu", env_files=[env])
        finally:
            s3a_splade.resolve_revision, transformers.AutoTokenizer.from_pretrained, transformers.AutoModelForMaskedLM.from_pretrained = real
    assert seen["resolve"] == ("naver/splade-v3", "tok123") and seen["tok"] == ("/fake/snap", "tok123")
    assert seen["model"][:2] == ("/fake/snap", "tok123") and seen["device"] == "cpu" and seen["eval"] is True
    assert e.revision == "revfake0000000000" and dict(os.environ) == before


@case("S3a: 전략 — 희소 내적 점수(S3b와 같은 식) · 설정에 모델·max_length·잘림 정책 · id/버전")
def _():
    class SpEmb:
        encoder = fake_splade_encoder(max_length=512)

        def encode_chunks(self, chunks):
            return None, [{"1": .5, "2": 1.0}, {"1": .5}, {"9": 1.0}]

        def encode_query(self, text):
            return None, {"1": .4, "2": .2}
    s = s3a_splade.S3aSplade("c@v1", SpEmb())
    s.index(chunks_of("x", "y", "z"))
    got = s.search("q", 10)
    # d0 = .4*.5 + .2*1 = .4 · d1 = .2 · d2 겹침 없음 → 제외
    assert [c for c, _ in got] == ["d0#0", "d1#0"] and abs(got[0][1] - 0.4) < 1e-12 and abs(got[1][1] - 0.2) < 1e-12
    st = s.settings()
    assert s.id == "S3a" and s.version == "v1" and st["embedding_model"] == "naver/splade-v3@splrev0123456789"
    assert st["max_length"] == 512 and "자르고" in st["truncation"]


@case("S5b: S3a+S4 문서 단위 RRF — 손계산과 같다 · 대표 청크(동점은 S3a 쪽) · 구성요소 키 s3a/s4 · 같은 chunk_k · k 불일치는 거부 · 새로 색인")
def _():
    chunk_to_doc = {"A#0": "A", "A#1": "A", "B#0": "B", "C#0": "C"}
    chunks = [{"chunk_id": c, "doc_id": d, "source_type": "repo", "text": "t"} for c, d in chunk_to_doc.items()]
    s3a = FakeComponent("S3a", [("A#1", 9.0), ("B#0", 8.0), ("A#0", 7.0)])
    s4 = FakeComponent("S4", [("C#0", 0.9), ("B#0", 0.8), ("A#0", 0.7)])
    s = s5b_rrf.S5bRRF(s3a, s4, rrf_k=60, chunk_k=100, sparse="s3a")      # SPLADE-v3 구성요소 (기본은 s3a.ko — #83)
    assert s.id == "S5b" and s.version == "v1"
    s.index(chunks)
    assert s3a.n_indexed == 4 and s4.n_indexed == 4 and s.n_indexed == 4
    got = s.search("q", 100)
    # S5a 테스트와 같은 손계산: A = 1/61 + 1/63 · B = 2/62 · C = 1/61
    want = {"A": 1 / 61 + 1 / 63, "B": 2 / 62, "C": 1 / 61}
    by_doc = {chunk_to_doc[c]: sc for c, sc in got}
    assert len(got) == 3 and all(abs(by_doc[d] - want[d]) < 1e-9 for d in want), (by_doc, want)
    assert [chunk_to_doc[c] for c, _ in got] == sorted(want, key=lambda d: -want[d])
    rep = {chunk_to_doc[c]: c for c, _ in got}
    assert rep["A"] == "A#1" and rep["C"] == "C#0" and rep["B"] == "B#0"
    # 같은 구성요소 순위를 넣은 S5a와 출력이 같다 (같은 융합 코드)
    s5a = s5a_rrf.S5aRRF(FakeComponent("S2", s3a._hits), FakeComponent("S4", s4._hits), rrf_k=60, chunk_k=100)
    s5a.index(chunks)
    assert s5a.search("q", 100) == got
    # 대표 청크 동점(두 구성요소가 같은 순위) → 앞 구성요소(S3a) 쪽 청크
    t1 = FakeComponent("S3a", [("A#0", 2.0), ("B#0", 1.0)])
    t2 = FakeComponent("S4", [("A#1", 2.0), ("B#0", 1.0)])
    tie = s5b_rrf.S5bRRF(t1, t2, sparse="s3a")
    tie.index(chunks)
    assert dict((chunk_to_doc[c], c) for c, _ in tie.search("q", 100))["A"] == "A#0"
    st = s.settings()
    assert list(st["components"]) == ["s3a", "s4"] and st["components"]["s3a"]["marker"] == "S3a"
    assert st["fusion"]["params"] == {"k": 60} and st["component_chunk_k"] == 100 and st["reuse_of_saved_results"] is False
    assert s3a.asked and set(s3a.asked) == {100} and set(s4.asked) == {100}
    assert raises(ValueError, s.search, "q", 50)
    assert s.index_size_bytes() == 20 and s.size_breakdown() == {"s3a": 10, "s4": 10}
    for bad in ({"rrf_k": 0}, {"chunk_k": 0}, {"rrf_k": 1.5}):
        assert raises(ValueError, s5b_rrf.S5bRRF, s3a, s4, **bad), bad


@case("CLI: 새 전략(s1 · s3a · s4.int8 · s4.bbq · s5b)이 --strategy에 있고 make_strategy가 알맞은 객체를 만든다 · 점 있는 id도 결과 키에 들어간다")
def _():
    import importlib
    from types import SimpleNamespace
    cli = importlib.import_module("arena.cli")
    assert cli.STRATEGY_CHOICES == ["s1", "s2", "s3a", "s3a.ko", "s3b", "s4", "s4.int8", "s4.bbq", "s4.kure", "s5a", "s5b", "s6", "s7"]
    assert set(cli.SPLADE_STRATEGIES) == {"s3a", "s5b"} and {"s4.int8", "s4.bbq", "s5b"} <= set(cli.BGE_STRATEGIES)
    assert {"s3a", "s5b", "s4.bbq"} <= set(cli.EMBEDDING_STRATEGIES) and "s1" not in cli.EMBEDDING_STRATEGIES
    args = SimpleNamespace(es_url="http://x", hnsw_m=16, ef_construction=100, num_candidates=100, rrf_k=60, chunk_k=100)
    cfg = {"version": "c-all@v1"}
    with tempfile.TemporaryDirectory() as tmp:
        emb = embed.Embedder(FakeEncoder(), "c-all@v1", "p@v1", cache_root=tmp)
        spl = s3a_splade.SpladeEmbedder(fake_splade_encoder(), "c-all@v1", "p@v1", cache_root=tmp)

        def mk(n):
            return cli.make_strategy(n, args, cfg, {"version": "p@v1"}, emb, splade=spl, docs=S1_DOCS)
        assert type(mk("s1")) is s1_match.S1Match and type(mk("s3a")) is s3a_splade.S3aSplade
        assert type(mk("s4.int8")) is s4_dense.S4Int8 and type(mk("s4.bbq")) is s4_dense.S4BBQ and type(mk("s4")) is s4_dense.S4Dense
        b = cli.make_strategy("s5b", SimpleNamespace(**vars(args), s5b_sparse="s3a"), cfg, {"version": "p@v1"}, emb, splade=spl)
        assert type(b) is s5b_rrf.S5bRRF and type(b.s3a) is s3a_splade.S3aSplade and type(b.s4) is s4_dense.S4Dense
        assert type(mk("s5a")) is s5a_rrf.S5aRRF
    for sid in ("S1", "S3a", "S4.int8", "S4.bbq", "S5b", "S5b.ko"):
        key = harness.result_key(sid, "v1", "c-all@v1", "toy@v0")
        assert key == f"{sid}.v1__c-all@v1__toy@v0__T0__seed0", key


@case("CLI: --strategy에 s2 · s3b · s4 · s5a · s6이 있다 · 결과 키에 전략 id/버전이 들어간다")
def _():
    import importlib
    cli = importlib.import_module("arena.cli")
    assert {"s2", "s3b", "s4", "s5a", "s6"} <= set(cli.STRATEGY_CHOICES) and "s6" in cli.EMBEDDING_STRATEGIES
    assert harness.result_key("S5a", "v1", "c-all@v1", "toy@v0").startswith("S5a.v1__")
    assert harness.result_key("S6", "v1", "c-all@v1", "toy@v0").startswith("S6.v1__")
    assert harness.result_key("S3b", "v1", "c-all@v1", "toy@v0").startswith("S3b.v1__")
    assert harness.result_key("S4", "v1", "c-all@v1", "toy@v0").startswith("S4.v1__")


# --- HYO-35 (이슈 #66): S4.kure KURE-v1 · S3a.ko KURE-Sparse-nano · S5b SPLADE 선택 -----------------------------------------------
# 가짜 모델만 쓴다 — 다운로드 없음.

class FakeSTModel:
    """SentenceTransformer 흉내: tokenizer(공백 단어 + 특수 토큰 2개) · prompts(비어 있음 = 접두 없음) · max_seq_length · encode."""

    class _Tok:
        def __call__(self, texts, add_special_tokens=True, truncation=False):
            return {"input_ids": [list(range(len(t.split()) + (2 if add_special_tokens else 0))) for t in texts]}

    def __init__(self, prompts=None):
        self.tokenizer = self._Tok()
        self.prompts = {} if prompts is None else prompts
        self.max_seq_length = 8192
        self.calls = []

    def encode(self, texts, **kw):
        import numpy as np
        self.calls.append((list(texts), dict(kw)))
        out = np.zeros((len(texts), 1024), dtype=np.float32)
        for i, t in enumerate(texts):
            out[i, len(t) % 1024] = 1.0
        return out


def kure_encoder(model=None, **kw):
    return embed.SentenceTransformerDenseEncoder("nlpai-lab/KURE-v1", "8b418a58414668e75532ed045c22d9ca018ae2b2",
                                                 device="cpu", model=model or FakeSTModel(), **kw)


@case("S4.kure 인코더: 질의 · 문서가 같은 인자(fp32 · normalize · 접두 없음 · max_seq_length 512)로 인코딩되고 sparse 자리는 비어 있다")
def _():
    m = FakeSTModel()
    enc = kure_encoder(m)
    assert m.max_seq_length == 512 and enc.max_length == 512            # 모델 기본 8192를 512로 내린다
    d, s = enc.encode_docs(["alpha beta", "gamma"])
    q, _ = enc.encode_queries(["alpha"])
    assert d.shape == (2, 1024) and d.dtype.name == "float32" and q.shape == (1, 1024) and s == [{}, {}]
    assert len(m.calls) == 2 and m.calls[0][1] == m.calls[1][1], m.calls
    assert m.calls[0][1]["normalize_embeddings"] is True and m.calls[0][1]["convert_to_numpy"] is True
    assert not any("prompt" in k for k in m.calls[0][1]) and enc.instruction_prefix is None and enc.prompts == {}
    assert enc.revision == "8b418a58414668e75532ed045c22d9ca018ae2b2"


@case("S4.kure 인코더: 512토큰(특수 토큰 포함)을 넘는 입력은 실패한다 — 510+2는 통과 · 511+2는 실패하고 모델에 가지도 않는다 · 최대 토큰 수 기록")
def _():
    m = FakeSTModel()
    enc = kure_encoder(m)
    enc.encode_docs([" ".join(["w"] * 510)])
    assert enc.max_tokens_seen == 512
    assert raises(embed.TruncationError, enc.encode_docs, ["a b", " ".join(["w"] * 511)])
    assert len(m.calls) == 1
    bad = FakeSTModel()
    bad.encode = lambda texts, **kw: __import__("numpy").zeros((len(texts), 512), dtype="float32")
    assert raises(ValueError, kure_encoder(bad).encode_docs, ["a b"])           # 차원이 1024가 아니면 실패


@case("S4.kure: 색인 본문 · 설정은 S4와 같고 id · 인덱스 이름 · 임베딩 모델만 다르다 — 같은 청크 · 같은 HNSW 파라미터")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        bge = embed.Embedder(FakeEncoder(), "c-all@v1", "p@v1", cache_root=tmp)
        ku = embed.Embedder(kure_encoder(), "c-all@v1", "p@v1", cache_root=tmp)
        a = s4_dense.S4Dense("c-all@v1", bge, m=24, ef_construction=150, num_candidates=80)
        b = s4_dense.S4Kure("c-all@v1", ku, m=24, ef_construction=150, num_candidates=80)
        assert (a.id, b.id, b.version) == ("S4", "S4.kure", "v1")
        assert (a.index_name, b.index_name) == ("arena-c-all-v1-s4", "arena-c-all-v1-s4-kure")
        assert a._body() == b._body()
        sa, sb = a.settings(), b.settings()
        for key in ("index", "embedding_model"):
            assert sa.pop(key) != sb.pop(key)
        assert sa == sb
        assert b.settings()["embedding_model"] == "nlpai-lab/KURE-v1@8b418a58414668e75532ed045c22d9ca018ae2b2"
        f = b._body()["mappings"]["properties"]["embedding"]
        assert f == {"type": "dense_vector", "dims": 1024, "index": True, "similarity": "cosine",
                     "index_options": {"type": "hnsw", "m": 24, "ef_construction": 150}}, f
        assert b.quantization is None and b.oversample is None


@case("S4.kure: 임베딩 캐시 이름공간이 모델마다 다르다 (모델 id + revision) — KURE 캐시가 BGE-M3 캐시를 건드리지 않고, 두 번째 실행은 캐시에서 읽는다")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        m = FakeSTModel()
        ku = embed.Embedder(kure_encoder(m), "c-all@v1", "p@v1", cache_root=tmp)
        bge = embed.Embedder(FakeEncoder(), "c-all@v1", "p@v1", cache_root=tmp)
        assert ku.namespace != bge.namespace and "KURE-v1-8b418a584146" in ku.namespace, ku.namespace
        other_rev = embed.Embedder(embed.SentenceTransformerDenseEncoder("nlpai-lab/KURE-v1", "ffffffffffffffff", device="cpu", model=FakeSTModel()),
                                   "c-all@v1", "p@v1", cache_root=tmp)
        assert other_rev.namespace != ku.namespace                          # revision이 바뀌면 캐시도 바뀐다
        ch = chunks_of("alpha beta", "gamma")
        ku.encode_chunks(ch)
        assert (Path(tmp) / ku.namespace / "index.json").is_file() and not (Path(tmp) / bge.namespace).exists()
        ku2 = embed.Embedder(kure_encoder(m), "c-all@v1", "p@v1", cache_root=tmp)
        before = len(m.calls)
        ku2.encode_chunks(ch)
        assert len(m.calls) == before and ku2.stats["cache_hits"] == 2
        info = ku.info()
        assert info["model_id"] == "nlpai-lab/KURE-v1" and info["instruction_prefix"] is None and info["dtype"] == "float32"
        assert "sentence-transformers" in info["libraries"] and info["max_tokens_seen_in_encoded"] == 4


class FakeKureSparseModel:
    """SparseEncoder 흉내 — 질의용 · 문서용 메서드를 따로 기록한다. 받은 입력(접두 지시문은 모델 안에서 붙으므로 원문)과 호출한 메서드를 남긴다."""

    class _Tok:
        def __call__(self, texts, add_special_tokens=True, truncation=False):
            return {"input_ids": [list(range(len(t.split()) + (2 if add_special_tokens else 0))) for t in texts]}

    def __init__(self, prompts=None):
        self.tokenizer = self._Tok()
        self.prompts = {"query": "[Q] ", "document": "[D] "} if prompts is None else prompts
        self.max_seq_length = 512
        self.calls = []

    def _enc(self, kind, texts, **kw):
        import torch
        self.calls.append((kind, list(texts), dict(kw)))
        out = torch.zeros(len(texts), 12)
        for i, t in enumerate(texts):
            out[i, len(t) % 12] = 1.5 if kind == "query" else 2.5          # 질의 · 문서가 구별되는 값
        return out

    def encode_query(self, texts, **kw):
        return self._enc("query", texts, **kw)

    def encode_document(self, texts, **kw):
        return self._enc("document", texts, **kw)

    def encode(self, *a, **kw):                          # 직접 encode는 부르면 안 된다 (접두 지시문이 안 붙는다)
        raise AssertionError("encode()를 직접 부르면 질의 · 문서 접두 지시문이 빠진다")


def kure_sparse_encoder(model=None, **kw):
    from arena.strategies import s3a_ko_kure_sparse as ko
    return ko.KureSparseEncoder(device="cpu", model=model or FakeKureSparseModel(), **kw)


@case("S3a.ko 인코더: 문서는 encode_document · 질의는 encode_query로 부른다 (encode 직접 호출 없음) · 핀 고정 모델 id · 표현은 0이 아닌 칸만")
def _():
    from arena.strategies import s3a_ko_kure_sparse as ko
    m = FakeKureSparseModel()
    enc = kure_sparse_encoder(m, max_length=64)
    assert (enc.model_id, enc.revision, enc.trust_remote_code) == ("nlpai-lab/KURE-Sparse-nano", "1eb281e81edcf44cbb401d475054f817b49fddbd", True)
    assert ko.REVISION == "1eb281e81edcf44cbb401d475054f817b49fddbd" and ko.MAX_LENGTH == 8192
    docs, tr = enc.encode_docs(["alpha beta", "gamma"])
    qs, _ = enc.encode_queries(["alpha"])
    assert [c[0] for c in m.calls] == ["document", "query"] and enc.call_log == ["document", "query"]
    assert docs == [{str(len("alpha beta") % 12): 2.5}, {str(len("gamma") % 12): 2.5}] and tr == [False, False]
    assert qs == [{str(len("alpha") % 12): 1.5}]
    assert m.calls[0][2]["convert_to_sparse_tensor"] is False and m.max_seq_length == 64
    assert raises(ValueError, kure_sparse_encoder, FakeKureSparseModel(prompts={}))                  # prompts가 없으면 실패
    assert raises(ValueError, kure_sparse_encoder, FakeKureSparseModel(prompts={"query": "[Q] "}))


@case("S3a.ko 인코더: 접두 지시문까지 포함해 max_length를 넘으면 실패한다 (조용히 자르지 않는다) · 최대 토큰 수 기록 · 질의도 같다")
def _():
    m = FakeKureSparseModel()
    enc = kure_sparse_encoder(m, max_length=12)
    # 접두 "[D] "가 단어 하나로 센다 — 단어 9개 + 접두 1 + 특수 2 = 12 → 통과 · 단어 10개 → 13 → 실패
    docs, tr = enc.encode_docs([" ".join(["w"] * 8)])
    assert len(m.calls) == 1 and enc.max_tokens_seen == 11
    enc.encode_docs([" ".join(["w"] * 9)])
    assert enc.max_tokens_seen == 12
    assert raises(embed.TruncationError, enc.encode_docs, ["a", " ".join(["w"] * 10)])
    assert raises(embed.TruncationError, enc.encode_queries, [" ".join(["w"] * 10)])
    assert len(m.calls) == 2                                          # 실패한 입력은 모델에 가지도 않았다


@case("S3a.ko 임베더: 캐시 이름공간이 S3a와 다르다 · 문서/질의 메서드 구분 · 잘림 0 · system.json 기록(모델 · revision · trust_remote_code · prompts · 버전)")
def _():
    from arena.strategies import s3a_ko_kure_sparse as ko
    with tempfile.TemporaryDirectory() as tmp:
        m = FakeKureSparseModel()
        emb = ko.KureSparseEmbedder(kure_sparse_encoder(m), "c@v1", "p@v1", cache_root=tmp)
        spl = s3a_splade.SpladeEmbedder(fake_splade_encoder(), "c@v1", "p@v1", cache_root=tmp)
        assert emb.namespace != spl.namespace and "KURE-Sparse-nano-1eb281e81edc" in emb.namespace and emb.namespace.endswith("fp32-L8192")
        ch = chunks_of("alpha beta", "gamma")
        _, sp = emb.encode_chunks(ch)
        _, q = emb.encode_query("alpha")
        assert len(sp) == 2 and len(q) == 1 and [c[0] for c in m.calls] == ["document", "query"]
        emb2 = ko.KureSparseEmbedder(kure_sparse_encoder(m), "c@v1", "p@v1", cache_root=tmp)         # 두 번째: 캐시
        emb2.encode_chunks(ch)
        assert [c[0] for c in m.calls] == ["document", "query"] and emb2.stats["cache_hits"] == 2
        info = emb.info()
        assert info["model_id"] == "nlpai-lab/KURE-Sparse-nano" and info["revision"] == "1eb281e81edcf44cbb401d475054f817b49fddbd"
        assert info["trust_remote_code"] is True and info["prompts"] == {"query": "[Q] ", "document": "[D] "}
        assert info["dtype"] == "float32" and info["similarity"] == "dot" and info["truncated_chunks"] == 0
        assert info["max_tokens_seen_in_encoded"] == 5 and info["max_length"] == 8192 and info["libraries"]["torch"]
        assert info["query_method"] == "encode_query" and info["document_method"] == "encode_document"
        assert info["chunk_encode_seconds"] >= 0 and info["cache_namespace"] == emb.namespace
        s = ko.S3aKo("c@v1", emb)
        assert s.id == "S3a.ko" and s.version == "v1"
        s.index(ch)
        assert s.n_indexed == 2 and s.index_size_bytes() == (len(sp[0]) + len(sp[1])) * 8
        hits = s.search("alpha", 10)                                       # 점수 = 질의 · 문서 내적 (같은 칸이 켜진 청크만)
        assert hits and all(isinstance(c, str) for c, _ in hits)
        st = s.settings()
        assert st["embedding_model"] == "nlpai-lab/KURE-Sparse-nano@1eb281e81edcf44cbb401d475054f817b49fddbd" and st["trust_remote_code"] is True
        assert st["prompts"] == {"query": "[Q] ", "document": "[D] "} and st["max_length"] == 8192


@case("S5b: --s5b-sparse 선택 — 기본은 s3a.ko(dev 2026-10-13, #83) · 구성요소 키·sparse_component·결과 id(S5b / S5b.ko)가 선택을 따른다 · 알 수 없는 값은 거부")
def _():
    hits3 = [("A#1", 9.0), ("B#0", 8.0)]
    hits4 = [("B#0", 0.9), ("A#1", 0.8)]
    default = s5b_rrf.S5bRRF(FakeComponent("S3a", hits3), FakeComponent("S4", hits4), rrf_k=60, sparse="s3a")    # k 기본값은 구성요소마다 달라 맞춘다 (#75)
    assert s5b_rrf.S5bRRF(FakeComponent("S3a.ko", []), FakeComponent("S4", [])).sparse == "s3a.ko" == s5b_rrf.DEFAULT_SPARSE
    import importlib
    assert s5b_rrf.DEFAULT_SPARSE == importlib.import_module("arena.cli").S5B_DEFAULT_SPARSE        # 두 곳의 기본이 같다
    st = default.settings()
    assert list(st["components"]) == ["s3a", "s4"] and st["sparse_component"] == "s3a" and default.sparse == "s3a"
    ko = s5b_rrf.S5bRRF(FakeComponent("S3a.ko", hits3), FakeComponent("S4", hits4), sparse="s3a.ko")
    sk = ko.settings()
    assert list(sk["components"]) == ["s3a.ko", "s4"] and sk["sparse_component"] == "s3a.ko"
    assert sk["components"]["s3a.ko"]["marker"] == "S3a.ko" and ko.id == "S5b.ko" and default.id == "S5b"
    assert harness.result_key(ko.id, ko.version, "c-all@v1", "g") != harness.result_key(default.id, default.version, "c-all@v1", "g")   # 결과 키가 갈린다
    assert raises(ValueError, s5b_rrf.S5bRRF, FakeComponent("x", []), FakeComponent("y", []), sparse="s3b")
    for s in (default, ko):                                              # 순위 · 점수는 구성요소 선택과 무관하게 같은 규칙
        s.index([{"chunk_id": c, "doc_id": c.split("#")[0], "source_type": "repo", "text": c} for c in ("A#1", "B#0")])
    assert default.search("q", 100) == ko.search("q", 100)


@case("CLI: s4.kure · s3a.ko가 --strategy에 있다 · make_strategy가 알맞은 객체를 만든다 · s5b는 --s5b-sparse로 SPLADE를 고르고 기본은 s3a.ko(#83) · 다른 전략에 쓰면 거부 · 핀 고정")
def _():
    import importlib
    from types import SimpleNamespace
    from arena.strategies import s3a_ko_kure_sparse as ko
    cli = importlib.import_module("arena.cli")
    assert {"s4.kure", "s3a.ko"} <= set(cli.STRATEGY_CHOICES) and "s4.kure" in cli.KURE_STRATEGIES and "s3a.ko" in cli.KURE_SPARSE_STRATEGIES
    assert "s4.kure" not in cli.BGE_STRATEGIES and "s3a.ko" not in cli.SPLADE_STRATEGIES              # BGE-M3 · HF 토큰이 필요 없다
    assert {"s4.kure", "s3a.ko"} <= set(cli.EMBEDDING_STRATEGIES)
    assert cli.KURE_MODEL_ID == "nlpai-lab/KURE-v1" and cli.KURE_REVISION == "8b418a58414668e75532ed045c22d9ca018ae2b2"
    assert ko.MODEL_ID == "nlpai-lab/KURE-Sparse-nano"
    base = dict(es_url="http://x", hnsw_m=16, ef_construction=100, num_candidates=100, rrf_k=60, chunk_k=100)
    cfg = {"version": "c-all@v1"}
    with tempfile.TemporaryDirectory() as tmp:
        kemb = embed.Embedder(kure_encoder(), "c-all@v1", "p@v1", cache_root=tmp)
        bemb = embed.Embedder(FakeEncoder(), "c-all@v1", "p@v1", cache_root=tmp)
        sko = ko.KureSparseEmbedder(kure_sparse_encoder(), "c-all@v1", "p@v1", cache_root=tmp)
        spl = s3a_splade.SpladeEmbedder(fake_splade_encoder(), "c-all@v1", "p@v1", cache_root=tmp)
        a = cli.make_strategy("s4.kure", SimpleNamespace(**base), cfg, {"version": "p@v1"}, kemb)
        assert type(a) is s4_dense.S4Kure and a.embedder is kemb
        assert type(cli.make_strategy("s3a.ko", SimpleNamespace(**base), cfg, {"version": "p@v1"}, None, splade=sko)) is ko.S3aKo
        n = cli.make_strategy("s5b", SimpleNamespace(**base), cfg, {"version": "p@v1"}, bemb, splade=sko)           # s5b_sparse 인자 없음 = 기본 s3a.ko
        assert type(n.s3a) is ko.S3aKo and n.sparse == "s3a.ko" and n.id == "S5b.ko"
        d = cli.make_strategy("s5b", SimpleNamespace(**base, s5b_sparse="s3a"), cfg, {"version": "p@v1"}, bemb, splade=spl)
        assert type(d.s3a) is s3a_splade.S3aSplade and d.sparse == "s3a" and list(d.settings()["components"]) == ["s3a", "s4"] and d.id == "S5b"
        k = cli.make_strategy("s5b", SimpleNamespace(**base, s5b_sparse="s3a.ko"), cfg, {"version": "p@v1"}, bemb, splade=sko)
        assert type(k.s3a) is ko.S3aKo and type(k.s4) is s4_dense.S4Dense and list(k.settings()["components"]) == ["s3a.ko", "s4"]
        assert k.settings()["sparse_component"] == "s3a.ko"
    for args, want in ((SimpleNamespace(strategy="s5b", s5b_sparse="s3a.ko"), "s3a.ko"), (SimpleNamespace(strategy="s5b", s5b_sparse="s3a"), "s3a"),
                       (SimpleNamespace(strategy="s5b"), "s3a.ko"), (SimpleNamespace(strategy="s5b", s5b_sparse=None), "s3a.ko"), (SimpleNamespace(strategy="s3a.ko"), "s3a.ko"),
                       (SimpleNamespace(strategy="s3a"), "s3a"), (SimpleNamespace(strategy="s4.kure"), None), (SimpleNamespace(strategy="s2"), None)):
        assert cli.sparse_kind(args) == want, (args, want)
    for sid in ("S4.kure", "S3a.ko"):
        assert harness.result_key(sid, "v1", "c-all@v1", "toy@v0") == f"{sid}.v1__c-all@v1__toy@v0__T0__seed0"
    try:                                                                  # 다른 전략에 --s5b-sparse를 쓰면 코퍼스를 읽기 전에 거부
        cli.main(["run", "--strategy", "s2", "--s5b-sparse", "s3a.ko", "--queries", "q", "--qrels", "r", "--gold-version", "toy@v0",
                  "--snapshots", "s", "--adr", "a"])
        raise AssertionError("거부되지 않았다")
    except SystemExit as e:
        assert e.code == 2



@case("dev 기본값 (이슈 #75, 2026-10-13): RRF k — S5a 60 · S5b(s3a) 10 · S5b(s3a.ko) 60 · --rrf-k를 주면 그 값 · S4 num_candidates 200")
def _():
    import importlib
    from types import SimpleNamespace
    from arena.strategies import s3a_ko_kure_sparse as ko
    cli = importlib.import_module("arena.cli")
    assert s5b_rrf.S5bRRF(FakeComponent("S3a", []), FakeComponent("S4", []), sparse="s3a").rrf_k == 10
    assert s5b_rrf.S5bRRF(FakeComponent("S3a.ko", []), FakeComponent("S4", [])).rrf_k == 60          # 기본 s3a.ko (#83)
    assert s5b_rrf.S5bRRF(FakeComponent("S3a.ko", []), FakeComponent("S4", []), sparse="s3a.ko").rrf_k == 60
    assert s5a_rrf.S5aRRF(FakeComponent("S2", []), FakeComponent("S4", [])).rrf_k == 60
    base = dict(es_url="http://x", hnsw_m=16, ef_construction=100, num_candidates=200, rrf_k=None, chunk_k=100)
    cfg = {"version": "c-all@v1"}
    with tempfile.TemporaryDirectory() as tmp:
        bemb = embed.Embedder(FakeEncoder(), "c-all@v1", "p@v1", cache_root=tmp)
        spl = s3a_splade.SpladeEmbedder(fake_splade_encoder(), "c-all@v1", "p@v1", cache_root=tmp)
        sko = ko.KureSparseEmbedder(kure_sparse_encoder(), "c-all@v1", "p@v1", cache_root=tmp)

        def k_of(name, splade=None, **kw):
            s = cli.make_strategy(name, SimpleNamespace(**{**base, **kw}), cfg, {"version": "p@v1"}, bemb, splade=splade)
            return s.settings()["fusion"]["params"]["k"], s
        assert k_of("s5a")[0] == 60 and k_of("s5b", spl, s5b_sparse="s3a")[0] == 10 and k_of("s5b", sko, s5b_sparse="s3a.ko")[0] == 60
        assert k_of("s5b", sko)[0] == 60                                                      # 기본 s3a.ko (#83)
        assert k_of("s5b", spl, rrf_k=60, s5b_sparse="s3a")[0] == 60 and k_of("s5a", rrf_k=10)[0] == 10          # 명시하면 그 값
        k, s = k_of("s5b", spl, s5b_sparse="s3a")
        assert "dev에서 정함" in s.settings()["fusion"]["status"] and s.s4.num_candidates == 200


@case("§6 진단 변형 (이슈 #83): flat · int8_flat · bbq_flat — 색인 본문에 m·ef_construction이 없다 · 인덱스 이름이 따로 · bbq_flat만 재채점 · CLI 선택지(격자 밖)")
def _():
    import importlib
    from types import SimpleNamespace
    cli = importlib.import_module("arena.cli")
    with tempfile.TemporaryDirectory() as tmp:
        emb = embed.Embedder(FakeEncoder(), "c-all@v1", "p@v1", cache_root=tmp)
        want = {s4_dense.S4Flat: ("S4.flat", "flat", "arena-c-all-v1-s4-flat", None, None),
                s4_dense.S4Int8Flat: ("S4.int8flat", "int8_flat", "arena-c-all-v1-s4-int8flat", "int8", None),
                s4_dense.S4BBQFlat: ("S4.bbqflat", "bbq_flat", "arena-c-all-v1-s4-bbqflat", "bbq", 3.0)}
        for cls, (sid, typ, index, quant, over) in want.items():
            s = cls("c-all@v1", emb, m=24, ef_construction=150)
            f = s._body()["mappings"]["properties"]["embedding"]
            assert f["index_options"] == {"type": typ}, f                 # 그래프 파라미터를 넣으면 ES가 거부한다
            st = s.settings()
            assert st["dense_vector"]["index_options"] == {"type": typ} and st["quantization"] == quant
            assert (s.id, s.index_name, s.oversample) == (sid, index, over)
            assert ("rescore_vector" in st["query"]) == (over is not None)
        hnsw = s4_dense.S4Dense("c-all@v1", emb, m=24, ef_construction=150)._body()["mappings"]["properties"]["embedding"]
        assert hnsw["index_options"] == {"type": "hnsw", "m": 24, "ef_construction": 150}      # hnsw 계열은 그대로
        args = SimpleNamespace(es_url="http://x", hnsw_m=16, ef_construction=100, num_candidates=200, rrf_k=None, chunk_k=100)
        for name, cls in (("s4.flat", s4_dense.S4Flat), ("s4.int8flat", s4_dense.S4Int8Flat), ("s4.bbqflat", s4_dense.S4BBQFlat)):
            assert type(cli.make_strategy(name, args, {"version": "c-all@v1"}, {"version": "p@v1"}, emb)) is cls
    assert cli.DIAG_STRATEGIES == ["s4.flat", "s4.int8flat", "s4.bbqflat"]
    assert not set(cli.DIAG_STRATEGIES) & set(cli.STRATEGY_CHOICES)          # 격자 전략 목록에는 넣지 않는다


# --- JHS 보조 지표 (HYO-34 · retrieval-arena §5 변경 2026-10-13) ----------------------------------------------------

from arena import jhs_metrics as jm  # noqa: E402


def rel_doc(repo, tag):
    return {"doc_id": f"github-release:{repo}@{tag}", "corpus": "c-doc", "source_type": "release_note",
            "meta": {"repo": repo, "tag": tag}}


def jhs_nodes():
    return jm.Nodes({"version": "nodes@test", "technologies": [
        {"id": "tech:lc", "label": "LangChain", "aliases": [{"text": "LangChain", "source": "adr:0009"}]},
        {"id": "tech:lca", "label": "langchain-anthropic", "aliases": [{"text": "langchain-anthropic", "source": "s"}]},
        {"id": "tech:cc", "label": "Claude Code", "aliases": [{"text": "Claude Code", "source": "s"}]}],
        "components": [{"id": "component:dr/product", "repo": "dr", "name": "product", "source": "s"},
                       {"id": "component:dr/skill", "repo": "dr", "name": "skill", "source": "s"}]})


@case("JHS 버전: tag 읽기 — 모노레포 `<패키지>==` · v 접두어 · 숫자 아닌 tag는 None · 정답 릴리스가 하나가 아니면 실패")
def _():
    assert jm.split_tag("langchain-anthropic==1.7.5") == ("langchain-anthropic", "1.7.5")
    assert jm.split_tag("v0.23.0") == (None, "v0.23.0")
    assert jm.parse_version("v0.23.0") == (0, 23, 0) and jm.parse_version("1.2.13") == (1, 2, 13)
    assert jm.parse_version("b11240") is None and jm.parse_version(None) is None
    rels = jm.release_index([rel_doc("o/a", "v1.0.0"), rel_doc("o/a", "v1.1.0"),
                             {"doc_id": "adr:0001", "corpus": "c-adr", "source_type": "adr", "meta": {}}])
    assert set(rels) == {"github-release:o/a@v1.0.0", "github-release:o/a@v1.1.0"}
    q = {"qid": "C1", "type": "C", "expected_version": "1.1.0"}
    assert jm.correct_release(q, {"github-release:o/a@v1.1.0": 2}, rels) == "github-release:o/a@v1.1.0"
    assert raises(ValueError, jm.correct_release, q, {"github-release:o/a@v1.1.0": 1}, rels)       # 등급 2가 아니다
    assert raises(ValueError, jm.correct_release, q, {"github-release:o/a@v1.0.0": 2}, rels)       # 버전이 다르다
    assert raises(ValueError, jm.release_index, [{"doc_id": "github-release:x@1", "meta": {}}])     # repo·tag 없음


@case("JHS 버전 혼동 (C): 이전 형제가 정답 위 · 정답이 상위 k 밖인데 이전 형제가 안 → 위험 / 이후 형제 · 등급 1 형제 · 다른 패키지 → 위험 아님")
def _():
    docs = [rel_doc("o/a", t) for t in ("v1.0.0", "v1.1.0", "v1.2.0", "v1.0.5", "v1.1.1")] + \
           [rel_doc("o/m", "core==1.0.0"), rel_doc("o/m", "app==1.1.0"), rel_doc("o/m", "app==0.9.0"), rel_doc("o/solo", "v2.0.0")]
    rels = jm.release_index(docs)
    R = "github-release:"
    queries = [{"qid": f"C{i}", "type": "C", "expected_version": v} for i, v in
               ((1, "1.1.0"), (2, "1.1.0"), (3, "1.1.0"), (4, "1.1.0"), (5, "1.1.0"), (6, "2.0.0"))] + \
              [{"qid": "E1", "type": "E", "expected_version": "1.1.0"}]
    qrels = {q: {R + "o/a@v1.1.0": 2, R + "o/a@v1.0.5": 1} for q in ("C1", "C2", "C3", "C4", "E1")}
    qrels["C5"] = {R + "o/m@app==1.1.0": 2}
    qrels["C6"] = {R + "o/solo@v2.0.0": 2}
    filler = [f"x{i}" for i in range(12)]
    run = {"C1": {R + "o/a@v1.0.0": 3, R + "o/a@v1.1.0": 2},                     # 이전 형제가 정답 위 → 위험
           "C2": {d: 1 for d in filler[:10] + [R + "o/a@v1.1.0"] + [R + "o/a@v1.0.0"]},    # 둘 다 상위 10 밖 → 위험 아님
           "C3": {R + "o/a@v1.0.0": 2, **{d: 1 for d in filler}},                # 정답 없음 + 이전 형제 상위 k → 위험
           "C4": {R + "o/a@v1.2.0": 3, R + "o/a@v1.0.5": 2.5, R + "o/a@v1.1.0": 2, R + "o/a@v1.0.0": 1},  # 이후 형제 · 등급1 형제만 위
           "C5": {R + "o/m@core==1.0.0": 3, R + "o/m@app==0.9.0": 2, R + "o/m@app==1.1.0": 1},  # 다른 패키지는 형제 아님
           "C6": {R + "o/solo@v2.0.0": 1}, "E1": {R + "o/a@v1.0.0": 2}}
    t = jm.version_confusion(run, qrels, queries, rels, k=10)
    rows = {r["qid"]: r for r in t["rows"]}
    assert "E1" not in rows                                                      # E는 넣지 않는다
    assert t["risk_qids"] == ["C1", "C3", "C5"], t["risk_qids"]
    assert rows["C1"]["risk_docs"] == [R + "o/a@v1.0.0"] and rows["C1"]["correct_rank"] == 2
    assert [s["doc"] for s in rows["C4"]["later"]] == [R + "o/a@v1.2.0", R + "o/a@v1.1.1"] and not rows["C4"]["risk"]
    assert R + "o/a@v1.0.5" not in {s["doc"] for g in ("earlier", "later") for s in rows["C4"][g]}   # 등급 1은 형제가 아니다
    assert [s["doc"] for s in rows["C5"]["earlier"]] == [R + "o/m@app==0.9.0"]  # core==는 다른 패키지
    assert rows["C6"]["note"] == "형제 없음" and not rows["C6"]["risk"]


@case("JHS Correct Version@k (C·E): 정답 릴리스 적중 · 「Recall과 같음」은 등급 2가 릴리스뿐일 때만")
def _():
    R = "github-release:"
    rels = jm.release_index([rel_doc("o/a", "v1.1.0")])
    queries = [{"qid": "C1", "type": "C", "expected_version": "v1.1.0"},
               {"qid": "E1", "type": "E", "expected_version": "1.1.0"}]
    qrels = {"C1": {R + "o/a@v1.1.0": 2}, "E1": {R + "o/a@v1.1.0": 2, "stack:dr/product/requirements.txt": 2}}
    run = {"C1": {"x": 2, R + "o/a@v1.1.0": 1}, "E1": {R + "o/a@v1.1.0": 1}}
    m = jm.correct_version(run, qrels, queries, rels, ks=(1, 5))
    assert m["@1"]["C"]["mean"] == 0.0 and m["@5"]["C"]["mean"] == 1.0 and m["@5"]["C"]["same_as_recall"]
    assert m["@5"]["E"]["mean"] == 1.0 and not m["@5"]["E"]["same_as_recall"]     # Recall은 0.5 (manifest를 놓침)


@case("JHS 변경 2026-10-15: 버전 비교 정규화 — 끝 0 · v 접두어 · 숫자 아닌 tag는 대소문자 무시 문자열 · 같은 버전 형제는 order_unknown")
def _():
    assert jm.version_key("1.3") == jm.version_key("1.3.0") == jm.version_key("v1.3") == (1, 3)
    assert jm.version_key("1.0") == (1,) and jm.version_key("0.0") == (0,)
    assert jm.version_key("B11240") == jm.version_key("b11240") == "b11240"
    assert jm.version_key(None) is None and jm.version_key("  ") is None
    assert jm.version_key("1.3.1") != jm.version_key("1.3")
    R = "github-release:"
    rels = jm.release_index([rel_doc("g/l", "b100"), rel_doc("o/a", "v1.3.0"), rel_doc("o/a", "v1.2.9"),
                             rel_doc("o/a", "1.3"), rel_doc("o/a", "v1.10.0")])
    assert jm.correct_release({"qid": "C1", "expected_version": "B100"}, {R + "g/l@b100": 2}, rels) == R + "g/l@b100"
    q = {"qid": "C2", "type": "C", "expected_version": "1.3"}
    assert jm.correct_release(q, {R + "o/a@v1.3.0": 2}, rels) == R + "o/a@v1.3.0"            # 1.3 = 1.3.0
    rows = jm.version_confusion({"C2": {}}, {"C2": {R + "o/a@v1.3.0": 2}}, [q], rels)["rows"]
    assert [s["doc"] for s in rows[0]["earlier"]] == [R + "o/a@v1.2.9"]                       # (1,2,9) < (1,3)
    assert [s["doc"] for s in rows[0]["later"]] == [R + "o/a@v1.10.0"]                        # 1.10 > 1.3 (문자열 순서 아님)
    assert [s["doc"] for s in rows[0]["order_unknown"]] == [R + "o/a@1.3"]                    # 같은 버전의 다른 문서


@case("JHS 변경 2026-10-15: 정답 릴리스를 못 찾는 질의는 「판정 불가」 — 평균·Recall과 같음·위험 수에서 빼고 이유를 적는다 (실행은 멈추지 않는다)")
def _():
    R = "github-release:"
    rels = jm.release_index([rel_doc("o/a", "v1.0.0"), rel_doc("o/a", "v1.1.0")])
    queries = [{"qid": "C1", "type": "C", "expected_version": "1.1.0"},
               {"qid": "C2", "type": "C", "expected_version": "2.0.0"},                      # 버전이 맞는 릴리스 없음
               {"qid": "C3", "type": "C"},                                                    # expected_version 없음
               {"qid": "E1", "type": "E", "expected_version": "1.1.0"}]
    qrels = {"C1": {R + "o/a@v1.1.0": 2}, "C2": {R + "o/a@v1.1.0": 2}, "C3": {R + "o/a@v1.1.0": 2},
             "E1": {R + "o/a@v1.0.0": 2, R + "o/a@v1.1.0": 1}}                               # 등급 2의 버전이 다르다
    run = {q: {R + "o/a@v1.0.0": 2, R + "o/a@v1.1.0": 1} for q in qrels}
    m = jm.correct_version(run, qrels, queries, rels, ks=(1,))
    assert m["@1"]["C"]["n"] == 1 and m["@1"]["C"]["mean"] == 0.0 and set(m["@1"]["C"]["undetermined"]) == {"C2", "C3"}
    assert m["@1"]["C"]["same_as_recall"] is True
    assert m["@1"]["E"] == {"n": 0, "mean": None, "per_query": {}, "same_as_recall": None,
                            "undetermined": {"E1": m["@1"]["E"]["undetermined"]["E1"]}}
    t = jm.version_confusion(run, qrels, queries, rels, k=10)
    assert t["risk_qids"] == ["C1"] and t["undetermined_qids"] == ["C2", "C3"]
    rows = {r["qid"]: r for r in t["rows"]}
    assert rows["C2"]["risk"] is None and rows["C2"]["note"].startswith("판정 불가")
    out = jm.compute(run, {}, qrels, queries, [rel_doc("o/a", "v1.0.0"), rel_doc("o/a", "v1.1.0")], {}, jhs_nodes())
    assert out["version_confusion"]["undetermined_qids"] == ["C2", "C3"]                      # compute가 멈추지 않는다


@case("JHS 변경 2026-10-15: 지표 계산이 예외로 실패해도 헤드라인 결과는 저장된다 — jhs.error에 적고 오류를 돌려준다")
def _():
    from arena import cli
    ok, err = cli.jhs_block({}, {}, {}, [], [], {}, jhs_nodes())
    assert err is None and "error" not in ok
    bad, err = cli.jhs_block({}, {}, {}, [], [{"doc_id": "github-release:x@1", "meta": {}}], {}, jhs_nodes())  # meta 없음 → ValueError
    assert err and err.startswith("ValueError") and bad["error"] == err and "Traceback" in bad["traceback"]
    with tempfile.TemporaryDirectory() as tmp:
        d = harness.save_results(tmp, "k", {"q": {}}, {"overall": {}, "jhs": bad}, {}, {})
        assert json.loads((d / "metrics.json").read_text(encoding="utf-8"))["jhs"]["error"] == err


@case("JHS component 근거 (E): 같은 repo 스택 표 행(별칭+component 칸) · manifest 디렉터리 — 경계·다른 repo·다른 칸·상위 k 밖은 잡지 않는다")
def _():
    nodes = jhs_nodes()
    row = "| langgraph · langchain-anthropic | product (spike) |\n| Claude Code routines | skill |\n| LangChain 문서 | eval |"
    texts = {"stack:dr/CLAUDE.md#stack#1": row,
             "stack:dr/product/requirements.txt#0": "langchain==1.4.3\nlangchain-anthropic==1.7.5",
             "stack:other/CLAUDE.md#stack#1": "| langchain | product |"}
    kind = jm.component_evidence_kind
    assert kind("stack:dr/CLAUDE.md#stack", row, "dr", "product", ["langchain-anthropic"]) == "stack-row"
    assert kind("stack:dr/CLAUDE.md#stack", row, "dr", "product", ["LangChain"]) is None    # langchain-anthropic 안의 langchain은 아니다
    assert kind("stack:dr/CLAUDE.md#stack", row, "dr", "skill", ["Claude Code"]) == "stack-row"
    assert kind("stack:dr/CLAUDE.md#stack", "| Claude Code를 쓴다 | skill에서 |", "dr", "skill", ["Claude Code"]) == "stack-row"  # 조사
    assert kind("stack:dr/CLAUDE.md#stack", "| product 도구 | eval |", "dr", "product", ["product"]) is None  # component가 의존성 칸에만
    assert kind("stack:dr/product/requirements.txt", texts["stack:dr/product/requirements.txt#0"], "dr", "product", ["LangChain"]) == "manifest"
    assert kind("stack:dr/requirements.txt", "langchain==1", "dr", "product", ["LangChain"]) is None   # 루트 manifest는 component가 없다
    assert kind("stack:other/CLAUDE.md#stack", texts["stack:other/CLAUDE.md#stack#1"], "dr", "product", ["LangChain"]) is None
    queries = [{"qid": "E1", "type": "E", "nodes": [{"technology": "LangChain", "repo": "dr", "component": "product"}]},
               {"qid": "E2", "type": "E", "nodes": [{"technology": "Claude Code", "repo": "dr", "component": "skill"}]},
               {"qid": "E3", "type": "E", "nodes": [{"technology": "없는 기술", "repo": "dr", "component": "product"}]},
               {"qid": "C1", "type": "C", "nodes": [{"technology": "LangChain", "repo": "dr", "component": "product"}]}]
    qrels = {"E1": {"stack:dr/product/requirements.txt": 2}, "E2": {"r": 2}, "E3": {"r": 2}, "C1": {"r": 2}}
    far = {f"x{i}": 1 for i in range(10)}
    run = {"E1": {"stack:other/CLAUDE.md#stack": 3, "stack:dr/product/requirements.txt": 2},
           "E2": {**far, "stack:dr/CLAUDE.md#stack": 0.5},                                # 11위 — 상위 10 밖
           "E3": {"stack:dr/product/requirements.txt": 1}, "C1": {"stack:dr/product/requirements.txt": 1}}
    reps = {"E1": {"stack:other/CLAUDE.md#stack": "stack:other/CLAUDE.md#stack#1",
                   "stack:dr/product/requirements.txt": "stack:dr/product/requirements.txt#0"},
            "E2": {"stack:dr/CLAUDE.md#stack": "stack:dr/CLAUDE.md#stack#1"},
            "E3": {"stack:dr/product/requirements.txt": "stack:dr/product/requirements.txt#0"}}
    t = jm.component_evidence(run, reps, texts, queries, qrels, nodes, k=10)
    rows = {r["qid"]: r for r in t["rows"]}
    assert set(rows) == {"E1", "E2", "E3"}                                          # C는 넣지 않는다
    assert rows["E1"]["first_rank"] == 2 and rows["E1"]["evidence"][0]["via"] == "manifest" and rows["E1"]["overlaps_grade2"]
    assert rows["E2"]["evidence"] == [] and rows["E2"]["first_rank"] is None
    assert rows["E3"]["note"] == "nodes@v1에 없는 기술" and rows["E3"]["node"] is None


@case("JHS 커뮤니티 비율@k: reddit·hn 비율의 질의 평균 · 유형별 · 빈 결과는 뺀다")
def _():
    queries = [{"qid": "q1", "type": "B"}, {"qid": "q2", "type": "C"}, {"qid": "q3", "type": "C"}]
    st = {"r1": "community", "h1": "community", "d1": "release_note", "d2": "adr"}
    run = {"q1": {"r1": 3, "d1": 2}, "q2": {"d2": 1, "h1": 0.5, "r1": 0.1}, "q3": {}}
    m = jm.community_share(run, queries, st, ks=(1, 5))
    assert m["@1"]["overall"] == 0.5 and m["@1"]["by_type"] == {"B": 1.0, "C": 0.0}
    assert m["@5"]["overall"] == round((0.5 + 2 / 3) / 2, 4) and m["@5"]["by_type"]["C"] == round(2 / 3, 4)


@case("JHS Seed Recall@k: id 일치만 · B는 뺀다 · 어휘에 없는 노드는 놓친 것으로 세고 따로 적는다")
def _():
    nodes = jhs_nodes()
    queries = [{"qid": "E1", "type": "E", "nodes": [{"technology": "langchain", "repo": "dr", "component": "product"}]},
               {"qid": "K1", "type": "K", "nodes": [{"technology": "LangChain", "repo": "dr", "component": "retrieval-arena"}]},
               {"qid": "B1", "type": "B", "nodes": [{"technology": "GGUF reranker"}]}]
    seeds = {"E1": ["tech:lca", "tech:lc", "component:dr/product"], "K1": ["tech:lc"], "B1": ["tech:cc"]}
    r = jm.seed_recall(seeds, queries, nodes, ks=(1, 3))
    assert r["n"] == 2 and "B1" not in r["per_query"]
    assert r["per_query"]["E1"] == {"@1": 0.0, "@3": 1.0}                      # 별칭은 대소문자만 무시 — 1위는 다른 노드
    assert r["per_query"]["K1"] == {"@1": 0.5, "@3": 0.5}                      # retrieval-arena는 어휘에 없다 → 놓침
    assert r["not_in_graph"] == {"K1": ["component:dr/retrieval-arena"]}
    assert jm.Nodes.tech_id(nodes, "Lang Chain") is None                       # 흔들림은 지표가 흡수하지 않는다


@case("JHS nodes 어휘 검사가 잡는다: 두 노드에 같은 별칭 · 출처 없음 · 출처 문서에 없는 별칭 · component id 형식")
def _():
    base = {"version": "v", "components": []}
    t = lambda i, a, s="s": {"id": i, "label": i, "aliases": [{"text": a, "source": s}]}     # noqa: E731
    assert raises(ValueError, jm.Nodes, {**base, "technologies": [t("a", "LangChain"), t("b", "langchain")]})
    assert raises(ValueError, jm.Nodes, {**base, "technologies": [{"id": "a", "aliases": [{"text": "x"}]}]})
    assert raises(ValueError, jm.Nodes, {"version": "v", "technologies": [],
                                         "components": [{"id": "dr/product", "repo": "dr", "name": "product", "source": "s"}]})
    n = jm.Nodes({"version": "v", "technologies": [t("a", "LangChain", "adr:1"), t("b", "Neo4j", "adr:9")],
                  "components": [{"id": "component:dr/product", "repo": "dr", "name": "product", "source": "adr:1"}]})
    bad = jm.check_nodes(n, {"adr:1": "LangChain을 product에 쓴다"})
    assert len(bad) == 1 and "adr:9" in bad[0]
    assert jm.check_nodes(n, {"adr:1": "langchain · product", "adr:9": "NEO4J"}) == []
    real = jm.load_nodes(HERE.parent / "eval" / "arena" / "nodes@v1.json")         # 등록된 어휘 파일 자체가 검사를 통과한다
    assert real.version == "nodes@v1" and real.tech_id("OpenAI Agents SDK") == real.tech_id("openai-agents")
    v2 = jm.load_nodes(HERE.parent / "eval" / "arena" / "nodes@v2.json")           # v2 = v1 + ADR affected component (기술 노드·별칭은 같다)
    assert v2.version == "nodes@v2" and v2.alias == real.alias and set(real.components) < set(v2.components)
    assert {"component:decision-radar/retrieval-arena", "component:decision-radar/runtime", "component:decision-radar/ui",
            "component:medical-mate-ai/obs"} <= set(v2.components) - set(real.components)


@case("S7 graph@v1: 스택 행 USES · manifest PINS(문서 있는 버전은 그 릴리스, 없으면 version 노드) · NEXT · ADR · source_hint · RECORDED_AS · 경로 존재율")
def _():
    from arena import graph
    nodes = jm.Nodes({"version": "nodes@test", "technologies": [
        {"id": "tech:lc", "label": "LangChain", "aliases": [{"text": "LangChain", "source": "s"}, {"text": "langchain-ai/langchain", "source": "s"}]},
        {"id": "tech:lca", "label": "langchain-anthropic", "aliases": [{"text": "langchain-anthropic", "source": "s"}]},
        {"id": "tech:leann", "label": "LEANN", "aliases": [{"text": "LEANN", "source": "s"}]}],
        "components": [{"id": "component:dr/product", "repo": "dr", "name": "product", "source": "s"},
                       {"id": "component:dr/runtime", "repo": "dr", "name": "runtime", "source": "adr:0009"},
                       {"id": "component:dr/retrieval-arena", "repo": "dr", "name": "retrieval-arena", "source": "adr:0004"}]})
    rel = lambda t: {"repo": "langchain-ai/langchain", "tag": t}                                     # noqa: E731
    adr = lambda n, fm: {"doc_id": f"adr:{n}", "corpus": "c-adr", "source_type": "adr", "meta": {},     # noqa: E731
                         "text": "---\n" + fm + "\n---\n# 본문"}
    docs = [
        {"doc_id": "github-release:lc@langchain==1.4.3", "corpus": "c-doc", "source_type": "release_note", "meta": rel("langchain==1.4.3")},
        {"doc_id": "github-release:lc@langchain==1.5.0", "corpus": "c-doc", "source_type": "release_note", "meta": rel("langchain==1.5.0")},
        {"doc_id": "github-release:lc@langchain-openai==1.0", "corpus": "c-doc", "source_type": "release_note", "meta": rel("langchain-openai==1.0")},
        {"doc_id": "source-hint:leann", "corpus": "c-doc", "source_type": "official_doc", "meta": {"url": "https://www.github.com/x/LEANN/releases/"}},
        {"doc_id": "stack:dr/CLAUDE.md#stack", "corpus": "c-code", "source_type": "stack", "meta": {"repo": "dr", "path": "CLAUDE.md"},
         "text": "| 의존성 | component |\n|---|---|\n| langchain · langchain-anthropic | product (spike, ADR 0009) |"},
        {"doc_id": "stack:dr/product/requirements.txt", "corpus": "c-code", "source_type": "stack",
         "meta": {"repo": "dr", "path": "product/requirements.txt"}, "text": "# 주석\nlangchain==1.4.3\nlangchain-anthropic>=1.7\nnumpy==2"},
        adr("0009", "technology: LangChain\naffected:\n- {repo: dr, component: runtime}"),
        adr("0004", "technology: LEANN\naffected:\n- {repo: dr, component: retrieval-arena}\nrevisit_when:\n"
                    "- {id: c1, source_hint: github.com/x/leann/releases}\n- {id: c2, source_hint: repo:dr/product/requirements.txt}\n"
                    "- {id: c3, source_hint: docs.nowhere.io}")]
    G, report = graph.build(docs, nodes)
    e = lambda a, b, r: any(d["rel"] == r for d in G.get_edge_data(a, b, default={}).values())        # noqa: E731
    assert e("component:dr/product", "tech:lc", "USES") and e("component:dr/product", "tech:lca", "USES")
    assert e("tech:lc", "github-release:lc@langchain==1.4.3", "HAS_RELEASE")                      # 모노레포는 tag의 패키지로
    assert report["unlinked_releases"] == ["github-release:lc@langchain-openai==1.0"]
    assert e("stack:dr/product/requirements.txt", "github-release:lc@langchain==1.4.3", "PINS")    # 문서 있는 버전은 그 릴리스로
    assert e("stack:dr/product/requirements.txt", "version:tech:lca@1.7", "PINS")                  # 없으면 version 노드
    assert G.nodes["version:tech:lca@1.7"]["doc"] is False and G["stack:dr/product/requirements.txt"]["version:tech:lca@1.7"][0]["op"] == ">="
    assert report["unparsed_pins"] == ["stack:dr/product/requirements.txt: numpy==2"]
    assert e("stack:dr/product/requirements.txt", "component:dr/product", "DECLARES")              # manifest 디렉터리 = component
    assert e("github-release:lc@langchain==1.4.3", "github-release:lc@langchain==1.5.0", "NEXT")
    assert e("adr:0009", "tech:lc", "DECIDES") and e("adr:0009", "component:dr/runtime", "AFFECTS")
    assert e("component:dr/product", "component:dr/runtime", "RECORDED_AS")                         # 스택 행이 ADR 0009를 인용
    assert e("adr:0004", "source-hint:leann", "REVISIT_SOURCE") and e("adr:0004", "stack:dr/product/requirements.txt", "REVISIT_SOURCE")
    assert report["unmatched_hints"] == ["adr:0004 c3: docs.nowhere.io"]
    assert graph.stats(G)["kinds"]["release"] == 4
    q = {"qid": "K1", "type": "K", "split": "dev", "nodes": [{"technology": "LEANN", "repo": "dr", "component": "retrieval-arena"}]}
    row = graph.reachability(G, [q], {"K1": {"adr:0004": 2, "github-release:lc@langchain==1.5.0": 2, "hn:x": 1}}, nodes)[0]
    assert row["g2"] == {"n": 2, "in_graph": 2, "@1": 1, "@2": 1, "@3": 1}                         # 1.5.0은 LEANN에서 4홉
    assert row["g1"]["in_graph"] == 0


@case("하네스: run_queries가 대표 청크를 남긴다 · save_results extra 파일 (이름 검사) · peak_memory · build_cost(cold 여부)")
def _():
    from types import SimpleNamespace

    from arena import cli

    class Fake(harness.Strategy):
        def search(self, query, k):
            return [("a#1", 2.0), ("b#0", 1.5), ("a#0", 1.0)]
    reps = {}
    run, _ = harness.run_queries(Fake(), [{"qid": "q1", "text": "x"}], {"a#0": "A", "a#1": "A", "b#0": "B"}, rep_chunks=reps)
    assert reps == {"q1": {"A": "a#1", "B": "b#0"}} and list(run["q1"]) == ["A", "B"]
    with tempfile.TemporaryDirectory() as tmp:
        d = harness.save_results(tmp, "k", {}, {}, {}, {}, extra={"rep_chunks.json": reps})
        assert json.loads((d / "rep_chunks.json").read_text(encoding="utf-8")) == reps
        for bad in ("run.json", "x.txt", "../x.json"):
            assert raises(ValueError, harness.save_results, tmp, "k2", {}, {}, {}, {}, extra={bad: {}})
    mem = harness.peak_memory()
    assert isinstance(mem["peak_rss_bytes"], int) and mem["peak_rss_bytes"] > 0, mem
    e = lambda hits: SimpleNamespace(info=lambda: {"model_id": "m", "chunks": 10, "cache_hits": hits, "encoded": 10 - hits,  # noqa: E731
                                                     "chunk_encode_seconds": 1.0, "device": "cpu"})
    sysd = {"index_build_seconds": 5.0}
    cold = cli.build_cost(sysd, [e(0)], SimpleNamespace())
    warm = cli.build_cost(sysd, [e(0), e(3)], SimpleNamespace(build_llm_cost_usd=0.25))
    assert cold["cold_build"] and cold["cold_build_seconds"] == 5.0 and cold["build_llm_cost_usd"] == 0.0
    assert not warm["cold_build"] and warm["cold_build_seconds"] is None and warm["note"] and warm["build_llm_cost_usd"] == 0.25
    assert cli.build_cost(sysd, [], SimpleNamespace())["cold_build"]                  # BM25: 임베딩 없음 → 늘 cold
    assert set(cli.DIAG_STRATEGIES) <= set(cli.BGE_STRATEGIES) <= set(cli.EMBEDDING_STRATEGIES)


# --- S7 구조 그래프 실행 (HYO-36) — ES 없이: 가짜 BM25 + 주입한 그래프 ----------------------------------------------------

class FakeBM25:
    """S2BM25 자리 — 청크 순위를 고정해 돌려준다."""
    id, version = "S2", "v1"

    def __init__(self, hits):
        self._hits, self.took_ms, self.n_indexed, self.asked, self.indexed, self.dropped = hits, [], 0, [], 0, False

    def settings(self):
        return {"marker": "bm25"}

    def index(self, chunks):
        self.n_indexed, self.indexed = len(chunks), self.indexed + 1

    def search(self, q, k):
        self.asked.append(k)
        return list(self._hits)

    def index_size_bytes(self):
        return 77

    def server_version(self):
        return "9.x"

    def drop(self):
        self.dropped = True


def s7_builder(kinds, edges):
    """kinds {id: kind 또는 (kind, doc)} · edges [(a, b)] → graph.build 자리를 하는 builder."""
    import networkx as nx

    def builder(docs, nodes):
        G = nx.MultiDiGraph(version="graph@v1", nodes_version="nodes@test")
        for n, k in kinds.items():
            kind, doc = k if isinstance(k, tuple) else (k, None)
            G.add_node(n, kind=kind, **({} if doc is None else {"doc": doc}))
        for a, b in edges:
            G.add_edge(a, b, rel="X")
        return G, {}
    return builder


def s7_make(hits, kinds, edges, docs_with_chunks, seed_k=1, alpha=0.15, chunk_k=100):
    """docs_with_chunks {doc: [chunk 이름 접미]} → (S7Graph, FakeBM25, 청크 목록). 색인까지 해 둔다."""
    from types import SimpleNamespace

    from arena.strategies import s7_graph
    chunks = [{"chunk_id": f"{d}#{i}", "doc_id": d, "source_type": "repo", "text": "t"}
              for d, idx in docs_with_chunks.items() for i in idx]
    bm = FakeBM25(hits)
    s = s7_graph.S7Graph(bm, [], SimpleNamespace(version="nodes@test"), seed_k=seed_k, alpha=alpha, chunk_k=chunk_k,
                         graph_builder=s7_builder(kinds, edges))
    s.index(chunks)
    return s, bm, chunks



def s7_docs(res, chunk_to_doc):
    return [d for d, _, _ in harness.rollup_chunks(res, chunk_to_doc)]


@case("S7: 순간이동 α → networkx 감쇠 1−α로 바꿔 넘긴다 — nx.pagerank(alpha=0.85)와 같은 순서, α를 바꾸면 순서가 달라진다 (변환을 빼면 실패)")
def _():
    import networkx as nx
    edges = [("S", "N"), ("S", "M"), ("M", "F1"), ("M", "F2"), ("M", "F3"), ("F1", "F4"), ("N", "T")]
    names = ["S", "N", "M", "F1", "F2", "F3", "F4", "T"]
    kinds = {n: ("release", True) for n in names}
    orders = {}
    for alpha in (0.15, 0.5):
        s, _, chunks = s7_make([("S#0", 5.0)], kinds, edges, {n: [0] for n in names}, seed_k=1, alpha=alpha)
        assert abs(s.nx_damping - (1 - alpha)) < 1e-12 and s.settings()["nx_damping"] == s.nx_damping
        assert s.settings()["teleport_alpha"] == alpha
        got = s7_docs(s.search("q", 20), {c["chunk_id"]: c["doc_id"] for c in chunks})
        G = nx.Graph(edges)
        ppr = nx.pagerank(G, alpha=1 - alpha, personalization={"S": 1.0}, max_iter=100, tol=1e-6)         # 손으로 변환한 기대값
        want = sorted(names, key=lambda n: (-round(ppr[n], 12), n))
        assert got == want, (alpha, got, want)
        orders[alpha] = got
    assert orders[0.15] != orders[0.5], orders                   # 이 그래프에서 α가 순서를 바꾼다 — 변환을 빼면(=α를 감쇠로 넘기면) 위 기대값과 어긋난다
    assert orders[0.15].index("F1") < orders[0.15].index("T") and orders[0.5].index("T") < orders[0.5].index("F1")
    s = s7_make([("S#0", 5.0)], kinds, edges, {n: [0] for n in names}, alpha=0.15)[0]
    s.nx_damping = 0.15                                            # 변환을 빠뜨린 구현을 흉내 — 결과가 달라져야 한다 (회귀 증거)
    assert s7_docs(s.search("q", 20), {f"{n}#0": n for n in names}) != orders[0.15]
    for bad in (0, 1, -0.1, 1.5, True, None):
        assert raises(ValueError, s7_make, [], kinds, edges, {"S": [0]}, alpha=bad), bad
    for bad in (0, -1, True, 2.5):
        assert raises(ValueError, s7_make, [], kinds, edges, {"S": [0]}, seed_k=bad), bad


@case("S7: PPR 문서 먼저 · BM25로 채움(중복 없음) · version 노드(doc=False)·technology·component·청크 없는 노드는 결과에 없다 · 시드 가중치는 BM25 점수 비례 · k로 자른다")
def _():
    import networkx as nx
    kinds = {"tech:t": "technology", "comp:c": "component", "R1": ("release", True), "R2": ("release", True),
             "R3": ("release", True), "V": ("release", False), "A1": "adr", "D1": "document", "P": ("release", True)}
    edges = [("tech:t", "R1"), ("tech:t", "R1"), ("tech:t", "R2"), ("R1", "R2"), ("A1", "tech:t"), ("A1", "comp:c"),
             ("comp:c", "R1"), ("D1", "V"), ("R2", "V"), ("D1", "comp:c"), ("tech:t", "P")]        # R1-tech 평행 관계 2개 · R3 고립 · P는 청크 없음
    docs = {"R1": [0, 1], "R2": [0], "R3": [0], "A1": [0], "D1": [0], "X": [0]}                    # X = 그래프 밖 문서 (커뮤니티 글)
    hits = [("R1#1", 9.0), ("X#0", 8.0), ("A1#0", 7.0), ("R3#0", 6.0), ("R1#0", 5.0)]
    s, bm, chunks = s7_make(hits, kinds, edges, docs, seed_k=3, alpha=0.3)
    c2d = {c["chunk_id"]: c["doc_id"] for c in chunks}
    res = s.search("q", 50)
    assert bm.asked == [100]                                                                      # BM25는 chunk_k만큼 가져온다
    got = s7_docs(res, c2d)
    assert len(got) == len(set(got)) == len(res)                                                  # 문서마다 하나 · 중복 없음
    assert not ({"tech:t", "comp:c", "V", "P"} & set(got))                                        # 문서 아닌 노드 · 청크 없는 노드는 없다
    # 기대값 — 시드 = 상위 3 BM25 문서(R1, X, A1) 중 그래프 노드(R1, A1), 가중치 9:7
    Gu = nx.Graph([(a, b) for a, b in edges])
    Gu.add_nodes_from(kinds)
    ppr = nx.pagerank(Gu, alpha=0.7, personalization={"R1": 9 / 16, "A1": 7 / 16}, max_iter=100, tol=1e-6)
    ppr_docs = sorted((n for n in docs if n in Gu and ppr[n] > 0), key=lambda n: (-round(ppr[n], 12), n))
    assert got[:len(ppr_docs)] == ppr_docs and set(ppr_docs) == {"R1", "A1", "R2", "D1"}, (got, ppr_docs)
    assert got[len(ppr_docs):] == ["X", "R3"]                                                     # BM25 순서로 채움 (이미 나온 R1·A1은 건너뜀)
    assert [v for _, v in res] == sorted({v for _, v in res}, reverse=True) and len({v for _, v in res}) == len(res)   # 엄격 감소
    assert dict((c2d[c], c) for c, _ in res)["R1"] == "R1#1" and dict((c2d[c], c) for c, _ in res)["R2"] == "R2#0"     # BM25가 올린 청크 · 아니면 첫 청크
    assert [c2d[c] for c, _ in s.search("q", 2)] == got[:2]                                       # k로 자른다
    # 시드 가중치가 균등이면 달라지는 그래프에서 점수 비례임을 확인 (회귀 증거)
    uni = nx.pagerank(Gu, alpha=0.7, personalization={"R1": 0.5, "A1": 0.5}, max_iter=100, tol=1e-6)
    assert [round(ppr[n], 9) for n in ppr_docs] != [round(uni[n], 9) for n in ppr_docs]


@case("S7: 같은 PPR 점수는 BM25 순위 → (BM25에 없는 문서는 뒤) 문서 id 순 · 대표 청크 = BM25 청크, 없으면 첫 청크")
def _():
    names = ["S", "a", "b", "c", "d"]
    kinds = {n: ("release", True) for n in names}
    edges = [("S", n) for n in "abcd"]                                  # a·b·c·d는 S에 대칭으로 붙어 PPR 점수가 같다
    docs = {"S": [0], "a": [0, 1], "b": [0], "c": [0, 1], "d": [0]}
    hits = [("S#0", 10.0), ("c#1", 3.0), ("d#0", 2.0)]                  # BM25 순위: S, c, d (a·b는 없음)
    s, _, chunks = s7_make(hits, kinds, edges, docs, seed_k=1)
    c2d = {c["chunk_id"]: c["doc_id"] for c in chunks}
    res = s.search("q", 20)
    assert s7_docs(res, c2d) == ["S", "c", "d", "a", "b"], res
    rep = dict((c2d[c], c) for c, _ in res)
    assert rep["c"] == "c#1" and rep["a"] == "a#0"
    assert [v for _, v in res] == [5.0, 4.0, 3.0, 2.0, 1.0]


@case("S7: 시드가 없으면 BM25 순서 그대로 — 상위 seed_k 안에 그래프 노드가 없으면 그 밖의 그래프 노드가 있어도 시드가 아니다 · last_seeds 빈 목록")
def _():
    kinds = {"R1": ("release", True), "R2": ("release", True), "tech:t": "technology"}
    edges = [("tech:t", "R1"), ("tech:t", "R2"), ("R1", "R2")]
    docs = {"X": [0, 1], "Y": [0], "R1": [0], "R2": [0]}
    hits = [("X#0", 9.0), ("Y#0", 8.0), ("R1#0", 7.0), ("X#1", 6.0)]
    s, _, chunks = s7_make(hits, kinds, edges, docs, seed_k=2)
    c2d = {c["chunk_id"]: c["doc_id"] for c in chunks}
    res = s.search("q", 50)
    assert s7_docs(res, c2d) == ["X", "Y", "R1"] and s.last_seeds == []
    s2 = s7_make(hits, kinds, edges, docs, seed_k=3)[0]                 # k=3이면 R1이 시드가 된다 — PPR이 R2를 끌어온다
    assert s7_docs(s2.search("q", 50), c2d)[:2] == ["R1", "R2"] and s2.last_seeds == ["R1", "tech:t"]


@case("S7: last_seeds = BM25 순서로 [문서 노드, 1홉 technology·component (id순)] 이어 붙이고 중복은 처음 것만 · 검색마다 새로 채운다")
def _():
    kinds = {"tech:t": "technology", "comp:c": "component", "R1": ("release", True), "R2": ("release", True),
             "A1": "adr", "R3": ("release", True)}
    edges = [("tech:t", "R1"), ("comp:c", "R1"), ("R1", "R2"), ("A1", "tech:t"), ("A1", "comp:c")]
    docs = {"R1": [0], "R2": [0], "A1": [0], "R3": [0], "X": [0]}
    hits = [("R1#0", 9.0), ("X#0", 8.0), ("A1#0", 7.0), ("R3#0", 6.0)]
    s, bm, _ = s7_make(hits, kinds, edges, docs, seed_k=3)
    s.search("q", 20)
    assert s.last_seeds == ["R1", "comp:c", "tech:t", "A1"], s.last_seeds              # R2(이웃 release)는 안 붙는다 · 두 번째 문서의 이웃은 중복이라 건너뜀
    bm._hits = [("R3#0", 6.0)]
    s.search("q", 20)
    assert s.last_seeds == ["R3"]                                                      # 이웃 없는 시드


@case("S7: index가 BM25 색인과 그래프 구축을 함께 한다(한 번) · settings(α·감쇠·max_iter·tol·그래프 통계·구축 초) · 결과 키는 seed_k·α와 무관 · 크기·버전은 BM25 것")
def _():
    calls = []
    kinds = {"R1": ("release", True), "tech:t": "technology"}
    inner = s7_builder(kinds, [("tech:t", "R1")])

    def builder(docs, nodes):
        calls.append(1)
        return inner(docs, nodes)
    from types import SimpleNamespace

    from arena.strategies import s7_graph as mod
    bm = FakeBM25([("R1#0", 1.0)])
    s = mod.S7Graph(bm, [], SimpleNamespace(version="nodes@test"), seed_k=10, alpha=0.3, graph_builder=builder)
    assert s.id == "S7" and s.version == "v1" and s.settings()["graph"]["build_seconds"] is None
    s.index([{"chunk_id": "R1#0", "doc_id": "R1", "source_type": "repo", "text": "t"}])
    s.search("a", 100)
    s.search("b", 100)
    assert calls == [1] and bm.indexed == 1 and s.n_indexed == 1
    st = s.settings()
    assert st["seed_k"] == 10 and st["teleport_alpha"] == 0.3 and abs(st["nx_damping"] - 0.7) < 1e-12
    assert st["max_iter"] == 100 and st["tol"] == 1e-6 and st["first_stage"]["marker"] == "bm25"
    assert st["graph"]["version"] == "graph@v1" and st["graph"]["nodes_version"] == "nodes@test" and st["graph"]["stats"]["nodes"] == 2
    assert st["graph"]["build_seconds"] >= 0 and "undirected" in st["graph"]["ppr_graph"]
    assert s.index_size_bytes() == 77 and s.server_version() == "9.x" and s.took_ms is bm.took_ms
    s.drop()
    assert bm.dropped
    assert harness.result_key("S7", "v1", "c-all@v1", "dev@v1").startswith("S7.v1__")        # seed_k·α는 키에 안 들어간다 — 칸마다 --results를 따로 쓴다


@case("하네스: run_queries(seeds=)가 전략의 last_seeds를 질의마다 담는다 · last_seeds 없는 전략이면 비워 둔다 · jhs_metrics.seed_recall과 이어진다")
def _():
    from types import SimpleNamespace

    class Fake(harness.Strategy):
        last_seeds = []

        def search(self, query, k):
            self.last_seeds = [f"seed:{query}", "tech:x"]
            return [("a#0", 2.0)]

    class Plain(harness.Strategy):
        def search(self, query, k):
            return [("a#0", 2.0)]
    qs = [{"qid": "q1", "text": "one"}, {"qid": "q2", "text": "two"}]
    seeds = {}
    harness.run_queries(Fake(), qs, {"a#0": "A"}, seeds=seeds)
    assert seeds == {"q1": ["seed:one", "tech:x"], "q2": ["seed:two", "tech:x"]}
    seeds = {}
    harness.run_queries(Plain(), qs, {"a#0": "A"}, seeds=seeds)
    assert seeds == {}
    harness.run_queries(Fake(), qs, {"a#0": "A"})                                       # 기본값 None이면 이전과 같다
    nodes = SimpleNamespace(version="nodes@test", query_node_ids=lambda q: ({"tech:x"}, []))
    sr = jm.seed_recall({"K1": ["doc", "tech:x"]}, [{"qid": "K1", "type": "K"}], nodes)
    assert sr["mean"]["@1"] == 0.0 and sr["mean"]["@5"] == 1.0


@case("CLI s7: --strategy s7 · --s7-seed-k/--s7-alpha는 s7에서만(아니면 오류) · s7이면 둘 다 필수 · 범위 검사 · --nodes 기본은 s7=nodes@v2, 그 밖은 nodes@v1 · make_strategy가 S7Graph를 만든다")
def _():
    import contextlib
    import io
    from types import SimpleNamespace

    from arena import cli
    from arena.strategies import s2_bm25, s7_graph as mod
    assert "s7" in cli.STRATEGY_CHOICES and "s7" not in cli.EMBEDDING_STRATEGIES
    base = ["run", "--snapshots", "x", "--adr", "y", "--queries", "q", "--qrels", "r", "--gold-version", "toy@v0"]
    seen = []
    orig = cli.cmd_run
    cli.cmd_run = lambda a: seen.append(a)

    def parse(*extra):
        seen.clear()
        with contextlib.redirect_stderr(io.StringIO()) as err:
            try:
                cli.main(base + list(extra))
            except SystemExit as e:
                assert e.code == 2, e.code
                return None, err.getvalue()
        return seen[0], ""
    try:
        for extra in (["--strategy", "s2", "--s7-alpha", "0.3"], ["--strategy", "s5a", "--s7-seed-k", "5"]):
            a, msg = parse(*extra)
            assert a is None and "s7에서만" in msg and "조용히 무시하지 않는다" in msg, (extra, msg)
        for extra in (["--strategy", "s7"], ["--strategy", "s7", "--s7-alpha", "0.3"], ["--strategy", "s7", "--s7-seed-k", "5"]):
            a, msg = parse(*extra)
            assert a is None and "함께" in msg, (extra, msg)
        for extra in (["--s7-seed-k", "0", "--s7-alpha", "0.3"], ["--s7-seed-k", "5", "--s7-alpha", "0"], ["--s7-seed-k", "5", "--s7-alpha", "1"]):
            assert parse("--strategy", "s7", *extra)[0] is None, extra
        a, _ = parse("--strategy", "s7", "--s7-seed-k", "10", "--s7-alpha", "0.3")
        assert a.s7_seed_k == 10 and a.s7_alpha == 0.3 and a.nodes == str(cli.NODES_V2_DEFAULT) and a.nodes.endswith("nodes@v2.json")
        assert parse("--strategy", "s2")[0].nodes == str(cli.NODES_DEFAULT) and cli.NODES_DEFAULT.name == "nodes@v1.json"
        assert parse("--strategy", "s5a")[0].nodes == str(cli.NODES_DEFAULT)
        assert parse("--strategy", "s7", "--s7-seed-k", "5", "--s7-alpha", "0.5", "--nodes", "mine.json")[0].nodes == "mine.json"
    finally:
        cli.cmd_run = orig
    args = SimpleNamespace(es_url="http://x", chunk_k=50, s7_seed_k=5, s7_alpha=0.5)
    nodes = SimpleNamespace(version="nodes@test")
    st = cli.make_strategy("s7", args, {"version": "c-all@v1"}, {"version": "p@v1"}, None, docs=[], nodes=nodes)
    assert type(st) is mod.S7Graph and type(st.bm25) is s2_bm25.S2BM25 and st.chunk_k == 50 and st.seed_k == 5
    assert st.nodes is nodes and abs(st.nx_damping - 0.5) < 1e-12 and st.bm25.index_name == s2_bm25.index_name_for("c-all@v1", "s2")


if FAILS:
    print(f"\nFAILED {len(FAILS)} — {FAILS}")
    sys.exit(1)
print("\nOK — 실패 0")
