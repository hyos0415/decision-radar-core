---
status: proposed
date: 2026-10-13
deciders: [shinhm21]
---
# react-markdown으로 ADR 마크다운 렌더링

- 관련 작업: [HYO-102](https://linear.app/<workspace>/issue/HYO-102)
- 구현 초안: [PR #69](https://github.com/hyos0415/decision-radar/pull/69)(draft)

## Context and Problem Statement

데스크톱 앱의 Inbox 상세 화면(`desktop/src/renderer/src/components/ItemDetail.tsx` 156번째 줄)과 Decisions 상세 화면(`components/DecisionsView.tsx` 115번째 줄)은 ADR 원문을 문자열 그대로 출력합니다. 두 화면이 쓰는 `.document` 스타일(`assets/design.css`)에는 `white-space: pre-wrap`이 들어 있습니다. 그래서 줄바꿈만 유지되고, 제목 기호(`#`), 굵게 표시(`**`), 표, front matter가 글자 그대로 보입니다. 위 줄 번호와 `design.css`는 HYO-101(디자인 시안 골격) 브랜치 `fe/design-system` 기준입니다. 2026-10-13 현재 main은 같은 원문을 `<pre>`에 `whitespace-pre-wrap`으로 출력합니다.

백엔드(`desktop/backend/service.py`)는 ADR 파일 전체를 front matter까지 포함해 `markdown` 필드로 보냅니다. 이 본문에는 모델이 작성한 초안도 들어 있으므로, 화면은 이 내용을 신뢰할 수 없는 입력으로 다뤄야 합니다.

제약은 세 가지입니다. 첫째, `desktop/src`는 프론트 담당 영역이고 API 계약은 레포 주인이 바꿉니다. 둘째, 앱의 CSP는 이미지를 앱 자체 파일과 `data:` 주소에서만 불러오게 제한합니다(`img-src 'self' data:`). 셋째, 앱은 macOS와 Windows에서 모두 동작해야 합니다.

사람이 승인과 거부를 판단하는 화면에서 ADR 원문을 어떻게 안전하고 읽기 쉽게 보여 줄 것인가?

## Decision Drivers

- 모델이 쓴 본문을 화면에 그려도 스크립트가 실행되지 않아야 합니다.
- API 계약은 바꾸지 않습니다. 계약을 바꾸면 레포 주인 영역까지 고쳐야 합니다.
- HYO-101에서 정한 디자인 토큰과 모양이 맞아야 합니다.
- ADR에 자주 나오는 표와 체크박스(GitHub 마크다운 문법)가 제대로 보여야 합니다.

## Decision Outcome

채택: 프론트에서 react-markdown + remark-gfm으로 렌더링하고, 스타일은 `.document` 아래에 토큰으로 직접 작성합니다. 근거는 아래 [Pros and Cons of the Options](#pros-and-cons-of-the-options)의 비교표에 있습니다.

함께 정한 세부 사항은 다음과 같습니다.

- front matter는 `remark-frontmatter`로 본문에서 숨깁니다. 상태와 날짜는 이미 화면 위쪽에 표시되기 때문입니다.
- 본문의 HTML 태그를 렌더링하는 `rehype-raw`는 쓰지 않습니다.
- 링크를 누르면 앱 창 안에서 페이지가 바뀌지 않고 시스템 브라우저가 열리게 합니다. main 프로세스의 `setWindowOpenHandler`에서 `shell.openExternal`을 호출한 뒤 `{ action: 'deny' }`를 돌려주고, `will-navigate` 이벤트로 앱 창의 페이지 이동을 막습니다. 외부로 여는 주소는 `http`와 `https`로 시작하는 것만 허용합니다.
- CSP는 지금 설정을 유지합니다. 외부 이미지는 계속 차단되지만, 이 앱은 인터넷 연결 없이 스텁 모드로 동작하므로 문제가 없습니다.
- 마크다운 편집기와 미리보기 전환 기능은 이번 결정에 포함하지 않습니다.

### Consequences

- 쉬워지는 것:
  - 사람이 승인과 거부를 판단할 때 ADR 원문을 읽기 쉬워집니다.
  - API는 그대로 유지됩니다.
  - 본문의 스크립트가 화면에서 실행될 경로가 줄어듭니다.
- 어려워지는 것과 치르는 비용:
  - npm 의존성이 세 개(`react-markdown`, `remark-gfm`, `remark-frontmatter`) 늘어납니다.
  - `design.css`에서 마크다운 요소별 스타일을 직접 관리해야 합니다.
  - 본문에 HTML 태그나 외부 이미지가 들어 있는 ADR은 작성자가 의도한 모양으로 보이지 않습니다.

## Pros and Cons of the Options

결정은 "프론트에서 react-markdown으로 렌더링한다" 하나입니다. 아래 세 쟁점은 이 결정을 구성하는 세부 선택이므로 같은 ADR 안에 씁니다.

### 쟁점 1. 렌더링할 위치


| 기준         | 프론트에서 렌더링                         | 백엔드에서 HTML로 렌더링                                           |
| ---------- | --------------------------------- | --------------------------------------------------------- |
| 스크립트 실행 차단 | react-markdown이 HTML 태그를 렌더링하지 않음 | 받은 HTML을 프론트에서 그대로 화면에 넣어야 함                              |
| API 계약 유지  | 바꾸지 않음                            | `schemas.py`, OpenAPI, `test_backend.py`, 생성 타입을 함께 고쳐야 함 |
| 담당 영역      | 프론트 안에서 끝남                        | 레포 주인 영역을 고쳐야 함                                           |
| 판정         | 채택                                | 기각                                                        |


- 백엔드에서 HTML로 렌더링: 이 방식을 쓰려면 `schemas.py`, OpenAPI, `test_backend.py`, 생성된 API 타입을 한 PR에서 함께 고쳐야 합니다. 그러면 레포 주인 영역을 고치게 되고, 결국 프론트에서도 받은 HTML을 그대로 화면에 넣어야 합니다.

### 쟁점 2. 라이브러리


| 기준         | react-markdown + remark-gfm                              | marked + DOMPurify                          |
| ---------- | -------------------------------------------------------- | ------------------------------------------- |
| 스크립트 실행 차단 | 기본 설정에서 HTML 태그를 렌더링하지 않음. `dangerouslySetInnerHTML` 불필요 | DOMPurify 단계를 빠뜨리거나 설정을 잘못하면 스크립트가 실행될 수 있음 |
| 표와 체크박스    | remark-gfm으로 지원                                          | `gfm` 옵션(기본값 `true`)으로 GitHub 마크다운 명세를 따름   |
| 용량         | 의존성이 늘어남                                                 | 더 작음                                        |
| 판정         | 채택                                                       | 기각                                          |


- marked + DOMPurify: marked로 HTML을 만들고 DOMPurify로 정리한 뒤 `dangerouslySetInnerHTML`로 넣는 방식입니다. 용량은 더 작지만, 안전이 DOMPurify 단계 하나에 달려 있습니다. 이 단계를 빠뜨리거나 설정을 잘못하면 스크립트가 실행될 수 있습니다.

### 쟁점 3. 스타일


| 기준         | `.document` 아래에 토큰으로 직접 작성 | `@tailwindcss/typography`의 `prose` |
| ---------- | -------------------------- | ---------------------------------- |
| 디자인 토큰과 일치 | 현재 토큰을 그대로 씀               | 자체 색과 간격을 써서 토큰과 맞지 않음             |
| 작업량        | 요소별 스타일을 직접 관리해야 함         | 덮어쓰는 작업량이 직접 작성하는 작업량과 비슷함         |
| 판정         | 채택                         | 기각                                 |

## 근거 자료

- [repo] `decision-radar/desktop/src/renderer/src/components/ItemDetail.tsx` — ADR 원문을 문자열 그대로 출력하는 위치
- [repo] `decision-radar/desktop/backend/service.py` — ADR 파일 전체를 front matter까지 포함해 `markdown` 필드로 보내는 위치
- [official_doc] [https://github.com/remarkjs/react-markdown](https://github.com/remarkjs/react-markdown) — README(10.1.0) "Security" 절: "Use of `react-markdown` is secure by default." "Appendix A: HTML in markdown" 절: "`react-markdown` typically escapes HTML (or ignores it, with `skipHtml`) because it is dangerous and defeats the purpose of this library."
- [official_doc] [https://marked.js.org/using_advanced](https://marked.js.org/using_advanced) — `gfm` 옵션: 기본값 `true`, "If true, use approved GitHub Flavored Markdown (GFM) specification." 제거된 `sanitize` 옵션 설명: "use a sanitize library, like DOMPurify (recommended), sanitize-html or insane on the output HTML!"

## Links

- [HYO-102](https://linear.app/<workspace>/issue/HYO-102) — 이 결정을 구현하는 작업
- HYO-101(디자인 시안 골격) 
- [`docs/design/desktop-app.md`](../../../docs/design/desktop-app.md) (E6 데스크톱 앱 설계)

## 갱신 기록

## Research Note