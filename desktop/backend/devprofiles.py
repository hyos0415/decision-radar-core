"""개발 데이터 프로필 — 앱이 어느 스택의 개발 데이터로 도는지 고른다 (`RADAR_DEVDATA=<이름>`, 비우면 default).

default  레포 주인의 스택 — eval 픽스처(ADR 5개 · 대상 repo 2개)와 코어 스텁 대본 (devdata.py가 만든다)
<이름>   `desktop/backend/profiles/<이름>/` 폴더의 파일로 만든다 — 프론트 담당이 자기 repo의 스택으로 앱을 보는 용도.
         폴더 구성: profile.json · stub_script.json · conversations.json · repos/<repo>/ · adr/ · snapshots/
프로필마다 데이터 폴더가 따로다(`desktop/.dev-data-<이름>/`) — 바꿔 띄워도 섞이지 않는다. 폴더에는 만든 프로필 이름을 적어 둔다.
"""

import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
PROFILES_DIR = os.path.join(HERE, "profiles")
DEFAULT = "default"
MARKER = "profile.txt"
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def resolve(name):
    """이름을 정규화하고 있는 프로필인지 본다 (경로로 쓰이므로 글자도 제한한다). 비면 default."""
    name = (name or "").strip().lower() or DEFAULT
    if name == DEFAULT:
        return name
    if not _NAME.match(name) or not os.path.isdir(os.path.join(PROFILES_DIR, name)):
        raise ValueError(f"모르는 개발 데이터 프로필 {name!r} — {available()}")
    return name


def available():
    found = sorted(d for d in os.listdir(PROFILES_DIR) if os.path.isfile(os.path.join(PROFILES_DIR, d, "profile.json"))) \
        if os.path.isdir(PROFILES_DIR) else []
    return [DEFAULT, *found]


def data_dir_for(name):
    return os.path.join(REPO, "desktop", ".dev-data" if name == DEFAULT else f".dev-data-{name}")


def path(name, *parts):
    return os.path.join(PROFILES_DIR, name, *parts)


def read_marker(data_dir):
    """데이터 폴더를 만든 프로필. 표시가 없으면(앞 버전이 만든 폴더) default다."""
    try:
        return open(os.path.join(data_dir, MARKER), encoding="utf-8").read().strip() or DEFAULT
    except OSError:
        return DEFAULT


def write_marker(data_dir, name):
    with open(os.path.join(data_dir, MARKER), "w", encoding="utf-8", newline="\n") as f:
        f.write(name + "\n")


def load_json(name, file):
    return json.load(open(path(name, file), encoding="utf-8"))


def stub_script(name):
    """프로필의 스텁 대본 (capture · detect · revisit). default는 None — 코어 대본을 쓴다."""
    return None if name == DEFAULT else load_json(name, "stub_script.json")
