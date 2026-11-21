"""개발용 데이터 — eval 픽스처(ADR 5개 · 대상 repo 2개)의 사본에 스텁 capture 대기 항목을 만든다.

실제 ADR repo(hyos0415/adr)는 건드리지 않는다. 사본은 `<data>/`(기본 desktop/.dev-data, gitignore)에 있고 git도 아니다.
대기 항목은 스텁 모델로 capture 1턴을 돌려 만든다 — 코어가 실제로 멈춘 thread라서 승인도 코어 경로 그대로 돈다.
스냅숏(detect 소식·revisit 출처)은 eval 러너의 픽스처 조립(`run_case.build`)을 빌려 `<data>/snapshots`에 둔다 — 웹에 나가지 않는다.

프로필(`RADAR_DEVDATA=<이름>` · `--profile`): default는 위와 같다. 다른 이름은 `desktop/backend/profiles/<이름>/`의 파일로 같은 모양의 데이터를
`desktop/.dev-data-<이름>/`에 만든다 (devprofiles.py). 프로필 데이터는 전부 예시다 — eval 픽스처는 건드리지 않는다.
"""

import datetime as dt
import json
import os
import shutil
import sys
import tempfile

import devprofiles
from service import REPO, RadarService

FIX = os.path.join(REPO, "eval", "fixture")
# (스텁 대본 = eval 케이스, 대화 = 픽스처 대화, 며칠 전부터 대기) — 대본은 대화를 읽고 사람이 적은 판단이다 (product/radar/stub_script.json)
SEED = [("JHS-031", "V4", 6), ("JHS-014", "V2", 3), ("JHS-029", "V9", 0)]
# 스냅숏: revisit은 Jev v0.9.0 릴리스(S02 — 0001 c1 충족), 나머지는 조용한 기본. detect 소식은 후보·겹침(LEANN)·제외가 섞이게
DEV_CASE = {"id": "DEV", "setup": {"snapshots": {"jev_releases": "S02"}, "feed": ["D01", "D02", "D04", "D05", "D06", "D08"]}}


def is_seeded(data_dir):
    return os.path.isdir(os.path.join(data_dir, "adr-fixture", "docs", "adr"))


def ensure_snapshots(data_dir, profile=None):
    """없으면 만든다 (앞 버전에서 만든 개발 데이터에도 붙는다). 만들었으면 True."""
    dst = os.path.join(data_dir, "snapshots")
    if os.path.isdir(dst):
        return False
    profile = profile or devprofiles.read_marker(data_dir)
    if profile != devprofiles.DEFAULT:  # 프로필의 소식·출처 사본 — 웹에 나가지 않는다
        shutil.copytree(devprofiles.path(profile, "snapshots"), dst)
        return True
    sys.path.insert(0, os.path.join(REPO, "eval", "runner"))
    import run_case
    work = tempfile.mkdtemp(prefix="radar-dev-snap-")
    try:
        run_case.build(DEV_CASE, work)
        shutil.copytree(os.path.join(work, "snapshots"), dst)
        for r in ("finance-verifier", "medical-mate"):  # repo 출처(jev_eval·corpus 슬롯)는 대상 repo 안에 파일로 놓인다
            shutil.copytree(os.path.join(work, r), os.path.join(data_dir, r), dirs_exist_ok=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return True


TOPICS_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "topics_example.jsonl")
TOPICS_FIXTURE_WEEK = "2026-W42"


def ensure_topics(data_dir):
    """화제 탭용 예시 — `<data>/topics`에 주차 파일이 없으면 합성 항목(topics_example.jsonl, 전부 [예시])으로 만든다. 만들었으면 True.
    비공개 스냅숏 repo 없이도 스텁 모드에서 화제 탭이 보이게 하려는 것이다. 모양은 `python -m pipeline.topics run`이 쓰는 것과 같다."""
    dst = os.path.join(data_dir, "topics")
    if os.path.isdir(dst) and any(n.endswith(".json") for n in os.listdir(dst)):
        return False
    from pipeline import topics as pt
    items = pt.read_items(TOPICS_FIXTURE)
    res = pt.build_topics(items, week=TOPICS_FIXTURE_WEEK, k=pt.K_DEFAULT)
    os.makedirs(dst, exist_ok=True)
    with open(os.path.join(dst, f"{TOPICS_FIXTURE_WEEK}.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return True


LIST_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "list_example.json")
LIST_FIXTURE_WEEK = "2026-W45"


def ensure_lists(data_dir):
    """이번 주 목록 예시 (HYO-168) — `<data>/lists/<주차>/list.json`이 없으면 합성 목록(list_example.json, 전부 [예시])을 둔다. 만들었으면 True.
    모양은 `python -m pipeline.detect_feed`가 쓰는 list.json과 같다 (test_backend가 대조한다)."""
    root = os.path.join(data_dir, "lists")
    if os.path.isdir(root) and any(os.path.exists(os.path.join(root, n, "list.json")) for n in os.listdir(root)):
        return False
    os.makedirs(os.path.join(root, LIST_FIXTURE_WEEK), exist_ok=True)
    shutil.copyfile(LIST_FIXTURE, os.path.join(root, LIST_FIXTURE_WEEK, "list.json"))
    return True


CHECK_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "check_example.json")


def ensure_check(data_dir):
    """자리 질의 「확인 필요」 예시 (HYO-245) — default 프로필이고 `<data>/lists/<주차>/check.json`이 하나도 없으면 합성 예시(check_example.json,
    전부 [예시], 자리 repo는 픽스처 대상 repo)를 목록 예시 주차에 둔다. 만들었으면 True. 시드(seed)에는 넣지 않는다 — 회귀의 Inbox 건수를 흔들지 않게
    서버가 뜰 때만 더한다. 모양은 `pipeline.detect_feed`의 check.json과 같다 (test_backend가 대조한다)."""
    if devprofiles.read_marker(data_dir) != devprofiles.DEFAULT:
        return False
    root = os.path.join(data_dir, "lists")
    if os.path.isdir(root) and any(os.path.exists(os.path.join(root, n, "check.json")) for n in os.listdir(root)):
        return False
    os.makedirs(os.path.join(root, LIST_FIXTURE_WEEK), exist_ok=True)
    shutil.copyfile(CHECK_FIXTURE, os.path.join(root, LIST_FIXTURE_WEEK, "check.json"))
    return True


EXPERIMENT_EXAMPLE = """---
status: proposed
date: 2026-11-21
technology: pydantic
use: 의존성 릴리스 2.12.3 대응 (security)
affected:
  - {{repo: medical-mate, component: chat-api}}
evidence:
  - {{url: github.example/pydantic/pydantic/releases/tag/v2.12.3, source_type: release_note}}
revisit_when: []
---
# [예시] pydantic 2.12.3 보안 릴리스 대응

> 개발 예시 — 「실험해 보기」(HYO-116) 화면을 스텁에서 보려고 서버가 뜰 때 더한다. 스텁 실험은 대상 repo 사본 · 설치 · 검사를 하지 않는다.

## Context and Problem Statement
[예시] chat-api가 쓰는 pydantic의 보안 릴리스.

## Decision Outcome
제안 — 올려 보고 기존 검사로 확인한다. 채택 여부는 사람이 정한다.

## Research Note
- 재검토 조건 없음: 대응 작업
"""
EXPERIMENT_CHECKS = {"_comment": "개발 예시 (HYO-116) — 스텁은 사본 · 설치 · 검사를 하지 않는다. 레시피 고르기(plan)만 진짜로 돈다",
                     "medical-mate": {"python": "3.12", "components": {"chat-api": {"manifests": ["requirements.txt"],
                                                                                    "run": [{"script": "tests/test_chat.py"}]}}}}


def ensure_experiment(data_dir):
    """「실험해 보기」 예시 (HYO-116) — default 프로필이고 의존성 릴리스 대응 결정이 없으면 예시 결정 하나(다음 번호)와 검사 설정
    (adr-fixture/radar-checks.json)을 더한다. 만들었으면 True. 시드에는 넣지 않는다 — 회귀의 결정 건수(5개)를 흔들지 않게 서버가 뜰 때만."""
    if devprofiles.read_marker(data_dir) != devprofiles.DEFAULT:
        return False
    root = os.path.join(data_dir, "adr-fixture")
    adr_dir = os.path.join(root, "docs", "adr")
    names = sorted(n for n in os.listdir(adr_dir) if n.endswith(".md")) if os.path.isdir(adr_dir) else []
    if not names or any("의존성 릴리스" in open(os.path.join(adr_dir, n), encoding="utf-8").read() for n in names):
        return False
    nxt = max(int(n[:4]) for n in names if n[:4].isdigit()) + 1
    with open(os.path.join(adr_dir, f"{nxt:04d}-pydantic-example.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(EXPERIMENT_EXAMPLE.format())
    checks = os.path.join(root, "radar-checks.json")
    if not os.path.exists(checks):
        with open(checks, "w", encoding="utf-8", newline="\n") as f:
            json.dump(EXPERIMENT_CHECKS, f, ensure_ascii=False, indent=1)
    return True


def seed_profile(data_dir, profile):
    """프로필 폴더 → 데이터 폴더. ADR 사본(adr-fixture) · 대상 repo 사본 · 스냅숏 · 스텁 capture 대기 항목."""
    meta = devprofiles.load_json(profile, "profile.json")
    os.makedirs(data_dir, exist_ok=True)
    shutil.copytree(devprofiles.path(profile, "adr"), os.path.join(data_dir, "adr-fixture"))  # radar-targets.json이 ../<repo>로 가리킨다
    for r in meta["repos"]:
        shutil.copytree(devprofiles.path(profile, "repos", r), os.path.join(data_dir, r))
    devprofiles.write_marker(data_dir, profile)  # 서비스가 어느 프로필 대본을 쓸지 — 폴더가 기억한다
    ensure_snapshots(data_dir, profile)
    ensure_topics(data_dir)
    ensure_lists(data_dir)
    svc = RadarService(data_dir)
    convs = devprofiles.load_json(profile, "conversations.json")
    out = []
    for stub, conv, days_ago in meta["seed"]:
        since = (dt.datetime.now().astimezone() - dt.timedelta(days=days_ago)).isoformat(timespec="seconds")
        item_id, rep = svc.capture(convs[conv], stub=stub, since=since)
        out.append({"stub": stub, "item": item_id, "awaiting": rep.get("awaiting_approval")})
    return out


def seed(data_dir, profile=devprofiles.DEFAULT):
    if profile != devprofiles.DEFAULT:
        return seed_profile(data_dir, profile)
    os.makedirs(data_dir, exist_ok=True)
    shutil.copytree(os.path.join(FIX, "adr-fixture"), os.path.join(data_dir, "adr-fixture"))
    for r in ("finance-verifier", "medical-mate"):  # radar-targets.json이 ../<repo>로 가리킨다
        shutil.copytree(os.path.join(FIX, "repos", r), os.path.join(data_dir, r))
    ensure_snapshots(data_dir)
    ensure_topics(data_dir)
    ensure_lists(data_dir)
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
    ap.add_argument("--profile", default=os.environ.get("RADAR_DEVDATA"), help=f"개발 데이터 프로필 {devprofiles.available()} (기본: RADAR_DEVDATA, 없으면 default)")
    ap.add_argument("--data-dir", default=None, help="기본: 프로필의 데이터 폴더 (default → desktop/.dev-data, 그 밖 → desktop/.dev-data-<이름>)")
    ap.add_argument("--reset", action="store_true", help="있던 개발 데이터를 지우고 다시 만든다")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        a.profile = devprofiles.resolve(a.profile)
    except ValueError as e:
        sys.exit(str(e))
    a.data_dir = a.data_dir or devprofiles.data_dir_for(a.profile)
    if a.reset and os.path.isdir(a.data_dir):
        if not is_seeded(a.data_dir):  # 개발 데이터가 아닌 폴더를 지우지 않는다
            sys.exit(f"개발 데이터 폴더가 아니다 — 지우지 않았다: {a.data_dir}")
        shutil.rmtree(a.data_dir)
    if is_seeded(a.data_dir):
        print(f"이미 있다: {a.data_dir} (--reset으로 다시 만든다)")
    else:
        print(json.dumps(seed(a.data_dir, a.profile), ensure_ascii=False, indent=1))
