"""API 계약을 파일로 — desktop/openapi.json (HYO-91). 화면은 이 파일에서 TS 타입을 만든다 (`npm run gen:api`).

  <python> desktop/backend/export_openapi.py           # 다시 쓴다
  <python> desktop/backend/export_openapi.py --check   # 커밋된 파일과 다르면 exit 1 (CI 드리프트 검사)

서비스를 띄우지 않는다 — 계약만 뽑는 앱(create_app(None)).
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import server  # noqa: E402

OUT = os.path.join(os.path.dirname(HERE), "openapi.json")


def spec_text():
    return json.dumps(server.create_app(None).openapi(), ensure_ascii=False, indent=2) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    text = spec_text()
    if a.check:
        old = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else ""
        if old != text:
            print("desktop/openapi.json이 schemas.py·server.py와 다르다 — python desktop/backend/export_openapi.py 후 cd desktop && npm run gen:api", file=sys.stderr)
            return 1
        print("openapi.json 최신")
        return 0
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
