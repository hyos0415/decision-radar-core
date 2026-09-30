"""데이터 경계 가드 (런타임 스파이크 P5) — 한 턴이 도는 프로세스마다 켠다. capture 2턴은 다른 프로세스라 거기서도 켠다."""

import ipaddress
import socket
import traceback

from agents.tracing import TracingProcessor


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
        self.allow, self.attempts, self.stacks = set(allow), [], {}

    def _block(self, host):
        self.attempts.append(host)
        if host not in self.stacks:  # 호스트마다 첫 시도의 호출 위치 — 누가 밖으로 나가려 했나 (P5 진단)
            self.stacks[host] = [f"{f.filename.split('site-packages')[-1]}:{f.lineno} {f.name}"
                                 for f in traceback.extract_stack()[-16:-2]]
        raise OSError(f"netguard: {host} 차단 (P5)")

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
                guard._block(str(host))
            return guard._gai(host, *a, **k)

        def conn(sock, addr):
            host = addr[0] if isinstance(addr, tuple) else str(addr)
            if not guard._ok(str(host)):
                guard._block(str(host))
            return guard._conn(sock, addr)

        socket.getaddrinfo, socket.socket.connect = gai, conn
        return self

    def __exit__(self, *exc):
        socket.getaddrinfo, socket.socket.connect = self._gai, self._conn
