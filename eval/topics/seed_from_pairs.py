"""build_pairs 출력(pairs.json) → 판정 페이지용 seed-set.json (항목을 배열로 — 항목 id에 `/`·`.`이 있어 DB 필드 이름으로 쓰지 않는다).

  python eval/topics/seed_from_pairs.py --run <eval/runs/topics-W..>

이미 seed-set.json이 있으면 한국어 설명(ko)은 남기고 짝만 다시 쓴다.
"""
import argparse
import json
import sys
from pathlib import Path


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    a = ap.parse_args(argv)
    run = Path(a.run)
    d = json.loads((run / "pairs.json").read_text(encoding="utf-8"))
    old = {}
    if (run / "seed-set.json").exists():
        old = {it["id"]: it.get("ko") for it in json.loads((run / "seed-set.json").read_text(encoding="utf-8"))["items"]}
    items = [{"id": k, **v, **({"ko": old[k]} if old.get(k) else {})} for k, v in d["items"].items()]
    (run / "seed-set.json").write_text(json.dumps({"meta": d["meta"], "items": items, "pairs": d["pairs"]}, ensure_ascii=False), encoding="utf-8")
    print(f"-> {run / 'seed-set.json'} · 항목 {len(items)} · 짝 {len(d['pairs'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
