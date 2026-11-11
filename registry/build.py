"""데이터 등록부 빌드 (HYO-180) — Turtle 원본 → SHACL 검사 → OWL 추론 → registry.json.

  python registry/build.py            # registry.json 다시 만들기 (검사가 실패하면 쓰지 않는다)
  python registry/build.py --check    # 검사 실패 · Turtle과 registry.json이 어긋남 → 종료 코드 1 (CI)

빌드 · 테스트에만 rdflib · pySHACL · owlrl이 필요하다 (registry/requirements.txt). 실행 경로는 registry.json만 읽는다
(registry/lookup.py, 표준 라이브러리만). 설계 docs/design/data-registry.md §3 · §6 · §7.

순서 (§3):
  1. SHACL 검사 — **추론 없이**. 원본 트리플 + ontology.ttl(subClassOf가 sh:class에 쓰인다). rdfs:range 추론을 켜면
     오타 낸 대상 ID가 「CollectionUnit이다」로 추론돼 관계 검사(sh:class)를 통과한다. 그 대상에 이름이 없어 다른 모양에
     우연히 걸릴 뿐이다 (test_typo_target_caught_without_inference_but_slips_with_rdfs)
  2. OWL 추론 (owlrl, OWL RL) — 역관계(collects · hasChannel)를 채운다. 사람은 한쪽만 적는다
  3. JSON — 키 정렬 · 시각 없음. 같은 Turtle이면 바이트까지 같은 JSON (CI가 다시 만들어 비교한다)
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if __package__ in (None, ""):
    sys.path.insert(0, str(HERE.parent))
from registry.lookup import normalize  # noqa: E402

OUT = HERE / "registry.json"
ONTOLOGY = "ontology.ttl"
SHAPES = "shapes.ttl"
DATA = ["sources.ttl"]          # 원본 데이터 — 기술 · component가 생기면 여기에 더한다

BASE = "https://github.com/hyos0415/decision-radar/registry"
NS = BASE + "#"
PREFIXES = {"source": BASE + "/source/", "channel": BASE + "/channel/"}   # ID 접두 (설계 §5-1 — 종류 접두 + 이름)
CLASSES = ["CollectionUnit", "ItemSource", "Channel"]
NORMALIZE_RULE = "strip; lower; [-_.\\s]+ -> -"


class RegistryError(Exception):
    pass


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def load_graphs(src=HERE):
    from rdflib import Graph
    src = Path(src)
    ont = Graph().parse(src / ONTOLOGY, format="turtle")
    shapes = Graph().parse(src / SHAPES, format="turtle")
    data = Graph()
    for f in DATA:
        data.parse(src / f, format="turtle")
    return ont, data, shapes


def validate(data, shapes, ont, inference="none"):
    """SHACL → (통과?, 사람이 읽는 보고). inference는 테스트가 「추론을 켜면 무엇이 빠져나가나」를 볼 때만 바꾼다."""
    import pyshacl
    conforms, _, text = pyshacl.validate(data, shacl_graph=shapes, ont_graph=ont, inference=inference,
                                         abort_on_first=False, allow_warnings=False, meta_shacl=False)
    return bool(conforms), text


def infer(data, ont):
    import owlrl
    from rdflib import Graph
    g = Graph()
    for t in ont:
        g.add(t)
    for t in data:
        g.add(t)
    owlrl.DeductiveClosure(owlrl.OWLRL_Semantics).expand(g)
    return g


def compact(uri):
    s = str(uri)
    for p, base in PREFIXES.items():
        if s.startswith(base) and len(s) > len(base):
            return f"{p}:{s[len(base):]}"
    raise RegistryError(f"등록부 ID 접두가 아니다: {s} (쓸 수 있는 접두: {', '.join(PREFIXES)})")


def extract(data, inferred):
    """이름 · 원본 속성은 원본(data)에서, 역관계는 추론 그래프에서. owlrl은 리터럴을 자료형 붙은 꼴로 한 벌 더 만들 수 있어
    이름은 원본에서만 읽는다."""
    from rdflib import RDF, Namespace, URIRef
    from rdflib.namespace import SKOS
    R = Namespace(NS)
    objects = {}
    for cls in CLASSES:
        for s in set(data.subjects(RDF.type, R[cls])):
            if not isinstance(s, URIRef):
                raise RegistryError(f"{cls} 개체가 IRI가 아니다: {s!r}")
            objects.setdefault(s, set()).add(cls)
    out, names = {}, {c: {} for c in CLASSES}
    for s, types in objects.items():
        oid = compact(s)
        o = {"label": str(data.value(s, SKOS.prefLabel)), "types": [c for c in CLASSES if c in types]}
        alt = sorted({str(x) for x in data.objects(s, SKOS.altLabel)})
        hidden = sorted({str(x) for x in data.objects(s, SKOS.hiddenLabel)})
        if alt:
            o["altLabels"] = alt
        if hidden:
            o["hiddenLabels"] = hidden
        for prop, key in (("collectedBy", "collectedBy"), ("channelOf", "channelOf")):
            v = data.value(s, R[prop])
            if v is not None:
                o[key] = compact(v)
        p = data.value(s, R.channelPrefix)
        if p is not None:
            o["prefix"] = str(p)
        if "CollectionUnit" in types:
            o["items"] = sorted(compact(x) for x in set(inferred.objects(s, R.collects)))
            o["channels"] = sorted(compact(x) for x in set(inferred.objects(s, R.hasChannel)))
        out[oid] = o
        for cls in o["types"]:
            for lab in [o["label"]] + alt + hidden:
                n = normalize(lab)
                if names[cls].get(n, oid) != oid:     # SHACL이 이미 막는다 — 두 규칙이 어긋났을 때를 위한 안전망
                    raise RegistryError(f"{cls} 안에서 정규화 이름 {n!r}이 {names[cls][n]} · {oid}에 겹친다")
                names[cls][n] = oid
    return out, names


def build_doc(src=HERE):
    """원본 폴더 → registry.json 문서(dict). 검사가 실패하면 RegistryError (보고 전문 포함)."""
    src = Path(src)
    ont, data, shapes = load_graphs(src)
    ok, report = validate(data, shapes, ont)
    if not ok:
        raise RegistryError("SHACL 검사 실패 — registry.json을 쓰지 않는다\n" + report)
    objects, names = extract(data, infer(data, ont))
    return {
        "version": 1,
        "generated_by": "registry/build.py (HYO-180) — 손으로 고치지 않는다. *.ttl을 고치고 다시 빌드한다",
        "normalize": NORMALIZE_RULE,
        "sources_sha256": {f: _sha(src / f) for f in sorted([ONTOLOGY, SHAPES] + DATA)},
        "names": names,
        "objects": objects,
    }


def dumps(doc):
    return json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="registry/build.py", description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="쓰지 않고 비교만 — 어긋나면 1")
    ap.add_argument("--src", default=str(HERE), help="Turtle 원본 폴더 (테스트용)")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)
    try:
        text = dumps(build_doc(a.src))
    except RegistryError as e:
        print(e, file=sys.stderr)
        return 1
    out = Path(a.out)
    if a.check:
        cur = out.read_text(encoding="utf-8") if out.exists() else ""
        if cur != text:
            print(f"{out}이 Turtle 원본과 어긋난다 — python registry/build.py 로 다시 만들고 커밋한다", file=sys.stderr)
            return 1
        print(f"등록부 검사 통과 · {out.name} 최신")
        return 0
    out.write_text(text, encoding="utf-8", newline="\n")
    doc = json.loads(text)
    counts = " · ".join(f"{c} {sum(c in o['types'] for o in doc['objects'].values())}" for c in CLASSES)
    print(f"{out} — 대상 {len(doc['objects'])} ({counts})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
