"""Arena 엔진 스모크 — compose로 띄운 ES·Neo4j가 Arena가 기대는 것을 실제로 하는지 확인한다 (HYO-31).

문서로만 확인한 것(doc-pipeline §7)을 Basic 라이선스에서 직접 검증한다:
  되어야 하는 것: nori · BM25 k1/b · dense_vector hnsw/int8_hnsw/bbq_hnsw · kNN · n10s · SHACL 프로시저
  막혀야 하는 것: RRF retriever · bbq_disk (Enterprise)
  기록할 것: dense_vector 기본 index_options (명시하지 않았을 때)

  python infra/smoke.py            # 사람이 읽는 출력
  python infra/smoke.py --json     # 결과 JSON
표준 라이브러리만 쓴다. 테스트 인덱스는 끝에 지운다.
"""
import json
import random
import sys
import urllib.error
import urllib.request

ES = "http://127.0.0.1:9200"
NEO = "http://127.0.0.1:7474/db/neo4j/tx/commit"
PREFIX = "jhs-smoke-"
results = []


def call(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw[:300].decode("utf-8", "replace")}


def check(name, ok, detail, expect="ok"):
    results.append({"check": name, "pass": bool(ok), "expect": expect, "detail": detail})


def es_reason(body):
    err = body.get("error", {})
    if isinstance(err, dict):
        root = (err.get("root_cause") or [{}])[0]
        return f"{err.get('type')}: {root.get('reason') or err.get('reason')}"
    return str(err)[:200]


def es_checks():
    st, info = call("GET", ES)
    ver = info.get("version", {}).get("number")
    check("es.version", ver == "9.5.4", ver)

    st, lic = call("GET", ES + "/_license")
    check("es.license_basic", lic.get("license", {}).get("type") == "basic", lic.get("license", {}).get("type"))

    st, plugins = call("GET", ES + "/_cat/plugins?format=json")
    names = [p.get("component") for p in plugins] if isinstance(plugins, list) else plugins
    check("es.plugin_nori", "analysis-nori" in names, names)

    text = "LEANN이 incremental insert를 지원하기 시작했나?"
    st, an = call("POST", ES + "/_analyze", {"tokenizer": "nori_tokenizer", "text": text})
    toks = [t["token"] for t in an.get("tokens", [])]
    check("es.nori_tokenize", st == 200 and len(toks) > 3, toks)

    # BM25 k1·b + nori 분석기
    idx = PREFIX + "bm25"
    call("DELETE", ES + "/" + idx)
    st, b = call("PUT", ES + "/" + idx, {
        "settings": {"index": {"similarity": {"bm25x": {"type": "BM25", "k1": 1.2, "b": 0.75}}},
                     "analysis": {"analyzer": {"ko": {"type": "custom", "tokenizer": "nori_tokenizer"}}}},
        "mappings": {"properties": {"body": {"type": "text", "analyzer": "ko", "similarity": "bm25x"}}}})
    check("es.bm25_custom_similarity", st == 200, b if st != 200 else "k1=1.2 b=0.75")
    docs = ["벡터 색인 저장 공간을 줄이는 LEANN", "Bedrock Guardrails로 PII 마스킹", "vLLM max_num_seqs 기본값"]
    for i, d in enumerate(docs):
        call("PUT", f"{ES}/{idx}/_doc/{i}?refresh=true", {"body": d})
    st, s = call("POST", f"{ES}/{idx}/_search", {"query": {"match": {"body": "저장 공간 줄이기"}}})
    hits = [h["_id"] for h in s.get("hits", {}).get("hits", [])]
    check("es.bm25_search", hits[:1] == ["0"], hits)

    # dense_vector: 명시한 색인 방식들 + 기본값 기록
    dims = 1024
    random.seed(0)
    vecs = [[random.uniform(-1, 1) for _ in range(dims)] for _ in range(5)]
    for opt in ("hnsw", "int8_hnsw", "bbq_hnsw", "flat", "int8_flat", "bbq_flat"):
        idx = PREFIX + "vec-" + opt.replace("_", "-")
        call("DELETE", ES + "/" + idx)
        st, b = call("PUT", ES + "/" + idx, {
            "settings": {"index.mapping.exclude_source_vectors": False},
            "mappings": {"properties": {"v": {"type": "dense_vector", "dims": dims, "similarity": "cosine",
                                              "index_options": {"type": opt}}}}})
        ok = st == 200
        if ok:
            for i, v in enumerate(vecs):
                call("PUT", f"{ES}/{idx}/_doc/{i}?refresh=true", {"v": v})
            st2, s = call("POST", f"{ES}/{idx}/_search", {"knn": {"field": "v", "query_vector": vecs[2], "k": 1,
                                                                   "num_candidates": 10}})
            top = [h["_id"] for h in s.get("hits", {}).get("hits", [])]
            ok = top == ["2"]
            check(f"es.dense_{opt}", ok, f"top1={top}")
        else:
            check(f"es.dense_{opt}", False, es_reason(b))

    idx = PREFIX + "vec-default"
    call("DELETE", ES + "/" + idx)
    call("PUT", ES + "/" + idx, {"mappings": {"properties": {"v": {"type": "dense_vector", "dims": dims}}}})
    st, m = call("GET", f"{ES}/{idx}/_mapping/field/v?include_defaults=true")
    try:
        mapping = m[idx]["mappings"]["v"]["mapping"]["v"]
        default_opt = mapping.get("index_options", {}).get("type")
        default_sim = mapping.get("similarity")
    except (KeyError, TypeError):
        default_opt, default_sim = None, None
    check("es.dense_default_index_options (기록)", default_opt is not None,
          f"index_options={default_opt} similarity={default_sim} (dims={dims}, Basic)", expect="record")

    # 막혀야 하는 것
    # bbq_disk는 매핑 생성은 통과하고 **문서를 넣을 때** 403이 난다 (2026-10-02 실측). 빈 인덱스 검색은 200 + 빈 결과라
    # bulk 오류를 안 보는 코드는 조용히 빈 색인을 만든다 → 매핑이 아니라 색인 단계에서 확인한다
    idx = PREFIX + "vec-bbq-disk"
    call("DELETE", ES + "/" + idx)
    st, b = call("PUT", ES + "/" + idx, {"mappings": {"properties": {"v": {
        "type": "dense_vector", "dims": dims, "index_options": {"type": "bbq_disk"}}}}})
    if st >= 400:
        check("es.bbq_disk_blocked", True, "매핑 단계에서 막힘: " + es_reason(b), expect="blocked")
    else:
        st, b = call("PUT", f"{ES}/{idx}/_doc/0?refresh=true", {"v": vecs[0]})
        check("es.bbq_disk_blocked", st >= 400,
              ("매핑은 통과, 색인에서 막힘: " + es_reason(b)) if st >= 400 else "문서가 들어감 — 예상과 다름",
              expect="blocked")

    st, b = call("POST", f"{ES}/{PREFIX}bm25/_search", {"retriever": {"rrf": {"retrievers": [
        {"standard": {"query": {"match": {"body": "LEANN"}}}},
        {"standard": {"query": {"match": {"body": "PII"}}}}]}}})
    check("es.rrf_blocked", st >= 400, es_reason(b) if st >= 400 else "실행됨 — 예상과 다름", expect="blocked")

    # 정리 — ES는 기본으로 와일드카드 삭제를 막는다(action.destructive_requires_name) → 이름마다 지운다
    st, idxs = call("GET", f"{ES}/_cat/indices/{PREFIX}*?format=json&h=index")
    for row in idxs if isinstance(idxs, list) else []:
        call("DELETE", f"{ES}/{row['index']}")
    st, left = call("GET", f"{ES}/_cat/indices/{PREFIX}*?format=json&h=index")
    check("es.cleanup", isinstance(left, list) and not left, [r["index"] for r in left] if isinstance(left, list) else left)


def cypher(stmt):
    st, body = call("POST", NEO, {"statements": [{"statement": stmt}]})
    errs = body.get("errors") or []
    rows = [r["row"] for r in (body.get("results") or [{}])[0].get("data", [])] if not errs else []
    return st, rows, errs


def neo_checks():
    st, rows, errs = cypher("CALL dbms.components() YIELD name, versions, edition RETURN name, versions[0], edition")
    check("neo4j.version", st == 200 and not errs and rows and rows[0][1].startswith("5.26"), rows or errs)

    st, rows, errs = cypher("SHOW PROCEDURES YIELD name WHERE name STARTS WITH 'n10s' RETURN count(name)")
    n = rows[0][0] if rows else 0
    check("neo4j.n10s_loaded", n > 0, f"n10s procedures={n}" if not errs else errs)

    st, rows, errs = cypher("SHOW PROCEDURES YIELD name WHERE name STARTS WITH 'n10s.validation.shacl' RETURN collect(name)")
    check("neo4j.n10s_shacl", bool(rows and rows[0][0]), rows[0][0] if rows else errs)

    # n10s 초기화 → 해제 (빈 DB에서만 의미가 있다 — 이미 데이터가 있으면 건너뛴다)
    st, rows, errs = cypher("MATCH (n) RETURN count(n)")
    if rows and rows[0][0] == 0:
        cypher("CREATE CONSTRAINT n10s_unique_uri IF NOT EXISTS FOR (r:Resource) REQUIRE r.uri IS UNIQUE")
        st, rows, errs = cypher("CALL n10s.graphconfig.init()")
        check("neo4j.n10s_graphconfig_init", not errs, "ok" if not errs else errs)
        cypher("MATCH (n:_GraphConfig) DETACH DELETE n")
        cypher("DROP CONSTRAINT n10s_unique_uri IF EXISTS")
    else:
        check("neo4j.n10s_graphconfig_init", True, "DB에 데이터가 있어 건너뜀", expect="skip")


def main():
    for fn in (es_checks, neo_checks):
        try:
            fn()
        except Exception as e:  # 엔진이 안 떠 있으면 여기로 온다
            check(fn.__name__, False, f"{type(e).__name__}: {e}")
    failed = [r for r in results if not r["pass"]]
    if "--json" in sys.argv:
        print(json.dumps({"results": results, "failed": len(failed)}, ensure_ascii=False, indent=1))
    else:
        for r in results:
            mark = "PASS" if r["pass"] else "FAIL"
            print(f"{mark}  {r['check']:<42} {r['detail']}")
        print(f"\n{len(results) - len(failed)}/{len(results)} 통과")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
