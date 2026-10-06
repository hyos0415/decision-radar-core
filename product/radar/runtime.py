"""런타임 인터페이스 — eval 어댑터(common/eval_adapter.py의 run_revisit·run_capture와 radar/evalkit.py의 run_detect)가 부른다.

settings(spec)는 호출 기록에 쓸 모델 이름이다 (스파이크에선 effort 설정 자리였다 — effort는 모델 생성자에 들어 있다).
"""

import os
import sys

from common.env import MODEL

from .capture import capture_turn1
from .detect import detect
from .revisit import revisit

HERE = os.path.dirname(os.path.abspath(__file__))


class Runtime:
    name = "langgraph"
    real_model = "anthropic"
    state_filename = "capture-checkpoint.sqlite"

    def add_args(self, ap):
        pass

    def real_spec(self, a):
        return {"kind": "anthropic"}

    def label(self, spec):
        return "anthropic"

    def new_shapes(self, spec):
        if spec["kind"] == "stub":
            return None
        from . import models
        return models.RequestShapes()

    def make_model(self, spec, shapes, flow):
        if spec["kind"] == "stub":
            from .stub_model import StubChat
            return StubChat(script=spec["script"], flow=flow)
        from . import models
        return models.make_anthropic(shapes)

    def settings(self, spec):
        return "stub" if spec["kind"] == "stub" else MODEL

    revisit = staticmethod(revisit)
    detect = staticmethod(detect)
    capture_turn1 = staticmethod(capture_turn1)

    def resume_cmd(self, spec, state_path, decision, now, radar_py, out_dir):
        cmd = [sys.executable, os.path.join(HERE, "resume.py"), "--state", state_path, "--decision", decision, "--now", now,
               "--radar", radar_py, "--out", out_dir]
        return cmd + (["--stub-script", spec["script_file"]] if spec["kind"] == "stub" else ["--anthropic"])
