"""작업 + 폴링 (HYO-85, 사용자 결정 10/8) — 오래 걸리는 일(capture 1턴 · detect · revisit)을 접수만 하고 바로 돌려준다.

  POST /jobs/{capture|detect|revisit}  → 202 {"job": {...}}   (capture는 {"conversation": "..."})
  GET  /jobs                            → {"items": [...]}     최신 먼저 — 화면의 「상태 창」
  GET  /jobs/{id}                       → {...}

작업 하나: id · kind · state(queued → running → done | error) · created/started/finished · message(한 줄) · result(동기 엔드포인트와 같은 모양) · item.
끝나면 화면이 Inbox를 다시 읽고 OS 알림을 띄운다 (알림은 화면 몫). 실제 모델 실행은 분 단위라 창에서 기다리지 않게 하는 것이 목적이다.

일꾼 스레드는 하나 — 작업은 접수 순서대로 하나씩 돈다 (쓰기는 어차피 RadarService.lock으로 줄 선다).
입력 검사(빈 대화 · 모르는 흐름 · 데모에서 revisit)는 접수할 때 한다 — 잘못된 요청은 작업이 되지 않고 4xx로 돌아온다.
기록은 `<data>/jobs.json`. 백엔드가 다시 뜨면 끝나지 않은 작업은 error(「백엔드가 다시 시작됐다」)로 닫는다 — 다시 돌리지 않는다(과금).
"""

import json
import os
import queue
import threading
import uuid

from service import ServiceError, now_iso

KINDS = ("capture", "detect", "revisit")
JOBS_FILE = "jobs.json"
KEEP = 100


class Jobs:
    def __init__(self, svc, start=True):
        self.svc = svc
        self.path = os.path.join(svc.data_dir, JOBS_FILE)
        self.lock = threading.Lock()
        self.q = queue.Queue()
        self.items = self._load()
        self.thread = threading.Thread(target=self._loop, name="radar-jobs", daemon=True)
        if start:
            self.thread.start()

    def _load(self):
        try:
            items = json.load(open(self.path, encoding="utf-8"))
        except (OSError, ValueError):
            return []
        for j in items:
            if j.get("state") in ("queued", "running"):
                j.update(state="error", finished=now_iso(), error="백엔드가 다시 시작됐다 — 끝나지 않은 작업이다 (다시 돌리지 않았다)")
        return items

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.items[-KEEP:], f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def submit(self, kind, body=None):
        body = body or {}
        if kind not in KINDS:
            raise ServiceError(404, f"모르는 작업 {kind!r} — {list(KINDS)}")
        if kind == "capture":
            self.svc.check_conversation(body.get("conversation"))
            payload = {"conversation": body["conversation"]}
        else:
            self.svc.check_flow(kind)
            payload = {}
        job = {"id": f"job-{uuid.uuid4().hex[:10]}", "kind": kind, "state": "queued", "created": now_iso(), "started": None,
               "finished": None, "message": "대기 중", "result": None, "error": None, "item": None, "mode": self.svc.mode}
        with self.lock:
            self.items.append(job)
            self._save()
        self.svc.log("job_submitted", job["id"], kind=kind)
        self.q.put((job["id"], payload))
        return dict(job)

    def list(self, limit=50):
        with self.lock:
            return [dict(j) for j in reversed(self.items[-limit:])]

    def get(self, job_id):
        with self.lock:
            j = next((j for j in self.items if j["id"] == job_id), None)
        if j is None:
            raise ServiceError(404, f"작업 {job_id}가 없다")
        return dict(j)

    def _update(self, job_id, **kw):
        with self.lock:
            j = next(j for j in self.items if j["id"] == job_id)
            j.update(kw)
            self._save()

    def run_one(self, job_id, payload):
        kind = self.get(job_id)["kind"]
        self._update(job_id, state="running", started=now_iso(), message="실행 중")
        try:
            if kind == "capture":
                res = self.svc.submit_capture(payload["conversation"])
                done = {"result": res, "message": res.get("message"), "item": res.get("item")}
            else:
                res = self.svc.run(kind, trigger="job")
                if not res.get("ok"):
                    raise RuntimeError(res.get("error") or "실행 실패")
                done = {"result": res, "message": " · ".join(res.get("summary") or []) or "끝",
                        "item": None}
            self._update(job_id, state="done", finished=now_iso(), **done)
        except Exception as e:  # noqa: BLE001 — 작업이 죽어도 일꾼은 산다. 화면에 보인다
            msg = e.message if isinstance(e, ServiceError) else f"{type(e).__name__}: {str(e)[:300]}"
            self._update(job_id, state="error", finished=now_iso(), error=msg, message=msg)
        j = self.get(job_id)
        self.svc.log("job_done", job_id, kind=kind, state=j["state"])

    def _loop(self):
        while True:
            job_id, payload = self.q.get()
            try:
                self.run_one(job_id, payload)
            finally:
                self.q.task_done()

    def join(self):
        """테스트용 — 접수된 작업이 다 끝날 때까지 기다린다."""
        self.q.join()
