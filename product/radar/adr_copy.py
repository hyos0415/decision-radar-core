"""ADR repo 사본 — 실제 수집 주차로 돌릴 때 원본을 건드리지 않게 (HYO-84 run_detect · HYO-85 데스크톱 데모 모드).

docs/adr와 radar-targets.json(+ 있으면 별도 파일 .radar/items)만 복사한다. 대상 repo 경로(상대 경로)는 원본 위치 기준으로 풀어 절대 경로로 적는다 — 사본 위치에서도 같은 repo를 가리킨다.
git이 아니다: 승인은 로컬 내보내기(`RADAR_EXPORT=local`)가 별도 파일에 적는다.
"""

import json
import os
import shutil


def copy_adr(src, dst):
    """(사본의 docs/adr, 사본의 radar-targets.json)."""
    src = os.path.abspath(src)
    shutil.copytree(os.path.join(src, "docs", "adr"), os.path.join(dst, "docs", "adr"))
    items = os.path.join(src, ".radar", "items")
    if os.path.isdir(items):  # 별도 파일 — 사람이 거부한 소식을 사본의 detect도 알게 (HYO-142, API 그림자가 주 실행과 같은 입력을 본다)
        shutil.copytree(items, os.path.join(dst, ".radar", "items"))
    targets = json.load(open(os.path.join(src, "radar-targets.json"), encoding="utf-8"))
    for t in targets:
        t["path"] = os.path.abspath(os.path.join(src, t["path"]))
    with open(os.path.join(dst, "radar-targets.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(targets, f, ensure_ascii=False, indent=1)
    return os.path.join(dst, "docs", "adr"), os.path.join(dst, "radar-targets.json")
