"""데스크톱 앱 백엔드의 본체 — product/radar의 HITL 함수를 감싸기만 한다 (설계 docs/design/desktop-app.md §2, D1).

코어(`product/radar`)의 동작은 바꾸지 않는다. 여기서 하는 일:
  Inbox 목록  대기 중인 capture thread(checkpoint) + 검토 대기 별도 파일(`hitl.review_queue`)
  상세       대기 초안 · 저장될 ADR 미리보기(`ops.render`) · 「바뀐 점」(멈출 때의 지문과 지금의 지문 비교 — 읽기만)
  답         `capture_turn2`(approve·edit·reject + stale 답)를 그대로 부른다
  행동 로그   `<data>/events.jsonl` (실사용 지표, 비교 프로토콜 §7)
  capture    대화 → `capture_turn1` → 승인 대기 (R9: ADR repo 쓰기 0)
  Decisions  `radar.py list` + ADR 파일 제목 — 읽기만 (R8: status는 화면에서 바꾸지 않는다)
  실행       `detect`·`revisit` 그래프를 그대로 한 번 돌리고 보고서를 요약한다 (`<data>/runs.jsonl`)

안전 (작업 지시서 §5):
  - 모델은 스텁이 기본이다. 실제 모델은 `RADAR_APP_MODEL=anthropic`일 때만이고, 지금은 그것도 거부한다(과금 전 사용자 승인)
  - `RADAR_EXPORT`는 자식 환경에서 지우고 `RADAR_DRY_RUN=1`을 강제한다 — 내보내기(git push)가 켜질 길이 둘 다 막힌다 (`hitl.exporter_for`)
  - 키(.env)는 이 프로세스만 읽는다 (스텁 모드에서는 읽지도 않는다)
"""

import asyncio
import datetime as dt
import json
import os
import re
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "product"))

from radar import hitl  # noqa: E402  — radar/__init__이 spike/common을 경로에 올린다
from radar import script as stub_scripts  # noqa: E402
from radar.capture import capture_agent, capture_turn1, capture_turn2, thread_config  # noqa: E402
from radar.detect import detect  # noqa: E402
from radar.revisit import revisit  # noqa: E402
from radar.stub_model import StubChat  # noqa: E402
from common import ops  # noqa: E402
from common.core import SKILL_RADAR, Ctx, RadarError, radar  # noqa: E402
from common.schemas import AdrDraft  # noqa: E402
from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: E402
import yaml  # noqa: E402

STATE_FILE = "capture-checkpoint.sqlite"
META_FILE = "app-meta.json"
EVENTS_FILE = "events.jsonl"
RUNS_FILE = "runs.jsonl"
DEFAULT_STUB = "JHS-014"  # 2턴 스텁 대본 — 2턴에서는 도구 결과 순서만 보고 끝내므로 항목마다 달라도 같다 (stub_policy.capture_step)
UI_EVENTS = ("app_opened", "item_opened", "notification_shown", "notification_clicked", "capture_opened", "decision_opened")
FLOWS = ("detect", "revisit")
MAX_CONVERSATION = 200_000
OK_OUTCOMES = ("written", "rejected", "discarded", "rejudge", "recovered")
NUMBER_NOTE = "번호는 저장할 때 정해짐"


class ServiceError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def now_iso():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _days(since):
    try:
        t = dt.datetime.fromisoformat(since)
    except (TypeError, ValueError):
        return 0
    if t.tzinfo is None:
        t = t.astimezone()
    return max(0, (dt.datetime.now().astimezone() - t).days)


def _pending_draft(interrupts):
    for it in interrupts:
        for a in (it.value or {}).get("action_requests", []):
            if a["name"] == "propose_adr":
                return a["args"].get("draft")
    return None


_TITLE = re.compile(r"^#\s+(.+)$", re.M)


class RadarService:
    """한 프로세스에 하나. 쓰는 일은 전부 lock 하나로 줄 세운다 (Inbox 답 · capture · 시드)."""

    def __init__(self, data_dir, *, adr_dir=None, targets=None, model=None, radar_py=SKILL_RADAR):
        self.data_dir = os.path.abspath(data_dir)
        self.adr_dir = os.path.abspath(adr_dir or os.path.join(self.data_dir, "adr-fixture", "docs", "adr"))
        self.targets = os.path.abspath(targets or os.path.join(self.adr_dir, "..", "..", "radar-targets.json"))
        self.state_path = os.path.join(self.data_dir, STATE_FILE)
        self.meta_path = os.path.join(self.data_dir, META_FILE)
        self.events_path = os.path.join(self.data_dir, EVENTS_FILE)
        self.runs_path = os.path.join(self.data_dir, RUNS_FILE)
        self.snapshot_dir = os.path.join(self.data_dir, "snapshots")
        self.tmp = os.path.join(self.data_dir, "tmp")
        self.radar_py = radar_py
        self.mode = model or os.environ.get("RADAR_APP_MODEL") or "stub"
        if self.mode != "stub":
            # 실제 모델은 과금이다 — 호출 수·비용을 사용자에게 말하고 승인받는 경로가 생기기 전에는 켜지 않는다 (작업 지시서 §5)
            raise ServiceError(500, f"모델 {self.mode!r}: 마일스톤 1은 스텁만 지원한다 (RADAR_APP_MODEL을 비우거나 stub)")
        os.makedirs(self.tmp, exist_ok=True)
        self.lock = threading.RLock()

    # ── 환경 ─────────────────────────────────────────────────────

    def env(self):
        env = {k: v for k, v in os.environ.items() if k != "RADAR_EXPORT"}  # 내보내기 스위치는 물려받지 않는다
        env.update({
            "RADAR_ADR_DIR": self.adr_dir, "RADAR_TARGETS": self.targets,
            "RADAR_DRY_RUN": "1",  # exporter_for가 dry-run이면 no-op — RADAR_EXPORT가 어디서 들어와도 git을 안 부른다
            "RADAR_SNAPSHOT_DIR": self.snapshot_dir,  # detect·revisit는 스냅숏 모드만 (check_scope) — 웹에 나가지 않는다
            "RADAR_SERVED_LOG": os.path.join(self.data_dir, "served.log"),
            "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false",
            "PYTHONIOENCODING": "utf-8", "TEMP": self.tmp, "TMP": self.tmp, "TMPDIR": self.tmp,
        })
        return env

    def ctx(self):
        c = Ctx.from_env(self.env(), radar_py=self.radar_py)
        c.now = now_iso()
        return c

    def model(self, item_id=None):
        sid = (self._meta().get("items", {}).get(item_id) or {}).get("stub") if item_id else None
        return StubChat(script=stub_scripts.capture_script(sid or DEFAULT_STUB), flow="capture")

    # ── 앱 메타 (대기 시작 시각 · 원래 대화) — 코어 상태가 아니라 화면용 곁다리. 없어도 목록은 나온다 ─────

    def _meta(self):
        try:
            return json.load(open(self.meta_path, encoding="utf-8"))
        except (OSError, ValueError):
            return {"items": {}}

    def _put_meta(self, item_id, **kw):
        m = self._meta()
        m.setdefault("items", {}).setdefault(item_id, {}).update(kw)
        tmp = self.meta_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.meta_path)

    # ── 행동 로그 ────────────────────────────────────────────────

    def log(self, event, item=None, source="backend", **detail):
        rec = {"at": now_iso(), "event": event, "item": item, "source": source, "mode": self.mode}
        if detail:
            rec["detail"] = detail
        with self.lock, open(self.events_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def ui_event(self, event, item=None, detail=None):
        if event not in UI_EVENTS:
            raise ServiceError(400, f"모르는 이벤트 {event!r} — {list(UI_EVENTS)}")
        self.log(event, item, source="app", **(detail or {}))

    # ── Inbox ────────────────────────────────────────────────────

    def _captures(self, ctx):
        """대기 중인 capture thread → (id, 초안, 대기 시작, 멈출 때 지문, 범위). checkpoint만 읽는다."""
        if not os.path.exists(self.state_path):
            return []
        store, meta, out = hitl.StateStore(self.state_path), self._meta().get("items", {}), []
        with SqliteSaver.from_conn_string(self.state_path) as cp:
            agent = capture_agent(self.model(), cp)
            for tid in store.threads():
                snap = agent.get_state(thread_config(tid))
                draft = _pending_draft(snap.interrupts)
                if draft is None:
                    continue
                fp, scope = store.get(tid)
                since = (meta.get(tid) or {}).get("since") or snap.created_at
                out.append({"id": tid, "draft": draft, "since": since, "fp": fp, "scope": scope or hitl.scope_of(draft)})
        return out

    def _changes(self, ctx, fp, scope):
        """재개 때 「바뀐 점」 미리보기 — 멈출 때의 지문과 지금을 비교만 한다 (check_resume과 같은 비교, 회복·쓰기 없음)."""
        if fp is None:
            return ["멈출 때의 지문이 없다 — 무엇이 바뀌었는지 비교할 수 없다"]
        impl = hitl.fingerprint_for(ctx, fp.get("impl"))
        new = impl.compute(ctx, scope)
        return [] if impl.same(fp, new) else impl.diff(fp, new)

    def _capture_row(self, ctx, c):
        d = c["draft"]
        changes = self._changes(ctx, c["fp"], c["scope"])
        row = {"id": c["id"], "kind": "capture", "title": d.get("title"), "technology": d.get("technology"),
               "affected": d.get("affected") or [], "since": c["since"], "waiting_days": _days(c["since"]),
               "state": "stale" if changes else "awaiting_approval", "actions": ["approve", "edit", "reject"]}
        return row, changes

    def _review_row(self, ctx, q):
        """검토 대기 별도 파일(detect 초안 · 내보내기 보류). 내보내기를 켜야 답할 수 있어 마일스톤 1에서는 보기만."""
        item = hitl.read_item(ctx, q["item"]) or {}
        wi = item.get("write_intent") or {}
        path = os.path.join(hitl.repo_root(ctx), wi["path"]) if wi.get("path") else None
        text = open(path, encoding="utf-8").read() if path and os.path.exists(path) else ""
        m = _TITLE.search(text)
        scope = item.get("scope") or {}
        since = q["export"].get("since") or q["created"]
        row = {"id": q["item"], "kind": q["kind"], "title": m.group(1).strip() if m else q["item"],
               "technology": scope.get("technology"), "affected": scope.get("affected") or [], "since": since,
               "waiting_days": _days(since), "state": q["export"].get("state"), "actions": []}
        return row, text

    def inbox(self):
        with self.lock:
            ctx = self.ctx()
            rows = [self._capture_row(ctx, c)[0] for c in self._captures(ctx)]
            rows += [self._review_row(ctx, q)[0] for q in hitl.review_queue(ctx)]
        return sorted(rows, key=lambda r: str(r["since"]))

    def detail(self, item_id):
        with self.lock:
            ctx = self.ctx()
            for c in self._captures(ctx):
                if c["id"] == item_id:
                    row, changes = self._capture_row(ctx, c)
                    md = ops.render(AdrDraft.model_validate(c["draft"]), ctx.now)
                    return {**row, "draft": c["draft"], "markdown": md, "changes": changes, "number_note": NUMBER_NOTE}
            for q in hitl.review_queue(ctx):
                if q["item"] == item_id:
                    row, text = self._review_row(ctx, q)
                    return {**row, "draft": None, "markdown": text, "changes": (q["export"].get("changes") or []),
                            "number_note": f"ADR {q.get('adr')} (로컬 초안)", "note": "detect 검토는 아직 화면에서 답할 수 없다 — 내보내기 설정이 필요하다"}
        raise ServiceError(404, f"{item_id}: 대기 항목이 아니다")

    # ── 답 ───────────────────────────────────────────────────────

    def answer(self, item_id, decision, *, stale_answer=None, draft=None):
        if decision not in ("approve", "edit", "reject"):
            raise ServiceError(400, f"모르는 답 {decision!r}")
        if decision == "edit" and not isinstance(draft, dict):
            raise ServiceError(400, "edit에는 draft가 필요하다")
        with self.lock:
            ctx = self.ctx()
            ids = {c["id"] for c in self._captures(ctx)}
            if item_id not in ids:
                if any(q["item"] == item_id for q in hitl.review_queue(ctx)):
                    raise ServiceError(409, "detect 검토는 아직 화면에서 답할 수 없다 — 내보내기(RADAR_EXPORT)가 필요하다 (결정 필요)")
                raise ServiceError(404, f"{item_id}: 대기 항목이 아니다")
            try:
                rep, _ = capture_turn2(ctx, self.model(item_id), self.state_path, decision, ctx.now, self.mode,
                                       item_id=item_id, edited=draft, stale_answer=stale_answer)
            except hitl.HitlError as e:
                return self._done(item_id, decision, stale_answer, {"outcome": "error", "message": str(e)})
            res = self._outcome(ctx, item_id, decision, rep)
            if res["outcome"] == "rejudge":
                res = self._rejudge(item_id, res)
            return self._done(item_id, decision, stale_answer, res)

    def _done(self, item_id, decision, stale_answer, res):
        detail = {"outcome": res["outcome"]}
        if stale_answer:
            detail["stale_answer"] = stale_answer
        for k in ("adr", "errors", "changes", "new_item"):
            if res.get(k):
                detail[k] = res[k]
        self.log(decision, item_id, **detail)
        return res

    def _outcome(self, ctx, item_id, decision, rep):
        h = rep.get("hitl") or {}
        if h.get("edit_errors"):
            return {"outcome": "edit_errors", "message": rep.get("note") or "고친 초안이 검사를 통과하지 못했다", "errors": h["edit_errors"]}
        if h.get("stale"):
            return {"outcome": "stale", "message": rep.get("note"), "changes": h["stale"]["changes"], "answers": h["stale"]["answers"]}
        if h.get("outcome") == "discard":
            return {"outcome": "discarded", "message": rep.get("note")}
        if h.get("outcome") == "rejudge":
            return {"outcome": "rejudge", "message": rep.get("note")}
        if h.get("blocked"):
            return {"outcome": "blocked", "message": rep.get("note"), "blocked": h["blocked"]}
        if h.get("recovery"):
            return {"outcome": "recovered", "message": rep.get("note"), "validate_errors": h.get("validate_errors") or []}
        if decision == "reject":
            return {"outcome": "rejected", "message": "거부했다 — 아무것도 쓰지 않았다"}
        wi = (hitl.read_item(ctx, item_id) or {}).get("write_intent") or {}
        if wi.get("state") == "done":
            errs = wi.get("validate_errors") or []
            msg = f"ADR {wi['adr']} 저장됨" + (f" — validate 오류 {len(errs)}개 (파일은 남았다)" if errs else "")
            return {"outcome": "written", "message": msg, "adr": wi["adr"], "path": wi["path"], "validate_errors": errs}
        return {"outcome": "error", "message": f"예상하지 못한 결과: {rep.get('note')}"}

    def _rejudge(self, item_id, res):
        """「다시 판정」 — 코어는 항목을 닫고 호출자에게 capture_turn1을 다시 부르라고 한다. 원래 대화가 있으면 그렇게 한다."""
        m = self._meta().get("items", {}).get(item_id) or {}
        if not m.get("conversation"):
            return {**res, "message": "다시 판정하려면 대화를 다시 넣어 달라 (원래 대화가 앱에 없다)"}
        new_id, _ = self.capture(m["conversation"], stub=m.get("stub"))
        return {**res, "message": f"새 세상에서 다시 판정했다 → {new_id}", "new_item": new_id}

    # ── capture (1턴) ────────────────────────────────────────────

    def capture(self, conversation, *, stub=None, since=None):
        """대화 → capture 1턴 → 승인 대기에서 멈춘다 (R9: ADR repo 쓰기 0). (item_id 또는 None, 보고서)."""
        with self.lock:
            ctx = self.ctx()
            model = StubChat(script=stub_scripts.capture_script(stub or DEFAULT_STUB), flow="capture")
            rep, _ = asyncio.run(capture_turn1(ctx, model, conversation, ctx.now, self.state_path, self.mode))
            item_id = rep.get("item_id") if rep.get("awaiting_approval") else None
            if item_id:
                self._put_meta(item_id, since=since or ctx.now, conversation=conversation, stub=stub or DEFAULT_STUB)
                self.log("item_created", item_id, kind="capture")
            return item_id, rep

    def submit_capture(self, conversation):
        """화면의 빠른 capture 창 — 대화 → 1턴. 스텁 대본은 대화에 나온 기술로 고른다 (`pick_stub`)."""
        if not isinstance(conversation, str) or not conversation.strip():
            raise ServiceError(400, "대화가 비었다")
        if len(conversation) > MAX_CONVERSATION:
            raise ServiceError(413, f"대화가 너무 길다 ({len(conversation)}자 > {MAX_CONVERSATION})")
        stub, matched = pick_stub(conversation)
        item_id, rep = self.capture(conversation, stub=stub)
        note = "" if matched else " — 스텁: 대화와 맞는 대본이 없어 기본 대본(KoPII-small)으로 만들었다 (내용은 대화와 무관)"
        if item_id:
            res = {"outcome": "awaiting", "item": item_id, "message": "Inbox에 추가됨 — 승인 대기" + note, "link_candidates": []}
        elif rep.get("link_candidates"):
            res = {"outcome": "linked", "item": None, "link_candidates": list(rep["link_candidates"]),
                   "message": "이미 같은 결정이 있다: " + ", ".join(rep["link_candidates"]) + " — 새 초안을 만들지 않았다"}
        else:
            res = {"outcome": "no_draft", "item": None, "link_candidates": [], "message": rep.get("note") or "초안이 나오지 않았다"}
        self.log("capture_submitted", res["item"], outcome=res["outcome"], chars=len(conversation), stub=stub,
                 **({"link_candidates": res["link_candidates"]} if res["link_candidates"] else {}))
        return res

    # ── Decisions (읽기만 — R8) ──────────────────────────────────

    def decisions(self):
        with self.lock:
            ctx = self.ctx()
            try:
                rows = radar(ctx, "list", self.adr_dir)
            except RadarError as e:
                raise ServiceError(500, f"radar.py list 실패: {e}")
        return [self._decision_row(r) for r in rows]

    def decision(self, adr_id):
        for row in self.decisions():
            if row["id"] == adr_id:
                return {**row, "markdown": open(os.path.join(self.adr_dir, row["file"]), encoding="utf-8").read()}
        raise ServiceError(404, f"ADR {adr_id}가 없다")

    def _decision_row(self, r):
        path = os.path.join(self.adr_dir, os.path.basename(r["file"].replace("\\", "/")))
        text = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
        m = _TITLE.search(text)
        fm = _front_matter(text)
        last = {c.get("condition_id"): c for c in (r.get("revisit_last_checked") or []) if isinstance(c, dict)}
        conds, summary = [], {"met": 0, "not_met": 0, "undetermined": 0, "unchecked": 0}
        for c in r.get("revisit_when") or []:
            lc = last.get(c.get("id"))
            res = (lc or {}).get("result")
            summary[res if res in summary else "unchecked"] += 1
            conds.append({"id": c.get("id"), "check_type": c.get("check_type"), "condition": c.get("condition"),
                          "last": {"result": res, "checked_at": str(lc.get("checked_at")), "reason": lc.get("reason")} if lc else None})
        times = [str(c.get("checked_at")) for c in last.values() if c.get("checked_at")]
        return {"id": r["id"], "title": m.group(1).strip() if m else os.path.basename(path), "file": os.path.basename(path),
                "status": r.get("status"), "technology": r.get("technology"), "use": r.get("use"),
                "date": str(fm.get("date")) if fm.get("date") else None, "affected": r.get("affected") or [],
                "revisit": conds, "revisit_summary": summary, "last_checked": max(times) if times else None}

    # ── 실행 (detect · revisit) ──────────────────────────────────

    def run(self, flow, trigger="manual"):
        """흐름 하나를 스텁 모델로 한 번 돌린다. 코어 그래프를 그대로 부르고 보고서만 요약한다. 스냅숏 모드 + dry-run (check_scope)."""
        if flow not in FLOWS:
            raise ServiceError(404, f"모르는 흐름 {flow!r} — {list(FLOWS)}")
        with self.lock:
            ctx = self.ctx()
            if flow == "detect":
                fn, script = detect, stub_scripts.detect_script()
            else:  # JHS-001 대본 — 스냅숏 S02(Jev v0.9.0 릴리스)에서 0001 c1이 충족된다
                fn, script = revisit, stub_scripts.revisit_script(stub_scripts.SCRIPT["cases"]["JHS-001"])
            res = {"id": f"{ctx.now}-{flow}", "flow": flow, "at": ctx.now, "mode": self.mode, "trigger": trigger, "ok": True, "error": None,
                   "summary": [], "drafts": [], "alerts": []}
            try:
                rep, diag = asyncio.run(fn(ctx, StubChat(script=script, flow=flow), ctx.now, self.mode))
                res.update(self._summarize_detect(rep) if flow == "detect" else self._summarize_revisit(rep))
                if diag.get("p3_errors"):
                    res["summary"].append(f"오류 {len(diag['p3_errors'])}건: " + "; ".join(e.get("kind", "?") for e in diag["p3_errors"]))
            except Exception as e:  # 실행이 죽어도 앱은 산다 — 기록에 남기고 보인다
                res.update(ok=False, error=f"{type(e).__name__}: {str(e)[:300]}")
            with open(self.runs_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(res, ensure_ascii=False) + "\n")
            self.log("run", res["id"], flow=flow, trigger=trigger, ok=res["ok"], drafts=len(res["drafts"]), alerts=len(res["alerts"]))
            return res

    def runs(self, limit=50):
        try:
            lines = open(self.runs_path, encoding="utf-8").read().splitlines()
        except OSError:
            return []
        return [json.loads(x) for x in reversed(lines[-limit:]) if x.strip()]

    def _summarize_detect(self, rep):
        try:
            n_feed = len(json.load(open(os.path.join(self.snapshot_dir, "feed.json"), encoding="utf-8")))
        except (OSError, ValueError):
            n_feed = None
        cands, skipped = rep.get("candidates") or [], rep.get("skipped_existing") or []
        drafts = []
        for c in cands:
            if not c.get("adr"):
                continue
            f = next(iter(sorted(p for p in os.listdir(self.adr_dir) if p.startswith(c["adr"] + "-"))), None)
            m = _TITLE.search(open(os.path.join(self.adr_dir, f), encoding="utf-8").read()) if f else None
            drafts.append({"adr": c["adr"], "title": m.group(1).strip() if m else c["technology"], "technology": c["technology"],
                           "validate_errors": c.get("validate_errors") or []})
        head = f"소식 {n_feed}건 → " if n_feed is not None else ""
        summary = [f"{head}후보 {len(cands)}건 · 이미 있는 결정이라 건너뜀 {len(skipped)}건 · 제외 {len(rep.get('excluded') or [])}건"]
        if drafts:
            bad = sum(1 for d in drafts if d["validate_errors"])
            summary.append(f"초안 {len(drafts)}건 작성 (proposed)" + (f" — validate 실패 {bad}건" if bad else ""))
        else:
            summary.append("새 초안 없음")
        return {"summary": summary, "drafts": drafts}

    def _summarize_revisit(self, rep):
        results = rep.get("results") or []
        by = {(r["adr"], r["condition_id"]): r for r in results}
        conds = {(row["id"], c["id"]): c["condition"] for row in self.decisions() for c in row["revisit"]}
        alerts = [{"adr": a["adr"], "condition": a["condition_id"],
                   "result": (by.get((a["adr"], a["condition_id"])) or {}).get("result", "met"),
                   "message": conds.get((a["adr"], a["condition_id"])) or ""} for a in rep.get("alerts") or []]
        met = sum(1 for r in results if r.get("result") == "met")
        changed = sum(1 for r in results if r.get("changed"))
        summary = [f"ADR {len(rep.get('adrs_checked') or [])}개 · 조건 {len(results)}개 확인",
                   f"조건 충족 {met}건 · 지난번과 달라짐 {changed}건 · 알림 {len(alerts)}건"]
        return {"summary": summary, "alerts": alerts}


def _front_matter(text):
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return {}
    try:
        d = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}
    return d if isinstance(d, dict) else {}


def _capture_stubs():
    """대화에서 고를 수 있는 capture 스텁 대본 — 초안이 있는 것 + 같은 결정이 이미 있는 것(Jev, JHS-013: link로 끝난다)."""
    out = {}
    for cid, sc in stub_scripts.SCRIPT["capture"].items():
        if cid.startswith("_") or not isinstance(sc, dict):
            continue
        if sc.get("draft") is not None or cid == "JHS-013":
            out[cid] = sc
    return out


def pick_stub(conversation):
    """스텁 모드에서만 — 대화에 나온 기술·repo·component·용도로 대본을 고른다 → (대본 ID, 기술이 맞았나).
    실제 모델은 대화를 읽고 초안을 쓴다. 스텁은 미리 적은 초안 중 가장 가까운 것을 낼 뿐이다."""
    text = conversation.lower()
    best, score = None, 0
    for cid, sc in sorted(_capture_stubs().items()):
        m = sc["match"]
        if (m.get("technology") or "").lower() not in text:
            continue
        s = 4 + sum(1 for k in ("repo", "component", "use") if (m.get(k) or "").lower() in text)
        if s > score:
            best, score = cid, s
    return (best, True) if best else (DEFAULT_STUB, False)
