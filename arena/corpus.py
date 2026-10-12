"""코퍼스 로더 (HYO-32) — 문서 단위가 기본 단위다.

모든 문서: {"doc_id", "corpus": "c-doc"|"c-adr"|"c-code", "title", "text", "source_type", "meta": {...}}
코퍼스 설정(버전·커밋·파일 목록)은 JSON 하나(arena/corpus/c-all@v1.json)에 고정하고, 폴더 위치는 호출자가 인자로 준다
(Windows · M1 Mac 둘 다 돈다 — 경로 하드코딩 금지).

문서 id 규칙 (이미 정해짐 — 바꾸지 않는다):
  C-DOC  스냅숏 items.jsonl의 `id` 그대로 (+ 보충 jsonl의 `source-hint:*`). vendor-page는 pages/ 전문을 읽는다
  C-ADR  `adr:NNNN` — ADR repo 특정 커밋의 docs/adr/*.md, frontmatter의 revisit_last_checked 칸만 뺀다
  C-CODE `stack:<repo>/CLAUDE.md#stack`(스택 절만) · `stack:<repo>/<경로>`(manifest)
"""
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from collector.item import html_to_text      # noqa: E402
from collector.snapshot import page_file     # noqa: E402

# id 접두어 → source_type. 순서가 중요하다(긴 접두어 먼저: github-release: 가 github: 보다 앞).
PREFIX_SOURCE_TYPES = [
    ("github-release:", "release_note"),
    ("github:", "repo"),
    ("hf-model:", "model_card"),
    ("hf-paper:", "paper"),
    ("vendor-page:", "official_doc"),
    ("vendor:", "official_doc"),
    ("source-hint:", "official_doc"),
    ("reddit:", "community"),
    ("hn:", "community"),
]
HTML_SOURCES = ("reddit:", "hn:")     # 이 출처의 text는 HTML 조각이다 → 텍스트로 바꾼다 (collector.item.html_to_text)
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def source_type_of(doc_id, corpus):
    if corpus == "c-adr":
        return "adr"
    if corpus == "c-code":
        return "stack"
    for prefix, st in PREFIX_SOURCE_TYPES:
        if doc_id.startswith(prefix):
            return st
    raise ValueError(f"source_type을 정할 수 없는 id 접두어: {doc_id!r}")


def make_doc(doc_id, corpus, title, text, meta):
    return {"doc_id": doc_id, "corpus": corpus, "title": title, "text": text,
            "source_type": source_type_of(doc_id, corpus), "meta": meta}


def load_config(path):
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("version", "chunk_policy", "c-doc", "c-adr", "c-code"):
        if key not in cfg:
            raise ValueError(f"코퍼스 설정에 {key!r}가 없다: {path}")
    return cfg


# --- git ---------------------------------------------------------------------------------------------------------

def git_show(repo, commit, path):
    r = subprocess.run(["git", "-C", str(repo), "show", f"{commit}:{path}"], capture_output=True)
    if r.returncode != 0:
        raise FileNotFoundError(f"git show {commit}:{path} ({repo}) 실패: {r.stderr.decode('utf-8', 'replace').strip()}")
    return r.stdout.decode("utf-8").replace("\r\n", "\n")


def git_ls(repo, commit, directory):
    r = subprocess.run(["git", "-C", str(repo), "ls-tree", "--name-only", commit, directory.rstrip("/") + "/"],
                       capture_output=True)
    if r.returncode != 0:
        raise FileNotFoundError(f"git ls-tree {commit} {directory} ({repo}) 실패: {r.stderr.decode('utf-8', 'replace').strip()}")
    return [ln for ln in r.stdout.decode("utf-8").split("\n") if ln]


# --- C-DOC -------------------------------------------------------------------------------------------------------

def _read_jsonl(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError as e:
                    raise ValueError(f"{path}:{n} JSON 오류: {e}") from None
    return out


def _item_to_doc(item, snapshot_dir):
    doc_id = item["id"]
    text = item.get("text") or ""
    meta = dict(item.get("meta") or {})
    if doc_id.startswith("vendor-page:"):
        # 전문은 pages/에 있다. 매핑 규칙은 collector.snapshot.page_file()을 그대로 쓴다
        rel = page_file(item)
        declared = meta.get("text_file")
        if declared and declared != rel:
            raise ValueError(f"{doc_id}: page_file()={rel} 인데 meta.text_file={declared} — 매핑 규칙이 어긋났다")
        p = Path(snapshot_dir) / rel
        if not p.is_file():
            raise FileNotFoundError(f"{doc_id}: 전문 파일이 없다 {p}")
        text = p.read_text(encoding="utf-8").removesuffix("\n")     # snapshot.write()가 붙인 끝 줄바꿈
    elif doc_id.startswith(HTML_SOURCES):
        text = html_to_text(text, lines=True)
    meta.pop("text_file", None)
    meta["url"] = item.get("url", "")
    for k in ("created_at", "fetched_at", "first_seen"):
        if item.get(k):
            meta[k] = item[k]
    return make_doc(doc_id, "c-doc", item.get("title") or doc_id, text, meta)


def load_c_doc(snapshots_root, week, supplements):
    """snapshots_root/<week>/items.jsonl + 보충 jsonl들. supplements = [jsonl 경로, ...]"""
    snap = Path(snapshots_root) / week
    items_path = snap / "items.jsonl"
    if not items_path.is_file():
        raise FileNotFoundError(f"스냅숏 items.jsonl이 없다: {items_path}")
    docs = [_item_to_doc(it, snap) for it in _read_jsonl(items_path)]
    for sp in supplements:
        for it in _read_jsonl(sp):
            if not it["id"].startswith("source-hint:"):
                raise ValueError(f"보충 파일의 id는 source-hint:* 여야 한다: {it['id']} ({sp})")
            docs.append(_item_to_doc(it, snap))
    return docs


# --- C-ADR -------------------------------------------------------------------------------------------------------

def split_frontmatter(text):
    """(frontmatter 줄 목록, 본문). 맨 앞이 `---`가 아니면 (None, text)."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], "\n".join(lines[i + 1:])
    return None, text


def strip_frontmatter_key(fm_lines, key):
    """최상위 키 하나와 그 값(들여쓴 줄 · `- ` 목록 · 빈 줄)을 뺀다. 줄 단위라 나머지 칸의 모양은 그대로다."""
    out, skipping = [], False
    top_key = re.compile(r"^[A-Za-z_][\w-]*\s*:")
    for ln in fm_lines:
        if top_key.match(ln):
            skipping = ln.split(":", 1)[0].strip() == key
        if not skipping:
            out.append(ln)
    return out


def _fm_meta(fm_lines):
    import yaml
    try:
        d = yaml.safe_load("\n".join(fm_lines)) or {}
    except yaml.YAMLError:
        d = {}
    return {k: d[k] for k in ("status", "date", "technology", "use") if k in d}


def adr_doc(file_name, raw, commit, strip_keys):
    m = re.match(r"^(\d{4})-", file_name)
    if not m:
        raise ValueError(f"ADR 파일 이름이 NNNN-로 시작하지 않는다: {file_name}")
    fm, body = split_frontmatter(raw)
    if fm is None:
        raise ValueError(f"ADR에 frontmatter가 없다: {file_name}")
    for key in strip_keys:
        fm = strip_frontmatter_key(fm, key)
    text = "---\n" + "\n".join(fm) + "\n---\n" + body
    h1 = next((ln[2:].strip() for ln in body.split("\n") if ln.startswith("# ")), None)
    meta = {"file": file_name, "commit": commit, **{k: str(v) for k, v in _fm_meta(fm).items()}}
    return make_doc(f"adr:{m.group(1)}", "c-adr", h1 or Path(file_name).stem, text, meta)


def load_c_adr(adr_repo, commit, strip_keys=("revisit_last_checked",), directory="docs/adr"):
    docs = []
    for path in sorted(git_ls(adr_repo, commit, directory)):
        name = path.rsplit("/", 1)[-1]
        if not name.endswith(".md"):
            continue
        docs.append(adr_doc(name, git_show(adr_repo, commit, path), commit, strip_keys))
    return docs


# --- C-CODE ------------------------------------------------------------------------------------------------------

def extract_stack_section(md):
    """제목에 「스택」이 든 첫 heading부터 같은 수준(이상)의 다음 heading 전까지. 코드 펜스 안의 `#`는 heading이 아니다."""
    lines = md.split("\n")
    in_fence, start, level = False, None, None
    for i, ln in enumerate(lines):
        if _FENCE.match(ln):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING.match(ln)
        if not m:
            continue
        if start is None:
            if "스택" in m.group(2):
                start, level = i, len(m.group(1))
        elif len(m.group(1)) <= level:
            return "\n".join(lines[start:i]).rstrip("\n")
    if start is None:
        raise ValueError("CLAUDE.md에 제목에 「스택」이 든 heading이 없다")
    return "\n".join(lines[start:]).rstrip("\n")


def code_docs(repo_name, commit, files, read):
    """files = 경로 목록, read(path) -> 파일 텍스트. CLAUDE.md는 스택 절만."""
    docs = []
    for path in files:
        raw = read(path)
        meta = {"repo": repo_name, "path": path, "commit": commit}
        if path.rsplit("/", 1)[-1] == "CLAUDE.md":
            docs.append(make_doc(f"stack:{repo_name}/{path}#stack", "c-code", f"{repo_name} {path} — 스택 절",
                                 extract_stack_section(raw), meta))
        else:
            docs.append(make_doc(f"stack:{repo_name}/{path}", "c-code", f"{repo_name}/{path}", raw.rstrip("\n"), meta))
    return docs


def load_c_code(repo_paths, repos_cfg):
    docs = []
    for r in repos_cfg:
        name = r["name"]
        if name not in repo_paths:
            raise ValueError(f"C-CODE repo {name!r}의 경로 인자가 없다 (--repo {name}=<경로>)")
        docs += code_docs(name, r["commit"], r["files"], lambda p, rp=repo_paths[name], c=r["commit"]: git_show(rp, c, p))
    return docs


# --- 전체 --------------------------------------------------------------------------------------------------------

def load_corpus(cfg, snapshots_root, adr_repo, repo_paths, supplement_paths):
    """cfg = load_config(...). repo_paths = {repo 이름: 경로}. supplement_paths = {보충 이름: jsonl 경로}."""
    cd = cfg["c-doc"]
    missing = [s for s in cd.get("supplements", []) if s not in supplement_paths]
    if missing:
        raise ValueError(f"보충 파일 경로 인자가 없다: {missing} (--supplement <이름>=<경로>)")
    docs = load_c_doc(snapshots_root, cd["snapshot_week"], [supplement_paths[s] for s in cd.get("supplements", [])])
    docs += load_c_adr(adr_repo, cfg["c-adr"]["commit"], cfg["c-adr"].get("strip_frontmatter_keys", ["revisit_last_checked"]))
    docs += load_c_code(repo_paths, cfg["c-code"]["repos"])
    ids = [d["doc_id"] for d in docs]
    seen, dup = set(), set()
    for i in ids:
        (dup if i in seen else seen).add(i)
    dup = sorted(dup)
    if dup:
        raise ValueError(f"문서 id가 겹친다: {dup[:5]}")
    return docs
