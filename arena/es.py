"""Elasticsearch를 urllib로 직접 부른다 (infra/smoke.py와 같은 방식 — elasticsearch 클라이언트 라이브러리를 넣지 않는다).

알아 둘 것(infra/README.md): bulk 응답의 `errors`를 반드시 확인한다 · 와일드카드 삭제는 막혀 있다(이름마다 지운다) ·
단일 노드라 replicas 0으로 만든다.
"""
import json
import os
import urllib.error
import urllib.request

DEFAULT_URL = os.environ.get("ARENA_ES_URL", "http://127.0.0.1:9200")


class ESError(RuntimeError):
    pass


def call(base, method, path, body=None, ndjson=False, timeout=120):
    """-> (status, 본문 dict). HTTP 오류도 돌려준다(예외로 만들지 않는다) — 호출자가 status를 본다."""
    if ndjson:
        data = body.encode("utf-8")
        ctype = "application/x-ndjson"
    else:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        ctype = "application/json"
    req = urllib.request.Request(base.rstrip("/") + path, data=data, method=method, headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw[:300].decode("utf-8", "replace")}


def reason(body):
    err = body.get("error", {}) if isinstance(body, dict) else {}
    if isinstance(err, dict):
        root = (err.get("root_cause") or [{}])[0]
        return f"{err.get('type')}: {root.get('reason') or err.get('reason')}"
    return str(err)[:200]


def expect(status, body, what, ok=(200, 201)):
    if status not in ok:
        raise ESError(f"{what} 실패 ({status}): {reason(body)}")
    return body


def bulk_index(base, index, rows, batch=200):
    """rows = [(_id, 문서 dict)]. 응답의 errors를 확인한다 — 안 보면 조용히 빈 색인이 된다(bbq_disk 실측)."""
    for i in range(0, len(rows), batch):
        part = rows[i:i + batch]
        lines = []
        for _id, doc in part:
            lines.append(json.dumps({"index": {"_index": index, "_id": _id}}))
            lines.append(json.dumps(doc, ensure_ascii=False))
        st, body = call(base, "POST", "/_bulk", "\n".join(lines) + "\n", ndjson=True)
        expect(st, body, "bulk")
        if body.get("errors"):
            bad = [it["index"] for it in body["items"] if it["index"].get("error")]
            raise ESError(f"bulk 오류 {len(bad)}건: {bad[0]['_id']} → {bad[0]['error']}")
