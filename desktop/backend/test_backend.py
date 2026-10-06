"""데스크톱 백엔드 회귀 — 스텁 모델, 임시 폴더의 픽스처 사본, 네트워크 없음 (FastAPI TestClient).

  <레포 .venv python> desktop/backend/test_backend.py

보는 것: Inbox 목록·상세 · R9(승인 전 쓰기 0) · 승인이 코어 경로로 ADR을 쓴다 · 대기 중 세상이 바뀌면 「바뀐 점」과 stale 답 ·
edit(검사 실패는 쓰지 않음, status 칸 거부) · 거부 · 버림 · 행동 로그 · 내보내기 스위치가 새지 않는다 · 실제 모델 거부.
"""

import glob
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from fastapi.testclient import TestClient  # noqa: E402

import devdata  # noqa: E402
import server  # noqa: E402
import service  # noqa: E402
from service import RadarService, ServiceError, hitl  # noqa: E402

FAILS = []


def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + (f" — {info}" if info and not ok else ""))
    if not ok:
        FAILS.append(name)


def adrs(svc):
    return sorted(os.path.basename(p) for p in glob.glob(os.path.join(svc.adr_dir, "*.md")))


def events(svc):
    return [json.loads(line) for line in open(svc.events_path, encoding="utf-8")] if os.path.exists(svc.events_path) else []


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    tmp = tempfile.mkdtemp(prefix="radar-desktop-test-")
    os.environ["RADAR_EXPORT"] = "git"  # 일부러 켠다 — 백엔드가 물려받지 않아야 한다
    try:
        data = os.path.join(tmp, "data")
        seeded = devdata.seed(data)
        check("시드: 대기 항목 3개", [s["awaiting"] for s in seeded] == [True] * 3, seeded)
        svc = RadarService(data)
        c = TestClient(server.create_app(svc))
        base = adrs(svc)

        # 안전 — 내보내기·실제 모델
        env = svc.env()
        check("RADAR_EXPORT를 물려받지 않음", "RADAR_EXPORT" not in env)
        check("dry-run 강제", env.get("RADAR_DRY_RUN") == "1")
        check("내보내기 no-op", not hitl.exporter_for(svc.ctx()).active)
        try:
            RadarService(data, model="anthropic")
            check("실제 모델 거부", False)
        except ServiceError:
            check("실제 모델 거부", True)

        h = c.get("/health").json()
        check("health", h["ok"] and h["mode"] == "stub", h)
        pre = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"}
        ok = c.options("/inbox/x/approve", headers={"Origin": "http://localhost:5173", **pre})
        check("CORS: 개발 화면 출처 허용", ok.headers.get("access-control-allow-origin") == "http://localhost:5173", dict(ok.headers))
        bad = c.options("/inbox/x/approve", headers={"Origin": "https://evil.example", **pre})
        check("CORS: 바깥 출처 거부", "access-control-allow-origin" not in bad.headers, dict(bad.headers))

        items = c.get("/inbox").json()["items"]
        check("목록 3개, 오래된 순", [i["waiting_days"] for i in items] == [6, 3, 0], items)
        check("목록: 모두 승인 대기", {i["state"] for i in items} == {"awaiting_approval"})
        check("R9: 승인 전 ADR 쓰기 0", adrs(svc) == base and not os.path.isdir(hitl.items_dir(svc.ctx())))
        a, b, d = (i["id"] for i in items)

        det = c.get(f"/inbox/{a}").json()
        check("상세: 미리보기가 proposed", det["markdown"].startswith("---\nstatus: proposed"), det["markdown"][:80])
        check("상세: 바뀐 점 없음", det["changes"] == [])
        check("상세: status 칸 없는 초안", "status" not in det["draft"])
        check("없는 항목 404", c.get("/inbox/nope").status_code == 404)

        # 승인 — 코어 write-ahead 경로로 쓴다
        r = c.post(f"/inbox/{a}/approve", json={}).json()
        check("승인 → written", r["outcome"] == "written" and r["adr"] == "0006", r)
        check("ADR 파일 생김", len(adrs(svc)) == len(base) + 1 and adrs(svc)[-1].startswith("0006-"))
        item = hitl.read_item(svc.ctx(), a)
        check("별도 파일: 쓰기 완료", item and item["write_intent"]["state"] == "done" and "status" not in item)
        left = c.get("/inbox").json()["items"]
        check("승인한 항목은 목록에서 빠짐", a not in {i["id"] for i in left} and len(left) == 2)

        # 대기 중 세상이 바뀌었다 (0006이 생김) → 바뀐 점 + stale
        check("남은 항목: 바뀜 표시", {i["state"] for i in left} == {"stale"}, left)
        det = c.get(f"/inbox/{b}").json()
        check("상세: 바뀐 점에 새 ADR", any("0006" in x for x in det["changes"]), det["changes"])
        r = c.post(f"/inbox/{b}/approve", json={}).json()
        check("승인 → stale (쓰지 않음)", r["outcome"] == "stale" and r["changes"] and len(adrs(svc)) == len(base) + 1, r)
        r = c.post(f"/inbox/{b}/approve", json={"stale_answer": "continue"}).json()
        check("계속 → written 0007", r["outcome"] == "written" and r["adr"] == "0007", r)

        # edit — status 칸 거부, validate 실패는 쓰지 않음, 고친 초안이 쓰인다
        draft = c.get(f"/inbox/{d}").json()["draft"]
        r = c.post(f"/inbox/{d}/edit", json={"draft": {**draft, "status": "accepted"}}).json()
        check("edit: status 칸 거부", r["outcome"] == "edit_errors" and any("status" in e for e in r["errors"]), r)
        bad = {**draft, "evidence": []}
        r = c.post(f"/inbox/{d}/edit", json={"draft": bad}).json()
        check("edit: validate 실패 → 쓰지 않음", r["outcome"] == "edit_errors" and len(adrs(svc)) == len(base) + 2, r)
        good = {**draft, "title": draft["title"] + " (고침)"}
        r = c.post(f"/inbox/{d}/edit", json={"draft": good}).json()
        if r["outcome"] == "stale":
            r = c.post(f"/inbox/{d}/edit", json={"draft": good, "stale_answer": "continue"}).json()
        check("edit → written", r["outcome"] == "written", r)
        body = open(os.path.join(hitl.repo_root(svc.ctx()), r["path"]), encoding="utf-8").read() if r.get("path") else ""
        check("고친 제목이 파일에", "(고침)" in body)
        hist = hitl.read_item(svc.ctx(), d)["history"]
        check("history에 원래·고친 해시", any(h.get("draft_hash_original") and h.get("draft_hash_edited") for h in hist))

        # 거부 — 아무것도 쓰지 않는다
        convs = json.load(open(os.path.join(devdata.FIX, "conversations.json"), encoding="utf-8"))
        dup, rep = svc.capture(convs["V2"], stub="JHS-014")  # 이미 저장한 기술 — 코어가 새 초안 대신 link 후보로 끝낸다 (R7)
        check("같은 기술 다시 → 대기 없음", dup is None and rep.get("link_candidates"), rep.get("link_candidates"))
        e, _ = svc.capture(convs["V8"], stub="JHS-033")
        check("새 대화 → 대기", e is not None)
        n = len(adrs(svc))
        r = c.post(f"/inbox/{e}/reject", json={}).json()
        check("거부 → rejected, 쓰기 0", r["outcome"] == "rejected" and len(adrs(svc)) == n and hitl.read_item(svc.ctx(), e) is None, r)

        # 사람이 ADR repo를 고쳤다 → 버림
        f, _ = svc.capture(convs["V8"], stub="JHS-033")
        p = glob.glob(os.path.join(svc.adr_dir, "0004-*.md"))[0]
        txt = open(p, encoding="utf-8").read()
        open(p, "w", encoding="utf-8", newline="\n").write(txt.replace("# ", "# (사람이 고침) ", 1))
        det = c.get(f"/inbox/{f}").json()
        check("사람의 수정이 바뀐 점에", any("0004" in x for x in det["changes"]), det["changes"])
        r = c.post(f"/inbox/{f}/approve", json={"stale_answer": "discard"}).json()
        check("버림 → discarded, 쓰기 0", r["outcome"] in ("discarded", "stale") and len(adrs(svc)) == n, r)
        if r["outcome"] == "stale":  # 처음엔 묻고, 답을 담아 다시 보낸다
            r = c.post(f"/inbox/{f}/approve", json={"stale_answer": "discard"}).json()
        check("목록 비었음", c.get("/inbox").json()["items"] == [], c.get("/inbox").json())

        # 다시 판정 — 원래 대화로 새 항목
        g, _ = svc.capture(convs["V8"], stub="JHS-033")
        open(p, "w", encoding="utf-8", newline="\n").write(txt)  # 세상을 또 바꾼다
        r = c.post(f"/inbox/{g}/approve", json={"stale_answer": "rejudge"}).json()
        # 같은 대화라 item-id가 같을 수 있다 — 닫힌 thread 이름은 코어(new_item_id)가 다시 쓴다. 대기 시작 시각이 새로 잡혀야 한다
        check("다시 판정 → 새 대기 항목", r["outcome"] == "rejudge" and r.get("new_item") in {i["id"] for i in c.get("/inbox").json()["items"]}, r)
        check("새 항목은 바뀐 점 없음", c.get(f"/inbox/{r['new_item']}").json()["changes"] == [])

        # 행동 로그
        check("이벤트 기록", c.post("/events", json={"event": "item_opened", "item": b}).json() == {"ok": True})
        check("모르는 이벤트 400", c.post("/events", json={"event": "rm -rf"}).status_code == 400)
        ev = events(svc)
        kinds = [x["event"] for x in ev]
        check("로그: 승인·edit·거부·열어 봄", {"approve", "edit", "reject", "item_opened", "item_created"} <= set(kinds), kinds)
        check("로그: 승인 결과가 남음", any(x["event"] == "approve" and x["detail"]["outcome"] == "written" for x in ev))
        check("로그: 화면 이벤트는 source=app", all(x["source"] == "app" for x in ev if x["event"] == "item_opened"))

        scenario_m2(os.path.join(tmp, "data2"))
    finally:
        os.environ.pop("RADAR_EXPORT", None)
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'OK' if not FAILS else 'FAILED'} — 실패 {len(FAILS)}")
    return 1 if FAILS else 0


def scenario_m2(data):
    """마일스톤 2 — 빠른 capture · Decisions(읽기만) · 실행(detect·revisit)."""
    print("\n— 마일스톤 2 —")
    devdata.seed(data)
    svc = RadarService(data)
    c = TestClient(server.create_app(svc))
    convs = json.load(open(os.path.join(devdata.FIX, "conversations.json"), encoding="utf-8"))

    # 스텁 대본 고르기 — 대화에 나온 기술·repo로
    check("대본: Bedrock + finance-verifier → JHS-031", service.pick_stub(convs["V4"]) == ("JHS-031", True), service.pick_stub(convs["V4"]))
    check("대본: Bedrock + pii-guard → JHS-033", service.pick_stub(convs["V8"]) == ("JHS-033", True), service.pick_stub(convs["V8"]))
    check("대본: 모르는 대화 → 기본(표시)", service.pick_stub("오늘 점심 뭐 먹지") == (service.DEFAULT_STUB, False))

    # capture
    base, n_items = adrs(svc), len(c.get("/inbox").json()["items"])
    check("capture: 빈 대화 400", c.post("/capture", json={"conversation": "  "}).status_code == 400)
    r = c.post("/capture", json={"conversation": convs["V8"]}).json()
    check("capture → 승인 대기", r["outcome"] == "awaiting" and r["item"] in {i["id"] for i in c.get("/inbox").json()["items"]}, r)
    check("capture: R9 쓰기 0", adrs(svc) == base and len(c.get("/inbox").json()["items"]) == n_items + 1)
    r = c.post("/capture", json={"conversation": convs["V1"]}).json()
    check("capture: 같은 결정(Jev) → linked 0001", r["outcome"] == "linked" and r["link_candidates"] == ["0001"], r)
    r = c.post("/capture", json={"conversation": "회의 메모: 다음 주에 뭔가 정하자"}).json()
    check("capture: 대본 없음 → 기본 대본 + 표시", r["outcome"] == "awaiting" and "스텁" in r["message"], r)
    pending = r["item"]

    # Decisions — 읽기만
    ds = c.get("/decisions").json()["items"]
    check("decisions: 5개 번호 순", [d["id"] for d in ds] == ["0001", "0002", "0003", "0004", "0005"], [d["id"] for d in ds])
    d1 = next(d for d in ds if d["id"] == "0001")
    check("decisions: 0001 보류·재검토 3조건", d1["status"] == "hold" and sum(d1["revisit_summary"].values()) == 3 and d1["title"], d1)
    check("decisions: 상세 원문", c.get("/decisions/0001").json()["markdown"].startswith("---"))
    check("decisions: 없는 번호 404", c.get("/decisions/9999").status_code == 404)
    check("decisions: status 바꾸는 길 없음 (R8)", all(getattr(c, m)("/decisions/0001", json={"status": "accepted"}).status_code == 405
                                                 for m in ("post", "put", "patch")))
    statuses = {d["id"]: d["status"] for d in ds}

    # revisit — 기록용 칸만 바뀐다 → 대기 항목은 낡지 않는다 (결정 7)
    r = c.post("/runs/revisit", json={}).json()
    check("revisit 실행 ok", r["ok"] and r["flow"] == "revisit", r)
    check("revisit: 0001 c1 충족 알림", any(a["adr"] == "0001" and a["condition"] == "c1" and a["result"] == "met" for a in r["alerts"]), r["alerts"])
    d1 = c.get("/decisions/0001").json()
    check("decisions: 0001 c1 최근 결과 met", next(x for x in d1["revisit"] if x["id"] == "c1")["last"]["result"] == "met")
    check("revisit 뒤 대기 항목은 안 낡음", c.get(f"/inbox/{pending}").json()["changes"] == [], c.get(f"/inbox/{pending}").json()["changes"])

    # detect — 초안을 쓴다 → 대기 항목은 낡는다
    r = c.post("/runs/detect", json={}).json()
    check("detect 실행 ok + 초안", r["ok"] and r["drafts"], r)
    check("detect: 겹침(LEANN) 건너뜀이 요약에", "건너뜀 1건" in r["summary"][0], r["summary"])
    ds2 = {d["id"]: d for d in c.get("/decisions").json()["items"]}
    check("detect 초안은 proposed", all(ds2[x["adr"]]["status"] == "proposed" for x in r["drafts"]))
    check("기존 ADR status 그대로 (R8)", all(ds2[k]["status"] == v for k, v in statuses.items()))
    check("detect 뒤 대기 항목은 바뀜", c.get(f"/inbox/{pending}").json()["changes"] != [])
    check("실행: 모르는 흐름 404", c.post("/runs/rm", json={}).status_code == 404)
    runs = c.get("/runs").json()["items"]
    check("실행 기록: 최신 먼저", [x["flow"] for x in runs] == ["detect", "revisit"], [x["flow"] for x in runs])
    kinds = {x["event"] for x in events(svc)}
    check("로그: capture_submitted·run", {"capture_submitted", "run"} <= kinds, kinds)
    check("화면 이벤트 decision_opened", c.post("/events", json={"event": "decision_opened", "item": "0001"}).status_code == 200)

    # 정기 실행 — 칸 계산과 따라잡기 (가짜 시계)
    import datetime as dt
    import scheduling
    kst = dt.timezone(dt.timedelta(hours=9))
    tue = dt.datetime(2026, 10, 6, 9, 0, tzinfo=kst)  # 화 09:00 — revisit 칸(월 09:00)은 지났고 detect 칸(화 10:00)은 아직
    check("칸: revisit 지난 칸 = 월 09:00", scheduling.last_slot("revisit", tue) == dt.datetime(2026, 10, 5, 9, 0, tzinfo=kst))
    check("칸: detect 지난 칸 = 지난주 화", scheduling.last_slot("detect", tue) == dt.datetime(2026, 9, 29, 10, 0, tzinfo=kst))
    check("칸 경계: 정각은 그 칸", scheduling.last_slot("revisit", dt.datetime(2026, 10, 5, 9, 0, tzinfo=kst)).day == 5)
    old = [{"flow": "revisit", "at": "2026-10-04T12:00:00+09:00"}, {"flow": "detect", "at": "2026-09-30T08:00:00+09:00"}]
    check("차례: 칸 뒤 실행 없으면 due", scheduling.due("revisit", tue, old) and not scheduling.due("detect", tue, old))
    sched_data = os.path.join(os.path.dirname(data), "data3")
    devdata.seed(sched_data)
    s3 = RadarService(sched_data)
    sch = scheduling.Scheduler(s3)
    check("tick: 처음엔 둘 다 따라잡음", sorted(sch.tick(tue)) == ["detect", "revisit"])
    check("tick: 같은 주엔 다시 안 돎", sch.tick(tue + dt.timedelta(hours=2)) == [], s3.runs()[:1])
    check("tick: 실행 기록에 trigger=schedule", all(r["trigger"] == "schedule" for r in s3.runs()))
    info = TestClient(server.create_app(s3)).get("/schedule").json()
    check("GET /schedule: 기본 꺼짐 + 두 흐름", info["enabled"] is False and {f["flow"] for f in info["flows"]} == {"revisit", "detect"}, info)


if __name__ == "__main__":
    sys.exit(main())
