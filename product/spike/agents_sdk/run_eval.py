"""스파이크 eval 어댑터 — 스킬 러너(eval/runner/run_case.py)의 build·grade를 그대로 쓰고 실행만 제품 파이프라인으로 바꾼다.

  python product/spike/agents_sdk/run_eval.py --stub                 # 기준 6케이스 + 음성 대조 2 (API 없음)
  python product/spike/agents_sdk/run_eval.py --stub JHS-037 --k 3

채점기는 한 벌이다 (비교 프로토콜 §4). 제품 trace를 스킬 transcript와 같은 모양으로 만들어 넘긴다.
격리도 같다: radar.py는 작업 디렉터리의 사본(runroot)에서 돌린다 — 레포 경로에 닿는 호출은 answer_key_access에 걸린다.
산출물: eval/runs/<시각>-agents_sdk-<모델>/<case>-<i>/ (transcript.jsonl · grade.json · 작업 디렉터리 사본)
"""

import argparse
import asyncio
import datetime as dt
import hashlib
import ipaddress
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from pipeline import ROOT, SKILL_RADAR, Ctx, revisit  # noqa: E402
from stub_model import StubModel  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "eval", "runner"))
import run_case as rc  # noqa: E402

from agents import set_trace_processors  # noqa: E402
from agents.tracing import TracingProcessor  # noqa: E402

SPIKE_CASES = ["JHS-001", "JHS-016", "JHS-021", "JHS-037", "JHS-043", "JHS-044"]  # runtime-spike.md §2
SAFETY_CASES = {"JHS-037", "JHS-043", "JHS-044"}  # P2
SCRIPT = json.load(open(os.path.join(HERE, "stub_script.json"), encoding="utf-8"))


# ── P5: 데이터 경계 ─────────────────────────────────────────────────

class CountingProcessor(TracingProcessor):
    """기본 exporter(OpenAI 업로드)를 이것으로 **갈아 끼운다**. 트레이스가 하나라도 생기면 세어 둔다."""

    def __init__(self):
        self.traces = self.spans = 0

    def on_trace_start(self, trace):
        self.traces += 1

    def on_trace_end(self, trace):
        pass

    def on_span_start(self, span):
        self.spans += 1

    def on_span_end(self, span):
        pass

    def shutdown(self):
        pass

    def force_flush(self):
        pass


class NetGuard:
    """이 프로세스의 외부 연결을 기록하고 막는다. 허용 목록 밖 호스트 이름 해석·연결은 OSError.

    한계: getaddrinfo와 socket.connect만 본다 — IP 리터럴로 이벤트 루프가 직접 여는 연결은 못 본다.
    radar.py 하위 프로세스는 따로 돈다(네트워크 코드 없음).
    """

    def __init__(self, allow=()):
        self.allow, self.attempts = set(allow), []

    def _ok(self, host):
        try:
            return ipaddress.ip_address(host).is_loopback  # 이벤트 루프의 self-pipe 등
        except ValueError:
            return host in self.allow or host == "localhost"

    def __enter__(self):
        self._gai, self._conn = socket.getaddrinfo, socket.socket.connect
        guard = self

        def gai(host, *a, **k):
            if not guard._ok(str(host)):
                guard.attempts.append(str(host))
                raise OSError(f"netguard: {host} 차단 (P5)")
            return guard._gai(host, *a, **k)

        def conn(sock, addr):
            host = addr[0] if isinstance(addr, tuple) else str(addr)
            if not guard._ok(str(host)):
                guard.attempts.append(str(host))
                raise OSError(f"netguard: {host} 차단 (P5)")
            return guard._conn(sock, addr)

        socket.getaddrinfo, socket.socket.connect = gai, conn
        return self

    def __exit__(self, *exc):
        socket.getaddrinfo, socket.socket.connect = self._gai, self._conn


# ── P4: 규칙층 재사용 ────────────────────────────────────────────────

def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def radar_unmodified():
    """radar.py가 커밋된 상태 그대로인가 (작업 트리 diff 0줄). 사본 해시는 실행마다 따로 대조한다."""
    rel = os.path.relpath(SKILL_RADAR, ROOT).replace("\\", "/")
    p = subprocess.run(["git", "-C", ROOT, "diff", "--numstat", "HEAD", "--", rel], capture_output=True, text=True)
    head = subprocess.run(["git", "-C", ROOT, "log", "-1", "--format=%h", "--", rel], capture_output=True, text=True)
    return {"file": rel, "diff_numstat": p.stdout.strip(), "last_commit": head.stdout.strip(), "ok": not p.stdout.strip()}


# ── 실행 1회 ─────────────────────────────────────────────────────────

def run_one(case, model, model_name, out_dir, allow_hosts=(), radar_from_repo=False):
    work = tempfile.mkdtemp(prefix="jhs-spike-")
    before = rc.build(case, work)
    radar_py = os.path.join(work, "runroot", "scripts", "radar.py")
    os.makedirs(os.path.dirname(radar_py))
    shutil.copy(SKILL_RADAR, radar_py)
    if radar_from_repo:  # 음성 대조 전용 — 격리가 깨진 실행
        radar_py = SKILL_RADAR
    env = dict(os.environ)
    env.update({
        "RADAR_ADR_DIR": os.path.join(work, "adr-fixture", "docs", "adr"),
        "RADAR_TARGETS": os.path.join(work, "adr-fixture", "radar-targets.json"),
        "RADAR_SNAPSHOT_DIR": os.path.join(work, "snapshots"),
        "RADAR_SERVED_LOG": os.path.join(work, "served.log"),
        "RADAR_DRY_RUN": "1",
        "OPENAI_AGENTS_DISABLE_TRACING": "1",
        "TEMP": os.path.join(work, "tmp"), "TMP": os.path.join(work, "tmp"), "TMPDIR": os.path.join(work, "tmp"),
    })
    open(env["RADAR_SERVED_LOG"], "w").close()
    ctx = Ctx.from_env(env, radar_py=radar_py)
    now = (case.get("setup") or {}).get("now", rc.NOW)

    tp = CountingProcessor()
    set_trace_processors([tp])
    t0 = dt.datetime.now()
    crash = None
    with NetGuard(allow_hosts) as ng:
        try:
            report, diag = asyncio.run(revisit(ctx, model, now))
        except Exception as e:  # 파이프라인 자체가 죽으면 run_report 없음 → 채점에서 떨어진다
            report, diag, crash = None, {"p3_errors": []}, f"{type(e).__name__}: {e}"
    wall = (dt.datetime.now() - t0).total_seconds()

    events = [{"type": "system", "subtype": "init", "model": model_name}] + ctx.events
    text = "```json\n" + json.dumps({"run_report": report}, ensure_ascii=False, indent=1) + "\n```" if report else f"crash: {crash}"
    events.append({"type": "result", "result": text, "num_turns": diag.get("model_calls"),
                   "duration_ms": int(wall * 1000), "total_cost_usd": None})
    with open(os.path.join(out_dir, "transcript.jsonl"), "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    g = rc.grade(case, before, work, events)
    copy_same = sha(radar_py) == sha(SKILL_RADAR)
    g["spike"] = {
        "P3_errors": diag["p3_errors"],
        "P4_radar_copy_identical": copy_same,
        "P5_net_attempts": ng.attempts,
        "P5_traces": tp.traces, "P5_spans": tp.spans,
        "crash": crash,
        "input_tokens": diag.get("input_tokens"), "output_tokens": diag.get("output_tokens"),
    }
    g["diag"].update(wall_s=round(wall, 2))
    json.dump(g, open(os.path.join(out_dir, "grade.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    shutil.copytree(work, os.path.join(out_dir, "work"))
    shutil.rmtree(work, ignore_errors=True)
    return g


def failed(g):
    return [c["check"] for c in g["checks"] if not c["ok"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="*", help="기본: 기준 6케이스 (+ --stub면 음성 대조)")
    ap.add_argument("--stub", action="store_true", help="스텁 모델 (API 없음). 지금은 이것만 된다")
    ap.add_argument("--k", type=int, default=1)
    a = ap.parse_args()
    if not a.stub:
        sys.exit("실제 모델 실행은 아직 없다 — API 실험은 파이프라인 뒤 (사용자 2026-09-30). --stub")

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-agents_sdk-stub"
    root = os.path.join(rc.EVAL, "runs", stamp)
    p4 = radar_unmodified()
    rows = []

    cases = a.cases or SPIKE_CASES
    for cid in cases:
        case = rc.load_case(cid)
        for i in range(a.k):
            out = os.path.join(root, f"{cid}-{i + 1}")
            os.makedirs(out)
            script = {**SCRIPT["base"], **SCRIPT["cases"].get(cid, {})}
            g = run_one(case, StubModel(script), "stub", out)
            rows.append(("case", cid, g))
            print(json.dumps({"case": cid, "run": i + 1, "pass": g["pass"], "safety_pass": g["safety_pass"],
                              "failed": failed(g), **g["spike"], "tool_calls": g["diag"]["tool_calls"]}, ensure_ascii=False))

    ctrl_ok = True
    if not a.cases:  # 음성 대조 — 채점기가 제품 trace를 무는지
        for name, c in SCRIPT["controls"].items():
            out = os.path.join(root, name)
            os.makedirs(out)
            g = run_one(rc.load_case(c["case"]), StubModel({**SCRIPT["base"], **c["script"]}), "stub", out,
                        radar_from_repo=c.get("radar") == "repo")
            missing = [m for m in c["must_fail"] if m not in failed(g)]
            p3 = [e["kind"] for e in g["spike"]["P3_errors"]]
            ok = not missing and (not c["must_fail"] or not g["pass"]) and (c.get("expect_p3") is None or c["expect_p3"] in p3)
            ctrl_ok &= ok
            print(json.dumps({"control": name, "caught": ok, "grade_pass": g["pass"], "failed": failed(g),
                              "expected_but_passed": missing, "p3": p3}, ensure_ascii=False))

    graded = [g for kind, _, g in rows]
    summary = {
        "runs": root,
        "P1_pass": f"{sum(g['pass'] for g in graded)}/{len(graded)}",
        "P2_safety_cases": {cid: all(g["pass"] for k, c, g in rows if c == cid) for cid in sorted(SAFETY_CASES & set(cases))},
        "P3_errors": sum(len(g["spike"]["P3_errors"]) for g in graded),
        "P3_note": "스텁은 P3를 잴 수 없다 — 어댑터 400은 실제 모델에서만",
        "P4": {**p4, "copies_identical": all(g["spike"]["P4_radar_copy_identical"] for g in graded)},
        "P5": {"net_attempts": sum(len(g["spike"]["P5_net_attempts"]) for g in graded),
               "traces": sum(g["spike"]["P5_traces"] for g in graded)},
        "controls_caught": ctrl_ok if not a.cases else None,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    ok = all(g["pass"] for g in graded) and ctrl_ok and p4["ok"] and summary["P5"]["net_attempts"] == 0 and summary["P5"]["traces"] == 0
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
