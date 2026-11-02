# 실사용 기간 변경 기록

> 비교 프로토콜 §7 변경 (2026-10-26)에 따라, 제품 실사용 기간(2026-11-02 ~ 11-29)에 엔진·프롬프트·코드를 바꿀 때마다 한 줄씩 남긴다.
> 주별 지표는 이 표의 날짜를 경계로 나눠 보고한다. 지우지 않는다 — 틀렸으면 아래에 정정 줄을 더한다.

| 날짜 | 무엇 | 왜 | 커밋 · PR |
|---|---|---|---|
| 2026-11-02 | detect 초안 검토 자리: 앱 Inbox만 → **ADR repo PR** (merge = 채택 · close = 기각, 앱 버튼도 PR을 merge·close, `RADAR_REVIEW=app`으로 되돌림). 행동 로그 시각 = GitHub mergedAt·closedAt | 스킬 기간처럼 어디서든 검토 — 장소 차이를 도구 차이로 재지 않게 (비교 프로토콜 §7 변경 11/2). 첫 detect(11/2 09:02, ADR 0017)는 바꾸기 전 실행 | HYO-158 |
| 2026-11-02 | revisit 입력: detect 입력(관문 통과 릴리스 노트만) → **`radar-snapshots/revisit/<주>/`** — 수집 PC가 화요일에 ADR 웹 `source_hint` 문서를 받아 얼리고, 같은 주 detect 입력·벤더 페이지도 넣는다. 모델은 여전히 웹에 안 나간다 | 11/2 09:02 첫 revisit이 12조건 전부 판정 불가(출처 문서 404 — ADR repo 2db52fd). 스킬은 웹을 직접 열어 판정했다 — 입력 차이를 도구 차이로 재지 않게 (사용자 결정 11/2, 선택지 1). 남는 차이: 출처가 최대 6일 묵는다(화 수집 → 월 revisit, 스킬은 그 자리에서 열었다). 첫 적용은 11/3 수집 → 11/9 revisit. 11/2 revisit은 바꾸기 전 | HYO-159 |
| 2026-11-02 | 실사용 기기(맥) 환경: `radar-targets.json`의 대상 레포 3개(`finance_verifier` · `fineprint` · `medical-mate-ai-radar`)가 형제 폴더에 **없던 것을 clone** (각 레포 main, 코드 변경 없음). 맥의 이 사본은 자동으로 당기지 않는다 — 수집 PC의 mirror 당기기(`collector/run.py`)는 맥에서 안 돈다 | 11/2 09:01 revisit에서 repo 조건 2개가 `read_repo_file` 404로 판정 불가(ADR 0001 c1 · ADR 0002 c2 — ADR repo 2db52fd). 스킬은 대상 레포를 읽을 수 있었다 — 환경 차이를 도구 차이로 재지 않게. 11/2 09:02 detect의 영향 분석도 이 레포들을 못 읽었다(초안 0017은 decision-radar만 대상). 첫 적용은 11/9 revisit | HYO-55 (#237) |
