"""TypeSafe Jev(System One) 백엔드 — HTTP API를 표준 라이브러리(urllib)로 직접 부른다 (importance-policy §8 「변경 (2026-10-06)」).

`typesafe_sdk`를 쓰지 않는다 (의존성 추가 금지 — 실험 백엔드 하나로 스택을 늘리지 않는다). 요청·응답 모양의 근거 (로컬 자료로 확인, 네트워크 접근 없음):
  - 엔드포인트  `POST {base}/v1/systemone` · 기본 base `https://api.typesafe.ai`   (typesafe_sdk `_core/constants.py` SYSTEM_ONE_PATH · `constants.py` DEFAULT_BASE_URL)
  - 인증        `Authorization: Bearer <key>` · `Accept`/`Content-Type: application/json`  (`_core/transport.py prepare`)
  - 요청 본문   {"state": <문자열·JSON 객체·배열>, "model": "<id>", "questions": {이름: 질문}}   (`_core/endpoints.py prepare_system_one`)
      질문 noul   {"type":"noul","instructions":…,"criteria":{"true":…,"false":…}}  → 답 {"type":"noul","noul": P(예)}
      질문 choice {"type":"choice","instructions":…,"criteria":{선택지: 설명}}      → 답 {"type":"choice","choice":…,"confidence":…,"probabilities":{선택지: p}}
  - 응답 본문   {"model": "<답한 모델의 버전 ID>", "usage": {"input_tokens","output_tokens"}, "answers": {이름: 답}}   (`response_types.py`, `_schemas/models.py`)
  - 429·재시도  `retry-after-ms`(밀리초)·`retry-after`(초 또는 HTTP 날짜) 헤더 (`errors.py parse_retry_after`) — docs.typesafe.ai/models.md도 같은 말
  - 가격        입력 토큰당 $0.042/Mtok, 출력 무료 · 모델 `jev-1.13.0` (docs.typesafe.ai/models.md · finance_verifier run_eval_jev.py)
확인하지 못한 것: 실제 API와의 연결(키 확인 호출 금지), docs.typesafe.ai/api · llms.txt 원문(네트워크 금지), 4xx·429 본문의 정확한 모양.

- 모델은 `jev-1.13.0` 고정 — alias(`jev-latest`·`jev-preview`)는 거부한다. 응답의 `model` 필드를 호출 기록에 남기고, 요청한 것과 다르면 `model_mismatch`를 켠다
- 키: 레포 루트 `.env`의 `TYPESAFE_API_KEY`. os.environ에 넣지도, 읽지도 않는다
- 데이터 경계: NetGuard로 TypeSafe API 호스트만 허용한다 (GPT 경로의 api.openai.com과 섞지 않는다 — 백엔드별 허용 목록). 밖으로 나가려는 시도는 재시도 없이 중단
- 재시도: `product/radar/retry.py` 정책(연결 오류·타임아웃·429·5xx만, 2·4·8·16초). 429·5xx에 retry-after가 있으면 그 시간 이상 기다린다
- 문턱: **p ≥ 0.5 고정** (등록값 — 정답지로 맞추지 않는다)
"""
import json
import socket
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime

from . import policy
from .agents_backend import find_env_file, load_guard, load_retry
from .backends import Backend, BoundaryError, LabelAnswer, check_labels, labels_from_scores, CHANGE_SET, TIER_SET
from .prompts import jev_change_questions, jev_change_state, jev_tier_question, jev_tier_state

BASE_URL = "https://api.typesafe.ai"
SYSTEM_ONE_PATH = "/v1/systemone"
ALLOWED_HOSTS = {"api.typesafe.ai"}          # Jev 경로 전용 — GPT 경로의 api.openai.com과 섞지 않는다
MODEL_ID = "jev-1.13.0"
PINNED_MODELS = {MODEL_ID}                   # alias(jev-latest·jev-preview)는 새 릴리스가 나오면 답이 바뀐다 — 고정 ID만 쓴다
INPUT_USD_PER_MTOK = 0.042                   # 출력 토큰은 무료 (docs.typesafe.ai/models.md)
THRESHOLD = 0.5                              # 등록값 — 바꾸려면 재등록 (importance-policy §8). 정답지를 보고 맞추지 않는다
RETRY_AFTER_CAP_S = 60.0
DEFAULT_TIMEOUT_S = 60.0                     # finance_verifier run_eval_jev.py와 같다 (SDK 기본 10초는 첫 호출에 모자랐다)
USER_AGENT = "jhs-decisions-importance/1"


class JevHTTPError(Exception):
    """HTTP 4xx·5xx. status_code가 있어 retry.is_transient가 429·5xx만 재시도로 본다."""

    def __init__(self, status_code, body="", retry_after=None):
        super().__init__(f"HTTP {status_code}: {body[:200]}")
        self.status_code, self.retry_after = status_code, retry_after


class JevConnectionError(ConnectionError):
    """연결 실패 — 재시도 대상."""


class JevResponseError(ValueError):
    """응답 모양이 기대와 다르다 — 재시도해도 같으므로 재시도하지 않는다."""


def typesafe_key():
    """`.env`에서 TYPESAFE_API_KEY 한 줄만 읽는다 (product/spike/common/env.py load_dotenv와 같은 방식 — os.environ은 건드리지 않는다)."""
    f = find_env_file()
    if f:
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("TYPESAFE_API_KEY="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v
    raise SystemExit(".env에 TYPESAFE_API_KEY가 없다 (RADAR_ENV_FILE로 경로를 줄 수 있다)")


def parse_retry_after(headers):
    """retry-after-ms → retry-after(초 | HTTP 날짜) 순으로 읽어 초를 돌려준다. 없거나 못 읽으면 None (SDK errors.parse_retry_after와 같은 순서)."""
    for name, mult in (("retry-after-ms", 0.001), ("retry-after", 1.0)):
        raw = headers.get(name) if headers is not None else None
        if raw is None:
            continue
        try:
            v = float(str(raw).strip() or "0")
        except ValueError:
            if name == "retry-after":
                try:
                    return max(0.0, parsedate_to_datetime(raw).timestamp() - time.time())
                except (ValueError, TypeError, OverflowError):
                    pass
            continue
        if v == v and v not in (float("inf"), float("-inf")) and v >= 0:
            return v * mult
    return None


def cost_usd(input_tokens):
    return round(input_tokens * INPUT_USD_PER_MTOK / 1e6, 8)


def _prob(x, where):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not (0.0 <= x <= 1.0):
        raise JevResponseError(f"{where}: 확률이 아니다 {x!r}")
    return float(x)


class JevBackend(Backend):
    def __init__(self, model=MODEL_ID, *, key=None, log=None, base_url=BASE_URL, opener=None, guard=True, timeout=DEFAULT_TIMEOUT_S):
        """key를 주지 않으면 .env에서 읽는다. opener(.open(request, timeout=)) — 테스트가 가짜 HTTP를 꽂는 자리 (없으면 urllib 기본).
        guard=True면 NetGuard로 ALLOWED_HOSTS(+루프백)만 열어 둔다."""
        super().__init__(log)
        if model not in PINNED_MODELS:
            raise ValueError(f"{model}: 고정 버전 ID만 쓴다 {sorted(PINNED_MODELS)} — alias(jev-latest·jev-preview)는 거부")
        self.name = self.model = model
        self.base_url, self.timeout = base_url.rstrip("/"), timeout
        self.retry = load_retry()
        self.key = key if key is not None else typesafe_key()
        self.opener = opener or urllib.request.build_opener()
        self.models_returned = set()
        self.guard = None
        if guard:
            self.guard = load_guard().NetGuard(allow=ALLOWED_HOSTS)
            self.guard.__enter__()

    # -- HTTP 한 번
    def _once(self, body):
        req = urllib.request.Request(self.base_url + SYSTEM_ONE_PATH, data=body, method="POST",
                                     headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json",
                                              "Accept": "application/json", "User-Agent": USER_AGENT})
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            try:
                text = e.read().decode("utf-8", "replace")
            except OSError:
                text = ""
            raise JevHTTPError(e.code, text, parse_retry_after(e.headers)) from None
        except urllib.error.URLError as e:
            if isinstance(e.reason, (socket.timeout, TimeoutError)):
                raise TimeoutError(str(e.reason)) from None
            raise JevConnectionError(f"{type(e.reason).__name__}: {e.reason}") from None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise JevResponseError(f"JSON이 아니다: {e}") from None

    def _call(self, flow, state, questions, parse):
        """한 요청(질문 여럿 병렬) → (parse(answers)의 결과, 호출 기록). 실패하면 기록을 남기고 예외를 올린다.
        응답이 왔지만 모양이 깨져 parse가 실패한 호출은 토큰·비용을 그대로 적고 error를 단다 (돈은 이미 나갔다)."""
        body = json.dumps({"state": state, "model": self.model, "questions": questions}, ensure_ascii=False).encode("utf-8")
        r, retried, t0 = self.retry, [], time.perf_counter()
        guard = self.guard
        before = len(guard.attempts) if guard else 0
        rec = {"flow": flow, "requested_model": self.model, "estimated": False}
        attempts = i = o = 0
        try:
            for n in range(r.MAX_ATTEMPTS):
                attempts = n + 1
                try:
                    data = self._once(body)
                    break
                except Exception as e:  # noqa: BLE001
                    if guard and len(guard.attempts) > before:
                        raise BoundaryError(f"허용 밖 호스트 시도: {sorted(set(guard.attempts[before:]))}") from e
                    if n + 1 >= r.MAX_ATTEMPTS or not r.is_transient(e):
                        raise
                    retried.append(r.error_kind(e))
                    wait = r.delay(n)
                    ra = getattr(e, "retry_after", None)
                    if ra is not None:
                        wait = max(wait, min(float(ra), RETRY_AFTER_CAP_S))     # 429의 retry-after는 존중한다 (정책 지연보다 짧아지지 않게 max)
                    r.SLEEPER.sleep(wait)
            if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
                raise JevResponseError("answers가 없다")
            usage = data.get("usage") or {}
            i, o = usage.get("input_tokens"), usage.get("output_tokens")
            if not isinstance(i, int) or isinstance(i, bool):
                raise JevResponseError("usage.input_tokens가 없다 — 비용을 모르는 호출을 성공으로 세지 않는다")
            returned = data.get("model")
            self.models_returned.add(returned)
            o = o if isinstance(o, int) and not isinstance(o, bool) else 0
            result = parse(data["answers"])
        except Exception as e:  # noqa: BLE001
            billed = i if isinstance(i, int) and not isinstance(i, bool) else 0
            self.log.add({**rec, "model": locals().get("returned"), "input_tokens": billed, "output_tokens": o if isinstance(o, int) else 0,
                          "cost_usd": cost_usd(billed), "latency_s": round(time.perf_counter() - t0, 3),
                          "error": f"{type(e).__name__}: {str(e)[:200]}", "attempts": attempts, **({"retried": retried} if retried else {}),
                          **({"blocked_hosts": sorted(set(guard.attempts))} if isinstance(e, BoundaryError) and guard else {})})
            raise
        call = self.log.add({**rec, "model": returned, "model_mismatch": returned != self.model, "input_tokens": i,
                             "output_tokens": o, "cost_usd": cost_usd(i), "latency_s": round(time.perf_counter() - t0, 3),
                             "attempts": attempts, **({"retried": retried} if retried else {})})
        return result, call

    # -- 변화 종류: 종류마다 예/아니오 질문 · 한 요청에 병렬 · 확률 ≥ THRESHOLD면 예
    def classify_changes(self, q):
        def parse(answers):
            per = {}
            for kind in policy.CHANGES:
                a = answers.get(kind)
                if not isinstance(a, dict) or a.get("type") != "noul":
                    raise JevResponseError(f"{kind}: noul 답이 없다")
                p = _prob(a.get("noul"), kind)
                per[kind] = {"yes": p >= THRESHOLD, "prob": p}
            return per
        per, call = self._call("changes", jev_change_state(q), jev_change_questions(), parse)
        labels = check_labels(labels_from_scores(per, THRESHOLD, policy.CHANGES), CHANGE_SET, "changes")
        return LabelAnswer(labels=labels, per_label=per, reason=None, calls=[call])

    # -- 등급: L0~L8 선택지 한 질문. 고른 등급 + (두 번째 등급의 확률 ≥ THRESHOLD면 그것도)
    def classify_tiers(self, q):
        def parse(answers):
            a = answers.get("tier")
            if not isinstance(a, dict) or a.get("type") != "choice" or not isinstance(a.get("probabilities"), dict):
                raise JevResponseError("tier: choice 답이 없다")
            probs = {k: _prob(v, f"tier.{k}") for k, v in a["probabilities"].items()}
            check_labels(list(probs), TIER_SET, "tiers")
            if a.get("choice") not in probs:
                raise JevResponseError(f"tier: choice {a.get('choice')!r}가 probabilities에 없다")
            return a["choice"], probs
        (top, probs), call = self._call("tiers", jev_tier_state(q), {"tier": jev_tier_question()}, parse)
        rest = sorted(((p, t) for t, p in probs.items() if t != top), key=lambda x: (-x[0], x[1]))
        chosen = {top} | ({rest[0][1]} if rest and rest[0][0] >= THRESHOLD else set())
        labels = [t for t in policy.TIERS if t in chosen]
        per = {t: {"yes": t in chosen, "prob": probs[t]} for t in policy.TIERS if t in probs}
        return LabelAnswer(labels=labels, per_label=per, unsure=not labels, reason=None, calls=[call])

    def run_info(self):
        return {"models_returned": sorted(m for m in self.models_returned if m), "requested_model": self.model, "threshold": THRESHOLD,
                "blocked_hosts": sorted(set(self.guard.attempts)) if self.guard else []}

    def close(self):
        if self.guard:
            self.guard.__exit__(None, None, None)
            self.guard = None
