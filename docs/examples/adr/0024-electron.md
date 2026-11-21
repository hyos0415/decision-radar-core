---
status: adopted
date: '2026-11-16'
technology: Electron
use: 의존성 릴리스 2026-11-11 Node.js 보안 릴리스 대응 (security)
affected:
- repo: decision-radar
  component: desktop
evidence:
- url: https://nodejs.org/en/blog/vulnerability/november-2026-security-releases
  source_type: official_doc
---
# Node.js 2026-11-11 보안 릴리스에 따른 Electron 번들 Node 대응 검토

> 2026-11-22 채택 — 사용자 결정. desktop의 Electron을 45.0.0 → 45.4.1로 올렸다(hyos0415/decision-radar#362, merge 1ae8a04). 45.0.0은 Node 24.22.0을 품어 영향 범위였고, Node v24.22.3이 이번 보안 릴리스이며 Electron 45.3.3부터 이를 품는다. 올린 뒤 번들 Node 24.22.3을 확인했다. CI(프론트 lint · build, 백엔드 회귀)를 통과했고 실사용 앱도 45.4.1로 다시 띄웠다. 노출 점검: desktop 코드가 쓰는 Node 네트워크 API는 빈 포트를 고르는 `net.createServer`(`desktop/src/main/backend.ts`) 하나이고, http2 · https · tls 서버나 respondWithFile은 쓰지 않는다. 백엔드는 Python(FastAPI)이라 이 공지와 무관하다.

## Context and Problem Statement
Node.js 프로젝트가 2026-11-11에 26.x, 24.x, 22.x 라인 보안 릴리스를 냈다. 공지에 나온 것은 llhttp(22.x·24.x·26.x), undici(24.x·26.x), sqlite(22.x·24.x) 의존성 업데이트와 high 등급 취약점들이다. HTTP/2 쪽은 세션 teardown 중 assertion 실패로 서버가 종료되는 문제(CVE-2026-123863), 종료 중 프로토콜 오류로 메모리·fd가 누수되는 문제(CVE-2026-123865), 스트림 teardown 때 해제된 응답 메모리가 상대에게 전송되는 문제(CVE-2026-123866), respondWithFile() 스트림 리셋 시 use-after-free(CVE-2026-123874)다. 그 밖에 HTTPS의 zero-length TLS write 재진입에 따른 힙 메모리 유출(CVE-2026-123864), TLS 서버 deferred handshake callback의 uncaught exception(CVE-2026-126051), TLS 1.2 renegotiation 때 servername을 바꿔 클라이언트 인증서 인증을 우회하는 문제(CVE-2026-123872)가 있다. 대부분 Node.js 서버 API(http2·https·tls 서버)를 쓰는 경우에 해당한다. decision-radar의 desktop 컴포넌트는 Electron(ADR 0011, 제품 클라이언트)을 쓰며 Electron은 Node.js를 번들한다. 다만 번들 Node 버전이 이 패치를 언제 포함하는지, 그리고 desktop 앱이 http2/https/tls 서버를 여는지는 공지에서 확인되지 않았다. 공지 본문은 일부가 잘려 있어 CVE 목록이 더 있을 수 있다.

## Decision Outcome
제안: (1) Electron이 이번 Node.js 패치를 반영한 릴리스를 내는지 Electron 릴리스 노트에서 확인하고, 반영 릴리스가 나오면 desktop의 Electron 버전 상향을 검토한다. (2) 그 전에 desktop 코드가 main/utility 프로세스에서 http2·https·tls 서버(또는 respondWithFile 등)를 실제로 여는지 점검해 노출 여부를 판단한다. 클라이언트 용도로만 쓰면 영향이 제한적일 수 있으나 이 판단은 확인이 필요하다. 채택 여부와 업그레이드 시점은 사람이 정한다.

관련 결정: 0011, 0013

## Research Note
- 재검토 조건 없음: 대응 작업(의존성 보안 릴리스).
- 미확인: Electron 현재 사용 버전과 번들 Node 라인(22/24/26)이 이번 패치 대상인지.
- 미확인: Electron이 이 Node.js 패치를 포함한 릴리스를 냈는지(Electron 릴리스 노트 미조회).
- 미확인: desktop 앱이 http2/https/tls 서버 API를 쓰는지.
- 공지 본문이 잘려 CVE-2026-123874 이후 항목은 확인하지 못했다.
- 관련 ADR 0011(Electron, adopted)이 있고, 0013(FastAPI)은 다른 기술이라 직접 관련이 없다.
