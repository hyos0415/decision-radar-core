"""가짜 `codex exec` — CodexCliBackend 배관 테스트용 (HYO-169). 대본은 env RADAR_FAKE_CODEX의 JSON 파일.

대본 키: last(마지막 답 dict, 없으면 파일을 안 쓴다) · raw_last(문자열 그대로) · fail(turn.failed 메시지) · usage · sleep(초)
본 것(argv · stdin · cwd · env 키 유무)은 대본 파일 옆 `<대본>.seen.json`에 남긴다.
"""
import json
import os
import sys
import time
from pathlib import Path


def main():
    argv = sys.argv[1:]
    if argv == ["--version"]:
        print("codex-cli 0.0.0-fake")
        return 0
    script_path = Path(os.environ["RADAR_FAKE_CODEX"])
    script = json.loads(script_path.read_text(encoding="utf-8"))
    stdin = sys.stdin.buffer.read().decode("utf-8")
    opt = {argv[i]: argv[i + 1] for i in range(len(argv) - 1) if argv[i].startswith("-")}
    schema = json.loads(Path(opt["--output-schema"]).read_text(encoding="utf-8"))
    Path(str(script_path) + ".seen.json").write_text(json.dumps({
        "argv": argv, "stdin": stdin, "cwd": os.getcwd(), "schema": schema,
        "openai_key": "OPENAI_API_KEY" in os.environ, "codex_key": "CODEX_API_KEY" in os.environ}, ensure_ascii=False), encoding="utf-8")
    time.sleep(script.get("sleep", 0))
    print(json.dumps({"type": "turn.started"}))
    if script.get("fail"):
        print(json.dumps({"type": "turn.failed", "error": {"message": script["fail"]}}))
        return 1
    if "raw_last" in script:
        Path(opt["-o"]).write_text(script["raw_last"], encoding="utf-8")
    elif script.get("last") is not None:
        Path(opt["-o"]).write_text(json.dumps(script["last"], ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"type": "turn.completed", "usage": script.get("usage", {"input_tokens": 300, "cached_input_tokens": 100, "output_tokens": 40})}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
