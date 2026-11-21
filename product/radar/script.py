"""스텁 대본 — 스파이크의 것(spike/common/stub_script.json) 위에 제품 것(radar/stub_script.json)을 얹는다."""

import copy
import json
import os

from . import _spike

HERE = os.path.dirname(os.path.abspath(__file__))
_SPIKE = os.path.join(_spike.SPIKE_DIR, "common", "stub_script.json")
_SYNTHETIC = os.path.join(HERE, "..", "..", "eval", "synthetic", "stub_script.json")   # 합성 케이스(SYN-*) 대본 · 대조 (HYO-235)


def load():
    s = json.load(open(_SPIKE, encoding="utf-8"))
    x = json.load(open(os.path.join(HERE, "stub_script.json"), encoding="utf-8"))
    s["cases"].update(x["cases"])
    s["capture"].update(x["capture"])
    s["controls"].update(x["controls"])
    s["detect"] = x["detect"]
    s["detect_controls"] = x["detect_controls"]
    if os.path.exists(_SYNTHETIC):
        y = json.load(open(_SYNTHETIC, encoding="utf-8"))
        for key in ("cases", "capture", "controls", "capture_controls", "detect_controls"):
            s.setdefault(key, {}).update(y.get(key) or {})
    return s


SCRIPT = load()


def revisit_script(extra):
    return {**SCRIPT["base"], **extra}


def capture_script(cid, force_propose=False, draft_from=None):
    s = dict(SCRIPT["capture"][cid])
    if draft_from:
        s["draft"] = SCRIPT["capture"][draft_from]["draft"]
    s["force_propose"] = force_propose
    return s


def detect_script(overlay=None):
    """기본 판단표 + 음성 대조의 덮어쓰기(소식 url 단위)."""
    d = copy.deepcopy(SCRIPT["detect"])
    d.setdefault("repairs", {})
    for k in ("items", "drafts", "repairs"):
        d[k].update((overlay or {}).get("detect", {}).get(k, {}))
    return {"detect": d}
