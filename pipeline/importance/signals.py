"""대상 repo에서 결정론적으로 읽을 수 있는 신호 (importance-policy §4 「등급을 매기는 주체」의 스크립트 검증 재료).

지금 구현한 것: requirements*.txt · pyproject.toml(dependencies · optional-dependencies · dependency-groups · poetry group) ·
package.json(dependencies · devDependencies, HYO-122) · Dockerfile `FROM` · compose `image`.
신호 칸: manifest(파일 이름|None) · group("main"|"dev"|NPM_DEV) · pin("pinned"|"range"|"floating") · image(bool) · image_ref.
NPM_DEV = package.json devDependencies — dev 그룹(S2 → L7)이 아니다. Electron·Vite 앱은 번들되는 런타임(electron·react)을 devDependencies에 둔다 (HYO-122 ⑩).
못 한 것: tiers.py의 NOT_IMPLEMENTED.
우리 버전(read_repo_versions, HYO-148)은 위 신호와 따로 읽는다 — 등급 지문에 넣지 않으려고. 관문의 범위 정리(scope.py)가 쓴다.
"""
import fnmatch
import re
import tomllib
from pathlib import Path

from collector import stack

DEV_GROUPS = {"dev", "test", "tests", "lint", "docs", "ci", "typing", "style", "format"}
NPM_DEV = "npm-dev"
_GROUP_RANK = {"main": 0, NPM_DEV: 1, "dev": 2}    # 한 이름이 여러 곳에 있으면 운영 쪽이 이긴다 (npm-dev는 운영일 수도 있어 dev보다 앞)
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
    if not s or s.startswith(("#", "-", "git+", "http://", "https://")):      # "http"만 보면 httpx가 URL로 오인돼 빠진다
        return None
    m = _REQ.match(s)
    return (m.group(1), _pin(m.group(3))) if m else None


def _add(out, name, manifest, group, pin):
    key = norm(name)
    old = out.get(key)
    if old and (old["group"] == "main" or _GROUP_RANK.get(old["group"], 9) < _GROUP_RANK.get(group, 9)):    # main이 이긴다 (운영에 들어간다)
        return
    out[key] = {"name": name, "manifest": manifest, "group": group, "pin": pin, "image": False, "image_ref": None}


def _files(repo, exclude, match):
    """collector.stack이 고른 manifest 중 이름이 맞는 것 — 수집기와 같은 목록 (git 추적 파일 − exclude)."""
    return [f for f in stack.manifest_files(repo, exclude)[0] if match(f.name)]


def read_python_manifests(repo, exclude=()):
    repo = Path(repo)
    out = {}
    for f in _files(repo, exclude, lambda n: fnmatch.fnmatchcase(n, "requirements*.txt")):
        stem = f.stem.lower()
        group = "dev" if any(w in stem for w in ("dev", "test", "lint")) else "main"
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            r = parse_requirement(line)
            if r:
                _add(out, r[0], f.name, group, r[1])
    for py in _files(repo, exclude, lambda n: n == "pyproject.toml"):
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


_NPM_EXACT = re.compile(r"^=?v?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)*$")


def _npm_pin(spec):
    """package.json 버전 문자열 → pin. `1.2.3`만 고정, `^`·`~`·범위·태그 아닌 것은 range, 비었거나 `*`·`latest`는 떠 있음."""
    s = (spec or "").strip()
    if s in ("", "*", "x", "latest"):
        return "floating"
    return "pinned" if _NPM_EXACT.match(s) else "range"


def read_package_json(repo, exclude=(), out=None):
    """package.json(git 추적 − exclude — 수집기와 같은 목록) → 신호. dependencies → main, devDependencies → NPM_DEV."""
    out = {} if out is None else out
    for e in stack.package_json_entries(repo, exclude):
        _add(out, e["name"], "package.json", "main" if e["section"] == "dependencies" else NPM_DEV, _npm_pin(e.get("spec")))
    return out


def _image_pin(ref):
    tag = ref.rsplit(":", 1)[1] if ":" in ref.rsplit("/", 1)[-1] else ""
    return "floating" if tag in ("", "latest") else "pinned"


def read_images(repo, exclude=()):
    """Dockerfile FROM · compose image. 앞선 스테이지 이름(FROM x AS y의 y)과 scratch는 기술이 아니다."""
    repo, out = Path(repo), {}
    for f in _files(repo, exclude, lambda n: n.startswith("Dockerfile")):
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
    for f in _files(repo, exclude, lambda n: n.startswith(("compose", "docker-compose"))):
        try:
            d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        for svc in (d.get("services") or {}).values():
            ref = (svc or {}).get("image")
            if isinstance(ref, str) and "$" not in ref:
                out[ref] = {"name": ref, "manifest": None, "group": None, "pin": _image_pin(ref), "image": True, "image_ref": f"{f.name} image"}
    return out


def read_repo_signals(repo, exclude=()):
    """repo 폴더 → {정규화 이름: 신호}. 패키지(Python·npm)와 이미지를 함께 (이미지는 이름 그대로 소문자 키)."""
    sig = read_package_json(repo, exclude, read_python_manifests(repo, exclude))
    for ref, s in read_images(repo, exclude).items():
        sig[ref.lower()] = s
    return sig


# ---------------------------------------------------------------------------- 우리 버전 (importance-policy §3·§4 변경 2026-10-27, HYO-148)
# 위 신호와 따로 둔다 — 신호는 등급 파일의 지문(tiers.fingerprint)에 들어가서, 버전을 넣으면 패치를 올릴 때마다 등급을 다시 부른다.
# 관문이 돌 때마다 manifest에서 새로 읽는다 (cli.build_gate → Gate(versions=…)). 사람이 적은 overrides `version`이 이긴다.
_SPEC_CLAUSE = re.compile(r"^\s*(===|==|~=|>=|\^|~|=)?\s*v?(\d+(?:\.\d+)*)(?:\.[x*])*", re.I)


def spec_version(spec):
    """버전 문자열 → 우리 버전(고정 값 또는 하한) | None. `^39.2.6` → 39.2.6 · `>=6,<7` → 6 · `~=1.2` → 1.2 · `1.x` → 1.
    상한만(`<2`)·제외(`!=`)·초과(`>`)·떠 있음(`*`·latest)·경로·git은 None — 모르는 것을 지어내지 않는다."""
    for clause in re.split(r"\|\||,", spec or ""):
        c = clause.strip()
        if not c or c.startswith(("<", "!", ">")) and not c.startswith(">="):
            continue
        m = _SPEC_CLAUSE.match(c)
        if m:
            return m.group(2)
    return None


def requirement_spec(line):
    """requirements 한 줄 → (이름, 버전 문자열) | None. parse_requirement와 같은 줄을 받는다."""
    s = line.split(" #")[0].strip()
    if not s or s.startswith(("#", "-", "git+", "http://", "https://")):
        return None
    m = _REQ.match(s)
    return (m.group(1), m.group(3) or "") if m else None


def image_version(ref):
    """이미지 태그 → 버전 | None. `python:3.12-slim` → 3.12 · `…/elasticsearch:9.5.4` → 9.5.4 · latest·태그 없음 → None."""
    tag = ref.rsplit(":", 1)[1] if ":" in ref.rsplit("/", 1)[-1] else ""
    m = re.match(r"v?(\d+(?:\.\d+)*)", tag)
    return m.group(1) if m else None


def _add_version(out, name, manifest, spec, version, repo):
    if version:
        row = {"name": name, "manifest": manifest, "spec": spec, "version": version, "repo": repo}
        if row not in out.setdefault(norm(name), []):
            out[norm(name)].append(row)


_CI_RUNTIME = re.compile(r"^\s*(?:-\s*)?(python|node)-version:\s*(.*)$")
_CI_ITEM = re.compile(r"^\s*-\s*(.+)$")
_RUNTIME_VER = {"python": re.compile(r"(?<![\w.])(\d+\.\d+(?:\.\d+)?)(?![\w.])"), "node": re.compile(r"(?<![\d.])(\d+(?:\.\d+){0,2})(?![\w])")}   # node는 `v22.11.0` · `24.x`도 적는다


def _ci_runtimes(text):
    """워크플로 글 → [(런타임, 버전)]. `python-version: "3.12"` · `[3.11, "3.12"]` · 아래 줄 목록. `${{ … }}`·`3.x`처럼 줄을 모르는 값은 버린다."""
    out, lines = [], text.splitlines()
    for i, line in enumerate(lines):
        m = _CI_RUNTIME.match(line)
        if not m:
            continue
        vals = [m.group(2).split(" #")[0]]
        if not vals[0].strip():                              # 값이 아래 줄 목록이다
            vals = []
            for nxt in lines[i + 1:]:
                it = _CI_ITEM.match(nxt)
                if not it:
                    break
                vals.append(it.group(1).split(" #")[0])
        for v in vals:
            if "${{" not in v:
                out += [(m.group(1), x) for x in _RUNTIME_VER[m.group(1)].findall(v)]
    return out


def read_runtime_versions(repo, exclude=(), label=None, out=None):
    """repo가 manifest 밖에 적어 둔 언어 런타임 버전을 read_repo_versions와 같은 모양으로 더한다 (HYO-202) — `.python-version` · `.nvmrc` ·
    GitHub Actions의 `python-version` · `node-version`. 추적 파일만 (git이 아니면 있는 파일). 관문의 범위 정리만 쓴다 — 보안 공지 대조 · basis는 그대로."""
    repo, out = Path(repo), {} if out is None else out
    label = label or repo.name
    tracked, _ = stack._git_files(repo)
    ok = lambda rel: (tracked is None or rel in tracked) and not stack._excluded(rel, exclude) and (repo / rel).is_file()   # noqa: E731
    for rel, name in ((".python-version", "python"), (".nvmrc", "node")):
        if ok(rel):
            first = next((s.strip() for s in (repo / rel).read_text(encoding="utf-8", errors="replace").splitlines() if s.strip() and not s.lstrip().startswith("#")), "")
            m = _RUNTIME_VER[name].search(first)
            _add_version(out, name, rel, first, m.group(1) if m else None, label)
    wf = repo / ".github" / "workflows"
    for f in sorted(wf.glob("*.y*ml")) if wf.is_dir() else []:
        rel = f.relative_to(repo).as_posix()
        if ok(rel):
            for name, v in _ci_runtimes(f.read_text(encoding="utf-8", errors="replace")):
                _add_version(out, name, f"{rel} {name}-version", v, v, label)
    return out


def read_repo_versions(repo, exclude=(), label=None, out=None):
    """repo 폴더 → {정규화 이름: [{name, manifest, spec, version, repo}]}. 대상 repo가 여럿이면 out에 이어 쓴다 — 한 기술에 버전이 여럿일 수 있다.
    읽는 곳: requirements*.txt · pyproject(dependencies · optional · groups · poetry · requires-python → python) · package.json ·
    Dockerfile FROM · compose image (태그 → 그 이미지, python 이미지는 python에도)."""
    repo, out = Path(repo), {} if out is None else out
    label = label or repo.name
    for f in _files(repo, exclude, lambda n: fnmatch.fnmatchcase(n, "requirements*.txt")):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            r = requirement_spec(line)
            if r:
                _add_version(out, r[0], f.name, r[1], spec_version(r[1]), label)
    for py in _files(repo, exclude, lambda n: n == "pyproject.toml"):
        d = tomllib.loads(py.read_text(encoding="utf-8"))
        proj = d.get("project", {})
        rp = proj.get("requires-python")
        if isinstance(rp, str):
            _add_version(out, "python", "pyproject.toml requires-python", rp, spec_version(rp), label)
        specs = list(proj.get("dependencies", []))
        for group in list(proj.get("optional-dependencies", {}).values()) + list(d.get("dependency-groups", {}).values()):
            specs += [s for s in group if isinstance(s, str)]
        for s in specs:
            r = requirement_spec(s)
            if r:
                _add_version(out, r[0], "pyproject.toml", r[1], spec_version(r[1]), label)
        poetry = d.get("tool", {}).get("poetry", {})
        deps = [poetry.get("dependencies", {})] + [b.get("dependencies", {}) for b in poetry.get("group", {}).values()]
        for dd in deps:
            for n, v in dd.items():
                v = v.get("version", "") if isinstance(v, dict) else v
                if isinstance(v, str):
                    _add_version(out, n, "pyproject.toml", v, spec_version(v), label)
    for e in stack.package_json_entries(repo, exclude):
        _add_version(out, e["name"], e["file"], e.get("spec") or "", spec_version(e.get("spec")), label)
    for ref, s in read_images(repo, exclude).items():
        v = image_version(ref)
        _add_version(out, ref, s["image_ref"], ref, v, label)
        if ref.rsplit("/", 1)[-1].split(":")[0].lower() == "python":
            _add_version(out, "python", s["image_ref"], ref, v, label)
    return out
