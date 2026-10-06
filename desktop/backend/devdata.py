"""개발용 데이터 — eval 픽스처(ADR 5개 · 대상 repo 2개)의 사본에 스텁 capture 대기 항목을 만든다.

실제 ADR repo(hyos0415/adr)는 건드리지 않는다. 사본은 `<data>/`(기본 desktop/.dev-data, gitignore)에 있고 git도 아니다.
대기 항목은 스텁 모델로 capture 1턴을 돌려 만든다 — 코어가 실제로 멈춘 thread라서 승인도 코어 경로 그대로 돈다.
"""

import datetime as dt
import json
import os
import shutil

from service import REPO, RadarService

FIX = os.path.join(REPO, "eval", "fixture")
# (스텁 대본 = eval 케이스, 대화 = 픽스처 대화, 며칠 전부터 대기) — 대본은 대화를 읽고 사람이 적은 판단이다 (product/radar/stub_script.json)
SEED = [("JHS-031", "V4", 6), ("JHS-014", "V2", 3), ("JHS-029", "V9", 0)]


def is_seeded(data_dir):
    return os.path.isdir(os.path.join(data_dir, "adr-fixture", "docs", "adr"))


def seed(data_dir):
    os.makedirs(data_dir, exist_ok=True)
    shutil.copytree(os.path.join(FIX, "adr-fixture"), os.path.join(data_dir, "adr-fixture"))
    for r in ("finance-verifier", "medical-mate"):  # radar-targets.json이 ../<repo>로 가리킨다
        shutil.copytree(os.path.join(FIX, "repos", r), os.path.join(data_dir, r))
    svc = RadarService(data_dir)
    convs = json.load(open(os.path.join(FIX, "conversations.json"), encoding="utf-8"))
    out = []
    for stub, conv, days_ago in SEED:
        since = (dt.datetime.now().astimezone() - dt.timedelta(days=days_ago)).isoformat(timespec="seconds")
        item_id, rep = svc.capture(convs[conv], stub=stub, since=since)
        out.append({"stub": stub, "item": item_id, "awaiting": rep.get("awaiting_approval")})
    return out


if __name__ == "__main__":
    import argparse
    import sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=os.path.join(REPO, "desktop", ".dev-data"))
    ap.add_argument("--reset", action="store_true", help="있던 개발 데이터를 지우고 다시 만든다")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if a.reset and os.path.isdir(a.data_dir):
        if not is_seeded(a.data_dir):  # 개발 데이터가 아닌 폴더를 지우지 않는다
            sys.exit(f"개발 데이터 폴더가 아니다 — 지우지 않았다: {a.data_dir}")
        shutil.rmtree(a.data_dir)
    if is_seeded(a.data_dir):
        print(f"이미 있다: {a.data_dir} (--reset으로 다시 만든다)")
    else:
        print(json.dumps(seed(a.data_dir), ensure_ascii=False, indent=1))
