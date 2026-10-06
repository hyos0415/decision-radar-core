"""정기 실행 — 앱이 떠 있으면 앱이 백엔드에 시킨다 (desktop-app.md §2). 꺼져 있던 동안 지난 칸은 켜질 때 한 번 따라잡는다.

  revisit  매주 월 09:00 (스킬 routine과 같은 요일 — HYO-21)
  detect   매주 화 10:00 (수집기가 화요일에 스냅숏을 만든 뒤 — collector/README.md)

기본은 꺼짐 — `RADAR_SCHEDULE=on`일 때만 돈다. 개발 데이터가 저절로 바뀌면(detect 초안 → 대기 항목이 낡음) 화면을 볼 때 헷갈린다.
앱이 꺼져 있을 때의 정기 실행(D2)은 여기서 다루지 않는다.
"""

import datetime as dt
import threading

SLOTS = {"revisit": (0, 9, 0), "detect": (1, 10, 0)}  # (요일 월=0, 시, 분)
DAY = "월화수목금토일"
CHECK_SECONDS = 300


def last_slot(flow, now):
    """now 이전(같으면 포함)의 가장 최근 칸."""
    wd, h, m = SLOTS[flow]
    cand = now.replace(hour=h, minute=m, second=0, microsecond=0) - dt.timedelta(days=(now.weekday() - wd) % 7)
    return cand if cand <= now else cand - dt.timedelta(days=7)


def due(flow, now, runs):
    """지난 칸 이후로 그 흐름을 돌린 적이 없으면 돌릴 차례다. runs: GET /runs 모양(at = ISO 시각)."""
    since = last_slot(flow, now)
    for r in runs:
        if r.get("flow") != flow:
            continue
        try:
            at = dt.datetime.fromisoformat(r["at"])
        except (KeyError, TypeError, ValueError):
            continue
        if at >= since:
            return False
    return True


def describe(now, runs, enabled):
    flows = []
    for flow, (wd, h, m) in SLOTS.items():
        ls = last_slot(flow, now)
        last = next((r["at"] for r in runs if r.get("flow") == flow), None)
        flows.append({"flow": flow, "slot": f"매주 {DAY[wd]} {h:02d}:{m:02d}", "last_slot": ls.isoformat(timespec="minutes"),
                      "next_slot": (ls + dt.timedelta(days=7)).isoformat(timespec="minutes"), "last_run": last,
                      "due": due(flow, now, runs)})
    return {"enabled": enabled, "flows": flows}


class Scheduler:
    def __init__(self, svc, interval=CHECK_SECONDS):
        self.svc, self.interval = svc, interval
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="radar-schedule", daemon=True)

    def start(self):
        self.thread.start()
        return self

    def tick(self, now=None):
        """할 차례인 흐름을 돌린다 → 돌린 흐름 목록."""
        now = now or dt.datetime.now().astimezone()
        done = []
        for flow in SLOTS:
            if due(flow, now, self.svc.runs()):
                self.svc.run(flow, trigger="schedule")
                done.append(flow)
        return done

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception as e:  # 정기 실행이 죽어도 앱은 산다 — 실행 기록(runs)에 실패가 남고 다음 칸에 다시 돈다
                print(f"[schedule] {type(e).__name__}: {e}", flush=True)
            self.stop_event.wait(self.interval)
