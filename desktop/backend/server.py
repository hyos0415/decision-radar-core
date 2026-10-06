"""Decision Radar 데스크톱 백엔드 — FastAPI (localhost). Electron main이 자식 프로세스로 띄우고 끈다 (desktop-app.md §2).

  <레포 .venv python> desktop/backend/server.py --port P [--data-dir D]

API 모양: desktop/README.md 「백엔드 API」. 엔드포인트는 전부 동기 함수다 — 코어(capture)가 안에서 asyncio.run을 쓰고,
FastAPI가 동기 함수를 스레드 풀에서 돌리므로 이벤트 루프가 겹치지 않는다. 쓰기는 RadarService의 lock 하나로 줄 세운다.
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from fastapi import Body, FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

import devdata  # noqa: E402
import scheduling  # noqa: E402
from service import REPO, RadarService, ServiceError  # noqa: E402

DEFAULT_DATA = os.path.join(REPO, "desktop", ".dev-data")


def create_app(svc: RadarService, scheduler_on: bool = False) -> FastAPI:
    app = FastAPI(title="Decision Radar backend", docs_url=None, redoc_url=None)
    # 화면(개발 서버 localhost:5173 등)과 포트가 달라 교차 출처다. localhost 출처만 허용한다 — 아무 웹 페이지나 승인 API를 부르지 못하게
    app.add_middleware(CORSMiddleware, allow_origin_regex=r"^http://(localhost|127\.0\.0\.1)(:\d+)?$",
                       allow_methods=["GET", "POST"], allow_headers=["content-type"])

    @app.exception_handler(ServiceError)
    def _service_error(_req, e: ServiceError):
        return JSONResponse(status_code=e.status, content={"detail": e.message})

    @app.get("/health")
    def health():
        return {"ok": True, "mode": svc.mode, "data_dir": svc.data_dir, "adr_dir": svc.adr_dir}

    @app.get("/inbox")
    def inbox():
        return {"items": svc.inbox()}

    @app.get("/inbox/{item_id}")
    def detail(item_id: str):
        return svc.detail(item_id)

    @app.post("/inbox/{item_id}/approve")
    def approve(item_id: str, body: dict = Body(default={})):
        return svc.answer(item_id, "approve", stale_answer=body.get("stale_answer"))

    @app.post("/inbox/{item_id}/edit")
    def edit(item_id: str, body: dict = Body(...)):
        return svc.answer(item_id, "edit", stale_answer=body.get("stale_answer"), draft=body.get("draft"))

    @app.post("/inbox/{item_id}/reject")
    def reject(item_id: str, body: dict = Body(default={})):
        return svc.answer(item_id, "reject")

    @app.post("/capture")
    def capture(body: dict = Body(...)):
        return svc.submit_capture(body.get("conversation"))

    @app.get("/decisions")
    def decisions():
        return {"items": svc.decisions()}

    @app.get("/decisions/{adr_id}")
    def decision(adr_id: str):
        return svc.decision(adr_id)

    @app.post("/runs/{flow}")
    def run(flow: str, body: dict = Body(default={})):
        return svc.run(flow)

    @app.get("/runs")
    def runs():
        return {"items": svc.runs()}

    @app.get("/schedule")
    def schedule_info():
        import datetime as dt
        return scheduling.describe(dt.datetime.now().astimezone(), svc.runs(), scheduler_on)

    @app.post("/events")
    def events(body: dict = Body(...)):
        if not isinstance(body.get("event"), str):
            raise HTTPException(400, "event가 필요하다")
        svc.ui_event(body["event"], body.get("item"), body.get("detail") if isinstance(body.get("detail"), dict) else None)
        return {"ok": True}

    return app


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--data-dir", default=os.environ.get("RADAR_APP_DATA") or DEFAULT_DATA)
    a = ap.parse_args(argv)
    if a.host not in ("127.0.0.1", "localhost"):
        sys.exit("localhost에서만 띄운다")
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8")
    if not devdata.is_seeded(a.data_dir):
        print(f"개발 데이터를 만든다: {a.data_dir}", flush=True)
        print(devdata.seed(a.data_dir), flush=True)
    elif devdata.ensure_snapshots(a.data_dir):
        print("개발 데이터에 스냅숏을 더했다", flush=True)
    svc = RadarService(a.data_dir)
    scheduler_on = os.environ.get("RADAR_SCHEDULE") == "on"  # 기본 꺼짐 — scheduling.py 머리말
    if scheduler_on:
        scheduling.Scheduler(svc).start()
        print("정기 실행 켜짐 (revisit 월 09:00 · detect 화 10:00, 지난 칸은 따라잡는다)", flush=True)
    import uvicorn
    uvicorn.run(create_app(svc, scheduler_on), host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
