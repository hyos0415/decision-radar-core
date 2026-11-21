"""비동기 클라이언트가 이벤트 루프를 건너 재사용되지 않는지 (HYO-48 후속) — 실제 API 없이 127.0.0.1 로컬 서버로.

  python product/radar/test_loop_client.py

배경: 2026-10-06 게이트에서 호출 1,337회 중 590회가 첫 시도에 AnthropicConnectionError였다(detect 실행마다 첫 호출, revisit fan-out).
원인 가설: langchain-anthropic의 비동기 httpx 클라이언트는 프로세스 전체 공유(lru_cache)인데 eval은 실행마다 asyncio.run으로 새 루프를 만든다.
이 테스트는 같은 프로세스에서 asyncio.run을 여러 번 돌리며 keep-alive 서버를 부르고, 재시도(retry.py)를 끈 채(MAX_ATTEMPTS=1)
첫 시도 실패가 0인지 본다. 재시도를 켜 두면 실패가 가려지기 때문이다.
ADR 0030 뒤: models.AnthropicModel이 Anthropic SDK를 직접 부르고 비동기 클라이언트를 이벤트 루프마다 새로 만든다(_aclient).
`--mutant`는 클라이언트 하나를 프로세스 전체가 같이 쓰게 되돌린 상태다 (예전 결함의 모양).
"""

import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path[0] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # product/
sys.stdout.reconfigure(encoding="utf-8")

from radar import models, retry  # noqa: E402
from radar.loop import Request  # noqa: E402
from common.schemas import Verdict  # noqa: E402

REQUEST = Request(agent="t", system="s", messages=[{"role": "user", "content": "hi"}], tools=[], schema=Verdict)

BODY = json.dumps({"id": "msg_x", "type": "message", "role": "assistant", "model": "m", "stop_reason": "end_turn", "stop_sequence": None,
                   "content": [{"type": "text", "text": "ok"}], "usage": {"input_tokens": 1, "output_tokens": 1}}).encode()
HITS = []


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive — 풀에 연결이 남아야 루프 간 재사용이 일어난다

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        HITS.append(self.client_address)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)

    def log_message(self, *a):
        pass


def one_run(url, fan=1):
    """실행 1회 = 모델 새로 만들기(runtime.make_model과 같다) + asyncio.run 새 루프. 첫 시도 실패 수를 돌려준다."""
    m = models.make_anthropic(api_key="test-key", base_url=url)

    async def go():
        rs = await asyncio.gather(*[m.acall(REQUEST) for _ in range(fan)], return_exceptions=True)
        return [r for r in rs if isinstance(r, Exception)]

    return asyncio.run(go())


def main():
    mutant = "--mutant" in sys.argv
    if mutant:  # 수정 전 상태: 비동기 클라이언트 하나를 프로세스 전체가 같이 쓴다 (루프를 건너 keep-alive 연결이 재사용된다)
        shared = {}

        def one_client(self):
            if "c" not in shared:
                shared["c"] = models.anthropic.AsyncAnthropic(**self._kw)
            return shared["c"]
        models.AnthropicModel._aclient = one_client

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    retry.MAX_ATTEMPTS = 1  # 재시도가 실패를 가리지 않게

    fails = []
    for i in range(5):
        errs = one_run(url, fan=1 if i % 2 == 0 else 4)  # 단일 호출 실행과 fan-out 실행을 섞는다
        print(f"  실행 {i + 1}: 첫 시도 실패 {len(errs)}" + (f" — {type(errs[0]).__name__}: {errs[0]}" if errs else ""))
        fails += errs
    srv.shutdown()
    ok = not fails
    print(("all passed" if ok else f"FAILED — 첫 시도 실패 {len(fails)}건") + (" (변이: 수정 전 상태)" if mutant else ""))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
