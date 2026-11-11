"""데이터 등록부(registry/, HYO-180) 테스트 — 빌드 · SHACL 검사 · 조회 · 얼린 주의 이름이 모두 등록돼 있나.

  python registry/tests/test_registry.py      # rdflib · pySHACL · owlrl 필요 (registry/requirements.txt)

규율 3: 새 검사는 잡혀야 할 것이 잡히는지 확인한다 — 공백이 든 이름 겹침(정규식 이스케이프 · 규율 4) · 오타 낸 대상 ID ·
수집 단위 둘 · 빈 수집 단위 · 겹치는 통로 접두. 그리고 설계 §3의 주장(「OWL range 추론을 켜면 오타 낸 대상이 검사를 빠져나간다」)도 확인한다.
얼린 주 재검사는 radar-snapshots가 있을 때만 (env RADAR_SNAPSHOT_REPO 또는 레포 옆 폴더 — CI에는 없어서 건너뛴다).
"""
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from registry import build, lookup  # noqa: E402

SRC = REPO / "registry"
HEAD = """@prefix radar:   <https://github.com/hyos0415/decision-radar/registry#> .
@prefix source:  <https://github.com/hyos0415/decision-radar/registry/source/> .
@prefix channel: <https://github.com/hyos0415/decision-radar/registry/channel/> .
@prefix skos:    <http://www.w3.org/2004/02/skos/core#> .
"""


def with_extra(turtle):
    """원본을 임시 폴더에 복사하고 sources.ttl 끝에 트리플을 덧붙인다 → 그 폴더."""
    d = Path(tempfile.mkdtemp(prefix="registry-test-"))
    for f in [build.ONTOLOGY, build.SHAPES] + build.DATA:
        shutil.copy(SRC / f, d / f)
    with open(d / "sources.ttl", "a", encoding="utf-8", newline="\n") as fh:
        fh.write("\n" + HEAD + turtle + "\n")
    return d


def fails_with(turtle, needle, shacl=True):
    """빌드가 needle을 담은 오류로 실패해야 한다. shacl=True면 **SHACL 층이** 잡았는지도 본다 — build.extract의 파이썬 안전망이
    같은 「겹친다」로 잡아 SHACL 정규식이 망가진 것을 가리는 일이 있었다 (11/11 변이 확인: shapes.ttl에서 \\s를 빼도 통과)."""
    d = with_extra(turtle)
    if shacl:
        ont, data, shapes = build.load_graphs(d)
        ok, report = build.validate(data, shapes, ont)
        assert not ok, f"SHACL이 잡지 못했다: {turtle}"
        assert needle in report, f"SHACL이 다른 이유로 실패했다 — 기대 {needle!r}\n{report}"
    try:
        build.build_doc(d)
    except build.RegistryError as e:
        assert needle in str(e), f"다른 이유로 실패했다 — 기대 {needle!r}\n{e}"
        return str(e)
    raise AssertionError(f"검사가 잡지 못했다: {turtle}")


# ── 빌드 ──

def test_registry_json_is_current():
    """registry.json이 Turtle 원본에서 다시 만든 것과 바이트까지 같다 (CI의 build.py --check와 같은 확인)."""
    assert build.dumps(build.build_doc(SRC)) == (SRC / "registry.json").read_text(encoding="utf-8"), "python registry/build.py 로 다시 만든다"


def test_build_is_deterministic():
    assert build.dumps(build.build_doc(SRC)) == build.dumps(build.build_doc(SRC))


def test_sources_shape():
    reg = lookup.load()
    assert reg.collection_units() == ["github", "hn", "huggingface", "reddit", "security", "vendor"], reg.collection_units()
    assert reg.of_type("ItemSource") == ["source:github", "source:hn", "source:huggingface", "source:nodejs", "source:osv",
                                         "source:python", "source:reddit", "source:vendor"]
    assert len(reg.of_type("Channel")) == 11
    # HYO-179: python · nodejs · osv는 security로 묶인다 (OWL 역관계로 채운 items)
    assert {reg.unit_of_source(s) for s in ("python", "nodejs", "osv")} == {"security"}
    assert reg.objects["source:security"]["items"] == ["source:nodejs", "source:osv", "source:python"]
    assert reg.objects["source:security"]["channels"] == ["channel:runtime-security", "channel:security-advisories"]
    assert reg.unit_of_source("reddit") == "reddit" and reg.unit_of_source("go") is None
    assert reg.objects["source:reddit"]["types"] == ["CollectionUnit", "ItemSource"]
    # 종류 안에서만 찾는다 — vendor는 수집 단위이면서 통로
    assert reg.resolve("CollectionUnit", "vendor") == "source:vendor" and reg.resolve("Channel", "vendor") == "channel:vendor"
    assert reg.resolve("CollectionUnit", "python") is None


def test_channel_kind():
    reg = lookup.load()
    cases = {"r/LocalLLaMA": "channel:subreddit", "vendor:anthropic": "channel:vendor", "task:text-generation": "channel:task",
             "runtime-security:python": "channel:runtime-security", "stories": "channel:stories",
             "security-advisories": "channel:security-advisories", "dependency-releases": "channel:dependency-releases",
             "r/": None, "task": None, "vendor": None, "podcasts": None, "rss:foo": None}
    got = {c: reg.channel_kind(c) for c in cases}
    assert got == cases, got


def test_normalize_python_matches_sparql():
    """lookup.normalize(파이썬)와 shapes.ttl의 SPARQL REPLACE가 모든 등록 이름 + 어려운 예에서 같은 값을 낸다."""
    from rdflib import Graph, Literal
    names = [o["label"] for o in lookup.load().objects.values()] + [
        "Amazon Bedrock", "llama.cpp", "Node.js", "huggingface_hub", "A__B..C", "x - y", "GPT-6 Luna", "jhs-arena/elasticsearch-nori:9.5.4"]
    shapes = (SRC / "shapes.ttl").read_text(encoding="utf-8")
    assert shapes.count('"[-_.\\\\\\\\s]+"') == 2, "shapes.ttl의 정규식 문자열이 바뀌었다 — 이 테스트의 SPARQL도 맞춘다"
    g = Graph()
    for n in names:
        q = 'SELECT ?n WHERE { BIND (REPLACE(LCASE(STR(?l)), "[-_.\\\\s]+", "-") AS ?n) }'
        got = str(next(iter(g.query(q, initBindings={"l": Literal(n)})))[0])
        assert got == lookup.normalize(n), (n, got, lookup.normalize(n))


# ── SHACL이 잡아야 하는 것 (규율 3) ──

def test_collision_with_space_is_caught():
    """공백이 든 이름 — 정규식 \\s가 Turtle · SPARQL 이스케이프를 거쳐 살아 있는지 (규율 4)."""
    fails_with('source:hn skos:altLabel "Hacker News" .\n'
               'source:hackernews a radar:ItemSource ; skos:prefLabel "hacker-news" ; radar:collectedBy source:hn .', "겹친다")


def test_collision_dot_underscore_is_caught():
    fails_with('source:node_js a radar:ItemSource ; skos:prefLabel "Node.JS" ; radar:collectedBy source:security .\n'
               'source:nodejs skos:altLabel "node_js" .', "겹친다")


def test_same_name_across_kinds_is_fine():
    """vendor(수집 단위) · vendor(통로)처럼 종류가 다르면 겹침이 아니다 — 지금 원본이 그렇다."""
    doc = build.build_doc(SRC)
    assert doc["names"]["CollectionUnit"]["vendor"] != doc["names"]["Channel"]["vendor"]


def _results(data, shapes, ont, inference):
    """SHACL 결과 → {(focus, path)} (compact 이름)."""
    import pyshacl
    from rdflib.namespace import SH
    _, rg, _ = pyshacl.validate(data, shacl_graph=shapes, ont_graph=ont, inference=inference)
    short = lambda x: str(x).rsplit("/", 1)[-1].rsplit("#", 1)[-1] if x is not None else None  # noqa: E731
    return {(short(rg.value(r, SH.focusNode)), short(rg.value(r, SH.resultPath))) for r in rg.subjects(SH.focusNode, None)}


def test_typo_target_caught_without_inference_but_slips_with_rdfs():
    """설계 §3: OWL range는 검증이 아니라 추론이다. 오타 낸 수집 단위 ID(securty)를 대상으로 쓰면
    추론 없이 도는 SHACL은 그 관계(sh:class)에서 잡고, rdfs 추론을 켜면 「securty도 CollectionUnit」이 추론돼 sh:class가 통과한다.
    그때 빌드가 실패하는 것은 추론으로 생긴 수집 단위 securty에 prefLabel이 없어 이름 모양에 **우연히** 걸려서다 —
    관계 검사가 잡은 게 아니다 (11/11 실측 — 처음 가정 「통과해 버린다」는 틀렸다)."""
    typo = 'source:go a radar:ItemSource ; skos:prefLabel "go" ; radar:collectedBy source:securty .'
    fails_with(typo, "collectedBy")
    ont, data, shapes = build.load_graphs(with_extra(typo))
    none = _results(data, shapes, ont, "none")
    rdfs = _results(data, shapes, ont, "rdfs")
    assert ("go", "collectedBy") in none, none
    assert ("go", "collectedBy") not in rdfs, rdfs                 # 관계 검사가 추론에 묻힌다
    assert rdfs == {("securty", "prefLabel")}, rdfs                 # 남은 것은 우연한 이름 검사뿐


def test_two_units_caught():
    fails_with("source:python radar:collectedBy source:hn .", "정확히 하나")


def test_missing_unit_caught():
    fails_with('source:go a radar:ItemSource ; skos:prefLabel "go" .', "정확히 하나")


def test_empty_unit_caught():
    fails_with('source:podcasts a radar:CollectionUnit ; skos:prefLabel "podcasts" .', "항목 출처가 하나도 없다")


def test_duplicate_prefix_caught():
    fails_with('channel:subreddit2 a radar:Channel ; skos:prefLabel "subreddit2" ; radar:channelPrefix "r/" ; radar:channelOf source:reddit .',
               "겹친다")


def test_two_preflabels_caught():
    fails_with('source:osv skos:prefLabel "OSV.dev" .', "prefLabel")


def test_unknown_prefix_id_rejected():
    fails_with('<https://example.com/go> a radar:ItemSource ; skos:prefLabel "go" ; radar:collectedBy source:security .', "접두",
               shacl=False)   # SHACL은 IRI 모양을 보지 않는다 — 빌드(compact)가 막는다


# ── 확장성 — 새 출처는 Turtle 한 줄 ──

def test_new_runtime_is_one_line():
    doc = build.build_doc(with_extra('source:go a radar:ItemSource ; skos:prefLabel "go" ; radar:collectedBy source:security .'))
    reg = lookup.Registry(doc)
    assert reg.unit_of_source("go") == "security"
    assert doc["objects"]["source:security"]["items"] == ["source:go", "source:nodejs", "source:osv", "source:python"]


def test_check_detects_drift(tmp=None):
    d = with_extra('source:go a radar:ItemSource ; skos:prefLabel "go" ; radar:collectedBy source:security .')
    out = d / "registry.json"
    shutil.copy(SRC / "registry.json", out)
    assert build.main(["--check", "--src", str(d), "--out", str(out)]) == 1
    assert build.main(["--src", str(d), "--out", str(out)]) == 0
    assert build.main(["--check", "--src", str(d), "--out", str(out)]) == 0


# ── 얼린 주 — 지금까지 나온 이름이 모두 등록돼 있나 ──

def test_frozen_weeks_all_registered():
    snaps = Path(os.environ.get("RADAR_SNAPSHOT_REPO") or REPO.parent / "radar-snapshots")
    weeks = sorted(p for p in snaps.glob("20??-W??") if (p / "items.jsonl").exists())
    if not weeks:
        print("  (radar-snapshots 없음 — 건너뜀)")
        return
    reg = lookup.load()
    missing = {}
    for w in weeks:
        for line in (w / "items.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            it = json.loads(line)
            if reg.unit_of_source(it["source"]) is None:
                missing.setdefault(("source", it["source"]), w.name)
            for c in it.get("channels") or []:
                if reg.channel_kind(c) is None:
                    missing.setdefault(("channel", c), w.name)
    assert not missing, missing
    print(f"  ({len(weeks)}주 — 항목 source · 통로 전부 등록됨)")


def main():
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}\n{traceback.format_exc()}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
