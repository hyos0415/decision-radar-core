"""실행 컨텍스트와 radar.py 호출. 실행한 도구 호출은 스킬 eval transcript와 같은 모양(tool_use)으로 남긴다 (비교 프로토콜 §4)."""

import dataclasses
import json
import os
import shlex
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SKILL_RADAR = os.path.join(ROOT, ".claude", "skills", "jhs-decisions", "scripts", "radar.py")


class RadarError(RuntimeError):
    pass


@dataclasses.dataclass
class Ctx:
    radar_py: str
    env: dict
    adr_dir: str
    targets: dict  # repo 이름 → 절대 경로
    dry_run: bool
    events: list = dataclasses.field(default_factory=list)
    calls: int = 0  # 지금 판정에서 모델이 부른 도구 수 — P3 「도구 호출 누락」
    now: str = ""  # capture 초안의 date
    excludes: dict = dataclasses.field(default_factory=dict)  # repo 이름 → radar-targets.json의 exclude (내 스택이 아닌 폴더 — repo_grep이 건너뛴다, HYO-205)

    def record(self, name, inp):
        self.events.append({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}})

    def tool_calls(self):
        return sum(1 for e in self.events if e["type"] == "assistant")

    @classmethod
    def from_env(cls, env, radar_py=SKILL_RADAR):
        adr_dir = env.get("RADAR_ADR_DIR") or os.path.join(".", "docs", "adr")
        tpath = env.get("RADAR_TARGETS") or os.path.join(adr_dir, "..", "..", "radar-targets.json")
        base = os.path.dirname(os.path.abspath(tpath))
        rows = json.load(open(tpath, encoding="utf-8")) if os.path.exists(tpath) else []
        targets = {t["repo"]: os.path.abspath(os.path.join(base, t["path"])) for t in rows}
        return cls(radar_py=radar_py, env=dict(env, PYTHONIOENCODING="utf-8"), adr_dir=adr_dir,
                   targets=targets, dry_run=env.get("RADAR_DRY_RUN") == "1",
                   excludes={t["repo"]: list(t.get("exclude") or []) for t in rows})


def radar(ctx, *args, stdin=None, ok_codes=(0,)):
    """radar.py를 부르고 JSON 출력을 돌려준다. 기록은 스킬 transcript의 Bash 호출과 같은 모양.
    validate는 오류가 있으면 exit 1이다 — 그래도 JSON을 받으려면 ok_codes=(0, 1)."""
    argv = [ctx.radar_py, *args]
    inp = {"command": "python " + " ".join(shlex.quote(a) for a in argv)}
    if stdin is not None:
        inp["stdin"] = stdin
    ctx.record("Bash", inp)
    p = subprocess.run([sys.executable, *argv], input=stdin, env=ctx.env, capture_output=True, text=True, encoding="utf-8")
    if p.returncode not in ok_codes:
        raise RadarError(f"radar.py {args[0]} exit {p.returncode}: {p.stderr.strip()[:300]}")
    return json.loads(p.stdout)


def check_scope(ctx):
    """스파이크 범위: 스냅숏 모드 + dry-run만."""
    if not ctx.env.get("RADAR_SNAPSHOT_DIR"):
        raise NotImplementedError("라이브 모드(WebFetch)는 스파이크 범위 밖 — RADAR_SNAPSHOT_DIR 필요")
    if not ctx.dry_run:
        raise NotImplementedError("PR·issue는 스파이크 범위 밖 — RADAR_DRY_RUN=1 필요")
