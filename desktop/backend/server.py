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

from fastapi import Body, FastAPI, Query  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

import devdata  # noqa: E402
import devprofiles  # noqa: E402
import scheduling  # noqa: E402
from jobs import Jobs  # noqa: E402
from service import REPO, RadarService, ServiceError  # noqa: E402
import schemas as S  # noqa: E402  — service가 코어 경로를 올린 뒤에

DEFAULT_DATA = devprofiles.data_dir_for(devprofiles.DEFAULT)
DEMO_DATA = os.path.join(os.path.dirname(HERE), ".dev-data-demo")
API_VERSION = "1.0.0"  # 계약 버전 — 화면이 깨질 변경이면 올린다
ERR = {"model": S.ErrorBody, "description": "서비스 오류 — detail 한 줄"}


def create_app(svc: RadarService = None, scheduler_on: bool = False, jobs: Jobs = None) -> FastAPI:
    """svc=None이면 계약(OpenAPI)만 뽑는 앱이다 — export_openapi.py가 쓴다. 엔드포인트는 부를 때만 svc를 본다."""
    app = FastAPI(title="Decision Radar backend", version=API_VERSION, docs_url=None, redoc_url=None,
                  description="데스크톱 앱 백엔드 (localhost). 계약: desktop/backend/schemas.py → desktop/openapi.json → api.gen.ts",
                  responses={400: ERR, 404: ERR, 409: ERR, 413: ERR, 500: ERR})
    jobs = jobs or (Jobs(svc) if svc is not None else None)
    app.state.jobs = jobs
    # 화면(개발 서버 localhost:5173 등)과 포트가 달라 교차 출처다. localhost 출처만 허용한다 — 아무 웹 페이지나 승인 API를 부르지 못하게
    app.add_middleware(CORSMiddleware, allow_origin_regex=r"^http://(localhost|127\.0\.0\.1)(:\d+)?$",
                       allow_methods=["GET", "POST"], allow_headers=["content-type"])

    @app.exception_handler(ServiceError)
    def _service_error(_req, e: ServiceError):
        return JSONResponse(status_code=e.status, content={"detail": e.message})

    @app.get("/health", response_model=S.Health, operation_id="health")
    def health():
        return svc.health()

    @app.get("/inbox", response_model=S.InboxList, operation_id="inbox")
    def inbox():
        return {"items": svc.inbox()}

    @app.get("/inbox/{item_id}", response_model=S.InboxDetail, operation_id="inboxDetail")
    def detail(item_id: str):
        return svc.detail(item_id)

    @app.post("/inbox/{item_id}/approve", response_model=S.ActionResult, operation_id="approve")
    def approve(item_id: str, body: S.AnswerRequest = Body(default_factory=S.AnswerRequest)):
        return svc.answer(item_id, "approve", stale_answer=body.stale_answer)

    @app.post("/inbox/{item_id}/edit", response_model=S.ActionResult, operation_id="edit")
    def edit(item_id: str, body: S.EditRequest):
        return svc.answer(item_id, "edit", stale_answer=body.stale_answer, draft=body.draft)

    @app.post("/inbox/{item_id}/reject", response_model=S.ActionResult, operation_id="reject")
    def reject(item_id: str):
        return svc.answer(item_id, "reject")

    @app.post("/inbox/{item_id}/ack", response_model=S.ActionResult, operation_id="ack")
    def ack(item_id: str):
        """「확인함」 — 접수된 capture(끝난 것)·revisit 충족 알림을 Inbox에서 치운다. ADR은 안 바꾼다 (R8)."""
        return svc.ack(item_id)

    @app.post("/capture", response_model=S.CaptureResult, operation_id="capture")
    def capture(body: S.CaptureRequest):
        return svc.submit_capture(body.conversation, repo=body.repo)

    @app.get("/targets", response_model=S.TargetList, operation_id="targets")
    def targets():
        return {"items": [{"repo": r} for r in svc.target_repos()]}

    @app.get("/decisions", response_model=S.DecisionList, operation_id="decisions")
    def decisions():
        return {"items": svc.decisions()}

    @app.get("/decisions/{adr_id}", response_model=S.DecisionDetail, operation_id="decision")
    def decision(adr_id: str):
        return svc.decision(adr_id)

    @app.post("/runs/{flow}", response_model=S.RunResult, operation_id="run")
    def run(flow: str):
        return svc.run(flow)

    @app.get("/runs", response_model=S.RunList, operation_id="runs")
    def runs():
        return {"items": svc.runs()}

    # 작업 + 폴링 (HYO-85) — 접수만 하고 바로 돌려준다. 상태는 GET으로 (jobs.py 머리말)
    @app.post("/jobs/{kind}", status_code=202, response_model=S.JobEnvelope, operation_id="submitJob")
    def submit_job(kind: str, body: S.JobRequest = Body(default_factory=S.JobRequest)):
        return {"job": jobs.submit(kind, body.model_dump(exclude_none=True))}

    @app.get("/jobs", response_model=S.JobList, operation_id="jobs")
    def list_jobs():
        return {"items": jobs.list()}

    @app.get("/jobs/{job_id}", response_model=S.Job, operation_id="job")
    def get_job(job_id: str):
        return jobs.get(job_id)

    @app.get("/schedule", response_model=S.Schedule, operation_id="schedule")
    def schedule_info():
        import datetime as dt
        return scheduling.describe(dt.datetime.now().astimezone(), svc.runs(), scheduler_on, svc.flows)

    @app.get("/notifications", response_model=S.NotificationList, operation_id="notifications")
    def notifications(limit: int = Query(default=100, ge=1, le=1000, description="최근 몇 통까지")):
        """지난 알림 (HYO-110) — 행동 로그의 notification_shown에 클릭 여부·항목의 지금 상태를 붙인다. 읽기만."""
        return {"items": svc.notifications(limit)}

    @app.post("/events", response_model=S.Ok, operation_id="event")
    def events(body: S.EventRequest):
        svc.ui_event(body.event, body.item, body.detail)
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
