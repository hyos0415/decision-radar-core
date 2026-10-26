"""데스크톱 백엔드 회귀 — 스텁 모델, 임시 폴더의 픽스처 사본, 네트워크 없음 (FastAPI TestClient).

  <레포 .venv python> desktop/backend/test_backend.py

보는 것: Inbox 목록·상세 · R9(승인 전 쓰기 0) · 승인이 코어 경로로 ADR을 쓴다 · 대기 중 세상이 바뀌면 「바뀐 점」과 stale 답 ·
edit(검사 실패는 쓰지 않음, status 칸 거부) · 거부 · 버림 · 행동 로그 · 내보내기 스위치가 새지 않는다 · 실제 모델 거부 ·
개발 데이터 프로필(andbread — 선택·폴더 분리·시드·실행, default는 그대로) ·
HYO-85: 로컬 내보내기로 detect 초안 승인·수정·거부 · 「왜 왔나」 · 작업+폴링 · 데모 모드(대역 모델 — 사본에만 쓰기, 원본 무변경, 안전장치).
HYO-110: 지난 알림 목록(클릭 짝 · 열어 봄 · 항목의 지금 상태 · 묶은 알림).
HYO-127: 화제 탭(레인 C) — 주차 목록 · k 자르기 · 저장/저장 빼기 · capture의 topic · 개발 예시.
HYO-99: 실사용 모드 — 임시 git repo + 로컬 bare remote · 대역 모델로 시작 거부 · ctx 둘 · snapshots pull · 승인/거부/기록 커밋 · capture push.
"""

import glob
import hashlib
import dataclasses
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from fastapi.testclient import TestClient  # noqa: E402

import devdata  # noqa: E402
import devprofiles  # noqa: E402
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
        check("RADAR_EXPORT를 물려받지 않음 (git → 로컬)", env.get("RADAR_EXPORT") == "local")
        check("dry-run 강제", env.get("RADAR_DRY_RUN") == "1")
        check("내보내기는 로컬 (git 없음)", type(hitl.exporter_for(svc.ctx())).__name__ == "LocalExporter")
        for kw, name in (({"model": "anthropic"}, "실제 모델 거부 (데모 플래그 없음)"), ({"demo": True}, "데모 플래그만 있으면 거부 (모델 이름 없음)"),
                         ({"model": "gpt-6-luna", "demo": True}, "데모라도 다른 모델 거부")):
            try:
                RadarService(data, **kw)
                check(name, False)
            except ServiceError:
                check(name, True)

        import export_openapi  # 계약(HYO-91) — 커밋된 openapi.json이 지금 모델과 같아야 화면 타입이 맞는다
        check("계약: openapi.json 최신 (export_openapi.py로 다시 뽑는다)", export_openapi.spec_text() == open(export_openapi.OUT, encoding="utf-8").read())
        h = c.get("/health").json()
        check("health", h["ok"] and h["mode"] == "stub", h)
        check("health: 프로필 default", h["profile"] == "default" and not os.path.exists(os.path.join(data, devprofiles.MARKER)), h)
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
        check("승인(capture 2턴) 사용량 + 승인 이벤트에도", r["usage"] and r["usage"]["model_calls"] >= 1 and r["usage"]["estimated"]
              and any(e["event"] == "approve" and e["item"] == a and (e.get("detail") or {}).get("usage") == r["usage"] for e in events(svc)), r.get("usage"))
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
        scenario_profile(os.path.join(tmp, "data-andbread"))
        scenario_hyo85(tmp)
        scenario_hyo92(tmp)
        scenario_hyo95(tmp)
        scenario_hyo110(tmp)
        scenario_hyo127(tmp)
        scenario_hyo132(tmp)
        scenario_real(tmp)
    finally:
        os.environ.pop("RADAR_EXPORT", None)
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'OK' if not FAILS else 'FAILED'} — 실패 {len(FAILS)}")
    return 1 if FAILS else 0


def _git(cwd, *args):
    import subprocess
    p = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "LC_ALL": "C"})
    if p.returncode:
        raise RuntimeError(f"git {' '.join(args)} → {p.returncode}: {p.stderr.strip()[:200]}")
    return p.stdout.strip()


def _clone(remote, dst):
    _git(os.path.dirname(dst), "clone", "-q", remote, dst)
    for k, v in (("user.name", "radar-test"), ("user.email", "radar@example.invalid"), ("commit.gpgsign", "false")):
        _git(dst, "config", k, v)


def scenario_real(tmp):
    """HYO-99 3단계 실사용 모드 — 실제 ADR repo 대신 임시 git repo + 로컬 bare remote, 모델 자리엔 대역(StubChat). GitHub·API에 닿지 않는다.
    시작 거부 · ctx 둘(실행 dry-run / 답 git) · radar-snapshots pull → 가장 새 detect 입력 · 초안은 작업 사본에만 · 승인 = main에 커밋 1개 ·
    거부는 커밋 없음 · revisit 기록 커밋(기록용 칸만, 승인 없이) · capture 승인도 push."""
    print("\n— HYO-99: 실사용 모드 —")
    from radar import script as stub_scripts
    from radar.stub_model import StubChat
    saved = {k: os.environ.get(k) for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")}
    os.environ.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")  # 전역 git 설정(서명·autocrlf)이 흔들지 않게
    try:
        _scenario_real(tmp, stub_scripts, StubChat)
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def _scenario_real(tmp, stub_scripts, StubChat):
    root = os.path.join(tmp, "real")
    os.makedirs(root)
    # ADR repo: 픽스처를 main으로 커밋해 bare remote에 올리고 앱의 작업 사본·「사람」의 clone을 둔다
    seedsrc = os.path.join(root, "seed")
    shutil.copytree(os.path.join(devdata.FIX, "adr-fixture"), seedsrc)
    for r in ("finance-verifier", "medical-mate"):  # radar-targets.json이 ../<repo>로 가리킨다
        shutil.copytree(os.path.join(devdata.FIX, "repos", r), os.path.join(root, r))
    adr_remote = os.path.join(root, "adr.git")
    _git(root, "init", "--bare", "-q", "-b", "main", adr_remote)
    _git(seedsrc, "init", "-q", "-b", "main")
    _git(seedsrc, "add", "-A")
    _git(seedsrc, "-c", "user.name=s", "-c", "user.email=s@x.invalid", "commit", "-q", "-m", "fixture")
    _git(seedsrc, "push", "-q", adr_remote, "main")
    adr = os.path.join(root, "adr")
    _clone(adr_remote, adr)
    base = _git(adr_remote, "rev-parse", "main")
    # radar-snapshots: 수집 PC(windows)가 detect 입력을 push하고, 앱 쪽 작업 사본은 pull만 한다
    snap_remote = os.path.join(root, "snap.git")
    _git(root, "init", "--bare", "-q", "-b", "main", snap_remote)
    win = os.path.join(root, "snap-windows")
    _git(root, "init", "-q", "-b", "main", win)
    for k, v in (("user.name", "w"), ("user.email", "w@x.invalid"), ("commit.gpgsign", "false")):
        _git(win, "config", k, v)
    open(os.path.join(win, "README.md"), "w", encoding="utf-8").write("snap\n")
    _git(win, "add", "-A")
    _git(win, "commit", "-q", "-m", "init")
    _git(win, "push", "-q", snap_remote, "main")
    snaps = os.path.join(root, "radar-snapshots")
    _clone(snap_remote, snaps)
    devdata.ensure_snapshots(os.path.join(root, "devsnap"))
    shutil.copytree(os.path.join(root, "devsnap", "snapshots"), os.path.join(win, "detect", "2026-W44"))
    _git(win, "add", "-A")
    _git(win, "commit", "-q", "-m", "detect 2026-W44")
    _git(win, "push", "-q", snap_remote, "main")

    def fake(flow):
        if flow == "detect":
            return StubChat(script=stub_scripts.detect_script(), flow=flow)
        if flow == "revisit":
            return StubChat(script=stub_scripts.revisit_script(stub_scripts.SCRIPT["cases"]["JHS-001"]), flow=flow)
        return StubChat(script=stub_scripts.capture_script(service.DEFAULT_STUB), flow=flow)

    data = os.path.join(root, "app-data")
    kw = dict(app_mode="real", real_adr=adr, snapshot_repo=snaps, engine="api", chat_factory=fake)

    # 시작 거부 — 실제 ADR repo가 아니면 열지 않는다
    plain = os.path.join(root, "plain")
    shutil.copytree(os.path.join(devdata.FIX, "adr-fixture"), plain)
    side = os.path.join(root, "side")
    _clone(adr_remote, side)
    _git(side, "checkout", "-q", "-b", "feature")
    old_bin = os.environ.get("RADAR_CLAUDE_BIN")
    os.environ["RADAR_CLAUDE_BIN"] = os.path.join(root, "no-such-claude")
    try:
        for over, name, needle in (({"real_adr": plain}, "git이 아닌 폴더", "git 작업 사본"),
                                   ({"real_adr": os.path.join(adr, "docs")}, "repo 맨 위가 아님", "git 작업 사본"),
                                   ({"real_adr": side}, "main이 아닌 브랜치", "main이 아니다"),
                                   ({"real_adr": root, "engine": "api"}, "docs/adr 없음", "docs"),
                                   ({"engine": "claude-cli"}, "claude CLI 없음", "claude CLI"),
                                   ({"engine": "codex"}, "모르는 엔진", "RADAR_APP_ENGINE")):
            try:
                RadarService(data if over.get("real_adr") != root else os.path.join(tmp, "elsewhere"), **{**kw, **over})
                check(f"실사용 시작 거부: {name}", False)
            except ServiceError as e:
                check(f"실사용 시작 거부: {name}", needle in e.message, e.message)
        try:
            RadarService(os.path.join(adr, "data"), **kw)
            check("실사용 시작 거부: 데이터 폴더가 ADR repo 안", False)
        except ServiceError as e:
            check("실사용 시작 거부: 데이터 폴더가 ADR repo 안", "겹친다" in e.message, e.message)
        for extra, name in (({"demo": True}, "데모와 같이"), ({"model": "anthropic"}, "RADAR_APP_MODEL과 같이"), ({"app_mode": "live"}, "모르는 모드")):
            try:
                RadarService(data, **{**kw, **extra})
                check(f"실사용 시작 거부: {name}", False)
            except ServiceError:
                check(f"실사용 시작 거부: {name}", True)
    finally:
        os.environ.pop("RADAR_CLAUDE_BIN") if old_bin is None else os.environ.__setitem__("RADAR_CLAUDE_BIN", old_bin)

    svc = RadarService(data, **kw)
    app = server.create_app(svc)
    c = TestClient(app)
    h = c.get("/health").json()
    check("실사용 health: mode real · 엔진 · ADR repo · 아직 입력 없음(pull 전)", h["mode"] == "real" and h["engine"] == "api" and h["flows"] == ["detect", "revisit"]
          and h["real"]["adr_repo"] == os.path.abspath(adr) and h["real"]["week"] is None and h["adr_dir"].startswith(os.path.abspath(adr)), h)
    check("실사용 ctx 둘: 답은 git(dry-run 아님) · 실행은 로컬(dry-run)", svc.env()["RADAR_EXPORT"] == "git" and svc.env()["RADAR_DRY_RUN"] == "0"
          and svc.env(export=False)["RADAR_EXPORT"] == "local" and svc.env(export=False)["RADAR_DRY_RUN"] == "1")
    check("스텁 모드는 그대로 로컬 · dry-run", all(RadarService(os.path.join(tmp, "data")).env(x)["RADAR_DRY_RUN"] == "1" for x in (None, True, False)))

    # detect — pull로 새 주차를 받고, 초안은 작업 사본에만 (원격 무변경)
    r = c.post("/runs/detect", json={}).json()
    check("실사용 detect: pull한 입력 주차 · 초안", r["ok"] and r["mode"] == "real" and "입력 2026-W44" in r["summary"] and r["drafts"], r)
    check("실사용 detect: 원격은 그대로 (승인 전 push 0)", _git(adr_remote, "rev-parse", "main") == base)
    untracked = _git(adr, "status", "--porcelain")
    check("실사용 detect: 초안은 작업 사본에 미추적으로", all(line.startswith("??") for line in untracked.splitlines()) and "docs/adr/" in untracked, untracked)
    rows = [i for i in c.get("/inbox").json()["items"] if i["kind"] == "detect"]
    check("실사용 Inbox: detect 항목 · 답 가능", len(rows) >= 2 and all(i["actions"] == ["approve", "edit", "reject"] for i in rows), rows)
    a = c.post(f"/inbox/{rows[0]['id']}/approve", json={}).json()
    files = _git(adr_remote, "diff-tree", "--no-commit-id", "--name-status", "-r", "main")
    check("실사용 승인 → main에 커밋 1개 (새 ADR + 항목 파일, A만)", a["outcome"] == "written" and _git(adr_remote, "rev-list", "--count", f"{base}..main") == "1"
          and f"docs/adr/" in files and ".radar/items/" in files and all(line.startswith("A") for line in files.splitlines()), (a, files))
    head = _git(adr_remote, "rev-parse", "main")
    rj = c.post(f"/inbox/{rows[1]['id']}/reject", json={}).json()
    files = _git(adr_remote, "diff-tree", "--no-commit-id", "--name-status", "-r", "main")
    check("실사용 거부 → 초안 지움 · 닫힌 항목 기록만 커밋 (ADR 파일 없음)", rj["outcome"] == "rejected" and _git(adr_remote, "rev-parse", "main~1") == head
          and files.startswith("A\t.radar/items/") and len(files.splitlines()) == 1 and not glob.glob(os.path.join(svc.adr_dir, rj["adr"] + "-*.md")), (rj, files))
    head = _git(adr_remote, "rev-parse", "main")

    # revisit — 기록용 칸만 승인 없이 커밋, 충족 알림은 Inbox로
    rv = c.post("/runs/revisit", json={}).json()
    rec = [x for x in rv["summary"] if x.startswith("재검토 기록 커밋")]
    files = _git(adr_remote, "diff-tree", "--no-commit-id", "--name-status", "-r", "main")
    check("실사용 revisit → 기록 커밋 1개 (기존 ADR 수정 M만)", rv["ok"] and rec and "막힘" not in rec[0] and _git(adr_remote, "rev-parse", "main~1") == head
          and files and all(line.startswith("M\tdocs/adr/") for line in files.splitlines()), (rv["summary"], files))
    check("실사용 revisit: 커밋 내용은 기록용 칸만", all(hitl.record_only_errors(_git(adr_remote, "show", f"main~1:{line.split(chr(9))[1]}"),
                                                                  _git(adr_remote, "show", f"main:{line.split(chr(9))[1]}")) is None for line in files.splitlines()))
    check("실사용 revisit: 충족 알림 → Inbox", any(i["kind"] == "revisit" for i in c.get("/inbox").json()["items"]))
    check("실사용 revisit: 행동 로그에 기록 커밋", any(e["event"] == "revisit_record" and e["detail"]["state"] == "exported" for e in events(svc)))
    # 사람이 다른 clone에서 push해 원격이 앞섬 → 다음 실행이 따라잡고(ff) capture 승인도 그 위에 push한다 (기록 커밋이 막히는 경우는 test_export.py)
    human = os.path.join(root, "human")
    _clone(adr_remote, human)
    open(os.path.join(human, "README-human.md"), "w", encoding="utf-8").write("사람\n")
    _git(human, "add", "-A")
    _git(human, "commit", "-q", "-m", "사람의 변경")
    _git(human, "push", "-q", "origin", "main")
    hh = _git(adr_remote, "rev-parse", "main")
    res = svc.submit_capture("Kopii small 모델을 쓸지 정하자")
    ap = c.post(f"/inbox/{res['item']}/approve", json={}).json() if res.get("item") else {}
    check("실사용 capture 승인 → main에 커밋", ap.get("outcome") == "written" and _git(adr_remote, "rev-list", "--count", f"{hh}..main") == "1", (res, ap))
    u = r.get("usage") or {}
    check("실사용 사용량: billing (대역은 api) · 정가 환산 칸", u.get("billing") == "api" and "cost_usd_equiv" in u and not u.get("estimated"), u)


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
    u = r["usage"]
    check("capture: 사용량 — 스텁은 모델 stub · 추정 · 비용 0 (HYO-98)", u and u["estimated"] and u["cost_usd"] == 0 and u["model_calls"] >= 1
          and u["input_tokens"] > 0, u)
    check("capture: 항목 상세에 만든 사용량", c.get(f"/inbox/{r['item']}").json()["usage"] == u)
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
    failed = [f"{d['id']}/{x['id']}" for d in c.get("/decisions").json()["items"] for x in d["revisit"]
              if (x["last"] or {}).get("reason") and str(x["last"]["reason"]).startswith("fetch_failed")]
    check("revisit: 출처를 다 읽음 (fetch_failed 없음)", not failed, failed)
    check("revisit 뒤 대기 항목은 안 낡음", c.get(f"/inbox/{pending}").json()["changes"] == [], c.get(f"/inbox/{pending}").json()["changes"])

    # detect — 초안을 쓴다. 검토 대기 초안은 아직 세상이 아니다(승인 전) → 대기 항목은 안 낡는다. 승인하면 낡는다
    r = c.post("/runs/detect", json={}).json()
    check("detect 실행 ok + 초안", r["ok"] and r["drafts"], r)
    check("detect: 겹침(LEANN) 건너뜀이 요약에", "건너뜀 1건" in r["summary"][0], r["summary"])
    check("detect: 버린 소식마다 이유·제목, 건수는 요약과 같다 (HYO-97)", r["excluded"] and all(e["reason"] and e["title"] for e in r["excluded"])
          and f"제외 {len(r['excluded'])}건" in r["summary"][0] and all(e["why"] is None for e in r["excluded"]), r["excluded"])
    check("detect 실행 기록에도 버린 이유 (runs.jsonl)", c.get("/runs").json()["items"][0]["excluded"] == r["excluded"])
    check("detect 실행 사용량 — 스텁 추정 · 비용 0", r["usage"] and r["usage"]["estimated"] and r["usage"]["cost_usd"] == 0, r["usage"])
    ds2 = {d["id"]: d for d in c.get("/decisions").json()["items"]}
    check("detect 초안은 proposed", all(ds2[x["adr"]]["status"] == "proposed" for x in r["drafts"]))
    check("기존 ADR status 그대로 (R8)", all(ds2[k]["status"] == v for k, v in statuses.items()))
    check("detect 뒤(검토 대기) 대기 항목은 안 낡음", c.get(f"/inbox/{pending}").json()["changes"] == [])
    rows = [i for i in c.get("/inbox").json()["items"] if i["kind"] == "detect"]
    check("detect 초안이 Inbox에 (승인·수정·거부)", len(rows) == len(r["drafts"]) and all(i["actions"] == ["approve", "edit", "reject"] for i in rows), rows)
    a = c.post(f"/inbox/{rows[0]['id']}/approve", json={}).json()
    check("detect 승인 → written", a["outcome"] == "written", a)
    check("detect 승인 뒤 대기 항목은 바뀜", c.get(f"/inbox/{pending}").json()["changes"] != [])
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
    tue = dt.datetime(2026, 10, 6, 9, 0, tzinfo=kst)  # 화 09:00 — revisit 칸(월 09:00)은 지났고 detect 칸(화 18:00)은 아직
    check("칸: revisit 지난 칸 = 월 09:00", scheduling.last_slot("revisit", tue) == dt.datetime(2026, 10, 5, 9, 0, tzinfo=kst))
    check("칸: detect 지난 칸 = 지난주 화", scheduling.last_slot("detect", tue) == dt.datetime(2026, 9, 29, 18, 0, tzinfo=kst))
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
    check("detect 칸은 수집(화 16:00) 뒤", scheduling.last_slot("detect", dt.datetime(2026, 10, 6, 17, 0, tzinfo=kst)).day == 29
          and scheduling.last_slot("detect", dt.datetime(2026, 10, 6, 18, 0, tzinfo=kst)).day == 6)

    # 수집 스냅숏에서 만든 입력(pipeline.detect_feed)이면 실행 요약 첫 줄이 깔때기 건수다 (HYO-84)
    funnel = {"items": 2290, "lane_a": 222, "kept": 8, "cap": "5/8", "to_detect": 5}
    with open(os.path.join(s3.snapshot_dir, "funnel.json"), "w", encoding="utf-8") as f:
        json.dump(funnel, f)
    r = s3.run("detect")
    check("detect 요약: 깔때기 건수 한 줄이 먼저", r["ok"] and r["summary"][0] == "수집 2290 → 레인 A 222 → 관문 남김 8 → detect 입력 5 (5/8)"
          and r["summary"][1].startswith("소식 "), r["summary"])


def scenario_profile(data):
    """개발 데이터 프로필 — 선택 · 폴더 분리 · andbread 시드(ADR 0001~0004 · 대기 3건) · detect·revisit 실행 · default는 그대로."""
    print("\n— 개발 데이터 프로필 —")
    # 선택 규칙
    check("프로필: 비면 default", devprofiles.resolve(None) == "default" and devprofiles.resolve("  ") == "default" and devprofiles.resolve("Default") == "default")
    check("프로필: andbread 선택", devprofiles.resolve("andbread") == "andbread" and "andbread" in devprofiles.available())
    for bad in ("nope", "../andbread", "andbread/..", "a b"):
        try:
            devprofiles.resolve(bad)
            check(f"프로필: {bad!r} 거부", False)
        except ValueError:
            check(f"프로필: {bad!r} 거부", True)
    d_default, d_ab = devprofiles.data_dir_for("default"), devprofiles.data_dir_for("andbread")
    check("프로필: 데이터 폴더가 따로", d_default != d_ab and os.path.basename(d_default) == ".dev-data" and os.path.basename(d_ab) == ".dev-data-andbread", (d_default, d_ab))

    # 시드
    seeded = devdata.seed(data, "andbread")
    check("andbread 시드: 대기 3건", [s["awaiting"] for s in seeded] == [True] * 3, seeded)
    check("andbread 시드: 프로필 표시", devprofiles.read_marker(data) == "andbread")
    svc = RadarService(data)
    c = TestClient(server.create_app(svc))
    check("andbread: health 프로필", c.get("/health").json()["profile"] == "andbread", c.get("/health").json())
    try:
        RadarService(data, profile="default")
        check("프로필 섞임 거부 (andbread 폴더에 default)", False)
    except ServiceError:
        check("프로필 섞임 거부 (andbread 폴더에 default)", True)
    check("andbread: ADR 0001~0004", sorted(os.path.basename(p)[:4] for p in glob.glob(os.path.join(svc.adr_dir, "*.md"))) == ["0001", "0002", "0003", "0004"], adrs(svc))
    ds = c.get("/decisions").json()["items"]
    check("andbread: Decisions 제목이 (예시)", len(ds) == 4 and all(d["title"].startswith("(예시)") for d in ds), [d["title"] for d in ds])
    check("andbread: ADR이 대상 repo를 가리킴", all(a["repo"] == "Andbread_Frontend" for d in ds for a in d["affected"]))
    check("andbread: 0003 보류 + 재검토 2조건", next(d for d in ds if d["id"] == "0003")["status"] == "hold" and
          sum(next(d for d in ds if d["id"] == "0003")["revisit_summary"].values()) == 2)
    check("andbread: 대상 repo 사본(CLAUDE.md·package.json)", all(os.path.isfile(os.path.join(data, "Andbread_Frontend", f)) for f in ("CLAUDE.md", "package.json")))
    check("andbread: 대상 repo 사본은 두 파일뿐", sorted(os.listdir(os.path.join(data, "Andbread_Frontend"))) == ["CLAUDE.md", "package.json"])
    check("andbread: CLAUDE.md 머리에 예시 표시", open(os.path.join(data, "Andbread_Frontend", "CLAUDE.md"), encoding="utf-8").read().startswith("> 예시"))
    import subprocess
    v = subprocess.run([sys.executable, service.SKILL_RADAR, "validate", svc.adr_dir], capture_output=True, text=True, encoding="utf-8")
    check("andbread: radar.py validate 통과", v.returncode == 0, v.stdout[-300:])

    items = c.get("/inbox").json()["items"]
    check("andbread: Inbox 3건, 오래된 순 6·3·0일", [i["waiting_days"] for i in items] == [6, 3, 0], items)
    check("andbread: 대기 초안이 andbread 대상", all(i["affected"] and i["affected"][0]["repo"] == "Andbread_Frontend" for i in items), items)
    check("andbread: 대기 초안 제목", [i["technology"] for i in items] == ["Supabase Realtime", "Next.js", "zod"], [i["technology"] for i in items])
    det = c.get(f"/inbox/{items[0]['id']}").json()
    check("andbread: 상세 미리보기 proposed + 재검토 조건", det["markdown"].startswith("---\nstatus: proposed") and "revisit_when" in det["markdown"], det["markdown"][:120])

    # 빠른 capture — 프로필 대본으로 고른다
    convs = devprofiles.load_json("andbread", "conversations.json")
    check("andbread 대본 고르기", [service.pick_stub(convs[k], svc._stubs(), svc.default_stub)[0] for k in ("realtime", "nextjs-cache", "zod-form", "link-zustand")]
          == ["AND-RT", "AND-CACHE", "AND-ZOD", "AND-ZUSTAND"])
    r = c.post("/capture", json={"conversation": convs["link-zustand"]}).json()
    check("andbread capture: zustand → linked 0001", r["outcome"] == "linked" and r["link_candidates"] == ["0001"], r)
    r = c.post("/capture", json={"conversation": "회의 메모: 점심 뭐 먹지"}).json()
    check("andbread capture: 대본 없음 → 기본 대본 + 표시", r["outcome"] == "awaiting" and "Supabase Realtime" in r["message"], r)

    # 승인 — 코어 경로로 andbread ADR이 쓰인다
    r = c.post(f"/inbox/{items[0]['id']}/approve", json={}).json()
    check("andbread: 승인 → written 0005", r["outcome"] == "written" and r["adr"] == "0005" and not r["validate_errors"], r)

    # 실행 — detect: 후보 2 · 건너뜀 1(Tailwind, 0003과 겹침) · 제외 3
    r = c.post("/runs/detect", json={}).json()
    check("andbread detect: ok", r["ok"], r)
    check("andbread detect: 소식 6 → 후보 2·건너뜀 1·제외 3", r["summary"][0] == "소식 6건 → 후보 2건 · 이미 있는 결정이라 건너뜀 1건 · 제외 3건", r["summary"])
    check("andbread detect: 제외 3건에 이유", len(r["excluded"]) == 3 and all(e["reason"] for e in r["excluded"]), r["excluded"])
    check("andbread detect: 초안 2건 validate 통과", len(r["drafts"]) == 2 and not any(d["validate_errors"] for d in r["drafts"]), r["drafts"])
    ds2 = {d["id"]: d for d in c.get("/decisions").json()["items"]}
    check("andbread detect: 초안은 proposed, 기존 ADR 불변", all(ds2[d["adr"]]["status"] == "proposed" for d in r["drafts"]) and
          all(ds2[k["id"]]["status"] == k["status"] for k in ds if k["id"] in ds2))
    # 실행 — revisit: 0003 c1 충족 알림
    r = c.post("/runs/revisit", json={}).json()
    check("andbread revisit: ok", r["ok"], r)
    check("andbread revisit: 0003 c1 충족 알림", [(a["adr"], a["condition"], a["result"]) for a in r["alerts"]] == [("0003", "c1", "met")], r["alerts"])
    check("andbread revisit: 알림 문구가 조건", "Tailwind v4" in r["alerts"][0]["message"], r["alerts"])
    d3 = c.get("/decisions/0003").json()
    check("andbread revisit: 0003 c1 최근 결과 met", next(x for x in d3["revisit"] if x["id"] == "c1")["last"]["result"] == "met")
    check("andbread revisit: 출처를 읽음 (fetch_failed 없음)", not any(str((x["last"] or {}).get("reason", "")).startswith("fetch_failed") for d in c.get("/decisions").json()["items"] for x in d["revisit"]))

    # default는 그대로 — 다른 폴더, 코어 대본, 5개 ADR, 프로필 대본이 새지 않는다
    d2 = os.path.join(os.path.dirname(data), "data-default-after")
    devdata.seed(d2)
    s2 = RadarService(d2)
    check("default: 프로필 default · 코어 대본", s2.profile == "default" and s2.pscript is None and s2.default_stub == service.DEFAULT_STUB)
    check("default: ADR 번호 0001~0005", [d["id"] for d in s2.decisions()] == ["0001", "0002", "0003", "0004", "0005"])
    check("default: 대기 3건·표시 파일 없음", len(s2.inbox()) == 3 and not os.path.exists(os.path.join(d2, devprofiles.MARKER)))
    check("default: pick_stub 인자 없이 그대로", service.pick_stub("오늘 점심 뭐 먹지") == (service.DEFAULT_STUB, False))
    try:
        RadarService(d2, profile="andbread")
        check("프로필 섞임 거부 (default 폴더에 andbread)", False)
    except ServiceError:
        check("프로필 섞임 거부 (default 폴더에 andbread)", True)
    check("프로필 대본이 코어 SCRIPT에 섞이지 않음", not any(k.startswith("AND-") for k in service.stub_scripts.SCRIPT["capture"]))
    # 서버 기동 경로의 선택 — 프로필은 인자 > RADAR_DEVDATA, 폴더는 RADAR_APP_DATA > --data-dir > 프로필 기본 폴더
    t = server.resolve_target
    check("선택: 아무것도 없으면 default·.dev-data", t(None, None, {}) == ("default", devprofiles.data_dir_for("default")))
    check("선택: RADAR_DEVDATA=andbread → .dev-data-andbread", t(None, None, {"RADAR_DEVDATA": "andbread"}) == ("andbread", d_ab))
    check("선택: RADAR_DEVDATA=default → default", t(None, None, {"RADAR_DEVDATA": "default"}) == ("default", d_default))
    check("선택: --profile이 환경변수보다 먼저", t("default", None, {"RADAR_DEVDATA": "andbread"})[0] == "default")
    check("선택: --data-dir이 프로필 폴더보다 먼저", t(None, "X", {"RADAR_DEVDATA": "andbread"}) == ("andbread", "X"))
    check("선택: RADAR_APP_DATA가 가장 먼저", t(None, "X", {"RADAR_DEVDATA": "andbread", "RADAR_APP_DATA": "Y"}) == ("andbread", "Y"))
    try:
        t(None, None, {"RADAR_DEVDATA": "nope"})
        check("선택: 모르는 프로필 거부", False)
    except ValueError:
        check("선택: 모르는 프로필 거부", True)



def _tree_hash(root):
    h = hashlib.sha256()
    for p in sorted(glob.glob(os.path.join(root, "**", "*"), recursive=True)):
        if os.path.isfile(p):
            h.update(os.path.relpath(p, root).encode())
            h.update(open(p, "rb").read())
    return h.hexdigest()


def scenario_hyo85(tmp):
    """HYO-85 — detect 검토(로컬 내보내기: 승인·수정·거부, 편집용 초안) · 작업+폴링 · 데모 모드(대역 모델)."""
    print("\n— HYO-85: detect 검토 · 작업 · 데모 모드 —")
    from radar import script as stub_scripts
    from radar.stub_model import StubChat
    from common import ops
    from common.schemas import AdrDraft

    data = os.path.join(tmp, "data85")
    devdata.seed(data)
    svc = RadarService(data)
    app = server.create_app(svc)
    c, jobs = TestClient(app), app.state.jobs

    # 작업 + 폴링 — 접수는 바로 돌아오고, 끝나면 결과가 붙는다
    bad = [c.post("/jobs/capture", json={}).status_code, c.post("/jobs/capture", json={"conversation": "  "}).status_code,
           c.post("/jobs/rm", json={}).status_code]
    check("작업 접수 거부: 빈 대화 400 · 모르는 작업 404", bad == [400, 400, 404], bad)
    convs = json.load(open(os.path.join(devdata.FIX, "conversations.json"), encoding="utf-8"))
    j = c.post("/jobs/capture", json={"conversation": convs["V3"]})
    check("작업 접수 202 + id", j.status_code == 202 and j.json()["job"]["state"] in ("queued", "running", "done"), j.json())
    jd = c.post("/jobs/detect", json={}).json()["job"]
    jobs.join()
    j1 = c.get(f"/jobs/{j.json()['job']['id']}").json()
    j2 = c.get(f"/jobs/{jd['id']}").json()
    check("capture 작업 끝 → Inbox 항목", j1["state"] == "done" and j1["item"] in {i["id"] for i in c.get("/inbox").json()["items"]}, j1)
    check("detect 작업 끝 → 요약 · 초안", j2["state"] == "done" and j2["result"]["drafts"] and j2["message"].startswith("소식 "), j2)
    lst = c.get("/jobs").json()["items"]
    check("작업 목록 최신 먼저", [x["id"] for x in lst[:2]] == [jd["id"], j1["id"]], [x["id"] for x in lst])
    check("작업 없는 id 404", c.get("/jobs/job-none").status_code == 404)
    check("로그: job_submitted·job_done", {"job_submitted", "job_done"} <= {x["event"] for x in events(svc)})
    # 다시 뜨면 끝나지 않은 작업은 error로 닫는다 (다시 돌리지 않는다 — 과금)
    saved = json.load(open(jobs.path, encoding="utf-8"))
    saved.append({**saved[-1], "id": "job-stuck", "state": "running", "finished": None})
    json.dump(saved, open(jobs.path, "w", encoding="utf-8"))
    import jobs as jobs_mod
    j3 = jobs_mod.Jobs(svc, start=False).get("job-stuck")
    check("재시작: 끝나지 않은 작업은 error", j3["state"] == "error" and "다시 시작" in j3["error"], j3)

    # detect 검토 — 편집용 초안 · 수정 · 거부 (로컬 내보내기, git 없음)
    rows = [i for i in c.get("/inbox").json()["items"] if i["kind"] == "detect"]
    check("detect 항목 2개 이상 + why 칸(관문 없으면 null)", len(rows) >= 2 and all("why" in i and i["why"] is None for i in rows), rows)
    det = c.get(f"/inbox/{rows[0]['id']}").json()
    d = det["draft"]
    check("detect 상세: 편집용 초안이 파일과 같다 (render 왕복)", d is not None and ops.render(AdrDraft.model_validate(d), "2026-10-08")[4:].split("\n", 2)[2]
          == det["markdown"].split("\n", 3)[3] if d else False, (d, det["markdown"][:200]))
    bad_edit = c.post(f"/inbox/{rows[0]['id']}/edit", json={"draft": {**d, "status": "adopted"}}).json()
    check("detect edit: status 칸 거부 · 안 씀", bad_edit["outcome"] == "edit_errors" and rows[0]["id"] in {i["id"] for i in c.get("/inbox").json()["items"]}, bad_edit)
    e = c.post(f"/inbox/{rows[0]['id']}/edit", json={"draft": {**d, "title": "사람이 고친 제목"}}).json()
    path = os.path.join(svc.adr_dir, "..", "..", e.get("path") or "x")
    check("detect edit → 같은 번호로 고쳐 승인", e["outcome"] == "written" and e["adr"] == det["number_note"].split()[1]
          and "# 사람이 고친 제목" in open(path, encoding="utf-8").read(), e)
    adr2 = c.get(f"/inbox/{rows[1]['id']}").json()["number_note"].split()[1]
    rj = c.post(f"/inbox/{rows[1]['id']}/reject", json={}).json()
    check("detect 거부 → 초안 파일 삭제", rj["outcome"] == "rejected" and not glob.glob(os.path.join(svc.adr_dir, adr2 + "-*.md")), rj)
    check("답한 detect 항목은 Inbox에서 빠짐", not {rows[0]["id"], rows[1]["id"]} & {i["id"] for i in c.get("/inbox").json()["items"]})
    st = [hitl.read_item(svc.ctx(), rows[0]["id"])["export"], hitl.read_item(svc.ctx(), rows[1]["id"])["export"]]
    check("별도 파일: 승인 done · 거부 closed", st == [{"state": "done"}, {"state": "closed", "reason": "reject"}], st)

    # 데모 모드 — 실제 모델 자리에 대역(StubChat)을 꽂아 API 없이 배관만 본다
    src = os.path.join(tmp, "data85", "adr-fixture")
    before = _tree_hash(src)
    snap = os.path.join(tmp, "demo-snap")
    shutil.copytree(svc.snapshot_dir, snap)
    feed = json.load(open(os.path.join(snap, "feed.json"), encoding="utf-8"))
    json.dump({f["url"]: {"id": f["id"], "reason": f"{f['package'] if f.get('package') else 'x'} L5 × breaking", "kept_by": []} for f in feed},
              open(os.path.join(snap, "gate.json"), "w", encoding="utf-8"))
    json.dump({"items": 2290, "lane_a": 222, "kept": 4, "cap": "4/4", "to_detect": len(feed)}, open(os.path.join(snap, "funnel.json"), "w", encoding="utf-8"))
    calls = []

    def fake(flow):
        calls.append(flow)
        return StubChat(script=stub_scripts.detect_script(), flow=flow)

    ddir = os.path.join(tmp, "demo-data")
    for kw, name in (({"demo_snapshot": os.path.join(tmp, "none")}, "데모: detect 입력 없으면 거부"),
                     ({"demo_snapshot": snap, "demo_adr": tmp}, "데모: 데이터 폴더가 원본 안이면 거부")):
        try:
            RadarService(ddir, model="anthropic", demo=True, chat_factory=fake, **{"demo_adr": src, **kw})
            check(name, False)
        except ServiceError:
            check(name, True)
    dsvc = RadarService(ddir, model="anthropic", demo=True, demo_adr=src, demo_snapshot=snap, chat_factory=fake)
    dapp = server.create_app(dsvc)
    dc = TestClient(dapp)
    h = dc.get("/health").json()
    check("데모 health: mode demo · detect만 · 사본 · 건수", h["mode"] == "demo" and h["flows"] == ["detect"] and h["demo"]["adr_copy"].startswith(ddir)
          and h["demo"]["funnel"].startswith("수집 2290") and h["model"] != "stub", h)
    check("데모: revisit 거부 (실행 · 작업)", dc.post("/runs/revisit", json={}).status_code == 409 and dc.post("/jobs/revisit", json={}).status_code == 409)
    check("데모: 쓰기 경로는 사본", dsvc.adr_dir.startswith(ddir) and dsvc.env()["RADAR_ADR_DIR"].startswith(ddir) and dsvc.env()["RADAR_DRY_RUN"] == "1")
    jd = dc.post("/jobs/detect", json={}).json()["job"]
    dapp.state.jobs.join()
    r = dc.get(f"/jobs/{jd['id']}").json()
    check("데모 detect 작업 → 대역 모델로 · 요약 첫 줄 건수", r["state"] == "done" and calls and set(calls) == {"detect"}
          and r["result"]["summary"][0].startswith("수집 2290") and r["result"]["drafts"], r)
    du = r["result"]["usage"]
    check("데모 detect: 사용량 — 추정 아님 · 호출 수 · 모델", du and not du["estimated"] and du["model_calls"] >= 1 and du["model"] and du["cost_usd"] is not None, du)
    from common.env import cost_for
    check("단가표: 아는 모델(날짜 붙은 이름도) · 모르는 모델 None · 로컬 0", cost_for("claude-sonnet-5-5", 10**6, 10**6) == 12.0
          and cost_for("claude-sonnet-5-5-20260930", 10**6, 0) == 2.0 and cost_for("gpt-oss-20b", 10**6, 10**6) is None
          and cost_for("ollama/gemma4:e4b", 10**6, 10**6) == 0.0 and cost_for("claude-sonnet-5-50", 1, 1) is None)
    check("데모 detect: 실행 이벤트에 사용량", any(e.get("event") == "run" and (e.get("detail") or {}).get("usage") == du
          for e in map(json.loads, open(dsvc.events_path, encoding="utf-8"))), du)
    check("데모 detect: 버린 소식에 관문의 왜 왔나도 짝으로", r["result"]["excluded"] and all(e["reason"] and e["why"] and "× breaking" in e["why"]
          for e in r["result"]["excluded"]), r["result"]["excluded"])
    drows = [i for i in dc.get("/inbox").json()["items"] if i["kind"] == "detect"]
    check("데모 Inbox: detect 항목에 왜 왔나", drows and all(i["why"] and "× breaking" in i["why"] for i in drows), drows)
    a = dc.post(f"/inbox/{drows[0]['id']}/approve", json={}).json()
    check("데모 승인 → 사본에 ADR", a["outcome"] == "written" and glob.glob(os.path.join(dsvc.adr_dir, a["adr"] + "-*.md")), a)
    check("데모: 원본 ADR repo 무변경", _tree_hash(src) == before)
    dsvc2 = RadarService(ddir, model="anthropic", demo=True, demo_adr=src, demo_snapshot=snap, chat_factory=fake)
    check("데모 재시작: 사본을 이어 쓴다 (다시 복사하지 않음)", glob.glob(os.path.join(dsvc2.adr_dir, a["adr"] + "-*.md")))


def scenario_hyo92(tmp):
    """HYO-92 화면 설계 ①·③ — 접수된 capture가 Inbox에(대기 → 처리 → 초안이면 빠짐 / 초안 없이 끝나면 확인함) · revisit 충족 알림이 Inbox에(확인함은 ADR을 안 바꾼다, R8)."""
    print("\n— HYO-92: 접수된 capture · revisit 충족 알림 —")
    import jobs as jobs_mod
    data = os.path.join(tmp, "data92")
    devdata.seed(data)
    svc = RadarService(data)
    jobs = jobs_mod.Jobs(svc, start=False)  # 일꾼을 안 띄우고 손으로 하나씩 돌린다 — 대기 상태를 볼 수 있게
    c = TestClient(server.create_app(svc, jobs=jobs))
    convs = json.load(open(os.path.join(devdata.FIX, "conversations.json"), encoding="utf-8"))

    def rows(kind):
        return [i for i in c.get("/inbox").json()["items"] if i["kind"] == kind]

    def step():
        jid, payload = jobs.q.get()
        jobs.run_one(jid, payload)
        jobs.q.task_done()

    # ① 접수 → 대기 행 → 처리 → 초안이면 대기 행은 빠지고 capture 항목이 잇는다
    j1 = c.post("/jobs/capture", json={"conversation": "\n  " + convs["V8"]}).json()["job"]
    p = rows("pending")
    check("① 접수 즉시 Inbox에 대기 행 (queued · 답 없음 · 제목=대화 첫 줄)", len(p) == 1 and p[0]["id"] == j1["id"] and p[0]["state"] == "queued"
          and p[0]["actions"] == [] and convs["V8"].strip().startswith(p[0]["title"].rstrip("…")) and len(p[0]["title"]) <= 81, p)
    check("① 처리 중인 접수는 확인함으로 못 치움 409", c.post(f"/inbox/{j1['id']}/ack").status_code == 409)
    step()
    done = c.get(f"/jobs/{j1['id']}").json()
    check("① 초안이 나오면 대기 행은 빠지고 capture 항목", not rows("pending") and done["item"] in {i["id"] for i in rows("capture")}, (rows("pending"), done))
    # 초안 없이 끝남(같은 결정 있음) → 확인함으로 치운다
    j2 = c.post("/jobs/capture", json={"conversation": convs["V1"]}).json()["job"]
    step()
    p = rows("pending")
    check("① 같은 결정 → 대기 행 linked + 확인함", len(p) == 1 and p[0]["state"] == "linked" and p[0]["actions"] == ["ack"] and "0001" in (p[0]["message"] or ""), p)
    check("① 대기 행에 승인은 409", c.post(f"/inbox/{j2['id']}/approve", json={}).status_code == 409)
    det = c.get(f"/inbox/{j2['id']}").json()
    check("① 대기 행 상세", det["kind"] == "pending" and det["draft"] is None, det)
    a = c.post(f"/inbox/{j2['id']}/ack").json()
    check("① 확인함 → 사라짐 · 작업 기록엔 acked", a["outcome"] == "acked" and not rows("pending") and c.get(f"/jobs/{j2['id']}").json()["acked"], a)
    check("① 두 번 확인함 404", c.post(f"/inbox/{j2['id']}/ack").status_code == 404)
    # 실패한 접수
    orig = svc.submit_capture
    svc.submit_capture = lambda conv, repo=None, topic=None: (_ for _ in ()).throw(RuntimeError("모델 연결 실패"))
    j3 = c.post("/jobs/capture", json={"conversation": "아무 대화"}).json()["job"]
    step()
    svc.submit_capture = orig
    p = rows("pending")
    check("① 실패 → 대기 행 error + 이유 + 확인함", len(p) == 1 and p[0]["id"] == j3["id"] and p[0]["state"] == "error" and "모델 연결 실패" in p[0]["message"]
          and p[0]["actions"] == ["ack"], p)
    c.post(f"/inbox/{j3['id']}/ack")
    check("① 초안(capture)에 확인함은 409", c.post(f"/inbox/{done['item']}/ack").status_code == 409)

    # ③ revisit 충족 → Inbox (0001 c1 — 스냅숏 S02)
    adr1 = glob.glob(os.path.join(svc.adr_dir, "0001-*.md"))[0]
    r = c.post("/runs/revisit", json={}).json()
    before = open(adr1, "rb").read()  # revisit 실행은 기록용 칸(revisit_last_checked)을 쓴다 — 확인함 전후를 비교한다
    rv = rows("revisit")
    check("③ 충족 알림이 Inbox에 (adr · 조건 문장 · 확인함)", r["alerts"] and len(rv) == len(r["alerts"]) and rv[0]["adr"] == "0001" and rv[0]["state"] == "met"
          and rv[0]["actions"] == ["ack"] and rv[0]["message"].startswith("c1") and rv[0]["title"].startswith("재검토 조건 충족"), rv)
    det = c.get(f"/inbox/{rv[0]['id']}").json()
    check("③ 상세 = ADR 원문 (「ADR 열기」 재료)", det["markdown"] == before.decode("utf-8") and det["number_note"] == "ADR 0001", det["number_note"])
    n = len(svc._record_alerts(r["alerts"], "2026-10-09T09:00:00+09:00"))
    check("③ 안 치운 같은 알림은 다시 안 만든다", n == 0 and len(rows("revisit")) == len(rv))
    check("③ 알림에 승인은 409", c.post(f"/inbox/{rv[0]['id']}/approve", json={}).status_code == 409)
    a = c.post(f"/inbox/{rv[0]['id']}/ack").json()
    st = {d["id"]: d["status"] for d in c.get("/decisions").json()["items"]}
    check("③ 확인함 → 사라짐 · ADR 파일 바이트 그대로 · status 그대로 (R8)", a["outcome"] == "acked" and len(rows("revisit")) == len(rv) - 1
          and open(adr1, "rb").read() == before and st["0001"] == "hold", (a, st.get("0001")))
    check("③ 치운 뒤 같은 조건이 다시 충족되면 새 알림", len(svc._record_alerts([{"adr": "0001", "condition": "c1", "result": "met", "message": "x"}],
                                                                      "2026-10-16T09:00:00+09:00")) == 1)
    kinds = [x["event"] for x in events(svc)]
    check("로그: ack · item_created(revisit)", "ack" in kinds and any(x["event"] == "item_created" and (x.get("detail") or {}).get("kind") == "revisit" for x in events(svc)))




def scenario_hyo95(tmp):
    """HYO-95 — 빠른 capture의 repo 칸: 대상 목록 · 정한 repo가 초안에 박힘(승인은 승인) · 대상 밖 repo는 쓰지 않고 사람에게 묻는다 (R18)."""
    print("\n— HYO-95: capture repo 지정 · 대상 밖 repo 막기 —")
    import jobs as jobs_mod
    data = os.path.join(tmp, "data95")
    devdata.seed(data)
    svc = RadarService(data)
    c = TestClient(server.create_app(svc, jobs=jobs_mod.Jobs(svc, start=False)))
    convs = json.load(open(os.path.join(devdata.FIX, "conversations.json"), encoding="utf-8"))
    t = [x["repo"] for x in c.get("/targets").json()["items"]]
    check("대상 목록 = radar-targets.json", t == ["finance-verifier", "medical-mate"], t)
    check("대상 밖 repo → 400 (capture · 작업)", c.post("/capture", json={"conversation": convs["V8"], "repo": "unknown"}).status_code == 400
          and c.post("/jobs/capture", json={"conversation": convs["V8"], "repo": "nope"}).status_code == 400)

    # V8 대본은 medical-mate/pii-guard 초안 — finance-verifier로 정하면 repo만 바뀌고 component는 모델(대본) 것
    r = c.post("/capture", json={"conversation": convs["V8"], "repo": "finance-verifier"}).json()
    det = c.get(f"/inbox/{r['item']}").json()
    aff = det["draft"]["affected"]
    check("정한 repo가 Inbox 초안에 처음부터 박힘 · component는 그대로", aff and all(a["repo"] == "finance-verifier" for a in aff)
          and [a["component"] for a in aff] == ["pii-guard"] and det["affected_errors"] == [], aff)
    check("미리보기에도 정한 repo", "repo: finance-verifier" in det["markdown"], det["markdown"][:300])
    a = c.post(f"/inbox/{r['item']}/approve", json={}).json()
    path = os.path.join(svc.adr_dir, "..", "..", a.get("path") or "x")
    check("승인 → 정한 repo로 저장 · 이벤트는 approve", a["outcome"] == "written" and "repo: finance-verifier" in open(path, encoding="utf-8").read()
          and any(e["event"] == "approve" and e["item"] == r["item"] for e in events(svc)), a)
    check("capture_submitted 이벤트에 repo", any(e["event"] == "capture_submitted" and (e.get("detail") or {}).get("repo") == "finance-verifier"
                                             for e in events(svc)))

    # 자동(repo 없음)은 지금과 같다
    r = c.post("/capture", json={"conversation": convs["V8"]}).json()
    auto_id = r["item"]
    check("자동: 대본의 repo 그대로", [x["repo"] for x in c.get(f"/inbox/{r['item']}").json()["draft"]["affected"]] == ["medical-mate"])

    # 대상 밖 repo가 들어간 초안 (2026-10-11 실측 0012 `repo: unknown`) — API는 막으니 서비스로 직접 만든다 (규율 3: 잡혀야 할 게 잡히는가)
    base = adrs(svc)
    bad_id, _ = svc.capture(convs["V4"], stub="JHS-031", repo="unknown")
    det = c.get(f"/inbox/{bad_id}").json()
    check("대상 밖 repo 초안 → 상세에 affected_errors", det["affected_errors"] and "R18" in det["affected_errors"][0], det["affected_errors"])
    a = c.post(f"/inbox/{bad_id}/approve", json={}).json()
    check("그대로 승인 → 쓰지 않음 · 대기 유지 · 이유", a["outcome"] == "edit_errors" and a["errors"] and adrs(svc) == base
          and bad_id in {i["id"] for i in c.get("/inbox").json()["items"]}, a)
    d = {k: v for k, v in det["draft"].items()}
    e = c.post(f"/inbox/{bad_id}/edit", json={"draft": {**d, "affected": [{"repo": "unknown", "component": "x"}]}}).json()
    check("수정도 대상 밖이면 거부", e["outcome"] == "edit_errors" and any("R18" in x for x in e["errors"]) and adrs(svc) == base, e)
    e = c.post(f"/inbox/{bad_id}/edit", json={"draft": {**d, "affected": [{"repo": "finance-verifier", "component": "verifier-core"}]}}).json()
    check("repo를 고쳐 승인 → 저장", e["outcome"] == "written" and len(adrs(svc)) == len(base) + 1, e)
    # 빈 affected (2026-10-11 실제 모델 재확인: 자동 모드에서 모델이 affected를 비웠다) — 대상 목록과 무관하게 막는다
    check("빈 affected → affected_errors (대상 목록 없어도)", hitl.affected_errors(svc.ctx(), {"affected": []})
          and hitl.affected_errors(dataclasses.replace(svc.ctx(), targets={}), {"affected": []}))
    det = c.get(f"/inbox/{auto_id}").json()
    e = c.post(f"/inbox/{auto_id}/edit", json={"draft": {**det["draft"], "affected": []}}).json()
    check("수정으로 affected를 비우면 거부", e["outcome"] == "edit_errors" and any("비었다" in x for x in e["errors"]) and len(adrs(svc)) == len(base) + 1, e)


def scenario_hyo110(tmp):
    """HYO-110 — 지난 알림 목록: notification_shown을 읽기만 한다 · 클릭 짝(nid / item·kind) · 열어 봄 · 항목의 지금 상태 · 묶은 알림 · 예전 kind."""
    print("\n— HYO-110: 지난 알림 목록 —")
    data = os.path.join(tmp, "data110")
    devdata.seed(data)
    svc = RadarService(data)
    c = TestClient(server.create_app(svc))

    def ev(event, item=None, **detail):
        assert c.post("/events", json={"event": event, "item": item, "detail": detail}).status_code == 200

    def notes(**q):
        r = c.get("/notifications", params=q)
        assert r.status_code == 200, r.text
        return r.json()["items"]

    check("알림 없음 → 빈 목록", notes() == [])
    a, b, d = [i["id"] for i in c.get("/inbox").json()["items"]]
    ev("app_opened")
    ev("notification_shown", a, kind="capture", title="승인 대기 새 항목", body="초안 a", nid="n-a")
    ev("notification_shown", None, kind="detect", title="detect 초안 2건", body="b 외 1건", items=[b, d])  # 묶은 알림 (HYO-109) — nid 없음
    ev("notification_shown", "0001", kind="run_drafts")  # 예전 실행 경로 알림 — item이 ADR 번호, 제목 없음
    ev("notification_clicked", a, kind="capture", nid="n-a")
    ev("item_opened", b)
    ev("notification_clicked", None, kind="detect")  # nid 없음 → 같은 (item, kind)의 바로 앞 알림
    r = c.post(f"/inbox/{a}/approve", json={}).json()
    files = sorted(os.listdir(data))
    n = notes()
    check("최근 것 먼저 · 세 통", [x["kind"] for x in n] == ["run_drafts", "detect", "capture"], [x["kind"] for x in n])
    na, nb, legacy = n[2], n[1], n[0]
    check("제목·본문 = 화면이 보낸 OS 알림 문구", na["title"] == "승인 대기 새 항목" and na["body"] == "초안 a" and legacy["title"] is None, na)
    check("클릭 짝: nid", na["clicked"] and na["clicked_at"], na)
    check("클릭 짝: nid 없으면 같은 item·kind", nb["clicked"] and not legacy["clicked"], (nb, legacy))
    check("승인한 항목 → answered · 결과 · ADR 번호", r["outcome"] == "written" and na["items"][0]["status"] == "answered"
          and na["items"][0]["answer"] == "approve" and na["items"][0]["outcome"] == "written" and na["items"][0]["adr"] == r["adr"], na["items"])
    check("묶은 알림 → 항목 둘 · 지금 Inbox에 있음 · 종류·제목", [x["id"] for x in nb["items"]] == [b, d]
          and all(x["status"] == "inbox" and x["kind"] == "capture" and x["title"] and x["state"] in ("awaiting_approval", "stale") for x in nb["items"]), nb["items"])
    check("열어 봄 = 알림 뒤의 item_opened (항목마다)", [x["opened"] for x in nb["items"]] == [True, False] and not na["items"][0]["opened"], nb["items"])
    check("예전 kind는 item 그대로 · 지금 상태 gone", legacy["items"] == [{**legacy["items"][0], "id": "0001", "status": "gone"}], legacy["items"])
    check("limit = 최근 몇 통", [x["kind"] for x in notes(limit=1)] == ["run_drafts"] and c.get("/notifications", params={"limit": 0}).status_code == 422)
    check("읽기만 — 데이터 폴더에 새 파일 없음", sorted(os.listdir(data)) == files)
    check("같은 데이터면 id가 그대로", [x["id"] for x in notes()] == [x["id"] for x in n])

    # 열어 보기 전 알림은 열어 봄이 아니다 · 클릭이 두 번이어도 한 통
    ev("item_opened", d)
    ev("notification_shown", d, kind="capture", nid="n-d")
    ev("notification_clicked", d, kind="capture", nid="n-d")
    ev("notification_clicked", d, kind="capture", nid="n-d")
    n = notes()
    check("알림 앞의 item_opened는 안 셈", n[0]["items"][0]["id"] == d and not n[0]["items"][0]["opened"] and n[0]["clicked"] and len(n) == 4, n[0])
    # 같은 항목에 두 통 — 앞 것을 누르면 nid로 앞 것에 (item·kind로 짝지으면 뒤 것에 붙는다)
    ev("notification_shown", b, kind="capture", nid="n-b1")
    ev("notification_shown", b, kind="capture", nid="n-b2")
    ev("notification_clicked", b, kind="capture", nid="n-b1")
    n = notes()
    check("클릭 짝: nid가 item·kind보다 정확", [x["clicked"] for x in n[:2]] == [False, True], [x["clicked"] for x in n[:2]])

    # revisit 충족 → 확인함 → answered(ack · acked · ADR)
    c.post("/runs/revisit", json={})
    rv = [i for i in c.get("/inbox").json()["items"] if i["kind"] == "revisit"][0]
    ev("notification_shown", rv["id"], kind="revisit", title="재검토 조건 충족", nid="n-rv")
    x = notes()[0]["items"][0]
    check("revisit 항목: inbox · ADR 번호", x["status"] == "inbox" and x["kind"] == "revisit" and x["adr"] == "0001", x)
    c.post(f"/inbox/{rv['id']}/ack")
    x = notes()[0]["items"][0]
    check("확인함 뒤: answered · ack · acked · ADR 번호", (x["status"], x["answer"], x["outcome"], x["adr"]) == ("answered", "ack", "acked", "0001"), x)

    # detect 초안: 승인하면 쓴 ADR · 거부하면 초안을 지웠으니 번호 없음 (지운 ADR로 보내지 않게)
    r = c.post("/runs/detect", json={}).json()
    rows = [i for i in c.get("/inbox").json()["items"] if i["kind"] == "detect"]
    ok_id, no_id = rows[0]["id"], rows[1]["id"]
    for x in (ok_id, no_id):
        ev("notification_shown", x, kind="detect", nid=f"n-{x}")
    xs = {x["id"]: x for nn in notes()[:2] for x in nn["items"]}
    check("detect 초안: inbox", all(xs[x]["status"] == "inbox" for x in (ok_id, no_id)), xs)
    ap, rj = c.post(f"/inbox/{ok_id}/approve", json={}).json(), c.post(f"/inbox/{no_id}/reject").json()
    xs = {x["id"]: x for nn in notes()[:2] for x in nn["items"]}
    check("detect 승인 → ADR 번호 · 그 ADR이 있다", ap["outcome"] == "written" and xs[ok_id]["adr"] == ap["adr"]
          and c.get(f"/decisions/{ap['adr']}").status_code == 200, xs[ok_id])
    check("detect 거부 → answered · rejected · ADR 번호 없음 (초안을 지웠다)", rj["outcome"] == "rejected" and rj["adr"]
          and (xs[no_id]["status"], xs[no_id]["outcome"], xs[no_id]["adr"]) == ("answered", "rejected", None)
          and c.get(f"/decisions/{rj['adr']}").status_code == 404, (rj, xs[no_id]))

    # 깨진 줄(쓰는 중 읽힌 마지막 줄 등)은 건너뛴다
    with open(svc.events_path, "a", encoding="utf-8") as f:
        f.write('{"at": "2026-10-13T00:00:00+09:00", "event": "notification_sh')
    check("깨진 줄은 건너뜀", len(notes()) == 9)

def scenario_hyo132(tmp):
    """HYO-132 — 남김 카드 표시: 관문 kept_by로 강제 이전(+마감일) · 선택 실험 · 표시 없음. 코드가 정한다 (모델 아님)."""
    print("\n— HYO-132: 남김 카드 표시 —")
    import datetime as dt
    from pipeline.detect_feed import url_key
    data = os.path.join(tmp, "data132")
    devdata.seed(data)
    svc = RadarService(data)
    app = server.create_app(svc)
    c, jobs = TestClient(app), app.state.jobs
    c.post("/jobs/detect", json={})
    jobs.join()
    rows = [i for i in c.get("/inbox").json()["items"] if i["kind"] == "detect"]
    check("준비: detect 항목 2개 이상 · 관문 없으면 action null", len(rows) >= 2 and all(i["action"] is None for i in rows), rows)
    check("capture 항목은 action null", all(i["action"] is None for i in c.get("/inbox").json()["items"] if i["kind"] == "capture"))

    def evidence(row):
        md = c.get(f"/inbox/{row['id']}").json()["markdown"]
        return [e["url"] for e in service._front_matter(md).get("evidence") or [] if isinstance(e, dict) and e.get("url")]

    snap = svc.snapshot_dir
    index = json.load(open(os.path.join(snap, "index.json"), encoding="utf-8"))
    os.makedirs(os.path.join(snap, "items"), exist_ok=True)
    today = dt.datetime.fromisoformat(service.now_iso()).date()
    due = today + dt.timedelta(days=30)

    def setup(url, change, body):
        """url의 관문 항목(kept_by = change)과 전문 파일을 쓴다. body None이면 파일 없음 (index는 가리키는데 파일이 없는 경우)."""
        gate = json.load(open(os.path.join(snap, "gate.json"), encoding="utf-8")) if os.path.exists(os.path.join(snap, "gate.json")) else {}
        gate[url] = {"id": "x", "reason": f"anthropic L2 × {change}", "kept_by": [{"technology": "anthropic", "tier": "L2", "change": change}] if change else []}
        json.dump(gate, open(os.path.join(snap, "gate.json"), "w", encoding="utf-8"))
        name = f"items/H132-{abs(hash(url)) % 10**6}.md"
        if body is None:
            if os.path.exists(os.path.join(snap, name)):
                os.remove(os.path.join(snap, name))
        else:
            open(os.path.join(snap, name), "w", encoding="utf-8", newline="\n").write(body)
        index[url_key(url)] = {"source_type": "official_doc", "file": name}
        json.dump(index, open(os.path.join(snap, "index.json"), "w", encoding="utf-8"))

    u0, u1 = evidence(rows[0])[0], evidence(rows[1])[0]
    body = f"# claude-x\n\nclaude-x will be retired on {due.isoformat()}.\nSee migration guide.\n"
    setup(u0, "model_retirement", body)
    setup(u1, "successor_model", "# claude-y\n\nclaude-y is available.\n")
    got = {i["id"]: i for i in c.get("/inbox").json()["items"]}
    a0, a1 = got[rows[0]["id"]]["action"], got[rows[1]["id"]]["action"]
    check("모델 은퇴 + 본문에 날짜 하나 → 강제 이전 · 마감일 · D-day",
          a0 and a0["kind"] == "forced_migration" and a0["label"] == "강제 이전" and a0["deadline"] == due.isoformat() and a0["days_left"] == 30
          and a0["deadline_note"] is None and "retired" in a0["deadline_quote"], a0)
    check("후속 모델 → 선택 실험 · 마감 없음", a1 and a1["kind"] == "optional_trial" and a1["label"] == "선택 실험" and a1["deadline"] is None
          and a1["days_left"] is None, a1)
    d0 = c.get(f"/inbox/{rows[0]['id']}").json()
    check("상세에도 action이 실린다", d0["action"] == a0, d0["action"])

    setup(u1, "breaking", "# z\n\nretired on 2099-01-01\n")
    check("breaking만 → action null", {i["id"]: i for i in c.get("/inbox").json()["items"]}[rows[1]["id"]]["action"] is None)
    setup(u1, "deprecation", "# z\n\nretired on 2099-01-01\n")
    check("deprecation만 → 강제 아님 · action null", {i["id"]: i for i in c.get("/inbox").json()["items"]}[rows[1]["id"]]["action"] is None)
    setup(u0, "eol", None)
    a = {i["id"]: i for i in c.get("/inbox").json()["items"]}[rows[0]["id"]]["action"]
    check("관문 항목은 있는데 전문 파일이 없다 → 강제 이전 · 마감일 null · 「마감일 확인 필요」",
          a and a["kind"] == "forced_migration" and a["deadline"] is None and a["days_left"] is None and a["deadline_note"] == "마감일 확인 필요"
          and "EOL" in a["reason"], a)
    setup(u0, "model_retirement", f"# x\n\nretired on 2027-01-01 and shutdown on 2027-03-01\n")
    a = {i["id"]: i for i in c.get("/inbox").json()["items"]}[rows[0]["id"]]["action"]
    check("본문에 날짜가 여럿 → 마감일 null", a and a["deadline"] is None and a["deadline_note"] == "마감일 확인 필요", a)

    # 근거가 둘이면: 강제 이전이 선택 실험보다 · 강제끼리는 마감일 있는 쪽이 먼저
    def text_of(*urls):
        return "---\nevidence:\n" + "".join(f"  - url: {u}\n" for u in urls) + "---\n# t\n"
    urls = ["https://example.test/a", "https://example.test/b", "https://example.test/c"]
    setup(urls[0], "successor_model", "# a\n")
    setup(urls[1], "eol", "# b\n\nEOL date\n")
    setup(urls[2], "model_retirement", f"# c\n\nretired on {due.isoformat()}\n")
    check("근거 둘: 선택 실험 + 강제 이전 → 강제 이전", svc._card_action(text_of(urls[0], urls[1]))["kind"] == "forced_migration")
    check("근거 둘: 마감일 없는 강제 + 있는 강제 → 마감일 있는 쪽", svc._card_action(text_of(urls[1], urls[2]))["deadline"] == due.isoformat())
    check("근거 url에 관문 항목이 없으면 None", svc._card_action(text_of("https://example.test/none")) is None and svc._card_action("본문만") is None)
    out = c.get("/openapi.json").json()
    check("OpenAPI에 CardAction이 있다", "CardAction" in out["components"]["schemas"])


def scenario_hyo127(tmp):
    """HYO-127 — 화제 탭(레인 C): 주차 목록·k 자르기·404·빈 상태 · 저장/저장 빼기(멱등·복사본) · capture의 topic · 새 UI 이벤트 · 개발 예시."""
    print("\n— HYO-127: 화제 탭 —")
    import jobs as jobs_mod
    from pipeline import topics as pt
    data = os.path.join(tmp, "data127")
    os.makedirs(data)
    svc = RadarService(data)
    c = TestClient(server.create_app(svc, jobs=jobs_mod.Jobs(svc, start=False)))

    r = c.get("/topics")
    check("파일 없음 → 빈 목록 (오류 아님)", r.status_code == 200 and r.json() == {"week": None, "weeks": [], "method": None, "k": 0, "counts": None, "items": []}, r.text)
    check("파일 없음 → 저장 목록도 빈 목록", c.get("/topics/saved").json() == {"items": []})
    check("파일 없음 → 주차를 줘도 빈 목록", c.get("/topics", params={"week": "2026-W41"}).json()["items"] == [])

    # 개발 예시 — 합성 항목으로 만든다. 다시 불러도 덮어쓰지 않는다
    check("ensure_topics: 처음엔 만든다", devdata.ensure_topics(data) is True)
    check("ensure_topics: 있으면 다시 만들지 않는다", devdata.ensure_topics(data) is False)
    res = json.load(open(os.path.join(data, "topics", devdata.TOPICS_FIXTURE_WEEK + ".json"), encoding="utf-8"))
    items = pt.read_items(devdata.TOPICS_FIXTURE)
    check("예시 항목은 전부 [예시] 제목", items and all(i["title"].startswith("[예시]") for i in items))
    multi = [t for t in res["topics"] if t["n_sources"] > 1]
    check("예시: 여러 출처 토픽 2개 이상 (HN+Reddit · GitHub+HF)", len(multi) >= 2, [t["title"] for t in multi])
    xp = [t for t in res["topics"] if t["n_sources"] == 1 and any(i["source"] == "reddit" and "벤치마크" in i["title"] for i in t["items"])]
    check("예시: 크로스포스트는 한 글로 센다", len(xp) == 1 and xp[0]["n_items"] == 1 and xp[0]["sources"] == {"reddit": 1}, xp)
    check("예시: 한 출처 토픽이 hn·github·huggingface·reddit에 걸친다",
          {s for t in res["topics"] if t["n_sources"] == 1 for s in t["sources"]} == {"hn", "github", "huggingface", "reddit"})

    d = c.get("/topics").json()
    check("GET /topics: 가장 새 주차 · k개 · 순위대로", d["week"] == "2026-W42" and d["weeks"] == ["2026-W42"] and d["k"] == 10 and len(d["items"]) == 10
          and [t["rank"] for t in d["items"]] == list(range(1, 11)) and d["method"] == res["method"] and d["counts"] == res["counts"], d["weeks"])
    top = d["items"][0]
    check("TopicRow: 글 목록 · 대표 인기 · 저장 안 됨", top["posts"] and top["signal"] == top["posts"][0]["signal"] and top["saved"] is False
          and top["n_items"] == len(top["posts"]) and top["week"] == "2026-W42", top)
    check("capture_seed: 제목 · 글 줄 · 결정 질문", top["capture_seed"].startswith(f"화제 (2026-W42): {top['title']}\n- ")
          and top["capture_seed"].endswith("\n\n결정 질문: ") and top["posts"][0]["url"] in top["capture_seed"], top["capture_seed"])
    print("  예시 상위 10:")
    for t in d["items"]:
        print(f"   {t['rank']:>2}. {t['section']:<6} {t['sources']} {t['title']}")

    # 주차 목록 — 새 것 먼저, 주차 이름이 아닌 파일은 무시, k 자르기
    older = {**res, "week": "2026-W41", "k": 3}
    json.dump(older, open(os.path.join(data, "topics", "2026-W41.json"), "w", encoding="utf-8"), ensure_ascii=False)
    open(os.path.join(data, "topics", "notes.json"), "w", encoding="utf-8").write("{}")
    open(os.path.join(data, "topics", "2026-W5.json"), "w", encoding="utf-8").write("{}")
    d = c.get("/topics").json()
    check("주차 목록은 새 것 먼저 · 주차 이름 아닌 파일은 무시", d["weeks"] == ["2026-W42", "2026-W41"] and d["week"] == "2026-W42", d["weeks"])
    d41 = c.get("/topics", params={"week": "2026-W41"}).json()
    check("week 지정 · k 자르기 (파일 k=3)", d41["week"] == "2026-W41" and d41["k"] == 3 and len(d41["items"]) == 3
          and [t["id"] for t in d41["items"]] == [t["id"] for t in res["topics"][:3]] and d41["items"][0]["week"] == "2026-W41")
    check("모르는 주차 → 404", c.get("/topics", params={"week": "2026-W01"}).status_code == 404
          and c.get("/topics", params={"week": "../x"}).status_code == 404)

    # 저장 · 저장 빼기
    tid = top["id"]
    s1 = c.post(f"/topics/{tid}/save", json={"week": "2026-W42"})
    check("저장 → saved", s1.status_code == 200 and s1.json()["ok"] and s1.json()["saved"] is True, s1.text)
    s2 = c.post(f"/topics/{tid}/save", json={"week": "2026-W42"}).json()
    check("두 번 저장 → 멱등 (이미 저장함)", s2["saved"] is True and "이미" in s2["message"] and len(c.get("/topics/saved").json()["items"]) == 1, s2)
    check("GET /topics의 saved 표시", {t["id"]: t["saved"] for t in c.get("/topics").json()["items"]}[tid] is True
          and sum(t["saved"] for t in c.get("/topics").json()["items"]) == 1)
    check("없는 화제 저장 → 404 · 모르는 주차 → 404", c.post("/topics/t-nope/save", json={"week": "2026-W42"}).status_code == 404
          and c.post(f"/topics/{tid}/save", json={"week": "2026-W01"}).status_code == 404)
    check("week 빠짐 → 422", c.post(f"/topics/{tid}/save", json={}).status_code == 422)
    second = d["items"][1]["id"]
    second_save = c.post(f"/topics/{second}/save", json={"week": "2026-W42"})
    sv = c.get("/topics/saved").json()["items"]
    check("저장 목록: 최근 저장 먼저 · 복사본 모양", [x["id"] for x in sv] == [second, tid] and sv[1]["posts"] == top["posts"] and sv[1]["title"] == top["title"]
          and sv[1]["week"] == "2026-W42" and sv[1]["saved_at"] and sv[1]["n_items"] == top["n_items"] and sv[1]["sources"] == top["sources"], sv)
    check("저장 파일은 {items} 한 벌 · 임시 파일 안 남음", set(json.load(open(svc.topics_saved_path, encoding="utf-8"))) == {"items"}
          and not os.path.exists(svc.topics_saved_path + ".tmp") and second_save.status_code == 200)
    os.remove(os.path.join(data, "topics", "2026-W42.json"))
    check("주차 파일을 지워도 저장한 복사본은 남는다", [x["id"] for x in c.get("/topics/saved").json()["items"]] == [second, tid]
          and c.get("/topics").json()["week"] == "2026-W41")
    u = c.post(f"/topics/{tid}/unsave").json()
    check("저장 빼기 → saved false · 목록에서 사라짐", u["ok"] and u["saved"] is False and [x["id"] for x in c.get("/topics/saved").json()["items"]] == [second], u)
    check("저장하지 않은 화제 빼기 → 404", c.post(f"/topics/{tid}/unsave").status_code == 404)
    evs = events(svc)
    check("행동 로그: topic_saved(week) · topic_unsaved",
          any(e["event"] == "topic_saved" and e["item"] == tid and e["detail"] == {"week": "2026-W42"} for e in evs)
          and any(e["event"] == "topic_unsaved" and e["item"] == tid for e in evs)
          and sum(e["event"] == "topic_saved" and e["item"] == tid for e in evs) == 1)  # 멱등 두 번째는 기록하지 않는다

    # 화제 폴더 환경 변수
    alt = os.path.join(tmp, "alt-topics")
    os.makedirs(alt)
    shutil.copy(os.path.join(data, "topics", "2026-W41.json"), os.path.join(alt, "2026-W40.json"))
    os.environ["RADAR_TOPICS_DIR"] = alt
    try:
        svc2 = RadarService(data)
        check("RADAR_TOPICS_DIR이 화제 폴더를 바꾼다", svc2.topics_dir == os.path.abspath(alt) and svc2.topics()["weeks"] == ["2026-W40"])
    finally:
        del os.environ["RADAR_TOPICS_DIR"]
    check("기본 화제 폴더 = <data>/topics", svc.topics_dir == os.path.abspath(os.path.join(data, "topics")))

    # capture 연결 — 동작은 그대로, 행동 로그에만 남는다
    convs = json.load(open(os.path.join(devdata.FIX, "conversations.json"), encoding="utf-8"))
    cdata = os.path.join(tmp, "data127c")
    devdata.seed(cdata)
    csvc = RadarService(cdata)
    jj = jobs_mod.Jobs(csvc, start=False)
    cc = TestClient(server.create_app(csvc, jobs=jj))
    ref = {"id": tid, "week": "2026-W42"}
    r = cc.post("/capture", json={"conversation": convs["V8"], "topic": ref})
    check("capture(topic) → 접수", r.status_code == 200 and r.json()["outcome"] == "awaiting", r.text)
    ce = [e for e in events(csvc) if e["event"] == "capture_submitted"]
    check("capture_submitted detail에 topic", ce and ce[-1]["detail"]["topic"] == ref, ce)
    cc.post("/capture", json={"conversation": convs["V4"]})
    check("topic 없으면 detail에 topic 칸 없음", "topic" not in [e for e in events(csvc) if e["event"] == "capture_submitted"][-1]["detail"])
    check("topic 모양이 틀리면 422", cc.post("/capture", json={"conversation": convs["V8"], "topic": {"id": "x"}}).status_code == 422)
    j = cc.post("/jobs/capture", json={"conversation": convs["V8"], "topic": ref})
    jid = j.json()["job"]["id"]
    jj.run_one(*jj.q.get_nowait())
    check("작업 capture도 topic을 넘긴다", j.status_code == 202 and jj.get(jid)["state"] == "done"
          and [e for e in events(csvc) if e["event"] == "capture_submitted"][-1]["detail"]["topic"] == ref)

    # UI 이벤트
    ok = [c.post("/events", json={"event": ev, "item": it}).status_code for ev, it in (("topics_opened", None), ("topic_opened", tid))]
    check("새 UI 이벤트 topics_opened · topic_opened 접수", ok == [200, 200]
          and any(e["event"] == "topic_opened" and e["item"] == tid and e["source"] == "app" for e in events(svc)))

    # 개발 데이터 시드가 예시를 만든다 (기본 프로필 · 다른 프로필)
    check("개발 데이터 시드가 화제 예시 주차를 만든다", os.path.exists(os.path.join(cdata, "topics", devdata.TOPICS_FIXTURE_WEEK + ".json"))
          and cc.get("/topics").json()["week"] == devdata.TOPICS_FIXTURE_WEEK)
    adata = os.path.join(tmp, "data127a")
    devdata.seed(adata, "andbread")
    check("프로필 시드도 화제 예시를 만든다", os.path.exists(os.path.join(adata, "topics", devdata.TOPICS_FIXTURE_WEEK + ".json")))


if __name__ == "__main__":
    sys.exit(main())
