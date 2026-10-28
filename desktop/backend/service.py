"""데스크톱 앱 백엔드의 본체 — product/radar의 HITL 함수를 감싸기만 한다 (설계 docs/design/desktop-app.md §2, D1).

코어(`product/radar`)의 동작은 바꾸지 않는다. 여기서 하는 일:
  Inbox 목록  대기 중인 capture thread(checkpoint) + 검토 대기 별도 파일(`hitl.review_queue`)
  상세       대기 초안 · 저장될 ADR 미리보기(`ops.render`) · 「바뀐 점」(멈출 때의 지문과 지금의 지문 비교 — 읽기만)
  답         `capture_turn2`(approve·edit·reject + stale 답)를 그대로 부른다
  행동 로그   `<data>/events.jsonl` (실사용 지표, 비교 프로토콜 §7)
  지난 알림   행동 로그의 `notification_shown` + 클릭·항목의 지금 상태 — 읽기만, 따로 저장하지 않는다 (HYO-110)
  capture    대화 → `capture_turn1` → 승인 대기 (R9: ADR repo 쓰기 0)
  Decisions  `radar.py list` + ADR 파일 제목 — 읽기만 (R8: status는 화면에서 바꾸지 않는다)
  실행       `detect`·`revisit` 그래프를 그대로 한 번 돌리고 보고서를 요약한다 (`<data>/runs.jsonl`)

안전 (작업 지시서 §5):
  - 모델은 스텁이 기본이다. 실제 모델은 **데모 모드**(`RADAR_APP_MODEL=anthropic` + `RADAR_APP_DEMO=1`, HYO-85)에서만 — 하나만 있으면 거부한다
  - 데모 모드는 ADR repo **사본**(`<data>/adr`, 원본 `RADAR_DEMO_ADR` 기본 `../adr`)에만 쓰고, detect는 `RADAR_DEMO_SNAPSHOT`(pipeline.detect_feed 출력)을 읽는다
  - 내보내기는 늘 로컬(`RADAR_EXPORT=local` — git을 부르지 않고 승인 기록만, `export.LocalExporter`). 물려받은 `RADAR_EXPORT`는 지우고
    `RADAR_DRY_RUN=1`을 강제한다 — git push가 켜질 길이 없다 (`hitl.exporter_for`)
  - 키(.env)는 이 프로세스만 읽는다 (스텁 모드에서는 읽지도 않는다)

실사용 모드 (`RADAR_APP_MODE=real`, HYO-99 3단계 — 레포 주인 결정 10/26): 실제 ADR repo(`RADAR_REAL_ADR`, 기본 `../adr`)에 쓰고 main에 push한다.
  - 시작 검사: ADR repo가 git 작업 사본의 맨 위 · main 브랜치 · origin 있음 · docs/adr와 radar-targets.json 있음 · 데이터 폴더와 안 겹침.
    엔진이 claude-cli면 CLI 바이너리도. 하나라도 틀리면 시작을 거부한다 (데모·RADAR_APP_MODEL과 같이 주면 거부)
  - 엔진: detect·revisit = `RADAR_APP_ENGINE`(기본 claude-cli — 구독 `claude -p`, engines.ClaudeCliRunner) 또는 api. capture는 늘 API(.env 키)
  - ctx 둘: 그래프 실행(detect·revisit)은 dry-run + 로컬 내보내기 — 초안은 작업 사본에만 쓰고 검토 대기로 둔다 (check_scope).
    답·capture는 `RADAR_EXPORT=git`(dry-run 아님) — 승인 = 커밋 1개를 main에 push (hitl-state 결정 8)
  - revisit 직후 `revisit_last_checked`만 바뀐 파일은 승인 없이 커밋·push한다 (`hitl.export_record` — 기록용 칸 밖이 바뀌었으면 거부)
  - 실행 전: radar-snapshots(`RADAR_SNAPSHOT_REPO`, 기본 `../radar-snapshots`)를 pull만 하고 가장 새 `detect/<주차>/`(pipeline.detect_feed 출력)를
    detect 입력으로 쓴다 — 만드는 쪽은 수집 PC(Windows)의 주간 작업이다. ADR repo는 원격을 따라잡는다(ff)

읽기(Inbox·상세·Decisions)는 쓰기 잠금을 기다리지 않는다 — 실제 모델 실행(분 단위) 동안에도 화면이 멈추지 않게. 쓰기는 lock 하나로 줄 세운다.
"""

import asyncio
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "product"))
sys.path.insert(1, REPO)  # pipeline.detect_feed — 실행 요약의 건수 한 줄 (HYO-84)

import devprofiles  # noqa: E402
import engine_status  # noqa: E402
from radar import hitl  # noqa: E402  — radar/__init__이 spike/common을 경로에 올린다
from radar.adr_copy import copy_adr  # noqa: E402
from radar import script as stub_scripts  # noqa: E402
from radar.capture import capture_agent, capture_turn1, capture_turn2, thread_config  # noqa: E402
from radar.detect import detect  # noqa: E402
from radar.revisit import revisit  # noqa: E402
from radar.stub_model import StubChat  # noqa: E402
from common import ops  # noqa: E402
from pipeline.detect_feed import funnel_line, url_key  # noqa: E402
from pipeline import topic_hier  # noqa: E402
from pipeline.importance.card_action import card_action  # noqa: E402
from common.core import SKILL_RADAR, Ctx, RadarError, radar  # noqa: E402
from common.env import MODEL as REAL_MODEL, load_dotenv  # noqa: E402
from common.schemas import AdrDraft, RevisitCondition  # noqa: E402
from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: E402
import yaml  # noqa: E402

STATE_FILE = "capture-checkpoint.sqlite"
META_FILE = "app-meta.json"
EVENTS_FILE = "events.jsonl"
RUNS_FILE = "runs.jsonl"
SHADOW_FILE = "shadow.jsonl"  # API 그림자 기록 (HYO-99 3단계) — 화면에 안 띄운다
TOPICS_SAVED_FILE = "topics-saved.json"  # 저장한 화제 {"items": [...]} — 주차 파일과 따로 둔 복사본
TOPIC_HIER_FEEDBACK_FILE = "topic-hier-feedback.jsonl"  # 화제 부모 제안 응답 (HYO-152) — 추가만 하는 JSONL, 마지막 응답이 센다
BRANCH_ORDER = ("P1-prefix", "P1-shared", "P2")  # 갈래 동률이면 이 순서
WEEK_NAME = re.compile(r"^\d{4}-W\d{2}$")  # 화제 주차 파일 이름 (pipeline.topics의 `<주차>.json`)
CAPTURE_SEED_POSTS = 5  # capture 창에 미리 채우는 글 수
ALERTS_FILE = "alerts.json"  # revisit 충족 알림 — Inbox 항목 (화면 설계 ③). 앱 데이터에만, ADR repo에는 안 쓴다
DEFAULT_STUB = "JHS-014"  # 2턴 스텁 대본 — 2턴에서는 도구 결과 순서만 보고 끝내므로 항목마다 달라도 같다 (stub_policy.capture_step)
UI_EVENTS = ("app_opened", "item_opened", "notification_shown", "notification_clicked", "capture_opened", "decision_opened",
             "topics_opened", "topic_opened")
FLOWS = ("detect", "revisit")
MAX_CONVERSATION = 200_000
OK_OUTCOMES = ("written", "rejected", "discarded", "rejudge", "recovered")
NUMBER_NOTE = "번호는 저장할 때 정해짐"
DEMO_ADR_DEFAULT = os.path.join(os.path.dirname(REPO), "adr")
APP_MODES = ("stub", "real")
ENGINES = ("claude-cli", "api")
SNAPSHOT_REPO_DEFAULT = os.path.join(os.path.dirname(REPO), "radar-snapshots")
DETECT_FEEDS = "detect"  # radar-snapshots/detect/<주차>/ — 수집 PC가 관문 → detect 입력까지 만들어 push한다


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

    def __init__(self, data_dir, *, adr_dir=None, targets=None, model=None, radar_py=SKILL_RADAR, profile=None,
                 demo=None, demo_adr=None, demo_snapshot=None, chat_factory=None, app_mode=None, real_adr=None, snapshot_repo=None,
                 engine=None):
        self.data_dir = os.path.abspath(data_dir)
        # 개발 데이터 프로필 — 폴더를 만든 프로필이 이긴다. 다른 프로필을 달라고 하면 섞이지 않게 거부한다 (devprofiles.py)
        made = devprofiles.read_marker(self.data_dir)
        want = devprofiles.resolve(profile) if profile else made
        if want != made:
            raise ServiceError(500, f"데이터 폴더 {self.data_dir}는 프로필 {made!r}로 만들어졌다 — {want!r}를 쓰려면 다른 폴더를 쓴다")
        self.profile = made
        self.pscript = devprofiles.stub_script(self.profile)  # default는 None (코어 대본)
        self.default_stub = DEFAULT_STUB if self.pscript is None else devprofiles.load_json(self.profile, "profile.json")["default_capture"]
        self.adr_dir = os.path.abspath(adr_dir or os.path.join(self.data_dir, "adr-fixture", "docs", "adr"))
        self.targets = os.path.abspath(targets or os.path.join(self.adr_dir, "..", "..", "radar-targets.json"))
        self.state_path = os.path.join(self.data_dir, STATE_FILE)
        self.meta_path = os.path.join(self.data_dir, META_FILE)
        self.events_path = os.path.join(self.data_dir, EVENTS_FILE)
        self.runs_path = os.path.join(self.data_dir, RUNS_FILE)
        self.alerts_path = os.path.join(self.data_dir, ALERTS_FILE)
        # 레인 C 화제 (HYO-127) — `python -m pipeline.topics run --out <폴더>`가 쓴 `<주차>.json`을 읽기만 한다. 저장한 화제는 따로 (복사본)
        self.topics_dir = os.path.abspath(os.environ.get("RADAR_TOPICS_DIR") or os.path.join(self.data_dir, "topics"))
        self.topics_saved_path = os.path.join(self.data_dir, TOPICS_SAVED_FILE)
        self.topic_feedback_path = os.path.join(self.data_dir, TOPIC_HIER_FEEDBACK_FILE)
        self._hier_cache = {}  # {주차: (파일 mtime, 인덱스, {토픽 id: 제안 계산})} — 주차 파일이 바뀌면 버린다
        self.jobs = None  # jobs.Jobs가 붙는다 (접수된 capture를 Inbox에)
        self.snapshot_dir = os.path.join(self.data_dir, "snapshots")
        self.tmp = os.path.join(self.data_dir, "tmp")
        self.radar_py = radar_py
        want_model = model or os.environ.get("RADAR_APP_MODEL") or "stub"
        demo = (os.environ.get("RADAR_APP_DEMO") == "1") if demo is None else demo
        app_mode = app_mode or os.environ.get("RADAR_APP_MODE") or "stub"
        if app_mode not in APP_MODES:
            raise ServiceError(500, f"RADAR_APP_MODE {app_mode!r} — {list(APP_MODES)}")
        if app_mode == "real" and (demo or model or os.environ.get("RADAR_APP_MODEL")):
            raise ServiceError(500, "실사용 모드는 데모(RADAR_APP_DEMO)·RADAR_APP_MODEL과 같이 쓰지 않는다 — 엔진은 RADAR_APP_ENGINE")
        # 실제 모델은 과금이다 — 데모 플래그와 모델 이름을 둘 다 명시해야 연다. 하나만 있으면 실수로 보고 거부한다 (HYO-85)
        if (want_model != "stub" or demo) and not (want_model == "anthropic" and demo):
            raise ServiceError(500, f"모델 {want_model!r} · 데모 {demo}: 실제 모델은 데모 모드에서만 — RADAR_APP_MODEL=anthropic과 RADAR_APP_DEMO=1을 "
                                    "같이 준다 (아니면 둘 다 비워 스텁)")
        self.mode = "real" if app_mode == "real" else "demo" if demo else "stub"
        self.model_name = "stub" if self.mode == "stub" else REAL_MODEL
        self.flows = ("detect",) if demo else FLOWS  # 데모는 detect만 실제 모델로 — revisit은 데모 입력에 출처가 없어 의미가 없다
        self.chat_factory = chat_factory  # 테스트가 실제 모델 자리에 대역을 꽂는다 (API 호출 없이 데모·실사용 모드 배관 검사)
        self.demo_info = self.real_info = None
        self.shadow = False  # 실사용 + claude-cli면 켜진다 (_setup_real)
        self.shadow_factory = None  # 테스트가 그림자 모델 자리에 대역을 꽂는다
        self.shadow_async = True  # 테스트는 False — 실행 끝에 바로 돈다
        self.shadow_threads = []
        self._shadow_lock = threading.Lock()
        self.login_launcher = subprocess.Popen  # 테스트는 바꿔 끼운다 — 실제 터미널을 열지 않게
        self._engine_cache = None  # (시각, 상태) — 화면이 자주 불러도 claude를 매번 띄우지 않게
        self.engine = None if self.mode == "stub" else "api"
        if demo:
            self._setup_demo(demo_adr or os.environ.get("RADAR_DEMO_ADR") or DEMO_ADR_DEFAULT,
                             demo_snapshot or os.environ.get("RADAR_DEMO_SNAPSHOT"))
        if self.mode == "real":
            self._setup_real(real_adr or os.environ.get("RADAR_REAL_ADR") or DEMO_ADR_DEFAULT,
                             snapshot_repo or os.environ.get("RADAR_SNAPSHOT_REPO") or SNAPSHOT_REPO_DEFAULT,
                             engine or os.environ.get("RADAR_APP_ENGINE") or "claude-cli")
        os.makedirs(self.tmp, exist_ok=True)
        self.lock = threading.RLock()

    def _setup_demo(self, adr_src, snapshot):
        """데모 모드 — ADR 사본(처음 한 번 복사, 있으면 이어 쓴다)과 detect 입력 폴더. 원본 ADR repo는 읽기만 한다."""
        adr_src = os.path.abspath(adr_src)
        if not snapshot or not os.path.exists(os.path.join(snapshot, "feed.json")):
            raise ServiceError(500, f"데모 모드: RADAR_DEMO_SNAPSHOT에 feed.json이 없다 ({snapshot!r}) — python -m pipeline.detect_feed로 만든다")
        if _overlaps(adr_src, self.data_dir):
            raise ServiceError(500, f"데모 모드: 데이터 폴더 {self.data_dir}와 원본 ADR repo {adr_src}가 겹친다 — 사본이 원본을 덮을 수 있다")
        copy_root = os.path.join(self.data_dir, "adr")
        if not os.path.isdir(os.path.join(copy_root, "docs", "adr")):
            if not os.path.isdir(os.path.join(adr_src, "docs", "adr")):
                raise ServiceError(500, f"데모 모드: 원본 ADR repo에 docs/adr가 없다 ({adr_src})")
            os.makedirs(self.data_dir, exist_ok=True)
            copy_adr(adr_src, copy_root)
        self.adr_dir = os.path.join(copy_root, "docs", "adr")
        self.targets = os.path.join(copy_root, "radar-targets.json")
        self.snapshot_dir = os.path.abspath(snapshot)
        self.demo_info = {"adr_source": adr_src, "adr_copy": copy_root, "snapshot": self.snapshot_dir}

    def _setup_real(self, adr_root, snapshot_repo, engine):
        """실사용 모드 — 실제 ADR repo가 맞는지 보고 아니면 시작을 거부한다 (머리말). detect 입력은 실행 때마다 고른다 (`_prepare_real`)."""
        adr_root, snapshot_repo = os.path.abspath(adr_root), os.path.abspath(snapshot_repo)
        errs = []
        if engine not in ENGINES:
            errs.append(f"RADAR_APP_ENGINE {engine!r} — {list(ENGINES)}")
        for rel in (os.path.join("docs", "adr"), "radar-targets.json"):
            if not os.path.exists(os.path.join(adr_root, rel)):
                errs.append(f"ADR repo에 {rel}가 없다")
        top = _git(adr_root, "rev-parse", "--show-toplevel")
        if top is None or not _same_dir(top, adr_root):
            errs.append(f"git 작업 사본의 맨 위가 아니다 (git 맨 위: {top})")
        else:
            branch = _git(adr_root, "rev-parse", "--abbrev-ref", "HEAD")
            if branch != "main":
                errs.append(f"브랜치가 main이 아니다 ({branch}) — 승인은 main에 push한다")
            if not _git(adr_root, "remote", "get-url", "origin"):
                errs.append("origin 원격이 없다")
        if _overlaps(adr_root, self.data_dir):
            errs.append(f"데이터 폴더 {self.data_dir}와 ADR repo가 겹친다")
        claude_bin = None
        if engine == "claude-cli":
            claude_bin = os.environ.get("RADAR_CLAUDE_BIN") or shutil.which("claude")
            if not claude_bin or not (os.path.exists(claude_bin) or shutil.which(claude_bin)):
                errs.append(f"claude CLI를 못 찾았다 ({claude_bin}) — 설치하거나 RADAR_CLAUDE_BIN, 아니면 RADAR_APP_ENGINE=api")
        if errs:
            raise ServiceError(500, f"실사용 모드를 열지 않았다 — ADR repo {adr_root}: " + " · ".join(errs))
        self.engine, self.snapshot_repo = engine, snapshot_repo
        self.adr_dir = os.path.join(adr_root, "docs", "adr")
        self.targets = os.path.join(adr_root, "radar-targets.json")
        self.snapshot_dir = _latest_feed(snapshot_repo) or os.path.join(snapshot_repo, DETECT_FEEDS)
        # API 그림자 (사용자 10/26): 주 엔진이 구독 CLI면 같은 입력을 API로도 돌려 기록만 — 4주 뒤 엔진 비교. RADAR_SHADOW=off로 끈다
        self.shadow = engine == "claude-cli" and os.environ.get("RADAR_SHADOW", "on") != "off"
        self.real_info = {"adr_repo": adr_root, "snapshot_repo": snapshot_repo, "claude_bin": claude_bin, "shadow": self.shadow}

    def _api_key(self):
        return bool(load_dotenv().get("ANTHROPIC_API_KEY"))

    def chat(self, flow, script=None):
        """흐름 하나의 모델. 스텁이면 대본, 데모면 실제 Claude, 실사용이면 detect·revisit은 엔진(claude-cli · api), capture는 API.
        테스트는 chat_factory로 대역을 꽂는다."""
        if self.mode == "stub":
            return StubChat(script=script, flow=flow)
        if self.chat_factory is not None:
            return self.chat_factory(flow)
        if self.mode == "real" and flow != "capture" and self.engine == "claude-cli":
            from radar.engines import ClaudeCliRunner
            return ClaudeCliRunner(model=REAL_MODEL, bin=self.real_info["claude_bin"])
        if not self._api_key():  # anthropic_key()는 SystemExit — 작업 일꾼을 죽이지 않게 먼저 본다
            raise ServiceError(409, f".env에 ANTHROPIC_API_KEY가 없다 — {flow}는 API로 돈다")
        from radar import models
        return models.make_anthropic()

    def health(self):
        h = {"ok": True, "mode": self.mode, "profile": self.profile, "data_dir": self.data_dir, "adr_dir": self.adr_dir,
             "model": self.model_name, "flows": list(self.flows), "engine": self.engine}
        if self.demo_info:
            h["demo"] = {**self.demo_info, "funnel": self._funnel_line()}
        if self.real_info:
            feed = self.snapshot_dir if os.path.exists(os.path.join(self.snapshot_dir, "feed.json")) else None
            h["real"] = {**self.real_info, "snapshot": feed, "week": os.path.basename(feed) if feed else None,
                         "api_key": self._api_key(), "funnel": self._funnel_line()}
        return h

    ENGINE_TTL = 15

    def engine_info(self, refresh=False):
        """엔진 상태 (`GET /engine`) — 지금 detect·revisit을 돌릴 수 있나 · CLI 설치·버전·로그인. 스텁에서도 CLI는 읽는다(읽기만, 사용량 0).
        CLI 상태는 ENGINE_TTL초 동안 다시 쓴다 — refresh면 새로 읽는다 (로그인 창을 닫고 난 뒤)."""
        now = time.time()
        if refresh or self._engine_cache is None or now - self._engine_cache[0] > self.ENGINE_TTL:
            self._engine_cache = (now, engine_status.cli_status(self._claude_bin()))
        cli = self._engine_cache[1]
        api_key = self.mode != "stub" and self._api_key()
        ready, reason = engine_status.readiness(self.engine, cli, api_key)
        return {"mode": self.mode, "engine": self.engine, "ready": ready, "reason": reason, "cli": cli,
                "api_key": api_key, "capture_ready": self.mode == "stub" or api_key,
                "login_command": engine_status.login_command(cli["path"])}

    def engine_login(self):
        """OS 터미널에 `claude auth login --claudeai`를 연다 (`POST /engine/login`). 로그인은 사람이 터미널에서 마친다 — 앱은 코드를 받지 않는다."""
        self._engine_cache = None
        res = engine_status.open_login(self._claude_bin(), launcher=self.login_launcher)
        self.log("engine_login", None, opened=res["opened"])
        return res

    def _claude_bin(self):
        return (self.real_info or {}).get("claude_bin") or engine_status.find_bin()

    def _funnel_line(self):
        try:
            return funnel_line(json.load(open(os.path.join(self.snapshot_dir, "funnel.json"), encoding="utf-8")))
        except (OSError, ValueError):
            return None

    def _gate_why(self):
        """detect 입력을 만든 관문의 「왜 남았나」 (pipeline.detect_feed의 gate.json: url → reason·kept_by). 없으면 {}."""
        try:
            return json.load(open(os.path.join(self.snapshot_dir, "gate.json"), encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    # ── 환경 ─────────────────────────────────────────────────────

    def env(self, export=None):
        """실사용 모드의 기본(export=None)은 git 내보내기 — 답·capture. 그래프 실행은 export=False (머리말 「ctx 둘」). 스텁·데모는 늘 로컬."""
        git_export = self.mode == "real" and export is not False
        env = {k: v for k, v in os.environ.items() if k not in ("RADAR_EXPORT", "RADAR_DRY_RUN")}  # 내보내기 스위치는 물려받지 않는다
        env.update({
            "RADAR_ADR_DIR": self.adr_dir, "RADAR_TARGETS": self.targets,
            # 로컬: 승인 기록만 — git을 부르지 않는다. detect 초안을 Inbox에서 답할 수 있게 (HYO-85)
            # dry-run이면 git 내보내기가 no-op — RADAR_EXPORT=git이 어디서 들어와도 git을 안 부른다
            "RADAR_EXPORT": "git" if git_export else "local", "RADAR_DRY_RUN": "0" if git_export else "1",
            "RADAR_SNAPSHOT_DIR": self.snapshot_dir,  # detect·revisit는 스냅숏 모드만 (check_scope) — 웹에 나가지 않는다
            "RADAR_SERVED_LOG": os.path.join(self.data_dir, "served.log"),
            "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false",
            "PYTHONIOENCODING": "utf-8", "TEMP": self.tmp, "TMP": self.tmp, "TMPDIR": self.tmp,
        })
        # 거부한 소식을 detect가 다시 올리나 (HYO-142) — 실사용은 늘 안 올린다. 스텁·데모는 같은 초안을 다시 띄워 알림을 시험하려고 올린다(바꿀 수 있다)
        if self.mode == "real":
            env["RADAR_DETECT_RESURFACE"] = "0"
        else:
            env.setdefault("RADAR_DETECT_RESURFACE", "1")
        return env

    def ctx(self, export=None):
        c = Ctx.from_env(self.env(export), radar_py=self.radar_py)
        c.now = now_iso()
        return c

    def _usage(self, ctx):
        """이 작업의 모델 사용량 (HYO-98) — ctx.events의 llm_call 줄(product/radar/llm_log)을 모은다. ctx는 작업마다 새로 만든다.
        비용은 모델별 단가표(common.env.PRICES)로 — 모르는 모델이 섞이면 None(0이 아니다). 스텁은 추정 토큰·비용 0. 부른 적 없으면 None."""
        calls = [e for e in ctx.events if e.get("type") == "llm_call"]
        if not calls:
            return None
        costs = [c.get("cost_usd") for c in calls]
        billing = {"stub" if self.mode == "stub" else c.get("billing") or "api" for c in calls}  # 구독 CLI는 subscription (engines.ClaudeCliRunner)
        equiv = [c["cost_usd_equiv"] for c in calls if c.get("cost_usd_equiv") is not None]
        return {"model": ", ".join(sorted({c.get("model") or self.model_name for c in calls})), "model_calls": len(calls),
                "input_tokens": sum(c.get("input_tokens") or 0 for c in calls), "output_tokens": sum(c.get("output_tokens") or 0 for c in calls),
                "cost_usd": None if any(x is None for x in costs) else round(sum(costs), 5), "estimated": self.mode == "stub",
                "billing": billing.pop() if len(billing) == 1 else "mixed", "cost_usd_equiv": round(sum(equiv), 5) if equiv else None}

    def model(self, item_id=None):
        sid = (self._meta().get("items", {}).get(item_id) or {}).get("stub") if item_id else None
        return self.chat("capture", self.capture_script(sid or self.default_stub))

    def capture_script(self, sid):
        """capture 스텁 대본 — 프로필 것이 있으면 그것, 없으면 코어 대본 (product/radar/stub_script.json)."""
        if self.pscript is None:
            return stub_scripts.capture_script(sid)
        return {**self.pscript["capture"][sid], "force_propose": False}

    def _stubs(self):
        return None if self.pscript is None else self.pscript["capture"]

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

    def _events(self):
        """행동 로그 → [(줄 번호, 기록)]. 깨진 줄은 건너뛴다 (쓰는 중에 읽힌 마지막 줄 등)."""
        try:
            lines = open(self.events_path, encoding="utf-8").read().splitlines()
        except OSError:
            return []
        out = []
        for i, line in enumerate(lines):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                out.append((i, rec))
        return out

    # ── 지난 알림 (HYO-110) — notification_shown을 읽기만 한다. 따로 저장하지 않는다 ─────

    ANSWERS = ("approve", "edit", "reject", "ack")
    ADR_KEPT = ("written", "acked")  # 답한 뒤에도 ADR 파일이 남는 결과 — 승인으로 씀 · revisit 확인함

    def notifications(self, limit=100):
        """지난 OS 알림 — notification_shown 한 줄이 한 통. 클릭(notification_clicked) · 항목 열어 봄(item_opened) · 항목의 지금 상태를 붙인다.
        클릭 짝: detail.nid가 있으면 같은 nid, 없으면 같은 (item, kind)의 바로 앞 알림. 최근 것 먼저."""
        evs = self._events()
        shown, clicks, opened, answers = [], {}, {}, {}
        for i, e in evs:
            ev, item, d = e.get("event"), e.get("item"), e.get("detail") or {}
            if ev == "notification_shown":
                shown.append((i, e))
            elif ev == "notification_clicked":
                key = ("nid", d["nid"]) if d.get("nid") else ("ik", item, d.get("kind"))
                for j, s in reversed(shown):  # 이 클릭보다 앞선 알림 중 가장 가까운 것
                    sd = s.get("detail") or {}
                    skey = ("nid", sd.get("nid")) if d.get("nid") else ("ik", s.get("item"), sd.get("kind"))
                    if skey == key:
                        clicks.setdefault(j, e.get("at"))
                        break
            elif ev == "item_opened" and item:
                opened.setdefault(item, []).append(i)
            elif ev in self.ANSWERS and item:
                answers[item] = e  # 마지막 답이 이긴다
        inbox = {r["id"]: r for r in self.inbox()}
        out = []
        for i, e in reversed(shown[-limit:] if limit else shown):
            d = e.get("detail") or {}
            ids = [x for x in d.get("items") or [] if isinstance(x, str)] or ([e["item"]] if e.get("item") else [])
            out.append({"id": f"n{i}", "at": e.get("at"), "kind": d.get("kind"), "title": d.get("title"), "body": d.get("body"),
                        "clicked": i in clicks, "clicked_at": clicks.get(i),
                        "items": [self._notification_item(x, inbox, answers, any(k > i for k in opened.get(x, []))) for x in ids]})
        return out

    def _notification_item(self, item_id, inbox, answers, was_opened):
        row, ans = inbox.get(item_id), answers.get(item_id)
        it = {"id": item_id, "status": "gone", "kind": None, "title": None, "state": None, "answer": None, "outcome": None,
              "answered_at": None, "adr": None, "new_item": None, "opened": was_opened}
        if row is not None:  # 답했어도 stale·edit_errors 등으로 아직 남아 있으면 Inbox가 이긴다
            it.update(status="inbox", kind=row["kind"], title=row["title"], state=row["state"], adr=row.get("adr"))
        elif ans is not None:
            ad = ans.get("detail") or {}
            outcome = ad.get("outcome") or ("acked" if ans["event"] == "ack" else None)
            # 남아 있는 ADR만 — 거부·버림·다시 판정은 초안을 지웠으니 번호를 넘기지 않는다 (화면이 없는 ADR로 보내지 않게)
            it.update(status="answered", answer=ans["event"], outcome=outcome, answered_at=ans.get("at"),
                      adr=ad.get("adr") if outcome in self.ADR_KEPT else None, new_item=ad.get("new_item"))
        return it

    # ── Inbox ────────────────────────────────────────────────────

    def _captures(self, ctx):
        """대기 중인 capture thread → (id, 초안, 대기 시작, 멈출 때 지문, 범위). checkpoint만 읽는다."""
        if not os.path.exists(self.state_path):
            return []
        store, meta, out = hitl.StateStore(self.state_path), self._meta().get("items", {}), []
        with SqliteSaver.from_conn_string(self.state_path) as cp:
            agent = capture_agent(StubChat(script=self.capture_script(self.default_stub), flow="capture"), cp)  # 상태 읽기만 — 모델을 부르지 않는다
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
               "state": "stale" if changes else "awaiting_approval", "actions": ["approve", "edit", "reject"], "why": None, "action": None}
        return row, changes

    def _item_usage(self, item_id):
        """capture 항목을 만드는 데 든 사용량 (1턴, 앱 메타) — 메타가 없으면 None."""
        return (self._meta().get("items", {}).get(item_id) or {}).get("usage")

    def _review_row(self, ctx, q):
        """검토 대기 별도 파일(detect 초안 · 내보내기 보류). 로컬 내보내기라 승인·수정·거부를 받는다 (HYO-85).
        why = 이 소식이 왜 왔나 — detect 입력을 만든 관문의 판정(gate.json, 예 `anthropic L5 × deprecation`). 근거 url로 잇는다."""
        item = hitl.read_item(ctx, q["item"]) or {}
        wi = item.get("write_intent") or {}
        path = os.path.join(hitl.repo_root(ctx), wi["path"]) if wi.get("path") else None
        text = open(path, encoding="utf-8").read() if path and os.path.exists(path) else ""
        m = _TITLE.search(text)
        scope = item.get("scope") or {}
        since = q["created"]  # 처음 도착 시각 — export.since는 상태가 바뀔 때마다 새로 쓰여(review → stale) 대기 일수로 쓰지 않는다 (HYO-144)
        row = {"id": q["item"], "kind": q["kind"], "title": m.group(1).strip() if m else q["item"],
               "technology": scope.get("technology"), "affected": scope.get("affected") or [], "since": since,
               "waiting_days": _days(since), "state": q["export"].get("state"),
               "actions": ["approve", "edit", "reject"] if hitl.exporter_for(ctx).active else [], "why": self._why(text), "action": self._card_action(text)}
        return row, text

    def _why(self, text):
        gate = self._gate_why()
        reasons = []
        for e in _front_matter(text).get("evidence") or []:
            g = gate.get(e.get("url")) if isinstance(e, dict) else None
            if g and g.get("reason") and g["reason"] not in reasons:
                reasons.append(g["reason"])
        return "; ".join(reasons) or None

    def _item_body(self, url):
        """스냅숏에서 항목 전문 (index.json: url_key → file). 없으면 빈 문자열."""
        try:
            idx = json.load(open(os.path.join(self.snapshot_dir, "index.json"), encoding="utf-8"))
            f = (idx.get(url_key(url)) or {}).get("file")
            return open(os.path.join(self.snapshot_dir, f), encoding="utf-8").read() if f else ""
        except (OSError, ValueError, AttributeError):
            return ""

    def _card_action(self, text):
        """남김 카드 표시 (HYO-132) — 관문 kept_by의 변화 종류로 코드가 정한다 (pipeline.importance.card_action). 근거 url마다 구해
        강제 이전 > 선택 실험, 강제끼리는 마감일 있는 것(가장 이른 것) 먼저. 관문 항목이 없으면 None."""
        gate, today = self._gate_why(), dt.datetime.fromisoformat(now_iso()).date()
        acts = []
        for e in _front_matter(text).get("evidence") or []:
            url = e.get("url") if isinstance(e, dict) else None
            g = gate.get(url) if url else None
            if not isinstance(g, dict):
                continue
            a = card_action(g.get("kept_by") or [], self._item_body(url), today)
            if a:
                acts.append(a)
        forced = [a for a in acts if a["kind"] == "forced_migration"]
        if forced:
            return min(forced, key=lambda a: (a["deadline"] is None, a["deadline"] or ""))
        return acts[0] if acts else None

    def inbox(self):
        ctx = self.ctx()  # 읽기 — 쓰기 잠금을 기다리지 않는다 (머리말)
        rows = [self._capture_row(ctx, c)[0] for c in self._captures(ctx)]
        rows += [self._review_row(ctx, q)[0] for q in hitl.review_queue(ctx)]
        rows += [self._pending_row(j) for j in (self.jobs.pending() if self.jobs else [])]
        rows += [self._alert_row(a) for a in self._alerts() if not a.get("acked")]
        return sorted(rows, key=lambda r: str(r["since"]))

    # ── 접수된 capture (화면 설계 ①) · revisit 충족 알림 (화면 설계 ③) — 답은 「확인함」뿐 ──────

    PENDING_MSG = {"queued": "접수했어요 — 차례를 기다리고 있어요", "running": "처리하고 있어요", "error": None, "linked": None, "no_draft": None}

    def _pending_row(self, j):
        st = j["pending_state"]
        done = st in ("error", "linked", "no_draft")
        return {"id": j["id"], "kind": "pending", "title": j.get("preview") or "capture", "technology": None, "affected": [],
                "since": j["created"], "waiting_days": _days(j["created"]), "state": st, "actions": ["ack"] if done else [],
                "why": None, "action": None, "adr": None, "message": self.PENDING_MSG.get(st) or j.get("error") or j.get("message")}

    def _alerts(self):
        try:
            return json.load(open(self.alerts_path, encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _save_alerts(self, alerts):
        tmp = self.alerts_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(alerts, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.alerts_path)

    def _record_alerts(self, alerts, at):
        """revisit 실행의 충족 알림 → Inbox 항목. 같은 (ADR, 조건)이 아직 안 치운 채 있으면 새로 만들지 않고 시각만 갱신한다."""
        if not alerts:
            return []
        rows = {d["id"]: d for d in self._safe_decisions()}
        cur, new_ids = self._alerts(), []
        for a in alerts:
            open_one = next((x for x in cur if not x.get("acked") and x["adr"] == a["adr"] and x["condition"] == a["condition"]), None)
            if open_one:
                open_one["last_seen"] = at
                continue
            d = rows.get(a["adr"]) or {}
            aid = f"revisit-{a['adr']}-{a['condition']}-{re.sub(r'[^0-9]', '', at)[:14]}"
            cur.append({"id": aid, "adr": a["adr"], "condition": a["condition"], "result": a.get("result") or "met", "message": a.get("message") or "",
                        "title": d.get("title") or f"ADR {a['adr']}", "technology": d.get("technology"), "affected": d.get("affected") or [],
                        "since": at, "last_seen": at, "acked": False})
            new_ids.append(aid)
            self.log("item_created", aid, kind="revisit", adr=a["adr"], condition=a["condition"])
        self._save_alerts(cur)
        return new_ids

    def _safe_decisions(self):
        try:
            return self.decisions()
        except ServiceError:
            return []

    def _alert_row(self, a):
        return {"id": a["id"], "kind": "revisit", "title": f"재검토 조건 충족 — {a['title']}", "technology": a.get("technology"),
                "affected": a.get("affected") or [], "since": a["since"], "waiting_days": _days(a["since"]),
                "state": a["result"] if a["result"] in ("met", "not_met", "undetermined") else "met", "actions": ["ack"],
                "why": None, "action": None, "adr": a["adr"], "message": f"{a['condition']}: {a['message']}" if a.get("message") else a["condition"]}

    def ack(self, item_id):
        """「확인함」 — Inbox에서 치운다. ADR 파일·status는 건드리지 않는다 (R8). 승인·거부할 초안(capture·detect)에는 쓰지 않는다."""
        with self.lock:
            alerts = self._alerts()
            a = next((x for x in alerts if x["id"] == item_id and not x.get("acked")), None)
            if a is not None:
                a.update(acked=True, acked_at=now_iso())
                self._save_alerts(alerts)
                res = {"outcome": "acked", "message": f"확인했어요 — ADR {a['adr']}은 그대로예요 (status는 ADR 파일에서 사람이 바꿔요)", "adr": a["adr"]}
            elif self.jobs and any(j["id"] == item_id for j in self.jobs.pending()):
                self.jobs.ack(item_id)
                res = {"outcome": "acked", "message": "확인했어요 — 접수 기록을 치웠어요"}
            elif any(r["id"] == item_id for r in self.inbox()):
                raise ServiceError(409, f"{item_id}: 초안은 승인·수정·거부로 답한다 — 확인함은 접수·재검토 알림만")
            else:
                raise ServiceError(404, f"{item_id}: 대기 항목이 아니다")
            self.log("ack", item_id, **({"adr": res["adr"]} if res.get("adr") else {}))
            return res

    def detail(self, item_id):
        ctx = self.ctx()  # 읽기 — 쓰기 잠금을 기다리지 않는다
        for c in self._captures(ctx):
            if c["id"] == item_id:
                row, changes = self._capture_row(ctx, c)
                md = ops.render(AdrDraft.model_validate(c["draft"]), ctx.now)
                return {**row, "draft": c["draft"], "markdown": md, "changes": changes, "number_note": NUMBER_NOTE,
                        "usage": self._item_usage(item_id), "affected_errors": hitl.affected_errors(ctx, c["draft"])}
        for q in hitl.review_queue(ctx):
            if q["item"] == item_id:
                row, text = self._review_row(ctx, q)
                d = _parse_adr(text)
                out = {**row, "draft": d, "markdown": text, "changes": (q["export"].get("changes") or []),
                       "number_note": f"ADR {q.get('adr')} (로컬 초안)", "affected_errors": hitl.affected_errors(ctx, d) if d else []}
                if not row["actions"]:
                    out["note"] = "내보내기가 꺼져 있어서 답할 수 없어요"
                return out
        for r in self.inbox():
            if r["id"] == item_id and r["kind"] in ("pending", "revisit"):
                md = ""
                if r["kind"] == "revisit":
                    try:
                        md = self.decision(r["adr"])["markdown"]
                    except ServiceError:
                        md = ""
                return {**r, "draft": None, "markdown": md, "changes": [], "number_note": f"ADR {r['adr']}" if r["adr"] else ""}
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
                q = next((q for q in hitl.review_queue(ctx) if q["item"] == item_id), None)
                if q is None:
                    if any(r["id"] == item_id for r in self.inbox()):
                        raise ServiceError(409, f"{item_id}: 접수·재검토 알림은 「확인함」(ack)으로 답한다")
                    raise ServiceError(404, f"{item_id}: 대기 항목이 아니다")
                return self._done(item_id, decision, stale_answer, self._review_answer(ctx, q, decision, stale_answer, draft))
            try:
                rep, _ = capture_turn2(ctx, self.model(item_id), self.state_path, decision, ctx.now, self.model_name,
                                       item_id=item_id, edited=draft, stale_answer=stale_answer)
            except hitl.HitlError as e:
                return self._done(item_id, decision, stale_answer, {"outcome": "error", "message": str(e)})
            res = {**self._outcome(ctx, item_id, decision, rep), "usage": self._usage(ctx)}
            if res["outcome"] == "rejudge":
                res = self._rejudge(item_id, res)
            return self._done(item_id, decision, stale_answer, res)

    def _review_answer(self, ctx, q, decision, stale_answer, draft):
        """detect 초안(검토 대기)에 답한다 — 코어 `hitl.review_item` 그대로. 로컬 내보내기라 승인은 사본에 남고 git은 안 부른다.
        edit = 쓰기 전 검사(`check_edit`) → 같은 번호로 다시 쓰기(`rewrite_adr`, 사람이 고친 파일이면 덮지 않음) → 승인."""
        item_id, adr = q["item"], q.get("adr")
        try:
            if decision == "edit":
                d, errs = hitl.check_edit(ctx, draft)
                if errs:
                    return {"outcome": "edit_errors", "message": "고친 초안이 검사를 통과하지 못했어요 — 아무것도 쓰지 않았어요", "errors": errs}
                werrs = hitl.rewrite_adr(ctx, item_id, adr, d)
                if werrs and str(werrs[0]).startswith("blocked"):
                    return {"outcome": "blocked", "message": werrs[0]}
                decision = "approve"
            r = hitl.review_item(ctx, item_id, decision, stale_answer=stale_answer)
        except hitl.HitlError as e:
            return {"outcome": "error", "message": str(e)}
        st, oc = r.get("state"), r.get("outcome")
        if oc == "reject":
            return {"outcome": "rejected", "message": f"거부했어요 — ADR {adr} 초안을 지웠어요", "adr": adr}
        if oc == "discard":
            return {"outcome": "discarded", "message": f"버렸어요 — ADR {adr} 초안을 지웠어요", "adr": adr}
        if oc == "rejudge":
            return {"outcome": "rejudge", "message": f"ADR {adr} 초안을 지웠어요 — 다시 판정하려면 detect를 다시 돌려 주세요", "adr": adr}
        if st == "stale":
            return {"outcome": "stale", "message": "검토하는 동안 ADR repo가 바뀌었어요 — 계속·다시 판정·버림 중에 골라 주세요",
                    "changes": r.get("changes") or [], "answers": list(hitl.STALE_ANSWERS)}
        if st == "exported":
            wi = (hitl.read_item(ctx, item_id) or {}).get("write_intent") or {}
            errs = wi.get("validate_errors") or []
            return {"outcome": "written", "message": f"ADR {adr} 승인했어요 (proposed로 사본에 남았어요)" + (f" — validate 오류 {len(errs)}개" if errs else ""),
                    "adr": adr, "path": wi.get("path"), "validate_errors": errs}
        return {"outcome": "blocked", "message": f"막혔어요: {r.get('reason') or st}", "blocked": r}

    def _done(self, item_id, decision, stale_answer, res):
        detail = {"outcome": res["outcome"]}
        if stale_answer:
            detail["stale_answer"] = stale_answer
        for k in ("adr", "errors", "changes", "new_item", "usage"):
            if res.get(k):
                detail[k] = res[k]
        self.log(decision, item_id, **detail)
        return res

    def _outcome(self, ctx, item_id, decision, rep):
        h = rep.get("hitl") or {}
        if h.get("edit_errors"):
            return {"outcome": "edit_errors", "message": rep.get("note") or "고친 초안이 검사를 통과하지 못했어요", "errors": h["edit_errors"]}
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
            return {"outcome": "rejected", "message": "거부했어요 — 아무것도 쓰지 않았어요"}
        wi = (hitl.read_item(ctx, item_id) or {}).get("write_intent") or {}
        if wi.get("state") == "done":
            errs = wi.get("validate_errors") or []
            msg = f"ADR {wi['adr']} 저장했어요" + (f" — validate 오류 {len(errs)}개 (파일은 남았어요)" if errs else "")
            return {"outcome": "written", "message": msg, "adr": wi["adr"], "path": wi["path"], "validate_errors": errs}
        return {"outcome": "error", "message": f"예상하지 못한 결과예요: {rep.get('note')}"}

    def _rejudge(self, item_id, res):
        """「다시 판정」 — 코어는 항목을 닫고 호출자에게 capture_turn1을 다시 부르라고 한다. 원래 대화가 있으면 그렇게 한다."""
        m = self._meta().get("items", {}).get(item_id) or {}
        if not m.get("conversation"):
            return {**res, "message": "다시 판정하려면 대화를 다시 넣어 주세요 (원래 대화가 앱에 없어요)"}
        new_id, _ = self.capture(m["conversation"], stub=m.get("stub"), repo=m.get("repo"))
        return {**res, "message": f"바뀐 ADR repo로 다시 판정했어요 → {new_id}", "new_item": new_id}

    # ── capture (1턴) ────────────────────────────────────────────

    def capture(self, conversation, *, stub=None, since=None, repo=None):
        """대화 → capture 1턴 → 승인 대기에서 멈춘다 (R9: ADR repo 쓰기 0). (item_id 또는 None, 보고서)."""
        with self.lock:
            ctx = self.ctx()
            model = self.chat("capture", self.capture_script(stub or self.default_stub))
            rep, _ = asyncio.run(capture_turn1(ctx, model, conversation, ctx.now, self.state_path, self.model_name, repo=repo))
            usage = self._usage(ctx)
            item_id = rep.get("item_id") if rep.get("awaiting_approval") else None
            if item_id:
                self._put_meta(item_id, since=since or ctx.now, conversation=conversation, stub=stub or self.default_stub, usage=usage, repo=repo)
                self.log("item_created", item_id, kind="capture")
            return item_id, {**rep, "usage": usage}

    @staticmethod
    def check_conversation(conversation):
        if not isinstance(conversation, str) or not conversation.strip():
            raise ServiceError(400, "대화가 비었다")
        if len(conversation) > MAX_CONVERSATION:
            raise ServiceError(413, f"대화가 너무 길다 ({len(conversation)}자 > {MAX_CONVERSATION})")

    def target_repos(self):
        """레이더 대상 repo (radar-targets.json) — 빠른 capture의 repo 드롭다운 (HYO-95)."""
        return sorted(self.ctx().targets)

    def check_repo(self, repo):
        """빈 값은 「자동」(None). 대상 밖이면 400 — 화면은 GET /targets에서만 고른다."""
        if repo is None or not str(repo).strip():
            return None
        if repo not in self.target_repos():
            raise ServiceError(400, f"레이더 대상이 아닌 repo {repo!r} — {self.target_repos()}")
        return repo

    def submit_capture(self, conversation, repo=None, topic=None):
        """화면의 빠른 capture 창 — 대화 → 1턴. 스텁 대본은 대화에 나온 기술로 고른다 (`pick_stub`). 데모면 실제 모델이 대화를 읽는다."""
        self.check_conversation(conversation)
        repo = self.check_repo(repo)
        stub, matched = pick_stub(conversation, self._stubs(), self.default_stub) if self.mode == "stub" else (None, True)
        item_id, rep = self.capture(conversation, stub=stub, repo=repo)
        tech = self.capture_script(self.default_stub)["match"]["technology"]
        note = "" if matched else f" — 스텁: 대화와 맞는 대본이 없어서 기본 대본({tech})으로 만들었어요 (내용은 대화와 상관없어요)"
        if item_id:
            res = {"outcome": "awaiting", "item": item_id, "message": "Inbox에 추가했어요 — 승인을 기다리고 있어요" + note, "link_candidates": []}
        elif rep.get("link_candidates"):
            res = {"outcome": "linked", "item": None, "link_candidates": list(rep["link_candidates"]),
                   "message": "이미 같은 결정이 있어요: " + ", ".join(rep["link_candidates"]) + " — 새 초안을 만들지 않았어요"}
        else:
            res = {"outcome": "no_draft", "item": None, "link_candidates": [], "message": rep.get("note") or "초안이 나오지 않았어요"}
        res["usage"] = rep.get("usage")
        self.log("capture_submitted", res["item"], outcome=res["outcome"], chars=len(conversation), stub=stub, usage=res["usage"], repo=repo,
                 **({"topic": {"id": topic["id"], "week": topic["week"]}} if topic else {}),
                 **({"link_candidates": res["link_candidates"]} if res["link_candidates"] else {}))
        return res

    # ── 화제 (레인 C, HYO-127) ───────────────────────────────────

    def _topic_weeks(self):
        """화제 폴더의 주차 이름 — 새 것 먼저. 주차 이름이 아닌 파일은 무시한다."""
        try:
            names = os.listdir(self.topics_dir)
        except OSError:
            return []
        return sorted((n[:-5] for n in names if n.endswith(".json") and WEEK_NAME.match(n[:-5])), reverse=True)

    def _topic_file(self, week):
        if not WEEK_NAME.match(week or "") or week not in self._topic_weeks():
            raise ServiceError(404, f"화제 주차 {week!r}가 없다 — {self._topic_weeks()}")
        try:
            return json.load(open(os.path.join(self.topics_dir, week + ".json"), encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ServiceError(500, f"화제 파일 {week}.json을 못 읽었다: {e}")

    def _saved_topics(self):
        try:
            return json.load(open(self.topics_saved_path, encoding="utf-8")).get("items") or []
        except (OSError, ValueError):
            return []

    def _put_saved_topics(self, items):
        tmp = self.topics_saved_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"items": items}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.topics_saved_path)

    @staticmethod
    def _topic_posts(t):
        return [{"source": it["source"], "title": it.get("title") or "", "url": it.get("url") or "", "signal": it.get("signal") or ""}
                for it in t.get("items") or []]

    @staticmethod
    def _capture_seed(week, title, posts):
        """화제 카드에서 capture 창을 열 때 미리 채우는 글 — 결정 질문과 대화는 사람이 이어 쓴다."""
        lines = [f"화제 ({week}): {title}"]
        lines += [f"- {p['signal'] + ' · ' if p['signal'] else ''}{p['title']} {p['url']}".rstrip() for p in posts[:CAPTURE_SEED_POSTS]]
        return "\n".join(lines) + "\n\n결정 질문: "

    def topics(self, week=None):
        """주차의 상위 k 토픽 (k는 파일이 정한다). 파일이 하나도 없으면 빈 목록 — 오류가 아니다."""
        weeks = self._topic_weeks()
        if not weeks:
            return {"week": None, "weeks": [], "method": None, "k": 0, "counts": None, "items": []}
        week = week or weeks[0]
        f = self._topic_file(week)
        k = int(f.get("k") or 0)
        saved = {x["id"] for x in self._saved_topics()}
        fb = self._hier_feedback(week)
        by_id = {t["id"]: t for t in f.get("topics") or []}
        rows = []
        for t in (f.get("topics") or [])[:k]:
            posts = self._topic_posts(t)
            rows.append({"id": t["id"], "week": week, "rank": t["rank"], "section": t["section"], "title": t["title"], "url": t["url"],
                         "n_sources": t["n_sources"], "sources": t["sources"], "n_items": t["n_items"],
                         "signal": posts[0]["signal"] if posts else "", "posts": posts, "saved": t["id"] in saved,
                         "capture_seed": self._capture_seed(week, t["title"], posts),
                         "parent_suggestion": self._parent_suggestion_view(week, f, t["id"], fb),
                         "parent_manual": self._parent_manual_view(by_id, fb["manual"].get(t["id"]))})
        return {"week": week, "weeks": weeks, "method": f.get("method"), "k": k, "counts": f.get("counts"), "items": rows}

    # ── 화제 계층 제안 모드 + 피드백 루프 (HYO-152, 등록: eval/topics/hier-registration.md v3) ──

    def _hier_feedback(self, week):
        """그 주의 응답 — 마지막 것만. {"answer": {(자식, 부모): accept|reject}, "manual": {자식: 부모 화제 id}}.
        직접 묶기는 자식마다 마지막 manual/manual_clear가 이긴다 (clear면 없음)."""
        answer, manual = {}, {}
        try:
            lines = open(self.topic_feedback_path, encoding="utf-8").read().splitlines()
        except OSError:
            lines = []
        for ln in lines:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if r.get("week") != week:
                continue
            c, p, v = r.get("child_topic"), r.get("parent_topic"), r.get("verdict")
            if v in ("accept", "reject"):
                answer[(c, p)] = v
            elif v == "manual":
                manual[c] = p
            elif v == "manual_clear":
                manual.pop(c, None)
        return {"answer": answer, "manual": manual}

    def _append_hier_feedback(self, rec):
        """호출자가 self.lock을 잡고 있어야 한다."""
        with open(self.topic_feedback_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    @staticmethod
    def _hier_flags(it):
        """(부모가 될 수 있나, 자식이 될 수 있나) — 규칙이 발화하려면 부모 쪽엔 모델 이름이, 자식 쪽엔 양자화 표시 또는 「쓴다」 동사가 있어야 한다."""
        nm = topic_hier.model_name(it)
        child = bool((nm and topic_hier.MARKERS & set(topic_hier.tokens(nm))) or topic_hier.USE_VERB.search(it.get("title") or ""))
        return bool(nm), child

    def _hier_cache_entry(self, week, f):
        """주차 파일 mtime으로 캐시 — 화제가 수천 개여도 짝 검사는 발화할 수 있는 글끼리만 한다."""
        try:
            mt = os.path.getmtime(os.path.join(self.topics_dir, week + ".json"))
        except OSError:
            mt = None
        hit = self._hier_cache.get(week)
        if hit and hit[0] == mt:
            return hit
        named, childish = [], []
        for ti, t in enumerate(f.get("topics") or []):
            for it in t.get("items") or []:
                is_named, is_child = self._hier_flags(it)
                if is_named:
                    named.append((ti, it))
                if is_child:
                    childish.append((ti, it))
        hit = (mt, {"named": named, "childish": childish}, {})
        self._hier_cache[week] = hit
        return hit

    def _compute_parent_suggestion(self, week, f, topic_id):
        """topic_id의 부모 후보 계산 — 없으면 None. 응답(verdict)은 섞지 않는다 (캐시되는 값)."""
        _, idx, memo = self._hier_cache_entry(week, f)
        if topic_id in memo:
            return memo[topic_id]
        topics = f.get("topics") or []
        ti = next((i for i, t in enumerate(topics) if t["id"] == topic_id), None)
        res = None
        if ti is not None:
            res = self._suggest(topics, ti, idx)
        memo[topic_id] = res
        return res

    @staticmethod
    def _suggest(topics, ti, idx):
        mine = topics[ti].get("items") or []
        flags = [(it, *RadarService._hier_flags(it)) for it in mine]
        # 후보 짝 (내 글, 다른 화제 글) — 발화할 수 있는 쪽이 짝의 어느 한쪽에 있는 것만
        pairs = {}
        for pj, pit in idx["named"]:
            if pj != ti:
                for it, _, is_child in flags:
                    if is_child:
                        pairs[(it["id"], pit["id"], pj)] = (it, pit)
        for pj, pit in idx["childish"]:
            if pj != ti:
                for it, is_named, _ in flags:
                    if is_named:
                        pairs[(it["id"], pit["id"], pj)] = (it, pit)
        # 내 화제 안에 부모 글과 이름이 같은 글이 있으면 그 화제는 부모 자체를 담고 있다 — 하위가 아니다 (등록 v3 변경 2026-10-28)
        my_names = {topic_hier.squash(n) for n in (topic_hier.model_name(it) for it in mine) if n}
        fires = {}  # 부모 화제 index → {"fwd": [(자식 글, 부모 글, 갈래)], "rev": 거꾸로 발화했거나 부모를 담은 짝 수}
        for (cid, pid, pj), (ci, pit) in pairs.items():
            side, br = topic_hier.parent_branch(ci, pit)
            if side is None:
                continue
            d = fires.setdefault(pj, {"fwd": [], "rev": 0})
            if side == "b" and topic_hier.squash(topic_hier.model_name(pit)) in my_names:
                d["rev"] += 1
            elif side == "b":
                d["fwd"].append((cid, pid, br))
            else:
                d["rev"] += 1
        best = None
        for pj, d in fires.items():
            if d["rev"] or not d["fwd"]:
                continue  # 양방향 발화는 모르는 것으로 두고 후보에서 뺀다
            key = (-len(d["fwd"]), topics[pj]["rank"])
            if best is None or key < best[0]:
                best = (key, pj, d["fwd"])
        if best is None:
            return None
        _, pj, fwd = best
        cnt = {b: sum(1 for x in fwd if x[2] == b) for b in BRANCH_ORDER}
        rule = max(BRANCH_ORDER, key=lambda b: (cnt[b], -BRANCH_ORDER.index(b)))
        pt = topics[pj]
        order = {it["id"]: n for n, it in enumerate(mine)}
        return {"topic_id": pt["id"], "title": pt["title"], "url": pt["url"], "rank": pt["rank"], "rule": rule,
                "pairs": [[c, p] for c, p, _ in sorted(fwd, key=lambda x: (order.get(x[0], 0), x[1]))]}

    def _parent_suggestion_view(self, week, f, topic_id, fb):
        sug = self._compute_parent_suggestion(week, f, topic_id)
        if sug is None:
            return None
        return {**sug, "verdict": fb["answer"].get((topic_id, sug["topic_id"]))}

    @staticmethod
    def _parent_manual_view(by_id, parent_id):
        t = by_id.get(parent_id) if parent_id else None
        return {"topic_id": t["id"], "title": t["title"]} if t else None

    def topic_parent_feedback(self, topic_id, week, parent_id, verdict):
        """부모 제안에 맞음(accept)·아님(reject)로 답한다. 지금 계산되는 제안에만 답할 수 있고, 짝·갈래는 서버가 계산한 것을 적는다."""
        f = self._topic_file(week)
        if not any(x["id"] == topic_id for x in f.get("topics") or []):
            raise ServiceError(404, f"{week}에 화제 {topic_id}가 없다")
        sug = self._compute_parent_suggestion(week, f, topic_id)
        if sug is None or sug["topic_id"] != parent_id:
            raise ServiceError(404, f"화제 {topic_id}에 부모 제안 {parent_id}가 없다 — 지금 제안은 {sug['topic_id'] if sug else '없음'}")
        rec = {"week": week, "child_topic": topic_id, "parent_topic": parent_id, "pairs": sug["pairs"], "rule": sug["rule"],
               "verdict": verdict, "by": "user-feedback", "at": now_iso()}
        with self.lock:
            self._append_hier_feedback(rec)
        self.log("topic_parent_feedback", topic_id, week=week, parent=parent_id, rule=sug["rule"], verdict=verdict)
        return {"ok": True, "verdict": verdict, "message": "맞다고 답했어요" if verdict == "accept" else "아니라고 답했어요"}

    def topic_set_parent(self, topic_id, week, parent_id):
        """직접 묶기 — parent_id의 화제를 부모로. None이면 직접 묶은 부모를 푼다."""
        f = self._topic_file(week)
        by_id = {x["id"]: x for x in f.get("topics") or []}
        if topic_id not in by_id:
            raise ServiceError(404, f"{week}에 화제 {topic_id}가 없다")
        with self.lock:
            if parent_id is None:
                cur = self._hier_feedback(week)["manual"].get(topic_id)
                if cur is None:
                    return {"ok": True, "verdict": None, "message": "직접 묶은 부모가 없어요"}
                rec_parent, verdict, msg = cur, "manual_clear", "직접 묶은 부모를 풀었어요"
            else:
                if parent_id not in by_id:
                    raise ServiceError(404, f"{week}에 부모 화제 {parent_id}가 없다")
                if parent_id == topic_id:
                    raise ServiceError(400, "자기 자신을 부모로 묶을 수 없다")
                rec_parent, verdict, msg = parent_id, "manual", "직접 묶었어요"
            self._append_hier_feedback({"week": week, "child_topic": topic_id, "parent_topic": rec_parent, "pairs": [], "rule": "manual",
                                        "verdict": verdict, "by": "user-feedback", "at": now_iso()})
        self.log("topic_parent_manual", topic_id, week=week, parent=rec_parent, verdict=verdict)
        return {"ok": True, "verdict": verdict, "message": msg}

    def topic_parent_candidates(self, week, q, limit):
        """직접 묶기 선택창 — 그 주 화제 중 제목에 q가 든 것, 순위순."""
        week = week or (self._topic_weeks() or [None])[0]
        if week is None:
            return []
        needle = (q or "").strip().lower()
        out = [{"id": t["id"], "title": t["title"], "rank": t["rank"]} for t in self._topic_file(week).get("topics") or []
               if needle in (t.get("title") or "").lower()]
        return sorted(out, key=lambda x: x["rank"])[:limit]

    def saved_topics(self):
        return list(reversed(self._saved_topics()))  # 저장한 순서대로 쌓이므로 뒤집으면 최근 것 먼저

    def save_topic(self, topic_id, week):
        """화제를 저장한다 — 주차 파일이 다시 만들어져도 남도록 복사본을 둔다. 두 번 눌러도 한 번만 (멱등)."""
        t = next((x for x in self._topic_file(week).get("topics") or [] if x["id"] == topic_id), None)
        if t is None:
            raise ServiceError(404, f"{week}에 화제 {topic_id}가 없다")
        with self.lock:
            items = self._saved_topics()
            if any(x["id"] == topic_id for x in items):
                return {"ok": True, "saved": True, "message": "이미 저장한 화제예요"}
            items.append({"id": topic_id, "week": week, "title": t["title"], "url": t["url"], "n_sources": t["n_sources"],
                          "sources": t["sources"], "n_items": t["n_items"], "posts": self._topic_posts(t), "saved_at": now_iso()})
            self._put_saved_topics(items)
        self.log("topic_saved", topic_id, week=week)
        return {"ok": True, "saved": True, "message": "저장했어요"}

    def unsave_topic(self, topic_id):
        with self.lock:
            items = self._saved_topics()
            if not any(x["id"] == topic_id for x in items):
                raise ServiceError(404, f"저장한 화제 {topic_id}가 없다")
            self._put_saved_topics([x for x in items if x["id"] != topic_id])
        self.log("topic_unsaved", topic_id)
        return {"ok": True, "saved": False, "message": "저장을 뺐어요"}

    # ── Decisions (읽기만 — R8) ──────────────────────────────────

    def decisions(self):
        ctx = self.ctx()  # 읽기 — 쓰기 잠금을 기다리지 않는다
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

    def check_flow(self, flow):
        if flow not in FLOWS:
            raise ServiceError(404, f"모르는 흐름 {flow!r} — {list(FLOWS)}")
        if flow not in self.flows:
            raise ServiceError(409, f"{self.mode} 모드에서는 {flow}를 돌리지 않는다 — {list(self.flows)}만")

    def _prepare_real(self, flow):
        """실사용 실행 전 — radar-snapshots를 당기고(pull만) 가장 새 detect 입력을 고르고, ADR repo를 원격에 맞춘다(ff). 요약 줄."""
        notes = []
        if _git(self.snapshot_repo, "pull", "--ff-only", "-q") is None:
            notes.append(f"radar-snapshots를 못 당겼다 — 가진 것으로 돈다 ({self.snapshot_repo})")
        feed = _latest_feed(self.snapshot_repo)
        if feed:
            self.snapshot_dir = feed
            notes.append(f"입력 {os.path.basename(feed)}")
        elif flow == "detect":
            raise RuntimeError(f"detect 입력이 없다 — {os.path.join(self.snapshot_repo, DETECT_FEEDS)}/<주차>/feed.json (수집 PC의 주간 작업이 만든다)")
        else:
            notes.append("detect 입력이 없어 출처를 못 연다 — 조건은 판정 불가로 남는다")
        b = hitl.sync_exporter(self.ctx())
        if b:
            notes.append(f"ADR repo를 원격에 못 맞췄다 ({b.get('reason')}) — 로컬 상태로 돈다")
        return notes

    def _export_record(self, rep):
        """revisit 직후 — 기록용 칸만 바뀐 ADR을 커밋 1개로 main에 (사용자 10/26). 요약 한 줄 (쓴 파일이 없으면 None)."""
        files = rep.get("files_written") or []
        if not files:
            return None
        ids = sorted(os.path.basename(f)[:4] for f in files)
        r = hitl.export_record(self.ctx(), files, f"radar revisit {str(rep.get('now') or '')[:10]}: revisit_last_checked — ADR {', '.join(ids)}")
        self.log("revisit_record", None, state=r["state"], adrs=ids, commit=r.get("commit"), reason=r.get("reason"), detail=r.get("detail"))
        if r["state"] == "exported":
            return f"재검토 기록 커밋 {r['commit'][:7]} (ADR {len(ids)}개)"
        why = r.get("reason") + (f": {r['detail']}" if r.get("detail") else "")
        return f"재검토 기록 커밋 막힘 ({why}) — 파일은 ADR repo 작업 사본에 남았다"

    def run(self, flow, trigger="manual"):
        """흐름 하나를 한 번 돌린다 (스텁 대본, 데모·실사용이면 실제 모델). 코어 그래프를 그대로 부르고 보고서만 요약한다.
        스냅숏 모드 + dry-run (check_scope) — 실사용 모드도 그래프는 dry-run ctx로 돌고, 내보내기는 답과 기록 커밋만 한다."""
        self.check_flow(flow)
        with self.lock:
            ctx = self.ctx(export=False)
            if self.pscript is not None:  # 프로필 대본 — 판단표는 프로필 스냅숏(`<data>/snapshots`)의 소식·출처를 읽고 적은 것
                if flow == "detect":
                    fn, script = detect, {"detect": self.pscript["detect"]}
                else:
                    fn, script = revisit, stub_scripts.revisit_script(self.pscript["revisit"])
            elif flow == "detect":
                fn, script = detect, stub_scripts.detect_script()
            else:  # JHS-001 대본 — 스냅숏 S02(Jev v0.9.0 릴리스)에서 0001 c1이 충족된다
                fn, script = revisit, stub_scripts.revisit_script(stub_scripts.SCRIPT["cases"]["JHS-001"])
            res = {"id": f"{ctx.now}-{flow}", "flow": flow, "at": ctx.now, "mode": self.mode, "trigger": trigger, "ok": True, "error": None,
                   "summary": [], "drafts": [], "alerts": [], "excluded": []}
            shadow = main_view = None
            try:
                notes = []
                if self.mode == "real":
                    notes = self._prepare_real(flow)
                    ctx.env["RADAR_SNAPSHOT_DIR"] = self.snapshot_dir  # 방금 고른 detect 입력
                    if flow == "detect":
                        res["input"] = os.path.basename(self.snapshot_dir)
                        if trigger == "schedule" and self._ran_input(res["input"]):
                            # 수집 PC의 주간 사슬이 늦거나 실패했다 — 지난 입력을 다시 돌리지 않는다 (HYO-142). 실행 기록에 안 남겨서
                            # 정기 실행이 다음 확인(5분)에 다시 본다 — 새 입력이 push되면 그때 돈다. 손 실행은 막지 않는다
                            return {**res, "skipped": "same_input",
                                    "summary": notes + [f"입력 {res['input']}은 이미 돌렸다 — 새 주 입력을 기다린다"]}
                    if self.shadow:  # 주 실행이 ADR을 바꾸기 전에 떠 둔다 — 그림자도 같은 입력을 본다
                        shadow = self._shadow_prepare(res["id"])
                queued = {q["item"] for q in hitl.review_queue(ctx)} if flow == "detect" else None
                rep, diag = asyncio.run(fn(ctx, self.chat(flow, script), ctx.now, self.model_name))
                if queued is not None:  # 새 detect 초안 = 실행 뒤 검토 대기에 새로 생긴 항목 — 후보 대비 검토율의 분모 (HYO-56, 비교 프로토콜 §7)
                    for q in hitl.review_queue(ctx):
                        if q["item"] not in queued and q["kind"] == "detect":
                            self.log("item_created", q["item"], kind="detect", adr=q["adr"], run=res["id"])
                res.update(self._summarize_detect(rep) if flow == "detect" else self._summarize_revisit(rep))
                main_view = self._compare_view(flow, rep, res)
                res["summary"] = notes + res["summary"]
                if flow == "revisit":
                    self._record_alerts(res["alerts"], ctx.now)  # 충족 알림은 Inbox로 (화면 설계 ③)
                    if self.mode == "real":
                        res["summary"] += [x for x in [self._export_record(rep)] if x]
                if diag.get("p3_errors"):
                    res["summary"].append(f"오류 {len(diag['p3_errors'])}건: " + "; ".join(e.get("kind", "?") for e in diag["p3_errors"]))
            except Exception as e:  # 실행이 죽어도 앱은 산다 — 기록에 남기고 보인다
                res.update(ok=False, error=f"{type(e).__name__}: {str(e)[:300]}")
            res["usage"] = self._usage(ctx)  # 죽은 실행도 그때까지 부른 만큼 (HYO-98)
            with open(self.runs_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(res, ensure_ascii=False) + "\n")
            self.log("run", res["id"], flow=flow, trigger=trigger, ok=res["ok"], drafts=len(res["drafts"]), alerts=len(res["alerts"]), excluded=len(res["excluded"]),
                     usage=res["usage"])
        if shadow is not None:  # 잠금 밖에서 — 그림자가 분 단위로 돌아도 화면·다음 실행을 막지 않는다
            args = (flow, shadow, res, main_view)
            if self.shadow_async:
                t = threading.Thread(target=self._shadow_run, args=args, name=f"radar-shadow-{flow}", daemon=True)
                self.shadow_threads.append(t)
                t.start()
            else:
                self._shadow_run(*args)
        return res

    # ── API 그림자 (HYO-99 3단계) ───────────────────────────────────
    # 같은 입력(실행 직전 ADR 사본 + 같은 detect 입력)을 API 엔진으로 한 번 더 — Inbox·알림·실행 기록·ADR repo에는 아무것도 안 쓴다.
    # 두 엔진의 결과를 나란히 <data>/shadow.jsonl에 한 줄. 실패해도 주 실행에는 영향이 없다.

    def _shadow_prepare(self, run_id):
        d = os.path.join(self.data_dir, "shadow", re.sub(r"[^0-9A-Za-z-]", "", run_id))
        try:
            shutil.rmtree(d, ignore_errors=True)
            adr_dir, targets = copy_adr(self.real_info["adr_repo"], os.path.join(d, "adr"))
        except (OSError, ValueError) as e:
            return {"dir": d, "error": f"ADR 사본을 못 떴다: {type(e).__name__}: {e}"}
        return {"dir": d, "adr_dir": adr_dir, "targets": targets, "snapshot": self.snapshot_dir}

    def _shadow_chat(self, flow):
        if self.shadow_factory is not None:
            return self.shadow_factory(flow)
        if not self._api_key():
            return None
        from radar import models
        return models.make_anthropic()

    def _compare_view(self, flow, rep, summ):
        """엔진 비교에 쓸 모양 — 같은 칸을 두 엔진에서 뽑는다."""
        if flow == "detect":
            return {"candidates": sorted(c.get("technology") or "" for c in rep.get("candidates") or []),
                    "drafts": [{"technology": d["technology"], "title": d["title"], "validate_errors": len(d["validate_errors"])} for d in summ["drafts"]],
                    "excluded": [{"url": e["url"], "reason": e["reason"]} for e in summ["excluded"]]}
        return {"results": [{"adr": r.get("adr"), "condition": r.get("condition_id"), "result": r.get("result"), "changed": bool(r.get("changed"))}
                            for r in rep.get("results") or []],
                "alerts": [{"adr": a["adr"], "condition": a["condition"]} for a in summ["alerts"]]}

    def _shadow_run(self, flow, sh, main, main_view):
        rec = {"id": main["id"], "flow": flow, "at": main["at"], "trigger": main["trigger"], "input": os.path.basename(sh.get("snapshot") or "") or None,
               "main": {"engine": self.engine, "ok": main["ok"], "error": main["error"], "usage": main.get("usage"), **(main_view or {})}}
        c = None
        try:
            if sh.get("error"):
                raise RuntimeError(sh["error"])
            chat = self._shadow_chat(flow)
            if chat is None:
                rec["shadow"] = {"engine": "api", "ok": False, "skipped": "no_key", "error": ".env에 ANTHROPIC_API_KEY가 없다"}
            else:
                env = self.env(export=False)
                env.update(RADAR_ADR_DIR=sh["adr_dir"], RADAR_TARGETS=sh["targets"], RADAR_SNAPSHOT_DIR=sh["snapshot"],
                           RADAR_SERVED_LOG=os.path.join(sh["dir"], "served.log"))
                c = Ctx.from_env(env, radar_py=self.radar_py)
                c.now = main["at"]
                rep, _diag = asyncio.run((detect if flow == "detect" else revisit)(c, chat, c.now, REAL_MODEL))
                summ = self._summarize_detect(rep, adr_dir=sh["adr_dir"]) if flow == "detect" else self._summarize_revisit(rep)
                rec["shadow"] = {"engine": "api", "model": REAL_MODEL, "ok": True, "error": None, "usage": self._usage(c),
                                 **self._compare_view(flow, rep, summ)}
        except Exception as e:  # 그림자가 죽어도 주 실행·앱은 산다
            rec["shadow"] = {"engine": "api", "ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}", "usage": self._usage(c) if c else None}
        finally:
            shutil.rmtree(sh["dir"], ignore_errors=True)
        with self._shadow_lock, open(os.path.join(self.data_dir, SHADOW_FILE), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def _ran_input(self, week):
        """이 detect 입력(주차)으로 끝까지 돈 detect가 있나 — 예전 기록은 요약 첫 줄의 「입력 <주차>」로 본다."""
        return any(r.get("flow") == "detect" and r.get("ok") and (r.get("input") == week or f"입력 {week}" in (r.get("summary") or []))
                   for r in self.runs(limit=500))

    def runs(self, limit=50):
        try:
            lines = open(self.runs_path, encoding="utf-8").read().splitlines()
        except OSError:
            return []
        return [json.loads(x) for x in reversed(lines[-limit:]) if x.strip()]

    def _summarize_detect(self, rep, adr_dir=None):
        try:
            feed = json.load(open(os.path.join(self.snapshot_dir, "feed.json"), encoding="utf-8"))
            n_feed = len(feed)
        except (OSError, ValueError):
            feed, n_feed = [], None
        titles = {f.get("url"): f.get("title") for f in feed if isinstance(f, dict)}
        gate = self._gate_why()
        # 버린 소식과 이유 (HYO-97) — 초안 0건이어도 무엇을 보고 왜 버렸는지 남는다. why = 관문이 남긴 이유(수집 스냅숏 입력일 때)
        excluded = [{"url": e.get("url") or "", "title": titles.get(e.get("url")), "reason": e.get("reason") or "",
                     "why": (gate.get(e.get("url")) or {}).get("reason")} for e in rep.get("excluded") or []]
        cands, skipped = rep.get("candidates") or [], rep.get("skipped_existing") or []
        drafts = []
        for c in cands:
            if not c.get("adr"):
                continue
            d = adr_dir or self.adr_dir  # 그림자는 ADR 사본
            f = next(iter(sorted(p for p in os.listdir(d) if p.startswith(c["adr"] + "-"))), None)
            m = _TITLE.search(open(os.path.join(d, f), encoding="utf-8").read()) if f else None
            drafts.append({"adr": c["adr"], "title": m.group(1).strip() if m else c["technology"], "technology": c["technology"],
                           "validate_errors": c.get("validate_errors") or []})
        head = f"소식 {n_feed}건 → " if n_feed is not None else ""
        summary = [x for x in [self._funnel_line()] if x]  # 수집 스냅숏에서 만든 입력이면(pipeline.detect_feed) 깔때기 건수 한 줄 (HYO-84)
        summary += [f"{head}후보 {len(cands)}건 · 이미 있는 결정이라 건너뜀 {len(skipped)}건 · 제외 {len(excluded)}건"]
        if drafts:
            bad = sum(1 for d in drafts if d["validate_errors"])
            summary.append(f"초안 {len(drafts)}건 작성 (proposed)" + (f" — validate 실패 {bad}건" if bad else ""))
        else:
            summary.append("새 초안 없음")
        return {"summary": summary, "drafts": drafts, "excluded": excluded}

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


_SECTION = re.compile(r"^## (.+)$", re.M)
_WEEK_DIR = re.compile(r"^\d{4}-W\d{2}$")


def _git(cwd, *args):
    """git 한 번 → 출력(앞뒤 공백 뺌), 실패면 None. 묻지 않는다(GIT_TERMINAL_PROMPT=0) — 인증이 필요하면 실패로 끝난다."""
    try:
        p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=120,
                           env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def _same_dir(a, b):
    """같은 폴더인가 — 문자열로 비교하지 않는다. macOS 임시 폴더는 /var → /private/var 링크, Windows는 8.3 짧은 이름(RUNNER~1)이 섞인다."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _overlaps(a, b):
    """한 폴더가 다른 폴더 안(또는 같은 폴더)인가. 링크·짧은 이름을 풀고 비교하고, Windows에서 드라이브가 다르면 안 겹친다."""
    a, b = (os.path.normcase(os.path.realpath(p)) for p in (a, b))
    try:
        return os.path.commonpath([a, b]) in (a, b)
    except ValueError:  # 다른 드라이브 (C: / D:)
        return False


def _latest_feed(snapshot_repo):
    """radar-snapshots/detect/<주차>/ 중 feed.json이 있는 가장 새 주차. 없으면 None."""
    root = os.path.join(snapshot_repo, DETECT_FEEDS)
    try:
        weeks = sorted((n for n in os.listdir(root) if _WEEK_DIR.match(n) and os.path.exists(os.path.join(root, n, "feed.json"))), reverse=True)
    except OSError:
        return None
    return os.path.join(root, weeks[0]) if weeks else None


def _parse_adr(text):
    """detect 초안 파일 → 편집용 초안(AdrDraft 모양). `ops.render`의 거꾸로 — 못 읽으면 None (화면은 마크다운만 보인다)."""
    fm = _front_matter(text)
    body = re.sub(r"\A---\s*\n.*?\n---\s*\n", "", text, count=1, flags=re.S)
    m = _TITLE.search(body)
    parts = _SECTION.split(body)
    sec = {parts[i].strip(): parts[i + 1].strip("\n") for i in range(1, len(parts) - 1, 2)}
    decision, related = [], []
    for line in (sec.get("Decision Outcome") or "").splitlines():
        if line.startswith("관련 결정: "):
            related = [x.strip() for x in line[len("관련 결정: "):].split(",") if x.strip()]
        else:
            decision.append(line)
    notes = [line[2:] for line in (sec.get("Research Note") or "").splitlines() if line.startswith("- ")]
    rw = [{**{k: None for k in RevisitCondition.model_fields}, **c} if isinstance(c, dict) else c for c in fm.get("revisit_when") or []]  # render가 뺀 null 칸
    d = {"title": m.group(1).strip() if m else "", "technology": fm.get("technology"), "use": fm.get("use"),
         "affected": fm.get("affected") or [], "evidence": fm.get("evidence") or [], "context": (sec.get("Context and Problem Statement") or "").strip(),
         "decision": "\n".join(decision).strip(), "related": related, "research_note": notes, "revisit_when": rw}
    try:
        return AdrDraft.model_validate(d).model_dump(mode="json")
    except Exception:  # noqa: BLE001 — 사람이 손으로 고친 파일 등
        return None


def _front_matter(text):
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return {}
    try:
        d = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}
    return d if isinstance(d, dict) else {}


def _capture_stubs(scripts=None):
    """대화에서 고를 수 있는 capture 스텁 대본 — 초안이 있는 것 + 같은 결정이 이미 있는 것(Jev, JHS-013: link로 끝난다).
    프로필 대본은 `"link": true`로 같은 결정이 있는 쪽을 표시한다."""
    out = {}
    for cid, sc in (scripts if scripts is not None else stub_scripts.SCRIPT["capture"]).items():
        if cid.startswith("_") or not isinstance(sc, dict):
            continue
        if sc.get("draft") is not None or cid == "JHS-013" or sc.get("link"):
            out[cid] = sc
    return out


def pick_stub(conversation, scripts=None, default=DEFAULT_STUB):
    """스텁 모드에서만 — 대화에 나온 기술·repo·component·용도로 대본을 고른다 → (대본 ID, 기술이 맞았나).
    실제 모델은 대화를 읽고 초안을 쓴다. 스텁은 미리 적은 초안 중 가장 가까운 것을 낼 뿐이다.
    scripts는 프로필의 capture 대본 (없으면 코어 대본)."""
    text = conversation.lower()
    best, score = None, 0
    for cid, sc in sorted(_capture_stubs(scripts).items()):
        m = sc["match"]
        if (m.get("technology") or "").lower() not in text:
            continue
        s = 4 + sum(1 for k in ("repo", "component", "use") if (m.get(k) or "").lower() in text)
        if s > score:
            best, score = cid, s
    return (best, True) if best else (default, False)
