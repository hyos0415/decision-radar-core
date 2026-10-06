"""스텁 모델의 판단 — revisit·capture는 spike/common/stub_policy 그대로, detect를 여기서 더한다.

**스텁 통과는 배관 검증일 뿐이다.** 스텁은 정해진 답을 낸다 — 통과는 그래프·도구·radar.py·채점기가 이어졌다는 뜻이지
모델이 잘 판단한다는 뜻이 아니다. 판단은 실제 모델로만 잰다.

detect — 소식(feed) 단위 판단표 (케이스가 아니라 소식 ID가 열쇠라서 7케이스가 같은 판단을 공유한다):
- triage: 소식 목록(<feed>)의 url마다 script["detect"]["items"][url] — candidate면 technology·dependency·affected, exclude면 reason
- draft:  후보(<candidate>)의 소식 url로 script["detect"]["drafts"][url] — DraftBody 그대로
스텁은 겹침(R14)을 모른다 — LEANN(D06)도 후보로 올린다. 겹침은 그래프의 screen 노드와 radar.py match가 잡아야 한다.
"""

import json
import re

from common.stub_policy import capture_step, parse_condition, revisit_step  # noqa: F401  — 두 런타임이 같은 함수를 쓴다

FEED = re.compile(r"<feed>(.*?)</feed>", re.S)
CAND = re.compile(r"<candidate>(.*?)</candidate>", re.S)


def detect_triage_step(script, prompt):
    feed = json.loads(FEED.search(prompt).group(1))
    items = script["detect"]["items"]
    cands, excluded = [], []
    for it in feed:
        s = items.get(it["url"])
        if s is None:  # 판단표에 없는 소식 — 버린다 (모르는 것을 후보로 올리지 않는다)
            excluded.append({"url": it["url"], "reason": "스텁 판단표에 없음"})
        elif s["verdict"] == "candidate":
            cands.append({"technology": s["technology"], "dependency": s["dependency"], "affected": s["affected"],
                          "source_urls": [it["url"]]})
        else:
            excluded.append({"url": it["url"], "reason": s["reason"]})
    return ("final", {"candidates": cands, "excluded": excluded})


def detect_draft_step(script, prompt):
    data = json.loads(CAND.search(prompt).group(1))
    return ("final", script["detect"]["drafts"][data["news"]["url"]])
