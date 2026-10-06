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
    finally:
        os.environ.pop("RADAR_EXPORT", None)
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'OK' if not FAILS else 'FAILED'} — 실패 {len(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
