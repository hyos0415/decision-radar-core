"""대상 repo에서 결정론적으로 읽을 수 있는 신호 (importance-policy §4 「등급을 매기는 주체」의 스크립트 검증 재료).

지금 구현한 것: requirements*.txt · pyproject.toml(dependencies · optional-dependencies · dependency-groups · poetry group) ·
Dockerfile `FROM` · compose `image`.
신호 칸: manifest(파일 이름|None) · group("main"|"dev") · pin("pinned"|"range"|"floating") · image(bool) · image_ref.
못 한 것: tiers.py의 NOT_IMPLEMENTED.
"""
import re
import tomllib
from pathlib import Path

DEV_GROUPS = {"dev", "test", "tests", "lint", "docs", "ci", "typing", "style", "format"}
_REQ = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)\s*(\[[^\]]*\])?\s*((?:===|==|~=|!=|>=|<=|>|<)[^;#\s,]*(?:\s*,\s*(?:===|==|~=|!=|>=|<=|>|<)[^;#\s,]*)*)?")
_FROM = re.compile(r"^\s*FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?", re.I)


def norm(name):
    """PEP 503 식 정규화 — 대소문자·`_`·`.` 무시."""
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


def _pin(spec):
    if not spec:
        return "floating"
    if spec.startswith(("==", "===")) and "," not in spec:
        return "pinned"
    return "range"


def parse_requirement(line):
    """requirements 한 줄 → (이름, pin) | None. 주석·옵션·URL 의존성은 건너뛴다."""
    s = line.split(" #")[0].strip()
    if not s or s.startswith(("#", "-", "git+", "http")):
        return None
    m = _REQ.match(s)
    return (m.group(1), _pin(m.group(3))) if m else None


def _add(out, name, manifest, group, pin):
    key = norm(name)
    old = out.get(key)
    if old and old["group"] == "main":    # 한 기술이 main에도 있으면 main이 이긴다 (운영에 들어간다)
        return
    out[key] = {"name": name, "manifest": manifest, "group": group, "pin": pin, "image": False, "image_ref": None}


def read_python_manifests(repo):
    repo = Path(repo)
    out = {}
    for f in sorted(repo.rglob("requirements*.txt")):
        if any(p in {".venv", "venv", "node_modules", ".git"} for p in f.parts):
            continue
        stem = f.stem.lower()
        group = "dev" if any(w in stem for w in ("dev", "test", "lint")) else "main"
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            r = parse_requirement(line)
            if r:
                _add(out, r[0], f.name, group, r[1])
    py = repo / "pyproject.toml"
    if py.exists():
        d = tomllib.loads(py.read_text(encoding="utf-8"))
        proj = d.get("project", {})
        for spec in proj.get("dependencies", []):
            r = parse_requirement(spec)
            if r:
                _add(out, r[0], "pyproject.toml", "main", r[1])
        for g, specs in proj.get("optional-dependencies", {}).items():
            for spec in specs:
                r = parse_requirement(spec)
                if r:
                    _add(out, r[0], "pyproject.toml", "dev" if g.lower() in DEV_GROUPS else "main", r[1])
        for g, specs in d.get("dependency-groups", {}).items():
            for spec in specs:
                r = parse_requirement(spec) if isinstance(spec, str) else None
                if r:
                    _add(out, r[0], "pyproject.toml", "dev" if g.lower() in DEV_GROUPS else "main", r[1])
        poetry = d.get("tool", {}).get("poetry", {})
        for n in poetry.get("dependencies", {}):
            if n.lower() != "python":
                _add(out, n, "pyproject.toml", "main", "range")
        for g, body in poetry.get("group", {}).items():
            for n in body.get("dependencies", {}):
                _add(out, n, "pyproject.toml", "dev" if g.lower() in DEV_GROUPS else "main", "range")
    return out


def _image_pin(ref):
    tag = ref.rsplit(":", 1)[1] if ":" in ref.rsplit("/", 1)[-1] else ""
    return "floating" if tag in ("", "latest") else "pinned"


def read_images(repo):
    """Dockerfile FROM · compose image. 앞선 스테이지 이름(FROM x AS y의 y)과 scratch는 기술이 아니다."""
    repo, out = Path(repo), {}
    for f in sorted(repo.rglob("Dockerfile*")):
        if any(p in {".venv", "node_modules", ".git"} for p in f.parts) or not f.is_file():
            continue
        stages = set()
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _FROM.match(line)
            if not m:
                continue
            ref = m.group(1)
            if m.group(2):
                stages.add(m.group(2).lower())
            if ref.lower() in stages or ref.lower() == "scratch" or "$" in ref:
                continue
            out[ref] = {"name": ref, "manifest": None, "group": None, "pin": _image_pin(ref), "image": True, "image_ref": f"{f.name} FROM"}
    import yaml
    for f in sorted(list(repo.rglob("compose*.y*ml")) + list(repo.rglob("docker-compose*.y*ml"))):
        if any(p in {".venv", "node_modules", ".git"} for p in f.parts):
            continue
        try:
            d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        for svc in (d.get("services") or {}).values():
            ref = (svc or {}).get("image")
            if isinstance(ref, str) and "$" not in ref:
                out[ref] = {"name": ref, "manifest": None, "group": None, "pin": _image_pin(ref), "image": True, "image_ref": f"{f.name} image"}
    return out


def read_repo_signals(repo):
    """repo 폴더 → {정규화 이름: 신호}. 패키지와 이미지를 함께 (이미지는 이름 그대로 소문자 키)."""
    sig = read_python_manifests(repo)
    for ref, s in read_images(repo).items():
        sig[ref.lower()] = s
    return sig
