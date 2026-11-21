"""실험 실행기 (HYO-116 첫 조각) — 승인한 결정 하나 → 레시피 고르기 → 격리된 사본에서 돌리기 → 결과.

데스크톱 앱의 「실험해 보기」(결정 상세)와 명령줄이 같이 쓴다:

  python -m pipeline.experiment plan --adr ../adr/docs/adr/0017-fastapi.md
  python -m pipeline.experiment run  --adr ../adr/docs/adr/0017-fastapi.md [--export] [--out <폴더>]

레시피 = 손으로 해 본 실험을 그대로 옮긴 것이다. 결정 기록의 모양으로 고른다.
  dependency  의존성 릴리스 대응(use가 「의존성 릴리스 <버전> 대응」) → 올려 보기(`pipeline/tryout.py` — 격리된 사본에서
              기준 검사 → 고정 버전 올림 → 같은 검사). Python 고정 줄 · npm package.json(HYO-248) 둘 다. 로컬 · 모델 호출 0
  embedding   검색 임베딩 자리(radar-slots.yaml `retrieval-dense`의 repo · component)에 닿는 새 후보 → Arena 정답지로 지금 것(BGE-M3)과
              짝 비교(`arena/compare_embed.py`, HYO-240 — HYO-228 손 실험을 옮김). 로컬 · 모델 호출 0 · 내보내지 않는다.
              torch · sentence-transformers가 든 Arena 환경이 따로 필요하다 (`RADAR_ARENA_PYTHON` 또는 ../venvs/arena · arena-qwen)
  다음 조각    gate(HYO-169 정답 118건) · medical-llm(HYO-229 추출 평가)

원칙 (HYO-116):
- **결정은 사람** — ADR을 고치지 않고 status를 바꾸지 않는다. 결과는 근거다
- **대상 repo의 main · 작업 폴더에 쓰지 않는다** — 사본에서만 돈다. 내보내기(브랜치 push · draft PR)는 export를 켰을 때 레시피가 정한 조건에서만
- **비용 문** — 레시피마다 cost(local · subscription · api)를 적는다. local만 바로 돈다. subscription · api는 confirm 없이는 돌지 않는다
  (유료 API 크레딧을 자동으로 쓰지 않는다 — 시험용 선불이다, 사용자 2026-11-21). 첫 조각의 레시피는 local뿐이다
- 레시피가 없거나 돌릴 수 없으면 지어내지 않고 **이유**를 적는다
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml

from arena import compare_embed
from pipeline import tryout

REPO = Path(__file__).resolve().parents[1]
COST_TEXT = {"local": "로컬 — 모델 호출 · 비용 없음", "subscription": "구독 세션을 쓴다 — 세션 수를 보고 확인해야 돈다",
             "api": "유료 API — 견적을 보고 확인해야 돈다"}
DEP_USE = re.compile(r"의존성 릴리스\s+\S.*대응")
NO_RECIPE = "이 결정에 맞는 실험 레시피가 아직 없다 — 지금은 의존성 릴리스 대응 · 검색 임베딩 후보만 (관문 분류 · 의료 LLM 레시피는 다음 조각)"
HF_MODEL = re.compile(r"huggingface\.co/(?!datasets/|spaces/|docs/|blog/|papers/)([A-Za-z0-9][\w.\-]*/[\w.\-]+)")


def _configs(root):
    """ADR repo 루트 → (대상 {repo: 절대 경로}, 검사 설정 | None). 검사 설정이 없으면 None — 이유로 돌려준다."""
    root = Path(root)
    targets = tryout.load_targets(root / "radar-targets.json")
    checks_path = root / "radar-checks.json"
    checks = json.loads(checks_path.read_text(encoding="utf-8")) if checks_path.exists() else None
    return targets, checks


class Dependency:
    """의존성 릴리스 대응 → 올려 보기 (HYO-239). 통과(pass)일 때만 draft PR을 연다 (`tryout.may_export`)."""
    id, label, cost = "dependency", "의존성 올려 보기", "local"
    exports = True
    export_rule = "통과(pass)일 때만 브랜치 push + draft PR"

    def match(self, adr, root=None):
        return bool(DEP_USE.search(adr.get("use") or ""))

    def plan(self, adr, root):
        version = tryout.release_version(adr)
        if not version:
            return {"ready": False, "reason": "올릴 버전을 결정 기록(use)에서 못 읽었다"}
        targets, checks = _configs(root)
        if checks is None:
            return {"ready": False, "reason": "검사 설정(radar-checks.json)이 ADR repo에 없다 — 무엇을 설치하고 무엇을 돌릴지 모른다"}
        try:
            repo, comp, check = tryout.choose_target(adr, targets, checks)
        except ValueError as e:
            return {"ready": False, "reason": str(e)}
        # 고정 줄이 지금 대상 repo에 있나 미리 본다 (읽기만) — 이미 걷어낸 의존성(langchain · ADR 0030)이 「돌릴 수 있음」으로 보이지 않게 (HYO-116).
        # 실제 실행은 원격 기본 브랜치의 사본에서 다시 본다. package.json에 있으면 npm 경로 (HYO-248)
        dep = tryout.npm_dep(targets[repo], check.get("npm"), adr["technology"])
        if dep:
            if not dep["from"]:
                return {"ready": False, "reason": f"{dep['lock']}에 {dep['name']} 항목이 없다 — 지금 설치 버전을 모른다"}
            if not (check["npm"].get("run")):
                return {"ready": False, "reason": f"{repo} · {comp}의 npm 검사(radar-checks.json의 npm.run)가 없다"}
            frm, where, how = dep["from"], dep["lock"], "package.json · lockfile 올림"
        else:
            _, pins = tryout.bump_all(targets[repo], check.get("manifests") or [], [(adr["technology"], version)])
            if not pins:
                return {"ready": False, "reason": f"{repo}의 manifest({', '.join(check.get('manifests') or []) or '없음'})에 "
                                                  f"{adr['technology']} 고정 줄(`이름==버전`)이 없다 — 이미 뺐거나 범위로 고정했다"}
            frm, where, how = pins[0]["from"], pins[0]["manifest"], "고정 버전 올림"
        if frm == version:
            return {"ready": False, "reason": f"이미 {version}이다 — 올릴 것이 없다 ({where})"}
        return {"ready": True, "params": {"package": adr["technology"], "to": version, "repo": repo, "component": comp},
                "detail": f"{adr['technology']} {frm} → {version} ({repo} · {comp}) — 격리된 사본에서 기준 검사 → {how} → 같은 검사"}

    def run(self, adr, root, plan, out, export=False):
        p = plan["params"]
        targets, checks = _configs(root)
        _, _, check = tryout.choose_target(adr, targets, checks, p["repo"], p["component"])
        res = tryout.tryout(adr, p["repo"], targets[p["repo"]], p["component"], check, p["package"], p["to"], out, export=export)
        return {"verdict": res["verdict"], "verdict_text": res["verdict_text"], "ok": res["verdict"] in ("pass", "already"),
                "report_md": tryout.report(res), "export": res.get("export")}


def arena_python():
    """Arena 실험 환경(torch · sentence-transformers · ranx)의 python — `RADAR_ARENA_PYTHON`, 없으면 형제 폴더 venvs/arena · arena-qwen.
    없으면 None (임베딩 레시피는 「환경 없음」으로 멈춘다 — 레시피가 환경을 만들지 않는다: 수 GB를 말없이 받지 않게)."""
    env = os.environ.get("RADAR_ARENA_PYTHON")
    if env:
        return env if Path(env).exists() else None
    sub = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    return next((str(p) for name in ("arena", "arena-qwen") if (p := REPO.parent / "venvs" / name / sub).exists()), None)


class Embedding:
    """검색 임베딩 자리 후보 → Arena 정답지로 지금 것(BGE-M3)과 짝 비교 (HYO-240). 결과는 근거 — 내보내지 않는다."""
    id, label, cost = "embedding", "임베딩 정답지 비교", "local"
    exports = False
    export_rule = "내보내지 않는다 — 결과는 근거, 채택은 사람"
    slot_id = "retrieval-dense"
    timeout = 2 * 3600

    def _slot(self, root):
        """radar-slots.yaml의 검색 임베딩 자리 → (repo, {component}) | None."""
        p = Path(root) / "radar-slots.yaml"
        if not p.exists():
            return None
        for s in (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("slots") or []:
            if s.get("id") == self.slot_id:
                comp = s.get("component")
                return s.get("repo"), set(comp if isinstance(comp, list) else [comp])
        return None

    def match(self, adr, root=None):
        if DEP_USE.search(adr.get("use") or "") or root is None:
            return False
        slot = self._slot(root)
        return bool(slot) and any(a.get("repo") == slot[0] and a.get("component") in slot[1] for a in adr.get("affected") or [])

    @staticmethod
    def model_id(adr):
        """HF 모델 id — technology가 `조직/이름`이면 그것, 아니면 evidence의 huggingface.co/<조직>/<이름>. 못 찾으면 None (지어내지 않는다)."""
        tech = (adr.get("technology") or "").strip()
        if re.fullmatch(r"[A-Za-z0-9][\w.\-]*/[\w.\-]+", tech):
            return tech
        for e in adr.get("evidence") or []:
            m = HF_MODEL.search(str((e or {}).get("url") or ""))
            if m:
                return m.group(1).rstrip("/")
        return None

    def plan(self, adr, root):
        model = self.model_id(adr)
        if not model:
            return {"ready": False, "reason": "HF 모델 id를 못 읽었다 — technology가 `조직/이름`이 아니고 evidence에 huggingface.co/<조직>/<이름>이 없다"}
        if not arena_python():
            return {"ready": False, "reason": "Arena 실험 환경이 없다 — RADAR_ARENA_PYTHON 또는 ../venvs/arena "
                                              "(uv pip install -r requirements.txt -r arena/requirements.txt)"}
        return {"ready": True, "params": {"model": model},
                "detail": f"{model} vs {compare_embed.BASE_ID} — Arena 정답지(gold@v1.2 test 36문항) nDCG@10 짝 비교, "
                          f"95% 구간이 0을 넘으면 나음 · M1에서 약 20분 (처음엔 모델 받기 포함)"}

    def run(self, adr, root, plan, out, export=False):
        out = Path(out) / "embed"
        cmd = [arena_python(), "-m", "arena.compare_embed", "--candidate", plan["params"]["model"], "--out", str(out),
               "--siblings", str(Path(root).resolve().parent)]          # 코퍼스의 형제 폴더 = ADR repo 옆 (radar-snapshots · 얼린 대상 repo)
        out.mkdir(parents=True, exist_ok=True)
        log = out / "run.log"
        try:
            with open(log, "w", encoding="utf-8") as f:
                code = subprocess.run(cmd, cwd=REPO, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                      timeout=self.timeout, env=tryout.check_env()).returncode
        except subprocess.TimeoutExpired:
            code = 124
        rep = out / "report.json"
        if code != 0 or not rep.exists():
            return {"verdict": "error", "verdict_text": f"실행 실패(종료 {code}) — {tryout.tail(log, 3) or '로그 없음'}", "ok": False,
                    "report_md": f"# 임베딩 비교 실패\n\n```\n{tryout.tail(log, 25)}\n```\n", "export": None}
        r = json.loads(rep.read_text(encoding="utf-8"))
        return {"verdict": r["verdict"], "verdict_text": compare_embed.VERDICTS[r["verdict"]], "ok": r["verdict"] == "better",
                "report_md": (out / "report.md").read_text(encoding="utf-8"), "export": None}


RECIPES = [Dependency(), Embedding()]


def _adr_root(adr_path):
    return Path(adr_path).resolve().parents[2]       # <root>/docs/adr/<file>


def plan(adr_path, root=None):
    """결정 기록 → 무엇을 돌릴지 (돌리지 않는다). ready가 False면 reason에 왜 못 돌리는지."""
    root = Path(root) if root else _adr_root(adr_path)
    try:
        adr = tryout.read_adr(adr_path)
    except (SystemExit, OSError, ValueError) as e:
        return {"adr": Path(adr_path).name.split("-", 1)[0], "recipe": None, "label": None, "cost": None, "cost_text": None,
                "ready": False, "reason": f"결정 기록을 못 읽었다: {e}"}
    for r in RECIPES:
        try:
            hit = r.match(adr, root)
        except (OSError, ValueError, yaml.YAMLError):     # 자리 설정이 깨졌으면 그 레시피는 고르지 않는다
            hit = False
        if hit:
            try:
                p = r.plan(adr, root)
            except (OSError, ValueError) as e:      # 대상 목록 · 검사 설정이 깨졌다 — 돌리지 않고 이유로
                p = {"ready": False, "reason": f"설정을 못 읽었다: {e}"}
            return {"adr": adr["id"], "recipe": r.id, "label": r.label, "cost": r.cost, "cost_text": COST_TEXT[r.cost],
                    "export_rule": r.export_rule, "exports": r.exports, **p}
    return {"adr": adr["id"], "recipe": None, "label": None, "cost": None, "cost_text": None, "ready": False, "reason": NO_RECIPE}


def _result(pl, verdict, verdict_text, ok, at, seconds=None, report_md=None, export=None, stub=False):
    return {"adr": pl["adr"], "recipe": pl.get("recipe"), "label": pl.get("label"), "at": at, "seconds": seconds,
            "verdict": verdict, "verdict_text": verdict_text, "ok": ok,
            "summary": f"{pl.get('label') or '실험'}: {verdict_text}", "report_md": report_md, "export": export, "stub": stub}


def run(adr_path, out, export=False, confirm=False, root=None):
    """한 건을 돌린다 → 결과 dict (out에 experiment.json · 레시피의 결과 파일). 돌릴 수 없으면 verdict unavailable · needs_confirm."""
    root = Path(root) if root else _adr_root(adr_path)
    at = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    pl = plan(adr_path, root)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    if not pl["ready"]:
        res = _result(pl, "unavailable", f"돌릴 수 없음 — {pl['reason']}", False, at)
    elif pl["cost"] != "local" and not confirm:
        res = _result(pl, "needs_confirm", f"확인이 필요하다 — {COST_TEXT[pl['cost']]}", False, at)
    else:
        recipe = next(r for r in RECIPES if r.id == pl["recipe"])
        t0 = time.time()
        r = recipe.run(tryout.read_adr(adr_path), root, pl, out, export=export)
        res = _result(pl, r["verdict"], r["verdict_text"], r["ok"], at, round(time.time() - t0, 1), r.get("report_md"), r.get("export"))
    (out / "experiment.json").write_text(json.dumps({"plan": pl, "result": res}, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    return res


def stub(adr_path, root=None):
    """스텁 · 데모 모드용 — 레시피 고르기(plan)는 진짜로 하고, 사본 · 설치 · 검사 · 내보내기는 하지 않는다 (네트워크 · 대상 repo 0)."""
    pl = plan(adr_path, root)
    at = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    if not pl["ready"]:
        return _result(pl, "unavailable", f"돌릴 수 없음 — {pl['reason']}", False, at, stub=True)
    detail = pl.get("detail") or ""
    md = (f"# [스텁] {pl['label']}\n\n**결과: 통과로 흉내 냈다 — 실제로 돌리지 않았다**\n\n- 할 일: {detail}\n"
          f"- 스텁 모드는 대상 repo 사본 · 설치 · 검사 · 내보내기를 하지 않는다 (실사용 모드에서 돈다)\n")
    return _result(pl, "pass", "[스텁] 통과로 흉내 냄 — 실제로 돌리지 않았다", True, at, 0.0, md, None, stub=True)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="pipeline.experiment", description="승인한 결정 하나를 레시피로 실험한다 (HYO-116)")
    ap.add_argument("op", choices=["plan", "run"])
    ap.add_argument("--adr", required=True, help="결정 기록 파일 (<ADR repo>/docs/adr/NNNN-*.md)")
    ap.add_argument("--out", help="결과 폴더 (기본: pipeline/out/experiment/<번호>-<시각>)")
    ap.add_argument("--export", action="store_true", help="레시피가 정한 조건(예: 통과)이면 브랜치 push + draft PR")
    ap.add_argument("--confirm", action="store_true", help="local이 아닌 레시피(구독 · 유료 API)를 돌린다")
    a = ap.parse_args(argv)
    if a.op == "plan":
        print(json.dumps(plan(a.adr), ensure_ascii=False, indent=1))
        return 0
    adr_id = Path(a.adr).name.split("-", 1)[0]
    out = a.out or Path("pipeline/out/experiment") / f"{adr_id}-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    res = run(a.adr, out, export=a.export, confirm=a.confirm)
    print(res["summary"])
    ex = res.get("export")
    if ex:
        print(f"내보내기: {ex.get('skipped_reason') or '완료'}" + (f" · {ex['pr_url']}" if ex.get("pr_url") else ""))
    print(f"-> {out}")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
