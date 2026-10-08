"""Decision Radar 데스크톱 백엔드 — FastAPI (localhost). Electron main이 자식 프로세스로 띄우고 끈다 (desktop-app.md §2).

  <레포 .venv python> desktop/backend/server.py --port P [--data-dir D] [--profile NAME]

개발 데이터 프로필은 `--profile` 또는 `RADAR_DEVDATA` (비우면 default) — 데이터 폴더는 `RADAR_APP_DATA` > `--data-dir` > 프로필의 기본 폴더 순.
데모 모드(HYO-85): `RADAR_APP_DEMO=1` + `RADAR_APP_MODEL=anthropic` + `RADAR_DEMO_SNAPSHOT=<detect 입력 폴더>` [`RADAR_DEMO_ADR=../adr`].
  데이터 폴더 기본은 `desktop/.dev-data-demo` — 개발 데이터(스텁 픽스처)를 만들지 않고 ADR repo 사본을 만든다. 실제 모델(과금)이다.

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
import devprofiles  # noqa: E402
import scheduling  # noqa: E402
from jobs import Jobs  # noqa: E402
from service import REPO, RadarService, ServiceError  # noqa: E402

DEFAULT_DATA = devprofiles.data_dir_for(devprofiles.DEFAULT)
DEMO_DATA = os.path.join(os.path.dirname(HERE), ".dev-data-demo")


def create_app(svc: RadarService, scheduler_on: bool = False, jobs: Jobs = None) -> FastAPI:
    app = FastAPI(title="Decision Radar backend", docs_url=None, redoc_url=None)
    jobs = jobs or Jobs(svc)
    app.state.jobs = jobs
    # 화면(개발 서버 localhost:5173 등)과 포트가 달라 교차 출처다. localhost 출처만 허용한다 — 아무 웹 페이지나 승인 API를 부르지 못하게
    app.add_middleware(CORSMiddleware, allow_origin_regex=r"^http://(localhost|127\.0\.0\.1)(:\d+)?$",
                       allow_methods=["GET", "POST"], allow_headers=["content-type"])

    @app.exception_handler(ServiceError)
    def _service_error(_req, e: ServiceError):
        return JSONResponse(status_code=e.status, content={"detail": e.message})

    @app.get("/health")
    def health():
        return svc.health()

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

    # 작업 + 폴링 (HYO-85) — 접수만 하고 바로 돌려준다. 상태는 GET으로 (jobs.py 머리말)
    @app.post("/jobs/{kind}", status_code=202)
    def submit_job(kind: str, body: dict = Body(default={})):
        return {"job": jobs.submit(kind, body)}

    @app.get("/jobs")
    def list_jobs():
        return {"items": jobs.list()}

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str):
        return jobs.get(job_id)

    @app.get("/schedule")
    def schedule_info():
        import datetime as dt
        return scheduling.describe(dt.datetime.now().astimezone(), svc.runs(), scheduler_on, svc.flows)

    @app.post("/events")
    def events(body: dict = Body(...)):
        if not isinstance(body.get("event"), str):
            raise HTTPException(400, "event가 필요하다")
        svc.ui_event(body["event"], body.get("item"), body.get("detail") if isinstance(body.get("detail"), dict) else None)
        return {"ok": True}

    return app


def resolve_target(profile_arg, data_dir_arg, env=None):
    """(프로필, 데이터 폴더) — 프로필은 인자 > RADAR_DEVDATA, 폴더는 RADAR_APP_DATA(명시) > --data-dir > 프로필의 기본 폴더."""
    env = os.environ if env is None else env
    profile = devprofiles.resolve(profile_arg or env.get("RADAR_DEVDATA"))
    return profile, env.get("RADAR_APP_DATA") or data_dir_arg or devprofiles.data_dir_for(profile)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--profile", default=None)
    a = ap.parse_args(argv)
    if a.host not in ("127.0.0.1", "localhost"):
        sys.exit("localhost에서만 띄운다")
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8")
    if os.environ.get("RADAR_APP_DEMO") == "1":  # 데모 모드 — 개발 데이터를 만들지 않는다. 사본·검사는 RadarService가 한다
        # --data-dir은 보지 않는다 — Electron이 늘 스텁 개발 데이터 폴더를 넘긴다. 데모는 따로(RADAR_APP_DATA > .dev-data-demo)
        data_dir = os.environ.get("RADAR_APP_DATA") or DEMO_DATA
        if devdata.is_seeded(data_dir):
            sys.exit(f"데모 모드: {data_dir}는 스텁 개발 데이터 폴더다 — 섞이지 않게 다른 폴더(RADAR_APP_DATA)를 쓴다")
        try:
            svc = RadarService(data_dir)
        except ServiceError as e:
            sys.exit(e.message)
        h = svc.health()
        print(f"데모 모드 — 실제 모델 {h['model']} (과금) · ADR 사본 {h['demo']['adr_copy']} · detect 입력 {h['demo']['snapshot']}", flush=True)
        if h["demo"]["funnel"]:
            print(f"  {h['demo']['funnel']}", flush=True)
        return serve(svc, a)
    try:
        profile, a.data_dir = resolve_target(a.profile, a.data_dir)
    except ValueError as e:
        sys.exit(str(e))
    if not devdata.is_seeded(a.data_dir):
        print(f"개발 데이터를 만든다: {a.data_dir} (프로필 {profile})", flush=True)
        print(devdata.seed(a.data_dir, profile), flush=True)
    elif devdata.ensure_snapshots(a.data_dir):
        print("개발 데이터에 스냅숏을 더했다", flush=True)
    try:
        svc = RadarService(a.data_dir, profile=profile)  # 폴더를 만든 프로필과 다르면 거부한다
    except ServiceError as e:
        sys.exit(e.message)
    serve(svc, a)


def serve(svc, a):
    scheduler_on = os.environ.get("RADAR_SCHEDULE") == "on"  # 기본 꺼짐 — scheduling.py 머리말
    if scheduler_on:
        scheduling.Scheduler(svc).start()
        print(f"정기 실행 켜짐 ({', '.join(f['slot'] + ' ' + f['flow'] for f in scheduling.describe(scheduling.dt.datetime.now().astimezone(), [], True, svc.flows)['flows'])}"
              ", 지난 칸은 따라잡는다)", flush=True)
    import uvicorn
    uvicorn.run(create_app(svc, scheduler_on), host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
