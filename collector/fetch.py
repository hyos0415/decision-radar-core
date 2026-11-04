"""HTTP 가져오기 — 호스트별 최소 간격과 429 재시도. 표준 라이브러리만 쓴다 (stdlib http와 이름이 겹치지 않게 fetch.py)."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "decision-radar-collector/0.1 (personal research; github.com/hyos0415)"


class Fetcher:
    def __init__(self, min_interval=None, default_interval=1.0, retries=2, sleep=time.sleep, now=time.monotonic):
        self.min_interval = dict(min_interval or {})    # host -> 초
        self.default_interval = default_interval
        self.retries = retries
        self._last = {}
        self._sleep = sleep
        self._now = now
        self.log = []                                    # (url, status, 시도 횟수) — run.json에 남긴다

    def _wait(self, host):
        gap = self.min_interval.get(host, self.default_interval)
        last = self._last.get(host)
        if last is not None:
            remaining = gap - (self._now() - last)
            if remaining > 0:
                self._sleep(remaining)
        self._last[host] = self._now()

    def get(self, url, headers=None):
        return self._request(url, headers)

    def _request(self, url, headers=None, data=None):
        host = urllib.parse.urlparse(url).netloc
        attempt = 0
        while True:
            attempt += 1
            self._wait(host)
            req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    body = r.read()
                    self.log.append((url, r.status, attempt))
                    return r.status, body
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt <= self.retries:
                    wait = float(e.headers.get("Retry-After") or 0) or self.min_interval.get(host, 30) * 1.5
                    self._sleep(wait)
                    continue
                self.log.append((url, e.code, attempt))
                return e.code, e.read() if hasattr(e, "read") else b""
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt <= self.retries:
                    self._sleep(5)
                    continue
                self.log.append((url, f"error: {type(e).__name__}", attempt))
                return None, b""

    def get_json(self, url, headers=None):
        st, body = self.get(url, headers)
        if st != 200:
            raise FetchError(url, st, body[:300])
        return json.loads(body)

    def post_json(self, url, payload, headers=None):
        """JSON 본문 POST (OSV querybatch). 재시도·간격·log는 get과 같다."""
        st, body = self._request(url, {"Content-Type": "application/json", **(headers or {})}, json.dumps(payload).encode("utf-8"))
        if st != 200:
            raise FetchError(url, st, body[:300])
        return json.loads(body)


class FetchError(Exception):
    def __init__(self, url, status, body):
        super().__init__(f"{status} {url} {body!r}")
        self.url, self.status, self.body = url, status, body
