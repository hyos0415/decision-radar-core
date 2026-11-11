"""출처 건강 검사 (HYO-172 1단계) — 조용히 고장 난 수집 출처를 지난 4주와 비교해 알린다.

  python -m collector.health check --week 2026-W45          # 얼린 주를 다시 검사해 출력만 (얼린 폴더에는 쓰지 않는다)
  python -m collector.health alert --week 2026-W46 [--dry-run]   # <주>/source-health.json → Linear 이슈 (주간 사슬 health 단계)

2026-W45(11/3)부터 Gemini changelog가 JS 페이지로 바뀌어 본문 45자였는데, 수집기 run.json의 `failed` 한 줄로만 남아
아무도 못 봤다(`non_200`은 매주 `pypi python` 404라 소음). 그래서 출처마다 「이상」만 골라 한 파일 · 한 줄로 올린다.

검사 단위와 규칙 (사용자 결정 11/5):
- source:<이름>   (reddit · hn · github · huggingface · vendor · security) — 죽음(crash) · 0건 · 항목 수가 지난 4주 중앙값의 절반 미만
- page:<url>      벤더 페이지 — 못 받음 · 수집기가 실패로 적음 · 본문이 지난 4주 중앙값의 절반 미만 · 다른 도메인으로 넘어감 · 3주째 그대로
- feed:<url>      벤더 RSS · Atom 피드 — 실패 · 다른 도메인 · 3주째 새 항목 없음
- url:<url>       그 밖의 실패 · 200 아닌 응답 (GitHub · Reddit · 보안 채널 …)
「3주째」는 페이지 · RSS만 본다 — GitHub releases는 조용한 게 정상이고(LEANN W42~W44 0건), 저장소가 죽거나 옮기면 실패 ·
리다이렉트로 잡힌다. 보안 채널은 0건이 정상이라 실패 · 리다이렉트만 본다.
알려진 예외는 `collector/source-health.yaml`에 키로 등록해 뺀다.

결과: 얼릴 때(`collector.snapshot freeze`) `<주>/source-health.json` — 데스크톱 앱 「이번 주」 머리 한 줄이 읽는다.
알림: `LINEAR_API_KEY`(.env)가 있으면 출처마다 Linear 이슈 하나 — 이미 열린 이슈가 있으면 새로 만들지 않고 「N주째」 댓글,
정상으로 돌아오면 「정상으로 돌아왔다」 댓글 한 번. 닫는 것은 사람이다. 같은 주를 다시 돌려도 두 번 쓰지 않는다(collector/out/health-alerts.json).
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
FILE = "source-health.json"
KNOWN = HERE / "source-health.yaml"
ALERTS = HERE / "out" / "health-alerts.json"
BASE_WEEKS = 4       # 비교할 지난 주 수
STALE_WEEKS = 3      # 이만큼 연속 그대로면 「3주째 그대로」
STREAK_DEPTH = 8     # 「N주째」를 셀 때 거슬러 볼 최대 주 수
SOURCES = ["reddit", "hn", "github", "huggingface", "vendor", "security"]
QUIET_SOURCES = {"security"}   # 보안 채널은 0건이 정상 (사용자 11/5, doc-pipeline §4-7) — 항목도 `osv` · `python` · `nodejs`로 세어진다
RULES = {"failed": "실패", "zero": "0건", "half": "절반 미만", "moved": "도메인 바뀜", "stale": "3주째 그대로"}
HEAD_CHARS = 200

LINEAR_API = "https://api.linear.app/graphql"
LINEAR_TEAM = "40426146-b589-4dd1-bf07-7c98b5e449a2"      # HYO
LINEAR_PROJECT = "b6e8eb07-e26b-4bf6-9fec-f21821823d42"   # Decision Radar
TITLE_PREFIX = "출처 고장:"
KEY_LINE = "health-key: "


# ── 주 자료 ──

def week_data(manifest, runs, items, texts=None):
    """검사 한 주 분량 — manifest · 그 주 실행 기록(run.json들) · 피드별 항목 수 · 페이지 전문(이번 주만, 증거 앞부분용)."""
    feeds = {}
    for it in items:
        f = (it.get("meta") or {}).get("feed")
        if f and it.get("source") == "vendor":
            c = feeds.setdefault(f, {"items": 0, "new": 0})
            c["items"] += 1
            c["new"] += bool(it.get("new"))
    return {"week": manifest["week"], "manifest": manifest, "runs": runs, "feeds": feeds, "texts": texts or {}}


def load_week(repo, week, texts=False):
    d = Path(repo) / week
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((d / "runs").glob("*.json"))]
    items = [json.loads(l) for l in (d / "items.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    t = {}
    if texts:
        for p in manifest.get("pages") or []:
            f = d / p["file"]
            if f.exists() and p.get("status") not in ("missing", "replaced"):
                t[p["id"]] = f.read_text(encoding="utf-8")
    return week_data(manifest, runs, items, t)


def history(repo, week, depth=BASE_WEEKS + STREAK_DEPTH):
    """week 앞의 얼린 주들(오래된 것부터, 최대 depth개)."""
    from collector import snapshot
    weeks = [w for w in snapshot.frozen_weeks(repo) if w < week][-depth:]
    return [load_week(repo, w) for w in weeks]


def rss_feeds(vendors_cfg):
    """3주 규칙을 거는 벤더 RSS · Atom 피드 url → 벤더."""
    return {f["url"]: v for v, spec in (vendors_cfg.get("vendors") or {}).items()
            for f in spec.get("feeds") or [] if f.get("kind") in ("rss", "atom")}


def load_known(path=KNOWN):
    try:
        return {k["key"]: k.get("why") or "" for k in (yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}).get("known") or []}
    except OSError:
        return {}


# ── 검사 ──

def _host(u):
    return urllib.parse.urlparse(u or "").netloc.lower()


def _median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _failed_key(source, e, pages, feeds):
    u = e.get("url")
    if u:
        return ("page" if u in pages else "feed" if u in feeds else "url"), u
    what = e.get("repo") or e.get("channel") or e.get("what") or e.get("vendor") or ("window" if e.get("window") else "?")
    return "url", f"{source}:{what}"


def evaluate(hist, cur, feeds_cfg):
    """한 주 검사 → {키: 문제}. hist = 이 주 앞의 주 자료(오래된 것부터). 알려진 예외 · N주째는 check()가 붙인다."""
    base = hist[-BASE_WEEKS:]
    m = cur["manifest"]
    out = {}

    def add(kind, name, url, rule, detail, **ev):
        key = f"{kind}:{url}" if kind != "source" else f"source:{name}"
        p = out.setdefault(key, {"key": key, "kind": kind, "name": name, "url": url, "reasons": [], "evidence": {}})
        if not any(r["rule"] == rule for r in p["reasons"]):
            p["reasons"].append({"rule": rule, "detail": detail})
        p["evidence"].update({k: v for k, v in ev.items() if v is not None})

    # 출처 단위 — 죽음 · 0건 · 절반 미만
    crashed = {s for r in m.get("runs") or [] for s in r.get("crashed") or []}
    by_source = (m.get("counts") or {}).get("by_source") or {}
    ran = {s for r in cur["runs"] for s in (r.get("sources") or {})}
    for s in SOURCES:
        if s not in ran and s not in by_source:
            continue   # 이 주에 돌지 않은 출처 (보안 채널은 W46부터)
        n = (by_source.get(s) or {}).get("items", 0)
        prev = _median([((h["manifest"].get("counts") or {}).get("by_source") or {}).get(s, {}).get("items") for h in base])
        if s in crashed:
            msg = next((r["sources"][s]["crash"] for r in cur["runs"] if "crash" in (r.get("sources") or {}).get(s, {})), "crash")
            add("source", s, None, "failed", f"수집기가 죽었다: {msg[:200]}", count=n, baseline_count=prev)
        if s in QUIET_SOURCES:
            continue   # 0건 · 적음이 정상 — 죽음 · 실패 · 리다이렉트만 본다 (W46에 「0건」으로 잘못 울렸다, HYO-179)
        if n == 0 and s in ran:
            add("source", s, None, "zero", "항목 0건", count=n, baseline_count=prev)
        elif prev and n * 2 < prev:
            add("source", s, None, "half", f"항목 {n:,}건 — 지난 {len(base)}주 중앙값 {prev:,.0f}건의 절반 미만", count=n, baseline_count=prev)

    # 페이지
    pages = {p["url"]: p for p in m.get("pages") or []}
    for p in m.get("pages") or []:
        if p.get("status") == "replaced":
            continue   # 옮긴 옛 주소 (vendors.yaml replaces) — 새 주소가 이어받는다
        name, url = p["vendor"], p["url"]
        head = (cur["texts"].get(p["id"]) or "")[:HEAD_CHARS] or None
        if p.get("status") == "missing":
            add("page", name, url, "failed", "이번 주에 못 받았다 (지난 주에는 있었다)")
            continue
        prev_rows = [next((q for q in h["manifest"].get("pages") or [] if q["id"] == p["id"] and q.get("status") not in ("missing", "replaced")), None)
                     for h in base]
        prev = _median([q["text_chars"] for q in prev_rows if q])
        if prev and p["text_chars"] * 2 < prev:
            add("page", name, url, "half", f"본문 {p['text_chars']:,}자 — 지난 {len(base)}주 중앙값 {prev:,.0f}자의 절반 미만",
                chars=p["text_chars"], baseline_chars=prev, head=head)
        recent = [p] + [q for q in reversed(prev_rows)][:STALE_WEEKS - 1]
        if len(recent) == STALE_WEEKS and all(q and q.get("status") == "unchanged" for q in recent):
            add("page", name, url, "stale", f"{STALE_WEEKS}주째 그대로 (본문 해시가 안 바뀜)", chars=p["text_chars"])

    # RSS · Atom 피드 — 3주째 새 항목 없음. 이번 주 실행이 고른 벤더의 피드만 (규칙이 안 고른 벤더는 받지도 않았다)
    picked = {v for r in cur["runs"] for v in ((r.get("sources") or {}).get("vendor") or {}).get("picked") or {}}
    for url, vendor in feeds_cfg.items():
        if vendor not in picked:
            continue
        news = [(cur["feeds"].get(url) or {}).get("new", 0)] + [(h["feeds"].get(url) or {}).get("new", 0) for h in reversed(base)][:STALE_WEEKS - 1]
        if len(news) == STALE_WEEKS and not any(news):
            add("feed", vendor, url, "stale", f"{STALE_WEEKS}주째 새 항목 없음", count=(cur["feeds"].get(url) or {}).get("items", 0))

    # 실행 기록 — 출처가 적은 실패 · 200 아닌 응답 · 다른 도메인으로 넘어간 리다이렉트
    for r in cur["runs"]:
        for s, rep in (r.get("sources") or {}).items():
            for e in rep.get("failed") or []:
                kind, url = _failed_key(s, e, pages, feeds_cfg)
                name = (pages.get(url) or {}).get("vendor") or feeds_cfg.get(url) or e.get("vendor") or s
                add(kind, name, url, "failed", str(e.get("status") or e.get("error") or "실패")[:200])
        http = r.get("http") or {}
        for e in http.get("non_200") or []:
            u = e["url"]
            kind = "page" if u in pages else "feed" if u in feeds_cfg else "url"
            add(kind, (pages.get(u) or {}).get("vendor") or feeds_cfg.get(u) or _host(u), u, "failed", f"HTTP {e.get('status')}")
        for e in http.get("redirects") or []:
            u, final = e["url"], e["final"]
            if _host(u) != _host(final):
                kind = "page" if u in pages else "feed" if u in feeds_cfg else "url"
                add(kind, (pages.get(u) or {}).get("vendor") or feeds_cfg.get(u) or _host(u), u, "moved", f"{_host(u)} → {final}", final_url=final)
    return out


def check(hist, cur, feeds_cfg, known):
    """이번 주 결과 문서 (source-health.json). hist는 오래된 것부터 — N주째를 세려고 BASE_WEEKS보다 길게 받는다."""
    now = evaluate(hist, cur, feeds_cfg)
    skipped = [{"key": k, "why": known[k]} for k in now if k in known]
    problems = [p for k, p in now.items() if k not in known]
    past = {}   # 거슬러 가며 주마다 문제 키 (그 주의 앞 주들로 검사)
    for p in problems:
        streak, since = 1, cur["week"]
        for i in range(len(hist) - 1, max(-1, len(hist) - 1 - STREAK_DEPTH), -1):
            if i not in past:
                past[i] = set(evaluate(hist[:i], hist[i], feeds_cfg))
            if p["key"] not in past[i]:
                break
            streak, since = streak + 1, hist[i]["week"]
        p.update(streak=streak, since=since)
    problems.sort(key=lambda p: (-p["streak"], p["kind"], p["key"]))
    checked = (len([s for s in SOURCES if s in ((cur["manifest"].get("counts") or {}).get("by_source") or {})])
               + len([p for p in cur["manifest"].get("pages") or [] if p.get("status") != "replaced"]) + len(feeds_cfg))
    return {"version": 1, "week": cur["week"], "checked": checked, "line": line(problems, checked), "problems": problems, "known": skipped}


def line(problems, checked):
    if not problems:
        return f"출처 이상 없음 ({checked}곳)"
    names = list(dict.fromkeys(p["name"] + (f" ({p['streak']}주째)" if p["streak"] > 1 else "") for p in problems))  # 같은 벤더 페이지 둘은 한 번
    return f"출처 고장 {len(problems)} — " + ", ".join(names[:3]) + (f" 외 {len(names) - 3}" if len(names) > 3 else "")


def check_build(repo, week, items, pages, manifest, runs, vendors_cfg=None, known=None):
    """collector.snapshot build 결과(아직 안 쓴 주)를 검사한다 — freeze가 부른다."""
    texts = {p["id"]: pages.get(p["file"], "") for p in manifest.get("pages") or [] if p.get("status") not in ("missing", "replaced")}
    cur = week_data(manifest, [run for _, run, _ in runs], items, texts)
    cfg = vendors_cfg if vendors_cfg is not None else yaml.safe_load((HERE / "vendors.yaml").read_text(encoding="utf-8"))
    return check(history(repo, week), cur, rss_feeds(cfg), load_known() if known is None else known)


# ── 알림 (Linear) ──

def env_value(key):
    """os.environ → .env(이 체크아웃, worktree면 메인 체크아웃) 한 줄. 없으면 None. os.environ에 넣지 않는다."""
    if os.environ.get(key):
        return os.environ[key]
    cands = [os.environ.get("RADAR_ENV_FILE"), REPO / ".env"]
    try:
        common = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=REPO, capture_output=True, text=True, timeout=10).stdout.strip()
        if common:
            cands.append((REPO / common).resolve().parent / ".env")
    except (OSError, subprocess.SubprocessError):
        pass
    for c in cands:
        if c and Path(c).exists():
            for l in Path(c).read_text(encoding="utf-8").splitlines():
                if l.strip().startswith(key + "="):
                    v = l.split("=", 1)[1].strip().strip('"').strip("'")
                    if v:
                        return v
    return None


class Linear:
    def __init__(self, key, post=None):
        self.key = key
        self._post = post or self._http

    def _http(self, payload):
        req = urllib.request.Request(LINEAR_API, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Authorization": self.key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())

    def q(self, query, **variables):
        d = self._post({"query": query, "variables": variables})
        if d.get("errors"):
            raise RuntimeError(f"Linear: {d['errors'][0].get('message')}")
        return d["data"]

    def open_issues(self):
        """열린 출처 고장 이슈 → {health-key: {id, identifier}}."""
        d = self.q("""query($team: ID!, $prefix: String!) { issues(first: 100, filter: {team: {id: {eq: $team}}, title: {startsWith: $prefix},
                   state: {type: {nin: ["completed", "canceled"]}}}) { nodes { id identifier description } } }""", team=LINEAR_TEAM, prefix=TITLE_PREFIX)
        out = {}
        for n in d["issues"]["nodes"]:
            for l in (n.get("description") or "").splitlines():
                if l.strip().strip("`").startswith(KEY_LINE):
                    out[l.strip().strip("`")[len(KEY_LINE):].strip()] = {"id": n["id"], "identifier": n["identifier"]}
        return out

    def create(self, title, body):
        d = self.q("""mutation($input: IssueCreateInput!) { issueCreate(input: $input) { success issue { id identifier } } }""",
                   input={"teamId": LINEAR_TEAM, "projectId": LINEAR_PROJECT, "title": title, "description": body, "priority": 2})
        return d["issueCreate"]["issue"]

    def comment(self, issue_id, body):
        self.q("""mutation($input: CommentCreateInput!) { commentCreate(input: $input) { success } }""", input={"issueId": issue_id, "body": body})


def _reasons(p):
    return " · ".join(RULES[r["rule"]] for r in p["reasons"])


def issue_text(doc, p):
    title = f"{TITLE_PREFIX} {p['name']} — {p['url'] or p['key']} ({_reasons(p)})"
    ev = p.get("evidence") or {}
    lines = [f"주간 수집 {doc['week']}의 출처 건강 검사가 찾은 이상 ({p['streak']}주째, {p['since']}부터).", ""]
    lines += [f"- **{RULES[r['rule']]}**: {r['detail']}" for r in p["reasons"]]
    if "chars" in ev:
        lines.append(f"- 본문: 지금 {ev['chars']:,}자" + (f" · 지난 4주 중앙값 {ev['baseline_chars']:,.0f}자" if ev.get("baseline_chars") else ""))
    if "count" in ev:
        lines.append(f"- 항목: 지금 {ev['count']:,}건" + (f" · 지난 4주 중앙값 {ev['baseline_count']:,.0f}건" if ev.get("baseline_count") else ""))
    if ev.get("final_url"):
        lines.append(f"- 넘어간 주소: {ev['final_url']}")
    if ev.get("head"):
        lines += ["", "받은 앞부분:", "```", ev["head"], "```"]
    lines += ["", f"고치는 곳: `collector/vendors.yaml` · `collector/pool.yaml` (주소를 옮기면 `replaces`로 빈 주를 메운다). 일부러 둘 것이면 "
              f"`collector/source-health.yaml`에 예외로 등록한다. 고치면 사람이 닫는다.",
              "", f"자동 생성 — `collector/health.py` (HYO-172)", f"`{KEY_LINE}{p['key']}`"]
    return title, "\n".join(lines)


def alert(doc, linear, state):
    """문제마다 이슈 하나(열린 이슈가 있으면 N주째 댓글) · 정상으로 돌아온 열린 이슈에 댓글. state = {"<주>|<키>": 무엇을 했나} (이어 쓴다)."""
    week, done = doc["week"], []
    opened = linear.open_issues()
    for p in doc["problems"]:
        mark = f"{week}|{p['key']}"
        if mark in state:
            continue
        if p["key"] in opened:
            linear.comment(opened[p["key"]]["id"], f"{week}: {p['streak']}주째 — " + " · ".join(f"{RULES[r['rule']]}: {r['detail']}" for r in p["reasons"]))
            state[mark] = f"댓글 {opened[p['key']]['identifier']}"
        else:
            title, body = issue_text(doc, p)
            state[mark] = f"이슈 {linear.create(title, body)['identifier']}"
        done.append(f"{p['name']} → {state[mark]}")
    now = {p["key"] for p in doc["problems"]} | {k["key"] for k in doc.get("known") or []}
    for key, iss in opened.items():
        mark = f"{week}|{key}|ok"
        if key not in now and mark not in state:
            linear.comment(iss["id"], f"{week}: 정상으로 돌아왔다 (출처 건강 검사에서 이상 없음). 원인을 고쳤으면 닫는다.")
            state[mark] = f"정상 댓글 {iss['identifier']}"
            done.append(f"{key} → {state[mark]}")
    return done


# ── CLI ──

def cmd_check(a):
    hist = history(a.snapshots, a.week)
    cfg = yaml.safe_load((HERE / "vendors.yaml").read_text(encoding="utf-8"))
    doc = check(hist, load_week(a.snapshots, a.week, texts=True), rss_feeds(cfg), load_known())
    if a.write:
        Path(a.write).write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(doc["line"])
    for p in doc["problems"]:
        print(f"- {p['key']} ({p['streak']}주째, {p['since']}부터): " + " · ".join(f"{RULES[r['rule']]}: {r['detail']}" for r in p["reasons"]))
    for k in doc["known"]:
        print(f"  (예외) {k['key']} — {k['why']}")
    return 0


def cmd_alert(a):
    f = Path(a.snapshots) / a.week / FILE
    if not f.exists():
        print(f"{f} 이 없다 — 알림 없음 (검사는 얼릴 때 돈다)")
        return 0
    doc = json.loads(f.read_text(encoding="utf-8"))
    print(doc["line"])
    if a.dry_run:
        for p in doc["problems"]:
            print(issue_text(doc, p)[0])
        print("(--dry-run: Linear에 쓰지 않았다)")
        return 0
    key = env_value("LINEAR_API_KEY")
    if not key:
        print("LINEAR_API_KEY 없음 (.env) — Linear 알림을 건너뛴다. 앱 「이번 주」 머리 한 줄은 그대로 보인다")
        return 0
    state = json.loads(Path(a.state).read_text(encoding="utf-8")) if Path(a.state).exists() else {}
    try:
        done = alert(doc, Linear(key), state)
    finally:
        Path(a.state).parent.mkdir(parents=True, exist_ok=True)
        Path(a.state).write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    for d in done:
        print(f"- {d}")
    if not done:
        print("새로 쓸 알림 없음")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="collector.health", description=__doc__.split("\n")[0])
    ap.add_argument("--snapshots", default=os.environ.get("RADAR_SNAPSHOT_REPO") or str(REPO.parent / "radar-snapshots"))
    sp = ap.add_subparsers(dest="cmd", required=True)
    c = sp.add_parser("check", help="얼린 주를 다시 검사해 출력 (얼린 폴더에는 쓰지 않는다)")
    c.add_argument("--week", required=True)
    c.add_argument("--write", help="결과 JSON을 이 경로에")
    c.set_defaults(fn=cmd_check)
    al = sp.add_parser("alert", help="<주>/source-health.json → Linear")
    al.add_argument("--week", required=True)
    al.add_argument("--dry-run", action="store_true")
    al.add_argument("--state", default=str(ALERTS))
    al.set_defaults(fn=cmd_alert)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    if __package__ in (None, ""):
        sys.path.insert(0, str(REPO))
    sys.exit(main())
